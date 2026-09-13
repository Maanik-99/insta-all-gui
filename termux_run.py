#!/usr/bin/env python3
"""
Termux / On-Device Android Python runner for 1cookies create .py.
Runs directly on the Android phone via Termux using local wireless ADB or root (su).

Usage on Android (Termux):
1. pkg install python android-tools git
2. pip install requests
3. adb connect 127.0.0.1:5555   (or use Shizuku / su)
4. python termux_run.py
"""

import subprocess
import time
import os
import sys
import re
import json
import xml.etree.ElementTree as ET
import urllib3

urllib3.disable_warnings()

PACKAGE_NAME = "mark.via.gp"
TARGET_URL = "https://www.instagram.com/accounts/login"
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

CFG = load_config()
WEBHOOK_URL = CFG.get("google_sheet", {}).get("google_sheet_webhook_url", "https://script.google.com/macros/s/AKfycby0FMC_Y4T_vGeWOsKRwbmKg6RBIHFOxqNhIaqgjrTDhpmgTyAkOmX-noCmo3TEIPSr/exec")

def run_cmd(cmd_str):
    try:
        res = subprocess.run(cmd_str, shell=True, capture_output=True, encoding='utf-8', errors='ignore')
        return res.stdout.strip() if res.stdout else ""
    except Exception as e:
        print(f"[ERR] Command execution failed: {e}")
        return ""

def log(tag, message):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{tag:<12}] {message}", flush=True)

def get_screen_hierarchy():
    dump_path = "/sdcard/window_dump.xml"
    run_cmd(f"uiautomator dump {dump_path}")
    xml_str = run_cmd(f"cat {dump_path}")
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
    if text: texts.append(text)
    if desc and desc != text: texts.append(desc)
    for child in node:
        texts.extend(extract_screen_texts(child))
    return texts

def parse_bounds(bounds_str):
    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
    if m:
        l, t, r, b = map(int, m.groups())
        return (l + r) // 2, (t + b) // 2
    return None

def get_clipboard():
    out = run_cmd("cmd clipboard get")
    if out and "Error" not in out and "exception" not in out.lower():
        return out.strip()
    return ""

def get_next_account():
    import requests
    try:
        res = requests.post(WEBHOOK_URL, json={"action": "get_next_account"}, timeout=12)
        data = res.json()
        if data.get("success") and "account" in data:
            acc = data["account"]
            return acc["email"], acc["password"], acc["rowIndex"], acc["totalCount"]
    except Exception as e:
        log("GSHEET-ERR", f"Failed fetching account: {e}")
    return None

def update_status(email, status, reason="", row_idx=1):
    import requests
    try:
        requests.post(WEBHOOK_URL, json={
            "action": "update_status",
            "email": email,
            "status": status,
            "reason": reason,
            "rowIndex": row_idx
        }, timeout=12)
    except Exception:
        pass

def save_done(email, username, password, cookies):
    import requests
    try:
        requests.post(WEBHOOK_URL, json={
            "action": "save_done_account",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "email": email,
            "username": username,
            "password": password,
            "cookies": cookies
        }, timeout=12)
        log("GSHEET", f"Saved done account {email} to Sheet2!")
    except Exception as e:
        log("GSHEET-ERR", f"Failed saving to Sheet2: {e}")

def main():
    log("INIT", "Starting On-Device Termux Instagram Cookie Harvester...")
    while True:
        acc = get_next_account()
        if not acc:
            log("WAIT", "No pending accounts in Sheet. Waiting 10s...")
            time.sleep(10)
            continue

        email, pwd, row_idx, total = acc
        log("ACC", f"[{row_idx}/{total}] Processing: {email}")

        # Launch Via Browser
        run_cmd(f"am force-stop {PACKAGE_NAME}")
        time.sleep(0.3)
        run_cmd(f'am start -a android.intent.action.VIEW -d "{TARGET_URL}" {PACKAGE_NAME}')
        time.sleep(2.0)

        # Loop states
        success = False
        captured_uname = ""
        for _ in range(60):
            root = get_screen_hierarchy()
            texts = extract_screen_texts(root)
            blob = " ".join(texts).lower()

            if "view cookies" in blob:
                run_cmd("input tap 200 400")
                time.sleep(1.2)
            elif "copy" in blob and ("cookies" in blob or "site configuration" in blob):
                run_cmd("input tap 540 1200")
                time.sleep(0.8)
                cookies = get_clipboard()
                if cookies:
                    save_done(email, captured_uname, pwd, cookies)
                    update_status(email, "done", "SUCCESS", row_idx)
                    success = True
                    break
            elif "save your login info" in blob or "suggested for you" in blob:
                run_cmd("input tap 60 70") # SSL checkmark
                time.sleep(1.2)
            elif "username, email or mobile number" in blob or "phone number" in blob:
                run_cmd("input tap 540 700")
                time.sleep(0.3)
                run_cmd(f'input text "{email}"')
                time.sleep(0.3)
                run_cmd("input tap 540 850")
                time.sleep(0.3)
                run_cmd(f'input text "{pwd}"')
                time.sleep(0.3)
                run_cmd("input keyevent 111")
                time.sleep(0.2)
                run_cmd("input tap 540 980") # Log in
                time.sleep(2.5)
            elif "allow and continue" in blob:
                run_cmd("input swipe 500 1200 500 600 300")
                time.sleep(0.5)
                run_cmd("input tap 500 1350")
                time.sleep(3.0)
            elif "i agree" in blob or "agree to instagram" in blob:
                run_cmd("input tap 500 1450")
                time.sleep(3.0)
            elif "create a username" in blob or "what's your name" in blob:
                run_cmd("input tap 500 1100") # Next
                time.sleep(1.5)
            elif "no instagram account found" in blob:
                run_cmd("input swipe 500 1200 500 500 300")
                time.sleep(0.5)
                run_cmd("input tap 500 1450")
                time.sleep(1.8)
            time.sleep(1.0)

        if not success:
            update_status(email, "failed", "TIMEOUT", row_idx)

if __name__ == "__main__":
    main()
