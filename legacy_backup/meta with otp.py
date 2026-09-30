#!/usr/bin/env python3
"""
Meta Auth & Registration Automation - Standalone All-In-One Script.
Fully self-contained client for automating the Meta Account / Kadabra registration and verification flow.
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
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlencode, quote

try:
    from curl_cffi import requests
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False

import nacl.public
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s"
)
try:
    from NINJA import TempMailNinjaAPI
    HAS_NINJA = True
except ImportError:
    try:
        from ninja import TempMailNinjaAPI
        HAS_NINJA = True
    except ImportError:
        HAS_NINJA = False

logger = logging.getLogger("MetaAuth")


# =====================================================================
# 1. CRYPTO & ENVELOPE ENCRYPTION
# =====================================================================

def format_pwd_browser(encrypted_b64: str, timestamp: Optional[int] = None, version: int = 5) -> str:
    """
    Formats the password string in Meta's browser format:
    #PWD_BROWSER:<version>:<timestamp>:<base64_payload>
    """
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
    1. Generates ephemeral 32-byte AES-256 key.
    2. Encrypts password using AES-256-GCM with 12 zero IV bytes and timestamp string as AAD.
    3. Encrypts AES key using TweetNaCl SealedBox with Meta's X25519 public key.
    4. Packs binary envelope:
       [version: 1B] + [key_id: 1B] + [sealed_len: 2B (0x50, 0x00)] + [sealed_key: 80B] + [tag: 16B] + [ciphertext]
    """
    ts = timestamp or int(time.time())
    ts_str = str(ts)

    # 1. Generate 32-byte AES-256 key
    aes_key = os.urandom(32)

    # 2. Encrypt password with AES-GCM (12-byte zero IV, timestamp as AAD)
    iv = bytes(12)
    aesgcm = AESGCM(aes_key)
    aad = ts_str.encode("utf-8")
    plaintext = password.encode("utf-8")

    ciphertext_and_tag = aesgcm.encrypt(iv, plaintext, aad)
    ciphertext = ciphertext_and_tag[:-16]
    tag = ciphertext_and_tag[-16:]

    # 3. Encrypt AES key using TweetNaCl SealedBox
    recipient_pk_bytes = bytes.fromhex(public_key_hex)
    recipient_pk = nacl.public.PublicKey(recipient_pk_bytes)
    sealed_box = nacl.public.SealedBox(recipient_pk)
    sealed_key = sealed_box.encrypt(aes_key)

    sealed_len = len(sealed_key)  # 80 bytes

    # 4. Construct Meta binary envelope
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
    """
    Computes Meta/Facebook dynamic jazoest checksum from LSD or fb_dtsg.
    Formula: '2' + sum of ASCII codepoints of the token.
    """
    if not token:
        return "22206"
    return "2" + str(sum(ord(c) for c in token))


# =====================================================================
# 2. FALCO TELEMETRY
# =====================================================================

def build_falco_payload(
    event_name: str,
    event_data: Dict[str, Any],
    client_session_id: str,
    web_session_id: str = "npdr3i:q5lguu:55za44",
    claim: str = "hmac_ttl.1789134777.AR1GWI4H8HqheGesK9mJOlXPT5j4lcjCd4vFk0DAT3lhNJOv"
) -> Tuple[str, str]:
    """
    Builds the (ts, q) payload for /ajax/bz falco event posts.
    """
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
# 3. META AUTH CLIENT
# =====================================================================

class MetaAuthClient:
    """
    Automates the Meta Account / Kadabra registration and auth flow on auth.meta.com.
    """

    BASE_URL = "https://auth.meta.com"
    SOURCE_APP_ID = "1522763855472543"  # Meta AI / Kadabra client app ID
    TOS_CMS_ID = "957798449862312"

    def __init__(self, proxy: Optional[str] = None, impersonate: str = "chrome124"):
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
        """
        Step 1: Loads the initial landing page to acquire datr cookies, LSD, dynamic jazoest, rcs, and public keys.
        """
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

            # Log cookies
            cookies_dict = resp.cookies.get_dict() if hasattr(resp.cookies, "get_dict") else {}
            if cookies_dict:
                logger.info(f"Session cookies acquired: {list(cookies_dict.keys())}")

            # Extract rcs token
            if "rcs=" in resp.url:
                self.rcs = resp.url.split("rcs=")[-1].split("&")[0]
                logger.info(f"Extracted RCS signature: {self.rcs[:30]}...")

            # Extract LSD Token
            import re
            lsd_match = re.search(r'\["LSD",\[\],\{"token":"([^"]+)"\}', text)
            if not lsd_match:
                lsd_match = re.search(r'name="lsd"\s+value="([^"]+)"', text)
            if lsd_match:
                self.lsd = lsd_match.group(1)
                self.jazoest = compute_jazoest(self.lsd)
                logger.info(f"Extracted LSD: {self.lsd} | Calculated Jazoest: {self.jazoest}")

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

            # Update default session headers
            self.session.headers["referer"] = self._get_current_referer()
            return True
        except Exception as e:
            logger.error(f"Failed to initialize auth session: {e}")
            return False

    def send_falco_event(self, event_name: str, event_data: Dict[str, Any]) -> bool:
        """
        Dispatches Falco telemetry event to /ajax/bz.
        """
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
        """
        Encrypts password using Meta's envelope encryption.
        """
        pk = self.public_key or "19c30cf2379e56b8ab368d6f2479cb893a09a023db2d062b2686bb8761881e47"
        return encrypt_meta_password(
            password=password,
            public_key_hex=pk,
            key_id=self.key_id,
            timestamp=timestamp
        )

    def check_contact_point_availability(self, email: str) -> Dict[str, Any]:
        """
        Step 2: Checks email availability.
        Endpoint: POST /api/check-contact-point-availability/
        """
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
        """
        Step 3: Validates password strength / payload.
        Endpoint: POST /api/check-password/
        """
        logger.info("Validating password...")
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
        logger.info(f"Check password response [{resp.status_code}]: {resp.text[:250]}")
        return self._parse_fb_response(resp.text)

    def check_date_of_birth(self, email: str, dob: str = "2000-01-01") -> Dict[str, Any]:
        """
        Step 4: Checks date of birth eligibility.
        Endpoint: POST /api/check-date-of-birth/
        """
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

    def suggest_username(self) -> Dict[str, Any]:
        """
        Step 5: Requests generated username suggestions from Kadabra endpoint.
        Endpoint: POST /api/kadabra/suggest_username/
        """
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
        """
        Step 6: Validates user handle availability and TOS status.
        Endpoint: POST /api/kadabra/check_profile/
        """
        logger.info(f"Validating profile handle: {username}")
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
        """
        Step 7: Submits final registration and creates the Meta account!
        Endpoint: POST /login/device-based/kadabra-register-save-credentials/
        """
        logger.info(f"Submitting final registration for {email} ({username})...")
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
        logger.info(f"Registration response status [{resp.status_code}]: {resp.text[:300]}")

        cookies_dict = resp.cookies.get_dict() if hasattr(resp.cookies, "get_dict") else {}
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

        return parsed

    def confirm_email_otp(self, code: str, email: Optional[str] = None) -> Dict[str, Any]:
        """
        Submits 6-digit confirmation code / OTP to confirm and activate Meta account.
        Uses FRLConfirmEmailMutation (doc_id: 9851798224911796).
        """
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
        return self.send_graphql_request(doc_id="9851798224911796", variables=variables)

    def resend_email_otp(self, email: Optional[str] = None) -> Dict[str, Any]:
        """
        Resends email confirmation OTP code.
        Uses FRLResendOTPMutation (doc_id: 25885129117818218).
        """
        target_email = email or self.email
        logger.info(f"Requesting OTP resend for {target_email}...")
        variables = {
            "input": {
                "contact_point": {
                    "sensitive_string_value": target_email
                },
                "contact_point_type": "EMAIL_ADDRESS",
                "event_flow": "ntm",
                "rl_client_session_id": self.client_session_id,
                "source_app_id": self.SOURCE_APP_ID,
                "waterfall_id": self.waterfall_id
            }
        }
        return self.send_graphql_request(doc_id="25885129117818218", variables=variables)

    def send_graphql_request(self, doc_id: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends GraphQL query/mutation (e.g. email OTP confirmation).
        Endpoint: POST /api/graphql/
        """
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
        """
        Strips 'for (;;);' anti-hijacking prefix and parses JSON.
        """
        clean_text = text
        if clean_text.startswith("for (;;);"):
            clean_text = clean_text[9:]
        try:
            return json.loads(clean_text)
        except Exception:
            return {"raw_response": text}


# =====================================================================
# 4. RUNNER & WORKFLOW ENTRY POINT
# =====================================================================

def generate_random_email(domain: str = "mailinator.com") -> str:
    prefix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"meta_test_{prefix}@{domain}"


def run_registration(
    email: Optional[str] = None,
    password: str = "SecurePassword2026!",
    dob: str = "2001-08-14",
    proxy: Optional[str] = None,
    auto_otp: bool = True
):
    ninja_client: Optional[Any] = None

    if not email:
        if HAS_NINJA:
            print("\n[*] Generating temporary email via TempMail Ninja...")
            try:
                ninja_client = TempMailNinjaAPI()
                ninja_client.connect()
                target_email = ninja_client.create_temp_email()
                print(f"[*] Generated Ninja email: {target_email}")
            except Exception as e:
                print(f"[!] TempMail Ninja generation failed ({e}), falling back to random email...")
                ninja_client = None
                target_email = generate_random_email()
        else:
            target_email = generate_random_email()
            print(f"[*] Using random email: {target_email}")
    else:
        target_email = email

    print("=" * 60)
    print("       META AUTH & REGISTRATION AUTOMATION CLIENT       ")
    print("=" * 60)

    # 1. Instantiate Client
    client = MetaAuthClient(proxy=proxy, impersonate="chrome124")

    # 2. Initialize Session
    print("\n[1/7] Initializing session on auth.meta.com...")
    if not client.init_session():
        print("[!] Failed to initialize session. Exiting.")
        return

    print(f"      Waterfall ID: {client.waterfall_id}")
    print(f"      Client Session ID: {client.client_session_id}")
    print(f"      LSD Token: {client.lsd}")
    print(f"      Key ID: {client.key_id}")
    print(f"      Public Key: {client.public_key[:16] if client.public_key else 'default'}...")
    print(f"      Jurisdiction: {client.jurisdiction}")

    # 3. Check Contact Point Availability
    print(f"\n[2/7] Checking availability for email: {target_email}...")
    cp_resp = client.check_contact_point_availability(email=target_email)
    print(f"      Result: {cp_resp}")

    # 4. Check Password
    print(f"\n[3/7] Validating password with server...")
    pwd_resp = client.check_password(email=target_email, password_raw_or_payload=password, dob=dob)
    print(f"      Result: {pwd_resp}")

    # 5. Check Date of Birth
    print(f"\n[4/7] Checking DOB eligibility ({dob})...")
    dob_resp = client.check_date_of_birth(email=target_email, dob=dob)
    print(f"      Result: {dob_resp}")

    # 6. Suggest Username
    print(f"\n[5/7] Requesting suggested username...")
    suggest_resp = client.suggest_username()
    suggested_username = (
        suggest_resp.get("payload", {}).get("username")
        if isinstance(suggest_resp, dict) and "payload" in suggest_resp
        else None
    )
    if not suggested_username:
        suggested_username = "meta_user_" + client.waterfall_id[:8]

    print(f"      Using Username: {suggested_username}")

    # 7. Check Profile & TOS Consent
    print(f"\n[6/7] Confirming Kadabra profile handle: {suggested_username}...")
    profile_resp = client.check_profile(username=suggested_username)
    print(f"      Result: {profile_resp}")

    # 8. Submit Registration & Save Credentials
    print(f"\n[7/7] Submitting final registration for {target_email} ({suggested_username})...")
    reg_resp = client.register_save_credentials(
        email=target_email,
        password_raw_or_payload=password,
        username=suggested_username,
        dob=dob
    )
    print(f"\n[*] Final Registration Response:\n{reg_resp}")

    if client.created_user_id:
        print(f"\n[SUCCESS] Meta Account registered! Account ID: {client.created_user_id}")
        payload = reg_resp.get("payload", {})
        if isinstance(payload, dict) and not payload.get("confirmed", True):
            print(f"\n[*] A 6-digit confirmation code has been sent to {target_email}.")
            otp_code = None

            # Automatically poll TempMail Ninja for OTP if ninja_client was used
            if auto_otp and ninja_client:
                print(f"[*] Automatically waiting for OTP from TempMail Ninja inbox (up to 90s)...")
                try:
                    otp_code = ninja_client.wait_for_otp(timeout=90, poll_interval=3)
                    if otp_code:
                        print(f"[*] Successfully retrieved OTP code: {otp_code}")
                except Exception as e:
                    print(f"[-] Error waiting for OTP via TempMail Ninja: {e}")

            if not otp_code:
                try:
                    otp_code = input("Enter the 6-digit confirmation code from your email (or press Enter to skip): ").strip()
                except (EOFError, KeyboardInterrupt):
                    otp_code = None

            if otp_code:
                print(f"\n[*] Submitting confirmation code {otp_code}...")
                confirm_resp = client.confirm_email_otp(code=otp_code)
                confirm_data = confirm_resp.get("data", {}).get("confirm_email", {}) if "data" in confirm_resp else confirm_resp.get("confirm_email", {})
                if confirm_data.get("isConfirmed") or confirm_data.get("is_confirmed") or confirm_data.get("accountId"):
                    account_id = confirm_data.get("accountId") or confirm_data.get("account_id") or client.created_user_id
                    print(f"\n[SUCCESS] Meta Account {target_email} successfully confirmed and activated! (Account ID: {account_id})")
                else:
                    print(f"[*] Response: {confirm_resp}")

            if ninja_client:
                ninja_client.disconnect()
        else:
            if ninja_client:
                ninja_client.disconnect()
    else:
        print("\n[*] Registration submitted. Check the response payload above for details.")
        if ninja_client:
            ninja_client.disconnect()


if __name__ == "__main__":
    email_arg = sys.argv[1] if len(sys.argv) > 1 else None
    pwd_arg = sys.argv[2] if len(sys.argv) > 2 else "SecurePassword2026!"
    
    run_registration(
        email=email_arg,
        password=pwd_arg,
        dob="2001-08-14",
        proxy=None  # e.g. "http://user:pass@host:port"
    )
