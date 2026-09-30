"""
Mail.cx Temporary Email Client & OTP Extractor
----------------------------------------------
Author: Antigravity AI
Description:
  Automated temporary email generator and real-time inbox monitor for https://mail.cx.
  Features:
    - Dynamic system domain discovery from Mail.cx API
    - Random domain and local-part email generation
    - Real-time Server-Sent Events (SSE) push listener for instant email detection
    - Long-polling fallback to guarantee zero missed emails
    - Intelligent OTP / Verification Code and Activation Link extraction
    - Programmatic API for bot/script automation + interactive CLI
"""

import sys
import io
import time
import json
import re
import random
import string
import uuid
import threading
from typing import List, Dict, Optional, Tuple, Any

import requests
from bs4 import BeautifulSoup

# Ensure safe UTF-8 terminal encoding on Windows
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    elif sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
except Exception:
    pass


def log(msg: str):
    """Immediate thread-safe flush logger"""
    print(msg, flush=True)


class MailCxAPI:
    """
    Client for the Mail.cx temporary email service (https://mail.cx).
    """
    BASE_URL = "https://mail.cx"
    FALLBACK_DOMAINS = ["uqu.me", "ddker.com", "9k3r.com"]

    def __init__(self, client_id: Optional[str] = None):
        self.client_id = client_id or str(uuid.uuid4())
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Referer": "https://mail.cx/",
            "X-Client-ID": self.client_id
        })

        self.current_email: Optional[str] = None
        self.available_domains: List[str] = []
        self.processed_message_ids = set()

        # SSE Listener state
        self._sse_thread: Optional[threading.Thread] = None
        self._sse_stop_event = threading.Event()
        self._sse_session: Optional[requests.Session] = None

        # Fetch active domains
        self.refresh_domains()

    def refresh_domains(self) -> List[str]:
        """
        Fetches the current active domains from /v1/config.
        Falls back to known static domains if config is unreachable.
        """
        try:
            res = self.session.get(f"{self.BASE_URL}/v1/config", timeout=10)
            if res.status_code == 200:
                data = res.json()
                sys_domains = data.get("system_domains", [])
                domains = [d.get("domain") for d in sys_domains if d.get("domain")]
                if domains:
                    self.available_domains = domains
                    return self.available_domains
        except Exception as e:
            log(f"[!] Warning: Could not fetch /v1/config domains ({e}). Using fallbacks.")

        self.available_domains = list(self.FALLBACK_DOMAINS)
        return self.available_domains

    def generate_random_email(
        self,
        domain: Optional[str] = None,
        username: Optional[str] = None,
        length: int = 6
    ) -> str:
        """
        Generates a new random temporary email address.
        If domain is not specified, randomly selects from all available mail.cx domains.
        """
        if not self.available_domains:
            self.refresh_domains()

        selected_domain = domain if domain else random.choice(self.available_domains)

        if not username:
            # Generate random 6-character alphanumeric local part (mail.cx standard format)
            chars = string.ascii_lowercase + string.digits
            username = ''.join(random.choice(chars) for _ in range(max(2, length)))

        self.current_email = f"{username}@{selected_domain}"
        self.processed_message_ids.clear()
        return self.current_email

    def get_inbox(self, email_address: Optional[str] = None, timeout: int = 30) -> List[Dict[str, Any]]:
        """
        Fetches the message list for the given email inbox.
        mail.cx returns a JSON object containing {"emails": [...]}.
        """
        email = email_address or self.current_email
        if not email:
            raise ValueError("No email address specified.")

        try:
            url = f"{self.BASE_URL}/v1/inbox/{requests.utils.quote(email)}"
            res = self.session.get(url, timeout=timeout)

            if res.status_code == 204:
                return []
            elif res.status_code == 200:
                data = res.json()
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return data.get("emails") or data.get("messages") or []
                return []
            else:
                log(f"[-] Inbox fetch status: {res.status_code} ({res.text})")
                return []
        except requests.exceptions.Timeout:
            # Long-polling timed out on client side without new message
            return []
        except Exception as e:
            log(f"[-] Error fetching inbox: {e}")
            return []

    def get_email_detail(self, email_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves full details (body HTML/text, sender, headers, attachments) for a message.
        """
        try:
            url = f"{self.BASE_URL}/v1/email/{email_id}"
            res = self.session.get(url, timeout=15)
            if res.status_code == 200:
                return res.json()
            else:
                log(f"[-] Failed to fetch email {email_id}: HTTP {res.status_code}")
                return None
        except Exception as e:
            log(f"[-] Error fetching email details ({email_id}): {e}")
            return None

    def delete_email(self, email_id: str) -> bool:
        """Deletes a specific email by ID."""
        try:
            url = f"{self.BASE_URL}/v1/email/{email_id}"
            res = self.session.delete(url, timeout=10)
            return res.status_code in (200, 204)
        except Exception:
            return False

    def clear_inbox(self, email_address: Optional[str] = None) -> bool:
        """Clears all messages in the inbox for the specified email."""
        email = email_address or self.current_email
        if not email:
            return False
        try:
            url = f"{self.BASE_URL}/v1/inbox/{requests.utils.quote(email)}"
            res = self.session.delete(url, timeout=10)
            return res.status_code in (200, 204)
        except Exception:
            return False

    def extract_otp_or_code(
        self,
        subject: str = "",
        body_text: str = "",
        body_html: str = ""
    ) -> Optional[str]:
        """
        Multi-strategy OTP, PIN, and verification code extractor.
        Accurately parses 4-8 digit codes, Google 'G-XXXXXX' codes, and token codes.
        """
        # Convert HTML to clean text if available
        html_text = ""
        if body_html:
            try:
                soup = BeautifulSoup(body_html, "html.parser")
                for s in soup(["script", "style", "head", "meta"]):
                    s.decompose()
                html_text = soup.get_text(separator="\n", strip=True)
            except Exception:
                html_text = re.sub(r'<[^>]+>', ' ', body_html)

        combined_text = f"{subject}\n{body_text}\n{html_text}"

        # Strategy 1: Explicit Code/OTP keyword context (Highest confidence)
        explicit_patterns = [
            r'(?:verification|confirmation|security|auth(?:entication)?|login|access|activation|passcode|one-time)?\s*(?:code|otp|pin|código|kod|pin-code)[\s\:\-\n\r]+(?:is\s*)?([0-9]{4,8}|[Gg]\-[0-9]{6}|[0-9]{3,4}[\-\s][0-9]{3,4})\b',
            r'(?:enter|use|with)\s+(?:the\s+)?(?:code\s+)?([0-9]{4,8})\s+(?:to\s+(?:confirm|verify|login|proceed|sign))',
            r'\b(?:code|otp|pin|token)[\s\:\=\n\r]+([0-9]{4,8})\b',
            r'([0-9]{4,8})\s+is\s+your\s+(?:verification|confirmation|security|login|otp|code)',
            r'your\s+(?:verification|confirmation|login|security)?\s*(?:code|otp|pin)\s+is[:\s]+([0-9]{4,8}|[a-zA-Z0-9]{5,8})\b'
        ]

        for pat in explicit_patterns:
            m = re.search(pat, combined_text, re.IGNORECASE)
            if m:
                code = m.group(1).strip()
                return code

        # Strategy 2: Subject line standalone digits (Very common for Google, Meta, Steam, etc.)
        subj_matches = re.findall(r'\b([0-9]{4,8})\b', subject)
        if subj_matches:
            for code in subj_matches:
                if not (code.startswith('202') or code.startswith('203') or code.startswith('0000')):
                    return code

        # Strategy 3: 6-digit standalone numbers in body (Most standard OTP length)
        matches_6 = re.findall(r'\b([0-9]{6})\b', combined_text)
        if matches_6:
            for code in matches_6:
                if not (code.startswith('202') or code.startswith('203') or code.startswith('000000')):
                    return code

        # Strategy 4: 8-digit standalone numbers in body
        matches_8 = re.findall(r'\b([0-9]{8})\b', combined_text)
        if matches_8:
            return matches_8[0]

        # Strategy 5: 4 or 5 digit numbers in body (Filter out calendar years)
        matches_4_5 = re.findall(r'\b([0-9]{4,5})\b', combined_text)
        if matches_4_5:
            for code in matches_4_5:
                if not (code.startswith('202') or code.startswith('203') or code.startswith('199') or code == '2000'):
                    return code

        return None

    def extract_links(self, body_html: str = "", body_text: str = "") -> List[str]:
        """Extracts clickable URLs and verification links from email body."""
        links = []
        if body_html:
            try:
                soup = BeautifulSoup(body_html, "html.parser")
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if href.startswith('http://') or href.startswith('https://'):
                        if href not in links:
                            links.append(href)
            except Exception:
                pass

        if not links and body_text:
            text_links = re.findall(r'https?://[^\s<>"\']+', body_text)
            for link in text_links:
                if link not in links:
                    links.append(link)

        return links

    def parse_and_display_message(self, email_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parses full email payload, prints structured terminal report, and extracts OTPs.
        """
        email_id = email_data.get("id") or email_data.get("_id") or "unknown"
        subject = email_data.get("subject") or "(No Subject)"
        sender = email_data.get("from") or email_data.get("from_address") or "Unknown Sender"
        date_str = email_data.get("date") or email_data.get("created_at") or email_data.get("received_at") or time.strftime("%Y-%m-%d %H:%M:%S")

        # Extract text / html body (mail.cx uses "html_body" and "text_body")
        body_text = (
            email_data.get("text_body")
            or email_data.get("body_text")
            or email_data.get("text")
            or email_data.get("body")
            or ""
        )
        body_html = (
            email_data.get("html_body")
            or email_data.get("body_html")
            or email_data.get("html")
            or ""
        )

        # Clean body text for preview
        clean_text = body_text
        if not clean_text and body_html:
            try:
                soup = BeautifulSoup(body_html, "html.parser")
                for s in soup(["script", "style", "head", "meta"]):
                    s.decompose()
                clean_text = soup.get_text(separator="\n", strip=True)
            except Exception:
                clean_text = re.sub(r'<[^>]+>', ' ', body_html)

        if not clean_text:
            clean_text = email_data.get("intro") or email_data.get("snippet") or ""

        # Extract OTP Code
        extracted_otp = self.extract_otp_or_code(subject, body_text, body_html)

        # Extract verification links
        links = self.extract_links(body_html, body_text)

        # Log formatted output
        log("\n" + "=" * 65)
        log(" 📩 [NEW EMAIL RECEIVED]")
        log("=" * 65)
        log(f"  • ID      : {email_id}")
        log(f"  • FROM    : {sender}")
        log(f"  • SUBJECT : {subject}")
        log(f"  • DATE    : {date_str}")
        log("-" * 65)

        if extracted_otp:
            log(f"\n  ⭐ EXTRACTED CODE / OTP: [ {extracted_otp} ] ⭐\n")
        else:
            log("  [!] No standard numeric OTP detected in subject/body.")

        if links:
            log("  🔗 Action / Verification Links:")
            for idx, link in enumerate(links[:3], 1):
                log(f"     [{idx}] {link}")

        log("-" * 65)
        log("  📄 MESSAGE CONTENT:")
        preview_lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
        preview = "\n  ".join(preview_lines[:20]) if preview_lines else "  (Empty body)"
        log(f"  {preview}")
        if len(preview_lines) > 20:
            log("  ... [truncated remainder of message] ...")
        log("=" * 65 + "\n")

        return {
            "id": email_id,
            "from": sender,
            "subject": subject,
            "date": date_str,
            "body_text": clean_text,
            "otp": extracted_otp,
            "links": links,
            "raw": email_data
        }

    def check_and_read_inbox(self) -> List[Dict[str, Any]]:
        """
        Polls the inbox for any new messages, fetches their full content, and extracts OTPs.
        """
        inbox_items = self.get_inbox(timeout=5)
        new_messages = []

        for item in inbox_items:
            msg_id = item.get("id") or item.get("_id")
            if not msg_id or msg_id in self.processed_message_ids:
                continue

            self.processed_message_ids.add(msg_id)

            # Fetch detailed message content
            detail = self.get_email_detail(msg_id)
            if not detail:
                # If detail fetch fails, use basic inbox item
                detail = item

            parsed = self.parse_and_display_message(detail)
            new_messages.append(parsed)

        return new_messages

    def start_sse_listener(self, email_address: Optional[str] = None, on_email_callback=None):
        """
        Starts a background Server-Sent Events (SSE) listener for instant email alerts.
        """
        email = email_address or self.current_email
        if not email:
            raise ValueError("No email address specified.")

        self.stop_sse_listener()
        self._sse_stop_event.clear()

        def _listener_loop():
            sse_url = f"{self.BASE_URL}/v1/sse/addr?address={requests.utils.quote(email)}"
            self._sse_session = requests.Session()
            self._sse_session.headers.update({
                "User-Agent": self.session.headers["User-Agent"],
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                "Referer": "https://mail.cx/"
            })

            while not self._sse_stop_event.is_set():
                try:
                    with self._sse_session.get(sse_url, stream=True, timeout=90) as response:
                        if response.status_code != 200:
                            time.sleep(3)
                            continue

                        current_event = None
                        for line in response.iter_lines(decode_unicode=True):
                            if self._sse_stop_event.is_set():
                                break

                            if not line:
                                continue

                            if line.startswith("event:"):
                                current_event = line.replace("event:", "").strip()
                            elif line.startswith("data:"):
                                raw_data = line.replace("data:", "", 1).strip()
                                # Real-time email notification received
                                log("\n[*] [REAL-TIME PUSH] Incoming email event triggered!")
                                try:
                                    parsed_data = json.loads(raw_data)
                                    msg_id = parsed_data.get("id") or parsed_data.get("_id")
                                    if msg_id and msg_id not in self.processed_message_ids:
                                        self.processed_message_ids.add(msg_id)
                                        detail = self.get_email_detail(msg_id) or parsed_data
                                        parsed = self.parse_and_display_message(detail)
                                        if on_email_callback:
                                            on_email_callback(parsed)
                                except Exception:
                                    # Fallback to standard inbox check
                                    new_msgs = self.check_and_read_inbox()
                                    if on_email_callback:
                                        for m in new_msgs:
                                            on_email_callback(m)
                                current_event = None
                            elif line.startswith("id:"):
                                if current_event == "email":
                                    new_msgs = self.check_and_read_inbox()
                                    if on_email_callback:
                                        for m in new_msgs:
                                            on_email_callback(m)
                                    current_event = None
                except Exception:
                    if not self._sse_stop_event.is_set():
                        time.sleep(3)

        self._sse_thread = threading.Thread(target=_listener_loop, daemon=True)
        self._sse_thread.start()

    def stop_sse_listener(self):
        """Stops the active background SSE stream listener."""
        self._sse_stop_event.set()
        if self._sse_session:
            try:
                self._sse_session.close()
            except Exception:
                pass
        self._sse_thread = None

    def wait_for_otp(
        self,
        email_address: Optional[str] = None,
        timeout: int = 120,
        poll_interval: int = 3
    ) -> Optional[str]:
        """
        Waits for a new email to arrive and returns the extracted OTP/Verification code.
        Ideal for automation scripts, bots, and account signup flows.
        """
        email = email_address or self.current_email
        if not email:
            raise ValueError("No email address specified.")

        log(f"[*] Monitoring inbox for [{email}] (Timeout: {timeout}s)...")
        log("[*] Waiting for incoming verification email & OTP...")

        # Start background SSE push listener
        self.start_sse_listener(email)

        start_time = time.time()
        try:
            while time.time() - start_time < timeout:
                # Poll inbox as secondary fallback
                messages = self.check_and_read_inbox()
                for msg in messages:
                    if msg.get("otp"):
                        return msg["otp"]

                time.sleep(poll_interval)

            log(f"[-] Timeout reached ({timeout}s). No OTP received.")
            return None
        finally:
            self.stop_sse_listener()

    def wait_for_message(
        self,
        email_address: Optional[str] = None,
        timeout: int = 120,
        poll_interval: int = 3
    ) -> Optional[Dict[str, Any]]:
        """
        Waits for any new message to arrive and returns the parsed message dictionary.
        """
        email = email_address or self.current_email
        if not email:
            raise ValueError("No email address specified.")

        log(f"[*] Waiting for message in [{email}] (Timeout: {timeout}s)...")
        self.start_sse_listener(email)

        start_time = time.time()
        try:
            while time.time() - start_time < timeout:
                messages = self.check_and_read_inbox()
                if messages:
                    return messages[0]
                time.sleep(poll_interval)
            return None
        finally:
            self.stop_sse_listener()


def run_interactive():
    """Interactive CLI runner for mail.cx"""
    client = MailCxAPI()

    log("=" * 65)
    log("       MAIL.CX TEMPORARY EMAIL & OTP EXTRACTOR")
    log("=" * 65)
    log(f"[*] Loaded {len(client.available_domains)} System Domains: {', '.join(client.available_domains)}")

    # Generate first random email across domains
    email = client.generate_random_email()
    log("\n" + "=" * 65)
    log(f" [+] ACTIVE TEMP EMAIL : {email}")
    log("=" * 65)

    # Start real-time push listener
    client.start_sse_listener(email)
    log("[*] Real-time SSE listener active. Send an email to this address anytime!\n")

    try:
        while True:
            log("\n[OPTIONS]:")
            log("  [1] Generate NEW email with Random Domain")
            log("  [2] Select specific domain for new email")
            log("  [3] Check Inbox manually now")
            log("  [4] Wait & extract OTP (Automated 120s loop)")
            log("  [5] Clear Inbox")
            log("  [6] Exit")

            choice = input("\nEnter your choice (1-6): ").strip()

            if choice == "1":
                email = client.generate_random_email()
                log("\n" + "=" * 65)
                log(f" [+] NEW TEMP EMAIL (Random Domain): {email}")
                log("=" * 65)
                client.start_sse_listener(email)

            elif choice == "2":
                log("\nAvailable Domains:")
                for idx, dom in enumerate(client.available_domains, 1):
                    log(f"  [{idx}] {dom}")
                dom_idx = input(f"Select domain (1-{len(client.available_domains)}): ").strip()
                try:
                    selected_dom = client.available_domains[int(dom_idx) - 1]
                    email = client.generate_random_email(domain=selected_dom)
                    log("\n" + "=" * 65)
                    log(f" [+] NEW TEMP EMAIL ({selected_dom}): {email}")
                    log("=" * 65)
                    client.start_sse_listener(email)
                except Exception:
                    log("[-] Invalid selection.")

            elif choice == "3":
                log(f"\n[*] Checking inbox for {client.current_email}...")
                msgs = client.check_and_read_inbox()
                if not msgs:
                    log("[i] Inbox is currently empty (0 unread messages).")

            elif choice == "4":
                log(f"\n[*] Waiting for OTP on {client.current_email}...")
                otp = client.wait_for_otp(timeout=120)
                if otp:
                    log(f"\n🎉 SUCCESS: OTP Code Received -> [ {otp} ]\n")

            elif choice == "5":
                if client.clear_inbox():
                    log("[+] Inbox cleared successfully.")
                else:
                    log("[-] Failed to clear inbox or inbox already empty.")

            elif choice == "6":
                log("[*] Exiting. Goodbye!")
                break

            else:
                log("[-] Invalid option. Please choose 1 to 6.")

    except KeyboardInterrupt:
        log("\n[*] Interrupted by user. Exiting...")
    finally:
        client.stop_sse_listener()


def extract_otp_from_mail_cx_text(text: str, subj: str = "", mail_from: str = "", expected_digits: Optional[int] = None) -> Optional[str]:
    """
    Extract OTP / Verification code from email text or subject with optional digit length filtering.
    Optimized for Instagram, Meta AI, Facebook, and standard OTP formats.
    """
    full_text = f"{subj} {text}".strip()
    if not full_text:
        return None

    # Clean HTML tags and HTML entities
    clean_text = re.sub(r'<[^>]+>', ' ', full_text)
    clean_text = re.sub(r'&[a-z0-9#]+;', ' ', clean_text)
    clean_text = ' '.join(clean_text.split())

    if expected_digits == 8:
        # Strict 8-digit patterns for Instagram / Meta 2FA security codes
        patterns_8 = [
            r'(?:security\s+code|confirmation\s+code|confirm\s+code|your\s+code|code\s+is|is\s+your\s+security\s+code|is\s+your\s+instagram\s+code)[\s:\n]*\b(\d{8})\b',
            r'\b(\d{8})\b[\s:\n]*(?:is\s+your\s+code|is\s+your\s+confirmation|is\s+your\s+security\s+code|is\s+your\s+instagram\s+code)',
            r'(?:FB-|Meta\s*Code:?|Instagram\s*Code:?\s*)(\b\d{8}\b)',
            r'(?:confirmation|verification|security)?\s*code[^\d]*(\d{8})',
            r'\b(\d{8})\b',
        ]
        for pat in patterns_8:
            m = re.search(pat, clean_text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    elif expected_digits == 6:
        # Strict 6-digit patterns for Meta AI registration codes
        patterns_6 = [
            r'(?:confirmation\s+code|security\s+code|confirm\s+code|your\s+code|code\s+is)[\s:\n]*\b(\d{6})\b',
            r'\b(\d{6})\b[\s:\n]*(?:is\s+your\s+code|is\s+your\s+confirmation)',
            r'(?:FB-|Meta\s*Code:?\s*)(\b\d{6}\b)',
            r'(?:confirmation|verification|security)?\s*code[^\d]*(\d{6})',
            r'\b(\d{6})\b',
        ]
        for pat in patterns_6:
            m = re.search(pat, clean_text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    else:
        # Generic: Prefer 8-digit codes first, then 6-digit codes
        patterns = [
            r'(?:security\s+code|confirmation\s+code|confirm\s+code|your\s+code|code\s+is)[\s:\n]*\b(\d{8})\b',
            r'\b(\d{8})\b[\s:\n]*(?:is\s+your\s+code|is\s+your\s+confirmation|is\s+your\s+security\s+code)',
            r'(?:confirmation|verification|security)?\s*code[^\d]*(\d{8})',
            r'\b(\d{8})\b',
            r'(?:confirmation\s+code|security\s+code|confirm\s+code|your\s+code|code\s+is)[\s:\n]*\b(\d{6})\b',
            r'\b(\d{6})\b[\s:\n]*(?:is\s+your\s+code|is\s+your\s+confirmation)',
            r'(?:FB-|Meta\s*Code:?\s*)(\b\d{6}\b)',
            r'(?:confirmation|verification|security)?\s*code[^\d]*(\d{6})',
            r'\b(\d{6})\b',
        ]
        for pat in patterns:
            m = re.search(pat, clean_text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None


def generate_mail_cx_account(email_prefix: str = "", password: str = "", instance_name: str = "") -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Creates/generates a Mail.cx email address and returns (email, password, token)
    Token format: MAILCX:{email}
    """
    client = MailCxAPI()
    try:
        domains = client.available_domains or client.refresh_domains()
        domain = random.choice(domains) if domains else "uqu.me"
        prefix = email_prefix.strip() if email_prefix else ""
        if prefix:
            clean_prefix = re.sub(r'[^a-zA-Z0-9]', '', prefix.lower())
            email = client.generate_random_email(domain=domain, username=clean_prefix)
        else:
            email = client.generate_random_email(domain=domain)

        token_str = f"MAILCX:{email}"
        log_msg = f"Created Mail.cx Address -> {email}"
        inst_tag = f"[{instance_name:^6}]" if instance_name else "[  MAIN  ]"
        print(f"{inst_tag} [MAILCX-GEN   ] {log_msg}", flush=True)
        return email, password, token_str
    except Exception as e:
        err_msg = f"Failed generating Mail.cx account: {e}"
        inst_tag = f"[{instance_name:^6}]" if instance_name else "[  MAIN  ]"
        print(f"{inst_tag} [MAILCX-ERR   ] {err_msg}", flush=True)
        return None, None, None


def fetch_otp_mail_cx(token: str, max_wait_sec: int = 60, expected_digits: Optional[int] = None, instance_name: str = "") -> Optional[str]:
    """
    Polls Mail.cx inbox for the verification code.
    Supports expected_digits (e.g. 8 for 2FA, 6 for Meta AI registration).
    Token format can be 'MAILCX:user@domain.com' or plain 'user@domain.com'.
    """
    email = token
    if email.startswith("MAILCX:"):
        email = email.split(":", 1)[1]

    if not email:
        return None

    client = MailCxAPI()
    client.current_email = email

    inst_tag = f"[{instance_name:^6}]" if instance_name else "[  MAIN  ]"
    digit_info = f" ({expected_digits}-digit expected)" if expected_digits else ""
    print(f"{inst_tag} [MAILCX-OTP   ] Polling Mail.cx inbox for {email}{digit_info} (timeout {max_wait_sec}s)...", flush=True)

    start_time = time.time()
    poll_interval = 3

    while time.time() - start_time < max_wait_sec:
        try:
            inbox_items = client.get_inbox(email_address=email, timeout=5)
            if inbox_items and isinstance(inbox_items, list):
                for item in inbox_items:
                    msg_id = item.get("id") or item.get("_id")
                    detail = None
                    if msg_id:
                        detail = client.get_email_detail(msg_id)
                    if not detail:
                        detail = item

                    subject = str(detail.get("subject") or "")
                    sender = str(detail.get("from") or detail.get("from_address") or "")
                    body_text = str(detail.get("text_body") or detail.get("body_text") or detail.get("text") or detail.get("body") or "")
                    body_html = str(detail.get("html_body") or detail.get("body_html") or detail.get("html") or "")

                    parsed_html = ""
                    if body_html:
                        try:
                            soup = BeautifulSoup(body_html, "html.parser")
                            parsed_html = soup.get_text(separator=" ", strip=True)
                        except Exception:
                            parsed_html = re.sub(r'<[^>]+>', ' ', body_html)

                    combined_text = f"{subject}\n{body_text}\n{parsed_html}\n{detail.get('preview', '')}\n{detail.get('intro', '')}"

                    otp = extract_otp_from_mail_cx_text(combined_text, subj=subject, mail_from=sender, expected_digits=expected_digits)
                    if otp:
                        otp_type = f"{len(otp)}-digit 2FA Security Code" if len(otp) == 8 else f"{len(otp)}-digit OTP"
                        print(f"{inst_tag} [OTP-SUCCESS   ] Found {otp_type} (Mail.cx): {otp}", flush=True)
                        return otp
        except Exception as e:
            print(f"{inst_tag} [MAILCX-WARN  ] Mail.cx poll error: {e}", flush=True)

        time.sleep(poll_interval)

    print(f"{inst_tag} [OTP-TIMEOUT  ] Timed out waiting for Mail.cx OTP for {email}.", flush=True)
    return None


if __name__ == "__main__":
    run_interactive()

