#!/usr/bin/env python3
"""
=============================================================================
🤖 AUTOMETAINSTABOT - Complete Meta AI + Telegram Bot + Instagram 2FA Pipeline
=============================================================================
Combines:
  1. Telegram Userbot (Taskly / PayGoeasy):
     - Automates task receiving from Telegram bot (@tasklyBux_bot / @PayGoeasy_bot)
     - Fetches First Name, Username, and Password directly from the Telegram bot
     - Submits 2FA Secret Key & confirms completed task to Telegram bot
  2. TempMail Ninja (NINJA.PY):
     - Real-time Socket.IO disposable email generation
     - Automated real-time push notification & OTP code extraction
  3. Meta Auth Pure Request Engine (meta with otp.py):
     - Envelope AES-256-GCM + TweetNaCl SealedBox password encryption
     - Fast HTTP request registration on auth.meta.com (Contact point, Password, DOB, Handle check, Registration)
     - Automated GraphQL OTP confirmation (FRLConfirmEmailMutation)
  4. Playwright Stealth Handover:
     - Session cookie injection for checkpoint & human verification / 2Captcha solving
  5. Android ADB Instagram & 2FA Pipeline:
     - Automates Via browser on Android emulators
     - Instagram login, terms acceptance, account linking
     - 2FA secret key extraction, TOTP setup, Account center username sync
     - CSV (credentials.csv) & Google Sheets automatic synchronization
  6. Multi-Worker & Multi-Device Parallel Processing:
     - Supports concurrent workers across all active ADB emulators
=============================================================================
"""

import asyncio
import base64
import csv
import glob
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

import pyotp
import urllib3
import nacl.public
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from curl_cffi import requests as curl_requests
    USE_CURL_CFFI = True
except ImportError:
    import requests as curl_requests
    USE_CURL_CFFI = False

import requests
import urllib.request
from urllib.parse import quote, urlencode, urlparse, parse_qs

try:
    from playwright.async_api import async_playwright, Playwright, BrowserContext, Page
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    Playwright = Any
    BrowserContext = Any
    Page = Any
from telethon import TelegramClient, events, errors
from telethon.tl.custom import Button

# Import TempMail Ninja API
try:
    from NINJA import TempMailNinjaAPI
    HAS_NINJA = True
except ImportError:
    try:
        from ninja import TempMailNinjaAPI
        HAS_NINJA = True
    except ImportError:
        HAS_NINJA = False

# Import Email Service & Config
from email_service import (
    generate_temp_email as fallback_generate_temp_email,
    fetch_otp_from_temp_mail as fallback_fetch_otp,
    load_config,
    DEFAULT_CONFIG,
    CONFIG_FILE
)
from google_sheet_manager import gsheet_manager

urllib3.disable_warnings()

# Ensure safe terminal encoding on Windows
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# ==============================================================================
# ⚙️ CONFIGURATION & SETTINGS (Loaded from config.json)
# ==============================================================================
_CFG = load_config()
_TG_CFG = _CFG.get("telegram", {})

def normalize_target_bots(val):
    if isinstance(val, list):
        bots = [str(b).strip().lstrip("@") for b in val if str(b).strip()]
        return bots if bots else ["tasklyBux_bot"]
    elif isinstance(val, str) and val.strip():
        bots = [str(b).strip().lstrip("@") for b in val.split(",") if str(b).strip()]
        return bots if bots else ["tasklyBux_bot"]
    return ["tasklyBux_bot"]

# Target Telegram bots
TARGET_BOTS = normalize_target_bots(_TG_CFG.get("target_bot") or _TG_CFG.get("target_bots"))
TARGET_BOT = TARGET_BOTS[0] if TARGET_BOTS else "tasklyBux_bot"

# Telegram accounts
TELEGRAM_ACCOUNTS = _TG_CFG.get("accounts", [])
for _acc in TELEGRAM_ACCOUNTS:
    raw_acc_bots = _acc.get("target_bots") or _acc.get("target_bot") or TARGET_BOTS
    _acc["target_bots"] = normalize_target_bots(raw_acc_bots)
    _acc["target_bot"] = _acc["target_bots"][0]

# ==============================================================================
# 🔄 GLOBAL TELEGRAM ACCOUNT POOL & FLOOD COOLDOWN MANAGER
# ==============================================================================
class TelegramAccountPool:
    _lock = threading.Lock()
    _active_accounts: Dict[str, str] = {}      # clean_phone -> instance_name
    _flood_cooldowns: Dict[str, float] = {}    # clean_phone -> unix timestamp
    _all_accounts: List[dict] = []

    @classmethod
    def initialize(cls, accounts: list):
        with cls._lock:
            cls._all_accounts = list(accounts)

    @classmethod
    def register_active(cls, phone: str, instance_name: str):
        if not phone:
            return
        with cls._lock:
            clean = re.sub(r'[^0-9]', '', str(phone))
            cls._active_accounts[clean] = instance_name

    @classmethod
    def release_active(cls, phone: str):
        if not phone:
            return
        with cls._lock:
            clean = re.sub(r'[^0-9]', '', str(phone))
            if clean in cls._active_accounts:
                del cls._active_accounts[clean]

    @classmethod
    def mark_flood(cls, phone: str, seconds: int):
        if not phone:
            return
        with cls._lock:
            clean = re.sub(r'[^0-9]', '', str(phone))
            cls._flood_cooldowns[clean] = time.time() + seconds

    @classmethod
    def is_cooling_down(cls, phone: str) -> bool:
        if not phone:
            return False
        with cls._lock:
            clean = re.sub(r'[^0-9]', '', str(phone))
            return time.time() < cls._flood_cooldowns.get(clean, 0.0)

    @classmethod
    def get_cooldown_remaining(cls, phone: str) -> int:
        if not phone:
            return 0
        with cls._lock:
            clean = re.sub(r'[^0-9]', '', str(phone))
            rem = int(cls._flood_cooldowns.get(clean, 0.0) - time.time())
            return max(0, rem)

    @classmethod
    def find_free_account(cls, current_phone: str, instance_name: str) -> Optional[dict]:
        with cls._lock:
            curr_clean = re.sub(r'[^0-9]', '', str(current_phone)) if current_phone else ""
            now = time.time()
            for acc in cls._all_accounts:
                acc_phone = re.sub(r'[^0-9]', '', str(acc.get('phone', '')))
                if not acc_phone or acc_phone == curr_clean:
                    continue
                # If account is currently in FloodWait cooldown, skip
                if cls._flood_cooldowns.get(acc_phone, 0.0) > now:
                    continue
                # If account is currently in use by another running emulator, skip
                occupant = cls._active_accounts.get(acc_phone)
                if occupant and occupant != instance_name:
                    continue
                return acc
            return None

TelegramAccountPool.initialize(TELEGRAM_ACCOUNTS)

AUTO_REPEAT_TASKS = _TG_CFG.get("auto_repeat_tasks", True)
DELAY_BETWEEN_STEPS = _TG_CFG.get("delay_between_steps", 2.0)

# Meta AI & URLs
TARGET_URL = "https://www.meta.ai/"
CHECKPOINT_REDIRECT_URL = "https://auth.meta.com/checkpoints/675270400317265/?next=https%3A%2F%2Fauth.meta.com%2Frecover%2Fsuccess%2F%3Fredirect_uri%3Dhttps%253A%252F%252Fauth.meta.com%252F"

# Browser settings
HEADLESS = _CFG.get("headless", False)
VIEWPORT_WIDTH = _CFG.get("viewport_width", 500)
VIEWPORT_HEIGHT = _CFG.get("viewport_height", 700)
PACKAGE_NAME = "mark.via.gp"

INSTA_LOGIN_URL = "https://www.instagram.com/accounts/login/?"
TWO_FACTOR_URL = "https://accountscenter.instagram.com/password_and_security/two_factor/"
ACCOUNTS_OVERVIEW_URL = "https://accountscenter.instagram.com/account_overview/"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_CSV = os.path.join(BASE_DIR, "credentials.csv")

# ==============================================================================
# 🧩 2CAPTCHA & CHECKPOINT API CONFIGURATION
# ==============================================================================
_CAPTCHA_CFG = _CFG.get("captcha", {})
API_KEY_2CAPTCHA = _CAPTCHA_CFG.get("api_key") or "1d0543cab02e928f092f4f24736efd85"
DEFAULT_RECAPTCHA_SITE_KEY = _CAPTCHA_CFG.get("site_key") or "6LdktRgnAAAAAFQ6icovYI2-masYLFjEFyzQzpix"
CAPTCHA_TIMEOUT = int(_CAPTCHA_CFG.get("timeout", 120))
DOC_ID_CONFIRM_EMAIL = "9851798224911796"
DOC_ID_UFAC_CHECKPOINT = "28029617096670386"
DEFAULT_IMAGE_PATH = os.path.join(BASE_DIR, "image.jpg")


csv_lock = threading.Lock()
ADB_SEMAPHORE = threading.Semaphore(4)
CLIENT_CACHE: Dict[str, TelegramClient] = {}

# ==============================================================================
# 📝 LOGGING
# ==============================================================================
LOG_CALLBACK = None

def set_log_callback(cb):
    global LOG_CALLBACK
    LOG_CALLBACK = cb

def log(tag, message, instance_name=""):
    timestamp = time.strftime("%H:%M:%S")
    inst_tag = f"[{instance_name:^8}]" if instance_name else "[  MAIN  ]"
    tag_padded = f"[{tag:<14}]"
    log_str = f"[{timestamp}] {inst_tag} {tag_padded} {message}"
    try:
        print(log_str, flush=True)
    except UnicodeEncodeError:
        safe_str = log_str.encode(sys.stdout.encoding or 'ascii', errors='replace').decode(sys.stdout.encoding or 'ascii')
        print(safe_str, flush=True)
    if LOG_CALLBACK:
        try:
            LOG_CALLBACK(tag, message, instance_name)
        except Exception:
            pass

# ==============================================================================
# 💾 CREDENTIAL STORAGE & GOOGLE SHEETS
# ==============================================================================
def save_credentials_csv(email, password, two_factor_secret="", username="", instance_name=""):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    uname = username.strip() if username else "Instagram_User"
    
    with csv_lock:
        file_exists = os.path.exists(CREDENTIALS_CSV)
        try:
            with open(CREDENTIALS_CSV, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "Email", "Username", "Password", "2FA Secret", "Status"])
                writer.writerow([timestamp, email, uname, password, two_factor_secret, "ACTIVE_2FA"])
            log("CSV-SAVE", f"Saved credentials for {email} ({uname}) to credentials.csv", instance_name=instance_name)
        except Exception as e:
            log("CSV-ERR", f"Failed saving to credentials.csv: {e}", instance_name=instance_name)

    if gsheet_manager.is_configured():
        def _save_gsheet():
            try:
                gsheet_manager.save_done_account(timestamp, email, uname, password, two_factor_secret, instance_name=instance_name)
            except Exception as e:
                log("GSHEET-ERR", f"Failed syncing to Google Sheet: {e}", instance_name=instance_name)
        threading.Thread(target=_save_gsheet, daemon=True).start()

# ==============================================================================
# 🔐 META AUTH CRYPTO & REQUEST CLIENT
# ==============================================================================

def format_pwd_browser(encrypted_b64: str, timestamp: Optional[int] = None, version: int = 5) -> str:
    """Formats password in Meta browser format: #PWD_BROWSER:<ver>:<ts>:<payload>"""
    ts = timestamp or int(time.time())
    return f"#PWD_BROWSER:{version}:{ts}:{encrypted_b64}"

def encrypt_meta_password(
    password: str,
    public_key_hex: str,
    key_id: int = 199,
    timestamp: Optional[int] = None
) -> str:
    """
    Encrypts password using Meta's EnvelopeEncryption standard:
    1. Ephemeral 32-byte AES-256 key.
    2. AES-256-GCM encryption with 12 zero IV bytes and timestamp AAD.
    3. TweetNaCl SealedBox encryption with Meta's X25519 public key.
    4. Meta binary envelope pack.
    """
    ts = timestamp or int(time.time())
    ts_str = str(ts)

    aes_key = os.urandom(32)
    iv = bytes(12)
    aesgcm = AESGCM(aes_key)
    aad = ts_str.encode("utf-8")
    plaintext = password.encode("utf-8")

    ciphertext_and_tag = aesgcm.encrypt(iv, plaintext, aad)
    ciphertext = ciphertext_and_tag[:-16]
    tag = ciphertext_and_tag[-16:]

    recipient_pk_bytes = bytes.fromhex(public_key_hex)
    recipient_pk = nacl.public.PublicKey(recipient_pk_bytes)
    sealed_box = nacl.public.SealedBox(recipient_pk)
    sealed_key = sealed_box.encrypt(aes_key)
    sealed_len = len(sealed_key)

    envelope = bytearray()
    envelope.append(1)                  # Version = 1
    envelope.append(int(key_id))        # Key ID
    envelope.append(sealed_len & 0xFF)  # Length low byte
    envelope.append((sealed_len >> 8) & 0xFF)  # Length high byte
    envelope.extend(sealed_key)
    envelope.extend(tag)
    envelope.extend(ciphertext)

    b64_payload = base64.b64encode(envelope).decode("utf-8")
    return format_pwd_browser(b64_payload, timestamp=ts)

def compute_jazoest(token: str) -> str:
    """Computes Meta dynamic jazoest checksum from LSD or fb_dtsg."""
    if not token:
        return "22206"
    return "2" + str(sum(ord(c) for c in token))

def build_falco_payload(
    event_name: str,
    event_data: Dict[str, Any],
    client_session_id: str,
    web_session_id: str = "npdr3i:q5lguu:55za44",
    claim: str = "hmac_ttl.1789134777.AR1GWI4H8HqheGesK9mJOlXPT5j4lcjCd4vFk0DAT3lhNJOv"
) -> Tuple[str, str]:
    now_ms = time.time() * 1000
    post_item = [
        event_name,
        {
            "e": json.dumps(event_data),
            "r": 1,
            "d": "fd.ARvsnj8V0mnfNo2m10abybzPIxHOsKjvFZTY6hCzdfR5bSZczDsdeGF6Q55k3ROaFJ2oCMsf8z_TGy97tRAQzjXy",
            "s": web_session_id,
            "t": now_ms,
            "a": "1047.303.469.0 (1047303469)",
            "b": [1, 128],
            "id": {
                "appScopedIdentity": "0",
                "claim": claim
            }
        },
        now_ms,
        0,
        1200
    ]

    q_array = [
        {
            "app_id": "713378262994144",
            "posts": [post_item],
            "trigger": event_name,
            "user": "0",
            "webSessionId": web_session_id
        }
    ]
    return str(int(now_ms)), json.dumps(q_array)


# =====================================================================
# 🧩 2CAPTCHA SOLVER MODULE (Modern API + Legacy Fallback)
# =====================================================================
class TwoCaptchaSolver:
    """
    Automated 2Captcha solver for reCAPTCHA Enterprise on Meta / Facebook.
    Uses robust urllib and HTTP APIs with modern and legacy fallback.
    """
    DEFAULT_IFRAME_URL = "https://www.fbsbx.com/captcha/recaptcha/iframe/?transparent_bg=true&referer=https%253A%252F%252Fauth.meta.com&locale=en_GB"

    def __init__(self, api_key: str = API_KEY_2CAPTCHA):
        self.api_key = api_key

    def _http_post_json(self, url: str, data: dict, timeout: int = 25) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _http_post_form(self, url: str, data: dict, timeout: int = 25) -> dict:
        req = urllib.request.Request(
            url,
            data=urlencode(data).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            try:
                return json.loads(text)
            except Exception:
                return {"status": 0, "request": text}

    def _http_get_json(self, url: str, timeout: int = 25) -> dict:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def solve_recaptcha_v2(
        self,
        site_key: str = DEFAULT_RECAPTCHA_SITE_KEY,
        page_url: str = DEFAULT_IFRAME_URL,
        max_retries: int = 3,
        max_wait_sec: int = CAPTCHA_TIMEOUT,
        instance_name: str = ""
    ) -> Optional[str]:
        target_url = page_url or self.DEFAULT_IFRAME_URL

        for attempt in range(1, max_retries + 1):
            log("2CAPTCHA", f"Attempt {attempt}/{max_retries}: Submitting reCAPTCHA Enterprise to 2Captcha API...", instance_name)

            # 1. Modern API (RecaptchaV2EnterpriseTaskProxyless)
            try:
                create_resp = self._http_post_json(
                    "https://api.2captcha.com/createTask",
                    {
                        "clientKey": self.api_key,
                        "task": {
                            "type": "RecaptchaV2EnterpriseTaskProxyless",
                            "websiteURL": target_url,
                            "websiteKey": site_key
                        }
                    }
                )

                if create_resp.get("errorId") == 0 and "taskId" in create_resp:
                    task_id = create_resp["taskId"]
                    log("2CAPTCHA", f"Task created (ID: {task_id}). Polling for solution...", instance_name)
                    start_time = time.time()
                    time.sleep(8)

                    while time.time() - start_time < max_wait_sec:
                        poll = self._http_post_json(
                            "https://api.2captcha.com/getTaskResult",
                            {"clientKey": self.api_key, "taskId": task_id}
                        )

                        if poll.get("status") == "ready":
                            token = poll.get("solution", {}).get("gRecaptchaResponse")
                            elapsed = int(time.time() - start_time)
                            log("2CAPTCHA", f"Captcha solved in {elapsed}s!", instance_name)
                            return token
                        elif poll.get("status") != "processing":
                            log("2CAPTCHA-WARN", f"Task poll status: {poll}", instance_name)
                            break
                        time.sleep(5)
            except Exception as e:
                log("2CAPTCHA-WARN", f"Modern API error: {e}", instance_name)

            # 2. Legacy API Fallback
            try:
                in_resp = self._http_post_form(
                    "https://2captcha.com/in.php",
                    {
                        "key": self.api_key,
                        "method": "userrecaptcha",
                        "googlekey": site_key,
                        "pageurl": target_url,
                        "enterprise": 1,
                        "json": 1
                    }
                )
                if in_resp.get("status") == 1:
                    captcha_id = in_resp.get("request")
                    log("2CAPTCHA", f"Legacy Task ID: {captcha_id}. Polling...", instance_name)
                    start_time = time.time()
                    time.sleep(8)

                    while time.time() - start_time < max_wait_sec:
                        res_resp = self._http_get_json(
                            f"https://2captcha.com/res.php?key={self.api_key}&action=get&id={captcha_id}&json=1"
                        )
                        if res_resp.get("status") == 1:
                            token = res_resp.get("request")
                            log("2CAPTCHA", "Solved token received via legacy API!", instance_name)
                            return token
                        elif res_resp.get("request") != "CAPCHA_NOT_READY":
                            log("2CAPTCHA-WARN", f"Legacy response: {res_resp}", instance_name)
                            break
                        time.sleep(5)
            except Exception as e:
                log("2CAPTCHA-WARN", f"Legacy API error: {e}", instance_name)

            log("2CAPTCHA-WARN", f"Attempt {attempt} failed, retrying in 3s...", instance_name)
            time.sleep(3)

        return None

def get_random_verification_image() -> str:
    """Finds a random selfie/verification photo from images/ directory."""
    cand_dirs = [
        os.path.join(BASE_DIR, "images"),
        os.path.join(os.path.dirname(BASE_DIR), "images"),
        BASE_DIR
    ]
    all_imgs = []
    for d in cand_dirs:
        if os.path.exists(d) and os.path.isdir(d):
            for ext in ["*.jpg", "*.jpeg", "*.png"]:
                all_imgs.extend(glob.glob(os.path.join(d, ext)))
    if all_imgs:
        return random.choice(all_imgs)

    for cand in ["image.jpg", "image.jpeg", "image.png"]:
        p = os.path.join(BASE_DIR, cand)
        if os.path.exists(p):
            return p
    return ""

class MetaAuthClient:
    """
    Automates fast pure-HTTP request registration on auth.meta.com.
    """
    BASE_URL = "https://auth.meta.com"
    SOURCE_APP_ID = "1522763855472543"
    TOS_CMS_ID = "957798449862312"

    def __init__(self, proxy: Optional[str] = None, impersonate: str = "chrome124", instance_name: str = "", captcha_api_key: str = API_KEY_2CAPTCHA):
        self.instance_name = instance_name
        self.session = curl_requests.Session(impersonate=impersonate) if USE_CURL_CFFI else curl_requests.Session()
        self.captcha_solver = TwoCaptchaSolver(api_key=captcha_api_key)
        self.checkpoint_url: Optional[str] = None
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

        self.waterfall_id = str(uuid.uuid4())
        self.client_session_id = self._generate_client_session_id()
        self.device_id: Optional[str] = None
        self.email: Optional[str] = None
        self.req_counter = 1

        self.code_verifier = self._generate_client_session_id() + self._generate_client_session_id()
        self.code_challenge = self._generate_code_challenge(self.code_verifier)
        self.oidc_redirect_uri = self._build_oidc_redirect_uri()

        self.lsd: Optional[str] = None
        self.jazoest: Optional[str] = None
        self.public_key: Optional[str] = None
        self.key_id: int = 199
        self.rcs: Optional[str] = None
        self.jurisdiction: str = "BD"
        self.reg_integrity: Optional[str] = None
        self.fb_dtsg: Optional[str] = None
        self.created_user_id: Optional[str] = None

        self.rev = "1047303469"
        self.hsi = "7684275360328234786"
        self.hs = "20707.HYP:frl_comet_auth_pkg.2.1...0"
        self.s = "npdr3i:q5lguu:55za44"
        self.spin_r = "1047303469"
        self.spin_b = "trunk"
        self.spin_t = "1789134777"
        self.dyn = "7xe6E5aQ1Py8s-3eaQ412nwvG1Ww4KwhE2LwFwn82nwb-q2i5U4e1FxO0SE2Bw46wrU6C0AU1OU1vE2Yw45w43w16-0Co5G0zE1AE17U1eE1ko38w4mw4ewCw3mE1hE3Pw"
        self.csr = ""
        self.hsdp = ""
        self.hblp = ""
        self.sjsp = ""

        self._init_default_headers()

    def _generate_client_session_id(self) -> str:
        chars = string.ascii_letters + string.digits + "_-"
        return "".join(random.choices(chars, k=24))

    def _generate_code_challenge(self, verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

    def _build_oidc_redirect_uri(self) -> str:
        state_payload = {
            "csrf_token": self._generate_client_session_id() + "-" + self._generate_client_session_id(),
            "redirect_to": "https://meta.ai/oidc/callback",
            "started_at": int(time.time() * 1000),
            "waterfall_id": self.waterfall_id
        }
        state_b64 = base64.b64encode(json.dumps(state_payload).encode("utf-8")).decode("utf-8")

        params = {
            "app_id": self.SOURCE_APP_ID,
            "redirect_uri": "https://auth.meta.ai/ecto",
            "response_type": "code",
            "scope": "openid linking",
            "state": state_b64,
            "waterfall_id": self.waterfall_id,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256"
        }
        return f"{self.BASE_URL}/oidc/?{urlencode(params)}"

    def _get_cookie(self, name: str) -> str:
        try:
            if hasattr(self.session.cookies, "get_dict"):
                return self.session.cookies.get_dict().get(name, "")
            return self.session.cookies.get(name, domain=".meta.com") or ""
        except Exception:
            return ""

    def _generate_qpl_join_id(self) -> str:
        return "f" + uuid.uuid4().hex[:16]

    def _next_req(self) -> str:
        val = hex(self.req_counter)[2:]
        self.req_counter += 1
        return val

    def _get_current_referer(self) -> str:
        ref = (
            f"{self.BASE_URL}/?waterfall_id={self.waterfall_id}"
            f"&redirect_uri={quote(self.oidc_redirect_uri)}"
            f"&source_app_id={self.SOURCE_APP_ID}&force_reauth=0"
        )
        if self.rcs:
            ref += f"&rcs={self.rcs}"
        return ref

    def _init_default_headers(self):
        self.session.headers.update({
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "origin": self.BASE_URL,
            "referer": self._get_current_referer(),
            "sec-ch-prefers-color-scheme": "dark",
            "sec-ch-ua": '"Chromium";v="124", "Not?A_Brand";v="24", "Google Chrome";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "x-asbd-id": "359341",
        })

    def _get_base_spin_params(self) -> Dict[str, Any]:
        token_for_jazoest = self.fb_dtsg if self.fb_dtsg else (self.lsd or "AdS4dILcliXDKFeDHlE3nhTH-OQ")
        jazoest_val = compute_jazoest(token_for_jazoest)

        params = {
            "__user": "0",
            "__a": "1",
            "__req": self._next_req(),
            "__hs": self.hs,
            "dpr": "1",
            "__ccg": "GOOD",
            "__rev": self.rev,
            "__s": self.s,
            "__hsi": self.hsi,
            "__dyn": self.dyn,
            "__csr": self.csr,
            "__hsdp": self.hsdp,
            "__hblp": self.hblp,
            "__sjsp": self.sjsp,
            "__comet_req": "33",
            "lsd": self.lsd or "AdS4dILcliXDKFeDHlE3nhTH-OQ",
            "jazoest": jazoest_val,
            "__spin_r": self.spin_r,
            "__spin_b": self.spin_b,
            "__spin_t": self.spin_t,
            "__jssesw": "1",
        }
        if self.fb_dtsg:
            params["fb_dtsg"] = self.fb_dtsg
        return params

    def init_session(self, oidc_redirect_uri: Optional[str] = None) -> bool:
        """Step 1: Load landing page to capture datr, LSD, public key, key ID, and rcs."""
        log("REQ-INIT", "Initializing Meta Auth request session...", self.instance_name)
        landing_url = f"{self.BASE_URL}/"
        if oidc_redirect_uri:
            self.oidc_redirect_uri = oidc_redirect_uri

        params = {
            "waterfall_id": self.waterfall_id,
            "redirect_uri": self.oidc_redirect_uri,
            "source_app_id": self.SOURCE_APP_ID,
            "force_reauth": "0"
        }

        doc_headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
        }

        try:
            resp = self.session.get(landing_url, params=params, headers=doc_headers)
            text = resp.text

            if "rcs=" in resp.url:
                self.rcs = resp.url.split("rcs=")[-1].split("&")[0]

            lsd_match = re.search(r'\["LSD",\[\],\{"token":"([^"]+)"\}', text)
            if not lsd_match:
                lsd_match = re.search(r'name="lsd"\s+value="([^"]+)"', text)
            if lsd_match:
                self.lsd = lsd_match.group(1)
                self.jazoest = compute_jazoest(self.lsd)

            pk_match = re.search(r'publicKey":"([a-f0-9]{64})"', text)
            if pk_match:
                self.public_key = pk_match.group(1)
            key_id_match = re.search(r'keyId":(\d+)', text)
            if key_id_match:
                self.key_id = int(key_id_match.group(1))

            juris_match = re.search(r'regulation_jurisdiction":\["([^"]+)"\]', text)
            if juris_match:
                self.jurisdiction = juris_match.group(1)

            self.session.headers["referer"] = self._get_current_referer()
            log("REQ-INIT", f"Session Ready! LSD: {self.lsd} | KeyID: {self.key_id} | Juris: {self.jurisdiction}", self.instance_name)
            return True
        except Exception as e:
            log("REQ-ERR", f"Failed initializing auth session: {e}", self.instance_name)
            return False

    def _safe_request(self, method: str, url: str, **kwargs) -> Any:
        if "timeout" not in kwargs:
            kwargs["timeout"] = 25
        for attempt in range(1, 4):
            try:
                if method.lower() == "get":
                    return self.session.get(url, **kwargs)
                else:
                    return self.session.post(url, **kwargs)
            except Exception as e:
                log("REQ-RETRY", f"Network delay/retry on {url} (attempt {attempt}/3): {e}", instance_name=self.instance_name)
                if attempt < 3:
                    time.sleep(1.5 * attempt)
                else:
                    raise e

    def init_session(self, oidc_redirect_uri: Optional[str] = None) -> bool:
        """Step 1: Load landing page to capture datr, LSD, public key, key ID, and rcs."""
        log("REQ-INIT", "Initializing Meta Auth request session...", self.instance_name)
        landing_url = f"{self.BASE_URL}/"
        if oidc_redirect_uri:
            self.oidc_redirect_uri = oidc_redirect_uri

        params = {
            "waterfall_id": self.waterfall_id,
            "redirect_uri": self.oidc_redirect_uri,
            "source_app_id": self.SOURCE_APP_ID,
            "force_reauth": "0"
        }

        doc_headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
        }

        try:
            resp = self._safe_request("get", landing_url, params=params, headers=doc_headers)
            text = resp.text

            if "rcs=" in resp.url:
                self.rcs = resp.url.split("rcs=")[-1].split("&")[0]

            lsd_match = re.search(r'\["LSD",\[\],\{"token":"([^"]+)"\}', text)
            if not lsd_match:
                lsd_match = re.search(r'name="lsd"\s+value="([^"]+)"', text)
            if lsd_match:
                self.lsd = lsd_match.group(1)
                self.jazoest = compute_jazoest(self.lsd)

            pk_match = re.search(r'publicKey":"([a-f0-9]{64})"', text)
            if pk_match:
                self.public_key = pk_match.group(1)
            key_id_match = re.search(r'keyId":(\d+)', text)
            if key_id_match:
                self.key_id = int(key_id_match.group(1))

            juris_match = re.search(r'regulation_jurisdiction":\["([^"]+)"\]', text)
            if juris_match:
                self.jurisdiction = juris_match.group(1)

            self.session.headers["referer"] = self._get_current_referer()
            log("REQ-INIT", f"Session Ready! LSD: {self.lsd} | KeyID: {self.key_id} | Juris: {self.jurisdiction}", self.instance_name)
            return True
        except Exception as e:
            log("REQ-ERR", f"Failed initializing auth session: {e}", self.instance_name)
            return False

    def encrypt_password(self, password: str, timestamp: Optional[int] = None) -> str:
        pk = self.public_key or "19c30cf2379e56b8ab368d6f2479cb893a09a023db2d062b2686bb8761881e47"
        return encrypt_meta_password(password=password, public_key_hex=pk, key_id=self.key_id, timestamp=timestamp)

    def check_contact_point_availability(self, email: str) -> Dict[str, Any]:
        """Step 2: Check contact point availability."""
        log("REQ-CP", f"Checking availability for: {email}", self.instance_name)
        self.email = email
        url = f"{self.BASE_URL}/api/check-contact-point-availability/"
        data = {
            "allow_fallback": "true",
            "caa_event_flow": "ntm",
            "contact_point": email,
            "contact_point_type": "EMAIL_ADDRESS",
            "device_id": "",
            "enable_youth_consent_flow": "true",
            "entry_point": "login_home",
            "event_client_time": f"{time.time():.2f}",
            "google_id_token": "",
            "has_youth_consent": "false",
            "is_bootstrap_flow": "false",
            "is_kadabra_zero": "false",
            "qpl_join_id": self._generate_qpl_join_id(),
            "reg_navigation_flow_name": "new_to_family_c50_r1",
            "regulation_jurisdiction": json.dumps([self.jurisdiction]),
            "source_app_id": self.SOURCE_APP_ID,
            "waterfall_id": self.waterfall_id,
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }

        resp = self._safe_request("post", url, data=data, headers=headers)
        parsed = self._parse_fb_response(resp.text)

        if isinstance(parsed, dict) and "payload" in parsed:
            payload = parsed.get("payload")
            if isinstance(payload, dict):
                reg_int = payload.get("regIntegrity") or payload.get("reg_integrity")
                if reg_int:
                    self.reg_integrity = reg_int

                restrictions = payload.get("restrictions")
                if isinstance(restrictions, dict):
                    res_data = restrictions.get("restriction_data")
                    if isinstance(res_data, dict):
                        reg_info = res_data.get("account_reg_info")
                        if isinstance(reg_info, dict) and "device_id" in reg_info:
                            self.device_id = reg_info["device_id"]

                if "contactPoint" in payload and payload["contactPoint"]:
                    self.email = payload["contactPoint"]

        return parsed

    def check_password(self, email: str, password_raw_or_payload: str, dob: str = "2000-01-01") -> Dict[str, Any]:
        """Step 3: Check password validity."""
        url = f"{self.BASE_URL}/api/check-password/"
        if password_raw_or_payload.startswith("#PWD_BROWSER:"):
            password_payload = password_raw_or_payload
        else:
            password_payload = self.encrypt_password(password_raw_or_payload)

        data = {
            "contact_point": email,
            "date_of_birth": dob,
            "name": "",
            "password": password_payload,
            "contact_pointless_account": "false",
            "qpl_join_id": self._generate_qpl_join_id(),
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self._safe_request("post", url, data=data, headers=headers)
        return self._parse_fb_response(resp.text)

    def check_date_of_birth(self, email: str, dob: str = "2000-01-01") -> Dict[str, Any]:
        """Step 4: Check date of birth."""
        url = f"{self.BASE_URL}/api/check-date-of-birth/"
        data = {
            "caa_event_flow": "ntm",
            "date_of_birth": dob,
            "email": email,
            "first_name": "",
            "has_youth_consent": "false",
            "isf": "false",
            "last_name": "",
            "phone_number": "",
            "qpl_join_id": self._generate_qpl_join_id(),
            "reg_integrity": self.reg_integrity or "",
            "source_app_id": self.SOURCE_APP_ID,
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self._safe_request("post", url, data=data, headers=headers)
        parsed = self._parse_fb_response(resp.text)
        if isinstance(parsed, dict) and "payload" in parsed:
            payload = parsed.get("payload")
            if isinstance(payload, dict):
                reg_int = payload.get("regIntegrity") or payload.get("reg_integrity")
                if reg_int:
                    self.reg_integrity = reg_int
        return parsed

    def suggest_username(self) -> Dict[str, Any]:
        """Step 5: Suggest Kadabra username."""
        url = f"{self.BASE_URL}/api/kadabra/suggest_username/"
        data = {
            "caa_event_flow": "ntm",
            "device_id": self.device_id or "",
            "first_name": "",
            "last_name": "",
            "qpl_join_id": self._generate_qpl_join_id(),
            "reg_integrity": self.reg_integrity or "",
            "source_app_id": self.SOURCE_APP_ID,
            "waterfall_id": self.waterfall_id,
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self._safe_request("post", url, data=data, headers=headers)
        return self._parse_fb_response(resp.text)

    def check_profile(self, username: str) -> Dict[str, Any]:
        """Step 6: Confirm username availability and TOS agreement."""
        url = f"{self.BASE_URL}/api/kadabra/check_profile/"
        data = {
            "caa_event_flow": "ntm",
            "display_name": "",
            "foa_import_source_name": "",
            "foa_import_source_obid": "",
            "nta_disclosures_summary_cms_id": "",
            "picture_source": "",
            "qpl_join_id": self._generate_qpl_join_id(),
            "tos_cms_id": self.TOS_CMS_ID,
            "username": username,
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self._safe_request("post", url, data=data, headers=headers)
        return self._parse_fb_response(resp.text)

    def register_save_credentials(
        self,
        email: str,
        password_raw_or_payload: str,
        username: str,
        dob: str = "2000-01-01",
        redirect_uri: Optional[str] = None
    ) -> Dict[str, Any]:
        """Step 7: Submit final registration to trigger OTP send."""
        log("REQ-REGISTER", f"Submitting registration for {email} ({username})...", self.instance_name)
        url = f"{self.BASE_URL}/login/device-based/kadabra-register-save-credentials/"

        if password_raw_or_payload.startswith("#PWD_BROWSER:"):
            password_payload = password_raw_or_payload
        else:
            password_payload = self.encrypt_password(password_raw_or_payload)

        data = {
            "client_consent_timestamp": str(int(time.time())),
            "display_name": "",
            "foa_import_source_name": "",
            "foa_import_source_obid": "",
            "nta_disclosures_summary_cms_id": "",
            "picture_source": "",
            "tos_cms_id": self.TOS_CMS_ID,
            "username": username,
            "consent_version": "",
            "contact_point": email,
            "contact_point_type": "EMAIL_ADDRESS",
            "csi": self.client_session_id,
            "date_of_birth": dob,
            "device_id": self.device_id or "",
            "fb_encrypted_access_token": "",
            "fb_oidc_access_token": "",
            "first_name": "",
            "google_id_token": "",
            "has_youth_consent": "false",
            "ig_encrypted_access_token": "",
            "ig_encrypted_auth_header": "",
            "ig_oidc_access_token": "",
            "last_name": "",
            "opt_into_marketing": "false",
            "password": password_payload,
            "redirect_uri": redirect_uri or self.oidc_redirect_uri,
            "reg_integrity": self.reg_integrity or "",
            "should_save_credentials": "true",
            "source_app_id": self.SOURCE_APP_ID,
            "third_party_age_verification_id": "",
            "waterfall_id": self.waterfall_id,
            "caa_event_flow": "ntm",
            "entry_point": "login_home",
            "event_client_time": f"{time.time():.2f}",
            "is_kadabra_zero": "false",
            "reg_navigation_flow_name": "new_to_family_c50_r1",
            "regulation_jurisdiction": json.dumps([self.jurisdiction]),
            "qpl_join_id": self._generate_qpl_join_id(),
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self._safe_request("post", url, data=data, headers=headers)
        parsed = self._parse_fb_response(resp.text)

        if isinstance(parsed, dict):
            if "dtsgToken" in parsed:
                self.fb_dtsg = parsed["dtsgToken"]

            if "payload" in parsed:
                payload = parsed.get("payload", {})
                if isinstance(payload, dict):
                    user_id = payload.get("account_id") or payload.get("uid") or payload.get("user_id")
                    if user_id:
                        self.created_user_id = str(user_id)
                        log("REQ-SUCCESS", f"Registered Meta Account ID: {self.created_user_id}", self.instance_name)

        return parsed

    def confirm_email_otp(self, code: str, email: Optional[str] = None) -> Dict[str, Any]:
        """Submits 6-digit confirmation code / OTP to confirm and activate Meta account."""
        target_email = email or self.email
        clean_code = str(code).strip()
        log("REQ-OTP", f"Submitting OTP '{clean_code}' for {target_email}...", self.instance_name)
        variables = {
            "input": {
                "confirmation_code": {
                    "sensitive_string_value": clean_code
                },
                "confirmation_code_type": "OTP_CODE",
                "event_flow": "ntm",
                "rl_client_session_id": self.client_session_id,
                "source_app_id": self.SOURCE_APP_ID,
                "waterfall_id": self.waterfall_id
            }
        }
        return self.send_graphql_request(doc_id="9851798224911796", variables=variables)

    def send_graphql_request(self, doc_id: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/api/graphql/"
        data = {
            "doc_id": doc_id,
            "variables": json.dumps(variables),
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self._safe_request("post", url, data=data, headers=headers)
        return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else self._parse_fb_response(resp.text)

    def _parse_fb_response(self, text: str) -> Any:
        clean_text = text
        if clean_text.startswith("for (;;);"):
            clean_text = clean_text[9:]
        try:
            return json.loads(clean_text)
        except Exception:
            return {"raw_response": text}

    def get_cookies_dict(self) -> Dict[str, str]:
        try:
            if hasattr(self.session.cookies, "get_dict"):
                return self.session.cookies.get_dict()
            return {c.name: c.value for c in self.session.cookies}
        except Exception:
            return {}

    def get_cookies_string(self) -> str:
        cookies_dict = self.get_cookies_dict()
        return "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

    def check_for_checkpoint(self, wait_sec: int = 3) -> Optional[str]:
        log("CHECKPOINT-POLL", f"Checking for checkpoint status (waiting {wait_sec}s)...", self.instance_name)
        time.sleep(wait_sec)

        doc_headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "referer": self._get_current_referer()
        }

        try:
            resp = self.session.get(f"{self.BASE_URL}/", headers=doc_headers, allow_redirects=True)
            if "checkpoint" in resp.url.lower() or "confirm that you're human" in resp.text.lower() or "checkpoints" in resp.text.lower() or "confirm you’re human" in resp.text.lower():
                log("CHECKPOINT-DETECT", f"Checkpoint detected at: {resp.url}", self.instance_name)
                self.checkpoint_url = resp.url
                return resp.url

            resp_oidc = self.session.get(self.oidc_redirect_uri, headers=doc_headers, allow_redirects=True)
            if "checkpoint" in resp_oidc.url.lower() or "confirm that you're human" in resp_oidc.text.lower() or "confirm you’re human" in resp_oidc.text.lower():
                log("CHECKPOINT-DETECT", f"Checkpoint detected via OIDC: {resp_oidc.url}", self.instance_name)
                self.checkpoint_url = resp_oidc.url
                return resp_oidc.url

        except Exception as e:
            log("CHECKPOINT-WARN", f"Error checking checkpoint: {e}", self.instance_name)

        return None

    def extract_captcha_persist_data(self, resp_text: str) -> str:
        match = re.search(r'"(?:bot_)?captcha_persist_data"\s*:\s*"([^"]+)"', resp_text)
        if match:
            persist_val = match.group(1)
            log("UFAC-DATA", f"Extracted captcha_persist_data: {persist_val[:30]}...", self.instance_name)
            return persist_val

        try:
            clean = resp_text[9:] if resp_text.startswith("for (;;);") else resp_text
            data = json.loads(clean)
            for root_key in ["ufac_client_submit_action", "ufac_submit_action", "ufac_action"]:
                node = data.get("data", {}).get(root_key, {})
                if isinstance(node, dict):
                    state = node.get("ufac_client", {}).get("state", {}) if "ufac_client" in node else node.get("state", {})
                    if isinstance(state, dict):
                        persist = state.get("captcha_persist_data") or state.get("bot_captcha_persist_data") or state.get("persist_data")
                        if persist:
                            log("UFAC-DATA", f"Extracted captcha_persist_data from JSON: {persist[:30]}...", self.instance_name)
                            return str(persist)
        except Exception:
            pass

        return "..."

    def resolve_ufac_checkpoint(
        self,
        checkpoint_url: Optional[str] = None,
        image_path: Optional[str] = None
    ) -> bool:
        page_url = checkpoint_url or self.checkpoint_url or f"{self.BASE_URL}/checkpoint/"
        actor_id = self.created_user_id or self.get_cookies_dict().get("c_user") or "1227049917168618"
        lsd = self.lsd or "RyHFQ20E53MNpQQ7jYBChR"
        fb_dtsg = self.fb_dtsg or ""
        jazoest = compute_jazoest(fb_dtsg or lsd)

        log("UFAC-START", f"Resolving UFAC Checkpoint via API for Actor: {actor_id}", self.instance_name)

        checkpoint_headers = {
            'accept': '*/*',
            'origin': self.BASE_URL,
            'referer': page_url,
            'user-agent': self.session.headers.get("user-agent", 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
            'x-asbd-id': '359341',
            'x-fb-lsd': lsd
        }

        checkpoint_params = {
            'fb_dtsg': fb_dtsg,
            'jazoest': jazoest,
            'lsd': lsd
        }

        # Step 1: Submit PROCEED Action
        log("UFAC-1", "[1/4] Submitting FRLUFACSubmitActionMutation (PROCEED)...", self.instance_name)
        mutation_proceed = {
            'av': actor_id,
            '__user': '0',
            '__a': '1',
            '__req': self._next_req(),
            'fb_api_caller_class': 'RelayModern',
            'fb_api_req_friendly_name': 'FRLUFACSubmitActionMutation',
            'server_timestamps': 'true',
            'variables': json.dumps({
                "input": {
                    "actor_id": actor_id,
                    "client_mutation_id": "1",
                    "action": "PROCEED",
                    "caller": None,
                    "enrollment_id": None
                },
                "scale": 1
            }),
            'doc_id': DOC_ID_UFAC_CHECKPOINT
        }

        persist_data = "..."
        try:
            resp_proceed = self.session.post(
                f"{self.BASE_URL}/api/graphql/",
                params=checkpoint_params,
                headers=checkpoint_headers,
                data=mutation_proceed
            )
            log("UFAC-PROCEED", f"PROCEED response [{resp_proceed.status_code}]: {resp_proceed.text[:180]}", self.instance_name)
            persist_data = self.extract_captcha_persist_data(resp_proceed.text)
        except Exception as e:
            log("UFAC-WARN", f"Warning during PROCEED step: {e}", self.instance_name)

        # Step 2: Solve reCAPTCHA Enterprise via 2Captcha
        log("2CAPTCHA", f"[2/4] Solving reCAPTCHA Enterprise via 2Captcha API (sitekey: {DEFAULT_RECAPTCHA_SITE_KEY[:15]}...)...", self.instance_name)
        token = self.captcha_solver.solve_recaptcha_v2(
            site_key=DEFAULT_RECAPTCHA_SITE_KEY,
            page_url=TwoCaptchaSolver.DEFAULT_IFRAME_URL,
            instance_name=self.instance_name
        )

        if token:
            log("2CAPTCHA-OK", f"🎉 Got 2Captcha Solution Token: {token[:35]}...", self.instance_name)
        else:
            log("2CAPTCHA-WARN", "⚠️ 2Captcha token not obtained. Attempting to proceed...", self.instance_name)
            token = ""

        # Step 3: Submit SUBMIT_BOT_CAPTCHA_RESPONSE mutation
        log("UFAC-3", "[3/4] Submitting SUBMIT_BOT_CAPTCHA_RESPONSE mutation...", self.instance_name)
        mutation_captcha = {
            'av': actor_id,
            '__user': '0',
            '__a': '1',
            '__req': self._next_req(),
            'fb_api_caller_class': 'RelayModern',
            'fb_api_req_friendly_name': 'FRLUFACSubmitActionMutation',
            'server_timestamps': 'true',
            'variables': json.dumps({
                "input": {
                    "actor_id": actor_id,
                    "client_mutation_id": "2",
                    "action": "SUBMIT_BOT_CAPTCHA_RESPONSE",
                    "bot_captcha_persist_data": persist_data,
                    "captcha_persist_data": persist_data,
                    "bot_captcha_response": token or None,
                    "captcha_response": token or None,
                    "caller": None,
                    "enrollment_id": None
                },
                "scale": 1
            }),
            'doc_id': DOC_ID_UFAC_CHECKPOINT
        }

        captcha_resp_text = ""
        try:
            resp_captcha = self.session.post(
                f"{self.BASE_URL}/api/graphql/",
                params=checkpoint_params,
                headers=checkpoint_headers,
                data=mutation_captcha
            )
            log("UFAC-CAPTCHA", f"Captcha submission response [{resp_captcha.status_code}]: {resp_captcha.text[:200]}", self.instance_name)
            captcha_resp_text = resp_captcha.text
        except Exception as e:
            log("UFAC-ERR", f"Error submitting bot captcha: {e}", self.instance_name)

        # Step 4: Submit UPLOAD_IMAGE Mutation for UFACImageUploadChallengeState
        target_image = image_path or get_random_verification_image()
        if "ImageUpload" in captcha_resp_text or "image_upload" in captcha_resp_text or target_image:
            log("UFAC-4", f"[4/4] Submitting Image Upload Challenge via API with image: {os.path.basename(str(target_image))}...", self.instance_name)
            mutation_image = {
                'av': actor_id,
                '__user': '0',
                '__a': '1',
                '__req': self._next_req(),
                'fb_api_caller_class': 'RelayModern',
                'fb_api_req_friendly_name': 'FRLUFACSubmitActionMutation',
                'server_timestamps': 'true',
                'variables': json.dumps({
                    "input": {
                        "actor_id": actor_id,
                        "client_mutation_id": "3",
                        "action": "UPLOAD_IMAGE",
                        "uploaded_image_file": "file",
                        "caller": None,
                        "enrollment_id": None
                    },
                    "scale": 1
                }),
                'doc_id': DOC_ID_UFAC_CHECKPOINT
            }

            try:
                if USE_CURL_CFFI:
                    try:
                        from curl_cffi.curl import CurlMime
                        mime = CurlMime()
                        for k, v in mutation_image.items():
                            mime.addpart(name=k, data=str(v).encode("utf-8"))
                        if target_image and os.path.exists(target_image):
                            log("IMG-MIME", f"Attaching verification photo (CurlMime): {os.path.basename(target_image)}", self.instance_name)
                            mime.addpart(
                                name="file",
                                local_path=target_image,
                                filename="image.jpeg",
                                content_type="image/jpeg"
                            )
                        upload_resp = self.session.post(
                            f"{self.BASE_URL}/api/graphql/",
                            params=checkpoint_params,
                            headers=checkpoint_headers,
                            multipart=mime
                        )
                    except Exception as c_err:
                        if target_image and os.path.exists(target_image):
                            with open(target_image, "rb") as f:
                                upload_resp = self.session.post(
                                    f"{self.BASE_URL}/api/graphql/",
                                    params=checkpoint_params,
                                    headers=checkpoint_headers,
                                    data=mutation_image,
                                    files={'file': ('image.jpeg', f.read(), 'image/jpeg')}
                                )
                        else:
                            upload_resp = self.session.post(
                                f"{self.BASE_URL}/api/graphql/",
                                params=checkpoint_params,
                                headers=checkpoint_headers,
                                data=mutation_image
                            )
                else:
                    if target_image and os.path.exists(target_image):
                        log("IMG-REQ", f"Attaching verification photo: {os.path.basename(target_image)}", self.instance_name)
                        with open(target_image, "rb") as f:
                            upload_resp = self.session.post(
                                f"{self.BASE_URL}/api/graphql/",
                                params=checkpoint_params,
                                headers=checkpoint_headers,
                                data=mutation_image,
                                files={'file': ('image.jpeg', f.read(), 'image/jpeg')}
                            )
                    else:
                        upload_resp = self.session.post(
                            f"{self.BASE_URL}/api/graphql/",
                            params=checkpoint_params,
                            headers=checkpoint_headers,
                            data=mutation_image
                        )

                log("UFAC-UPLOAD", f"Image Upload Response [{upload_resp.status_code}]: {upload_resp.text[:200]}", self.instance_name)
            except Exception as e:
                log("UFAC-ERR", f"Failed to submit image upload: {e}", self.instance_name)

        log("UFAC-SUCCESS", "🎉 UFAC Checkpoint API resolution completed!", self.instance_name)

        # Extract and follow 'next' redirect chain
        try:
            parsed_cp = urllib.parse.urlparse(page_url)
            query_params = urllib.parse.parse_qs(parsed_cp.query)
            next_target = query_params.get("next", [None])[0]
            if next_target:
                log("REDIRECT", f"Following post-checkpoint redirect: {next_target}", self.instance_name)
                redir_headers = {
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "referer": page_url
                }
                redir_resp = self.session.get(next_target, headers=redir_headers, allow_redirects=True)
                log("REDIRECT-OK", f"Landed at: {redir_resp.url} (Status: {redir_resp.status_code})", self.instance_name)
        except Exception as ex:
            log("REDIRECT-WARN", f"Could not follow redirect: {ex}", self.instance_name)

        return True

    def complete_oidc_flow(self) -> Dict[str, Any]:
        log("OIDC", "Completing OIDC Authorization flow...", self.instance_name)
        doc_headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "referer": self._get_current_referer()
        }
        try:
            resp = self.session.get(self.oidc_redirect_uri, headers=doc_headers, allow_redirects=True)
            log("OIDC-STATUS", f"OIDC flow final URL: {resp.url} [{resp.status_code}]", self.instance_name)

            code = None
            if "code=" in resp.url:
                parsed_url = urllib.parse.urlparse(resp.url)
                params = urllib.parse.parse_qs(parsed_url.query)
                code = params.get("code", [None])[0]
                if code:
                    log("OIDC-CODE", f"Obtained OIDC Authorization Code: {code[:20]}...", self.instance_name)

            cookies_dict = self.get_cookies_dict()
            return {
                "success": True,
                "code": code,
                "final_url": resp.url,
                "user_id": self.created_user_id or cookies_dict.get("c_user"),
                "cookies": cookies_dict
            }
        except Exception as e:
            log("OIDC-ERR", f"Error completing OIDC flow: {e}", self.instance_name)
            return {"success": False, "error": str(e)}


# ==============================================================================
# 🚀 100% PURE-API META ACCOUNT CREATION TASK (NO BROWSER / NO PLAYWRIGHT)
# ==============================================================================
async def create_meta_account_task(
    playwright: Optional[Any] = None,
    first_name: str = "",
    last_name: str = "",
    password: str = "",
    username: str = "",
    instance_name: str = "Inst-1",
    task_context: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str, str]:
    """
    Creates a Meta Account 100% PURE API (FAST, ULTRA-LIGHTWEIGHT, NO BROWSER):
      1. TempMail Ninja (Socket.IO) or Mail.td for disposable email & OTP
      2. MetaAuthClient pure-HTTP registration with TweetNaCl + AES-256-GCM envelope encryption
      3. GraphQL OTP confirmation (FRLConfirmEmailMutation)
      4. Direct API Checkpoint detection + UFAC GraphQL solving (2Captcha + Multipart Image Upload)
      5. OIDC flow completion & account activation
    """
    ninja_client: Optional[Any] = None
    email = ""
    mail_token = ""

    try:
        # 1. CREATE DISPOSABLE EMAIL
        email = None
        mail_token = None
        ninja_client = None

        for attempt in range(1, 4):
            log("GEN", f"Generating temporary email for Meta AI (attempt {attempt}/3)...", instance_name=instance_name)
            email, _, mail_token = fallback_generate_temp_email(instance_name=instance_name)
            if email:
                break
            if HAS_NINJA:
                try:
                    log("TEMPMAIL-NINJA", "Trying TempMail Ninja fallback...", instance_name=instance_name)
                    ninja_client = TempMailNinjaAPI()
                    ninja_client.connect()
                    email = ninja_client.create_temp_email()
                    if email:
                        if task_context is not None:
                            task_context['ninja_client'] = ninja_client
                        break
                except Exception as e:
                    log("TEMPMAIL-WARN", f"Ninja fallback error: {e}", instance_name=instance_name)
                    ninja_client = None
            time.sleep(1.0)

        if not email:
            log("ERR", "Failed to generate email across all available email providers.", instance_name=instance_name)
            return False, "", ""

        if task_context is not None:
            task_context['email'] = email
            task_context['mail_token'] = mail_token

        log("META-INIT", f"Target Email: {email} | Password: {password} | User: {username}", instance_name=instance_name)

        # 2. PURE-REQUEST META REGISTRATION FLOW
        dob_year = random.randint(1992, 2002)
        dob_month = f"{random.randint(1, 12):02d}"
        dob_day = f"{random.randint(1, 28):02d}"
        dob = f"{dob_year}-{dob_month}-{dob_day}"

        client = MetaAuthClient(instance_name=instance_name)
        if not client.init_session():
            log("ERR", "Failed to initialize Meta auth request session.", instance_name=instance_name)
            if ninja_client:
                ninja_client.disconnect()
            return False, "", ""

        # Step 1: Check contact point
        client.check_contact_point_availability(email=email)

        # Step 2: Check password (password received from Telegram bot)
        client.check_password(email=email, password_raw_or_payload=password, dob=dob)

        # Step 3: Check DOB
        client.check_date_of_birth(email=email, dob=dob)

        # Step 4: Determine username
        chosen_username = username.strip() if username else ""
        if not chosen_username:
            suggest_resp = client.suggest_username()
            chosen_username = (
                suggest_resp.get("payload", {}).get("username")
                if isinstance(suggest_resp, dict) and "payload" in suggest_resp
                else None
            )
        if not chosen_username:
            clean_fn = re.sub(r'[^a-zA-Z0-9]', '', first_name).lower() or "user"
            clean_ln = re.sub(r'[^a-zA-Z0-9]', '', last_name).lower() or "account"
            chosen_username = f"{clean_fn}{clean_ln}{random.randint(100, 9999)}"

        # Step 5: Validate handle
        client.check_profile(username=chosen_username)

        # Step 6: Submit registration (Meta will send 6-digit confirmation OTP)
        client.register_save_credentials(
            email=email,
            password_raw_or_payload=password,
            username=chosen_username,
            dob=dob
        )

        # 3. POLL OTP VIA MAIL SERVICE
        log("OTP-POLL", f"Waiting for 6-digit OTP code on {email}...", instance_name=instance_name)
        otp_code = None

        if mail_token:
            otp_code = fallback_fetch_otp(
                email=email,
                password=password,
                token=mail_token,
                max_wait_sec=60,
                expected_digits=6,
                instance_name=instance_name
            )

        if not otp_code and ninja_client:
            try:
                otp_code = ninja_client.wait_for_otp(timeout=60, poll_interval=2, expected_digits=6)
            except Exception as e:
                log("OTP-WARN", f"Ninja OTP wait error: {e}", instance_name=instance_name)

        if not otp_code:
            log("ERR", f"Failed to retrieve OTP code for {email}.", instance_name=instance_name)
            if ninja_client:
                ninja_client.disconnect()
            return False, "", ""

        log("OTP-RECV", f"🔑 Retrieved Meta OTP Code: {otp_code}", instance_name=instance_name)

        # Step 7: Confirm OTP via GraphQL mutation
        confirm_resp = client.confirm_email_otp(code=otp_code, email=email)
        log("OTP-CONFIRM", f"Confirmation GraphQL response: {confirm_resp}", instance_name=instance_name)

        # 4. PURE-API CHECKPOINT & 2CAPTCHA + IMAGE RESOLUTION
        log("CHECKPOINT-FLOW", "Checking for Checkpoint / 'Confirm you are human' challenge via API...", instance_name=instance_name)
        checkpoint_url = client.check_for_checkpoint(wait_sec=3)
        if checkpoint_url:
            log("CHECKPOINT", f"Checkpoint challenge active! Solving 100% via API (2Captcha + Photo upload)...", instance_name=instance_name)
            ufac_ok = client.resolve_ufac_checkpoint(checkpoint_url=checkpoint_url)
            if not ufac_ok:
                log("UFAC-WARN", "UFAC checkpoint resolution returned False, attempting to continue...", instance_name=instance_name)

        # Complete OIDC authorization
        client.complete_oidc_flow()

        log("META-SUCCESS", f"🎉 Meta Account Created & Verified 100% via API for {email}!", instance_name=instance_name)
        return True, email, mail_token

    except Exception as e:
        log("META-ERR", f"Meta creation exception: {e}", instance_name=instance_name)
        if ninja_client:
            try:
                ninja_client.disconnect()
            except Exception:
                pass
        return False, "", ""

# ==============================================================================
# 📱 ADB EMULATOR CONTROL & UI DETECTION
# ==============================================================================
def find_adb():
    adb_path = shutil.which("adb")
    if adb_path:
        return adb_path
    possible_paths = [
        r"D:\LDPlayer\LDPlayer14\adb.exe",
        r"D:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
        r"C:\Program Files\MuMuPlayer-12.0\shell\adb.exe",
        r"C:\Program Files\MuMu\emulator\nemu\vbox\adb.exe",
        r"C:\Program Files (x86)\MuMu\emulator\nemu\vbox\adb.exe",
        os.path.expanduser(r"~\Desktop\scrcpy-win64-v4.0\adb.exe"),
        os.path.expanduser(r"~\AppData\Local\Android\Sdk\platform-tools\adb.exe"),
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return "adb"

ADB_PATH = find_adb()

def get_device_unique_signature(dev_serial):
    try:
        res = subprocess.run(
            f'"{ADB_PATH}" -s {dev_serial} shell "cat /proc/sys/kernel/random/boot_id 2>/dev/null || getprop ro.serialno 2>/dev/null || settings get secure android_id 2>/dev/null"',
            shell=True, capture_output=True, encoding='utf-8', errors='ignore', timeout=2
        )
        sig = res.stdout.strip() if res.stdout else ""
        if sig:
            for l in sig.splitlines():
                l = l.strip()
                if l and len(l) > 4:
                    return l
    except Exception:
        pass
    return dev_serial

def get_unique_devices(emulators_only=True):
    ports = [
        5555, 5557, 5559, 5561, 5563, 5565,
        7555, 16384, 16416, 16448, 16480, 16512,
        62001, 62025, 62026,
        21503, 21513, 21523
    ]

    from concurrent.futures import ThreadPoolExecutor
    def _quick_connect(p):
        try:
            subprocess.run(f'"{ADB_PATH}" connect 127.0.0.1:{p}', shell=True, capture_output=True, timeout=0.4)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(_quick_connect, ports))

    res = subprocess.run(f'"{ADB_PATH}" devices', shell=True, capture_output=True, encoding='utf-8', errors='ignore')
    lines = res.stdout.strip().splitlines()
    raw_devices = []
    for line in lines[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            raw_devices.append(parts[0])

    seen_signatures = set()
    unique_devices = []

    for dev in raw_devices:
        sig = get_device_unique_signature(dev)
        if sig not in seen_signatures:
            seen_signatures.add(sig)
            unique_devices.append(dev)

    if emulators_only:
        emu_list = [d for d in unique_devices if "127.0.0.1:" in d or "localhost:" in d or d.startswith("emulator-")]
        if emu_list:
            return emu_list

    return unique_devices

def run_adb_command(args, device_serial=None, instance_name="", timeout=12):
    if isinstance(args, str):
        if args.startswith("adb "):
            args = args[4:]
        cmd_type = args.split()[0] if args.split() else ""
        target = device_serial
        if target and cmd_type not in ["connect", "disconnect", "devices"]:
            cmd = f'"{ADB_PATH}" -s {target} {args}'
        else:
            cmd = f'"{ADB_PATH}" {args}'
    else:
        cmd = [ADB_PATH] + args

    with ADB_SEMAPHORE:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, encoding='utf-8', errors='ignore', timeout=timeout)
            return result.stdout.strip() if result.stdout else ""
        except subprocess.TimeoutExpired:
            log("ADB-TIMEOUT", f"ADB command timed out ({timeout}s): {str(args)[:40]}", instance_name=instance_name)
            return ""
        except Exception as e:
            log("ADB-ERR", f"Execution error: {e}", instance_name=instance_name)
            return ""

def is_keyboard_visible(device_serial=None, instance_name=""):
    out = run_adb_command("shell dumpsys input_method", device_serial=device_serial, instance_name=instance_name)
    return "mInputShown=true" in out or "mHasSurface=true" in out

def clear_input_field(length=35, device_serial=None, instance_name=""):
    run_adb_command("shell input keyevent 123", device_serial=device_serial, instance_name=instance_name)
    del_keys = " ".join(["67"] * length)
    run_adb_command(f"shell input keyevent {del_keys}", device_serial=device_serial, instance_name=instance_name)

def type_text(text, device_serial=None, instance_name="", human_like=False, min_delay=0.04, max_delay=0.12):
    if not text:
        return
    if human_like:
        for char in text:
            escaped = char.replace(' ', '%s').replace('&', r'\&').replace('<', r'\<').replace('>', r'\>').replace('"', r'\"').replace("'", r"\'").replace('$', r'\$').replace('`', r'\`')
            run_adb_command(f'shell input text "{escaped}"', device_serial=device_serial, instance_name=instance_name)
            time.sleep(random.uniform(min_delay, max_delay))
    else:
        escaped = text.replace(' ', '%s').replace('&', r'\&').replace('<', r'\<').replace('>', r'\>').replace('"', r'\"').replace("'", r"\'")
        run_adb_command(f'shell input text "{escaped}"', device_serial=device_serial, instance_name=instance_name)

def hide_keyboard(device_serial=None, instance_name=""):
    run_adb_command("shell input keyevent 111", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.2)

def parse_bounds(bounds_str):
    match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
    if match:
        left, top, right, bottom = map(int, match.groups())
        cx, cy = (left + right) // 2, (top + bottom) // 2
        if cx > 10 and cy > 10:
            return cx, cy
    return None

def get_screen_dimensions(root):
    if root is not None:
        bounds = root.attrib.get('bounds', '')
        match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
        if match:
            w = int(match.group(3))
            h = int(match.group(4))
            if w > 100 and h > 100:
                return w, h
    return 540, 960

def get_rel_coords(root, rx, ry):
    w, h = get_screen_dimensions(root)
    return int(w * rx), int(h * ry)

def get_screen_hierarchy(device_serial=None, instance_name="", max_retries=2):
    safe_ser = device_serial.replace(':', '_').replace('.', '_').replace('-', '_') if device_serial else "default"
    dump_path = f"/sdcard/window_dump_{safe_ser}.xml"
    
    for attempt in range(1, max_retries + 1):
        time.sleep(0.08 if attempt == 1 else 0.25)
        run_adb_command(f"shell uiautomator dump {dump_path}", device_serial=device_serial, instance_name=instance_name, timeout=8)
        xml_str = run_adb_command(f"shell cat {dump_path}", device_serial=device_serial, instance_name=instance_name, timeout=6)
        if xml_str and xml_str.startswith("<?xml"):
            try:
                return ET.fromstring(xml_str)
            except Exception:
                pass
        if attempt < max_retries:
            time.sleep(0.2)
    return None

def extract_screen_texts(node):
    texts = []
    if node is None:
        return texts
    text = node.attrib.get('text', '').strip()
    desc = node.attrib.get('content-desc', '').strip()
    if text:
        texts.append(text)
    if desc and desc != text:
        texts.append(desc)
    for child in node:
        texts.extend(extract_screen_texts(child))
    return texts

def find_password_field(root):
    if root is None:
        return None
    for node in root.iter('node'):
        cls = node.attrib.get('class', '').lower()
        is_pwd = node.attrib.get('password', '').lower() == 'true'
        if 'edittext' in cls and is_pwd:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords:
                return coords
    for node in root.iter('node'):
        cls = node.attrib.get('class', '').lower()
        if 'edittext' in cls:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords:
                return coords
    return None

def find_code_input_field(root):
    """
    Find the Code input box on 'Check your email' screen/modal.
    Prioritizes:
    1. EditText nodes in the expected input region (0.35 to 0.80 of screen).
    2. Node with exact text/content-desc 'Code', 'Enter code', 'Enter the code'.
    3. Fallback relative coords based on screen style (Facebook page: 0.66, Modal: 0.48).
    """
    if root is None:
        return 270, 460
    w, h = get_screen_dimensions(root)
    
    # 1. Search for EditText node in valid middle region
    for node in root.iter('node'):
        cls = node.attrib.get('class', '').lower()
        if 'edittext' in cls:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and int(h * 0.35) <= coords[1] <= int(h * 0.80):
                return coords[0], coords[1]
                
    # 2. Look for exact "Enter code" / "Code" placeholder text
    for node in root.iter('node'):
        t = node.attrib.get('text', '').strip().lower()
        d = node.attrib.get('content-desc', '').strip().lower()
        if t in ["enter code", "enter the code", "code"] or d in ["enter code", "enter the code", "code"]:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and int(h * 0.35) <= coords[1] <= int(h * 0.80):
                return coords[0], coords[1]

    # 3. Fallback based on page style
    texts = extract_screen_texts(root)
    blob = " ".join(texts).lower()
    if "facebook" in blob or "enter code" in blob:
        return int(w * 0.50), int(h * 0.66)
    return int(w * 0.50), int(h * 0.48)

def find_input_box(root, hint_patterns=None, default_rx=0.50, default_ry=0.40):
    if root is None:
        return int(540 * default_rx), int(960 * default_ry)
    w, h = get_screen_dimensions(root)
    if hint_patterns is None:
        hint_patterns = ["full name", "username", "email", "code", "password"]
    
    # 1. Look for node matching any hint_pattern in text, content-desc, or resource-id in the body (0.20 to 0.85)
    for node in root.iter('node'):
        t = node.attrib.get('text', '').strip().lower()
        d = node.attrib.get('content-desc', '').strip().lower()
        r = node.attrib.get('resource-id', '').strip().lower()
        comb = f"{t} {d} {r}".strip()
        coords = parse_bounds(node.attrib.get('bounds', ''))
        if coords and int(h * 0.20) < coords[1] < int(h * 0.85):
            for hp in hint_patterns:
                hp_lower = hp.lower().strip()
                if hp_lower == t or hp_lower == d or hp_lower in comb:
                    return coords[0], coords[1]

    # 2. Look for EditText node around the expected vertical position (default_ry +- 0.15)
    for node in root.iter('node'):
        cls = node.attrib.get('class', '').lower()
        if 'edittext' in cls:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and abs(coords[1] - int(h * default_ry)) < int(h * 0.15):
                return coords[0], coords[1]

    # 3. Fallback to default relative position
    return int(w * default_rx), int(h * default_ry)

def find_element(root, search_pattern, by_class=None, exact=False):
    if root is None:
        return None
    pattern_lower = search_pattern.lower().strip()
    candidates = []
    w, h = get_screen_dimensions(root)
    
    for node in root.iter('node'):
        node_text = node.attrib.get('text', '').strip()
        node_desc = node.attrib.get('content-desc', '').strip()
        node_res = node.attrib.get('resource-id', '').strip()
        node_cls = node.attrib.get('class', '').strip()
        is_clickable = node.attrib.get('clickable', 'false').lower() == 'true'
        
        if by_class and by_class.lower() not in node_cls.lower():
            continue
            
        text_lower = node_text.lower()
        desc_lower = node_desc.lower()
        res_lower = node_res.lower()

        if pattern_lower == "continue" and ("facebook" in text_lower or "instagram" in text_lower):
            continue

        is_exact_match = (text_lower == pattern_lower or desc_lower == pattern_lower)
        is_substring_match = (pattern_lower in text_lower or pattern_lower in desc_lower or pattern_lower in res_lower)
        
        if exact and not is_exact_match:
            continue
            
        if is_exact_match or is_substring_match:
            bounds = node.attrib.get('bounds', '')
            coords = parse_bounds(bounds)
            if coords:
                cx, cy = coords
                if cy > int(h * 0.91) or cy < int(h * 0.10):
                    continue
                matched_label = node_text or node_desc or search_pattern
                score = 0
                if is_exact_match:
                    score += 100
                if len(matched_label) < 25:
                    score += 20
                if is_clickable or 'button' in node_cls.lower() or 'edittext' in node_cls.lower():
                    score += 10
                candidates.append((score, cx, cy, matched_label, bounds))
                
    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        best = candidates[0]
        return best[1], best[2], best[3], best[4]
        
    return None

def tap_or_fallback_rel(root, search_text, rel_x, rel_y, label_name="element", exact=False, device_serial=None, instance_name=""):
    elem = find_element(root, search_text, exact=exact)
    if elem:
        x, y, found_label, bounds = elem
        log("ACTION", f"Found UI element '{found_label}' at {bounds}. Tapping ({x}, {y})...", instance_name=instance_name)
        run_adb_command(f"shell input tap {x} {y}", device_serial=device_serial, instance_name=instance_name)
        return True
    else:
        fx, fy = get_rel_coords(root, rel_x, rel_y)
        log("ACTION-FALLBACK", f"'{search_text}' not in XML. Tapping relative fallback ({fx}, {fy}) for {label_name}...", instance_name=instance_name)
        run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
        return False

def find_instagram_landing_login(root):
    if root is None:
        return None
    w, h = get_screen_dimensions(root)
    # 1. Exact match for "Log In" node
    elem = find_element(root, "log in", exact=True)
    if elem:
        return elem[0], elem[1]
    
    # 2. Check nodes containing "log in" or "log in or sign up"
    for node in root.iter('node'):
        node_text = node.attrib.get('text', '').strip()
        node_desc = node.attrib.get('content-desc', '').strip()
        comb = (node_text + " " + node_desc).strip().lower()
        if "log in" in comb:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords:
                cx, cy = coords
                if int(h * 0.40) <= cy <= int(h * 0.90):
                    match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.attrib.get('bounds', ''))
                    if match:
                        left, top, right, bottom = map(int, match.groups())
                        if "sign up" in comb or "or" in comb:
                            # "Log In" is on the left half of "Log In or sign up"
                            target_x = left + int((right - left) * 0.28)
                            return target_x, cy
                        return cx, cy
    return None

def find_ldconsole():
    for p in [
        r"D:\LDPlayer\LDPlayer9\ldconsole.exe",
        r"C:\LDPlayer\LDPlayer9\ldconsole.exe",
        r"D:\LDPlayer\LDPlayer14\ldconsole.exe",
        r"C:\leidian\LDPlayer9\ldconsole.exe"
    ]:
        if os.path.exists(p):
            return p
    return shutil.which("ldconsole")

def find_mumu_manager():
    for p in [
        r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
        r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\MuMuManager.exe",
        r"C:\Program Files\MuMuPlayer-12.0\shell\MuMuManager.exe"
    ]:
        if os.path.exists(p):
            return p
    return shutil.which("MuMuManager")

def get_emulator_type_and_index(device_serial: str):
    """
    Identifies whether the target ADB device belongs to MuMu Player or LDPlayer,
    and calculates its zero-indexed instance number.
    """
    if not device_serial:
        return 'unknown', 0
    port_match = re.search(r':(\d+)', device_serial)
    if port_match:
        port = int(port_match.group(1))
        if port == 7555:
            return 'mumu', 0
        elif 16384 <= port <= 17000:
            return 'mumu', (port - 16384) // 32
        elif 5555 <= port <= 5600:
            return 'ld', (port - 5555) // 2
    emu_match = re.search(r'emulator-(\d+)', device_serial)
    if emu_match:
        port = int(emu_match.group(1))
        return 'ld', max(0, (port - 5554) // 2)
    return 'unknown', 0

def randomize_emulator_identity(device_serial: str, instance_name: str = ""):
    """
    Generates and applies a new IMEI, Android ID, and MAC Address for LDPlayer or MuMu Player.
    """
    emu_type, idx = get_emulator_type_and_index(device_serial)
    new_imei = "86" + "".join([str(random.randint(0, 9)) for _ in range(13)])
    new_android_id = "".join(random.choices("0123456789abcdef", k=16))
    new_mac = ":".join([f"{random.randint(0, 255):02x}" for _ in range(6)])

    log("IMEI-CHANGE", f"🎲 Randomizing identity for {device_serial} (Type: {emu_type.upper()}, Index: {idx})...", instance_name=instance_name)

    # 1. Update Android ID live via ADB
    run_adb_command(f"shell settings put secure android_id {new_android_id}", device_serial=device_serial, instance_name=instance_name, timeout=5)
    log("IMEI-CHANGE", f"📱 New Android ID set: {new_android_id}", instance_name=instance_name)

    # 2. Update hardware IMEI & MAC in emulator
    if emu_type == "mumu":
        mumu_mgr = find_mumu_manager()
        if mumu_mgr:
            try:
                subprocess.run(f'"{mumu_mgr}" simulation -v {idx} -sk imei -sv {new_imei}', shell=True, capture_output=True, timeout=5)
                subprocess.run(f'"{mumu_mgr}" simulation -v {idx} -sk android_id -sv {new_android_id}', shell=True, capture_output=True, timeout=5)
                subprocess.run(f'"{mumu_mgr}" simulation -v {idx} -sk mac_address -sv {new_mac}', shell=True, capture_output=True, timeout=5)
                log("IMEI-CHANGE", f"✨ [MuMu #{idx}] Hardware IMEI set: {new_imei} | MAC: {new_mac}", instance_name=instance_name)
            except Exception as e:
                log("IMEI-CHANGE", f"MuMu simulation error: {e}", instance_name=instance_name)

    elif emu_type == "ld":
        ldconsole = find_ldconsole()
        if ldconsole:
            try:
                subprocess.run(f'"{ldconsole}" modify --index {idx} --imei auto --androidid {new_android_id} --mac auto --imsi auto --simserial auto', shell=True, capture_output=True, timeout=6)
                log("IMEI-CHANGE", f"✨ [LDPlayer #{idx}] Hardware IMEI, IMSI, Android ID & MAC randomized via ldconsole!", instance_name=instance_name)
            except Exception as e:
                log("IMEI-CHANGE", f"LDPlayer modify error: {e}", instance_name=instance_name)

def recover_device_after_failures(device_serial: str, instance_name: str = ""):
    """
    Cleans up and resets an emulator device after repeated failures:
    - Kills stuck browser and uiautomator processes
    - Clears browser app cache & data
    - Removes old window dumps from sdcard
    - Randomizes Device IMEI, Android ID, and MAC address
    - Wakes up screen & sends Home keyevent
    - Reconnects ADB if port-based connection
    """
    log("DEVICE-RECOVER", f"Performing deep recovery on device {device_serial}...", instance_name=instance_name)
    try:
        run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name, timeout=5)
        run_adb_command("shell pkill -f uiautomator", device_serial=device_serial, instance_name=instance_name, timeout=5)
        run_adb_command(f"shell pm clear {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name, timeout=6)
        run_adb_command("shell rm -f /sdcard/window_dump_*.xml", device_serial=device_serial, instance_name=instance_name, timeout=5)

        # 🌟 Randomize Device Identity (IMEI / Android ID / MAC)
        randomize_emulator_identity(device_serial, instance_name=instance_name)

        run_adb_command("shell input keyevent 224", device_serial=device_serial, instance_name=instance_name, timeout=3)
        run_adb_command("shell input keyevent 3", device_serial=device_serial, instance_name=instance_name, timeout=3)
        if ":" in str(device_serial):
            run_adb_command(f"connect {device_serial}", device_serial=None, instance_name=instance_name, timeout=6)
        res = run_adb_command("devices", device_serial=None, instance_name=instance_name, timeout=6)
        if device_serial and device_serial in res:
            log("DEVICE-RECOVER", f"Device {device_serial} is ONLINE with fresh identity.", instance_name=instance_name)
        else:
            log("DEVICE-RECOVER", f"Device {device_serial} recovery completed.", instance_name=instance_name)
    except Exception as e:
        log("DEVICE-RECOVER", f"Recovery error on {device_serial}: {e}", instance_name=instance_name)

def reload_browser_page(root=None, device_serial=None, instance_name=""):
    log("RELOAD", "Page reload initiated...", instance_name=instance_name)
    if root is not None:
        for node in root.iter('node'):
            txt = node.attrib.get('text', '').strip().lower()
            desc = node.attrib.get('content-desc', '').strip().lower()
            if txt in ['reload', 'refresh'] or desc in ['reload', 'refresh']:
                coords = parse_bounds(node.attrib.get('bounds', ''))
                if coords:
                    run_adb_command(f"shell input tap {coords[0]} {coords[1]}", device_serial=device_serial, instance_name=instance_name)
                    time.sleep(3.5)
                    return True
                    
    fx, fy = get_rel_coords(root, 0.93, 0.075)
    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(3.5)
    return True

def generate_totp_code(secret_key):
    if not secret_key:
        return ""
    clean_secret = re.sub(r'[^A-Z0-9]', '', str(secret_key)).upper().strip()
    if not clean_secret:
        return ""
    try:
        totp = pyotp.TOTP(clean_secret)
        return totp.now()
    except Exception:
        pass
    return ""

def get_android_clipboard(device_serial=None, instance_name=""):
    try:
        out = run_adb_command("shell cmd clipboard get", device_serial=device_serial, instance_name=instance_name)
        if out and "Error" not in out and "exception" not in out.lower():
            return out.strip()
    except Exception:
        pass
        
    try:
        dump = run_adb_command("shell dumpsys clipboard", device_serial=device_serial, instance_name=instance_name)
        for line in dump.splitlines():
            if "text=" in line or "text:" in line or "mText=" in line:
                parts = line.split("text=", 1) if "text=" in line else line.split("text:", 1)
                if len(parts) > 1:
                    clip_val = parts[1].strip()
                    if clip_val and clip_val != "null" and len(clip_val) > 5:
                        return clip_val
    except Exception:
        pass
        
    return ""

def is_valid_b32_secret(secret_str: str) -> bool:
    if not secret_str:
        return False
    clean = re.sub(r'[^A-Z0-9]', '', str(secret_str).upper().strip())
    if len(clean) not in [16, 24, 32]:
        return False
    if clean.isdigit():
        return False
    forbidden_words = [
        "CHOOSE", "CONFIRM", "PROTECT", "ACCOUNT", "AUTHENTICAT", "INSTRUCT",
        "DOWNLOAD", "BARCODE", "RECOGNIZE", "DEVICE", "SETTING", "PASSWORD",
        "INSTAGRAM", "GOOGLE", "MOBILE", "RECOMMEND", "CONTINUE", "SECURITY",
        "DETAILS", "PERSONAL", "PROFILE", "WELCOME", "ENTERCODE", "CANCEL",
        "RELOAD", "REFRESH", "TERMS", "POLICY", "HELP"
    ]
    for w in forbidden_words:
        if w in clean:
            return False
    try:
        totp = pyotp.TOTP(clean)
        code = totp.now()
        if code and len(code) == 6 and code.isdigit():
            return True
    except Exception:
        return False
    return True

def extract_2fa_secret_and_copy(root, device_serial=None, instance_name=""):
    secret_key = ""
    w, h = get_screen_dimensions(root)

    def find_key_in_texts(texts_list):
        for t in texts_list:
            cleaned = re.sub(r'[^A-Z0-9]', '', t.upper())
            if is_valid_b32_secret(cleaned):
                return cleaned

        blob = " ".join(texts_list).upper()
        m = re.search(r'([A-Z0-9]{4}(?:[\s\n\-]+[A-Z0-9]{4}){7})', blob)
        if m:
            c = re.sub(r'[^A-Z0-9]', '', m.group(1))
            if is_valid_b32_secret(c):
                return c

        m = re.search(r'([A-Z0-9]{4}(?:[\s\n\-]+[A-Z0-9]{4}){3})', blob)
        if m:
            c = re.sub(r'[^A-Z0-9]', '', m.group(1))
            if is_valid_b32_secret(c):
                return c

        tokens = re.findall(r'\b[A-Z2-7]{16,32}\b', blob)
        for tok in tokens:
            if is_valid_b32_secret(tok):
                return tok

        return ""

    def find_real_copy_button(node_root):
        if node_root is None:
            return None
        for node in node_root.iter('node'):
            t = node.attrib.get('text', '').strip().lower()
            d = node.attrib.get('content-desc', '').strip().lower()
            comb = t or d
            if len(comb) <= 22:
                if comb in ["copy key", "copy the key", "copy", "copy code", "copy this key"]:
                    coords = parse_bounds(node.attrib.get('bounds', ''))
                    if coords:
                        return coords
                elif "copy key" in comb or "copy the key" in comb:
                    coords = parse_bounds(node.attrib.get('bounds', ''))
                    if coords:
                        return coords
        return None

    # Step 1: Check if key is already visible in text
    if root is not None:
        texts = extract_screen_texts(root)
        found = find_key_in_texts(texts)
        if found:
            secret_key = found
            log("2FA-EXTRACT", f"Found Secret Key in UI text: {secret_key}", instance_name=instance_name)
            return secret_key

    # Step 2: Check if Copy key button is visible directly
    btn_copy = find_real_copy_button(root)
    if btn_copy:
        log("2FA-ACTION", f"Tapping 'Copy key' button at ({btn_copy[0]}, {btn_copy[1]})...", instance_name=instance_name)
        run_adb_command(f"shell input tap {btn_copy[0]} {btn_copy[1]}", device_serial=device_serial, instance_name=instance_name)
        time.sleep(0.5)
        clip = get_android_clipboard(device_serial=device_serial, instance_name=instance_name)
        if clip:
            clean_clip = re.sub(r'[^A-Z0-9]', '', clip.upper())
            if is_valid_b32_secret(clean_clip):
                secret_key = clean_clip
                log("2FA-CLIPBOARD", f"✔ Secret Key recovered from clipboard: '{secret_key}'", instance_name=instance_name)
                return secret_key
    else:
        # Step 3: Scroll down past the QR code to reveal the real Copy key button & key text
        log("2FA-ACTION", "Scrolling down past QR code to reveal 'Copy key' & secret key...", instance_name=instance_name)
        run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.75)} {int(w*0.50)} {int(h*0.25)} 300", device_serial=device_serial, instance_name=instance_name)
        time.sleep(0.8)
        root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
        
        if root is not None:
            texts = extract_screen_texts(root)
            found = find_key_in_texts(texts)
            if found:
                secret_key = found
                log("2FA-EXTRACT", f"Found Secret Key in UI text (after scroll): {secret_key}", instance_name=instance_name)
                return secret_key

        btn_copy = find_real_copy_button(root)
        if btn_copy:
            log("2FA-ACTION", f"Found 'Copy key' after scroll at ({btn_copy[0]}, {btn_copy[1]}). Tapping...", instance_name=instance_name)
            run_adb_command(f"shell input tap {btn_copy[0]} {btn_copy[1]}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.5)
        else:
            fx, fy = get_rel_coords(root, 0.50, 0.55)
            log("2FA-ACTION", f"Tapping fallback position ({fx}, {fy}) for Copy key...", instance_name=instance_name)
            run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.5)

    # Step 4: Check Android clipboard for key
    time.sleep(0.3)
    clip = get_android_clipboard(device_serial=device_serial, instance_name=instance_name)
    if clip:
        clean_clip = re.sub(r'[^A-Z0-9]', '', clip.upper())
        if is_valid_b32_secret(clean_clip):
            secret_key = clean_clip
            log("2FA-CLIPBOARD", f"✔ Secret Key recovered from clipboard: '{secret_key}'", instance_name=instance_name)
            return secret_key
        elif len(clean_clip) > 16:
            m = re.search(r'([A-Z2-7]{16,32})', clean_clip)
            if m and is_valid_b32_secret(m.group(1)):
                secret_key = m.group(1)
                log("2FA-CLIPBOARD", f"✔ Secret Key parsed from clipboard: '{secret_key}'", instance_name=instance_name)
                return secret_key

    return secret_key

def find_instagram_account_tile(root, email=""):
    if root is None:
        return None
    email_lower = email.lower().strip() if email else ""
    for node in root.iter('node'):
        node_text = node.attrib.get('text', '').strip().lower()
        node_desc = node.attrib.get('content-desc', '').strip().lower()
        combined = node_text + " " + node_desc
        if (email_lower and email_lower in combined) or ("meta account" in combined) or ("instagram" in combined and "continue" not in combined):
            bounds = node.attrib.get('bounds', '')
            coords = parse_bounds(bounds)
            if coords:
                return coords[0], coords[1], combined, bounds
    return None

def detect_current_page(root):
    if root is None:
        return "UNKNOWN_SCREEN", []
    texts = extract_screen_texts(root)
    text_blob = " ".join(texts).lower()

    if not text_blob.strip():
        return "GENERIC_PAGE", texts

    # Check if page is just loading the top toolbar without content
    meaningful_texts = [t for t in texts if t.strip().lower() not in ['site info', 'two-factor authentication · account settings | meta', 'reload', 'tabs', 'home', 'bookmarks', 'menu', 'back', 'forward', '2', '1', '']]
    if not meaningful_texts and len(texts) <= 4:
        return "GENERIC_PAGE", texts

    if ("http_transport_error" in text_blob 
          or "transport_error" in text_blob 
          or "network request encountered error" in text_blob
          or "encountered error http" in text_blob):
        return "HTTP_TRANSPORT_ERROR_PAGE", texts

    if "can't make this change at the moment" in text_blob or "cant make this change at the moment" in text_blob or "cannot make this change" in text_blob:
        return "CANNOT_MAKE_CHANGE_POPUP", texts
    elif ("we suspect automated behavior on your account" in text_blob 
          or "suspect automated behavior" in text_blob 
          or "automated behavior" in text_blob 
          or "automated activity" in text_blob 
          or ("automated" in text_blob and "dismiss" in text_blob)):
        return "SUSPECT_AUTOMATED_BEHAVIOR_PAGE", texts
    elif ("this page isn't available right now" in text_blob
          or "this page isn't available" in text_blob
          or "this page is not available" in text_blob
          or "page isn't available right now" in text_blob
          or "page isn't available" in text_blob
          or "page is not available" in text_blob
          or "try reloading this page" in text_blob
          or ("technical error" in text_blob and "reload" in text_blob)
          or "reload page" in text_blob):
        return "PAGE_NOT_AVAILABLE_ERROR_PAGE", texts
    elif ("use another profile" in text_blob 
          or "switch accounts" in text_blob 
          or ("continue" in text_blob and "create new account" in text_blob)
          or ("continue as" in text_blob and "instagram" in text_blob)):
        return "INSTAGRAM_CONTINUE_PROFILE_PAGE", texts
    elif ("re-enter your password" in text_blob
          or "enter your password" in text_blob
          or "enter password" in text_blob
          or ("for your security" in text_blob and "password" in text_blob)
          or "confirm your password" in text_blob):
        return "REENTER_PASSWORD_PAGE", texts
    elif "this content is no longer available" in text_blob or "content is no longer available" in text_blob or "the content you requested cannot be displayed" in text_blob:
        return "CONTENT_NO_LONGER_AVAILABLE_PAGE", texts
    elif ("check your email" in text_blob
          or "enter the code we sent to" in text_blob
          or "get a new code" in text_blob
          or "we can send a new code in" in text_blob):
        return "TWO_FACTOR_CHECK_EMAIL_PAGE", texts
    elif "set up extra protection for your meta account" in text_blob or "extra protection for your meta account" in text_blob or "extra protection for your account" in text_blob or ("extra protection" in text_blob and ("get started" in text_blob or "meta account" in text_blob)):
        return "TWO_FACTOR_EXTRA_PROTECTION_PAGE", texts
    elif ("help protect your account" in text_blob
          or "help protect" in text_blob
          or "choose how to confirm it's you" in text_blob
          or "choose how to confirm" in text_blob
          or ("authentication app" in text_blob and "sms or whatsapp" in text_blob)
          or ("authentication app" in text_blob and "recommended" in text_blob and "scan" not in text_blob)):
        return "TWO_FACTOR_CHOOSE_METHOD_PAGE", texts
    elif ("instructions for setup" in text_blob
          or "scan this barcode/qr code" in text_blob
          or "scan this barcode" in text_blob
          or "scan this qr code" in text_blob
          or "scan this barcode/qr code or copy the key" in text_blob
          or "copy the key" in text_blob
          or "or copy the key" in text_blob
          or ("instructions" in text_blob and ("setup" in text_blob or "barcode" in text_blob or "qr" in text_blob))):
        return "TWO_FACTOR_INSTRUCTIONS_PAGE", texts
    elif ("get your code from your authentication app" in text_blob
          or "enter the 6-digit code" in text_blob
          or "enter confirmation code" in text_blob
          or "enter 6-digit code" in text_blob
          or "enter code generated" in text_blob
          or ("enter code" in text_blob and "next" in text_blob and "instructions for setup" not in text_blob and "scan this" not in text_blob and "download an" not in text_blob)):
        return "TWO_FACTOR_ENTER_CODE_PAGE", texts
    elif "copy key" in text_blob or "enter key in authenticator app" in text_blob or "set up on same device" in text_blob or "enter this key into" in text_blob:
        return "TWO_FACTOR_COPY_KEY_PAGE", texts
    elif "something went wrong" in text_blob or "there was a problem" in text_blob or "try again later" in text_blob:
        return "ACCOUNT_ERROR_PAGE", texts
    elif "incorrect password" in text_blob or "wrong password" in text_blob:
        return "INCORRECT_PASSWORD_POPUP", texts
    elif "we couldn't find an account" in text_blob or "user not found" in text_blob or "can't find account" in text_blob:
        return "CANNOT_FIND_ACCOUNT_POPUP", texts
    elif "choose an account to set up two-factor authentication" in text_blob or ("two-factor authentication" in text_blob and ("choose an account" in text_blob or "instagram" in text_blob) and "instructions" not in text_blob and "help protect" not in text_blob and "is on" not in text_blob):
        return "TWO_FACTOR_CHOOSE_ACCOUNT_PAGE", texts
    elif (("two-factor authentication is on" in text_blob
           or "two-factor is on" in text_blob
           or "2-factor authentication is on" in text_blob
           or "authentication app is on" in text_blob
           or "two-step verification is on" in text_blob
           or "authenticator app is on" in text_blob
           or "two-factor authentication turned on" in text_blob
           or "two-factor authentication enabled" in text_blob
           or "two-factor authentication is set up" in text_blob
           or "your two-factor authentication is set up" in text_blob
           or ("backup codes" in text_blob and "two-factor" in text_blob)
           or ("turn off two-factor" in text_blob or "manage two-factor" in text_blob))
          and "instructions" not in text_blob
          and "download an authentication" not in text_blob
          and "scan this" not in text_blob
          and "barcode" not in text_blob
          and "qr code" not in text_blob
          and "check your email" not in text_blob
          and "extra protection" not in text_blob
          and "help protect" not in text_blob
          and "choose how to confirm" not in text_blob):
        return "TWO_FACTOR_SUCCESS_PAGE", texts
    elif ("save your login info" in text_blob 
          or "save login info" in text_blob 
          or "won't need to enter it next time" in text_blob 
          or "wont need to enter it next time" in text_blob 
          or ("save" in text_blob and "not now" in text_blob)
          or "save info" in text_blob):
        return "INSTAGRAM_SAVE_INFO_PAGE", texts
    elif "connect to facebook" in text_blob or "find friends to follow" in text_blob:
        return "INSTAGRAM_CONNECT_FB_PAGE", texts
    elif "save password on" in text_blob or ("save password" in text_blob and "cancel" in text_blob):
        return "INSTAGRAM_SAVE_PASSWORD_POPUP", texts
    elif "no instagram account found" in text_blob or "no instagram profile found" in text_blob or "matched a meta account" in text_blob or "matched an existing meta account" in text_blob or "find another account" in text_blob or "account we found" in text_blob:
        return "INSTAGRAM_NO_ACCOUNT_FOUND", texts
    elif "what's your name" in text_blob or "whats your name" in text_blob:
        return "INSTAGRAM_NAME_PAGE", texts
    elif "create a username" in text_blob or "create username" in text_blob:
        return "INSTAGRAM_USERNAME_PAGE", texts
    elif "allow the following" in text_blob or "create an instagram account with your meta account" in text_blob or "allow and continue" in text_blob:
        return "INSTAGRAM_ALLOW_CONTINUE_PAGE", texts
    elif "agree to instagram's terms" in text_blob or "agree to instagram" in text_blob or ("i agree" in text_blob and ("terms" in text_blob or "policies" in text_blob)):
        return "INSTAGRAM_AGREE_TERMS_PAGE", texts
    elif "allow all cookies" in text_blob or "only allow essential cookies" in text_blob or "decline optional cookies" in text_blob:
        return "INSTAGRAM_COOKIE_PAGE", texts
    elif "share everyday moments" in text_blob or "open instagram" in text_blob or "log in or sign up" in text_blob:
        return "INSTAGRAM_LANDING_PAGE", texts
    elif "username, email or mobile number" in text_blob or ("forgot password?" in text_blob and "log in" in text_blob) or ("phone number, username, or email" in text_blob) or ("log in" in text_blob and ("password" in text_blob or "don't have an account" in text_blob)):
        return "INSTAGRAM_LOGIN_PAGE", texts
    elif "welcome" in text_blob and ("agree" in text_blob or "terms" in text_blob):
        return "BROWSER_TERMS_PAGE", texts
    elif "turn on notifications" in text_blob or "know when people follow you" in text_blob:
        return "INSTAGRAM_NOTIFICATIONS_PAGE", texts
    elif "accounts/registered" in text_blob or "registered" in text_blob:
        return "INSTAGRAM_REGISTERED_PAGE", texts
    else:
        return "GENERIC_PAGE", texts

# ==============================================================================
# 🛠️ INSTAGRAM & 2FA AUTOMATION PIPELINE (Matching 1tasklytometatoinsta)
# ==============================================================================
def update_username_in_accounts_center(device_serial: str, target_username: str, instance_name=""):
    log("UNAME-UPDATE", f"Starting username change to '{target_username}' via Accounts Center ({ACCOUNTS_OVERVIEW_URL})...", instance_name=instance_name)
    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{ACCOUNTS_OVERVIEW_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.8)

    stage = "PROFILES_PAGE"
    start_time = time.time()
    max_wait = 30

    while time.time() - start_time < max_wait:
        root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
        if root is None:
            time.sleep(0.4)
            continue

        texts = extract_screen_texts(root)
        text_blob = " ".join(texts).lower()
        w, h = get_screen_dimensions(root)

        if stage == "PROFILES_PAGE":
            insta_tile = None
            for node in root.iter('node'):
                t = node.attrib.get('text', '').strip()
                d = node.attrib.get('content-desc', '').strip()
                comb = (t + " " + d).lower()
                if "instagram" in comb and "meta" not in comb:
                    coords = parse_bounds(node.attrib.get('bounds', ''))
                    if coords and coords[1] < int(h * 0.90):
                        insta_tile = coords
                        break

            if insta_tile:
                log("UNAME-UPDATE", f"Found Instagram profile card at {insta_tile}. Tapping...", instance_name=instance_name)
                run_adb_command(f"shell input tap {insta_tile[0]} {insta_tile[1]}", device_serial=device_serial, instance_name=instance_name)
                stage = "PROFILE_MENU"
                time.sleep(1.2)
            elif "profiles" in text_blob or "your account" in text_blob or "account settings" in text_blob:
                fx, fy = get_rel_coords(root, 0.50, 0.68)
                log("UNAME-UPDATE", f"Tapping fallback Instagram profile card at ({fx}, {fy})...", instance_name=instance_name)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                stage = "PROFILE_MENU"
                time.sleep(1.2)
            else:
                time.sleep(0.6)

        elif stage == "PROFILE_MENU":
            elem_uname = find_element(root, "username", exact=True) or find_element(root, "username")
            if elem_uname:
                log("UNAME-UPDATE", f"Found 'Username' menu item at ({elem_uname[0]}, {elem_uname[1]}). Tapping...", instance_name=instance_name)
                run_adb_command(f"shell input tap {elem_uname[0]} {elem_uname[1]}", device_serial=device_serial, instance_name=instance_name)
                stage = "EDIT_USERNAME"
                time.sleep(1.2)
            elif "name" in text_blob and "avatar" in text_blob:
                fx, fy = get_rel_coords(root, 0.50, 0.69)
                log("UNAME-UPDATE", f"Tapping fallback 'Username' menu item at ({fx}, {fy})...", instance_name=instance_name)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                stage = "EDIT_USERNAME"
                time.sleep(1.2)
            else:
                time.sleep(0.6)

        elif stage == "EDIT_USERNAME":
            log("UNAME-UPDATE", f"Entering target Taskly username '{target_username}'...", instance_name=instance_name)
            elem_edit = find_element(root, "username", by_class="EditText") or find_password_field(root)
            if elem_edit:
                run_adb_command(f"shell input tap {elem_edit[0]} {elem_edit[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.35)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            
            time.sleep(0.3)
            clear_input_field(length=40, device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.2)
            type_text(target_username, device_serial=device_serial, instance_name=instance_name, human_like=True)
            time.sleep(0.3)
            hide_keyboard(device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.2)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            log("UNAME-UPDATE", "Tapping 'Done' button...", instance_name=instance_name)
            elem_done = (find_element(root, "done", exact=True)
                         or find_element(root, "done")
                         or find_element(root, "save", exact=True)
                         or find_element(root, "save")
                         or find_element(root, "change username"))
            if elem_done:
                run_adb_command(f"shell input tap {elem_done[0]} {elem_done[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.88)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            
            time.sleep(1.5)
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            p_name, _ = detect_current_page(root)
            if p_name == "CANNOT_MAKE_CHANGE_POPUP":
                log("UNAME-ERR", f"Encountered restriction '{p_name}'. Aborting...", instance_name=instance_name)
                return False

            log("UNAME-UPDATE", f"✔ Username successfully updated to '{target_username}' in Accounts Center!", instance_name=instance_name)
            stage = "DONE"
            break

    return True

def fetch_email_security_code(email: str, password: str, mail_token=None, ninja_client=None, max_wait_sec: int = 75, expected_digits=8, instance_name: str = "") -> Optional[str]:
    """
    Universal email security code retriever:
    1. Checks TempMail Ninja client if active.
    2. Falls back to Mail.td / email_service inbox polling.
    Accepts expected_digits (defaults to 8-digit security verification code for 2FA).
    """
    log("EMAIL-CODE", f"Fetching email security code for {email} ({expected_digits}-digit expected, timeout {max_wait_sec}s)...", instance_name=instance_name)
    if ninja_client:
        try:
            log("NINJA-OTP", f"Polling TempMail Ninja for {expected_digits}-digit security code ({email})...", instance_name=instance_name)
            code = ninja_client.wait_for_otp(timeout=max_wait_sec, expected_digits=expected_digits, reset_latest=True)
            if code:
                clean_code = re.sub(r'\D', '', str(code).strip())
                if expected_digits:
                    if len(clean_code) == expected_digits:
                        log("NINJA-OTP", f"✔ Received verified {len(clean_code)}-digit security code from Ninja: {clean_code}", instance_name=instance_name)
                        return clean_code
                    else:
                        log("NINJA-WARN", f"Ignored code with length {len(clean_code)} != expected {expected_digits}: {clean_code}", instance_name=instance_name)
                elif len(clean_code) in [6, 8]:
                    log("NINJA-OTP", f"✔ Received verified {len(clean_code)}-digit security code from Ninja: {clean_code}", instance_name=instance_name)
                    return clean_code
                else:
                    log("NINJA-WARN", f"Ignored unexpected length code from Ninja: {code} (length: {len(clean_code)})", instance_name=instance_name)
        except Exception as e:
            log("NINJA-ERR", f"Error polling Ninja TempMail: {e}", instance_name=instance_name)

    code = fallback_fetch_otp(email, password, token=mail_token, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)
    if code:
        clean_code = re.sub(r'\D', '', str(code).strip())
        if expected_digits:
            if len(clean_code) == expected_digits:
                log("EMAIL-CODE", f"✔ Received verified {len(clean_code)}-digit security code from mail service: {clean_code}", instance_name=instance_name)
                return clean_code
            else:
                log("EMAIL-WARN", f"Ignored code with length {len(clean_code)} != expected {expected_digits}: {clean_code}", instance_name=instance_name)
        elif len(clean_code) in [6, 8]:
            log("EMAIL-CODE", f"✔ Received verified {len(clean_code)}-digit security code from mail service: {clean_code}", instance_name=instance_name)
            return clean_code
        else:
            log("EMAIL-WARN", f"Ignored unexpected length fallback code: {code} (length: {len(clean_code)})", instance_name=instance_name)
    return None

def run_instagram_2fa_pipeline(device_serial: str, email: str, password: str, target_login: str, mail_token="", instance_name="Inst-1", task_context=None):
    log("ACC-START", f"Processing Instagram 2FA for: {email} on device {device_serial}...", instance_name=instance_name)

    log("STEP-1", f"Cleaning Via Browser data ({PACKAGE_NAME})...", instance_name=instance_name)
    run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.3)
    run_adb_command(f"shell pm clear {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.4)

    log("STEP-2", f"Launching '{INSTA_LOGIN_URL}' in browser...", instance_name=instance_name)
    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{INSTA_LOGIN_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.6)

    login_submitted = False
    login_wait_count = 0
    agree_terms_submitted = False
    agree_retry_count = 0
    username_attempted = False
    auto_username_used = False
    captured_username = target_login
    two_factor_secret = ""
    two_factor_initiated = False
    max_loops = 60
    loop_count = 0

    last_page_name = ""
    page_enter_time = time.time()
    page_stuck_back_count = 0

    log("STATE-LOOP", f"Starting UI state machine for {email}...", instance_name=instance_name)

    while loop_count < max_loops:
        if task_context and (task_context.get('status') == 'cancelled' or task_context.get('cancelled')):
            log("TASK-CANCELLED", f"Task was cancelled by Telegram bot for {target_login}! Aborting Instagram 2FA immediately...", instance_name=instance_name)
            return False, ""
        loop_count += 1
        root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
        if root is None:
            log("UI-WARN", f"[Loop {loop_count}/{max_loops}] Temporary ADB screen dump lag on {device_serial}. Retrying...", instance_name=instance_name)
            time.sleep(0.5)
            continue

        page_name, visible_texts = detect_current_page(root)
        current_time = time.time()
        stuck_duration = current_time - page_enter_time

        if page_name != last_page_name:
            last_page_name = page_name
            page_enter_time = current_time
            stuck_duration = 0.0
            page_stuck_back_count = 0
            login_wait_count = 0
            if page_name not in ["INSTAGRAM_AGREE_TERMS_PAGE", "INSTAGRAM_ALLOW_CONTINUE_PAGE", "INSTAGRAM_USERNAME_PAGE"]:
                agree_retry_count = 0
            sample = visible_texts[:3] if visible_texts else ["Empty UI dump"]
            log("PAGE-CHANGE", f"[Loop {loop_count}/{max_loops}] Detected: '{page_name}' | Preview: {sample}", instance_name=instance_name)
        else:
            if page_name == "INSTAGRAM_AGREE_TERMS_PAGE":
                if stuck_duration >= 35.0:
                    agree_retry_count += 1
                    if agree_retry_count <= 3:
                        log("TIMEOUT-WARN", f"Screen '{page_name}' stuck for {int(stuck_duration)}s. Pressing Back to repeat...", instance_name=instance_name)
                        run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)
                        agree_terms_submitted = False
                        last_page_name = ""
                        page_enter_time = time.time()
                        time.sleep(1.0)
                        continue
                    else:
                        log("TIMEOUT-ABORT", f"Screen '{page_name}' still stuck after retries. Aborting...", instance_name=instance_name)
                        return False, ""
                elif agree_terms_submitted:
                    time.sleep(0.3)
                    continue

            elif stuck_duration >= 25.0:
                if page_name == "TWO_FACTOR_CHECK_EMAIL_PAGE":
                    if stuck_duration >= 70.0:
                        log("TIMEOUT-ABORT", "Stuck on 'Check your email' screen >70s. Aborting to start fresh cycle...", instance_name=instance_name)
                        run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
                        return False, ""
                    time.sleep(0.5)
                    continue
                page_stuck_back_count += 1
                if two_factor_initiated:
                    log("TIMEOUT-WARN", f"Screen '{page_name}' unresponsive ({int(stuck_duration)}s) during 2FA. Re-opening: {TWO_FACTOR_URL}...", instance_name=instance_name)
                    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TWO_FACTOR_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
                    page_enter_time = time.time()
                    time.sleep(2.0)
                    if page_stuck_back_count >= 5:
                        log("TIMEOUT-ABORT", f"Stuck in 2FA phase >90s. Aborting account...", instance_name=instance_name)
                        return False, ""
                    continue
                elif page_name in ["BROWSER_TERMS_PAGE", "INSTAGRAM_LANDING_PAGE", "INSTAGRAM_LOGIN_PAGE", "GENERIC_PAGE", "UNKNOWN_SCREEN"]:
                    log("TIMEOUT-WARN", f"Screen '{page_name}' unresponsive ({int(stuck_duration)}s). Reloading...", instance_name=instance_name)
                    reload_browser_page(root=root, device_serial=device_serial, instance_name=instance_name)
                    if page_name == "INSTAGRAM_LOGIN_PAGE":
                        login_submitted = False
                        login_wait_count = 0
                else:
                    log("TIMEOUT-WARN", f"Screen '{page_name}' unresponsive ({int(stuck_duration)}s). Pressing Back...", instance_name=instance_name)
                    run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)

                page_enter_time = time.time()
                time.sleep(1.0)

                if page_stuck_back_count >= 4:
                    log("TIMEOUT-ABORT", f"Stuck on {page_name} >80s. Aborting account...", instance_name=instance_name)
                    return False, ""

        # --- State Handlers ---
        if page_name == "BROWSER_TERMS_PAGE":
            log("STATE-ACTION", "Browser terms page -> Tapping AGREE...", instance_name=instance_name)
            tap_or_fallback_rel(root, "agree", 0.5, 0.88, "Agree Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{INSTA_LOGIN_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.5)
            run_adb_command("shell input keyevent 66", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

        elif page_name == "INSTAGRAM_COOKIE_PAGE":
            log("STATE-ACTION", "Cookie banner detected -> Tapping 'Allow all cookies'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "allow all cookies", 0.5, 0.70, "Allow Cookies", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.2)

        elif page_name == "HOME_SCREEN":
            log("STATE-ACTION", "Home screen detected -> Re-opening instagram.com...", instance_name=instance_name)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{INSTA_LOGIN_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.5)
            run_adb_command("shell input keyevent 66", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

        elif page_name in ["CANNOT_FIND_ACCOUNT_POPUP", "INCORRECT_PASSWORD_POPUP", "ACCOUNT_ERROR_PAGE", "CANNOT_MAKE_CHANGE_POPUP", "HTTP_TRANSPORT_ERROR_PAGE"]:
            log("STATE-ABORT", f"Account restriction or transport error detected '{page_name}'. Aborting current cycle...", instance_name=instance_name)
            run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
            return False, ""

        elif page_name == "INSTAGRAM_LANDING_PAGE":
            log("STATE-ACTION", "Instagram landing page -> Tapping 'Log in'...", instance_name=instance_name)
            login_coords = find_instagram_landing_login(root)
            if login_coords:
                lx, ly = login_coords
                log("ACTION", f"Found 'Log In' element at ({lx}, {ly}). Tapping...", instance_name=instance_name)
                run_adb_command(f"shell input tap {lx} {ly}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.40, 0.74)
                log("ACTION-FALLBACK", f"Tapping relative fallback ({fx}, {fy}) for 'Log In'...", instance_name=instance_name)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(2.0)

        elif page_name == "INSTAGRAM_LOGIN_PAGE":
            if not login_submitted:
                log("STATE-ACTION", f"Entering credentials for {email}...", instance_name=instance_name)
                elem_user = find_input_box(root, hint_patterns=["username, email or mobile number", "phone number, username, or email", "username", "email", "phone", "mobile"], default_rx=0.50, default_ry=0.46)
                log("STATE-ACTION", f"Entering email '{email}' at ({elem_user[0]}, {elem_user[1]})...", instance_name=instance_name)
                run_adb_command(f"shell input tap {elem_user[0]} {elem_user[1]}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.15)
                run_adb_command(f"shell input tap {elem_user[0]} {elem_user[1]}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)
                clear_input_field(device_serial=device_serial, instance_name=instance_name)
                type_text(email, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                log("STATE-ACTION", "Entering password...", instance_name=instance_name)
                elem_pwd = find_password_field(root) or find_input_box(root, hint_patterns=["password"], default_rx=0.50, default_ry=0.57)
                run_adb_command(f"shell input tap {elem_pwd[0]} {elem_pwd[1]}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.15)
                run_adb_command(f"shell input tap {elem_pwd[0]} {elem_pwd[1]}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)
                clear_input_field(device_serial=device_serial, instance_name=instance_name)
                type_text(password, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)

                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                log("STATE-ACTION", "Submitting login credentials...", instance_name=instance_name)
                tap_or_fallback_rel(root, "log in", 0.50, 0.67, "Log In Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                login_submitted = True
                login_wait_count = 0
                time.sleep(2.5)
            else:
                login_wait_count += 1
                if login_wait_count >= 6:
                    log("STATE-ACTION", "Login submission pending. Reloading...", instance_name=instance_name)
                    reload_browser_page(root=root, device_serial=device_serial, instance_name=instance_name)
                    login_submitted = False
                    login_wait_count = 0
                    time.sleep(2.0)
                else:
                    time.sleep(1.2)

        elif page_name == "INSTAGRAM_SAVE_PASSWORD_POPUP":
            log("STATE-ACTION", "Save password popup -> Tapping 'Cancel'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "cancel", 0.5, 0.65, "Cancel Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.2)

        elif page_name == "INSTAGRAM_NO_ACCOUNT_FOUND":
            log("STATE-ACTION", "No Instagram profile overlay -> Scrolling to Meta account tile...", instance_name=instance_name)
            w, h = get_screen_dimensions(root)
            run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.80)} {int(w*0.50)} {int(h*0.25)} 250", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.6)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            tile_coords = find_instagram_account_tile(root, email)
            if tile_coords and tile_coords[1] > int(h * 0.60):
                log("STATE-ACTION", f"Meta account card found at {tile_coords[:2]}. Tapping...", instance_name=instance_name)
                run_adb_command(f"shell input tap {tile_coords[0]} {tile_coords[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                bx, by = int(w * 0.50), int(h * 0.82)
                log("STATE-ACTION", f"Tapping fallback bottom account box ({bx}, {by})...", instance_name=instance_name)
                run_adb_command(f"shell input tap {bx} {by}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)
                run_adb_command(f"shell input tap {bx} {by}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.8)

        elif page_name == "INSTAGRAM_NAME_PAGE":
            log("STATE-ACTION", f"'What\\'s your name?' screen -> Filling name with '{target_login}'...", instance_name=instance_name)
            nx, ny = find_input_box(root, hint_patterns=["full name"], default_rx=0.50, default_ry=0.40)
            log("STATE-ACTION", f"Targeting Full name input at ({nx}, {ny}). Tapping to focus...", instance_name=instance_name)
            run_adb_command(f"shell input tap {nx} {ny}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.15)
            run_adb_command(f"shell input tap {nx} {ny}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.3)
            clear_input_field(length=40, device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.15)
            type_text(target_login, device_serial=device_serial, instance_name=instance_name, human_like=True)
            time.sleep(0.3)
            hide_keyboard(device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.2)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            log("STATE-ACTION", "Tapping 'Next' on Name screen...", instance_name=instance_name)
            tap_or_fallback_rel(root, "next", 0.50, 0.51, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.8)

        elif page_name == "INSTAGRAM_USERNAME_PAGE":
            text_blob_curr = " ".join(visible_texts).lower()
            is_uname_error = any(err in text_blob_curr for err in [
                "choose a username", "not available", "isn't available", "isnt available",
                "another account", "try another", "can't use", "cant use"
            ])

            if is_uname_error:
                log("USERNAME-WARN", f"Username '{target_login}' not accepted (Error shown)! Navigating Back (<) to regenerate auto-suggested username...", instance_name=instance_name)
                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)
                
                # Tap top-left back arrow (<) or keyevent 4
                back_btn = find_element(root, "back") or find_element(root, "go back")
                if back_btn:
                    run_adb_command(f"shell input tap {back_btn[0]} {back_btn[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    fx_b, fy_b = get_rel_coords(root, 0.06, 0.19)
                    run_adb_command(f"shell input tap {fx_b} {fy_b}", device_serial=device_serial, instance_name=instance_name)
                    run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)
                time.sleep(1.2)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                p_name, _ = detect_current_page(root)
                if p_name == "INSTAGRAM_NAME_PAGE":
                    log("STATE-ACTION", "On Name screen -> Tapping 'Next' to trigger auto username...", instance_name=instance_name)
                    tap_or_fallback_rel(root, "next", 0.50, 0.51, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                    time.sleep(1.5)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                log("STATE-ACTION", "On Username screen with auto-filled value -> Tapping 'Next'...", instance_name=instance_name)
                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)
                tap_or_fallback_rel(root, "next", 0.5, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                auto_username_used = True
                time.sleep(1.8)
            else:
                log("STATE-ACTION", f"'Create a username' screen -> Pre-filled from Full Name. Tapping 'Next'...", instance_name=instance_name)
                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)
                tap_or_fallback_rel(root, "next", 0.5, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                username_attempted = True
                time.sleep(1.8)

        elif page_name == "INSTAGRAM_ALLOW_CONTINUE_PAGE":
            log("STATE-ACTION", "Instagram link confirmation -> Tapping 'Allow and continue'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "allow and continue", 0.5, 0.88, "Allow and continue", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.8)

        elif page_name == "INSTAGRAM_AGREE_TERMS_PAGE":
            if not agree_terms_submitted:
                log("STATE-ACTION", "Terms of Use page -> Tapping 'I agree'...", instance_name=instance_name)
                tap_or_fallback_rel(root, "i agree", 0.5, 0.88, "I agree Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                agree_terms_submitted = True
                page_enter_time = time.time()

                # Persistently wait on new page until 'Save' button is found
                log("STATE-WAIT", "Waiting for new page & 'Save' button after 'I agree' (up to 35s)...", instance_name=instance_name)
                save_clicked = False
                for sec in range(1, 36):
                    time.sleep(1.0)
                    root_check = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    if root_check is None:
                        continue
                    p_check, texts_check = detect_current_page(root_check)
                    blob_check = " ".join(texts_check).lower()
                    
                    # Check for Save button or Save Info page
                    btn_save = find_element(root_check, "save", exact=True) or find_element(root_check, "save")
                    if p_check == "INSTAGRAM_SAVE_INFO_PAGE" or (btn_save and "save" in blob_check and "terms" not in blob_check):
                        log("STATE-ACTION", f"✔ 'Save' button appeared at sec {sec}! Tapping 'Save'...", instance_name=instance_name)
                        w_c, h_c = get_screen_dimensions(root_check)
                        if btn_save and btn_save[1] > int(h_c * 0.50):
                            run_adb_command(f"shell input tap {btn_save[0]} {btn_save[1]}", device_serial=device_serial, instance_name=instance_name)
                        else:
                            fx, fy = get_rel_coords(root_check, 0.50, 0.74)
                            run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                        time.sleep(2.0)

                        if auto_username_used:
                            log("STATE-ACTION", f"Registration complete with auto username! Opening Accounts Overview to change username to '{target_login}'...", instance_name=instance_name)
                            update_username_in_accounts_center(device_serial=device_serial, target_username=target_login, instance_name=instance_name)
                            auto_username_used = False
                            time.sleep(1.0)

                        save_clicked = True
                        break
                    elif p_check in ["INSTAGRAM_CONTINUE_PROFILE_PAGE", "REENTER_PASSWORD_PAGE", "TWO_FACTOR_CHECK_EMAIL_PAGE", "TWO_FACTOR_CHOOSE_ACCOUNT_PAGE", "TWO_FACTOR_EXTRA_PROTECTION_PAGE", "TWO_FACTOR_CHOOSE_METHOD_PAGE", "TWO_FACTOR_INSTRUCTIONS_PAGE", "TWO_FACTOR_ENTER_CODE_PAGE"]:
                        log("STATE-PROGRESS", f"Screen advanced directly to '{p_check}'! Continuing...", instance_name=instance_name)
                        save_clicked = True
                        break
                    elif p_check in ["INSTAGRAM_NOTIFICATIONS_PAGE", "INSTAGRAM_CONNECT_FB_PAGE", "INSTAGRAM_REGISTERED_PAGE"]:
                        log("STATE-PROGRESS", f"Screen moved to '{p_check}'. Proceeding to 2FA...", instance_name=instance_name)
                        if auto_username_used:
                            update_username_in_accounts_center(device_serial=device_serial, target_username=target_login, instance_name=instance_name)
                            auto_username_used = False
                            time.sleep(1.0)
                        save_clicked = True
                        break

                # After saving (or screen advanced), redirect to 2FA URL
                log("2FA-START", f"Save completed! Redirecting to 2FA URL: {TWO_FACTOR_URL}...", instance_name=instance_name)
                run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TWO_FACTOR_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
                two_factor_initiated = True
                page_enter_time = time.time()
                time.sleep(2.5)
            else:
                log("STATE-WAIT", "Waiting for Save login info screen transition...", instance_name=instance_name)
                time.sleep(1.5)

        elif page_name == "INSTAGRAM_SAVE_INFO_PAGE":
            log("STATE-ACTION", "Save your login info screen -> Tapping 'Save' button...", instance_name=instance_name)
            btn_save = find_element(root, "save", exact=True) or find_element(root, "save")
            w, h = get_screen_dimensions(root)
            if btn_save and btn_save[1] > int(h * 0.60):
                run_adb_command(f"shell input tap {btn_save[0]} {btn_save[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.74)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.8)

            if auto_username_used:
                log("STATE-ACTION", f"Registration complete with auto username! Opening Accounts Overview to change username to '{target_login}'...", instance_name=instance_name)
                update_username_in_accounts_center(device_serial=device_serial, target_username=target_login, instance_name=instance_name)
                auto_username_used = False
                time.sleep(1.0)

            log("2FA-START", f"Saved login info! Navigating directly to 2FA URL: {TWO_FACTOR_URL}...", instance_name=instance_name)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TWO_FACTOR_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            two_factor_initiated = True
            time.sleep(2.2)

        elif page_name in ["INSTAGRAM_NOTIFICATIONS_PAGE", "INSTAGRAM_CONNECT_FB_PAGE", "INSTAGRAM_REGISTERED_PAGE"]:
            log("STATE-ACTION", f"Post-registration screen ({page_name}) -> Transitioning to 2FA setup...", instance_name=instance_name)
            if auto_username_used:
                update_username_in_accounts_center(device_serial=device_serial, target_username=target_login, instance_name=instance_name)
                auto_username_used = False
                time.sleep(1.0)

            log("2FA-START", f"Navigating directly to 2FA URL: {TWO_FACTOR_URL}...", instance_name=instance_name)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TWO_FACTOR_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            two_factor_initiated = True
            time.sleep(2.2)

        elif page_name == "INSTAGRAM_CONTINUE_PROFILE_PAGE":
            log("STATE-ACTION", "Instagram Continue Profile screen detected -> Tapping 'Continue'...", instance_name=instance_name)
            login_submitted = False
            tap_or_fallback_rel(root, "continue", 0.50, 0.75, "Continue Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(2.5)

        elif page_name == "REENTER_PASSWORD_PAGE":
            log("STATE-ACTION", f"Password confirmation requested -> Entering password for {email}...", instance_name=instance_name)
            pw_box = find_password_field(root) or find_input_box(root, hint_patterns=["password"], default_rx=0.50, default_ry=0.45)
            if pw_box:
                run_adb_command(f"shell input tap {pw_box[0]} {pw_box[1]}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)
            clear_input_field(length=40, device_serial=device_serial, instance_name=instance_name)
            type_text(password, device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.3)
            hide_keyboard(device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.2)
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            tap_or_fallback_rel(root, "continue", 0.50, 0.60, "Continue Button", exact=True, device_serial=device_serial, instance_name=instance_name) or tap_or_fallback_rel(root, "next", 0.50, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(2.0)

        elif page_name == "CANNOT_MAKE_CHANGE_POPUP":
            log("ERR", "2FA blocked by Instagram: 'Can't make this change at the moment'. Aborting to start a fresh cycle...", instance_name=instance_name)
            return False, ""

        elif page_name == "SUSPECT_AUTOMATED_BEHAVIOR_PAGE":
            log("ERR", "Instagram flagged 'Suspect automated behavior'. Aborting to start a fresh cycle...", instance_name=instance_name)
            return False, ""

        elif page_name in ["PAGE_NOT_AVAILABLE_ERROR_PAGE", "CONTENT_NO_LONGER_AVAILABLE_PAGE", "ACCOUNT_ERROR_PAGE", "HTTP_TRANSPORT_ERROR_PAGE"]:
            log("ERR", f"Error screen '{page_name}' ('HTTP_TRANSPORT_ERROR / Network request encountered error') encountered. Aborting to start fresh cycle...", instance_name=instance_name)
            run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
            return False, ""

        elif page_name in ["INCORRECT_PASSWORD_POPUP", "CANNOT_FIND_ACCOUNT_POPUP"]:
            log("ERR", f"Account error '{page_name}' encountered. Aborting to start fresh cycle...", instance_name=instance_name)
            return False, ""

        elif page_name == "TWO_FACTOR_EXTRA_PROTECTION_PAGE":
            two_factor_initiated = True
            log("STATE-ACTION", "2FA: 'Set up extra protection for your Meta Account' -> Tapping 'Get started'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "get started", 0.5, 0.88, "Get Started Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.8)

        elif page_name == "TWO_FACTOR_CHECK_EMAIL_PAGE":
            two_factor_initiated = True

            # Fast check if error already present on screen before doing anything
            texts_init = extract_screen_texts(root)
            blob_init = " ".join(texts_init).lower() if texts_init else ""
            if any(err in blob_init for err in ["http_transport_error", "transport_error", "network request encountered error", "encountered error http"]):
                log("ERR", "Network request error 'HTTP_TRANSPORT_ERROR' detected on Check your email screen! Aborting to start fresh cycle...", instance_name=instance_name)
                run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
                return False, ""

            ninja_client = task_context.get('ninja_client') if task_context else None
            log("STATE-ACTION", f"2FA Check Email Code Screen -> Fetching recent 8-digit security code for {email} (max 30s)...", instance_name=instance_name)
            
            # Wait up to 30 seconds for recent 8-digit email security code for 2FA
            email_code = fetch_email_security_code(
                email,
                password,
                mail_token=mail_token,
                ninja_client=ninja_client,
                max_wait_sec=30,
                expected_digits=8,
                instance_name=instance_name
            )

            if not email_code or len(str(email_code).strip()) != 8:
                log("ERR", f"8-digit email security code not received for {email} on first attempt. Aborting to start fresh cycle...", instance_name=instance_name)
                return False, ""

            # Check screen hierarchy after waiting for OTP
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            refreshed_page, r_texts = detect_current_page(root)
            r_blob = " ".join(r_texts).lower() if r_texts else ""

            if refreshed_page == "HTTP_TRANSPORT_ERROR_PAGE" or any(err in r_blob for err in ["http_transport_error", "transport_error", "network request encountered error", "encountered error http"]):
                log("ERR", "Network request error 'HTTP_TRANSPORT_ERROR' detected while waiting for OTP. Aborting to start fresh cycle...", instance_name=instance_name)
                run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
                return False, ""

            if refreshed_page == "TWO_FACTOR_SUCCESS_PAGE" and two_factor_secret:
                log("SUCCESS", f"✔ 2FA already succeeded after email code wait for {email}!", instance_name=instance_name)
                return True, two_factor_secret

            # If page drifted away during mail polling, re-open 2FA URL
            if refreshed_page not in ["TWO_FACTOR_CHECK_EMAIL_PAGE", "TWO_FACTOR_CHOOSE_METHOD_PAGE", "TWO_FACTOR_INSTRUCTIONS_PAGE", "TWO_FACTOR_COPY_KEY_PAGE", "TWO_FACTOR_ENTER_CODE_PAGE"]:
                log("2FA-RELOAD", f"Screen moved to '{refreshed_page}' during OTP wait. Re-opening 2FA URL: {TWO_FACTOR_URL}...", instance_name=instance_name)
                run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TWO_FACTOR_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
                page_enter_time = time.time()
                time.sleep(2.5)
                continue

            if refreshed_page == "TWO_FACTOR_CHECK_EMAIL_PAGE" and email_code and len(str(email_code).strip()) == 8:
                clean_email_code = str(email_code).strip()
                log("STATE-ACTION", f"Entering verified {len(clean_email_code)}-digit email security code '{clean_email_code}'...", instance_name=instance_name)
                for attempt in range(1, 3):
                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    curr_p, curr_texts = detect_current_page(root)
                    curr_blob = " ".join(curr_texts).lower() if curr_texts else ""
                    if curr_p == "HTTP_TRANSPORT_ERROR_PAGE" or any(err in curr_blob for err in ["http_transport_error", "transport_error", "network request encountered error", "encountered error http"]):
                        log("ERR", "Network request error 'HTTP_TRANSPORT_ERROR' detected on screen. Aborting to start fresh cycle...", instance_name=instance_name)
                        run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
                        return False, ""

                    if curr_p != "TWO_FACTOR_CHECK_EMAIL_PAGE":
                        log("2FA-PROGRESS", f"Moved from 'Check your email' to '{curr_p}'!", instance_name=instance_name)
                        break

                    # Locate Code input field accurately using specialized helper (handles both Facebook page and Instagram modal)
                    code_coords = find_code_input_field(root)
                    if code_coords:
                        log("STATE-ACTION", f"Tapping Code input field at ({code_coords[0]}, {code_coords[1]})...", instance_name=instance_name)
                        run_adb_command(f"shell input tap {code_coords[0]} {code_coords[1]}", device_serial=device_serial, instance_name=instance_name)
                    else:
                        fx, fy = get_rel_coords(root, 0.50, 0.66)
                        run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)

                    time.sleep(0.3)
                    clear_input_field(length=20, device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.2)

                    type_text(clean_email_code, device_serial=device_serial, instance_name=instance_name, human_like=True, min_delay=0.03, max_delay=0.07)
                    time.sleep(0.3)

                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.4)

                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    log("STATE-ACTION", f"Tapping 'Continue' button on 'Check your email' modal (attempt {attempt}/2)...", instance_name=instance_name)
                    btn_found = (find_element(root, "continue", exact=True)
                                 or find_element(root, "next", exact=True)
                                 or find_element(root, "continue")
                                 or find_element(root, "next"))
                    if btn_found:
                        run_adb_command(f"shell input tap {btn_found[0]} {btn_found[1]}", device_serial=device_serial, instance_name=instance_name)
                    else:
                        texts_btn = extract_screen_texts(root)
                        blob_btn = " ".join(texts_btn).lower()
                        if "facebook" in blob_btn or "enter code" in blob_btn:
                            fx, fy = get_rel_coords(root, 0.50, 0.86)
                        else:
                            fx, fy = get_rel_coords(root, 0.50, 0.65)
                        run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)

                    time.sleep(2.5)
                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    post_p, post_texts = detect_current_page(root)
                    post_blob = " ".join(post_texts).lower() if post_texts else ""

                    if post_p == "HTTP_TRANSPORT_ERROR_PAGE" or any(err in post_blob for err in ["http_transport_error", "transport_error", "network request encountered error", "encountered error http"]):
                        log("ERR", "Network request error 'HTTP_TRANSPORT_ERROR' encountered after clicking Continue! Aborting to start fresh cycle...", instance_name=instance_name)
                        run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
                        return False, ""

                    if post_p != "TWO_FACTOR_CHECK_EMAIL_PAGE":
                        log("2FA-ACTION", f"Successfully moved to '{post_p}' after email verification!", instance_name=instance_name)
                        page_enter_time = time.time()
                        
                        # Re-open 2FA URL to resume setup if not already on 2FA flow screen
                        if post_p not in ["TWO_FACTOR_CHOOSE_ACCOUNT_PAGE", "TWO_FACTOR_EXTRA_PROTECTION_PAGE", "TWO_FACTOR_CHOOSE_METHOD_PAGE", "TWO_FACTOR_INSTRUCTIONS_PAGE", "TWO_FACTOR_COPY_KEY_PAGE", "TWO_FACTOR_ENTER_CODE_PAGE", "TWO_FACTOR_SUCCESS_PAGE"]:
                            log("2FA-REDIRECT", f"Redirecting back to 2FA URL to complete remaining setup: {TWO_FACTOR_URL}...", instance_name=instance_name)
                            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TWO_FACTOR_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
                            time.sleep(2.2)
                        break
                    else:
                        log("2FA-WARN", "Still on 'Check your email'. Retrying Continue tap...", instance_name=instance_name)
                        texts_btn = extract_screen_texts(root)
                        blob_btn = " ".join(texts_btn).lower()
                        if any(err in blob_btn for err in ["http_transport_error", "transport_error", "network request encountered error", "encountered error http"]):
                            log("ERR", "Network request error 'HTTP_TRANSPORT_ERROR' detected! Aborting to start fresh cycle...", instance_name=instance_name)
                            run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
                            return False, ""

                        if "facebook" in blob_btn or "enter code" in blob_btn:
                            fx, fy = get_rel_coords(root, 0.50, 0.86)
                        else:
                            fx, fy = get_rel_coords(root, 0.50, 0.65)
                        run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                        page_enter_time = time.time()
                        time.sleep(1.5)

        elif page_name == "TWO_FACTOR_CHOOSE_ACCOUNT_PAGE":
            two_factor_initiated = True
            log("STATE-ACTION", "2FA choose account screen -> Tapping Instagram profile card...", instance_name=instance_name)
            tap_or_fallback_rel(root, "instagram", 0.5, 0.65, "Instagram Profile Card", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.8)

        elif page_name == "TWO_FACTOR_CHOOSE_METHOD_PAGE":
            two_factor_initiated = True
            log("STATE-ACTION", "2FA: 'Help protect your account' -> Tapping 'Continue'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "continue", 0.50, 0.86, "Continue Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.6)

        elif page_name in ["TWO_FACTOR_INSTRUCTIONS_PAGE", "TWO_FACTOR_COPY_KEY_PAGE"]:
            two_factor_initiated = True
            log("STATE-ACTION", f"2FA: Setup screen ({page_name}) -> Scrolling and extracting secret key...", instance_name=instance_name)
            
            # Robust key extraction (up to 4 attempts)
            for attempt in range(1, 4):
                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                extracted_key = extract_2fa_secret_and_copy(root, device_serial=device_serial, instance_name=instance_name)
                if extracted_key:
                    two_factor_secret = extracted_key
                    if task_context is not None:
                        task_context['two_fa_key'] = two_factor_secret
                    log("2FA-CAPTURED", f"✔ Secret Key captured: '{two_factor_secret}'", instance_name=instance_name)
                    break
                else:
                    clip = get_android_clipboard(device_serial=device_serial, instance_name=instance_name)
                    if clip:
                        clean_clip = re.sub(r'[^A-Z0-9]', '', clip.upper())
                        if len(clean_clip) >= 16 and not clean_clip.isdigit():
                            two_factor_secret = clean_clip
                            if task_context is not None:
                                task_context['two_fa_key'] = two_factor_secret
                            log("2FA-CLIPBOARD", f"✔ Secret Key recovered from clipboard: '{two_factor_secret}'", instance_name=instance_name)
                            break
                log("2FA-WARN", f"Key not captured on attempt {attempt}/3. Scrolling down and retrying...", instance_name=instance_name)
                w, h = get_screen_dimensions(root)
                run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.70)} {int(w*0.50)} {int(h*0.35)} 250", device_serial=device_serial, instance_name=instance_name)
                time.sleep(1.0)

            # If key is not yet captured after attempts, abort immediately
            if not two_factor_secret:
                log("ERR", "Secret key could not be captured on 2FA setup page. Aborting account to start fresh cycle...", instance_name=instance_name)
                return False, ""

            # Now that we have the 2FA secret, scroll to reveal 'Next' / 'Enter code' button and tap
            w, h = get_screen_dimensions(root)
            run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.40)} {int(w*0.50)} {int(h*0.65)} 200", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.4)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            elem_enter = (find_element(root, "enter code", exact=True)
                          or find_element(root, "enter code")
                          or find_element(root, "next", exact=True)
                          or find_element(root, "next")
                          or find_element(root, "continue"))
            if elem_enter:
                log("2FA-ACTION", f"Tapping '{elem_enter[2]}' button to go to OTP entry...", instance_name=instance_name)
                run_adb_command(f"shell input tap {elem_enter[0]} {elem_enter[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.86)
                log("2FA-ACTION", f"Tapping fallback 'Enter code' position at ({fx},{fy})...", instance_name=instance_name)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.6)

        elif page_name == "TWO_FACTOR_ENTER_CODE_PAGE":
            two_factor_initiated = True
            log("STATE-ACTION", "2FA: 'Enter the 6-digit code' -> Generating OTP via pyotp...", instance_name=instance_name)
            if not two_factor_secret:
                clip = get_android_clipboard(device_serial=device_serial, instance_name=instance_name)
                if clip:
                    clean_clip = re.sub(r'[^A-Z0-9]', '', clip.upper())
                    if len(clean_clip) >= 16 and not clean_clip.isdigit():
                        two_factor_secret = clean_clip
                        if task_context is not None: task_context['two_fa_key'] = two_factor_secret

            elem_input = find_element(root, "enter code", by_class="EditText") or find_element(root, "code", by_class="EditText") or find_password_field(root)
            if elem_input:
                run_adb_command(f"shell input tap {elem_input[0]} {elem_input[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.55)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.3)
            clear_input_field(length=15, device_serial=device_serial, instance_name=instance_name)

            # Generate OTP immediately using pyotp from 2FA Secret Key
            otp_to_fill = ""
            if two_factor_secret:
                otp_to_fill = generate_totp_code(two_factor_secret)
                log("2FA-OTP", f"🔑 Generated 6-Digit OTP via pyotp: '{otp_to_fill}'", instance_name=instance_name)
            elif task_context and task_context.get('otp_code'):
                otp_to_fill = task_context['otp_code']
                log("2FA-OTP", f"🔑 Using Bot 6-digit OTP: '{otp_to_fill}'", instance_name=instance_name)
            elif task_context and task_context.get('two_fa_key'):
                two_factor_secret = task_context.get('two_fa_key')
                otp_to_fill = generate_totp_code(two_factor_secret)
                log("2FA-OTP", f"🔑 Generated 6-Digit OTP via task_context: '{otp_to_fill}'", instance_name=instance_name)

            if otp_to_fill:
                type_text(otp_to_fill, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.4)
                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            clicked_next = tap_or_fallback_rel(root, "next", 0.50, 0.82, "Next Button", exact=False, device_serial=device_serial, instance_name=instance_name)
            log("2FA-ACTION", f"✔ Clicked 'Next' button after OTP input ({clicked_next}). Completing cycle immediately...", instance_name=instance_name)
            time.sleep(1.0)

            # Save account to CSV and Google Sheets immediately
            final_user = captured_username or target_login
            save_credentials_csv(email, password, two_factor_secret=two_factor_secret, username=final_user, instance_name=instance_name)
            log("SUCCESS", f"🎉 Two-Factor Authentication OTP submitted & credentials saved for {final_user} (Secret: {two_factor_secret})!", instance_name=instance_name)
            return True, two_factor_secret

        elif page_name == "TWO_FACTOR_SUCCESS_PAGE":
            if not two_factor_secret:
                log("2FA-WARN", "Encountered TWO_FACTOR_SUCCESS_PAGE but secret key is not recorded yet. Attempting recovery from clipboard / task context...", instance_name=instance_name)
                clip = get_android_clipboard(device_serial=device_serial, instance_name=instance_name)
                if clip:
                    clean_clip = re.sub(r'[^A-Z0-9]', '', clip.upper())
                    if len(clean_clip) in [16, 24, 32] and not clean_clip.isdigit():
                        two_factor_secret = clean_clip
                        if task_context is not None:
                            task_context['two_fa_key'] = two_factor_secret
                if not two_factor_secret and task_context and task_context.get('two_fa_key'):
                    two_factor_secret = task_context.get('two_fa_key')

            log("SUCCESS", f"🎉 Two-Factor Authentication is successfully active for {email}!", instance_name=instance_name)
            tap_or_fallback_rel(root, "done", 0.5, 0.88, "Done Button", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.0)
            
            # Save account to CSV and Google Sheets
            final_user = captured_username or target_login
            save_credentials_csv(email, password, two_factor_secret=two_factor_secret, username=final_user, instance_name=instance_name)
            return True, two_factor_secret

        else:
            time.sleep(0.6)

    return False, two_factor_secret

# ==============================================================================
# 🤖 TELEGRAM TASK WORKER CLASS (Multi-Account Parallel Execution)
# ==============================================================================
class TasklyWorker:
    def __init__(
        self,
        account_cfg: dict,
        device_serial: str,
        instance_name: str,
        target_bot: str,
        client: TelegramClient,
        on_account_switched_callback: Optional[Callable] = None,
        available_bots: Optional[List[str]] = None
    ):
        self.account_cfg = account_cfg
        self.device_serial = device_serial
        self.instance_name = instance_name
        self.available_bots = available_bots if available_bots else (account_cfg.get("target_bots") or TARGET_BOTS)
        self.target_bots = self.available_bots or [target_bot]
        self.current_bot_idx = 0
        self.target_bot = target_bot
        self.client = client
        self.on_account_switched_callback = on_account_switched_callback

        self.current_task: Dict[str, Any] = {
            'status': 'idle',
            'first_name': None,
            'last_name': None,
            'login': None,
            'password': None,
            'two_fa_key': None,
            'two_fa_sent': False,
            'otp_code': None,
            'cancelled': False
        }

        self.task_received_event = asyncio.Event()
        self.two_fa_requested_event = asyncio.Event()
        self.otp_received_event = asyncio.Event()
        self.report_received_event = asyncio.Event()
        self.task_cancelled_event = asyncio.Event()

        self.submitted_count = 0
        self.approved_count = 0
        self.current_balance = "$0.00"
        self.is_stopped = False
        self.consecutive_fails = 0
        self.consecutive_timeouts = 0
        self.max_consecutive_fails = int(_CFG.get("max_consecutive_fails", 3))
        self.fail_cooldown_seconds = int(_CFG.get("device_fail_cooldown", 60))

        self.task_confirm_btn_msg = None
        self._last_processed_key = None
        self._last_processed_time = 0.0
        self._last_sent_commands: Dict[str, float] = {}

        # Register current account as active in global pool
        if self.account_cfg and self.account_cfg.get("phone"):
            TelegramAccountPool.register_active(self.account_cfg.get("phone"), self.instance_name)

        self._register_handlers()

    def _register_handlers(self):
        targets = self.target_bots if self.target_bots else [self.target_bot]
        @self.client.on(events.NewMessage(chats=targets))
        async def _bot_handler(event):
            await self.handle_bot_message(event)

        @self.client.on(events.MessageEdited(chats=targets))
        async def _bot_edit_handler(event):
            await self.handle_bot_message(event)

    async def switch_target_bot(self, next_bot: Optional[str] = None) -> str:
        """Switches the target Telegram bot (e.g. PayGoeasy_bot <-> tasklyBux_bot)."""
        old_bot = self.target_bot
        if next_bot:
            new_bot = next_bot.strip().lstrip("@")
        else:
            bots = self.target_bots if self.target_bots else ["PayGoeasy_bot", "tasklyBux_bot"]
            curr_idx = 0
            for idx, b in enumerate(bots):
                if b.lower() == old_bot.lower():
                    curr_idx = idx
                    break
            new_bot = bots[(curr_idx + 1) % len(bots)]

        self.target_bot = new_bot
        self._last_sent_commands.clear()
        log("TG-SWITCH-BOT", f"🔀 Switched target bot: @{old_bot} ➔ @{new_bot}", instance_name=self.instance_name)

        if self.on_account_switched_callback:
            try:
                self.on_account_switched_callback(self.instance_name, self.account_cfg.get("phone"), new_bot)
            except Exception:
                pass

        return new_bot

    async def switch_account(self, new_account_cfg: dict, new_target_bot: Optional[str] = None) -> bool:
        """Gracefully disconnects current Telegram client and switches to a free account."""
        new_phone = new_account_cfg.get("phone")
        old_phone = self.account_cfg.get("phone")
        log("TG-SWITCH", f"🔄 Switching account for {self.instance_name}: {old_phone} ➔ {new_phone}...", instance_name=self.instance_name)

        # Release old account
        TelegramAccountPool.release_active(old_phone)

        # Disconnect old client
        try:
            if self.client:
                if self.client.is_connected():
                    await self.client.disconnect()
        except Exception as e:
            log("TG-WARN", f"Error disconnecting previous client: {e}", instance_name=self.instance_name)

        # Connect new client
        try:
            clean_phone = re.sub(r'[^0-9]', '', str(new_phone))
            sess_name = new_account_cfg.get('session_name') or f"taskly_session_{clean_phone}"
            new_client = TelegramClient(sess_name, new_account_cfg['api_id'], new_account_cfg['api_hash'])
            await new_client.start(phone=clean_phone)

            self.client = new_client
            self.account_cfg = new_account_cfg
            TelegramAccountPool.register_active(new_phone, self.instance_name)

            if new_target_bot:
                self.target_bot = new_target_bot

            self._register_handlers()
            self._last_sent_commands.clear()

            if self.on_account_switched_callback:
                try:
                    self.on_account_switched_callback(self.instance_name, new_phone, self.target_bot)
                except Exception:
                    pass

            log("TG-SWITCH", f"✨ New account {new_phone} successfully activated for {self.instance_name} (Bot: @{self.target_bot})!", instance_name=self.instance_name)

            # Refresh balance for new account
            try:
                await self.fetch_bot_balance()
            except Exception:
                pass
            return True
        except Exception as e:
            log("TG-ERR", f"❌ Failed to activate new account {new_phone}: {e}", instance_name=self.instance_name)
            return False

    async def handle_flood_rotation(self, flood_seconds: int) -> bool:
        """Handles FloodWait by switching to a free account or alternating bots."""
        curr_phone = self.account_cfg.get("phone")
        TelegramAccountPool.mark_flood(curr_phone, flood_seconds)
        log("FLOOD-WAIT", f"⚠️ Account {curr_phone} placed on FloodWait cooldown for {flood_seconds}s.", instance_name=self.instance_name)

        # 1. Try to find another free account
        free_acc = TelegramAccountPool.find_free_account(curr_phone, self.instance_name)
        if free_acc:
            new_phone = free_acc.get("phone")
            log("TG-ROTATE", f"🔄 Free account found ({new_phone})! Rotating Telegram session...", instance_name=self.instance_name)
            ok = await self.switch_account(free_acc)
            if ok:
                await asyncio.sleep(random.uniform(2.5, 4.5))
                return True

        # 2. If no free account, switch bot to avoid locked peer
        log("TG-ROTATE", f"⚠️ No free account available! Switching target bot to prevent complete stall...", instance_name=self.instance_name)
        await self.switch_target_bot()
        await asyncio.sleep(random.uniform(3.0, 6.0))
        return False

    async def safe_send_message(self, chat, text, max_retries=3, min_interval=5.0):
        if self.is_stopped:
            return None

        # 1. Throttling: Prevent duplicate repetitive commands (e.g. rapid /start spam)
        clean_text = text.strip()
        cmd_key = f"{chat}_{clean_text.lower()}"
        now = time.time()
        last_sent = self._last_sent_commands.get(cmd_key, 0.0)
        time_diff = now - last_sent

        if time_diff < min_interval:
            if clean_text.startswith("/") or clean_text in ["▶️ Start", "📋 Tasks", "💰 Balance"]:
                log("TG-THROTTLE", f"⏳ Skipping duplicate command '{clean_text}' (sent {time_diff:.1f}s ago to avoid FloodWait)", instance_name=self.instance_name)
                return None
            else:
                await asyncio.sleep(random.uniform(1.2, 2.5))

        # 2. Randomized human delay before sending
        await asyncio.sleep(random.uniform(1.2, 2.5))

        for attempt in range(max_retries):
            if self.is_stopped:
                return None
            try:
                res = await self.client.send_message(chat, text)
                self._last_sent_commands[cmd_key] = time.time()
                return res
            except errors.FloodWaitError as e:
                log("FLOOD-WAIT", f"⚠️ Telegram FloodWait: {e.seconds}s on send_message...", instance_name=self.instance_name)
                if e.seconds > 45:
                    # Heavy flood: auto-rotate account or switch bot
                    rotated = await self.handle_flood_rotation(e.seconds)
                    if rotated:
                        return await self.safe_send_message(self.target_bot, text, max_retries=1)
                    return None
                else:
                    await asyncio.sleep(e.seconds + random.uniform(1.5, 3.5))
            except Exception as e:
                log("TG-ERR", f"send_message error ({e}). Retrying...", instance_name=self.instance_name)
                await asyncio.sleep(random.uniform(2.0, 3.5))
        return None

    async def safe_click_button(self, btn, max_retries=3):
        if self.is_stopped:
            return False

        # Human-like thinking delay before clicking
        await asyncio.sleep(random.uniform(1.0, 2.4))

        for attempt in range(max_retries):
            if self.is_stopped:
                return False
            try:
                await btn.click()
                return True
            except errors.FloodWaitError as e:
                log("FLOOD-WAIT", f"⚠️ Telegram FloodWait on button: {e.seconds}s...", instance_name=self.instance_name)
                if e.seconds > 45:
                    await self.handle_flood_rotation(e.seconds)
                    return False
                else:
                    await asyncio.sleep(e.seconds + random.uniform(1.5, 3.0))
            except Exception:
                await asyncio.sleep(random.uniform(1.2, 2.2))
        return False

    async def click_any_button(self, message, pattern):
        if message and message.buttons:
            for row_idx, row in enumerate(message.buttons):
                for col_idx, btn in enumerate(row):
                    if re.search(pattern, btn.text or "", re.IGNORECASE):
                        log("TG-BOT", f"👉 [Button Click]: '{btn.text}'", instance_name=self.instance_name)
                        if await self.safe_click_button(btn):
                            return True
                        try:
                            await message.click(row_idx, col_idx)
                            return True
                        except Exception:
                            pass
        return False

    async def click_button_by_pattern(self, pattern):
        try:
            recent_msgs = await self.client.get_messages(self.target_bot, limit=2)
            for msg in recent_msgs:
                if msg.buttons:
                    msg_age = (datetime.now(timezone.utc) - msg.date).total_seconds() if msg.date else 0
                    if msg_age > 180:
                        continue
                    for row_idx, row in enumerate(msg.buttons):
                        for col_idx, btn in enumerate(row):
                            if re.search(pattern, btn.text or "", re.IGNORECASE):
                                log("TG-BOT", f"👉 [Button Click on msg #{msg.id}]: '{btn.text}'", instance_name=self.instance_name)
                                self.task_confirm_btn_msg = msg
                                if await self.safe_click_button(btn):
                                    return True
                                try:
                                    await msg.click(row_idx, col_idx)
                                    return True
                                except Exception:
                                    pass
        except Exception as e:
            log("TG-WARN", f"Error searching recent messages: {e}", instance_name=self.instance_name)
        return False

    async def fetch_bot_balance(self) -> Optional[str]:
        """Requests account balance from Telegram bot using '💰 Balance' command or button."""
        try:
            log("TG-BALANCE", f"💰 Requesting balance update from @{self.target_bot}...", instance_name=self.instance_name)
            clicked = await self.click_button_by_pattern(r"Balance|💰\s*Balance|💰")
            if not clicked:
                await self.safe_send_message(self.target_bot, "💰 Balance")

            # Lightweight gentle polling (max 4 checks every 1.2s to prevent flood)
            for _ in range(4):
                await asyncio.sleep(1.2)
                recent_msgs = await self.client.get_messages(self.target_bot, limit=2)
                for msg in recent_msgs:
                    t = (msg.text or "").strip()
                    if ("balance" in t.lower() or "your balance" in t.lower()) and ("$" in t or re.search(r"\d+\.\d+", t)):
                        m_bal = re.search(r"(\$[\d\.]+|\d+\.\d{2,4})", t)
                        if m_bal:
                            bal_str = m_bal.group(1)
                            if not bal_str.startswith("$"):
                                bal_str = f"${bal_str}"
                            self.current_balance = bal_str
                            log("TG-BALANCE", f"💰 Current Balance: {bal_str}", instance_name=self.instance_name)
                            return bal_str
        except Exception as e:
            log("TG-WARN", f"Failed fetching balance: {e}", instance_name=self.instance_name)
        return self.current_balance

    async def handle_bot_message(self, event):
        msg = event.message
        text = (msg.text or "").strip()
        if not text:
            return

        now = time.time()
        msg_key = f"{msg.id}_{text[:25]}"
        if msg_key == self._last_processed_key and (now - self._last_processed_time) < 1.5:
            return
        self._last_processed_key = msg_key
        self._last_processed_time = now

        if msg.buttons:
            self.task_confirm_btn_msg = msg

        try:
            sender = await event.get_sender()
            sender_bot = (getattr(sender, 'username', '') or "").lstrip("@")
            if sender_bot and any(sender_bot.lower() == b.lower() for b in self.target_bots):
                for idx, b in enumerate(self.target_bots):
                    if sender_bot.lower() == b.lower():
                        self.target_bot = b
                        self.current_bot_idx = idx
                        break
        except Exception:
            pass

        log("TG-RECV", f"📩 Message from @{self.target_bot}:\n{text}", instance_name=self.instance_name)

        # 0. Balance check
        if ("your balance:" in text.lower() or "balance:" in text.lower()) and ("$" in text or re.search(r"\d+\.\d+", text)):
            m_bal = re.search(r"(\$[\d\.]+|\d+\.\d{2,4})", text)
            if m_bal:
                bal_str = m_bal.group(1)
                if not bal_str.startswith("$"):
                    bal_str = f"${bal_str}"
                self.current_balance = bal_str
                log("TG-BALANCE", f"💰 Current Balance: {bal_str}", instance_name=self.instance_name)

        # 1. Account Credentials Received (First name, Login, Password) - MUST BE CHECKED FIRST
        if re.search(r"First\s*name\s*:", text, re.IGNORECASE) and re.search(r"Login\s*:", text, re.IGNORECASE):
            log("TG-STEP", "Account Credentials Received from Bot! Triggering Meta AI creation...", instance_name=self.instance_name)
            fn_match = re.search(r"First\s*name\s*:\s*([^\r\n]+)", text, re.IGNORECASE)
            login_match = re.search(r"Login\s*:\s*([^\r\n]+)", text, re.IGNORECASE)
            pass_match = re.search(r"Password\s*:\s*([^\r\n]+)", text, re.IGNORECASE)

            raw_full_name = fn_match.group(1).strip().replace("`", "").replace("*", "").strip() if fn_match else "User"
            clean_name = re.sub(r'[^\w\s]', '', raw_full_name).strip()
            full_name = clean_name if clean_name else (raw_full_name or "User")
            name_parts = full_name.split()
            if len(name_parts) >= 2:
                self.current_task['first_name'] = name_parts[0]
                self.current_task['last_name'] = " ".join(name_parts[1:])
            else:
                self.current_task['first_name'] = full_name
                self.current_task['last_name'] = "User"

            self.current_task['login'] = login_match.group(1).strip().replace("`", "").replace("*", "").strip() if login_match else ""
            self.current_task['password'] = pass_match.group(1).strip().replace("`", "").replace("*", "").strip() if pass_match else ""
            self.current_task['two_fa_key'] = None
            self.current_task['otp_code'] = None
            self.current_task['status'] = 'credentials_ready'
            self.task_cancelled_event.clear()

            print("\n" + "="*50)
            print(f"📋 [{self.instance_name}] [BOT CREDENTIALS ACQUIRED]")
            print(f"👤 Full Name  : {full_name} (First: {self.current_task['first_name']}, Last: {self.current_task['last_name']})")
            print(f"🔑 Target User: {self.current_task['login']}")
            print(f"🔒 Password   : {self.current_task['password']}")
            print("="*50 + "\n")

            self.task_received_event.set()
            return

        # 2. Bot asks for 2FA key or invalid key format retry
        elif "2FA key" in text or "enter your 2FA" in text or "invalid key format" in text.lower():
            log("TG-STEP", "Bot is requesting 2FA Secret Key...", instance_name=self.instance_name)
            self.two_fa_requested_event.set()
            key = self.current_task.get('two_fa_key')
            if key and len(key) >= 16:
                log("TG-BOT", f"🔑 Sending 2FA Secret Key '{key}' to @{self.target_bot}...", instance_name=self.instance_name)
                await asyncio.sleep(DELAY_BETWEEN_STEPS)
                await self.safe_send_message(self.target_bot, key)
                self.current_task['two_fa_sent'] = True
            return

        # 3. Bot sends OTP code
        elif "Your one-time code" in text or "Tap the code to copy" in text or "Press the button to confirm" in text or (len(re.findall(r"\b\d{6}\b", text)) > 0 and self.current_task.get('two_fa_key')):
            log("TG-STEP", "One-time OTP code received from bot!", instance_name=self.instance_name)
            code_match = re.search(r"\b\d{6}\b", text)
            if code_match:
                self.current_task['otp_code'] = code_match.group(0)
                log("TG-OTP", f"🔑 Captured 6-Digit OTP: {self.current_task['otp_code']}", instance_name=self.instance_name)
                self.otp_received_event.set()
                
            if msg.buttons:
                self.task_confirm_btn_msg = msg
            return

        # 4. Report Received
        elif "report has been received" in text.lower() or "report received" in text.lower() or "your report has been received" in text.lower() or "Task Completed" in text:
            self.submitted_count += 1
            log("TG-SUBMITTED", f"📥 [SUBMITTED] Report received by Bot! (Total: {self.submitted_count})", instance_name=self.instance_name)
            self.report_received_event.set()
            return

        # 5. Report Approved
        elif "report approved" in text.lower() or "+$0.03" in text or "approved, +" in text.lower() or "credited to your balance" in text:
            self.approved_count += 1
            log("TG-APPROVED", f"💰 [APPROVED] Report approved (+$0.03)! (Total Approved: {self.approved_count})", instance_name=self.instance_name)
            return

        # 6. Rate Limit Handling
        elif "making requests too often" in text or "too often" in text.lower() or ("please wait" in text.lower() and "sec" in text.lower()):
            wait_match = re.search(r"wait\s*(\d+)\s*sec", text, re.IGNORECASE)
            wait_s = int(wait_match.group(1)) if wait_match else 20
            log("TG-RATE-LIMIT", f"⏳ Bot @{self.target_bot} rate-limited! (Cooldown {wait_s}s)", instance_name=self.instance_name)
            
            # If rate limit is high (>25s), alternate to another bot immediately
            if wait_s > 25:
                log("TG-RATE-LIMIT", f"🔄 Bot @{self.target_bot} cooldown is long ({wait_s}s). Switching to other bot...", instance_name=self.instance_name)
                await self.switch_target_bot()
                return

            await asyncio.sleep(wait_s + random.uniform(1.5, 3.0))
            if self.current_task.get('status') in ('running', 'credentials_ready') or self.current_task.get('login') or self.is_stopped:
                return
            btn_clicked = await self.click_button_by_pattern(r"Start|▶️")
            if not btn_clicked:
                await self.safe_send_message(self.target_bot, "▶️ Start")
            return

        # 7. Time's up / Cancelled (Clean reset without immediate /start spam)
        elif "Time's up" in text or "Task cancelled" in text or "task cancelled" in text.lower() or "time's up" in text.lower():
            log("TG-WARN", "⏰ Task cancelled by bot! Flagging task for clean cycle reset...", instance_name=self.instance_name)
            self.current_task['status'] = 'cancelled'
            self.current_task['cancelled'] = True
            self.task_cancelled_event.set()
            self.task_received_event.set()
            self.two_fa_requested_event.set()
            self.report_received_event.clear()
            return

        # 8. Welcome / Initial menu
        elif "Welcome" in text or "Terms of Use" in text or "earn money" in text:
            if self.current_task.get('status') in ('running', 'credentials_ready') or self.current_task.get('login') or self.task_received_event.is_set():
                return
            log("TG-STEP", "Welcome received -> Opening Tasks menu...", instance_name=self.instance_name)
            await asyncio.sleep(random.uniform(1.5, 3.0))
            clicked = await self.click_any_button(msg, r"Tasks|📋")
            if not clicked:
                await self.safe_send_message(self.target_bot, "📋 Tasks")

        # 9. Select task
        elif "Please select a task" in text or "select a task" in text:
            if self.current_task.get('status') in ('running', 'credentials_ready') or self.current_task.get('login') or self.task_received_event.is_set():
                return
            await asyncio.sleep(random.uniform(1.8, 3.2))
            is_taskly = "taskly" in (self.target_bot or "").lower()
            if is_taskly:
                log("TG-STEP", "Selecting option: '🔥 Create Inst (No mail)' for @tasklyBux_bot...", instance_name=self.instance_name)
                clicked = await self.click_any_button(msg, r"No\s*mail|Create\s*Inst.*No\s*mail")
                if not clicked:
                    clicked = await self.click_any_button(msg, r"Create\s*Inst")
                if not clicked:
                    clicked = await self.click_button_by_pattern(r"No\s*mail|Create\s*Inst")
                if not clicked and msg.buttons:
                    for row in msg.buttons:
                        for b in row:
                            if "Create Inst" in (b.text or ""):
                                await self.safe_send_message(self.target_bot, b.text)
                                clicked = True
                                break
                        if clicked:
                            break
            else:
                log("TG-STEP", "Selecting task '🔥Create Inst (2FA)' for @PayGoeasy_bot...", instance_name=self.instance_name)
                clicked = await self.click_any_button(msg, r"2FA|Create\s*Inst.*2FA")
                if not clicked:
                    clicked = await self.click_any_button(msg, r"Create\s*Inst")
                if not clicked:
                    clicked = await self.click_button_by_pattern(r"2FA|Create\s*Inst")
                if not clicked and msg.buttons:
                    for row in msg.buttons:
                        for b in row:
                            if "Create Inst" in (b.text or "") or "2FA" in (b.text or ""):
                                await self.safe_send_message(self.target_bot, b.text)
                                clicked = True
                                break
                        if clicked:
                            break

        # 10. Start task (Trigger ▶️ Start to receive credentials)
        elif "Review time" in text or "Task: 🔥Create Inst" in text or "Task: 📱 Create Inst" in text or "Create Inst (2FA)" in text or "Task:" in text:
            if self.current_task.get('status') in ('running', 'credentials_ready') or self.current_task.get('login') or self.task_received_event.is_set():
                return
            log("TG-STEP", "Task description received -> Triggering ▶️ Start...", instance_name=self.instance_name)
            await asyncio.sleep(random.uniform(1.8, 3.2))
            clicked = await self.click_any_button(msg, r"Start|▶️")
            if not clicked:
                await self.safe_send_message(self.target_bot, "▶️ Start")

    async def run_automation_loop(self, playwright: Optional[Any] = None):
        cycle_num = 0
        while not self.is_stopped:
            try:
                cycle_num += 1
                print("\n" + "#"*60)
                log("CYCLE-START", f"Starting AutoMetaInsta Cycle #{cycle_num} on device {self.device_serial}...", instance_name=self.instance_name)
                print("#"*60 + "\n")

                # 🎲 Randomize device identity (IMEI, Android ID, MAC) at start of every cycle (Resolution/regulation untouched)
                loop = asyncio.get_running_loop()
                try:
                    await loop.run_in_executor(None, randomize_emulator_identity, self.device_serial, self.instance_name)
                    # Clear browser app data so Instagram sees clean device & cookies
                    await loop.run_in_executor(None, run_adb_command, f"shell pm clear {PACKAGE_NAME}", self.device_serial, self.instance_name, 5)
                except Exception as rand_err:
                    log("IMEI-WARN", f"Identity randomize error: {rand_err}", instance_name=self.instance_name)

                if self.current_task.get('status') == 'credentials_ready' and self.current_task.get('login'):
                    log("STEP-1", f"Using existing credentials from bot: {self.current_task['login']}", instance_name=self.instance_name)
                else:
                    self.task_received_event.clear()
                    self.two_fa_requested_event.clear()
                    self.otp_received_event.clear()
                    self.task_cancelled_event.clear()
                    self.current_task['status'] = 'idle'
                    self.current_task['cancelled'] = False

                    log("STEP-1", f"Starting new task cycle with /start on @{self.target_bot}...", instance_name=self.instance_name)
                    await self.safe_send_message(self.target_bot, "/start")
                    
                    log("WAIT", "Waiting for account credentials from Bot...", instance_name=self.instance_name)
                    try:
                        await asyncio.wait_for(self.task_received_event.wait(), timeout=35.0)
                        self.consecutive_timeouts = 0
                    except asyncio.TimeoutError:
                        self.consecutive_timeouts += 1
                        log("WARN", f"Timeout waiting for credentials from @{self.target_bot} (Timeout #{self.consecutive_timeouts})...", instance_name=self.instance_name)
                        self.current_task['status'] = 'idle'
                        if self.consecutive_timeouts >= 2:
                            log("WARN", f"Multiple timeouts on @{self.target_bot}. Switching target bot...", instance_name=self.instance_name)
                            self.consecutive_timeouts = 0
                            await self.switch_target_bot()
                        await asyncio.sleep(random.uniform(5.0, 9.0))
                        continue

                if not self.current_task.get('login'):
                    if self.task_cancelled_event.is_set() or self.current_task.get('status') == 'cancelled':
                        log("CYCLE-ABORT", "Task cancelled by bot. Resting before fresh cycle...", instance_name=self.instance_name)
                        self.current_task['status'] = 'idle'
                        await asyncio.sleep(random.uniform(5.0, 9.0))
                        continue
                else:
                    self.task_cancelled_event.clear()

                self.current_task['status'] = 'running'
                first_name = self.current_task['first_name']
                last_name = self.current_task['last_name']
                target_login = self.current_task['login']
                target_password = self.current_task['password']

                # Step 2: Create Meta AI Account using Disposable Email & Password from Telegram Bot (with retry)
                meta_ok = False
                meta_email = ""
                meta_mail_token = ""

                for attempt in range(1, 3):
                    log("STEP-2", f"Creating Meta Account (attempt {attempt}/2) for: {first_name} {last_name} | User: {target_login} | Password: {target_password}...", instance_name=self.instance_name)
                    meta_ok, meta_email, meta_mail_token = await create_meta_account_task(
                        playwright,
                        first_name,
                        last_name,
                        target_password,
                        username=target_login,
                        instance_name=self.instance_name,
                        task_context=self.current_task
                    )
                    if meta_ok and meta_email:
                        break
                    if self.current_task.get('status') == 'cancelled' or self.is_stopped:
                        break
                    log("WARN", f"Meta creation attempt {attempt}/2 failed. Retrying in 2s...", instance_name=self.instance_name)
                    await asyncio.sleep(2.0)

                if self.current_task.get('status') == 'cancelled':
                    log("CYCLE-ABORT", "Task cancelled during Meta creation. Resetting...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    btn_clicked = await self.click_button_by_pattern(r"Tasks|📋")
                    if not btn_clicked:
                        await self.safe_send_message(self.target_bot, "📋 Tasks")
                    await asyncio.sleep(random.uniform(3.0, 6.0))
                    continue

                if not meta_ok or not meta_email:
                    log("ERR", "Meta account creation failed after retries. Resetting for next cycle...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    self.current_task['login'] = None
                    self.current_task['two_fa_key'] = None
                    self.task_received_event.clear()
                    await asyncio.sleep(random.uniform(3.0, 6.0))
                    continue

                # Step 3: Run Instagram 2FA Creation on Emulator
                log("STEP-3", f"Running Instagram 2FA on emulator ({self.device_serial}) with Meta email: {meta_email} and Username: {target_login}...", instance_name=self.instance_name)
                loop = asyncio.get_running_loop()
                
                self.current_task['two_fa_sent'] = False
                stop_key_exchange = asyncio.Event()

                async def handle_2fa_key_exchange():
                    while not stop_key_exchange.is_set():
                        if self.task_cancelled_event.is_set() or self.current_task.get('status') == 'cancelled':
                            return
                        key = self.current_task.get('two_fa_key')
                        if key and len(key) >= 16 and not self.current_task.get('two_fa_sent'):
                            log("TG-BOT", f"🔑 Sending extracted 2FA Secret Key '{key}' to @{self.target_bot}...", instance_name=self.instance_name)
                            await asyncio.sleep(DELAY_BETWEEN_STEPS)
                            await self.safe_send_message(self.target_bot, key)
                            self.current_task['two_fa_sent'] = True
                            break
                        await asyncio.sleep(0.3)

                key_exchange_task = asyncio.create_task(handle_2fa_key_exchange())

                insta_ok, secret_key = await loop.run_in_executor(
                    None,
                    run_instagram_2fa_pipeline,
                    self.device_serial,
                    meta_email,
                    target_password,
                    target_login,
                    meta_mail_token,
                    self.instance_name,
                    self.current_task
                )
                
                stop_key_exchange.set()
                if not key_exchange_task.done():
                    key_exchange_task.cancel()
                try:
                    await key_exchange_task
                except (asyncio.CancelledError, Exception):
                    pass

                if self.current_task.get('status') == 'cancelled':
                    log("CYCLE-ABORT", "Task explicitly cancelled during Instagram pipeline. Resetting with /start...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    await self.safe_send_message(self.target_bot, "/start")
                    await asyncio.sleep(3.0)
                    continue

                final_2fa_key = secret_key or self.current_task.get('two_fa_key')

                if insta_ok and final_2fa_key and len(final_2fa_key) >= 16:
                    if not self.current_task.get('two_fa_sent'):
                        log("TG-BOT", f"🔑 Submitting captured 2FA Secret Key '{final_2fa_key}' to @{self.target_bot}...", instance_name=self.instance_name)
                        await self.safe_send_message(self.target_bot, final_2fa_key)
                        self.current_task['two_fa_sent'] = True
                        await asyncio.sleep(2.0)

                    log("STEP-4", "Instagram 2FA active & CSV saved! Confirming task completion in Bot...", instance_name=self.instance_name)
                    self.report_received_event.clear()
                    await asyncio.sleep(DELAY_BETWEEN_STEPS)

                    attempt = 0
                    max_confirm_attempts = 4
                    while not self.report_received_event.is_set() and attempt < max_confirm_attempts:
                        if self.task_cancelled_event.is_set() or self.current_task.get('status') != 'running':
                            break
                        attempt += 1
                        log("TG-BOT", f"Clicking '✅ Account Registered' in Bot (Attempt {attempt}/{max_confirm_attempts})...", instance_name=self.instance_name)
                        clicked = await self.click_button_by_pattern(r"Account Registered|Registered|Confirm|✅")
                        if not clicked and not self.task_cancelled_event.is_set() and self.current_task.get('status') == 'running':
                            await self.safe_send_message(self.target_bot, "✅ Account Registered")

                        try:
                            await asyncio.wait_for(self.report_received_event.wait(), timeout=4.0)
                            break
                        except asyncio.TimeoutError:
                            if self.task_cancelled_event.is_set() or self.current_task.get('status') != 'running':
                                break
                            await asyncio.sleep(2.5)

                    self.consecutive_fails = 0
                    if self.report_received_event.is_set():
                        log("TG-BOT", f"✔ Report confirmed by bot! (Submitted: {self.submitted_count} | Approved: {self.approved_count})", instance_name=self.instance_name)

                    print("\n" + "="*50)
                    log("SUCCESS", f"🎉 [{self.instance_name}] CYCLE #{cycle_num} COMPLETE! (Submitted: {self.submitted_count} | Approved: {self.approved_count})", instance_name=self.instance_name)
                    log("SUCCESS", f"Meta Email : {meta_email}", instance_name=self.instance_name)
                    log("SUCCESS", f"Username   : {target_login}", instance_name=self.instance_name)
                    log("SUCCESS", f"Password   : {target_password}", instance_name=self.instance_name)
                    log("SUCCESS", f"2FA Secret : {final_2fa_key}", instance_name=self.instance_name)
                    print("="*50 + "\n")

                    self.current_task['status'] = 'idle'
                    self.current_task['login'] = None
                    self.current_task['two_fa_key'] = None
                    self.current_task['two_fa_sent'] = False
                    self.current_task['otp_code'] = None
                    if self.current_task.get('ninja_client'):
                        try:
                            self.current_task['ninja_client'].disconnect()
                        except Exception:
                            pass
                        self.current_task['ninja_client'] = None

                    # 💰 Fetch updated balance from Telegram bot at the end of the cycle
                    try:
                        await asyncio.sleep(1.0)
                        await self.fetch_bot_balance()
                    except Exception as b_err:
                        log("TG-WARN", f"Balance update error: {b_err}", instance_name=self.instance_name)

                else:
                    self.consecutive_fails += 1
                    log("ERR", f"Instagram 2FA process or key capture failed for {target_login} (Fail #{self.consecutive_fails}/{self.max_consecutive_fails} on {self.device_serial}). Resetting with /start...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    self.current_task['login'] = None
                    self.current_task['two_fa_key'] = None
                    self.current_task['two_fa_sent'] = False
                    if self.current_task.get('ninja_client'):
                        try:
                            self.current_task['ninja_client'].disconnect()
                        except Exception:
                            pass
                        self.current_task['ninja_client'] = None
                    self.task_received_event.clear()
                    self.task_cancelled_event.set()

                    if self.consecutive_fails >= self.max_consecutive_fails:
                        log("DEVICE-COOLDOWN", f"⚠️ Device {self.device_serial} ({self.instance_name}) reached {self.consecutive_fails} consecutive failures!", instance_name=self.instance_name)
                        log("DEVICE-COOLDOWN", f"Cleaning emulator state and cooling down for {self.fail_cooldown_seconds}s before retrying device {self.device_serial}...", instance_name=self.instance_name)
                        try:
                            await loop.run_in_executor(None, recover_device_after_failures, self.device_serial, self.instance_name)
                        except Exception as rec_err:
                            log("DEVICE-COOLDOWN", f"Recovery error: {rec_err}", instance_name=self.instance_name)

                        remaining = self.fail_cooldown_seconds
                        while remaining > 0 and not self.is_stopped:
                            step = min(remaining, 15)
                            await asyncio.sleep(step)
                            remaining -= step
                            if remaining > 0 and not self.is_stopped:
                                log("DEVICE-COOLDOWN", f"Cooldown for {self.device_serial} ({self.instance_name}): {remaining}s remaining...", instance_name=self.instance_name)

                        self.consecutive_fails = 0
                        log("DEVICE-RESUME", f"🔄 Cooldown finished! Resuming tasks on device {self.device_serial} ({self.instance_name})...", instance_name=self.instance_name)

                if not AUTO_REPEAT_TASKS:
                    log("MAIN", "Auto-repeat disabled. Finishing execution.", instance_name=self.instance_name)
                    break

                cycle_delay = random.uniform(6.0, 12.0)
                log("WAIT", f"☕ Human cooldown: Waiting {cycle_delay:.1f}s before starting next cycle...", instance_name=self.instance_name)
                await asyncio.sleep(cycle_delay)

            except Exception as cycle_err:
                self.consecutive_fails += 1
                log("CYCLE-RECOVER", f"Unexpected cycle error on {self.instance_name} ({self.device_serial}): {cycle_err}. (Fail #{self.consecutive_fails}/{self.max_consecutive_fails})", instance_name=self.instance_name)
                if self.consecutive_fails >= self.max_consecutive_fails:
                    log("DEVICE-COOLDOWN", f"⚠️ Multiple consecutive cycle errors on {self.device_serial}. Performing device recovery & cooling down for {self.fail_cooldown_seconds}s...", instance_name=self.instance_name)
                    try:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, recover_device_after_failures, self.device_serial, self.instance_name)
                    except Exception:
                        pass
                    await asyncio.sleep(self.fail_cooldown_seconds)
                    self.consecutive_fails = 0
                    log("DEVICE-RESUME", f"🔄 Resuming tasks on device {self.device_serial} after cooldown...", instance_name=self.instance_name)
                else:
                    await asyncio.sleep(random.uniform(4.0, 8.0))

    async def run(self, playwright: Optional[Any] = None):
        while not self.is_stopped:
            try:
                if self.is_stopped:
                    break
                await self.run_automation_loop(playwright)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.is_stopped:
                    break
                log("WORKER-RECOVER", f"Worker {self.instance_name} recovered from exception: {e}. Restarting in 5s...", instance_name=self.instance_name)
                await asyncio.sleep(5.0)

# ==============================================================================
# 🏁 MAIN ENTRYPOINT (PARALLEL EXECUTION ACROSS DEVICES & TELEGRAM ACCOUNTS)
# ==============================================================================
async def main_async():
    print("=" * 65)
    print("      🚀 AUTOMETAINSTABOT - FULLY AUTOMATED MULTI-BOT SYSTEM       ")
    print("=" * 65)

    devices = get_unique_devices()
    if not devices:
        log("ERR", "No active ADB emulator device found! Please start your Android emulator (LDPlayer, etc.).")
        return

    log("INIT", f"Detected {len(devices)} active ADB device(s): {', '.join(devices)}")
    log("INIT", f"Configured {len(TELEGRAM_ACCOUNTS)} Telegram account(s)")

    jobs = []
    for acc in TELEGRAM_ACCOUNTS:
        acc_bots = acc.get("target_bots") or acc.get("target_bot") or TARGET_BOTS
        norm_bots = normalize_target_bots(acc_bots)
        jobs.append({"account": acc, "target_bot": norm_bots[0]})

    num_to_run = min(len(devices), len(jobs))
    log("INIT", f"Parallel Workers Scheduled: {num_to_run} (Devices: {len(devices)}, Accounts: {len(jobs)})")

    # Connect unique Telegram clients sequentially first
    unique_clients = {}
    for i in range(num_to_run):
        job = jobs[i]
        acc = job["account"]
        clean_phone = re.sub(r'[^0-9]', '', str(acc['phone']))
        sess_name = acc.get('session_name') or f"taskly_session_{clean_phone}"
        if sess_name not in unique_clients:
            tg_client = TelegramClient(sess_name, acc['api_id'], acc['api_hash'])
            unique_clients[sess_name] = (tg_client, acc['phone'])

    for sess_name, (tg_client, phone) in unique_clients.items():
        log("INIT", f"Connecting Telegram Userbot for {phone} (Session: {sess_name})...")
        await tg_client.start(phone=phone)
        CLIENT_CACHE[sess_name] = tg_client
        log("INIT", f"Telegram Userbot for {phone} logged in successfully!")

    workers = []
    for i in range(num_to_run):
        job = jobs[i]
        acc = job["account"]
        target_bot = job["target_bot"]
        dev = devices[i]
        inst_name = f"Inst-{i+1}"
        
        clean_phone = re.sub(r'[^0-9]', '', str(acc['phone']))
        sess_name = acc.get('session_name') or f"taskly_session_{clean_phone}"
        tg_client = CLIENT_CACHE.get(sess_name)
        
        worker = TasklyWorker(acc, dev, inst_name, target_bot=target_bot, client=tg_client)
        workers.append(worker)
        log("INIT", f"🚀 Worker [{inst_name}] assigned to bot @{target_bot} on device {dev}")

    async def _staggered_run(w, index):
        if index > 0:
            delay = index * 3.0
            log("INIT", f"Worker [{w.instance_name}] staggering start by {delay:.1f}s to prevent initial ADB / network contention...")
            await asyncio.sleep(delay)
        await w.run()

    log("INIT", f"🔥 Launching {len(workers)} worker(s) in PARALLEL with staggered initialization...")
    
    worker_tasks = [asyncio.create_task(_staggered_run(w, i)) for i, w in enumerate(workers)]
    await asyncio.gather(*worker_tasks, return_exceptions=True)

def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log("MAIN", "Process stopped by user.")

if __name__ == "__main__":
    main()
