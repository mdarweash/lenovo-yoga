#!/usr/bin/env python3
"""
yb9-touchpad-daemon — Persistent Yoga Book 9 touchpad daemon.

Mimics the Windows YB9.Service.exe + YB9.TouchPad.exe behavior:
  - Maintains persistent USB connection to INGENIC MCU
  - Sends periodic keepalives to prevent MCU timeout
  - Activates/deactivates touchpad mode on command
  - Re-sends touchpad state periodically to prevent MCU revert

Usage:
    sudo python3 yb9-touchpad-daemon.py start          # foreground
    sudo python3 yb9-touchpad-daemon.py start --detach  # background

    # While running, send commands via:
    sudo python3 yb9-touchpad-daemon.py touchpad on
    sudo python3 yb9-touchpad-daemon.py touchpad off
    sudo python3 yb9-touchpad-daemon.py status
"""

import argparse
import json
import os
import struct
import sys
import time
import signal
import threading
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RD_DIR = os.path.join(SCRIPT_DIR, "..", "windows", "rd")
sys.path.insert(0, RD_DIR)

from yb9_usb import (send_frame, send_frame_bytes, get_device, release_device,
                     reset_device, wait_for_device, EP_IN, EP_OUT)

STATE_FILE = "/tmp/yb9-touchpad-state.json"
GEOMETRIES = {
    "touchpad": {"width": 3017, "height": 1700},
}


def build_rect(left, top, right, bottom):
    return struct.pack("<HHHH", left, top, right, bottom)


def build_touchpad_profile(width, height):
    """Build touchpad geometry matching Windows TouchPadMainWindow.xml layout."""
    cap_fraction = 80 / 500
    btn_fraction = 90 / 500
    side_margin = 40 / 500
    bottom_margin = 40 / 500
    btn_gap = 10 / 500

    caption_h = int(height * cap_fraction)
    btn_h = int(height * btn_fraction)
    side_m = int(width * side_margin)
    bottom_m = int(height * bottom_margin)
    gap = int(width * btn_gap)

    frame_rect = build_rect(0, 0, width, height)
    touchable_rect1 = build_rect(side_m, caption_h, width - side_m, height - bottom_m - btn_h)

    btn_total_width = width - 2 * side_m - gap
    half_btn = btn_total_width // 2

    l_button = build_rect(side_m, height - bottom_m - btn_h, side_m + half_btn, height - bottom_m)
    r_button = build_rect(side_m + half_btn + gap, height - bottom_m - btn_h, width - side_m, height - bottom_m)

    return frame_rect, l_button, r_button, touchable_rect1


def build_short_form_0x31(frame_rect, l_button, r_button, touchable_rect1,
                          touchable_rect2=None, src_id=0, disable_for_mini=0):
    if touchable_rect2 is None:
        touchable_rect2 = build_rect(0, 0, 0, 0)
    packed_flags = ((src_id & 0x3) << 1) | (disable_for_mini & 0x1)
    payload = bytearray()
    payload += frame_rect + l_button + r_button + touchable_rect1
    payload += bytes([packed_flags])
    payload += touchable_rect2
    return bytes(payload)


def get_timestamp_bytes():
    now = datetime.now()
    return struct.pack("<HBBBBB", now.year, now.month, now.day,
                       now.hour, now.minute, now.second)


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"mode": "none", "pid": None}


def send_init_sequence():
    """Send the MCU initialization sequence."""
    send_frame(0x4b, "0008b80b10271027")
    time.sleep(0.05)
    send_frame(0x21, "8001010000")
    time.sleep(0.05)
    send_frame(0x28, "0000")
    time.sleep(0.05)
    send_frame(0x26, get_timestamp_bytes().hex())


def send_touchpad_on():
    """Activate touchpad mode."""
    geo = GEOMETRIES["touchpad"]
    w, h = geo["width"], geo["height"]

    send_frame(0x25, "01")
    time.sleep(0.05)
    send_frame(0x20, "0100")
    time.sleep(0.05)
    send_frame(0x21, "7e01000000")
    time.sleep(0.05)
    send_frame(0x21, struct.pack("<BHH", 0x40, w, h))
    time.sleep(0.05)
    send_frame(0x21, struct.pack("<BHH", 0x41, w, h))
    time.sleep(0.05)

    frame_rect, l_button, r_button, touchable_rect1 = build_touchpad_profile(w, h)
    payload_0x31 = build_short_form_0x31(frame_rect, l_button, r_button, touchable_rect1)
    send_frame_bytes(0x31, payload_0x31)
    time.sleep(0.05)
    send_frame(0x26, get_timestamp_bytes().hex())


def send_touchpad_off():
    """Deactivate touchpad mode, restore touchscreen."""
    send_frame(0x25, "00")
    time.sleep(0.05)
    send_frame(0x21, "7e000000")
    time.sleep(0.05)
    send_frame(0x26, get_timestamp_bytes().hex())


def send_touchpad_refresh():
    """Re-send touchpad state to prevent MCU revert (lightweight)."""
    geo = GEOMETRIES["touchpad"]
    w, h = geo["width"], geo["height"]

    send_frame(0x26, get_timestamp_bytes().hex())
    send_frame(0x25, "01")
    send_frame(0x21, "7e01000000")

    frame_rect, l_button, r_button, touchable_rect1 = build_touchpad_profile(w, h)
    payload_0x31 = build_short_form_0x31(frame_rect, l_button, r_button, touchable_rect1)
    send_frame_bytes(0x31, payload_0x31)


def drain_mcu():
    """Read and discard any pending MCU responses."""
    try:
        dev = get_device()
        dev.read(EP_IN, 512, timeout=10)
    except Exception:
        pass


def daemon_loop():
    """Main daemon loop — keeps MCU alive and maintains touchpad state."""
    state = load_state()
    mode = state.get("mode", "none")
    print(f"Daemon starting. Current mode: {mode}")
    print(f"Commands: echo '{{\"mode\":\"touchpad\"}}' > {STATE_FILE}")
    print(f"          echo '{{\"mode\":\"screen\"}}'  > {STATE_FILE}")

    save_state({"mode": mode, "pid": os.getpid()})

    # Track last state refresh
    last_refresh = time.time()
    refresh_interval = 5.0   # re-send touchpad state every 5s
    keepalive_interval = 1.5 # send keepalive every 1.5s
    last_keepalive = time.time()

    running = True
    def stop(signum=None, frame=None):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    while running:
        now = time.time()

        # Check for mode changes via state file
        try:
            current = load_state()
            new_mode = current.get("mode", mode)
            if new_mode != mode:
                print(f"Mode change: {mode} -> {new_mode}")
                mode = new_mode
                if mode == "touchpad":
                    send_touchpad_on()
                    last_refresh = now
                elif mode == "screen":
                    send_touchpad_off()
                save_state({"mode": mode, "pid": os.getpid()})
        except Exception:
            pass

        # Periodic keepalive
        if now - last_keepalive >= keepalive_interval:
            try:
                drain_mcu()
                send_frame(0x26, get_timestamp_bytes().hex())
                last_keepalive = now
            except Exception as e:
                print(f"Keepalive failed: {e}, reconnecting...")
                try:
                    reset_device()
                    wait_for_device(timeout=10)
                    send_init_sequence()
                    if mode == "touchpad":
                        send_touchpad_on()
                    last_refresh = now
                except Exception as e2:
                    print(f"Reconnect failed: {e2}")
                    time.sleep(2)
                    continue

        # Periodic state refresh for touchpad mode
        if mode == "touchpad" and now - last_refresh >= refresh_interval:
            try:
                send_touchpad_refresh()
                last_refresh = now
            except Exception:
                pass

        time.sleep(0.3)

    # Cleanup
    print("Daemon stopping...")
    if mode == "touchpad":
        try:
            send_touchpad_off()
        except Exception:
            pass
    try:
        release_device()
    except Exception:
        pass
    save_state({"mode": "screen", "pid": None})
    print("Daemon stopped.")


def main():
    parser = argparse.ArgumentParser(description="Yoga Book 9 touchpad daemon")
    sub = parser.add_subparsers(dest="command")

    start_p = sub.add_parser("start", help="Start the daemon")
    start_p.add_argument("--detach", action="store_true", help="Run in background")

    sub.add_parser("stop", help="Stop the daemon")

    tp = sub.add_parser("touchpad", help="Switch touchpad mode")
    tp.add_argument("mode", choices=["on", "off"])

    sub.add_parser("status", help="Show current mode")
    sub.add_parser("init", help="Send init sequence only")

    args = parser.parse_args()

    if args.command == "start":
        print("Connecting to INGENIC MCU...")
        try:
            wait_for_device(timeout=5)
        except RuntimeError:
            print("ERROR: INGENIC device not found. Is it connected?")
            sys.exit(1)

        send_init_sequence()

        if args.detach:
            if os.fork() > 0:
                print(f"Daemon started in background (PID {os.getpid()})")
                sys.exit(0)
            os.setsid()
            if os.fork() > 0:
                sys.exit(0)
            # Redirect stdout/stderr to log
            log_path = os.path.join(SCRIPT_DIR, "touchpad-daemon.log")
            sys.stdout = open(log_path, "a")
            sys.stderr = sys.stdout

        daemon_loop()

    elif args.command == "stop":
        st = load_state()
        pid = st.get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"Sent SIGTERM to daemon PID {pid}")
            except ProcessLookupError:
                print("Daemon not running")
            save_state({"mode": "screen", "pid": None})
        else:
            print("No daemon PID found")

    elif args.command == "touchpad":
        mode = "touchpad" if args.mode == "on" else "screen"
        st = load_state()
        st["mode"] = mode
        save_state(st)
        print(f"Mode set to: {mode}")
        pid = st.get("pid")
        if pid:
            try:
                os.kill(pid, 0)
                print(f"Daemon PID {pid} is running and will pick up the change")
            except ProcessLookupError:
                print("Daemon is not running. Start it first: sudo python3 yb9-touchpad-daemon.py start")
        else:
            print("No daemon running. Start it first: sudo python3 yb9-touchpad-daemon.py start")

    elif args.command == "status":
        st = load_state()
        print(f"Mode: {st.get('mode', 'unknown')}")
        print(f"Daemon PID: {st.get('pid', 'none')}")
        pid = st.get("pid")
        if pid:
            try:
                os.kill(pid, 0)
                print(f"Daemon status: running (PID {pid})")
            except ProcessLookupError:
                print("Daemon status: not running (stale PID)")
        else:
            print("Daemon status: not running")

    elif args.command == "init":
        wait_for_device(timeout=5)
        send_init_sequence()
        print("Init sequence sent.")
        release_device()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
