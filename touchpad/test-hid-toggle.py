#!/usr/bin/env python3
"""
test-hid-toggle.py — Validate the Windows capture discovery: HID mode toggle on IF2 EP 0x02.

The Windows USB captures (2026-05-16) revealed the ACTUAL activation mechanism:
  - HID output report [0x20, 0x00] to Interface 2 EP 0x02 activates touchpad
  - OSKP 0x20 sync every 1000ms keeps it alive (NOT 0x26 timestamp)
  - OSKP 0x31 geometry configures the touchpad area

The old Linux approach (OSKP 0x25 01 mode toggle + 0x26 keepalive) was wrong
and caused the MCU to revert after ~7 seconds.

This script replicates the EXACT Windows sequence from Capture 10:
  1. SET_IDLE on IF2 (MCU STALLs — harmless, Windows sends it)
  2. Start polling EP 0x83 (IF2 HID interrupt IN)
  3. Start polling EP 0x81 (IF1 OSKP bulk IN)
  4. SET_LINE_CODING on IF0 (Windows uses 115200, we use 9600 to avoid crash)
  5. HID [0x20, 0x00] to IF2 EP 0x02 — THE MODE TOGGLE
  6. OSKP 0x20 sync on IF1
  7. OSKP 0x31 geometry on IF1
  8. Steady state: 0x20 sync every ~1s

Usage:
    sudo python3 test-hid-toggle.py [--duration 30] [--no-geometry] [--no-if0]
"""
import sys
import os
import time
import struct
import threading
import select
import signal
import argparse

import usb.core
import usb.util

# ─── Constants ────────────────────────────────────────────────────────

VID = 0x17ef
PID = 0x6161

IF0 = 0   # CDC ACM — serial control channel
IF1 = 1   # Vendor Specific (0xFF) — OSKP bulk EP 0x01 OUT / EP 0x81 IN
IF2 = 2   # HID Keyboard — EP 0x02 OUT / EP 0x83 IN

EP1_OUT = 0x01
EP1_IN  = 0x81
EP2_OUT = 0x02   # HID Interrupt OUT — mode toggle goes here
EP2_IN  = 0x83   # HID Interrupt IN  — keyboard data
EP0_INT = 0x82   # IF0 CDC interrupt IN

SET_LINE_CODING = 0x20
SET_CONTROL_LINE_STATE = 0x22
SET_IDLE = 0x0a

SYNC_INTERVAL_MS = 1000  # Windows sends 0x20 sync every ~1s

INPUT_EVENT_SIZE = struct.calcsize("llHHi")
EV_ABS = 0x03

TIMEOUT = 5000

# ─── Geometry (from Windows captures + decompilation) ─────────────────

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
        pack_rect(0, 0, w, h),                              # frameRect
        pack_rect(sm, h - bm - btn_h, sm + half_btn, h - bm),  # LButton
        pack_rect(sm + half_btn + gap, h - bm - btn_h, w - sm, h - bm),  # RButton
        pack_rect(sm, cap_h, w - sm, h - bm - btn_h),       # touchableRect1
        bytes([0x00]),                                       # flags
        pack_rect(0, 0, 0, 0),                              # touchableRect2
    ])


# ─── OSKP Frame Builder ──────────────────────────────────────────────

def build_oskp(ptype, payload=b""):
    frame = b"OSKP"
    frame += struct.pack("<H", len(payload) + 1)
    frame += bytes([ptype])
    frame += payload
    return frame


def send_oskp(dev, ptype, payload=b""):
    frame = build_oskp(ptype, payload)
    dev.write(EP1_OUT, frame, timeout=TIMEOUT)


# ─── Event monitoring ────────────────────────────────────────────────

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
            fd = os.open(f"/dev/input/event{evnum}",
                         os.O_WRONLY | os.O_NONBLOCK)
            for t, c, v in [(0x03, 0x39, -1), (0x01, 0x14a, 0), (0x00, 0x00, 0)]:
                os.write(fd, struct.pack("llHHi", 0, 0, t, c, v))
            os.close(fd)
        except Exception:
            pass


def monitor_hid_events(evnum, stop_event, results):
    try:
        fd = os.open(f"/dev/input/event{evnum}",
                     os.O_RDONLY | os.O_NONBLOCK)
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
        description="Validate HID [0x20,0x00] mode toggle from Windows captures")
    parser.add_argument("--duration", type=int, default=30,
                        help="Monitor duration in seconds (default: 30)")
    parser.add_argument("--no-geometry", action="store_true",
                        help="Skip 0x31 geometry (test if toggle alone works)")
    parser.add_argument("--no-if0", action="store_true",
                        help="Skip IF0 CDC ACM session entirely")
    args = parser.parse_args()

    ev16 = find_event_num("Touchscreen Bottom")
    ev19 = find_event_num("Emulated Touchpad")
    if ev16 is None or ev19 is None:
        print(f"ERROR: event devices not found. ev16={ev16} ev19={ev19}")
        sys.exit(1)

    flush_stuck_touches()
    print(f"Monitor: event{ev16} (touchscreen), event{ev19} (touchpad)")
    print()

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"ERROR: Device {VID:04x}:{PID:04x} not found")
        sys.exit(1)
    print(f"[open] Found {VID:04x}:{PID:04x}")

    start_time = time.time()
    stop_all = threading.Event()

    def elapsed():
        return time.time() - start_time

    # ── Claim IF1 (Vendor Specific — OSKP bulk) ────────────────────
    print(f"\n[{elapsed():.2f}s] Claiming IF1 (OSKP bulk)...")
    if dev.is_kernel_driver_active(IF1):
        dev.detach_kernel_driver(IF1)
        print(f"[open]   IF1 kernel driver detached")
    usb.util.claim_interface(dev, IF1)
    print(f"[open]   IF1 claimed (EP 0x01 OUT, EP 0x81 IN)")

    # Start EP 0x81 reader (OSKP responses from MCU)
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
                                # Position report — only log first few
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
    print(f"[open]   EP 0x81 reader started")

    # ── Claim IF2 (HID Keyboard — EP 0x02 OUT for mode toggle) ─────
    print(f"\n[{elapsed():.2f}s] Claiming IF2 (HID — EP 0x02 OUT for mode toggle)...")
    if dev.is_kernel_driver_active(IF2):
        print(f"[open]   IF2 — detaching usbhid (keyboard will pause)")
        dev.detach_kernel_driver(IF2)
    usb.util.claim_interface(dev, IF2)
    print(f"[open]   IF2 claimed (EP 0x02 OUT, EP 0x83 IN)")

    # SET_IDLE on IF2 — Windows sends this, MCU STALLs it, harmless
    try:
        dev.ctrl_transfer(0x21, SET_IDLE, 0, IF2, b"")
        print(f"[open]   SET_IDLE on IF2 OK")
    except usb.core.USBError:
        print(f"[open]   SET_IDLE on IF2 STALLed (expected per Windows captures)")

    # Start EP 0x83 reader (keyboard HID data)
    ep83_count = 0
    def ep83_reader():
        nonlocal ep83_count
        while not stop_all.is_set():
            try:
                data = dev.read(EP2_IN, 64, timeout=500)
                if data and len(data) > 0:
                    ep83_count += 1
                    if ep83_count <= 5:
                        print(f"  [{elapsed():6.2f}s] EP83 HID: {bytes(data).hex()}")
            except usb.core.USBError:
                pass
            except Exception:
                pass

    threading.Thread(target=ep83_reader, daemon=True).start()
    print(f"[open]   EP 0x83 reader started")

    # ── Claim IF0 (CDC ACM — optional, matches Windows) ────────────
    if0_claimed = False
    if not args.no_if0:
        print(f"\n[{elapsed():.2f}s] Claiming IF0 (CDC ACM)...")
        try:
            if dev.is_kernel_driver_active(IF0):
                print(f"[open]   IF0 — detaching kernel driver")
                dev.detach_kernel_driver(IF0)
            usb.util.claim_interface(dev, IF0)
            if0_claimed = True
            print(f"[open]   IF0 claimed")

            # SET_LINE_CODING — Windows sends 115200, but that crashes on Linux.
            # 9600 is safe per test-set-line-coding.py.
            line_coding = struct.pack("<IBBB", 9600, 0, 0, 8)
            try:
                dev.ctrl_transfer(0x21, SET_LINE_CODING, 0, IF0, line_coding)
                print(f"[open]   SET_LINE_CODING(9600, 8N1) OK")
            except usb.core.USBError as e:
                print(f"[open]   SET_LINE_CODING failed: {e}")

            # SET_CONTROL_LINE_STATE — DTR=1, RTS=1
            try:
                dev.ctrl_transfer(0x21, SET_CONTROL_LINE_STATE, 0x03, IF0, b"")
                print(f"[open]   SET_CONTROL_LINE_STATE(DTR=1, RTS=1) OK")
            except usb.core.USBError as e:
                print(f"[open]   SET_CONTROL_LINE_STATE failed: {e}")

            # Start EP 0x82 reader (CDC serial state)
            def ep82_reader():
                while not stop_all.is_set():
                    try:
                        data = dev.read(EP0_INT, 10, timeout=1000)
                        if data:
                            print(f"  [{elapsed():6.2f}s] EP0x82: {bytes(data).hex()}")
                    except usb.core.USBError:
                        pass
                    except Exception:
                        time.sleep(0.1)

            threading.Thread(target=ep82_reader, daemon=True).start()
            print(f"[open]   EP 0x82 reader started")

        except Exception as e:
            print(f"[open]   IF0 claim failed (non-fatal): {e}")

    # ── THE ACTIVATION SEQUENCE (from Capture 10) ──────────────────
    print(f"\n{'='*60}")
    print(f"[activate] Windows Capture 10 activation sequence")
    print(f"[activate] HID [0x20, 0x00] → IF2 EP 0x02 — THE MODE TOGGLE")
    print(f"{'='*60}\n")

    # Step 1: HID output report [0x20, 0x00] to IF2 EP 0x02
    # This is what Windows does in Frame 164 of Capture 10.
    # MCU immediately switches from touchscreen to touchpad mode.
    print(f"[{elapsed():.2f}s] >>> HID [0x20, 0x00] → EP 0x02 OUT (MODE TOGGLE)")
    try:
        dev.write(EP2_OUT, bytes([0x20, 0x00]), timeout=TIMEOUT)
        print(f"[{elapsed():.2f}s]     OK!")
    except usb.core.USBError as e:
        print(f"[{elapsed():.2f}s]     FAILED: {e}")
        print(f"[!] HID output report failed. Trying SET_REPORT fallback...")
        try:
            dev.ctrl_transfer(0x21, 0x09, 0x0200, IF2,
                              bytes([0x20, 0x00]))
            print(f"[{elapsed():.2f}s]     SET_REPORT OK!")
        except usb.core.USBError as e2:
            print(f"[{elapsed():.2f}s]     SET_REPORT also failed: {e2}")

    time.sleep(0.35)  # MCU takes ~350ms to start flooding EP 0x84

    # Step 2: OSKP 0x20 sync on IF1 EP 0x01
    # From Capture 10 Frame 253: first sync at ~5.99s (0.35s after toggle)
    print(f"[{elapsed():.2f}s] >>> OSKP 0x20 sync → EP 0x01")
    send_oskp(dev, 0x20, bytes([0x01, 0x00]))
    time.sleep(0.05)

    # Step 3: OSKP 0x31 geometry on IF1 EP 0x01
    # From Capture 10 Frame 257: geometry at ~6.03s
    if not args.no_geometry:
        geo = build_touchpad_geometry()
        print(f"[{elapsed():.2f}s] >>> OSKP 0x31 geometry ({len(geo)}B) → EP 0x01")
        send_oskp(dev, 0x31, geo)
        time.sleep(0.05)

    print(f"\n[{elapsed():.2f}s] Activation complete. Starting keepalive...\n")

    # ── Steady state: 0x20 sync every 1000ms ──────────────────────
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
    print(f"[keepalive] 0x20 sync every {SYNC_INTERVAL_MS}ms started")

    # ── Monitor HID events ─────────────────────────────────────────
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

    # ── Results ────────────────────────────────────────────────────
    t = elapsed()
    print(f"\n{'='*60}")
    print(f"Results after {t:.1f}s:")
    print(f"  Touchpad events (event{ev19}):    {len(tp_events)}")
    print(f"  Touchscreen events (event{ev16}): {len(ts_events)}")
    print(f"  OSKP responses (EP 0x81):         {ep81_count}")
    print(f"  HID reports (EP 0x83):            {ep83_count}")
    print(f"  Sync ticks:                       {tick}")
    print(f"  Sync errors:                      {sync_errors}")
    if tp_events:
        print(f"  Touchpad window: {tp_events[0]-start_time:.2f}s — {tp_events[-1]-start_time:.2f}s")
    if ts_events:
        print(f"  First touchscreen event: {ts_events[0]-start_time:.2f}s")
    if tp_events and not ts_events:
        print(f"\n  ✓✓✓ TOUCHPAD HELD for full {args.duration}s!")
        print(f"  ✓✓✓ HID [0x20, 0x00] toggle WORKS — root cause confirmed!")
    elif tp_events and ts_events:
        print(f"\n  ✗ REVERTED at {ts_events[0]-start_time:.1f}s")
        print(f"  → HID toggle alone insufficient, investigate further")
    else:
        print(f"\n  ? No touch input detected")
    print(f"{'='*60}")

    # ── Cleanup ────────────────────────────────────────────────────
    print(f"\n[{elapsed():.2f}s] Cleaning up...")
    stop_all.set()
    hid_stop.set()

    # Deactivate: send 0x31 all-zeros (Windows method from Capture 06)
    try:
        send_oskp(dev, 0x31, bytes(41))
        print(f"  0x31 all-zeros (clear geometry)")
        time.sleep(0.1)
    except Exception:
        pass

    # Release interfaces
    try:
        usb.util.release_interface(dev, IF2)
        print(f"  IF2 released")
    except Exception:
        pass

    try:
        if if0_claimed:
            dev.ctrl_transfer(0x21, SET_CONTROL_LINE_STATE, 0x00, IF0, b"")
    except Exception:
        pass

    try:
        if if0_claimed:
            usb.util.release_interface(dev, IF0)
            print(f"  IF0 released")
    except Exception:
        pass

    try:
        usb.util.release_interface(dev, IF1)
        print(f"  IF1 released")
    except Exception:
        pass

    # Re-attach kernel drivers
    try:
        dev.attach_kernel_driver(IF2)
        print(f"  IF2 usbhid re-attached")
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
