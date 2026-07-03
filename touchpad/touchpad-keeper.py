#!/usr/bin/env python3
"""
touchpad-keeper.py — Keep Yoga Book 9 touchpad alive by blocking touchscreen
revert events and periodically re-activating the MCU.

Strategy:
  1. Grab event16 (bottom touchscreen) via EVIOCGRAB so KWin ignores revert events
  2. Activate touchpad mode on the MCU
  3. Periodically re-send activation to keep the MCU in touchpad mode
  4. On exit: release event16 grab, restore touchscreen mode

The MCU still reverts every ~7s, but the revert is invisible because
event16 is grabbed. The re-activation restores touchpad mode before
the user notices.

Usage:
    sudo python3 touchpad-keeper.py          # foreground
    sudo python3 touchpad-keeper.py --detach  # background
    sudo python3 touchpad-keeper.py --stop    # stop background daemon
"""
import argparse
import fcntl
import os
import signal
import struct
import sys
import time
import select
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "windows", "rd"))
from yb9_usb import (send_frame, send_frame_bytes, get_device, release_device,
                     EP_IN, wait_for_device, reset_device)

# ─── Constants ────────────────────────────────────────────────────────
EVIOCGRAB = 0x40044590
EVIOCRMFF = 0x40044591
INPUT_EVENT_SIZE = struct.calcsize("llHHi")
PID_FILE = "/tmp/yb9-touchpad-keeper.pid"

# ─── Geometry helpers ─────────────────────────────────────────────────

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

GEO_PAYLOAD = build_0x31()

# ─── Find devices ─────────────────────────────────────────────────────

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

# ─── Touchscreen grab ─────────────────────────────────────────────────

class TouchscreenGrabber:
    """Grab event16 exclusively so KWin ignores touchscreen events during touchpad mode."""

    def __init__(self, event_num):
        self.event_num = event_num
        self.path = f"/dev/input/event{event_num}"
        self.fd = None
        self.grabbed = False

    def grab(self):
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
            fcntl.ioctl(self.fd, EVIOCGRAB, 1)
            self.grabbed = True
            # Flush any pending events
            self._drain()
            return True
        except Exception as e:
            print(f"  [warn] could not grab {self.path}: {e}")
            return False

    def release(self):
        if self.fd is not None:
            try:
                # Flush and send touch-up to clear any stuck state
                self._flush_touch_up()
                fcntl.ioctl(self.fd, EVIOCGRAB, 0)
            except Exception:
                pass
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None
            self.grabbed = False

    def _drain(self):
        """Read and discard pending events."""
        if self.fd is None:
            return
        try:
            while True:
                data = os.read(self.fd, INPUT_EVENT_SIZE * 32)
                if len(data) < INPUT_EVENT_SIZE:
                    break
        except (BlockingIOError, OSError):
            pass

    def _flush_touch_up(self):
        """Send touch-up events to clear stuck touches."""
        if self.fd is None:
            return
        try:
            for ev_type, ev_code, ev_val in [
                (0x03, 0x39, -1),   # ABS_MT_TRACKING_ID = -1
                (0x01, 0x14a, 0),   # BTN_TOUCH = 0
                (0x00, 0x00, 0),    # SYN_REPORT
            ]:
                ev = struct.pack("llHHi", 0, 0, ev_type, ev_code, ev_val)
                os.write(self.fd, ev)
        except Exception:
            pass

    def drain_loop(self, stop_event):
        """Background thread: continuously drain events while grabbed."""
        while not stop_event.is_set() and self.grabbed:
            try:
                readable, _, _ = select.select([self.fd], [], [], 0.5)
                if readable:
                    self._drain()
            except Exception:
                time.sleep(0.1)

# ─── Flush helpers ─────────────────────────────────────────────────────

def flush_stuck_touches():
    """Flush stuck touches on event16 and event19."""
    for evnum in [16, 19]:
        try:
            fd = os.open(f"/dev/input/event{evnum}", os.O_WRONLY | os.O_NONBLOCK)
            for t, c, v in [(0x03, 0x39, -1), (0x01, 0x14a, 0), (0x00, 0x00, 0)]:
                os.write(fd, struct.pack("llHHi", 0, 0, t, c, v))
            os.close(fd)
        except Exception:
            pass

# ─── MCU commands ──────────────────────────────────────────────────────

def send_init():
    send_frame(0x4b, "0008b80b10271027"); time.sleep(0.03)
    send_frame(0x21, "8001010000");        time.sleep(0.03)
    send_frame(0x28, "0000");              time.sleep(0.03)
    send_frame(0x26, ts_bytes().hex());    time.sleep(0.05)

def send_touchpad_on():
    send_frame(0x25, "01");                                          time.sleep(0.03)
    send_frame(0x20, "0100");                                        time.sleep(0.03)
    send_frame(0x21, "7e01000000");                                  time.sleep(0.03)
    send_frame_bytes(0x21, struct.pack("<BHH", 0x40, 3017, 1700));  time.sleep(0.03)
    send_frame_bytes(0x21, struct.pack("<BHH", 0x41, 3017, 1700));  time.sleep(0.03)
    send_frame_bytes(0x21, struct.pack("<BBBBB", 0x31, 0, 0, 0, 0));time.sleep(0.03)
    send_frame_bytes(0xa3, b"\x01");                                 time.sleep(0.03)
    send_frame_bytes(0x31, GEO_PAYLOAD);                             time.sleep(0.03)
    send_frame(0x26, ts_bytes().hex())

def send_touchpad_refresh():
    """Full off→on cycle to re-activate touchpad mode cleanly.
    
    Sending 0x25 01 while MCU is already in touchpad mode causes immediate
    revert (confirmed by variant 7 tests). So we deactivate first, wait for
    settle, then fully reactivate.
    """
    # Deactivate
    send_frame(0x25, "00")
    send_frame(0x21, "7e00000000")
    time.sleep(0.15)
    # Full re-activation
    send_touchpad_on()

def send_touchpad_off():
    send_frame(0x25, "00")
    send_frame(0x21, "7e00000000")
    send_frame(0x26, ts_bytes().hex())

# ─── Main loop ─────────────────────────────────────────────────────────

def run_foreground(interval=3.0):
    ev16 = find_event_num("Touchscreen Bottom")
    ev19 = find_event_num("Emulated Touchpad")
    if ev16 is None:
        print("ERROR: bottom touchscreen event device not found")
        sys.exit(1)

    print(f"Touchscreen: event{ev16}, Touchpad: event{ev19}")

    # ── Connect to MCU ──
    print("Connecting to MCU...")
    wait_for_device(timeout=5)

    # ── MCU reader thread (discard responses) ──
    import threading

    stop_all = threading.Event()

    def mcu_reader():
        while not stop_all.is_set():
            try:
                dev = get_device()
                data = dev.read(EP_IN, 512, timeout=200)
                # Discard — we don't need to process responses
            except Exception:
                pass

    reader_thread = threading.Thread(target=mcu_reader, daemon=True)
    reader_thread.start()

    # ── Grab touchscreen ──
    print(f"Grabbing event{ev16} (touchscreen)...")
    grabber = TouchscreenGrabber(ev16)
    if not grabber.grab():
        print("ERROR: could not grab touchscreen. Are you root?")
        sys.exit(1)
    print("  Grabbed — touchscreen events blocked")
    start = time.time()

    # Drain thread
    drain_thread = threading.Thread(target=grabber.drain_loop, args=(stop_all,), daemon=True)
    drain_thread.start()

    # ── Activate touchpad ──
    print("Activating touchpad mode...")
    flush_stuck_touches()
    send_init()
    send_touchpad_on()
    print("  Touchpad active")

    # Write PID file
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # ── HID monitor for diagnostics ──
    tp_event_count = [0]
    ts_event_count = [0]
    import select as _select

    def hid_monitor():
        """Count touchpad and touchscreen HID events for diagnostics."""
        try:
            fd_tp = os.open(f"/dev/input/event{ev19}", os.O_RDONLY | os.O_NONBLOCK)
        except Exception:
            return
        while not stop_all.is_set():
            try:
                readable, _, _ = _select.select([fd_tp], [], [], 0.5)
                if not readable:
                    continue
                data = os.read(fd_tp, INPUT_EVENT_SIZE * 64)
                for i in range(len(data) // INPUT_EVENT_SIZE):
                    ev = data[i * INPUT_EVENT_SIZE:(i + 1) * INPUT_EVENT_SIZE]
                    _, _, ev_type, ev_code, _ = struct.unpack("llHHi", ev)
                    if ev_type == 0x03 and ev_code in (0x35, 0x36):
                        tp_event_count[0] += 1
            except Exception:
                pass
        try:
            os.close(fd_tp)
        except Exception:
            pass

    monitor_thread = threading.Thread(target=hid_monitor, daemon=True)
    monitor_thread.start()

    # ── Main keepalive loop ──
    running = True
    def stop(signum=None, frame=None):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    last_refresh = time.time()
    last_diag = time.time()
    cycle = 0

    print(f"\nTouchpad keeper running. Re-activation every {interval:.1f}s")
    print("Press Ctrl+C to stop. Touch the bottom screen to test.\n")

    while running:
        time.sleep(0.5)
        now = time.time()

        # Diagnostic output every 5s
        if now - last_diag >= 5.0:
            count = tp_event_count[0]
            tp_event_count[0] = 0
            print(f"  [{now - start:.0f}s] tp_events={count} cycle={cycle}")
            last_diag = now

        if now - last_refresh >= interval:
            try:
                send_touchpad_refresh()
                last_refresh = now
                cycle += 1
            except Exception as e:
                print(f"  Re-activation failed: {e}, reconnecting...")
                try:
                    reset_device()
                    wait_for_device(timeout=10)
                    send_init()
                    send_touchpad_on()
                    last_refresh = now
                    print("  Reconnected and re-activated")
                except Exception as e2:
                    print(f"  Reconnect failed: {e2}")
                    time.sleep(2)

    # ── Cleanup ──
    print("\nStopping...")
    stop_all.set()

    # Restore touchscreen
    print("Restoring touchscreen mode...")
    try:
        send_touchpad_off()
        time.sleep(0.2)
    except Exception:
        pass

    # Release touchscreen grab
    print("Releasing touchscreen grab...")
    grabber.release()
    flush_stuck_touches()

    # Release USB
    try:
        release_device()
    except Exception:
        pass

    # Remove PID file
    try:
        os.unlink(PID_FILE)
    except Exception:
        pass

    print("Done. Touchscreen restored.")


def stop_daemon():
    """Stop a running background daemon."""
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to PID {pid}")
        time.sleep(1)
        # Check if still running
        try:
            os.kill(pid, 0)
            print("Still running, sending SIGKILL...")
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.unlink(PID_FILE)
        except Exception:
            pass
    except FileNotFoundError:
        print("No PID file found — daemon not running")
    except ProcessLookupError:
        print("Daemon not running")
        try:
            os.unlink(PID_FILE)
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Yoga Book 9 touchpad keeper")
    parser.add_argument("--detach", action="store_true", help="Run in background")
    parser.add_argument("--stop", action="store_true", help="Stop background daemon")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="Re-activation interval in seconds (default: 3.0)")
    args = parser.parse_args()

    if args.stop:
        stop_daemon()
        return

    if args.detach:
        # Double-fork daemonization
        if os.fork() > 0:
            print(f"Started in background (see {PID_FILE})")
            sys.exit(0)
        os.setsid()
        if os.fork() > 0:
            sys.exit(0)
        # Redirect output to log
        log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "touchpad-keeper.log")
        sys.stdout = open(log, "a")
        sys.stderr = sys.stdout
        sys.stdin = open("/dev/null", "r")

    run_foreground(interval=args.interval)


if __name__ == "__main__":
    main()
