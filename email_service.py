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
    "browser_channel": "chrome",
    "headless": False,
    "viewport_width": 500,
    "viewport_height": 700,
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
    Generate temporary email using Mail.td service.
    Returns: (email, password, mail_token)
    """
    pwd = generate_custom_password(instance_name=instance_name)
    first, last = get_random_name()
    prefix = f"{re.sub(r'[^a-z0-9]', '', first.lower())}{random.randint(100, 999)}"

    log("GEN", "Generating email via Mail.td service...", instance_name=instance_name)
    email, strong_pwd, token = generate_mail_td_account(
        email_prefix=prefix,
        password=pwd,
        instance_name=instance_name
    )
    if email and token:
        return email, (strong_pwd or pwd), token

    # Fallback attempt
    log("GEN-WARN", "Mail.td retry attempt...", instance_name=instance_name)
    email, strong_pwd, token = generate_mail_td_account(
        email_prefix=f"{prefix}{random.randint(10, 99)}",
        password=pwd,
        instance_name=instance_name
    )
    return email, (strong_pwd or pwd), token

def fetch_otp_from_temp_mail(email: str, password: str, token: str | None = None, max_wait_sec: int = 60, expected_digits: int | None = None, instance_name: str = "") -> str | None:
    """
    Fetch OTP code from Mail.td inbox.
    Supports expected_digits (e.g. 8 for 2FA security code, 6 for Meta AI registration).
    """
    digit_info = f" ({expected_digits}-digit expected)" if expected_digits else ""
    log("OTP-FETCH", f"Polling Mail.td inbox for {email}{digit_info} (timeout {max_wait_sec}s)...", instance_name=instance_name)
    if token and token.startswith("MAILTD:"):
        return fetch_otp_mail_td(token, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)
    else:
        try:
            client = MailTDClient()
            data = client.login(email, password)
            acc_id = data.get("id")
            jwt_token = data.get("token")
            if acc_id and jwt_token:
                token_str = f"MAILTD:{acc_id}:{jwt_token}"
                return fetch_otp_mail_td(token_str, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)
        except Exception as e:
            log("MAILTD-WARN", f"Mail.td auto-login error for {email}: {e}", instance_name=instance_name)
    return None
