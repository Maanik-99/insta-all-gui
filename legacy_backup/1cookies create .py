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

urllib3.disable_warnings()

# Locks & Queues
csv_lock = threading.Lock()
account_queue = queue.Queue()
total_accounts_count = 0

from google_sheet_manager import gsheet_manager

# Configuration
PACKAGE_NAME = "mark.via.gp"
TARGET_URL = "https://www.instagram.com/accounts/login"
META_ACC_CSV = os.path.join(os.path.dirname(__file__), "MetaAcc.csv")
CREDENTIALS_CSV = os.path.join(os.path.dirname(__file__), "credentials.csv")
CREDENTIALS_TXT = os.path.join(os.path.dirname(__file__), "credentials.txt")

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
    """Clean & formatted console logger with padded tag headers"""
    timestamp = time.strftime("%H:%M:%S")
    inst_tag = f"[{instance_name:^6}]" if instance_name else "[  MAIN  ]"
    tag_padded = f"[{tag:<14}]"
    log_str = f"[{timestamp}] {inst_tag} {tag_padded} {message}"
    try:
        print(log_str, flush=True)
    except UnicodeEncodeError:
        safe_str = log_str.encode(sys.stdout.encoding or 'ascii', errors='replace').decode(sys.stdout.encoding or 'ascii')
        print(safe_str, flush=True)

def get_unique_devices():
    """Scan ADB ports and return list of unique active connected device serials"""
    ports = [5555, 5554, 5556, 5558, 5560, 62001]
    for p in ports:
        try:
            subprocess.run(f'"{ADB_PATH}" connect 127.0.0.1:{p}', shell=True, capture_output=True, timeout=3)
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
    escaped = text.replace(' ', '%s').replace('&', r'\&').replace('<', r'\<').replace('>', r'\>').replace('"', r'\"').replace("'", r"\'")
    run_adb_command(f'shell input text "{escaped}"', device_serial=device_serial, instance_name=instance_name)

def hide_keyboard(device_serial=None, instance_name=""):
    """Dismiss soft keyboard safely ONLY if it is currently visible"""
    if is_keyboard_visible(device_serial=device_serial, instance_name=instance_name):
        log("ACTION", "Soft keyboard detected as OPEN. Hiding keyboard safely via KEYCODE_ESCAPE...", instance_name=instance_name)
        run_adb_command("shell input keyevent 111", device_serial=device_serial, instance_name=instance_name)
        time.sleep(0.5)
    else:
        log("ACTION", "Keyboard is already CLOSED. Skipping ESCAPE keyevent to prevent browser minimize.", instance_name=instance_name)

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

def tap_or_fallback_rel(root, search_text, rel_x, rel_y, label_name="element", exact=False, device_serial=None, instance_name=""):
    """Find element dynamically by text, or fallback to screen-ratio relative coordinates"""
    elem = find_element(root, search_text, exact=exact)
    if elem:
        x, y, found_label, bounds = elem
        log("ACTION", f"Found UI element '{found_label}' at {bounds}. Tapping center ({x}, {y})...", instance_name=instance_name)
        run_adb_command(f"shell input tap {x} {y}", device_serial=device_serial, instance_name=instance_name)
        return True
    else:
        fx, fy = get_rel_coords(root, rel_x, rel_y)
        log("ACTION-FALLBACK", f"'{search_text}' not explicitly found in XML dump. Tapping relative fallback ({fx}, {fy}) for {label_name}...", instance_name=instance_name)
        run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
        return False

def reload_browser_page(root=None, device_serial=None, instance_name=""):
    """Reload active webpage using Via browser's top-right reload button (↻)"""
    log("RELOAD", "Page element not fully loaded / Blank page detected. Tapping top-right Reload button...", instance_name=instance_name)
    if root is not None:
        for node in root.iter('node'):
            txt = node.attrib.get('text', '').strip().lower()
            desc = node.attrib.get('content-desc', '').strip().lower()
            if txt in ['reload', 'refresh'] or desc in ['reload', 'refresh']:
                coords = parse_bounds(node.attrib.get('bounds', ''))
                if coords:
                    log("RELOAD", f"Found Reload button at {coords}. Tapping center...", instance_name=instance_name)
                    run_adb_command(f"shell input tap {coords[0]} {coords[1]}", device_serial=device_serial, instance_name=instance_name)
                    time.sleep(1.8)
                    return True
                    
    fx, fy = get_rel_coords(root, 0.93, 0.075)
    log("RELOAD", f"Tapping relative fallback Reload position ({fx}, {fy})...", instance_name=instance_name)
    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.8)
    return True

def open_via_site_info(root=None, device_serial=None, instance_name=""):
    """Tap Via Browser's SSL / Site Security checkmark icon [✓] in top address bar to open Site Info"""
    log("STATE-ACTION", "Tapping Via Browser checkmark / site info icon [✓]...", instance_name=instance_name)
    if root is not None:
        w, h = get_screen_dimensions(root)
        for node in root.iter('node'):
            bounds_str = node.attrib.get('bounds', '')
            coords = parse_bounds(bounds_str)
            if coords:
                cx, cy = coords
                if cy < int(h * 0.10) and cx < int(w * 0.18):
                    res_id = node.attrib.get('resource-id', '').lower()
                    cls = node.attrib.get('class', '').lower()
                    desc = node.attrib.get('content-desc', '').lower()
                    if 'image' in cls or 'icon' in res_id or 'ssl' in res_id or 'security' in res_id or 'site' in desc or 'lock' in res_id or 'title' in res_id:
                        log("SITE-INFO", f"Found SSL/Site Info icon at {coords}. Tapping...", instance_name=instance_name)
                        run_adb_command(f"shell input tap {cx} {cy}", device_serial=device_serial, instance_name=instance_name)
                        return True

    fx, fy = get_rel_coords(root, 0.06, 0.065)
    log("SITE-INFO", f"Tapping address bar checkmark icon at ({fx}, {fy})...", instance_name=instance_name)
    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
    return True

def detect_current_page(root):
    """Inspect screen text and identify the current page / state"""
    if root is None:
        return "UNKNOWN_SCREEN", []
    
    texts = extract_screen_texts(root)
    text_blob = " ".join(texts).lower()
    
    if "can't find account" in text_blob or "cant find account" in text_blob or "we can't find an account" in text_blob or "we cant find an account" in text_blob or "incorrect password" in text_blob:
        return "CANNOT_FIND_ACCOUNT_POPUP", texts
    elif "home screen" in text_blob or "system apps" in text_blob or "ldstore" in text_blob or "search games" in text_blob:
        return "HOME_SCREEN", texts
    elif (
        "something went wrong" in text_blob
        or "we're sorry" in text_blob
        or "were sorry" in text_blob
        or "unable to create an account" in text_blob
        or ("please try again" in text_blob and ("sorry" in text_blob or "wrong" in text_blob or "error" in text_blob or "ok" in text_blob))
    ):
        return "ACCOUNT_ERROR_PAGE", texts
    elif "view cookies" in text_blob or "view certificate" in text_blob or "site configuration" in text_blob:
        return "VIA_SITE_INFO_POPUP", texts
    elif "copy" in text_blob and ("cookies" in text_blob or "site configuration" in text_blob or "view certificate" in text_blob):
        return "VIA_COOKIES_DIALOG", texts
    elif (
        "save your login info" in text_blob
        or "save login info" in text_blob
        or "won't need to enter it next time" in text_blob
        or "wont need to enter it next time" in text_blob
        or "just click your profile photo" in text_blob
        or "login info for" in text_blob
        or "save info" in text_blob
        or ("save" in text_blob and "not now" in text_blob)
    ):
        return "INSTAGRAM_SAVE_INFO_PAGE", texts
    elif "connect to facebook" in text_blob or "find friends to follow" in text_blob or "find facebook friends" in text_blob:
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
    elif (
        "suggested for you" in text_blob
        or ("messages" in text_blob and "reels" in text_blob)
        or ("search" in text_blob and "reels" in text_blob)
        or ("stories" in text_blob and "profile" in text_blob)
    ):
        return "INSTAGRAM_FEED_PAGE", texts
    else:
        return "GENERIC_PAGE", texts

def extract_instagram_username(root):
    """Extract Instagram username from screen UI hierarchy dump or text blob"""
    if root is None:
        return ""
    
    texts = extract_screen_texts(root)
    blob = " ".join(texts)
    
    m = re.search(r"(?:login info for|info for|save the login info for)\s+([a-zA-Z0-9_\.]+)", blob, re.IGNORECASE)
    if m:
        uname = m.group(1).strip().rstrip('.,?')
        if len(uname) >= 3:
            return uname
        
    ignore_words = {
        "site", "info", "instagram", "reload", "save", "info?", "login", "profile", 
        "photo", "next", "create", "username", "home", "menu", "not", "now", "back", 
        "forward", "cancel", "tabs", "tab", "add", "change", "connect", "facebook", 
        "friends", "follow", "notifications", "allow", "continue", "terms", "policies",
        "agree", "search", "reels", "messages", "feed", "we'll", "well", "enter", "time", "just", "click"
    }
    for node in root.iter('node'):
        cls = node.attrib.get('class', '')
        txt = node.attrib.get('text', '').strip() or node.attrib.get('content-desc', '').strip()
        if txt and " " not in txt and len(txt) >= 3 and not txt.isdigit():
            if txt.lower() not in ignore_words and re.match(r'^[a-zA-Z0-9_\.]+$', txt):
                if 'edittext' in cls.lower() or 'textview' in cls.lower() or 'view' in cls.lower():
                    return txt
                    
    for txt in texts:
        txt_clean = txt.strip()
        if txt_clean and " " not in txt_clean and len(txt_clean) >= 3 and not txt_clean.isdigit():
            if txt_clean.lower() not in ignore_words and re.match(r'^[a-zA-Z0-9_\.]+$', txt_clean):
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
        if '@' in t_lower or 'horizon' in t_lower or (email_user and email_user in t_lower) or any(k in t_lower for k in ["meta", "wombat", "qabq", "admiredental", "sugtbt", "uuf", "6n9", "end", "nqmo"]):
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

def save_credentials(email, password, status="UNKNOWN", username="", instance_name=""):
    """Log credentials to console (100% Online mode - no offline files saved)"""
    pass

def save_credentials_csv(email, password, cookies, username="", instance_name=""):
    """Save account data directly and exclusively to Google Sheet Sheet2 (100% Online)"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        if gsheet_manager.is_configured():
            saved = gsheet_manager.save_done_account(timestamp, email, username, password, cookies, instance_name=instance_name)
            if saved:
                log("GSHEET-SUCCESS", f">>> ONLINE SYNC: Done account '{email}' saved to Google Sheet ('{gsheet_manager.done_sheet_name}') <<<", instance_name=instance_name)
        else:
            log("GSHEET-ERR", "Google Sheet is not configured! Please provide spreadsheet_id in config.json.", instance_name=instance_name)
    except Exception as e:
        log("GSHEET-ERR", f"Failed saving to Google Sheet Sheet2: {e}", instance_name=instance_name)

def init_meta_csv():
    """Ensure Google Sheet is connected via Google Sheets API for 100% online operations"""
    if not gsheet_manager.is_api_mode():
        log("GSHEET-ERR", "==========================================================================================")
        log("GSHEET-ERR", " Google Sheet API is NOT configured! 'spreadsheet_id' is empty in config.json.")
        log("GSHEET-ERR", " cookies create .py MUST read accounts from Sheet1 via Google Sheets API.")
        log("GSHEET-ERR", " Please paste your Google Sheet URL into config.json under 'google_sheet.spreadsheet_id'!")
        log("GSHEET-ERR", "==========================================================================================")
        return 0

    ok, msg = gsheet_manager.connect()
    if ok:
        log("INIT-GSHEET", f"100% Online Google Sheet mode active ({gsheet_manager.mode}). Input: '{gsheet_manager.input_sheet_name}', Output: '{gsheet_manager.done_sheet_name}'.")
        return 1
    else:
        log("GSHEET-ERR", f"Google Sheet connection failed: {msg}")
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
                log("PICK", f"Picked free account: {acc[0]} (Row {acc[2]}/{acc[3]}) -> Marked as 'running' in Google Sheet '{gsheet_manager.input_sheet_name}' (Col E)", instance_name=instance_name)
                return acc
        except Exception as e:
            log("GSHEET-ERR", f"Failed picking account from Google Sheet: {e}", instance_name=instance_name)

    return None

def update_meta_account_status(email, status, reason="", instance_name="", row_idx=None):
    """Update account status to 'done' or 'failed' (and reason) in Google Sheet Sheet1 (100% Online)"""
    if gsheet_manager.is_configured():
        try:
            gsheet_manager.update_account_status(row_idx, email, status, reason=reason, instance_name=instance_name)
            log("STATUS-UPDATE", f"Account '{email}' status updated to '{status}' ({reason}) in Google Sheet '{gsheet_manager.input_sheet_name}' (Col E & F)", instance_name=instance_name)
        except Exception as e:
            log("GSHEET-ERR", f"Failed updating status in Google Sheet: {e}", instance_name=instance_name)



def process_single_account(device_serial, instance_name, email, password, acc_index, total_count):
    """Process a single Meta account to Instagram creation on the specified device"""
    log("ACC-START", f"[{acc_index}/{total_count}] Processing Meta Account: {email}...", instance_name=instance_name)

    # 1. Clean Via Browser State
    log("STEP-1", f"Cleaning Via Browser data ({PACKAGE_NAME})...", instance_name=instance_name)
    run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.2)
    run_adb_command(f"shell pm clear {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.3)

    # 2. Open instagram.com directly
    log("STEP-2", f"Opening '{TARGET_URL}' directly in Via Browser...", instance_name=instance_name)
    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.8)

    # State tracking variables
    login_submitted = False
    allow_continue_submitted = False
    agree_terms_submitted = False
    captured_username = ""
    error_retry_count = 0
    max_loops = 80
    loop_count = 0

    page_action_counts = {}
    last_page_name = ""
    page_enter_time = time.time()
    page_reload_count = 0
    agree_retry_count = 0

    log("STATE-MACHINE", f"Starting state machine loop for {email}...", instance_name=instance_name)

    while loop_count < max_loops:
        loop_count += 1
        root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
        page_name, visible_texts = detect_current_page(root)
        current_time = time.time()

        if page_name != last_page_name:
            last_page_name = page_name
            page_enter_time = current_time
            page_reload_count = 0
            if page_name not in ["INSTAGRAM_AGREE_TERMS_PAGE", "INSTAGRAM_ALLOW_CONTINUE_PAGE", "INSTAGRAM_USERNAME_PAGE"]:
                agree_retry_count = 0
            sample = visible_texts[:4] if visible_texts else ["No visible text"]
            log("PAGE-CHANGE", f"[Loop {loop_count}/{max_loops}] Page: '{page_name}' | Text: {sample}", instance_name=instance_name)
        else:
            stuck_duration = current_time - page_enter_time

            # Special handling for INSTAGRAM_AGREE_TERMS_PAGE:
            # If stuck for >= 30s after clicking 'I agree', do NOT reload the page.
            # Instead, press Back and repeat the process from previous step for exactly 1 retry.
            if page_name == "INSTAGRAM_AGREE_TERMS_PAGE":
                if stuck_duration >= 30.0:
                    agree_retry_count += 1
                    if agree_retry_count <= 1:
                        log("TIMEOUT-WARN", f"Page '{page_name}' stuck for {int(stuck_duration)}s after 'I agree'. Pressing Back to repeat process (1 retry attempt)...", instance_name=instance_name)
                        run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)
                        agree_terms_submitted = False
                        allow_continue_submitted = False
                        last_page_name = ""
                        page_enter_time = time.time()
                        if "INSTAGRAM_AGREE_TERMS_PAGE" in page_action_counts:
                            page_action_counts["INSTAGRAM_AGREE_TERMS_PAGE"] = 0
                        if "INSTAGRAM_ALLOW_CONTINUE_PAGE" in page_action_counts:
                            page_action_counts["INSTAGRAM_ALLOW_CONTINUE_PAGE"] = 0
                        time.sleep(2.0)
                        continue
                    else:
                        fail_reason = "STUCK_TERMS_AGREE_AFTER_RETRY"
                        log("TIMEOUT-ABORT", f"Page '{page_name}' still stuck after retry (>30s). Marking failed with reason in sheet and moving to next account...", instance_name=instance_name)
                        save_credentials(email, password, fail_reason, instance_name=instance_name)
                        return False, fail_reason
                elif agree_terms_submitted:
                    # I agree submitted, waiting for Instagram session cookies / post-login page
                    time.sleep(0.5)
                    continue

            elif stuck_duration >= 30.0:
                page_reload_count += 1
                if page_reload_count >= 2:
                    fail_reason = f"STUCK_TIMEOUT_{page_name}"
                    log("TIMEOUT-ABORT", f"Page '{page_name}' not progressing after 2 reload attempts (>60s). Moving to next account...", instance_name=instance_name)
                    save_credentials(email, password, fail_reason, instance_name=instance_name)
                    return False, fail_reason

                log("TIMEOUT-WARN", f"Page '{page_name}' loading/stuck for {int(stuck_duration)}s. Reloading page (Attempt {page_reload_count}/2)...", instance_name=instance_name)
                reload_browser_page(root=root, device_serial=device_serial, instance_name=instance_name)
                if page_name == "INSTAGRAM_LOGIN_PAGE":
                    login_submitted = False
                elif page_name == "INSTAGRAM_ALLOW_CONTINUE_PAGE":
                    allow_continue_submitted = False

                page_enter_time = time.time()
                time.sleep(1.0)
                continue
            elif page_name == "INSTAGRAM_LOGIN_PAGE" and login_submitted:
                # Login already submitted, waiting for Instagram response/next page up to 30s
                time.sleep(0.4)
                continue
            elif page_name == "INSTAGRAM_ALLOW_CONTINUE_PAGE" and allow_continue_submitted:
                # Allow & continue submitted, waiting for server to link account & load next page
                time.sleep(0.5)
                continue
            elif page_name in ["GENERIC_PAGE", "UNKNOWN_SCREEN"]:
                time.sleep(0.4)
                continue

        # --- Check Action Limit per Page to prevent infinite loops ---
        if page_name not in ["VIA_SITE_INFO_POPUP", "VIA_COOKIES_DIALOG", "GENERIC_PAGE", "UNKNOWN_SCREEN"]:
            page_action_counts[page_name] = page_action_counts.get(page_name, 0) + 1
            if page_action_counts[page_name] > 2:
                log("ABORT-MAX-TRIES", f"Step '{page_name}' exceeded maximum action attempts (2). Moving to next account...", instance_name=instance_name)
                save_credentials(email, password, f"EXCEEDED_MAX_TRIES_{page_name}", instance_name=instance_name)
                return False

        # --- Handle States Instantly upon page detection ---
        if page_name == "BROWSER_TERMS_PAGE":
            log("STATE-ACTION", "Via Browser terms page detected. Tapping AGREE...", instance_name=instance_name)
            tap_or_fallback_rel(root, "agree", 0.5, 0.88, "Agree Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.4)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

        elif page_name == "HOME_SCREEN":
            log("STATE-ACTION", "Home screen detected. Re-opening instagram.com...", instance_name=instance_name)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.8)

        elif page_name == "INSTAGRAM_LANDING_PAGE":
            log("STATE-ACTION", "Instagram landing page detected. Tapping 'Log in'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "log in", 0.5, 0.74, "Log In Link", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.8)

        elif page_name == "INSTAGRAM_LOGIN_PAGE":
            if not login_submitted:
                log("STATE-ACTION", f"Entering email '{email}' and password...", instance_name=instance_name)
                elem_user = find_element(root, "username", by_class="EditText") or find_element(root, "email", by_class="EditText") or find_element(root, "phone", by_class="EditText")
                if elem_user:
                    run_adb_command(f"shell input tap {elem_user[0]} {elem_user[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    fx, fy = get_rel_coords(root, 0.5, 0.46)
                    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.25)
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
                time.sleep(0.25)
                clear_input_field(device_serial=device_serial, instance_name=instance_name)
                type_text(password, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)

                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.25)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                log("STATE-ACTION", "Clicking 'Log in' button...", instance_name=instance_name)
                tap_or_fallback_rel(root, "log in", 0.5, 0.67, "Log In Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                login_submitted = True
                login_wait_count = 0
                time.sleep(2.0)
            else:
                login_wait_count += 1
                if login_wait_count >= 2:
                    log("STATE-ABORT", f"Login submission pending after {login_wait_count} checks. Moving to next account...", instance_name=instance_name)
                    save_credentials(email, password, "LOGIN_FAILED_OR_STUCK", instance_name=instance_name)
                    return False, "LOGIN_FAILED_OR_STUCK"
                else:
                    log("STATE-WAIT", f"Login already submitted ({login_wait_count}/2). Waiting for next page...", instance_name=instance_name)
                    time.sleep(1.5)

        elif page_name == "INSTAGRAM_SAVE_PASSWORD_POPUP":
            log("STATE-ACTION", "Save password popup detected! Clicking 'Cancel'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "cancel", 0.5, 0.65, "Cancel Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.0)

        elif page_name == "INSTAGRAM_NO_ACCOUNT_FOUND":
            log("STATE-ACTION", "No Instagram account found overlay detected! Scrolling down to find Meta account card...", instance_name=instance_name)
            w, h = get_screen_dimensions(root)
            run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.80)} {int(w*0.50)} {int(h*0.25)} 300", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.6)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            tile_coords = find_instagram_account_tile(root, email)
            if tile_coords and tile_coords[1] > int(h * 0.60):
                log("STATE-ACTION", f"Found Meta account card at {tile_coords[:2]}. Tapping...", instance_name=instance_name)
                run_adb_command(f"shell input tap {tile_coords[0]} {tile_coords[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                bx, by = int(w * 0.50), int(h * 0.82)
                log("STATE-ACTION", f"Tapping middle of bottom account box at ({bx}, {by})...", instance_name=instance_name)
                run_adb_command(f"shell input tap {bx} {by}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)
                run_adb_command(f"shell input tap {bx} {by}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.8)

        elif page_name == "INSTAGRAM_NAME_PAGE":
            log("STATE-ACTION", "What's your name? page detected! Clicking 'Next'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

        elif page_name == "INSTAGRAM_USERNAME_PAGE":
            log("STATE-ACTION", "Create a username page detected! Extracting username...", instance_name=instance_name)
            extracted_uname = extract_instagram_username(root)
            if extracted_uname:
                captured_username = extracted_uname
                log("USERNAME-CAPTURED", f"Captured Instagram Username: '{captured_username}'", instance_name=instance_name)
            tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

        elif page_name == "INSTAGRAM_ALLOW_CONTINUE_PAGE":
            if not allow_continue_submitted:
                log("STATE-ACTION", "Allow and continue page detected! Submitting and waiting for account creation...", instance_name=instance_name)
                w, h = get_screen_dimensions(root)
                run_adb_command(f"shell input swipe {int(w*0.5)} {int(h*0.75)} {int(w*0.5)} {int(h*0.45)} 450", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.6)
                
                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                tap_or_fallback_rel(root, "allow and continue", 0.5, 0.80, "Allow & Continue Button", device_serial=device_serial, instance_name=instance_name)
                allow_continue_submitted = True
                page_enter_time = time.time()  # Reset timeout timer to give full 30s window for server creation
                time.sleep(3.5)

        elif page_name == "INSTAGRAM_AGREE_TERMS_PAGE":
            if not agree_terms_submitted:
                log("STATE-ACTION", "Agree to Instagram terms detected! Clicking 'I agree'...", instance_name=instance_name)
                elem_agree = find_element(root, "i agree", exact=True) or find_element(root, "i agree") or find_element(root, "agree")
                if elem_agree:
                    run_adb_command(f"shell input tap {elem_agree[0]} {elem_agree[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    tap_or_fallback_rel(root, "i agree", 0.5, 0.81, "I Agree Button", device_serial=device_serial, instance_name=instance_name)
                agree_terms_submitted = True
                page_enter_time = time.time()  # Reset timeout timer to give full 30s window for post-login cookies
                time.sleep(3.0)

        elif page_name in ["INSTAGRAM_SAVE_INFO_PAGE", "INSTAGRAM_CONNECT_FB_PAGE", "INSTAGRAM_NOTIFICATIONS_PAGE", "INSTAGRAM_FEED_PAGE"]:
            if not captured_username:
                extracted_uname = extract_instagram_username(root)
                if extracted_uname:
                    captured_username = extracted_uname
                    log("USERNAME-CAPTURED", f"Captured Instagram Username: '{captured_username}'", instance_name=instance_name)
            open_via_site_info(root=root, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.2)

        elif page_name == "VIA_SITE_INFO_POPUP":
            log("STATE-ACTION", "Via site info menu detected! Tapping 'View Cookies'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "view cookies", 0.30, 0.38, "View Cookies Link", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.2)

        elif page_name == "VIA_COOKIES_DIALOG":
            log("STATE-ACTION", "Via cookies dialog detected! Clicking 'Copy'...", instance_name=instance_name)
            elem_copy = find_element(root, "copy", exact=True) or find_element(root, "copy")
            if elem_copy:
                run_adb_command(f"shell input tap {elem_copy[0]} {elem_copy[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.5, 0.65)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)

            cookies = get_android_clipboard(device_serial=device_serial, instance_name=instance_name)
            if not cookies or len(cookies) < 5:
                cookies = extract_cookies_from_screen_texts(visible_texts)
            if not cookies:
                cookies = "NO_COOKIES_FOUND"

            if not captured_username:
                captured_username = extract_instagram_username(root)

            log("COOKIES-CAPTURED", f"Extracted Cookies: {cookies[:100]}...", instance_name=instance_name)
            save_credentials_csv(email, password, cookies, username=captured_username, instance_name=instance_name)
            save_credentials(email, password, f"REGISTRATION_SUCCESS | Username: {captured_username} | Cookies: {cookies[:60]}", username=captured_username, instance_name=instance_name)
            time.sleep(0.8)
            log("STATE-SUCCESS", f"Account '{email}' successfully linked to Instagram! (Username: '{captured_username}')", instance_name=instance_name)
            return True, "SUCCESS"

        elif page_name == "CANNOT_FIND_ACCOUNT_POPUP":
            log("STATE-WARN", f"Login failed: 'Can't find account' / 'Incorrect password' popup detected for {email}.", instance_name=instance_name)
            save_credentials(email, password, "CANNOT_FIND_ACCOUNT_OR_WRONG_PASSWORD", instance_name=instance_name)
            return False, "CANNOT_FIND_ACCOUNT_OR_WRONG_PASSWORD"

        elif page_name == "ACCOUNT_ERROR_PAGE":
            error_retry_count += 1
            if error_retry_count <= 1:
                log("STATE-RETRY", f"'Something went wrong' popup detected for {email}. Tapping 'OK' and retrying 1 more click...", instance_name=instance_name)
                # 1. Tap 'OK' button to dismiss snackbar
                elem_ok = find_element(root, "ok", exact=True) or find_element(root, "ok")
                if elem_ok:
                    run_adb_command(f"shell input tap {elem_ok[0]} {elem_ok[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    fx, fy = get_rel_coords(root, 0.78, 0.84)
                    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.6)

                # 2. Tap the action button to retry proceeding (Allow and continue / Next / Agree / Retry)
                root_after = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                tap_or_fallback_rel(root_after, "allow and continue", 0.5, 0.78, "Allow & Continue Button", device_serial=device_serial, instance_name=instance_name)
                time.sleep(2.0)
            else:
                log("STATE-WARN", f"'Something went wrong' persisted after retry for {email}. Skipping to next account...", instance_name=instance_name)
                save_credentials(email, password, "ACCOUNT_ERROR_SOMETHING_WENT_WRONG", instance_name=instance_name)
                return False, "ACCOUNT_ERROR_SOMETHING_WENT_WRONG"

        else:
            if len(visible_texts) <= 5:
                log("STATE-WARN", f"Incomplete / blank page detected ({visible_texts}). Pressing Back...", instance_name=instance_name)
                run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)
                time.sleep(1.0)
            else:
                log("STATE-WAIT", f"Generic screen '{page_name}'. Waiting...", instance_name=instance_name)
                time.sleep(1.2)

    log("STATE-FINISH", f"Loop limit reached for {email}.", instance_name=instance_name)
    save_credentials(email, password, "LOOP_LIMIT_REACHED", instance_name=instance_name)
    return False, "LOOP_LIMIT_REACHED"

def worker_instance(device_serial, instance_name, stop_event=None):
    """Worker thread picking free accounts dynamically from Google Sheet or MetaAcc.csv on an emulator instance"""
    log("INIT", f"Worker thread started for {instance_name} ({device_serial}).", instance_name=instance_name)
    processed_count = 0
    poll_interval_seconds = 10

    while True:
        if stop_event and stop_event.is_set():
            break

        acc_info = get_next_free_meta_account(instance_name=instance_name)
        if not acc_info:
            log("WAIT-ACCS", f"No pending/free accounts available right now. Waiting {poll_interval_seconds}s for new accounts to arrive in Sheet/CSV...", instance_name=instance_name)
            for _ in range(poll_interval_seconds):
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1.0)
            continue

        email, password, current_idx, total_accs = acc_info
        processed_count += 1

        success, reason = False, "UNKNOWN"
        try:
            res = process_single_account(device_serial, instance_name, email, password, current_idx, total_accs)
            if isinstance(res, tuple):
                success, reason = res
            else:
                success = bool(res)
                reason = "SUCCESS" if success else "FAILED"
        except Exception as e:
            log("ERR", f"Unexpected exception on {email}: {e}", instance_name=instance_name)
            success, reason = False, f"EXCEPTION: {e}"
        finally:
            final_status = "done" if success else "failed"
            update_meta_account_status(email, final_status, reason=reason, instance_name=instance_name, row_idx=current_idx)

        time.sleep(1.0)

    log("FINISH", f"{instance_name} worker stopped. (Total accounts processed by this instance: {processed_count})", instance_name=instance_name)


def main():
    log("INIT", "==================================================================")
    log("INIT", "  Starting Meta-to-Instagram Account Creator (Dynamic MetaAcc.csv) ")
    log("INIT", "==================================================================")

    # 1. Initialize MetaAcc.csv Status column
    init_meta_csv()

    # 2. Detect Connected Emulator Devices
    devices = get_unique_devices()
    if not devices:
        log("ERR", "No connected ADB emulator instances found! Please ensure LDPlayer instances are running.")
        return

    log("INIT", f"Detected {len(devices)} active emulator instance(s): {devices}")

    # 3. Spawn Worker Threads for Parallel Multi-Instance Execution
    stop_event = threading.Event()
    threads = []
    for idx, device in enumerate(devices, 1):
        inst_name = f"Inst-{idx}"
        log("LAUNCH", f"Spawning worker thread for {inst_name} ({device})...")
        t = threading.Thread(target=worker_instance, args=(device, inst_name, stop_event), daemon=True)
        threads.append(t)
        t.start()
        time.sleep(1.0)

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1.0)
    except KeyboardInterrupt:
        log("SHUTDOWN", "Shutdown requested by user (Ctrl+C). Stopping all worker threads...")
        stop_event.set()
        for t in threads:
            t.join(timeout=5.0)

    log("COMPLETED", "All Meta accounts processed / worker session closed.")

if __name__ == "__main__":
    main()
