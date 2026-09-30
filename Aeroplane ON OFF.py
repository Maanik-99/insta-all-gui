import subprocess
import time
import re
from datetime import datetime

# ============ কনফিগারেশন ============
ADB = r"C:\Users\_\Downloads\platform-tools-latest-windows\platform-tools\adb.exe"
SERIAL = "RKG6ORMZPFXGAE8X"
WAIT_MINUTES = 30
FLIGHT_ON_SECONDS = 5
RECONNECT_WAIT = 12
RETRY_ON_SECONDS = 8
RETRY_WAIT = 15
# =====================================


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run_adb(args, capture=True):
    cmd = [ADB, "-s", SERIAL] + args
    try:
        result = subprocess.run(cmd, capture_output=capture, text=True, timeout=30)
        return result.stdout.strip() if capture else ""
    except subprocess.TimeoutExpired:
        log("⚠ ADB command timed out!")
        return ""
    except FileNotFoundError:
        log("❌ ADB not found! Check the path.")
        return ""


def check_device():
    return run_adb(["get-state"]) == "device"


def flight_mode(state):
    action = "enable" if state else "disable"
    run_adb(["shell", "cmd", "connectivity", "airplane-mode", action])


def get_ip():
    """একাধিক পদ্ধতিতে IP বের করার চেষ্টা"""

    # পদ্ধতি ১: নির্দিষ্ট ইন্টারফেসে ip addr show
    for iface in ["ccmni0", "ccmni1", "rmnet_data0", "pdp0"]:
        out = run_adb(["shell", "ip", "-4", "addr", "show", iface])
        match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
        if match:
            return match.group(1)

    # পদ্ধতি ২: সব ইন্টারফেসে ip addr show
    out = run_adb(["shell", "ip", "-4", "addr", "show"])
    for iface in ["ccmni", "rmnet", "pdp"]:
        match = re.search(
            rf"{iface}\w*.*?inet\s+(\d+\.\d+\.\d+\.\d+)",
            out, re.DOTALL
        )
        if match:
            return match.group(1)

    # পদ্ধতি ৩: ifconfig
    for iface in ["ccmni0", "rmnet_data0"]:
        out = run_adb(["shell", "ifconfig", iface])
        match = re.search(r"inet addr:(\d+\.\d+\.\d+\.\d+)", out)
        if not match:
            match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
        if match:
            return match.group(1)

    # পদ্ধতি ৪: getprop
    out = run_adb(["shell", "getprop", "dhcp.ccmni0.ipaddress"])
    if out and "." in out:
        return out.strip()

    return "N/A"


def change_ip_once():
    flight_mode(True)
    log(f"Flight Mode ON (waiting {FLIGHT_ON_SECONDS}s)")
    time.sleep(FLIGHT_ON_SECONDS)

    flight_mode(False)
    log(f"Flight Mode OFF (waiting {RECONNECT_WAIT}s for reconnect)")
    time.sleep(RECONNECT_WAIT)


def ensure_ip_changed(old_ip):
    attempts = 0
    while True:
        attempts += 1
        new_ip = get_ip()
        log(f"New IP: {new_ip}")

        if new_ip != "N/A" and new_ip != old_ip:
            log(f"✅ SUCCESS! IP changed: {old_ip} → {new_ip}")
            return new_ip

        log(f"⚠ Same IP ({attempts} attempt). Toggling Flight Mode again...")
        flight_mode(True)
        time.sleep(RETRY_ON_SECONDS)
        flight_mode(False)
        log(f"Flight Mode OFF (waiting {RETRY_WAIT}s)")
        time.sleep(RETRY_WAIT)


def main():
    print("=" * 50)
    print("  Flight Mode Auto Toggle with IP Check")
    print("=" * 50)
    print()

    if not check_device():
        log("❌ Device not connected! Check USB cable.")
        return

    log(f"✅ Device connected: {SERIAL}")

    flight_mode(False)
    time.sleep(5)

    old_ip = get_ip()
    log(f"Initial IP: {old_ip}")

    if old_ip == "N/A":
        log("⚠ Initial IP is N/A — IP check may not work!")
        log("  Check diag.py output to find the correct interface.")
    print()

    try:
        while True:
            log(f"Waiting {WAIT_MINUTES} minutes...")
            time.sleep(WAIT_MINUTES * 60)

            change_ip_once()
            old_ip = ensure_ip_changed(old_ip)
            print()

    except KeyboardInterrupt:
        log("⏹ Stopped. Turning Flight Mode OFF...")
        flight_mode(False)
        log("Done.")


if __name__ == "__main__":
    main()