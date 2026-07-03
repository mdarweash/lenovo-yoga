#!/usr/bin/env python3
"""
Yoga Book 9 14 — Userspace touch driver for INGENIC dual-screen touch.

Reads raw HID reports from hidraw, parses finger touch coordinates,
and injects proper EV_ABS events via uinput for both screens.

Fixes:
  #1 — eDP-1 touch rotation (180° transform applied)
  #2 — eDP-2 touch not working (report ID 0x38 now handled)
  #3 — Touchpad (report ID 0x50) creates separate uinput pointer device

Usage: sudo python3 yoga-touch-driver.py [--detach]
"""

import os
import sys
import struct
import fcntl
import select
import signal
import time
import argparse
import logging

# ─── HID Report layout ───────────────────────────────────────────────
# Each finger collection in the HID descriptor is 5 bytes:
#   Byte 0: bits 0-1 = Altitude + Tablet Pick (Constant), bit 2 = Touch (1=touched), bits 3-7 = Contact ID
#   Bytes 1-2: X coordinate (16-bit LE, max 30182)
#   Bytes 3-4: Y coordinate (16-bit LE, max 18864)

FINGER_SIZE = 5
MAX_FINGERS = 10
X_MAX = 30182
Y_MAX = 18864

# Whiteboard/touchpad report
WB_X_MAX = 3017
WB_Y_MAX = 1700

# ─── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("yoga-touch")

# ─── Linux input constants ───────────────────────────────────────────
# uinput ioctl numbers — must use proper Linux ioctl encoding
def _IOC(dir_, type_, nr, size):
    return (dir_ << 30) | (size << 16) | (type_ << 8) | nr

_IOC_WRITE = 1
_IOC_READ  = 2
_IOC_NONE  = 0

_UI_BASE = ord("U")
UI_DEV_CREATE  = _IOC(_IOC_NONE, _UI_BASE, 0, 0)
UI_DEV_DESTROY = _IOC(_IOC_NONE, _UI_BASE, 1, 0)
UI_SET_EVBIT   = _IOC(_IOC_WRITE, _UI_BASE, 100, 4)  # int
UI_SET_KEYBIT  = _IOC(_IOC_WRITE, _UI_BASE, 101, 4)  # int
UI_SET_ABSBIT  = _IOC(_IOC_WRITE, _UI_BASE, 103, 4)  # int
UI_SET_PROPBIT = _IOC(_IOC_WRITE, _UI_BASE, 108, 4)  # int

# input event types
EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
SYN_REPORT = 0x00
BTN_TOUCH = 0x14a
ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_SLOT = 0x2f
ABS_MT_TRACKING_ID = 0x39
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TOOL_TYPE = 0x37

# input properties
INPUT_PROP_DIRECT = 0x01

# uinput_user_dev struct layout (from linux/uinput.h)
# struct uinput_user_dev {
#   char name[UINPUT_MAX_NAME_SIZE];     128 bytes
#   char phys[256];                       256 bytes
#   char uniq[256];                       256 bytes
#   __u16 id[4];  {bustype,vendor,product,version}  8 bytes
#   __u32 ff_effects_max;                 4 bytes
#   __s32 absmax[ABS_CNT];               64 * 4 = 256 bytes
#   __s32 absmin[ABS_CNT];               256 bytes
#   __s32 absfuzz[ABS_CNT];              256 bytes
#   __s32 absflat[ABS_CNT];              256 bytes
# };
UINPUT_MAX_NAME_SIZE = 128
ABS_CNT = 64


def _ui_ioctl(fd, request, value=0):
    """Perform a uinput ioctl."""
    fcntl.ioctl(fd, request, value)


def setup_uinput_device(name, bustype, vendor, product, x_max, y_max,
                        is_direct=True):
    """Create and configure a uinput device. Returns (fd, device_node)."""
    fd = os.open("/dev/uinput", os.O_RDWR)

    # Enable event types
    _ui_ioctl(fd, UI_SET_EVBIT, EV_SYN)
    _ui_ioctl(fd, UI_SET_EVBIT, EV_KEY)
    _ui_ioctl(fd, UI_SET_EVBIT, EV_ABS)

    # Enable keys
    _ui_ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH)

    # Enable absolute axes
    _ui_ioctl(fd, UI_SET_ABSBIT, ABS_X)
    _ui_ioctl(fd, UI_SET_ABSBIT, ABS_Y)
    _ui_ioctl(fd, UI_SET_ABSBIT, ABS_MT_SLOT)
    _ui_ioctl(fd, UI_SET_ABSBIT, ABS_MT_TRACKING_ID)
    _ui_ioctl(fd, UI_SET_ABSBIT, ABS_MT_POSITION_X)
    _ui_ioctl(fd, UI_SET_ABSBIT, ABS_MT_POSITION_Y)
    _ui_ioctl(fd, UI_SET_ABSBIT, ABS_MT_TOOL_TYPE)

    # Set INPUT_PROP_DIRECT so Wayland/compositor treats this as a touchscreen
    # (Skip if kernel doesn't support it — not critical)
    if is_direct:
        try:
            _ui_ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
        except OSError:
            pass

    # Build uinput_user_dev struct and write it to the fd
    name_bytes = name.encode("utf-8")[:UINPUT_MAX_NAME_SIZE - 1].ljust(UINPUT_MAX_NAME_SIZE, b"\x00")
    phys_bytes = b"usb-0000:00:00.3/input3".ljust(256, b"\x00")
    uniq_bytes = b"\x00" * 256
    id_bytes = struct.pack("<HHHH", bustype, vendor, product, 0)
    ff_max = struct.pack("<I", 0)

    absmax = [0] * ABS_CNT
    absmin = [0] * ABS_CNT
    absfuzz = [0] * ABS_CNT
    absflat = [0] * ABS_CNT

    absmax[ABS_X] = x_max
    absmax[ABS_Y] = y_max
    absmax[ABS_MT_SLOT] = MAX_FINGERS - 1
    absmax[ABS_MT_TRACKING_ID] = MAX_FINGERS - 1
    absmax[ABS_MT_POSITION_X] = x_max
    absmax[ABS_MT_POSITION_Y] = y_max
    absmax[ABS_MT_TOOL_TYPE] = 1  # 0=Finger, 1=Pen

    abs_arrays = b""
    for arr in (absmax, absmin, absfuzz, absflat):
        abs_arrays += struct.pack(f"<{ABS_CNT}i", *arr)

    udev_struct = name_bytes + phys_bytes + uniq_bytes + id_bytes + ff_max + abs_arrays
    os.write(fd, udev_struct)

    # Create the device
    _ui_ioctl(fd, UI_DEV_CREATE)

    # Find the device node by scanning /dev/input
    device_node = None
    for _ in range(50):
        try:
            for f in os.listdir("/dev/input/"):
                if not f.startswith("event"):
                    continue
                try:
                    npath = f"/sys/class/input/{f}/device/name"
                    with open(npath) as nf:
                        devname = nf.read().strip()
                    if devname == name:
                        device_node = f"/dev/input/{f}"
                        break
                except (FileNotFoundError, PermissionError):
                    pass
            if device_node:
                break
        except (FileNotFoundError, PermissionError):
            pass
        time.sleep(0.05)

    if device_node:
        log.info("Created uinput device: %s", device_node)
    else:
        log.warning("Created uinput fd=%d (could not determine device node)", fd)

    return fd, device_node


# input_event struct: struct timeval { long tv_sec; long tv_usec; } + u16 type + u16 code + s32 value
# On 64-bit Linux, long = 8 bytes, so total = 8 + 8 + 2 + 2 + 4 = 24 bytes
INPUT_EVENT_SIZE = struct.calcsize("llHHi")  # 24 bytes on x86_64
INPUT_EVENT_FMT = "llHHi"


def emit_event(fd, ev_type, ev_code, ev_value):
    """Emit a single input event via uinput by writing an input_event struct."""
    ev = struct.pack(INPUT_EVENT_FMT, 0, 0, ev_type, ev_code, ev_value)
    os.write(fd, ev)


def emit_syn(fd):
    """Emit SYN_REPORT."""
    emit_event(fd, EV_SYN, SYN_REPORT, 0)


def inject_touch_down(fd, contact_id, x, y):
    """Inject a finger touch down event using multi-touch protocol."""
    emit_event(fd, EV_ABS, ABS_MT_SLOT, contact_id)
    emit_event(fd, EV_ABS, ABS_MT_TRACKING_ID, contact_id + 1)
    emit_event(fd, EV_ABS, ABS_MT_POSITION_X, x)
    emit_event(fd, EV_ABS, ABS_MT_POSITION_Y, y)
    emit_event(fd, EV_ABS, ABS_MT_TOOL_TYPE, 0)  # Finger
    emit_event(fd, EV_KEY, BTN_TOUCH, 1)
    emit_syn(fd)


def inject_touch_up(fd, contact_id):
    """Inject a finger touch up event."""
    emit_event(fd, EV_ABS, ABS_MT_SLOT, contact_id)
    emit_event(fd, EV_ABS, ABS_MT_TRACKING_ID, -1)
    emit_event(fd, EV_KEY, BTN_TOUCH, 0)
    emit_syn(fd)


def inject_touch_move(fd, contact_id, x, y):
    """Inject a finger move event."""
    emit_event(fd, EV_ABS, ABS_MT_SLOT, contact_id)
    emit_event(fd, EV_ABS, ABS_MT_POSITION_X, x)
    emit_event(fd, EV_ABS, ABS_MT_POSITION_Y, y)
    emit_syn(fd)


# ─── Rotation transform ──────────────────────────────────────────────
# eDP-1 is rotated 180° (inverted). For 180° rotation:
#   x' = X_MAX - x
#   y' = Y_MAX - y

def transform_180(x, y, x_max, y_max):
    return x_max - x, y_max - y


# ─── Parse HID report ────────────────────────────────────────────────────

def parse_touch_report(data):
    """Parse a touch report and return list of (contact_id, touching, x, y)."""
    fingers = []
    for i in range(MAX_FINGERS):
        offset = i * FINGER_SIZE
        if offset + FINGER_SIZE > len(data):
            break

        flags = data[offset]
        touch = (flags >> 2) & 1
        contact_id = (flags >> 3) & 0x1F
        x = data[offset + 1] | (data[offset + 2] << 8)
        y = data[offset + 3] | (data[offset + 4] << 8)

        fingers.append((contact_id, touch, x, y))
    return fingers


# ─── Main driver ─────────────────────────────────────────────────────────

def find_hidraw_device():
    """Find the hidraw device for INGENIC touch interface (USB interface 3)."""
    for dev in sorted(os.listdir("/sys/class/hidraw/")):
        link = os.path.realpath(f"/sys/class/hidraw/{dev}/device")
        # Match 17EF:6161 on USB interface :1.3 (interface 3)
        if "17EF:6161" in link and ":1.3/" in link:
            path = f"/dev/{dev}"
            if os.path.exists(path):
                log.info("Auto-detected hidraw device: %s", path)
                return path
    # Fallback
    return "/dev/hidraw5"


def run(hidraw_path=None):
    """Main touch driver loop."""

    # Track previous touch state per contact per screen
    prev_touch1 = {}  # contact_id -> (x, y) for eDP-1
    prev_touch2 = {}  # contact_id -> (x, y) for eDP-2
    prev_wb = {}      # contact_id -> (x, y) for touchpad

    # Find hidraw device
    if hidraw_path is None:
        hidraw_path = find_hidraw_device()
    log.info("Opening hidraw device: %s", hidraw_path)
    hidraw_fd = os.open(hidraw_path, os.O_RDONLY | os.O_NONBLOCK)

    # Create uinput devices
    log.info("Creating uinput devices...")
    edp1_fd, edp1_node = setup_uinput_device(
        "Yoga Book 9 eDP-1 Touch",
        bustype=0x03, vendor=0x17EF, product=0x6161,
        x_max=X_MAX, y_max=Y_MAX,
    )
    edp2_fd, edp2_node = setup_uinput_device(
        "Yoga Book 9 eDP-2 Touch",
        bustype=0x03, vendor=0x17EF, product=0x6161,
        x_max=X_MAX, y_max=Y_MAX,
    )
    wb_fd, wb_node = setup_uinput_device(
        "Yoga Book 9 Touchpad",
        bustype=0x03, vendor=0x17EF, product=0x6161,
        x_max=WB_X_MAX, y_max=WB_Y_MAX,
        is_direct=False,
    )

    log.info("eDP-1: %s, eDP-2: %s, Touchpad: %s", edp1_node, edp2_node, wb_node)
    log.info("Listening for touch reports. Ctrl+C to stop.")

    # Signal handler for cleanup
    def cleanup(signum=None, frame=None):
        log.info("Shutting down...")
        for fd in (hidraw_fd, edp1_fd, edp2_fd, wb_fd):
            try:
                _ui_ioctl(fd, UI_DEV_DESTROY)
                os.close(fd)
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # Main event loop
    try:
        while True:
            readable, _, _ = select.select([hidraw_fd], [], [], 0.5)
            if not readable:
                continue

            try:
                data = os.read(hidraw_fd, 512)
            except OSError:
                log.error("Read error, continuing...")
                time.sleep(0.1)
                continue

            if len(data) < 2:
                continue

            report_id = data[0]
            payload = data[1:]

            if report_id == 0x30:
                # eDP-1 touch — apply 180° rotation
                fingers = parse_touch_report(payload)
                for contact_id, touching, x, y in fingers:
                    rx, ry = transform_180(x, y, X_MAX, Y_MAX)
                    if touching:
                        if contact_id in prev_touch1:
                            inject_touch_move(edp1_fd, contact_id, rx, ry)
                        else:
                            inject_touch_down(edp1_fd, contact_id, rx, ry)
                        prev_touch1[contact_id] = (rx, ry)
                    else:
                        if contact_id in prev_touch1:
                            inject_touch_up(edp1_fd, contact_id)
                            del prev_touch1[contact_id]

            elif report_id == 0x38:
                # eDP-2 touch — no rotation
                fingers = parse_touch_report(payload)
                for contact_id, touching, x, y in fingers:
                    if touching:
                        if contact_id in prev_touch2:
                            inject_touch_move(edp2_fd, contact_id, x, y)
                        else:
                            inject_touch_down(edp2_fd, contact_id, x, y)
                        prev_touch2[contact_id] = (x, y)
                    else:
                        if contact_id in prev_touch2:
                            inject_touch_up(edp2_fd, contact_id)
                            del prev_touch2[contact_id]

            elif report_id == 0x50:
                # Whiteboard/touchpad — no rotation
                fingers = parse_touch_report(payload)
                for contact_id, touching, x, y in fingers:
                    if touching:
                        if contact_id in prev_wb:
                            inject_touch_move(wb_fd, contact_id, x, y)
                        else:
                            inject_touch_down(wb_fd, contact_id, x, y)
                        prev_wb[contact_id] = (x, y)
                    else:
                        if contact_id in prev_wb:
                            inject_touch_up(wb_fd, contact_id)
                            del prev_wb[contact_id]

    except KeyboardInterrupt:
        cleanup()


def main():
    parser = argparse.ArgumentParser(description="Yoga Book 9 dual-screen touch driver")
    parser.add_argument("--detach", action="store_true", help="Run in background (daemon)")
    parser.add_argument("--hidraw", default=None, help="Path to hidraw device (auto-detected if omitted)")
    args = parser.parse_args()

    if args.detach:
        # Simple double-fork daemonization
        if os.fork() > 0:
            sys.exit(0)
        os.setsid()
        if os.fork() > 0:
            sys.exit(0)
        log.info("Running in background (detached)")

    run(args.hidraw)


if __name__ == "__main__":
    main()
