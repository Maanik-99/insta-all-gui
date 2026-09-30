"""
==============================================================================
🚀 AUTOMETAINSTABOT - ULTIMATE MULTI-EMULATOR CONTROLLER & GUI DASHBOARD
==============================================================================
Features:
- 100% Lag-Free Architecture (All I/O, Subprocesses & Bot Pipelines in Background)
- Side-by-Side Live View Layout (Screen Preview on LEFT, Log Box on RIGHT)
- Compact Vertical Height to fit multiple emulator cards on screen
- Auto-Trimming Log Buffer (Max 50 lines to prevent memory bloat and lag)
- Exact Monotonic Submission & Approval Counters (100% Fix for Double Counting)
- Full Online & Offline Emulator Instance Detection (LDPlayer & MuMu Player)
- Turn Offline Instances Online (One-click Launch)
- Turn Online Instances Offline (One-click Shutdown)
- One-Click Device / IMEI / Android ID / MAC Randomization for LD & MuMu
- Per-Instance Target Bot Selector (@PayGoeasy_bot vs @tasklyBux_bot)
- Interactive 60FPS Screen Mirroring via scrcpy (One-click touch/mouse control)
- One-Click Window Organizer & Auto-Tile (Neatly arranges all emulator windows)
- Run / Stop / Restart AutoMetaInstaBot Pipeline per Instance
- Global Master Controls (Start All, Stop All, Launch All, Randomize All, Tile Windows)
==============================================================================
"""

import asyncio
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if os.getcwd() != BASE_DIR:
    try:
        os.chdir(BASE_DIR)
    except Exception:
        pass
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import customtkinter as ctk
from PIL import Image

# Import Core Bot Engine & Config
import autometainstabot
from emulator_manager import (
    EmulatorInstance,
    scan_all_instances,
    launch_emulator,
    shutdown_emulator,
    restart_emulator,
    randomize_emulator_identity,
    connect_device_adb,
    capture_device_screen,
    launch_scrcpy,
    show_emulator_window,
    organize_all_windows,
    ADB_BIN,
    LDCONSOLE_BIN,
    MUMU_BIN,
    SCRCPY_BIN,
)

# CustomTkinter styling
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Thread-safe log queue for GUI
GUI_LOG_QUEUE = queue.Queue()


def simplify_log_message(tag: str, raw_msg: str) -> Optional[str]:
    """Transforms verbose logs into clean, emoji-rich, readable status updates."""
    tag_clean = tag.upper().strip()
    msg_clean = raw_msg.strip()

    # Filter out noisy internal debug dumps
    if any(k in tag_clean for k in ["XML", "HIERARCHY", "DUMP", "PAGE-DUMP", "COORD", "REL-COORD", "DEV-SCAN"]):
        return None
    if any(k in msg_clean.lower() for k in ["xml hierarchy", "empty ui dump", "getting screen hierarchy"]):
        return None

    # Highlight key milestones
    if "CYCLE-START" in tag_clean or "Starting Taskly" in msg_clean:
        m = re.search(r"Cycle #?(\d+)", msg_clean, re.IGNORECASE)
        c_num = m.group(1) if m else "1"
        return f"🚀 Starting Cycle #{c_num}..."

    elif "STEP-1" in tag_clean or "Initializing task menu" in msg_clean:
        return "📋 Requesting task from Telegram Bot..."

    elif "BOT CREDENTIALS ACQUIRED" in msg_clean or "Credentials Received" in msg_clean:
        m_user = re.search(r"Target User\s*:\s*([^\r\n]+)", msg_clean)
        u_name = m_user.group(1).strip() if m_user else "Account"
        return f"👤 Got Task from Bot: Username '{u_name}'"

    elif "STEP-2" in tag_clean and "Meta AI" in msg_clean:
        m_name = re.search(r"for:\s*([^\r\n|]+)", msg_clean)
        n_str = m_name.group(1).strip() if m_name else "User"
        return f"🌐 Meta AI: Registering account ({n_str})..."

    elif "STEP-6" in tag_clean or ("Waiting for" in msg_clean and "OTP" in msg_clean):
        return "✉️ Waiting for Meta AI OTP email code..."

    elif "OTP-SUCCESS" in tag_clean or "Found OTP" in msg_clean or "Found Meta OTP" in msg_clean:
        m_otp = re.search(r":\s*(\d{6,8})", msg_clean)
        otp = m_otp.group(1) if m_otp else "Code"
        return f"🔑 Meta OTP Code Received: {otp}"

    elif "STEP-3" in tag_clean or "Running Instagram 2FA" in msg_clean:
        return "📱 Emulator: Starting Instagram & 2FA Setup..."

    elif "2FA-CAPTURED" in tag_clean or "Secret Key:" in msg_clean:
        m_key = re.search(r"Secret Key:\s*'([^']+)'", msg_clean)
        key = m_key.group(1) if m_key else "Key"
        return f"🔐 2FA Secret Key Captured: {key}"

    elif "2FA-OTP" in tag_clean or "Generated 6-Digit OTP" in msg_clean:
        m_totp = re.search(r"pyotp:\s*'(\d{6})'", msg_clean)
        totp = m_totp.group(1) if m_totp else "OTP"
        return f"📲 2FA Authenticator TOTP: {totp}"

    elif "EMAIL-CODE" in tag_clean or "Fetching 8-digit security code" in msg_clean:
        return "📬 Fetching 8-digit Instagram security code from inbox..."

    elif "Found 8-digit 2FA Security Code" in msg_clean:
        m_code = re.search(r":\s*(\d{8})", msg_clean)
        code = m_code.group(1) if m_code else ""
        return f"🔑 8-digit Security Code Received: {code}"

    elif "TG-SUBMITTED" in tag_clean or "report has been received" in msg_clean.lower() or "report received" in msg_clean.lower():
        m_s = re.search(r"(?:Total(?:\s+Submitted)?|Submitted):\s*(\d+)", msg_clean, re.IGNORECASE)
        s_str = f" [Total: {m_s.group(1)}]" if m_s else ""
        return f"📥 ✅ Report Received & Submitted to Bot!{s_str}"

    elif "TG-BALANCE" in tag_clean or "your balance:" in msg_clean.lower() or "current balance:" in msg_clean.lower() or ("balance:" in msg_clean.lower() and "$" in msg_clean):
        m_b = re.search(r"(\$[\d\.]+|\d+\.\d{2,4})", msg_clean)
        b_str = m_b.group(1) if m_b else msg_clean
        if not b_str.startswith("$") and re.match(r"^\d", b_str):
            b_str = f"${b_str}"
        return f"💰 Current Balance: {b_str}"

    elif "TG-APPROVED" in tag_clean or "report approved" in msg_clean.lower() or "+$0.03" in msg_clean:
        m_a = re.search(r"(?:Total(?:\s+Approved)?|Approved):\s*(\d+)", msg_clean, re.IGNORECASE)
        a_str = f" [Total: {m_a.group(1)}]" if m_a else ""
        return f"💰 ✅ Report Approved (+$0.03)!{a_str}"

    elif "Report confirmed and accepted" in msg_clean:
        return "✅ Bot confirmed task report & credited reward!"

    elif "IMEI-CHANGE" in tag_clean:
        return f"🎲 {msg_clean}"

    elif "SUCCESS" in tag_clean:
        return f"✅ {msg_clean}"

    elif "ERR" in tag_clean or "FAILED" in tag_clean:
        return f"❌ {msg_clean}"

    elif "WARN" in tag_clean or "CYCLE-ABORT" in tag_clean:
        return f"⚠️ {msg_clean}"

    if len(msg_clean) < 140 and not ("{" in msg_clean and "}" in msg_clean):
        return msg_clean

    return None


def custom_gui_log_callback(tag: str, message: str, instance_name: str = ""):
    """Intercepts autometainstabot logs and pushes to thread-safe GUI queue."""
    simple_msg = simplify_log_message(tag, message)
    if simple_msg:
        t_now = datetime.now().strftime("%H:%M:%S")
        GUI_LOG_QUEUE.put({
            "instance": instance_name or "MAIN",
            "tag": tag,
            "time": t_now,
            "text": simple_msg,
            "raw": message
        })


# Bind autometainstabot logging
autometainstabot.set_log_callback(custom_gui_log_callback)


class BotRunnerThread(threading.Thread):
    """
    Dedicated background thread per instance for running autometainstabot.TasklyWorker.
    Owns an isolated asyncio loop and TelegramClient session.
    """
    def __init__(self, instance_key: str, account_cfg: dict, device_serial: str, target_bot: str, on_state_change=None, on_account_switched=None, available_bots=None, task_mode: str = "2fa"):
        super().__init__(daemon=True)
        self.instance_key = instance_key
        self.account_cfg = account_cfg
        self.device_serial = device_serial
        self.target_bot = target_bot
        self.on_state_change = on_state_change
        self.on_account_switched = on_account_switched
        self.available_bots = available_bots or ["PayGoeasy_bot", "tasklyBux_bot"]
        self.task_mode = task_mode

        self.worker: Optional[autometainstabot.TasklyWorker] = None
        self.tg_client = None
        self.loop = None
        self._is_stopped = False

    def _on_worker_account_switched(self, instance_name: str, new_phone: str, new_bot: str):
        if self.on_account_switched:
            self.on_account_switched(self.instance_key, new_phone, new_bot)

    def run(self):
        self._is_stopped = False
        mode_icon = "🍪" if self.task_mode == "cookies" else "🔑"
        if self.on_state_change:
            self.on_state_change(self.instance_key, f"Running ({mode_icon} @{self.target_bot})", "running")

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        try:
            self.loop.run_until_complete(self._async_run())
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        except Exception as e:
            if not self._is_stopped:
                custom_gui_log_callback("ERR", f"Worker failed: {e}", self.instance_key)
                if self.on_state_change:
                    self.on_state_change(self.instance_key, "Error", "error")
        finally:
            self._cleanup()

    async def _async_run(self):
        from telethon import TelegramClient

        acc = dict(self.account_cfg)
        clean_phone = re.sub(r'[^0-9]', '', str(acc.get('phone', '')))
        sess_name = acc.get('session_name') or f"taskly_session_{clean_phone}"
        sess_path = os.path.join(BASE_DIR, sess_name) if not os.path.isabs(sess_name) else sess_name

        target_bot = self.target_bot or "PayGoeasy_bot"
        acc["target_bot"] = target_bot
        acc["target_bots"] = self.available_bots

        custom_gui_log_callback("INIT", f"Connecting Telegram for {acc.get('phone')} (Bot: @{target_bot}, Mode: {self.task_mode.upper()})...", self.instance_key)
        self.tg_client = TelegramClient(sess_path, acc['api_id'], acc['api_hash'])
        await self.tg_client.start(phone=clean_phone)
        custom_gui_log_callback("INIT", f"Telegram connected! Launching {self.task_mode.upper()} pipeline targeting @{target_bot}...", self.instance_key)

        self.worker = autometainstabot.TasklyWorker(
            account_cfg=acc,
            device_serial=self.device_serial,
            instance_name=self.instance_key,
            target_bot=target_bot,
            client=self.tg_client,
            on_account_switched_callback=self._on_worker_account_switched,
            available_bots=self.available_bots,
            task_mode=self.task_mode
        )

        # Initial balance update from bot
        try:
            await self.worker.fetch_bot_balance()
        except Exception:
            pass

        # Pure-API Meta Pipeline (Fast, lightweight, 100% direct API)
        await self.worker.run()

    def _cleanup(self):
        # Release account from global pool
        if self.worker and hasattr(self.worker, 'account_cfg') and self.worker.account_cfg.get('phone'):
            autometainstabot.TelegramAccountPool.release_active(self.worker.account_cfg.get('phone'))
        elif self.account_cfg and self.account_cfg.get('phone'):
            autometainstabot.TelegramAccountPool.release_active(self.account_cfg.get('phone'))

        try:
            pending = [t for t in asyncio.all_tasks(self.loop) if not t.done()]
            if pending:
                for t in pending:
                    t.cancel()
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass

        try:
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        except Exception:
            pass

        try:
            self.loop.close()
        except Exception:
            pass

        if self.tg_client:
            try:
                if hasattr(self.tg_client, 'session') and hasattr(self.tg_client.session, 'close'):
                    self.tg_client.session.close()
            except Exception:
                pass
            self.tg_client = None

        self._is_stopped = True
        if self.on_state_change:
            self.on_state_change(self.instance_key, "Stopped", "stopped")

    def stop(self):
        self._is_stopped = True
        if self.worker:
            self.worker.is_stopped = True
        if self.loop and self.loop.is_running():
            def _cancel():
                for t in asyncio.all_tasks(self.loop):
                    t.cancel()
            self.loop.call_soon_threadsafe(_cancel)


class EmulatorCard(ctk.CTkFrame):
    """
    Rich, compact, interactive card displaying an emulator instance (Online or Offline).
    Layout:
    - Header Row: Brand badge, Name, Status Pill
    - Specs Bar: ADB Serial, PID, Quick Mirror, Quick Show
    - Acc & Bot Row: Telegram Account Dropdown & Target Bot Dropdown
    - Stats Bar: Bal, Cycles, Sub, App (Monotonic, Exact Counters)
    - Body Frame (Horizontal Split):
      - Left Frame: Live Screen Preview (Compact, width 130px, height 195px)
      - Right Frame: Log Box (Autotrimmed to max 50 lines to prevent lag)
    - Action Buttons Bar: Turn Online/Shutdown, Random IMEI, Live View toggle, Run/Stop Bot, Clear
    """
    def __init__(
        self,
        master,
        instance_data: EmulatorInstance,
        accounts: List[dict],
        available_bots: List[str],
        dashboard,
    ):
        super().__init__(master, corner_radius=12, fg_color="#181b22", border_width=1, border_color="#282e3d")
        self.data = instance_data
        self.accounts = accounts
        self.available_bots = available_bots if available_bots else ["PayGoeasy_bot", "tasklyBux_bot"]
        self.dashboard = dashboard
        self.cfg = getattr(dashboard, 'cfg', {}) if dashboard else autometainstabot._CFG
        self.instance_key = f"{self.data.emu_type.upper()}-{self.data.index}"

        # Current metrics (exact monotonic values)
        self.meta_created_count = 0
        self.submitted_count = 0
        self.approved_count = 0
        self.cycle_count = 0
        self.balance = "$0.00"
        self.is_bot_running = False
        self._meta_counted_in_cycle = False

        self.selected_account = accounts[self.data.index % len(accounts)] if accounts else {}
        self.selected_bot = self.available_bots[0]

        # Live Screen Preview state
        self.preview_active = False
        self.preview_thread: Optional[threading.Thread] = None

        self._build_ui()

    def _build_ui(self):
        # 1. Header Frame
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 3))

        # Brand / Index badge
        type_color = "#3b82f6" if self.data.emu_type == "mumu" else "#f97316"
        badge_text = f"MuMu #{self.data.index}" if self.data.emu_type == "mumu" else f"LDPlayer #{self.data.index}"
        
        self.badge_lbl = ctk.CTkLabel(
            header,
            text=badge_text,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#ffffff",
            fg_color=type_color,
            corner_radius=6,
            padx=8,
            pady=2,
        )
        self.badge_lbl.pack(side="left")

        # Name
        self.name_lbl = ctk.CTkLabel(
            header,
            text=f"  {self.data.name}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f1f5f9"
        )
        self.name_lbl.pack(side="left")

        # Status Pill
        self.status_pill = ctk.CTkLabel(
            header,
            text="● Offline" if not self.data.is_online else "● Online",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#ef4444" if not self.data.is_online else "#22c55e",
            fg_color="#2d1519" if not self.data.is_online else "#142d1e",
            corner_radius=8,
            padx=10,
            pady=3,
        )
        self.status_pill.pack(side="right")

        # 2. Specs & Device Info Bar
        info_bar = ctk.CTkFrame(self, fg_color="#11141a", corner_radius=8)
        info_bar.pack(fill="x", padx=12, pady=3)

        serial_text = self.data.adb_serial if self.data.is_online else "Disconnected"
        self.info_lbl = ctk.CTkLabel(
            info_bar,
            text=f"ADB: {serial_text}  |  PID: {self.data.pid if self.data.pid > 0 else 'N/A'}",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8"
        )
        self.info_lbl.pack(side="left", padx=10, pady=3)

        # Quick Mirror & Show Window shortcuts
        self.btn_quick_show = ctk.CTkButton(
            info_bar,
            text="👁️ Show",
            font=ctk.CTkFont(size=10),
            width=48,
            height=20,
            fg_color="#1e293b",
            hover_color="#334155",
            command=self._on_show_window
        )
        self.btn_quick_show.pack(side="right", padx=(2, 6), pady=2)

        self.btn_quick_mirror = ctk.CTkButton(
            info_bar,
            text="🔍 Mirror",
            font=ctk.CTkFont(size=10, weight="bold"),
            width=56,
            height=20,
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self._on_launch_mirror
        )
        self.btn_quick_mirror.pack(side="right", padx=2, pady=2)
        if not self.data.is_online:
            self.btn_quick_mirror.configure(state="disabled", fg_color="#334155")
            self.btn_quick_show.configure(state="disabled", fg_color="#334155")

        # 3. Telegram Account & Target Bot Configuration Row
        config_frame = ctk.CTkFrame(self, fg_color="transparent")
        config_frame.pack(fill="x", padx=12, pady=2)

        # Account Selector
        ctk.CTkLabel(
            config_frame,
            text="📱 Acc:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#cbd5e1"
        ).pack(side="left")

        account_options = [f"{a.get('phone')}" for a in self.accounts]
        if not account_options:
            account_options = ["Not Configured"]

        default_acc_str = account_options[self.data.index % len(account_options)] if account_options else "Default"
        self.acc_combo = ctk.CTkComboBox(
            config_frame,
            values=account_options,
            command=self._on_account_selected,
            font=ctk.CTkFont(size=11),
            width=145,
            height=24,
            fg_color="#1e2430",
            border_color="#334155"
        )
        self.acc_combo.set(default_acc_str)
        self.acc_combo.pack(side="left", padx=(4, 10))

        # Target Bot Selector (User requirement: Choose between bots from GUI)
        ctk.CTkLabel(
            config_frame,
            text="🤖 Bot:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#cbd5e1"
        ).pack(side="left")

        clean_bots = [b.lstrip("@") for b in self.available_bots]
        self.bot_combo = ctk.CTkComboBox(
            config_frame,
            values=clean_bots,
            command=self._on_bot_selected,
            font=ctk.CTkFont(size=11),
            width=135,
            height=24,
            fg_color="#1e2430",
            border_color="#334155"
        )
        if self.dashboard and hasattr(self.dashboard, 'selected_global_bot') and self.dashboard.selected_global_bot:
            default_bot = self.dashboard.selected_global_bot
        elif clean_bots:
            default_bot = clean_bots[0]
        else:
            default_bot = "PayGoeasy_bot"
        self.selected_bot = default_bot
        self.bot_combo.set(default_bot)
        self.bot_combo.pack(side="left", padx=4)

        # Mode Selector (Cookies vs 2FA)
        ctk.CTkLabel(
            config_frame,
            text="📋 Mode:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#cbd5e1"
        ).pack(side="left", padx=(6, 2))

        if self.dashboard and hasattr(self.dashboard, 'cfg') and self.dashboard.cfg.get("task_mode"):
            cfg_mode = str(self.dashboard.cfg.get("task_mode")).lower()
        else:
            cfg_mode = self.cfg.get("task_mode", "2fa").lower()
        default_mode_str = "🔑 2FA" if "2fa" in cfg_mode else "🍪 Cookies"
        self.selected_mode = "2fa" if "2fa" in cfg_mode else "cookies"

        self.mode_combo = ctk.CTkComboBox(
            config_frame,
            values=["🔑 2FA", "🍪 Cookies"],
            command=self._on_mode_selected,
            font=ctk.CTkFont(size=11),
            width=115,
            height=24,
            fg_color="#1e2430",
            border_color="#334155"
        )
        self.mode_combo.set(default_mode_str)
        self.mode_combo.pack(side="left", padx=4)

        # 4. Stats Bar
        stats_frame = ctk.CTkFrame(self, fg_color="#131722", corner_radius=6)
        stats_frame.pack(fill="x", padx=12, pady=3)

        self.stats_lbl = ctk.CTkLabel(
            stats_frame,
            text="💰 Bal: $0.00  |  🌐 Meta: 0  |  📥 Sub: 0  |  📈 Success: 0.0%",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38bdf8"
        )
        self.stats_lbl.pack(padx=8, pady=2)

        # 5. BODY FRAME: Horizontal Split (Left = Live Screen Preview, Right = Log Box)
        # This keeps the card compact and displays logs on the right side of the screen!
        self.body_frame = ctk.CTkFrame(self, fg_color="transparent", height=195)
        self.body_frame.pack(fill="x", expand=False, padx=12, pady=4)
        self.body_frame.pack_propagate(False)

        # Left: Live Preview Frame (Compact Phone ratio 125x190)
        self.preview_frame = ctk.CTkFrame(
            self.body_frame,
            width=130,
            height=195,
            fg_color="#0a0d13",
            corner_radius=8,
            border_width=1,
            border_color="#1f293d"
        )
        self.preview_frame.pack_propagate(False)

        self.preview_lbl = ctk.CTkLabel(
            self.preview_frame,
            text="📱 Loading...",
            font=ctk.CTkFont(size=10),
            text_color="#64748b"
        )
        self.preview_lbl.pack(expand=True, fill="both", padx=2, pady=2)

        # Right: Log Box (Auto-trimmed to max 50 lines to prevent lag)
        self.log_box = ctk.CTkTextbox(
            self.body_frame,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color="#0b0e14",
            text_color="#e2e8f0",
            corner_radius=8,
            border_width=1,
            border_color="#1e2533",
            wrap="word",
            height=195
        )
        # Initially, log box takes full width
        self.log_box.pack(side="left", fill="both", expand=True)
        self.log_box.configure(state="disabled")

        # 6. Action Buttons Bar
        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.pack(fill="x", padx=12, pady=(2, 8))

        # Online/Offline toggle
        self.btn_online_toggle = ctk.CTkButton(
            btn_bar,
            text="🚀 Turn Online" if not self.data.is_online else "⏹ Shutdown",
            font=ctk.CTkFont(size=11, weight="bold"),
            width=88,
            height=26,
            fg_color="#059669" if not self.data.is_online else "#b91c1c",
            hover_color="#047857" if not self.data.is_online else "#991b1b",
            command=self._on_toggle_online
        )
        self.btn_online_toggle.pack(side="left", padx=(0, 4))

        # Randomize Device / IMEI
        self.btn_randomize = ctk.CTkButton(
            btn_bar,
            text="🎲 Random IMEI",
            font=ctk.CTkFont(size=11, weight="bold"),
            width=92,
            height=26,
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            command=self._on_randomize_imei
        )
        self.btn_randomize.pack(side="left", padx=4)

        # In-GUI Live Screen Toggle
        self.btn_live_view = ctk.CTkButton(
            btn_bar,
            text="📱 Live View",
            font=ctk.CTkFont(size=11, weight="bold"),
            width=82,
            height=26,
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self._on_toggle_live_view
        )
        self.btn_live_view.pack(side="left", padx=4)
        if not self.data.is_online:
            self.btn_live_view.configure(state="disabled", fg_color="#334155")

        # Run / Stop Bot Button
        self.btn_run_bot = ctk.CTkButton(
            btn_bar,
            text="▶ Run Bot",
            font=ctk.CTkFont(size=11, weight="bold"),
            width=76,
            height=26,
            fg_color="#16a34a",
            hover_color="#15803d",
            command=self._on_toggle_bot
        )
        self.btn_run_bot.pack(side="left", padx=4)
        if not self.data.is_online:
            self.btn_run_bot.configure(state="disabled", fg_color="#334155")

        # Clear Logs
        self.btn_clear = ctk.CTkButton(
            btn_bar,
            text="🗑 Clear",
            font=ctk.CTkFont(size=11),
            width=50,
            height=26,
            fg_color="#334155",
            hover_color="#475569",
            command=self.clear_logs
        )
        self.btn_clear.pack(side="right")

    def _on_account_selected(self, choice: str):
        for acc in self.accounts:
            if acc.get("phone") and str(acc.get("phone")) in choice:
                self.selected_account = acc
                self.append_log(datetime.now().strftime("%H:%M:%S"), f"Switched account to {acc.get('phone')}")
                break

    def _on_bot_selected(self, choice: str):
        self.selected_bot = choice.strip().lstrip("@")
        self.append_log(datetime.now().strftime("%H:%M:%S"), f"Target bot set to: @{self.selected_bot}")

    def _on_mode_selected(self, choice: str):
        self.selected_mode = "2fa" if "2fa" in choice.lower() else "cookies"
        self.append_log(datetime.now().strftime("%H:%M:%S"), f"Task mode set to: {self.selected_mode.upper()}")

    def update_instance_state(self, new_data: EmulatorInstance):
        old_on = self.data.is_online
        old_serial = self.data.adb_serial
        old_pid = self.data.pid

        self.data = new_data
        is_on = new_data.is_online

        # If online state, serial, and PID have not changed, skip widget reconfiguration!
        if old_on == is_on and old_serial == new_data.adb_serial and old_pid == new_data.pid:
            self.sync_worker_counts()
            return

        if is_on:
            if not self.is_bot_running:
                self.set_status("Online", "online")
                self.btn_run_bot.configure(state="normal", fg_color="#16a34a", text="▶ Run Bot")
            self.btn_online_toggle.configure(text="⏹ Shutdown", fg_color="#b91c1c", hover_color="#991b1b")
            self.info_lbl.configure(text=f"ADB: {self.data.adb_serial}  |  PID: {self.data.pid}")
            self.btn_live_view.configure(state="normal", fg_color="#0284c7")
            self.btn_quick_mirror.configure(state="normal", fg_color="#0284c7")
            self.btn_quick_show.configure(state="normal", fg_color="#1e293b")
        else:
            if self.is_bot_running:
                self.stop_bot()
            if self.preview_active:
                self._stop_live_preview()
            self.set_status("Offline", "offline")
            self.btn_run_bot.configure(state="disabled", fg_color="#334155", text="▶ Run Bot")
            self.btn_online_toggle.configure(text="🚀 Turn Online", fg_color="#059669", hover_color="#047857")
            self.info_lbl.configure(text="ADB: Disconnected  |  PID: N/A")
            self.btn_live_view.configure(state="disabled", fg_color="#334155")
            self.btn_quick_mirror.configure(state="disabled", fg_color="#334155")
            self.btn_quick_show.configure(state="disabled", fg_color="#334155")

        # Sync authoritative worker counts
        self.sync_worker_counts()

    def sync_worker_counts(self):
        """Pulls authoritative monotonic counter directly from TasklyWorker instance."""
        runner = self.dashboard.worker_threads.get(self.instance_key)
        if runner and runner.worker:
            w = runner.worker
            changed = False
            w_count = max(getattr(w, 'submitted_count', 0), getattr(w, 'approved_count', 0))
            if w_count > self.submitted_count:
                self.submitted_count = w_count
                changed = True
            if hasattr(w, 'current_balance') and w.current_balance:
                self.balance = w.current_balance
                changed = True
            if changed:
                self.update_stats()
                self.dashboard.update_global_summary()

    def _on_toggle_online(self):
        if not self.data.is_online:
            self.append_log(datetime.now().strftime("%H:%M:%S"), f"🚀 Sending launch command to {self.data.name}...")
            self.set_status("Booting...", "working")
            self.btn_online_toggle.configure(state="disabled")

            def _launch():
                ok, msg = launch_emulator(self.data.emu_type, self.data.index)
                self.append_log(datetime.now().strftime("%H:%M:%S"), f"Launch response: {msg}")
                time.sleep(4.0)
                self.after(0, self.dashboard.refresh_instances_now)
                self.after(0, lambda: self.btn_online_toggle.configure(state="normal"))

            threading.Thread(target=_launch, daemon=True).start()
        else:
            self.append_log(datetime.now().strftime("%H:%M:%S"), f"⏹ Shutting down {self.data.name}...")
            self.set_status("Closing...", "waiting")
            if self.is_bot_running:
                self.stop_bot()
            if self.preview_active:
                self._stop_live_preview()

            def _shutdown():
                ok, msg = shutdown_emulator(self.data.emu_type, self.data.index)
                self.append_log(datetime.now().strftime("%H:%M:%S"), f"Shutdown response: {msg}")
                time.sleep(2.0)
                self.after(0, self.dashboard.refresh_instances_now)

            threading.Thread(target=_shutdown, daemon=True).start()

    def _on_randomize_imei(self):
        self.append_log(datetime.now().strftime("%H:%M:%S"), f"🎲 Randomizing hardware identity & IMEI...")
        self.btn_randomize.configure(state="disabled")

        def _do_rand():
            ok, ident, msg = randomize_emulator_identity(
                self.data.emu_type,
                self.data.index,
                device_serial=self.data.adb_serial if self.data.is_online else None
            )
            time_now = datetime.now().strftime("%H:%M:%S")
            if ok:
                self.append_log(time_now, f"✨ Identity Randomized!")
                self.append_log(time_now, f"   IMEI: {ident['imei']} | Android ID: {ident['android_id']}")
                self.append_log(time_now, f"   MAC:  {ident['mac']}")
            else:
                self.append_log(time_now, f"❌ Randomize Failed: {msg}")

            self.after(0, lambda: self.btn_randomize.configure(state="normal"))

        threading.Thread(target=_do_rand, daemon=True).start()

    def _on_show_window(self):
        self.append_log(datetime.now().strftime("%H:%M:%S"), f"Bringing emulator window to foreground...")
        threading.Thread(target=lambda: show_emulator_window(self.data.emu_type, self.data.index), daemon=True).start()

    def _on_launch_mirror(self):
        if not self.data.is_online:
            return
        self.append_log(datetime.now().strftime("%H:%M:%S"), f"🔍 Launching interactive screen mirror via scrcpy...")
        threading.Thread(target=lambda: launch_scrcpy(self.data.adb_serial, title=f"Mirror - {self.data.name}"), daemon=True).start()

    def _on_toggle_live_view(self):
        if not self.preview_active:
            self._start_live_preview()
        else:
            self._stop_live_preview()

    def _start_live_preview(self):
        if not self.data.is_online:
            return
        self.preview_active = True
        self.btn_live_view.configure(text="📱 Hide View", fg_color="#f59e0b", hover_color="#d97706")
        
        # Split layout horizontally: Screen on LEFT, Log Box on RIGHT!
        self.log_box.pack_forget()
        self.preview_frame.pack(side="left", padx=(0, 6), fill="y")
        self.log_box.pack(side="left", fill="both", expand=True)

        self.append_log(datetime.now().strftime("%H:%M:%S"), "📱 Live Screen Stream started (Left-docked).")

        def _stream():
            if ":" in self.data.adb_serial:
                connect_device_adb(self.data.adb_serial)
            while self.preview_active and self.data.is_online:
                try:
                    img = capture_device_screen(self.data.adb_serial, target_size=(124, 190))
                    if img and self.preview_active:
                        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                        self.after(0, lambda im=ctk_img: self._update_preview_label(im))
                except Exception:
                    pass
                time.sleep(1.2)  # Lightweight 1.2s interval to ensure ZERO lag

        self.preview_thread = threading.Thread(target=_stream, daemon=True)
        self.preview_thread.start()

    def _stop_live_preview(self):
        self.preview_active = False
        self.btn_live_view.configure(text="📱 Live View", fg_color="#0284c7", hover_color="#0369a1")
        self.preview_frame.pack_forget()
        self.append_log(datetime.now().strftime("%H:%M:%S"), "📱 Live Screen Stream closed.")

    def _update_preview_label(self, ctk_img):
        if self.preview_active and self.preview_lbl:
            self.preview_lbl.configure(image=ctk_img, text="")

    def _on_toggle_bot(self):
        if not self.is_bot_running:
            self.start_bot()
        else:
            self.stop_bot()

    def start_bot(self):
        if not self.data.is_online:
            self.append_log(datetime.now().strftime("%H:%M:%S"), "❌ Cannot run bot: Emulator is OFFLINE! Click 'Turn Online' first.")
            return

        if not self.selected_account:
            self.append_log(datetime.now().strftime("%H:%M:%S"), "❌ No Telegram account assigned to this instance!")
            return

        chosen_bot = self.selected_bot.strip().lstrip("@")
        acc_copy = dict(self.selected_account)
        acc_copy["target_bot"] = chosen_bot
        acc_copy["target_bots"] = [chosen_bot]

        mode_str = self.selected_mode.upper()
        self.append_log(datetime.now().strftime("%H:%M:%S"), f"🚀 Starting bot on {self.data.adb_serial} targeting @{chosen_bot} (Mode: {mode_str})...")
        mode_icon = "🍪" if self.selected_mode == "cookies" else "🔑"
        self.set_status(f"Running ({mode_icon} @{chosen_bot})", "running")
        self.is_bot_running = True
        self.btn_run_bot.configure(text="⏹ Stop Bot", fg_color="#dc2626", hover_color="#b91c1c")

        self.dashboard.start_worker_for_instance(
            instance_key=self.instance_key,
            account_cfg=acc_copy,
            device_serial=self.data.adb_serial,
            target_bot=chosen_bot,
            on_state_change=self._on_worker_state_change,
            on_account_switched=self._on_worker_account_switched,
            available_bots=self.available_bots,
            task_mode=self.selected_mode
        )

    def _on_worker_account_switched(self, key: str, new_phone: str, new_bot: str):
        def _apply():
            if new_phone:
                self.acc_combo.set(str(new_phone))
                for a in self.accounts:
                    if a.get("phone") and str(a.get("phone")) in str(new_phone):
                        self.selected_account = a
                        break
            if new_bot:
                clean_b = str(new_bot).lstrip("@")
                self.selected_bot = clean_b
                self.bot_combo.set(clean_b)
                self.set_status(f"Running (@{clean_b})", "running")
            self.append_log(datetime.now().strftime("%H:%M:%S"), f"🔀 Switched to Acc: {new_phone} | Bot: @{new_bot}")
        self.after(0, _apply)

    def stop_bot(self):
        self.append_log(datetime.now().strftime("%H:%M:%S"), "⏹ Stopping bot pipeline...")
        self.set_status("Stopping...", "waiting")
        self.dashboard.stop_worker_for_instance(self.instance_key)
        self.is_bot_running = False
        self.btn_run_bot.configure(text="▶ Run Bot", fg_color="#16a34a", hover_color="#15803d")

    def _on_worker_state_change(self, key: str, status: str, color_type: str):
        self.after(0, lambda: self.set_status(status, color_type))
        if status == "Stopped":
            self.is_bot_running = False
            self.btn_run_bot.configure(text="▶ Run Bot", fg_color="#16a34a", hover_color="#15803d")
        self.sync_worker_counts()

    def append_log(self, time_str: str, text: str):
        # 1. Insert into textbox with auto-trimming to max 50 lines (Prevents lag/memory load)
        line = f"[{time_str}] {text}\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)

        try:
            line_count = int(self.log_box.index("end-1c").split(".")[0])
            if line_count > 50:
                # Trim oldest lines to keep at 45
                self.log_box.delete("1.0", f"{line_count - 45}.0")
        except Exception:
            pass

        self.log_box.see("end")
        self.log_box.configure(state="disabled")

        # 2. Extract EXACT Monotonic Counters (Fixes Double Counting!)
        m_b = re.search(r"(\$[\d\.]+|\d+\.\d{2,4})", text)
        if m_b and ("Balance:" in text or "TG-BALANCE" in text or "Your balance:" in text or "Bal:" in text):
            val = m_b.group(1)
            self.balance = val if val.startswith("$") else f"${val}"
            self.update_stats()
            self.dashboard.update_global_summary()

        # Track Meta account creation
        if any(k in text for k in ["META-SUCCESS", "Meta Account Created", "Running Instagram 2FA on emulator"]):
            if not getattr(self, '_meta_counted_in_cycle', False):
                self.meta_created_count += 1
                self._meta_counted_in_cycle = True
                self.update_stats()
                self.dashboard.update_global_summary()

        # Parse exact count from log (both Submitted and Approved are treated as the same counter)
        m_count = re.search(r"(?:Total(?:\s+(?:Submitted|Approved))?|Submitted|Approved|Sub|App):\s*(\d+)", text, re.IGNORECASE)
        if m_count:
            val = int(m_count.group(1))
            if val > self.submitted_count:
                self.submitted_count = val
                self.update_stats()
                self.dashboard.update_global_summary()

        m_cyc = re.search(r"Cycle #?(\d+)", text, re.IGNORECASE)
        if m_cyc:
            val = int(m_cyc.group(1))
            if val > self.cycle_count:
                self.cycle_count = val
                self._meta_counted_in_cycle = False
                self.update_stats()
                self.dashboard.update_global_summary()
        elif "Starting Cycle" in text:
            self.cycle_count += 1
            self._meta_counted_in_cycle = False
            self.update_stats()
            self.dashboard.update_global_summary()

    def update_stats(self):
        ratio = (self.submitted_count / self.meta_created_count * 100.0) if self.meta_created_count > 0 else 0.0
        ratio_str = f"{min(ratio, 100.0):.1f}%"
        new_text = f"💰 Bal: {self.balance}  |  🌐 Meta: {self.meta_created_count}  |  📥 Sub: {self.submitted_count}  |  📈 Success: {ratio_str}"
        if self.stats_lbl.cget("text") != new_text:
            self.stats_lbl.configure(text=new_text)

    def clear_logs(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def set_status(self, status: str, color_type: str = "running"):
        colors = {
            "online": ("● Online", "#22c55e", "#142d1e"),
            "offline": ("● Offline", "#ef4444", "#2d1519"),
            "running": (f"● {status}", "#38bdf8", "#11263c"),
            "working": (f"● {status}", "#38bdf8", "#11263c"),
            "waiting": (f"● {status}", "#fbbf24", "#302611"),
            "stopped": ("● Stopped", "#94a3b8", "#1e293b"),
            "error": ("● Error", "#f87171", "#331818"),
        }
        text, fg, bg = colors.get(color_type, (f"● {status}", "#94a3b8", "#1e293b"))
        if self.status_pill.cget("text") != text:
            self.status_pill.configure(text=text, text_color=fg, fg_color=bg)


class AutoMetaInstaGUI(ctk.CTk):
    """
    Main Application Window.
    Super-responsive, lagless dashboard showing all online & offline instances.
    """
    def __init__(self):
        super().__init__()

        self.title("🚀 AutoMetaInstaBot — Multi-Emulator Master & Controller")
        self.geometry("1240, 860")
        self.minsize(960, 680)
        self.configure(fg_color="#0e1117")

        # Load accounts & target bots from config.json
        self.cfg = autometainstabot.load_config()
        self.accounts = self.cfg.get("telegram", {}).get("accounts", [])
        
        raw_bots = self.cfg.get("telegram", {}).get("target_bot") or ["PayGoeasy_bot", "tasklyBux_bot"]
        if isinstance(raw_bots, str):
            self.available_bots = [b.strip().lstrip("@") for b in raw_bots.split(",") if b.strip()]
        elif isinstance(raw_bots, list):
            self.available_bots = [str(b).strip().lstrip("@") for b in raw_bots if str(b).strip()]
        else:
            self.available_bots = ["PayGoeasy_bot", "tasklyBux_bot"]

        # Ensure both known bots are present as options
        for b in ["PayGoeasy_bot", "tasklyBux_bot"]:
            if b not in self.available_bots:
                self.available_bots.append(b)

        # Initialize global Telegram Account Pool
        autometainstabot.TelegramAccountPool.initialize(self.accounts)

        # Active state
        self.instances: List[EmulatorInstance] = []
        self.cards: Dict[str, EmulatorCard] = {}
        self.worker_threads: Dict[str, BotRunnerThread] = {}
        self.current_filter: str = "all"
        self._is_refreshing: bool = False
        clean_bots = [b.strip().lstrip("@") for b in self.available_bots]
        self.selected_global_bot: str = clean_bots[0] if clean_bots else "PayGoeasy_bot"

        self._build_header()
        self._build_filter_bar()
        self._build_instances_container()
        self._start_log_consumer()

        # Non-blocking initial emulator scan
        self.refresh_instances_now()

        # Periodic non-blocking background heartbeat to refresh statuses
        self._schedule_heartbeat()
        self._schedule_counts_sync()

    def _build_header(self):
        header_frame = ctk.CTkFrame(self, fg_color="#161b24", corner_radius=12, border_width=1, border_color="#232b3b")
        header_frame.pack(fill="x", padx=16, pady=(14, 8))

        # Title & Summary Text
        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=16, pady=12)

        title_lbl = ctk.CTkLabel(
            title_box,
            text="🚀 AutoMetaInstaBot Controller",
            font=ctk.CTkFont(size=19, weight="bold"),
            text_color="#f8fafc"
        )
        title_lbl.pack(anchor="w")

        self.summary_lbl = ctk.CTkLabel(
            title_box,
            text="Scanning emulator instances (LDPlayer & MuMu Player)...",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8"
        )
        self.summary_lbl.pack(anchor="w", pady=(2, 0))

        self.global_stats_lbl = ctk.CTkLabel(
            title_box,
            text="🏆 Submitted: 0  |  💰 Approved: 0  |  💵 Balance: $0.00",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8"
        )
        self.global_stats_lbl.pack(anchor="w", pady=(2, 0))

        # Action Buttons
        btn_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        btn_box.pack(side="right", padx=16, pady=12)

        self.btn_start_all = ctk.CTkButton(
            btn_box,
            text="▶ Start All Bots",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=105,
            height=32,
            fg_color="#16a34a",
            hover_color="#15803d",
            command=self.start_all_online_bots
        )
        self.btn_start_all.pack(side="left", padx=3)

        self.btn_stop_all = ctk.CTkButton(
            btn_box,
            text="⏹ Stop All",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=85,
            height=32,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self.stop_all_bots
        )
        self.btn_stop_all.pack(side="left", padx=3)

        self.btn_launch_all = ctk.CTkButton(
            btn_box,
            text="🚀 Launch All",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=95,
            height=32,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self.launch_all_offline
        )
        self.btn_launch_all.pack(side="left", padx=3)

        self.btn_tile_all = ctk.CTkButton(
            btn_box,
            text="📐 Tile Windows",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=100,
            height=32,
            fg_color="#0891b2",
            hover_color="#0e7490",
            command=self.organize_windows_now
        )
        self.btn_tile_all.pack(side="left", padx=3)

        self.btn_rand_all = ctk.CTkButton(
            btn_box,
            text="🎲 Random All",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=100,
            height=32,
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            command=self.randomize_all_identities
        )
        self.btn_rand_all.pack(side="left", padx=3)

        self.btn_refresh = ctk.CTkButton(
            btn_box,
            text="🔄 Refresh",
            font=ctk.CTkFont(size=12),
            width=75,
            height=32,
            fg_color="#334155",
            hover_color="#475569",
            command=self.refresh_instances_now
        )
        self.btn_refresh.pack(side="left", padx=(3, 0))

    def _build_filter_bar(self):
        filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        filter_frame.pack(fill="x", padx=16, pady=(0, 6))

        # Tabs for filtering
        self.filter_buttons = {}
        tab_names = [
            ("all", "All Instances"),
            ("online", "🟢 Online Only"),
            ("offline", "🔴 Offline Only"),
            ("running", "⚡ Running Bots"),
            ("mumu", "MuMu Player"),
            ("ld", "LDPlayer"),
        ]

        for code, label in tab_names:
            btn = ctk.CTkButton(
                filter_frame,
                text=label,
                font=ctk.CTkFont(size=11, weight="bold" if code == "all" else "normal"),
                height=26,
                fg_color="#2563eb" if code == "all" else "#1e2430",
                hover_color="#3b82f6",
                command=lambda c=code: self.set_filter(c)
            )
            btn.pack(side="left", padx=3)
            self.filter_buttons[code] = btn

        # Global Controls Frame (Global Bot & Global Mode)
        global_ctrl_frame = ctk.CTkFrame(
            filter_frame,
            fg_color="#161b24",
            corner_radius=6,
            border_width=1,
            border_color="#232b3b"
        )
        global_ctrl_frame.pack(side="right", padx=(10, 0))

        # Global Bot Selector
        ctk.CTkLabel(
            global_ctrl_frame,
            text="🤖 Global Bot:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#a78bfa"
        ).pack(side="left", padx=(8, 4), pady=2)

        clean_bots = [b.strip().lstrip("@") for b in self.available_bots]
        default_global_bot = self.selected_global_bot or (clean_bots[0] if clean_bots else "PayGoeasy_bot")

        self.global_bot_combo = ctk.CTkOptionMenu(
            global_ctrl_frame,
            values=clean_bots,
            command=self._on_global_bot_changed,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=135,
            height=26,
            fg_color="#1e293b",
            button_color="#334155",
            button_hover_color="#475569"
        )
        self.global_bot_combo.set(default_global_bot)
        self.global_bot_combo.pack(side="left", padx=(0, 10), pady=2)

        # Global Mode Selector
        ctk.CTkLabel(
            global_ctrl_frame,
            text="🌐 Global Mode:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38bdf8"
        ).pack(side="left", padx=(4, 4), pady=2)

        cfg_mode = self.cfg.get("task_mode", "2fa").lower()
        default_global_mode = "🔑 2FA" if "2fa" in cfg_mode else "🍪 Cookies"

        self.global_mode_combo = ctk.CTkOptionMenu(
            global_ctrl_frame,
            values=["🔑 2FA", "🍪 Cookies"],
            command=self._on_global_mode_changed,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=115,
            height=26,
            fg_color="#1e293b",
            button_color="#334155",
            button_hover_color="#475569"
        )
        self.global_mode_combo.set(default_global_mode)
        self.global_mode_combo.pack(side="left", padx=(0, 4), pady=2)

    def _on_global_bot_changed(self, choice: str):
        chosen_bot = choice.strip().lstrip("@")
        self.selected_global_bot = chosen_bot

        # Update in-memory app config and persist to config.json
        if "telegram" in self.cfg:
            current_bots = self.cfg["telegram"].get("target_bot")
            if isinstance(current_bots, list):
                if chosen_bot in current_bots:
                    current_bots.remove(chosen_bot)
                current_bots.insert(0, chosen_bot)
                self.cfg["telegram"]["target_bot"] = current_bots
            elif isinstance(current_bots, str):
                self.cfg["telegram"]["target_bot"] = chosen_bot

            try:
                autometainstabot.save_config(self.cfg)
            except Exception as e:
                print(f"Error persisting global target_bot to config.json: {e}")

        # Update all active emulator cards
        for card in self.cards.values():
            card.selected_bot = chosen_bot
            card.bot_combo.set(chosen_bot)
            card.append_log(
                datetime.now().strftime("%H:%M:%S"),
                f"🤖 Global bot applied: @{chosen_bot}"
            )

    def _on_global_mode_changed(self, choice: str):
        new_mode = "2fa" if "2fa" in choice.lower() else "cookies"
        display_str = "🔑 2FA" if new_mode == "2fa" else "🍪 Cookies"

        # Update in-memory app configuration
        self.cfg["task_mode"] = new_mode

        # Persist to config.json
        try:
            autometainstabot.save_config(self.cfg)
        except Exception as e:
            print(f"Error persisting global task_mode to config.json: {e}")

        # Update all active emulator cards
        for card in self.cards.values():
            card.cfg["task_mode"] = new_mode
            card.selected_mode = new_mode
            card.mode_combo.set(display_str)
            card.append_log(
                datetime.now().strftime("%H:%M:%S"),
                f"🌐 Global mode applied: {display_str}"
            )

    def _build_instances_container(self):
        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll_frame.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        # 2-column grid
        self.scroll_frame.grid_columnconfigure(0, weight=1)
        self.scroll_frame.grid_columnconfigure(1, weight=1)

    def set_filter(self, code: str):
        self.current_filter = code
        for c, btn in self.filter_buttons.items():
            if c == code:
                btn.configure(fg_color="#2563eb", font=ctk.CTkFont(size=11, weight="bold"))
            else:
                btn.configure(fg_color="#1e2430", font=ctk.CTkFont(size=11, weight="normal"))
        self._apply_filter()

    def _apply_filter(self, force: bool = False):
        new_layout = []
        row = 0
        col = 0
        for key, card in self.cards.items():
            inst = card.data
            show = True

            if self.current_filter == "online" and not inst.is_online:
                show = False
            elif self.current_filter == "offline" and inst.is_online:
                show = False
            elif self.current_filter == "running" and not card.is_bot_running:
                show = False
            elif self.current_filter == "mumu" and inst.emu_type != "mumu":
                show = False
            elif self.current_filter == "ld" and inst.emu_type != "ld":
                show = False

            if show:
                new_layout.append((key, row, col))
                col += 1
                if col > 1:
                    col = 0
                    row += 1

        # Prevent layout recalculation if nothing changed
        if not force and hasattr(self, '_current_grid_layout') and self._current_grid_layout == new_layout:
            return

        self._current_grid_layout = new_layout
        visible_keys = {item[0] for item in new_layout}

        for key, card in self.cards.items():
            if key not in visible_keys:
                card.grid_forget()

        for key, r, c in new_layout:
            self.cards[key].grid(row=r, column=c, padx=6, pady=6, sticky="nsew")

    def refresh_instances_now(self, is_manual: bool = True):
        if self._is_refreshing:
            return
        self._is_refreshing = True
        if is_manual:
            self.summary_lbl.configure(text="Scanning emulator instances & ADB devices...")

        def _scan():
            instances = scan_all_instances()
            self.after(0, lambda: self._on_instances_scanned(instances, is_manual=is_manual))

        threading.Thread(target=_scan, daemon=True).start()

    def _on_instances_scanned(self, instances: List[EmulatorInstance], is_manual: bool = False):
        self._is_refreshing = False
        self.instances = instances

        online_count = sum(1 for i in instances if i.is_online)
        offline_count = len(instances) - online_count

        new_summary = f"Total: {len(instances)} Instances | 🟢 Online: {online_count} | 🔴 Offline: {offline_count} | Bots: {', '.join(self.available_bots)}"
        if self.summary_lbl.cget("text") != new_summary:
            self.summary_lbl.configure(text=new_summary)

        new_cards_added = False
        for inst in instances:
            key = f"{inst.emu_type.upper()}-{inst.index}"
            if key in self.cards:
                self.cards[key].update_instance_state(inst)
            else:
                card = EmulatorCard(
                    self.scroll_frame,
                    instance_data=inst,
                    accounts=self.accounts,
                    available_bots=self.available_bots,
                    dashboard=self
                )
                self.cards[key] = card
                new_cards_added = True

        self._apply_filter(force=new_cards_added)
        self.update_global_summary()

    def _schedule_heartbeat(self):
        """Heartbeat to poll background updates smoothly and without screen shaking."""
        def _check():
            self.refresh_instances_now(is_manual=False)
            self.after(10000, self._schedule_heartbeat)

        self.after(10000, _check)

    def _schedule_counts_sync(self):
        """Fast, lightweight counter synchronization (every 2.5s) without subprocess or layout lag."""
        def _sync():
            for card in self.cards.values():
                card.sync_worker_counts()
            self.after(2500, self._schedule_counts_sync)

        self.after(2500, _sync)

    def start_worker_for_instance(self, instance_key: str, account_cfg: dict, device_serial: str, target_bot: str, on_state_change, on_account_switched=None, available_bots=None, task_mode: str = "2fa"):
        # Stop any existing runner for this key
        old_runner = self.worker_threads.get(instance_key)
        if old_runner and old_runner.is_alive():
            old_runner.stop()

        runner = BotRunnerThread(
            instance_key=instance_key,
            account_cfg=account_cfg,
            device_serial=device_serial,
            target_bot=target_bot,
            on_state_change=on_state_change,
            on_account_switched=on_account_switched,
            available_bots=available_bots or self.available_bots,
            task_mode=task_mode
        )
        self.worker_threads[instance_key] = runner
        runner.start()
        self.update_global_summary()

    def stop_worker_for_instance(self, instance_key: str):
        runner = self.worker_threads.get(instance_key)
        if runner and runner.is_alive():
            runner.stop()
        self.update_global_summary()

    def start_all_online_bots(self):
        for idx, (key, card) in enumerate(self.cards.items()):
            if card.data.is_online and not card.is_bot_running:
                delay = idx * 3000
                self.after(delay, card.start_bot)

    def stop_all_bots(self):
        for card in self.cards.values():
            if card.is_bot_running:
                card.stop_bot()

    def launch_all_offline(self):
        for card in self.cards.values():
            if not card.data.is_online:
                card._on_toggle_online()

    def randomize_all_identities(self):
        for card in self.cards.values():
            card._on_randomize_imei()

    def organize_windows_now(self):
        self.summary_lbl.configure(text="Organizing and auto-tiling all emulator windows...")
        def _tile():
            ok, msg = organize_all_windows()
            time.sleep(1.0)
            self.after(0, self.refresh_instances_now)
        threading.Thread(target=_tile, daemon=True).start()

    def update_global_summary(self):
        total_meta = sum(c.meta_created_count for c in self.cards.values())
        total_sub = sum(c.submitted_count for c in self.cards.values())
        active_bots = sum(1 for c in self.cards.values() if c.is_bot_running)

        global_ratio = (total_sub / total_meta * 100.0) if total_meta > 0 else 0.0
        ratio_str = f"{min(global_ratio, 100.0):.1f}%"

        total_bal = 0.0
        for c in self.cards.values():
            try:
                b_clean = re.sub(r'[^\d\.]', '', c.balance)
                if b_clean:
                    total_bal += float(b_clean)
            except Exception:
                pass

        bal_str = f"${total_bal:.2f}"
        new_stats = f"⚡ Active: {active_bots}  |  🌐 Meta Created: {total_meta}  |  📥 Submitted: {total_sub}  |  📈 Success Rate: {ratio_str}  |  💵 Bal: {bal_str}"
        if self.global_stats_lbl.cget("text") != new_stats:
            self.global_stats_lbl.configure(text=new_stats)

    def _start_log_consumer(self):
        """Consumes queue messages and routes to appropriate cards."""
        def poll_queue():
            while not GUI_LOG_QUEUE.empty():
                try:
                    item = GUI_LOG_QUEUE.get_nowait()
                    inst_name = item.get("instance", "MAIN").upper()
                    time_str = item.get("time", "")
                    text = item.get("text", "")

                    matched = False
                    for key, card in self.cards.items():
                        if key in inst_name or inst_name in key:
                            card.append_log(time_str, text)
                            matched = True
                            break

                    if not matched:
                        for card in self.cards.values():
                            if card.is_bot_running:
                                card.append_log(time_str, text)
                except Exception:
                    pass

            self.after(150, poll_queue)

        self.after(150, poll_queue)


def main():
    app = AutoMetaInstaGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
