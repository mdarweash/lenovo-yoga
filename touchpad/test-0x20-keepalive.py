#!/usr/bin/env python3
"""
test-0x20-keepalive.py — Test if OSKP 0x20 sync (from Windows captures) keeps touchpad alive.

Hypothesis:
  - OSKP 0x25 01 activates touchpad (proven, works for ~7s)
  - OSKP 0x26 keepalive does NOT prevent revert (proven, fails)
  - Windows captures show 0x20 sync every 1s, never 0x26
  - Maybe 0x20 sync IS the correct keepalive that prevents revert

This test:
  1. Activates with 0x25 01 (known to work)
  2. Sends 0x20 sync every 1s (NOT 0x26) as keepalive
  3. Sends 0x31 geometry once
  4. Monitors for 30s — no periodic re-activation, no 0x26

If touchpad holds past 7s → root cause is keepalive type (0x20 vs 0x26).
If it still reverts at ~7s → root cause is elsewhere.

Usage:
    sudo python3 test-0x20-keepalive.py [--duration 30] [--old-keepalive]
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

VID = 0x17ef
PID = 0x6161
IF1 = 1
EP1_OUT = 0x01
EP1_IN = 0x81
TIMEOUT = 5000

INPUT_EVENT_SIZE = struct.calcsize("llHHi")
EV_ABS = 0x03


def build_oskp(ptype, payload=b""):
    frame = b"OSKP"
    frame += struct.pack("<H", len(payload) + 1)
    frame += bytes([ptype])
    frame += payload
    return frame


def send_oskp(dev, ptype, payload=b""):
    dev.write(EP1_OUT, build_oskp(ptype, payload), timeout=TIMEOUT)


def ts_bytes():
    from datetime import datetime
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


def main():
    parser = argparse.ArgumentParser(
        description="Test 0x20 sync keepalive vs 0x26 timestamp keepalive")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--old-keepalive", action="store_true",
                        help="Use old 0x26 keepalive for comparison")
    parser.add_argument("--no-if0", action="store_true",
                        help="Skip IF0 CDC ACM session")
    parser.add_argument("--baud", type=int, default=9600,
                        help="SET_LINE_CODING baud rate (default: 9600)")
    parser.add_argument("--keepalive-ms", type=int, default=1000,
                        help="Keepalive interval in ms (default: 1000)")
    args = parser.parse_args()

    ev_ts = find_event_num("Touchscreen Bottom")
    ev_tp = find_event_num("Emulated Touchpad")
    if ev_ts is None or ev_tp is None:
        print(f"ERROR: event devices not found. ts={ev_ts} tp={ev_tp}")
        sys.exit(1)

    flush_stuck_touches()

    # Check USB autosuspend status
    for d in sorted(os.listdir("/sys/bus/usb/devices/")):
        try:
            with open(f"/sys/bus/usb/devices/{d}/idVendor") as f:
                if f.read().strip() != f"{VID:04x}":
                    continue
            with open(f"/sys/bus/usb/devices/{d}/idProduct") as f:
                if f.read().strip() != f"{PID:04x}":
                    continue
            auto = "(unknown)"
            try:
                with open(f"/sys/bus/usb/devices/{d}/power/autosuspend") as f:
                    auto = f.read().strip()
            except Exception:
                pass
            ctrl = "(unknown)"
            try:
                with open(f"/sys/bus/usb/devices/{d}/power/control") as f:
                    ctrl = f.read().strip()
            except Exception:
                pass
            print(f"USB autosuspend: {d} control={ctrl} autosuspend={auto}s")
            if ctrl == "auto":
                print(f"  ⚠ USB autosuspend is ON — may cause MCU timeout!")
                print(f"  Fix: echo on | sudo tee /sys/bus/usb/devices/{d}/power/control")
        except Exception:
            pass

    keepalive_type = "0x26 OLD" if args.old_keepalive else "0x20 WINDOWS"
    print(f"=== Touchpad keepalive test: {keepalive_type} ===")
    print(f"Monitor: event{ev_ts} (touchscreen), event{ev_tp} (touchpad)")
    print()

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"ERROR: Device {VID:04x}:{PID:04x} not found")
        sys.exit(1)

    if dev.is_kernel_driver_active(IF1):
        dev.detach_kernel_driver(IF1)
    usb.util.claim_interface(dev, IF1)
    print(f"IF1 claimed")

    # Check IF1 altsettings
    try:
        for altsetting in dev.get_active_configuration()[(IF1, 0)].alternates:
            eps = ", ".join(
                f"EP0x{e.bEndpointAddress:02x}"
                f"{'IN' if e.bEndpointAddress & 0x80 else 'OUT'}"
                for e in altsetting.endpoints
            )
            print(f"  IF1 altsetting {altsetting.bAlternateSetting}: "
                  f"class=0x{altsetting.bInterfaceClass:02x}  {eps}")
    except Exception as e:
        print(f"  Could not enumerate altsettings: {e}")

    # Try setting IF1 to altsetting 1 (if it has one)
    try:
        # intf = cfg[(IF1, 0)] — try setting altsetting 1
        dev.set_interface_altsetting(IF1, 1)
        print(f"  IF1 set to altsetting 1")
    except usb.core.USBError as e:
        if e.errno == 32 or "pipe" in str(e).lower():
            pass  # altsetting 1 doesn't exist or same as 0
        else:
            print(f"  set_interface_altsetting(IF1, 1) failed: {e}")
    except Exception as e:
        pass  # altsetting 1 may not exist

    start_time = time.time()
    stop_all = threading.Event()

    def elapsed():
        return time.time() - start_time

    # ── Claim IF0 (CDC ACM session — matches Windows) ───────────────
    if0_claimed = False
    if not args.no_if0:
        print(f"\n[{elapsed():.2f}s] Claiming IF0 (CDC ACM session)...")
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
            usb.util.claim_interface(dev, 0)
            if0_claimed = True

            # SET_LINE_CODING (Windows uses 115200; 9600 is safe default)
            line_coding = struct.pack("<IBBB", args.baud, 0, 0, 8)
            try:
                dev.ctrl_transfer(0x21, 0x20, 0, 0, line_coding)
                print(f"  SET_LINE_CODING({args.baud}, 8N1) OK")
            except usb.core.USBError as e:
                print(f"  SET_LINE_CODING failed: {e}")

            # SET_CONTROL_LINE_STATE: DTR=1, RTS=1
            try:
                dev.ctrl_transfer(0x21, 0x22, 0x03, 0, b"")
                print(f"  SET_CONTROL_LINE_STATE(DTR=1, RTS=1) OK")
            except usb.core.USBError as e:
                print(f"  SET_CONTROL_LINE_STATE failed: {e}")

            # EP 0x82 interrupt reader (serial state notifications)
            def ep82_reader():
                while not stop_all.is_set():
                    try:
                        data = dev.read(0x82, 10, timeout=1000)
                        if data:
                            print(f"  [{elapsed():6.2f}s] EP0x82: {bytes(data).hex()}")
                    except usb.core.USBError:
                        pass
                    except Exception:
                        time.sleep(0.1)
            threading.Thread(target=ep82_reader, daemon=True).start()
            print(f"  EP 0x82 reader started")

            # EP 0x84 bulk IN reader (MCU serial data / touch data)
            # Windows ReadThread continuously drains this via ReadFile.
            # If we don't read it, MCU TX buffer fills → session timeout.
            ep84_count = 0
            def ep84_reader():
                nonlocal ep84_count
                while not stop_all.is_set():
                    try:
                        data = dev.read(0x84, 512, timeout=500)
                        if data and len(data) > 0:
                            ep84_count += 1
                            t = elapsed()
                            if ep84_count <= 5:
                                print(f"  [{t:6.2f}s] EP0x84 ({len(data)}B): {bytes(data)[:20].hex()}...")
                    except usb.core.USBError:
                        pass
                    except Exception:
                        pass
            threading.Thread(target=ep84_reader, daemon=True).start()
            print(f"  EP 0x84 reader started")
        except Exception as e:
            print(f"  IF0 claim failed (non-fatal): {e}")

    # ── EP 0x81 reader ───────────────────────────────────────────────
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
                                    print(f"  [{t:6.2f}s] MCU 0xa2: {payload.hex()}")
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

    # ── Activation: proven 0x25 01 sequence ─────────────────────────
    print(f"\n[{elapsed():.2f}s] Activating touchpad (0x25 01)...")

    send_oskp(dev, 0x25, bytes([0x01]))
    time.sleep(0.05)

    send_oskp(dev, 0x20, bytes([0x01, 0x00]))
    time.sleep(0.05)

    send_oskp(dev, 0x21, bytes([0x7e, 0x01, 0x00, 0x00, 0x00]))
    time.sleep(0.05)

    # Screen info
    send_oskp(dev, 0x21, struct.pack("<BHH", 0x40, 3017, 1700))
    time.sleep(0.03)
    send_oskp(dev, 0x21, struct.pack("<BHH", 0x41, 3017, 1700))
    time.sleep(0.03)
    send_oskp(dev, 0x21, bytes([0x31, 0, 0, 0, 0]))
    time.sleep(0.03)
    send_oskp(dev, 0xa3, bytes([0x01]))
    time.sleep(0.03)

    # Geometry
    geo = build_touchpad_geometry()
    send_oskp(dev, 0x31, geo)
    time.sleep(0.05)

    print(f"[{elapsed():.2f}s] Activation sequence sent")

    # ── Keepalive ────────────────────────────────────────────────────
    tick = 0
    def keepalive_loop():
        nonlocal tick
        time.sleep(1.0)
        while not stop_all.is_set():
            tick += 1
            try:
                if args.old_keepalive:
                    send_oskp(dev, 0x26, ts_bytes())
                else:
                    send_oskp(dev, 0x20, bytes([0x01, 0x00]))
            except Exception as e:
                print(f"  [{elapsed():6.2f}s] keepalive FAILED: {e}")
            ka_ms = args.keepalive_ms if not args.old_keepalive else 1500
            stop_all.wait(ka_ms / 1000.0)

    threading.Thread(target=keepalive_loop, daemon=True).start()
    ka_ms = args.keepalive_ms if not args.old_keepalive else 1500
    ka_label = f"0x26 every {ka_ms}ms" if args.old_keepalive else f"0x20 every {ka_ms}ms"
    print(f"[{elapsed():.2f}s] Keepalive started: {ka_label}")
    print(f"\n=== Monitoring for {args.duration}s — touch the bottom screen! ===\n")

    # ── Monitor ──────────────────────────────────────────────────────
    hid_stop = threading.Event()
    ts_events = []
    tp_events = []
    threading.Thread(target=monitor_hid_events,
                     args=(ev_ts, hid_stop, ts_events), daemon=True).start()
    threading.Thread(target=monitor_hid_events,
                     args=(ev_tp, hid_stop, tp_events), daemon=True).start()

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

    # ── Results ──────────────────────────────────────────────────────
    t = elapsed()
    print(f"\n{'='*60}")
    print(f"Results ({ka_label}):")
    print(f"  Touchpad events (event{ev_tp}):    {len(tp_events)}")
    print(f"  Touchscreen events (event{ev_ts}): {len(ts_events)}")
    print(f"  OSKP responses:                    {ep81_count}")
    print(f"  Keepalive ticks:                   {tick}")
    if tp_events:
        print(f"  Touchpad window: "
              f"{tp_events[0]-start_time:.2f}s — "
              f"{tp_events[-1]-start_time:.2f}s")
    if ts_events:
        print(f"  First revert: {ts_events[0]-start_time:.2f}s")
    if tp_events and not ts_events:
        print(f"\n  ✓✓✓ TOUCHPAD HELD for full {args.duration}s!")
        print(f"  ✓✓✓ {keepalive_type} keepalive WORKS!")
    elif tp_events and ts_events:
        revert_t = ts_events[0] - start_time
        print(f"\n  ✗ REVERTED at {revert_t:.1f}s")
        if args.old_keepalive:
            print(f"  → Confirms 0x26 doesn't prevent revert")
        else:
            print(f"  → 0x20 sync alone doesn't prevent revert either")
    print(f"{'='*60}")

    # ── Cleanup ──────────────────────────────────────────────────────
    stop_all.set()
    hid_stop.set()

    print(f"\nRestoring...")
    try:
        send_oskp(dev, 0x21, bytes([0x7e, 0x00, 0x00, 0x00, 0x00]))
        time.sleep(0.05)
    except Exception:
        pass
    try:
        send_oskp(dev, 0x25, bytes([0x00]))
        time.sleep(0.05)
    except Exception:
        pass
    try:
        if if0_claimed:
            dev.ctrl_transfer(0x21, 0x22, 0x00, 0, b"")
            usb.util.release_interface(dev, 0)
            print(f"  IF0 released")
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
