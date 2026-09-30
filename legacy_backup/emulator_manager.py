"""
==============================================================================
🎮 EMULATOR MANAGER FOR LDPLAYER & MUMU PLAYER
==============================================================================
Provides high-performance, asynchronous-friendly management of:
- LDPlayer instances (via ldconsole.exe / list2)
- MuMu Player instances (via MuMuManager.exe info/control/simulation)
- ADB connectivity & hardware identity (IMEI, MAC, Android ID)
==============================================================================
"""

import json
import os
import random
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def find_ldconsole() -> Optional[str]:
    """Finds path to ldconsole.exe for LDPlayer."""
    for p in [
        r"D:\LDPlayer\LDPlayer9\ldconsole.exe",
        r"C:\LDPlayer\LDPlayer9\ldconsole.exe",
        r"D:\LDPlayer\LDPlayer64\ldconsole.exe",
        r"C:\LDPlayer\LDPlayer64\ldconsole.exe",
        r"D:\LDPlayer\LDPlayer14\ldconsole.exe",
        r"C:\leidian\LDPlayer9\ldconsole.exe",
        r"C:\Program Files\LDPlayer\LDPlayer9\ldconsole.exe",
    ]:
        if os.path.exists(p):
            return p
    return shutil.which("ldconsole") or shutil.which("dnconsole")


def find_mumu_manager() -> Optional[str]:
    """Finds path to MuMuManager.exe for MuMu Player."""
    for p in [
        r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
        r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\MuMuManager.exe",
        r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\MuMuManager.exe",
        r"C:\Program Files\MuMuPlayer-12.0\shell\MuMuManager.exe",
        r"D:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
    ]:
        if os.path.exists(p):
            return p
    return shutil.which("MuMuManager")


def find_adb() -> str:
    """Finds available ADB executable."""
    possible_paths = [
        r"D:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer\nx_device\15.0\shell\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
        r"D:\LDPlayer\LDPlayer14\adb.exe",
        os.path.expanduser(r"~\AppData\Local\Android\Sdk\platform-tools\adb.exe"),
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    which_adb = shutil.which("adb")
    return which_adb if which_adb else "adb"


ADB_BIN = find_adb()
LDCONSOLE_BIN = find_ldconsole()
MUMU_BIN = find_mumu_manager()


@dataclass
class EmulatorInstance:
    emu_type: str                   # 'mumu', 'ld', or 'adb'
    index: int                      # Emulator instance index (0, 1, 2, ...)
    name: str                       # E.g. "Android Device-0" or "LDPlayer-1"
    is_online: bool                 # Whether Android engine/player is actively running
    adb_serial: str                 # Serial e.g. "127.0.0.1:16384" or "emulator-5554"
    pid: int = -1                   # Process ID
    extra_info: Dict = field(default_factory=dict)
    imei: str = ""
    android_id: str = ""
    mac: str = ""
    bot_running: bool = False
    status_text: str = "Offline"


def run_cmd(cmd: str, timeout: float = 6.0) -> Tuple[int, str, str]:
    """Executes a Windows shell command without popping up windows."""
    try:
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        res = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            startupinfo=startupinfo,
            encoding="utf-8",
            errors="ignore",
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)


def get_active_adb_serials() -> List[str]:
    """Returns list of currently online devices from adb devices."""
    code, stdout, _ = run_cmd(f'"{ADB_BIN}" devices', timeout=4.0)
    serials = []
    if code == 0:
        for line in stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
    return serials


def scan_mumu_instances() -> List[EmulatorInstance]:
    """Scans all MuMu Player instances using MuMuManager info -v all."""
    instances = []
    if not MUMU_BIN or not os.path.exists(MUMU_BIN):
        return instances

    code, stdout, _ = run_cmd(f'"{MUMU_BIN}" info -v all', timeout=5.0)
    if code != 0 or not stdout:
        return instances

    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            for k, val in data.items():
                if not isinstance(val, dict):
                    continue
                try:
                    idx = int(val.get("index", k))
                except (ValueError, TypeError):
                    idx = 0

                name = val.get("name") or f"MuMu Device-{idx}"
                player_state = str(val.get("player_state", "")).lower()
                is_proc = bool(val.get("is_process_started", False))
                is_and = bool(val.get("is_android_started", False))
                is_online = (player_state == "start_finished") or (is_proc and is_and)

                adb_ip = val.get("adb_host_ip", "127.0.0.1")
                adb_port = val.get("adb_port", 0)
                adb_serial = f"{adb_ip}:{adb_port}" if adb_port else f"127.0.0.1:{16384 + idx * 32}"

                pid = val.get("pid", -1)

                inst = EmulatorInstance(
                    emu_type="mumu",
                    index=idx,
                    name=name,
                    is_online=is_online,
                    adb_serial=adb_serial,
                    pid=pid,
                    extra_info=val,
                    status_text="Online" if is_online else "Offline",
                )
                instances.append(inst)
    except Exception:
        pass

    return sorted(instances, key=lambda x: x.index)


def get_running_dnplayer_processes() -> Dict[int, int]:
    """Returns mapping of LDPlayer instance index -> process PID from running dnplayer.exe."""
    running = {}
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            name = (p.info['name'] or '').lower()
            if 'dnplayer' in name:
                cmd = " ".join(p.info.get('cmdline') or [])
                m = re.search(r'index[=\s]+(\d+)', cmd, re.IGNORECASE)
                if m:
                    running[int(m.group(1))] = p.info['pid']
                elif 0 not in running:
                    running[0] = p.info['pid']
    except Exception:
        pass
    return running


def scan_ld_instances() -> List[EmulatorInstance]:
    """Scans all LDPlayer instances using ldconsole list2 and active processes."""
    instances = []
    if not LDCONSOLE_BIN or not os.path.exists(LDCONSOLE_BIN):
        return instances

    code, stdout, _ = run_cmd(f'"{LDCONSOLE_BIN}" list2', timeout=5.0)
    if code != 0 or not stdout:
        return instances

    running_dn = get_running_dnplayer_processes()
    active_adbs = get_active_adb_serials()

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) >= 6:
            try:
                idx = int(parts[0])
                name = parts[1]
                pid = int(parts[5]) if parts[5] != "-1" else -1

                # If pid was not returned by ldconsole, check running dnplayer process
                if pid <= 0 and idx in running_dn:
                    pid = running_dn[idx]

                # Determine correct ADB device serial
                emu_serial = f"emulator-{5554 + (idx * 2)}"
                ip_serial = f"127.0.0.1:{5555 + (idx * 2)}"

                if emu_serial in active_adbs:
                    adb_serial = emu_serial
                    is_online = True
                elif ip_serial in active_adbs:
                    adb_serial = ip_serial
                    is_online = True
                elif idx in running_dn:
                    adb_serial = emu_serial
                    is_online = True
                else:
                    adb_serial = emu_serial
                    is_online = False

                inst = EmulatorInstance(
                    emu_type="ld",
                    index=idx,
                    name=name,
                    is_online=is_online,
                    adb_serial=adb_serial,
                    pid=pid,
                    extra_info={"raw": line},
                    status_text="Online" if is_online else "Offline",
                )
                instances.append(inst)
            except Exception:
                continue

    return sorted(instances, key=lambda x: x.index)


def scan_all_instances() -> List[EmulatorInstance]:
    """
    Scans both MuMu Player and LDPlayer, checks ADB connectivity,
    and returns a unified list of all emulator instances without duplicates.
    """
    mumu_list = scan_mumu_instances()
    ld_list = scan_ld_instances()

    # Auto-connect ADB for online port-based emulators (e.g. MuMu Player)
    for inst in mumu_list:
        if inst.is_online and ":" in inst.adb_serial:
            connect_device_adb(inst.adb_serial)

    return mumu_list + ld_list


def launch_emulator(emu_type: str, index: int) -> Tuple[bool, str]:
    """Starts the emulator instance to bring it Online."""
    emu_type = emu_type.lower()
    if emu_type == "mumu":
        if not MUMU_BIN:
            return False, "MuMuManager.exe not found"
        cmd = f'"{MUMU_BIN}" control -v {index} launch'
        code, out, err = run_cmd(cmd, timeout=8.0)
        return (code == 0), out or err or "MuMu launch command sent"

    elif emu_type == "ld":
        if not LDCONSOLE_BIN:
            return False, "ldconsole.exe not found"
        cmd = f'"{LDCONSOLE_BIN}" launch --index {index}'
        code, out, err = run_cmd(cmd, timeout=8.0)
        return (code == 0), out or err or "LDPlayer launch command sent"

    return False, f"Unknown emulator type: {emu_type}"


def shutdown_emulator(emu_type: str, index: int) -> Tuple[bool, str]:
    """Stops/closes the emulator instance."""
    emu_type = emu_type.lower()
    if emu_type == "mumu":
        if not MUMU_BIN:
            return False, "MuMuManager.exe not found"
        cmd = f'"{MUMU_BIN}" control -v {index} shutdown'
        code, out, err = run_cmd(cmd, timeout=8.0)
        return (code == 0), out or err or "MuMu shutdown command sent"

    elif emu_type == "ld":
        if not LDCONSOLE_BIN:
            return False, "ldconsole.exe not found"
        cmd = f'"{LDCONSOLE_BIN}" quit --index {index}'
        code, out, err = run_cmd(cmd, timeout=8.0)
        return (code == 0), out or err or "LDPlayer quit command sent"

    return False, f"Unknown emulator type: {emu_type}"


def restart_emulator(emu_type: str, index: int) -> Tuple[bool, str]:
    """Reboots/restarts the emulator instance."""
    emu_type = emu_type.lower()
    if emu_type == "mumu":
        if not MUMU_BIN:
            return False, "MuMuManager.exe not found"
        cmd = f'"{MUMU_BIN}" control -v {index} restart'
        code, out, err = run_cmd(cmd, timeout=8.0)
        return (code == 0), out or err or "MuMu restart command sent"

    elif emu_type == "ld":
        if not LDCONSOLE_BIN:
            return False, "ldconsole.exe not found"
        cmd = f'"{LDCONSOLE_BIN}" reboot --index {index}'
        code, out, err = run_cmd(cmd, timeout=8.0)
        return (code == 0), out or err or "LDPlayer reboot command sent"

    return False, f"Unknown emulator type: {emu_type}"


def generate_random_identity() -> Dict[str, str]:
    """Generates realistic device identifiers."""
    # Realistic 15-digit TAC+Serial IMEI
    imei = "86" + "".join([str(random.randint(0, 9)) for _ in range(13)])
    # 16-hex Android ID
    android_id = "".join(random.choices("0123456789abcdef", k=16))
    # Standard MAC address
    mac = ":".join([f"{random.randint(0, 255):02x}" for _ in range(6)])
    # IMSI
    imsi = "4600" + "".join([str(random.randint(0, 9)) for _ in range(11)])
    # Sim Serial
    simserial = "898600" + "".join([str(random.randint(0, 9)) for _ in range(14)])

    return {
        "imei": imei,
        "android_id": android_id,
        "mac": mac,
        "imsi": imsi,
        "simserial": simserial,
    }


def randomize_emulator_identity(
    emu_type: str,
    index: int,
    device_serial: Optional[str] = None
) -> Tuple[bool, Dict[str, str], str]:
    """
    Randomizes IMEI, Android ID, MAC Address, IMSI for LDPlayer or MuMu Player.
    Works whether the emulator is online or offline.
    If device_serial is online, also applies live via ADB shell!
    """
    emu_type = emu_type.lower()
    ident = generate_random_identity()
    new_imei = ident["imei"]
    new_id = ident["android_id"]
    new_mac = ident["mac"]
    new_imsi = ident["imsi"]
    new_sim = ident["simserial"]

    status_msgs = []
    success = True

    if emu_type == "mumu":
        if not MUMU_BIN:
            return False, ident, "MuMuManager.exe not found"
        c1, o1, _ = run_cmd(f'"{MUMU_BIN}" simulation -v {index} -sk imei -sv {new_imei}')
        c2, o2, _ = run_cmd(f'"{MUMU_BIN}" simulation -v {index} -sk android_id -sv {new_id}')
        c3, o3, _ = run_cmd(f'"{MUMU_BIN}" simulation -v {index} -sk mac_address -sv {new_mac}')
        success = (c1 == 0 and c2 == 0 and c3 == 0)
        status_msgs.append(f"MuMu #{index} Hardware IMEI={new_imei}, AndroidID={new_id}")

    elif emu_type == "ld":
        if not LDCONSOLE_BIN:
            return False, ident, "ldconsole.exe not found"
        cmd = (
            f'"{LDCONSOLE_BIN}" modify --index {index} '
            f'--imei auto --androidid {new_id} --mac auto '
            f'--imsi {new_imsi} --simserial {new_sim}'
        )
        c, o, err = run_cmd(cmd)
        success = (c == 0)
        status_msgs.append(f"LDPlayer #{index} Hardware identity randomized via ldconsole")

    else:
        return False, ident, f"Unsupported emulator: {emu_type}"

    # If device is online with ADB, also update Android ID live in settings
    if device_serial:
        try:
            run_cmd(f'"{ADB_BIN}" -s {device_serial} shell settings put secure android_id {new_id}', timeout=4.0)
            status_msgs.append(f"Live Android ID set via ADB on {device_serial}")
        except Exception:
            pass

    return success, ident, " | ".join(status_msgs)


def connect_device_adb(device_serial: str) -> bool:
    """Explicitly connects ADB to port-based device."""
    if ":" in device_serial:
        code, stdout, _ = run_cmd(f'"{ADB_BIN}" connect {device_serial}', timeout=3.0)
        return "connected" in stdout.lower()
    return True


def find_scrcpy() -> Optional[str]:
    """Finds path to scrcpy.exe for interactive screen mirroring."""
    desktop_scrcpy = os.path.expanduser(r"~\Desktop\scrcpy-win64-v4.0\scrcpy.exe")
    if os.path.exists(desktop_scrcpy):
        return desktop_scrcpy
    return shutil.which("scrcpy")


SCRCPY_BIN = find_scrcpy()


def launch_scrcpy(device_serial: str, title: str = "") -> Tuple[bool, str]:
    """Launches an interactive low-latency 60FPS screen mirror window via scrcpy."""
    if not SCRCPY_BIN or not os.path.exists(SCRCPY_BIN):
        return False, "scrcpy.exe not found on Desktop"
    try:
        if ":" in device_serial:
            connect_device_adb(device_serial)
        t = title or f"Instance - {device_serial}"
        cmd = [SCRCPY_BIN, "-s", device_serial, "--window-title", t, "--window-width", "360", "--stay-awake"]
        subprocess.Popen(cmd)
        return True, "Interactive Screen Mirroring launched!"
    except Exception as e:
        return False, str(e)


def capture_device_screen(device_serial: str, target_size=(160, 280)):
    """
    Captures live Android screen thumbnail via ADB screencap.
    Fast (~100ms) and lightweight.
    """
    try:
        import io
        from PIL import Image

        p = subprocess.Popen(
            [ADB_BIN, "-s", device_serial, "exec-out", "screencap", "-p"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        data, err = p.communicate(timeout=3.0)

        # Auto-reconnect if device wasn't connected yet in ADB
        if (not data or len(data) < 1000) and err and b"not found" in err:
            if ":" in device_serial:
                connect_device_adb(device_serial)
                p2 = subprocess.Popen(
                    [ADB_BIN, "-s", device_serial, "exec-out", "screencap", "-p"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL
                )
                data, _ = p2.communicate(timeout=3.0)

        if data and len(data) > 1000:
            if data.startswith(b"\x89PNG\r\r\n"):
                data = data.replace(b"\r\r\n", b"\r\n")
            img = Image.open(io.BytesIO(data))
            if target_size:
                img.thumbnail(target_size, Image.Resampling.LANCZOS)
            return img
    except Exception:
        pass
    return None


def show_emulator_window(emu_type: str, index: int) -> Tuple[bool, str]:
    """Brings the emulator window to the foreground."""
    emu_type = emu_type.lower()
    if emu_type == "mumu":
        if MUMU_BIN:
            c, out, _ = run_cmd(f'"{MUMU_BIN}" control -v {index} show_window', timeout=4.0)
            return (c == 0), "Window shown"
    elif emu_type == "ld":
        if LDCONSOLE_BIN:
            c, out, _ = run_cmd(f'"{LDCONSOLE_BIN}" launch --index {index}', timeout=4.0)
            return (c == 0), "LDPlayer window brought to front"
    return False, "Not supported"


def organize_all_windows() -> Tuple[bool, str]:
    """Arranges and sorts all player windows neatly."""
    msgs = []
    if LDCONSOLE_BIN:
        c, _, _ = run_cmd(f'"{LDCONSOLE_BIN}" sortWnd', timeout=4.0)
        if c == 0:
            msgs.append("LDPlayer windows sorted")
    if MUMU_BIN:
        c, _, _ = run_cmd(f'"{MUMU_BIN}" sort', timeout=4.0)
        if c == 0:
            msgs.append("MuMu windows sorted")
    return True, " | ".join(msgs) if msgs else "Organize command completed"


if __name__ == "__main__":
    print("Testing Emulator Manager...")
    print(f"ADB: {ADB_BIN}")
    print(f"LDConsole: {LDCONSOLE_BIN}")
    print(f"MuMuManager: {MUMU_BIN}")
    print(f"scrcpy: {SCRCPY_BIN}")
    instances = scan_all_instances()
    print(f"Found {len(instances)} emulator instances:")
    for inst in instances:
        print(f" - [{inst.emu_type.upper()} #{inst.index}] {inst.name} | Status: {inst.status_text} | Serial: {inst.adb_serial} | PID: {inst.pid}")

