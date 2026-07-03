#!/usr/bin/env python3
"""
test-ack-reply.py — Test if replying to MCU 0x75 acknowledgment prevents mode revert.

Strategy: activate touchpad, send geometry immediately (no blocking wait),
reply to 0x75 in background reader the instant it arrives. Mode detection
uses HID input events on event16 (touchscreen) vs event19 (touchpad).

Usage:
    sudo python3 test-ack-reply.py [variant]

Variants:
    1  — Echo 0x75 payload back to MCU
    2  — Send 0x75 01 (minimal)
    3  — Send 0x75 empty payload
    4  — Send 0x76 01
    5  — Send 0x20 0100 + 0x21 7e01000000 (state sync as reply)
    6  — No reply at all (baseline)
    7  — Re-send 0x25 01 as reply
"""
import sys, os, time, struct, threading, argparse, select

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "windows", "rd"))
from yb9_usb import (send_frame, send_frame_bytes, get_device, release_device,
                     EP_IN, wait_for_device)
from datetime import datetime

# ─── Input event constants ────────────────────────────────────────────
INPUT_EVENT_SIZE = struct.calcsize("llHHi")
EV_ABS = 0x03
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36

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

def find_event_device(name_fragment):
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
            for ev_type, ev_code, ev_val in [
                (0x03, 0x39, -1), (0x01, 0x14a, 0), (0x00, 0x00, 0),
            ]:
                os.write(fd, struct.pack("llHHi", 0, 0, ev_type, ev_code, ev_val))
            os.close(fd)
        except Exception:
            pass

def monitor_hid_events(evnum, stop_event, results):
    path = f"/dev/input/event{evnum}"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except Exception:
        return
    while not stop_event.is_set():
        try:
            readable, _, _ = select.select([fd], [], [], 0.2)
            if not readable:
                continue
            data = os.read(fd, INPUT_EVENT_SIZE * 64)
            count = len(data) // INPUT_EVENT_SIZE
            for i in range(count):
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

# ─── Ack reply variants ──────────────────────────────────────────────
# Each receives the 0x75 payload and sends a reply

VARIANTS = {
    1: ("echo 0x75 payload",       lambda p: send_frame_bytes(0x75, p)),
    2: ("minimal 0x75 01",         lambda p: send_frame(0x75, "01")),
    3: ("empty 0x75",              lambda p: send_frame(0x75, "")),
    4: ("ack 0x76 01",             lambda p: send_frame(0x76, "01")),
    5: ("state sync reply",        lambda p: (send_frame(0x20, "0100"), send_frame(0x21, "7e01000000"))),
    6: ("no reply (baseline)",     lambda p: None),
    7: ("re-activate 0x25 01",     lambda p: send_frame(0x25, "01")),
}

# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Test MCU 0x75 ack reply variants")
    parser.add_argument("variant", type=int, nargs="?", default=0,
                        help="Variant number (1-7). 0 = run all.")
    parser.add_argument("--duration", type=int, default=20,
                        help="Monitoring duration in seconds (default: 20)")
    args = parser.parse_args()

    ev16 = find_event_device("Touchscreen Bottom")
    ev19 = find_event_device("Emulated Touchpad")
    if ev16 is None or ev19 is None:
        print(f"ERROR: input devices not found. ev16={ev16} ev19={ev19}")
        sys.exit(1)
    print(f"Monitor: event{ev16} (touchscreen), event{ev19} (touchpad)\n")

    variants = [args.variant] if args.variant > 0 else sorted(VARIANTS.keys())
    for vid in variants:
        if vid not in VARIANTS:
            print(f"Unknown variant {vid}. Valid: {sorted(VARIANTS.keys())}")
            continue
        run_variant(vid, args.duration, ev16, ev19)
        if vid != variants[-1]:
            print("\n" + "=" * 60 + "\nWaiting 3s...\n" + "=" * 60 + "\n")
            time.sleep(3)

def run_variant(vid, duration, ev16, ev19):
    desc, reply_fn = VARIANTS[vid]
    print(f"{'=' * 60}")
    print(f"  VARIANT {vid}: {desc}")
    print(f"  {duration}s monitoring, 0x26 keepalive only, instant 0x75 reply")
    print(f"{'=' * 60}\n")

    flush_stuck_touches()
    print("Connecting...")
    wait_for_device(timeout=5)

    start_time = time.time()
    stop_all = threading.Event()
    ack_received = threading.Event()
    ack_payload = bytearray()
    ack_time = [0.0]
    reply_sent_time = [0.0]

    # ── MCU reader — replies to 0x75 IMMEDIATELY in background ──
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
                        ack_payload[:] = payload
                        ack_time[0] = elapsed
                        ack_received.set()
                        # Reply immediately!
                        if vid != 6:  # variant 6 = no reply
                            reply_fn(bytes(payload))
                            reply_elapsed = time.time() - start_time
                            reply_sent_time[0] = reply_elapsed
                            print(f"  [{reply_elapsed:6.2f}s] -> REPLIED ({desc})")
                    elif ftype == 0x50:
                        ver = payload[4:].decode('ascii', errors='replace').strip()
                        print(f"  [{elapsed:6.2f}s] MCU -> 0x50 FW: {ver}")
                    elif ftype == 0xa2:
                        pass  # silent
                    elif ftype == 0x26:
                        pass
                    else:
                        print(f"  [{elapsed:6.2f}s] MCU -> 0x{ftype:02x} {payload.hex()[:40]}")
            except Exception:
                pass

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    # ── HID event monitors ──
    hid_stop = threading.Event()
    ts_events = []
    tp_events = []
    threading.Thread(target=monitor_hid_events, args=(ev16, hid_stop, ts_events), daemon=True).start()
    threading.Thread(target=monitor_hid_events, args=(ev19, hid_stop, tp_events), daemon=True).start()

    # ── Full activation sequence — no blocking between phases ──
    print("--- Sending full activation sequence ---")

    # Phase 1: Init
    send_frame(0x4b, "0008b80b10271027"); time.sleep(0.05)
    send_frame(0x21, "8001010000");        time.sleep(0.05)
    send_frame(0x28, "0000");              time.sleep(0.05)
    send_frame(0x26, ts_bytes().hex());    time.sleep(0.1)

    # Phase 2: Activate + state sync (no wait for 0x75)
    send_frame(0x25, "01");  time.sleep(0.05)
    print(f"  [{time.time()-start_time:5.2f}s] Sent 0x25 01")
    send_frame(0x20, "0100");  time.sleep(0.05)
    send_frame(0x21, "7e01000000");  time.sleep(0.1)

    # Phase 3: Geometry immediately
    send_frame(0x26, ts_bytes().hex())
    send_frame_bytes(0x21, struct.pack("<BHH", 0x40, 3017, 1700))
    send_frame_bytes(0x21, struct.pack("<BHH", 0x41, 3017, 1700))
    send_frame_bytes(0x21, struct.pack("<BBBBB", 0x31, 0, 0, 0, 0))
    send_frame_bytes(0xa3, b"\x01")
    send_frame_bytes(0x31, build_0x31())
    send_frame(0x26, ts_bytes().hex())
    print(f"  [{time.time()-start_time:5.2f}s] Geometry sent")

    # ── Monitor ──
    print(f"\n--- Monitoring for {duration}s ---")
    print("  Touch the bottom screen! Watch cursor vs direct touch.\n")

    last_ts = 0
    last_tp = 0
    for i in range(duration):
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

        ack_info = ""
        if ack_received.is_set() and reply_sent_time[0] > 0:
            ack_info = f" ack@{ack_time[0]:.1f}s reply@{reply_sent_time[0]:.1f}s"

        print(f"  {elapsed:5.1f}s  tp:{new_tp:4d}  ts:{new_ts:4d}  {mode}{ack_info}")

        try:
            send_frame(0x26, ts_bytes().hex())
        except Exception as e:
            print(f"  {elapsed:.1f}s SEND FAILED: {e}")
            break

    # ── Results ──
    elapsed = time.time() - start_time
    print(f"\n--- Results: variant {vid} ({desc}) ---")
    print(f"  0x75 ack received: {ack_received.is_set()} at {ack_time[0]:.2f}s")
    if reply_sent_time[0] > 0:
        print(f"  Reply sent at:     {reply_sent_time[0]:.2f}s (delay: {reply_sent_time[0]-ack_time[0]*1000:.0f}ms)")
    print(f"  Touchpad events:   {len(tp_events)}")
    print(f"  Touchscreen events:{len(ts_events)}")

    if tp_events:
        print(f"  Touchpad window:   {tp_events[0][0]-start_time:.2f}s — {tp_events[-1][0]-start_time:.2f}s")
    if ts_events:
        print(f"  Touchscreen onset: {ts_events[0][0]-start_time:.2f}s")

    # Verdict
    if tp_events and not ts_events:
        print(f"  ✓ TOUCHPAD HELD for full {duration}s!")
    elif tp_events and ts_events:
        revert_t = ts_events[0][0] - start_time
        print(f"  ✗ REVERTED at {revert_t:.1f}s")
    elif ts_events and not tp_events:
        print(f"  ✗ Touchpad never activated")
    else:
        print(f"  ? No touch input — touch the screen during test!")

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
    flush_stuck_touches()
    try:
        release_device()
    except Exception:
        pass
    print("  Done.\n")

if __name__ == "__main__":
    main()
