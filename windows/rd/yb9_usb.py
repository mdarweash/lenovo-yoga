#!/usr/bin/env python3
"""
yb9_usb.py — Direct USB bulk transfer to INGENIC MCU (17ef:6161)

Interface 1, Bulk OUT EP 0x01, Bulk IN EP 0x81.
No serial driver needed — bypasses cdc_acm entirely.
"""

import usb.core
import usb.util
import struct
import sys
import time
from datetime import datetime

VID = 0x17ef
PID = 0x6161
IFACE = 1
EP_OUT = 0x01
EP_IN = 0x81
TIMEOUT = 5000

_device = None


def get_device():
    """Find and claim the INGENIC MCU USB device.

    Only claims interface 1 (vendor bulk). Leaves HID interfaces 2-6 alone
    so usbhid keeps providing touchscreen/keyboard/stylus input devices.
    """
    global _device
    if _device is not None:
        return _device

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError(f"Device {VID:04x}:{PID:04x} not found")

    # Only detach kernel driver from interface 1 (vendor bulk) if claimed.
    # Do NOT touch interfaces 0 (cdc_acm), 2-6 (usbhid) — they provide
    # the touchscreen, keyboard, stylus, and emulated touchpad devices.
    if dev.is_kernel_driver_active(IFACE):
        try:
            dev.detach_kernel_driver(IFACE)
        except usb.core.USBError as e:
            print(f"  [warn] cannot detach iface {IFACE}: {e}", file=sys.stderr)

    # Device is already configured by the kernel — do NOT call set_configuration()
    # as it will fail with "Resource busy" when other interfaces are in use.
    usb.util.claim_interface(dev, IFACE)

    _device = dev
    return dev


def release_device():
    """Release the USB device."""
    global _device
    if _device is not None:
        try:
            usb.util.release_interface(_device, IFACE)
            _device.attach_kernel_driver(IFACE)
        except Exception:
            pass
        _device = None


def reset_device():
    """Clear the cached device so get_device() will re-acquire it."""
    global _device
    if _device is not None:
        try:
            usb.util.dispose_resources(_device)
        except Exception:
            pass
        _device = None


def wait_for_device(timeout=10):
    """Wait for the INGENIC device to (re)appear, then claim it.

    Returns the device. Raises RuntimeError if not found within timeout.
    """
    reset_device()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return get_device()
        except RuntimeError:
            time.sleep(0.3)
    raise RuntimeError(f"Device {VID:04x}:{PID:04x} did not reappear within {timeout}s")


def send_frame(ptype, payload_hex=""):
    """Build and send an OSKP serial frame via USB bulk OUT.

    Wire format:
        4 bytes  magic "OSKP"
        2 bytes  LE16 wire_len = payload_len + 1
        1 byte   type
        N bytes  payload
    """
    dev = get_device()
    payload = bytes.fromhex(payload_hex) if payload_hex else b""
    wire_len = len(payload) + 1

    frame = b"OSKP"
    frame += struct.pack("<H", wire_len)
    frame += bytes([ptype])
    frame += payload

    dev.write(EP_OUT, frame, timeout=TIMEOUT)


def send_frame_bytes(ptype, payload=b""):
    """Send an OSKP frame with raw payload bytes."""
    dev = get_device()
    wire_len = len(payload) + 1

    frame = b"OSKP"
    frame += struct.pack("<H", wire_len)
    frame += bytes([ptype])
    frame += payload

    dev.write(EP_OUT, frame, timeout=TIMEOUT)


def read_response(timeout=2000, size=512):
    """Read a response from the MCU via USB bulk IN."""
    dev = get_device()
    return dev.read(EP_IN, size, timeout=timeout)


def get_timestamp():
    """Return current time as 7-byte hex string (year_le16, mon, day, hr, min, sec)."""
    now = datetime.now()
    return struct.pack("<HBBBBB",
        now.year,
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second,
    ).hex()
