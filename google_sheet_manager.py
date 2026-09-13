import os
import time
import json
import threading
import requests
import gspread
from google.oauth2.service_account import Credentials

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DEFAULT_SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "service_account.json")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

class GoogleSheetManager:
    """
    Centralized, thread-safe Google Sheets API & Webhook Service Manager.
    Supports official Google Sheets API v4 (Service Account) with automatic Webhook fallback.
    """
    def __init__(self, config_file=CONFIG_FILE):
        self.config_file = config_file
        self.lock = threading.RLock()
        self.client = None
        self.spreadsheet = None
        self.input_sheet = None
        self.done_sheet = None
        self.mode = "Google Sheets API"
        self.input_sheet_name = "Sheet1"
        self.done_sheet_name = "Sheet2"
        self.claimed_rows = set()
        self._last_connect_time = 0

    def _log(self, tag, message, instance_name=""):
        timestamp = time.strftime("%H:%M:%S")
        inst_tag = f"[{instance_name:^6}]" if instance_name else "[  MAIN  ]"
        tag_padded = f"[{tag:<14}]"
        print(f"[{timestamp}] {inst_tag} {tag_padded} {message}", flush=True)

    def load_config(self):
        """Read Google Sheet configuration settings from config.json"""
        cfg = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception as e:
                self._log("CONFIG-ERR", f"Failed reading config.json: {e}")

        gs_cfg = cfg.get("google_sheet", {})
        
        sa_file = gs_cfg.get("service_account_file") or cfg.get("service_account_file", "service_account.json")
        if not os.path.isabs(sa_file):
            sa_file = os.path.join(BASE_DIR, sa_file)

        sheet_id = (
            gs_cfg.get("spreadsheet_id")
            or gs_cfg.get("spreadsheet_url")
            or gs_cfg.get("spreadsheet_id_or_url")
            or cfg.get("google_sheet_url")
            or cfg.get("spreadsheet_id", "")
        ).strip()

        webhook_url = (
            gs_cfg.get("google_sheet_webhook_url")
            or cfg.get("google_sheet_webhook_url", "")
        ).strip()

        self.input_sheet_name = gs_cfg.get("input_sheet_name") or cfg.get("input_sheet_name", "Sheet1")
        self.done_sheet_name = gs_cfg.get("done_sheet_name") or cfg.get("done_sheet_name", "Sheet2")
        enabled = gs_cfg.get("enabled", True)

        return {
            "enabled": enabled,
            "service_account_file": sa_file,
            "spreadsheet_id": sheet_id,
            "webhook_url": webhook_url,
            "input_sheet_name": self.input_sheet_name,
            "done_sheet_name": self.done_sheet_name
        }

    def is_configured(self):
        """Check if either Google Sheets API or Webhook service is configured"""
        cfg = self.load_config()
        if not cfg["enabled"]:
            return False
        # 1. API mode
        if os.path.exists(cfg["service_account_file"]) and cfg["spreadsheet_id"]:
            return True
        # 2. Webhook fallback mode
        if cfg["webhook_url"]:
            return True
        return False

    def is_api_mode(self):
        """Check if official Google Sheets API service is active"""
        cfg = self.load_config()
        return bool(cfg["enabled"] and os.path.exists(cfg["service_account_file"]) and cfg["spreadsheet_id"])

    def connect(self, force_refresh=False):
        """Authenticate with Google Sheets API using Service Account and open Spreadsheet"""
        if not self.is_api_mode():
            cfg = self.load_config()
            if cfg["webhook_url"]:
                self.mode = "Google Apps Script Webhook"
                return True, "Webhook mode active"
            return False, "Neither spreadsheet_id nor google_sheet_webhook_url configured"

        with self.lock:
            if self.spreadsheet and not force_refresh and (time.time() - self._last_connect_time < 300):
                return True, "Already connected"

            cfg = self.load_config()
            sa_file = cfg["service_account_file"]
            sheet_target = cfg["spreadsheet_id"]

            try:
                credentials = Credentials.from_service_account_file(sa_file, scopes=SCOPES)
                self.client = gspread.authorize(credentials)

                if sheet_target.startswith("https://"):
                    self.spreadsheet = self.client.open_by_url(sheet_target)
                else:
                    self.spreadsheet = self.client.open_by_key(sheet_target)

                worksheets = {ws.title: ws for ws in self.spreadsheet.worksheets()}

                if self.input_sheet_name in worksheets:
                    self.input_sheet = worksheets[self.input_sheet_name]
                else:
                    self.input_sheet = self.spreadsheet.sheet1
                    self.input_sheet_name = self.input_sheet.title

                if self.done_sheet_name in worksheets:
                    self.done_sheet = worksheets[self.done_sheet_name]
                else:
                    try:
                        self.done_sheet = self.spreadsheet.add_worksheet(title=self.done_sheet_name, rows="1000", cols="10")
                        self.done_sheet.append_row(["Timestamp", "Email", "Username", "Password", "Data"])
                    except Exception:
                        self.done_sheet = self.spreadsheet.sheet1

                self.mode = "Google Sheets API (Service Account)"
                self._last_connect_time = time.time()
                return True, "Successfully connected via Google Sheets API"
            except Exception as e:
                self.spreadsheet = None
                return False, f"API Connection failed: {e}"

    def save_created_account(self, timestamp, email, username, password, status="", instance_name=""):
        """
        Save newly created Meta account to Google Sheet (Sheet1) or via Webhook.
        """
        cfg = self.load_config()
        if not cfg["enabled"]:
            return False

        # 1. Try Google Sheets API
        if self.is_api_mode():
            with self.lock:
                ok, msg = self.connect()
                if ok and self.input_sheet:
                    try:
                        all_values = self.input_sheet.get_all_values()
                        if not all_values:
                            self.input_sheet.append_row(["Timestamp", "Email", "Username", "Password", "Status"])
                            all_values = [["Timestamp", "Email", "Username", "Password", "Status"]]

                        for row in all_values:
                            if len(row) > 1 and row[1].strip().lower() == email.strip().lower():
                                self._log("GSHEET-DUP", f"Account '{email}' already exists in '{self.input_sheet_name}'", instance_name=instance_name)
                                return True

                        row_payload = [str(timestamp), str(email).strip(), str(username).strip(), str(password).strip(), str(status).strip()]
                        self.input_sheet.append_row(row_payload)
                        self._log("GSHEET-SAVED", f"Account '{email}' saved to '{self.input_sheet_name}' via Google Sheets API", instance_name=instance_name)
                        return True
                    except Exception as e:
                        self._log("GSHEET-ERR", f"API save failed: {e}. Trying fallback...", instance_name=instance_name)

        # 2. Webhook Fallback
        webhook_url = cfg.get("webhook_url", "")
        if webhook_url:
            try:
                payload = {
                    "timestamp": timestamp,
                    "email": email,
                    "username": username,
                    "password": password,
                    "status": status
                }
                res = requests.post(webhook_url, json=payload, timeout=12)
                if res.status_code in [200, 302]:
                    self._log("GSHEET-SAVED", f"Account '{email}' saved via Google Webhook", instance_name=instance_name)
                    return True
                else:
                    self._log("GSHEET-WARN", f"Webhook response: {res.status_code}", instance_name=instance_name)
            except Exception as w_err:
                self._log("GSHEET-ERR", f"Webhook failed: {w_err}", instance_name=instance_name)

        if not self.is_configured():
            self._log("GSHEET-WARN", "Google Sheet not configured! Provide 'spreadsheet_id' or 'google_sheet_webhook_url' in config.json.", instance_name=instance_name)

        return False

    def save_done_account(self, timestamp, email, username, password, extra_data="", instance_name=""):
        """
        Save finished account (with cookies/2FA) to Sheet2 or via Webhook.
        """
        cfg = self.load_config()
        if not cfg["enabled"]:
            return False

        # 1. Try Google Sheets API
        if self.is_api_mode():
            with self.lock:
                ok, msg = self.connect()
                if ok and self.done_sheet:
                    try:
                        all_values = self.done_sheet.get_all_values()
                        if not all_values:
                            self.done_sheet.append_row(["Timestamp", "Email", "Username", "Password", "Data"])

                        for row in all_values:
                            if len(row) > 1 and row[1].strip().lower() == email.strip().lower():
                                self._log("GSHEET-DUP", f"Account '{email}' already exists in '{self.done_sheet_name}'", instance_name=instance_name)
                                return True

                        row_payload = [str(timestamp), str(email).strip(), str(username).strip(), str(password).strip(), str(extra_data).strip()]
                        self.done_sheet.append_row(row_payload)
                        self._log("GSHEET-SAVED", f"Done account '{email}' saved to '{self.done_sheet_name}' via Google Sheets API", instance_name=instance_name)
                        return True
                    except Exception as e:
                        self._log("GSHEET-ERR", f"API save failed: {e}. Trying fallback...", instance_name=instance_name)

        # 2. Webhook Fallback
        webhook_url = cfg.get("webhook_url", "")
        if webhook_url:
            try:
                payload = {
                    "timestamp": timestamp,
                    "email": email,
                    "username": username,
                    "password": password,
                    "data": extra_data,
                    "cookies": extra_data
                }
                res = requests.post(webhook_url, json=payload, timeout=12)
                if res.status_code in [200, 302]:
                    self._log("GSHEET-SAVED", f"Done account '{email}' synced via Google Webhook", instance_name=instance_name)
                    return True
                else:
                    self._log("GSHEET-WARN", f"Webhook response: {res.status_code}", instance_name=instance_name)
            except Exception as w_err:
                self._log("GSHEET-ERR", f"Webhook failed: {w_err}", instance_name=instance_name)

        return False

    def get_next_free_account(self, instance_name=""):
        """
        Fetch next available pending account from input_sheet (Sheet1) via Google Sheets API.
        """
        if not self.is_api_mode():
            return None

        with self.lock:
            ok, msg = self.connect()
            if not ok or not self.input_sheet:
                return None

            try:
                all_rows = self.input_sheet.get_all_values()
                if not all_rows:
                    return None

                email_col = 1
                pwd_col = 3
                status_col = 4

                # Check if first row is a header row or actual data row
                first_row = [h.strip().lower() for h in all_rows[0]]
                is_header = any("email" in h or "timestamp" in h or "password" in h for h in first_row)
                start_row = 1 if is_header else 0

                if is_header:
                    for idx, name in enumerate(first_row):
                        if "email" in name or "mail" in name:
                            email_col = idx
                        elif "pass" in name or "pwd" in name:
                            pwd_col = idx
                        elif "status" in name or "state" in name:
                            status_col = idx

                for row_idx in range(start_row, len(all_rows)):
                    if row_idx in self.claimed_rows:
                        continue

                    row = all_rows[row_idx]
                    email = row[email_col].strip() if len(row) > email_col else ""
                    pwd = row[pwd_col].strip() if len(row) > pwd_col else ""
                    status = row[status_col].strip().lower() if len(row) > status_col else ""

                    if not email or "@" not in email or email.lower() == "email":
                        continue

                    if not pwd:
                        continue

                    if status not in ["done", "running", "failed"]:
                        try:
                            self.input_sheet.update_cell(row_idx + 1, status_col + 1, "running")
                        except Exception as upd_err:
                            self._log("GSHEET-WARN", f"Failed updating cell status: {upd_err}", instance_name=instance_name)

                        self.claimed_rows.add(row_idx)
                        return (email, pwd, row_idx + 1, len(all_rows))

                return None
            except Exception as e:
                self._log("GSHEET-ERR", f"Failed fetching account from '{self.input_sheet_name}': {e}", instance_name=instance_name)
                return None


    def update_account_status(self, row_idx, email, status, reason="", instance_name=""):
        """Update account status in input_sheet (Sheet1) via Google Sheets API or Webhook"""
        cfg = self.load_config()
        if not cfg["enabled"]:
            return False

        if self.is_api_mode():
            with self.lock:
                ok, msg = self.connect()
                if ok and self.input_sheet:
                    try:
                        all_rows = self.input_sheet.get_all_values()
                        if not all_rows:
                            return False

                        header = [h.strip().lower() for h in all_rows[0]]
                        status_col = 4
                        reason_col = 5

                        for idx, name in enumerate(header):
                            if "status" in name or "state" in name:
                                status_col = idx
                            elif "reason" in name or "error" in name or "note" in name:
                                reason_col = idx

                        target_row = None
                        if row_idx and 1 <= row_idx <= len(all_rows):
                            target_row = row_idx
                        else:
                            for r_i in range(1, len(all_rows)):
                                r = all_rows[r_i]
                                for cell in r:
                                    if cell.strip().lower() == email.strip().lower():
                                        target_row = r_i + 1
                                        break
                                if target_row:
                                    break

                        if target_row:
                            self.input_sheet.update_cell(target_row, status_col + 1, status)
                            if reason:
                                self.input_sheet.update_cell(target_row, reason_col + 1, str(reason))
                            self._log("GSHEET-STATUS", f"Row {target_row} ({email}) status='{status}' ({reason}) in '{self.input_sheet_name}'", instance_name=instance_name)
                            return True

                        return False
                    except Exception as e:
                        self._log("GSHEET-ERR", f"Failed updating status for '{email}': {e}", instance_name=instance_name)

        # Webhook fallback
        webhook_url = cfg.get("webhook_url", "")
        if webhook_url:
            try:
                payload = {
                    "action": "update_status",
                    "email": email,
                    "status": status,
                    "reason": reason,
                    "row_idx": row_idx
                }
                res = requests.post(webhook_url, json=payload, timeout=8)
                if res.status_code in [200, 302]:
                    self._log("GSHEET-STATUS", f"Status '{status}' ({reason}) synced via Webhook for {email}", instance_name=instance_name)
                    return True
            except Exception:
                pass

        return False


# Global singleton instance
gsheet_manager = GoogleSheetManager()
