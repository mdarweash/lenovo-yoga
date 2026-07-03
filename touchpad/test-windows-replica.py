#!/usr/bin/env python3
"""
test-windows-replica.py — Faithful replication of YB9.Service.exe connect + touchpad.

Copies the exact Windows sequence from Ghidra decompilation:

1. Claim interface 0 (CDC ACM control)
2. SET_LINE_CODING(9600, 8N1) — safe per test-set-line-coding.py results
3. SET_CONTROL_LINE_STATE(DTR=1, RTS=1)
4. Claim interface 1, send OSKP frames through bulk (same as WriteFile)
5. Init: 0x25 [00] + 0x31 [33 zeros]
6. Keepalive thread: 0x26 every 1500ms (matching KeepConnectThread)
7. Touchpad on: 0x21 7e01000000

Then monitors touchpad mode for 30s via event16/event19 HID events.

Usage:
    sudo python3 test-windows-replica.py [--duration 30]
"""
import sys, os, time, struct, threading, select, signal, argparse
import usb.core, usb.util

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "windows", "rd"))
from yb9_usb import (send_frame, send_frame_bytes, get_device, release_device,
                     EP_IN, wait_for_device, reset_device, VID, PID)

# ─── Constants ────────────────────────────────────────────────────────
IFACE0 = 0
IFACE1 = 1
SET_LINE_CODING = 0x20
SET_CONTROL_LINE_STATE = 0x22

INPUT_EVENT_SIZE = struct.calcsize("llHHi")
EV_ABS = 0x03

# ─── Helpers ──────────────────────────────────────────────────────────

def ts_bytes():
    from datetime import datetime
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

# ─── Interface 0 CDC ACM session ──────────────────────────────────────

_iface0_dev = None
_iface0_claimed = False

def setup_iface0_session():
    """
    Replicate Windows: SetupComm + SetCommState + PurgeComm.
    
    1. Claim interface 0
    2. SET_LINE_CODING(9600, 8N1) — matches Windows default DCB
    3. SET_CONTROL_LINE_STATE(DTR=1, RTS=1)
    """
    global _iface0_dev, _iface0_claimed
    
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("Device not found")
    
    _iface0_dev = dev
    
    # Detach kernel driver if bound (cdc_acm)
    if dev.is_kernel_driver_active(IFACE0):
        print("  Detaching kernel driver from interface 0...")
        dev.detach_kernel_driver(IFACE0)
    
    # Claim interface 0
    usb.util.claim_interface(dev, IFACE0)
    _iface0_claimed = True
    print("  Claimed interface 0")
    
    # SET_LINE_CODING: 9600 baud, 8 data bits, no parity, 1 stop bit
    # This matches Windows default DCB (SetCommState)
    line_coding = struct.pack("<IBBB", 9600, 0, 0, 8)  # baud, stop=0(1bit), parity=0(none), databits=8
    dev.ctrl_transfer(0x21, SET_LINE_CODING, 0, IFACE0, line_coding)
    print("  SET_LINE_CODING(9600, 8N1)")
    
    # SET_CONTROL_LINE_STATE: DTR=1, RTS=1
    # Windows asserts these when opening the COM port
    dev.ctrl_transfer(0x21, SET_CONTROL_LINE_STATE, 0x03, IFACE0, b"")
    print("  SET_CONTROL_LINE_STATE(DTR=1, RTS=1)")

def teardown_iface0_session():
    global _iface0_dev, _iface0_claimed
    if _iface0_dev is not None and _iface0_claimed:
        try:
            # Deassert DTR/RTS
            _iface0_dev.ctrl_transfer(0x21, SET_CONTROL_LINE_STATE, 0x00, IFACE0, b"")
        except Exception:
            pass
        try:
            usb.util.release_interface(_iface0_dev, IFACE0)
        except Exception:
            pass
        _iface0_claimed = False

# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Windows YB9.Service.exe replica test")
    parser.add_argument("--duration", type=int, default=30, help="Monitor duration (default: 30s)")
    parser.add_argument("--no-touchpad", action="store_true", help="Only init + keepalive, no touchpad mode")
    args = parser.parse_args()

    ev16 = find_event_num("Touchscreen Bottom")
    ev19 = find_event_num("Emulated Touchpad")
    if ev16 is None or ev19 is None:
        print(f"ERROR: event devices not found. ev16={ev16} ev19={ev19}")
        sys.exit(1)

    flush_stuck_touches()
    print(f"Monitor: event{ev16} (touchscreen), event{ev19} (touchpad)\n")

    # ── Step 1: Setup interface 0 CDC session (Windows: CreateFile + SetCommState) ──
    print("=== Step 1: CDC ACM session on interface 0 (Windows: CreateFileW) ===")
    setup_iface0_session()

    # ── Step 2: Claim interface 1 for OSKP bulk (Windows: WriteFile/ReadFile) ──
    print("\n=== Step 2: Claim interface 1 for OSKP bulk ===")
    wait_for_device(timeout=5)
    print("  Interface 1 claimed")

    start_time = time.time()
    stop_all = threading.Event()

    # MCU reader thread — bulk IN EP 0x81 (Windows: ReadFile on serial)
    def reader():
        while not stop_all.is_set():
            try:
                dev = get_device()
                data = dev.read(EP_IN, 512, timeout=200)
                if not data:
                    continue
                # Parse OSKP frames for logging
                pos = 0
                raw = bytes(data)
                while pos + 6 <= len(raw):
                    if raw[pos:pos+4] == b"OSKP":
                        wlen = struct.unpack("<H", raw[pos+4:pos+6])[0]
                        if pos + 6 + wlen <= len(raw):
                            ftype = raw[pos+6]
                            payload = raw[pos+7:pos+6+wlen]
                            elapsed = time.time() - start_time
                            if ftype == 0x75:
                                print(f"  [{elapsed:6.2f}s] MCU -> 0x75 ACK: {payload.hex()}")
                            elif ftype == 0x50:
                                ver = payload[4:].decode('ascii', errors='replace').strip()
                                print(f"  [{elapsed:6.2f}s] MCU -> 0x50 FW: {ver}")
                            elif ftype not in (0xa2, 0x26):
                                print(f"  [{elapsed:6.2f}s] MCU -> 0x{ftype:02x} {payload.hex()[:40]}")
                            pos += 6 + wlen
                        else:
                            break
                    else:
                        pos += 1
            except Exception:
                pass

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    # CDC interrupt reader — EP 0x82 on interface 0 (Windows: WaitCommEvent)
    # Windows ReadThread continuously polls this for serial state notifications.
    def interrupt_reader():
        while not stop_all.is_set():
            try:
                if _iface0_dev is not None:
                    data = _iface0_dev.read(0x82, 10, timeout=1000)
                    if data:
                        elapsed = time.time() - start_time
                        print(f"  [{elapsed:6.2f}s] EP82 interrupt: {bytes(data).hex()}")
            except Exception:
                time.sleep(0.1)

    intr_thread = threading.Thread(target=interrupt_reader, daemon=True)
    intr_thread.start()
    print("  EP 0x82 interrupt reader started")

    # ── Step 3: Send init commands (Windows: write thread init) ──
    print("\n=== Step 3: Init commands (Windows: write thread startup) ===")
    # a. type=0x25, payload=[0x00]
    send_frame(0x25, "00")
    print("  0x25 00 (init toggle)")
    time.sleep(0.05)
    # b. type=0x31, payload=[33 zero bytes]
    send_frame(0x31, "00" * 33)
    print("  0x31 [33 zeros] (init config)")

    # ── Step 4: Start KeepConnectThread (Windows: 0x26 every 1500ms) ──
    print("\n=== Step 4: KeepConnectThread (0x26 every 1500ms) ===")
    def keepalive_thread():
        time.sleep(1.5)  # Initial sleep after connect (matches Windows)
        while not stop_all.is_set():
            try:
                send_frame(0x26, ts_bytes().hex())
            except Exception:
                pass
            stop_all.wait(1.5)  # Sleep(1500ms)

    ka_thread = threading.Thread(target=keepalive_thread, daemon=True)
    ka_thread.start()
    print("  Keepalive thread started (1500ms interval)")

    # ── Step 4b: ResendTouchPadRect (Windows TouchPad app periodic geometry) ──
    if not args.no_touchpad:
        def resend_rect_thread():
            # Start re-sending immediately after activation (step 5)
            time.sleep(1.0)  # Wait for step 5 to complete
            while not stop_all.is_set():
                try:
                    # Re-send touchpad state + geometry (NO 0x25 01 — that causes revert!)
                    send_frame(0x21, "7e01000000")
                    send_frame_bytes(0x21, struct.pack("<BHH", 0x40, 3017, 1700))
                    send_frame_bytes(0x21, struct.pack("<BHH", 0x41, 3017, 1700))
                    send_frame_bytes(0x31, build_0x31())
                except Exception:
                    pass
                stop_all.wait(3.0)  # Re-send every 3s

        rect_thread = threading.Thread(target=resend_rect_thread, daemon=True)
        rect_thread.start()
        print("  ResendTouchPadRect thread started (3s interval)")

    # ── Step 5: Touchpad activation (known-working sequence from test-ack-reply.py) ──
    if not args.no_touchpad:
        print("\n=== Step 5: Touchpad activation ===")
        time.sleep(0.5)

        # Full init (matches working tests)
        send_frame(0x4b, "0008b80b10271027"); time.sleep(0.03)
        send_frame(0x21, "8001010000");        time.sleep(0.03)
        send_frame(0x28, "0000");              time.sleep(0.03)
        send_frame(0x26, ts_bytes().hex());    time.sleep(0.1)

        # Activate touchpad mode
        send_frame(0x25, "01");                                        time.sleep(0.03)
        send_frame(0x20, "0100");                                      time.sleep(0.03)
        send_frame(0x21, "7e01000000");                                time.sleep(0.03)

        # Screen info
        send_frame_bytes(0x21, struct.pack("<BHH", 0x40, 3017, 1700));  time.sleep(0.03)
        send_frame_bytes(0x21, struct.pack("<BHH", 0x41, 3017, 1700));  time.sleep(0.03)
        send_frame_bytes(0x21, struct.pack("<BBBBB", 0x31, 0, 0, 0, 0));time.sleep(0.03)
        send_frame_bytes(0xa3, b"\x01");                                time.sleep(0.03)

        # Geometry
        send_frame_bytes(0x31, build_0x31())
        send_frame(0x26, ts_bytes().hex())
        print("  Full activation sequence sent")

    # ── Step 6: Monitor ──
    print(f"\n=== Step 6: Monitor for {args.duration}s ===")
    print("  Touch the bottom screen every 2-3 seconds!\n")

    hid_stop = threading.Event()
    ts_events = []
    tp_events = []
    threading.Thread(target=monitor_hid_events, args=(ev16, hid_stop, ts_events), daemon=True).start()
    threading.Thread(target=monitor_hid_events, args=(ev19, hid_stop, tp_events), daemon=True).start()

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

    # ── Results ──
    elapsed = time.time() - start_time
    print(f"\n--- Results ---")
    print(f"  Duration: {elapsed:.1f}s")
    print(f"  Touchpad events (event19):    {len(tp_events)}")
    print(f"  Touchscreen events (event16): {len(ts_events)}")
    if tp_events:
        print(f"  Touchpad window: {tp_events[0]-start_time:.2f}s — {tp_events[-1]-start_time:.2f}s")
    if ts_events:
        print(f"  Touchscreen onset: {ts_events[0]-start_time:.2f}s")
    if tp_events and not ts_events:
        print(f"  ✓ TOUCHPAD HELD for full {args.duration}s!")
    elif tp_events and ts_events:
        print(f"  ✗ REVERTED at {ts_events[0]-start_time:.1f}s")
    else:
        print(f"  ? No touch input")

    # ── Cleanup ──
    stop_all.set()
    hid_stop.set()
    reader_thread.join(timeout=2)
    ka_thread.join(timeout=2)

    print("\nRestoring...")
    if not args.no_touchpad:
        try:
            send_frame(0x21, "7e00000000")
            time.sleep(0.1)
        except Exception:
            pass

    teardown_iface0_session()
    flush_stuck_touches()

    try:
        release_device()
    except Exception:
        pass
    print("  Done.\n")

if __name__ == "__main__":
    main()
