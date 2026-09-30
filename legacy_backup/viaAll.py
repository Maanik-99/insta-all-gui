import asyncio
import csv
import io
import json
import os
import random
import re
import shutil
import string
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pyotp
import requests
import socketio
import urllib3
from bs4 import BeautifulSoup
from telethon import TelegramClient, errors, events
from telethon.tl.custom import Button

from google_sheet_manager import gsheet_manager

urllib3.disable_warnings()

# Ensure safe terminal encoding & unbuffered output on all Windows platforms
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    elif sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
except Exception:
    pass

# ==============================================================================
# ⚙️ CONFIGURATION & SETTINGS LOADER
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
CREDENTIALS_CSV = os.path.join(BASE_DIR, "credentials.csv")
CREDENTIALS_TXT = os.path.join(BASE_DIR, "credentials.txt")

csv_lock = threading.Lock()
LOG_CALLBACK = None

def load_config() -> dict:
    default_cfg = {
        "num_workers": 1,
        "browser_channel": "chrome",
        "headless": False,
        "viewport_width": 500,
        "viewport_height": 700,
        "telegram": {
            "target_bot": ["tasklyBux_bot"],
            "auto_repeat_tasks": True,
            "delay_between_steps": 0.5,
            "accounts": []
        }
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                default_cfg.update(cfg)
        except Exception as e:
            print(f"[CONFIG-ERR] Failed loading config.json: {e}", flush=True)
    return default_cfg

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

TARGET_BOTS = normalize_target_bots(_TG_CFG.get("target_bot") or _TG_CFG.get("target_bots"))
TARGET_BOT = TARGET_BOTS[0] if TARGET_BOTS else "tasklyBux_bot"
TELEGRAM_ACCOUNTS = _TG_CFG.get("accounts", [])
AUTO_REPEAT_TASKS = _TG_CFG.get("auto_repeat_tasks", True)
DELAY_BETWEEN_STEPS = _TG_CFG.get("delay_between_steps", 0.5)

# Via Browser package name & URLs
PACKAGE_NAME = "mark.via.gp"
META_AI_URL = "https://www.meta.ai/api/oidc/start?waterfall_id=44d8eaaf-4ea5-43dc-addf-94ab370c4864"
INSTA_LOGIN_URL = "https://www.instagram.com/accounts/login"
TWO_FACTOR_URL = "https://accountscenter.instagram.com/password_and_security/two_factor/"
ACCOUNTS_OVERVIEW_URL = "https://accountscenter.instagram.com/account_overview/"

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
# 💾 CREDENTIALS & STORAGE HELPERS
# ==============================================================================
def save_credentials_csv(email, password, two_factor_secret="", username="", status="ACTIVE_2FA", instance_name=""):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    uname = username.strip() if username else "Instagram_User"
    
    with csv_lock:
        file_exists = os.path.exists(CREDENTIALS_CSV)
        try:
            with open(CREDENTIALS_CSV, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "Email", "Username", "Password", "2FA Secret", "Status"])
                writer.writerow([timestamp, email, uname, password, two_factor_secret, status])
            log("CSV-SUCCESS", f"Credentials saved to credentials.csv (User: {uname})", instance_name=instance_name)
        except Exception as e:
            log("CSV-ERR", f"Failed saving to credentials.csv: {e}", instance_name=instance_name)

    if gsheet_manager.is_configured():
        def _save_gsheet():
            try:
                gsheet_manager.save_done_account(timestamp, email, uname, password, two_factor_secret, instance_name=instance_name)
            except Exception as e:
                log("GSHEET-ERR", f"Failed syncing to Google Sheet: {e}", instance_name=instance_name)
        threading.Thread(target=_save_gsheet, daemon=True).start()

def save_credentials_txt(email, password, status="UNKNOWN", username="", instance_name=""):
    with csv_lock:
        uname_str = f" | Username: {username}" if username else ""
        try:
            with open(CREDENTIALS_TXT, "a", encoding="utf-8") as f:
                f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')} | Email: {email} | Password: {password}{uname_str} | Status: {status}\n")
        except Exception:
            pass

# ==============================================================================
# 🥷 TEMPMAIL NINJA API CLIENT (from NINJA.PY)
# ==============================================================================
class TempMailNinjaAPI:
    """
    TempMail Ninja (https://tempmail.ninja) API Client.
    Direct Socket.IO API connection - No browser required!
    """
    BASE_URL = "https://tm-app.solucioneswc.com:2083"

    def __init__(self, locale="en", instance_name=""):
        self.locale = locale
        self.instance_name = instance_name
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

        self.sio.on("connect", self._on_connect)
        self.sio.on("disconnect", self._on_disconnect)
        self.sio.on("new_message_notify", self._on_new_message_notify)

    def _on_connect(self):
        log("NINJA-API", "Connected successfully to TempMail Ninja backend API.", self.instance_name)

    def _on_disconnect(self):
        log("NINJA-API", "Socket disconnected from server.", self.instance_name)

    def _on_new_message_notify(self, data):
        log("NINJA-ALERT", "[LIVE ALERT] NEW EMAIL MESSAGE ARRIVED!", self.instance_name)
        self.check_and_read_inbox()

    def _emit_ack(self, event, *args, timeout=10):
        result = []
        event_done = threading.Event()

        def callback(response):
            result.append(response)
            event_done.set()

        try:
            self.sio.emit(event, *args, callback=callback)
            event_done.wait(timeout=timeout)
        except Exception as e:
            log("NINJA-ERR", f"Error emitting event '{event}': {e}", self.instance_name)
            return None

        return result[0] if result else None

    def connect(self):
        try:
            self.sio.connect(
                f"{self.BASE_URL}?locale={self.locale}",
                headers=self.headers,
                transports=['websocket', 'polling']
            )
            guest_data = self._emit_ack("get_guest_user_data", {"token": None})
            if guest_data:
                ip = guest_data.get("ip", "Unknown")
                country = guest_data.get("country", "Unknown")
                log("NINJA-INIT", f"Guest session initialized (IP: {ip}, Country: {country})", self.instance_name)
            return True
        except Exception as e:
            log("NINJA-ERR", f"Failed to connect to TempMail Ninja: {e}", self.instance_name)
            return False

    def create_temp_email(self):
        log("NINJA-EMAIL", "Creating temporary email address...", self.instance_name)
        res = self._emit_ack("generate_temp_mail")

        if not res or "emailData" not in res:
            raise RuntimeError(f"Failed to generate temp email. Response: {res}")

        self.email_data = res.get("emailData", {})
        self.email_address = self.email_data.get("emailAddress") or self.email_data.get("address")
        self.visitor_token = res.get("newToken")
        expires_at = self.email_data.get("expiresAt") or self.email_data.get("expires_at")

        log("NINJA-EMAIL", f"Created Temp Email: {self.email_address} (Expires: {expires_at})", self.instance_name)
        return self.email_address

    def extract_otp_or_code(self, subject, body_text):
        full_text = f"{subject}\n{body_text}"

        keyword_patterns = [
            r'(?:confirmation|verification|security|auth(?:entication)?|login|access|activation|passcode|one-time)?\s*(?:code|otp|pin|código|kod)[\s\:\-\n\r]+(?:is\s*)?([0-9]{4,8}|[Gg]\-[0-9]{6}|[0-9]{3,4}[\-\s][0-9]{3,4})\b',
            r'(?:enter|use|with)\s+(?:the\s+)?(?:code\s+)?([0-9]{4,8})\s+(?:to\s+(?:confirm|verify|login|proceed|sign))',
            r'(?:code|otp|pin)[\s\:\=]+([0-9]{4,8})\b',
        ]
        for pattern in keyword_patterns:
            m = re.search(pattern, full_text, re.IGNORECASE)
            if m:
                code = m.group(1).strip()
                return code

        subj_m = re.search(r'\b([0-9]{6}|[0-9]{8})\b', subject)
        if subj_m:
            return subj_m.group(1)

        matches_6 = re.findall(r'\b([0-9]{6})\b', body_text)
        if matches_6:
            for c in matches_6:
                if not c.startswith('000000'):
                    return c

        matches_8 = re.findall(r'\b([0-9]{8})\b', body_text)
        if matches_8:
            for c in matches_8:
                return c

        matches_4_5 = re.findall(r'\b([0-9]{4,5})\b', body_text)
        if matches_4_5:
            for c in matches_4_5:
                if not (c.startswith('202') or c.startswith('203')):
                    return c

        return None

    def get_message_detail(self, message_id):
        res = self._emit_ack("get_email_message", {"messageId": message_id, "messageType": "inbound"}, timeout=15)
        if not res or "emailMessage" not in res:
            return None
        return res.get("emailMessage", {})

    def check_and_read_inbox(self):
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

            clean_text = raw_text.strip() if raw_text else ""
            if not clean_text and raw_html:
                soup = BeautifulSoup(raw_html, "html.parser")
                clean_text = soup.get_text(separator="\n").strip()

            extracted_otp = self.extract_otp_or_code(subject, clean_text)
            if extracted_otp:
                self.latest_otp = extracted_otp
                log("NINJA-OTP", f"⭐ [EXTRACTED OTP / CODE]: >>> {extracted_otp} <<<", self.instance_name)

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

        return new_found_messages

    def wait_for_otp(self, timeout=60, poll_interval=3, expected_digits=None):
        log("NINJA-WAIT", f"Waiting for OTP code on '{self.email_address}' (Timeout: {timeout}s)...", self.instance_name)
        start_time = time.time()

        while time.time() - start_time < timeout:
            self.check_and_read_inbox()
            if self.latest_otp:
                if expected_digits is not None:
                    if len(str(self.latest_otp)) == expected_digits:
                        log("NINJA-FOUND", f"✔ OTP matching {expected_digits} digits: {self.latest_otp}", self.instance_name)
                        return self.latest_otp
                else:
                    log("NINJA-FOUND", f"✔ OTP detected: {self.latest_otp}", self.instance_name)
                    return self.latest_otp
            time.sleep(poll_interval)

        log("NINJA-TIMEOUT", "Timeout reached waiting for OTP from TempMail Ninja.", self.instance_name)
        return None

    def disconnect(self):
        self.is_running = False
        try:
            if self.sio.connected:
                self.sio.disconnect()
        except Exception:
            pass


# ==============================================================================
# 📱 ADB EMULATOR CONTROL & AUTOMATION HELPERS
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
        emu_list = [d for d in unique_devices if ("127.0.0.1:" in d or "localhost:" in d or d.startswith("emulator-") or (":" in d and d.split(":")[-1].isdigit())) and "rkg6ormzpfxgae8x" not in d.lower()]
        return emu_list

    return unique_devices

def run_adb_command(args, device_serial=None, instance_name=""):
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

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, encoding='utf-8', errors='ignore')
        return result.stdout.strip() if result.stdout else ""
    except Exception as e:
        log("ADB-ERR", f"Execution error: {e}", instance_name=instance_name)
        return ""

def is_keyboard_visible(device_serial=None, instance_name=""):
    out = run_adb_command("shell dumpsys input_method", device_serial=device_serial, instance_name=instance_name)
    return "mInputShown=true" in out or "mHasSurface=true" in out

def clear_input_field(length=35, device_serial=None, instance_name=""):
    if is_keyboard_visible(device_serial=device_serial, instance_name=instance_name):
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
    if is_keyboard_visible(device_serial=device_serial, instance_name=instance_name):
        run_adb_command("shell input keyevent 111", device_serial=device_serial, instance_name=instance_name)
        time.sleep(0.4)

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

def get_screen_hierarchy(device_serial=None, instance_name=""):
    time.sleep(0.1)
    safe_ser = device_serial.replace(':', '_').replace('.', '_').replace('-', '_') if device_serial else "default"
    dump_path = f"/sdcard/window_dump_{safe_ser}.xml"
    run_adb_command(f"shell uiautomator dump {dump_path}", device_serial=device_serial, instance_name=instance_name)
    xml_str = run_adb_command(f"shell cat {dump_path}", device_serial=device_serial, instance_name=instance_name)
    if not xml_str or not xml_str.startswith("<?xml"):
        return None
    try:
        return ET.fromstring(xml_str)
    except Exception:
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

def find_otp_field(root):
    if root is None:
        return get_rel_coords(root, 0.50, 0.435)
    w, h = get_screen_dimensions(root)
    
    def is_invalid_otp_node(text_str):
        t = text_str.lower()
        return any(bad in t for bad in ["send code", "again", "resend", "continue", "edit", "already", "look for", "notification"])

    for node in root.iter('node'):
        cls = node.attrib.get('class', '').lower()
        txt = node.attrib.get('text', '').strip()
        desc = node.attrib.get('content-desc', '').strip()
        if is_invalid_otp_node(txt) or is_invalid_otp_node(desc):
            continue
        if 'edittext' in cls:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and int(h * 0.30) <= coords[1] <= int(h * 0.55):
                return coords

    for node in root.iter('node'):
        txt = node.attrib.get('text', '').strip()
        desc = node.attrib.get('content-desc', '').strip()
        if is_invalid_otp_node(txt) or is_invalid_otp_node(desc):
            continue
        txt_lower = txt.lower()
        desc_lower = desc.lower()
        if txt_lower in ["code", "confirmation code", "enter code", "6-digit code"] or desc_lower in ["code", "confirmation code"]:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and int(h * 0.30) <= coords[1] <= int(h * 0.55):
                return coords

    return get_rel_coords(root, 0.50, 0.435)

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

def extract_2fa_secret_and_copy(root, device_serial=None, instance_name=""):
    secret_key = ""
    w, h = get_screen_dimensions(root)

    def find_key_in_texts(texts_list):
        ignore_words = {
            "AUTHENTICATION", "INSTRUCTIONS", "SETTINGSONYOUR", "TWOFACTORAUTH",
            "INSTAGRAMFROM", "CHANGEPASSWORD", "PASSWORDANDSEC", "ACCOUNTSETTINGS",
            "PERSONALDETAILS", "LOGGEDINWITH", "DOWNLOADANAPP", "TWOFACTORAUTHENTICATION"
        }
        for t in texts_list:
            cleaned = re.sub(r'[^A-Z0-9]', '', t.upper())
            if len(cleaned) in [32, 24, 16] and not cleaned.isdigit():
                if not any(ign in cleaned for ign in ignore_words):
                    return cleaned

        blob = " ".join(texts_list).upper()
        m = re.search(r'([A-Z0-9]{4}(?:[\s\n\-]+[A-Z0-9]{4}){7})', blob)
        if m:
            c = re.sub(r'[^A-Z0-9]', '', m.group(1))
            if len(c) == 32 and not c.isdigit():
                return c

        m = re.search(r'([A-Z0-9]{4}(?:[\s\n\-]+[A-Z0-9]{4}){3})', blob)
        if m:
            c = re.sub(r'[^A-Z0-9]', '', m.group(1))
            if len(c) == 16 and not c.isdigit():
                return c

        chunks = []
        for word in blob.split():
            clean_w = re.sub(r'[^A-Z0-9]', '', word)
            if len(clean_w) == 4 and not clean_w.isdigit() and clean_w not in ["COPY", "NEXT", "INFO", "PAGE", "MORE", "FROM", "META", "HELP", "STEP", "BACK", "TABS", "MENU", "SITE", "WITH"]:
                chunks.append(clean_w)
            else:
                if len(chunks) in [4, 6, 8]:
                    return "".join(chunks)
                chunks = []
        if len(chunks) in [4, 6, 8]:
            return "".join(chunks)

        return ""

    if root is not None:
        texts = extract_screen_texts(root)
        found = find_key_in_texts(texts)
        if found:
            secret_key = found
            log("2FA-EXTRACT", f"Found Secret Key in UI text: {secret_key}", instance_name=instance_name)

    elem_copy = find_element(root, "copy key", exact=True) or find_element(root, "copy key") or find_element(root, "copy")
    if elem_copy:
        log("2FA-ACTION", f"Tapping 'Copy key' at ({elem_copy[0]}, {elem_copy[1]})...", instance_name=instance_name)
        run_adb_command(f"shell input tap {elem_copy[0]} {elem_copy[1]}", device_serial=device_serial, instance_name=instance_name)
        time.sleep(0.4)
        run_adb_command(f"shell input tap {elem_copy[0]} {elem_copy[1]}", device_serial=device_serial, instance_name=instance_name)
    else:
        log("2FA-ACTION", "Scrolling down to reveal 'Copy key' button...", instance_name=instance_name)
        run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.65)} {int(w*0.50)} {int(h*0.45)} 250", device_serial=device_serial, instance_name=instance_name)
        time.sleep(1.2)
        root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
        elem_copy = find_element(root, "copy key", exact=True) or find_element(root, "copy key") or find_element(root, "copy")
        if elem_copy:
            log("2FA-ACTION", f"Found 'Copy key' after scroll. Tapping ({elem_copy[0]}, {elem_copy[1]})...", instance_name=instance_name)
            run_adb_command(f"shell input tap {elem_copy[0]} {elem_copy[1]}", device_serial=device_serial, instance_name=instance_name)
        else:
            fx, fy = get_rel_coords(root, 0.24, 0.66)
            log("2FA-ACTION", f"Tapping relative fallback 'Copy key' at ({fx}, {fy})...", instance_name=instance_name)
            run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)

    time.sleep(0.8)

    if not secret_key and root is not None:
        texts = extract_screen_texts(root)
        found = find_key_in_texts(texts)
        if found:
            secret_key = found
            log("2FA-EXTRACT", f"Found Secret Key in UI text (Post-scroll): {secret_key}", instance_name=instance_name)

    return secret_key

def find_instagram_account_tile(root, email=""):
    if root is None:
        return get_rel_coords(root, 0.50, 0.85)
    w, h = get_screen_dimensions(root)
    email_user = email.split('@')[0].lower() if email and '@' in email else ""
    
    for node in root.iter('node'):
        txt = node.attrib.get('text', '').strip()
        desc = node.attrib.get('content-desc', '').strip()
        t_lower = (txt + " " + desc).lower()
        if '@' in t_lower or 'horizon' in t_lower or (email_user and email_user in t_lower) or any(k in t_lower for k in ["meta", "popik", "lat", "ninja", "admiredental", "wombat", "qabq", "horse", "penguin", "jellyfish"]):
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and coords[1] >= int(h * 0.72):
                return coords

    for node in root.iter('node'):
        coords = parse_bounds(node.attrib.get('bounds', ''))
        if coords and int(w * 0.10) <= coords[0] <= int(w * 0.90) and int(h * 0.76) <= coords[1] <= int(h * 0.92):
            return coords

    return get_rel_coords(root, 0.50, 0.85)

def find_instagram_2fa_account_tile(root, username=""):
    if root is None:
        return None
    w, h = get_screen_dimensions(root)
    if username:
        for node in root.iter('node'):
            txt = node.attrib.get('text', '').strip()
            desc = node.attrib.get('content-desc', '').strip()
            if username.lower() in txt.lower() or username.lower() in desc.lower():
                coords = parse_bounds(node.attrib.get('bounds', ''))
                if coords:
                    return coords[0], coords[1]
    for node in root.iter('node'):
        txt = node.attrib.get('text', '').strip()
        desc = node.attrib.get('content-desc', '').strip()
        t_low = (txt + " " + desc).lower()
        if "instagram" in t_low and "meta" not in t_low:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and coords[1] < int(h * 0.75):
                return coords[0], coords[1]
    return get_rel_coords(root, 0.50, 0.49)

def find_month_dropdown_coords(root):
    if root is None:
        return get_rel_coords(root, 0.25, 0.40)
    months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
    for node in root.iter('node'):
        txt = node.attrib.get('text', '').strip().lower()
        desc = node.attrib.get('content-desc', '').strip().lower()
        cls = node.attrib.get('class', '').lower()
        if 'month' in txt or 'month' in desc or any(txt.startswith(m) or desc.startswith(m) for m in months) or ('spinner' in cls and any(m in txt for m in months)):
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and coords[1] > 0:
                return coords
    return get_rel_coords(root, 0.25, 0.40)

def find_day_dropdown_coords(root):
    if root is None:
        return get_rel_coords(root, 0.68, 0.40)
    for node in root.iter('node'):
        txt = node.attrib.get('text', '').strip().lower()
        desc = node.attrib.get('content-desc', '').strip().lower()
        cls = node.attrib.get('class', '').lower()
        if txt.startswith('day') or desc.startswith('day') or ('spinner' in cls and 'day' in txt):
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and coords[1] > 0:
                return coords
    return get_rel_coords(root, 0.68, 0.40)

def find_year_dropdown_coords(root):
    if root is None:
        return get_rel_coords(root, 0.50, 0.48)
    for node in root.iter('node'):
        txt = node.attrib.get('text', '').strip().lower()
        desc = node.attrib.get('content-desc', '').strip().lower()
        if ('year' in txt or 'year' in desc) and 'birthday' not in txt and 'date of birth' not in txt:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and coords[1] > 0:
                return coords
    return get_rel_coords(root, 0.50, 0.48)

def find_confirm_button(root):
    if root is None:
        return get_rel_coords(root, 0.50, 0.86)
    for node in root.iter('node'):
        txt = node.attrib.get('text', '').strip().lower()
        desc = node.attrib.get('content-desc', '').strip().lower()
        cls = node.attrib.get('class', '').lower()
        if (txt == 'confirm' or desc == 'confirm') and ('button' in cls or 'view' in cls):
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and coords[1] > 700:
                return coords
    return get_rel_coords(root, 0.50, 0.86)

def select_random_dob(root, device_serial=None, instance_name=""):
    w, h = get_screen_dimensions(root)
    
    # 1. Random Day (1 to 28)
    target_day = random.randint(1, 28)
    target_day_str = str(target_day)
    log("DOB-ACTION", f"Selecting Day: {target_day_str}...", instance_name=instance_name)
    day_coords = find_day_dropdown_coords(root)
    run_adb_command(f"shell input tap {day_coords[0]} {day_coords[1]}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.2)
    
    day_root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
    day_tapped = False
    if day_root is not None:
        for node in day_root.iter('node'):
            txt = node.attrib.get('text', '').strip() or node.attrib.get('content-desc', '').strip()
            if txt == target_day_str:
                coords = parse_bounds(node.attrib.get('bounds', ''))
                if coords and int(h * 0.25) <= coords[1] <= int(h * 0.75):
                    run_adb_command(f"shell input tap {coords[0]} {coords[1]}", device_serial=device_serial, instance_name=instance_name)
                    day_tapped = True
                    break
                    
    if not day_tapped:
        if target_day > 10:
            run_adb_command(f"shell input swipe {day_coords[0]} {int(h*0.70)} {day_coords[0]} {int(h*0.35)} 350", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)
            day_root2 = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            if day_root2 is not None:
                for node in day_root2.iter('node'):
                    txt = node.attrib.get('text', '').strip()
                    if txt == target_day_str:
                        coords = parse_bounds(node.attrib.get('bounds', ''))
                        if coords and int(h * 0.25) <= coords[1] <= int(h * 0.75):
                            run_adb_command(f"shell input tap {coords[0]} {coords[1]}", device_serial=device_serial, instance_name=instance_name)
                            day_tapped = True
                            break
        if not day_tapped:
            tap_y = random.uniform(0.44, 0.58)
            run_adb_command(f"shell input tap {day_coords[0]} {int(h*tap_y)}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.2)

    # 2. Random Month
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    target_month = random.choice(months)
    log("DOB-ACTION", f"Selecting Month: {target_month}...", instance_name=instance_name)
    month_coords = find_month_dropdown_coords(root)
    run_adb_command(f"shell input tap {month_coords[0]} {month_coords[1]}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.2)
    
    month_popup_root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
    month_tapped = False
    if month_popup_root is not None:
        for node in month_popup_root.iter('node'):
            txt = node.attrib.get('text', '').strip() or node.attrib.get('content-desc', '').strip()
            if txt.lower() == target_month.lower():
                coords = parse_bounds(node.attrib.get('bounds', ''))
                if coords and int(h * 0.25) <= coords[1] <= int(h * 0.75):
                    run_adb_command(f"shell input tap {coords[0]} {coords[1]}", device_serial=device_serial, instance_name=instance_name)
                    month_tapped = True
                    break
                    
    if not month_tapped:
        if months.index(target_month) > 5:
            run_adb_command(f"shell input swipe {month_coords[0]} {int(h*0.70)} {month_coords[0]} {int(h*0.35)} 350", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)
            month_popup_root2 = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            if month_popup_root2 is not None:
                for node in month_popup_root2.iter('node'):
                    txt = node.attrib.get('text', '').strip()
                    if txt.lower() == target_month.lower():
                        coords = parse_bounds(node.attrib.get('bounds', ''))
                        if coords and int(h * 0.25) <= coords[1] <= int(h * 0.75):
                            run_adb_command(f"shell input tap {coords[0]} {coords[1]}", device_serial=device_serial, instance_name=instance_name)
                            month_tapped = True
                            break
        if not month_tapped:
            tap_y = random.uniform(0.44, 0.58)
            run_adb_command(f"shell input tap {month_coords[0]} {int(h*tap_y)}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.2)

    # 3. Random Year (1985 - 2004)
    target_year = random.randint(1985, 2004)
    log("DOB-ACTION", f"Selecting Year: {target_year}...", instance_name=instance_name)
    yr_coords = find_year_dropdown_coords(root)
    run_adb_command(f"shell input tap {yr_coords[0]} {yr_coords[1]}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.2)

    run_adb_command("shell input keyevent 122", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.3)

    down_steps = 2026 - target_year
    for _ in range(down_steps):
        run_adb_command("shell input keyevent 20", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.3)

    run_adb_command("shell input keyevent 66", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.2)
    run_adb_command("shell input keyevent 23", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.8)

    check_root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
    if check_root is not None:
        check_texts = extract_screen_texts(check_root)
        check_blob = " ".join(check_texts).lower()
        if "select year" in check_blob:
            run_adb_command(f"shell input tap {int(w*0.5)} {int(h*0.18)}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)

    time.sleep(1.2)
    log("DOB-COMPLETE", f"DOB set: Day={target_day}, Month={target_month}, Year={target_year}", instance_name=instance_name)
    return target_year


def detect_current_page(root):
    if root is None:
        return "UNKNOWN_SCREEN", []
    
    texts = extract_screen_texts(root)
    text_blob = " ".join(texts).lower()
    
    if ("can't find account" in text_blob 
        or "cant find account" in text_blob 
        or "we can't find an account" in text_blob 
        or "we cant find an account" in text_blob
        or "try another mobile number or email" in text_blob
        or ("don't have an account" in text_blob and "sign up" in text_blob)):
        return "CANNOT_FIND_ACCOUNT_POPUP", texts
    elif ("can't make this change" in text_blob
          or "cant make this change" in text_blob
          or "you can't make this change at the moment" in text_blob
          or "you cant make this change at the moment" in text_blob
          or "device you don't usually use" in text_blob
          or "device you dont usually use" in text_blob
          or "used this device for a while" in text_blob):
        return "CANNOT_MAKE_CHANGE_POPUP", texts
    elif "incorrect password" in text_blob or "wrong password" in text_blob:
        return "INCORRECT_PASSWORD_POPUP", texts
    elif "home screen" in text_blob or "system apps" in text_blob or "ldstore" in text_blob or "search games" in text_blob:
        return "HOME_SCREEN", texts
    elif "something went wrong" in text_blob or "unable to create an account" in text_blob:
        return "ACCOUNT_ERROR_PAGE", texts
    elif ("two-factor authentication is on" in text_blob
          or "two-factor is on" in text_blob
          or "2-factor authentication is on" in text_blob
          or "authentication app is on" in text_blob
          or "two-step verification is on" in text_blob
          or "authenticator app is on" in text_blob
          or "two-factor authentication turned on" in text_blob
          or "two-factor authentication enabled" in text_blob
          or "two-factor authentication is set up" in text_blob
          or "your two-factor authentication is set up" in text_blob
          or ("two-factor" in text_blob and "is set up" in text_blob)
          or ("backup codes" in text_blob and "two-factor" in text_blob)):
        return "TWO_FACTOR_SUCCESS_PAGE", texts
    elif "get your code from your authentication app" in text_blob or "enter the 6-digit code" in text_blob or ("enter code" in text_blob and "next" in text_blob and "instructions for setup" not in text_blob and "scan this" not in text_blob):
        return "TWO_FACTOR_ENTER_CODE_PAGE", texts
    elif "instructions for setup" in text_blob or "scan this barcode/qr code" in text_blob or "copy the key" in text_blob or "download an authentication app" in text_blob:
        return "TWO_FACTOR_INSTRUCTIONS_PAGE", texts
    elif "set up extra protection for your account" in text_blob or "extra protection for your account" in text_blob or ("extra protection" in text_blob and "get started" in text_blob):
        return "TWO_FACTOR_EXTRA_PROTECTION_PAGE", texts
    elif ("check your email" in text_blob
          or ("enter the code we sent to" in text_blob and ("continue" in text_blob or "code" in text_blob))
          or "we can send a new code in" in text_blob):
        return "TWO_FACTOR_CHECK_EMAIL_PAGE", texts
    elif "help protect your account" in text_blob or "choose how to confirm it's you" in text_blob or ("authentication app" in text_blob and "recommended" in text_blob):
        return "TWO_FACTOR_CHOOSE_METHOD_PAGE", texts
    elif "choose an account to set up two-factor authentication" in text_blob or ("two-factor authentication" in text_blob and ("choose an account" in text_blob or "instagram" in text_blob) and "instructions" not in text_blob and "help protect" not in text_blob and "is on" not in text_blob):
        return "TWO_FACTOR_CHOOSE_ACCOUNT_PAGE", texts
    elif "save your login info?" in text_blob or "save your login info" in text_blob or "won't need to enter it next time" in text_blob or "save info" in text_blob:
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
    elif "username, email or mobile number" in text_blob or ("forgot password?" in text_blob and "log in" in text_blob) or ("phone number, username, or email" in text_blob) or ("log in" in text_blob and ("password" in text_blob or "don't have an account" in text_blob or "sign up" in text_blob)):
        return "INSTAGRAM_LOGIN_PAGE", texts
    elif "allow all cookies" in text_blob or "only allow essential cookies" in text_blob or "decline optional cookies" in text_blob:
        return "INSTAGRAM_COOKIE_PAGE", texts
    elif "share everyday moments" in text_blob or "open instagram" in text_blob:
        return "INSTAGRAM_LANDING_PAGE", texts
    elif "get started on meta ai" in text_blob or "date of birth" in text_blob or "years old" in text_blob:
        return "META_DOB_PASSWORD_PAGE", texts
    elif "edit your meta ai details" in text_blob or ("confirm" in text_blob and "username" in text_blob):
        return "META_CONFIRM_DETAILS_PAGE", texts
    elif "enter the confirmation code" in text_blob or "confirmation code" in text_blob or ("confirm your account" in text_blob and "code" in text_blob):
        return "OTP_VERIFICATION_PAGE", texts
    elif "welcome" in text_blob and ("agree" in text_blob or "terms" in text_blob):
        return "BROWSER_TERMS_PAGE", texts
    elif "where should we start?" in text_blob or "what can i do for you?" in text_blob or ("sign up" in text_blob and "log in" in text_blob and "meta ai" in text_blob):
        return "META_AI_HOME_PAGE", texts
    elif "allow the use of cookies from meta" in text_blob:
        return "META_COOKIES_DIALOG", texts
    elif "log in or create an account" in text_blob or "continue with facebook" in text_blob or "continue with instagram" in text_blob:
        return "META_LANDING_PAGE", texts
    elif "mobile number or email" in text_blob or "enter your mobile number or email" in text_blob or "what's your mobile number" in text_blob or "whats your mobile number" in text_blob:
        return "EMAIL_INPUT_PAGE", texts
    elif "use mobile number or email address" in text_blob or "create new account" in text_blob:
        return "META_LANDING_PAGE", texts
    elif "save your login info" in text_blob or "save login info" in text_blob or "save password" in text_blob:
        return "SAVE_LOGIN_PROMPT", texts
    elif "meta" in text_blob or "facebook" in text_blob or "instagram" in text_blob:
        return "META_GENERAL_PAGE", texts
    else:
        return "GENERIC_PAGE", texts


# ==============================================================================
# 🚀 PHASE 1: META AI ACCOUNT CREATION VIA BROWSER (ONLY VIA ALL + NINJA)
# ==============================================================================
def create_meta_account_via_browser(
    device_serial: str,
    first_name: str,
    last_name: str,
    password: str,
    username: str = "",
    instance_name: str = "Inst-1",
    task_context: dict = None
) -> Tuple[bool, str, Optional[TempMailNinjaAPI]]:
    log("META-INIT", f"Starting Meta AI registration for {first_name} on device {device_serial}...", instance_name=instance_name)

    # 1. Connect TempMail Ninja API
    ninja_client = TempMailNinjaAPI(instance_name=instance_name)
    if not ninja_client.connect():
        log("META-ERR", "Could not connect to TempMail Ninja API!", instance_name=instance_name)
        return False, "", None

    email = ninja_client.create_temp_email()
    if not email:
        log("META-ERR", "Failed to generate TempMail Ninja email!", instance_name=instance_name)
        ninja_client.disconnect()
        return False, "", None

    # 2. Reset Via Browser State
    log("META-STEP", f"Resetting Via Browser ({PACKAGE_NAME})...", instance_name=instance_name)
    run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.5)
    run_adb_command(f"shell pm clear {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.0)

    # 3. Launch Meta AI URL in Via Browser
    log("META-STEP", f"Opening Meta AI URL in Via Browser...", instance_name=instance_name)
    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{META_AI_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
    time.sleep(3.0)

    landing_clicked = False
    landing_retry_count = 0
    email_submitted = False
    password_submitted = False
    dob_submitted = False
    details_confirmed = False
    otp_submitted = False
    max_loops = 60
    loop_count = 0

    log("META-STATE", "Entering Meta Creation State Machine...", instance_name=instance_name)

    while loop_count < max_loops:
        if task_context and (task_context.get('status') == 'cancelled' or task_context.get('cancelled')):
            log("META-CANCEL", "Task was cancelled during Meta creation. Aborting...", instance_name=instance_name)
            ninja_client.disconnect()
            return False, "", None

        loop_count += 1
        root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
        page_name, visible_texts = detect_current_page(root)
        sample = visible_texts[:3] if visible_texts else ["No visible text"]
        log("META-LOOP", f"[Loop {loop_count}/{max_loops}] Active Page: '{page_name}' | Context: {sample}", instance_name=instance_name)

        if page_name == "ACCOUNT_ERROR_PAGE":
            log("META-WARN", "Error dialog detected. Tapping OK and regenerating email...", instance_name=instance_name)
            tap_or_fallback_rel(root, "ok", 0.5, 0.58, "OK Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)
            run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.5)
            run_adb_command(f"shell pm clear {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.0)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{META_AI_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.0)
            email = ninja_client.create_temp_email()
            landing_clicked = False
            email_submitted = False
            password_submitted = False
            dob_submitted = False
            details_confirmed = False
            otp_submitted = False
            continue

        elif page_name == "HOME_SCREEN":
            log("META-ACTION", "Home screen detected. Re-launching Meta URL...", instance_name=instance_name)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{META_AI_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.0)

        elif page_name == "BROWSER_TERMS_PAGE":
            log("META-ACTION", "Terms of service -> Tapping AGREE...", instance_name=instance_name)
            tap_or_fallback_rel(root, "agree", 0.5, 0.88, "Agree Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{META_AI_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.0)

        elif page_name == "META_AI_HOME_PAGE":
            landing_clicked = False
            log("META-ACTION", "Meta AI Home page -> Clicking 'Sign up'...", instance_name=instance_name)
            elem_su = find_element(root, "sign up") or find_element(root, "log in")
            if elem_su:
                run_adb_command(f"shell input tap {elem_su[0]} {elem_su[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.85, 0.17)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.0)

        elif page_name == "META_COOKIES_DIALOG":
            log("META-ACTION", "Consent dialog -> Tapping 'Allow all cookies'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "allow all cookies", 0.5, 0.77, "Allow All Cookies Button", device_serial=device_serial, instance_name=instance_name)
            time.sleep(2.5)

        elif page_name == "META_LANDING_PAGE":
            if not landing_clicked or landing_retry_count >= 2:
                log("META-ACTION", "Landing page -> Tapping 'Use mobile number or email address'...", instance_name=instance_name)
                tap_or_fallback_rel(root, "use mobile number or email address", 0.5, 0.68, "Use Email Option", device_serial=device_serial, instance_name=instance_name)
                landing_clicked = True
                landing_retry_count = 0
            else:
                landing_retry_count += 1
            time.sleep(2.5)

        elif page_name == "EMAIL_INPUT_PAGE":
            text_blob = " ".join(visible_texts).lower()
            if "please enter a valid" in text_blob:
                log("META-WARN", "Invalid email warning. Regenerating new temp email...", instance_name=instance_name)
                email = ninja_client.create_temp_email()

            log("META-ACTION", f"Typing Temp Email: {email}", instance_name=instance_name)
            elem_email = find_element(root, "email", by_class="EditText") or find_element(root, "mobile", by_class="EditText")
            if elem_email:
                run_adb_command(f"shell input tap {elem_email[0]} {elem_email[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.5, 0.435)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)
            
            clear_input_field(device_serial=device_serial, instance_name=instance_name)
            type_text(email, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.0)
            
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            log("META-ACTION", "Clicking 'Continue' button after email...", instance_name=instance_name)
            tap_or_fallback_rel(root, "continue", 0.5, 0.617, "Continue Button", device_serial=device_serial, instance_name=instance_name)
            email_submitted = True
            time.sleep(4.0)

        elif page_name == "META_DOB_PASSWORD_PAGE":
            log("META-ACTION", "DOB & Password page detected! Setting DOB & Password...", instance_name=instance_name)
            
            # 1. Random DOB
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            select_random_dob(root, device_serial=device_serial, instance_name=instance_name)
            dob_submitted = True
            time.sleep(1.0)

            # 2. Enter Password
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            pwd_coords = find_password_field(root)
            log("META-ACTION", f"Typing password '{password}'...", instance_name=instance_name)
            if pwd_coords:
                run_adb_command(f"shell input tap {pwd_coords[0]} {pwd_coords[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.5, 0.69)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)
            
            clear_input_field(device_serial=device_serial, instance_name=instance_name)
            type_text(password, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.0)
            
            # 3. Dismiss keyboard & reveal Next button
            hide_keyboard(device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)
            
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            elem_next = find_element(root, "next")
            if not elem_next:
                log("META-ACTION", "Swiping up once to reveal Next button...", instance_name=instance_name)
                run_adb_command("shell input swipe 270 650 270 350 400", device_serial=device_serial, instance_name=instance_name)
                time.sleep(1.0)
                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            
            log("META-ACTION", "Clicking 'Next' button...", instance_name=instance_name)
            tap_or_fallback_rel(root, "next", 0.5, 0.82, "Next Button", device_serial=device_serial, instance_name=instance_name)
            password_submitted = True
            time.sleep(4.0)

        elif page_name == "META_CONFIRM_DETAILS_PAGE":
            if not details_confirmed:
                log("META-ACTION", "Confirm details page -> Clicking 'Confirm' button...", instance_name=instance_name)
                conf_coords = find_confirm_button(root)
                run_adb_command(f"shell input tap {conf_coords[0]} {conf_coords[1]}", device_serial=device_serial, instance_name=instance_name)
                details_confirmed = True
                time.sleep(4.0)
            else:
                time.sleep(2.0)

        elif page_name == "OTP_VERIFICATION_PAGE":
            if not otp_submitted:
                log("META-OTP", "OTP screen detected! Polling OTP code from TempMail Ninja...", instance_name=instance_name)
                otp_code = ninja_client.wait_for_otp(timeout=60, poll_interval=3, expected_digits=6)
                if otp_code:
                    log("META-OTP", f"Retrieved OTP code: {otp_code}. Entering OTP...", instance_name=instance_name)
                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    otp_coords = find_otp_field(root)
                    if otp_coords:
                        run_adb_command(f"shell input tap {otp_coords[0]} {otp_coords[1]}", device_serial=device_serial, instance_name=instance_name)
                        time.sleep(0.3)
                        run_adb_command(f"shell input tap {otp_coords[0]} {otp_coords[1]}", device_serial=device_serial, instance_name=instance_name)
                    else:
                        fx, fy = get_rel_coords(root, 0.5, 0.46)
                        run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.5)
                    
                    clear_input_field(length=6, device_serial=device_serial, instance_name=instance_name)
                    type_text(otp_code, device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.5)
                    
                    digit_keycodes = " ".join([str(int(d) + 7) for d in otp_code if d.isdigit()])
                    if digit_keycodes:
                        run_adb_command(f"shell input keyevent {digit_keycodes}", device_serial=device_serial, instance_name=instance_name)
                        time.sleep(0.5)
                    
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.5)
                    
                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    log("META-ACTION", "Clicking 'Next' / 'Continue' to confirm OTP...", instance_name=instance_name)
                    elem_next = find_element(root, "next") or find_element(root, "continue") or find_element(root, "confirm") or find_element(root, "submit")
                    if elem_next:
                        run_adb_command(f"shell input tap {elem_next[0]} {elem_next[1]}", device_serial=device_serial, instance_name=instance_name)
                    else:
                        tap_or_fallback_rel(root, "next", 0.5, 0.65, "Next Button", device_serial=device_serial, instance_name=instance_name)
                        
                    otp_submitted = True
                    save_credentials_txt(email, password, f"META_OTP_VERIFIED_{otp_code}", username=username, instance_name=instance_name)
                    log("META-SUCCESS", f"🎉 Meta Account Created & OTP Verified for {email}!", instance_name=instance_name)
                    time.sleep(4.0)
                    return True, email, ninja_client
                else:
                    log("META-ERR", "Failed to retrieve OTP code from TempMail Ninja.", instance_name=instance_name)
                    ninja_client.disconnect()
                    return False, "", None
            else:
                log("META-SUCCESS", f"✔ Meta OTP verified!", instance_name=instance_name)
                return True, email, ninja_client

        elif page_name in ["INSTAGRAM_LANDING_PAGE", "INSTAGRAM_LOGIN_PAGE", "INSTAGRAM_NAME_PAGE", "INSTAGRAM_NO_ACCOUNT_FOUND"]:
            log("META-SUCCESS", f"Meta creation already complete, now on Instagram flow!", instance_name=instance_name)
            return True, email, ninja_client

        else:
            time.sleep(2.0)

    log("META-TIMEOUT", "Reached loop limit during Meta AI creation.", instance_name=instance_name)
    ninja_client.disconnect()
    return False, "", None


# ==============================================================================
# 🎯 PHASE 2: INSTAGRAM LOGIN, 2FA SETUP & COOKIE / CREDENTIAL PIPELINE
# ==============================================================================
def update_username_in_accounts_center(device_serial: str, target_username: str, instance_name=""):
    log("UNAME-UPDATE", f"Updating username to '{target_username}' via Accounts Center ({ACCOUNTS_OVERVIEW_URL})...", instance_name=instance_name)
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
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                stage = "EDIT_USERNAME"
                time.sleep(1.2)
            else:
                time.sleep(0.6)

        elif stage == "EDIT_USERNAME":
            log("UNAME-UPDATE", f"Entering target username '{target_username}'...", instance_name=instance_name)
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
            break

    return True

def run_instagram_2fa_pipeline(
    device_serial: str,
    email: str,
    password: str,
    target_login: str,
    ninja_client: Optional[TempMailNinjaAPI] = None,
    instance_name="Inst-1",
    task_context=None
) -> Tuple[bool, str]:
    log("ACC-START", f"Processing Instagram 2FA for: {email} on device {device_serial}...", instance_name=instance_name)

    log("STEP-1", f"Opening Instagram ({INSTA_LOGIN_URL}) in Via Browser (preserving Meta session)...", instance_name=instance_name)
    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{INSTA_LOGIN_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
    time.sleep(2.0)

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
            log("TASK-CANCELLED", f"Task was cancelled by Telegram bot for {target_login}! Aborting...", instance_name=instance_name)
            return False, ""
        loop_count += 1
        root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
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
                if stuck_duration >= 20.0:
                    agree_retry_count += 1
                    if agree_retry_count <= 3:
                        log("TIMEOUT-WARN", f"Screen '{page_name}' stuck for {int(stuck_duration)}s after 'I agree'. Pressing Back (attempt {agree_retry_count}/3)...", instance_name=instance_name)
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

            elif stuck_duration >= 18.0:
                if page_name == "TWO_FACTOR_CHECK_EMAIL_PAGE":
                    time.sleep(0.5)
                    continue
                page_stuck_back_count += 1
                if two_factor_initiated:
                    log("TIMEOUT-WARN", f"Screen '{page_name}' unresponsive ({int(stuck_duration)}s) during 2FA setup. Re-opening 2FA URL: {TWO_FACTOR_URL}...", instance_name=instance_name)
                    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TWO_FACTOR_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
                    page_enter_time = time.time()
                    time.sleep(2.0)
                    if page_stuck_back_count >= 5:
                        log("TIMEOUT-ABORT", "Stuck in 2FA phase >90s. Aborting...", instance_name=instance_name)
                        return False, ""
                    continue
                elif page_name in ["BROWSER_TERMS_PAGE", "INSTAGRAM_LANDING_PAGE", "INSTAGRAM_LOGIN_PAGE", "GENERIC_PAGE", "UNKNOWN_SCREEN"]:
                    log("TIMEOUT-WARN", f"Screen '{page_name}' unresponsive ({int(stuck_duration)}s). Reloading page...", instance_name=instance_name)
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
            log("STATE-ACTION", "Cookie banner -> Tapping 'Allow all cookies'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "allow all cookies", 0.5, 0.70, "Allow Cookies", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.2)

        elif page_name == "HOME_SCREEN":
            log("STATE-ACTION", "Home screen -> Re-opening instagram.com...", instance_name=instance_name)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{INSTA_LOGIN_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.5)
            run_adb_command("shell input keyevent 66", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

        elif page_name == "GENERIC_PAGE" and (loop_count in [2, 3] or stuck_duration >= 12.0):
            log("STATE-ACTION", f"Generic screen detected on loop #{loop_count}. Ensuring URL loaded...", instance_name=instance_name)
            run_adb_command("shell input keyevent 66", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)
            if stuck_duration >= 15.0:
                run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{INSTA_LOGIN_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.5)
                run_adb_command("shell input keyevent 66", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

        elif page_name in ["CANNOT_FIND_ACCOUNT_POPUP", "INCORRECT_PASSWORD_POPUP", "ACCOUNT_ERROR_PAGE", "CANNOT_MAKE_CHANGE_POPUP"]:
            log("STATE-ABORT", f"Account restriction detected '{page_name}'. Terminating cycle...", instance_name=instance_name)
            run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
            return False, ""

        elif page_name == "INSTAGRAM_LANDING_PAGE":
            log("STATE-ACTION", "Instagram landing page -> Tapping 'Log in'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "log in", 0.5, 0.74, "Log In Link", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

        elif page_name == "INSTAGRAM_LOGIN_PAGE":
            if not login_submitted:
                log("STATE-ACTION", f"Entering email '{email}' and password...", instance_name=instance_name)
                elem_user = find_element(root, "username", by_class="EditText") or find_element(root, "email", by_class="EditText") or find_element(root, "phone", by_class="EditText")
                if elem_user:
                    run_adb_command(f"shell input tap {elem_user[0]} {elem_user[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    fx, fy = get_rel_coords(root, 0.5, 0.46)
                    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)
                clear_input_field(device_serial=device_serial, instance_name=instance_name)
                type_text(email, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                elem_pwd = find_password_field(root) or find_element(root, "password", by_class="EditText")
                if elem_pwd:
                    run_adb_command(f"shell input tap {elem_pwd[0]} {elem_pwd[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    fx, fy = get_rel_coords(root, 0.5, 0.58)
                    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)
                clear_input_field(device_serial=device_serial, instance_name=instance_name)
                type_text(password, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)

                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                log("STATE-ACTION", "Submitting login credentials...", instance_name=instance_name)
                tap_or_fallback_rel(root, "log in", 0.5, 0.67, "Log In Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                login_submitted = True
                login_wait_count = 0
                time.sleep(2.0)
            else:
                login_wait_count += 1
                if login_wait_count >= 3:
                    log("STATE-ACTION", f"Login submission pending ({login_wait_count}). Reloading...", instance_name=instance_name)
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
            log("STATE-ACTION", "'What\\'s your name?' screen -> Tapping 'Next'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

        elif page_name == "INSTAGRAM_USERNAME_PAGE":
            text_blob_curr = " ".join(visible_texts).lower()
            is_uname_error = any(err in text_blob_curr for err in [
                "choose a username", "not available", "isn't available", "isnt available",
                "another account", "try another", "can't use", "cant use"
            ])

            if not username_attempted:
                log("STATE-ACTION", f"'Create a username' screen -> Typing Taskly username: '{target_login}'...", instance_name=instance_name)
                elem_uname = find_element(root, "username", by_class="EditText") or find_element(root, "create a username", by_class="EditText")
                if elem_uname:
                    run_adb_command(f"shell input tap {elem_uname[0]} {elem_uname[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    fx, fy = get_rel_coords(root, 0.5, 0.44)
                    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)
                clear_input_field(length=40, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)
                type_text(target_login, device_serial=device_serial, instance_name=instance_name, human_like=True)
                time.sleep(0.4)
                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                tap_or_fallback_rel(root, "next", 0.5, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                username_attempted = True
                time.sleep(1.8)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                new_page, new_texts = detect_current_page(root)
                new_text_blob = " ".join(new_texts).lower()

                has_err = any(err in new_text_blob for err in [
                    "choose a username", "not available", "isn't available", "isnt available",
                    "another account", "try another", "can't use", "cant use"
                ])

                if has_err or new_page == "INSTAGRAM_USERNAME_PAGE":
                    log("USERNAME-WARN", f"Username '{target_login}' not accepted! Navigating Back to auto-suggest username...", instance_name=instance_name)
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.3)
                    
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
                        log("STATE-ACTION", "On Name screen -> Tapping 'Next'...", instance_name=instance_name)
                        tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button", device_serial=device_serial, instance_name=instance_name)
                        time.sleep(1.5)

                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    log("STATE-ACTION", "On Username screen with auto-filled value -> Tapping 'Next'...", instance_name=instance_name)
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.2)
                    tap_or_fallback_rel(root, "next", 0.5, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                    auto_username_used = True
                    time.sleep(1.8)
            else:
                if is_uname_error:
                    log("USERNAME-WARN", "Username error on retry! Navigating Back and Next for auto-username...", instance_name=instance_name)
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.3)

                    back_btn = find_element(root, "back") or find_element(root, "go back")
                    if back_btn:
                        run_adb_command(f"shell input tap {back_btn[0]} {back_btn[1]}", device_serial=device_serial, instance_name=instance_name)
                    else:
                        fx_b, fy_b = get_rel_coords(root, 0.06, 0.19)
                        run_adb_command(f"shell input tap {fx_b} {fy_b}", device_serial=device_serial, instance_name=instance_name)
                        run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)
                    time.sleep(1.2)

                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button", device_serial=device_serial, instance_name=instance_name)
                    time.sleep(1.5)

                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    tap_or_fallback_rel(root, "next", 0.5, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                    auto_username_used = True
                    time.sleep(1.8)
                else:
                    log("STATE-ACTION", "Tapping Next on Username page...", instance_name=instance_name)
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    tap_or_fallback_rel(root, "next", 0.5, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                    time.sleep(1.5)

        elif page_name == "INSTAGRAM_ALLOW_CONTINUE_PAGE":
            log("STATE-ACTION", "'Allow & Continue' screen -> Scrolling and confirming...", instance_name=instance_name)
            w, h = get_screen_dimensions(root)
            run_adb_command(f"shell input swipe {int(w*0.5)} {int(h*0.75)} {int(w*0.5)} {int(h*0.45)} 350", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)
            
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            tap_or_fallback_rel(root, "allow and continue", 0.5, 0.80, "Allow & Continue Button", device_serial=device_serial, instance_name=instance_name)
            time.sleep(2.0)

        elif page_name == "INSTAGRAM_AGREE_TERMS_PAGE":
            if not agree_terms_submitted:
                log("STATE-ACTION", "'Agree to terms' screen -> Tapping 'I agree'...", instance_name=instance_name)
                elem_agree = find_element(root, "i agree", exact=True) or find_element(root, "i agree") or find_element(root, "agree")
                if elem_agree:
                    run_adb_command(f"shell input tap {elem_agree[0]} {elem_agree[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    tap_or_fallback_rel(root, "i agree", 0.5, 0.81, "I Agree Button", device_serial=device_serial, instance_name=instance_name)
                agree_terms_submitted = True
                page_enter_time = time.time()
                time.sleep(1.5)

        elif page_name in ["INSTAGRAM_REGISTERED_PAGE", "INSTAGRAM_SAVE_INFO_PAGE", "INSTAGRAM_CONNECT_FB_PAGE", "INSTAGRAM_NOTIFICATIONS_PAGE"]:
            two_factor_initiated = True
            if auto_username_used:
                log("STATE-ACTION", f"Registration complete with auto-username. Updating to '{target_login}' in Accounts Center...", instance_name=instance_name)
                update_username_in_accounts_center(device_serial=device_serial, target_username=target_login, instance_name=instance_name)
                auto_username_used = False

            log("STATE-ACTION", f"Logged in successfully! Navigating to 2FA Settings URL: {TWO_FACTOR_URL}...", instance_name=instance_name)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TWO_FACTOR_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(2.0)

        elif page_name == "TWO_FACTOR_CHOOSE_ACCOUNT_PAGE":
            two_factor_initiated = True
            log("STATE-ACTION", "2FA: 'Choose an account' -> Selecting Instagram account tile...", instance_name=instance_name)
            tile_coords = find_instagram_2fa_account_tile(root, captured_username)
            if tile_coords:
                run_adb_command(f"shell input tap {tile_coords[0]} {tile_coords[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.49)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.6)

        elif page_name == "TWO_FACTOR_EXTRA_PROTECTION_PAGE":
            two_factor_initiated = True
            log("STATE-ACTION", "2FA: 'Set up extra protection' -> Tapping 'Get started'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "get started", 0.50, 0.86, "Get Started Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.6)

        elif page_name == "TWO_FACTOR_CHECK_EMAIL_PAGE":
            two_factor_initiated = True
            log("STATE-ACTION", f"2FA: 'Check your email' -> Fetching 8-digit security code from inbox for {email}...", instance_name=instance_name)
            email_code = None
            if ninja_client:
                email_code = ninja_client.wait_for_otp(timeout=90, poll_interval=3, expected_digits=8)

            if not email_code:
                log("FAILED", f"✖ Email security code not received for {email}", instance_name=instance_name)
                return False, ""

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            refreshed_page, _ = detect_current_page(root)

            if refreshed_page == "TWO_FACTOR_SUCCESS_PAGE":
                log("SUCCESS", f"✔ 2FA already succeeded for {email}!", instance_name=instance_name)
                return True, two_factor_secret

            if refreshed_page not in ["TWO_FACTOR_CHECK_EMAIL_PAGE", "TWO_FACTOR_CHOOSE_METHOD_PAGE", "TWO_FACTOR_INSTRUCTIONS_PAGE", "TWO_FACTOR_ENTER_CODE_PAGE"]:
                log("2FA-RELOAD", f"Screen moved to '{refreshed_page}'. Re-opening 2FA URL: {TWO_FACTOR_URL}...", instance_name=instance_name)
                run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TWO_FACTOR_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
                time.sleep(2.5)
                continue

            if refreshed_page == "TWO_FACTOR_CHECK_EMAIL_PAGE":
                log("STATE-ACTION", f"Entering 8-digit security code '{email_code}'...", instance_name=instance_name)
                for attempt in range(1, 3):
                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    curr_p, _ = detect_current_page(root)
                    if curr_p != "TWO_FACTOR_CHECK_EMAIL_PAGE":
                        break

                    elem_code_input = (find_element(root, "code", by_class="EditText")
                                       or find_element(root, "enter code", by_class="EditText")
                                       or find_element(root, "enter the code", by_class="EditText")
                                       or find_password_field(root)
                                       or find_element(root, "code"))
                    if elem_code_input:
                        run_adb_command(f"shell input tap {elem_code_input[0]} {elem_code_input[1]}", device_serial=device_serial, instance_name=instance_name)
                    else:
                        fx, fy = get_rel_coords(root, 0.50, 0.49)
                        run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                    
                    time.sleep(0.3)
                    clear_input_field(length=20, device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.2)
                    
                    type_text(email_code, device_serial=device_serial, instance_name=instance_name, human_like=True, min_delay=0.03, max_delay=0.07)
                    time.sleep(0.3)
                    
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.4)

                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    btn_found = (find_element(root, "continue", exact=True)
                                 or find_element(root, "next", exact=True)
                                 or find_element(root, "continue")
                                 or find_element(root, "next"))
                    if btn_found:
                        run_adb_command(f"shell input tap {btn_found[0]} {btn_found[1]}", device_serial=device_serial, instance_name=instance_name)
                    else:
                        fx, fy = get_rel_coords(root, 0.50, 0.65)
                        run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)

                    time.sleep(2.0)
                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    post_p, _ = detect_current_page(root)
                    if post_p != "TWO_FACTOR_CHECK_EMAIL_PAGE":
                        break

                time.sleep(1.0)

        elif page_name == "TWO_FACTOR_CHOOSE_METHOD_PAGE":
            two_factor_initiated = True
            log("STATE-ACTION", "2FA: 'Help protect your account' -> Tapping 'Continue'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "continue", 0.50, 0.86, "Continue Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.6)

        elif page_name == "TWO_FACTOR_INSTRUCTIONS_PAGE":
            two_factor_initiated = True
            log("STATE-ACTION", "2FA: 'Instructions for setup' -> Scrolling & extracting Secret Key...", instance_name=instance_name)
            w, h = get_screen_dimensions(root)

            run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.70)} {int(w*0.50)} {int(h*0.35)} 250",
                            device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.6)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            extracted_key = extract_2fa_secret_and_copy(root, device_serial=device_serial, instance_name=instance_name)
            if extracted_key:
                two_factor_secret = extracted_key
                if task_context is not None: task_context['two_fa_key'] = two_factor_secret
                log("2FA-CAPTURED", f"Secret Key: '{two_factor_secret}'", instance_name=instance_name)
            else:
                clip = get_android_clipboard(device_serial=device_serial, instance_name=instance_name)
                if clip:
                    clean_clip = re.sub(r'[^A-Z0-9]', '', clip.upper())
                    if len(clean_clip) >= 16 and not clean_clip.isdigit():
                        two_factor_secret = clean_clip
                        if task_context is not None: task_context['two_fa_key'] = two_factor_secret
                        log("2FA-CLIPBOARD", f"Secret Key recovered from clipboard: '{two_factor_secret}'", instance_name=instance_name)

            run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.40)} {int(w*0.50)} {int(h*0.65)} 200",
                            device_serial=device_serial, instance_name=instance_name)
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
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.6)

        elif page_name == "TWO_FACTOR_ENTER_CODE_PAGE":
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
            clear_input_field(device_serial=device_serial, instance_name=instance_name)

            otp_to_fill = ""
            if two_factor_secret:
                otp_to_fill = generate_totp_code(two_factor_secret)
                log("2FA-OTP", f"🔑 Generated 6-Digit OTP via pyotp: '{otp_to_fill}'", instance_name=instance_name)
            elif task_context and task_context.get('otp_code'):
                otp_to_fill = task_context['otp_code']

            if otp_to_fill:
                type_text(otp_to_fill, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.4)
                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            clicked_next = tap_or_fallback_rel(root, "next", 0.50, 0.82, "Next Button", exact=False, device_serial=device_serial, instance_name=instance_name)
            log("2FA-ACTION", f"✔ Clicked 'Next' after OTP entry. Waiting 2.0s...", instance_name=instance_name)
            time.sleep(2.0)

            log("SUCCESS", f"✔ Instagram 2FA Setup Completed for {captured_username}! (Secret: {two_factor_secret})", instance_name=instance_name)
            save_credentials_csv(email, password, two_factor_secret, username=captured_username, instance_name=instance_name)
            return True, two_factor_secret

        elif page_name == "TWO_FACTOR_SUCCESS_PAGE":
            log("SUCCESS", f"✔ Instagram 2FA Setup Completed Successfully for {captured_username}! (Secret: {two_factor_secret})", instance_name=instance_name)
            save_credentials_csv(email, password, two_factor_secret, username=captured_username, instance_name=instance_name)
            return True, two_factor_secret

        else:
            time.sleep(2.0)

    return False, ""


# ==============================================================================
# 🤖 TELEGRAM USERBOT & MULTI-WORKER PIPELINE (from 1tasklytometatoinsta_multi_optimize.py)
# ==============================================================================
CLIENT_CACHE = {}

class TasklyWorker:
    def __init__(self, account_config: dict, device_serial: str, instance_name: str, target_bot: str = None, client: TelegramClient = None):
        self.config = account_config
        self.device_serial = device_serial
        self.instance_name = instance_name
        self.api_id = account_config['api_id']
        self.api_hash = account_config['api_hash']
        self.phone_number = account_config['phone']
        
        if target_bot:
            self.target_bot = str(target_bot).strip().lstrip("@")
            self.target_bots = [self.target_bot]
        else:
            raw_bots = account_config.get('target_bots') or account_config.get('target_bot') or TARGET_BOTS
            self.target_bots = normalize_target_bots(raw_bots)
            self.target_bot = self.target_bots[0]
            
        self.current_bot_idx = 0
        
        clean_phone = re.sub(r'[^0-9]', '', str(self.phone_number))
        self.session_name = account_config.get('session_name') or f"taskly_session_{clean_phone}"
        
        if client:
            self.client = client
        elif self.session_name in CLIENT_CACHE:
            self.client = CLIENT_CACHE[self.session_name]
        else:
            self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)
            CLIENT_CACHE[self.session_name] = self.client
        
        self.current_task = {
            'first_name': None,
            'last_name': None,
            'login': None,
            'password': None,
            'two_fa_key': None,
            'otp_code': None,
            'status': 'idle'
        }
        self.task_received_event = asyncio.Event()
        self.two_fa_requested_event = asyncio.Event()
        self.otp_received_event = asyncio.Event()
        self.report_received_event = asyncio.Event()
        self.task_cancelled_event = asyncio.Event()
        self.task_confirm_btn_msg = None
        self._last_processed_key = None
        self._last_processed_time = 0
        self._last_start_reset_time = 0
        self.submitted_count = 0
        self.approved_count = 0
        self.cycle_count = 0
        self.current_balance = "$0.00"

        self.is_stopped = False
        self._register_handlers()

    async def fetch_bot_balance(self) -> Optional[str]:
        try:
            log("TG-BALANCE", f"Checking account balance from @{self.target_bot}...", instance_name=self.instance_name)
            clicked = await self.click_button_by_pattern(r"Balance|💰\s*Balance|💰")
            if not clicked:
                await self.safe_send_message(self.target_bot, "💰 Balance")

            for _ in range(8):
                await asyncio.sleep(0.4)
                recent_msgs = await self.client.get_messages(self.target_bot, limit=4)
                for msg in recent_msgs:
                    t = (msg.text or "").strip()
                    if "balance" in t.lower() and ("$" in t or re.search(r"\d+\.\d+", t)):
                        m_bal = re.search(r"(\$[\d\.]+|\d+\.\d{2,4})", t)
                        if m_bal:
                            bal_str = m_bal.group(1)
                            if not bal_str.startswith("$"):
                                bal_str = f"${bal_str}"
                            self.current_balance = bal_str
                            log("TG-BALANCE", f"💰 Current Balance: {bal_str}", instance_name=self.instance_name)
                            return bal_str
        except Exception as e:
            log("TG-WARN", f"Balance check note: {e}", instance_name=self.instance_name)
        return None

    def stop(self):
        self.is_stopped = True
        self.task_cancelled_event.set()
        self.task_received_event.set()
        self.two_fa_requested_event.set()
        self.otp_received_event.set()
        self.report_received_event.set()
        try:
            run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=self.device_serial, instance_name=self.instance_name)
        except Exception:
            pass
        log("WORKER-STOP", f"Worker {self.instance_name} stopped.", instance_name=self.instance_name)

    def switch_to_next_bot(self):
        if len(self.target_bots) > 1:
            self.current_bot_idx = (self.current_bot_idx + 1) % len(self.target_bots)
            self.target_bot = self.target_bots[self.current_bot_idx]
            log("BOT-SWITCH", f"🔄 Active bot switched to: @{self.target_bot} ({self.current_bot_idx + 1}/{len(self.target_bots)})", instance_name=self.instance_name)

    def _register_handlers(self):
        targets = self.target_bots if self.target_bots else [self.target_bot]
        @self.client.on(events.NewMessage(chats=targets))
        async def _bot_handler(event):
            await self.handle_bot_message(event)

        @self.client.on(events.MessageEdited(chats=targets))
        async def _bot_edit_handler(event):
            await self.handle_bot_message(event)

    async def safe_send_message(self, chat, text, max_retries=5):
        for attempt in range(max_retries):
            try:
                return await self.client.send_message(chat, text)
            except errors.FloodWaitError as e:
                log("FLOOD-WAIT", f"⚠️ Telegram FloodWait: Waiting {e.seconds}s...", instance_name=self.instance_name)
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                log("TG-ERR", f"send_message error ({e}). Retrying in 2s (attempt {attempt+1}/{max_retries})...", instance_name=self.instance_name)
                await asyncio.sleep(2.0)
        return None

    async def safe_click_button(self, btn, max_retries=3):
        for attempt in range(max_retries):
            try:
                await btn.click()
                return True
            except errors.FloodWaitError as e:
                log("FLOOD-WAIT", f"⚠️ FloodWait on button click: Waiting {e.seconds}s...", instance_name=self.instance_name)
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                log("TG-BOT-WARN", f"Button click error ({e}). Retrying in 1.5s...", instance_name=self.instance_name)
                await asyncio.sleep(1.5)
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
            log("TG-WARN", f"Error searching recent messages for button: {e}", instance_name=self.instance_name)
        return False

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
        
        # Balance message
        if ("your balance:" in text.lower() or "balance:" in text.lower()) and ("$" in text or re.search(r"\d+\.\d+", text)):
            m_bal = re.search(r"(\$[\d\.]+|\d+\.\d{2,4})", text)
            if m_bal:
                bal_str = m_bal.group(1)
                if not bal_str.startswith("$"):
                    bal_str = f"${bal_str}"
                self.current_balance = bal_str
                log("TG-BALANCE", f"💰 Current Balance: {bal_str}", instance_name=self.instance_name)

        # Welcome
        if "Welcome" in text or "Terms of Use" in text or "earn money" in text:
            if self.current_task.get('status') in ('running', 'credentials_ready'):
                return
            log("TG-STEP", "Welcome received -> Opening Tasks menu...", instance_name=self.instance_name)
            await asyncio.sleep(DELAY_BETWEEN_STEPS)
            clicked = await self.click_any_button(msg, r"Tasks|📋")
            if not clicked:
                await self.safe_send_message(self.target_bot, "📋 Tasks")

        # Select task
        elif "Please select a task" in text or "select a task" in text:
            if self.current_task.get('status') in ('running', 'credentials_ready'):
                return
            await asyncio.sleep(DELAY_BETWEEN_STEPS)
            is_taskly = "taskly" in (self.target_bot or "").lower()
            if is_taskly:
                log("TG-STEP", "Selecting task: '🔥 Create Inst (No mail)' for @tasklyBux_bot...", instance_name=self.instance_name)
                clicked = await self.click_any_button(msg, r"No\s*mail|Create\s*Inst.*No\s*mail")
                if not clicked:
                    clicked = await self.click_any_button(msg, r"Create\s*Inst")
                if not clicked:
                    await self.safe_send_message(self.target_bot, "🔥Create Inst (No mail) ($0.0300)")
            else:
                log("TG-STEP", "Selecting task '🔥Create Inst (2FA)' for @PayGoeasy_bot...", instance_name=self.instance_name)
                clicked = await self.click_any_button(msg, r"2FA|Create\s*Inst.*2FA")
                if not clicked:
                    await self.safe_send_message(self.target_bot, "🔥Create Inst (2FA) ($0.0250)")

        # Start task
        elif "Review time" in text or "Task: 🔥Create Inst" in text or "Task: 📱 Create Inst" in text or "Create Inst (2FA)" in text or "Task:" in text:
            if self.current_task.get('status') in ('running', 'credentials_ready'):
                return
            log("TG-STEP", "Task description received -> Triggering ▶️ Start...", instance_name=self.instance_name)
            await asyncio.sleep(DELAY_BETWEEN_STEPS)
            clicked = await self.click_any_button(msg, r"Start|▶️")
            if not clicked:
                await self.safe_send_message(self.target_bot, "▶️ Start")

        # Account Credentials Received
        elif re.search(r"First\s*name\s*:", text, re.IGNORECASE) and re.search(r"Login\s*:", text, re.IGNORECASE):
            log("TG-STEP", "Account Credentials Received from Bot!", instance_name=self.instance_name)
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
            
            print("\n" + "="*45)
            print(f"📋 [{self.instance_name}] [BOT CREDENTIALS ACQUIRED]")
            print(f"👤 Full Name  : {raw_full_name} -> {full_name}")
            print(f"🔑 Target User: {self.current_task['login']}")
            print(f"🔒 Password   : {self.current_task['password']}")
            print("="*45 + "\n")
            
            self.task_received_event.set()

        # Bot asks for 2FA key
        elif "2FA key" in text or "enter your 2FA" in text:
            log("TG-STEP", "Bot is requesting 2FA Secret Key...", instance_name=self.instance_name)
            self.two_fa_requested_event.set()

        # Bot sends OTP code
        elif "Your one-time code" in text or "Tap the code to copy" in text or "Press the button to confirm" in text or (len(re.findall(r"\b\d{6}\b", text)) > 0 and self.current_task.get('two_fa_key')):
            log("TG-STEP", "One-time OTP code received from bot!", instance_name=self.instance_name)
            code_match = re.search(r"\b\d{6}\b", text)
            if code_match:
                self.current_task['otp_code'] = code_match.group(0)
                log("TG-OTP", f"🔑 Captured 6-Digit OTP: {self.current_task['otp_code']}", instance_name=self.instance_name)
                self.otp_received_event.set()

        # Report Received / Submitted
        elif "report has been received" in text.lower() or "report received" in text.lower() or "your report has been received" in text.lower() or "Task Completed" in text:
            self.submitted_count += 1
            log("TG-SUBMITTED", f"📥 [SUBMITTED] Report received by Bot! (Total: {self.submitted_count})", instance_name=self.instance_name)
            self.report_received_event.set()

        # Report Approved
        elif "report approved" in text.lower() or "+$0.03" in text or "approved, +" in text.lower() or "credited to your balance" in text:
            self.approved_count += 1
            log("TG-APPROVED", f"💰 [APPROVED] Report approved! (Total: {self.approved_count})", instance_name=self.instance_name)

        # Rate Limit
        elif "making requests too often" in text or "too often" in text.lower() or ("please wait" in text.lower() and "sec" in text.lower()):
            wait_match = re.search(r"wait\s*(\d+)\s*sec", text, re.IGNORECASE)
            wait_s = int(wait_match.group(1)) if wait_match else 20
            log("TG-RATE-LIMIT", f"⏳ Bot @{self.target_bot} rate-limited! (Cooldown {wait_s + 2}s)", instance_name=self.instance_name)
            if len(self.target_bots) > 1:
                self.switch_to_next_bot()
                await asyncio.sleep(DELAY_BETWEEN_STEPS)
                await self.safe_send_message(self.target_bot, "/start")
            else:
                await asyncio.sleep(wait_s + 2)
                await self.safe_send_message(self.target_bot, "/start")

        # Action Cancelled / Conversation Reset
        elif "previous conversation was reset" in text.lower() or "conversation was reset" in text.lower() or "action cancelled" in text.lower() or "action canceled" in text.lower():
            if self.current_task.get('status') in ('credentials_ready', 'running') or self.current_task.get('login'):
                return
            now_ts = time.time()
            if (now_ts - getattr(self, '_last_start_reset_time', 0)) < 4.0:
                return
            self._last_start_reset_time = now_ts

            self.task_cancelled_event.set()
            self.current_task['status'] = 'idle'
            self.current_task['login'] = None
            self.current_task['two_fa_key'] = None
            self.current_task['two_fa_sent'] = False
            self.task_received_event.clear()
            self.two_fa_requested_event.clear()
            self.otp_received_event.clear()
            self.report_received_event.clear()

            log("TG-STEP", "Reset received -> Sending /start...", instance_name=self.instance_name)
            await asyncio.sleep(1.0)
            await self.safe_send_message(self.target_bot, "/start")

        # Time's up / Cancelled
        elif "Time's up" in text or "Task cancelled" in text or "task cancelled" in text.lower() or "time's up" in text.lower():
            log("TG-WARN", "⏰ Task cancelled by bot!", instance_name=self.instance_name)
            self.current_task['status'] = 'cancelled'
            self.current_task['cancelled'] = True
            self.current_task['two_fa_sent'] = False
            self.task_cancelled_event.set()
            self.task_received_event.set()
            self.two_fa_requested_event.set()
            self.otp_received_event.set()
            self.report_received_event.clear()
            await asyncio.sleep(1.5)
            await self.safe_send_message(self.target_bot, "/start")

    async def run_automation_loop(self):
        cycle_num = 0
        while not self.is_stopped:
            try:
                if self.is_stopped:
                    break
                cycle_num += 1
                print("\n" + "#"*60)
                log("CYCLE-START", f"Starting Taskly -> Meta AI -> Instagram 2FA Cycle #{cycle_num} on {self.device_serial}...", instance_name=self.instance_name)
                print("#"*60 + "\n")

                if self.current_task.get('status') == 'credentials_ready' and self.current_task.get('login'):
                    log("STEP-1", f"Using existing credentials from bot: {self.current_task['login']}", instance_name=self.instance_name)
                else:
                    self.task_received_event.clear()
                    self.two_fa_requested_event.clear()
                    self.otp_received_event.clear()
                    self.task_cancelled_event.clear()
                    self.current_task['status'] = 'idle'
                    self.current_task['cancelled'] = False

                    if cycle_num == 1:
                        try:
                            await self.fetch_bot_balance()
                            await asyncio.sleep(0.8)
                        except Exception:
                            pass

                    log("STEP-1", f"Requesting task from @{self.target_bot}...", instance_name=self.instance_name)
                    btn_clicked = await self.click_button_by_pattern(r"Tasks|📋")
                    if not btn_clicked:
                        await self.safe_send_message(self.target_bot, "📋 Tasks")
                    
                    log("WAIT", "Waiting for account credentials from Bot...", instance_name=self.instance_name)
                    try:
                        await asyncio.wait_for(self.task_received_event.wait(), timeout=35.0)
                    except asyncio.TimeoutError:
                        log("WARN", "Timeout waiting for credentials. Sending /start to reset...", instance_name=self.instance_name)
                        self.current_task['status'] = 'idle'
                        await self.safe_send_message(self.target_bot, "/start")
                        await asyncio.sleep(3.0)
                        continue

                if not self.current_task.get('login'):
                    if self.task_cancelled_event.is_set() or self.current_task.get('status') == 'cancelled':
                        log("CYCLE-ABORT", "Task was cancelled by bot. Restarting...", instance_name=self.instance_name)
                        self.current_task['status'] = 'idle'
                        await self.safe_send_message(self.target_bot, "/start")
                        await asyncio.sleep(3.0)
                        continue
                else:
                    self.task_cancelled_event.clear()

                self.current_task['status'] = 'running'
                first_name = self.current_task['first_name']
                last_name = self.current_task['last_name']
                target_login = self.current_task['login']
                target_password = self.current_task['password']

                # =========================================================
                # STEP 2: META AI ACCOUNT CREATION (VIA BROWSER + NINJA API)
                # =========================================================
                log("STEP-2", f"Creating Meta AI account in Via Browser (No Playwright) for: {first_name} {last_name} ({target_login})...", instance_name=self.instance_name)
                loop = asyncio.get_running_loop()

                meta_ok, meta_email, ninja_client = await loop.run_in_executor(
                    None,
                    create_meta_account_via_browser,
                    self.device_serial,
                    first_name,
                    last_name,
                    target_password,
                    target_login,
                    self.instance_name,
                    self.current_task
                )

                if self.current_task.get('status') == 'cancelled':
                    log("CYCLE-ABORT", "Task cancelled during Meta creation. Resetting...", instance_name=self.instance_name)
                    if ninja_client:
                        ninja_client.disconnect()
                    self.current_task['status'] = 'idle'
                    await self.safe_send_message(self.target_bot, "/start")
                    await asyncio.sleep(3.0)
                    continue

                if not meta_ok or not meta_email:
                    log("ERR", "Meta AI account creation failed. Resetting bot task...", instance_name=self.instance_name)
                    if ninja_client:
                        ninja_client.disconnect()
                    self.current_task['status'] = 'idle'
                    self.current_task['login'] = None
                    self.current_task['two_fa_key'] = None
                    self.task_received_event.clear()
                    await self.safe_send_message(self.target_bot, "/start")
                    await asyncio.sleep(4.0)
                    continue

                # =========================================================
                # STEP 3: INSTAGRAM 2FA CREATION & KEY TRANSMISSION
                # =========================================================
                log("STEP-3", f"Running Instagram 2FA on emulator ({self.device_serial}) with Meta email: {meta_email} and Username: {target_login}...", instance_name=self.instance_name)
                
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
                    ninja_client,
                    self.instance_name,
                    self.current_task
                )

                if ninja_client:
                    ninja_client.disconnect()

                stop_key_exchange.set()
                if not key_exchange_task.done():
                    key_exchange_task.cancel()
                try:
                    await key_exchange_task
                except (asyncio.CancelledError, Exception):
                    pass

                if self.current_task.get('status') == 'cancelled':
                    log("CYCLE-ABORT", "Task explicitly cancelled by bot during Instagram pipeline. Resetting...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    await self.safe_send_message(self.target_bot, "/start")
                    await asyncio.sleep(3.0)
                    continue

                final_2fa_key = secret_key or self.current_task.get('two_fa_key')

                # =========================================================
                # STEP 4: CONFIRM TASK COMPLETION WITH BOT
                # =========================================================
                if insta_ok and final_2fa_key and len(final_2fa_key) >= 16:
                    if not self.current_task.get('two_fa_sent'):
                        log("TG-BOT", f"🔑 Submitting 2FA Secret Key '{final_2fa_key}' to @{self.target_bot}...", instance_name=self.instance_name)
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
                            log("TG-WARN", "Inline button not found, sending message text '✅ Account Registered'...", instance_name=self.instance_name)
                            await self.safe_send_message(self.target_bot, "✅ Account Registered")

                        try:
                            await asyncio.wait_for(self.report_received_event.wait(), timeout=4.0)
                            break
                        except asyncio.TimeoutError:
                            if self.task_cancelled_event.is_set() or self.current_task.get('status') != 'running':
                                break
                            log("TG-WARN", f"⚠️ Report confirmation pending (Attempt {attempt}). Retrying in 2.5s...", instance_name=self.instance_name)
                            await asyncio.sleep(2.5)

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

                    try:
                        await asyncio.sleep(1.0)
                        await self.fetch_bot_balance()
                    except Exception:
                        pass
                else:
                    log("ERR", f"Instagram 2FA process or key capture failed for {target_login}. Resetting bot task...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    self.current_task['login'] = None
                    self.current_task['two_fa_key'] = None
                    self.current_task['two_fa_sent'] = False
                    self.task_received_event.clear()
                    self.task_cancelled_event.set()
                    await self.safe_send_message(self.target_bot, "/start")

                if not AUTO_REPEAT_TASKS:
                    log("MAIN", "Auto-repeat disabled. Finishing execution.", instance_name=self.instance_name)
                    break

                if len(self.target_bots) > 1:
                    self.switch_to_next_bot()

                log("WAIT", f"Waiting 3 seconds before next task cycle (Next Bot: @{self.target_bot})...", instance_name=self.instance_name)
                await asyncio.sleep(3.0)

            except Exception as cycle_err:
                log("CYCLE-RECOVER", f"Unexpected cycle error on {self.instance_name}: {cycle_err}. Auto-recovering in 5s...", instance_name=self.instance_name)
                try:
                    await self.safe_send_message(self.target_bot, "/start")
                except Exception:
                    pass
                await asyncio.sleep(5.0)

    async def run(self):
        while not self.is_stopped:
            try:
                if self.is_stopped:
                    break
                await self.run_automation_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.is_stopped:
                    break
                log("WORKER-RECOVER", f"Worker {self.instance_name} recovered from exception: {e}. Restarting in 5s...", instance_name=self.instance_name)
                await asyncio.sleep(5.0)


# ==============================================================================
# 🏁 MAIN ENTRYPOINT (PARALLEL EXECUTION WITHOUT PLAYWRIGHT)
# ==============================================================================
async def main_async():
    devices = get_unique_devices()
    if not devices:
        log("ERR", "No active ADB emulator device found! Please ensure LDPlayer / Emulator is running.")
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
    if num_to_run < len(devices):
        log("WARN", f"Found {len(devices)} device(s) but only {len(jobs)} Telegram account(s). Running {num_to_run} worker(s).")

    # 1. Start Telegram clients
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

    # 2. Build workers
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

    # 3. Launch workers concurrently
    log("INIT", f"🔥 Launching {len(workers)} worker(s) simultaneously (Pure ADB + Via Browser, No Playwright)...")
    
    worker_tasks = [asyncio.create_task(worker.run()) for worker in workers]
    await asyncio.gather(*worker_tasks, return_exceptions=True)

def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log("MAIN", "Process stopped by user.")

if __name__ == "__main__":
    main()
