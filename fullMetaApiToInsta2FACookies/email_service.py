import csv
import json
import os
import random
import re
import string
import sys
import time
import requests
import urllib3

import mail_td
from mail_td import (
    generate_mail_td_account,
    fetch_otp_mail_td,
    extract_otp_from_mail_td_text,
    MailTDClient
)

import mail_cx
from mail_cx import (
    generate_mail_cx_account,
    fetch_otp_mail_cx,
    extract_otp_from_mail_cx_text,
    MailCxAPI
)

try:
    import NINJA
    from NINJA import (
        generate_ninja_account,
        fetch_otp_ninja,
        TempMailNinjaAPI,
        _NINJA_CLIENTS
    )
    HAS_NINJA = True
except ImportError:
    try:
        import ninja as NINJA
        from ninja import (
            generate_ninja_account,
            fetch_otp_ninja,
            TempMailNinjaAPI,
            _NINJA_CLIENTS
        )
        HAS_NINJA = True
    except ImportError:
        HAS_NINJA = False
        _NINJA_CLIENTS = {}

urllib3.disable_warnings()

# ----- Configuration Defaults & Paths -----
BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
NAMES_FILE = os.path.join(BASE_DIR, "names.csv")

# Name Caching
_CACHED_FIRST_NAMES: list[str] = []
_CACHED_LAST_NAMES: list[str] = []

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

DEFAULT_CONFIG = {
    "num_workers": 1,
    "task_mode": "2fa",
    "browser_channel": "chrome",
    "headless": False,
    "viewport_width": 500,
    "viewport_height": 700,
    "email_services": {
        "mail_td": False,
        "mail_cx": True,
        "ninja": True
    },
    "telegram": {
        "target_bot": "",
        "auto_repeat_tasks": True,
        "delay_between_steps": 0.5,
        "accounts": []
    },
    "password_config": {
        "prefix": "User",
        "random_chars_length": 3,
        "include_today_date": True
    },
    "google_sheet": {
        "enabled": False,
        "service_account_file": "service_account.json",
        "spreadsheet_id": "",
        "spreadsheet_url": "",
        "google_sheet_webhook_url": "",
        "input_sheet_name": "Sheet1",
        "done_sheet_name": "Sheet2"
    }
}

def load_config():
    """Load configuration settings from config.json"""
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                cfg.update(loaded)
        except Exception:
            pass
    return cfg

def save_config(cfg: dict) -> bool:
    """Save configuration settings to config.json"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
        return True
    except Exception as e:
        log("CONFIG", f"Failed to save config: {e}")
        return False

def get_active_email_services() -> list[str]:
    """
    Returns the list of active email services enabled in config.
    Checks 'email_services' and 'mail_services'.
    Example: ['mail_td', 'mail_cx', 'ninja'] or ['ninja'] or ['mail_cx']
    """
    cfg = load_config()
    services_cfg = cfg.get("email_services") or cfg.get("mail_services") or {}
    active = []

    # Check Mail.td setting
    if services_cfg.get("mail_td") is True or services_cfg.get("mailtd") is True or services_cfg.get("mail.td") is True:
        active.append("mail_td")

    # Check Mail.cx setting
    if services_cfg.get("mail_cx") is True or services_cfg.get("mailcx") is True or services_cfg.get("mail.cx") is True:
        active.append("mail_cx")

    # Check TempMail Ninja setting
    if services_cfg.get("ninja") is True or services_cfg.get("mail_ninja") is True or services_cfg.get("tempmail_ninja") is True:
        active.append("ninja")

    # If no service enabled, fallback to default active services
    if not active:
        active = ["mail_cx", "ninja"]

    return active

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

    if not first_names:
        first_names = ["alex", "james", "john", "david", "michael", "emily", "sarah", "daniel", "chris", "ryan", "matt", "jessica", "andrew", "kevin", "brian", "jason", "luke", "sam", "nathan", "brandon"]
    if not last_names:
        last_names = ["smith", "miller", "brown", "jones", "davis", "wilson", "taylor", "white", "clark", "hall", "allen", "young", "king", "wright", "scott", "green", "baker", "adams", "nelson", "hill"]

    _CACHED_FIRST_NAMES = first_names
    _CACHED_LAST_NAMES = last_names
    return _CACHED_FIRST_NAMES, _CACHED_LAST_NAMES

def get_random_name() -> tuple[str, str]:
    """Returns a random (first_name, last_name) tuple from names.csv"""
    first_list, last_list = load_names_from_csv()
    first_name = random.choice(first_list).strip().capitalize()
    last_name = random.choice(last_list).strip().capitalize()
    return first_name, last_name

def generate_custom_password(instance_name=""):
    """Generate custom password based on config rules"""
    cfg = load_config()
    pwd_cfg = cfg.get("password_config", {})
    prefix = pwd_cfg.get("prefix", "MAnik")
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

def extract_otp_from_text(text: str, mail_from: str = "", subj: str = "", expected_digits: int | None = None) -> str | None:
    """Extract OTP code from email text or subject with optional digit length filtering"""
    return extract_otp_from_mail_td_text(text, subj=subj, mail_from=mail_from, expected_digits=expected_digits)

def generate_temp_email(instance_name="") -> tuple[str, str, str | None]:
    """
    Generate temporary email using configured email services (Mail.td / Mail.cx).
    If multiple services are enabled in config, randomly selects one.
    Returns: (email, password, mail_token)
    """
    pwd = generate_custom_password(instance_name=instance_name)
    first, last = get_random_name()
    prefix = f"{re.sub(r'[^a-z0-9]', '', first.lower())}{random.randint(100, 999)}"

    active_services = get_active_email_services()
    chosen_service = random.choice(active_services)

    log("MAIL-SVC", f"Active services: {active_services} -> Selected: {chosen_service}", instance_name=instance_name)

    # List of attempts starting with chosen_service, followed by other active services as fallbacks
    attempt_order = [chosen_service] + [s for s in active_services if s != chosen_service]

    for service in attempt_order:
        if service == "ninja" and HAS_NINJA:
            log("GEN", "Generating email via TempMail Ninja service...", instance_name=instance_name)
            email, p, token = generate_ninja_account(
                password=pwd,
                instance_name=instance_name
            )
            if email and token:
                return email, pwd, token

        elif service == "mail_cx":
            log("GEN", "Generating email via Mail.cx service...", instance_name=instance_name)
            email, p, token = generate_mail_cx_account(
                email_prefix=prefix,
                password=pwd,
                instance_name=instance_name
            )
            if email and token:
                return email, pwd, token

            # Fallback retry
            email, p, token = generate_mail_cx_account(
                email_prefix=f"{prefix}{random.randint(10, 99)}",
                password=pwd,
                instance_name=instance_name
            )
            if email and token:
                return email, pwd, token

        elif service == "mail_td":
            log("GEN", "Generating email via Mail.td service...", instance_name=instance_name)
            email, strong_pwd, token = generate_mail_td_account(
                email_prefix=prefix,
                password=pwd,
                instance_name=instance_name
            )
            if email and token:
                return email, (strong_pwd or pwd), token

            # Fallback retry
            log("GEN-WARN", "Mail.td retry attempt...", instance_name=instance_name)
            email, strong_pwd, token = generate_mail_td_account(
                email_prefix=f"{prefix}{random.randint(10, 99)}",
                password=pwd,
                instance_name=instance_name
            )
            if email and token:
                return email, (strong_pwd or pwd), token

    # If all configured services failed, try any remaining services as hard fallback
    all_possible = ["mail_cx", "mail_td", "ninja"] if HAS_NINJA else ["mail_cx", "mail_td"]
    remaining = [s for s in all_possible if s not in attempt_order]
    for service in remaining:
        try:
            if service == "mail_cx":
                log("GEN-FALLBACK", "Fallback to Mail.cx service...", instance_name=instance_name)
                email, p, token = generate_mail_cx_account(email_prefix=prefix, password=pwd, instance_name=instance_name)
                if email and token:
                    return email, pwd, token
            elif service == "mail_td":
                log("GEN-FALLBACK", "Fallback to Mail.td service...", instance_name=instance_name)
                email, strong_pwd, token = generate_mail_td_account(email_prefix=prefix, password=pwd, instance_name=instance_name)
                if email and token:
                    return email, (strong_pwd or pwd), token
            elif service == "ninja" and HAS_NINJA:
                log("GEN-FALLBACK", "Fallback to Ninja service...", instance_name=instance_name)
                email, p, token = generate_ninja_account(password=pwd, instance_name=instance_name)
                if email and token:
                    return email, pwd, token
        except Exception as e:
            log("GEN-FALLBACK-ERR", f"Fallback service {service} failed: {e}", instance_name=instance_name)

    return None, None, None

def fetch_otp_from_temp_mail(email: str, password: str, token: str | None = None, max_wait_sec: int = 60, expected_digits: int | None = None, instance_name: str = "") -> str | None:
    """
    Fetch OTP code from Mail.td, Mail.cx, or TempMail Ninja inbox depending on token / email provider.
    Supports expected_digits (e.g. 8 for 2FA security code, 6 for Meta AI registration).
    """
    digit_info = f" ({expected_digits}-digit expected)" if expected_digits else ""

    # 0. Check if token explicitly identifies Ninja
    if token and (str(token).startswith("NINJA:") or "NINJA" in str(token)):
        log("OTP-FETCH", f"Polling TempMail Ninja inbox for {email}{digit_info} (timeout {max_wait_sec}s)...", instance_name=instance_name)
        if HAS_NINJA:
            return fetch_otp_ninja(token, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)

    # 1. Check if token explicitly identifies Mail.cx
    if token and token.startswith("MAILCX:"):
        log("OTP-FETCH", f"Polling Mail.cx inbox for {email}{digit_info} (timeout {max_wait_sec}s)...", instance_name=instance_name)
        return fetch_otp_mail_cx(token, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)

    # 2. Check if token explicitly identifies Mail.td
    if token and token.startswith("MAILTD:"):
        log("OTP-FETCH", f"Polling Mail.td inbox for {email}{digit_info} (timeout {max_wait_sec}s)...", instance_name=instance_name)
        return fetch_otp_mail_td(token, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)

    # 3. Check if registered in Ninja client pool
    if HAS_NINJA and (email in _NINJA_CLIENTS or (token and token in _NINJA_CLIENTS)):
        log("OTP-FETCH", f"Polling TempMail Ninja inbox for {email}{digit_info} (timeout {max_wait_sec}s)...", instance_name=instance_name)
        return fetch_otp_ninja(token or email, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)

    # 4. Check known Mail.cx domains
    known_cx_domains = ["uqu.me", "ddker.com", "9k3r.com"]
    if email and any(email.lower().endswith(f"@{d}") for d in known_cx_domains):
        log("OTP-FETCH", f"Polling Mail.cx inbox for {email}{digit_info} (timeout {max_wait_sec}s)...", instance_name=instance_name)
        return fetch_otp_mail_cx(email, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)

    # 5. Try Mail.td auto-login
    try:
        client = MailTDClient()
        data = client.login(email, password)
        acc_id = data.get("id")
        jwt_token = data.get("token")
        if acc_id and jwt_token:
            token_str = f"MAILTD:{acc_id}:{jwt_token}"
            log("OTP-FETCH", f"Polling Mail.td inbox for {email}{digit_info} (timeout {max_wait_sec}s)...", instance_name=instance_name)
            return fetch_otp_mail_td(token_str, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)
    except Exception as e:
        log("MAILTD-WARN", f"Mail.td auto-login attempt for {email}: {e}", instance_name=instance_name)

    # 6. Final fallback: try Mail.cx inbox
    log("OTP-FETCH", f"Fallback polling Mail.cx inbox for {email}{digit_info} (timeout {max_wait_sec}s)...", instance_name=instance_name)
    return fetch_otp_mail_cx(email, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)

def delete_previous_cpanel_email(instance_name=""):
    """Legacy helper for cPanel cleanup (no-op for Mail.td/Mail.cx)"""
    pass

def save_last_created_email(full_email="", instance_name=""):
    """Legacy helper for cPanel state tracking (no-op for Mail.td/Mail.cx)"""
    pass
