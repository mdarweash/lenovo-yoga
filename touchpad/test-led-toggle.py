#!/usr/bin/env python3
"""
test-led-toggle.py — Does a keyboard LED Output Report activate touchpad mode?

THE HYPOTHESIS
==============
Windows capture frame 164 (the CRITICAL activation frame) is:

    1.5.2  EP 0x02 OUT  URB_INTERRUPT  HID Data: 20 00

IF2's HID report descriptor (167 bytes, decoded from live sysfs) declares:

    Report ID 0x20  (Keyboard)
      Input:  8 modifier bits + 1 reserved byte + 6 keycodes
      Output: 5 LED bits (NumLock/CapsLock/ScrollLock/Compose/Kana) + 3 pad

So `[0x20, 0x00]` is just "all keyboard LEDs off" — a standard HID LED sync
report. The MCU likely reacts to this report arriving on EP 0x02 OUT through
a *bound HID driver* (usbhid/hid-generic), NOT to the raw bytes via pyusb.

THE PATH THAT IS NEW
====================
Previously tested paths (all FAILED):
  - pyusb raw interrupt OUT to IF2 EP 0x02  (no HID driver bound)
  - hidraw write to /dev/hidraw3              (routes to SET_REPORT control EP)

This test uses evdev EV_LED injection on event4 (the keyboard), which the
kernel HID layer translates to:
    hid_hw_output_report([0x20, <ledbits>])   on EP 0x02 OUT (interrupt OUT)
through the *bound* hid-generic driver. This is the closest Linux analog to
Windows frame 164 — and the one variant never tried.

Confirmed prerequisites (checked live):
  - event4 = "INGENIC ... Keyboard", HID_PHYS=.../input2  (IF2)
  - event4 capabilities:  B: LED=1f  (all 5 LEDs present)
  - /dev/input/event4 is group-writable by `input` → no root needed to inject

USAGE
=====
    python3 test-led-toggle.py            # interactive, ~30s, touch when prompted
    python3 test-led-toggle.py --window 10

The script needs NO root for the LED injection. It will tell you when to touch
the bottom screen during each phase.
"""
import os
import sys
import time
import struct
import select
import argparse

# ─── Constants ────────────────────────────────────────────────────────
INPUT_EVENT_SIZE = struct.calcsize("llHHi")  # 24 bytes
EV_SYN = 0x00
EV_ABS = 0x03
EV_LED = 0x11
SYN_REPORT = 0

LED_NUML, LED_CAPSL, LED_SCROLLL = 0, 1, 2
LED_COMPOSE, LED_KANA = 3, 4
ALL_LEDS = [LED_NUML, LED_CAPSL, LED_SCROLLL, LED_COMPOSE, LED_KANA]


# ─── Helpers ──────────────────────────────────────────────────────────

def find_event(name_fragment, phys_fragment=None):
    """Find /dev/input/eventN by name (and optional HID_PHYS fragment)."""
    for f in sorted(os.listdir("/dev/input/")):
        if not f.startswith("event"):
            continue
        base = f"/sys/class/input/{f}/device"
        try:
            name = open(f"{base}/name").read()
            if name_fragment in name:
                if phys_fragment is None:
                    return f"/dev/input/{f}"
                try:
                    phys = open(f"{base}/phys").read()
                    if phys_fragment in phys:
                        return f"/dev/input/{f}"
                except FileNotFoundError:
                    return f"/dev/input/{f}"
        except (FileNotFoundError, PermissionError):
            pass
    return None


def write_led(fd, led_code, value):
    """Inject EV_LED + EV_SYN. Returns True on success."""
    try:
        evt = struct.pack("llHHi", 0, 0, EV_LED, led_code, value)
        syn = struct.pack("llHHi", 0, 0, EV_SYN, SYN_REPORT, 0)
        os.write(fd, evt + syn)
        return True
    except OSError as e:
        print(f"  (LED write failed: {e})")
        return False


def count_abs_events(path, duration):
    """Open event device read-only, count EV_ABS events for `duration` seconds."""
    if path is None:
        return -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return -1
    count = 0
    end = time.time() + duration
    abs_pattern = struct.pack("<H", EV_ABS)
    while time.time() < end:
        remaining = max(0.1, end - time.time())
        r, _, _ = select.select([fd], [], [], min(0.5, remaining))
        if fd in r:
            try:
                data = os.read(fd, INPUT_EVENT_SIZE * 64)
                # count occurrences of EV_ABS type field in 24-byte records
                for i in range(0, len(data) - INPUT_EVENT_SIZE + 1, INPUT_EVENT_SIZE):
                    if data[i + 16:i + 18] == abs_pattern:
                        count += 1
            except BlockingIOError:
                pass
    os.close(fd)
    return count


def phase(label, usb_bytes_note, kb_fd, led_actions, ts_path, tp_path, window):
    """Run one phase: inject LED(s), monitor touch, print result."""
    print("\n" + "=" * 64)
    print(f" {label}")
    print(f"   USB output report on EP 0x02 OUT: {usb_bytes_note}")
    print("=" * 64)
    for led, val in led_actions:
        write_led(kb_fd, led, val)
        time.sleep(0.15)
    print(f"  >>> TOUCH THE BOTTOM SCREEN NOW for {window}s <<<")
    ts = count_abs_events(ts_path, window)
    tp = count_abs_events(tp_path, window)
    print(f"  touchscreen events: {ts:5d}    touchpad events: {tp:5d}")
    if tp > 0 and ts == 0:
        print("  → ★ TOUCHPAD ACTIVE — MCU switched mode! ★")
    elif ts > 0 and tp == 0:
        print("  → still touchscreen mode")
    elif ts > 0 and tp > 0:
        print("  → BOTH (transitioning/partial)")
    else:
        print("  → no touch detected (did you touch the screen?)")
    return ts, tp


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Test keyboard LED Output Report as touchpad toggle")
    ap.add_argument("--window", type=int, default=6,
                    help="seconds to monitor touch per phase (default 6)")
    args = ap.parse_args()

    kb_path = find_event("Keyboard")
    ts_path = find_event("Touchscreen Bottom")
    tp_path = find_event("Emulated Touchpad")

    print("=" * 64)
    print(" LED OUTPUT REPORT TOUCHPAD TOGGLE TEST")
    print("=" * 64)
    print(f"  Keyboard (IF2, EP 0x02 OUT):  {kb_path or 'NOT FOUND'}")
    print(f"  Touchscreen bottom (event6?):  {ts_path or 'NOT FOUND'}")
    print(f"  Emulated Touchpad:             {tp_path or '(absent = MCU in touchscreen mode)'}")

    if not kb_path:
        sys.exit("ERROR: keyboard event not found")
    if not ts_path:
        sys.exit("ERROR: touchscreen event not found")
    # tp_path may be None — that itself is information (MCU in touchscreen mode)

    # Verify EV_LED support
    try:
        with open("/proc/bus/input/devices") as f:
            proc_input = f.read()
    except OSError:
        proc_input = ""
    kb_block = ""
    for block in proc_input.split("\n\n"):
        if "INGENIC" in block and "Keyboard" in block:
            kb_block = block
            break
    led_line = next((l for l in kb_block.splitlines() if l.startswith("B: LED")), "")
    if not led_line or led_line.split("=", 1)[1].strip() == "0":
        sys.exit("ERROR: keyboard has no EV_LED capability — injection won't work")
    print(f"  EV_LED capability:             {led_line}")
    print()

    kb_fd = os.open(kb_path, os.O_WRONLY | os.O_NONBLOCK)

    # Restore LEDs off at exit
    import atexit
    def restore():
        for led in ALL_LEDS:
            try:
                write_led(kb_fd, led, 0)
            except Exception:
                pass
        try:
            os.close(kb_fd)
        except Exception:
            pass
    atexit.register(restore)

    # Baseline: touch the screen with NO LED injection
    print(">>> BASELINE: touch the bottom screen for "
          f"{args.window}s (no LED injection yet) <<<")
    ts0 = count_abs_events(ts_path, args.window)
    tp0 = count_abs_events(tp_path, args.window)
    print(f"  baseline — touchscreen: {ts0}   touchpad: {tp0}\n")

    # Phase A: CapsLock ON → [0x20, 0x02]
    ts_a, tp_a = phase(
        "PHASE A: CapsLock LED = ON",
        "[0x20, 0x02]",
        kb_fd, [(LED_CAPSL, 1)], ts_path, tp_path, args.window)

    # Phase B: CapsLock OFF → [0x20, 0x00]  (EXACT Windows bytes)
    ts_b, tp_b = phase(
        "PHASE B: CapsLock LED = OFF  (EXACT Windows pattern)",
        "[0x20, 0x00]  ← this is what frame 164 sent",
        kb_fd, [(LED_CAPSL, 0)], ts_path, tp_path, args.window)

    # Phase C: rapid multi-LED toggling — lots of output reports
    actions = []
    for _ in range(3):
        for led in ALL_LEDS:
            actions.append((led, 1))
        for led in ALL_LEDS:
            actions.append((led, 0))
    ts_c, tp_c = phase(
        "PHASE C: rapid all-LED toggle burst (15+ output reports)",
        "[0x20, 0x00..0x1f] many",
        kb_fd, actions, ts_path, tp_path, args.window)

    # Summary
    print("\n" + "=" * 64)
    print(" SUMMARY")
    print("=" * 64)
    print(f"  baseline (no LED):     ts={ts0:5d}  tp={tp0:5d}")
    print(f"  A: CapsLock ON  0x02:  ts={ts_a:5d}  tp={tp_a:5d}")
    print(f"  B: CapsLock OFF 0x00:  ts={ts_b:5d}  tp={tp_b:5d}")
    print(f"  C: LED burst:          ts={ts_c:5d}  tp={tp_c:5d}")
    total_tp = tp_a + tp_b + tp_c
    if total_tp > 0:
        print("\n  ✓✓✓ TOUCHPAD ACTIVATED via keyboard LED Output Report!")
        print("  → Hypothesis CONFIRMED. The toggle is the LED report on EP 0x02 OUT")
        print("    through a bound HID driver. Next: build hid-ingenic to make it stick.")
    else:
        print("\n  ✗ No touchpad activation in any phase.")
        print("  → The LED report via evdev did NOT switch the MCU.")
        print("  → Likely means: MCU needs the report through a specific transport,")
        print("    OR needs OSKP priming (0x25/0x20 sync) before/after the LED report.")
        print("  → Next: build hid-ingenic driver to issue hid_hw_output_report()")
        print("    directly AND combine with OSKP priming.")
    print("=" * 64)


if __name__ == "__main__":
    main()
