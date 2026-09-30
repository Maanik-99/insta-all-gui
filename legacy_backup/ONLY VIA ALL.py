import subprocess
import time
import shutil
import os
import sys
import re
import urllib.request
import json
import random
import string
import ssl
import xml.etree.ElementTree as ET
import imaplib
import email as email_lib
import requests
import urllib3
import csv
import hashlib
import secrets

try:
    from NINJA import TempMailNinjaAPI
except ImportError:
    try:
        from ninja import TempMailNinjaAPI
    except ImportError:
        TempMailNinjaAPI = None

urllib3.disable_warnings()

# Configuration
PACKAGE_NAME = "mark.via.gp"
TARGET_URL = "https://www.meta.ai/api/oidc/start?waterfall_id=44d8eaaf-4ea5-43dc-addf-94ab370c4864"

# cPanel API & Domain Configuration
CPANEL_HOST = "https://admiredentalbristol.com:2083"
CPANEL_USER = "admirebristol"
API_TOKEN = "O7EKK7GDGS1TCBYZC6AFOXPG63SUP6ZW"
CPANEL_DOMAIN = "admiredentalbristol.com"
LAST_CREATED_EMAIL_FILE = os.path.join(os.path.dirname(__file__), "last_email.json")

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
TARGET_DEVICE = None

def log(tag, message):
    """Structured console logger"""
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [{tag}] {message}", flush=True)

def is_emulator(device_id):
    """
    Check if a device serial belongs to an emulator and NOT a physical USB phone.
    Emulators have serials like 'emulator-5554', '127.0.0.1:5555', 'localhost:5555', etc.
    Physical USB devices have hardware serial numbers without port numbers (e.g. 'RKG6ORMZPFXGAE8X').
    """
    if not device_id:
        return False
    dev = str(device_id).strip()
    dev_lower = dev.lower()

    # Explicit exclusion for known USB physical devices (e.g. phone used for airplane mode / mobile IP rotation)
    if dev.upper() == "RKG6ORMZPFXGAE8X" or "rkg6ormzpfxgae8x" in dev_lower:
        return False

    # Standard Android Studio / AOSP / LDPlayer emulator serials (e.g. emulator-5554)
    if dev_lower.startswith("emulator-"):
        return True

    # Local IP / loopback TCP connections used by LDPlayer, MuMu, Nox, MEmu, BlueStacks
    if (dev_lower.startswith("127.0.0.1:") or 
        dev_lower.startswith("localhost:") or 
        dev_lower.startswith("127.") or 
        dev_lower.startswith("10.0.2.") or
        dev_lower.startswith("0.0.0.0:")):
        return True

    # General IP:port format check where port is an integer
    if ":" in dev:
        parts = dev.split(":")
        if len(parts) == 2 and parts[1].isdigit():
            return True

    # Any other device without ':' or 'emulator-' is a physical USB device (e.g. RKG6ORMZPFXGAE8X)
    return False

def get_target_device():
    """Get the active connected emulator ID, strictly ignoring any USB physical devices."""
    global TARGET_DEVICE
    if TARGET_DEVICE and is_emulator(TARGET_DEVICE):
        return TARGET_DEVICE
        
    try:
        res = subprocess.run(f'"{ADB_PATH}" devices', shell=True, capture_output=True, encoding='utf-8', errors='ignore')
        lines = res.stdout.strip().splitlines()
        emulators = []
        ignored_usb = []
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                dev = parts[0]
                if is_emulator(dev):
                    emulators.append(dev)
                else:
                    ignored_usb.append(dev)
                    
        if ignored_usb:
            for usb_dev in ignored_usb:
                log("ADB-FILTER", f"Ignoring physical USB device: '{usb_dev}' (Emulators only policy)")
                
        if emulators:
            # Prefer standard port 5555 or emulator-5554 if present, else first available emulator
            if "127.0.0.1:5555" in emulators:
                TARGET_DEVICE = "127.0.0.1:5555"
            elif "emulator-5554" in emulators:
                TARGET_DEVICE = "emulator-5554"
            else:
                TARGET_DEVICE = emulators[0]
            return TARGET_DEVICE
    except Exception as e:
        log("ADB-ERR", f"Error listing devices: {e}")
    return None

def run_adb_command(args):
    """Run ADB command safely with utf-8 encoding"""
    if isinstance(args, str):
        if args.startswith("adb "):
            args = args[4:]
        
        cmd_type = args.split()[0] if args.split() else ""
        device = get_target_device()
        if device and cmd_type not in ["connect", "disconnect", "devices"]:
            cmd = f'"{ADB_PATH}" -s {device} {args}'
        else:
            cmd = f'"{ADB_PATH}" {args}'
    else:
        cmd = [ADB_PATH] + args

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, encoding='utf-8', errors='ignore')
        if result.returncode != 0 and result.stderr:
            stderr_msg = result.stderr.strip()
            if not any(ign in stderr_msg for ign in ["already connected", "daemon started", "UiAutomationService", "killProcess"]):
                log("ADB-WARN", f"{stderr_msg}")
        return result.stdout.strip() if result.stdout else ""
    except Exception as e:
        log("ADB-ERR", f"Execution error: {e}")
        return ""

def clear_input_field(length=3):
    """Clear existing text safely without triggering browser back navigation"""
    if is_keyboard_visible():
        run_adb_command("shell input keyevent 123")  # Move to end
        del_keys = " ".join(["67"] * length)          # Send backspaces safely
        run_adb_command(f"shell input keyevent {del_keys}")

def type_text(text):
    """Type text safely into active focused input field via clean ADB shell escaping"""
    escaped = text.replace(' ', '%s').replace('&', r'\&').replace('<', r'\<').replace('>', r'\>').replace('"', r'\"').replace("'", r"\'")
    run_adb_command(f'shell input text "{escaped}"')

def is_keyboard_visible():
    """Check if soft keyboard is currently visible on screen via dumpsys"""
    out = run_adb_command("shell dumpsys input_method")
    return "mInputShown=true" in out or "mHasSurface=true" in out

def hide_keyboard():
    """Dismiss soft keyboard safely ONLY if it is currently visible"""
    if is_keyboard_visible():
        log("ACTION", "Soft keyboard detected as OPEN. Hiding keyboard safely via KEYCODE_ESCAPE...")
        run_adb_command("shell input keyevent 111")
        time.sleep(0.5)
    else:
        log("ACTION", "Keyboard is already CLOSED. Skipping ESCAPE keyevent to prevent browser minimize.")

def get_screen_hierarchy():
    """Dump current UI hierarchy XML using uiautomator and return parsed XML root"""
    time.sleep(0.4)  # Small pause to avoid uiautomator collision
    run_adb_command("shell uiautomator dump /sdcard/window_dump.xml")
    xml_str = run_adb_command("shell cat /sdcard/window_dump.xml")
    if not xml_str or not xml_str.startswith("<?xml"):
        return None
    try:
        return ET.fromstring(xml_str)
    except Exception as e:
        log("XML-ERR", f"Failed to parse UI XML dump: {e}")
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

def find_otp_field(root):
    """Accurately find OTP confirmation code input field coordinates while strictly excluding 'Send code again' buttons"""
    if root is None:
        return get_rel_coords(root, 0.50, 0.435)
        
    w, h = get_screen_dimensions(root)
    
    def is_invalid_otp_node(text_str):
        t = text_str.lower()
        return any(bad in t for bad in ["send code", "again", "resend", "continue", "edit", "already", "look for", "notification"])

    # Search 1: EditText or input class node in OTP area (Y between 0.32h and 0.54h)
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

    # Search 2: Node with exact text/desc 'code' or 'confirmation code' or '6-digit code' (Y between 0.32h and 0.54h)
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

    # Search 3: Any focusable/clickable element in OTP box Y-range (0.35h to 0.52h)
    for node in root.iter('node'):
        txt = node.attrib.get('text', '').strip()
        desc = node.attrib.get('content-desc', '').strip()
        if is_invalid_otp_node(txt) or is_invalid_otp_node(desc):
            continue
        clickable = node.attrib.get('clickable', '').lower() == 'true'
        focusable = node.attrib.get('focusable', '').lower() == 'true'
        if clickable or focusable:
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and int(w * 0.15) <= coords[0] <= int(w * 0.85) and int(h * 0.35) <= coords[1] <= int(h * 0.52):
                return coords

    # Default fallback: OTP Code box center is at X=0.50w, Y=0.435h
    return get_rel_coords(root, 0.50, 0.435)

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
                # Ignore elements in system bottom navigation bar (cy > 91%) or top browser toolbar (cy < 10%)
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

# SSL Context for temp mail API requests
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def detect_current_page(root):
    """Inspect screen text and identify the current page / state"""
    if root is None:
        return "UNKNOWN_SCREEN", []
    
    texts = extract_screen_texts(root)
    text_blob = " ".join(texts).lower()
    
    # Page rules (Order matters!)
    if "home screen" in text_blob or "system apps" in text_blob or "ldstore" in text_blob or "search games" in text_blob:
        return "HOME_SCREEN", texts
    elif "something went wrong" in text_blob or "unable to create an account" in text_blob:
        return "ACCOUNT_ERROR_PAGE", texts
    elif "view cookies" in text_blob or "view certificate" in text_blob or "site configuration" in text_blob:
        return "VIA_SITE_INFO_POPUP", texts
    elif "copy" in text_blob and ("cookies" in text_blob or "site configuration" in text_blob or "view certificate" in text_blob):
        return "VIA_COOKIES_DIALOG", texts
    elif "save your login info?" in text_blob or "save your login info" in text_blob or "won't need to enter it next time" in text_blob or "save info" in text_blob:
        return "INSTAGRAM_SAVE_INFO_PAGE", texts
    elif "connect to facebook" in text_blob or "find friends to follow" in text_blob:
        return "INSTAGRAM_CONNECT_FB_PAGE", texts
    elif "save password on" in text_blob or ("save password" in text_blob and "cancel" in text_blob):
        return "INSTAGRAM_SAVE_PASSWORD_POPUP", texts
    elif "no instagram account found" in text_blob or "matched an existing meta account" in text_blob:
        return "INSTAGRAM_NO_ACCOUNT_FOUND", texts
    elif "what's your name" in text_blob or "whats your name" in text_blob:
        return "INSTAGRAM_NAME_PAGE", texts
    elif "create a username" in text_blob or "create username" in text_blob:
        return "INSTAGRAM_USERNAME_PAGE", texts
    elif "allow the following" in text_blob or "create an instagram account with your meta account" in text_blob or "allow and continue" in text_blob:
        return "INSTAGRAM_ALLOW_CONTINUE_PAGE", texts
    elif "username, email or mobile number" in text_blob or ("forgot password?" in text_blob and "log in" in text_blob):
        return "INSTAGRAM_LOGIN_PAGE", texts
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
    elif "allow the use of cookies from meta" in text_blob or "allow all cookies" in text_blob:
        return "META_COOKIES_DIALOG", texts
    elif "log in or create an account" in text_blob or "continue with facebook" in text_blob or "continue with instagram" in text_blob:
        return "META_LANDING_PAGE", texts
    elif "facebook – log in" in text_blob or "log in to facebook" in text_blob or "facebook - log in" in text_blob:
        return "FACEBOOK_LOGIN_PAGE", texts
    elif "enter password" in text_blob or "enter password instead" in text_blob:
        return "PASSWORD_INPUT_PAGE", texts
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

def tap_or_fallback_rel(root, search_text, rel_x, rel_y, label_name="element", exact=False):
    """Find element dynamically by text, or fallback to screen-ratio relative coordinates"""
    elem = find_element(root, search_text, exact=exact)
    if elem:
        x, y, found_label, bounds = elem
        log("ACTION", f"Found UI element '{found_label}' at {bounds}. Tapping center ({x}, {y})...")
        run_adb_command(f"shell input tap {x} {y}")
        return True
    else:
        fx, fy = get_rel_coords(root, rel_x, rel_y)
        log("ACTION-FALLBACK", f"'{search_text}' not explicitly found in XML dump. Tapping relative fallback ({fx}, {fy}) for {label_name}...")
        run_adb_command(f"shell input tap {fx} {fy}")
        return False

FIRST_NAMES = ["alex", "james", "john", "david", "michael", "emily", "sarah", "daniel", "chris", "ryan", "matt", "jessica", "andrew", "kevin", "brian", "jason", "luke", "sam", "nathan", "brandon"]
LAST_NAMES = ["smith", "miller", "brown", "jones", "davis", "wilson", "taylor", "white", "clark", "hall", "allen", "young", "king", "wright", "scott", "green", "baker", "adams", "nelson", "hill"]

def delete_previous_cpanel_email():
    """Check last_email.json and delete the previously created cPanel email account before creating a new one"""
    if os.path.exists(LAST_CREATED_EMAIL_FILE):
        try:
            with open(LAST_CREATED_EMAIL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                old_email = data.get("email")
                if old_email:
                    log("CPANEL-DEL", f"Deleting previously created cPanel email: {old_email}...")
                    del_url = f"{CPANEL_HOST}/execute/Email/delete_pop"
                    res = cpanel_session.get(del_url, params={"email": old_email}, timeout=10)
                    log("CPANEL-DEL", f"Delete API response: {res.json()}")
        except Exception as e:
            log("CPANEL-DEL-WARN", f"Failed to delete old cPanel email: {e}")

def save_last_created_email(full_email):
    """Save the newly created cPanel email address to last_email.json"""
    try:
        with open(LAST_CREATED_EMAIL_FILE, "w", encoding="utf-8") as f:
            json.dump({"email": full_email, "timestamp": time.time()}, f, indent=2)
    except Exception as e:
        log("CPANEL-SAVE-WARN", f"Failed to save last email state: {e}")

def generate_temp_email():
    """Create a new email account using TempMail Ninja API (MAIN), with cPanel as FALLBACK"""
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    num = random.randint(1000, 9999)
    email_prefix = f"{first}{last}{num}"
    
    # Password must not exceed 8 characters (1 Upper, 4 lower, 2 digits, 1 symbol)
    pwd_letters = ''.join(random.choices(string.ascii_lowercase, k=4))
    pwd_digit = random.choice(string.digits)
    pwd_upper = random.choice(string.ascii_uppercase)
    pwd_symbol = random.choice("!@#$")
    password = f"{pwd_upper}{pwd_letters}{num}{pwd_symbol}"[:8]

    # --- MAIN: Try TempMail Ninja API ---
    log("TEMP-EMAIL", "Creating temporary email account on TempMail Ninja API (MAIN)...")
    if TempMailNinjaAPI is not None:
        try:
            ninja_client = TempMailNinjaAPI()
            ninja_client.connect()
            email_address = ninja_client.create_temp_email()
            if email_address:
                log("NINJA-SUCCESS", f"Successfully created TempMail Ninja email: {email_address}")
                return email_address, password, ninja_client
        except Exception as e:
            log("NINJA-WARN", f"TempMail Ninja creation failed ({e}). Falling back to cPanel...")
    else:
        log("NINJA-WARN", "TempMailNinjaAPI not imported. Falling back to cPanel...")

    # --- FALLBACK: Try cPanel API ---
    log("CPANEL-CREATE", "Using cPanel fallback email creation...")
    delete_previous_cpanel_email()
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
            log("CPANEL-SUCCESS", f"Successfully created cPanel email: {cpanel_full_email}")
            save_last_created_email(cpanel_full_email)
            return cpanel_full_email, password, f"CPANEL:{cpanel_full_email}:{password}"
        else:
            log("CPANEL-ERR", f"cPanel account creation API returned error: {res_json}")
    except Exception as e:
        log("CPANEL-ERR", f"Exception during cPanel email creation: {e}")
        
    return cpanel_full_email, password, f"CPANEL:{cpanel_full_email}:{password}"

def extract_otp_from_text(text, mail_from="", subj=""):
    """Extract 6-digit OTP code specifically from Meta email content while ignoring system emails"""
    mail_from_lower = mail_from.lower().strip()
    subj_lower = subj.lower().strip()
    combined = f"{mail_from_lower} {subj_lower} {text.lower()}".strip()
    
    if "welcome" in subj_lower and "meta" not in subj_lower:
        return None

    # Strip HTML tags & entities to turn HTML body into plain readable text
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    clean_text = re.sub(r'&[a-z0-9#]+;', ' ', clean_text)
    clean_text = ' '.join(clean_text.split())

    # Must be from Meta / Facebook or contain Meta confirmation keywords
    is_meta_email = any(k in combined for k in ["meta", "facebook", "instagram", "confirm your email", "confirmation code"])
    if not is_meta_email:
        return None

    # Priority 1: Match 6 digits directly following 'confirmation code', 'security code', 'code', etc.
    patterns = [
        r'(?:confirmation\s+code|security\s+code|confirm\s+code|your\s+code|code\s+is)[\s:\n]*\b(\d{6})\b',
        r'\b(\d{6})\b[\s:\n]*(?:is\s+your\s+code|is\s+your\s+confirmation)',
    ]
    for pat in patterns:
        match = re.search(pat, clean_text, re.IGNORECASE)
        if match:
            return match.group(1)
            
    # Priority 2: Match 6 digits ONLY if preceded within 50 chars by confirmation keywords
    match = re.search(r'(?:confirmation|code|security|verify|confirm)[^\d]{1,50}\b(\d{6})\b', clean_text, re.IGNORECASE)
    if match:
        return match.group(1)
            
    return None

def fetch_otp_from_temp_mail(email, password, token=None, max_wait_sec=60):
    """Fetch 6-digit OTP code from TempMail Ninja (MAIN) or cPanel (FALLBACK)"""
    log("OTP-FETCH", f"Polling inbox for {email} (timeout {max_wait_sec}s)...")
    start_time = time.time()

    # --- Option 1: TempMail Ninja Polling ---
    if TempMailNinjaAPI is not None and isinstance(token, TempMailNinjaAPI):
        try:
            otp_code = token.wait_for_otp(timeout=max_wait_sec, poll_interval=3)
            if otp_code:
                m = re.search(r'\b(\d{6})\b', str(otp_code))
                final_otp = m.group(1) if m else str(otp_code).strip()
                log("OTP-SUCCESS", f"Found Meta OTP code from TempMail Ninja: {final_otp}")
                return final_otp
        except Exception as e:
            log("OTP-WARN", f"Error during Ninja OTP wait: {e}")

    # --- Option 2: cPanel IMAP Polling (Fallback) ---
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
                                        content_type = part.get_content_type()
                                        if content_type in ["text/plain", "text/html"]:
                                            try:
                                                body += part.get_payload(decode=True).decode('utf-8', errors='ignore') + " "
                                            except Exception:
                                                pass
                                else:
                                    try:
                                        body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                                    except Exception:
                                        pass
                                
                                otp = extract_otp_from_text(f"{subj} {body}", mail_from=from_addr, subj=subj)
                                if otp:
                                    log("OTP-SUCCESS", f"Found Meta OTP code from cPanel IMAP: {otp}")
                                    mail.logout()
                                    return otp
            mail.logout()
        except Exception as e:
            log("OTP-WARN", f"cPanel IMAP connection/poll error ({e}). Retrying...")
            
        log("OTP-POLL", "OTP not received yet. Waiting 5s...")
        time.sleep(5)

    log("OTP-TIMEOUT", f"Timed out waiting for OTP email for {email}.")
    return None

def extract_instagram_username(root):
    """Extract Instagram username from screen UI hierarchy dump or text blob"""
    if root is None:
        return ""
    
    # 1. Search for regex pattern in text blob ("save the login info for <username>")
    texts = extract_screen_texts(root)
    blob = " ".join(texts)
    m = re.search(r"login info for\s+([a-zA-Z0-9_\.]+)", blob, re.IGNORECASE)
    if m:
        return m.group(1).strip()
        
    # 2. Search for EditText / TextView node containing username
    ignore_words = {"site", "info", "instagram", "reload", "save", "info?", "login", "profile", "photo", "next", "create", "username", "home", "menu", "not", "now", "back", "forward", "cancel", "tabs", "tab", "add", "change"}
    for node in root.iter('node'):
        cls = node.attrib.get('class', '')
        txt = node.attrib.get('text', '').strip() or node.attrib.get('content-desc', '').strip()
        if txt and " " not in txt and len(txt) >= 3:
            if txt.lower() not in ignore_words and not txt.isdigit():
                if 'edittext' in cls.lower() or 'textview' in cls.lower():
                    return txt
                    
    # 3. Fallback search across visible text tokens
    for txt in texts:
        txt_clean = txt.strip()
        if txt_clean and " " not in txt_clean and len(txt_clean) >= 3 and txt_clean.lower() not in ignore_words and not txt_clean.isdigit():
            return txt_clean

    return ""

def save_credentials(email, password, status="UNKNOWN", username=""):
    """Save generated credentials and username to credentials.txt"""
    cred_file = os.path.join(os.path.dirname(__file__), "credentials.txt")
    uname_str = f" | Username: {username}" if username else ""
    with open(cred_file, "a", encoding="utf-8") as f:
        f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')} | Email: {email} | Password: {password}{uname_str} | Status: {status}\n")
    log("SUCCESS", f"Credentials saved to '{cred_file}' (Username: '{username}', Status: {status})")

def save_credentials_csv(email, password, cookies, username="", status="REGISTRATION_SUCCESS"):
    """Save email, password, username, status, cookies, and timestamp into credentials.csv"""
    csv_file = os.path.join(os.path.dirname(__file__), "credentials.csv")
    file_exists = os.path.exists(csv_file)
    
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(csv_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Timestamp", "Email", "Password", "Username", "Status", "Cookies"])
            writer.writerow([timestamp, email, password, username, status, cookies])
        log("CSV-SUCCESS", f"Credentials, Username ('{username}') & cookies saved to '{csv_file}'")
    except Exception as e:
        log("CSV-ERR", f"Failed writing to credentials.csv ({e}). Saving fallback entry...")
        alt_csv = os.path.join(os.path.dirname(__file__), "credentials_export.csv")
        alt_exists = os.path.exists(alt_csv)
        try:
            with open(alt_csv, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not alt_exists:
                    writer.writerow(["Timestamp", "Email", "Password", "Username", "Status", "Cookies"])
                writer.writerow([timestamp, email, password, username, status, cookies])
            log("CSV-SUCCESS", f"Credentials & Username saved to fallback file '{alt_csv}'")
        except Exception:
            pass

def get_android_clipboard():
    """Retrieve text from Android device clipboard via dumpsys / service call / cmd clipboard"""
    try:
        out = run_adb_command("shell cmd clipboard get")
        if out and "Error" not in out and "exception" not in out.lower():
            return out.strip()
    except Exception:
        pass
        
    try:
        dump = run_adb_command("shell dumpsys clipboard")
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

def extract_cookies_from_screen_texts(texts):
    """Extract cookie values directly from visible UI hierarchy texts"""
    cookie_parts = []
    for t in texts:
        if "=" in t and (";" in t or "sessionid" in t.lower() or "csrftoken" in t.lower() or "mid=" in t.lower() or "datr=" in t.lower() or "ds_user_id=" in t.lower()):
            cookie_parts.append(t.strip())
    if cookie_parts:
        return " ".join(cookie_parts)
    return ""

def find_instagram_account_tile(root, email=""):
    """Accurately locate the account card / tile on 'No Instagram profile found' page (Center X = 0.50w, Y = 0.85h)"""
    if root is None:
        return get_rel_coords(root, 0.50, 0.85)
        
    w, h = get_screen_dimensions(root)
    email_user = email.split('@')[0].lower() if email and '@' in email else ""
    
    # Search 1: Node matching email, username, 'horizon', or account card text below Y=0.72h
    for node in root.iter('node'):
        txt = node.attrib.get('text', '').strip()
        desc = node.attrib.get('content-desc', '').strip()
        t_lower = (txt + " " + desc).lower()
        if '@' in t_lower or 'horizon' in t_lower or (email_user and email_user in t_lower) or any(k in t_lower for k in ["meta", "popik", "lat", "ninja", "admiredental", "wombat", "qabq", "horse", "penguin", "jellyfish"]):
            coords = parse_bounds(node.attrib.get('bounds', ''))
            if coords and coords[1] >= int(h * 0.72):
                return coords

    # Search 2: Clickable or focusable view node below 'OR' divider (Y between 0.76h and 0.92h)
    for node in root.iter('node'):
        coords = parse_bounds(node.attrib.get('bounds', ''))
        if coords and int(w * 0.10) <= coords[0] <= int(w * 0.90) and int(h * 0.76) <= coords[1] <= int(h * 0.92):
            return coords

    # Fallback coordinates: Center of the account card (X = 0.50w, Y = 0.85h)
    return get_rel_coords(root, 0.50, 0.85)

def find_month_dropdown_coords(root):
    """Find Month dropdown element coordinates on screen (dynamic for all page variants)"""
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
    """Find Day dropdown element coordinates on screen (dynamic for all page variants)"""
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
    """Find Year dropdown element coordinates on screen (dynamic for all page variants)"""
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
    """Accurately locate the blue Confirm button at the bottom of Edit Meta AI details page"""
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

def select_random_dob(root):
    """Randomly select Day (1-28), Month (Jan-Dec), and Year (1985-2004) adapting dynamically to layout variants"""
    w, h = get_screen_dimensions(root)
    
    # 1. Random Day (1 to 28)
    target_day = random.randint(1, 28)
    target_day_str = str(target_day)
    log("DOB-ACTION", f"Selecting Day: {target_day_str}...")
    
    day_coords = find_day_dropdown_coords(root)
    log("DOB-ACTION", f"Tapping Day dropdown at {day_coords}...")
    run_adb_command(f"shell input tap {day_coords[0]} {day_coords[1]}")
    time.sleep(1.2)
    
    day_root = get_screen_hierarchy()
    day_tapped = False
    if day_root is not None:
        for node in day_root.iter('node'):
            txt = node.attrib.get('text', '').strip() or node.attrib.get('content-desc', '').strip()
            if txt == target_day_str:
                coords = parse_bounds(node.attrib.get('bounds', ''))
                if coords and int(h * 0.25) <= coords[1] <= int(h * 0.75):
                    log("DOB-ACTION", f"Tapping Day {txt} at {coords}...")
                    run_adb_command(f"shell input tap {coords[0]} {coords[1]}")
                    day_tapped = True
                    break
                    
    if not day_tapped:
        if target_day > 10:
            run_adb_command(f"shell input swipe {day_coords[0]} {int(h*0.70)} {day_coords[0]} {int(h*0.35)} 350")
            time.sleep(0.8)
            day_root2 = get_screen_hierarchy()
            if day_root2 is not None:
                for node in day_root2.iter('node'):
                    txt = node.attrib.get('text', '').strip()
                    if txt == target_day_str:
                        coords = parse_bounds(node.attrib.get('bounds', ''))
                        if coords and int(h * 0.25) <= coords[1] <= int(h * 0.75):
                            log("DOB-ACTION", f"Tapping Day {txt} after scroll at {coords}...")
                            run_adb_command(f"shell input tap {coords[0]} {coords[1]}")
                            day_tapped = True
                            break
        if not day_tapped:
            tap_y = random.uniform(0.44, 0.58)
            run_adb_command(f"shell input tap {day_coords[0]} {int(h*tap_y)}")
    time.sleep(1.2)

    # 2. Random Month
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    target_month = random.choice(months)
    log("DOB-ACTION", f"Selecting Month: {target_month}...")
    
    month_coords = find_month_dropdown_coords(root)
    log("DOB-ACTION", f"Tapping Month dropdown at {month_coords}...")
    run_adb_command(f"shell input tap {month_coords[0]} {month_coords[1]}")
    time.sleep(1.2)
    
    month_popup_root = get_screen_hierarchy()
    month_tapped = False
    if month_popup_root is not None:
        for node in month_popup_root.iter('node'):
            txt = node.attrib.get('text', '').strip() or node.attrib.get('content-desc', '').strip()
            if txt.lower() == target_month.lower():
                coords = parse_bounds(node.attrib.get('bounds', ''))
                if coords and int(h * 0.25) <= coords[1] <= int(h * 0.75):
                    log("DOB-ACTION", f"Tapping Month {txt} at {coords}...")
                    run_adb_command(f"shell input tap {coords[0]} {coords[1]}")
                    month_tapped = True
                    break
                    
    if not month_tapped:
        if months.index(target_month) > 5:
            run_adb_command(f"shell input swipe {month_coords[0]} {int(h*0.70)} {month_coords[0]} {int(h*0.35)} 350")
            time.sleep(0.8)
            month_popup_root2 = get_screen_hierarchy()
            if month_popup_root2 is not None:
                for node in month_popup_root2.iter('node'):
                    txt = node.attrib.get('text', '').strip()
                    if txt.lower() == target_month.lower():
                        coords = parse_bounds(node.attrib.get('bounds', ''))
                        if coords and int(h * 0.25) <= coords[1] <= int(h * 0.75):
                            log("DOB-ACTION", f"Tapping Month {txt} after scroll at {coords}...")
                            run_adb_command(f"shell input tap {coords[0]} {coords[1]}")
                            month_tapped = True
                            break
        if not month_tapped:
            tap_y = random.uniform(0.44, 0.58)
            run_adb_command(f"shell input tap {month_coords[0]} {int(h*tap_y)}")
    time.sleep(1.2)

    # 3. Random Year (1985 - 2004)
    target_year = random.randint(1985, 2004)
    log("DOB-ACTION", f"Selecting Year: {target_year}...")
    yr_coords = find_year_dropdown_coords(root)
    log("DOB-ACTION", f"Tapping Year dropdown at {yr_coords}...")
    run_adb_command(f"shell input tap {yr_coords[0]} {yr_coords[1]}")
    time.sleep(1.2)

    # Reset focus cursor to top item (2026) using MOVE_HOME (keycode 122)
    run_adb_command("shell input keyevent 122")
    time.sleep(0.3)

    # Step DPAD_DOWN (2026 - target_year) times
    down_steps = 2026 - target_year
    log("DOB-ACTION", f"Stepping DPAD_DOWN {down_steps} steps to select Year {target_year}...")
    for _ in range(down_steps):
        run_adb_command("shell input keyevent 20")
    time.sleep(0.3)

    # Commit selection with ENTER + DPAD_CENTER
    run_adb_command("shell input keyevent 66")
    time.sleep(0.2)
    run_adb_command("shell input keyevent 23")
    time.sleep(0.8)

    # Ensure Year popup dialog is closed
    check_root = get_screen_hierarchy()
    if check_root is not None:
        check_texts = extract_screen_texts(check_root)
        check_blob = " ".join(check_texts).lower()
        if "select year" in check_blob:
            log("DOB-DISMISS", "Closing Year popup dialog...")
            run_adb_command(f"shell input tap {int(w*0.5)} {int(h*0.18)}")
            time.sleep(0.8)

    time.sleep(1.2)
    log("DOB-COMPLETE", f"DOB set: Day={target_day}, Month={target_month}, Year={target_year}")
    return target_year

def main():
    global TARGET_DEVICE
    log("INIT", "Starting Meta AI Registration Flow (meta.ai -> Sign up -> Email -> DOB & Password -> Confirm -> OTP Verification)...")
    
    # Check if user specified a target emulator via CLI argument
    if len(sys.argv) > 1:
        cli_arg = sys.argv[1].strip()
        if is_emulator(cli_arg):
            TARGET_DEVICE = cli_arg
            log("INIT", f"Using user-specified emulator from CLI argument: {TARGET_DEVICE}")
        else:
            log("WARN", f"Provided argument '{cli_arg}' is a physical USB device or invalid emulator serial. Ignored to protect USB device.")

    # 1. LDPlayer multi-port connect & auto-reconnect fallback
    log("STEP-1", "Scanning and connecting ADB to local emulator ports (5555, 5554, 5556, 16384, 16416, 62001, etc.)...")
    ports = [5555, 5554, 5556, 5558, 16384, 16416, 16448, 16480, 62001, 7555, 21503]
    for port in ports:
        run_adb_command(f"connect 127.0.0.1:{port}")
    time.sleep(2)

    target_device = get_target_device()
    if not target_device:
        log("STEP-1-RETRY", "No emulator found. Resetting ADB daemon and rescanning ports...")
        TARGET_DEVICE = None
        run_adb_command("kill-server")
        time.sleep(1)
        run_adb_command("start-server")
        time.sleep(1)
        for port in ports:
            run_adb_command(f"connect 127.0.0.1:{port}")
        time.sleep(2)
        target_device = get_target_device()

    if not target_device:
        log("ERROR", "No active connected emulator found via ADB! (Physical USB devices ignored). Please ensure LDPlayer or another Android emulator is running.")
        return
    log("STEP-1", f"Target emulator identified: {target_device} (All physical USB devices skipped)")

    # 2. Reset Browser Application State
    log("STEP-2", f"Resetting Via Browser state ({PACKAGE_NAME})...")
    run_adb_command(f"shell am force-stop {PACKAGE_NAME}")
    time.sleep(1)
    run_adb_command(f"shell pm clear {PACKAGE_NAME}")
    time.sleep(2)

    # 3. Launch Target Meta AI URL
    log("STEP-3", f"Opening '{TARGET_URL}' in Via Browser...")
    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}')
    time.sleep(4)

    # 4. Generate Temporary Email & Credentials
    email, strong_password, mail_token = generate_temp_email()

    # State Machine Variables
    landing_clicked = False
    landing_retry_count = 0
    email_submitted = False
    password_submitted = False
    dob_submitted = False
    details_confirmed = False
    otp_submitted = False
    insta_navigated = False
    captured_username = ""
    max_loops = 60
    loop_count = 0

    log("STATE-MACHINE", "Entering State Machine Execution Loop...")

    while loop_count < max_loops:
        loop_count += 1
        root = get_screen_hierarchy()
        page_name, visible_texts = detect_current_page(root)
        
        sample = visible_texts[:4] if visible_texts else ["No visible text"]
        log("STATE-LOOP", f"[Loop {loop_count}/{max_loops}] Active Page: '{page_name}' | Context: {sample}")

        if page_name == "ACCOUNT_ERROR_PAGE":
            log("STATE-WARN", "Account creation error dialog detected ('Something went wrong / Unable to create account'). Tapping OK / Close and regenerating email...")
            tap_or_fallback_rel(root, "ok", 0.5, 0.58, "OK Button", exact=True)
            time.sleep(2)
            log("STATE-ACTION", "Resetting browser and regenerating new temporary email...")
            run_adb_command(f"shell am force-stop {PACKAGE_NAME}")
            time.sleep(1)
            run_adb_command(f"shell pm clear {PACKAGE_NAME}")
            time.sleep(2)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}')
            time.sleep(4)
            if TempMailNinjaAPI is not None and isinstance(mail_token, TempMailNinjaAPI):
                try:
                    mail_token.disconnect()
                except Exception:
                    pass
            email, strong_password, mail_token = generate_temp_email()
            landing_clicked = False
            email_submitted = False
            password_submitted = False
            dob_submitted = False
            details_confirmed = False
            otp_submitted = False
            continue

        elif page_name == "HOME_SCREEN":
            log("STATE-ACTION", "Browser minimized or Home screen detected. Re-opening Via Browser...")
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}')
            time.sleep(4)

        elif page_name == "BROWSER_TERMS_PAGE":
            log("STATE-ACTION", "Browser terms screen detected. Tapping AGREE...")
            tap_or_fallback_rel(root, "agree", 0.5, 0.88, "Agree Button", exact=True)
            time.sleep(2)
            log("STATE-ACTION", f"Re-triggering Meta AI navigation URL ({TARGET_URL})...")
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}')
            time.sleep(4)

        elif page_name == "META_AI_HOME_PAGE":
            landing_clicked = False
            log("STATE-ACTION", "Meta AI Home page detected ('What can I do for you?'). Clicking 'Sign up' button...")
            elem_su = find_element(root, "sign up") or find_element(root, "log in")
            if elem_su:
                run_adb_command(f"shell input tap {elem_su[0]} {elem_su[1]}")
            else:
                fx, fy = get_rel_coords(root, 0.85, 0.17)
                log("ACTION-FALLBACK", f"Tapping relative fallback ({fx}, {fy}) for Sign up Button...")
                run_adb_command(f"shell input tap {fx} {fy}")
            time.sleep(4)

        elif page_name == "META_COOKIES_DIALOG":
            log("STATE-ACTION", "Meta cookies consent dialog detected. Tapping 'Allow all cookies' button...")
            tap_or_fallback_rel(root, "allow all cookies", 0.5, 0.77, "Allow All Cookies Button")
            time.sleep(3)

        elif page_name == "META_LANDING_PAGE":
            if not landing_clicked or landing_retry_count >= 2:
                log("STATE-ACTION", "Log in or create account page detected. Tapping 'Use mobile number or email address'...")
                tap_or_fallback_rel(root, "use mobile number or email address", 0.5, 0.68, "Use Email Option")
                landing_clicked = True
                landing_retry_count = 0
            else:
                landing_retry_count += 1
                log("STATE-WAIT", f"Already tapped 'Use mobile number or email address' (wait {landing_retry_count}/2)...")
            time.sleep(3)

        elif page_name == "EMAIL_INPUT_PAGE":
            text_blob = " ".join(visible_texts).lower()
            if "please enter a valid" in text_blob:
                log("STATE-WARN", "Validation error detected. Regenerating email...")
                if TempMailNinjaAPI is not None and isinstance(mail_token, TempMailNinjaAPI):
                    try:
                        mail_token.disconnect()
                    except Exception:
                        pass
                email, strong_password, mail_token = generate_temp_email()

            log("STATE-ACTION", "Focusing Email input field...")
            elem_email = find_element(root, "email", by_class="EditText") or find_element(root, "mobile", by_class="EditText")
            if elem_email:
                run_adb_command(f"shell input tap {elem_email[0]} {elem_email[1]}")
            else:
                fx, fy = get_rel_coords(root, 0.5, 0.435)
                run_adb_command(f"shell input tap {fx} {fy}")
            time.sleep(1)
            
            clear_input_field()
            log("STATE-ACTION", f"Typing email: {email}")
            type_text(email)
            time.sleep(2)
            
            root = get_screen_hierarchy()
            log("STATE-ACTION", "Clicking 'Continue' button...")
            tap_or_fallback_rel(root, "continue", 0.5, 0.617, "Continue Button")
            email_submitted = True
            time.sleep(5)

        elif page_name == "META_DOB_PASSWORD_PAGE":
            log("STATE-ACTION", "Get started on Meta AI page detected! Processing DOB & Password fields...")
            
            # Step 1: Select random Day, Month, and Year
            root = get_screen_hierarchy()
            log("DOB-ACTION", "Randomly selecting Day, Month, and Year...")
            select_random_dob(root)
            dob_submitted = True
            time.sleep(1.5)

            # Step 2: Focus Password field
            root = get_screen_hierarchy()
            pwd_coords = find_password_field(root)
            log("STATE-ACTION", f"Focusing Password input field at {pwd_coords}...")
            if pwd_coords:
                run_adb_command(f"shell input tap {pwd_coords[0]} {pwd_coords[1]}")
            else:
                fx, fy = get_rel_coords(root, 0.5, 0.69)
                run_adb_command(f"shell input tap {fx} {fy}")
            time.sleep(1.5)
            
            clear_input_field()
            log("STATE-ACTION", "Typing password...")
            type_text(strong_password)
            time.sleep(1.5)
            
            # Step 3: Dismiss soft keyboard ONLY if open so Next button is visible
            hide_keyboard()
            time.sleep(1.0)
            
            # Step 4: Check if Next button is visible; swipe page up ONCE ONLY IF Next is not showing
            root = get_screen_hierarchy()
            elem_next = find_element(root, "next")
            if not elem_next:
                log("STATE-ACTION", "Next button not visible on screen. Swiping page up once to reveal Next button...")
                run_adb_command("shell input swipe 270 650 270 350 400")
                time.sleep(1.5)
                root = get_screen_hierarchy()
            else:
                log("STATE-ACTION", "Next button is already visible on screen. No swipe needed.")
            
            log("STATE-ACTION", "Clicking 'Next' button after DOB & Password...")
            tap_or_fallback_rel(root, "next", 0.5, 0.82, "Next Button")
            password_submitted = True
            time.sleep(5)

        elif page_name == "META_CONFIRM_DETAILS_PAGE":
            if not details_confirmed:
                log("STATE-ACTION", "Edit Meta AI details page detected! Clicking 'Confirm' button...")
                conf_coords = find_confirm_button(root)
                log("STATE-ACTION", f"Tapping Confirm button at {conf_coords}...")
                run_adb_command(f"shell input tap {conf_coords[0]} {conf_coords[1]}")
                details_confirmed = True
                time.sleep(5)
            else:
                log("STATE-WAIT", "Details confirmed. Waiting for OTP screen...")
                time.sleep(3)

        elif page_name == "OTP_VERIFICATION_PAGE":
            if not otp_submitted:
                log("STATE-ACTION", "Enter confirmation code page detected! Fetching OTP from temp email...")
                otp_code = fetch_otp_from_temp_mail(email, strong_password, token=mail_token, max_wait_sec=60)
                
                if otp_code:
                    log("STATE-ACTION", f"Received OTP: {otp_code}. Focusing Confirmation code field...")
                    root = get_screen_hierarchy()
                    otp_coords = find_otp_field(root)
                    if otp_coords:
                        log("STATE-ACTION", f"Found OTP field at {otp_coords}. Tapping to focus...")
                        run_adb_command(f"shell input tap {otp_coords[0]} {otp_coords[1]}")
                        time.sleep(0.5)
                        run_adb_command(f"shell input tap {otp_coords[0]} {otp_coords[1]}")
                    else:
                        fx, fy = get_rel_coords(root, 0.5, 0.46)
                        log("ACTION-FALLBACK", f"Tapping OTP relative fallback ({fx}, {fy})...")
                        run_adb_command(f"shell input tap {fx} {fy}")
                        time.sleep(0.5)
                        run_adb_command(f"shell input tap {fx} {fy}")
                    time.sleep(1)
                    
                    clear_input_field(length=6)
                    time.sleep(0.5)
                    
                    log("STATE-ACTION", f"Typing OTP code '{otp_code}'...")
                    type_text(otp_code)
                    time.sleep(1)
                    
                    # Digit keyevents fallback for webview input compatibility
                    digit_keycodes = " ".join([str(int(d) + 7) for d in otp_code if d.isdigit()])
                    if digit_keycodes:
                        run_adb_command(f"shell input keyevent {digit_keycodes}")
                        time.sleep(1)
                    
                    hide_keyboard()
                    time.sleep(1)
                    
                    root = get_screen_hierarchy()
                    log("STATE-ACTION", "Clicking 'Next' / 'Continue' button after typing OTP...")
                    elem_next = find_element(root, "next") or find_element(root, "continue") or find_element(root, "confirm") or find_element(root, "submit")
                    if elem_next:
                        run_adb_command(f"shell input tap {elem_next[0]} {elem_next[1]}")
                    else:
                        tap_or_fallback_rel(root, "next", 0.5, 0.65, "Next Button")
                        
                    otp_submitted = True
                    save_credentials(email, strong_password, f"OTP_ENTERED_{otp_code}")
                    log("STATE-ACTION", "Waiting 5 sec after filling OTP, then opening instagram.com...")
                    time.sleep(5)
                    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "https://www.instagram.com/" {PACKAGE_NAME}')
                    insta_navigated = True
                    time.sleep(5)
                else:
                    log("STATE-WARN", "Failed to retrieve OTP code from temp mail.")
                    save_credentials(email, strong_password, "OTP_FETCH_FAILED")
                    break
            else:
                if not insta_navigated:
                    log("STATE-ACTION", "OTP submitted! Waiting 5 sec and opening instagram.com...")
                    time.sleep(5)
                    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "https://www.instagram.com/" {PACKAGE_NAME}')
                    insta_navigated = True
                    time.sleep(5)
                else:
                    log("STATE-WAIT", "Waiting for Instagram page to load...")
                    time.sleep(3)

        elif page_name == "INSTAGRAM_LANDING_PAGE":
            log("STATE-ACTION", "Instagram landing page detected. Clicking 'Log in' / 'Log in or sign up'...")
            tap_or_fallback_rel(root, "log in", 0.5, 0.74, "Log In Link")
            time.sleep(4)

        elif page_name == "INSTAGRAM_LOGIN_PAGE":
            log("STATE-ACTION", "Instagram login page detected. Entering Meta AI email and password...")
            
            # Step 1: Username / Email field
            elem_user = find_element(root, "username", by_class="EditText") or find_element(root, "email", by_class="EditText")
            if elem_user:
                run_adb_command(f"shell input tap {elem_user[0]} {elem_user[1]}")
            else:
                fx, fy = get_rel_coords(root, 0.5, 0.46)
                run_adb_command(f"shell input tap {fx} {fy}")
            time.sleep(1)
            clear_input_field()
            log("STATE-ACTION", f"Typing email: {email}")
            type_text(email)
            time.sleep(1.5)
            
            # Step 2: Password field
            root = get_screen_hierarchy()
            elem_pwd = find_password_field(root) or find_element(root, "password", by_class="EditText")
            if elem_pwd:
                run_adb_command(f"shell input tap {elem_pwd[0]} {elem_pwd[1]}")
            else:
                fx, fy = get_rel_coords(root, 0.5, 0.58)
                run_adb_command(f"shell input tap {fx} {fy}")
            time.sleep(1)
            clear_input_field()
            log("STATE-ACTION", "Typing password...")
            type_text(strong_password)
            time.sleep(1.5)
            
            # Step 3: Hide soft keyboard
            hide_keyboard()
            time.sleep(1)
            
            # Step 4: Click Log in button
            root = get_screen_hierarchy()
            log("STATE-ACTION", "Clicking 'Log in' button...")
            tap_or_fallback_rel(root, "log in", 0.5, 0.67, "Log In Button", exact=True)
            time.sleep(5)

        elif page_name == "INSTAGRAM_SAVE_PASSWORD_POPUP":
            log("STATE-ACTION", "Save password popup detected! Clicking 'Cancel'...")
            tap_or_fallback_rel(root, "cancel", 0.5, 0.65, "Cancel Button", exact=True)
            time.sleep(3)

        elif page_name == "INSTAGRAM_NO_ACCOUNT_FOUND":
            log("STATE-ACTION", "No Instagram account found page detected! Swiping down to reveal account tile...")
            w, h = get_screen_dimensions(root)
            run_adb_command(f"shell input swipe {int(w*0.5)} {int(h*0.75)} {int(w*0.5)} {int(h*0.48)} 450")
            time.sleep(2.5)
            
            root = get_screen_hierarchy()
            log("STATE-ACTION", "Clicking on shown Meta / Instagram account card (with blue Meta icon)...")
            tile_coords = find_instagram_account_tile(root, email)
            log("STATE-ACTION", f"Tapping Meta account card at {tile_coords}...")
            run_adb_command(f"shell input tap {tile_coords[0]} {tile_coords[1]}")
            time.sleep(0.5)
            run_adb_command(f"shell input tap {tile_coords[0]} {tile_coords[1]}")
            time.sleep(4)

        elif page_name == "INSTAGRAM_NAME_PAGE":
            log("STATE-ACTION", "What's your name? page detected! Clicking 'Next'...")
            tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button")
            time.sleep(4)

        elif page_name == "INSTAGRAM_USERNAME_PAGE":
            log("STATE-ACTION", "Create a username for Instagram page detected! Extracting username...")
            extracted_uname = extract_instagram_username(root)
            if extracted_uname:
                captured_username = extracted_uname
                log("USERNAME-CAPTURED", f"Captured Instagram Username: '{captured_username}'")
            log("STATE-ACTION", "Clicking 'Next' button on username page...")
            tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button")
            time.sleep(4)

        elif page_name == "INSTAGRAM_ALLOW_CONTINUE_PAGE":
            log("STATE-ACTION", "Allow and continue page detected! Swiping down slightly...")
            w, h = get_screen_dimensions(root)
            run_adb_command(f"shell input swipe {int(w*0.5)} {int(h*0.75)} {int(w*0.5)} {int(h*0.45)} 450")
            time.sleep(2)
            
            root = get_screen_hierarchy()
            log("STATE-ACTION", "Clicking 'Allow and continue' button...")
            tap_or_fallback_rel(root, "allow and continue", 0.5, 0.80, "Allow & Continue Button")
            time.sleep(5)

        elif page_name == "INSTAGRAM_SAVE_INFO_PAGE" or page_name == "INSTAGRAM_CONNECT_FB_PAGE":
            if not captured_username:
                extracted_uname = extract_instagram_username(root)
                if extracted_uname:
                    captured_username = extracted_uname
                    log("USERNAME-CAPTURED", f"Captured Instagram Username: '{captured_username}'")
            log("STATE-ACTION", "Instagram post-login page detected (Connect to Facebook / Save info)! Tapping top-left Via Browser shield checkmark icon [✓]...")
            fx, fy = get_rel_coords(root, 0.065, 0.11)
            run_adb_command(f"shell input tap {fx} {fy}")
            time.sleep(3)

        elif page_name == "VIA_SITE_INFO_POPUP":
            log("STATE-ACTION", "Via site info menu detected! Tapping 'View Cookies' (Red Pen Mark 2)...")
            tap_or_fallback_rel(root, "view cookies", 0.30, 0.38, "View Cookies Link")
            time.sleep(3)

        elif page_name == "VIA_COOKIES_DIALOG":
            log("STATE-ACTION", "Via cookies dialog detected! Clicking 'Copy' button...")
            elem_copy = find_element(root, "copy", exact=True) or find_element(root, "copy")
            if elem_copy:
                run_adb_command(f"shell input tap {elem_copy[0]} {elem_copy[1]}")
            else:
                fx, fy = get_rel_coords(root, 0.5, 0.65)
                log("ACTION-FALLBACK", f"Tapping relative fallback ({fx}, {fy}) for Copy button...")
                run_adb_command(f"shell input tap {fx} {fy}")
            time.sleep(2)
            
            cookies = get_android_clipboard()
            if not cookies or len(cookies) < 5:
                cookies = extract_cookies_from_screen_texts(visible_texts)
            if not cookies:
                cookies = "NO_COOKIES_FOUND"
                
            if not captured_username:
                captured_username = extract_instagram_username(root)
                
            log("COOKIES-CAPTURED", f"Extracted Cookies: {cookies[:100]}...")
            save_credentials_csv(email, strong_password, cookies, username=captured_username, status="REGISTRATION_SUCCESS")
            save_credentials(email, strong_password, f"REGISTRATION_SUCCESS | Username: {captured_username} | Cookies: {cookies[:60]}", username=captured_username)
            time.sleep(2)
            log("STATE-ACTION", "Instagram account creation with username & cookies export completed successfully!")
            break

        elif page_name == "FACEBOOK_LOGIN_PAGE" or page_name == "PASSWORD_INPUT_PAGE":
            log("STATE-ACTION", "Fallback Login page detected. Handling credentials entry...")
            if not password_submitted:
                elem_email = find_element(root, "email", by_class="EditText")
                if elem_email:
                    run_adb_command(f"shell input tap {elem_email[0]} {elem_email[1]}")
                    time.sleep(1)
                    type_text(email)
                
                elem_pwd = find_element(root, "pass", by_class="EditText") or find_element(root, "password", by_class="EditText")
                if elem_pwd:
                    run_adb_command(f"shell input tap {elem_pwd[0]} {elem_pwd[1]}")
                    time.sleep(1)
                    type_text(strong_password)
                
                tap_or_fallback_rel(root, "log in", 0.5, 0.60, "Log In Button")
                password_submitted = True
                time.sleep(5)

        elif page_name == "SAVE_LOGIN_PROMPT":
            log("STATE-ACTION", "Save login prompt detected. Tapping Save...")
            tap_or_fallback_rel(root, "save", 0.5, 0.70, "Save Login Info Button")
            time.sleep(2)
            save_credentials(email, strong_password, "COMPLETED_SAVED")
            break

        elif page_name == "META_GENERAL_PAGE":
            log("STATE-ACTION", "Meta general page detected. Checking available options...")
            text_blob = " ".join(visible_texts).lower()
            if "homepage" in text_blob or "scan qr code" in text_blob or "forgot" in text_blob or "can't log in" in text_blob:
                log("STATE-ACTION", "Via Homepage or Facebook fallback page detected. Re-launching target URL...")
                run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}')
            elif find_element(root, "sign up"):
                log("STATE-ACTION", "Found 'Sign up' button on page. Tapping...")
                tap_or_fallback_rel(root, "sign up", 0.78, 0.20, "Sign up Button")
            elif not landing_clicked and find_element(root, "use mobile number or email address"):
                tap_or_fallback_rel(root, "use mobile number or email address", 0.5, 0.69)
                landing_clicked = True
            elif find_element(root, "continue"):
                tap_or_fallback_rel(root, "continue", 0.5, 0.617)
            time.sleep(3)

        else:
            log("STATE-WAIT", f"Generic or transition screen '{page_name}'. Waiting for page load...")
            time.sleep(3)

    if loop_count >= max_loops:
        log("STATE-FINISH", "Reached maximum loop count limit.")
        save_credentials(email, strong_password, "LOOP_LIMIT_REACHED")

    if TempMailNinjaAPI is not None and isinstance(mail_token, TempMailNinjaAPI):
        try:
            mail_token.disconnect()
        except Exception:
            pass

    log("COMPLETED", "Meta AI automation workflow finished.")

if __name__ == "__main__":
    main()