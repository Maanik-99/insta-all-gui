#!/usr/bin/env python3
"""
Meta AI Hybrid Account Creator & Automation (Request + Browser Handover)
=======================================================================
Combines:
  1. Fast Pure-Request Registration (uptoemailotp.py)
     - Session init, token & crypto generation, availability checks,
       DOB, profile checks, and instant registration submission via requests/curl_cffi.
     - Automatic OTP polling from mail.td (or configured email service in config.json)
     - Automatic OTP confirmation via Meta GraphQL mutation.
  2. Playwright Browser Handover & Checkpoint Solving (meta.ai.py)
     - Injects session cookies into stealth Playwright browser.
     - Automatically handles 2Captcha / reCAPTCHA solver extension.
     - Automatically handles Selfie verification from ./images directory.
     - Automatically dismisses cookie banners, error dialogs, and 'Save login info'.
     - Confirms success state, extracts username, and auto-saves to credentials.csv & Google Sheet.
"""

import asyncio
import base64
import csv
import hashlib
import json
import logging
import os
import random
import re
import shutil
import string
import sys
import threading
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

import urllib3
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

try:
    from curl_cffi import requests
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False

import nacl.public
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Suppress insecure SSL warnings
urllib3.disable_warnings()

# Thread lock for safe CSV writing
csv_lock = threading.Lock()

# Configuration Defaults
TARGET_URL = "https://www.meta.ai/"
CHECKPOINT_REDIRECT_URL = "https://auth.meta.com/checkpoints/675270400317265/?next=https%3A%2F%2Fauth.meta.com%2Frecover%2Fsuccess%2F%3Fredirect_uri%3Dhttps%253A%252F%252Fauth.meta.com%252F"
HEADLESS = False
NUM_WORKERS = 1
VIEWPORT_WIDTH = 500
VIEWPORT_HEIGHT = 700

# Import Email & Config Services
from email_service import (
    CONFIG_FILE,
    DEFAULT_CONFIG,
    extract_otp_from_text,
    fetch_otp_from_temp_mail,
    generate_custom_password,
    generate_temp_email,
    get_random_name,
    load_config,
    load_names_from_csv,
    log,
)


# =====================================================================
# 1. CRYPTO & META ENVELOPE ENCRYPTION
# =====================================================================

def format_pwd_browser(encrypted_b64: str, timestamp: Optional[int] = None, version: int = 5) -> str:
    """Formats the password string in Meta's browser format: #PWD_BROWSER:<ver>:<ts>:<payload>"""
    ts = timestamp or int(time.time())
    return f"#PWD_BROWSER:{version}:{ts}:{encrypted_b64}"


def encrypt_meta_password(
    password: str,
    public_key_hex: str,
    key_id: int = 199,
    timestamp: Optional[int] = None
) -> str:
    """
    Encrypts password using Meta's EnvelopeEncryption standard:
    1. Ephemeral 32-byte AES-256 key.
    2. AES-256-GCM encryption with 12 zero IV bytes and timestamp AAD.
    3. TweetNaCl SealedBox encryption with Meta's X25519 public key.
    4. Meta binary envelope pack.
    """
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
    envelope.append(sealed_len & 0xFF)  # Length low byte
    envelope.append((sealed_len >> 8) & 0xFF)  # Length high byte
    envelope.extend(sealed_key)
    envelope.extend(tag)
    envelope.extend(ciphertext)

    b64_payload = base64.b64encode(envelope).decode("utf-8")
    return format_pwd_browser(b64_payload, timestamp=ts)


def compute_jazoest(token: str) -> str:
    """Computes Meta dynamic jazoest checksum from LSD or fb_dtsg."""
    if not token:
        return "22206"
    return "2" + str(sum(ord(c) for c in token))


# =====================================================================
# 2. FALCO TELEMETRY PAYLOAD BUILDER
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
# 3. META AUTH REQUEST CLIENT
# =====================================================================

class MetaAuthClient:
    """
    Automates the fast pure-HTTP request registration on auth.meta.com.
    """
    BASE_URL = "https://auth.meta.com"
    SOURCE_APP_ID = "1522763855472543"  # Meta AI / Kadabra client app ID
    TOS_CMS_ID = "957798449862312"

    def __init__(self, proxy: Optional[str] = None, impersonate: str = "chrome124", instance_name: str = ""):
        self.instance_name = instance_name
        self.session = requests.Session(impersonate=impersonate) if USE_CURL_CFFI else requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

        self.waterfall_id = str(uuid.uuid4())
        self.client_session_id = self._generate_client_session_id()
        self.device_id: Optional[str] = None
        self.email: Optional[str] = None
        self.req_counter = 1

        # OIDC PKCE & Waterfall Flow State
        self.code_verifier = self._generate_client_session_id() + self._generate_client_session_id()
        self.code_challenge = self._generate_code_challenge(self.code_verifier)
        self.oidc_redirect_uri = self._build_oidc_redirect_uri()

        # Dynamic tokens
        self.lsd: Optional[str] = None
        self.jazoest: Optional[str] = None
        self.public_key: Optional[str] = None
        self.key_id: int = 199
        self.rcs: Optional[str] = None
        self.jurisdiction: str = "BD"
        self.reg_integrity: Optional[str] = None
        self.fb_dtsg: Optional[str] = None
        self.created_user_id: Optional[str] = None

        # Build revisions
        self.rev = "1047303469"
        self.hsi = "7684275360328234786"
        self.hs = "20707.HYP:frl_comet_auth_pkg.2.1...0"
        self.s = "npdr3i:q5lguu:55za44"
        self.spin_r = "1047303469"
        self.spin_b = "trunk"
        self.spin_t = "1789134777"
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
        state_payload = {
            "csrf_token": self._generate_client_session_id() + "-" + self._generate_client_session_id(),
            "redirect_to": "https://meta.ai/oidc/callback",
            "started_at": int(time.time() * 1000),
            "waterfall_id": self.waterfall_id
        }
        state_b64 = base64.b64encode(json.dumps(state_payload).encode("utf-8")).decode("utf-8")

        params = {
            "app_id": self.SOURCE_APP_ID,
            "redirect_uri": "https://auth.meta.ai/ecto",
            "response_type": "code",
            "scope": "openid linking",
            "state": state_b64,
            "waterfall_id": self.waterfall_id,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256"
        }
        return f"{self.BASE_URL}/oidc/?{urlencode(params)}"

    def _get_cookie(self, name: str) -> str:
        try:
            if hasattr(self.session.cookies, "get_dict"):
                return self.session.cookies.get_dict().get(name, "")
            return self.session.cookies.get(name, domain=".meta.com") or ""
        except Exception:
            return ""

    def _generate_qpl_join_id(self) -> str:
        return "f" + uuid.uuid4().hex[:16]

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
            "accept-language": "en-US,en;q=0.9",
            "origin": self.BASE_URL,
            "referer": self._get_current_referer(),
            "sec-ch-prefers-color-scheme": "dark",
            "sec-ch-ua": '"Chromium";v="124", "Not?A_Brand";v="24", "Google Chrome";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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
        """Step 1: Load initial landing page to capture datr, LSD, public key, key ID, and rcs."""
        log("REQ-INIT", "Initializing Meta Auth session...", self.instance_name)
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
            text = resp.text

            # Extract rcs token
            if "rcs=" in resp.url:
                self.rcs = resp.url.split("rcs=")[-1].split("&")[0]

            # Extract LSD Token
            lsd_match = re.search(r'\["LSD",\[\],\{"token":"([^"]+)"\}', text)
            if not lsd_match:
                lsd_match = re.search(r'name="lsd"\s+value="([^"]+)"', text)
            if lsd_match:
                self.lsd = lsd_match.group(1)
                self.jazoest = compute_jazoest(self.lsd)

            # Extract Public Key & Key ID
            pk_match = re.search(r'publicKey":"([a-f0-9]{64})"', text)
            if pk_match:
                self.public_key = pk_match.group(1)
            key_id_match = re.search(r'keyId":(\d+)', text)
            if key_id_match:
                self.key_id = int(key_id_match.group(1))

            # Extract Jurisdiction
            juris_match = re.search(r'regulation_jurisdiction":\["([^"]+)"\]', text)
            if juris_match:
                self.jurisdiction = juris_match.group(1)

            self.session.headers["referer"] = self._get_current_referer()
            log("REQ-INIT", f"Session ready! LSD={self.lsd[:12] if self.lsd else 'N/A'}... KeyID={self.key_id}", self.instance_name)
            return True
        except Exception as e:
            log("REQ-ERR", f"Failed to initialize auth session: {e}", self.instance_name)
            return False

    def send_falco_event(self, event_name: str, event_data: Dict[str, Any]) -> bool:
        """Dispatches Falco telemetry event."""
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

    def check_contact_point_availability(self, email: str) -> Dict[str, Any]:
        """Step 2: Check email availability."""
        log("REQ-EMAIL", f"Checking contact point availability: {email}", self.instance_name)
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
        data.update(self._get_base_spin_params())

        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": self._get_current_referer(),
        }

        resp = self.session.post(url, data=data, headers=headers)
        parsed = self._parse_fb_response(resp.text)

        if isinstance(parsed, dict) and "payload" in parsed:
            payload = parsed.get("payload")
            if isinstance(payload, dict):
                reg_int = payload.get("regIntegrity") or payload.get("reg_integrity")
                if reg_int:
                    self.reg_integrity = reg_int

                restrictions = payload.get("restrictions")
                if isinstance(restrictions, dict):
                    res_data = restrictions.get("restriction_data")
                    if isinstance(res_data, dict):
                        reg_info = res_data.get("account_reg_info")
                        if isinstance(reg_info, dict) and "device_id" in reg_info:
                            self.device_id = reg_info["device_id"]

                if "contactPoint" in payload and payload["contactPoint"]:
                    self.email = payload["contactPoint"]

        return parsed

    def check_password(self, email: str, password_raw_or_payload: str, dob: str = "2000-01-01") -> Dict[str, Any]:
        """Step 3: Check password validity."""
        url = f"{self.BASE_URL}/api/check-password/"
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
        return self._parse_fb_response(resp.text)

    def check_date_of_birth(self, email: str, dob: str = "2000-01-01") -> Dict[str, Any]:
        """Step 4: Check date of birth."""
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
        parsed = self._parse_fb_response(resp.text)
        if isinstance(parsed, dict) and "payload" in parsed:
            payload = parsed.get("payload")
            if isinstance(payload, dict):
                reg_int = payload.get("regIntegrity") or payload.get("reg_integrity")
                if reg_int:
                    self.reg_integrity = reg_int
        return parsed

    def suggest_username(self) -> Dict[str, Any]:
        """Step 5: Suggest Kadabra username."""
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
        return self._parse_fb_response(resp.text)

    def check_profile(self, username: str) -> Dict[str, Any]:
        """Step 6: Confirm username availability and TOS agreement."""
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
        return self._parse_fb_response(resp.text)

    def register_save_credentials(
        self,
        email: str,
        password_raw_or_payload: str,
        username: str,
        dob: str = "2000-01-01",
        redirect_uri: Optional[str] = None
    ) -> Dict[str, Any]:
        """Step 7: Submit final registration to trigger OTP send."""
        log("REQ-REGISTER", f"Submitting registration for {email} ({username})...", self.instance_name)
        url = f"{self.BASE_URL}/login/device-based/kadabra-register-save-credentials/"

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
        parsed = self._parse_fb_response(resp.text)

        if isinstance(parsed, dict):
            if "dtsgToken" in parsed:
                self.fb_dtsg = parsed["dtsgToken"]

            if "payload" in parsed:
                payload = parsed.get("payload", {})
                if isinstance(payload, dict):
                    user_id = payload.get("account_id") or payload.get("uid") or payload.get("user_id")
                    if user_id:
                        self.created_user_id = str(user_id)
                        log("REQ-SUCCESS", f"Registered Meta Account ID: {self.created_user_id}", self.instance_name)

        return parsed

    def confirm_email_otp(self, code: str, email: Optional[str] = None) -> Dict[str, Any]:
        """Submits 6-digit confirmation code / OTP to confirm and activate Meta account."""
        target_email = email or self.email
        clean_code = str(code).strip()
        log("REQ-OTP-CONFIRM", f"Submitting OTP '{clean_code}' for {target_email} via GraphQL...", self.instance_name)
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
        return self.send_graphql_request(doc_id="9851798224911796", variables=variables)

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

    def _parse_fb_response(self, text: str) -> Any:
        clean_text = text
        if clean_text.startswith("for (;;);"):
            clean_text = clean_text[9:]
        try:
            return json.loads(clean_text)
        except Exception:
            return {"raw_response": text}


# =====================================================================
# 4. CREDENTIALS & GOOGLE SHEET SYNC
# =====================================================================

def send_to_google_sheet(timestamp: str, email: str, username: str, password: str, instance_name: str = ""):
    """Sync created account to Google Sheet Webhook."""
    try:
        cfg = load_config()
        webhook_url = cfg.get("google_sheet_webhook_url", "").strip()
        if not webhook_url:
            return

        def _post():
            try:
                payload = {
                    "timestamp": timestamp,
                    "email": email,
                    "username": username,
                    "password": password
                }
                res = requests.post(webhook_url, json=payload, timeout=12)
                if res.status_code in [200, 302]:
                    log("GSHEET", f"Instantly synced to Google Sheet -> {email}", instance_name=instance_name)
                else:
                    log("GSHEET-WARN", f"Google Sheet response code: {res.status_code}", instance_name=instance_name)
            except Exception as err:
                log("GSHEET-ERR", f"Failed syncing to Google Sheet: {err}", instance_name=instance_name)

        threading.Thread(target=_post, daemon=True).start()
    except Exception as e:
        log("GSHEET-ERR", f"GSheet trigger note: {e}", instance_name=instance_name)


def save_credentials_csv(email: str, password: str, username: str = "", instance_name: str = ""):
    """Save credentials to credentials.csv and trigger Google Sheet sync."""
    csv_file = os.path.join(os.path.dirname(__file__), "credentials.csv")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    uname = username.strip() if username else "Meta_User"
    with csv_lock:
        file_empty_or_missing = not os.path.exists(csv_file) or os.path.getsize(csv_file) == 0
        try:
            with open(csv_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if file_empty_or_missing:
                    writer.writerow(["Timestamp", "Email", "Username", "Password"])
                writer.writerow([timestamp, email, uname, password])
                f.flush()
                os.fsync(f.fileno())
            log("CSV-SUCCESS", f">>> SAVED TO CSV: Email: '{email}' | Username: '{uname}' <<<", instance_name=instance_name)
        except Exception as e:
            log("CSV-ERR", f"Failed writing CSV: {e}", instance_name=instance_name)

    send_to_google_sheet(timestamp, email, uname, password, instance_name=instance_name)


# =====================================================================
# 5. PLAYWRIGHT BROWSER AUTOMATION HELPERS
# =====================================================================

def cleanup_profile_locks(profile_dir: str):
    """Clean up any stale lock files."""
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
    Clean cache/cookies/history from profile while preserving 2Captcha/CapMonster extensions.
    """
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
    """Ensure dedicated user data directory for each parallel browser worker."""
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


def extract_cookies_for_playwright(session) -> List[Dict[str, Any]]:
    """Extract cookies from requests/curl_cffi session to inject into Playwright."""
    playwright_cookies = []
    seen = set()
    try:
        # 1. Extract from CookieJar (has rich metadata: name, value, domain, path, expires)
        jar = getattr(session.cookies, "jar", None)
        if jar is not None:
            for cookie in jar:
                name = str(getattr(cookie, "name", ""))
                value = str(getattr(cookie, "value", ""))
                if not name or not value:
                    continue
                domain = str(getattr(cookie, "domain", "")) or ".meta.com"
                path = str(getattr(cookie, "path", "")) or "/"
                key = (name, domain, path)
                if key not in seen:
                    seen.add(key)
                    c: Dict[str, Any] = {
                        "name": name,
                        "value": value,
                        "domain": domain,
                        "path": path,
                    }
                    if getattr(cookie, "secure", None) is not None:
                        c["secure"] = bool(cookie.secure)
                    if getattr(cookie, "expires", None) is not None and cookie.expires > time.time():
                        c["expires"] = int(cookie.expires)
                    playwright_cookies.append(c)

                # Clone cookies across related Meta domains if needed
                if "auth.meta.com" in domain:
                    for d in [".meta.com", ".meta.ai"]:
                        k = (name, d, path)
                        if k not in seen:
                            seen.add(k)
                            playwright_cookies.append({
                                "name": name,
                                "value": value,
                                "domain": d,
                                "path": path,
                            })

        # 2. Extract from get_dict() fallback
        if hasattr(session.cookies, "get_dict"):
            cookies_dict = session.cookies.get_dict()
            for name, value in cookies_dict.items():
                if not name or not value:
                    continue
                for d in [".auth.meta.com", ".meta.com", ".meta.ai"]:
                    key = (str(name), d, "/")
                    if key not in seen:
                        seen.add(key)
                        playwright_cookies.append({
                            "name": str(name),
                            "value": str(value),
                            "domain": d,
                            "path": "/",
                        })
    except Exception as e:
        log("COOKIE-WARN", f"Error extracting session cookies: {e}")
    return playwright_cookies


async def get_active_meta_page(context: BrowserContext, default_page: Optional[Page] = None) -> Page:
    """Locate active Meta AI / Checkpoint page across open tabs."""
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
                if "meta.ai" in p.url.lower():
                    return p
            except Exception:
                pass

        return pages[-1]
    except Exception:
        return default_page or (context.pages[0] if context.pages else None)


async def handle_cookie_banners(page: Page, instance_name: str = "") -> bool:
    """Dismiss cookie consent banners."""
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
            "button:has-text('Accept all cookies')",
            "button:has-text('Allow all')",
            "button:has-text('Accept all')"
        ]
        for sel in cookie_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=250):
                    log("ACTION", f"Dismissing cookie banner ({sel})...", instance_name=instance_name)
                    await loc.scroll_into_view_if_needed(timeout=400)
                    await loc.click(force=True, timeout=600)
                    await asyncio.sleep(0.4)
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


async def handle_save_login_info_prompt(page: Page, context: BrowserContext, instance_name: str = "") -> bool:
    """Dismiss 'Save your login info?' screen."""
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
        except Exception:
            pass
    return False


async def try_click_saved_account_card(page: Page, instance_name: str = "") -> bool:
    """Click saved Meta account card/pill on auth.meta.com."""
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


async def handle_recaptcha_2captcha(page: Page, context: BrowserContext, instance_name: str = "") -> bool:
    """
    Check if 'Confirm that you're human' or reCAPTCHA is present.
    Clicks 'Solve with 2Captcha' button, waits for solve, then clicks Continue.
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
                        log("2CAPTCHA", f"Waiting for 2Captcha solver 'ready' state (current: '{btn_text}')...", instance_name=instance_name)
                        try:
                            ready_btn = target_container.locator('.captcha-solver[data-state="ready"]').first
                            await ready_btn.wait_for(state="visible", timeout=5000)
                            log("2CAPTCHA", "Clicking 'Solve with 2Captcha' button...", instance_name=instance_name)
                            await ready_btn.click(force=True, timeout=2000)
                        except Exception:
                            try:
                                await solver_loc.click(force=True, timeout=2000)
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
                                        break
                                except Exception:
                                    break

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


async def try_auto_upload_image(target_page: Page, instance_name: str = "") -> bool:
    """Auto-upload selfie verification image from ./images directory."""
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
            except Exception:
                pass

        if not uploaded:
            try:
                file_input = target_page.locator("input.x1s85apg, div.xeq5yr9 input[type='file'], input[accept*='image'], input[type='file']").first
                if await file_input.count() > 0:
                    await file_input.set_input_files(chosen_path)
                    uploaded = True
                    log("IMG-UPLOAD", f"Directly attached '{chosen_img}' to input.", instance_name)
            except Exception:
                pass

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


async def extract_meta_username(context: BrowserContext) -> Optional[str]:
    """Extract logged-in Meta AI username."""
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


async def check_meta_success_state(context: BrowserContext) -> Tuple[bool, str]:
    """Check across open context pages if registration/verification is successful."""
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

            # 1. Logged in paths
            if path in ["/language", "/language/", "/settings", "/settings/", "/recover/success", "/recover/success/"] or "recover/success" in path:
                uname = await extract_meta_username(context) or "Meta_User"
                return True, uname

            # 2. Main Meta AI logged-in domain
            if "meta.ai" in url and "auth.meta.com" not in url:
                uname = await extract_meta_username(context) or "Meta_User"
                return True, uname

            # 3. Log out button found
            if await p.locator("a:has-text('Log Out'), button:has-text('Log Out'), [role='button']:has-text('Log Out')").count() > 0:
                uname = await extract_meta_username(context) or "Meta_User"
                return True, uname

            uname = await extract_meta_username(context)
            if uname:
                return True, uname
        except Exception:
            pass

    return False, ""


async def handle_human_verification_and_checkpoint(
    page: Page,
    context: BrowserContext,
    email: str = "",
    password: str = "",
    suggested_username: str = "",
    instance_name: str = ""
) -> bool:
    """
    Post-OTP Checkpoint and Human Verification Pipeline:
    1. Opens Checkpoint redirect URL.
    2. Automatically triggers 2Captcha reCAPTCHA solver.
    3. Automatically uploads verification selfie from ./images.
    4. Auto-detects completion or 30s timeout and saves credentials.
    """
    log("CHECKPOINT-FLOW", "Starting Checkpoint & Verification Handover...", instance_name)

    checkpoint_page = await get_active_meta_page(context, default_page=page)

    for attempt in range(1, 4):
        log("CHECKPOINT-NAV", f"Opening checkpoint URL (attempt {attempt}/3): {CHECKPOINT_REDIRECT_URL}", instance_name=instance_name)
        try:
            await checkpoint_page.goto(CHECKPOINT_REDIRECT_URL, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            log("CHECKPOINT-WARN", f"Checkpoint navigation note: {e}", instance_name=instance_name)

        await asyncio.sleep(2)
        await handle_cookie_banners(checkpoint_page, instance_name=instance_name)
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
            log("CHECKPOINT-NAV", f"Human verification / selfie active on attempt #{attempt}!", instance_name=instance_name)
            break
        else:
            if attempt < 3:
                await asyncio.sleep(2.0)

    # Human Verification Loop
    pipeline_start = time.time()
    img_uploaded = False
    post_upload_timer_start = None
    detected_user = None

    while time.time() - pipeline_start < 180:
        active_page = await get_active_meta_page(context, default_page=checkpoint_page)

        # 1. 2Captcha solver
        await handle_recaptcha_2captcha(active_page, context, instance_name=instance_name)

        # 2. Selfie upload
        if not img_uploaded:
            img_uploaded = await try_auto_upload_image(active_page, instance_name=instance_name)
            if img_uploaded:
                post_upload_timer_start = time.time()
                log("IMG-UPLOAD", ">>> Selfie image uploaded! Max 30s finalize timer started... <<<", instance_name)

        # 3. Saved account click / Continue
        await try_click_saved_account_card(active_page, instance_name=instance_name)
        await handle_save_login_info_prompt(active_page, context, instance_name=instance_name)

        # 4. Check success
        is_success, uname = await check_meta_success_state(context)
        if is_success:
            detected_user = uname
            log("SUCCESS", f">>> Registration & Verification SUCCESS detected! <<<", instance_name)
            break

        # 5. 30s timeout after selfie upload
        if post_upload_timer_start is not None:
            elapsed = time.time() - post_upload_timer_start
            if elapsed >= 30:
                log("SUCCESS", f">>> 30s elapsed after image upload. Marking SUCCESS! <<<", instance_name)
                detected_user = (await extract_meta_username(context)) or suggested_username or "Meta_User"
                break

        await asyncio.sleep(1.0)

    final_username = detected_user or suggested_username or "Meta_User"
    save_credentials_csv(email, password, username=final_username, instance_name=instance_name)
    return True


# =====================================================================
# 6. HYBRID WORKER PIPELINE
# =====================================================================

async def run_hybrid_registration_worker(playwright: Playwright, instance_name: str):
    """
    Executes the complete Hybrid Meta AI creation flow:
    Phase 1: Pure requests for setup + registration + OTP confirmation.
    Phase 2: Playwright browser for checkpoint solving + captcha + selfie upload.
    """
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
        log("CYCLE-START", f"=================== HYBRID CYCLE #{cycle_count} ({instance_name}) ===================", instance_name=instance_name)

        # -------------------------------------------------------------
        # PHASE 1: FAST PURE-REQUEST SIGNUP & OTP CONFIRMATION
        # -------------------------------------------------------------
        log("PHASE-1", "Starting pure-request registration (no browser)...", instance_name=instance_name)
        
        email, strong_password, mail_token = generate_temp_email(instance_name=instance_name)
        if not email:
            log("ERR", "Failed to generate email. Retrying in 3s...", instance_name=instance_name)
            await asyncio.sleep(3)
            continue

        dob_year = random.randint(1992, 2002)
        dob_month = f"{random.randint(1, 12):02d}"
        dob_day = f"{random.randint(1, 28):02d}"
        dob = f"{dob_year}-{dob_month}-{dob_day}"

        # Initialize Request Client
        client = MetaAuthClient(instance_name=instance_name)
        if not client.init_session():
            log("ERR", "Failed to initialize request session. Retrying in 3s...", instance_name=instance_name)
            await asyncio.sleep(3)
            continue

        # Check contact point availability
        cp_resp = client.check_contact_point_availability(email=email)
        
        # Check password
        client.check_password(email=email, password_raw_or_payload=strong_password, dob=dob)
        
        # Check DOB
        client.check_date_of_birth(email=email, dob=dob)
        
        # Suggest username
        suggest_resp = client.suggest_username()
        suggested_username = (
            suggest_resp.get("payload", {}).get("username")
            if isinstance(suggest_resp, dict) and "payload" in suggest_resp
            else None
        )
        if not suggested_username:
            first_n, last_n = get_random_name()
            suggested_username = f"{first_n.lower()}{last_n.lower()}{random.randint(100, 9999)}"

        # Validate username handle
        client.check_profile(username=suggested_username)

        # Submit registration (triggers OTP to email)
        reg_resp = client.register_save_credentials(
            email=email,
            password_raw_or_payload=strong_password,
            username=suggested_username,
            dob=dob
        )

        # Poll OTP from mail service (e.g. mail.td)
        log("OTP-POLL", f"Polling OTP for {email} from mail service...", instance_name=instance_name)
        otp_code = fetch_otp_from_temp_mail(
            email=email,
            password=strong_password,
            token=mail_token,
            max_wait_sec=60,
            instance_name=instance_name
        )

        otp_confirmed = False
        if otp_code:
            log("OTP-CONFIRM", f"Received OTP: {otp_code}. Submitting confirmation request...", instance_name=instance_name)
            confirm_resp = client.confirm_email_otp(code=otp_code, email=email)
            log("OTP-CONFIRM", f"Confirmation response: {confirm_resp}", instance_name=instance_name)
            otp_confirmed = True
        else:
            log("OTP-WARN", "OTP fetch timed out or not received. Handing over to browser to verify...", instance_name=instance_name)

        # -------------------------------------------------------------
        # PHASE 2: BROWSER HANDOVER FOR CHECKPOINT & CAPTCHA SOLVING
        # -------------------------------------------------------------
        log("PHASE-2", "Launching stealth browser and injecting session cookies...", instance_name=instance_name)

        context: Optional[BrowserContext] = None
        for ch in channels_to_try:
            try:
                ch_desc = f"channel='{ch}'" if ch else "Chromium engine"
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
                log("WORKER", f"Browser launched with {ch_desc}!", instance_name=instance_name)
                break
            except Exception as e:
                log("WORKER-WARN", f"Browser channel '{ch}' failed ({e}). Trying fallback...", instance_name=instance_name)

        if not context:
            log("WORKER-ERR", f"Fatal: Could not launch browser for {instance_name}! Retrying in 5s...", instance_name=instance_name)
            await asyncio.sleep(5)
            continue

        try:
            # Inject session cookies from requests into Playwright browser context
            playwright_cookies = extract_cookies_for_playwright(client.session)
            if playwright_cookies:
                try:
                    await context.add_cookies(playwright_cookies)
                    log("COOKIE-INJECT", f"Successfully injected {len(playwright_cookies)} session cookies into browser!", instance_name=instance_name)
                except Exception as c_err:
                    log("COOKIE-WARN", f"Cookie injection note: {c_err}", instance_name=instance_name)

            page = context.pages[0] if context.pages else await context.new_page()

            # Handle Checkpoint / Captcha / Selfie verification pipeline
            await handle_human_verification_and_checkpoint(
                page=page,
                context=context,
                email=email,
                password=strong_password,
                suggested_username=suggested_username,
                instance_name=instance_name
            )

            log("CYCLE-COMPLETE", f"Cycle #{cycle_count} finished successfully!", instance_name=instance_name)

        except Exception as e:
            log("CYCLE-ERR", f"Error during hybrid cycle #{cycle_count}: {e}", instance_name=instance_name)

        finally:
            log("CYCLE-CLOSE", f"Closing browser context for cycle #{cycle_count}...", instance_name=instance_name)
            try:
                if context:
                    for p in list(context.pages):
                        try:
                            await p.close()
                        except Exception:
                            pass
                    await context.close()
            except Exception as close_err:
                log("CYCLE-WARN", f"Browser close note: {close_err}", instance_name=instance_name)
            finally:
                context = None
                await asyncio.sleep(0.5)
                clean_profile_except_extensions(user_data_dir)

            log("CYCLE-RESET", f"Profile wiped. Starting next cycle in 2.5s...", instance_name=instance_name)
            await asyncio.sleep(2.5)


async def run_worker_with_stagger(playwright: Playwright, instance_name: str, delay: float = 0):
    if delay > 0:
        log("WORKER-WAIT", f"Worker {instance_name} waiting {delay:.1f}s to stagger launches...", instance_name=instance_name)
        await asyncio.sleep(delay)
    await run_hybrid_registration_worker(playwright, instance_name)


async def main_async():
    cfg = load_config()
    workers_count = cfg.get("num_workers", NUM_WORKERS)
    is_headless = cfg.get("headless", HEADLESS)
    channel = cfg.get("browser_channel", "chrome")
    mode_str = "Headless (Background ON)" if is_headless else "Headful (Window Visible OFF)"
    log("INIT", f"Starting Meta AI Hybrid Automation (Requests + Playwright) | Workers={workers_count} | Channel='{channel}' | Mode: {mode_str}")
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
