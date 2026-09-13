import asyncio
import csv
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
import xml.etree.ElementTree as ET

import pyotp
import requests
import urllib3
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page
from telethon import TelegramClient, events, errors
from telethon.tl.custom import Button

from email_service import (
    generate_temp_email,
    fetch_otp_from_temp_mail,
    load_config,
    DEFAULT_CONFIG,
    CONFIG_FILE
)
import mail_td
from google_sheet_manager import gsheet_manager

urllib3.disable_warnings()

# ==============================================================================
# ⚙️ কনফিগারেশন ও সেটিংস (config.json থেকে স্বয়ংক্রিয়ভাবে লোড হবে)
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

# 👉 টার্গেট টেলিগ্রাম বট ইউজারনেম (config.json থেকে লোড হবে)
TARGET_BOTS = normalize_target_bots(_TG_CFG.get("target_bot") or _TG_CFG.get("target_bots"))
TARGET_BOT = TARGET_BOTS[0] if TARGET_BOTS else "tasklyBux_bot"

# 👉 টেলিগ্রাম অ্যাকাউন্ট কনফিগারেশন (config.json থেকে লোড হবে)
TELEGRAM_ACCOUNTS = _TG_CFG.get("accounts", [])

for _acc in TELEGRAM_ACCOUNTS:
    raw_acc_bots = _acc.get("target_bots") or _acc.get("target_bot") or TARGET_BOTS
    _acc["target_bots"] = normalize_target_bots(raw_acc_bots)
    _acc["target_bot"] = _acc["target_bots"][0]

# 👉 বারবার স্বয়ংক্রিয়ভাবে পরবর্তী টাস্ক চালাবেন কিনা
AUTO_REPEAT_TASKS = _TG_CFG.get("auto_repeat_tasks", True)

# 👉 টেলিগ্রাম বটে বাটন ক্লিকের মাঝে অপেক্ষার সময় (অপ্টিমাইজড ফাস্ট রেসপন্স)
DELAY_BETWEEN_STEPS = _TG_CFG.get("delay_between_steps", 0.5)

# 👉 Meta AI তৈরির জন্য মূল URL সেটিংস
TARGET_URL = "https://www.meta.ai/"
CHECKPOINT_REDIRECT_URL = "https://auth.meta.com/checkpoints/675270400317265/?next=https%3A%2F%2Fauth.meta.com%2Frecover%2Fsuccess%2F%3Fredirect_uri%3Dhttps%253A%252F%252Fauth.meta.com%252F"

# 👉 ব্রাউজার সেটিংস (HEADLESS = True দিলে ব্রাউজার হাইড থাকবে, False দিলে সামনে ওপেন হবে)
HEADLESS = False
VIEWPORT_WIDTH = 500       # ব্রাউজার উইন্ডোর প্রস্থ (Width)
VIEWPORT_HEIGHT = 700      # ব্রাউজার উইন্ডোর উচ্চতা (Height)

# 👉 এমুলেটরে ব্যবহৃত ব্রাউজার প্যাকেজ নেম (ডিফল্ট: Via Browser)
PACKAGE_NAME = "mark.via.gp"

# 👉 ইনস্টাগ্রাম ও 2FA ইউআরএল সেটিংস
INSTA_LOGIN_URL = "https://www.instagram.com/accounts/login"
TWO_FACTOR_URL = "https://accountscenter.instagram.com/password_and_security/two_factor/"
ACCOUNTS_OVERVIEW_URL = "https://accountscenter.instagram.com/account_overview/"

# 👉 সফল হওয়া অ্যাকাউন্ট সংরক্ষণের CSV ফাইলের লোকেশন
BASE_DIR = os.path.dirname(__file__)
CREDENTIALS_CSV = os.path.join(BASE_DIR, "credentials.csv")

csv_lock = threading.Lock()

# ==============================================================================
# LOGGING
# ==============================================================================
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

# ==============================================================================
# STORAGE & GSHEET HELPERS
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
        except Exception as e:
            log("CSV-ERR", f"Failed saving to credentials.csv: {e}", instance_name=instance_name)

    if gsheet_manager.is_configured():
        def _save_gsheet():
            try:
                gsheet_manager.save_done_account(timestamp, email, uname, password, two_factor_secret, instance_name=instance_name)
            except Exception as e:
                log("GSHEET-ERR", f"Failed syncing to Google Sheet: {e}", instance_name=instance_name)
        threading.Thread(target=_save_gsheet, daemon=True).start()


# PLAYWRIGHT META AI AUTOMATION ENGINE (100% Matching 1meta.ai create.py)
# ==============================================================================
async def get_active_meta_page(context: BrowserContext, default_page: Page = None) -> Page:
    try:
        pages = context.pages
        if not pages:
            return default_page or await context.new_page()

        for p in reversed(pages):
            try:
                if p.is_closed():
                    continue
                url = p.url.lower()
                if any(k in url for k in ["auth.meta.com", "checkpoint", "challenge", "recover", "login", "signup", "confirm"]):
                    return p
            except Exception:
                pass

        for p in reversed(pages):
            try:
                if p.is_closed():
                    continue
                url = p.url.lower()
                if "meta.ai" in url:
                    return p
            except Exception:
                pass

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

async def human_type(locator, text: str, min_delay: int = 30, max_delay: int = 70):
    await locator.click()
    await asyncio.sleep(random.uniform(0.05, 0.12))

    try:
        await locator.press("Control+A")
        await asyncio.sleep(random.uniform(0.02, 0.05))
        await locator.press("Backspace")
        await asyncio.sleep(random.uniform(0.03, 0.08))
    except Exception:
        pass

    for i, ch in enumerate(text):
        delay_ms = random.randint(min_delay, max_delay)
        await locator.press_sequentially(ch, delay=delay_ms)
        if ch in [" ", "@", ".", "_", "-"] or (i > 0 and i % random.randint(4, 7) == 0):
            await asyncio.sleep(random.uniform(0.04, 0.12))

    await asyncio.sleep(random.uniform(0.05, 0.15))
    try:
        await locator.evaluate("el => { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }")
    except Exception:
        pass

async def robust_fill(page: Page, selectors: list[str] | str, value: str, desc="Input field", timeout=2000, human_speed=True) -> bool:
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
                        await loc.press_sequentially(value, delay=random.randint(20, 50))
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
                if await loc.is_visible(timeout=250):
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

async def click_next_or_continue(page: Page, timeout=1000) -> bool:
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

async def handle_cookie_banners(page: Page, instance_name="") -> bool:
    try:
        if page.is_closed():
            return False

        try:
            await page.evaluate('''() => {
                window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' });
                if (document.documentElement) document.documentElement.scrollTop = document.documentElement.scrollHeight;
                if (document.body) document.body.scrollTop = document.body.scrollHeight;
            }''')
        except Exception:
            pass

        cookie_selectors = [
            "span.xlyipyv:has-text('Allow all cookies')",
            "span.xuxw1ft:has-text('Allow all cookies')",
            "span.x1lliihq.x193iq5w:has-text('Allow all cookies')",
            "span:has-text('Allow all cookies')",
            "div[role='button']:has(span:has-text('Allow all cookies'))",
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
            "button:has-text('Allow cookies')",
            "button:has-text('Accept all')",
            "button:has-text('Allow all')",
            "button:has-text('Accept')",
            "button:has-text('Allow')",
            "div[role='button']:has-text('Allow all')",
            "div[role='button']:has-text('Accept all')"
        ]
        for sel in cookie_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=250):
                    log("ACTION", f"Clicking 'Allow all cookies' via ({sel})...", instance_name=instance_name)
                    await loc.scroll_into_view_if_needed(timeout=400)
                    await loc.click(force=True, timeout=600)
                    await asyncio.sleep(0.4)
                    return True
            except Exception:
                pass

        clicked_js = await page.evaluate('''() => {
            const allElements = Array.from(document.querySelectorAll("span, [role='button'], button, div[tabindex='0']"));
            for (const el of allElements) {
                const txt = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (txt === 'allow all cookies' || txt.includes('allow all cookies') || txt.includes('accept all cookies') || txt.includes('allow essential cookies')) {
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    el.click();
                    const parent = el.closest("[role='button']") || el.closest("button") || el;
                    parent.click();
                    parent.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    return true;
                }
            }
            return false;
        }''')
        if clicked_js:
            log("ACTION", "Dismissed cookie banner via JS evaluation!", instance_name=instance_name)
            await asyncio.sleep(0.4)
            return True
    except Exception as e:
        log("ACTION-WARN", f"handle_cookie_banners note: {e}", instance_name=instance_name)
    return False

async def dismiss_error_modals(page: Page, instance_name="") -> bool:
    try:
        if page.is_closed():
            return False

        has_dialog = await page.locator("div[role='dialog']:has-text('Something went wrong'), :has-text('Something went wrong'), div[role='dialog'] [role='button']:has-text('OK'), div[role='dialog'] button:has-text('OK')").count() > 0
        if has_dialog:
            log("ACTION", "Dismissing 'Something went wrong' popup by pressing ESC...", instance_name=instance_name)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

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
                    if await loc.is_visible(timeout=150):
                        await loc.click(force=True, timeout=400)
                        await asyncio.sleep(0.2)
                        break
                except Exception:
                    pass

            await handle_cookie_banners(page, instance_name=instance_name)
            return True
    except Exception as e:
        log("ACTION-WARN", f"dismiss_error_modals note: {e}", instance_name=instance_name)
    return False

async def dismiss_meta_ai_dialog_or_popup(page: Page, instance_name="") -> bool:
    try:
        if page.is_closed():
            return False

        # 1. Try finding and clicking close/dismiss buttons on any dialog/popup
        close_selectors = [
            "div[role='dialog'] [aria-label='Close']",
            "div[role='dialog'] [aria-label='close']",
            "div[role='dialog'] button:has-text('Close')",
            "div[role='dialog'] [role='button']:has-text('Close')",
            "div[role='dialog'] button:has-text('Not now')",
            "div[role='dialog'] [role='button']:has-text('Not now')",
            "div[role='dialog'] button:has-text('Dismiss')",
            "div[role='dialog'] button:has-text('Continue')",
            "[aria-label='Close']",
            "button[aria-label='Close']",
            "svg[aria-label='Close']",
            "button:has-text('Not now')"
        ]
        for sel in close_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=200):
                    log("ACTION", f"Dismissing meta.ai popup via button ({sel})...", instance_name=instance_name)
                    await loc.click(force=True, timeout=500)
                    await asyncio.sleep(0.4)
                    return True
            except Exception:
                pass

        # 2. Press Escape key in case it's dismissable
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.2)
        except Exception:
            pass

        # 3. Remove dialog/backdrop elements if still blocking (e.g., backdrop-blur-elevation-03, bg-fill-dialog)
        removed = await page.evaluate('''() => {
            let removedCount = 0;
            const popups = Array.from(document.querySelectorAll(
                ".backdrop-blur-elevation-03, .bg-fill-dialog, [class*='backdrop-blur'], div[role='dialog'], [aria-modal='true']"
            ));
            for (const el of popups) {
                // Check if element or its parent modal is present
                const parentModal = el.closest("[role='dialog']") || el.closest("[aria-modal='true']") || el;
                if (parentModal && parentModal !== document.body && parentModal !== document.documentElement) {
                    parentModal.style.display = 'none';
                    parentModal.remove();
                    removedCount++;
                }
            }
            if (removedCount > 0) {
                document.body.style.overflow = 'auto';
                if (document.documentElement) document.documentElement.style.overflow = 'auto';
                return true;
            }
            return false;
        }''')
        if removed:
            log("ACTION", "Removed meta.ai dialog/backdrop popup overlay from DOM!", instance_name=instance_name)
            await asyncio.sleep(0.3)
            return True
    except Exception as e:
        log("ACTION-WARN", f"dismiss_meta_ai_dialog_or_popup note: {e}", instance_name=instance_name)
    return False

async def handle_save_login_info_prompt(page: Page, context: BrowserContext, instance_name="") -> bool:
    for p in [page] + [pg for pg in context.pages if pg != page and not pg.is_closed()]:
        try:
            active_btn_selectors = [
                "div[role='button'][tabindex='0']:has-text('Not now')",
                "div[role='button']:not([aria-disabled='true']):has-text('Not now')",
                "[role='button']:not([aria-disabled='true']):has-text('Not now')",
                "button:not([disabled]):has-text('Not now')",
                "div[role='button']:has-text('Not now')"
            ]
            for sel in active_btn_selectors:
                try:
                    loc = p.locator(sel).last
                    if await loc.is_visible(timeout=250):
                        log("ACTION", f"Clicking active 'Not now' button ({sel})...", instance_name=instance_name)
                        await loc.click(force=True, timeout=800)
                        await asyncio.sleep(0.6)
                        return True
                except Exception:
                    pass

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
                return false;
            }''')
            if clicked_js:
                log("ACTION", "Clicked active 'Not now' button via JS DOM evaluation!", instance_name=instance_name)
                await asyncio.sleep(0.6)
                return True
        except Exception as e:
            log("ACTION-WARN", f"Save login prompt note: {e}", instance_name=instance_name)
    return False

async def try_click_saved_account_card(page: Page, instance_name="") -> bool:
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
                await asyncio.sleep(0.8)
                return True
    except Exception:
        pass
    return False

async def click_use_mobile_or_email(page: Page, context: BrowserContext, instance_name="") -> bool:
    pages_to_try = [page] + [p for p in context.pages if p != page and not p.is_closed()]

    for p in pages_to_try:
        try:
            if p.is_closed():
                continue

            try:
                text_btn = p.get_by_text("Use mobile number or email address", exact=False).first
                if await text_btn.is_visible(timeout=400):
                    log("ACTION", "Found 'Use mobile number or email address' via get_by_text. Clicking...", instance_name=instance_name)
                    await text_btn.click(force=True, timeout=1000)
                    await asyncio.sleep(1.0)
                    return True
            except Exception:
                pass

            exact_btn = p.locator("div[role='button'], [role='button'], button, a").filter(has_text=re.compile(r"Use mobile number or email address", re.I)).first
            if await exact_btn.is_visible(timeout=350):
                log("ACTION", "Clicking div[role='button'] 'Use mobile number or email address'...", instance_name=instance_name)
                try:
                    await exact_btn.click(timeout=1000)
                    await asyncio.sleep(1.0)
                    return True
                except Exception:
                    try:
                        await exact_btn.click(force=True, timeout=800)
                        await asyncio.sleep(1.0)
                        return True
                    except Exception:
                        pass

            clicked_js = await p.evaluate('''() => {
                const buttons = Array.from(document.querySelectorAll("div[role='button'], [role='button'], div[tabindex='0'], span, a, button"));
                for (const b of buttons) {
                    const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (txt.includes('facebook') || txt.includes('instagram')) continue;
                    if (txt === 'use mobile number or email address' || 
                        txt === 'use email address or mobile number' ||
                        txt.includes('use mobile number or email address') ||
                        txt.includes('use email address or mobile number') ||
                        txt.startsWith('use mobile number') ||
                        txt.startsWith('use email address')) {
                        const target = b.closest("[role='button']") || b;
                        target.scrollIntoView({ block: 'center', inline: 'center' });
                        target.click();
                        target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                        target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                        target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                        return true;
                    }
                }
                return false;
            }''')

            if clicked_js:
                log("ACTION", "Clicked 'Use mobile number or email address' via JS DOM execution!", instance_name=instance_name)
                await asyncio.sleep(1.0)
                return True

            btn_loc = p.locator("div[role='button']:has-text('Use mobile number or email address'), [role='button']:has-text('Use mobile number or email address'), span:has-text('Use mobile number or email address')").last
            if await btn_loc.is_visible(timeout=300):
                box = await btn_loc.bounding_box()
                if box:
                    click_x = box['x'] + box['width'] / 2
                    click_y = box['y'] + box['height'] / 2
                    log("ACTION", f"Clicking 'Use mobile number or email address' via coordinates ({click_x:.1f}, {click_y:.1f})...", instance_name=instance_name)
                    await p.mouse.click(click_x, click_y)
                    await asyncio.sleep(1.0)
                    return True

        except Exception as e:
            log("ACTION-WARN", f"click_use_mobile_or_email note: {e}", instance_name=instance_name)
            continue

    return False

async def handle_dob_selection(page: Page, instance_name="") -> bool:
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

async def is_otp_screen_active(page: Page) -> bool:
    try:
        if page.is_closed():
            return False
        
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

async def handle_dob_and_password_step(page: Page, context: BrowserContext, first_val: str, last_val: str, strong_password: str, instance_name="") -> bool:
    log("STEP-4", f"Starting Name ({first_val} {last_val}), DOB, and Password configuration...", instance_name=instance_name)
    
    for loop_idx in range(6):
        await asyncio.sleep(0.6)
        active_p = await get_active_meta_page(context, default_page=page)

        await handle_recaptcha_2captcha(active_p, context, instance_name=instance_name)

        if await is_otp_screen_active(active_p):
            log("STEP-4", "Detected OTP screen! Exiting Step 4 so OTP can be fetched and entered.", instance_name=instance_name)
            return True

        has_selfie = await active_p.locator(":has-text('Upload a verification selfie'), :has-text(\"Confirm that you're human\"), span:has-text('Upload Image'), div[aria-label='Upload Image']").count() > 0
        if has_selfie:
            log("STEP-4", "Detected verification selfie/human screen! Step 4 complete.", instance_name=instance_name)
            return True

        name_input_selectors = [
            "input[name*='firstname' i]", "input[name*='first_name' i]", "input[autocomplete='given-name']",
            "input[placeholder*='first name' i]", "input[aria-label*='first name' i]"
        ]
        lastname_input_selectors = [
            "input[name*='lastname' i]", "input[name*='last_name' i]", "input[name*='surname' i]", "input[autocomplete='family-name']",
            "input[placeholder*='last name' i]", "input[placeholder*='surname' i]", "input[aria-label*='last name' i]"
        ]
        fullname_input_selectors = [
            "input[name='name']", "input[name*='fullname' i]",
            "input[placeholder*='full name' i]", "input[placeholder*='name' i]", "input[aria-label*='name' i]"
        ]
        
        try:
            first_input = active_p.locator(name_input_selectors[0]).first
            if await first_input.is_visible(timeout=200):
                val = await first_input.input_value()
                if not val:
                    log("ACTION", f"Entering First & Last Name: {first_val} {last_val}", instance_name=instance_name)
                    await robust_fill(active_p, name_input_selectors, first_val, desc="First Name", timeout=600)
                    await robust_fill(active_p, lastname_input_selectors, last_val, desc="Last Name", timeout=600)
            else:
                full_input = active_p.locator(fullname_input_selectors[0]).first
                if await full_input.is_visible(timeout=200):
                    val = await full_input.input_value()
                    if not val:
                        log("ACTION", f"Entering Full Name: {first_val} {last_val}", instance_name=instance_name)
                        await robust_fill(active_p, fullname_input_selectors, f"{first_val} {last_val}", desc="Full Name", timeout=600)
        except Exception:
            pass

        try:
            has_dob = (
                await active_p.locator("select").count() >= 2 or
                await active_p.locator("[role='combobox'][aria-label*='month' i], [role='combobox'][aria-label*='day' i]").count() > 0 or
                await active_p.locator("input[name*='birthday' i], input[placeholder*='YYYY' i]").count() > 0
            )
            if has_dob:
                await handle_dob_selection(active_p, instance_name=instance_name)
        except Exception:
            pass

        try:
            pwd_inputs = active_p.locator("input[type='password'], input[name*='pass' i], input[autocomplete*='password' i], input[placeholder*='password' i]")
            pwd_count = await pwd_inputs.count()
            if pwd_count > 0:
                for p_idx in range(pwd_count):
                    p_loc = pwd_inputs.nth(p_idx)
                    if await p_loc.is_visible(timeout=200):
                        val = await p_loc.input_value()
                        if not val:
                            log("ACTION", f"Entering password into field #{p_idx+1}...", instance_name=instance_name)
                            await p_loc.fill(strong_password)
                            try:
                                await p_loc.evaluate("el => { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }")
                            except Exception:
                                pass
        except Exception:
            pass

        try:
            checkboxes = active_p.locator("input[type='checkbox']:not(:checked), [role='checkbox'][aria-checked='false']")
            cb_count = await checkboxes.count()
            if cb_count > 0:
                for c_idx in range(cb_count):
                    cb = checkboxes.nth(c_idx)
                    if await cb.is_visible(timeout=150):
                        log("ACTION", f"Checking agreement box #{c_idx+1}...", instance_name=instance_name)
                        await cb.click(force=True)
        except Exception:
            pass

        if await is_otp_screen_active(active_p):
            log("STEP-4", "Detected OTP screen! Exiting Step 4 without clicking Next.", instance_name=instance_name)
            return True

        await click_next_or_continue(active_p, timeout=1000)
        await asyncio.sleep(1.0)
        
        if await is_otp_screen_active(active_p):
            log("STEP-4", "Now on OTP screen! Step 4 complete.", instance_name=instance_name)
            return True

    return True

async def extract_meta_username(context: BrowserContext) -> str | None:
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
    for p in context.pages:
        try:
            if p.is_closed():
                continue
            url = p.url.lower()
            parsed = urllib.parse.urlparse(url)
            path = parsed.path.lower()

            if ("invalid_redirect" in url or "error=" in url or 
                "privacy" in path or "cookies" in path or "consent" in path or
                "privacy" in url or "consent" in url or "user_cookie_choice" in url):
                continue

            if await p.locator("[role='button']:has-text('Use mobile number'), input[placeholder*='Mobile' i], input[type='password'], input[name='contact'], :has-text('Continue with Facebook'), :has-text('Enter the code'), :has-text('Check your email'), :has-text('Allow the use of cookies from Meta'), span:has-text('Allow all cookies')").count() > 0:
                continue

            if path in ["/language", "/language/", "/settings", "/settings/", "/recover/success", "/recover/success/"] or "recover/success" in path:
                uname = await extract_meta_username(context) or "Meta_User"
                return True, uname

            if "meta.ai" in url and "auth.meta.com" not in url:
                uname = await extract_meta_username(context) or "Meta_User"
                return True, uname

            if await p.locator("a:has-text('Log Out'), button:has-text('Log Out'), [role='button']:has-text('Log Out'), a:has-text('LOG OUT'), button:has-text('LOG OUT')").count() > 0:
                if await p.locator("input[type='password'], input[name='contact']").count() == 0:
                    uname = await extract_meta_username(context) or "Meta_User"
                    return True, uname

            uname = await extract_meta_username(context)
            if uname:
                return True, uname
        except Exception:
            pass

    return False, ""

async def handle_recaptcha_2captcha(page: Page, context: BrowserContext, instance_name="") -> bool:
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

                for _ in range(12):
                    for sel in solver_selectors:
                        try:
                            loc = p.locator(sel).first
                            if await loc.is_visible(timeout=300):
                                solver_loc = loc
                                target_container = p
                                break
                        except Exception:
                            pass

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
                                except Exception:
                                    break

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
    try:
        if target_page.is_closed():
            return False

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

async def handle_human_verification(page: Page, context: BrowserContext, email="", password="", instance_name=""):
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

    max_verification_wait = 180
    pipeline_start = time.time()

    while time.time() - pipeline_start < max_verification_wait:
        active_page = target_page if (target_page and not target_page.is_closed()) else (await get_active_meta_page(context, default_page=page))

        await handle_recaptcha_2captcha(active_page, context, instance_name=instance_name)

        if not img_uploaded:
            img_uploaded = await try_auto_upload_image(active_page, instance_name=instance_name)
            if img_uploaded:
                post_upload_timer_start = time.time()
                log("IMG-UPLOAD", ">>> Image uploaded & Continue submitted! Max 30s wait timer started... <<<", instance_name)

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

        is_success, uname = await check_meta_success_state(context)
        if is_success:
            detected_user = uname
            log("SUCCESS", ">>> Registration & Verification SUCCESS detected! <<<", instance_name)
            break

        if post_upload_timer_start is not None:
            elapsed = time.time() - post_upload_timer_start
            if elapsed >= 30:
                log("SUCCESS", ">>> 30s wait elapsed after image upload. Assuming SUCCESS! <<<", instance_name)
                detected_user = (await extract_meta_username(context)) or "Meta_User"
                break

        await asyncio.sleep(0.8)

    log("HUMAN-VERIFY", "Verification pipeline finished.", instance_name)
    await asyncio.sleep(1.5)
    return True

def cleanup_profile_locks(profile_dir: str):
    if not os.path.exists(profile_dir):
        return
    lock_names = {"LOCK", "SingletonLock", "SingletonCookie", "SingletonSocket", "lock", ".parentlock", "parent.lock"}
    for root_dir, _, files in os.walk(profile_dir):
        for f in files:
            if f in lock_names or f.endswith(".lock") or "Singleton" in f or f == ".parentlock" or f.endswith(".pma"):
                try:
                    os.remove(os.path.join(root_dir, f))
                except Exception:
                    pass

def clean_profile_except_extensions(profile_dir: str):
    if not os.path.exists(profile_dir):
        return

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

    cleanup_profile_locks(profile_dir)

def get_worker_user_data_dir(instance_name: str) -> str:
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

# -------------- META CREATION TASK (100% Matching 1meta.ai create.py) --------------
async def create_meta_account_task(playwright: Playwright, first_name: str, last_name: str, password: str, instance_name="Inst-1"):
    user_data_dir = get_worker_user_data_dir(instance_name)
    cfg = load_config()
    configured_channel = cfg.get("browser_channel", "chrome")
    is_headless = cfg.get("headless", HEADLESS)
    vp_w = int(cfg.get("viewport_width", VIEWPORT_WIDTH))
    vp_h = int(cfg.get("viewport_height", VIEWPORT_HEIGHT))

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
        f"--window-size={vp_w},{vp_h}"
    ]

    context: BrowserContext = None
    channels_to_try = [configured_channel]
    for ch in ["chrome", "msedge", None]:
        if ch not in channels_to_try:
            channels_to_try.append(ch)

    for ch in channels_to_try:
        try:
            ch_desc = f"channel='{ch}'" if ch else "Chromium engine"
            log("WORKER", f"Launching browser with {ch_desc} ({user_data_dir})...", instance_name=instance_name)
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
        log("WORKER-ERR", f"Fatal: Could not launch browser for {instance_name}!", instance_name=instance_name)
        return False, "", ""

    if context.pages:
        page: Page = context.pages[0]
    else:
        page: Page = await context.new_page()

    try:
        await context.clear_cookies()
    except Exception:
        pass

    mail_token = ""
    try:
        email, _, mail_token = generate_temp_email(instance_name=instance_name)
        log("META-INIT", f"Target Email: {email} | Target Password: {password}", instance_name=instance_name)

        log("STEP-1", f"Navigating to {TARGET_URL}...", instance_name=instance_name)
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1.0)
        await handle_cookie_banners(page, instance_name=instance_name)
        await dismiss_meta_ai_dialog_or_popup(page, instance_name=instance_name)

        log("STEP-2", "Locating and clicking Sign Up / Log In button...", instance_name=instance_name)
        page = await get_active_meta_page(context, default_page=page)
        await dismiss_meta_ai_dialog_or_popup(page, instance_name=instance_name)

        if "meta.ai" in page.url.lower() and not ("auth.meta.com" in page.url.lower()):
            meta_ai_cta_selectors = [
                "button:has-text('Sign up')", "a:has-text('Sign up')",
                "button:has-text('Sign Up')", "a:has-text('Sign Up')",
                "div[role='button']:has-text('Sign up')", "button:has-text('Log in')",
                "a:has-text('Log in')", "div[role='button']:has-text('Log in')",
                "button:has-text('Log In')", "a:has-text('Log In')",
                "button[data-testid='login-button']", "a[href*='/login']", "a[href*='auth.meta.com']"
            ]
            for sel in meta_ai_cta_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=400):
                        log("ACTION", f"Clicking meta.ai auth CTA: '{sel}'...", instance_name=instance_name)
                        await btn.click(force=True, timeout=1000)
                        await asyncio.sleep(2.0)
                        break
                except Exception:
                    continue

        page = await get_active_meta_page(context, default_page=page)

        strict_email_check = (
            "input[name='contact'], "
            "input[placeholder*='mobile number or email address' i], "
            "input[aria-label*='mobile number or email address' i], "
            "input[placeholder*='email' i], "
            "input[type='email'], "
            "input[aria-label*='email' i], "
            "input[autocomplete*='email' i]"
        )

        email_screen_ready = False
        start_auth_wait = time.time()
        while time.time() - start_auth_wait < 25:
            page = await get_active_meta_page(context, default_page=page)
            
            if await page.locator(strict_email_check).first.is_visible(timeout=300):
                email_screen_ready = True
                break
                
            if "meta.ai" in page.url.lower() and not ("auth.meta.com" in page.url.lower()):
                await dismiss_meta_ai_dialog_or_popup(page, instance_name=instance_name)
                for sel in ["button:has-text('Sign up')", "button:has-text('Log in')", "button:has-text('Sign Up')", "button:has-text('Log In')", "a:has-text('Sign up')", "a:has-text('Log in')", "button[data-testid='login-button']"]:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=200):
                            await btn.click(force=True, timeout=600)
                            await asyncio.sleep(1.5)
                            break
                    except Exception:
                        pass
                page = await get_active_meta_page(context, default_page=page)

            clicked_opt = await click_use_mobile_or_email(page, context, instance_name=instance_name)
            if clicked_opt:
                log("ACTION", "Successfully clicked 'Use mobile number or email address'! Detecting email input field...", instance_name=instance_name)
                for _ in range(15):
                    page = await get_active_meta_page(context, default_page=page)
                    if await page.locator(strict_email_check).first.is_visible(timeout=200):
                        email_screen_ready = True
                        break
                    await asyncio.sleep(0.2)
                if email_screen_ready:
                    break

            await asyncio.sleep(0.4)

        page = await get_active_meta_page(context, default_page=page)

        log("STEP-3", f"Entering email: {email}", instance_name=instance_name)
        email_input_combined = (
            "input[name='contact'], "
            "input[placeholder*='mobile number or email address' i], "
            "input[aria-label*='mobile number or email address' i], "
            "input[type='email'], "
            "input[placeholder*='email' i], "
            "input[aria-label*='email' i], "
            "input[autocomplete*='email' i], "
            "input[autocomplete*='username' i], "
            "input[name*='email' i], "
            "input[id*='email' i]"
        )
        filled_email = await robust_fill(page, email_input_combined, email, desc="Email input", timeout=12000)
        if not filled_email:
            log("ERR", "Could not locate email input field.", instance_name=instance_name)
            return False, "", ""

        await asyncio.sleep(0.4)

        continue_combined = (
            "button[type='submit'], "
            "button:has-text('Continue'), "
            "button:has-text('Next'), "
            "div[role='button']:has-text('Continue'), "
            "div[role='button']:has-text('Next'), "
            "button:has-text('Sign up'), "
            "div[role='button']:has-text('Sign up')"
        )
        clicked_continue = await robust_click(page, continue_combined, desc="Email Continue Button", timeout=2000)
        if not clicked_continue:
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass
        await asyncio.sleep(1.5)

        page = await get_active_meta_page(context, default_page=page)
        await handle_recaptcha_2captcha(page, context, instance_name=instance_name)

        page = await get_active_meta_page(context, default_page=page)
        await handle_dob_and_password_step(page, context, first_name, last_name, password, instance_name=instance_name)
        await asyncio.sleep(1.0)

        page = await get_active_meta_page(context, default_page=page)
        await handle_recaptcha_2captcha(page, context, instance_name=instance_name)

        log("STEP-6", "Waiting for 6-digit OTP Verification...", instance_name=instance_name)
        otp_code = fetch_otp_from_temp_mail(email, password, token=mail_token, max_wait_sec=60, expected_digits=6, instance_name=instance_name)
        
        if not otp_code:
            log("ERR", "Failed to retrieve OTP code. Continuing to next cycle...", instance_name=instance_name)
            return False, "", ""

        log("OTP-ENTER", f"Entering OTP code: {otp_code}", instance_name=instance_name)
        page = await get_active_meta_page(context, default_page=page)

        digit_inputs = page.locator("input[data-index], input[aria-label*='digit' i], input[autocomplete*='one-time-code']")
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
            await robust_fill(page, otp_input_combined, otp_code, desc="OTP Input", timeout=8000)

        await asyncio.sleep(0.6)

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
        clicked_submit_otp = await robust_click(page, submit_otp_combined, desc="OTP Submit Button", timeout=2500)
        if not clicked_submit_otp:
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass

        log("OTP-POST-WAIT", "Waiting post-OTP submission and checking for 'Not now' / Cookie Banner / Captcha...", instance_name=instance_name)
        for _ in range(12):
            active_p = await get_active_meta_page(context, default_page=page)
            await handle_cookie_banners(active_p, instance_name=instance_name)
            clicked_not_now = await handle_save_login_info_prompt(active_p, context, instance_name=instance_name)
            await handle_recaptcha_2captcha(active_p, context, instance_name=instance_name)
            await dismiss_error_modals(active_p, instance_name=instance_name)
            if clicked_not_now:
                await asyncio.sleep(1.5)
            await asyncio.sleep(1)

        checkpoint_page = await get_active_meta_page(context, default_page=page)

        for attempt in range(1, 4):
            log("CHECKPOINT-NAV", f"Opening checkpoint URL (attempt {attempt}/3): {CHECKPOINT_REDIRECT_URL}", instance_name=instance_name)
            try:
                await checkpoint_page.goto(CHECKPOINT_REDIRECT_URL, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                log("CHECKPOINT-WARN", f"Checkpoint navigation note: {e}", instance_name=instance_name)

            await asyncio.sleep(2)

            await handle_recaptcha_2captcha(checkpoint_page, context, instance_name=instance_name)

            for p in context.pages:
                clicked_card = await try_click_saved_account_card(p, instance_name=instance_name)
                if clicked_card:
                    await asyncio.sleep(1.5)
                    break

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

        log("HUMAN-VERIFY", "Handling human verification / 2captcha / selfie...", instance_name=instance_name)
        await handle_human_verification(checkpoint_page, context, email=email, password=password, instance_name=instance_name)
        await asyncio.sleep(1.5)

        log("META-SUCCESS", f"Meta AI Account Created Successfully for {email}!", instance_name=instance_name)
        return True, email, mail_token

    except Exception as e:
        log("META-ERR", f"Meta creation exception: {e}", instance_name=instance_name)
        return False, "", ""
    finally:
        log("CYCLE-CLOSE", "Closing browser context completely...", instance_name=instance_name)
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

# ==============================================================================
# 📱 ADB এমুলেটর কন্ট্রোল ও সেটিংস (ইউজার এডিটেবল অপশন)
# ==============================================================================
def find_adb():
    """
    👉 পিসিতে ইনস্টল করা ADB (Android Debug Bridge) লোকেশন খুঁজে বের করে।
    আপনার LDPlayer বা অন্য কোনো এমুলেটরের adb.exe পাথ আলাদা ড্রাইভে থাকলে এখানে নতুন পাথ যোগ করতে পারেন।
    """
    adb_path = shutil.which("adb")
    if adb_path:
        return adb_path
    possible_paths = [
        r"D:\LDPlayer\LDPlayer14\adb.exe",                                           # LDPlayer 9/14 ডিফল্ট পাথ
        r"D:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",                    # MuMu Player 12
        r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
        r"C:\Program Files\MuMuPlayer-12.0\shell\adb.exe",
        r"C:\Program Files\MuMu\emulator\nemu\vbox\adb.exe",                         # MuMu Player 6
        r"C:\Program Files (x86)\MuMu\emulator\nemu\vbox\adb.exe",
        os.path.expanduser(r"~\Desktop\scrcpy-win64-v4.0\adb.exe"),
        os.path.expanduser(r"~\AppData\Local\Android\Sdk\platform-tools\adb.exe"),
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return "adb"

ADB_PATH = find_adb()

def get_unique_devices():
    """
    👉 সক্রিয় এমুলেটর পোর্ট (LDPlayer, MuMu Player, Nox, MEmu) দ্রুত প্যারালালে স্ক্যান ও সঠিক ডুপ্লিকেট রিমুভ করে।
    """
    ports = [
        5555, 5557, 5559, 5561, 5563, 5565,                           # LDPlayer standard ports
        7555, 16384, 16416, 16448, 16480, 16512,                     # MuMu Player
        62001, 62025, 62026,                                         # Nox Player
        21503, 21513, 21523                                          # MEmu Player
    ]

    from concurrent.futures import ThreadPoolExecutor
    def _quick_connect(p):
        try:
            subprocess.run(f'"{ADB_PATH}" connect 127.0.0.1:{p}', shell=True, capture_output=True, timeout=0.5)
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

    seen_ports = set()
    unique_devices = []

    for dev in raw_devices:
        canon_id = dev
        if dev.startswith("emulator-"):
            try:
                c_port = int(dev.split("-")[1])
                canon_id = f"port_{c_port + 1}"
            except Exception:
                canon_id = dev
        elif "127.0.0.1:" in dev or "localhost:" in dev:
            try:
                p_num = int(dev.split(":")[-1])
                canon_id = f"port_{p_num}"
            except Exception:
                canon_id = dev

        if canon_id not in seen_ports:
            seen_ports.add(canon_id)
            unique_devices.append(dev)

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

def type_text(text, device_serial=None, instance_name=""):
    if not text:
        return
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
    time.sleep(0.4)
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
        if '@' in t_lower or 'horizon' in t_lower or (email_user and email_user in t_lower) or any(k in t_lower for k in ["meta", "wombat", "qabq", "admiredental", "sugtbt", "uuf", "6n9", "end", "nqmo"]):
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
    elif "username, email or mobile number" in text_blob or ("forgot password?" in text_blob and "log in" in text_blob) or ("phone number, username, or email" in text_blob) or ("log in" in text_blob and ("password" in text_blob or "don't have an account" in text_blob or "sign up" in text_blob)):
        return "INSTAGRAM_LOGIN_PAGE", texts
    elif "allow all cookies" in text_blob or "only allow essential cookies" in text_blob or "decline optional cookies" in text_blob:
        return "INSTAGRAM_COOKIE_PAGE", texts
    elif "share everyday moments" in text_blob or "open instagram" in text_blob:
        return "INSTAGRAM_LANDING_PAGE", texts
    elif "welcome" in text_blob and ("agree" in text_blob or "terms" in text_blob):
        return "BROWSER_TERMS_PAGE", texts
    elif "turn on notifications" in text_blob or "know when people follow you" in text_blob:
        return "INSTAGRAM_NOTIFICATIONS_PAGE", texts
    elif "accounts/registered" in text_blob or "registered" in text_blob:
        return "INSTAGRAM_REGISTERED_PAGE", texts
    else:
        return "GENERIC_PAGE", texts

def fetch_otp_from_mail_td_service(email, password, max_wait_sec=60, expected_digits=None, instance_name=""):
    """Log in to Mail.td and poll inbox for Meta security code using mail_td module"""
    client = mail_td.MailTDClient()
    try:
        data = client.login(email, password)
        acc_id = data.get("id")
        jwt_token = data.get("token")
        if acc_id and jwt_token:
            token_str = f"MAILTD:{acc_id}:{jwt_token}"
            return mail_td.fetch_otp_mail_td(token_str, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)
    except Exception as e:
        log("MAILTD-WARN", f"Mail.td login/poll error for {email}: {e}", instance_name=instance_name)
    return None

def fetch_email_security_code(email, password, mail_token=None, max_wait_sec=60, expected_digits=8, instance_name=""):
    """
    Universal email security code retriever:
    Routes to Mail.td to fetch 8-digit security verification OTP for Instagram / 2FA.
    """
    log("EMAIL-CODE", f"Fetching 8-digit security code for {email} (timeout {max_wait_sec}s)...", instance_name=instance_name)
    return fetch_otp_from_temp_mail(email, password, token=mail_token, max_wait_sec=max_wait_sec, expected_digits=expected_digits, instance_name=instance_name)

def update_username_in_accounts_center(device_serial: str, target_username: str, instance_name=""):
    """
    Opens https://accountscenter.instagram.com/account_overview/
    1. Clicks on the Instagram profile card under Profiles.
    2. Clicks on 'Username'.
    3. Clears and types target_username into the username input field.
    4. Clicks 'Done' button.
    """
    log("UNAME-UPDATE", f"Starting username change to '{target_username}' via Accounts Center ({ACCOUNTS_OVERVIEW_URL})...", instance_name=instance_name)
    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{ACCOUNTS_OVERVIEW_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
    time.sleep(4.5)

    stage = "PROFILES_PAGE"
    start_time = time.time()
    max_wait = 45

    while time.time() - start_time < max_wait:
        root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
        if root is None:
            time.sleep(1.0)
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
                time.sleep(3.5)
            elif "profiles" in text_blob or "your account" in text_blob or "account settings" in text_blob:
                fx, fy = get_rel_coords(root, 0.50, 0.68)
                log("UNAME-UPDATE", f"Tapping fallback Instagram profile card at ({fx}, {fy})...", instance_name=instance_name)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                stage = "PROFILE_MENU"
                time.sleep(3.5)
            else:
                time.sleep(1.5)

        elif stage == "PROFILE_MENU":
            elem_uname = find_element(root, "username", exact=True) or find_element(root, "username")
            if elem_uname:
                log("UNAME-UPDATE", f"Found 'Username' menu item at ({elem_uname[0]}, {elem_uname[1]}). Tapping...", instance_name=instance_name)
                run_adb_command(f"shell input tap {elem_uname[0]} {elem_uname[1]}", device_serial=device_serial, instance_name=instance_name)
                stage = "EDIT_USERNAME"
                time.sleep(3.5)
            elif "name" in text_blob and "avatar" in text_blob:
                fx, fy = get_rel_coords(root, 0.50, 0.69)
                log("UNAME-UPDATE", f"Tapping fallback 'Username' menu item at ({fx}, {fy})...", instance_name=instance_name)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                stage = "EDIT_USERNAME"
                time.sleep(3.5)
            else:
                time.sleep(1.5)

        elif stage == "EDIT_USERNAME":
            log("UNAME-UPDATE", f"Entering target Taskly username '{target_username}'...", instance_name=instance_name)
            elem_edit = find_element(root, "username", by_class="EditText") or find_password_field(root)
            if elem_edit:
                run_adb_command(f"shell input tap {elem_edit[0]} {elem_edit[1]}", device_serial=device_serial, instance_name=instance_name)
            else:
                fx, fy = get_rel_coords(root, 0.50, 0.35)
                run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
            
            time.sleep(0.8)
            clear_input_field(length=40, device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.5)
            type_text(target_username, device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.2)
            hide_keyboard(device_serial=device_serial, instance_name=instance_name)
            time.sleep(0.8)

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
            
            time.sleep(2.0)
            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            p_name, _ = detect_current_page(root)
            if p_name == "CANNOT_MAKE_CHANGE_POPUP":
                log("UNAME-ERR", f"Encountered restriction '{p_name}'. Aborting...", instance_name=instance_name)
                return False

            log("UNAME-UPDATE", f"✔ Username successfully updated to '{target_username}' in Accounts Center!", instance_name=instance_name)
            stage = "DONE"
            break

    return True

# ==============================================================================
# INSTAGRAM & 2FA AUTOMATION PIPELINE (100% Matching 2fa create w2 save issue fix.py)
# ==============================================================================

def run_instagram_2fa_pipeline(device_serial: str, email: str, password: str, target_login: str, mail_token="", instance_name="Inst-1", task_context=None):
    log("ACC-START", f"Processing Instagram 2FA for: {email} on device {device_serial}...", instance_name=instance_name)

    log("STEP-1", f"Cleaning Via Browser data ({PACKAGE_NAME})...", instance_name=instance_name)
    run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(0.8)
    run_adb_command(f"shell pm clear {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
    time.sleep(1.2)

    log("STEP-2", f"Launching '{INSTA_LOGIN_URL}' in browser...", instance_name=instance_name)
    run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{INSTA_LOGIN_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
    time.sleep(3.5)

    login_submitted = False
    login_wait_count = 0
    agree_terms_submitted = False
    agree_retry_count = 0
    username_attempted = False
    auto_username_used = False
    captured_username = target_login
    two_factor_secret = ""
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
                if stuck_duration >= 30.0:
                    agree_retry_count += 1
                    if agree_retry_count <= 1:
                        log("TIMEOUT-WARN", f"Screen '{page_name}' stuck for {int(stuck_duration)}s after 'I agree'. Pressing Back to repeat process...", instance_name=instance_name)
                        run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)
                        agree_terms_submitted = False
                        last_page_name = ""
                        page_enter_time = time.time()
                        time.sleep(2.0)
                        continue
                    else:
                        log("TIMEOUT-ABORT", f"Screen '{page_name}' still stuck after retry (>30s). Aborting...", instance_name=instance_name)
                        return False, ""
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

                if page_stuck_back_count >= 4:
                    log("TIMEOUT-ABORT", f"Stuck on {page_name} >120s. Aborting account...", instance_name=instance_name)
                    return False, ""

        # --- State Handlers (matching 2fa create w2 save issue fix.py) ---
        if page_name == "BROWSER_TERMS_PAGE":
            log("STATE-ACTION", "Browser terms page -> Tapping AGREE...", instance_name=instance_name)
            tap_or_fallback_rel(root, "agree", 0.5, 0.88, "Agree Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(2.0)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{INSTA_LOGIN_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.0)
            run_adb_command("shell input keyevent 66", device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "INSTAGRAM_COOKIE_PAGE":
            log("STATE-ACTION", "Cookie banner detected -> Tapping 'Allow all cookies' / 'Decline'...", instance_name=instance_name)
            tap_or_fallback_rel(root, "allow all cookies", 0.5, 0.70, "Allow Cookies", device_serial=device_serial, instance_name=instance_name)
            time.sleep(2.5)

        elif page_name == "HOME_SCREEN":
            log("STATE-ACTION", "Home screen detected -> Re-opening instagram.com...", instance_name=instance_name)
            run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{INSTA_LOGIN_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.0)
            run_adb_command("shell input keyevent 66", device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name == "GENERIC_PAGE" and (loop_count in [2, 3] or stuck_duration >= 20.0):
            log("STATE-ACTION", f"Generic/Loading screen detected on loop #{loop_count} ({int(stuck_duration)}s). Ensuring URL loaded...", instance_name=instance_name)
            run_adb_command("shell input keyevent 66", device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)
            if stuck_duration >= 25.0:
                run_adb_command(f'shell am start -a android.intent.action.VIEW -d "{INSTA_LOGIN_URL}" {PACKAGE_NAME}', device_serial=device_serial, instance_name=instance_name)
                time.sleep(1.0)
                run_adb_command("shell input keyevent 66", device_serial=device_serial, instance_name=instance_name)
            time.sleep(3.5)

        elif page_name in ["CANNOT_FIND_ACCOUNT_POPUP", "INCORRECT_PASSWORD_POPUP", "ACCOUNT_ERROR_PAGE", "CANNOT_MAKE_CHANGE_POPUP"]:
            log("STATE-ABORT", f"Account restriction/issue detected '{page_name}'. Terminating current cycle immediately and restarting new task...", instance_name=instance_name)
            run_adb_command(f"shell am force-stop {PACKAGE_NAME}", device_serial=device_serial, instance_name=instance_name)
            return False, ""

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
            text_blob_curr = " ".join(visible_texts).lower()
            is_uname_error = any(err in text_blob_curr for err in [
                "choose a username", "not available", "isn't available", "isnt available",
                "another account", "try another", "can't use", "cant use"
            ])

            if not username_attempted:
                log("STATE-ACTION", f"'Create a username' screen -> Overwriting with Taskly username: '{target_login}'...", instance_name=instance_name)
                elem_uname = find_element(root, "username", by_class="EditText") or find_element(root, "create a username", by_class="EditText")
                if elem_uname:
                    run_adb_command(f"shell input tap {elem_uname[0]} {elem_uname[1]}", device_serial=device_serial, instance_name=instance_name)
                else:
                    fx, fy = get_rel_coords(root, 0.5, 0.44)
                    run_adb_command(f"shell input tap {fx} {fy}", device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.5)
                clear_input_field(length=40, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)
                type_text(target_login, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.5)
                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.3)

                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                tap_or_fallback_rel(root, "next", 0.5, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                username_attempted = True
                time.sleep(2.0)

                # Check if username is rejected ("choose a username to continue" / "not available" / still on page)
                root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                new_page, new_texts = detect_current_page(root)
                new_text_blob = " ".join(new_texts).lower()

                has_err = any(err in new_text_blob for err in [
                    "choose a username", "not available", "isn't available", "isnt available",
                    "another account", "try another", "can't use", "cant use"
                ])

                if has_err or new_page == "INSTAGRAM_USERNAME_PAGE":
                    log("USERNAME-WARN", f"Username '{target_login}' not accepted (Error shown)! Navigating Back (<) to regenerate auto-suggested username...", instance_name=instance_name)
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.4)
                    
                    # Tap top-left back arrow (<) or keyevent 4
                    back_btn = find_element(root, "back") or find_element(root, "go back")
                    if back_btn:
                        run_adb_command(f"shell input tap {back_btn[0]} {back_btn[1]}", device_serial=device_serial, instance_name=instance_name)
                    else:
                        fx_b, fy_b = get_rel_coords(root, 0.06, 0.19)
                        run_adb_command(f"shell input tap {fx_b} {fy_b}", device_serial=device_serial, instance_name=instance_name)
                        run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)
                    time.sleep(1.5)

                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    p_name, _ = detect_current_page(root)
                    if p_name == "INSTAGRAM_NAME_PAGE":
                        log("STATE-ACTION", "On Name screen -> Tapping 'Next' to trigger auto username...", instance_name=instance_name)
                        tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button", device_serial=device_serial, instance_name=instance_name)
                        time.sleep(2.0)

                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    log("STATE-ACTION", "On Username screen with auto-filled value -> Tapping 'Next'...", instance_name=instance_name)
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.3)
                    tap_or_fallback_rel(root, "next", 0.5, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                    auto_username_used = True
                    time.sleep(2.0)
            else:
                if is_uname_error:
                    log("USERNAME-WARN", "Username error detected on retry! Navigating Back (<) and Next for auto-username...", instance_name=instance_name)
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    time.sleep(0.4)

                    back_btn = find_element(root, "back") or find_element(root, "go back")
                    if back_btn:
                        run_adb_command(f"shell input tap {back_btn[0]} {back_btn[1]}", device_serial=device_serial, instance_name=instance_name)
                    else:
                        fx_b, fy_b = get_rel_coords(root, 0.06, 0.19)
                        run_adb_command(f"shell input tap {fx_b} {fy_b}", device_serial=device_serial, instance_name=instance_name)
                        run_adb_command("shell input keyevent 4", device_serial=device_serial, instance_name=instance_name)
                    time.sleep(1.5)

                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    tap_or_fallback_rel(root, "next", 0.5, 0.68, "Next Button", device_serial=device_serial, instance_name=instance_name)
                    time.sleep(2.0)

                    root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    tap_or_fallback_rel(root, "next", 0.5, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                    auto_username_used = True
                    time.sleep(2.0)
                else:
                    log("STATE-ACTION", "Tapping Next on Username page...", instance_name=instance_name)
                    hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                    tap_or_fallback_rel(root, "next", 0.5, 0.60, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
                    time.sleep(2.0)

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

        elif page_name in ["INSTAGRAM_REGISTERED_PAGE", "INSTAGRAM_SAVE_INFO_PAGE", "INSTAGRAM_CONNECT_FB_PAGE", "INSTAGRAM_NOTIFICATIONS_PAGE"]:
            if auto_username_used:
                log("STATE-ACTION", f"Registration complete with auto username! Opening Accounts Overview to change username to '{target_login}'...", instance_name=instance_name)
                update_username_in_accounts_center(device_serial=device_serial, target_username=target_login, instance_name=instance_name)
                auto_username_used = False

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
            log("STATE-ACTION", f"2FA: 'Check your email' -> Fetching 8-digit security code from inbox for {email}...", instance_name=instance_name)
            email_code = fetch_email_security_code(email, password, mail_token=mail_token, max_wait_sec=90, expected_digits=8, instance_name=instance_name)
            if not email_code:
                log("FAILED", f"✖ Email security code not received for {email}", instance_name=instance_name)
                return False, ""

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            refreshed_page, _ = detect_current_page(root)
            if refreshed_page == "TWO_FACTOR_SUCCESS_PAGE":
                log("SUCCESS", f"✔ 2FA already succeeded after email code wait for {email}!", instance_name=instance_name)
                return True, two_factor_secret

            log("STATE-ACTION", f"Entering email security code '{email_code}'...", instance_name=instance_name)
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

            run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.70)} {int(w*0.50)} {int(h*0.35)} 350",
                            device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.5)

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

            run_adb_command(f"shell input swipe {int(w*0.50)} {int(h*0.40)} {int(w*0.50)} {int(h*0.65)} 250",
                            device_serial=device_serial, instance_name=instance_name)
            time.sleep(1.0)

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
            time.sleep(3.5)

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

            # Generate OTP immediately using pyotp from 2FA Secret Key
            otp_to_fill = ""
            if two_factor_secret:
                otp_to_fill = generate_totp_code(two_factor_secret)
                log("2FA-OTP", f"🔑 Generated 6-Digit OTP via pyotp: '{otp_to_fill}'", instance_name=instance_name)
            elif task_context and task_context.get('otp_code'):
                otp_to_fill = task_context['otp_code']
                log("2FA-OTP", f"Using Bot 6-digit OTP: '{otp_to_fill}'", instance_name=instance_name)

            if otp_to_fill:
                type_text(otp_to_fill, device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.4)
                hide_keyboard(device_serial=device_serial, instance_name=instance_name)
                time.sleep(0.2)

            root = get_screen_hierarchy(device_serial=device_serial, instance_name=instance_name)
            tap_or_fallback_rel(root, "next", 0.50, 0.82, "Next Button", exact=True, device_serial=device_serial, instance_name=instance_name)
            time.sleep(2.0)

        elif page_name == "TWO_FACTOR_SUCCESS_PAGE":
            log("SUCCESS", f"✔ Instagram 2FA Setup Completed Successfully for {captured_username}! (Secret: {two_factor_secret})", instance_name=instance_name)
            save_credentials_csv(email, password, two_factor_secret, username=captured_username, instance_name=instance_name)
            return True, two_factor_secret

        else:
            time.sleep(2.0)

    return False, ""



# ==============================================================================
# MULTI-ACCOUNT TASKLY WORKER CLASS
# ==============================================================================
# Shared Telegram Client Cache by session name
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
        
        # Isolated state per worker
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
        self.success_count = 0
        self.cycle_count = 0

        self._register_handlers()

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
                log("FLOOD-WAIT", f"⚠️ Telegram FloodWait: Waiting {e.seconds}s before retrying send_message...", instance_name=self.instance_name)
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
                log("FLOOD-WAIT", f"⚠️ Telegram FloodWait on button click: Waiting {e.seconds}s...", instance_name=self.instance_name)
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
                        try:
                            await message.click(text=btn.text)
                            return True
                        except Exception:
                            pass
        return False

    async def click_button_by_pattern(self, pattern):
        """Finds and clicks any button matching pattern from cached message or recent dialog messages."""
        # 1. Check cached button message
        if self.task_confirm_btn_msg and self.task_confirm_btn_msg.buttons:
            for row in self.task_confirm_btn_msg.buttons:
                for btn in row:
                    if re.search(pattern, btn.text or "", re.IGNORECASE):
                        log("TG-BOT", f"👉 [Button Click on cached msg]: '{btn.text}'", instance_name=self.instance_name)
                        if await self.safe_click_button(btn):
                            return True

        # 2. Search latest recent messages from the bot
        try:
            recent_msgs = await self.client.get_messages(self.target_bot, limit=8)
            for msg in recent_msgs:
                if msg.buttons:
                    for row in msg.buttons:
                        for btn in row:
                            if re.search(pattern, btn.text or "", re.IGNORECASE):
                                log("TG-BOT", f"👉 [Button Click on msg #{msg.id}]: '{btn.text}'", instance_name=self.instance_name)
                                self.task_confirm_btn_msg = msg
                                if await self.safe_click_button(btn):
                                    return True
                                try:
                                    await msg.click(text=btn.text)
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

        # Identify which bot sent this message
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
        
        # 1. Welcome / Initial menu
        if "Welcome" in text or "Terms of Use" in text or "earn money" in text:
            if self.current_task.get('status') in ('running', 'credentials_ready'):
                return
            log("TG-STEP", "Welcome received -> Opening Tasks menu...", instance_name=self.instance_name)
            await asyncio.sleep(DELAY_BETWEEN_STEPS)
            clicked = await self.click_any_button(msg, r"Tasks|📋")
            if not clicked:
                log("TG-STEP", "Inline button not clicked. Sending '📋 Tasks' message fallback...", instance_name=self.instance_name)
                await self.safe_send_message(self.target_bot, "📋 Tasks")

        # 2. Select task
        elif "Please select a task" in text or "select a task" in text:
            if self.current_task.get('status') in ('running', 'credentials_ready'):
                return
            await asyncio.sleep(DELAY_BETWEEN_STEPS)
            
            is_taskly = "taskly" in (self.target_bot or "").lower()
            if is_taskly:
                log("TG-STEP", "Selecting option: '🔥 Create Inst (No mail) ($0.0300)' for @tasklyBux_bot...", instance_name=self.instance_name)
                clicked = await self.click_any_button(msg, r"No\s*mail|Create\s*Inst.*No\s*mail")
                if not clicked:
                    clicked = await self.click_any_button(msg, r"Create\s*Inst")
                if not clicked:
                    log("TG-STEP", "Sending task selection message fallback...", instance_name=self.instance_name)
                    await self.safe_send_message(self.target_bot, "🔥Create Inst (No mail) ($0.0300)")
            else:
                log("TG-STEP", "Selecting task '🔥Create Inst (2FA) ($0.0250)' for @PayGoeasy_bot...", instance_name=self.instance_name)
                clicked = await self.click_any_button(msg, r"2FA|Create\s*Inst.*2FA")
                if not clicked:
                    log("TG-STEP", "Sending task selection message fallback...", instance_name=self.instance_name)
                    await self.safe_send_message(self.target_bot, "🔥Create Inst (2FA) ($0.0250)")

        # 3. Start task (Trigger ▶️ Start to receive credentials)
        elif "Review time" in text or "Task: 🔥Create Inst" in text or "Task: 📱 Create Inst" in text or "Create Inst (2FA)" in text or "Task:" in text:
            if self.current_task.get('status') in ('running', 'credentials_ready'):
                return
            log("TG-STEP", "Task description received -> Triggering ▶️ Start...", instance_name=self.instance_name)
            await asyncio.sleep(DELAY_BETWEEN_STEPS)
            clicked = await self.click_any_button(msg, r"Start|▶️")
            if not clicked:
                log("TG-STEP", "Inline button not clicked. Sending '▶️ Start' message fallback to bot...", instance_name=self.instance_name)
                await self.safe_send_message(self.target_bot, "▶️ Start")

        # 4. Account Credentials Received (First name, Login, Password)
        elif re.search(r"First\s*name\s*:", text, re.IGNORECASE) and re.search(r"Login\s*:", text, re.IGNORECASE):
            log("TG-STEP", "Account Credentials Received from Bot! Triggering Meta AI creation...", instance_name=self.instance_name)
            fn_match = re.search(r"First\s*name\s*:\s*([^\r\n]+)", text, re.IGNORECASE)
            login_match = re.search(r"Login\s*:\s*([^\r\n]+)", text, re.IGNORECASE)
            pass_match = re.search(r"Password\s*:\s*([^\r\n]+)", text, re.IGNORECASE)
            
            raw_full_name = fn_match.group(1).strip().replace("`", "").replace("*", "").strip() if fn_match else "User"
            # Strip emojis or symbols so Meta AI form accepts the name smoothly
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
            
            print("\n" + "="*45)
            print(f"📋 [{self.instance_name}] [BOT CREDENTIALS ACQUIRED]")
            print(f"👤 Full Name  : {raw_full_name} -> {full_name} (First: {self.current_task['first_name']}, Last: {self.current_task['last_name']})")
            print(f"🔑 Target User: {self.current_task['login']}")
            print(f"🔒 Password   : {self.current_task['password']}")
            print("="*45 + "\n")
            
            self.task_received_event.set()
            
            if msg.buttons:
                is_taskly = "taskly" in (self.target_bot or "").lower()
                if not is_taskly:
                    await asyncio.sleep(DELAY_BETWEEN_STEPS)
                    await self.click_any_button(msg, r"Get code|📥")

        # 5. Bot asks for 2FA key ("Please enter your 2FA key to get the code:")
        elif "2FA key" in text or "enter your 2FA" in text:
            log("TG-STEP", "Bot is requesting 2FA Secret Key...", instance_name=self.instance_name)
            self.two_fa_requested_event.set()

        # 6. Bot sends OTP code
        elif "Your one-time code" in text or "Tap the code to copy" in text or "Press the button to confirm" in text or (len(re.findall(r"\b\d{6}\b", text)) > 0 and self.current_task.get('two_fa_key')):
            log("TG-STEP", "One-time OTP code received from bot!", instance_name=self.instance_name)
            code_match = re.search(r"\b\d{6}\b", text)
            if code_match:
                self.current_task['otp_code'] = code_match.group(0)
                log("TG-OTP", f"🔑 Captured 6-Digit OTP: {self.current_task['otp_code']}", instance_name=self.instance_name)
                self.otp_received_event.set()
                
            if msg.buttons:
                self.task_confirm_btn_msg = msg

        # 7. Task completed / Success / Report Received
        elif "report has been received" in text.lower() or "report received" in text.lower() or "report approved" in text.lower() or "your report has been received" in text.lower() or "Task Completed" in text or "credited to your balance" in text or "🎉" in text:
            log("TG-SUCCESS", "✅ Report Received & Accepted by Bot!", instance_name=self.instance_name)
            self.report_received_event.set()

        # 8. Rate Limit Handling
        elif "making requests too often" in text or "too often" in text.lower() or ("please wait" in text.lower() and "sec" in text.lower()):
            wait_match = re.search(r"wait\s*(\d+)\s*sec", text, re.IGNORECASE)
            wait_s = int(wait_match.group(1)) if wait_match else 20
            log("TG-RATE-LIMIT", f"⏳ Bot @{self.target_bot} rate-limited! (Cooldown {wait_s + 2}s)", instance_name=self.instance_name)
            
            if len(self.target_bots) > 1:
                self.switch_to_next_bot()
                log("TG-RATE-LIMIT", f"🚀 Rotating to next bot: @{self.target_bot}...", instance_name=self.instance_name)
                await asyncio.sleep(DELAY_BETWEEN_STEPS)
                await self.safe_send_message(self.target_bot, "/start")
            else:
                await asyncio.sleep(wait_s + 2)
                log("TG-RATE-LIMIT", "Resuming after rate limit pause. Sending /start...", instance_name=self.instance_name)
                await self.safe_send_message(self.target_bot, "/start")

        # 8b. Action Cancelled / Conversation Reset Handler (debounced)
        elif "previous conversation was reset" in text.lower() or "conversation was reset" in text.lower() or "action cancelled" in text.lower() or "action canceled" in text.lower():
            if self.current_task.get('status') in ('running', 'credentials_ready'):
                return
            now_ts = time.time()
            if (now_ts - getattr(self, '_last_start_reset_time', 0)) < 3.0:
                return
            self._last_start_reset_time = now_ts
            log("TG-STEP", "Conversation reset / Action cancelled received -> Resetting session with /start...", instance_name=self.instance_name)
            self.current_task['status'] = 'idle'
            self.task_received_event.clear()
            await asyncio.sleep(2.0)
            await self.safe_send_message(self.target_bot, "/start")

        # 9. Recovery on Invalid Key
        elif "Invalid key format" in text or "invalid key" in text.lower():
            log("TG-WARN", "Bot in key prompt error. Resetting menu...", instance_name=self.instance_name)
            self.current_task['status'] = 'idle'
            self.current_task['login'] = None
            self.current_task['two_fa_key'] = None
            self.task_received_event.clear()
            self.two_fa_requested_event.clear()
            self.otp_received_event.clear()
            await asyncio.sleep(2.0)
            await self.safe_send_message(self.target_bot, "/start")

        # 10. Time's up / Task cancelled from bot
        elif "Time's up" in text or "Task cancelled" in text or "task cancelled" in text.lower() or "time's up" in text.lower():
            log("TG-WARN", "⏰ Task cancelled by bot! Aborting task immediately...", instance_name=self.instance_name)
            self.current_task['status'] = 'cancelled'
            self.current_task['cancelled'] = True
            self.task_cancelled_event.set()
            self.task_received_event.set()
            self.two_fa_requested_event.set()
            self.otp_received_event.set()
            await asyncio.sleep(1.5)
            await self.safe_send_message(self.target_bot, "/start")
            
        elif msg.buttons:
            self.task_confirm_btn_msg = msg

    async def run_automation_loop(self, playwright: Playwright):
        cycle_num = 0
        while True:
            try:
                cycle_num += 1
                print("\n" + "#"*60)
                log("CYCLE-START", f"Starting Taskly-to-Meta-to-Instagram Cycle #{cycle_num} on device {self.device_serial}...", instance_name=self.instance_name)
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
                        log("STEP-1", f"Initializing task menu with @{self.target_bot} (/start)...", instance_name=self.instance_name)
                        await self.safe_send_message(self.target_bot, "/start")
                    else:
                        log("STEP-1", f"Requesting next task from @{self.target_bot}...", instance_name=self.instance_name)
                        btn_clicked = await self.click_button_by_pattern(r"Tasks|📋")
                        if not btn_clicked:
                            await self.safe_send_message(self.target_bot, "/start")
                    
                    log("WAIT", "Waiting for account credentials from Bot...", instance_name=self.instance_name)
                    try:
                        await asyncio.wait_for(self.task_received_event.wait(), timeout=35.0)
                    except asyncio.TimeoutError:
                        log("WARN", "Timeout waiting for credentials. Sending /start to reset...", instance_name=self.instance_name)
                        self.current_task['status'] = 'idle'
                        await self.safe_send_message(self.target_bot, "/start")
                        await asyncio.sleep(3.0)
                        continue

                if self.task_cancelled_event.is_set() or self.current_task.get('status') == 'cancelled':
                    log("CYCLE-ABORT", "Task was cancelled by bot. Starting fresh task from beginning...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    await self.safe_send_message(self.target_bot, "/start")
                    await asyncio.sleep(3.0)
                    continue

                self.current_task['status'] = 'running'
                first_name = self.current_task['first_name']
                last_name = self.current_task['last_name']
                target_login = self.current_task['login']
                target_password = self.current_task['password']

                # Step 2: Create Meta AI Account
                log("STEP-2", f"Creating Meta AI account for: {first_name} {last_name} | Password: {target_password}...", instance_name=self.instance_name)
                meta_ok, meta_email, meta_mail_token = await create_meta_account_task(playwright, first_name, last_name, target_password, instance_name=self.instance_name)
                if self.task_cancelled_event.is_set() or self.current_task.get('status') == 'cancelled':
                    log("CYCLE-ABORT", "Task cancelled during Meta creation. Resetting and starting fresh...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    await self.safe_send_message(self.target_bot, "/start")
                    await asyncio.sleep(3.0)
                    continue

                if not meta_ok or not meta_email:
                    log("ERR", "Meta AI account creation failed. Resetting bot task...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    self.current_task['login'] = None
                    self.current_task['two_fa_key'] = None
                    self.task_received_event.clear()
                    await self.safe_send_message(self.target_bot, "/start")
                    await asyncio.sleep(4.0)
                    continue

                # Step 3: Run Instagram 2FA Creation in Thread
                log("STEP-3", f"Running Instagram 2FA on emulator ({self.device_serial}) with Meta email: {meta_email} and Username: {target_login}...", instance_name=self.instance_name)
                loop = asyncio.get_running_loop()
                
                stop_key_exchange = asyncio.Event()

                async def handle_2fa_key_exchange():
                    while not self.current_task.get('two_fa_key') and not stop_key_exchange.is_set():
                        if self.task_cancelled_event.is_set() or self.current_task.get('status') == 'cancelled':
                            return
                        await asyncio.sleep(0.3)
                    
                    if stop_key_exchange.is_set() or self.task_cancelled_event.is_set() or self.current_task.get('status') == 'cancelled':
                        return

                    key = self.current_task.get('two_fa_key')
                    if key and len(key) >= 16:
                        log("TG-BOT", f"Sending extracted 2FA Secret Key '{key}' to @{self.target_bot}...", instance_name=self.instance_name)
                        await asyncio.sleep(DELAY_BETWEEN_STEPS)
                        await self.safe_send_message(self.target_bot, key)

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

                if self.task_cancelled_event.is_set() or self.current_task.get('status') == 'cancelled':
                    log("CYCLE-ABORT", "Task was cancelled during Instagram pipeline. Resetting and starting fresh...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    await self.safe_send_message(self.target_bot, "/start")
                    await asyncio.sleep(3.0)
                    continue

                if insta_ok:
                    log("STEP-4", "Instagram 2FA active & CSV saved! Confirming task completion in Bot...", instance_name=self.instance_name)
                    self.report_received_event.clear()
                    await asyncio.sleep(DELAY_BETWEEN_STEPS)

                    # Click '✅ Account Registered' button (using robust button search & fallback)
                    attempt = 0
                    max_confirm_attempts = 6
                    while not self.report_received_event.is_set() and attempt < max_confirm_attempts:
                        if self.task_cancelled_event.is_set() or self.current_task.get('status') == 'cancelled':
                            break
                        attempt += 1
                        log("TG-BOT", f"Clicking '✅ Account Registered' in Bot (Attempt {attempt}/{max_confirm_attempts})...", instance_name=self.instance_name)
                        clicked = await self.click_button_by_pattern(r"Account Registered|Registered|Confirm|✅")
                        if not clicked:
                            log("TG-WARN", "Button click failed, sending message text as fallback...", instance_name=self.instance_name)
                            await self.safe_send_message(self.target_bot, "✅ Account Registered")

                        try:
                            await asyncio.wait_for(self.report_received_event.wait(), timeout=4.0)
                            break
                        except asyncio.TimeoutError:
                            log("TG-WARN", f"⚠️ Report confirmation not received yet after attempt {attempt}. Retrying in 2.5s...", instance_name=self.instance_name)
                            await asyncio.sleep(2.5)

                    if self.report_received_event.is_set():
                        log("TG-BOT", "✔ Report confirmed and accepted by bot!", instance_name=self.instance_name)

                    self.success_count += 1
                    print("\n" + "="*50)
                    log("SUCCESS", f"🎉 [{self.instance_name}] CYCLE #{cycle_num} COMPLETE! (Instance Success: {self.success_count})", instance_name=self.instance_name)
                    log("SUCCESS", f"Meta Email : {meta_email}", instance_name=self.instance_name)
                    log("SUCCESS", f"Username   : {target_login}", instance_name=self.instance_name)
                    log("SUCCESS", f"Password   : {target_password}", instance_name=self.instance_name)
                    log("SUCCESS", f"2FA Secret : {secret_key}", instance_name=self.instance_name)
                    log("SUCCESS", f"Instance Total Success: {self.success_count}", instance_name=self.instance_name)
                    print("="*50 + "\n")
                    self.current_task['status'] = 'idle'
                    self.current_task['login'] = None
                    self.current_task['two_fa_key'] = None
                    self.current_task['otp_code'] = None
                else:
                    log("ERR", f"Instagram 2FA process failed for {target_login}. Resetting bot task...", instance_name=self.instance_name)
                    self.current_task['status'] = 'idle'
                    self.current_task['login'] = None
                    self.current_task['two_fa_key'] = None
                    self.task_received_event.clear()
                    await self.safe_send_message(self.target_bot, "/start")

                if not AUTO_REPEAT_TASKS:
                    log("MAIN", "Auto-repeat disabled. Finishing execution.", instance_name=self.instance_name)
                    break

                if len(self.target_bots) > 1:
                    self.switch_to_next_bot()

                log("WAIT", f"Waiting 3 seconds before starting next task cycle (Next Bot: @{self.target_bot})...", instance_name=self.instance_name)
                await asyncio.sleep(3.0)

            except Exception as cycle_err:
                log("CYCLE-RECOVER", f"Unexpected cycle error on {self.instance_name}: {cycle_err}. Auto-recovering in 5s...", instance_name=self.instance_name)
                try:
                    await self.safe_send_message(self.target_bot, "/start")
                except Exception:
                    pass
                await asyncio.sleep(5.0)

    async def run(self, playwright: Playwright):
        while True:
            try:
                await self.run_automation_loop(playwright)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log("WORKER-RECOVER", f"Worker {self.instance_name} recovered from exception: {e}. Restarting in 5s...", instance_name=self.instance_name)
                await asyncio.sleep(5.0)


# ==============================================================================
# MAIN ASYNC ENTRYPOINT (PARALLEL EXECUTION ACROSS BOTS & DEVICES)
# ==============================================================================
async def main_async():
    devices = get_unique_devices()
    if not devices:
        log("ERR", "No active ADB emulator device found! Please start LDPlayer/emulator.")
        return

    log("INIT", f"Detected {len(devices)} active ADB device(s): {', '.join(devices)}")
    log("INIT", f"Configured {len(TELEGRAM_ACCOUNTS)} Telegram account(s)")

    # Build unique parallel jobs list: 1 distinct account per worker/device
    jobs = []
    for acc in TELEGRAM_ACCOUNTS:
        acc_bots = acc.get("target_bots") or acc.get("target_bot") or TARGET_BOTS
        norm_bots = normalize_target_bots(acc_bots)
        jobs.append({"account": acc, "target_bot": norm_bots[0]})

    num_to_run = min(len(devices), len(jobs))
    log("INIT", f"Parallel Workers Scheduled: {num_to_run} (Devices: {len(devices)}, Accounts: {len(jobs)})")
    if num_to_run < len(devices):
        log("WARN", f"Found {len(devices)} device(s) but only {len(jobs)} unique Telegram account(s). Running {num_to_run} worker(s).")

    # 1. Connect unique Telegram clients sequentially first
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

    # 2. Run all workers concurrently in parallel
    log("INIT", f"🔥 Launching {len(workers)} worker(s) simultaneously in PARALLEL...")
    
    # Run Telegram listener background tasks for all unique clients
    tg_listeners = [asyncio.create_task(client.run_until_disconnected()) for client in CLIENT_CACHE.values()]
    
    async with async_playwright() as playwright:
        worker_tasks = [asyncio.create_task(worker.run(playwright)) for worker in workers]
        await asyncio.gather(*worker_tasks, return_exceptions=True)

def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log("MAIN", "Process stopped by user.")

if __name__ == "__main__":
    main()
