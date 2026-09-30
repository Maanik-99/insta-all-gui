#!/usr/bin/env python3
"""
Meta Auth & Registration Automation - Complete Pipeline.
Fully self-contained client for automating Meta Account / Kadabra registration:
  1. Mail.td Temp Email & Argon2 PoW Account Generation
  2. Envelope Password Encryption (TweetNaCl + AES-256-GCM) & Falco Telemetry
  3. Pre-registration Checks (Contact Point, Password, DOB, Username Suggestion, Profile/TOS)
  4. Account Registration (kadabra-register-save-credentials)
  5. GraphQL Email OTP Verification (FRLConfirmEmailMutation)
  6. Post-OTP Checkpoint Detection (polling & waiting for 'Confirm you are human')
  7. UFAC GraphQL Checkpoint Solving (FRLUFACSubmitActionMutation + 2Captcha + Image Upload)
  8. OIDC Authorization & Session Completion
  9. Structured Persistence to meta_accounts.json and accounts.txt
"""

import os
import sys
import time
import json
import uuid
import base64
import random
import string
import logging
import hashlib
import re
from typing import Dict, Any, Optional, Tuple, List
from urllib.parse import urlencode, quote, urlparse, parse_qs

try:
    from curl_cffi import requests
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False

try:
    import nacl.public
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

try:
    from mail_td import generate_mail_td_account, fetch_otp_mail_td
    HAS_MAIL_TD = True
except ImportError:
    HAS_MAIL_TD = False

try:
    from NINJA import TempMailNinjaAPI
    HAS_NINJA = True
except ImportError:
    try:
        from ninja import TempMailNinjaAPI
        HAS_NINJA = True
    except ImportError:
        HAS_NINJA = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s"
)
logger = logging.getLogger("MetaAuth")


# =====================================================================
# 0. CONFIGURATION & EMAIL PROVIDER OPTIONS
# =====================================================================

# Email Provider Configuration (choose "ninja", "mail_td", or "off"):
#   - "ninja"   : TempMail Ninja (Socket.IO real-time inbox & instant OTP)
#   - "mail_td" : Mail.td (Argon2 PoW mailbox & inbox polling)
#   - "off"     : Disabled (uses random email without auto-temp mail generation)
EMAIL_PROVIDER = "ninja"  # "ninja", "mail_td", or "off"

# On/Off boolean toggles (takes priority if explicitly set):
USE_NINJA = True      # Set True to use TempMail Ninja
USE_MAIL_TD = False   # Set True to use Mail.td

API_KEY_2CAPTCHA = "1d0543cab02e928f092f4f24736efd85"
DEFAULT_RECAPTCHA_SITE_KEY = "6LdktRgnAAAAAFQ6icovYI2-masYLFjEFyzQzpix"
DOC_ID_CONFIRM_EMAIL = "9851798224911796"
DOC_ID_UFAC_CHECKPOINT = "28029617096670386"
BASE_DIR = os.getcwd()
DEFAULT_IMAGE_PATH = os.path.join(BASE_DIR, "image.jpg")
ACCOUNTS_JSON_PATH = os.path.join(BASE_DIR, "meta_accounts.json")
ACCOUNTS_TXT_PATH = os.path.join(BASE_DIR, "accounts.txt")


# =====================================================================
# 1. 2CAPTCHA SOLVER MODULE WITH RETRIES & CLEAN URL
# =====================================================================

import urllib.request

class TwoCaptchaSolver:
    """
    Automated 2Captcha solver for reCAPTCHA Enterprise on Meta / Facebook.
    Uses robust urllib for API calls and RecaptchaV2EnterpriseTaskProxyless.
    """

    DEFAULT_IFRAME_URL = "https://www.fbsbx.com/captcha/recaptcha/iframe/?transparent_bg=true&referer=https%253A%252F%252Fauth.meta.com&locale=en_GB"

    def __init__(self, api_key: str = API_KEY_2CAPTCHA):
        self.api_key = api_key

    def _http_post_json(self, url: str, data: dict, timeout: int = 25) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _http_post_form(self, url: str, data: dict, timeout: int = 25) -> dict:
        req = urllib.request.Request(
            url,
            data=urlencode(data).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            try:
                return json.loads(text)
            except Exception:
                return {"status": 0, "request": text}

    def _http_get_json(self, url: str, timeout: int = 25) -> dict:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def solve_recaptcha_v2(
        self,
        site_key: str = DEFAULT_RECAPTCHA_SITE_KEY,
        page_url: str = DEFAULT_IFRAME_URL,
        max_retries: int = 3,
        max_wait_sec: int = 120
    ) -> Optional[str]:
        target_url = page_url or self.DEFAULT_IFRAME_URL

        for attempt in range(1, max_retries + 1):
            logger.info(f"[2Captcha] Attempt {attempt}/{max_retries}: Submitting reCAPTCHA Enterprise (sitekey: {site_key[:12]}..., url: {target_url[:60]}...)...")

            # 1. Modern API (RecaptchaV2EnterpriseTaskProxyless)
            try:
                create_resp = self._http_post_json(
                    "https://api.2captcha.com/createTask",
                    {
                        "clientKey": self.api_key,
                        "task": {
                            "type": "RecaptchaV2EnterpriseTaskProxyless",
                            "websiteURL": target_url,
                            "websiteKey": site_key
                        }
                    }
                )

                if create_resp.get("errorId") == 0 and "taskId" in create_resp:
                    task_id = create_resp["taskId"]
                    logger.info(f"[2Captcha] Task created (ID: {task_id}). Polling for solution...")
                    start_time = time.time()
                    time.sleep(10)

                    while time.time() - start_time < max_wait_sec:
                        poll = self._http_post_json(
                            "https://api.2captcha.com/getTaskResult",
                            {"clientKey": self.api_key, "taskId": task_id}
                        )

                        if poll.get("status") == "ready":
                            token = poll.get("solution", {}).get("gRecaptchaResponse")
                            logger.info(f"[2Captcha] Captcha solved in {int(time.time() - start_time)}s!")
                            return token
                        elif poll.get("status") != "processing":
                            logger.warning(f"[2Captcha] Task status: {poll}")
                            break
                        time.sleep(5)
            except Exception as e:
                logger.warning(f"[2Captcha] Modern API exception: {e}")

            # 2. Legacy API Fallback
            try:
                in_resp = self._http_post_form(
                    "https://2captcha.com/in.php",
                    {
                        "key": self.api_key,
                        "method": "userrecaptcha",
                        "googlekey": site_key,
                        "pageurl": target_url,
                        "enterprise": 1,
                        "json": 1
                    }
                )
                if in_resp.get("status") == 1:
                    captcha_id = in_resp.get("request")
                    logger.info(f"[2Captcha] Legacy Task ID: {captcha_id}. Polling...")
                    start_time = time.time()
                    time.sleep(10)

                    while time.time() - start_time < max_wait_sec:
                        res_resp = self._http_get_json(
                            f"https://2captcha.com/res.php?key={self.api_key}&action=get&id={captcha_id}&json=1"
                        )
                        if res_resp.get("status") == 1:
                            token = res_resp.get("request")
                            logger.info(f"[2Captcha] Solved token received via legacy API!")
                            return token
                        elif res_resp.get("request") != "CAPCHA_NOT_READY":
                            logger.warning(f"[2Captcha] Legacy response: {res_resp}")
                            break
                        time.sleep(5)
            except Exception as e:
                logger.warning(f"[2Captcha] Legacy API exception: {e}")

            logger.warning(f"[2Captcha] Attempt {attempt} failed, retrying in 3s...")
            time.sleep(3)

        return None


# =====================================================================
# 2. CRYPTO & ENVELOPE ENCRYPTION
# =====================================================================

def format_pwd_browser(encrypted_b64: str, timestamp: Optional[int] = None, version: int = 5) -> str:
    ts = timestamp or int(time.time())
    return f"#PWD_BROWSER:{version}:{ts}:{encrypted_b64}"


def encrypt_meta_password(
    password: str,
    public_key_hex: str,
    key_id: int = 199,
    timestamp: Optional[int] = None
) -> str:
    if not HAS_NACL or not HAS_CRYPTOGRAPHY:
        raise RuntimeError("nacl and cryptography packages are required for password encryption")

    ts = timestamp or int(time.time())
    ts_str = str(ts)

    aes_key = os.urandom(32)
    iv = bytes(12)
    aesgcm = AESGCM(aes_key)
    aad = ts_str.encode("utf-8")
    plaintext = password.encode("utf-8")

    ciphertext_and_tag = aesgcm.encrypt(iv, plaintext, aad)
    ciphertext = ciphertext_and_tag[:-16]
    tag = ciphertext_and_tag[-16:]

    recipient_pk_bytes = bytes.fromhex(public_key_hex)
    recipient_pk = nacl.public.PublicKey(recipient_pk_bytes)
    sealed_box = nacl.public.SealedBox(recipient_pk)
    sealed_key = sealed_box.encrypt(aes_key)

    sealed_len = len(sealed_key)

    envelope = bytearray()
    envelope.append(1)                  # Version = 1
    envelope.append(int(key_id))        # Key ID
    envelope.append(sealed_len & 0xFF)  # Length low byte (0x50)
    envelope.append((sealed_len >> 8) & 0xFF)  # Length high byte (0x00)
    envelope.extend(sealed_key)         # 80 bytes sealed key
    envelope.extend(tag)                # 16 bytes auth tag
    envelope.extend(ciphertext)         # Ciphertext

    b64_payload = base64.b64encode(envelope).decode("utf-8")
    return format_pwd_browser(b64_payload, timestamp=ts)


def compute_jazoest(token: str) -> str:
    if not token:
        return "22206"
    return "2" + str(sum(ord(c) for c in token))


# =====================================================================
# 3. FALCO TELEMETRY
# =====================================================================

def build_falco_payload(
    event_name: str,
    event_data: Dict[str, Any],
    client_session_id: str,
    web_session_id: str = "npdr3i:q5lguu:55za44",
    claim: str = "hmac_ttl.1789134777.AR1GWI4H8HqheGesK9mJOlXPT5j4lcjCd4vFk0DAT3lhNJOv"
) -> Tuple[str, str]:
    now_ms = time.time() * 1000
    post_item = [
        event_name,
        {
            "e": json.dumps(event_data),
            "r": 1,
            "d": "fd.ARvsnj8V0mnfNo2m10abybzPIxHOsKjvFZTY6hCzdfR5bSZczDsdeGF6Q55k3ROaFJ2oCMsf8z_TGy97tRAQzjXy",
            "s": web_session_id,
            "t": now_ms,
            "a": "1047.303.469.0 (1047303469)",
            "b": [1, 128],
            "id": {
                "appScopedIdentity": "0",
                "claim": claim
            }
        },
        now_ms,
        0,
        1200
    ]

    q_array = [
        {
            "app_id": "713378262994144",
            "posts": [post_item],
            "trigger": event_name,
            "user": "0",
            "webSessionId": web_session_id
        }
    ]

    return str(int(now_ms)), json.dumps(q_array)


# =====================================================================
# 4. META AUTH CLIENT
# =====================================================================

def generate_unique_device_fingerprint() -> Dict[str, Any]:
    """
    Generates a coherent, unique browser & OS device fingerprint per account.
    Randomizes:
      - Browser (Chrome 120-131, Edge 122-130)
      - OS Platform (Windows 10/11, macOS 14/15)
      - Matching TLS Impersonation (chrome124, chrome120, edge101, safari17_0)
      - Screen dimensions, color depth, pixel ratio
      - Client Hints (sec-ch-ua, sec-ch-ua-platform, sec-ch-ua-mobile)
      - Accept-Language headers
      - Hardware profile (concurrency, memory)
    """
    platforms = [
        {
            "os_name": "Windows",
            "platform_header": '"Windows"',
            "ua_os": "Windows NT 10.0; Win64; x64",
            "impersonates": ["chrome124", "chrome120", "chrome119", "edge101"]
        },
        {
            "os_name": "macOS",
            "platform_header": '"macOS"',
            "ua_os": "Macintosh; Intel Mac OS X 10_15_7",
            "impersonates": ["chrome124", "chrome120", "safari17_0", "safari18_0"]
        }
    ]

    selected_platform = random.choice(platforms)
    chrome_version = random.choice([120, 122, 124, 125, 126, 128, 130, 131])
    build_minor = random.randint(5000, 6999)
    patch_ver = random.randint(100, 199)
    full_version = f"{chrome_version}.0.{build_minor}.{patch_ver}"

    is_edge = random.random() < 0.25 and selected_platform["os_name"] == "Windows"

    if is_edge:
        browser_name = "Microsoft Edge"
        sec_ch_ua = f'"Microsoft Edge";v="{chrome_version}", "Chromium";v="{chrome_version}", "Not-A.Brand";v="99"'
        ua = f"Mozilla/5.0 ({selected_platform['ua_os']}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_version} Safari/537.36 Edg/{full_version}"
        impersonate = "edge101" if "edge101" in selected_platform["impersonates"] else "chrome124"
    else:
        browser_name = "Google Chrome"
        sec_ch_ua = f'"Google Chrome";v="{chrome_version}", "Chromium";v="{chrome_version}", "Not-A.Brand";v="99"'
        ua = f"Mozilla/5.0 ({selected_platform['ua_os']}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_version} Safari/537.36"
        impersonate = random.choice(selected_platform["impersonates"])

    languages = [
        "en-US,en;q=0.9",
        "en-GB,en-US;q=0.9,en;q=0.8",
        "en-CA,en-US;q=0.9,en;q=0.8",
        "en-AU,en-US;q=0.9,en;q=0.8",
        "en-US,en;q=0.9,bn;q=0.8",
        "en-US,en;q=0.9,es;q=0.8",
        "en-US,en;q=0.9,fr;q=0.8"
    ]
    accept_lang = random.choice(languages)

    screens = [
        {"w": 1920, "h": 1080, "dpr": 1},
        {"w": 2560, "h": 1440, "dpr": 1},
        {"w": 1366, "h": 768, "dpr": 1},
        {"w": 1536, "h": 864, "dpr": 1.25},
        {"w": 1440, "h": 900, "dpr": 2},
        {"w": 1680, "h": 1050, "dpr": 1},
        {"w": 1920, "h": 1200, "dpr": 1}
    ]
    screen = random.choice(screens)

    return {
        "os": selected_platform["os_name"],
        "browser": f"{browser_name} {chrome_version}",
        "user_agent": ua,
        "sec_ch_ua": sec_ch_ua,
        "sec_ch_ua_platform": selected_platform["platform_header"],
        "sec_ch_ua_mobile": "?0",
        "accept_language": accept_lang,
        "impersonate": impersonate,
        "screen_width": screen["w"],
        "screen_height": screen["h"],
        "device_pixel_ratio": screen["dpr"],
        "hardware_concurrency": random.choice([4, 8, 12, 16]),
        "device_memory": random.choice([8, 16, 32]),
    }


class MetaAuthClient:
    """
    Automates the Meta Account / Kadabra registration, OTP verification,
    checkpoint detection, and UFAC GraphQL / Captcha resolution flow on auth.meta.com.
    """

    BASE_URL = "https://auth.meta.com"
    SOURCE_APP_ID = "1522763855472543"
    TOS_CMS_ID = "957798449862312"

    def __init__(self, proxy: Optional[str] = None, impersonate: Optional[str] = None, captcha_api_key: str = API_KEY_2CAPTCHA):
        self.fingerprint = generate_unique_device_fingerprint()
        self.impersonate = impersonate or self.fingerprint["impersonate"]

        if USE_CURL_CFFI:
            try:
                self.session = requests.Session(impersonate=self.impersonate)
            except Exception:
                self.session = requests.Session(impersonate="chrome124")
        else:
            self.session = requests.Session()

        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

        self.waterfall_id = str(uuid.uuid4())
        self.client_session_id = self._generate_client_session_id()
        self.device_id: Optional[str] = None
        self.email: Optional[str] = None
        self.password: Optional[str] = None
        self.username: Optional[str] = None
        self.req_counter = 1

        self.captcha_solver = TwoCaptchaSolver(api_key=captcha_api_key)

        self.code_verifier = self._generate_client_session_id() + self._generate_client_session_id()
        self.code_challenge = self._generate_code_challenge(self.code_verifier)
        self.oidc_redirect_uri = self._build_oidc_redirect_uri()

        self.lsd: Optional[str] = None
        self.jazoest: Optional[str] = None
        self.public_key: Optional[str] = None
        self.key_id: int = 199
        self.rcs: Optional[str] = None
        self.jurisdiction: str = "BD"
        self.reg_integrity: Optional[str] = None
        self.fb_dtsg: Optional[str] = None
        self.created_user_id: Optional[str] = None
        self.checkpoint_url: Optional[str] = None

        self.rev = "1047303469"
        self.hsi = "7684275360328234786"
        self.hs = "20707.HYP:frl_comet_auth_pkg.2.1...0"
        self.s = "npdr3i:q5lguu:55za44"
        self.spin_r = "1047303469"
        self.spin_b = "trunk"
        self.spin_t = str(int(time.time()))
        self.dyn = "7xe6E5aQ1Py8s-3eaQ412nwvG1Ww4KwhE2LwFwn82nwb-q2i5U4e1FxO0SE2Bw46wrU6C0AU1OU1vE2Yw45w43w16-0Co5G0zE1AE17U1eE1ko38w4mw4ewCw3mE1hE3Pw"
        self.csr = ""
        self.hsdp = ""
        self.hblp = ""
        self.sjsp = ""

        self._init_default_headers()

    def _generate_client_session_id(self) -> str:
        chars = string.ascii_letters + string.digits + "_-"
        return "".join(random.choices(chars, k=24))

    def _generate_code_challenge(self, verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

    def _build_oidc_redirect_uri(self) -> str:
        state_dict = {
            "csrf_token": "".join(random.choices(string.ascii_letters + string.digits + "-_", k=43)),
            "redirect_to": "https://meta.ai/oidc/callback",
            "started_at": int(time.time() * 1000),
            "waterfall_id": self.waterfall_id
        }
        state_str = base64.b64encode(json.dumps(state_dict).encode("utf-8")).decode("utf-8")
        params = {
            "app_id": self.SOURCE_APP_ID,
            "redirect_uri": "https://auth.meta.ai/ecto",
            "response_type": "code",
            "scope": "openid linking",
            "state": state_str,
            "waterfall_id": self.waterfall_id,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256"
        }
        return f"{self.BASE_URL}/oidc/?{urlencode(params)}"

    def get_cookies_dict(self) -> Dict[str, str]:
        try:
            if hasattr(self.session.cookies, "get_dict"):
                return self.session.cookies.get_dict()
            return {c.name: c.value for c in self.session.cookies}
        except Exception:
            return {}

    def get_cookies_string(self) -> str:
        cookies_dict = self.get_cookies_dict()
        return "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

    def _get_cookie(self, name: str) -> str:
        return self.get_cookies_dict().get(name, "")

    def _generate_qpl_join_id(self) -> str:
        return str(random.randint(10**18, 10**19 - 1))

    def _next_req(self) -> str:
        val = hex(self.req_counter)[2:]
        self.req_counter += 1
        return val

    def _get_current_referer(self) -> str:
        ref = (
            f"{self.BASE_URL}/?waterfall_id={self.waterfall_id}"
            f"&redirect_uri={quote(self.oidc_redirect_uri)}"
            f"&source_app_id={self.SOURCE_APP_ID}&force_reauth=0"
        )
        if self.rcs:
            ref += f"&rcs={self.rcs}"
        return ref

    def _init_default_headers(self):
        self.session.headers.update({
            "accept": "*/*",
            "accept-language": self.fingerprint["accept_language"],
            "origin": self.BASE_URL,
            "referer": self._get_current_referer(),
            "sec-ch-prefers-color-scheme": "dark",
            "sec-ch-ua": self.fingerprint["sec_ch_ua"],
            "sec-ch-ua-mobile": self.fingerprint["sec_ch_ua_mobile"],
            "sec-ch-ua-platform": self.fingerprint["sec_ch_ua_platform"],
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": self.fingerprint["user_agent"],
            "x-asbd-id": "359341",
        })

    def _get_base_spin_params(self) -> Dict[str, Any]:
        token_for_jazoest = self.fb_dtsg if self.fb_dtsg else (self.lsd or "AdS4dILcliXDKFeDHlE3nhTH-OQ")
        jazoest_val = compute_jazoest(token_for_jazoest)

        params = {
            "__user": "0",
            "__a": "1",
            "__req": self._next_req(),
            "__hs": self.hs,
            "dpr": "1",
            "__ccg": "GOOD",
            "__rev": self.rev,
            "__s": self.s,
            "__hsi": self.hsi,
            "__dyn": self.dyn,
            "__csr": self.csr,
            "__hsdp": self.hsdp,
            "__hblp": self.hblp,
            "__sjsp": self.sjsp,
            "__comet_req": "33",
            "lsd": self.lsd or "AdS4dILcliXDKFeDHlE3nhTH-OQ",
            "jazoest": jazoest_val,
            "__spin_r": self.spin_r,
            "__spin_b": self.spin_b,
            "__spin_t": self.spin_t,
            "__jssesw": "1",
        }
        if self.fb_dtsg:
            params["fb_dtsg"] = self.fb_dtsg
        return params

    def init_session(self, oidc_redirect_uri: Optional[str] = None) -> bool:
        """Step 1: Loads the initial landing page to acquire datr cookies, LSD, dynamic jazoest, rcs, and public keys."""
        logger.info("Initializing Meta Auth session...")
        landing_url = f"{self.BASE_URL}/"
        if oidc_redirect_uri:
            self.oidc_redirect_uri = oidc_redirect_uri

        params = {
            "waterfall_id": self.waterfall_id,
            "redirect_uri": self.oidc_redirect_uri,
            "source_app_id": self.SOURCE_APP_ID,
            "force_reauth": "0"
        }

        doc_headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
        }

        try:
            resp = self.session.get(landing_url, params=params, headers=doc_headers)
            logger.info(f"Landing page response status: {resp.status_code}")
            text = resp.text

            cookies_dict = self.get_cookies_dict()
            if cookies_dict:
                logger.info(f"Session cookies acquired: {list(cookies_dict.keys())}")

            if "rcs=" in resp.url:
                self.rcs = resp.url.split("rcs=")[-1].split("&")[0]
                logger.info(f"Extracted RCS signature: {self.rcs[:30]}...")

            lsd_match = re.search(r'\["LSD",\[\],\{"token":"([^"]+)"\}', text)
            if not lsd_match:
                lsd_match = re.search(r'name="lsd"\s+value="([^"]+)"', text)
            if lsd_match:
                self.lsd = lsd_match.group(1)
                self.jazoest = compute_jazoest(self.lsd)
                logger.info(f"Extracted LSD: {self.lsd} | Calculated Jazoest: {self.jazoest}")

            pk_match = re.search(r'publicKey":"([a-f0-9]{64})"', text)
            if pk_match:
                self.public_key = pk_match.group(1)
            key_id_match = re.search(r'keyId":(\d+)', text)
            if key_id_match:
                self.key_id = int(key_id_match.group(1))

            juris_match = re.search(r'regulation_jurisdiction":\["([^"]+)"\]', text)
            if juris_match:
                self.jurisdiction = juris_match.group(1)

            self.session.headers["referer"] = self._get_current_referer()
            return True
        except Exception as e:
            logger.error(f"Failed to initialize auth session: {e}")
            return False

    def send_falco_event(self, event_name: str, event_data: Dict[str, Any]) -> bool:
        try:
            ts, q_str = build_falco_payload(event_name, event_data, self.client_session_id)
            url = f"{self.BASE_URL}/ajax/bz"
            params = self._get_base_spin_params()
            params["ph"] = "C3"

            boundary = "----WebKitFormBoundary" + "".join(random.choices(string.ascii_letters + string.digits, k=16))
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="ts"\r\n\r\n'
                f"{ts}\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="q"\r\n\r\n'
                f"{q_str}\r\n"
                f"--{boundary}--\r\n"
            )

            headers = {
                "content-type": f"multipart/form-data; boundary={boundary}",
                "referer": self._get_current_referer(),
            }

            self.session.post(url, params=params, data=body.encode("utf-8"), headers=headers)
            return True
        except Exception:
            return False

    def encrypt_password(self, password: str, timestamp: Optional[int] = None) -> str:
        pk = self.public_key or "19c30cf2379e56b8ab368d6f2479cb893a09a023db2d062b2686bb8761881e47"
        return encrypt_meta_password(
            password=password,
            public_key_hex=pk,
            key_id=self.key_id,
            timestamp=timestamp
        )

    def check_contact_point_availability(self, email: str, birthday: Optional[str] = None) -> Dict[str, Any]:
        logger.info(f"Checking availability for contact point: {email}")
        self.email = email

        self.send_falco_event("falco:caa_acquisition_client_events_rl", {
            "acquisition_client_params": {
                "entry_point": "login_home",
                "is_feta_ntm": True,
                "reg_instance": self._get_cookie("datr"),
                "regulation_jurisdiction": [self.jurisdiction],
                "state": "contactpoint_attempt"
            },
            "core": {
                "access_flow_version": "pre_mt_behavior",
                "contactpoint": email,
                "event": "registration_flow_state",
                "event_category": "registration_funnel",
                "event_flow": "ntm",
                "event_step": "contactpoint",
                "logged_out_identifier": email,
                "rl_client_session_id": self.client_session_id,
                "source_app_id": int(self.SOURCE_APP_ID),
                "waterfall_id": self.waterfall_id,
                "event_request_id": str(uuid.uuid4())
            }
        })

        url = f"{self.BASE_URL}/api/check-contact-point-availability/"
        data = {
            "allow_fallback": "true",
            "caa_event_flow": "ntm",
            "contact_point": email,
            "contact_point_type": "EMAIL_ADDRESS",
            "device_id": "",
            "enable_youth_consent_flow": "true",
            "entry_point": "login_home",
            "event_client_time": f"{time.time():.2f}",
            "google_id_token": "",
            "has_youth_consent": "false",
            "is_bootstrap_flow": "false",
            "is_kadabra_zero": "false",
            "qpl_join_id": self._generate_qpl_join_id(),
            "reg_navigation_flow_name": "new_to_family_c50_r1",
            "regulation_jurisdiction": json.dumps([self.jurisdiction]),
            "source_app_id": self.SOURCE_APP_ID,
            "waterfall_id": self.waterfall_id,
        }
        if birthday:
            data["date_of_birth"] = birthday
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }

        resp = self.session.post(url, data=data, headers=headers)
        logger.info(f"Contact point check response [{resp.status_code}]: {resp.text[:250]}")
        parsed = self._parse_fb_response(resp.text)

        if isinstance(parsed, dict) and "payload" in parsed:
            payload = parsed.get("payload")
            if isinstance(payload, dict):
                reg_int = payload.get("regIntegrity") or payload.get("reg_integrity")
                if reg_int:
                    self.reg_integrity = reg_int
                    logger.info(f"Captured reg_integrity token: {self.reg_integrity[:30]}...")

                restrictions = payload.get("restrictions")
                if isinstance(restrictions, dict):
                    res_data = restrictions.get("restriction_data")
                    if isinstance(res_data, dict):
                        reg_info = res_data.get("account_reg_info")
                        if isinstance(reg_info, dict) and "device_id" in reg_info:
                            self.device_id = reg_info["device_id"]
                            logger.info(f"Captured device_id: {self.device_id}")

                if "contactPoint" in payload and payload["contactPoint"]:
                    self.email = payload["contactPoint"]
                    logger.info(f"Updated email to normalized contact point: {self.email}")
                else:
                    self.email = email

        return parsed

    def check_password(self, email: str, password_raw_or_payload: str, dob: str = "2000-01-01") -> Dict[str, Any]:
        logger.info("Validating password...")
        url = f"{self.BASE_URL}/api/check-password/"
        self.password = password_raw_or_payload

        if password_raw_or_payload.startswith("#PWD_BROWSER:"):
            password_payload = password_raw_or_payload
        else:
            password_payload = self.encrypt_password(password_raw_or_payload)

        data = {
            "contact_point": email,
            "date_of_birth": dob,
            "name": "",
            "password": password_payload,
            "contact_pointless_account": "false",
            "qpl_join_id": self._generate_qpl_join_id(),
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self.session.post(url, data=data, headers=headers)
        logger.info(f"Check password response [{resp.status_code}]: {resp.text[:250]}")
        return self._parse_fb_response(resp.text)

    def check_date_of_birth(self, email: str, dob: str = "2000-01-01") -> Dict[str, Any]:
        logger.info(f"Checking DOB eligibility: {dob}")
        url = f"{self.BASE_URL}/api/check-date-of-birth/"

        data = {
            "caa_event_flow": "ntm",
            "date_of_birth": dob,
            "email": email,
            "first_name": "",
            "has_youth_consent": "false",
            "isf": "false",
            "last_name": "",
            "phone_number": "",
            "qpl_join_id": self._generate_qpl_join_id(),
            "reg_integrity": self.reg_integrity or "",
            "source_app_id": self.SOURCE_APP_ID,
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self.session.post(url, data=data, headers=headers)
        logger.info(f"Check DOB response [{resp.status_code}]: {resp.text[:250]}")
        parsed = self._parse_fb_response(resp.text)
        if isinstance(parsed, dict) and "payload" in parsed:
            payload = parsed.get("payload")
            if isinstance(payload, dict):
                reg_int = payload.get("regIntegrity") or payload.get("reg_integrity")
                if reg_int:
                    self.reg_integrity = reg_int
                    logger.info(f"Updated reg_integrity token from DOB: {self.reg_integrity[:30]}...")
        return parsed

    def check_marketing_opt_in(self, dob: str = "2000-01-01") -> Dict[str, Any]:
        url = f"{self.BASE_URL}/api/check-marketing-opt-in/"
        data = {
            "caa_event_flow": "ntm",
            "date_of_birth": dob,
            "opt_in_marketing": "false",
            "qpl_join_id": self._generate_qpl_join_id(),
            "reg_integrity": self.reg_integrity or "",
            "source_app_id": self.SOURCE_APP_ID,
        }
        data.update(self._get_base_spin_params())
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self.session.post(url, data=data, headers=headers)
        return self._parse_fb_response(resp.text)

    def suggest_username(self) -> Dict[str, Any]:
        logger.info("Requesting suggested username...")
        url = f"{self.BASE_URL}/api/kadabra/suggest_username/"

        data = {
            "caa_event_flow": "ntm",
            "device_id": self.device_id or "",
            "first_name": "",
            "last_name": "",
            "qpl_join_id": self._generate_qpl_join_id(),
            "reg_integrity": self.reg_integrity or "",
            "source_app_id": self.SOURCE_APP_ID,
            "waterfall_id": self.waterfall_id,
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self.session.post(url, data=data, headers=headers)
        parsed = self._parse_fb_response(resp.text)
        logger.info(f"Suggest username response: {parsed}")
        return parsed

    def check_profile(self, username: str) -> Dict[str, Any]:
        logger.info(f"Validating profile handle: {username}")
        self.username = username
        url = f"{self.BASE_URL}/api/kadabra/check_profile/"

        data = {
            "caa_event_flow": "ntm",
            "display_name": "",
            "foa_import_source_name": "",
            "foa_import_source_obid": "",
            "nta_disclosures_summary_cms_id": "",
            "picture_source": "",
            "qpl_join_id": self._generate_qpl_join_id(),
            "tos_cms_id": self.TOS_CMS_ID,
            "username": username,
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self.session.post(url, data=data, headers=headers)
        parsed = self._parse_fb_response(resp.text)
        logger.info(f"Check profile response: {parsed}")
        return parsed

    def register_save_credentials(
        self,
        email: str,
        password_raw_or_payload: str,
        username: str,
        dob: str = "2000-01-01",
        redirect_uri: Optional[str] = None
    ) -> Dict[str, Any]:
        logger.info(f"Submitting final registration for {email} ({username})...")
        url = f"{self.BASE_URL}/login/device-based/kadabra-register-save-credentials/"
        self.email = email
        self.password = password_raw_or_payload
        self.username = username

        if password_raw_or_payload.startswith("#PWD_BROWSER:"):
            password_payload = password_raw_or_payload
        else:
            password_payload = self.encrypt_password(password_raw_or_payload)

        data = {
            "client_consent_timestamp": str(int(time.time())),
            "display_name": "",
            "foa_import_source_name": "",
            "foa_import_source_obid": "",
            "nta_disclosures_summary_cms_id": "",
            "picture_source": "",
            "tos_cms_id": self.TOS_CMS_ID,
            "username": username,
            "consent_version": "",
            "contact_point": email,
            "contact_point_type": "EMAIL_ADDRESS",
            "csi": self.client_session_id,
            "date_of_birth": dob,
            "device_id": self.device_id or "",
            "fb_encrypted_access_token": "",
            "fb_oidc_access_token": "",
            "first_name": "",
            "google_id_token": "",
            "has_youth_consent": "false",
            "ig_encrypted_access_token": "",
            "ig_encrypted_auth_header": "",
            "ig_oidc_access_token": "",
            "last_name": "",
            "opt_into_marketing": "false",
            "password": password_payload,
            "redirect_uri": redirect_uri or self.oidc_redirect_uri,
            "reg_integrity": self.reg_integrity or "",
            "should_save_credentials": "true",
            "source_app_id": self.SOURCE_APP_ID,
            "third_party_age_verification_id": "",
            "waterfall_id": self.waterfall_id,
            "caa_event_flow": "ntm",
            "entry_point": "login_home",
            "event_client_time": f"{time.time():.2f}",
            "is_kadabra_zero": "false",
            "reg_navigation_flow_name": "new_to_family_c50_r1",
            "regulation_jurisdiction": json.dumps([self.jurisdiction]),
            "qpl_join_id": self._generate_qpl_join_id(),
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self.session.post(url, data=data, headers=headers)
        logger.info(f"Registration response status [{resp.status_code}]: {resp.text[:300]}")

        cookies_dict = self.get_cookies_dict()
        logger.info(f"Session cookies: {list(cookies_dict.keys())}")

        parsed = self._parse_fb_response(resp.text)
        if isinstance(parsed, dict):
            if "dtsgToken" in parsed:
                self.fb_dtsg = parsed["dtsgToken"]
                logger.info(f"Captured authenticated fb_dtsg token: {self.fb_dtsg[:30]}...")

            if "payload" in parsed:
                payload = parsed.get("payload", {})
                if isinstance(payload, dict):
                    user_id = payload.get("account_id") or payload.get("uid") or payload.get("user_id")
                    if user_id:
                        self.created_user_id = str(user_id)
                        logger.info(f"Successfully registered Meta Account ID: {self.created_user_id}")

                    if "checkpoint_url" in payload:
                        self.checkpoint_url = payload["checkpoint_url"]
                    elif "redirect_url" in payload and "checkpoint" in payload["redirect_url"]:
                        self.checkpoint_url = payload["redirect_url"]

        return parsed

    def confirm_email_otp(self, code: str, email: Optional[str] = None) -> Dict[str, Any]:
        target_email = email or self.email
        clean_code = str(code).strip()
        logger.info(f"Submitting email confirmation OTP '{clean_code}' for {target_email}...")
        variables = {
            "input": {
                "confirmation_code": {
                    "sensitive_string_value": clean_code
                },
                "confirmation_code_type": "OTP_CODE",
                "event_flow": "ntm",
                "rl_client_session_id": self.client_session_id,
                "source_app_id": self.SOURCE_APP_ID,
                "waterfall_id": self.waterfall_id
            }
        }
        resp = self.send_graphql_request(doc_id=DOC_ID_CONFIRM_EMAIL, variables=variables)
        
        if isinstance(resp, dict):
            data = resp.get("data", {})
            confirm_email = data.get("confirm_email", {}) if isinstance(data, dict) else {}
            if isinstance(confirm_email, dict):
                acc_id = confirm_email.get("accountId") or confirm_email.get("account_id")
                if acc_id:
                    self.created_user_id = str(acc_id)
                if confirm_email.get("checkpoint_url"):
                    self.checkpoint_url = confirm_email.get("checkpoint_url")
        return resp

    def send_graphql_request(self, doc_id: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/api/graphql/"
        data = {
            "doc_id": doc_id,
            "variables": json.dumps(variables),
        }
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }
        resp = self.session.post(url, data=data, headers=headers)
        return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else self._parse_fb_response(resp.text)

    # =====================================================================
    # CHECKPOINT DETECTION & UFAC RESOLVER
    # =====================================================================

    def check_for_checkpoint(self, wait_sec: int = 4) -> Optional[str]:
        """
        Checks if Meta has placed the account in a checkpoint.
        Waits wait_sec seconds then verifies response and redirect URLs.
        """
        logger.info(f"[*] Checking for async checkpoint status (waiting {wait_sec}s)...")
        time.sleep(wait_sec)

        doc_headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "referer": self._get_current_referer()
        }

        try:
            resp = self.session.get(f"{self.BASE_URL}/", headers=doc_headers, allow_redirects=True)
            if "checkpoint" in resp.url.lower() or "confirm that you're human" in resp.text.lower() or "checkpoints" in resp.text.lower():
                logger.info(f"[!] Checkpoint detected at: {resp.url}")
                self.checkpoint_url = resp.url
                return resp.url

            resp_oidc = self.session.get(self.oidc_redirect_uri, headers=doc_headers, allow_redirects=True)
            if "checkpoint" in resp_oidc.url.lower() or "confirm that you're human" in resp_oidc.text.lower():
                logger.info(f"[!] Checkpoint detected via OIDC: {resp_oidc.url}")
                self.checkpoint_url = resp_oidc.url
                return resp_oidc.url

        except Exception as e:
            logger.warning(f"Error checking checkpoint: {e}")

        return None

    def extract_captcha_persist_data(self, resp_text: str) -> str:
        """Extracts captcha_persist_data from UFAC GraphQL response."""
        match = re.search(r'"(?:bot_)?captcha_persist_data"\s*:\s*"([^"]+)"', resp_text)
        if match:
            persist_val = match.group(1)
            logger.info(f"[UFAC] Extracted captcha_persist_data via regex: {persist_val[:30]}...")
            return persist_val

        try:
            clean = resp_text[9:] if resp_text.startswith("for (;;);") else resp_text
            data = json.loads(clean)
            for root_key in ["ufac_client_submit_action", "ufac_submit_action", "ufac_action"]:
                node = data.get("data", {}).get(root_key, {})
                if isinstance(node, dict):
                    state = node.get("ufac_client", {}).get("state", {}) if "ufac_client" in node else node.get("state", {})
                    if isinstance(state, dict):
                        persist = state.get("captcha_persist_data") or state.get("bot_captcha_persist_data") or state.get("persist_data")
                        if persist:
                            logger.info(f"[UFAC] Extracted captcha_persist_data from JSON: {persist[:30]}...")
                            return str(persist)
        except Exception:
            pass

        return "..."

    def resolve_ufac_checkpoint(
        self,
        checkpoint_url: Optional[str] = None,
        image_path: str = DEFAULT_IMAGE_PATH
    ) -> bool:
        """
        Solves the Meta UFAC Checkpoint:
        1. Sends FRLUFACSubmitActionMutation with action: PROCEED.
        2. Solves reCAPTCHA v2 using 2Captcha.
        3. Submits SUBMIT_BOT_CAPTCHA_RESPONSE mutation with token + image upload.
        """
        page_url = checkpoint_url or self.checkpoint_url or f"{self.BASE_URL}/checkpoint/"
        actor_id = self.created_user_id or self.get_cookies_dict().get("c_user") or "1227049917168618"
        lsd = self.lsd or "RyHFQ20E53MNpQQ7jYBChR"
        fb_dtsg = self.fb_dtsg or ""
        jazoest = compute_jazoest(fb_dtsg or lsd)

        logger.info("=" * 65)
        logger.info(f"   RESOLVING UFAC CHECKPOINT FOR ACTOR: {actor_id}   ")
        logger.info("=" * 65)

        checkpoint_headers = {
            'accept': '*/*',
            'origin': self.BASE_URL,
            'referer': page_url,
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'x-asbd-id': '359341',
            'x-fb-lsd': lsd
        }

        checkpoint_params = {
            'fb_dtsg': fb_dtsg,
            'jazoest': jazoest,
            'lsd': lsd
        }

        # Step 1: Submit PROCEED Action
        logger.info("[1/3] Submitting FRLUFACSubmitActionMutation (PROCEED)...")
        mutation_proceed = {
            'av': actor_id,
            '__user': '0',
            '__a': '1',
            '__req': self._next_req(),
            'fb_api_caller_class': 'RelayModern',
            'fb_api_req_friendly_name': 'FRLUFACSubmitActionMutation',
            'server_timestamps': 'true',
            'variables': json.dumps({
                "input": {
                    "actor_id": actor_id,
                    "client_mutation_id": "1",
                    "action": "PROCEED",
                    "caller": None,
                    "enrollment_id": None
                },
                "scale": 1
            }),
            'doc_id': DOC_ID_UFAC_CHECKPOINT
        }

        persist_data = "..."
        try:
            resp_proceed = self.session.post(
                f"{self.BASE_URL}/api/graphql/",
                params=checkpoint_params,
                headers=checkpoint_headers,
                data=mutation_proceed
            )
            logger.info(f"Proceed status [{resp_proceed.status_code}]: {resp_proceed.text[:250]}")
            persist_data = self.extract_captcha_persist_data(resp_proceed.text)
        except Exception as e:
            logger.warning(f"Warning during PROCEED step: {e}")

        # Step 2: Attempt 2Captcha reCAPTCHA Enterprise
        logger.info(f"\n[2/3] Solving reCAPTCHA Enterprise via 2Captcha (sitekey: {DEFAULT_RECAPTCHA_SITE_KEY})...")
        token = self.captcha_solver.solve_recaptcha_v2(
            site_key=DEFAULT_RECAPTCHA_SITE_KEY,
            page_url=TwoCaptchaSolver.DEFAULT_IFRAME_URL
        )

        if token:
            logger.info(f"[SUCCESS] Got Captcha Solution Token: {token[:35]}...")
        else:
            logger.warning("[!] 2Captcha token not obtained. Proceeding with selfie/ID image upload and persist_data...")
            token = ""

        # Step 3: Submit BOT_CAPTCHA_RESPONSE mutation
        logger.info("\n[3/4] Submitting SUBMIT_BOT_CAPTCHA_RESPONSE mutation...")
        mutation_captcha = {
            'av': actor_id,
            '__user': '0',
            '__a': '1',
            '__req': self._next_req(),
            'fb_api_caller_class': 'RelayModern',
            'fb_api_req_friendly_name': 'FRLUFACSubmitActionMutation',
            'server_timestamps': 'true',
            'variables': json.dumps({
                "input": {
                    "actor_id": actor_id,
                    "client_mutation_id": "2",
                    "action": "SUBMIT_BOT_CAPTCHA_RESPONSE",
                    "bot_captcha_persist_data": persist_data,
                    "captcha_persist_data": persist_data,
                    "bot_captcha_response": token or None,
                    "captcha_response": token or None,
                    "caller": None,
                    "enrollment_id": None
                },
                "scale": 1
            }),
            'doc_id': DOC_ID_UFAC_CHECKPOINT
        }

        captcha_resp_text = ""
        try:
            resp_captcha = self.session.post(
                f"{self.BASE_URL}/api/graphql/",
                params=checkpoint_params,
                headers=checkpoint_headers,
                data=mutation_captcha
            )
            logger.info(f"Captcha Response [{resp_captcha.status_code}]: {resp_captcha.text[:300]}")
            captcha_resp_text = resp_captcha.text
        except Exception as e:
            logger.error(f"Error submitting bot captcha: {e}")

        # Step 4: Submit UPLOAD_IMAGE Mutation for UFACImageUploadChallengeState
        target_image = None
        for cand in [image_path, os.path.join(BASE_DIR, "image.jpg"), os.path.join(BASE_DIR, "image.jpeg"), os.path.join(BASE_DIR, "image.png")]:
            if cand and os.path.exists(cand):
                target_image = cand
                break

        if "ImageUpload" in captcha_resp_text or "image_upload" in captcha_resp_text or target_image:
            logger.info(f"\n[4/4] Submitting Image Upload Challenge (UPLOAD_IMAGE) with image: {target_image}...")
            mutation_image = {
                'av': actor_id,
                '__user': '0',
                '__a': '1',
                '__req': self._next_req(),
                'fb_api_caller_class': 'RelayModern',
                'fb_api_req_friendly_name': 'FRLUFACSubmitActionMutation',
                'server_timestamps': 'true',
                'variables': json.dumps({
                    "input": {
                        "actor_id": actor_id,
                        "client_mutation_id": "3",
                        "action": "UPLOAD_IMAGE",
                        "uploaded_image_file": "file",
                        "caller": None,
                        "enrollment_id": None
                    },
                    "scale": 1
                }),
                'doc_id': DOC_ID_UFAC_CHECKPOINT
            }

            try:
                if USE_CURL_CFFI:
                    from curl_cffi.curl import CurlMime
                    mime = CurlMime()
                    for k, v in mutation_image.items():
                        mime.addpart(name=k, data=str(v).encode("utf-8"))
                    if target_image and os.path.exists(target_image):
                        logger.info(f"[*] Attaching selfie/ID verification image (CurlMime): {target_image}")
                        mime.addpart(
                            name="file",
                            local_path=target_image,
                            filename="image.jpeg",
                            content_type="image/jpeg"
                        )
                    upload_resp = self.session.post(
                        f"{self.BASE_URL}/api/graphql/",
                        params=checkpoint_params,
                        headers=checkpoint_headers,
                        multipart=mime
                    )
                else:
                    if target_image and os.path.exists(target_image):
                        logger.info(f"[*] Attaching selfie/ID verification image: {target_image}")
                        with open(target_image, "rb") as f:
                            upload_resp = self.session.post(
                                f"{self.BASE_URL}/api/graphql/",
                                params=checkpoint_params,
                                headers=checkpoint_headers,
                                data=mutation_image,
                                files={'file': ('image.jpeg', f, 'image/jpeg')}
                            )
                    else:
                        upload_resp = self.session.post(
                            f"{self.BASE_URL}/api/graphql/",
                            params=checkpoint_params,
                            headers=checkpoint_headers,
                            data=mutation_image
                        )

                logger.info(f"Image Upload Response [{upload_resp.status_code}]: {upload_resp.text[:300]}")
            except Exception as e:
                logger.error(f"Failed to submit image upload: {e}")

        logger.info("[SUCCESS] UFAC Checkpoint pipeline completed!")

        # Extract and follow 'next' redirect chain (e.g. /recover/success/ -> /login/ -> /settings/)
        try:
            parsed_cp = urlparse(page_url)
            query_params = parse_qs(parsed_cp.query)
            next_target = query_params.get("next", [None])[0]
            if next_target:
                logger.info(f"[*] Following post-checkpoint redirect: {next_target}")
                redir_headers = {
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "referer": page_url
                }
                redir_resp = self.session.get(next_target, headers=redir_headers, allow_redirects=True)
                logger.info(f"[SUCCESS] Post-checkpoint landed at: {redir_resp.url} (Status: {redir_resp.status_code})")
        except Exception as ex:
            logger.warning(f"Could not follow post-checkpoint redirect: {ex}")

        return True

    def complete_oidc_flow(self) -> Dict[str, Any]:
        logger.info("Completing OIDC Authorization flow...")
        doc_headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "referer": self._get_current_referer()
        }
        try:
            resp = self.session.get(self.oidc_redirect_uri, headers=doc_headers, allow_redirects=True)
            logger.info(f"OIDC flow final URL: {resp.url} (Status: {resp.status_code})")

            code = None
            if "code=" in resp.url:
                parsed_url = urlparse(resp.url)
                params = parse_qs(parsed_url.query)
                code = params.get("code", [None])[0]
                if code:
                    logger.info(f"[SUCCESS] Obtained OIDC Authorization Code: {code[:20]}...")

            cookies_dict = self.get_cookies_dict()
            return {
                "success": True,
                "code": code,
                "final_url": resp.url,
                "user_id": self.created_user_id or cookies_dict.get("c_user"),
                "cookies": cookies_dict
            }
        except Exception as e:
            logger.error(f"Error completing OIDC flow: {e}")
            return {"success": False, "error": str(e)}

    def _parse_fb_response(self, text: str) -> Any:
        clean_text = text
        if clean_text.startswith("for (;;);"):
            clean_text = clean_text[9:]
        try:
            return json.loads(clean_text)
        except Exception:
            return {"raw_response": text}


# =====================================================================
# 5. ACCOUNT EXPORTER & PERSISTENCE
# =====================================================================

def save_registered_account(
    email: str,
    password: str,
    username: str,
    user_id: Optional[str] = None,
    cookies_str: str = "",
    status: str = "ACTIVATED"
):
    account_entry = {
        "email": email,
        "password": password,
        "username": username,
        "user_id": user_id or "",
        "cookies": cookies_str,
        "status": status,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    accounts_list = []
    if os.path.exists(ACCOUNTS_JSON_PATH):
        try:
            with open(ACCOUNTS_JSON_PATH, "r", encoding="utf-8") as f:
                accounts_list = json.load(f)
                if not isinstance(accounts_list, list):
                    accounts_list = []
        except Exception:
            accounts_list = []

    accounts_list.append(account_entry)
    with open(ACCOUNTS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(accounts_list, f, indent=2)

    with open(ACCOUNTS_TXT_PATH, "a", encoding="utf-8") as f:
        f.write(f"{email}:{password}:{user_id or ''}:{cookies_str}\n")

    logger.info(f"[SAVED] Account details saved to {ACCOUNTS_JSON_PATH} and {ACCOUNTS_TXT_PATH}")


# =====================================================================
# 6. RUNNER & COMPLETE WORKFLOW ENTRY POINT
# =====================================================================

def generate_random_email(domain: str = "mailinator.com") -> str:
    prefix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"meta_test_{prefix}@{domain}"


def generate_random_dob(min_age: int = 20, max_age: int = 50) -> str:
    """Generates a random Date of Birth (YYYY-MM-DD) for an age between min_age and max_age years."""
    current_year = time.localtime().tm_year
    birth_year = current_year - random.randint(min_age, max_age)
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return f"{birth_year:04d}-{birth_month:02d}-{birth_day:02d}"


def run_full_registration_flow(
    email: Optional[str] = None,
    password: str = "SecurePassword2026!",
    dob: Optional[str] = None,
    proxy: Optional[str] = None,
    auto_otp: bool = True,
    image_path: str = DEFAULT_IMAGE_PATH,
    email_provider: Optional[str] = None
) -> Dict[str, Any]:
    # Determine the effective email provider: "ninja", "mail_td", or "off"
    provider = (email_provider or "").lower().strip()
    if not provider:
        if USE_NINJA:
            provider = "ninja"
        elif USE_MAIL_TD:
            provider = "mail_td"
        else:
            provider = (EMAIL_PROVIDER or "ninja").lower().strip()

    mail_td_token: Optional[str] = None
    ninja_client: Optional[Any] = None
    target_dob = dob or generate_random_dob(min_age=20, max_age=50)

    try:
        if not email:
            if provider in ("ninja", "tempmail_ninja") and HAS_NINJA:
                print("\n[*] [Step 0] Generating temporary email via TempMail Ninja...")
                try:
                    ninja_client = TempMailNinjaAPI()
                    ninja_client.connect()
                    target_email = ninja_client.create_temp_email()
                    print(f"[*] Generated Ninja email: {target_email}")
                except Exception as e:
                    print(f"[!] TempMail Ninja generation failed ({e}), falling back...")
                    ninja_client = None
                    if HAS_MAIL_TD:
                        gen_email, gen_pwd, token_str = generate_mail_td_account()
                        if gen_email and token_str:
                            target_email = gen_email
                            mail_td_token = token_str
                            print(f"[*] Fallback Generated Mail.td email: {target_email}")
                        else:
                            target_email = generate_random_email()
                            print(f"[!] Fallback to random email: {target_email}")
                    else:
                        target_email = generate_random_email()
                        print(f"[*] Using random email: {target_email}")
            elif provider in ("mail_td", "mailtd", "mail.td") and HAS_MAIL_TD:
                print("\n[*] [Step 0] Generating temporary email via Mail.td...")
                gen_email, gen_pwd, token_str = generate_mail_td_account()
                if gen_email and token_str:
                    target_email = gen_email
                    mail_td_token = token_str
                    print(f"[*] Generated Mail.td email: {target_email}")
                else:
                    target_email = generate_random_email()
                    print(f"[!] Mail.td generation failed, falling back to random email: {target_email}")
            elif provider in ("off", "none", "disable", "disabled", "false"):
                target_email = generate_random_email()
                print(f"[*] Temp email provider is OFF. Using random email: {target_email}")
            else:
                target_email = generate_random_email()
                print(f"[*] Using random email: {target_email}")
        else:
            target_email = email

        print("\n" + "=" * 65)
        print("      META AUTH & KADABRA COMPLETE REGISTRATION PIPELINE      ")
        print("=" * 65)
        print(f"[*] Email Provider : {provider.upper() if not email else 'CUSTOM EMAIL'}")
        print(f"[*] Date of Birth  : {target_dob} (Age: ~{time.localtime().tm_year - int(target_dob.split('-')[0])} years)")

        client = MetaAuthClient(proxy=proxy)

        print("\n[1/8] Initializing session on auth.meta.com...")
        if not client.init_session():
            print("[!] Failed to initialize session. Exiting.")
            return {"success": False, "error": "session_init_failed"}

        print(f"      Device Profile: {client.fingerprint['os']} | {client.fingerprint['browser']} | {client.fingerprint['screen_width']}x{client.fingerprint['screen_height']} (TLS: {client.impersonate})")
        print(f"      Waterfall ID: {client.waterfall_id}")
        print(f"      Client Session ID: {client.client_session_id}")
        print(f"      LSD Token: {client.lsd}")
        print(f"      Key ID: {client.key_id}")
        print(f"      Public Key: {client.public_key[:16] if client.public_key else 'default'}...")
        print(f"      Jurisdiction: {client.jurisdiction}")

        print(f"\n[2/8] Checking availability for email: {target_email}...")
        cp_resp = client.check_contact_point_availability(email=target_email)
        print(f"      Result: {cp_resp}")

        print(f"\n[3/8] Validating password with server...")
        pwd_resp = client.check_password(email=target_email, password_raw_or_payload=password, dob=target_dob)
        print(f"      Result: {pwd_resp}")

        print(f"\n[4/8] Checking DOB eligibility ({target_dob})...")
        dob_resp = client.check_date_of_birth(email=target_email, dob=target_dob)
        print(f"      Result: {dob_resp}")

        client.check_marketing_opt_in(dob=target_dob)

        print(f"\n[5/8] Requesting suggested username...")
        suggest_resp = client.suggest_username()
        suggested_username = (
            suggest_resp.get("payload", {}).get("username")
            if isinstance(suggest_resp, dict) and "payload" in suggest_resp
            else None
        )
        if not suggested_username:
            suggested_username = "meta_user_" + client.waterfall_id[:8]

        print(f"      Using Username: {suggested_username}")

        print(f"\n[6/8] Confirming Kadabra profile handle: {suggested_username}...")
        profile_resp = client.check_profile(username=suggested_username)
        print(f"      Result: {profile_resp}")

        print(f"\n[7/8] Submitting final registration for {target_email} ({suggested_username})...")
        reg_resp = client.register_save_credentials(
            email=target_email,
            password_raw_or_payload=password,
            username=suggested_username,
            dob=target_dob
        )
        print(f"\n[*] Final Registration Response:\n{reg_resp}")

        account_confirmed = False
        account_id = client.created_user_id

        payload = reg_resp.get("payload", {}) if isinstance(reg_resp, dict) else {}
        if client.created_user_id or (isinstance(payload, dict) and payload.get("confirmed") is not None):
            print("\n[8/8] Handling Confirmation & Post-Registration Flow...")
            if isinstance(payload, dict) and not payload.get("confirmed", True):
                print(f"[*] A 6-digit confirmation code has been sent to {target_email}.")
                otp_code = None

                # 1. Option: TempMail Ninja Auto-OTP
                if auto_otp and ninja_client:
                    print(f"[*] Automatically waiting for OTP from TempMail Ninja inbox (up to 90s)...")
                    try:
                        otp_code = ninja_client.wait_for_otp(timeout=90, poll_interval=3)
                        if otp_code:
                            print(f"[*] Successfully retrieved OTP code from TempMail Ninja: {otp_code}")
                    except Exception as e:
                        print(f"[-] Error waiting for OTP via TempMail Ninja: {e}")

                # 2. Option: Mail.td Auto-OTP
                elif auto_otp and mail_td_token:
                    print(f"[*] Automatically waiting for OTP from Mail.td inbox (up to 90s)...")
                    otp_code = fetch_otp_mail_td(token=mail_td_token, max_wait_sec=90, expected_digits=6)
                    if otp_code:
                        print(f"[*] Successfully retrieved OTP code from Mail.td: {otp_code}")

                # 3. Manual Input Fallback
                if not otp_code and sys.stdin and sys.stdin.isatty():
                    try:
                        otp_code = input("Enter the 6-digit confirmation code from your email (or press Enter to skip): ").strip()
                    except (EOFError, KeyboardInterrupt):
                        otp_code = None

                if otp_code:
                    print(f"[*] Submitting confirmation code {otp_code}...")
                    confirm_resp = client.confirm_email_otp(code=otp_code)
                    confirm_data = confirm_resp.get("data", {}).get("confirm_email", {}) if "data" in confirm_resp else confirm_resp.get("confirm_email", {})
                    if confirm_data.get("isConfirmed") or confirm_data.get("is_confirmed") or confirm_data.get("accountId"):
                        account_id = confirm_data.get("accountId") or confirm_data.get("account_id") or client.created_user_id
                        account_confirmed = True
                        print(f"\n[SUCCESS] Meta Account {target_email} successfully confirmed and activated! (Account ID: {account_id})")
                    else:
                        print(f"[*] OTP Response: {confirm_resp}")
            else:
                account_confirmed = True

        # Checkpoint Detection & Solving
        print("\n[*] Checking for Checkpoint / 'Confirm you are human' challenge...")
        checkpoint_url = client.check_for_checkpoint(wait_sec=4)
        if checkpoint_url:
            print(f"[!] Checkpoint active at: {checkpoint_url}")
            print("[*] Launching UFAC GraphQL + 2Captcha + Image upload solver...")
            client.resolve_ufac_checkpoint(checkpoint_url=checkpoint_url, image_path=image_path)

        # OIDC Flow Completion
        print("\n[*] Completing OIDC authorization & session token generation...")
        oidc_result = client.complete_oidc_flow()

        cookies_str = client.get_cookies_string()
        final_user_id = account_id or client.created_user_id or client.get_cookies_dict().get("c_user")
        status_str = "ACTIVATED" if account_confirmed else "PENDING_CONFIRMATION"

        save_registered_account(
            email=target_email,
            password=password,
            username=suggested_username,
            user_id=final_user_id,
            cookies_str=cookies_str,
            status=status_str
        )

        print("\n" + "=" * 65)
        print("                     REGISTRATION SUMMARY                     ")
        print("=" * 65)
        print(f"  Email       : {target_email}")
        print(f"  Password    : {password}")
        print(f"  DOB (Age)   : {target_dob} (~{time.localtime().tm_year - int(target_dob.split('-')[0])} yrs)")
        print(f"  Username    : {suggested_username}")
        print(f"  Account ID  : {final_user_id or 'Created'}")
        print(f"  Status      : {status_str}")
        print(f"  Cookies     : {cookies_str[:80]}...")
        print("=" * 65)

        return {
            "success": True,
            "email": target_email,
            "password": password,
            "dob": target_dob,
            "username": suggested_username,
            "user_id": final_user_id,
            "cookies": cookies_str,
            "status": status_str,
            "oidc": oidc_result
        }
    finally:
        if ninja_client:
            try:
                ninja_client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Meta Auth & Registration Automation")
    parser.add_argument("email", nargs="?", default=None, help="Target email address (optional)")
    parser.add_argument("password", nargs="?", default="SecurePassword2026!", help="Account password")
    parser.add_argument("dob", nargs="?", default=None, help="Date of Birth YYYY-MM-DD (optional)")
    parser.add_argument("--provider", choices=["ninja", "mail_td", "mailtd", "off"], default=None,
                        help="Email provider to use: 'ninja', 'mail_td', or 'off' (default: from config)")
    parser.add_argument("--ninja", action="store_true", help="Use TempMail Ninja for temporary email & OTP")
    parser.add_argument("--mailtd", "--mail_td", action="store_true", dest="mailtd", help="Use Mail.td for temporary email & OTP")
    parser.add_argument("--off", "--no-temp", action="store_true", dest="off", help="Turn off temp email generation")
    parser.add_argument("--no-otp", action="store_true", help="Disable automatic OTP retrieval")
    parser.add_argument("--proxy", default=None, help="HTTP/HTTPS proxy string")
    parser.add_argument("--image", default=DEFAULT_IMAGE_PATH, help="Path to checkpoint image")

    args = parser.parse_args()

    # Determine provider from CLI flags if given
    cli_provider = args.provider
    if args.ninja:
        cli_provider = "ninja"
    elif args.mailtd:
        cli_provider = "mail_td"
    elif args.off:
        cli_provider = "off"

    # If first positional argument was actually a provider name (e.g. 'python all.py ninja')
    email_val = args.email
    if email_val and email_val.lower() in ("ninja", "mail_td", "mailtd", "off"):
        cli_provider = email_val.lower()
        email_val = None

    run_full_registration_flow(
        email=email_val,
        password=args.password,
        dob=args.dob,
        proxy=args.proxy,
        auto_otp=not args.no_otp,
        image_path=args.image,
        email_provider=cli_provider
    )