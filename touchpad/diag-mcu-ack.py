#!/usr/bin/env python3
"""
diag-mcu-ack.py — Test if acknowledging MCU 0x75 response prevents mode revert.

Hypothesis: the MCU sends 0x75 as a mode acknowledgment and expects the host
to respond. Without a response, it reverts after ~5s.
"""
import sys, os, time, struct, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "windows", "rd"))
from yb9_usb import (send_frame, send_frame_bytes, get_device, release_device,
                     EP_IN, wait_for_device)
from datetime import datetime

def ts_bytes():
    now = datetime.now()
    return struct.pack("<HBBBBB", now.year, now.month, now.day, now.hour, now.minute, now.second)

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
    return fr + lb + rb + tr + bytes([0]) + tr2

def parse_oskp(data):
    frames = []
    pos = 0
    while pos + 6 <= len(data):
        if data[pos:pos+4] == b"OSKP":
            plen = struct.unpack("<H", data[pos+4:pos+6])[0]
            if pos + 6 + plen <= len(data):
                frames.append((data[pos+6], data[pos+7:pos+6+plen]))
                pos += 6 + plen
            else:
                break
        else:
            pos += 1
    return frames

got_0x75 = threading.Event()
start_time = time.time()
stop_reader = threading.Event()

def reader():
    """Background reader that captures MCU responses and handles 0x75."""
    while not stop_reader.is_set():
        try:
            dev = get_device()
            data = dev.read(EP_IN, 512, timeout=200)
            if data:
                for ftype, payload in parse_oskp(bytes(data)):
                    elapsed = time.time() - start_time
                    if ftype == 0x75:
                        print(f"  [{elapsed:6.1f}s] MCU -> 0x75 ACK: {payload.hex()}")
                        got_0x75.set()
                    elif ftype == 0x50:
                        ver = payload[4:].decode('ascii', errors='replace').strip()
                        print(f"  [{elapsed:6.1f}s] MCU -> 0x50 FW: {ver}")
                    elif ftype == 0xa2:
                        # Touch events — just count, don't spam
                        pass
                    else:
                        print(f"  [{elapsed:6.1f}s] MCU -> type=0x{ftype:02x} len={len(payload)} {payload.hex()[:60]}")
        except Exception:
            pass

print("=== MCU Acknowledgment Test ===\n")
print("Connecting...")
wait_for_device(timeout=5)

reader_thread = threading.Thread(target=reader, daemon=True)
reader_thread.start()

# Init
print("\n--- Init ---")
send_frame(0x4b, "0008b80b10271027"); time.sleep(0.05)
send_frame(0x21, "8001010000"); time.sleep(0.05)
send_frame(0x28, "0000"); time.sleep(0.05)
send_frame(0x26, ts_bytes().hex()); time.sleep(0.1)

# Activate touchpad
print("\n--- Activate touchpad ---")
got_0x75.clear()
send_frame(0x25, "01")
print("  Sent 0x25 01, waiting for MCU 0x75 response...")

# Wait for the 0x75 acknowledgment
if got_0x75.wait(timeout=3):
    print("  Got 0x75! Sending acknowledgment back...")
    # Try different possible acknowledgments:

    # Attempt 1: Echo back a similar structure
    send_frame(0x75, "010101")
    print("  Sent 0x75 010101")
    time.sleep(0.1)

    # Attempt 2: Send the same payload back
    send_frame(0x75, "dd510000010101")
    print("  Sent 0x75 dd510000010101")
    time.sleep(0.1)

    # Attempt 3: Simple acknowledgment
    send_frame(0x20, "0100")
    send_frame(0x21, "7e01000000")
    print("  Sent state sync")
else:
    print("  No 0x75 received within 3s")

time.sleep(0.5)

# Phase 2: geometry
send_frame(0x26, ts_bytes().hex()); time.sleep(0.05)
send_frame_bytes(0x21, struct.pack("<BHH", 0x40, 3017, 1700)); time.sleep(0.05)
send_frame_bytes(0x21, struct.pack("<BHH", 0x41, 3017, 1700)); time.sleep(0.05)
send_frame_bytes(0x21, struct.pack("<BBBBB", 0x31, 0, 0, 0, 0)); time.sleep(0.05)
send_frame_bytes(0xa3, b"\x01"); time.sleep(0.05)
send_frame_bytes(0x31, build_0x31()); time.sleep(0.05)
send_frame(0x26, ts_bytes().hex())

print("\n--- Monitoring for 30s with ONLY 0x26 keepalive (no 0x25 re-send) ---")
print("If the acknowledgment works, touchpad should stay stable.\n")

for i in range(30):
    time.sleep(1.0)
    try:
        send_frame(0x26, ts_bytes().hex())
    except Exception as e:
        print(f"  [{i+1}s] FAILED: {e}")
        break

stop_reader.set()
reader_thread.join(timeout=2)

print("\nDone.")
try:
    release_device()
except:
    pass
