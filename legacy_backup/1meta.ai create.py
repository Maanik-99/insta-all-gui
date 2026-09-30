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

try:
    from argon2.low_level import hash_secret_raw, Type
    HAS_ARGON2 = True
except ImportError:
    HAS_ARGON2 = False

urllib3.disable_warnings()
csv_lock = threading.Lock()

# Configuration
TARGET_URL = "https://www.meta.ai/"
CHECKPOINT_REDIRECT_URL = "https://auth.meta.com/checkpoints/675270400317265/?next=https%3A%2F%2Fauth.meta.com%2Frecover%2Fsuccess%2F%3Fredirect_uri%3Dhttps%253A%252F%252Fauth.meta.com%252F"
HEADLESS = True  # Set to True for background headless mode
NUM_WORKERS = 3   # Number of parallel browser workers
VIEWPORT_WIDTH = 500    # Compact browser window width
VIEWPORT_HEIGHT = 700   # Compact browser window height

from email_service import (
    generate_temp_email,
    fetch_otp_from_temp_mail,
    extract_otp_from_text,
    get_random_name,
    load_names_from_csv,
    generate_custom_password,
    load_config,
    log,
    CONFIG_FILE,
    DEFAULT_CONFIG
)

from google_sheet_manager import gsheet_manager

def send_to_google_sheet(timestamp, email, username, password, instance_name=""):
    """Save created credentials to Google Sheet (Sheet1) using official Google Sheets API service"""
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
    """
    Universally locate and return the active Meta AI / Auth / Checkpoint page across all open tabs/popups.
    Brings the found page to front if necessary.
    """
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
    """
    Fast, reliable element clicking with fallback strategies:
    1. Direct fast click
    2. Fallback to force=True
    3. Fallback to JS click
    """
    if isinstance(selectors, str):
        query_candidates = [selectors]
    else:
        # Check combined comma-separated selector first for instant sub-millisecond evaluation
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
            
    # If combined failed and selectors is a list, try items individually with minimal wait
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
    """Type text with human cadence, randomized keystroke delays, and natural micro-pauses"""
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
    """
    Universally fill an input field smoothly without screen flickering:
    1. Human-type / Direct fill
    2. Fallback to clear + type
    3. Fallback to JS value set + trigger input events
    """
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

async def handle_cookie_banners(page: Page, instance_name="") -> bool:
    """Dismiss cookie consent banner by scrolling to the bottom and clicking 'Allow all cookies' or standard dismiss"""
    try:
        if page.is_closed():
            return False

        # 1. Scroll down to bottom
        try:
            await page.evaluate('''() => {
                window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' });
                if (document.documentElement) document.documentElement.scrollTop = document.documentElement.scrollHeight;
                if (document.body) document.body.scrollTop = document.body.scrollHeight;
            }''')
        except Exception:
            pass

        # Strategy 1: Targeted selectors
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

        # Strategy 2: Direct JS DOM Dispatch
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
    """Universally dismiss 'Something went wrong' popup by pressing Escape key first, then clicking OK / Close"""
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
    """Universally handle 'Save your login info?' screen by clicking the active 'Not now' button"""
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
                await asyncio.sleep(0.8)
                return True
    except Exception:
        pass
    return False

async def click_use_mobile_or_email(page: Page, context: BrowserContext, instance_name="") -> bool:
    """
    Specifically and reliably clicks 'Use mobile number or email address' button.
    Matches exact HTML structure: <div role="button" tabindex="0">Use mobile number or email address</div>
    """
    pages_to_try = [page] + [p for p in context.pages if p != page and not p.is_closed()]

    for p in pages_to_try:
        try:
            if p.is_closed():
                continue

            # Strategy 1: get_by_text directly on page
            try:
                text_btn = p.get_by_text("Use mobile number or email address", exact=False).first
                if await text_btn.is_visible(timeout=400):
                    log("ACTION", "Found 'Use mobile number or email address' via get_by_text. Clicking...", instance_name=instance_name)
                    await text_btn.click(force=True, timeout=1000)
                    await asyncio.sleep(1.0)
                    return True
            except Exception:
                pass

            # Strategy 2: Exact Playwright locator for div[role='button'] matching text
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

            # Strategy 3: Direct JavaScript exact text matching on div[role='button'] / span
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

            # Strategy 4: Bounding box coordinate click on the exact button
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
    """Universally and reliably select Month, Day, and Year on Meta DOB page without delay"""
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

async def handle_dob_and_password_step(page: Page, context: BrowserContext, strong_password: str, instance_name="") -> bool:
    """
    Handles Name, Birthday, Password, and Confirmation cleanly and rapidly,
    and IMMEDIATELY STOPS and exits once the OTP or Selfie verification screen is reached!
    """
    log("STEP-4", "Starting Name, DOB, and Password configuration...", instance_name=instance_name)
    first_val, last_val = get_random_name()
    
    for loop_idx in range(5):
        await asyncio.sleep(0.6)
        active_p = await get_active_meta_page(context, default_page=page)

        # Check for 2Captcha solver if present
        await handle_recaptcha_2captcha(active_p, context, instance_name=instance_name)

        # CHECK 1: If we reached OTP screen, STOP IMMEDIATELY!
        if await is_otp_screen_active(active_p):
            log("STEP-4", "Detected OTP screen! Exiting Step 4 so OTP can be fetched and entered.", instance_name=instance_name)
            return True

        # CHECK 2: If we reached Selfie/Human screen, STOP IMMEDIATELY!
        has_selfie = await active_p.locator(":has-text('Upload a verification selfie'), :has-text(\"Confirm that you're human\"), span:has-text('Upload Image'), div[aria-label='Upload Image']").count() > 0
        if has_selfie:
            log("STEP-4", "Detected verification selfie/human screen! Step 4 complete.", instance_name=instance_name)
            return True

        # 1. Handle Name Fields
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

        # 2. Handle DOB
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

        # 3. Handle Password
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

        # 4. Handle Checkboxes
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

        # Double check OTP again before clicking Next
        if await is_otp_screen_active(active_p):
            log("STEP-4", "Detected OTP screen! Exiting Step 4 without clicking Next.", instance_name=instance_name)
            return True

        # 5. Click Next / Continue / Submit
        await click_next_or_continue(active_p, timeout=1000)
        await asyncio.sleep(1.0)
        
        # Check if OTP page appeared after clicking Next
        if await is_otp_screen_active(active_p):
            log("STEP-4", "Now on OTP screen! Step 4 complete.", instance_name=instance_name)
            return True

    return True

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

            if ("invalid_redirect" in url or "error=" in url or 
                "privacy" in path or "cookies" in path or "consent" in path or
                "privacy" in url or "consent" in url or "user_cookie_choice" in url):
                continue

            if await p.locator("[role='button']:has-text('Use mobile number'), input[placeholder*='Mobile' i], input[type='password'], input[name='contact'], :has-text('Continue with Facebook'), :has-text('Enter the code'), :has-text('Check your email'), :has-text('Allow the use of cookies from Meta'), span:has-text('Allow all cookies')").count() > 0:
                continue

            # 1. Explicit logged-in URLs
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

async def handle_human_verification(page: Page, context: BrowserContext, email="", password="", instance_name=""):
    """
    Handle human verification & captcha pipeline (automated, non-blocking):
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
    max_verification_wait = 180
    pipeline_start = time.time()

    while time.time() - pipeline_start < max_verification_wait:
        active_page = target_page if (target_page and not target_page.is_closed()) else (await get_active_meta_page(context, default_page=page))

        # 1. Check if 2Captcha solver is present
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
        log("AUTO-SAVE", f"Auto-saving successful account to CSV & GSheet: Email='{email}' | Username='{final_username}'", instance_name)
        save_credentials_csv(email, password, username=final_username, instance_name=instance_name)

    log("HUMAN-VERIFY", "Verification pipeline finished. Continuing next cycle...", instance_name)
    await asyncio.sleep(1.5)
    return True

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

    # 2. Firefox Profile Cleaning
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

# -------------- MAIN REGISTRATION WORKER --------------
async def run_meta_registration_worker(playwright: Playwright, instance_name: str):
    """Run Meta AI registration workflow using robust multi-browser automation"""
    user_data_dir = get_worker_user_data_dir(instance_name)

    try:
        worker_num = int(re.search(r'\d+', instance_name).group(0)) if re.search(r'\d+', instance_name) else 1
    except Exception:
        worker_num = 1

    cfg = load_config()
    configured_channel = cfg.get("browser_channel", "chrome")
    is_headless = cfg.get("headless", HEADLESS)
    vp_w = int(cfg.get("viewport_width", VIEWPORT_WIDTH))
    vp_h = int(cfg.get("viewport_height", VIEWPORT_HEIGHT))

    col = (worker_num - 1) % 4
    row = (worker_num - 1) // 4
    pos_x = col * (vp_w + 15)
    pos_y = row * (vp_h // 2 + 40)

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

    channels_to_try = [configured_channel]
    for ch in ["chrome", "msedge", None]:
        if ch not in channels_to_try:
            channels_to_try.append(ch)

    cycle_count = 0

    while True:
        cycle_count += 1
        log("CYCLE-START", f"=================== CYCLE #{cycle_count} ({instance_name}) ===================", instance_name=instance_name)

        context: BrowserContext = None
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
            log("WORKER-ERR", f"Fatal: Could not launch browser for {instance_name}! Retrying in 5s...", instance_name=instance_name)
            await asyncio.sleep(5)
            continue

        if context.pages:
            page: Page = context.pages[0]
        else:
            page: Page = await context.new_page()

        try:
            await context.clear_cookies()
        except Exception:
            pass

        try:
            # 1. Generate credentials & email
            email, strong_password, mail_token = generate_temp_email(instance_name=instance_name)
            
            # Step 1: Open Meta AI
            log("STEP-1", f"Navigating to {TARGET_URL}...", instance_name=instance_name)
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.0)

            # Dismiss cookie banners & popups if present
            await handle_cookie_banners(page, instance_name=instance_name)
            await dismiss_meta_ai_dialog_or_popup(page, instance_name=instance_name)

            # Step 2: Click Sign In / Sign Up on meta.ai homepage
            log("STEP-2", "Locating and clicking Sign Up / Log In button...", instance_name=instance_name)
            page = await get_active_meta_page(context, default_page=page)
            await dismiss_meta_ai_dialog_or_popup(page, instance_name=instance_name)

            # If on meta.ai homepage, click top-right Sign up / Log in
            if "meta.ai" in page.url.lower() and not ("auth.meta.com" in page.url.lower()):
                meta_ai_cta_selectors = [
                    "button:has-text('Sign up')",
                    "a:has-text('Sign up')",
                    "button:has-text('Sign Up')",
                    "a:has-text('Sign Up')",
                    "div[role='button']:has-text('Sign up')",
                    "button:has-text('Log in')",
                    "a:has-text('Log in')",
                    "div[role='button']:has-text('Log in')",
                    "button:has-text('Log In')",
                    "a:has-text('Log In')",
                    "button[data-testid='login-button']",
                    "a[href*='/login']",
                    "a[href*='auth.meta.com']"
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

            # Switch to active page (auth.meta.com popup or redirect)
            page = await get_active_meta_page(context, default_page=page)

            # Step 2.5: Specifically click 'Use mobile number or email address' on auth.meta.com
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
                
                # If email input is already visible, we are ready!
                if await page.locator(strict_email_check).first.is_visible(timeout=300):
                    email_screen_ready = True
                    break
                    
                # If still on meta.ai homepage (CTA didn't trigger), re-click Sign up
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

                # Try clicking 'Use mobile number or email address'
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

            # Step 3: Enter Email
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
                continue

            await asyncio.sleep(0.4)

            # Click Continue after entering email
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

            # Check if 2Captcha / reCAPTCHA appeared
            page = await get_active_meta_page(context, default_page=page)
            await handle_recaptcha_2captcha(page, context, instance_name=instance_name)

            # Step 4: Handle Name, DOB, Password
            page = await get_active_meta_page(context, default_page=page)
            await handle_dob_and_password_step(page, context, strong_password, instance_name=instance_name)
            await asyncio.sleep(1.0)

            # Step 6: OTP Verification Page
            page = await get_active_meta_page(context, default_page=page)
            await handle_recaptcha_2captcha(page, context, instance_name=instance_name)

            log("STEP-6", "Waiting for OTP Verification...", instance_name=instance_name)
            otp_code = fetch_otp_from_temp_mail(email, strong_password, token=mail_token, max_wait_sec=60, instance_name=instance_name)
            
            if not otp_code:
                log("ERR", "Failed to retrieve OTP code. Continuing to next cycle...", instance_name=instance_name)
                save_credentials_csv(email, strong_password, username="OTP_FETCH_FAILED", instance_name=instance_name)
                continue

            log("OTP-ENTER", f"Entering OTP code: {otp_code}", instance_name=instance_name)
            page = await get_active_meta_page(context, default_page=page)

            # Strategy A: Check for individual 6-box OTP digits
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
                # Strategy B: Single OTP input
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

            # Submit OTP ONLY after filling it
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

            # ---------------- POST-OTP CONTINUOUS MONITORING (matching update-global.py) ----------------
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

            # ---------------- CHECKPOINT URL NAVIGATION (matching update-global.py) ----------------
            checkpoint_page = await get_active_meta_page(context, default_page=page)

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

            # ---------------- AUTOMATED HUMAN VERIFICATION (matching update-global.py) ----------------
            log("HUMAN-VERIFY", "Handling human verification / 2captcha / selfie...", instance_name=instance_name)
            hv_triggered = await handle_human_verification(checkpoint_page, context, email=email, password=strong_password, instance_name=instance_name)
            await asyncio.sleep(1.5)

            if not hv_triggered:
                detected_uname = await extract_meta_username(context) or ""
                save_credentials_csv(email, strong_password, username=detected_uname, instance_name=instance_name)
            
            log("CYCLE-COMPLETE", f"Cycle #{cycle_count} finished successfully!", instance_name=instance_name)

        except Exception as e:
            log("CYCLE-ERR", f"Error during cycle #{cycle_count}: {e}", instance_name=instance_name)

        finally:
            log("CYCLE-CLOSE", f"Closing browser context completely for cycle #{cycle_count}...", instance_name=instance_name)
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
    channel = cfg.get("browser_channel", "chrome")
    mode_str = "Headless (Background ON)" if is_headless else "Headful (Window Visible OFF)"
    log("INIT", f"Starting Playwright Meta AI Automation with {workers_count} parallel browser workers | Channel='{channel}' | Mode: {mode_str}")
    if gsheet_manager.is_configured():
        ok, msg = gsheet_manager.connect()
        if ok:
            log("INIT-GSHEET", f"Google Sheet API connected successfully! Mode: {gsheet_manager.mode}. Accounts will sync to '{gsheet_manager.input_sheet_name}'.")
        else:
            log("GSHEET-WARN", f"Google Sheet connection failed: {msg}. Falling back to local credentials.csv.")
    else:
        log("GSHEET-NOTE", "Google Sheet not configured (spreadsheet_id is empty in config.json). Accounts will save to credentials.csv.")

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
