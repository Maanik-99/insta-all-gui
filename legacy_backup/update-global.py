import asyncio
import csv
import hashlib
import imaplib
import json
import os
import random
import re
import secrets
import shutil
import ssl
import string
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import email as email_lib

import requests
import urllib3
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext, Page
from fingerprint_engine import generate_unique_browser_fingerprint
from google_sheet_manager import gsheet_manager

try:
    from argon2.low_level import hash_secret_raw, Type
    HAS_ARGON2 = True
except ImportError:
    HAS_ARGON2 = False

urllib3.disable_warnings()
csv_lock = threading.Lock()

# Configuration
TARGET_URL = "https://auth.meta.com/?error=invalid_redirect"
CHECKPOINT_REDIRECT_URL = "https://auth.meta.com/checkpoints/675270400317265/?next=https%3A%2F%2Fauth.meta.com%2Frecover%2Fsuccess%2F%3Fredirect_uri%3Dhttps%253A%252F%252Fauth.meta.com%252F"
HEADLESS = False  # Set to True for background headless mode
NUM_WORKERS = 1   # Number of parallel browser workers
VIEWPORT_WIDTH = 700    # Compact browser window width
VIEWPORT_HEIGHT = 850   # Compact browser window height

# cPanel API & Domain Configuration
CPANEL_HOST = "https://admiredentalbristol.com:2083"
CPANEL_USER = "admirebristol"
API_TOKEN = "O7EKK7GDGS1TCBYZC6AFOXPG63SUP6ZW"
CPANEL_DOMAIN = "admiredentalbristol.com"
LAST_CREATED_EMAIL_FILE = os.path.join(os.path.dirname(__file__), "last_email.json")

# Mail.td API Configuration
MAIL_TD_TOKEN = "td_457595df95ce703912022f19420afb7c5385aa8d26ab6309e208072f7c58461c"

# Fast cPanel API Session
cpanel_session = requests.Session()
cpanel_session.verify = False
cpanel_session.headers.update({
    "Authorization": f"cpanel {CPANEL_USER}:{API_TOKEN}",
    "Connection": "keep-alive"
})
adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
cpanel_session.mount("http://", adapter)
cpanel_session.mount("https://", adapter)

NAMES_FILE = os.path.join(os.path.dirname(__file__), "names.csv")
_CACHED_FIRST_NAMES: list[str] = []
_CACHED_LAST_NAMES: list[str] = []

def load_names_from_csv() -> tuple[list[str], list[str]]:
    """Load diverse multi-country first and last names dynamically from names.csv"""
    global _CACHED_FIRST_NAMES, _CACHED_LAST_NAMES
    if _CACHED_FIRST_NAMES and _CACHED_LAST_NAMES:
        return _CACHED_FIRST_NAMES, _CACHED_LAST_NAMES

    first_names = []
    last_names = []
    
    if os.path.exists(NAMES_FILE):
        try:
            with open(NAMES_FILE, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    fn = (row.get("first_name") or row.get("FirstName") or "").strip()
                    ln = (row.get("last_name") or row.get("LastName") or "").strip()
                    if fn:
                        first_names.append(fn)
                    if ln:
                        last_names.append(ln)
        except Exception as e:
            log("NAME-WARN", f"Failed reading names.csv: {e}")

    # Fallback default seeds if file is empty or unreadable
    if not first_names:
        first_names = ["Alex", "James", "John", "David", "Michael", "Emily", "Sarah", "Daniel", "Chris", "Ryan", "Mateo", "Lucas", "Noah", "Liam", "Kenji", "Aarav", "Rahim", "Luca", "Arthur", "Gabriel"]
    if not last_names:
        last_names = ["Smith", "Miller", "Brown", "Jones", "Davis", "Wilson", "Taylor", "White", "Clark", "Hall", "Garcia", "Martinez", "Hernandez", "Rossi", "Sato", "Sharma", "Khan", "Dubois", "Müller", "Silva"]

    _CACHED_FIRST_NAMES = first_names
    _CACHED_LAST_NAMES = last_names
    return _CACHED_FIRST_NAMES, _CACHED_LAST_NAMES

def get_random_name() -> tuple[str, str]:
    """Returns a random (first_name, last_name) tuple from names.csv"""
    first_list, last_list = load_names_from_csv()
    first_name = random.choice(first_list).strip().capitalize()
    last_name = random.choice(last_list).strip().capitalize()
    return first_name, last_name

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULT_CONFIG = {
    "num_workers": 3,
    "browser_channel": "firefox",
    "headless": False,
    "viewport_width": 500,
    "viewport_height": 700,
    "email_services": {
        "mail_td": False,
        "cpanel": True
    },
    "password_config": {
        "prefix": "Manik",
        "random_chars_length": 3,
        "include_today_date": True
    },
    "google_sheet_webhook_url": ""
}

def log(tag, message, instance_name=""):
    """Clean & optimized console logger with padded tag headers"""
    timestamp = time.strftime("%H:%M:%S")
    inst_tag = f"[{instance_name:^6}]" if instance_name else "[  MAIN  ]"
    tag_padded = f"[{tag:<14}]"
    log_str = f"[{timestamp}] {inst_tag} {tag_padded} {message}"
    try:
        print(log_str, flush=True)
    except UnicodeEncodeError:
        safe_str = log_str.encode(sys.stdout.encoding or 'ascii', errors='replace').decode(sys.stdout.encoding or 'ascii')
        print(safe_str, flush=True)

def load_config():
    """Load configuration settings from config.json with CLI override support"""
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                cfg.update(loaded)
        except Exception:
            pass
    else:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
        except Exception:
            pass

    # CLI flag overrides for headless mode
    if "--headless" in sys.argv:
        cfg["headless"] = True
    elif "--no-headless" in sys.argv or "--headful" in sys.argv:
        cfg["headless"] = False

    return cfg

def generate_custom_password(instance_name=""):
    """Generate custom password based on config rules"""
    cfg = load_config()
    pwd_cfg = cfg.get("password_config", {})
    prefix = pwd_cfg.get("prefix", "Manik")
    num_random = pwd_cfg.get("random_chars_length", 3)
    include_date = pwd_cfg.get("include_today_date", True)
    
    chars = string.ascii_letters + string.digits
    random_part = ''.join(random.choice(chars) for _ in range(num_random))
    
    date_part = time.strftime("%d") if include_date else ""
    if date_part.startswith("0") and len(date_part) == 2:
        date_part = date_part[1:]
        
    password = f"{prefix}{random_part}{date_part}"
    log("PWD-GEN", f"Generated custom password: '{password}'", instance_name=instance_name)
    return password

def get_instance_email_file(instance_name=""):
    inst = instance_name.strip() or "default"
    return os.path.join(os.path.dirname(__file__), f"last_email_{inst}.json")

def delete_previous_cpanel_email(instance_name=""):
    """Delete previously created cPanel email for this specific worker instance"""
    email_file = get_instance_email_file(instance_name)
    if os.path.exists(email_file):
        try:
            with open(email_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                old_email = data.get("email")
                if old_email:
                    log("CPANEL-DEL", f"Deleting previously created cPanel email: {old_email}...", instance_name=instance_name)
                    del_url = f"{CPANEL_HOST}/execute/Email/delete_pop"
                    res = cpanel_session.get(del_url, params={"email": old_email}, timeout=10)
                    log("CPANEL-DEL", f"Delete API response: {res.json()}", instance_name=instance_name)
        except Exception as e:
            log("CPANEL-DEL-WARN", f"Failed to delete old cPanel email: {e}", instance_name=instance_name)

def save_last_created_email(full_email, instance_name=""):
    """Save newly created cPanel email address for this worker"""
    email_file = get_instance_email_file(instance_name)
    try:
        with open(email_file, "w", encoding="utf-8") as f:
            json.dump({"email": full_email, "timestamp": time.time()}, f, indent=2)
    except Exception as e:
        log("CPANEL-SAVE-WARN", f"Failed to save last email state: {e}", instance_name=instance_name)

def derive_auth_key(address: str, password: str) -> str:
    """Derive Argon2 ID auth key for Mail.td"""
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

def solve_pow(address: str, timestamp: int, difficulty: int) -> str:
    """Solve SHA256 proof-of-work challenge for Mail.td"""
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

def create_mail_td_account(email_prefix, password, instance_name=""):
    """Create a new email account using Mail.td API"""
    session = requests.Session()
    try:
        domain_name = "qabq.com"
        domains_res = session.get("https://mail.td/api/domains", timeout=8)
        if domains_res.status_code == 200:
            res_data = domains_res.json()
            doms_list = []
            if isinstance(res_data, dict) and "domains" in res_data:
                doms_list = res_data.get("domains", [])
            elif isinstance(res_data, list):
                doms_list = res_data

            valid_domains = []
            for d in doms_list:
                if isinstance(d, dict) and "domain" in d:
                    valid_domains.append(d["domain"])
                elif isinstance(d, str):
                    valid_domains.append(d)

            if valid_domains:
                domain_name = random.choice(valid_domains)
                log("MAILTD-DOMAIN", f"Selected Mail.td domain: '@{domain_name}'", instance_name=instance_name)

        full_email = f"{email_prefix}@{domain_name}"
        auth_key = derive_auth_key(full_email, password)
        difficulty = 15
        pow_token = None
        
        for _ in range(4):
            timestamp = int(time.time())
            nonce = solve_pow(full_email, timestamp, difficulty)
            pow_data = {"t": timestamp, "n": nonce, "d": difficulty}
            if pow_token:
                pow_data["token"] = pow_token

            payload = {
                "address": full_email,
                "auth_key": auth_key,
                "pow": pow_data
            }
            res = session.post("https://mail.td/api/accounts", json=payload, timeout=12)
            acc_data = res.json()
            
            if acc_data.get("status") == "retry":
                difficulty = acc_data.get("required_difficulty", difficulty)
                pow_token = acc_data.get("token")
                continue
                
            res.raise_for_status()
            acc_id = acc_data.get("id")
            jwt_token = acc_data.get("token")
            if full_email and acc_id and jwt_token:
                log("MAILTD-SUCCESS", f"Created Mail.td email: {full_email}", instance_name=instance_name)
                return full_email, password, f"MAILTD:{acc_id}:{jwt_token}"
    except Exception as e:
        log("MAILTD-WARN", f"Mail.td creation failed ({e})", instance_name=instance_name)
    return None

def create_cpanel_account(email_prefix, password, instance_name=""):
    """Create a new email account using cPanel API"""
    log("CPANEL-CREATE", "Creating cPanel email account...", instance_name=instance_name)
    delete_previous_cpanel_email(instance_name=instance_name)
    cpanel_full_email = f"{email_prefix}@{CPANEL_DOMAIN}"
    try:
        add_url = f"{CPANEL_HOST}/execute/Email/add_pop"
        payload = {
            "email": email_prefix,
            "domain": CPANEL_DOMAIN,
            "password": password
        }
        res = cpanel_session.get(add_url, params=payload, timeout=12)
        res_json = res.json()
        if res_json.get("status") == 1:
            log("CPANEL-SUCCESS", f"Created cPanel email: {cpanel_full_email}", instance_name=instance_name)
            save_last_created_email(cpanel_full_email, instance_name=instance_name)
            return cpanel_full_email, password, f"CPANEL:{cpanel_full_email}:{password}"
        else:
            log("CPANEL-ERR", f"cPanel API error: {res_json}", instance_name=instance_name)
    except Exception as e:
        log("CPANEL-ERR", f"cPanel creation exception: {e}", instance_name=instance_name)
    return cpanel_full_email, password, f"CPANEL:{cpanel_full_email}:{password}"

def generate_temp_email(instance_name=""):
    """Generate new temp email based on config settings"""
    cfg = load_config()
    email_cfg = cfg.get("email_services", {})
    use_mail_td = email_cfg.get("mail_td", False)
    use_cpanel = email_cfg.get("cpanel", True)
    
    password = generate_custom_password(instance_name=instance_name)
    first, last = get_random_name()
    clean_first = re.sub(r'[^a-zA-Z0-9]', '', first.lower())
    clean_last = re.sub(r'[^a-zA-Z0-9]', '', last.lower())
    num = random.randint(1000, 9999)
    email_prefix = f"{clean_first}{clean_last}{num}"
    
    enabled_providers = []
    if use_mail_td:
        enabled_providers.append("mail_td")
    if use_cpanel:
        enabled_providers.append("cpanel")
        
    if not enabled_providers:
        log("MAIL-WARN", "No email provider enabled in config.json! Defaulting to cPanel", instance_name=instance_name)
        enabled_providers = ["cpanel"]
        
    selected_provider = random.choice(enabled_providers)
    log("MAIL-CREATE", f"Generating email for {first} {last} using {selected_provider.upper()}...", instance_name=instance_name)

    if selected_provider == "mail_td":
        res = create_mail_td_account(email_prefix, password, instance_name=instance_name)
        if res:
            return res
        if use_cpanel:
            log("MAILTD-WARN", "Mail.td failed. Falling back to cPanel...", instance_name=instance_name)
            return create_cpanel_account(email_prefix, password, instance_name=instance_name)
        else:
            log("MAILTD-WARN", "Retrying Mail.td creation...", instance_name=instance_name)
            return create_mail_td_account(f"{email_prefix}{random.randint(10,99)}", password, instance_name=instance_name)
    else:
        return create_cpanel_account(email_prefix, password, instance_name=instance_name)

def extract_otp_from_text(text, mail_from="", subj=""):
    """Extract 6-digit OTP code from email text"""
    mail_from_lower = mail_from.lower().strip()
    subj_lower = subj.lower().strip()
    combined = f"{mail_from_lower} {subj_lower} {text.lower()}".strip()
    
    if "welcome" in subj_lower and "meta" not in subj_lower:
        return None

    clean_text = re.sub(r'<[^>]+>', ' ', text)
    clean_text = re.sub(r'&[a-z0-9#]+;', ' ', clean_text)
    clean_text = ' '.join(clean_text.split())

    is_meta_email = any(k in combined for k in ["meta", "facebook", "instagram", "confirm your email", "confirmation code", "security code"])
    if not is_meta_email:
        return None

    patterns = [
        r'(?:confirmation\s+code|security\s+code|confirm\s+code|your\s+code|code\s+is)[\s:\n]*\b(\d{6})\b',
        r'\b(\d{6})\b[\s:\n]*(?:is\s+your\s+code|is\s+your\s+confirmation)',
    ]
    for pat in patterns:
        match = re.search(pat, clean_text, re.IGNORECASE)
        if match:
            return match.group(1)
            
    match = re.search(r'(?:confirmation|code|security|verify|confirm)[^\d]{1,50}\b(\d{6})\b', clean_text, re.IGNORECASE)
    if match:
        return match.group(1)
            
    return None

def fetch_otp_from_temp_mail(email, password, token=None, max_wait_sec=50, instance_name=""):
    """Fetch OTP code from inbox with fast polling"""
    log("OTP-FETCH", f"Polling inbox for {email} (timeout {max_wait_sec}s)...", instance_name=instance_name)
    start_time = time.time()
    session = requests.Session()

    if token and token.startswith("MAILTD:"):
        parts = token.split(":", 2)
        acc_id = parts[1] if len(parts) > 1 else ""
        jwt_token = parts[2] if len(parts) > 2 else ""
        headers = {"Authorization": f"Bearer {jwt_token}"}
        
        while time.time() - start_time < max_wait_sec:
            try:
                msg_url = f"https://mail.td/api/accounts/{acc_id}/messages"
                res = session.get(msg_url, headers=headers, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    messages = data.get("messages", []) if isinstance(data, dict) else data
                    for msg in messages:
                        msg_id = msg.get("id")
                        subj = str(msg.get("subject", ""))
                        intro = str(msg.get("intro", "") or msg.get("preview_text", ""))
                        from_addr = str(msg.get("from", "") or msg.get("sender", ""))
                        
                        otp = extract_otp_from_text(f"{subj} {intro}", mail_from=from_addr, subj=subj)
                        if otp:
                            log("OTP-SUCCESS", f"Found Meta OTP: {otp}", instance_name=instance_name)
                            return otp

                        if msg_id:
                            detail_url = f"https://mail.td/api/accounts/{acc_id}/messages/{msg_id}"
                            res_d = session.get(detail_url, headers=headers, timeout=10)
                            if res_d.status_code == 200:
                                detail = res_d.json()
                                body = str(detail.get("text_body", "")) + " " + str(detail.get("html_body", ""))
                                otp = extract_otp_from_text(body, mail_from=from_addr, subj=subj)
                                if otp:
                                    log("OTP-SUCCESS", f"Found Meta OTP: {otp}", instance_name=instance_name)
                                    return otp
            except Exception as e:
                log("OTP-WARN", f"Mail.td poll error: {e}", instance_name=instance_name)
            time.sleep(2.5)
        log("OTP-TIMEOUT", f"Timed out waiting for OTP for {email}.", instance_name=instance_name)
        return None

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    while time.time() - start_time < max_wait_sec:
        try:
            mail = imaplib.IMAP4_SSL(CPANEL_DOMAIN, 993, ssl_context=ssl_ctx)
            mail.login(email, password)
            mail.select('INBOX')
            status, msg_ids = mail.search(None, 'ALL')
            if status == 'OK' and msg_ids[0]:
                id_list = msg_ids[0].split()
                for msg_id in reversed(id_list):
                    res, msg_data = mail.fetch(msg_id, '(RFC822)')
                    if res == 'OK':
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email_lib.message_from_bytes(response_part[1])
                                subj = msg.get('Subject', '')
                                from_addr = msg.get('From', '')
                                body = ""
                                if msg.is_multipart():
                                    for part in msg.walk():
                                        if part.get_content_type() in ["text/plain", "text/html"]:
                                            body += part.get_payload(decode=True).decode('utf-8', errors='ignore') + " "
                                else:
                                    body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                                
                                otp = extract_otp_from_text(f"{subj} {body}", mail_from=from_addr, subj=subj)
                                if otp:
                                    log("OTP-SUCCESS", f"Found cPanel IMAP OTP: {otp}", instance_name=instance_name)
                                    try:
                                        mail.logout()
                                    except Exception:
                                        pass
                                    return otp
            try:
                mail.logout()
            except Exception:
                pass
        except Exception as e:
            log("OTP-WARN", f"IMAP poll error: {e}", instance_name=instance_name)
        time.sleep(2.5)

    return None

def send_to_google_sheet(timestamp, email, username, password, instance_name=""):
    """Save created credentials to Google Sheet using official Google Sheets API service"""
    try:
        if not gsheet_manager.is_configured():
            return

        def _save():
            try:
                gsheet_manager.save_created_account(
                    timestamp=timestamp,
                    email=email,
                    username=username,
                    password=password,
                    status="",
                    instance_name=instance_name
                )
            except Exception as err:
                log("GSHEET-ERR", f"Failed syncing to Google Sheet API: {err}", instance_name=instance_name)

        threading.Thread(target=_save, daemon=True).start()
    except Exception as e:
        log("GSHEET-ERR", f"GSheet trigger note: {e}", instance_name=instance_name)

def save_credentials_csv(email, password, username="", instance_name=""):
    """Save credentials directly to Google Sheet Sheet1 (100% Online mode)"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    uname = username.strip() if username else "Meta_User"
    send_to_google_sheet(timestamp, email, uname, password, instance_name=instance_name)


async def get_active_meta_page(context: BrowserContext, default_page: Page = None) -> Page:
    """Universally locate and return the active Meta page across all open tabs/popups."""
    try:
        pages = context.pages
        if not pages:
            return default_page or await context.new_page()

        # Priority 1: Auth / Checkpoint / Challenge / Verification page
        for p in reversed(pages):
            try:
                if p.is_closed():
                    continue
                url = p.url.lower()
                if any(k in url for k in ["auth.meta.com", "checkpoint", "challenge", "recover", "login", "signup", "confirm"]):
                    return p
            except Exception:
                pass

        # Priority 2: Meta AI main domain
        for p in reversed(pages):
            try:
                if p.is_closed():
                    continue
                url = p.url.lower()
                if "meta.ai" in url:
                    return p
            except Exception:
                pass

        # Priority 3: Any non-blank page
        for p in reversed(pages):
            try:
                if not p.is_closed() and p.url and not p.url.startswith("about:blank"):
                    return p
            except Exception:
                pass

        return pages[-1]
    except Exception:
        return default_page or (context.pages[0] if context.pages else None)

async def robust_click(page: Page, selectors: list[str] | str, desc="Element", timeout=1000, force_fallback=True) -> bool:
    """Fast, reliable element clicking with fallback strategies."""
    if isinstance(selectors, str):
        query_candidates = [selectors]
    else:
        combined = ", ".join(selectors)
        query_candidates = [combined]

    for sel in query_candidates:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=timeout):
                try:
                    await loc.click(timeout=timeout)
                    return True
                except Exception:
                    if force_fallback:
                        try:
                            await loc.click(force=True, timeout=600)
                            return True
                        except Exception:
                            try:
                                await loc.evaluate("el => el.click()")
                                return True
                            except Exception:
                                pass
        except Exception:
            continue
            
    if isinstance(selectors, list) and len(selectors) > 1:
        for single_sel in selectors:
            try:
                loc = page.locator(single_sel).first
                if await loc.is_visible(timeout=200):
                    try:
                        await loc.click(timeout=600)
                        return True
                    except Exception:
                        await loc.click(force=True, timeout=400)
                        return True
            except Exception:
                continue

    return False

async def human_type(locator, text: str, min_delay: int = 35, max_delay: int = 85):
    """Type text with genuine human cadence, randomized keystroke delays, and natural micro-pauses"""
    await locator.click()
    await asyncio.sleep(random.uniform(0.08, 0.16))

    try:
        await locator.press("Control+A")
        await asyncio.sleep(random.uniform(0.03, 0.08))
        await locator.press("Backspace")
        await asyncio.sleep(random.uniform(0.04, 0.10))
    except Exception:
        pass

    for i, ch in enumerate(text):
        delay_ms = random.randint(min_delay, max_delay)
        await locator.press_sequentially(ch, delay=delay_ms)
        
        # Natural human pauses at punctuation, spaces, or small rhythm breaks
        if ch in [" ", "@", ".", "_", "-"] or (i > 0 and i % random.randint(4, 7) == 0):
            await asyncio.sleep(random.uniform(0.06, 0.18))

    await asyncio.sleep(random.uniform(0.08, 0.20))
    try:
        await locator.evaluate("el => { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }")
    except Exception:
        pass

async def robust_fill(page: Page, selectors: list[str] | str, value: str, desc="Input field", timeout=2000, human_speed=True) -> bool:
    """Universally fill an input field with natural human typing and delays."""
    if isinstance(selectors, str):
        query_candidates = [selectors]
    else:
        combined = ", ".join(selectors)
        query_candidates = [combined]

    for sel in query_candidates:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=timeout):
                if human_speed:
                    try:
                        await human_type(loc, value)
                        return True
                    except Exception:
                        pass
                try:
                    await loc.click()
                    await loc.fill(value, timeout=timeout)
                    await loc.evaluate("el => { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }")
                    return True
                except Exception:
                    try:
                        await loc.click()
                        await loc.press("Control+A")
                        await loc.press("Backspace")
                        await loc.press_sequentially(value, delay=random.randint(30, 60))
                        return True
                    except Exception:
                        try:
                            await loc.evaluate(f"(el, val) => {{ el.value = val; el.dispatchEvent(new Event('input', {{ bubbles: true }})); el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}", value)
                            return True
                        except Exception:
                            pass
        except Exception:
            continue
            
    if isinstance(selectors, list) and len(selectors) > 1:
        for single_sel in selectors:
            try:
                loc = page.locator(single_sel).first
                if await loc.is_visible(timeout=300):
                    if human_speed:
                        try:
                            await human_type(loc, value)
                            return True
                        except Exception:
                            pass
                    await loc.fill(value)
                    return True
            except Exception:
                continue

    return False

# ==============================================================================
# REAL-TIME PAGE STATE DETECTION ENGINE
# ==============================================================================

async def extract_meta_username(context: BrowserContext) -> str | None:
    """Extract logged-in Meta AI username from user-menu-button elements across all open pages"""
    user_menu_combined = (
        "div[data-testid='user-menu-button'] span.truncate, "
        "div[data-testid='user-menu-button'] span, "
        "div[data-slot='dropdown-menu-trigger'][data-testid='user-menu-button'] span, "
        "div[data-slot='dropdown-menu-trigger'][data-testid='user-menu-button'], "
        "[data-testid='user-menu-button'], "
        "[aria-label='User menu'] span, "
        "[aria-label='User menu'], "
        "[data-testid='user-profile-button'], "
        "div[data-slot='sidebar-footer'] span.truncate, "
        "div[data-slot='sidebar-footer'] [role='button'] span"
    )
    ignored_keywords = [
        "user", "user menu", "user profile", "profile", "account",
        "log in", "sign up", "sign in", "settings", "menu", "help",
        "continue", "next", "confirm", "submit", "meta", "meta ai"
    ]
    for p in context.pages:
        try:
            if p.is_closed():
                continue
            el = p.locator(user_menu_combined).first
            if await el.is_visible(timeout=150):
                text = (await el.inner_text()).strip()
                if text:
                    for line in [l.strip() for l in text.splitlines() if l.strip()]:
                        if line.lower() not in ignored_keywords and len(line) >= 2:
                            return line
        except Exception:
            pass
    return None

async def check_meta_success_state(context: BrowserContext) -> tuple[bool, str]:
    """Check across all context pages if registration/verification reached a confirmed success state"""
    for p in context.pages:
        try:
            if p.is_closed():
                continue
            url = p.url.lower()
            title = (await p.title()).lower()
            parsed = urllib.parse.urlparse(url)
            path = parsed.path.lower()

            # Ignore initial auth login URLs, error redirects, privacy/cookies/consent pages
            if ("invalid_redirect" in url or "error=" in url or 
                "privacy" in path or "cookies" in path or "consent" in path or
                "privacy" in url or "consent" in url or "user_cookie_choice" in url):
                continue

            # If page contains login/auth form elements or cookie banners, it is NOT success
            if await p.locator("[role='button']:has-text('Use mobile number'), input[placeholder*='Mobile' i], input[type='password'], input[name='contact'], :has-text('Continue with Facebook'), :has-text('Enter the code'), :has-text('Check your email'), :has-text('Allow the use of cookies from Meta'), span:has-text('Allow all cookies')").count() > 0:
                continue

            # 1. Explicit logged-in URLs (match PATH directly, not query params)
            if path in ["/language", "/language/", "/settings", "/settings/", "/recover/success", "/recover/success/"] or "recover/success" in path:
                uname = await extract_meta_username(context) or "Meta_User"
                return True, uname

            # 2. Logged in Meta AI domain
            if "meta.ai" in url and "auth.meta.com" not in url:
                uname = await extract_meta_username(context) or "Meta_User"
                return True, uname

            # 3. Log out button inside active user session
            if await p.locator("a:has-text('Log Out'), button:has-text('Log Out'), [role='button']:has-text('Log Out'), a:has-text('LOG OUT'), button:has-text('LOG OUT')").count() > 0:
                if await p.locator("input[type='password'], input[name='contact']").count() == 0:
                    uname = await extract_meta_username(context) or "Meta_User"
                    return True, uname

            # 4. Verified user profile extraction
            uname = await extract_meta_username(context)
            if uname:
                return True, uname
        except Exception:
            pass

    return False, ""

async def is_otp_screen_active(page: Page) -> bool:
    """Accurately check if the current page is on OTP/Verification code screen"""
    try:
        if page.is_closed():
            return False
        
        # Check text indicators
        text_check = page.locator(
            ":has-text('Enter the code'), "
            ":has-text('Enter confirmation code'), "
            ":has-text('Enter code'), "
            ":has-text('Check your email'), "
            ":has-text('We sent a code'), "
            ":has-text('We sent an email'), "
            ":has-text('Enter the 6-digit code'), "
            ":has-text('6-digit code'), "
            ":has-text('Enter 6-digit code'), "
            ":has-text('Enter security code'), "
            ":has-text('Confirmation code')"
        ).first
        if await text_check.count() > 0:
            return True

        # Check input indicators
        input_check = page.locator(
            "input[autocomplete*='one-time-code'], "
            "input[name*='code' i], "
            "input[aria-label*='code' i], "
            "input[placeholder*='code' i], "
            "input[data-index]"
        ).first
        if await input_check.is_visible(timeout=150):
            return True
    except Exception:
        pass
    return False

async def detect_current_page_state(context: BrowserContext, default_page: Page = None) -> tuple[str, Page]:
    """
    Intelligently inspect all open pages and return the current exact state:
    - STATE_SUCCESS: Logged in / Meta AI home / Language / Settings
    - STATE_ERROR_MODAL: 'Something went wrong' popup / invalid redirect
    - STATE_COOKIE_BANNER: Cookie consent overlay
    - STATE_SAVE_LOGIN: 'Save your login info?' screen
    - STATE_RECAPTCHA: 2Captcha / reCAPTCHA challenge
    - STATE_SELFIE_UPLOAD: 'Upload a verification selfie' / photo upload
    - STATE_OTP_INPUT: OTP 6-digit input screen
    - STATE_PASSWORD_INPUT: Password input visible (or combined Name/DOB/Password)
    - STATE_DOB_INPUT: Birthday dropdowns/inputs visible
    - STATE_NAME_INPUT: Name input visible
    - STATE_TERMS_AGREE: 'Agree and continue' / checkboxes screen
    - STATE_EMAIL_INPUT: Email input visible
    - STATE_USE_EMAIL_CHOICE: 'Use mobile number or email address' / 'Create new account' / saved account card
    - STATE_CHECKPOINT: Checkpoint / Challenge URL
    - STATE_UNKNOWN: Unknown / loading
    """
    # Check across context pages
    pages_to_check = []
    if default_page and not default_page.is_closed():
        pages_to_check.append(default_page)
    for p in reversed(context.pages):
        if p not in pages_to_check and not p.is_closed():
            pages_to_check.append(p)

    if not pages_to_check:
        return "STATE_UNKNOWN", default_page or (await context.new_page())

    # 1. Success check across all tabs
    is_success, _ = await check_meta_success_state(context)
    if is_success:
        return "STATE_SUCCESS", pages_to_check[0]

    for p in pages_to_check:
        try:
            url = p.url.lower()

            # 2. Check for Error Modals ('Something went wrong')
            if await p.locator("div[role='dialog']:has-text('Something went wrong'), :has-text('Something went wrong'), div[role='dialog'] button:has-text('OK')").count() > 0:
                return "STATE_ERROR_MODAL", p

            # 3. Check for Cookie Banners / Privacy Cookie Policy Page (e.g. auth.meta.com/privacy/cookies)
            is_cookie_page = (
                "privacy" in url or "cookies" in url or
                await p.locator(
                    "span.xlyipyv:has-text('Allow all cookies'), "
                    "span.xuxw1ft:has-text('Allow all cookies'), "
                    "span:has-text('Allow all cookies'), "
                    "[role='button']:has-text('Allow all cookies'), "
                    "div[role='button']:has-text('Allow all cookies'), "
                    "button:has-text('Allow all cookies'), "
                    ":has-text('Allow the use of cookies from Meta'), "
                    ":has-text('About cookies'), "
                    ":has-text('Choose cookies by category'), "
                    ":has-text('Other ways that you can control your information'), "
                    "span:has-text('Accept all cookies'), "
                    "[role='button']:has-text('Accept all cookies'), "
                    "button:has-text('Accept all cookies'), "
                    "[role='button']:has-text('Allow essential cookies'), "
                    "button:has-text('Allow essential cookies'), "
                    "[role='button']:has-text('Decline optional cookies'), "
                    "button:has-text('Decline optional cookies')"
                ).count() > 0
            )
            if is_cookie_page:
                return "STATE_COOKIE_BANNER", p

            # 4. Check for 'Save your login info?' Prompt (Screenshot)
            if await p.locator(":has-text('Save your login info'), :has-text('save the login info'), button:has-text('Not now'), [role='button']:has-text('Not now'), span:has-text('Not now')").count() > 0:
                return "STATE_SAVE_LOGIN", p

            # 5. Check for Captcha / reCAPTCHA / 2Captcha (Screenshot: Confirm that you're human with 2Captcha solver)
            if (await p.locator(".captcha-solver, [class*='captcha-solver'], iframe[src*='recaptcha'], iframe[title*='reCAPTCHA'], :has-text('Solve with 2Captcha'), :has-text(\"I'm not a robot\"), :has-text('I’m not a robot'), :has-text(\"Confirm that you're human\"), :has-text(\"Confirm that you’re human\")").count() > 0 or
                (any(k in url for k in ["human", "checkpoint", "challenge"]) and await p.locator("iframe[src*='recaptcha'], .captcha-solver, :has-text('robot'), :has-text('Confirm that you')").count() > 0)):
                return "STATE_RECAPTCHA", p

            # 6. Check for Verification Selfie / Image Upload
            if await p.locator(":has-text('Upload a verification selfie'), :has-text('Take a verification selfie'), span:has-text('Upload Image'), div[aria-label='Upload Image'], button:has-text('Upload Image'), button:has-text('Upload photo'), input.x1s85apg, div.xeq5yr9 input[type='file'], input[type='file'][accept*='image']").count() > 0:
                return "STATE_SELFIE_UPLOAD", p

            # 7. Check for OTP Screen
            if await is_otp_screen_active(p):
                return "STATE_OTP_INPUT", p

            # 8. Check for Choice Screen: 'Use mobile number or email address' / Social Logins (Facebook / Instagram) / Saved Account
            has_choice_screen = (
                await p.locator(
                    "[role='button']:has-text('Use mobile number or email address'), "
                    "button:has-text('Use mobile number or email address'), "
                    "span:has-text('Use mobile number or email address'), "
                    "div:has-text('Use mobile number or email address'), "
                    "[role='button']:has-text('Use email address or mobile number'), "
                    "button:has-text('Use email address or mobile number'), "
                    "span:has-text('Use email address or mobile number'), "
                    ":has-text('Continue with Facebook'), "
                    ":has-text('Continue with Instagram'), "
                    ":has-text('Log in or create an account'), "
                    "[role='button']:has-text('Continue as'), "
                    "button:has-text('Continue as')"
                ).count() > 0 and
                await p.locator("input[name='contact'], input[placeholder*='Mobile number' i], input[placeholder*='mobile number or email' i], input[type='password'], input[autocomplete='given-name']").count() == 0
            )
            if has_choice_screen:
                return "STATE_USE_EMAIL_CHOICE", p

            # 9. Check for 'Create a new Meta account?' Confirmation Screen
            if (await p.locator(":has-text('Create a new Meta account?'), :has-text(\"We can't find a Meta account\"), button:has-text('Create new account'), [role='button']:has-text('Create new account')").count() > 0 and
                await p.locator("button:has-text('Create new account'), [role='button']:has-text('Create new account')").count() > 0):
                return "STATE_CREATE_ACCOUNT_CONFIRM", p

            # 10. Check for Email Input Screen ('What\'s your mobile number or email address?')
            has_email_screen = (
                await p.locator(":has-text(\"What's your mobile number or email address\"), :has-text('Enter your mobile number or email address'), :has-text('What’s your mobile number or email address')").count() > 0 or
                await p.locator("input[placeholder*='Mobile number' i], input[placeholder*='mobile number or email' i], input[aria-label*='Mobile number' i], input[name='contact'], input[type='email']").count() > 0
            )
            if has_email_screen:
                return "STATE_EMAIL_INPUT", p

            # 11. Check for Name Inputs ('What\'s your name?')
            has_name_screen = (
                await p.locator(":has-text(\"What's your name?\"), :has-text('What’s your name?')").count() > 0 or
                await p.locator("input[placeholder*='first name' i], input[aria-label*='first name' i], input[autocomplete='given-name']").count() > 0
            )
            if has_name_screen:
                return "STATE_NAME_INPUT", p

            # 12. Check for Password Input
            pwd_loc = p.locator("input[type='password'], input[name*='pass' i], input[autocomplete*='password' i], input[placeholder*='password' i]").first
            if await pwd_loc.is_visible(timeout=200):
                return "STATE_PASSWORD_INPUT", p

            # 13. Check for Birthday / DOB Selectors
            has_dob = (
                await p.locator(":has-text('date of birth'), :has-text('Date of birth')").count() > 0 or
                await p.locator("select[name*='month' i], select[name*='day' i], select[name*='year' i]").count() >= 2 or
                await p.locator("[role='combobox'][aria-label*='month' i], [role='combobox'][aria-label*='day' i]").count() > 0 or
                await p.locator("input[name*='birthday' i], input[placeholder*='YYYY' i], input[placeholder*='MM' i]").count() > 0
            )
            if has_dob:
                return "STATE_DOB_INPUT", p

            # 14. Check for Terms Agreement / Checkboxes
            terms_btn = p.locator("button:has-text('Agree and continue'), button:has-text('I agree'), div[role='button']:has-text('Agree and continue'), input[type='checkbox']:not(:checked)").first
            if await terms_btn.is_visible(timeout=200):
                return "STATE_TERMS_AGREE", p

            # 15. Check for 'Confirm that you're human to use your account' (Shield Checkpoint Screen)
            if (await p.locator(":has-text(\"Confirm that you're human to use your account\"), :has-text(\"Confirm that you’re human to use your account\")").count() > 0 or
                ("checkpoint" in url and await p.locator("button:has-text('Continue'), [role='button']:has-text('Continue')").count() > 0 and
                 await p.locator("input[placeholder*='Mobile' i], input[autocomplete='given-name']").count() == 0)):
                return "STATE_HUMAN_CONFIRM", p

            # 16. Check for Generic Checkpoint URL
            if "checkpoint" in url or "challenge" in url:
                return "STATE_CHECKPOINT", p

        except Exception:
            continue

    return "STATE_UNKNOWN", pages_to_check[0]

# ==============================================================================
# STATE-SPECIFIC ACTION HANDLERS
# ==============================================================================

async def handle_cookie_banners(page: Page, instance_name="") -> bool:
    """Dismiss cookie consent banner by scrolling to the bottom and clicking 'Allow all cookies'"""
    try:
        if page.is_closed():
            return False

        # 1. Scroll down to the bottom of the page across all containers
        try:
            await page.evaluate('''() => {
                window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' });
                if (document.documentElement) document.documentElement.scrollTop = document.documentElement.scrollHeight;
                if (document.body) document.body.scrollTop = document.body.scrollHeight;
                const scrollables = Array.from(document.querySelectorAll("div, main, section, [role='dialog'], [role='main']"));
                for (const s of scrollables) {
                    if (s.scrollHeight > s.clientHeight) {
                        s.scrollTop = s.scrollHeight;
                    }
                }
            }''')
            await asyncio.sleep(0.3)
            await page.keyboard.press("End")
            await asyncio.sleep(0.3)
        except Exception:
            pass

        # Strategy 1: Targeted Playwright locators for 'Allow all cookies'
        cookie_selectors = [
            "span.xlyipyv:has-text('Allow all cookies')",
            "span.xuxw1ft:has-text('Allow all cookies')",
            "span.x1lliihq.x193iq5w:has-text('Allow all cookies')",
            "span:has-text('Allow all cookies')",
            "div[role='button']:has(span:has-text('Allow all cookies'))",
            "[role='button']:has(span:has-text('Allow all cookies'))",
            "[role='button']:has-text('Allow all cookies')",
            "div[role='button']:has-text('Allow all cookies')",
            "button:has-text('Allow all cookies')",
            "[role='button']:has-text('Accept all cookies')",
            "div[role='button']:has-text('Accept all cookies')",
            "button:has-text('Accept all cookies')",
            "[role='button']:has-text('Allow essential cookies')",
            "button:has-text('Allow essential cookies')",
            "[role='button']:has-text('Decline optional cookies')",
            "button:has-text('Decline optional cookies')",
            "[role='button']:has-text('Allow cookies')",
            "button:has-text('Allow cookies')"
        ]
        for sel in cookie_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=300):
                    log("ACTION", f"Clicking 'Allow all cookies' via ({sel})...", instance_name=instance_name)
                    await loc.scroll_into_view_if_needed(timeout=500)
                    await loc.click(force=True, timeout=800)
                    await asyncio.sleep(0.6)
                    return True
            except Exception:
                pass

        # Strategy 2: Direct JS DOM Dispatch (scrolls target button into view and clicks it)
        clicked_js = await page.evaluate('''() => {
            window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' });
            const allElements = Array.from(document.querySelectorAll("span, [role='button'], button, div[tabindex='0']"));
            for (const el of allElements) {
                const txt = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (txt === 'allow all cookies' || txt.includes('allow all cookies') || txt.includes('accept all cookies') || txt.includes('allow essential cookies')) {
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    el.click();
                    const parent = el.closest("[role='button']") || el.closest("button") || el;
                    parent.click();
                    parent.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    parent.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    parent.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    return true;
                }
            }
            return false;
        }''')
        if clicked_js:
            log("ACTION", "Dismissed 'Allow all cookies' banner via JS evaluation!", instance_name=instance_name)
            await asyncio.sleep(0.6)
            return True

        # Strategy 3: Bounding box coordinate click
        span_loc = page.locator("span:has-text('Allow all cookies')").first
        if await span_loc.count() > 0:
            await span_loc.scroll_into_view_if_needed(timeout=500)
            box = await span_loc.bounding_box()
            if box:
                log("ACTION", f"Clicking 'Allow all cookies' via coordinates ({box['x']:.0f}, {box['y']:.0f})...", instance_name=instance_name)
                await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await asyncio.sleep(0.6)
                return True
    except Exception as e:
        log("ACTION-WARN", f"handle_cookie_banners note: {e}", instance_name=instance_name)
    return False

async def dismiss_error_modals(page: Page, instance_name="") -> bool:
    """Universally dismiss 'Something went wrong' popup by pressing Escape key first, then clicking 'Allow all cookies'"""
    try:
        if page.is_closed():
            return False

        has_dialog = await page.locator("div[role='dialog']:has-text('Something went wrong'), :has-text('Something went wrong'), div[role='dialog'] [role='button']:has-text('OK'), div[role='dialog'] button:has-text('OK')").count() > 0
        if has_dialog:
            log("ACTION", "Dismissing 'Something went wrong' popup by pressing ESC...", instance_name=instance_name)

            # 1. Press ESC key first as primary dismissal
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.4)

            # 2. If dialog is still visible, click OK / Close button or evaluate JS
            if await page.locator("div[role='dialog']:has-text('Something went wrong'), :has-text('Something went wrong')").count() > 0:
                ok_selectors = [
                    "div[role='dialog'] [role='button']:has-text('OK')",
                    "div[role='dialog'] div[role='button']:has-text('OK')",
                    "div[role='dialog'] button:has-text('OK')",
                    "div[role='dialog'] [aria-label='Close']",
                    "div[role='dialog'] [role='button'][tabindex='0']"
                ]
                for sel in ok_selectors:
                    try:
                        loc = page.locator(sel).first
                        if await loc.is_visible(timeout=200):
                            await loc.click(force=True, timeout=500)
                            await asyncio.sleep(0.3)
                            break
                    except Exception:
                        pass

                # JS click fallback for dialog buttons
                await page.evaluate('''() => {
                    const dialog = document.querySelector("div[role='dialog']");
                    if (dialog) {
                        const btns = Array.from(dialog.querySelectorAll("[role='button'], button, div[tabindex='0'], span"));
                        for (const btn of btns) {
                            const txt = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                            if (txt === 'ok' || btn.getAttribute('aria-label') === 'Close') {
                                const target = btn.closest("[role='button']") || btn.closest("button") || btn;
                                target.click();
                                target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                                return;
                            }
                        }
                    }
                }''')
                await asyncio.sleep(0.3)

            # 3. Immediately click 'Allow all cookies' on the underlying cookie banner
            log("ACTION", "Clicking 'Allow all cookies' after popup dismissal...", instance_name=instance_name)
            await handle_cookie_banners(page, instance_name=instance_name)
            return True
    except Exception as e:
        log("ACTION-WARN", f"dismiss_error_modals note: {e}", instance_name=instance_name)
    return False

async def handle_save_login_info_prompt(page: Page, context: BrowserContext, instance_name="") -> bool:
    """Universally handle 'Save your login info?' screen by clicking the active 'Not now' button"""
    for p in [page] + [pg for pg in context.pages if pg != page and not pg.is_closed()]:
        try:
            # 1. Target active, non-disabled role='button' containing 'Not now'
            active_btn_selectors = [
                "div[role='button'][tabindex='0']:has-text('Not now')",
                "div[role='button']:not([aria-disabled='true']):has-text('Not now')",
                "[role='button']:not([aria-disabled='true']):has-text('Not now')",
                "button:not([disabled]):has-text('Not now')",
                "div[role='button']:has-text('Not now')"
            ]
            for sel in active_btn_selectors:
                try:
                    loc = p.locator(sel).last  # The active button is after the aria-hidden placeholder
                    if await loc.is_visible(timeout=300):
                        log("ACTION", f"Clicking active 'Not now' button ({sel})...", instance_name=instance_name)
                        await loc.click(force=True, timeout=1000)
                        await asyncio.sleep(0.8)
                        return True
                except Exception:
                    pass

            # 2. Direct DOM evaluation targeting the real interactive Not now element
            clicked_js = await p.evaluate('''() => {
                const buttons = Array.from(document.querySelectorAll("div[role='button'], button, [role='button']"));
                for (const btn of buttons) {
                    const txt = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                    const disabled = btn.getAttribute('aria-disabled') === 'true' || btn.disabled;
                    const hidden = btn.closest('[aria-hidden="true"]');
                    if (txt.includes('not now') && !disabled && !hidden) {
                        btn.click();
                        btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                        return true;
                    }
                }
                const spans = Array.from(document.querySelectorAll("span"));
                for (const sp of spans) {
                    if ((sp.textContent || '').trim().toLowerCase() === 'not now') {
                        const parentBtn = sp.closest("[role='button']") || sp;
                        if (parentBtn.getAttribute('aria-disabled') !== 'true' && !parentBtn.closest('[aria-hidden="true"]')) {
                            parentBtn.click();
                            parentBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                            return true;
                        }
                    }
                }
                return false;
            }''')
            if clicked_js:
                log("ACTION", "Clicked active 'Not now' button via JS DOM evaluation!", instance_name=instance_name)
                await asyncio.sleep(0.8)
                return True
        except Exception as e:
            log("ACTION-WARN", f"Save login prompt note: {e}", instance_name=instance_name)
    return False

async def try_click_saved_account_card(page: Page, instance_name="") -> bool:
    """Click saved Meta account card/pill on auth.meta.com"""
    try:
        if page.is_closed():
            return False
        account_selectors_combined = (
            "div[role='button']:has-text('Continue as'), "
            "[role='button']:has-text('Continue as'), "
            "button:has-text('Continue as'), "
            "div[role='button']:has-text('@'), "
            "[role='button']:has-text('@'), "
            "div[role='button']:has-text('.com')"
        )
        btn = page.locator(account_selectors_combined).first
        if await btn.is_visible(timeout=200):
            txt = (await btn.inner_text()).replace('\n', ' ').strip()
            if "use another account" not in txt.lower() and "log in to another account" not in txt.lower():
                log("ACTION", f"Clicking saved account card: '{txt}'", instance_name=instance_name)
                await btn.click(force=True)
                await asyncio.sleep(1.0)
                return True
    except Exception:
        pass
    return False

async def click_use_mobile_or_email(page: Page, context: BrowserContext, instance_name="") -> bool:
    """Click 'Use mobile number or email address' or 'Create new account' button across all regions"""
    pages_to_try = [page] + [p for p in context.pages if p != page and not p.is_closed()]

    for p in pages_to_try:
        try:
            if p.is_closed():
                continue

            # Check if 'Create new account' confirmation is on screen
            create_acc_btn = p.locator("[role='button'], button").filter(has_text=re.compile(r"^Create new account$", re.I)).first
            if await create_acc_btn.is_visible(timeout=300):
                log("ACTION", "Clicking 'Create new account' confirmation button...", instance_name=instance_name)
                await create_acc_btn.click(force=True, timeout=1000)
                await asyncio.sleep(0.8)
                return True

            # Strategy 1: Targeted Playwright locators for 'Use mobile number or email address'
            selectors = [
                "[role='button']:has-text('Use mobile number or email address')",
                "div[role='button']:has-text('Use mobile number or email address')",
                "button:has-text('Use mobile number or email address')",
                "span:has-text('Use mobile number or email address')",
                "[role='button']:has-text('Use email address or mobile number')",
                "div[role='button']:has-text('Use email address or mobile number')",
                "button:has-text('Use email address or mobile number')",
                "span:has-text('Use email address or mobile number')",
            ]
            for sel in selectors:
                try:
                    loc = p.locator(sel).first
                    if await loc.is_visible(timeout=250):
                        log("ACTION", f"Clicking 'Use mobile number or email address' via ({sel})...", instance_name=instance_name)
                        await loc.click(force=True, timeout=800)
                        await asyncio.sleep(0.8)
                        return True
                except Exception:
                    pass

            # Strategy 2: Direct JavaScript DOM execution (bypasses overlay div style='inset:0px')
            clicked_js = await p.evaluate('''() => {
                // 1. Search for buttons/roles containing mobile or email text
                const allElements = Array.from(document.querySelectorAll("[role='button'], button, [role='listitem'], div[tabindex='0'], span"));
                for (const el of allElements) {
                    const txt = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (txt.includes('use mobile number or email address') || 
                        txt.includes('use email address or mobile number') ||
                        (txt.includes('mobile number') && txt.includes('email address'))) {
                        const targetBtn = el.closest("[role='button']") || el.closest("button") || el;
                        targetBtn.scrollIntoView({ block: 'center', inline: 'center' });
                        targetBtn.click();
                        targetBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                        targetBtn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                        targetBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                        return true;
                    }
                }
                
                // 2. If 'Continue with Facebook' or 'Continue with Instagram' is present, click 3rd option (email/mobile)
                const listButtons = Array.from(document.querySelectorAll("[role='list'] [role='button'], [role='listitem'] [role='button'], [role='listitem'] div[tabindex='0']"));
                if (listButtons.length >= 3) {
                    const thirdBtn = listButtons[2];
                    thirdBtn.scrollIntoView({ block: 'center', inline: 'center' });
                    thirdBtn.click();
                    thirdBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    thirdBtn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    thirdBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    return true;
                }
                return false;
            }''')

            if clicked_js:
                log("ACTION", "Clicked 'Use mobile number or email address' via JS DOM evaluation!", instance_name=instance_name)
                await asyncio.sleep(0.8)
                return True

            # Strategy 3: Bounding box coordinate click
            span_el = p.locator("span:has-text('Use mobile number or email address'), span:has-text('Use email address or mobile number')").first
            if await span_el.count() > 0:
                box = await span_el.bounding_box()
                if box:
                    log("ACTION", f"Clicking 'Use mobile number or email address' via coordinates ({box['x']:.0f}, {box['y']:.0f})...", instance_name=instance_name)
                    await p.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                    await asyncio.sleep(0.8)
                    return True

            # Check saved account card
            if await try_click_saved_account_card(p, instance_name=instance_name):
                return True

        except Exception as e:
            log("ACTION-WARN", f"click_use_mobile_or_email error: {e}", instance_name=instance_name)
            continue

    return False

async def handle_dob_selection(page: Page, instance_name="") -> bool:
    """Universally select Month, Day, and Year on Meta DOB page"""
    target_day_int = random.randint(1, 28)
    target_day = str(target_day_int)
    target_day_2digit = f"{target_day_int:02d}"
    
    months_data = [
        ("January", "Jan", "1", "01"),
        ("February", "Feb", "2", "02"),
        ("March", "Mar", "3", "03"),
        ("April", "Apr", "4", "04"),
        ("May", "May", "5", "05"),
        ("June", "Jun", "6", "06"),
        ("July", "Jul", "7", "07"),
        ("August", "Aug", "8", "08"),
        ("September", "Sep", "9", "09"),
        ("October", "Oct", "10", "10"),
        ("November", "Nov", "11", "11"),
        ("December", "Dec", "12", "12")
    ]
    chosen_month = random.choice(months_data)
    target_month_full, target_month_short, target_month_num, target_month_2digit = chosen_month
    target_year = str(random.randint(1990, 2002))
    
    log("DOB", f"Setting DOB -> {target_month_full} {target_day}, {target_year}", instance_name=instance_name)

    # Strategy 1: HTML <select>
    try:
        selects = page.locator("select")
        select_count = await selects.count()
        if select_count >= 3:
            month_sel = page.locator("select[name*='month' i], select[aria-label*='month' i], select[id*='month' i]").first
            day_sel = page.locator("select[name*='day' i], select[aria-label*='day' i], select[id*='day' i]").first
            year_sel = page.locator("select[name*='year' i], select[aria-label*='year' i], select[id*='year' i]").first

            if await month_sel.count() > 0:
                for opt_val in [target_month_full, target_month_short, target_month_num, target_month_2digit]:
                    try:
                        await month_sel.select_option(label=opt_val)
                        break
                    except Exception:
                        try:
                            await month_sel.select_option(value=opt_val)
                            break
                        except Exception:
                            pass
            else:
                try:
                    await selects.nth(0).select_option(index=int(target_month_num))
                except Exception:
                    pass

            if await day_sel.count() > 0:
                for opt_val in [target_day, target_day_2digit]:
                    try:
                        await day_sel.select_option(label=opt_val)
                        break
                    except Exception:
                        try:
                            await day_sel.select_option(value=opt_val)
                            break
                        except Exception:
                            pass
            else:
                try:
                    await selects.nth(1).select_option(index=target_day_int)
                except Exception:
                    pass

            if await year_sel.count() > 0:
                try:
                    await year_sel.select_option(label=target_year)
                except Exception:
                    try:
                        await year_sel.select_option(value=target_year)
                    except Exception:
                        pass
            else:
                try:
                    await selects.nth(2).select_option(label=target_year)
                except Exception:
                    pass

            return True
    except Exception:
        pass

    # Strategy 2: Direct inputs
    try:
        month_input = page.locator("input[name*='month' i], input[placeholder*='MM' i], input[aria-label*='month' i]").first
        day_input = page.locator("input[name*='day' i], input[placeholder*='DD' i], input[aria-label*='day' i]").first
        year_input = page.locator("input[name*='year' i], input[placeholder*='YYYY' i], input[aria-label*='year' i]").first
        
        if await month_input.is_visible(timeout=200) and await day_input.is_visible(timeout=200) and await year_input.is_visible(timeout=200):
            await month_input.fill(target_month_num)
            await day_input.fill(target_day)
            await year_input.fill(target_year)
            return True
    except Exception:
        pass

    # Strategy 3: Comboboxes
    async def select_combobox_option(cb_locator, match_strings: list[str], type_fallback_val: str) -> bool:
        try:
            if not await cb_locator.is_visible(timeout=400):
                return False
            await cb_locator.click()
            await asyncio.sleep(0.15)
            for match_str in match_strings:
                pat = re.compile(rf"\b{re.escape(match_str)}\b", re.I)
                opt = page.locator("[role='option'], div[role='option'], li[role='option'], [data-value]").filter(has_text=pat).first
                if await opt.is_visible(timeout=400):
                    await opt.click()
                    await asyncio.sleep(0.15)
                    return True
            try:
                await cb_locator.fill("")
                await cb_locator.press_sequentially(type_fallback_val, delay=15)
                await cb_locator.press("Enter")
                return True
            except Exception:
                pass
        except Exception:
            pass
        return False

    month_cb = page.locator("[role='combobox'][aria-label*='month' i], [aria-label*='Month' i]").first
    day_cb = page.locator("[role='combobox'][aria-label*='day' i], [aria-label*='Day' i]").first
    year_cb = page.locator("[role='combobox'][aria-label*='year' i], [aria-label*='Year' i]").first

    await select_combobox_option(month_cb, [target_month_full, target_month_short, target_month_num, target_month_2digit], target_month_full)
    await select_combobox_option(day_cb, [target_day, target_day_2digit], target_day)
    await select_combobox_option(year_cb, [target_year], target_year)

    return True

async def click_next_or_continue(page: Page, timeout=1200) -> bool:
    """Click Next / Continue / Submit / Confirm / Sign up button or press Enter"""
    next_btn_combined = (
        "button[type='submit'], "
        "button:has-text('Next'), "
        "button:has-text('Continue'), "
        "button:has-text('Sign up'), "
        "button:has-text('Create account'), "
        "button:has-text('Confirm'), "
        "button:has-text('Save'), "
        "button:has-text('Agree and continue'), "
        "button:has-text('I agree'), "
        "div[role='button']:has-text('Next'), "
        "div[role='button']:has-text('Continue'), "
        "div[role='button']:has-text('Sign up'), "
        "div[role='button']:has-text('Create account'), "
        "div[role='button']:has-text('Confirm'), "
        "div[role='button']:has-text('Agree and continue')"
    )
    clicked = await robust_click(page, next_btn_combined, desc="Next/Continue Button", timeout=timeout)
    if not clicked:
        try:
            await page.keyboard.press("Enter")
            return True
        except Exception:
            pass
    return clicked

async def handle_recaptcha_2captcha(page: Page, context: BrowserContext, instance_name="") -> bool:
    """
    Check if 'Confirm that you're human' or reCAPTCHA page is active.
    Waits for 2Captcha button to reach 'ready' state:
      page.locator('.captcha-solver[data-state="ready"]').click()
    Then waits for 'solved' state and clicks 'Continue'.
    """
    try:
        pages_to_check = [page] + [p for p in context.pages if p != page and not p.is_closed()]
        for p in pages_to_check:
            try:
                if p.is_closed():
                    continue

                url = p.url.lower()
                is_captcha_page = False
                if any(k in url for k in ["checkpoint", "challenge", "human", "confirm", "auth.meta.com"]):
                    if await p.locator(":has-text(\"Confirm that you're human\"), :has-text(\"Confirm that you’re human\"), :has-text('I\\'m not a robot'), :has-text('I’m not a robot'), iframe[src*='recaptcha'], iframe[title*='reCAPTCHA'], .captcha-solver, [class*='captcha-solver'], :has-text('Solve with 2Captcha')").count() > 0:
                        is_captcha_page = True

                if not is_captcha_page:
                    continue

                solver_loc = None
                target_container = p
                solver_selectors = [
                    '.captcha-solver[data-state="ready"]',
                    '.captcha-solver',
                    'button:has-text("Solve with 2Captcha")',
                    'div:has-text("Solve with 2Captcha")',
                    '[class*="captcha-solver"]',
                    '[data-state="ready"]',
                    'div.captcha-solver',
                    'button.captcha-solver'
                ]

                # Allow up to 6s for 2Captcha solver button to attach/render
                for _ in range(12):
                    # Check main page
                    for sel in solver_selectors:
                        try:
                            loc = p.locator(sel).first
                            if await loc.is_visible(timeout=300):
                                solver_loc = loc
                                target_container = p
                                break
                        except Exception:
                            pass

                    # Check iframes if not found on main page
                    if not solver_loc:
                        for frame in p.frames:
                            try:
                                for sel in solver_selectors:
                                    loc = frame.locator(sel).first
                                    if await loc.is_visible(timeout=250):
                                        solver_loc = loc
                                        target_container = frame
                                        break
                                if solver_loc:
                                    break
                            except Exception:
                                continue

                    if solver_loc:
                        break
                    await asyncio.sleep(0.5)

                if solver_loc:
                    state = (await solver_loc.get_attribute("data-state") or "").lower()
                    btn_text = ""
                    try:
                        btn_text = (await solver_loc.inner_text()).strip()
                    except Exception:
                        pass

                    if "solved" not in btn_text.lower() and state != "solved":
                        log("2CAPTCHA", f"Waiting for 2Captcha solver 'ready' state (current: state='{state}', text='{btn_text}')...", instance_name=instance_name)

                        # Wait for ready state if not already ready
                        try:
                            ready_btn = target_container.locator('.captcha-solver[data-state="ready"]').first
                            await ready_btn.wait_for(state="visible", timeout=5000)
                            log("2CAPTCHA", "2Captcha solver is 'ready'! Clicking solver button...", instance_name=instance_name)
                            await ready_btn.click(force=True, timeout=2000)
                        except Exception:
                            log("2CAPTCHA", "Clicking 2Captcha solver button directly...", instance_name=instance_name)
                            try:
                                await solver_loc.click(force=True, timeout=2000)
                            except Exception:
                                try:
                                    await solver_loc.evaluate("el => el.click()")
                                except Exception:
                                    pass

                        # Wait for 2Captcha to finish solving
                        log("2CAPTCHA", "Waiting for 2Captcha solver to solve reCAPTCHA...", instance_name=instance_name)
                        try:
                            solved_loc = target_container.locator('.captcha-solver[data-state="solved"]').first
                            await solved_loc.wait_for(state="attached", timeout=60000)
                            log("2CAPTCHA", ">>> Captcha solved successfully (data-state='solved')! <<<", instance_name=instance_name)
                        except Exception:
                            for _ in range(45):
                                await asyncio.sleep(1)
                                try:
                                    curr_t = (await solver_loc.inner_text()).lower()
                                    curr_s = (await solver_loc.get_attribute("data-state") or "").lower()
                                    if "solved" in curr_t or curr_s == "solved":
                                        log("2CAPTCHA", ">>> Captcha solved successfully! <<<", instance_name=instance_name)
                                        break
                                except Exception:
                                    break

                                # Check if Continue button became enabled
                                cont_check = p.locator("button:has-text('Continue'), div[role='button']:has-text('Continue'), button[type='submit']").first
                                try:
                                    if await cont_check.is_visible(timeout=100):
                                        aria_dis = await cont_check.get_attribute("aria-disabled")
                                        if aria_dis != "true" and not await cont_check.is_disabled():
                                            log("2CAPTCHA", ">>> Continue button enabled! Captcha solved. <<<", instance_name=instance_name)
                                            break
                                except Exception:
                                    pass

                        await asyncio.sleep(1.5)

                    # Now click the Continue button on the captcha page
                    continue_combined = (
                        "button:has-text('Continue'), "
                        "div[role='button']:has-text('Continue'), "
                        "[role='button']:has-text('Continue'), "
                        "button[type='submit'], "
                        "button:has-text('Next'), "
                        "div[role='button']:has-text('Next')"
                    )
                    for _ in range(6):
                        cont_btn = p.locator(continue_combined).first
                        if await cont_btn.is_visible(timeout=1000):
                            aria_dis = await cont_btn.get_attribute("aria-disabled")
                            if aria_dis != "true" and not await cont_btn.is_disabled():
                                log("2CAPTCHA", "Clicking 'Continue' button on captcha page...", instance_name=instance_name)
                                await cont_btn.click(force=True, timeout=3000)
                                await asyncio.sleep(2)
                                return True
                        await asyncio.sleep(0.5)
            except Exception:
                continue
    except Exception as e:
        log("2CAPTCHA-DEBUG", f"2Captcha handler note: {e}", instance_name=instance_name)
    return False

async def try_auto_upload_image(target_page: Page, instance_name="") -> bool:
    """Auto-upload selfie verification image from ./images directory and click Continue"""
    try:
        if target_page.is_closed():
            return False

        # Only trigger upload if on an actual upload page
        upload_indicators = (
            "span:has-text('Upload Image'), "
            "div[aria-label='Upload Image'], "
            "[role='button']:has-text('Upload Image'), "
            "button:has-text('Upload Image'), "
            "span:has-text('Upload photo'), "
            "button:has-text('Upload photo'), "
            ":has-text('Upload a verification selfie'), "
            ":has-text('Take a verification selfie'), "
            "input.x1s85apg, "
            "div.xeq5yr9 input[type='file'], "
            "input[type='file'][accept*='image']"
        )
        has_upload = await target_page.locator(upload_indicators).count() > 0
        if not has_upload:
            return False

        continue_combined = (
            "div[aria-label='Continue'][role='button'], "
            "div[role='button']:has-text('Continue'), "
            "button:has-text('Continue'), "
            "div[aria-label='Continue'], "
            "button[type='submit'], "
            "button:has-text('Submit'), "
            "div[role='button']:has-text('Submit')"
        )
        continue_btn = target_page.locator(continue_combined).first

        images_dir = os.path.join(os.path.dirname(__file__), "images")
        if not os.path.exists(images_dir):
            log("IMG-WARN", "./images directory does not exist.", instance_name)
            return False

        valid_exts = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.heic', '.heif')
        img_files = [f for f in os.listdir(images_dir) if f.lower().endswith(valid_exts)]
        if not img_files:
            log("IMG-WARN", "No images found in ./images directory.", instance_name)
            return False

        chosen_img = random.choice(img_files)
        chosen_path = os.path.join(images_dir, chosen_img)
        uploaded = False

        # Strategy 1: File chooser trigger
        btn_trigger = target_page.locator(
            "span:has-text('Upload Image'), "
            "div[aria-label='Upload Image'], "
            "[role='button']:has-text('Upload Image'), "
            "button:has-text('Upload Image'), "
            "span:has-text('Upload photo'), "
            "button:has-text('Upload photo')"
        ).first

        if await btn_trigger.is_visible(timeout=600):
            try:
                log("IMG-UPLOAD", f"Opening file chooser for '{chosen_img}'...", instance_name)
                async with target_page.expect_file_chooser(timeout=2500) as fc_info:
                    await btn_trigger.click(force=True)
                fc = await fc_info.value
                await fc.set_files(chosen_path)
                uploaded = True
                log("IMG-UPLOAD", f"Attached '{chosen_img}' via file dialog!", instance_name)
            except Exception as e:
                log("IMG-DEBUG", f"File chooser fallback: {e}", instance_name)

        # Strategy 2: Direct set_input_files
        if not uploaded:
            try:
                file_input = target_page.locator("input.x1s85apg, div.xeq5yr9 input[type='file'], input[accept*='image'], input[type='file']").first
                if await file_input.count() > 0:
                    await file_input.set_input_files(chosen_path)
                    uploaded = True
                    log("IMG-UPLOAD", f"Directly attached '{chosen_img}' to input.", instance_name)
            except Exception as e:
                log("IMG-DEBUG", f"Direct input set error: {e}", instance_name)

        if uploaded:
            log("IMG-UPLOAD", "Waiting for image processing & Continue button...", instance_name)
            for _ in range(12):
                await asyncio.sleep(1)
                try:
                    if await continue_btn.is_visible(timeout=400):
                        aria_disabled = await continue_btn.get_attribute("aria-disabled")
                        if aria_disabled != "true" and not await continue_btn.is_disabled():
                            log("IMG-UPLOAD", "Continue active. Submitting verification selfie...", instance_name)
                            await continue_btn.click(timeout=2500)
                            await asyncio.sleep(1.5)
                            return True
                except Exception:
                    pass

            try:
                if await continue_btn.is_visible(timeout=500):
                    await continue_btn.click(timeout=1500)
                    await asyncio.sleep(1.5)
                    return True
            except Exception:
                pass

    except Exception as e:
        if "closed" not in str(e).lower():
            log("IMG-WARN", f"Auto image upload note: {e}", instance_name)
    return False

async def handle_name_inputs(page: Page, first_name: str, last_name: str, instance_name="") -> bool:
    """Universally fill First Name and Surname/Last Name on Meta 'What\'s your name?' screen"""
    try:
        if page.is_closed():
            return False

        # Strategy 1: Targeted selectors
        first_selectors = (
            "input[name='firstname'], input[name='first_name'], input[autocomplete='given-name'], "
            "input[placeholder*='first' i], input[aria-label*='first' i], input[name*='first' i]"
        )
        last_selectors = (
            "input[name='lastname'], input[name='last_name'], input[name='surname'], input[autocomplete='family-name'], "
            "input[placeholder*='surname' i], input[placeholder*='last' i], input[aria-label*='surname' i], input[aria-label*='last' i]"
        )
        
        filled_first = await robust_fill(page, first_selectors, first_name, desc="First Name", timeout=800)
        filled_last = await robust_fill(page, last_selectors, last_name, desc="Surname", timeout=800)

        # Strategy 2: If targeted didn't fill both, use positional inputs on screen
        if not (filled_first and filled_last):
            visible_inputs = page.locator("input:visible")
            inp_count = await visible_inputs.count()
            if inp_count >= 2:
                log("NAME", "Filling First Name and Surname via input position (nth 0 and 1)...", instance_name=instance_name)
                i0 = visible_inputs.nth(0)
                await human_type(i0, first_name)
                
                i1 = visible_inputs.nth(1)
                await human_type(i1, last_name)
                filled_first = True
                filled_last = True
            elif inp_count == 1:
                i0 = visible_inputs.nth(0)
                await human_type(i0, f"{first_name} {last_name}")
                filled_first = True
                filled_last = True

        return filled_first and filled_last
    except Exception as e:
        log("NAME-WARN", f"Name input handling note: {e}", instance_name=instance_name)
    return False

async def ensure_checkpoint_navigation(context: BrowserContext, target_page: Page, instance_name="") -> bool:
    """Universally ensure every worker window navigates to CHECKPOINT_REDIRECT_URL and retries if not reached"""
    try:
        pages_to_check = [target_page] + [p for p in context.pages if p != target_page and not p.is_closed()]
        for p in pages_to_check:
            url = p.url.lower()
            if "checkpoint" in url or await p.locator(":has-text(\"Confirm that you're human\"), span:has-text('Upload Image'), div[aria-label='Upload Image']").count() > 0:
                return True

        active_p = pages_to_check[0]
        log("NAV", f"Actively navigating window to Checkpoint URL: {CHECKPOINT_REDIRECT_URL}...", instance_name=instance_name)
        for attempt in range(1, 4):
            try:
                await active_p.goto(CHECKPOINT_REDIRECT_URL, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1.2)
                for p in context.pages:
                    if not p.is_closed() and ("checkpoint" in p.url.lower() or await p.locator(":has-text(\"Confirm that you're human\")").count() > 0):
                        log("NAV", f"Checkpoint URL successfully reached on attempt #{attempt}!", instance_name=instance_name)
                        return True
            except Exception as e:
                log("NAV-WARN", f"Checkpoint nav attempt #{attempt} note: {e}", instance_name=instance_name)
                await asyncio.sleep(1.0)
    except Exception as err:
        log("NAV-ERR", f"Checkpoint nav error: {err}", instance_name=instance_name)
    return False

async def handle_human_verification(page: Page, context: BrowserContext, email="", password="", instance_name=""):
    """
    Handle human verification & captcha pipeline:
    1. Instantly clicks 'Solve with 2Captcha' and waits for solve -> clicks Continue.
    2. Auto-uploads selfie image from ./images directory -> clicks Continue.
    3. Waits max 30s after image upload: if target URL/success reached, completes; else assumes success and starts next cycle.
    """
    log("HUMAN-VERIFY", "Handling human verification / 2Captcha / image upload pipeline...", instance_name)

    target_page = page
    for p in context.pages:
        try:
            if p.is_closed():
                continue
            url = p.url.lower()
            if ("auth.meta.com" in url and "human" in url) or "challenge" in url or "checkpoint" in url:
                target_page = p
                break
            if await p.locator(":has-text('Confirm that you'), :has-text('Upload a verification selfie'), div[aria-label='Upload Image'], .captcha-solver, [class*='captcha-solver']").count() > 0:
                target_page = p
                break
        except Exception:
            pass

    try:
        await target_page.bring_to_front()
    except Exception:
        pass

    img_uploaded = False
    post_upload_timer_start = None
    detected_user = None

    # Run verification loop
    max_verification_wait = 180  # total max guard for entire verification pipeline
    pipeline_start = time.time()

    while time.time() - pipeline_start < max_verification_wait:
        # Find active verification page
        active_page = target_page if (target_page and not target_page.is_closed()) else (await get_active_meta_page(context, default_page=page))

        # 1. First check if 2Captcha solver is present on any page
        await handle_recaptcha_2captcha(active_page, context, instance_name=instance_name)

        # 2. Check if selfie image upload is ready
        if not img_uploaded:
            img_uploaded = await try_auto_upload_image(active_page, instance_name=instance_name)
            if img_uploaded:
                post_upload_timer_start = time.time()
                log("IMG-UPLOAD", ">>> Image uploaded & Continue submitted! Max 30s wait timer started... <<<", instance_name)

        # 3. Check for saved account card or Continue button
        await try_click_saved_account_card(active_page, instance_name=instance_name)
        try:
            cont_btn = active_page.locator("div[role='button']:has-text('Continue as'), button:has-text('Continue as'), div[role='button']:has-text('Continue')").first
            if await cont_btn.is_visible(timeout=150):
                aria_dis = await cont_btn.get_attribute("aria-disabled")
                if aria_dis != "true" and not await cont_btn.is_disabled():
                    await cont_btn.click()
                    await asyncio.sleep(1.0)
        except Exception:
            pass

        # 4. Check for success state
        is_success, uname = await check_meta_success_state(context)
        if is_success:
            detected_user = uname
            log("SUCCESS", f">>> Registration & Verification SUCCESS detected! <<<", instance_name)
            break

        # 5. If image was uploaded, enforce 30 seconds maximum wait
        if post_upload_timer_start is not None:
            elapsed = time.time() - post_upload_timer_start
            if elapsed >= 30:
                log("SUCCESS", f">>> 30s wait elapsed after image upload. Assuming SUCCESS (.success hoiche dhore)! Starting new cycle... <<<", instance_name)
                detected_user = (await extract_meta_username(context)) or "Meta_User"
                break

        await asyncio.sleep(0.8)

    final_username = detected_user or (await check_meta_success_state(context))[1] or "Meta_User"

    if email and password:
        log("AUTO-SAVE", f"Auto-saving successful account to CSV: Email='{email}' | Username='{final_username}'", instance_name)
        save_credentials_csv(email, password, username=final_username, instance_name=instance_name)

    log("HUMAN-VERIFY", "Verification pipeline finished. Continuing next cycle...", instance_name)
    await asyncio.sleep(1.5)
    return True

# ==============================================================================
# BROWSER PROFILE MANAGEMENT
# ==============================================================================

def cleanup_profile_locks(profile_dir: str):
    """Clean up any stale Chrome/Edge/Firefox lock files left behind by previous runs"""
    if not os.path.exists(profile_dir):
        return
    lock_names = {"LOCK", "SingletonLock", "SingletonCookie", "SingletonSocket", "lock", ".parentlock", "parent.lock"}
    for root_dir, _, files in os.walk(profile_dir):
        for f in files:
            if f in lock_names or f.endswith(".lock") or "Singleton" in f or f == ".parentlock":
                try:
                    os.remove(os.path.join(root_dir, f))
                except Exception:
                    pass

def clean_profile_except_extensions(profile_dir: str):
    """
    Clean up all previous browsing data, sessions, cookies, history, cache, logins,
    and temporary files from the worker profile, but strictly PRESERVE all extensions,
    extension settings, extension state/rules, and preferences.
    Works seamlessly for both Chrome/Chromium and Firefox profiles.
    """
    if not os.path.exists(profile_dir):
        return

    # 1. Chromium Profile Cleaning
    root_items_to_remove = [
        'BrowserMetrics', 'Crashpad', 'CrashpadMetrics', 'first_party_sets.db',
        'first_party_sets.db-journal', 'GPUPersistentCache', 'GrShaderCache',
        'Safe Browsing', 'segmentation_platform', 'ShaderCache', 'Variations',
        'LOCK', 'SingletonLock', 'SingletonCookie', 'SingletonSocket',
        'lock', '.parentlock', 'parent.lock'
    ]

    try:
        for item in os.listdir(profile_dir):
            if item == 'Default' or 'exten' in item.lower() or item == 'Local State':
                continue
            p = os.path.join(profile_dir, item)
            if any(item.startswith(x) for x in root_items_to_remove) or item.endswith('.pma') or item.endswith('.lock') or 'Singleton' in item:
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
                except Exception:
                    pass
    except Exception:
        pass

    default_dir = os.path.join(profile_dir, 'Default')
    if os.path.exists(default_dir):
        preserved_names = {
            'extension rules', 'extension scripts', 'extension state',
            'extensions', 'local extension settings', 'sync extension settings',
            'managed extension settings', 'preferences', 'secure preferences'
        }
        try:
            for item in os.listdir(default_dir):
                item_lower = item.lower()
                if item_lower in preserved_names or 'extension' in item_lower:
                    continue
                p = os.path.join(default_dir, item)
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
                except Exception:
                    pass
        except Exception:
            pass

    # 2. Firefox Profile Cleaning (Storage, Cookies, Caches, Sessions, History)
    ff_items_to_clean = [
        'cookies.sqlite', 'cookies.sqlite-wal', 'cookies.sqlite-shm',
        'cache2', 'jumpListCache', 'sessionstore.jsonlz4', 'sessionstore-backups',
        'places.sqlite', 'places.sqlite-wal', 'places.sqlite-shm',
        'favicons.sqlite', 'favicons.sqlite-wal', 'favicons.sqlite-shm',
        'formhistory.sqlite', 'formhistory.sqlite-wal', 'formhistory.sqlite-shm',
        'webappsstore.sqlite', 'webappsstore.sqlite-wal', 'webappsstore.sqlite-shm',
        'storage', 'datareporting', 'saved-telemetry-pings', 'shader-cache'
    ]
    for ff_item in ff_items_to_clean:
        p = os.path.join(profile_dir, ff_item)
        if os.path.exists(p):
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)
            except Exception:
                pass

    cleanup_profile_locks(profile_dir)

def get_worker_user_data_dir(instance_name: str) -> str:
    """Ensure dedicated user data directory for each parallel browser worker"""
    base_profile = os.path.join(os.path.dirname(__file__), "chrome_profile")
    profiles_root = os.path.join(os.path.dirname(__file__), "chrome_profiles")
    os.makedirs(profiles_root, exist_ok=True)
    worker_dir = os.path.join(profiles_root, f"profile_{instance_name}")

    if not os.path.exists(worker_dir):
        if os.path.exists(base_profile):
            try:
                shutil.copytree(
                    base_profile,
                    worker_dir,
                    ignore=shutil.ignore_patterns("LOCK", "LOG*", "Singleton*", "*Cache*", "GPUCache", "Crashpad*", "*.pma")
                )
            except Exception as e:
                log("WORKER-WARN", f"Base profile clone note: {e}", instance_name=instance_name)
                os.makedirs(worker_dir, exist_ok=True)
        else:
            os.makedirs(worker_dir, exist_ok=True)
    
    clean_profile_except_extensions(worker_dir)
    return worker_dir

# ==============================================================================
# MAIN DYNAMIC REGISTRATION WORKER (STATE MACHINE)
# ==============================================================================

async def run_meta_registration_worker(playwright: Playwright, instance_name: str):
    """
    Run Meta AI registration workflow using an Intelligent State Machine & Real-Time Page Detector.
    Never works blindly. Automatically identifies and executes actions based on the current exact screen.
    Each cycle completely opens a clean, stealth browser and cleanly closes it at the end of the cycle.
    """
    user_data_dir = get_worker_user_data_dir(instance_name)

    try:
        worker_num = int(re.search(r'\d+', instance_name).group(0)) if re.search(r'\d+', instance_name) else 1
    except Exception:
        worker_num = 1

    cycle_count = 0

    while True:
        cycle_count += 1
        log("CYCLE-START", f"=================== CYCLE #{cycle_count} ({instance_name}) ===================", instance_name=instance_name)

        cfg = load_config()
        configured_channel = cfg.get("browser_channel", "firefox")
        vp_w = int(cfg.get("viewport_width", VIEWPORT_WIDTH))
        vp_h = int(cfg.get("viewport_height", VIEWPORT_HEIGHT))
        is_headless = cfg.get("headless", HEADLESS)

        col = (worker_num - 1) % 4
        row = (worker_num - 1) // 4
        pos_x = col * (vp_w + 15)
        pos_y = row * (vp_h // 2 + 40)

        # Thoroughly clean previous cycle profile data while preserving all extensions
        clean_profile_except_extensions(user_data_dir)

        context: BrowserContext = None
        mode_desc = "Headless (Background)" if is_headless else "Headful (Visible Window)"

        # 1. Launch with Firefox if requested or default
        if str(configured_channel).lower() in ["firefox", "ff"]:
            try:
                log("WORKER", f"Launching Firefox browser [{mode_desc}] ({user_data_dir})...", instance_name=instance_name)
                ff_kwargs = {
                    "user_data_dir": user_data_dir,
                    "headless": is_headless,
                    "viewport": {"width": vp_w, "height": vp_h},
                    "firefox_user_prefs": {
                        "dom.webdriver.enabled": False,
                        "useAutomationExtension": False,
                        "privacy.resistFingerprinting": False,
                        "network.cookie.cookieBehavior": 0
                    }
                }
                context = await playwright.firefox.launch_persistent_context(**ff_kwargs)
                log("WORKER", f"Firefox browser instance {instance_name} launched successfully!", instance_name=instance_name)
            except Exception as e:
                log("WORKER-WARN", f"Firefox launch failed ({e}). Trying Chromium fallback...", instance_name=instance_name)
                configured_channel = "chrome"

        # 2. Chromium / Chrome fallback or if Chrome explicitly configured
        if not context:
            browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu-sandbox",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-ipc-flooding-protection",
                "--disable-hang-monitor",
                "--disable-client-side-phishing-detection",
                "--disable-component-update",
                "--disable-default-apps",
                "--no-first-run",
                "--no-default-browser-check",
                "--password-store=basic",
                "--use-mock-keychain",
                f"--window-size={vp_w},{vp_h}",
                f"--window-position={pos_x},{pos_y}"
            ]
            channels_to_try = [configured_channel] if configured_channel not in ["firefox", "ff"] else ["chrome"]
            for ch in ["chrome", "msedge", None]:
                if ch not in channels_to_try:
                    channels_to_try.append(ch)

            for ch in channels_to_try:
                try:
                    ch_desc = f"channel='{ch}'" if ch else "Chromium engine"
                    log("WORKER", f"Launching stealth browser [{mode_desc}] with {ch_desc} ({user_data_dir})...", instance_name=instance_name)
                    
                    launch_kwargs = {
                        "user_data_dir": user_data_dir,
                        "headless": is_headless,
                        "viewport": {"width": vp_w, "height": vp_h},
                        "args": browser_args,
                        "ignore_default_args": ["--disable-extensions", "--enable-automation"]
                    }
                    if ch:
                        launch_kwargs["channel"] = ch

                    context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
                    log("WORKER", f"Browser instance {instance_name} launched successfully with {ch_desc}!", instance_name=instance_name)
                    break
                except Exception as e:
                    log("WORKER-WARN", f"Browser channel '{ch}' failed ({e}). Trying fallback...", instance_name=instance_name)

        if not context:
            log("WORKER-ERR", f"Could not launch browser for {instance_name}! Retrying in 5s...", instance_name=instance_name)
            await asyncio.sleep(5)
            continue

        # Generate and apply a completely unique, realistic browser fingerprint for this cycle
        fp = generate_unique_browser_fingerprint()
        log("FINGERPRINT", f"Applied unique fingerprint: {fp.get_summary_str()}", instance_name=instance_name)

        try:
            await context.set_extra_http_headers(fp.get_http_headers())
            await context.add_init_script(fp.generate_injection_script())
        except Exception as fp_err:
            log("FP-WARN", f"Fingerprint application note: {fp_err}", instance_name=instance_name)

        page: Page = context.pages[0] if context.pages else await context.new_page()

        try:
            # 1. Generate credentials
            email, strong_password, mail_token = generate_temp_email(instance_name=instance_name)
            first_name, last_name = get_random_name()

            log("NAV", f"Navigating to {TARGET_URL}...", instance_name=instance_name)
            try:
                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=25000)
            except Exception as e:
                log("NAV-WARN", f"Initial navigation note: {e}", instance_name=instance_name)

            await asyncio.sleep(1.0)

            # State Machine Variables
            otp_code = None
            otp_submitted = False
            checkpoint_reached = False
            email_submitted = False
            last_logged_state = None
            consecutive_same_state = 0
            cycle_start_time = time.time()
            max_cycle_timeout = 240  # 4 minutes per cycle max
            cycle_success = False

            # ==========================================================
            # DYNAMIC STATE MACHINE LOOP
            # ==========================================================
            while time.time() - cycle_start_time < max_cycle_timeout:
                active_page = await get_active_meta_page(context, default_page=page)
                current_state, state_page = await detect_current_page_state(context, default_page=active_page)

                if current_state != last_logged_state:
                    log("PAGE-DETECT", f"Current Page State -> [{current_state}] (URL: {state_page.url})", instance_name=instance_name)
                    last_logged_state = current_state
                    consecutive_same_state = 0
                else:
                    consecutive_same_state += 1

                # Track if checkpoint page reached
                if current_state in ["STATE_HUMAN_CONFIRM", "STATE_SELFIE_UPLOAD", "STATE_CHECKPOINT"] or "checkpoint" in state_page.url.lower():
                    checkpoint_reached = True

                # Post-OTP Checkpoint enforcement for every worker window
                if otp_submitted and not checkpoint_reached and current_state not in ["STATE_SUCCESS", "STATE_RECAPTCHA", "STATE_SELFIE_UPLOAD"]:
                    await ensure_checkpoint_navigation(context, state_page, instance_name=instance_name)

                # ---------------- STATE: SUCCESS ----------------
                if current_state == "STATE_SUCCESS":
                    is_success, uname = await check_meta_success_state(context)
                    final_username = uname or "Meta_User"
                    log("SUCCESS", f">>> Registration SUCCESS Detected! Account: {email} | Username: {final_username} <<<", instance_name=instance_name)
                    save_credentials_csv(email, strong_password, username=final_username, instance_name=instance_name)
                    cycle_success = True
                    break

                # ---------------- STATE: ERROR MODAL ----------------
                elif current_state == "STATE_ERROR_MODAL":
                    await dismiss_error_modals(state_page, instance_name=instance_name)
                    await asyncio.sleep(0.5)

                # ---------------- STATE: COOKIE BANNER ----------------
                elif current_state == "STATE_COOKIE_BANNER":
                    await handle_cookie_banners(state_page, instance_name=instance_name)
                    await asyncio.sleep(0.4)

                # ---------------- STATE: SAVE LOGIN PROMPT ----------------
                elif current_state == "STATE_SAVE_LOGIN":
                    await handle_save_login_info_prompt(state_page, context, instance_name=instance_name)
                    if otp_submitted and not checkpoint_reached:
                        await ensure_checkpoint_navigation(context, state_page, instance_name=instance_name)
                    await asyncio.sleep(0.5)

                # ---------------- STATE: RECAPTCHA / 2CAPTCHA ----------------
                elif current_state == "STATE_RECAPTCHA":
                    log("ACTION", "Handling reCAPTCHA / 2Captcha on current page...", instance_name=instance_name)
                    await handle_recaptcha_2captcha(state_page, context, instance_name=instance_name)
                    await asyncio.sleep(1.0)

                # ---------------- STATE: SELFIE VERIFICATION UPLOAD ----------------
                elif current_state == "STATE_SELFIE_UPLOAD":
                    log("ACTION", "Handling selfie upload on current page...", instance_name=instance_name)
                    img_uploaded = await try_auto_upload_image(state_page, instance_name=instance_name)
                    if img_uploaded:
                        log("IMG-UPLOAD", "Image uploaded & continue clicked. Waiting max 30s for target / success...", instance_name=instance_name)
                        start_wait = time.time()
                        target_found = False
                        while time.time() - start_wait < 30:
                            is_success, uname = await check_meta_success_state(context)
                            if is_success:
                                target_found = True
                                final_username = uname or "Meta_User"
                                log("SUCCESS", f">>> Target/Success detected within 30s! Account: {email} | Username: {final_username} <<<", instance_name=instance_name)
                                save_credentials_csv(email, strong_password, username=final_username, instance_name=instance_name)
                                cycle_success = True
                                break

                            # Check for 2Captcha, saved card, or continue buttons
                            for p in context.pages:
                                if not p.is_closed():
                                    await handle_recaptcha_2captcha(p, context, instance_name=instance_name)
                                    await try_click_saved_account_card(p, instance_name=instance_name)
                                    try:
                                        c_btn = p.locator("button:has-text('Continue'), div[role='button']:has-text('Continue'), button:has-text('Next')").first
                                        if await c_btn.is_visible(timeout=150):
                                            await c_btn.click()
                                    except Exception:
                                        pass
                            await asyncio.sleep(1.0)

                        if not target_found:
                            log("SUCCESS", f">>> 30s wait elapsed after image upload. Assuming SUCCESS (.success hoiche dhore)! Starting new cycle... <<<", instance_name=instance_name)
                            final_username = (await extract_meta_username(context)) or "Meta_User"
                            save_credentials_csv(email, strong_password, username=final_username, instance_name=instance_name)
                            cycle_success = True
                        break
                    else:
                        await asyncio.sleep(1.5)

                # ---------------- STATE: USE EMAIL / CHOICE SCREEN (Screenshot 2) ----------------
                elif current_state == "STATE_USE_EMAIL_CHOICE":
                    await click_use_mobile_or_email(state_page, context, instance_name=instance_name)
                    await asyncio.sleep(0.8)

                # ---------------- STATE: CREATE NEW ACCOUNT CONFIRMATION (Screenshot 4) ----------------
                elif current_state == "STATE_CREATE_ACCOUNT_CONFIRM":
                    log("ACTION", "Detected 'Create a new Meta account?' -> Clicking 'Create new account' button...", instance_name=instance_name)
                    create_btn_combined = (
                        "button:has-text('Create new account'), "
                        "[role='button']:has-text('Create new account'), "
                        "div[role='button']:has-text('Create new account')"
                    )
                    await robust_click(state_page, create_btn_combined, desc="Create new account button", timeout=1500)
                    await asyncio.sleep(1.0)

                # ---------------- STATE: EMAIL INPUT SCREEN (Screenshot 3) ----------------
                elif current_state == "STATE_EMAIL_INPUT":
                    log("ACTION", f"Entering email into field: {email}", instance_name=instance_name)
                    email_selectors = (
                        "input[placeholder*='mobile number or email address' i], "
                        "input[aria-label*='mobile number or email address' i], "
                        "input[name='contact'], "
                        "input[type='email'], "
                        "input[placeholder*='email' i], "
                        "input[aria-label*='email' i], "
                        "input[autocomplete*='email' i], "
                        "input[autocomplete*='username' i], "
                        "input[name*='email' i], "
                        "input[id*='email' i]"
                    )
                    filled = await robust_fill(state_page, email_selectors, email, desc="Email input", timeout=1500)
                    if filled:
                        await asyncio.sleep(0.3)
                        log("ACTION", "Submitting email (Clicking Continue / Enter)...", instance_name=instance_name)
                        await click_next_or_continue(state_page, timeout=2000)
                        email_submitted = True
                        await asyncio.sleep(1.2)

                # ---------------- STATE: NAME INPUT SCREEN (Screenshot 5: First name & Surname) ----------------
                elif current_state == "STATE_NAME_INPUT":
                    log("ACTION", f"Entering Name -> First: {first_name}, Surname/Last: {last_name}", instance_name=instance_name)
                    await handle_name_inputs(state_page, first_name, last_name, instance_name=instance_name)

                    # If DOB is also on the same page, fill it
                    if (await state_page.locator("select").count() >= 2 or
                        await state_page.locator("[role='combobox'][aria-label*='month' i]").count() > 0 or
                        await state_page.locator("input[name*='birthday' i]").count() > 0):
                        await handle_dob_selection(state_page, instance_name=instance_name)

                    await asyncio.sleep(0.4)
                    log("ACTION", "Submitting Name (Clicking Next / Enter)...", instance_name=instance_name)
                    await click_next_or_continue(state_page, timeout=1800)
                    await asyncio.sleep(1.2)

                # ---------------- STATE: DOB / BIRTHDAY SCREEN ----------------
                elif current_state == "STATE_DOB_INPUT":
                    await handle_dob_selection(state_page, instance_name=instance_name)
                    await asyncio.sleep(0.3)
                    await click_next_or_continue(state_page, timeout=1500)
                    await asyncio.sleep(1.0)

                # ---------------- STATE: PASSWORD INPUT SCREEN ----------------
                elif current_state == "STATE_PASSWORD_INPUT":
                    # Check if Name is also on this screen and unfulfilled
                    name_input = state_page.locator("input[autocomplete='given-name'], input[name*='firstname' i]").first
                    if await name_input.is_visible(timeout=150):
                        val = await name_input.input_value()
                        if not val:
                            await robust_fill(state_page, "input[autocomplete='given-name'], input[name*='firstname' i]", first_name, desc="First Name")
                            await robust_fill(state_page, "input[autocomplete='family-name'], input[name*='lastname' i]", last_name, desc="Last Name")

                    # Check if DOB is on this screen
                    if (await state_page.locator("select").count() >= 2 or
                        await state_page.locator("[role='combobox'][aria-label*='month' i]").count() > 0):
                        await handle_dob_selection(state_page, instance_name=instance_name)

                    # Fill Password
                    pwd_inputs = state_page.locator("input[type='password'], input[name*='pass' i], input[autocomplete*='password' i], input[placeholder*='password' i]")
                    pwd_count = await pwd_inputs.count()
                    if pwd_count > 0:
                        for p_idx in range(pwd_count):
                            p_loc = pwd_inputs.nth(p_idx)
                            if await p_loc.is_visible(timeout=200):
                                val = await p_loc.input_value()
                                if not val:
                                    log("ACTION", f"Entering strong password into field #{p_idx+1}...", instance_name=instance_name)
                                    await p_loc.fill(strong_password)
                                    try:
                                        await p_loc.evaluate("el => { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }")
                                    except Exception:
                                        pass

                    # Check any visible agreement checkboxes
                    try:
                        checkboxes = state_page.locator("input[type='checkbox']:not(:checked), [role='checkbox'][aria-checked='false']")
                        cb_count = await checkboxes.count()
                        if cb_count > 0:
                            for c_idx in range(cb_count):
                                cb = checkboxes.nth(c_idx)
                                if await cb.is_visible(timeout=150):
                                    await cb.click(force=True)
                    except Exception:
                        pass

                    await asyncio.sleep(0.3)
                    await click_next_or_continue(state_page, timeout=1500)
                    await asyncio.sleep(1.2)

                # ---------------- STATE: TERMS AGREEMENT SCREEN ----------------
                elif current_state == "STATE_TERMS_AGREE":
                    log("ACTION", "Agreeing to terms & conditions...", instance_name=instance_name)
                    try:
                        checkboxes = state_page.locator("input[type='checkbox']:not(:checked), [role='checkbox'][aria-checked='false']")
                        cb_count = await checkboxes.count()
                        if cb_count > 0:
                            for c_idx in range(cb_count):
                                cb = checkboxes.nth(c_idx)
                                if await cb.is_visible(timeout=150):
                                    await cb.click(force=True)
                    except Exception:
                        pass
                    await click_next_or_continue(state_page, timeout=1500)
                    await asyncio.sleep(1.0)

                # ---------------- STATE: OTP INPUT SCREEN ----------------
                elif current_state == "STATE_OTP_INPUT":
                    if not otp_code:
                        log("OTP", f">>> Active OTP Screen Confirmed! Fetching OTP for {email}... <<<", instance_name=instance_name)
                        otp_code = fetch_otp_from_temp_mail(email, strong_password, token=mail_token, max_wait_sec=60, instance_name=instance_name)

                    if not otp_code:
                        log("OTP-ERR", f"Failed to retrieve OTP code within timeout for {email}. Restarting cycle...", instance_name=instance_name)
                        save_credentials_csv(email, strong_password, username="OTP_FETCH_FAILED", instance_name=instance_name)
                        break

                    log("ACTION", f"Entering OTP code: {otp_code} on OTP screen...", instance_name=instance_name)
                    # Check for 6 individual digit boxes
                    digit_inputs = state_page.locator("input[data-index], input[aria-label*='digit' i], input[autocomplete*='one-time-code']")
                    digit_count = await digit_inputs.count()
                    if digit_count == 6:
                        log("OTP-ENTER", "Entering 6-digit OTP across individual boxes...", instance_name=instance_name)
                        for idx, ch in enumerate(otp_code):
                            try:
                                box = digit_inputs.nth(idx)
                                await box.click()
                                await box.fill(ch)
                            except Exception:
                                pass
                    else:
                        otp_input_combined = (
                            "input[name*='code' i], "
                            "input[autocomplete*='one-time-code'], "
                            "input[inputmode='numeric'], "
                            "input[placeholder*='code' i], "
                            "input[aria-label*='code' i], "
                            "input[type='tel'], "
                            "input[type='text'], "
                            "input"
                        )
                        await robust_fill(state_page, otp_input_combined, otp_code, desc="OTP Input", timeout=10000)

                    await asyncio.sleep(0.8)
                    log("OTP-SUBMIT", "Submitting OTP...", instance_name=instance_name)
                    submit_otp_combined = (
                        "button:has-text('Continue'), "
                        "div[role='button']:has-text('Continue'), "
                        "button:has-text('Confirm'), "
                        "div[role='button']:has-text('Confirm'), "
                        "button:has-text('Next'), "
                        "div[role='button']:has-text('Next'), "
                        "button:has-text('Submit'), "
                        "div[role='button']:has-text('Submit'), "
                        "button[type='submit']"
                    )
                    clicked_submit_otp = await robust_click(state_page, submit_otp_combined, desc="OTP Submit Button", timeout=3000)
                    if not clicked_submit_otp:
                        try:
                            await state_page.keyboard.press("Enter")
                        except Exception:
                            pass

                    # Continuous post-OTP monitoring for 'Not now', cookie banner, 2Captcha, or next screens
                    log("OTP-POST-WAIT", "Waiting post-OTP submission and checking for 'Not now' / Cookie Banner / Captcha...", instance_name=instance_name)
                    for _ in range(12):
                        active_p = await get_active_meta_page(context, default_page=state_page)
                        await handle_cookie_banners(active_p, instance_name=instance_name)
                        clicked_not_now = await handle_save_login_info_prompt(active_p, context, instance_name=instance_name)
                        await handle_recaptcha_2captcha(active_p, context, instance_name=instance_name)
                        await dismiss_error_modals(active_p, instance_name=instance_name)
                        if clicked_not_now:
                            await asyncio.sleep(1.5)
                        await asyncio.sleep(1)

                    # Check if selfie / human verification page is already visible or navigate to checkpoint
                    checkpoint_page = await get_active_meta_page(context, default_page=state_page)

                    for attempt in range(1, 4):
                        log("CHECKPOINT-NAV", f"Opening checkpoint URL (attempt {attempt}/3): {CHECKPOINT_REDIRECT_URL}", instance_name=instance_name)
                        try:
                            await checkpoint_page.goto(CHECKPOINT_REDIRECT_URL, wait_until="domcontentloaded", timeout=20000)
                        except Exception as e:
                            log("CHECKPOINT-WARN", f"Checkpoint navigation note: {e}", instance_name=instance_name)

                        await asyncio.sleep(2)

                        # Check for 2Captcha solver on checkpoint page
                        await handle_recaptcha_2captcha(checkpoint_page, context, instance_name=instance_name)

                        # Check for saved account card and click it
                        for p in context.pages:
                            clicked_card = await try_click_saved_account_card(p, instance_name=instance_name)
                            if clicked_card:
                                await asyncio.sleep(1.5)
                                break

                        # Check if human verification / selfie page is active
                        is_hv_active = False
                        for p in context.pages:
                            try:
                                if (await p.locator(":has-text('Upload a verification selfie'), :has-text('Take a verification selfie'), span:has-text('Upload Image'), div[aria-label='Upload Image'], input.x1s85apg, div.xeq5yr9 input[type='file']").count() > 0 or
                                    await p.locator(":has-text('Confirm that you'), :has-text('Help us confirm')").count() > 0):
                                    is_hv_active = True
                                    checkpoint_page = p
                                    break
                            except Exception:
                                pass

                        if is_hv_active:
                            log("CHECKPOINT-NAV", f"Human verification / selfie page active on attempt #{attempt}!", instance_name=instance_name)
                            break
                        else:
                            if attempt < 3:
                                await asyncio.sleep(2.5)

                    # Handle Human Verification (2Captcha & Selfie Auto-Upload)
                    log("HUMAN-VERIFY", "Handling human verification / 2captcha / selfie...", instance_name=instance_name)
                    hv_triggered = await handle_human_verification(checkpoint_page, context, email=email, password=strong_password, instance_name=instance_name)
                    await asyncio.sleep(2)

                    if not hv_triggered:
                        detected_uname = await extract_meta_username(context) or ""
                        save_credentials_csv(email, strong_password, username=detected_uname, instance_name=instance_name)

                    cycle_success = True
                    break

                # ---------------- STATE: HUMAN CONFIRM CHECKPOINT SCREEN ----------------
                elif current_state == "STATE_HUMAN_CONFIRM":
                    log("ACTION", "Detected 'Confirm that you're human to use your account' -> Checking 2Captcha / Clicking 'Continue'...", instance_name=instance_name)
                    # Check and solve 2Captcha first if present
                    await handle_recaptcha_2captcha(state_page, context, instance_name=instance_name)
                    cont_btn = (
                        "button:has-text('Continue'), "
                        "div[role='button']:has-text('Continue'), "
                        "[role='button']:has-text('Continue'), "
                        "button[type='submit']"
                    )
                    await robust_click(state_page, cont_btn, desc="Checkpoint Continue Button", timeout=2500)
                    await asyncio.sleep(1.5)

                # ---------------- STATE: CHECKPOINT URL SCREEN ----------------
                elif current_state == "STATE_CHECKPOINT":
                    # Check for 2Captcha solver, selfie, or saved account cards
                    await handle_recaptcha_2captcha(state_page, context, instance_name=instance_name)
                    await try_auto_upload_image(state_page, instance_name=instance_name)
                    await try_click_saved_account_card(state_page, instance_name=instance_name)
                    # Also try clicking Continue if present
                    await robust_click(state_page, "button:has-text('Continue'), div[role='button']:has-text('Continue')", desc="Checkpoint Continue", timeout=1000)
                    await asyncio.sleep(1.0)

                # ---------------- STATE: UNKNOWN / WAITING ----------------
                elif current_state == "STATE_UNKNOWN":
                    # Dismiss any unexpected modals / check 2captcha / try saved card
                    await dismiss_error_modals(state_page, instance_name=instance_name)
                    await handle_recaptcha_2captcha(state_page, context, instance_name=instance_name)
                    await try_auto_upload_image(state_page, instance_name=instance_name)
                    await asyncio.sleep(1.0)

                # ---------------- STUCK WATCHDOG HEURISTICS ----------------
                if consecutive_same_state >= 6:
                    log("WATCHDOG", f"Still in state [{current_state}] (repeat #{consecutive_same_state}). Attempting recovery push...", instance_name=instance_name)
                    try:
                        await click_next_or_continue(state_page, timeout=800)
                    except Exception:
                        pass

                if consecutive_same_state >= 15:
                    log("WATCHDOG-WARN", f"Unresponsive state [{current_state}]. Aborting cycle to prevent lockup...", instance_name=instance_name)
                    break

                await asyncio.sleep(0.5)

            if cycle_success:
                log("CYCLE-COMPLETE", f"Cycle #{cycle_count} finished successfully with confirmed account creation!", instance_name=instance_name)
            else:
                log("CYCLE-FINISH", f"Cycle #{cycle_count} ended. Closing browser and preparing next cycle...", instance_name=instance_name)

        except Exception as e:
            log("CYCLE-ERR", f"Error during cycle #{cycle_count}: {e}", instance_name=instance_name)

        finally:
            log("CYCLE-CLOSE", f"Closing browser completely for cycle #{cycle_count}...", instance_name=instance_name)
            try:
                if context:
                    for p in list(context.pages):
                        try:
                            await p.close()
                        except Exception:
                            pass
                    await context.close()
            except Exception as close_err:
                log("CYCLE-WARN", f"Browser context close note: {close_err}", instance_name=instance_name)
            finally:
                context = None
                await asyncio.sleep(0.5)
                clean_profile_except_extensions(user_data_dir)

            log("CYCLE-RESET", f"Browser closed and profile data wiped (extensions preserved). Reopening fresh stealth browser in 2.5s for Cycle #{cycle_count + 1}...", instance_name=instance_name)
            await asyncio.sleep(2.5)

async def run_worker_with_stagger(playwright: Playwright, instance_name: str, delay: float = 0):
    if delay > 0:
        log("WORKER-WAIT", f"Worker {instance_name} waiting {delay:.1f}s to stagger browser launches...", instance_name=instance_name)
        await asyncio.sleep(delay)
    await run_meta_registration_worker(playwright, instance_name)

async def main_async():
    cfg = load_config()
    workers_count = cfg.get("num_workers", NUM_WORKERS)
    is_headless = cfg.get("headless", HEADLESS)
    channel = cfg.get("browser_channel", "firefox")
    mode_str = "Headless (Background ON)" if is_headless else "Headful (Window Visible OFF)"
    log("INIT", f"Starting Playwright Meta AI Automation with {workers_count} parallel browser workers | Channel='{channel}' | Mode: {mode_str}")
    async with async_playwright() as playwright:
        tasks = []
        for i in range(1, workers_count + 1):
            inst_name = f"Inst-{i}"
            tasks.append(run_worker_with_stagger(playwright, inst_name, delay=(i - 1) * 2.5))
        await asyncio.gather(*tasks)

def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log("MAIN", "Automation stopped by user.")

if __name__ == "__main__":
    main()