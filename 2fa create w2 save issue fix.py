import subprocess
import time
import shutil
import os
import sys
import re
import csv
import queue
import threading
import xml.etree.ElementTree as ET
import urllib3
import pyotp
import hashlib
import requests
import imaplib
import email as email_lib
import ssl
from argon2.low_level import hash_secret_raw, Type

# Email Service Integrations
import mail_td
import tempmail_plus

urllib3.disable_warnings()

# Thread Synchronization & Claim Tracking
csv_lock = threading.Lock()
claimed_emails_lock = threading.Lock()
claimed_emails_local = set()
processed_emails_local = set()

from google_sheet_manager import gsheet_manager

# Configuration & Paths
PACKAGE_NAME = "mark.via.gp"
TARGET_URL = "https://www.instagram.com/accounts/login"
TWO_FACTOR_URL = "https://accountscenter.instagram.com/password_and_security/two_factor/"
META_ACC_CSV = os.path.join(os.path.dirname(__file__), "MetaAcc.csv")
CREDENTIALS_CSV = os.path.join(os.path.dirname(__file__), "credentials.csv")
CREDENTIALS_TXT = os.path.join(os.path.dirname(__file__), "credentials.txt")

# cPanel API & Domain Configuration (Fallback)
CPANEL_HOST = "https://admiredentalbristol.com:2083"
CPANEL_USER = "admirebristol"
API_TOKEN = "O7EKK7GDGS1TCBYZC6AFOXPG63SUP6ZW"
CPANEL_DOMAIN = "admiredentalbristol.com"

def find_adb():
    """Find adb executable path on the system"""
    adb_path = shutil.which("adb")
    if adb_path:
        return adb_path
    
    possible_paths = [
        r"D:\LDPlayer\LDPlayer14\adb.exe",
        r"C:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
        os.path.expanduser(r"~\Desktop\scrcpy-win64-v4.0\adb.exe"),
        os.path.expanduser(r"~\AppData\Local\Android\Sdk\platform-tools\adb.exe"),
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return "adb"

ADB_PATH = find_adb()

def log(tag, message, instance_name=""):
    """Clean, formatted, and readable console logger with aligned headers"""
    timestamp = time.strftime("%H:%M:%S")
    inst_tag = f"[{instance_name:^8}]" if instance_name else "[  MAIN  ]"
    tag_padded = f"[{tag:<14}]"
    log_str = f"[{timestamp}] {inst_tag} {tag_padded} {message}"
    try:
        print(log_str, flush=True)
    except UnicodeEncodeError:
        safe_str = log_str.encode(sys.stdout.encoding or 'ascii', errors='replace').decode(sys.stdout.encoding or 'ascii')
        print(safe_str, flush=True)

def get_unique_devices():
    """Scan ADB ports and return list of unique active connected device serials"""
    ports = [5555, 5554, 5556, 5558, 5560, 5562, 5564, 5566, 62001, 62025, 62026]
    for p in ports:
        try:
            subprocess.run(f'"{ADB_PATH}" connect 127.0.0.1:{p}', shell=True, capture_output=True, timeout=2)
        except Exception:
            pass
            
    res = subprocess.run(f'"{ADB_PATH}" devices', shell=True, capture_output=True, encoding='utf-8', errors='ignore')
    lines = res.stdout.strip().splitlines()
    raw_devices = []
    for line in lines[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            raw_devices.append(parts[0])
            
    unique_devices = []
    for dev in raw_devices:
        if "127.0.0.1:" in dev:
            unique_devices.append(dev)
        elif dev.startswith("emulator-"):
            port_num = int(dev.split("-")[1])
            ip_equivalent = f"127.0.0.1:{port_num + 1}"
            if ip_equivalent not in unique_devices:
                unique_devices.append(dev)

    if not unique_devices and raw_devices:
        unique_devices = raw_devices
        
    return unique_devices

def run_adb_command(args, device_serial=None, instance_name=""):
    """Run ADB command safely targeting specific device_serial if provided"""
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
        if result.returncode != 0 and result.stderr:
            stderr_msg = result.stderr.strip()
            if not any(ign in stderr_msg for ign in ["already connected", "daemon started", "UiAutomationService", "killProcess"]):
                log("ADB-WARN", f"{stderr_msg}", instance_name=instance_name)
        return result.stdout.strip() if result.stdout else ""
    except Exception as e:
        log("ADB-ERR", f"Execution error: {e}", instance_name=instance_name)
        return ""

def is_keyboard_visible(device_serial=None, instance_name=""):
    """Check if soft keyboard is currently visible on screen via dumpsys"""
    out = run_adb_command("shell dumpsys input_method", device_serial=device_serial, instance_name=instance_name)
    return "mInputShown=true" in out or "mHasSurface=true" in out

def clear_input_field(length=30, device_serial=None, instance_name=""):
    """Clear existing text safely without triggering browser back navigation"""
    if is_keyboard_visible(device_serial=device_serial, instance_name=instance_name):
        run_adb_command("shell input keyevent 123", device_serial=device_serial, instance_name=instance_name)
        del_keys = " ".join(["67"] * length)
        run_adb_command(f"shell input keyevent {del_keys}", device_serial=device_serial, instance_name=instance_name)

def type_text(text, device_serial=None, instance_name=""):
    """Type text safely into active focused input field via clean ADB shell escaping"""
    if not text:
        return
    escaped = text.replace(' ', '%s').replace('&', r'\&').replace('<', r'\<').replace('>', r'\>').replace('"', r'\"').replace("'", r"\'")
    run_adb_command(f'shell input text "{escaped}"', device_serial=device_serial, instance_name=instance_name)

def hide_keyboard(device_serial=None, instance_name=""):
    """Dismiss soft keyboard safely ONLY if it is currently visible"""
    if is_keyboard_visible(device_serial=device_serial, instance_name=instance_name):
        run_adb_command("shell input keyevent 111", device_serial=device_serial, instance_name=instance_name)
        time.sleep(0.4)

def get_screen_hierarchy(device_serial=None, instance_name=""):
    """Dump current UI hierarchy XML using uiautomator and return parsed XML root"""
    time.sleep(0.4)
    safe_ser = device_serial.replace(':', '_').replace('.', '_').replace('-', '_') if device_serial else "default"
    dump_path = f"/sdcard/window_dump_{safe_ser}.xml"
    run_adb_command(f"shell uiautomator dump {dump_path}", device_serial=device_serial, instance_name=instance_name)
    xml_str = run_adb_command(f"shell cat {dump_path}", device_serial=device_serial, instance_name=instance_name)
    if not xml_str or not xml_str.startswith("<?xml"):
        return None
    try:
        return ET.fromstring(xml_str)
    except Exception as e:
        log("XML-ERR", f"Failed to parse UI XML dump: {e}", instance_name=instance_name)
        return None

def extract_screen_texts(node):
    """Recursively extract all visible non-empty texts and descriptions from UI hierarchy"""
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

def parse_bounds(bounds_str):
    """Parse bounds string '[left,top][right,bottom]' -> (center_x, center_y)"""
    match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
    if match:
        left, top, right, bottom = map(int, match.groups())
        cx, cy = (left + right) // 2, (top + bottom) // 2
        if cx > 10 and cy > 10:
            return cx, cy
    return None

def get_screen_dimensions(root):
    """Get active screen width and height from UI XML root bounds"""
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
    """Calculate absolute screen coordinates (x, y) from relative screen ratios (0.0 - 1.0)"""
    w, h = get_screen_dimensions(root)
    return int(w * rx), int(h * ry)

def find_password_field(root):
    """Find password EditText input field coordinates accurately"""
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

def find_element(root, search_pattern, by_class=None, exact=False):
    """Search for UI element matching text/desc/resource-id, returning (center_x, center_y, text, bounds)"""
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
    """Find element dynamically by text, or fallback to screen-ratio relative coordinates"""
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
    """Reload active webpage using Via browser's top-right reload button (↻)"""
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
    """Generate 6-digit TOTP code using pyotp or fallback HMAC-SHA1"""
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
    
    try:
        import base64
        import hashlib
        import hmac
        import struct
        missing_padding = len(clean_secret) % 8
        if missing_padding:
            clean_secret += '=' * (8 - missing_padding)
        key = base64.b32decode(clean_secret, casefold=True)
        counter = int(time.time() // 30)
        msg = struct.pack(">Q", counter)
        h = hmac.new(key, msg, hashlib.sha1).digest()
        offset = h[-1] & 0x0F
        code = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000
        return f"{code:06d}"
    except Exception:
        return ""

def derive_auth_key(address: str, password: str) -> str:
    """Derive Argon2 ID auth key for Mail.td account login / message polling"""
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

def extract_meta_security_code(text, mail_from="", subj=""):
    """Extract 6 to 8-digit security / confirmation code from Meta email body or subject"""
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    clean_text = re.sub(r'&[a-z0-9#]+;', ' ', clean_text)
    clean_text = ' '.join(clean_text.split())

    # Priority 1: Match 6-8 digits specifically following confirm / modify settings / identity patterns
    patterns = [
        r'(?:confirm\s+your\s+identity|confirmation\s+code|security\s+code|following\s+code\s+to\s+confirm|use\s+the\s+following\s+code|code\s+is)[\s:\n]*\b(\d{6,8})\b',
        r'\b(\d{6,8})\b[\s:\n]*(?:is\s+your\s+code|to\s+confirm\s+your\s+identity|is\s+your\s+security)',
        r'(?:confirm\s+your\s+identity|settings\s+on\s+your\s+meta\s+account|modify\s+settings)[^\d]{1,120}\b(\d{6,8})\b',
        r'(?:code|confirm|security|verify)[^\d]{1,50}\b(\d{6,8})\b'
    ]
    for pat in patterns:
        m = re.search(pat, clean_text, re.IGNORECASE)
        if m:
            return m.group(1)

    # Priority 2: Match any standalone 6-8 digit number in the email
    digits_match = re.findall(r'\b(\d{6,8})\b', clean_text)
    for code in digits_match:
        if code not in ["94025", "127375"]:  # ignore zip code / support ids
            return code

    return None

def fetch_otp_from_mail_td_service(email, password, max_wait_sec=60, instance_name=""):
    """Log in to Mail.td and poll inbox for Meta security code using mail_td module"""
    client = mail_td.MailTDClient()
    try:
        data = client.login(email, password)
        acc_id = data.get("id")
        jwt_token = data.get("token")
        if acc_id and jwt_token:
            token_str = f"MAILTD:{acc_id}:{jwt_token}"
            return mail_td.fetch_otp_mail_td(token_str, max_wait_sec=max_wait_sec, instance_name=instance_name)
    except Exception as e:
        log("MAILTD-WARN", f"Mail.td login/poll error for {email}: {e}", instance_name=instance_name)
    return None

def fetch_otp_from_imap(email, password, max_wait_sec=60, instance_name=""):
    """Poll cPanel / IMAP server for Meta confirmation code"""
    start_time = time.time()
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
                                            try:
                                                body += part.get_payload(decode=True).decode('utf-8', errors='ignore') + " "
                                            except Exception:
                                                pass
                                else:
                                    try:
                                        body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                                    except Exception:
                                        body = str(msg.get_payload())
                                code = extract_meta_security_code(body, mail_from=from_addr, subj=subj)
                                if code:
                                    log("EMAIL-CODE", f"Found Meta security code from IMAP: {code}", instance_name=instance_name)
                                    mail.logout()
                                    return code
            mail.logout()
        except Exception:
            pass
        time.sleep(4)
    return None

def fetch_email_security_code(email, password, max_wait_sec=60, instance_name=""):
    """
    Universal email security code retriever:
    Intelligently routes to TempMail.plus, Mail.td, or cPanel IMAP based on email domain with full multi-service fallback.
    """
    domain = email.split('@')[1].lower().strip() if '@' in email else ""
    log("EMAIL-CODE", f"Fetching security code for {email} (@{domain}, timeout {max_wait_sec}s)...", instance_name=instance_name)

    # 1. TempMail.plus domains (mailto.plus, fexpost.com, fexbox.org, mailbox.in.ua, rover.info, chitthi.in, fextemp.com, any.pink, merepost.com)
    if domain in tempmail_plus.DOMAINS or any(d in domain for d in ["plus", "fex", "mailbox", "rover", "chitthi", "merepost", "pink"]):
        log("EMAIL-ROUTE", f"Routing {email} to TempMail.plus service...", instance_name=instance_name)
        code = tempmail_plus.fetch_otp_tempmail_plus(email, max_wait_sec=max_wait_sec, instance_name=instance_name)
        if code:
            return code

    # 2. Mail.td domains (qabq.com, uuf.me, 6n9.net, 6n9.com, sugtbt.com, nqmo.com, end.tw, end.com, mail.td)
    known_mailtd = {"mail.td", "qabq.com", "uuf.me", "6n9.net", "6n9.com", "sugtbt.com", "nqmo.com", "end.tw", "end.com"}
    if domain in known_mailtd:
        log("EMAIL-ROUTE", f"Routing {email} to Mail.td service...", instance_name=instance_name)
        code = fetch_otp_from_mail_td_service(email, password, max_wait_sec=max_wait_sec, instance_name=instance_name)
        if code:
            return code

    # 3. cPanel / Custom IMAP domains
    if domain == CPANEL_DOMAIN or "admire" in domain:
        log("EMAIL-ROUTE", f"Routing {email} to cPanel IMAP...", instance_name=instance_name)
        code = fetch_otp_from_imap(email, password, max_wait_sec=max_wait_sec, instance_name=instance_name)
        if code:
            return code

    # 4. Universal Fallback: If domain was not recognized, try Mail.td first, then TempMail.plus, then IMAP
    log("EMAIL-FALLBACK", f"Domain '@{domain}' not strictly mapped. Trying all services in sequence...", instance_name=instance_name)
    code = fetch_otp_from_mail_td_service(email, password, max_wait_sec=min(max_wait_sec, 30), instance_name=instance_name)
    if code:
        return code

    code = tempmail_plus.fetch_otp_tempmail_plus(email, max_wait_sec=min(max_wait_sec, 25), instance_name=instance_name)
    if code:
        return code

    code = fetch_otp_from_imap(email, password, max_wait_sec=min(max_wait_sec, 20), instance_name=instance_name)
    if code:
        return code

    log("EMAIL-TIMEOUT", f"Timed out waiting for security code across all email services for {email}.", instance_name=instance_name)
    return None

def extract_2fa_secret_and_copy(root, device_serial=None, instance_name=""):
    """
    Robustly extract 2FA secret key from UI hierarchy texts,
    tap 'Copy key' button, and read Android clipboard as confirmation.
    """
    secret_key = ""
    w, h = get_screen_dimensions(root)

    def find_key_in_texts(texts_list):
        ignore_words = {
            "AUTHENTICATION", "INSTRUCTIONS", "SETTINGSONYOUR", "TWOFACTORAUTH",
            "INSTAGRAMFROM", "CHANGEPASSWORD", "PASSWORDANDSEC", "ACCOUNTSETTINGS",
            "PERSONALDETAILS", "LOGGEDINWITH", "DOWNLOADANAPP", "TWOFACTORAUTHENTICATION"
        }
        # 1. Single node match (32, 24, 16 chars)
        for t in texts_list:
            cleaned = re.sub(r'[^A-Z0-9]', '', t.upper())
            if len(cleaned) in [32, 24, 16] and not cleaned.isdigit():
                if not any(ign in cleaned for ign in ignore_words):
                    return cleaned

        # 2. Match groups of 4
        blob = " ".join(texts_list).upper()
        # 8 groups of 4 (32 chars)
        m = re.search(r'([A-Z0-9]{4}(?:[\s\n\-]+[A-Z0-9]{4}){7})', blob)
        if m:
            c = re.sub(r'[^A-Z0-9]', '', m.group(1))
            if len(c) == 32 and not c.isdigit():
                return c

        # 4 groups of 4 (16 chars)
        m = re.search(r'([A-Z0-9]{4}(?:[\s\n\-]+[A-Z0-9]{4}){3})', blob)
        if m:
            c = re.sub(r'[^A-Z0-9]', '', m.group(1))
            if len(c) == 16 and not c.isdigit():
                return c

        # 3. Consecutive 4-char words in list
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

    # 1. Try extracting key directly from current screen text/hierarchy
    if root is not None:
        texts = extract_screen_texts(root)
        found = find_key_in_texts(texts)
        if found:
            secret_key = found
            log("2FA-EXTRACT", f"Found Secret Key in UI text: {secret_key}", instance_name=instance_name)

    # 2. Find and tap 'Copy key' button
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

    # 3. If still not found, re-check XML post-scroll
    if not secret_key and root is not None:
        texts = extract_screen_texts(root)
        found = find_key_in_texts(texts)
        if found:
            secret_key = found
            log("2FA-EXTRACT", f"Found Secret Key in UI text (Post-scroll): {secret_key}", instance_name=instance_name)

    return secret_key

def find_instagram_2fa_account_tile(root, username=""):
    """Accurately locate the Instagram account row on 'Choose an account to set up two-factor authentication' page"""
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

def detect_current_page(root):
    """Inspect screen text and identify the current page / state"""
    if root is None:
        return "UNKNOWN_SCREEN", []
    
    texts = extract_screen_texts(root)
    text_blob = " ".join(texts).lower()
    
    if "can't find account" in text_blob or "cant find account" in text_blob or "we can't find an account" in text_blob or "we cant find an account" in text_blob:
        return "CANNOT_FIND_ACCOUNT_POPUP", texts
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
    elif "check your email" in text_blob or ("enter the code we sent to" in text_blob and "continue" in text_blob):
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
    elif "username, email or mobile number" in text_blob or ("forgot password?" in text_blob and "log in" in text_blob) or ("phone number, username, or email" in text_blob):
        return "INSTAGRAM_LOGIN_PAGE", texts
    elif "share everyday moments" in text_blob or "open instagram" in text_blob:
        return "INSTAGRAM_LANDING_PAGE", texts
    elif "welcome" in text_blob and ("agree" in text_blob or "terms" in text_blob):
        return "BROWSER_TERMS_PAGE", texts
    elif "turn on notifications" in text_blob or "know when people follow you" in text_blob:
        return "INSTAGRAM_NOTIFICATIONS_PAGE", texts
    else:
        return "GENERIC_PAGE", texts

def extract_instagram_username(root):
    """Extract Instagram username from screen UI hierarchy dump or text blob"""
    if root is None:
        return ""
    
    texts = extract_screen_texts(root)
    blob = " ".join(texts)
    m = re.search(r"login info for\s+([a-zA-Z0-9_\.]+)", blob, re.IGNORECASE)
    if m:
        return m.group(1).strip()
        
    ignore_words = {"site", "info", "instagram", "reload", "save", "info?", "login", "profile", "photo", "next", "create", "username", "home", "menu", "not", "now", "back", "forward", "cancel", "tabs", "tab", "add", "change"}
    for node in root.iter('node'):
        cls = node.attrib.get('class', '')
        txt = node.attrib.get('text', '').strip() or node.attrib.get('content-desc', '').strip()
        if txt and " " not in txt and len(txt) >= 3:
            if txt.lower() not in ignore_words and not txt.isdigit():
                if 'edittext' in cls.lower() or 'textview' in cls.lower():
                    return txt
                    
    for txt in texts:
        txt_clean = txt.strip()
        if txt_clean and " " not in txt_clean and len(txt_clean) >= 3 and txt_clean.lower() not in ignore_words and not txt_clean.isdigit():
            return txt_clean

    return ""

def get_android_clipboard(device_serial=None, instance_name=""):
    """Retrieve text from Android device clipboard via dumpsys / service call / cmd clipboard"""
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

def find_instagram_account_tile(root, email=""):
    """Accurately locate the account card / tile on 'No Instagram profile found' page (Center X = 0.50w, Y = 0.85h)"""
    if root is None:
        return get_rel_coords(root, 0.50, 0.85)
        
    w, h = get_screen_dimensions(root)
    email_user = email.split('@')[0].lower() if email and '@' in email else ""
    
    for node in root.iter('node'):
        txt = node.attrib.get('text', '').strip()
        desc = node.attrib.get('content-desc', '').strip()
        t_lower = (txt + " " + desc).lower()
        if '@' in t_lower or 'horizon' in t_lower or (email_user and email_user in t_lower) or any(k in t_lower for k in ["meta", "wombat", "qabq", "admiredental", "sugtbt", "uuf", "6n9", "end", "nqmo"]):
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and coords[1] >= int(h * 0.72):
                return coords

    for node in root.iter('node'):
        coords = parse_bounds(node.attrib.get('bounds', ''))
        if coords and int(w * 0.10) <= coords[0] <= int(w * 0.90) and int(h * 0.76) <= coords[1] <= int(h * 0.92):
            return coords

    return get_rel_coords(root, 0.50, 0.85)

def is_account_already_in_csv(email):
    """Check if email already exists in credentials.csv to prevent duplicates"""
    if not os.path.exists(CREDENTIALS_CSV):
        return False
    clean_em = email.strip().lower()
    with csv_lock:
        try:
            with open(CREDENTIALS_CSV, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)
                for r in reader:
                    if len(r) > 1 and r[1].strip().lower() == clean_em:
                        return True
        except Exception:
            pass
    return False

def save_credentials_csv(email, password, two_factor_key, username="", instance_name=""):
    """Save account data directly and exclusively to Google Sheet Sheet2 (100% Online mode)"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        if gsheet_manager.is_configured():
            saved = gsheet_manager.save_done_account(timestamp, email, username, password, two_factor_key, instance_name=instance_name)
            if saved:
                log("GSHEET-SUCCESS", f">>> ONLINE SYNC: Account '{email}' saved to Google Sheet ('{gsheet_manager.done_sheet_name}') <<<", instance_name=instance_name)
        else:
            log("GSHEET-ERR", "Google Sheet not configured! Please provide spreadsheet_id in config.json.", instance_name=instance_name)
    except Exception as e:
        log("GSHEET-ERR", f"Failed saving to Google Sheet Sheet2: {e}", instance_name=instance_name)

def init_meta_csv():
    """Ensure Google Sheet is connected for 100% online operations"""
    if gsheet_manager.is_configured():
        ok, msg = gsheet_manager.connect()
        if ok:
            log("INIT-GSHEET", f"100% Online Google Sheet mode active ({gsheet_manager.mode}). Input: '{gsheet_manager.input_sheet_name}', Output: '{gsheet_manager.done_sheet_name}'.")
            return 1
        else:
            log("GSHEET-ERR", f"Google Sheet connection failed: {msg}")
            return 0
    else:
        log("GSHEET-ERR", "Google Sheet not configured! Please provide Google Sheet URL in config.json under 'spreadsheet_id'.")
        return 0

def get_next_free_meta_account(instance_name=""):
    """
    Find the next available free account from Google Sheet (Sheet1 Col B & Col D),
    atomically mark as 'running' in Col E, and return (email, password, index, total_count).
    100% Online mode.
    """
    if gsheet_manager.is_configured():
        try:
            acc = gsheet_manager.get_next_free_account(instance_name=instance_name)
            if acc:
                with claimed_emails_lock:
                    claimed_emails_local.add(acc[0].strip().lower())
                log("PICK", f"Picked free account: {acc[0]} (Row {acc[2]}/{acc[3]}) -> Marked 'running' in Sheet1 (Col E)", instance_name=instance_name)
                return acc
        except Exception as e:
            log("GSHEET-ERR", f"Failed picking account from Google Sheet: {e}", instance_name=instance_name)

    return None

def update_meta_account_status(email, status, reason="", instance_name="", row_idx=None):
    """Update account status (Col E) and reason (Col F) in Google Sheet Sheet1 (100% Online mode)"""
    clean_em = email.strip().lower() if email else ""
    with claimed_emails_lock:
        claimed_emails_local.discard(clean_em)
        if status == "done":
            processed_emails_local.add(clean_em)

    if gsheet_manager.is_configured():
        try:
            gsheet_manager.update_account_status(row_idx, email, status, reason=reason, instance_name=instance_name)
            detail_msg = f" | Reason: {reason}" if reason and status == "failed" else ""
            log("STATUS-UPD", f"Account '{email}' -> Status: '{status}' (Col E){detail_msg} (Col F) in Sheet1", instance_name=instance_name)
        except Exception as e:
            log("GSHEET-ERR", f"Failed updating status in Google Sheet: {e}", instance_name=instance_name)

def process_single_account(device_serial, instance_name, email, password, acc_index, total_count):
    """
    Process a single Meta account to Instagram 2FA creation on the specified device.
    Returns tuple: (success: bool, failure_reason: str, username: str, two_factor_secret: str)
    """
    log("ACC-START", f"[{acc_index}/{total_count}] Processing Account: {email}...", instance_name=instance_name)

    # 1. Clean Via Browser State
    log("STEP-1", f"Cleaning Via Browser data ({PACKAGE_NAME})...", instance_name=instance_name)
    run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.8)
    run_adb_command(f"shell pm clear {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.2)

    # 2. Open instagram.com directly
    log("STEP-2", f"Launching '{TARGET_URL}' in browser...", instance_name=instance_name)
    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
    time.sleep(3.5)

    # State tracking variables
    login_submitted = False
    login_wait_count = 0
    agree_terms_submitted = False
    agree_retry_count = 0
    captured_username = ""
    two_factor_secret = ""
    otp_submitted = False   # True only after TOTP code has been typed + Next tapped
    max_loops = 50
    loop_count = 0

    last_page_name = ""
    page_enter_time = time.time()
    page_stuck_back_count = 0

    log("STATE-LOOP", f"Starting UI state machine for {email}...", instance_name=instance_name)

    while loop_count < max_loops:
        loop_count += 1
        root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
        page_name, visible_texts = detect_current_page(root)
        current_time = time.time()

        if page_name != last_page_name:
            last_page_name = page_name
            page_enter_time = current_time
            page_stuck_back_count = 0
            login_wait_count = 0
            if page_name not in ["INSTAGRAM_AGREE_TERMS_PAGE", "INSTAGRAM_ALLOW_CONTINUE_PAGE", "INSTAGRAM_USERNAME_PAGE"]:
                agree_retry_count = 0
            sample = visible_texts[:3] if visible_texts else ["Empty UI dump"]
            log("PAGE-CHANGE", f"[Loop {loop_count}/{max_loops}] Detected: '{page_name}' | Preview: {sample}", instance_name=instance_name)
        else:
            stuck_duration = current_time - page_enter_time

            # Special handling for INSTAGRAM_AGREE_TERMS_PAGE:
            # If stuck for >= 30s after clicking 'I agree', do NOT reload the page.
            # Instead, press Back and repeat the process from previous step for exactly 1 retry.
            if page_name == "INSTAGRAM_AGREE_TERMS_PAGE":
                if stuck_duration >= 30.0:
                    agree_retry_count += 1
                    if agree_retry_count <= 1:
                        log("TIMEOUT-WARN", f"Screen '{page_name}' stuck for {int(stuck_duration)}s after 'I agree'. Pressing Back to repeat process (1 retry attempt)...", instance_name=instance_name)
                        run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)
                        agree_terms_submitted = False
                        last_page_name = ""
                        page_enter_time = time.time()
                        time.sleep(2.0)
                        continue
                    else:
                        failure_reason = "STUCK_TERMS_AGREE_AFTER_RETRY"
                        log("TIMEOUT-ABORT", f"Screen '{page_name}' still stuck after retry (>30s). Marking failed in sheet and moving to next account...", instance_name=instance_name)
                        save_credentials(email, password, f"FAILED: {failure_reason}", instance_name=instance_name)
                        return False, failure_reason, captured_username, ""
                elif agree_terms_submitted:
                    time.sleep(0.5)
                    continue

            elif stuck_duration >= 30.0:
                page_stuck_back_count += 1
                if page_name in ["BROWSER_TERMS_PAGE", "INSTAGRAM_LANDING_PAGE", "INSTAGRAM_LOGIN_PAGE", "GENERIC_PAGE", "UNKNOWN_SCREEN"]:
                    log("TIMEOUT-WARN", f"Screen '{page_name}' unresponsive ({int(stuck_duration)}s). Reloading page...", instance_name=instance_name)
                    reload_browser_page(root=root, device_serial=device_serial, instance_name=instance_name)
                    if page_name == "INSTAGRAM_LOGIN_PAGE":
                        login_submitted = False
                        login_wait_count = 0
                else:
                    log("TIMEOUT-WARN", f"Screen '{page_name}' unresponsive ({int(stuck_duration)}s). Pressing Back...", instance_name=instance_name)
                    run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)

                page_enter_time = time.time()
                time.sleep(2.0)

                if page_stuck_back_count >= 2:
                    failure_reason = f"Stuck on {page_name} >60s"
                    log("TIMEOUT-ABORT", f"{failure_reason}. Aborting account...", instance_name=instance_name)
                    save_credentials(email, password, f"FAILED: {failure_reason}", instance_name=instance_name)
                    return False, failure_reason, captured_username, ""

        # --- State Handlers ---
        if page_name == "BROWSER_TERMS_PAGE":
            log("STATE-ACTION", "Browser terms page -> Tapping AGREE...", instance_name=instance_name)
            tap_or_fallback_rel(root, "agree", 0.5, 0.88, "Agree Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(2)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "HOME_SCREEN":
            log("STATE-ACTION", "Home screen detected -> Re-opening instagram.com...", instance_name=instance_name)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "INSTAGRAM_LANDING_PAGE":
            log("STATE-ACTION", "Instagram landing page -> Tapping 'Log in'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "log in", 0.5, 0.74, "Log In Link", device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "INSTAGRAM_LOGIN_PAGE":
            if not login_submitted:
                log("STATE-ACTION", f"Entering email '{email}' and password...", instance_name=instance_name)
                elem_user = find_element(root, "username", by_class="EditText") or find_element(root, "email", by_class="EditText") or find_element(root, "phone", by_class="EditText")
                if elem_user:
                    run_adb_command(f"shell input tap {elem_user[0]} {elem_user[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    fx, fy = get_rel_coords(root, 0.5, 0.46)
                    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.8)
                clear_input_field(device_serial=device_serial, instance_name=instance_name)
                type_text(email, device_serial=device_serial, instance_name=instance_name)
                time.sleep(1.2)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                elem_pwd = find_password_field(root) or find_element(root, "password", by_class="EditText")
                if elem_pwd:
                    run_adb_command(f"shell input tap {elem_pwd[0]} {elem_pwd[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    fx, fy = get_rel_coords(root, 0.5, 0.58)
                    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.8)
                clear_input_field(device_serial=device_serial, instance_name=instance_name)
                type_text(password, device_serial=device_serial, instance_name=instance_name)
                time.sleep(1.2)

                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.8)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                log("STATE-ACTION", "Submitting login credentials...", instance_name=instance_name)
                tap_or_fallback_rel(root, "log in", 0.5, 0.67, "Log In Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                login_submitted = True
                login_wait_count = 0
                time.sleep(4.5)
            else:
                login_wait_count += 1
                if login_wait_count >= 3:
                    log("STATE-ACTION", f"Login submission pending after {login_wait_count} checks. Reloading...", instance_name=instance_name)
                    reload_browser_page(root=root, device_serial=device_serial, instance_name=instance_name)
                    login_submitted = False
                    login_wait_count = 0
                    time.sleep(4.5)
                else:
                    log("STATE-WAIT", f"Waiting for post-login response ({login_wait_count}/3)...", instance_name=instance_name)
                    time.sleep(2.5)

        elif page_name == "INSTAGRAM_SAVE_PASSWORD_POPUP":
            log("STATE-ACTION", "Save password popup -> Tapping 'Cancel'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "cancel", 0.5, 0.65, "Cancel Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(2.5)

        elif page_name == "INSTAGRAM_NO_ACCOUNT_FOUND":
            log("STATE-ACTION", "No Instagram profile overlay -> Scrolling to Meta account tile...", instance_name=instance_name)
            w, h = get_screen_dimensions(root)
            run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.80)} {int(w*0.50)} {int(h*0.25)} 300", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.2)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            tile_coords = find_instagram_account_tile(root, email)
            if tile_coords and tile_coords[1] > int(h * 0.60):
                log("STATE-ACTION", f"Meta account card found at {tile_coords[:2]}. Tapping...", instance_name=instance_name)
                run_adb_command(f"shell input tap {tile_coords[0]} {tile_coords[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                bx, by = int(w * 0.50), int(h * 0.82)
                log("STATE-ACTION", f"Tapping fallback bottom account box ({bx}, {by})...", instance_name=instance_name)
                run_adb_command(f"shell input tap {bx} {by}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.4)
                run_adb_command(f"shell input tap {bx} {by}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "INSTAGRAM_NAME_PAGE":
            log("STATE-ACTION", "'What\\'s your name?' screen -> Tapping 'Next'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button", device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "INSTAGRAM_USERNAME_PAGE":
            log("STATE-ACTION", "'Create a username' screen -> Extracting username & tapping 'Next'...", instance_name=instance_name)
            extracted_uname = extract_instagram_username(root)
            if extracted_uname:
                captured_username = extracted_uname
                log("USERNAME-FOUND", f"Extracted Username: '{captured_username}'", instance_name=instance_name)
            tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button", device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "INSTAGRAM_ALLOW_CONTINUE_PAGE":
            log("STATE-ACTION", "'Allow & Continue' screen -> Scrolling and confirming...", instance_name=instance_name)
            w, h = get_screen_dimensions(root)
            run_adb_command(f"shell input swipe {int(w*0.5)} {int(h*0.75)} {int(w*0.5)} {int(h*0.45)} 450", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)
            
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            tap_or_fallback_rel(root, "allow and continue", 0.5, 0.80, "Allow & Continue Button", device_serial=device_serial, instance_name=instance_name)
            time.sleep(4.5)

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
                time.sleep(3.0)

        elif page_name in ["INSTAGRAM_SAVE_INFO_PAGE", "INSTAGRAM_CONNECT_FB_PAGE", "INSTAGRAM_NOTIFICATIONS_PAGE"]:
            if not captured_username:
                extracted_uname = extract_instagram_username(root)
                if extracted_uname:
                    captured_username = extracted_uname
                    log("USERNAME-FOUND", f"Extracted Username: '{captured_username}'", instance_name=instance_name)
            log("STATE-ACTION", f"Logged in successfully! Navigating to 2FA Settings URL: {TWO_FACTOR_URL}...", instance_name=instance_name)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TWO_FACTOR_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(4.5)

        elif page_name == "TWO_FACTOR_CHOOSE_ACCOUNT_PAGE":
            log("STATE-ACTION", "2FA: 'Choose an account' -> Selecting Instagram account tile...", instance_name=instance_name)
            tile_coords = find_instagram_2fa_account_tile(root, captured_username)
            if tile_coords:
                run_adb_command(f"shell input tap {tile_coords[0]} {tile_coords[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.49)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "TWO_FACTOR_EXTRA_PROTECTION_PAGE":
            log("STATE-ACTION", "2FA: 'Set up extra protection for your account' -> Tapping 'Get started'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "get started", 0.50, 0.86, "Get Started Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(4.0)

        elif page_name == "TWO_FACTOR_CHECK_EMAIL_PAGE":
            log("STATE-ACTION", f"2FA: 'Check your email' -> Fetching security code from inbox for {email}...", instance_name=instance_name)
            # Re-dump the fresh root AFTER the email code is fetched (page may have timed out/changed)
            email_code = fetch_email_security_code(email, password, max_wait_sec=90, instance_name=instance_name)
            if not email_code:
                failure_reason = "Email security code not received"
                log("FAILED", f"✖ {failure_reason} for {email}", instance_name=instance_name)
                save_credentials(email, password, f"FAILED: {failure_reason}", instance_name=instance_name)
                return False, failure_reason, captured_username, ""

            # Refresh hierarchy — the page may have changed while waiting for email
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            refreshed_page, _ = detect_current_page(root)
            if refreshed_page == "TWO_FACTOR_SUCCESS_PAGE":
                log("SUCCESS", f"✔ 2FA already succeeded after email code wait for {email}! (User: '{captured_username}')", instance_name=instance_name)
                save_credentials_csv(email, password, two_factor_secret, username=captured_username, instance_name=instance_name)
                save_credentials(email, password, f"2FA_ACTIVE | Secret: {two_factor_secret} | Username: {captured_username}", username=captured_username, instance_name=instance_name)
                return True, "", captured_username, two_factor_secret

            log("STATE-ACTION", f"Entering email security code '{email_code}'...", instance_name=instance_name)
            # Try multiple selectors for the code input field
            elem_code_input = (find_element(root, "enter code", by_class="EditText")
                               or find_element(root, "code", by_class="EditText")
                               or find_element(root, "enter the code", by_class="EditText")
                               or find_element(root, "code")
                               or find_password_field(root))
            if elem_code_input:
                run_adb_command(f"shell input tap {elem_code_input[0]} {elem_code_input[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.46)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)
            clear_input_field(device_serial=device_serial, instance_name=instance_name)
            type_text(email_code, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.2)

            hide_keyboard(device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)

            # Re-read root after code entry — button text may be "Continue" or "Next"
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            log("STATE-ACTION", "Tapping 'Continue'/'Next' after submitting email code...", instance_name=instance_name)
            btn_found = (find_element(root, "continue", exact=True)
                         or find_element(root, "next", exact=True)
                         or find_element(root, "continue")
                         or find_element(root, "next"))
            if btn_found:
                run_adb_command(f"shell input tap {btn_found[0]} {btn_found[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.62)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(4.5)

        elif page_name == "TWO_FACTOR_CHOOSE_METHOD_PAGE":
            log("STATE-ACTION", "2FA: 'Help protect your account' -> Tapping 'Continue'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "continue", 0.50, 0.86, "Continue Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "TWO_FACTOR_INSTRUCTIONS_PAGE":
            log("STATE-ACTION", "2FA: 'Instructions for setup' -> Scrolling down to reveal key & extracting...", instance_name=instance_name)
            w, h = get_screen_dimensions(root)

            # Step 1: Scroll down to reveal the text key area below the QR code
            run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.70)} {int(w*0.50)} {int(h*0.35)} 350",
                            device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

            # Step 2: Re-dump hierarchy after scroll to get the text key in view
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)

            # Step 3: Extract & copy the 2FA secret key
            extracted_key = extract_2fa_secret_and_copy(root, device_serial=device_serial, instance_name=instance_name)
            if extracted_key:
                two_factor_secret = extracted_key
                otp_preview = generate_totp_code(two_factor_secret)
                log("2FA-CAPTURED", f"Secret Key: '{two_factor_secret}' | OTP Code: '{otp_preview}'", instance_name=instance_name)
            else:
                # Fallback: read from clipboard (Copy key was tapped inside extract_2fa_secret_and_copy)
                clip = get_android_clipboard(device_serial=device_serial, instance_name=instance_name)
                if clip:
                    clean_clip = re.sub(r'[^A-Z0-9]', '', clip.upper())
                    if len(clean_clip) >= 16 and not clean_clip.isdigit():
                        two_factor_secret = clean_clip
                        log("2FA-CLIPBOARD", f"Secret Key recovered from clipboard: '{two_factor_secret}'", instance_name=instance_name)

            # Step 4: Scroll back up slightly so the "Enter code" button is visible
            run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.40)} {int(w*0.50)} {int(h*0.65)} 250",
                            device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.0)

            # Step 5: Re-read hierarchy and tap "Enter code" button
            # Screenshot confirms: this page uses "Enter code" button (NOT "Next")
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
                # Fallback: "Enter code" button is at ~86% height on this page
                fx, fy = get_rel_coords(root, 0.50, 0.86)
                log("2FA-ACTION", f"Tapping fallback 'Enter code' position at ({fx},{fy})...", instance_name=instance_name)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "TWO_FACTOR_ENTER_CODE_PAGE":
            log("STATE-ACTION", "2FA: 'Enter the 6-digit code' -> Generating OTP & submitting...", instance_name=instance_name)
            if not two_factor_secret:
                clip = get_android_clipboard(device_serial=device_serial, instance_name=instance_name)
                if clip:
                    clean_clip = re.sub(r'[^A-Z0-9]', '', clip.upper())
                    if len(clean_clip) >= 16 and not clean_clip.isdigit():
                        two_factor_secret = clean_clip

            elem_input = find_element(root, "enter code", by_class="EditText") or find_element(root, "code", by_class="EditText") or find_password_field(root)
            if elem_input:
                run_adb_command(f"shell input tap {elem_input[0]} {elem_input[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.55)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)
            clear_input_field(device_serial=device_serial, instance_name=instance_name)

            if not two_factor_secret or len(two_factor_secret) < 16:
                run_adb_command("shell input keyevent 279", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.5)
                pasted_root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                if pasted_root is not None:
                    for node in pasted_root.iter('node'):
                        t_val = (node.attrib.get('text', '') or node.attrib.get('content-desc', '')).strip()
                        c_val = re.sub(r'[^A-Z0-9]', '', t_val.upper())
                        if len(c_val) >= 16 and not c_val.isdigit():
                            two_factor_secret = c_val
                            log("2FA-CAPTURED", f"Recovered 2FA Secret Key via Paste: '{two_factor_secret}'", instance_name=instance_name)
                            break
                clear_input_field(device_serial=device_serial, instance_name=instance_name)

            otp_code = generate_totp_code(two_factor_secret)
            log("2FA-OTP", f"Typing OTP '{otp_code}' for secret '{two_factor_secret}'...", instance_name=instance_name)
            if otp_code:
                type_text(otp_code, device_serial=device_serial, instance_name=instance_name)
                time.sleep(1.2)
                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.8)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            tap_or_fallback_rel(root, "next", 0.50, 0.82, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            otp_submitted = True   # Mark OTP as submitted — success detection is now allowed
            time.sleep(4.5)

            # Immediately check for success page after OTP submission (before next loop iteration)
            root_after_otp = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            if root_after_otp is not None:
                otp_post_texts = extract_screen_texts(root_after_otp)
                otp_post_blob = " ".join(otp_post_texts).lower()
                # Only use explicit confirmed success phrases here (no ambiguous combos)
                otp_success_keywords = [
                    "two-factor authentication is on",
                    "authentication app is on",
                    "two-step verification is on",
                    "authenticator app is on",
                    "two-factor is on",
                    "two-factor authentication turned on",
                    "two-factor authentication enabled",
                    "two-factor authentication is set up",
                    "your two-factor authentication is set up",
                ]
                if any(kw in otp_post_blob for kw in otp_success_keywords):
                    log("SUCCESS", f"✔ 2FA Setup Completed (detected post-OTP) for {email}! Secret: {two_factor_secret} (User: '{captured_username}')", instance_name=instance_name)
                    save_credentials_csv(email, password, two_factor_secret, username=captured_username, instance_name=instance_name)
                    save_credentials(email, password, f"2FA_ACTIVE | Secret: {two_factor_secret} | Username: {captured_username}", username=captured_username, instance_name=instance_name)
                    return True, "", captured_username, two_factor_secret

        elif page_name == "TWO_FACTOR_SUCCESS_PAGE":
            log("SUCCESS", f"✔ 2FA Setup Completed Successfully for {email}! Secret: {two_factor_secret} (User: '{captured_username}')", instance_name=instance_name)
            save_credentials_csv(email, password, two_factor_secret, username=captured_username, instance_name=instance_name)
            save_credentials(email, password, f"2FA_ACTIVE | Secret: {two_factor_secret} | Username: {captured_username}", username=captured_username, instance_name=instance_name)
            time.sleep(1.5)
            return True, "", captured_username, two_factor_secret

        elif page_name == "CANNOT_FIND_ACCOUNT_POPUP":
            reason = "Can't find account popup"
            log("FAILED", f"✖ Login Failed: {reason} for {email}", instance_name=instance_name)
            save_credentials(email, password, f"FAILED: {reason}", instance_name=instance_name)
            return False, reason, captured_username, ""

        elif page_name == "INCORRECT_PASSWORD_POPUP":
            reason = "Incorrect password"
            log("FAILED", f"✖ Login Failed: {reason} for {email}", instance_name=instance_name)
            save_credentials(email, password, f"FAILED: {reason}", instance_name=instance_name)
            return False, reason, captured_username, ""

        elif page_name == "ACCOUNT_ERROR_PAGE":
            reason = "Something went wrong / Unable to create account"
            log("FAILED", f"✖ Instagram Error: {reason} for {email}", instance_name=instance_name)
            save_credentials(email, password, f"FAILED: {reason}", instance_name=instance_name)
            return False, reason, captured_username, ""

        else:
            # Safety: check if a success screen is showing under a GENERIC_PAGE classification.
            # IMPORTANT: Only allow this AFTER the OTP has been submitted (otp_submitted flag).
            if otp_submitted and visible_texts:
                generic_blob = " ".join(visible_texts).lower()
                # Use only explicit confirmed success phrases — no ambiguous keyword combos
                late_success_keywords = [
                    "two-factor authentication is on",
                    "authentication app is on",
                    "two-step verification is on",
                    "authenticator app is on",
                    "two-factor is on",
                    "two-factor authentication turned on",
                    "two-factor authentication enabled",
                    "two-factor authentication is set up",
                    "your two-factor authentication is set up",
                ]
                if any(kw in generic_blob for kw in late_success_keywords):
                    log("SUCCESS", f"✔ 2FA Setup Completed (late-detected on generic screen) for {email}! Secret: {two_factor_secret} (User: '{captured_username}')", instance_name=instance_name)
                    save_credentials_csv(email, password, two_factor_secret, username=captured_username, instance_name=instance_name)
                    save_credentials(email, password, f"2FA_ACTIVE | Secret: {two_factor_secret} | Username: {captured_username}", username=captured_username, instance_name=instance_name)
                    return True, "", captured_username, two_factor_secret

            if len(visible_texts) <= 5:
                log("STATE-WAIT", f"Incomplete/Blank page ({visible_texts}). Pressing Back...", instance_name=instance_name)
                run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)
                time.sleep(2.0)
            else:
                log("STATE-WAIT", f"Waiting on generic screen '{page_name}'...", instance_name=instance_name)
                time.sleep(2.5)

    limit_reason = "Loop limit reached (Automation Timeout)"
    log("FAILED", f"✖ {limit_reason} for {email}.", instance_name=instance_name)
    save_credentials(email, password, f"FAILED: {limit_reason}", instance_name=instance_name)
    return False, limit_reason, captured_username, ""

def worker_instance(device_serial, instance_name, stop_event=None):
    """Worker thread picking free accounts dynamically from Google Sheet or MetaAcc.csv on an emulator instance"""
    log("INIT", f"Worker thread online for {instance_name} ({device_serial}).", instance_name=instance_name)
    processed_count = 0
    poll_interval_seconds = 8

    while True:
        if stop_event and stop_event.is_set():
            break

        acc_info = get_next_free_meta_account(instance_name=instance_name)
        if not acc_info:
            log("WAIT-ACCS", f"No pending accounts available. Checking again in {poll_interval_seconds}s...", instance_name=instance_name)
            for _ in range(poll_interval_seconds):
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1.0)
            continue

        email, password, current_idx, total_accs = acc_info
        processed_count += 1

        success = False
        reason = ""
        captured_uname = ""
        secret = ""

        try:
            success, reason, captured_uname, secret = process_single_account(
                device_serial, instance_name, email, password, current_idx, total_accs
            )
        except Exception as e:
            reason = f"Exception: {str(e)[:40]}"
            log("ERR", f"Unexpected worker error on {email}: {e}", instance_name=instance_name)
            success = False
        finally:
            final_status = "done" if success else "failed"
            # Write final status in Col E and detailed failure reason in Col F
            update_meta_account_status(
                email, 
                final_status, 
                reason=reason if not success else "SUCCESS", 
                instance_name=instance_name, 
                row_idx=current_idx
            )

        time.sleep(2.5)

    log("FINISH", f"Worker {instance_name} stopped. Total processed: {processed_count}", instance_name=instance_name)

def main():
    log("INIT", "==================================================================")
    log("INIT", "    Meta-to-Instagram 2FA Creator (Smart Parallel Engine)        ")
    log("INIT", "==================================================================")

    # 1. Initialize Sheet / CSV headers (Col E = Status, Col F = Reason)
    init_meta_csv()

    # 2. Detect Active Connected Devices
    devices = get_unique_devices()
    if not devices:
        log("ERR", "No active ADB emulator instances found! Please ensure LDPlayer emulators are running.")
        return

    log("INIT", f"Detected {len(devices)} active emulator instance(s): {devices}")

    # 3. Spawn Parallel Multi-Instance Worker Threads
    stop_event = threading.Event()
    threads = []
    for idx, device in enumerate(devices, 1):
        inst_name = f"Inst-{idx}"
        log("LAUNCH", f"Spawning worker thread for {inst_name} ({device})...")
        t = threading.Thread(target=worker_instance, args=(device, inst_name, stop_event), daemon=True)
        threads.append(t)
        t.start()
        time.sleep(2.0)

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1.0)
    except KeyboardInterrupt:
        log("SHUTDOWN", "Shutdown requested (Ctrl+C). Stopping all instances gracefully...")
        stop_event.set()
        for t in threads:
            t.join(timeout=4.0)

    log("COMPLETED", "All worker tasks finished.")

if __name__ == "__main__":
    main()
