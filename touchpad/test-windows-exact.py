#!/usr/bin/env python3
"""
test-windows-exact.py — Replicate the EXACT Windows activation sequence.

Copies YB9.Service.exe behavior step by step from decompilation + Capture 10:

  1. Claim IF1 (OSKP bulk) — send init commands through here
  2. Claim IF0 (CDC ACM) — SET_LINE_CODING 115200 + DTR/RTS
  3. Init sequence (from YB9.Service.exe startup):
     a. OSKP 0x25 [0x00] — init toggle (touchpad OFF)
     b. OSKP 0x31 [33 zeros] — init config (clear geometry)
  4. KeepConnectThread (from decompilation):
     a. Sleep 1500ms
     b. OSKP 0x26 with timestamp — sent ONCE
  5. Activation (from Capture 10 frame-by-frame):
     a. SET_IDLE on IF2 (STALLed — expected)
     b. Start polling EP 0x83 IN (HID keyboard)
     c. Start polling EP 0x81 IN (OSKP responses)
     d. Start polling EP 0x82 IN (CDC ACM notifications)
     e. SET_LINE_CODING 115200 on IF0 (re-sent, like Windows)
     f. HID [0x20, 0x00] on IF2 EP 0x02 — THE mode toggle
  6. Post-activation:
     a. OSKP 0x20 sync
     b. OSKP 0x31 geometry
  7. Steady state: OSKP 0x20 sync every 1000ms

Usage:
    sudo python3 test-windows-exact.py [--duration 60] [--skip-init] [--skip-hid] [--hidraw]
"""
import sys
import os
import time
import struct
import threading
import select
import argparse
from datetime import datetime

import usb.core
import usb.util

VID = 0x17ef
PID = 0x6161

IF0 = 0   # CDC ACM
IF1 = 1   # Vendor Specific — OSKP bulk
IF2 = 2   # HID Keyboard

EP1_OUT = 0x01
EP1_IN  = 0x81
EP2_OUT = 0x02   # HID Interrupt OUT (IF2)
EP2_IN  = 0x83   # HID Interrupt IN  (IF2)
EP0_INT = 0x82   # CDC ACM notify (IF0)

SET_LINE_CODING = 0x20
SET_CONTROL_LINE_STATE = 0x22
SET_IDLE = 0x0a

TIMEOUT = 5000
SYNC_INTERVAL = 1.0   # 1000ms like Windows
INPUT_EVENT_SIZE = struct.calcsize("llHHi")
EV_ABS = 0x03


# ─── Helpers ──────────────────────────────────────────────────────────

def build_oskp(ptype, payload=b""):
    return b"OSKP" + struct.pack("<H", len(payload) + 1) + bytes([ptype]) + payload

def send_oskp(dev, ptype, payload=b""):
    dev.write(EP1_OUT, build_oskp(ptype, payload), timeout=TIMEOUT)

def ts_bytes():
    now = datetime.now()
    return struct.pack("<HBBBBB", now.year, now.month, now.day,
                       now.hour, now.minute, now.second)

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

def find_hidraw_for_interface(iface_num):
    import subprocess
    for hr in sorted(os.listdir("/dev/"), key=lambda x: x):
        if not hr.startswith("hidraw"):
            continue
        path = f"/dev/{hr}"
        try:
            r = subprocess.run(
                ["udevadm", "info", "-q", "property", path],
                capture_output=True, text=True, timeout=2)
            vid = pid = iface = None
            for line in r.stdout.splitlines():
                k, _, v = line.partition("=")
                if k == "ID_VENDOR_ID": vid = v
                elif k == "ID_MODEL_ID": pid = v
                elif k == "ID_USB_INTERFACE_NUM": iface = v
            if vid == "17ef" and pid == "6161" and int(iface) == iface_num:
                return path
        except Exception:
            pass
    return None

def flush_stuck_touches():
    for evnum in [13, 16]:
        try:
            fd = os.open(f"/dev/input/event{evnum}",
                         os.O_WRONLY | os.O_NONBLOCK)
            for t, c, v in [(0x03, 0x39, -1), (0x01, 0x14a, 0),
                            (0x00, 0x00, 0)]:
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
        description="Replicate EXACT Windows activation sequence")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--skip-init", action="store_true",
                        help="Skip YB9.Service.exe init sequence (0x25 00 + 0x31 zeros)")
    parser.add_argument("--skip-0x26", action="store_true",
                        help="Skip KeepConnectThread 0x26 keepalive")
    parser.add_argument("--skip-hid", action="store_true",
                        help="Skip HID toggle, use OSKP 0x25 01 instead")
    parser.add_argument("--hidraw", action="store_true",
                        help="Send HID toggle via hidraw instead of raw USB")
    parser.add_argument("--baud", type=int, default=115200,
                        help="SET_LINE_CODING baud rate (default: 115200, like Windows)")
    args = parser.parse_args()

    ev_ts = find_event_num("Touchscreen Bottom")
    ev_tp = find_event_num("Emulated Touchpad")
    if ev_ts is None or ev_tp is None:
        print(f"ERROR: event devices not found. ts={ev_ts} tp={ev_tp}")
        sys.exit(1)
    flush_stuck_touches()

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"ERROR: Device {VID:04x}:{PID:04x} not found")
        sys.exit(1)

    start_time = time.time()
    stop_all = threading.Event()
    def elapsed():
        return time.time() - start_time

    print(f"Monitor: event{ev_ts} (touchscreen), event{ev_tp} (touchpad)")
    print(f"[{elapsed():.2f}s] Found {VID:04x}:{PID:04x}")
    print()

    # ══════════════════════════════════════════════════════════════════
    # STEP 1: Claim IF1 (OSKP bulk) — like YB9.Service.exe opening device
    # ══════════════════════════════════════════════════════════════════
    print(f"[{elapsed():.2f}s] STEP 1: Claim IF1 (OSKP bulk)")
    if dev.is_kernel_driver_active(IF1):
        dev.detach_kernel_driver(IF1)
    usb.util.claim_interface(dev, IF1)
    print(f"[{elapsed():.2f}s]   IF1 claimed")

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
    print(f"[{elapsed():.2f}s]   EP 0x81 reader started")

    # SET_INTERFACE for IF1 — Windows USB stack sends this when binding driver
    try:
        usb.util.set_interface(dev, IF1, 0)
        print(f"[{elapsed():.2f}s]   SET_INTERFACE(IF1, altsetting 0) OK")
    except Exception as e:
        print(f"[{elapsed():.2f}s]   SET_INTERFACE(IF1, 0): {e}")

    # ══════════════════════════════════════════════════════════════════
    # STEP 2: Claim IF0 (CDC ACM) — like usbser.sys / Lenovo OSK Partner
    # ══════════════════════════════════════════════════════════════════
    print(f"\n[{elapsed():.2f}s] STEP 2: Claim IF0 (CDC ACM)")
    if0_claimed = False
    try:
        if dev.is_kernel_driver_active(IF0):
            dev.detach_kernel_driver(IF0)
        usb.util.claim_interface(dev, IF0)
        if0_claimed = True

        # SET_INTERFACE — Windows USB stack sends this when binding driver
        try:
            usb.util.set_interface(dev, IF0, 0)
            print(f"[{elapsed():.2f}s]   SET_INTERFACE(IF0, altsetting 0) OK")
        except Exception as e:
            print(f"[{elapsed():.2f}s]   SET_INTERFACE(IF0, 0): {e}")

        # SET_LINE_CODING — Windows uses 115200 baud (from Capture 10 frame 163)
        line_coding = struct.pack("<IBBB", args.baud, 0, 0, 8)
        dev.ctrl_transfer(0x21, SET_LINE_CODING, 0, IF0, line_coding)
        print(f"[{elapsed():.2f}s]   SET_LINE_CODING({args.baud}, 8N1) OK")

        # SET_CONTROL_LINE_STATE — DTR=1, RTS=1 (like opening COM port)
        dev.ctrl_transfer(0x21, SET_CONTROL_LINE_STATE, 0x03, IF0, b"")
        print(f"[{elapsed():.2f}s]   SET_CONTROL_LINE_STATE(DTR=1, RTS=1) OK")

        # EP 0x82 reader (CDC ACM serial state notifications)
        def ep82_reader():
            while not stop_all.is_set():
                try:
                    data = dev.read(EP0_INT, 10, timeout=1000)
                    if data:
                        print(f"  [{elapsed():6.2f}s] EP0x82 CDC notify: {bytes(data).hex()}")
                except usb.core.USBError:
                    pass
                except Exception:
                    time.sleep(0.1)
        threading.Thread(target=ep82_reader, daemon=True).start()
        print(f"[{elapsed():.2f}s]   EP 0x82 reader started")

    except Exception as e:
        print(f"[{elapsed():.2f}s]   IF0 claim failed: {e}")

    # ══════════════════════════════════════════════════════════════════
    # STEP 3: YB9.Service.exe init sequence (from decompilation)
    #   - 0x25 [0x00] — init toggle (touchpad OFF)
    #   - 0x31 [33 zero bytes] — init config (clear geometry)
    # ══════════════════════════════════════════════════════════════════
    if not args.skip_init:
        print(f"\n[{elapsed():.2f}s] STEP 3: YB9.Service.exe init sequence")
        print(f"[{elapsed():.2f}s]   >>> OSKP 0x25 [0x00] (init toggle — touchpad OFF)")
        send_oskp(dev, 0x25, bytes([0x00]))
        time.sleep(0.1)

        print(f"[{elapsed():.2f}s]   >>> OSKP 0x31 [33 zeros] (init config — clear geometry)")
        send_oskp(dev, 0x31, bytes(33))
        time.sleep(0.1)
        print(f"[{elapsed():.2f}s]   Init complete")
    else:
        print(f"\n[{elapsed():.2f}s] STEP 3: SKIPPED (--skip-init)")

    # ══════════════════════════════════════════════════════════════════
    # STEP 4: KeepConnectThread (from decompilation)
    #   - Sleep 1500ms
    #   - OSKP 0x26 with timestamp — sent ONCE
    # ══════════════════════════════════════════════════════════════════
    if not args.skip_0x26:
        print(f"\n[{elapsed():.2f}s] STEP 4: KeepConnectThread (sleep 1500ms...)")
        time.sleep(1.5)
        print(f"[{elapsed():.2f}s]   >>> OSKP 0x26 with timestamp")
        send_oskp(dev, 0x26, ts_bytes())
        print(f"[{elapsed():.2f}s]   0x26 sent (once, like Windows)")
    else:
        print(f"\n[{elapsed():.2f}s] STEP 4: SKIPPED (--skip-0x26)")

    # ══════════════════════════════════════════════════════════════════
    # STEP 5: Activation sequence (from Capture 10, frames 157-164)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"[{elapsed():.2f}s] STEP 5: ACTIVATION (Capture 10 sequence)")
    print(f"{'='*60}")

    # 5a: SET_IDLE on IF2 (frame 157 — STALLed, expected)
    print(f"\n[{elapsed():.2f}s] 5a: SET_IDLE on IF2 (will STALL — expected)")
    try:
        dev.ctrl_transfer(0x21, SET_IDLE, 0, IF2, b"")
        print(f"[{elapsed():.2f}s]     SET_IDLE OK (unexpected)")
    except usb.core.USBError:
        print(f"[{elapsed():.2f}s]     SET_IDLE STALLed (expected, like Windows)")

    # 5b: Claim IF2 IF using raw USB for HID toggle
    if2_claimed = False
    if not args.hidraw and not args.skip_hid:
        print(f"[{elapsed():.2f}s] 5b: Claim IF2 for HID toggle")
        if dev.is_kernel_driver_active(IF2):
            dev.detach_kernel_driver(IF2)
        usb.util.claim_interface(dev, IF2)
        if2_claimed = True
        print(f"[{elapsed():.2f}s]     IF2 claimed")

        # SET_INTERFACE — Windows USB stack sends this for HID interfaces
        try:
            usb.util.set_interface(dev, IF2, 0)
            print(f"[{elapsed():.2f}s]     SET_INTERFACE(IF2, 0) OK")
        except Exception as e:
            print(f"[{elapsed():.2f}s]     SET_INTERFACE(IF2, 0): {e}")

        # SET_PROTOCOL(Report) — Windows HID class driver sends this
        try:
            dev.ctrl_transfer(0x21, 0x0B, 1, IF2, b"")  # SET_PROTOCOL, Report=1
            print(f"[{elapsed():.2f}s]     SET_PROTOCOL(Report) OK")
        except usb.core.USBError as e:
            print(f"[{elapsed():.2f}s]     SET_PROTOCOL(Report): {e}")

        # EP 0x83 reader (keyboard HID data — frame 159)
        def ep83_reader():
            while not stop_all.is_set():
                try:
                    data = dev.read(EP2_IN, 64, timeout=500)
                    if data and len(data) > 0:
                        pass  # Don't spam, just drain
                except usb.core.USBError:
                    pass
                except Exception:
                    pass
        threading.Thread(target=ep83_reader, daemon=True).start()

    # 5c: Re-send SET_LINE_CODING (Windows re-sends at activation, frame 163)
    print(f"[{elapsed():.2f}s] 5c: Re-send SET_LINE_CODING({args.baud})")
    if if0_claimed:
        line_coding = struct.pack("<IBBB", args.baud, 0, 0, 8)
        try:
            dev.ctrl_transfer(0x21, SET_LINE_CODING, 0, IF0, line_coding)
            print(f"[{elapsed():.2f}s]     OK")
        except Exception as e:
            print(f"[{elapsed():.2f}s]     Failed: {e}")

    # 5d: THE MODE TOGGLE — HID [0x20, 0x00] (frame 164)
    if not args.skip_hid:
        if args.hidraw:
            # Send via hidraw (keep kernel HID driver on IF2)
            hidraw_if2 = find_hidraw_for_interface(2)
            if hidraw_if2:
                print(f"\n[{elapsed():.2f}s] 5d: HID [0x20, 0x00] via {hidraw_if2}")
                try:
                    hidraw_fd = os.open(hidraw_if2, os.O_RDWR | os.O_NONBLOCK)
                    written = os.write(hidraw_fd, bytes([0x20, 0x00]))
                    print(f"[{elapsed():.2f}s]     OK! Wrote {written} bytes")
                    os.close(hidraw_fd)
                except Exception as e:
                    print(f"[{elapsed():.2f}s]     Failed: {e}")
            else:
                print(f"\n[{elapsed():.2f}s] 5d: No hidraw for IF2, trying raw USB")
                if if2_claimed:
                    try:
                        dev.write(EP2_OUT, bytes([0x20, 0x00]), timeout=TIMEOUT)
                        print(f"[{elapsed():.2f}s]     Raw USB OK!")
                    except usb.core.USBError as e:
                        print(f"[{elapsed():.2f}s]     Raw USB failed: {e}")
        else:
            # Send via raw USB (like test-hid-toggle.py)
            print(f"\n[{elapsed():.2f}s] 5d: HID [0x20, 0x00] via raw USB EP 0x02")
            try:
                dev.write(EP2_OUT, bytes([0x20, 0x00]), timeout=TIMEOUT)
                print(f"[{elapsed():.2f}s]     OK!")
            except usb.core.USBError as e:
                print(f"[{elapsed():.2f}s]     Interrupt OUT failed: {e}")
                print(f"[{elapsed():.2f}s]     Trying SET_REPORT fallback...")
                try:
                    dev.ctrl_transfer(0x21, 0x09, 0x0200, IF2,
                                      bytes([0x20, 0x00]))
                    print(f"[{elapsed():.2f}s]     SET_REPORT OK!")
                except usb.core.USBError as e2:
                    print(f"[{elapsed():.2f}s]     SET_REPORT failed: {e2}")
    else:
        # Fallback: OSKP 0x25 01 (our proven Linux activation)
        print(f"\n[{elapsed():.2f}s] 5d: OSKP 0x25 [0x01] (fallback activation)")
        send_oskp(dev, 0x25, bytes([0x01]))
        print(f"[{elapsed():.2f}s]     Sent")

    time.sleep(0.35)  # MCU takes ~350ms to flood EP 0x84 (from Capture 10)

    # ══════════════════════════════════════════════════════════════════
    # STEP 6: Post-activation (from Capture 10, frames 253-257)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n[{elapsed():.2f}s] STEP 6: Post-activation")

    # 0x20 sync (frame 253)
    print(f"[{elapsed():.2f}s]   >>> OSKP 0x20 sync")
    send_oskp(dev, 0x20, bytes([0x01, 0x00]))
    time.sleep(0.04)

    # 0x31 geometry (frame 257)
    geo = build_touchpad_geometry()
    print(f"[{elapsed():.2f}s]   >>> OSKP 0x31 geometry ({len(geo)}B)")
    send_oskp(dev, 0x31, geo)

    # ══════════════════════════════════════════════════════════════════
    # STEP 7: Steady state — 0x20 sync every 1000ms (like Windows)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n[{elapsed():.2f}s] STEP 7: Steady state — 0x20 sync every {SYNC_INTERVAL*1000:.0f}ms")
    print(f"\n=== Monitoring for {args.duration}s — touch the bottom screen! ===\n")

    tick = 0
    sync_errors = 0

    def sync_thread():
        nonlocal tick, sync_errors
        while not stop_all.is_set():
            stop_all.wait(SYNC_INTERVAL)
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

    # Monitor events
    hid_stop = threading.Event()
    ts_events = []
    tp_events = []
    threading.Thread(
        target=monitor_hid_events, args=(ev_ts, hid_stop, ts_events),
        daemon=True).start()
    threading.Thread(
        target=monitor_hid_events, args=(ev_tp, hid_stop, tp_events),
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

    # ══════════════════════════════════════════════════════════════════
    # Results
    # ══════════════════════════════════════════════════════════════════
    t = elapsed()
    print(f"\n{'='*60}")
    print(f"Results after {t:.1f}s:")
    print(f"  Touchpad events:    {len(tp_events)}")
    print(f"  Touchscreen events: {len(ts_events)}")
    print(f"  OSKP responses:     {ep81_count}")
    print(f"  Sync ticks:         {tick}")
    print(f"  Sync errors:        {sync_errors}")
    if tp_events:
        print(f"  Touchpad window: {tp_events[0]-start_time:.2f}s — {tp_events[-1]-start_time:.2f}s")
    if ts_events:
        print(f"  First touchscreen: {ts_events[0]-start_time:.2f}s")
    if tp_events and not ts_events:
        print(f"\n  ✓✓✓ TOUCHPAD HELD for full {args.duration}s!")
    elif tp_events and ts_events:
        revert_time = ts_events[0] - start_time
        print(f"\n  ✗ REVERTED at {revert_time:.1f}s")
    else:
        print(f"\n  ? No touch input detected")
    print(f"{'='*60}")

    # Cleanup
    print(f"\n[{elapsed():.2f}s] Cleaning up...")
    stop_all.set()
    hid_stop.set()

    try:
        send_oskp(dev, 0x31, bytes(41))
    except Exception:
        pass

    if if2_claimed:
        try:
            usb.util.release_interface(dev, IF2)
            dev.attach_kernel_driver(IF2)
        except Exception:
            pass

    if if0_claimed:
        try:
            dev.ctrl_transfer(0x21, SET_CONTROL_LINE_STATE, 0x00, IF0, b"")
            usb.util.release_interface(dev, IF0)
        except Exception:
            pass

    try:
        usb.util.release_interface(dev, IF1)
        dev.attach_kernel_driver(IF1)
    except Exception:
        pass

    usb.util.dispose_resources(dev)
    flush_stuck_touches()
    print(f"  Done.\n")


if __name__ == "__main__":
    main()
