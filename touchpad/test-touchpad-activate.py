#!/usr/bin/env python3
"""
test-touchpad-activate.py — Activate Yoga Book 9 virtual touchpad on Linux.

Replays the full Windows-derived activation sequence with a proper short-form
0x31 geometry packet.  Three geometry candidates can be tested independently.

Based on reverse-engineering of YB9.TouchPad.exe and YB9.PhantomKB.exe.
Uses USB bulk transfers on interface 1 via yb9_usb.py.

Usage:
    sudo python3 test-touchpad-activate.py [--geometry GEO] [--dry-run] [--monitor]

    --geometry {scaled,native,portrait}
        scaled   = 1800 x 1125  (KDE logical, previously woke event19)
        native   = 2880 x 1800  (native landscape)
        portrait = 1800 x 2880  (Windows fallback, portrait)
        Default: scaled

    --dry-run
        Print the full packet sequence without sending anything.

    --monitor
        After sending the sequence, monitor event6 and event19 for 10 seconds.
"""

import argparse
import os
import struct
import subprocess
import sys
import time
from datetime import datetime

# Add parent rd directory for yb9_usb
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RD_DIR = os.path.join(SCRIPT_DIR, "..", "windows", "rd")
sys.path.insert(0, RD_DIR)

from yb9_usb import (send_frame, send_frame_bytes, get_device,
                     read_response, reset_device, release_device,
                     wait_for_device, EP_IN)

import usb.core


# ─── Geometry definitions ─────────────────────────────────────────────

# Each geometry defines the full bottom-panel coordinate space.
# The Windows touchpad UI fills these rectangles relative to that space.

GEOMETRIES = {
    "scaled": {
        "width": 1800,
        "height": 1125,
        "description": "KDE scaled logical (1800x1125)",
    },
    "native": {
        "width": 2880,
        "height": 1800,
        "description": "Native landscape (2880x1800)",
    },
    "portrait": {
        "width": 1800,
        "height": 2880,
        "description": "Native portrait fallback (1800x2880)",
    },
    "hid": {
        "width": 30182,
        "height": 18864,
        "description": "HID raw coordinate space (30182x18864)",
    },
    "touchpad": {
        "width": 3017,
        "height": 1700,
        "description": "HID touchpad-native coordinate space (3017x1700)",
    },
}


def build_rect(left, top, right, bottom):
    """Pack a rectangle as 4x uint16 LE (8 bytes)."""
    return struct.pack("<HHHH", left, top, right, bottom)


def build_short_form_0x31(
    frame_rect, l_button, r_button, touchable_rect1,
    touchable_rect2=None, src_id=0, disable_for_mini=0
):
    """Build a short-form 0x31 payload (41 bytes = 0x29).

    Layout:
        frameRect       (8 bytes)
        LButton         (8 bytes)
        RButton         (8 bytes)
        touchableRect1  (8 bytes)
        packed flags    (1 byte)
        touchableRect2  (8 bytes)

    Packed flags: ((SrcId & 0x3) << 1) | (DisableForMini & 0x1)
    """
    if touchable_rect2 is None:
        touchable_rect2 = build_rect(0, 0, 0, 0)

    packed_flags = ((src_id & 0x3) << 1) | (disable_for_mini & 0x1)

    payload = bytearray()
    payload += frame_rect
    payload += l_button
    payload += r_button
    payload += touchable_rect1
    payload += bytes([packed_flags])
    payload += touchable_rect2

    assert len(payload) == 0x29, f"Short-form payload must be 0x29 bytes, got {len(payload):#x}"
    return bytes(payload)


def build_touchpad_profile(width, height):
    """Build a realistic touchpad profile for the given panel geometry.

    Models the Windows UI layout from TouchPadMainWindow.xml:
      - touchpad_main_box  → frameRect     (entire touchpad window)
      - touch_area         → touchableRect1 (main finger tracking area)
      - btn_touchpad_left  → LButton       (bottom-left click zone)
      - btn_touchpad_right → RButton       (bottom-right click zone)

    Windows layout proportions (from XML):
      - caption_hbox: 80px tall at top
      - touch_line: 1px separator
      - touchpad_btn_hbox: 90px tall, bottom-aligned, 40px left/right margin, 40px bottom margin
      - Two buttons split equally with 10px gap between them
    """
    # Scale Windows layout proportions to target coordinate space
    # Windows window size = 500x500, caption=80, btn_hbox=90, margins=40
    cap_fraction = 80 / 500    # ~16% for caption at top
    btn_fraction = 90 / 500    # ~18% for button row at bottom
    side_margin = 40 / 500     # ~8% side margin
    bottom_margin = 40 / 500   # ~8% bottom margin
    btn_gap = 10 / 500         # ~2% gap between buttons

    caption_h = int(height * cap_fraction)
    btn_h = int(height * btn_fraction)
    side_m = int(width * side_margin)
    bottom_m = int(height * bottom_margin)
    gap = int(width * btn_gap)

    # frameRect: the entire touchpad UI window (full panel)
    frame_rect = build_rect(0, 0, width, height)

    # touchableRect1: touch_area — everything between caption and buttons
    touchable_rect1 = build_rect(
        side_m,                       # left: side margin
        caption_h,                    # top: below caption
        width - side_m,               # right
        height - bottom_m - btn_h,    # bottom: above button row
    )

    # Buttons span from side_m to width-side_m, split in half with a gap
    btn_total_width = width - 2 * side_m - gap
    half_btn = btn_total_width // 2

    # LButton: bottom-left click zone
    l_button = build_rect(
        side_m,                       # left
        height - bottom_m - btn_h,    # top: start of button row
        side_m + half_btn,            # right
        height - bottom_m,            # bottom
    )

    # RButton: bottom-right click zone
    r_button = build_rect(
        side_m + half_btn + gap,      # left (after gap)
        height - bottom_m - btn_h,    # top
        width - side_m,               # right
        height - bottom_m,            # bottom
    )

    return frame_rect, l_button, r_button, touchable_rect1


def get_timestamp_bytes():
    """Return current time as 7-byte payload (year_le16, mon, day, hr, min, sec)."""
    now = datetime.now()
    return struct.pack("<HBBBBB", now.year, now.month, now.day,
                       now.hour, now.minute, now.second)


# ─── Full activation sequence ─────────────────────────────────────────

def send_activation_sequence(geo_name, dry_run=False, src_id=0,
                             ori_b=0, ori_c=0, a3_flag=0x01,
                             wait_after_toggle=2.0, skip_ori=False,
                             minimal=False, force_reset=False,
                             no_disable_touch=False):
    """Send the full Windows-derived touchpad activation sequence.

    Args:
        geo_name: geometry key from GEOMETRIES
        dry_run: if True, only print what would be sent
        src_id: SrcId value for the packed flags byte (0-3)
        ori_b: orientation byte B for 0x21 0x31 command
        ori_c: orientation byte C for 0x21 0x31 command
        a3_flag: flag for 0xa3 command (0x00 or 0x01)
        wait_after_toggle: seconds to wait after 0x25 01 for device settle
    """

    geo = GEOMETRIES[geo_name]
    w, h = geo["width"], geo["height"]

    print(f"=== Geometry: {geo['description']} ({w}x{h}) ===")
    print(f"    SrcId={src_id}, oriB={ori_b}, oriC={ori_c}, a3={a3_flag}\n")

    # Build the short-form 0x31 profile
    frame_rect, l_button, r_button, touchable_rect1 = build_touchpad_profile(w, h)
    payload_0x31 = build_short_form_0x31(
        frame_rect, l_button, r_button, touchable_rect1,
        touchable_rect2=None, src_id=src_id, disable_for_mini=0,
    )

    step = [0]  # mutable counter

    def do_send(cmd_type, payload_hex, label, retries=3):
        step[0] += 1
        if dry_run:
            ptype_hex = f"0x{cmd_type:02x}"
            plen = len(bytes.fromhex(payload_hex)) if payload_hex else 0
            payload_ascii = payload_hex if len(payload_hex) <= 80 else payload_hex[:40] + "..." + payload_hex[-40:]
            print(f"  Step {step[0]:2d} [DRY] type={ptype_hex} len={plen} {label}")
            if payload_ascii:
                print(f"              payload: {payload_ascii}")
        else:
            for attempt in range(retries):
                try:
                    send_frame(cmd_type, payload_hex)
                    print(f"  Step {step[0]:2d} sent type=0x{cmd_type:02x} {label}")
                    time.sleep(0.1)
                    return
                except usb.core.USBError as e:
                    if attempt < retries - 1:
                        print(f"  Step {step[0]:2d} USB error ({e}), waiting for reconnect...")
                        reset_device()
                        time.sleep(1.0)
                        try:
                            wait_for_device(timeout=8)
                            print(f"  Step {step[0]:2d} device reconnected, retrying...")
                        except RuntimeError as re_err:
                            print(f"  Step {step[0]:2d} reconnect failed: {re_err}")
                    else:
                        print(f"  Step {step[0]:2d} FAILED after {retries} attempts: {e}")
                        raise

    def do_send_bytes(cmd_type, payload, label, retries=3):
        step[0] += 1
        if dry_run:
            print(f"  Step {step[0]:2d} [DRY] type=0x{cmd_type:02x} len={len(payload)} {label}")
            print(f"              payload: {payload.hex()}")
        else:
            for attempt in range(retries):
                try:
                    send_frame_bytes(cmd_type, payload)
                    print(f"  Step {step[0]:2d} sent type=0x{cmd_type:02x} len={len(payload)} {label}")
                    time.sleep(0.1)
                    return
                except usb.core.USBError as e:
                    if attempt < retries - 1:
                        print(f"  Step {step[0]:2d} USB error ({e}), waiting for reconnect...")
                        reset_device()
                        time.sleep(1.0)
                        try:
                            wait_for_device(timeout=8)
                            print(f"  Step {step[0]:2d} device reconnected, retrying...")
                        except RuntimeError as re_err:
                            print(f"  Step {step[0]:2d} reconnect failed: {re_err}")
                    else:
                        print(f"  Step {step[0]:2d} FAILED after {retries} attempts: {e}")
                        raise

    def do_wait(seconds, label):
        step[0] += 1
        if dry_run:
            print(f"  Step {step[0]:2d} [DRY] wait {seconds}s {label}")
        else:
            print(f"  Step {step[0]:2d} waiting {seconds}s {label}")
            time.sleep(seconds)

    # ── Phase 1: Init + Toggle (may cause disconnect) ─────────────────

    # Init timing/threshold block
    do_send(0x4b, "0008b80b10271027", "timing/threshold")

    # Init OSK params
    do_send(0x21, "8001010000", "init OSK param")

    # Status pair
    do_send(0x28, "0000", "status pair")

    # Keepalive
    do_send(0x26, get_timestamp_bytes().hex(), "keepalive")

    if not minimal and not no_disable_touch:
        # Disable bottom panel touchscreen
        do_send(0x27, "0000", "disable bottom panel touch (panel=0, off)")

    # Enable touchpad mode
    do_send(0x25, "01", "touchpad mode ON")

    if not minimal:
        # State sync — in minimal mode, these go in phase 2 (matching README)
        do_send(0x20, "0100", "sync flag")
        do_send(0x21, "7e01000000", "touchpad-on state sync (7e)")

    # ── Reconnect break ───────────────────────────────────────────────

    if force_reset and not dry_run:
        print("\n  --- Forcing USB reset (unbind/bind 3-6)... ---")
        release_device()
        import subprocess as sp
        sp.run(["sudo", "bash", "-c", "echo 3-6 > /sys/bus/usb/drivers/usb/unbind"],
               check=True)
        time.sleep(2)
        sp.run(["sudo", "bash", "-c", "echo 3-6 > /sys/bus/usb/drivers/usb/bind"],
               check=True)
        time.sleep(2)
        print("  --- USB device re-bound. Re-acquiring... ---")
        wait_for_device(timeout=10)
        print("  --- Device reconnected. ---\n")
    elif not dry_run:
        print("\n  --- Phase 1 complete. Waiting for device settle... ---")
        time.sleep(wait_after_toggle)
        try:
            send_frame(0x26, get_timestamp_bytes().hex())
            print("  --- Device still alive, continuing. ---\n")
        except Exception:
            print("  --- Device disconnected. Waiting for reconnect... ---")
            reset_device()
            wait_for_device(timeout=10)
            print("  --- Device reconnected. ---\n")
    else:
        step[0] += 1
        print(f"  Step {step[0]:2d} [DRY] wait + reconnect check")

    # ── Phase 2: Config + Geometry ────────────────────────────────────

    # Keepalive (re-establish session)
    do_send(0x26, get_timestamp_bytes().hex(), "keepalive")

    if minimal:
        # README best replay order: state sync AFTER reconnect
        do_send(0x20, "0100", "sync flag")
        do_send(0x21, "7e01000000", "touchpad-on state sync (7e)")

    # Screen info bX, bY
    screen_info_40 = struct.pack("<BHH", 0x40, w, h)
    do_send_bytes(0x21, screen_info_40, f"screen info bX={w} bY={h}")

    # Screen info cX, cY
    screen_info_41 = struct.pack("<BHH", 0x41, w, h)
    do_send_bytes(0x21, screen_info_41, f"screen info cX={w} cY={h}")

    # Orientation sync — skip in minimal mode
    if not skip_ori and not minimal:
        ori_payload = struct.pack("<BBBBB", 0x31, ori_b, ori_c, 0x00, 0x00)
        do_send_bytes(0x21, ori_payload, f"orientation oriB={ori_b} oriC={ori_c}")
        do_send(0xa3, f"{a3_flag:02x}", f"post-orientation flag={a3_flag}")

    # ── Phase 4: Rectangle geometry ───────────────────────────────────

    # Short-form rectangle geometry
    if dry_run:
        frame_vals = struct.unpack("<HHHH", frame_rect)
        lbtn_vals = struct.unpack("<HHHH", l_button)
        rbtn_vals = struct.unpack("<HHHH", r_button)
        touch_vals = struct.unpack("<HHHH", touchable_rect1)
        packed = ((src_id & 0x3) << 1) | 0
        print(f"  Step -- [DRY] type=0x31 len=0x{len(payload_0x31):02x} short-form geometry")
        print(f"              frameRect:      left={frame_vals[0]} top={frame_vals[1]} right={frame_vals[2]} bottom={frame_vals[3]}")
        print(f"              LButton:        left={lbtn_vals[0]} top={lbtn_vals[1]} right={lbtn_vals[2]} bottom={lbtn_vals[3]}")
        print(f"              RButton:        left={rbtn_vals[0]} top={rbtn_vals[1]} right={rbtn_vals[2]} bottom={rbtn_vals[3]}")
        print(f"              touchableRect1: left={touch_vals[0]} top={touch_vals[1]} right={touch_vals[2]} bottom={touch_vals[3]}")
        print(f"              touchableRect2: zero (empty)")
        print(f"              packed flags:   0x{packed:02x} (SrcId={src_id}, DisableForMini=0)")
        print(f"              full payload:   {payload_0x31.hex()}")
    else:
        do_send_bytes(0x31, payload_0x31, "short-form geometry")

    # Final keepalive
    do_send(0x26, get_timestamp_bytes().hex(), "keepalive")

    print(f"\n=== Sequence complete ({geo_name}) ===\n")
    return True


def monitor_events(duration=10, keepalive=False, geo_name=None):
    """Monitor event6 (touchscreen) and event19 (touchpad) for activity.

    If keepalive=True, sends periodic 0x26 keepalives to the MCU during
    monitoring to prevent the MCU from timing out and restarting.
    """
    print(f"Monitoring /dev/input/event6 and event19 for {duration}s...")
    if keepalive:
        print("Keepalive pings will be sent during monitoring to keep MCU alive.")
    print("Touch the lower screen to see where events arrive.\n")

    # Use raw fd reads — works without evtest/evdev, no pipe-buffering issues
    _monitor_with_raw_read(duration, keepalive=keepalive, geo_name=geo_name)


def _find_event_device(name_substr):
    """Find an event device path by partial name match."""
    for f in sorted(os.listdir("/sys/class/input/")):
        if not f.startswith("event"):
            continue
        name_path = f"/sys/class/input/{f}/device/name"
        try:
            with open(name_path) as nf:
                name = nf.read().strip()
            if name_substr.lower() in name.lower():
                return f"/dev/input/{f}", name
        except (FileNotFoundError, PermissionError):
            pass
    return None, None


def _monitor_with_raw_read(duration, keepalive=False, geo_name=None):
    """Monitor using raw fd reads on /dev/input/eventX.

    Each input_event is 24 bytes on 64-bit Linux: struct input_event {
        struct timeval { long tv_sec, long tv_usec };
        __u16 type; __u16 code; __s32 value;
    }

    If keepalive=True, sends periodic state-refresh + keepalive to MCU.
    """
    INPUT_EVENT_SIZE = 24
    INPUT_EVENT_FMT = "llHHi"

    # Auto-detect current event device paths
    ts_path, ts_name = _find_event_device("Touchscreen Bottom")
    tp_path, tp_name = _find_event_device("Emulated Touchpad")

    targets = []
    if ts_path:
        targets.append((ts_path, "touchscreen", ts_name))
    else:
        print("  WARNING: Could not find bottom touchscreen device")
    if tp_path:
        targets.append((tp_path, "touchpad", tp_name))
    else:
        print("  WARNING: Could not find emulated touchpad device")

    devices = {}
    for path, label, name in targets:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            devices[path] = (fd, label)
            print(f"  Watching {path} ({name}) [{label}]")
        except OSError as e:
            print(f"  Cannot open {path}: {e}")

    if not devices:
        print("No devices to monitor.")
        return

    # Pre-build geometry payload for state refresh if we have geometry info
    geo_payload = None
    if keepalive and geo_name and geo_name in GEOMETRIES:
        geo = GEOMETRIES[geo_name]
        w, h = geo["width"], geo["height"]
        frame_rect, l_button, r_button, touchable_rect1 = build_touchpad_profile(w, h)
        geo_payload = build_short_form_0x31(
            frame_rect, l_button, r_button, touchable_rect1,
            touchable_rect2=None, src_id=0, disable_for_mini=0,
        )

    print(f"\nMonitoring for {duration}s... touch the lower screen now!\n")
    start = time.time()
    last_keepalive = time.time()
    ka_count = 0
    counts = {p: 0 for p in devices}
    SKIP_TYPES = {0}  # EV_SYN

    try:
        while time.time() - start < duration:
            # Keepalive cycle: aggressive periodic re-activation
            if keepalive and time.time() - last_keepalive >= 0.8:
                try:
                    ka_count += 1
                    # Drain any pending MCU responses
                    try:
                        dev = get_device()
                        dev.read(EP_IN, 512, timeout=10)
                    except Exception:
                        pass
                    # Send keepalive
                    send_frame(0x26, get_timestamp_bytes().hex())
                    # Re-affirm touchpad mode every 2nd cycle (~1.6s)
                    if ka_count % 2 == 0:
                        send_frame(0x25, "01")
                    # Re-send full state + geometry every 3rd cycle (~2.4s)
                    if ka_count % 3 == 0:
                        send_frame(0x20, "0100")
                        send_frame(0x21, "7e01000000")
                        if geo_payload:
                            send_frame_bytes(0x31, geo_payload)
                    last_keepalive = time.time()
                except Exception as e:
                    print(f"  [keepalive failed at {time.time()-start:.1f}s: {e}]")
                    break

            got_any = False
            for path, (fd, label) in devices.items():
                while True:
                    try:
                        data = os.read(fd, INPUT_EVENT_SIZE)
                        if len(data) == INPUT_EVENT_SIZE:
                            tv_sec, tv_usec, ev_type, ev_code, ev_value = \
                                struct.unpack(INPUT_EVENT_FMT, data)
                            counts[path] += 1
                            got_any = True
                            if ev_type not in SKIP_TYPES and counts[path] <= 30:
                                print(f"  [{label}] type={ev_type} code=0x{ev_code:03x} value={ev_value}")
                    except BlockingIOError:
                        break
                    except OSError:
                        break
            if not got_any:
                time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        for path, (fd, label) in devices.items():
            try:
                os.close(fd)
            except Exception:
                pass

    elapsed = time.time() - start
    print(f"\nEvent counts after {elapsed:.1f}s: " + ", ".join(
        f"{devices[p][1]}={counts[p]}" for p in devices
    ))
    if keepalive:
        print(f"Keepalives sent: {ka_count}")


# ─── Diagnostics ──────────────────────────────────────────────────────

def run_diagnostics():
    """Run diagnostics: send commands and read MCU responses + raw HID info."""
    print("=== Yoga Book 9 Touchpad Diagnostics ===\n")

    # 1. Read MCU response after sending 0x25 01
    print("1. Sending 0x25 01 and reading MCU response...")
    send_frame(0x25, "01")
    time.sleep(0.5)
    try:
        resp = read_response(timeout=2000, size=512)
        print(f"   Response ({len(resp)} bytes): {bytes(resp).hex()}")
        # Parse OSKP frames
        data = bytes(resp)
        pos = 0
        while pos + 6 <= len(data):
            if data[pos:pos+4] == b"OSKP":
                plen = struct.unpack("<H", data[pos+4:pos+6])[0]
                payload = data[pos+6:pos+6+plen]
                print(f"   OSKP frame: payload_len={plen} payload={payload.hex()}")
                pos += 6 + plen
            else:
                pos += 1
    except Exception as e:
        print(f"   No response: {e}")

    # 2. Send 0x21 7e and read response
    print("\n2. Sending 0x21 7e01000000 and reading response...")
    send_frame(0x21, "7e01000000")
    time.sleep(0.3)
    try:
        resp = read_response(timeout=2000, size=512)
        print(f"   Response ({len(resp)} bytes): {bytes(resp).hex()}")
    except Exception as e:
        print(f"   No response: {e}")

    # 3. Check touchpad HID report descriptor
    print("\n3. Touchpad HID report descriptor:")
    tp_path, tp_name = _find_event_device("Emulated Touchpad")
    if tp_path:
        evt_num = tp_path.replace("/dev/input/event", "")
        sysfs = f"/sys/class/input/event{evt_num}/device/device/report_descriptor"
        # Try alternate path
        import glob
        rd_paths = glob.glob(f"/sys/class/input/event{evt_num}/device/**/report_descriptor", recursive=True)
        if not rd_paths:
            rd_paths = glob.glob(f"/sys/class/input/event{evt_num}/**/report_descriptor", recursive=True)
        for rdp in rd_paths:
            try:
                with open(rdp, "rb") as f:
                    rd = f.read()
                print(f"   Found: {rdp} ({len(rd)} bytes)")
                print(f"   Hex: {rd.hex()}")
            except Exception as e:
                print(f"   Cannot read {rdp}: {e}")
        if not rd_paths:
            print(f"   No report_descriptor found for {tp_path}")
            # Try hidraw
            import glob as g
            for hr in sorted(os.listdir("/sys/class/hidraw/")):
                info = os.path.realpath(f"/sys/class/hidraw/{hr}/device")
                name_file = f"/sys/class/hidraw/{hr}/device/rd"
                # Check if this hidraw belongs to INGENIC
                if "17EF:6161" in info:
                    rd_file = f"/sys/class/hidraw/{hr}/device/report_descriptor"
                    try:
                        with open(rd_file, "rb") as f:
                            rd = f.read()
                        # Check if this is the touchpad collection
                        print(f"   {hr}: {len(rd)} bytes")
                        # Just show the size for now
                    except Exception:
                        pass
    else:
        print("   Touchpad device not found")

    # 4. Check what hidraw devices exist for INGENIC
    print("\n4. INGENIC hidraw devices:")
    for hr in sorted(os.listdir("/sys/class/hidraw/")):
        link = os.path.realpath(f"/sys/class/hidraw/{hr}/device")
        if "17EF:6161" in link:
            iface = link.split(":1.")[-1].split("/")[0] if ":1." in link else "?"
            print(f"   /dev/{hr}  interface={iface}  {link}")

    print("\n=== Diagnostics complete ===")


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Activate Yoga Book 9 virtual touchpad (transient test)")
    parser.add_argument(
        "--geometry", "-g",
        choices=list(GEOMETRIES.keys()),
        default="scaled",
        help="Panel geometry to test (default: scaled)")
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Print packets without sending")
    parser.add_argument(
        "--monitor", "-m",
        action="store_true",
        help="Monitor event6/event19 for 10s after activation")
    parser.add_argument(
        "--monitor-only",
        action="store_true",
        help="Only monitor, don't send activation sequence")
    parser.add_argument(
        "--src-id",
        type=int,
        default=0,
        choices=[0, 1, 2, 3],
        help="SrcId value for packed flags (default: 0)")
    parser.add_argument(
        "--ori-b",
        type=int,
        default=0,
        help="Orientation byte B for 0x21 0x31 command (default: 0)")
    parser.add_argument(
        "--ori-c",
        type=int,
        default=0,
        help="Orientation byte C for 0x21 0x31 command (default: 0)")
    parser.add_argument(
        "--a3-flag",
        type=int,
        default=1,
        help="Post-orientation flag for 0xa3 command (default: 1)")
    parser.add_argument(
        "--wait",
        type=float,
        default=2.0,
        help="Seconds to wait after 0x25 01 for device settle (default: 2.0)")
    parser.add_argument(
        "--no-ori",
        action="store_true",
        help="Skip orientation sync (0x21 0x31) and 0xa3 commands")
    parser.add_argument(
        "--keepalive-loop",
        type=int,
        default=0,
        metavar="SECONDS",
        help="After activation, send keepalives in a loop for N seconds")
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Re-enable bottom panel touchscreen and disable touchpad mode")
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="Use the exact README 'best replay' sequence (no extra commands)")
    parser.add_argument(
        "--force-reset",
        action="store_true",
        help="Force USB unbind/bind between phases to trigger re-enumeration")
    parser.add_argument(
        "--no-disable-touch",
        action="store_true",
        help="Skip the 0x27 0000 (disable bottom panel touch) command")
    parser.add_argument(
        "--diag",
        action="store_true",
        help="Run diagnostics: read MCU responses and raw HID report info")
    parser.add_argument(
        "--monitor-duration",
        type=int,
        default=10,
        metavar="SECONDS",
        help="How long to monitor events (default: 10)")
    parser.add_argument(
        "--monitor-with-keepalive",
        action="store_true",
        help="Run keepalive in background thread while monitoring events")
    args = parser.parse_args()

    if args.restore:
        print("Restoring normal touchscreen mode...")
        print("  Sending: 0x25 00 (touchpad off)")
        send_frame(0x25, "00")
        time.sleep(0.1)
        print("  Sending: 0x27 0001 (enable bottom panel touch)")
        send_frame(0x27, "0001")
        time.sleep(0.1)
        print("  Sending: 0x21 7e000000 (touchpad-off state sync)")
        send_frame(0x21, "7e000000")
        time.sleep(0.1)
        print("Done. Bottom panel touchscreen should work normally now.")
        return

    if args.diag:
        run_diagnostics()
        return

    if not args.monitor_only:
        print(f"Yoga Book 9 Touchpad Activation Test")
        print(f"=====================================")
        print(f"Geometry: {args.geometry}")
        print(f"Dry run:  {args.dry_run}")
        print(f"SrcId:    {args.src_id}")
        print(f"OriB/OriC: {args.ori_b}/{args.ori_c}")
        print(f"A3 flag:  {args.a3_flag}")
        print(f"Skip ori: {args.no_ori}")
        print()

        ok = send_activation_sequence(
            args.geometry,
            dry_run=args.dry_run,
            src_id=args.src_id,
            ori_b=args.ori_b,
            ori_c=args.ori_c,
            a3_flag=args.a3_flag if not args.no_ori else 0,
            wait_after_toggle=args.wait,
            skip_ori=args.no_ori,
            minimal=args.minimal,
            force_reset=args.force_reset,
            no_disable_touch=args.no_disable_touch,
        )
        if not ok:
            print("Activation failed.")
            sys.exit(1)

    if args.keepalive_loop > 0 and not args.dry_run:
        print(f"Sending keepalives for {args.keepalive_loop}s (Ctrl+C to stop)...")
        start = time.time()
        try:
            while time.time() - start < args.keepalive_loop:
                send_frame(0x26, get_timestamp_bytes().hex())
                print(f"  keepalive at {time.time() - start:.1f}s")
                time.sleep(1.5)
        except KeyboardInterrupt:
            print("  stopped.")

    if args.monitor or args.monitor_only or args.monitor_with_keepalive:
        use_ka = args.monitor_with_keepalive and not args.dry_run
        monitor_events(duration=args.monitor_duration, keepalive=use_ka,
                       geo_name=args.geometry if use_ka else None)


if __name__ == "__main__":
    main()
