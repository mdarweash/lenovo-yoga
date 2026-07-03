#!/usr/bin/env python3
"""
test-hidraw-toggle.py — Test HID [0x20, 0x00] mode toggle via hidraw4
instead of raw USB interrupt transfer.

Theory: test-hid-toggle.py detaches the kernel HID driver from IF2 before
sending [0x20, 0x00] via raw USB. The MCU may check for proper HID driver
binding. This script keeps usbhid bound and sends the toggle through
/dev/hidraw4 (IF2's hidraw device).

Usage:
    sudo python3 test-hidraw-toggle.py [--duration 30] [--no-if0]
"""
import sys
import os
import time
import struct
import threading
import select
import argparse

import usb.core
import usb.util

# ─── Constants ────────────────────────────────────────────────────────

VID = 0x17ef
PID = 0x6161

IF0 = 0   # CDC ACM
IF1 = 1   # Vendor Specific — OSKP bulk
IF2 = 2   # HID Keyboard — EP 0x02 OUT / EP 0x83 IN

EP1_OUT = 0x01
EP1_IN  = 0x81

SET_LINE_CODING = 0x20
SET_CONTROL_LINE_STATE = 0x22

SYNC_INTERVAL_MS = 1000
INPUT_EVENT_SIZE = struct.calcsize("llHHi")
EV_ABS = 0x03
TIMEOUT = 5000


# ─── Helpers ──────────────────────────────────────────────────────────

def find_hidraw_for_interface(iface_num):
    """Find hidraw device for a specific USB interface of 17ef:6161."""
    for hr in sorted(os.listdir("/dev/"), key=lambda x: x):
        if not hr.startswith("hidraw"):
            continue
        path = f"/dev/{hr}"
        try:
            import subprocess
            result = subprocess.run(
                ["udevadm", "info", "-q", "property", path],
                capture_output=True, text=True, timeout=2)
            props = result.stdout
            vid = pid = iface = None
            for line in props.splitlines():
                k, _, v = line.partition("=")
                if k == "ID_VENDOR_ID":
                    vid = v
                elif k == "ID_MODEL_ID":
                    pid = v
                elif k == "ID_USB_INTERFACE_NUM":
                    iface = v
            if vid == "17ef" and pid == "6161" and int(iface) == iface_num:
                return path
        except Exception:
            pass
    return None


def build_touchpad_geometry():
    w, h = 3017, 1700
    pack_rect = lambda l, t, r, b: struct.pack("<HHHH", l, t, r, b)
    cap_h = int(h * 80 / 500)
    btn_h = int(h * 90 / 500)
    sm = int(w * 40 / 500)
    bm = int(h * 40 / 500)
    gap = int(w * 10 / 500)
    half_btn = (w - 2 * sm - gap) // 2
    return b"".join([
        pack_rect(0, 0, w, h),
        pack_rect(sm, h - bm - btn_h, sm + half_btn, h - bm),
        pack_rect(sm + half_btn + gap, h - bm - btn_h, w - sm, h - bm),
        pack_rect(sm, cap_h, w - sm, h - bm - btn_h),
        bytes([0x00]),
        pack_rect(0, 0, 0, 0),
    ])


def build_oskp(ptype, payload=b""):
    return b"OSKP" + struct.pack("<H", len(payload) + 1) + bytes([ptype]) + payload


def send_oskp(dev, ptype, payload=b""):
    dev.write(EP1_OUT, build_oskp(ptype, payload), timeout=TIMEOUT)


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
                _, _, ev_type, ev_code, _ = struct.unpack("llHHi", ev)
                if ev_type == EV_ABS and ev_code in (0x35, 0x36):
                    results.append(time.time())
        except Exception:
            pass
    try:
        os.close(fd)
    except Exception:
        pass


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Test HID toggle via hidraw (keep kernel HID driver on IF2)")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--no-if0", action="store_true",
                        help="Skip IF0 CDC ACM session")
    parser.add_argument("--no-oskp-activate", action="store_true",
                        help="Skip OSKP 0x25 activation (test if HID toggle alone works)")
    args = parser.parse_args()

    ev16 = find_event_num("Touchscreen Bottom")
    ev19 = find_event_num("Emulated Touchpad")
    if ev16 is None or ev19 is None:
        print(f"ERROR: event devices not found. ev16={ev16} ev19={ev19}")
        sys.exit(1)

    flush_stuck_touches()
    print(f"Monitor: event{ev16} (touchscreen), event{ev19} (touchpad)")

    # ── Find hidraw device for IF2 (HID Keyboard) ──────────────────
    hidraw_if2 = find_hidraw_for_interface(2)
    if hidraw_if2 is None:
        print("ERROR: No hidraw device found for IF2 (interface 2)")
        print("Available INGENIC hidraw devices:")
        for hr in sorted(os.listdir("/dev/")):
            if not hr.startswith("hidraw"):
                continue
            try:
                import subprocess
                r = subprocess.run(
                    ["udevadm", "info", "-q", "property", f"/dev/{hr}"],
                    capture_output=True, text=True, timeout=2)
                if "17ef" in r.stdout and "6161" in r.stdout:
                    iface = "?"
                    for line in r.stdout.splitlines():
                        if line.startswith("ID_USB_INTERFACE_NUM="):
                            iface = line.split("=")[1]
                    print(f"  /dev/{hr} — interface {iface}")
            except Exception:
                pass
        sys.exit(1)
    print(f"IF2 hidraw: {hidraw_if2}")

    # ── Open USB device ─────────────────────────────────────────────
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"ERROR: Device {VID:04x}:{PID:04x} not found")
        sys.exit(1)
    print(f"[open] Found {VID:04x}:{PID:04x}")

    start_time = time.time()
    stop_all = threading.Event()

    def elapsed():
        return time.time() - start_time

    # ── Claim IF1 (OSKP bulk) ONLY — leave IF2 alone! ──────────────
    print(f"\n[{elapsed():.2f}s] Claiming IF1 (OSKP bulk)...")
    if dev.is_kernel_driver_active(IF1):
        dev.detach_kernel_driver(IF1)
        print(f"[open]   IF1 kernel driver detached")
    usb.util.claim_interface(dev, IF1)
    print(f"[open]   IF1 claimed — IF2 left with kernel HID driver")

    # EP 0x81 reader (OSKP responses)
    ep81_count = 0
    def ep81_reader():
        nonlocal ep81_count
        while not stop_all.is_set():
            try:
                data = dev.read(EP1_IN, 512, timeout=500)
                if not data:
                    continue
                raw = bytes(data)
                pos = 0
                while pos + 6 <= len(raw):
                    if raw[pos:pos+4] == b"OSKP":
                        wlen = struct.unpack("<H", raw[pos+4:pos+6])[0]
                        total = 6 + wlen
                        if pos + total <= len(raw):
                            ftype = raw[pos+6]
                            payload = raw[pos+7:pos+6+wlen]
                            ep81_count += 1
                            t = elapsed()
                            if ftype == 0x75:
                                print(f"  [{t:6.2f}s] MCU 0x75 ACK: {payload.hex()}")
                            elif ftype == 0xa2:
                                if ep81_count <= 10:
                                    print(f"  [{t:6.2f}s] MCU 0xa2 pos: {payload.hex()}")
                            elif ftype == 0x50:
                                ver = payload[4:].decode('ascii', errors='replace').strip()
                                print(f"  [{t:6.2f}s] MCU 0x50 FW: {ver}")
                            else:
                                print(f"  [{t:6.2f}s] MCU 0x{ftype:02x}: {payload.hex()[:40]}")
                            pos += total
                        else:
                            break
                    else:
                        pos += 1
            except usb.core.USBError:
                pass
            except Exception:
                pass

    threading.Thread(target=ep81_reader, daemon=True).start()

    # ── Claim IF0 (CDC ACM) — optional ─────────────────────────────
    if0_claimed = False
    if not args.no_if0:
        print(f"\n[{elapsed():.2f}s] Claiming IF0 (CDC ACM)...")
        try:
            if dev.is_kernel_driver_active(IF0):
                dev.detach_kernel_driver(IF0)
            usb.util.claim_interface(dev, IF0)
            if0_claimed = True

            line_coding = struct.pack("<IBBB", 9600, 0, 0, 8)
            try:
                dev.ctrl_transfer(0x21, SET_LINE_CODING, 0, IF0, line_coding)
                print(f"[open]   SET_LINE_CODING(9600, 8N1) OK")
            except usb.core.USBError as e:
                print(f"[open]   SET_LINE_CODING failed: {e}")

            try:
                dev.ctrl_transfer(0x21, SET_CONTROL_LINE_STATE, 0x03, IF0, b"")
                print(f"[open]   SET_CONTROL_LINE_STATE(DTR=1, RTS=1) OK")
            except usb.core.USBError as e:
                print(f"[open]   SET_CONTROL_LINE_STATE failed: {e}")
        except Exception as e:
            print(f"[open]   IF0 claim failed: {e}")

    # ── Open hidraw for IF2 ─────────────────────────────────────────
    print(f"\n[{elapsed():.2f}s] Opening {hidraw_if2} for HID output report...")
    try:
        hidraw_fd = os.open(hidraw_if2, os.O_RDWR | os.O_NONBLOCK)
        print(f"[open]   {hidraw_if2} opened (kernel HID driver stays bound)")
    except Exception as e:
        print(f"[open]   Failed to open {hidraw_if2}: {e}")
        print("[open]   Trying write-only...")
        try:
            hidraw_fd = os.open(hidraw_if2, os.O_WRONLY)
            print(f"[open]   {hidraw_if2} opened write-only")
        except Exception as e2:
            print(f"[open]   Write-only also failed: {e2}")
            sys.exit(1)

    # ── Activation sequence ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"[activate] HID toggle via hidraw (kernel HID driver intact)")
    print(f"{'='*60}\n")

    # Option A: OSKP 0x25 01 activation first (proven to work for ~7s)
    if not args.no_oskp_activate:
        print(f"[{elapsed():.2f}s] >>> OSKP 0x25 01 (proven activation)")
        send_oskp(dev, 0x25, bytes([0x01]))
        time.sleep(0.5)

    # THE KEY TEST: HID [0x20, 0x00] via hidraw (NOT raw USB)
    print(f"[{elapsed():.2f}s] >>> HID [0x20, 0x00] via {hidraw_if2} (hidraw write)")
    try:
        # HID output report through hidraw: just write the raw report data
        # If device uses numbered reports, first byte is Report ID
        written = os.write(hidraw_fd, bytes([0x20, 0x00]))
        print(f"[{elapsed():.2f}s]     OK! Wrote {written} bytes via hidraw")
    except OSError as e:
        print(f"[{elapsed():.2f}s]     FAILED: {e}")
        print(f"[!] hidraw write failed. The HID driver may not support output reports")
        print(f"[!] on this device. Falling back to SET_REPORT control transfer...")
        # Try SET_REPORT control transfer without claiming IF2
        # This won't work if usbhid has the interface, but worth trying
        try:
            dev.ctrl_transfer(0x21, 0x09, 0x0200, IF2, bytes([0x20, 0x00]))
            print(f"[{elapsed():.2f}s]     SET_REPORT OK!")
        except Exception as e2:
            print(f"[{elapsed():.2f}s]     SET_REPORT also failed: {e2}")
            print(f"[!] Cannot send HID output report with kernel driver bound.")
            print(f"[!] This confirms the MCU expects HID output through the driver.")

    time.sleep(0.35)

    # OSKP 0x20 sync + 0x31 geometry
    print(f"[{elapsed():.2f}s] >>> OSKP 0x20 sync")
    send_oskp(dev, 0x20, bytes([0x01, 0x00]))
    time.sleep(0.05)

    geo = build_touchpad_geometry()
    print(f"[{elapsed():.2f}s] >>> OSKP 0x31 geometry ({len(geo)}B)")
    send_oskp(dev, 0x31, geo)

    print(f"\n[{elapsed():.2f}s] Activation complete. Starting keepalive...\n")

    # ── Steady state: 0x20 sync every 1s ────────────────────────────
    tick = 0
    sync_errors = 0

    def sync_thread():
        nonlocal tick, sync_errors
        while not stop_all.is_set():
            stop_all.wait(SYNC_INTERVAL_MS / 1000.0)
            if stop_all.is_set():
                break
            tick += 1
            try:
                send_oskp(dev, 0x20, bytes([0x01, 0x00]))
            except Exception as e:
                sync_errors += 1
                if sync_errors <= 3:
                    print(f"  [{elapsed():6.2f}s] sync FAILED: {e}")

    threading.Thread(target=sync_thread, daemon=True).start()

    # ── Periodically re-send HID toggle via hidraw ──────────────────
    hid_toggle_count = 0
    def hid_toggle_thread():
        nonlocal hid_toggle_count
        while not stop_all.is_set():
            stop_all.wait(5.0)  # Re-send every 5s
            if stop_all.is_set():
                break
            try:
                os.write(hidraw_fd, bytes([0x20, 0x00]))
                hid_toggle_count += 1
            except Exception:
                pass

    threading.Thread(target=hid_toggle_thread, daemon=True).start()
    print(f"[keepalive] OSKP 0x20 sync every {SYNC_INTERVAL_MS}ms + HID toggle every 5s")

    # ── Monitor events ──────────────────────────────────────────────
    print(f"\n=== Monitoring for {args.duration}s — touch the bottom screen! ===\n")

    hid_stop = threading.Event()
    ts_events = []
    tp_events = []
    threading.Thread(
        target=monitor_hid_events, args=(ev16, hid_stop, ts_events),
        daemon=True).start()
    threading.Thread(
        target=monitor_hid_events, args=(ev19, hid_stop, tp_events),
        daemon=True).start()

    last_ts = 0
    last_tp = 0
    for i in range(args.duration):
        time.sleep(1.0)
        t = elapsed()

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

        print(f"  {t:5.1f}s  tp:{new_tp:4d}  ts:{new_ts:4d}  {mode}")

    # ── Results ─────────────────────────────────────────────────────
    t = elapsed()
    print(f"\n{'='*60}")
    print(f"Results after {t:.1f}s:")
    print(f"  Touchpad events (event{ev19}):    {len(tp_events)}")
    print(f"  Touchscreen events (event{ev16}): {len(ts_events)}")
    print(f"  OSKP responses (EP 0x81):         {ep81_count}")
    print(f"  Sync ticks:                       {tick}")
    print(f"  HID toggle re-sends:              {hid_toggle_count}")
    if tp_events and not ts_events:
        print(f"\n  ✓✓✓ TOUCHPAD HELD for full {args.duration}s!")
        print(f"  ✓✓✓ HID toggle via hidraw WORKS — root cause confirmed!")
    elif tp_events and ts_events:
        print(f"\n  ✗ REVERTED at {ts_events[0]-start_time:.1f}s")
        revert_time = ts_events[0] - start_time
        print(f"  → Touchpad lasted {revert_time:.1f}s (baseline: ~15-17s)")
        if revert_time > 17:
            print(f"  → HID toggle via hidraw EXTENDED timeout — partial success!")
        else:
            print(f"  → No improvement from HID toggle via hidraw")
    else:
        print(f"\n  ? No touch input detected")
    print(f"{'='*60}")

    # ── Cleanup ─────────────────────────────────────────────────────
    print(f"\n[{elapsed():.2f}s] Cleaning up...")
    stop_all.set()
    hid_stop.set()

    try:
        send_oskp(dev, 0x31, bytes(41))
    except Exception:
        pass

    try:
        os.close(hidraw_fd)
        print(f"  {hidraw_if2} closed")
    except Exception:
        pass

    try:
        if if0_claimed:
            dev.ctrl_transfer(0x21, SET_CONTROL_LINE_STATE, 0x00, IF0, b"")
            usb.util.release_interface(dev, IF0)
            print(f"  IF0 released")
    except Exception:
        pass

    try:
        usb.util.release_interface(dev, IF1)
        print(f"  IF1 released")
    except Exception:
        pass

    try:
        dev.attach_kernel_driver(IF1)
    except Exception:
        pass

    usb.util.dispose_resources(dev)
    flush_stuck_touches()
    print(f"  Done.\n")


if __name__ == "__main__":
    main()
