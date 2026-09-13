"""
==============================================================================
🔥 INSTA AUTOMATION MULTI-INSTANCE GUI CONTROLLER & DASHBOARD 🔥
==============================================================================
- Instant Startup (<0.5s GUI Launch)
- Accurate Emulator Detection (Filters out physical USB phones)
- Independent Per-Instance Controls: Start, Stop, Restart
- Global Master Controls: Start All, Stop All, Restart All, Refresh
- Simplified, Human-Friendly Real-Time Log Boxes per Instance
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
from typing import Dict, Optional, List

import customtkinter as ctk

# Core imports
import importlib.util
OPTIMIZE_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "1tasklytometatoinsta_multi_optimize.py")
spec = importlib.util.spec_from_file_location("optimize_runner", OPTIMIZE_SCRIPT_PATH)
optimize_runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(optimize_runner)

# Appearance
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Global Event Queue for Thread-Safe GUI Updates
GUI_LOG_QUEUE = queue.Queue()


def simplify_log_message(tag: str, raw_msg: str) -> Optional[str]:
    """Transforms verbose logs into clean, readable status updates"""
    tag_clean = tag.upper().strip()
    msg_clean = raw_msg.strip()

    # Filter out noisy internal debug dumps
    if any(k in tag_clean for k in ["XML", "HIERARCHY", "DUMP", "PAGE-DUMP", "COORD", "REL-COORD", "DEV-SCAN"]):
        return None
    if any(k in msg_clean.lower() for k in ["xml hierarchy", "empty ui dump", "getting screen hierarchy"]):
        return None

    # Highlight key milestones
    if "CYCLE-START" in tag_clean or "Starting Taskly" in msg_clean:
        m = re.search(r"Cycle #(\d+)", msg_clean)
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
        m_s = re.search(r"Total Submitted:\s*(\d+)", msg_clean)
        s_str = f" [Total: {m_s.group(1)}]" if m_s else ""
        return f"📥 ✅ Report Received & Submitted to Bot!{s_str}"

    elif "TG-BALANCE" in tag_clean or "your balance:" in msg_clean.lower() or "current balance:" in msg_clean.lower() or ("balance:" in msg_clean.lower() and "$" in msg_clean):
        m_b = re.search(r"(\$[\d\.]+|\d+\.\d{2,4})", msg_clean)
        b_str = m_b.group(1) if m_b else msg_clean
        if not b_str.startswith("$") and re.match(r"^\d", b_str):
            b_str = f"${b_str}"
        return f"💰 Current Balance: {b_str}"

    elif "TG-APPROVED" in tag_clean or "report approved" in msg_clean.lower() or "+$0.03" in msg_clean:
        m_a = re.search(r"Total Approved:\s*(\d+)", msg_clean)
        a_str = f" [Total: {m_a.group(1)}]" if m_a else ""
        return f"💰 ✅ Report Approved (+$0.03)!{a_str}"

    elif "Report confirmed and accepted" in msg_clean:
        return "✅ Bot confirmed task report & credited reward!"

    elif "PHASE-1" in tag_clean:
        return "⚡ Starting fast pure-request signup..."

    elif "REQ-OTP-CONFIRM" in tag_clean or "OTP-CONFIRM" in tag_clean:
        return "📨 Submitting Meta OTP confirmation..."

    elif "PHASE-2" in tag_clean:
        return "🌐 Opening browser with session cookies..."

    elif "CHECKPOINT-FLOW" in tag_clean or "CHECKPOINT-NAV" in tag_clean:
        return "🛡️ Handling Meta Checkpoint & Human Verification..."

    elif "2CAPTCHA" in tag_clean:
        return f"🤖 2Captcha Solver: {msg_clean}"

    elif "IMG-UPLOAD" in tag_clean:
        return f"📸 Selfie Upload: {msg_clean}"

    elif "META-SUCCESS" in tag_clean:
        return "🎉 Meta AI Account Created & Verified!"

    elif "SUCCESS" in tag_clean:
        return f"✅ {msg_clean}"

    elif "ERR" in tag_clean or "FAILED" in tag_clean:
        return f"❌ {msg_clean}"

    elif "WARN" in tag_clean or "CYCLE-ABORT" in tag_clean:
        return f"⚠️ {msg_clean}"

    if len(msg_clean) < 140 and not ("{" in msg_clean and "}" in msg_clean):
        return msg_clean

    return None


def custom_gui_log_callback(tag, message, instance_name=""):
    """Intercepts optimize script logs and sends clean lines to GUI log queue"""
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


# Bind logging callback to runner module
optimize_runner.set_log_callback(custom_gui_log_callback)


class InstanceCard(ctk.CTkFrame):
    """UI Card for controlling an individual instance and showing its own log box"""
    def __init__(self, master, instance_id: int, account_info: dict, device_serial: str, on_start, on_stop, on_restart, dashboard=None):
        super().__init__(master, corner_radius=12, fg_color="#1a1d24", border_width=1, border_color="#2a303c")
        
        self.instance_id = instance_id
        self.instance_name = f"Inst-{instance_id}"
        self.account_info = account_info
        self.device_serial = device_serial
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_restart = on_restart
        self.dashboard = dashboard
        
        self.status = "Stopped"
        self.submitted_count = 0
        self.approved_count = 0
        self.cycle_count = 0
        self.balance = "$0.00"
        
        self._build_ui()

    def _build_ui(self):
        # Header Row
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=14, pady=(12, 4))

        title_label = ctk.CTkLabel(
            header_frame,
            text=f"⚡ INSTANCE {self.instance_id}",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#60a5fa"
        )
        title_label.pack(side="left")

        phone = self.account_info.get("phone", "Unknown")
        phone_badge = ctk.CTkLabel(
            header_frame,
            text=f"📱 {phone}",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8"
        )
        phone_badge.pack(side="left", padx=10)

        self.status_pill = ctk.CTkLabel(
            header_frame,
            text="● Stopped",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#ef4444",
            fg_color="#2d1519",
            corner_radius=8,
            padx=10,
            pady=3
        )
        self.status_pill.pack(side="right")

        # Info & Stats Row
        info_frame = ctk.CTkFrame(self, fg_color="#12151c", corner_radius=8)
        info_frame.pack(fill="x", padx=14, pady=4)

        raw_bots = self.account_info.get("target_bots") or self.account_info.get("target_bot") or ["tasklyBux_bot"]
        bot_name = raw_bots[0] if isinstance(raw_bots, list) else str(raw_bots)
        
        self.info_lbl = ctk.CTkLabel(
            info_frame,
            text=f"ADB: {self.device_serial}  |  Bot: @{bot_name}",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8"
        )
        self.info_lbl.pack(side="left", padx=10, pady=4)

        self.stats_lbl = ctk.CTkLabel(
            info_frame,
            text="💰 Bal: $0.00  |  Cycles: 0  |  📥 Sub: 0  |  💰 App: 0",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38bdf8"
        )
        self.stats_lbl.pack(side="right", padx=10, pady=4)

        # Dedicated Monospace Log Box
        self.log_box = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#0d1117",
            text_color="#e2e8f0",
            corner_radius=8,
            border_width=1,
            border_color="#1e293b",
            wrap="word",
            height=160
        )
        self.log_box.pack(fill="both", expand=True, padx=14, pady=8)
        self.log_box.configure(state="disabled")

        # Action Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=14, pady=(4, 12))

        self.btn_start = ctk.CTkButton(
            btn_frame,
            text="▶ Start",
            width=80,
            height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#16a34a",
            hover_color="#15803d",
            command=lambda: self.on_start(self.instance_id)
        )
        self.btn_start.pack(side="left", padx=(0, 6))

        self.btn_stop = ctk.CTkButton(
            btn_frame,
            text="⏹ Stop",
            width=80,
            height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=lambda: self.on_stop(self.instance_id)
        )
        self.btn_stop.pack(side="left", padx=6)

        self.btn_restart = ctk.CTkButton(
            btn_frame,
            text="🔄 Restart",
            width=85,
            height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=lambda: self.on_restart(self.instance_id)
        )
        self.btn_restart.pack(side="left", padx=6)

        self.btn_clear = ctk.CTkButton(
            btn_frame,
            text="🗑 Clear",
            width=70,
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color="#334155",
            hover_color="#475569",
            command=self.clear_logs
        )
        self.btn_clear.pack(side="right")

    def append_log(self, time_str: str, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{time_str}] {text}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

        if "Current Balance:" in text or "TG-BALANCE" in text or "Your balance:" in text or "Account Balance:" in text or "Bal:" in text:
            m_b = re.search(r"(\$[\d\.]+|\d+\.\d{2,4})", text)
            if m_b:
                val = m_b.group(1)
                self.balance = val if val.startswith("$") else f"${val}"
                self.update_stats()
                if self.dashboard and hasattr(self.dashboard, "update_global_summary"):
                    self.dashboard.update_global_summary()

        if "Report Received & Submitted" in text or "SUBMITTED" in text:
            self.submitted_count += 1
            self.update_stats()
            if self.dashboard and hasattr(self.dashboard, "update_global_summary"):
                self.dashboard.update_global_summary()
        elif "Report Approved" in text or "APPROVED" in text:
            self.approved_count += 1
            self.update_stats()
            if self.dashboard and hasattr(self.dashboard, "update_global_summary"):
                self.dashboard.update_global_summary()
        elif "Starting Cycle" in text:
            self.cycle_count += 1
            self.update_stats()
            if self.dashboard and hasattr(self.dashboard, "update_global_summary"):
                self.dashboard.update_global_summary()

    def clear_logs(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def update_stats(self):
        self.stats_lbl.configure(text=f"💰 Bal: {self.balance}  |  Cycles: {self.cycle_count}  |  📥 Sub: {self.submitted_count}  |  💰 App: {self.approved_count}")

    def update_device(self, new_device: str):
        self.device_serial = new_device
        raw_bots = self.account_info.get("target_bots") or self.account_info.get("target_bot") or ["tasklyBux_bot"]
        bot_name = raw_bots[0] if isinstance(raw_bots, list) else str(raw_bots)
        self.info_lbl.configure(text=f"ADB: {self.device_serial}  |  Bot: @{bot_name}")

    def set_status(self, status: str, color_type: str = "running"):
        self.status = status
        colors = {
            "running": ("● Running", "#22c55e", "#142d1e"),
            "working": (f"● {status}", "#38bdf8", "#11263c"),
            "waiting": (f"● {status}", "#fbbf24", "#302611"),
            "stopped": ("● Stopped", "#ef4444", "#2d1519"),
            "success": ("● Success", "#22c55e", "#142d1e"),
            "error": ("● Error", "#f87171", "#331818"),
        }
        text, fg, bg = colors.get(color_type, (f"● {status}", "#94a3b8", "#1e293b"))
        self.status_pill.configure(text=text, text_color=fg, fg_color=bg)


class WorkerRunnerThread(threading.Thread):
    """Dedicated Thread per worker instance with isolated event loop & Telethon client"""
    def __init__(self, worker_id: int, account_info: dict, device_serial: str, on_state_change=None):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self.instance_name = f"Inst-{worker_id}"
        self.account_info = account_info
        self.device_serial = device_serial
        self.on_state_change = on_state_change
        
        self.worker: Optional[optimize_runner.TasklyWorker] = None
        self.tg_client = None
        self.loop = None
        self._is_stopped = False

    def run(self):
        self._is_stopped = False
        if self.on_state_change:
            self.on_state_change(self.worker_id, "Running", "running")
            
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        try:
            self.loop.run_until_complete(self._async_run())
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        except Exception as e:
            if not self._is_stopped:
                optimize_runner.log("WORKER-ERR", f"Worker error: {e}", instance_name=self.instance_name)
                if self.on_state_change:
                    self.on_state_change(self.worker_id, "Error", "error")
        finally:
            # Gracefully cancel and wait for any remaining tasks on this loop
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

            # Force release SQLite session lock if still held
            if self.tg_client:
                try:
                    if hasattr(self.tg_client, 'session') and hasattr(self.tg_client.session, 'close'):
                        self.tg_client.session.close()
                except Exception:
                    pass
                self.tg_client = None

            self._is_stopped = True
            if self.on_state_change:
                self.on_state_change(self.worker_id, "Stopped", "stopped")

    async def _async_run(self):
        from playwright.async_api import async_playwright
        from telethon import TelegramClient

        acc = self.account_info
        clean_phone = re.sub(r'[^0-9]', '', str(acc['phone']))
        sess_name = acc.get('session_name') or f"taskly_session_{clean_phone}"

        # Connect isolated Telethon Client on current loop
        optimize_runner.log("INIT", f"Connecting Telegram Userbot for {acc['phone']}...", instance_name=self.instance_name)
        self.tg_client = TelegramClient(sess_name, acc['api_id'], acc['api_hash'], loop=self.loop)
        
        tg_listener = None
        try:
            await self.tg_client.start(phone=acc['phone'])
            optimize_runner.log("INIT", f"Telegram logged in successfully for {acc['phone']}", instance_name=self.instance_name)

            raw_bots = acc.get("target_bots") or acc.get("target_bot") or optimize_runner.TARGET_BOTS
            norm_bots = optimize_runner.normalize_target_bots(raw_bots)

            self.worker = optimize_runner.TasklyWorker(
                acc,
                self.device_serial,
                self.instance_name,
                target_bot=norm_bots[0],
                client=self.tg_client
            )

            # Start Telethon update receiver background task
            tg_listener = asyncio.create_task(self.tg_client.run_until_disconnected())

            async with async_playwright() as pw:
                await self.worker.run(pw)
        finally:
            if tg_listener and not tg_listener.done():
                tg_listener.cancel()
                try:
                    await tg_listener
                except (asyncio.CancelledError, Exception):
                    pass
            if self.tg_client:
                try:
                    if self.tg_client.is_connected():
                        await self.tg_client.disconnect()
                except Exception:
                    pass
                try:
                    if hasattr(self.tg_client, 'session') and hasattr(self.tg_client.session, 'close'):
                        self.tg_client.session.close()
                except Exception:
                    pass

    def stop(self):
        """Cleanly stops worker, browser context, and android app"""
        self._is_stopped = True
        if self.worker:
            self.worker.stop()
        if self.loop and self.loop.is_running():
            def _cancel_tasks():
                for task in asyncio.all_tasks(self.loop):
                    task.cancel()
            self.loop.call_soon_threadsafe(_cancel_tasks)


class MultiInstanceDashboard(ctk.CTk):
    """Main Application Window"""
    def __init__(self):
        super().__init__()

        self.title("⚡ Insta 2FA Multi-Instance Controller & Dashboard")
        self.geometry("1100, 780")
        self.minsize(800, 600)
        self.configure(fg_color="#0f1115")

        self.cfg = optimize_runner.load_config()
        self.accounts = self.cfg.get("telegram", {}).get("accounts", [])
        self.devices: List[str] = []

        self.instance_cards: Dict[int, InstanceCard] = {}
        self.worker_threads: Dict[int, WorkerRunnerThread] = {}

        self._build_header()
        self._build_instances_grid()
        self._start_log_consumer()

        # Fast non-blocking background device discovery
        threading.Thread(target=self._async_initial_device_scan, daemon=True).start()

    def _build_header(self):
        header_frame = ctk.CTkFrame(self, fg_color="#161922", corner_radius=14, border_width=1, border_color="#232936")
        header_frame.pack(fill="x", padx=16, pady=(16, 10))

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=16, pady=12)

        main_title = ctk.CTkLabel(
            title_box,
            text="🚀 Insta 2FA Multi-Instance Controller",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#f8fafc"
        )
        main_title.pack(anchor="w")

        self.status_summary_lbl = ctk.CTkLabel(
            title_box,
            text=f"Scanning ADB Emulators...  |  {len(self.accounts)} Telegram Account(s)",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8"
        )
        self.status_summary_lbl.pack(anchor="w")

        self.global_stats_lbl = ctk.CTkLabel(
            title_box,
            text="🏆 Total Success: 0  |  No instances completed yet",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8"
        )
        self.global_stats_lbl.pack(anchor="w", pady=(2, 0))

        # Action Buttons
        actions_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        actions_box.pack(side="right", padx=16, pady=12)

        self.btn_start_all = ctk.CTkButton(
            actions_box,
            text="▶ Start All",
            width=100,
            height=34,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#16a34a",
            hover_color="#15803d",
            command=self.start_all_instances
        )
        self.btn_start_all.pack(side="left", padx=6)

        self.btn_stop_all = ctk.CTkButton(
            actions_box,
            text="⏹ Stop All",
            width=100,
            height=34,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self.stop_all_instances
        )
        self.btn_stop_all.pack(side="left", padx=6)

        self.btn_restart_all = ctk.CTkButton(
            actions_box,
            text="🔄 Restart All",
            width=105,
            height=34,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self.restart_all_instances
        )
        self.btn_restart_all.pack(side="left", padx=6)

        self.btn_refresh = ctk.CTkButton(
            actions_box,
            text="🔄 Refresh",
            width=90,
            height=34,
            font=ctk.CTkFont(size=12),
            fg_color="#334155",
            hover_color="#475569",
            command=self.refresh_devices_and_config
        )
        self.btn_refresh.pack(side="left", padx=(6, 0))

    def _build_instances_grid(self):
        self.scroll_container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll_container.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        num_instances = len(self.accounts) if self.accounts else 1
        self.scroll_container.grid_columnconfigure(0, weight=1)
        self.scroll_container.grid_columnconfigure(1, weight=1)

        for i in range(num_instances):
            inst_id = i + 1
            acc = self.accounts[i] if i < len(self.accounts) else {"phone": "Not Configured"}
            dev = self.devices[i] if i < len(self.devices) else "Detecting..."

            card = InstanceCard(
                self.scroll_container,
                instance_id=inst_id,
                account_info=acc,
                device_serial=dev,
                on_start=self.start_instance,
                on_stop=self.stop_instance,
                on_restart=self.restart_instance,
                dashboard=self
            )
            row = i // 2
            col = i % 2
            card.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
            self.instance_cards[inst_id] = card

    def _async_initial_device_scan(self):
        """Fast background discovery of active emulators"""
        devs = optimize_runner.get_unique_devices(emulators_only=True)
        self.after(0, lambda: self._apply_discovered_devices(devs))

    def _apply_discovered_devices(self, devs: List[str]):
        self.devices = devs
        self.status_summary_lbl.configure(
            text=f"Detected {len(self.devices)} Active Emulator(s)  |  {len(self.accounts)} Telegram Account(s)"
        )
        for i, (inst_id, card) in enumerate(self.instance_cards.items()):
            assigned_dev = self.devices[i] if i < len(self.devices) else "No Device Detected"
            card.update_device(assigned_dev)
        self.update_global_summary()

    def update_global_summary(self):
        """Updates top header stats bar with total submitted/approved and per-instance breakdown"""
        total_sub = sum(c.submitted_count for c in self.instance_cards.values())
        total_app = sum(c.approved_count for c in self.instance_cards.values())
        total_cycles = sum(c.cycle_count for c in self.instance_cards.values())
        
        total_bal = 0.0
        for c in self.instance_cards.values():
            try:
                clean_b = re.sub(r'[^\d\.]', '', getattr(c, 'balance', '0.00'))
                if clean_b:
                    total_bal += float(clean_b)
            except Exception:
                pass
        bal_str = f"  |  💰 Total Bal: ${total_bal:.4f}" if total_bal > 0 else ""

        if self.instance_cards:
            breakdown = "  |  ".join([f"Inst-{k}: {getattr(c, 'balance', '$0.00')} (App: {c.approved_count})" for k, c in self.instance_cards.items()])
            self.global_stats_lbl.configure(
                text=f"🏆 Submitted: {total_sub}  |  💰 Approved: {total_app}{bal_str}  |  {breakdown}"
            )
        else:
            self.global_stats_lbl.configure(
                text=f"🏆 Submitted: {total_sub}  |  💰 Approved: {total_app}  |  Cycles: {total_cycles}"
            )

    def _start_log_consumer(self):
        """Processes incoming log messages and routes them to appropriate instance cards"""
        def poll_queue():
            while not GUI_LOG_QUEUE.empty():
                item = GUI_LOG_QUEUE.get_nowait()
                inst_name = item.get("instance", "MAIN")
                time_str = item.get("time", "")
                text = item.get("text", "")
                
                m = re.search(r"Inst-(\d+)", inst_name, re.IGNORECASE)
                if m:
                    inst_id = int(m.group(1))
                    card = self.instance_cards.get(inst_id)
                    if card:
                        card.append_log(time_str, text)
                        if "Starting" in text or "Registering" in text or "Opening browser" in text:
                            card.set_status("Working", "working")
                        elif "Waiting for" in text or "Fetching" in text or "Polling" in text or "Checking" in text:
                            card.set_status("Waiting", "waiting")
                        elif "CYCLE" in text and ("COMPLETE" in text or "SUCCESS" in text):
                            card.set_status("Success", "success")
                        elif "❌" in text or "Error" in text or "FAILED" in text:
                            card.set_status("Error", "error")

            self.after(150, poll_queue)

        self.after(150, poll_queue)

    def on_instance_state_changed(self, inst_id: int, status: str, color_type: str):
        card = self.instance_cards.get(inst_id)
        if card:
            self.after(0, lambda: card.set_status(status, color_type))

    def start_instance(self, inst_id: int):
        card = self.instance_cards.get(inst_id)
        if not card:
            return

        runner = self.worker_threads.get(inst_id)
        if runner and runner.is_alive() and not runner._is_stopped:
            card.append_log(datetime.now().strftime("%H:%M:%S"), "⚠️ Instance is already running!")
            return

        if card.device_serial in ["No Device Detected", "Detecting..."]:
            card.append_log(datetime.now().strftime("%H:%M:%S"), "❌ Cannot start: No active ADB emulator assigned! Please start emulator and click Refresh.")
            return

        card.set_status("Starting...", "working")

        def _do_start():
            old_runner = self.worker_threads.get(inst_id)
            if old_runner and old_runner.is_alive():
                old_runner.stop()
                old_runner.join(timeout=3.5)

            self.after(0, lambda: self._launch_worker(inst_id, card))

        threading.Thread(target=_do_start, daemon=True).start()

    def _launch_worker(self, inst_id: int, card: InstanceCard):
        card.append_log(datetime.now().strftime("%H:%M:%S"), f"🚀 Starting Instance on {card.device_serial}...")
        card.set_status("Starting...", "working")

        new_runner = WorkerRunnerThread(
            worker_id=inst_id,
            account_info=card.account_info,
            device_serial=card.device_serial,
            on_state_change=self.on_instance_state_changed
        )
        self.worker_threads[inst_id] = new_runner
        new_runner.start()

    def stop_instance(self, inst_id: int):
        card = self.instance_cards.get(inst_id)
        if not card:
            return

        runner = self.worker_threads.get(inst_id)
        if runner and runner.is_alive():
            card.append_log(datetime.now().strftime("%H:%M:%S"), "⏹ Stopping instance worker...")
            card.set_status("Stopping...", "waiting")
            runner.stop()
        else:
            card.set_status("Stopped", "stopped")

    def restart_instance(self, inst_id: int):
        card = self.instance_cards.get(inst_id)
        if not card:
            return
        card.append_log(datetime.now().strftime("%H:%M:%S"), "🔄 Restarting instance...")
        self.stop_instance(inst_id)
        
        def _do_restart():
            runner = self.worker_threads.get(inst_id)
            if runner and runner.is_alive():
                runner.join(timeout=3.5)
            self.after(0, lambda: self.start_instance(inst_id))

        threading.Thread(target=_do_restart, daemon=True).start()

    def start_all_instances(self):
        for idx, inst_id in enumerate(self.instance_cards.keys()):
            delay_ms = idx * 2500  # Stagger worker starts by 2.5s for clean parallel initialization
            self.after(delay_ms, lambda i=inst_id: self.start_instance(i))

    def stop_all_instances(self):
        for inst_id in self.instance_cards.keys():
            self.stop_instance(inst_id)

    def restart_all_instances(self):
        self.stop_all_instances()
        self.after(2000, self.start_all_instances)

    def refresh_devices_and_config(self):
        """Refreshes devices and config dynamically"""
        self.cfg = optimize_runner.load_config()
        self.accounts = self.cfg.get("telegram", {}).get("accounts", [])
        self.status_summary_lbl.configure(text="Refreshing emulators & config...")
        threading.Thread(target=self._async_initial_device_scan, daemon=True).start()


def main():
    app = MultiInstanceDashboard()
    app.mainloop()


if __name__ == "__main__":
    main()
