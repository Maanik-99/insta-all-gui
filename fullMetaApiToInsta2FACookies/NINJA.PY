import sys
import io
import time
import json
import re
import threading
import socketio
from bs4 import BeautifulSoup

# Ensure safe terminal encoding & unbuffered output on all Windows platforms
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    elif sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
except Exception:
    pass

def log(msg):
    """Safe immediate logging with flush"""
    print(msg, flush=True)

class TempMailNinjaAPI:
    """
    TempMail Ninja (https://tempmail.ninja) API Client.
    Direct Socket.IO API connection - No browser/selenium required!
    Features:
      - Instant email generation
      - Real-time incoming email notifications
      - Automated OTP / Confirmation Code extraction (6-digit, 8-digit, 4-digit)
      - Link extraction
      - wait_for_otp() helper for automation scripts
    """
    BASE_URL = "https://tm-app.solucioneswc.com:2083"

    def __init__(self, locale="en"):
        self.locale = locale
        self.sio = socketio.Client(
            logger=False,
            engineio_logger=False,
            reconnection=True,
            reconnection_attempts=5,
            reconnection_delay=2
        )
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Origin': 'https://tempmail.ninja',
            'Referer': 'https://tempmail.ninja/'
        }
        
        self.email_address = None
        self.visitor_token = None
        self.email_data = None
        self.processed_message_ids = set()
        self.latest_otp = None
        self.latest_message = None
        self.is_running = False
        self._otp_event = threading.Event()
        self._lock = threading.Lock()

        # Socket.IO Event Handlers
        self.sio.on("connect", self._on_connect)
        self.sio.on("disconnect", self._on_disconnect)
        self.sio.on("new_message_notify", self._on_new_message_notify)

    def _on_connect(self):
        log("[+] Connected successfully to TempMail Ninja backend API.")

    def _on_disconnect(self):
        log("[!] Socket disconnected from server.")

    def _on_new_message_notify(self, data):
        """Called automatically when the server pushes a real-time new message event"""
        self.check_and_read_inbox()

    def _ensure_connected(self):
        if not self.sio.connected:
            try:
                log(f"[*] Reconnecting to TempMail Ninja backend API...")
                self.sio.connect(
                    f"{self.BASE_URL}?locale={self.locale}",
                    headers=self.headers,
                    transports=['websocket', 'polling']
                )
                if self.visitor_token:
                    self._emit_ack("get_guest_user_data", {"token": self.visitor_token})
            except Exception as e:
                log(f"[-] Reconnection error: {e}")

    def _emit_ack(self, event, *args, timeout=10):
        """Helper to emit Socket.IO event and wait for acknowledgement"""
        if not self.sio.connected and event != "get_guest_user_data":
            self._ensure_connected()
        if not self.sio.connected:
            return None

        result = []
        event_done = threading.Event()

        def callback(response):
            result.append(response)
            event_done.set()

        try:
            self.sio.emit(event, *args, callback=callback)
            event_done.wait(timeout=timeout)
        except Exception as e:
            log(f"[-] Error emitting event '{event}': {e}")
            return None

        return result[0] if result else None

    def connect(self, token=None):
        """Connects to Socket.IO backend and initializes guest session"""
        if token:
            self.visitor_token = token
        log(f"[*] Connecting to {self.BASE_URL}...")
        self.sio.connect(
            f"{self.BASE_URL}?locale={self.locale}",
            headers=self.headers,
            transports=['websocket', 'polling']
        )

        # Initialize guest session
        guest_data = self._emit_ack("get_guest_user_data", {"token": self.visitor_token})
        if guest_data:
            ip = guest_data.get("ip", "Unknown")
            country = guest_data.get("country", "Unknown")
            log(f"[i] Guest session initialized (IP: {ip}, Country: {country})")

    def create_temp_email(self):
        """Generates a temporary email address via API request"""
        res = self._emit_ack("generate_temp_mail")

        if not res or "emailData" not in res:
            raise RuntimeError(f"Failed to generate temp email. Response: {res}")

        self.email_data = res.get("emailData", {})
        self.email_address = self.email_data.get("emailAddress") or self.email_data.get("address")
        self.visitor_token = res.get("newToken")
        expires_at = self.email_data.get("expiresAt") or self.email_data.get("expires_at")

        log(f"[+] Created Ninja TempMail: {self.email_address}")
        return self.email_address

    def extract_otp_or_code(self, subject, body_text, expected_digits=None):
        """
        Smart extraction of 6-digit, 8-digit, or 4-digit OTP / Confirmation Code
        from email subject and body with flexible length support.
        Filters out false positives (e.g. Menlo Park zip code 94025).
        """
        # Remove known footer boilerplate / zip codes (e.g. Menlo Park, CA 94025)
        clean_body = re.sub(r'Menlo\s+Park[^\n\r]*\b94025\b', '', body_text, flags=re.IGNORECASE)
        clean_body = re.sub(r'\b(?:CA|California)\s+94025\b', '', clean_body, flags=re.IGNORECASE)
        full_text = f"{subject}\n{clean_body}"

        # Ignore pure notification emails that do not contain verification codes
        ignore_subjects = [
            "did you just move a new profile",
            "action needed on your meta account",
            "security alert",
            "new login from",
            "account connected"
        ]
        if any(ign in subject.lower() for ign in ignore_subjects) and not re.search(r'\b(?:code|otp|pin|código|confirm)\b', full_text, re.IGNORECASE):
            return None

        if expected_digits:
            if isinstance(expected_digits, int):
                allowed_lengths = {expected_digits}
            elif isinstance(expected_digits, (list, tuple, set)):
                allowed_lengths = set(expected_digits)
            else:
                allowed_lengths = {6, 8}
        else:
            allowed_lengths = {6, 8, 4}

        # 1. High Priority: Keyword followed directly by code (supports 4 to 8 digits)
        keyword_patterns = [
            r'(?:confirmation|verification|security|auth(?:entication)?|login|access|activation|passcode|one-time)?\s*(?:code|otp|pin|código|kod)[\s\:\-\n\r]+(?:is\s*)?([0-9]{4,8}|[Gg]\-[0-9]{6}|[0-9]{3,4}[\-\s][0-9]{3,4})\b',
            r'(?:enter|use|with)\s+(?:the\s+)?(?:code\s+)?([0-9]{4,8})\s+(?:to\s+(?:confirm|verify|login|proceed|sign))',
            r'(?:code|otp|pin)[\s\:\=]+([0-9]{4,8})\b',
            r'\b([0-9]{6,8})\s+(?:is\s+your\s+(?:Instagram|Meta|Facebook|security|confirmation)\s+code)\b',
        ]
        for pattern in keyword_patterns:
            m = re.search(pattern, full_text, re.IGNORECASE)
            if m:
                raw_c = re.sub(r'\D', '', m.group(1))
                if len(raw_c) in allowed_lengths and raw_c != "94025":
                    return raw_c

        # 2. Subject Line numbers matching allowed lengths (check 8-digit first, then 6-digit)
        for d in sorted(allowed_lengths, reverse=True):
            subj_m = re.search(rf'\b([0-9]{{{d}}})\b', subject)
            if subj_m and subj_m.group(1) != "94025":
                return subj_m.group(1)

        # 3. Body numbers matching allowed lengths (check 8-digit first, then 6-digit)
        for d in sorted(allowed_lengths, reverse=True):
            body_matches = re.findall(rf'\b([0-9]{{{d}}})\b', clean_body)
            if body_matches:
                for c in reversed(body_matches):
                    if not c.startswith('000000') and c != "94025":
                        return c

        return None

    def get_message_detail(self, message_id):
        """Fetches complete message details by ID"""
        res = self._emit_ack("get_email_message", {"messageId": message_id, "messageType": "inbound"}, timeout=15)
        if not res or "emailMessage" not in res:
            return None
        return res.get("emailMessage", {})

    def check_and_read_inbox(self, expected_digits=None):
        """Fetches inbox and reads all unread messages, extracting OTP codes automatically"""
        self._ensure_connected()
        res = self._emit_ack("get_email_messages", {"messageType": "inbound"})
        if not res or not isinstance(res, dict):
            return []

        messages = res.get("email_messages", [])
        if not messages:
            return []

        new_found_messages = []

        for msg in messages:
            msg_id = msg.get("id") or msg.get("email_message_id")
            if not msg_id or msg_id in self.processed_message_ids:
                continue

            self.processed_message_ids.add(msg_id)
            
            # Fetch complete message content
            detail = self.get_message_detail(msg_id)
            msg_data = detail.get("data", {}) if detail else msg

            sender = msg_data.get("from")
            if isinstance(sender, dict):
                sender = sender.get("text") or sender.get("value", [{}])[0].get("address", "")
            
            recipient = msg_data.get("to")
            if isinstance(recipient, dict):
                recipient = recipient.get("text") or recipient.get("value", [{}])[0].get("address", "")

            subject = msg_data.get("subject", "No Subject")
            date_str = msg_data.get("date") or msg.get("created_at", "")
            raw_html = msg_data.get("html") or msg_data.get("content") or msg.get("content", "")
            raw_text = msg_data.get("text", "")

            # Convert HTML to clean readable text
            clean_text = raw_text.strip() if raw_text else ""
            if not clean_text and raw_html:
                soup = BeautifulSoup(raw_html, "html.parser")
                clean_text = soup.get_text(separator="\n").strip()

            # Smart OTP Extraction
            extracted_otp = self.extract_otp_or_code(subject, clean_text, expected_digits=expected_digits)
            if extracted_otp:
                with self._lock:
                    self.latest_otp = extracted_otp
                    self._otp_event.set()

            message_summary = {
                "id": msg_id,
                "from": sender,
                "to": recipient,
                "subject": subject,
                "date": date_str,
                "content": clean_text,
                "otp": extracted_otp
            }
            self.latest_message = message_summary
            new_found_messages.append(message_summary)

            # Clean and concise one-line logging
            if extracted_otp:
                log(f"⭐ [NINJA-OTP] Extracted Code: >>> {extracted_otp} <<< (From: {sender} | Subj: {subject})")
            else:
                log(f"[*] [NINJA-MAIL] Received email from: {sender} | Subj: {subject}")

        return new_found_messages

    def wait_for_otp(self, timeout=120, poll_interval=2, expected_digits=None, reset_latest=False):
        """
        Blocks until a fresh OTP / Confirmation code is received or timeout is reached.
        """
        if reset_latest:
            with self._lock:
                self.latest_otp = None
                self._otp_event.clear()

        if expected_digits:
            if isinstance(expected_digits, int):
                allowed_lengths = {expected_digits}
            elif isinstance(expected_digits, (list, tuple, set)):
                allowed_lengths = set(expected_digits)
            else:
                allowed_lengths = {6, 8}
        else:
            allowed_lengths = {4, 5, 6, 7, 8}

        log(f"[*] Waiting for OTP on '{self.email_address}' (Expected: {sorted(allowed_lengths)} digits, Timeout: {timeout}s)...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            # 1. First check if latest_otp already arrived via socket notification
            with self._lock:
                if self.latest_otp:
                    otp = str(self.latest_otp).strip()
                    if len(otp) in allowed_lengths:
                        log(f"[✔] OTP successfully detected: {otp}")
                        return otp

            # 2. Check inbox via polling
            self._ensure_connected()
            new_msgs = self.check_and_read_inbox(expected_digits=expected_digits)
            for m in new_msgs:
                if m.get("otp"):
                    otp = str(m["otp"]).strip()
                    if len(otp) in allowed_lengths:
                        with self._lock:
                            self.latest_otp = otp
                        log(f"[✔] OTP successfully detected: {otp}")
                        return otp

            # 3. Wait on event or timeout
            remaining = timeout - (time.time() - start_time)
            if remaining <= 0:
                break
            wait_time = min(poll_interval, remaining)
            if self._otp_event.wait(timeout=wait_time):
                self._otp_event.clear()
                with self._lock:
                    if self.latest_otp:
                        otp = str(self.latest_otp).strip()
                        if len(otp) in allowed_lengths:
                            log(f"[✔] OTP successfully detected: {otp}")
                            return otp

        log("[-] Timeout reached waiting for OTP.")
        return None

    def listen_forever(self, poll_interval=5):
        """Continuously listens for incoming emails in real-time"""
        self.is_running = True
        log(f"[*] Listening for incoming emails on '{self.email_address}'...")
        log("[*] Real-time Push Notifications + Auto-polling + Auto-OTP extractor active.")
        log("[*] (Press Ctrl+C to stop at any time)\n")

        try:
            while self.is_running:
                self.check_and_read_inbox()
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            log("\n[*] Stopping listener...")
        finally:
            self.disconnect()

    def disconnect(self):
        """Closes socket connection gracefully"""
        self.is_running = False
        if self.sio.connected:
            self.sio.disconnect()
            log("[*] Connection closed.")

# Global registry for active Ninja clients
_NINJA_CLIENTS: dict[str, TempMailNinjaAPI] = {}

def get_or_create_ninja_client(token_or_email: str | None = None) -> TempMailNinjaAPI | None:
    """
    Returns existing connected TempMailNinjaAPI instance or reconnects via visitor token.
    """
    if not token_or_email:
        return None

    key = str(token_or_email).strip()
    if key in _NINJA_CLIENTS:
        return _NINJA_CLIENTS[key]

    token = key
    if token.startswith("NINJA:"):
        parts = token.split(":")
        if len(parts) >= 2 and parts[1]:
            token = parts[1]

    try:
        client = TempMailNinjaAPI()
        client.connect(token=token)
        _NINJA_CLIENTS[key] = client
        return client
    except Exception as e:
        log(f"[-] [NINJA] Failed to create/reconnect client for {key}: {e}")
        return None

def generate_ninja_account(password: str = "", instance_name: str = "") -> tuple[str | None, str, str | None]:
    """
    Generates a temporary email using TempMail Ninja.
    Returns: (email, password, token)
    """
    try:
        client = TempMailNinjaAPI()
        client.connect()
        email = client.create_temp_email()
        if not email:
            return None, password, None

        token = f"NINJA:{client.visitor_token}:{email}"
        _NINJA_CLIENTS[email] = client
        _NINJA_CLIENTS[token] = client
        if client.visitor_token:
            _NINJA_CLIENTS[client.visitor_token] = client

        return email, password, token
    except Exception as e:
        log(f"[-] [NINJA] Error generating Ninja email: {e}")
        return None, password, None

def fetch_otp_ninja(token_or_email: str, max_wait_sec: int = 60, expected_digits: int | None = None, instance_name: str = "") -> str | None:
    """
    Polls/waits for OTP / verification code from TempMail Ninja.
    """
    client = get_or_create_ninja_client(token_or_email)
    if not client:
        return None
    try:
        otp = client.wait_for_otp(
            timeout=max_wait_sec,
            poll_interval=2,
            expected_digits=expected_digits,
            reset_latest=False
        )
        return otp
    except Exception as e:
        log(f"[-] [NINJA] Error waiting for OTP: {e}")
        return None


def main():
    client = TempMailNinjaAPI()
    try:
        # 1. Connect to backend API
        client.connect()

        # 2. Create new temp email
        email = client.create_temp_email()

        # 3. Wait and read any incoming messages with auto OTP detection
        client.listen_forever(poll_interval=5)

    except Exception as e:
        log(f"[-] Error: {e}")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
