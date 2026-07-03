#!/usr/bin/env python3
"""
diag-mcu-responses.py — Read MCU responses during touchpad activation.

Shows what the MCU sends back, which may explain the mode revert.
Requires sudo for USB access.
"""
import sys, os, time, struct
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "windows", "rd"))
from yb9_usb import (send_frame, send_frame_bytes, get_device, release_device,
                     EP_IN, reset_device, wait_for_device)
from datetime import datetime

def ts_bytes():
    now = datetime.now()
    return struct.pack("<HBBBBB", now.year, now.month, now.day, now.hour, now.minute, now.second)

def parse_oskp(data):
    """Parse OSKP frames from raw data."""
    frames = []
    pos = 0
    raw_hex = data.hex()
    while pos + 6 <= len(data):
        if data[pos:pos+4] == b"OSKP":
            plen = struct.unpack("<H", data[pos+4:pos+6])[0]
            if pos + 6 + plen <= len(data):
                payload = data[pos+6:pos+6+plen]
                frames.append((payload[0], payload[1:]))
                pos += 6 + plen
            else:
                break
        else:
            pos += 1
    return frames

def build_rect(l, t, r, b):
    return struct.pack("<HHHH", l, t, r, b)

def build_0x31():
    w, h = 3017, 1700
    cap_h = int(h * 80/500)
    btn_h = int(h * 90/500)
    sm = int(w * 40/500)
    bm = int(h * 40/500)
    gap = int(w * 10/500)
    fr = build_rect(0, 0, w, h)
    tr = build_rect(sm, cap_h, w-sm, h-bm-btn_h)
    btw = w - 2*sm - gap
    lb = build_rect(sm, h-bm-btn_h, sm+btw//2, h-bm)
    rb = build_rect(sm+btw//2+gap, h-bm-btn_h, w-sm, h-bm)
    tr2 = build_rect(0, 0, 0, 0)
    flags = 0
    payload = fr + lb + rb + tr + bytes([flags]) + tr2
    return payload

print("=== MCU Response Diagnostic ===\n")

# Connect
print("Connecting...")
wait_for_device(timeout=5)

# Background reader thread
import threading
stop_reader = threading.Event()
responses = []
def reader():
    while not stop_reader.is_set():
        try:
            dev = get_device()
            data = dev.read(EP_IN, 512, timeout=200)
            if data:
                frames = parse_oskp(bytes(data))
                elapsed = time.time() - start_time
                for ftype, payload in frames:
                    entry = (elapsed, ftype, payload)
                    responses.append(entry)
                    print(f"  [{elapsed:6.1f}s] MCU -> type=0x{ftype:02x} len={len(payload)} payload={payload.hex()[:80]}")
        except Exception:
            pass

start_time = time.time()
reader_thread = threading.Thread(target=reader, daemon=True)
reader_thread.start()

# Send activation sequence (same as --no-disable-touch)
print("\n--- Sending activation sequence ---")
steps = [
    (0x4b, "0008b80b10271027", "timing/threshold"),
    (0x21, "8001010000", "init OSK param"),
    (0x28, "0000", "status pair"),
    (0x26, ts_bytes().hex(), "keepalive"),
    # NO 0x27 (no disable touch)
    (0x25, "01", "touchpad mode ON"),
    (0x20, "0100", "sync flag"),
    (0x21, "7e01000000", "touchpad-on state sync"),
]

for cmd_type, payload, label in steps:
    send_frame(cmd_type, payload)
    print(f"  -> type=0x{cmd_type:02x} {label}")
    time.sleep(0.1)

print("  waiting 2s for settle...")
time.sleep(2)

# Phase 2
phase2 = [
    (0x26, ts_bytes().hex(), "keepalive"),
    (0x21, struct.pack("<BHH", 0x40, 3017, 1700), "screen info 40"),
    (0x21, struct.pack("<BHH", 0x41, 3017, 1700), "screen info 41"),
    (0x21, struct.pack("<BBBBB", 0x31, 0, 0, 0, 0), "orientation"),
    (0xa3, "01", "post-orientation flag"),
]

for cmd_type, payload, label in phase2:
    if isinstance(payload, bytes):
        send_frame_bytes(cmd_type, payload)
    else:
        send_frame(cmd_type, payload)
    print(f"  -> type=0x{cmd_type:02x} {label}")
    time.sleep(0.1)

send_frame_bytes(0x31, build_0x31())
print(f"  -> type=0x31 geometry (41 bytes)")
time.sleep(0.1)
send_frame(0x26, ts_bytes().hex())
print(f"  -> type=0x26 keepalive")

print("\n--- Monitoring with keepalive for 30s ---")
print("Touch the bottom screen. Watching MCU responses...\n")

# Send periodic keepalives and monitor
geo_payload = build_0x31()
for i in range(30):
    time.sleep(1.0)
    try:
        send_frame(0x26, ts_bytes().hex())
        if i % 2 == 0:
            send_frame(0x25, "01")
            send_frame(0x20, "0100")
            send_frame(0x21, "7e01000000")
            send_frame_bytes(0x31, geo_payload)
    except Exception as e:
        print(f"  [{i+1}s] SEND FAILED: {e}")
        break

stop_reader.set()
reader_thread.join(timeout=2)

print(f"\n--- Summary ---")
print(f"Total MCU responses: {len(responses)}")
if responses:
    print("\nAll MCU responses:")
    for elapsed, ftype, payload in responses:
        # Try to interpret some known types
        extra = ""
        if ftype == 0x9a and len(payload) >= 7:
            gesture_id = payload[4]
            screen = payload[5]
            extra = f" [gesture id={gesture_id} screen={screen}]"
        elif ftype == 0x26:
            extra = f" [keepalive ack]"
        elif ftype == 0x25:
            extra = f" [mode response: {'ON' if payload[0]==1 else 'OFF'}]"
        print(f"  [{elapsed:6.1f}s] type=0x{ftype:02x} len={len(payload)} {payload.hex()[:60]}{extra}")

try:
    release_device()
except:
    pass
