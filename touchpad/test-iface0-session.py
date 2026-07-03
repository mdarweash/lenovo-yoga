#!/usr/bin/env python3
"""
test-iface0-session.py — Test if asserting DTR on interface 0 prevents MCU mode revert.

Theory: the MCU ties its touchpad mode session to the CDC ACM serial connection
on interface 0. On Windows, YB9.Service.exe opens the serial port, which asserts
DTR via SET_CONTROL_LINE_STATE. The MCU sees DTR=1 and maintains the session.

On Linux, interface 0 is deauthorized, so DTR is never asserted → MCU times out.

This script:
1. Re-authorizes interface 0 (if deauthorized)
2. Claims interface 0 with pyusb (NOT cdc_acm)
3. Sends SET_CONTROL_LINE_STATE (DTR=1) — skips SET_LINE_CODING (crashes MCU)
4. Activates touchpad mode via interface 1
5. Monitors for 30s with only 0x26 keepalives

Usage:
    sudo python3 test-iface0-session.py [--duration 30] [--dtr-only]
"""
import sys, os, time, struct, threading, argparse, select
import usb.core, usb.util

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "windows", "rd"))
from yb9_usb import (send_frame, send_frame_bytes, get_device, release_device,
                     EP_IN, wait_for_device, VID, PID)
from datetime import datetime

# ─── Constants ────────────────────────────────────────────────────────
INPUT_EVENT_SIZE = struct.calcsize("llHHi")
EV_ABS = 0x03
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36

# CDC ACM control transfers
USB_CDC_SET_CONTROL_LINE_STATE = 0x22
USB_CDC_SET_LINE_CODING = 0x20  # THIS CRASHES THE MCU — DO NOT SEND

IFACE0 = 0  # CDC ACM control interface

# ─── Helpers ──────────────────────────────────────────────────────────

def ts_bytes():
    now = datetime.now()
    return struct.pack("<HBBBBB", now.year, now.month, now.day, now.hour, now.minute, now.second)

def build_rect(l, t, r, b):
    return struct.pack("<HHHH", l, t, r, b)

def build_0x31():
    w, h = 3017, 1700
    cap_h = int(h * 80 / 500)
    btn_h = int(h * 90 / 500)
    sm = int(w * 40 / 500)
    bm = int(h * 40 / 500)
    gap = int(w * 10 / 500)
    fr = build_rect(0, 0, w, h)
    tr = build_rect(sm, cap_h, w - sm, h - bm - btn_h)
    btw = w - 2 * sm - gap
    lb = build_rect(sm, h - bm - btn_h, sm + btw // 2, h - bm)
    rb = build_rect(sm + btw // 2 + gap, h - bm - btn_h, w - sm, h - bm)
    tr2 = build_rect(0, 0, 0, 0)
    return fr + lb + rb + tr + bytes([0]) + tr2

def parse_oskp(data):
    frames = []
    pos = 0
    while pos + 6 <= len(data):
        if data[pos:pos + 4] == b"OSKP":
            plen = struct.unpack("<H", data[pos + 4:pos + 6])[0]
            if pos + 6 + plen <= len(data):
                frames.append((data[pos + 6], data[pos + 7:pos + 6 + plen]))
                pos += 6 + plen
            else:
                break
        else:
            pos += 1
    return frames

def find_event_num(name_fragment):
    for f in sorted(os.listdir("/dev/input/")):
        if not f.startswith("event"):
            continue
        try:
            with open(f"/sys/class/input/{f}/device/name") as nf:
                if name_fragment in nf.read():
                    return int(f.replace("event", ""))
        except (FileNotFoundError, PermissionError):
            pass
    return None

def flush_stuck_touches():
    for evnum in [16, 19]:
        try:
            fd = os.open(f"/dev/input/event{evnum}", os.O_WRONLY | os.O_NONBLOCK)
            for t, c, v in [(0x03, 0x39, -1), (0x01, 0x14a, 0), (0x00, 0x00, 0)]:
                os.write(fd, struct.pack("llHHi", 0, 0, t, c, v))
            os.close(fd)
        except Exception:
            pass

def monitor_hid_events(evnum, stop_event, results):
    try:
        fd = os.open(f"/dev/input/event{evnum}", os.O_RDONLY | os.O_NONBLOCK)
    except Exception:
        return
    while not stop_event.is_set():
        try:
            readable, _, _ = select.select([fd], [], [], 0.2)
            if not readable:
                continue
            data = os.read(fd, INPUT_EVENT_SIZE * 64)
            for i in range(len(data) // INPUT_EVENT_SIZE):
                ev = data[i * INPUT_EVENT_SIZE:(i + 1) * INPUT_EVENT_SIZE]
                _, _, ev_type, ev_code, ev_val = struct.unpack("llHHi", ev)
                if ev_type == EV_ABS and ev_code in (ABS_MT_POSITION_X, ABS_MT_POSITION_Y):
                    results.append((time.time(), ev_code, ev_val))
        except Exception:
            pass
    try:
        os.close(fd)
    except Exception:
        pass

# ─── Interface 0 session ─────────────────────────────────────────────

def reauthorize_iface0():
    """Re-authorize interface 0 if deauthorized."""
    auth_file = "/sys/bus/usb/devices/3-6/3-6:1.0/authorized"
    if not os.path.exists(auth_file):
        print("  WARNING: auth file not found, searching...")
        for d in os.listdir("/sys/bus/usb/devices/"):
            candidate = f"/sys/bus/usb/devices/{d}/{d}:1.0/authorized"
            if os.path.exists(candidate):
                try:
                    with open(f"/sys/bus/usb/devices/{d}/idVendor") as f:
                        if f.read().strip() != "17ef":
                            continue
                    with open(f"/sys/bus/usb/devices/{d}/idProduct") as f:
                        if f.read().strip() != "6161":
                            continue
                    auth_file = candidate
                    break
                except Exception:
                    continue

    cur = open(auth_file).read().strip()
    print(f"  Interface 0 authorized={cur}")
    if cur == "0":
        print("  Re-authorizing interface 0...")
        # First, prevent cdc_acm from binding by removing the device ID
        try:
            with open("/sys/bus/usb/drivers/cdc_acm/remove_id", "w") as f:
                f.write("17ef 6161\n")
            print("  Removed 17ef:6161 from cdc_acm match table")
        except Exception as e:
            print(f"  cdc_acm remove_id: {e} (may be OK)")
        with open(auth_file, "w") as f:
            f.write("1\n")
        time.sleep(0.5)  # wait for USB re-enumeration
        print("  Interface 0 re-authorized")
    else:
        print("  Already authorized")

_iface0_dev = None

def claim_iface0():
    """Claim interface 0 with pyusb. Returns the device."""
    global _iface0_dev
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("Device not found")

    # Detach any kernel driver (cdc_acm) from interface 0
    if dev.is_kernel_driver_active(IFACE0):
        print(f"  Detaching kernel driver from interface 0...")
        try:
            dev.detach_kernel_driver(IFACE0)
            print("  Detached")
        except usb.core.USBError as e:
            print(f"  Cannot detach: {e}")
            print("  Trying remove_id + unbind...")
            # Force unbind
            try:
                for d in os.listdir("/sys/bus/usb/drivers/cdc_acm/"):
                    if "3-6" in d:
                        with open(f"/sys/bus/usb/drivers/cdc_acm/{d}/driver/unbind", "w") as f:
                            f.write(d + "\n")
                        print(f"  Unbound {d}")
            except Exception as e2:
                print(f"  Unbind failed: {e2}")
            # Try again
            time.sleep(0.3)
            if dev.is_kernel_driver_active(IFACE0):
                dev.detach_kernel_driver(IFACE0)

    usb.util.claim_interface(dev, IFACE0)
    _iface0_dev = dev
    print("  Claimed interface 0")
    return dev

def set_dtr_rts(dev, dtr=True, rts=True):
    """Send SET_CONTROL_LINE_STATE to assert DTR/RTS via interface 0.
    
    This is what a serial port open does on Windows — tells the MCU
    "host is connected." We skip SET_LINE_CODING (bRequest 0x20) which
    crashes the INGENIC MCU.
    """
    value = 0
    if dtr:
        value |= 0x01  # DTR
    if rts:
        value |= 0x02  # RTS

    # bmRequestType: 0x21 = host-to-device, class, interface
    # bRequest: 0x22 = SET_CONTROL_LINE_STATE
    # wValue: DTR/RTS flags
    # wIndex: interface number (0)
    # wLength: 0
    try:
        dev.ctrl_transfer(0x21, USB_CDC_SET_CONTROL_LINE_STATE, value, IFACE0, b"")
        print(f"  SET_CONTROL_LINE_STATE: DTR={dtr} RTS={rts} (value=0x{value:02x})")
        return True
    except usb.core.USBError as e:
        print(f"  SET_CONTROL_LINE_STATE FAILED: {e}")
        return False

def release_iface0():
    global _iface0_dev
    if _iface0_dev is not None:
        try:
            # Deassert DTR
            set_dtr_rts(_iface0_dev, dtr=False, rts=False)
        except Exception:
            pass
        try:
            usb.util.release_interface(_iface0_dev, IFACE0)
            _iface0_dev.attach_kernel_driver(IFACE0)
        except Exception:
            pass
        _iface0_dev = None

def deauthorize_iface0():
    """Deauthorize interface 0 after test."""
    auth_file = None
    for d in os.listdir("/sys/bus/usb/devices/"):
        candidate = f"/sys/bus/usb/devices/{d}/{d}:1.0/authorized"
        if os.path.exists(candidate):
            try:
                with open(f"/sys/bus/usb/devices/{d}/idVendor") as f:
                    if f.read().strip() != "17ef":
                        continue
                with open(f"/sys/bus/usb/devices/{d}/idProduct") as f:
                    if f.read().strip() != "6161":
                        continue
                auth_file = candidate
                break
            except Exception:
                continue
    if auth_file:
        try:
            with open(auth_file, "w") as f:
                f.write("0\n")
            print("  Interface 0 deauthorized (restored)")
        except Exception as e:
            print(f"  Could not deauthorize: {e}")

# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Test interface 0 session for touchpad persistence")
    parser.add_argument("--duration", type=int, default=30, help="Monitor duration (default: 30s)")
    parser.add_argument("--dtr-only", action="store_true",
                        help="Only assert DTR, don't send keepalives through iface0")
    parser.add_argument("--no-activate", action="store_true",
                        help="Don't activate touchpad — just assert DTR and monitor")
    args = parser.parse_args()

    ev16 = find_event_num("Touchscreen Bottom")
    ev19 = find_event_num("Emulated Touchpad")
    if ev16 is None or ev19 is None:
        print(f"ERROR: event devices not found. ev16={ev16} ev19={ev19}")
        sys.exit(1)

    flush_stuck_touches()
    print(f"Monitor: event{ev16} (touchscreen), event{ev19} (touchpad)\n")

    # ── Step 1: Re-authorize and claim interface 0 ──
    print("=== Step 1: Setup interface 0 session ===")
    reauthorize_iface0()
    time.sleep(0.5)

    iface0_dev = claim_iface0()
    time.sleep(0.2)

    # Assert DTR — this tells the MCU "host serial session is active"
    set_dtr_rts(iface0_dev, dtr=True, rts=True)
    time.sleep(0.3)

    # ── Step 2: Activate touchpad via interface 1 ──
    print("\n=== Step 2: Activate touchpad ===")
    wait_for_device(timeout=5)

    start_time = time.time()
    stop_all = threading.Event()

    # MCU reader
    def reader():
        while not stop_all.is_set():
            try:
                dev = get_device()
                data = dev.read(EP_IN, 512, timeout=200)
                if not data:
                    continue
                for ftype, payload in parse_oskp(bytes(data)):
                    elapsed = time.time() - start_time
                    if ftype == 0x75:
                        print(f"  [{elapsed:6.2f}s] MCU -> 0x75 ACK: {payload.hex()}")
                    elif ftype == 0x50:
                        ver = payload[4:].decode('ascii', errors='replace').strip()
                        print(f"  [{elapsed:6.2f}s] MCU -> 0x50 FW: {ver}")
                    elif ftype not in (0xa2, 0x26):
                        print(f"  [{elapsed:6.2f}s] MCU -> 0x{ftype:02x} {payload.hex()[:40]}")
            except Exception:
                pass

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    # HID monitors
    hid_stop = threading.Event()
    ts_events = []
    tp_events = []
    threading.Thread(target=monitor_hid_events, args=(ev16, hid_stop, ts_events), daemon=True).start()
    threading.Thread(target=monitor_hid_events, args=(ev19, hid_stop, tp_events), daemon=True).start()

    if not args.no_activate:
        # Send full activation sequence
        print("Sending activation sequence...")
        send_frame(0x4b, "0008b80b10271027"); time.sleep(0.05)
        send_frame(0x21, "8001010000");        time.sleep(0.05)
        send_frame(0x28, "0000");              time.sleep(0.05)
        send_frame(0x26, ts_bytes().hex());    time.sleep(0.1)

        send_frame(0x25, "01");  time.sleep(0.05)
        print(f"  [{time.time()-start_time:5.2f}s] 0x25 01 sent")
        send_frame(0x20, "0100");  time.sleep(0.05)
        send_frame(0x21, "7e01000000");  time.sleep(0.1)

        send_frame(0x26, ts_bytes().hex())
        send_frame_bytes(0x21, struct.pack("<BHH", 0x40, 3017, 1700))
        send_frame_bytes(0x21, struct.pack("<BHH", 0x41, 3017, 1700))
        send_frame_bytes(0x21, struct.pack("<BBBBB", 0x31, 0, 0, 0, 0))
        send_frame_bytes(0xa3, b"\x01")
        send_frame_bytes(0x31, build_0x31())
        send_frame(0x26, ts_bytes().hex())
        print(f"  [{time.time()-start_time:5.2f}s] Geometry sent")

    # ── Step 3: Monitor ──
    print(f"\n=== Step 3: Monitor for {args.duration}s ===")
    print("  Interface 0: DTR asserted, session active")
    print("  Only 0x26 keepalives on interface 1")
    print("  Touch the bottom screen every 2-3 seconds!\n")

    last_ts = 0
    last_tp = 0
    for i in range(args.duration):
        time.sleep(1.0)
        elapsed = time.time() - start_time

        ts_now = len(ts_events)
        tp_now = len(tp_events)
        new_ts = ts_now - last_ts
        new_tp = tp_now - last_tp
        last_ts = ts_now
        last_tp = tp_now

        mode = "[no touch]"
        if new_tp > 0 and new_ts == 0:
            mode = "[TOUCHPAD ✓]"
        elif new_ts > 0 and new_tp == 0:
            mode = "[TOUCHSCREEN — REVERTED]"
        elif new_ts > 0 and new_tp > 0:
            mode = "[BOTH]"

        print(f"  {elapsed:5.1f}s  tp:{new_tp:4d}  ts:{new_ts:4d}  {mode}")

        # Send keepalive through interface 1
        try:
            send_frame(0x26, ts_bytes().hex())
        except Exception as e:
            print(f"  {elapsed:.1f}s SEND FAILED: {e}")
            break

    # ── Results ──
    elapsed = time.time() - start_time
    print(f"\n--- Results ---")
    print(f"  Duration: {elapsed:.1f}s")
    print(f"  Touchpad events (event19):    {len(tp_events)}")
    print(f"  Touchscreen events (event16): {len(ts_events)}")
    if tp_events:
        print(f"  Touchpad window: {tp_events[0][0]-start_time:.2f}s — {tp_events[-1][0]-start_time:.2f}s")
    if ts_events:
        print(f"  Touchscreen onset: {ts_events[0][0]-start_time:.2f}s")

    if tp_events and not ts_events:
        print(f"  ✓ TOUCHPAD HELD for full {args.duration}s!")
    elif tp_events and ts_events:
        revert_t = ts_events[0][0] - start_time
        print(f"  ✗ REVERTED at {revert_t:.1f}s")
    else:
        print(f"  ? No touch input")

    # ── Cleanup ──
    stop_all.set()
    hid_stop.set()
    reader_thread.join(timeout=2)

    print("\nRestoring...")
    try:
        send_frame(0x25, "00")
        send_frame(0x21, "7e00000000")
        time.sleep(0.3)
    except Exception:
        pass

    release_iface0()
    time.sleep(0.3)
    deauthorize_iface0()
    flush_stuck_touches()

    try:
        release_device()
    except Exception:
        pass
    print("  Done.\n")

if __name__ == "__main__":
    main()
