import requests
import hashlib
import time
import secrets
import string
import json
import re
import sys
import random
from html.parser import HTMLParser
from typing import Optional, List, Dict, Any

try:
    from argon2.low_level import hash_secret_raw, Type
    HAS_ARGON2 = True
except ImportError:
    HAS_ARGON2 = False

BASE_URL = "https://mail.td/api"
WS_URL = "wss://mail.td/api/ws"

def log(tag, message, instance_name=""):
    """Clean & optimized console logger"""
    timestamp = time.strftime("%H:%M:%S")
    inst_tag = f"[{instance_name:^6}]" if instance_name else "[  MAIN  ]"
    tag_padded = f"[{tag:<14}]"
    log_str = f"[{timestamp}] {inst_tag} {tag_padded} {message}"
    try:
        print(log_str, flush=True)
    except UnicodeEncodeError:
        safe_str = log_str.encode(sys.stdout.encoding or 'ascii', errors='replace').decode(sys.stdout.encoding or 'ascii')
        print(safe_str, flush=True)

class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.links = []
        self._current_href = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            for attr, val in attrs:
                if attr.lower() == 'href' and val and val.startswith(('http://', 'https://')):
                    self._current_href = val
                    if val not in self.links:
                        self.links.append(val)

    def handle_data(self, data):
        cleaned = data.strip()
        if cleaned:
            self.text_parts.append(cleaned)

    def get_text(self) -> str:
        return "\n".join(self.text_parts)


class MailTDClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.session = requests.Session()
        self.account_id: Optional[str] = None
        self.address: Optional[str] = None
        self.token: Optional[str] = None

    @staticmethod
    def generate_random_prefix(length: int = 6) -> str:
        chars = string.ascii_lowercase + string.digits
        return ''.join(secrets.choice(chars) for _ in range(length))

    @staticmethod
    def generate_random_password(length: int = 8) -> str:
        chars = string.ascii_letters + string.digits + "!@#$%"
        return ''.join(secrets.choice(chars) for _ in range(length))

    @staticmethod
    def derive_auth_key(address: str, password: str) -> str:
        if not HAS_ARGON2:
            raise ImportError("argon2-cffi package is required for Mail.td PoW auth key derivation.")
        normalized_address = address.lower().strip()
        salt = hashlib.sha256(normalized_address.encode('utf-8')).digest()
        key = hash_secret_raw(
            secret=password.encode('utf-8'),
            salt=salt,
            time_cost=3,
            memory_cost=16384,
            parallelism=1,
            hash_len=32,
            type=Type.ID
        )
        return key.hex()

    @staticmethod
    def solve_pow(address: str, timestamp: int, difficulty: int) -> str:
        prefix = (address + str(timestamp)).encode('utf-8')
        nonce = 0
        num_bytes = difficulty // 8
        rem_bits = difficulty % 8
        mask = (0xFF << (8 - rem_bits)) & 0xFF if rem_bits > 0 else 0
        
        while True:
            data = prefix + str(nonce).encode('utf-8')
            h = hashlib.sha256(data).digest()
            ok = True
            for b in h[:num_bytes]:
                if b != 0:
                    ok = False
                    break
            if ok and rem_bits > 0:
                if (h[num_bytes] & mask) != 0:
                    ok = False
            if ok:
                return str(nonce)
            nonce += 1

    @staticmethod
    def get_domains() -> List[str]:
        try:
            res = requests.get(f"{BASE_URL}/domains", timeout=8)
            res.raise_for_status()
            data = res.json()
            domains_list = data.get("domains", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            valid_domains = []
            for d in domains_list:
                if isinstance(d, dict) and "domain" in d:
                    valid_domains.append(d["domain"])
                elif isinstance(d, str):
                    valid_domains.append(d)
            return valid_domains or ["qabq.com", "mail.td"]
        except Exception:
            return ["qabq.com", "mail.td"]

    def create_account(self, address: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
        """
        Creates a new temporary email account on mail.td
        """
        if not address:
            domains = self.get_domains()
            domain = random.choice(domains) if domains else "qabq.com"
            prefix = self.generate_random_prefix()
            address = f"{prefix}@{domain}"
        
        if not password:
            password = self.generate_random_password()

        auth_key = self.derive_auth_key(address, password)

        difficulty = 15
        pow_token = None
        max_attempts = 4

        for _ in range(max_attempts):
            timestamp = int(time.time())
            nonce = self.solve_pow(address, timestamp, difficulty)

            pow_data = {
                "t": timestamp,
                "n": nonce,
                "d": difficulty
            }
            if pow_token:
                pow_data["token"] = pow_token

            payload = {
                "address": address,
                "auth_key": auth_key,
                "pow": pow_data
            }

            res = self.session.post(f"{self.base_url}/accounts", json=payload, timeout=12)
            data = res.json()

            if data.get("status") == "retry":
                difficulty = data.get("required_difficulty", difficulty)
                pow_token = data.get("token")
                continue

            res.raise_for_status()

            self.account_id = data["id"]
            self.address = data["address"]
            self.token = data["token"]

            return {
                "id": self.account_id,
                "address": self.address,
                "token": self.token,
                "password": password
            }
        
        raise RuntimeError("Exhausted POW retries during account creation.")

    def login(self, address: str, password: str) -> Dict[str, Any]:
        """
        Log into an existing mail.td account
        """
        auth_key = self.derive_auth_key(address, password)
        payload = {
            "address": address,
            "auth_key": auth_key
        }
        res = self.session.post(f"{self.base_url}/token", json=payload, timeout=12)
        res.raise_for_status()
        data = res.json()

        self.account_id = data["id"]
        self.address = data["address"]
        self.token = data["token"]

        return data

    def _headers(self) -> Dict[str, str]:
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get_messages(self, page: int = 1) -> List[Dict[str, Any]]:
        """
        Get message list for current account
        """
        if not self.account_id:
            raise RuntimeError("Account not created or logged in.")
        
        res = self.session.get(f"{self.base_url}/accounts/{self.account_id}/messages?page={page}", headers=self._headers(), timeout=10)
        res.raise_for_status()
        data = res.json()
        if isinstance(data, dict):
            return data.get("messages", [])
        return data

    def get_message(self, message_id: str) -> Dict[str, Any]:
        """
        Fetch full details of a specific message
        """
        if not self.account_id:
            raise RuntimeError("Account not created or logged in.")
        
        res = self.session.get(f"{self.base_url}/accounts/{self.account_id}/messages/{message_id}", headers=self._headers(), timeout=10)
        res.raise_for_status()
        return res.json()


def extract_otp_from_mail_td_text(body_text: str, subj: str = "", mail_from: str = "", expected_digits: Optional[int] = None) -> Optional[str]:
    """
    Extracts verification / security code from email body/subject.
    If expected_digits is specified (e.g. 8 for 2FA security code, 6 for Meta AI),
    strictly matches codes with that exact digit count.
    """
    full_text = f"{subj} {body_text}".strip()
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


def generate_mail_td_account(email_prefix: str = "", password: str = "", instance_name: str = "") -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Creates a new mail.td account and returns (email, password, token)
    Token format: MAILTD:{account_id}:{token}
    """
    client = MailTDClient()
    try:
        domains = client.get_domains()
        domain = random.choice(domains) if domains else "qabq.com"
        prefix = email_prefix.strip() if email_prefix else client.generate_random_prefix(8)
        address = f"{prefix}@{domain}"
        
        acc = client.create_account(address=address, password=password)
        email = acc.get("address")
        pwd = acc.get("password")
        token_str = f"MAILTD:{acc.get('id')}:{acc.get('token')}"
        log("MAILTD-GEN", f"Created Mail.td Account -> {email}", instance_name=instance_name)
        return email, pwd, token_str
    except Exception as e:
        log("MAILTD-ERR", f"Failed registering Mail.td account: {e}", instance_name=instance_name)
        return None, None, None


def fetch_otp_mail_td(token: str, max_wait_sec: int = 60, expected_digits: Optional[int] = None, instance_name: str = "") -> Optional[str]:
    """
    Polls Mail.td inbox for the verification code using the client.
    Guarantees that the newest/latest message is checked first.
    If expected_digits is specified (e.g. 8 for 2FA), only matching codes will be returned.
    """
    parts = token.split(":", 2) if token and token.startswith("MAILTD:") else []
    acc_id = parts[1] if len(parts) > 1 else ""
    jwt_token = parts[2] if len(parts) > 2 else ""

    if not acc_id or not jwt_token:
        log("MAILTD-WARN", "Invalid Mail.td token format for OTP fetching", instance_name=instance_name)
        return None

    client = MailTDClient()
    client.account_id = acc_id
    client.token = jwt_token

    start_time = time.time()
    poll_interval = 3

    while time.time() - start_time < max_wait_sec:
        try:
            msgs = client.get_messages()
            if msgs and isinstance(msgs, list):
                # Ensure the latest message is checked first
                def _sort_key(m):
                    if isinstance(m, dict):
                        for k in ("created_at", "createdAt", "timestamp", "id"):
                            v = m.get(k)
                            if v is not None:
                                try:
                                    return int(v)
                                except (ValueError, TypeError):
                                    return str(v)
                    return 0

                try:
                    sorted_msgs = sorted(msgs, key=_sort_key, reverse=True)
                except Exception:
                    sorted_msgs = list(reversed(msgs))

                for summary in sorted_msgs:
                    msg_id = summary.get("id")
                    subj = str(summary.get("subject", ""))
                    from_addr = str(summary.get("from") or summary.get("sender", ""))

                    # Check subject first
                    otp = extract_otp_from_mail_td_text("", subj=subj, mail_from=from_addr, expected_digits=expected_digits)
                    if otp:
                        otp_type = f"{len(otp)}-digit 2FA Security Code" if len(otp) == 8 else f"{len(otp)}-digit OTP"
                        log("OTP-SUCCESS", f"Found {otp_type} (Subject): {otp}", instance_name=instance_name)
                        return otp

                    if msg_id:
                        full_msg = client.get_message(msg_id)
                        text_body = full_msg.get("text_body") or ""
                        html_body = full_msg.get("html_body") or ""

                        parsed_text = ""
                        if html_body:
                            parser = HTMLTextExtractor()
                            try:
                                parser.feed(html_body)
                                parsed_text = parser.get_text()
                            except Exception:
                                pass

                        body_to_check = f"{text_body}\n{parsed_text}\n{summary.get('preview_text', '')}"
                        otp = extract_otp_from_mail_td_text(body_to_check, subj=subj, mail_from=from_addr, expected_digits=expected_digits)
                        if otp:
                            otp_type = f"{len(otp)}-digit 2FA Security Code" if len(otp) == 8 else f"{len(otp)}-digit OTP"
                            log("OTP-SUCCESS", f"Found {otp_type}: {otp}", instance_name=instance_name)
                            return otp
        except Exception as e:
            log("OTP-WARN", f"Mail.td poll error: {e}", instance_name=instance_name)

        time.sleep(poll_interval)

    log("OTP-TIMEOUT", f"Timed out waiting for Mail.td OTP.", instance_name=instance_name)
    return None
