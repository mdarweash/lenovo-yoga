#!/usr/bin/env python3
"""
dump-mcu-firmware.py — Dump MCU firmware from INGENIC IF4 (OSK Provider Firmware).

IF4 (hidraw6) exposes a firmware update interface:
  Report ID 0x01: Feature (60B) — command/response
  Report ID 0x30: Feature (7B)  — small control
  Report ID 0x40: Feature (127B) — large block read/write
  Report ID 0x42: Input (127B)  — large block read response

Usage:
    sudo python3 dump-mcu-firmware.py [--hidraw /dev/hidraw6]
"""
import sys
import os
import struct
import fcntl
import argparse

# HID ioctl constants
HIDIOCGRDESCSIZE = 0x80044801  # Get report descriptor size
HIDIOCGRDESC     = 0x90044802  # Get report descriptor
HIDIOCSFEATURE   = 0xC0044806  # Set feature report (len + 1 for report ID)
HIDIOCGFEATURE   = 0xC0044807  # Get feature report
HIDIOCGRAWNAME   = 0x80044804  # Get raw name

def hid_get_feature(fd, report_id, buf_size):
    """Send GET_REPORT (Feature) via ioctl. Returns bytes."""
    buf = bytes([report_id]) + b'\x00' * buf_size
    # ioctl: HIDIOCGFEATURE(len) where buf[0] = report_id
    result = fcntl.ioctl(fd, 0xC0044807, buf)
    return buf

def hid_set_feature(fd, report_id, data):
    """Send SET_REPORT (Feature) via ioctl."""
    buf = bytes([report_id]) + data
    fcntl.ioctl(fd, 0xC0044806, buf)

def read_hidraw(fd, size=512):
    """Read from hidraw device."""
    try:
        return os.read(fd, size)
    except OSError:
        return b''

def main():
    parser = argparse.ArgumentParser(description='Dump MCU firmware from IF4')
    parser.add_argument('--hidraw', default='/dev/hidraw6',
                        help='HID device for IF4 (default: /dev/hidraw6)')
    parser.add_argument('--probe', action='store_true',
                        help='Only probe the interface, don\'t dump')
    parser.add_argument('--output', default='mcu-firmware.bin',
                        help='Output file (default: mcu-firmware.bin)')
    args = parser.parse_args()

    print(f"Opening {args.hidraw}...")
    fd = os.open(args.hidraw, os.O_RDWR | os.O_NONBLOCK)

    # ── Probe: read Report ID 0x30 (7-byte feature, small control) ──
    print("\n=== Probing firmware interface ===")

    # Get device name
    try:
        name_buf = bytearray(256)
        fcntl.ioctl(fd, HIDIOCGRAWNAME, name_buf)
        name = name_buf.split(b'\x00')[0].decode('ascii', errors='replace')
        print(f"Device: {name}")
    except Exception as e:
        print(f"Name: {e}")

    # Try Report ID 0x01 Feature (60 bytes) — likely firmware version/info
    print("\n--- Report ID 0x01 (Feature, 60B) ---")
    try:
        buf = hid_get_feature(fd, 0x01, 60)
        data = bytes(buf)
        print(f"  Raw ({len(data)}B): {data[:32].hex()}")
        # Try to decode as ASCII
        ascii_parts = data[1:].split(b'\x00')[0]
        if len(ascii_parts) > 4 and all(32 <= b < 127 for b in ascii_parts):
            print(f"  ASCII: {ascii_parts.decode('ascii', errors='replace')}")
    except Exception as e:
        print(f"  Error: {e}")

    # Try Report ID 0x30 (7-byte feature) — small control
    print("\n--- Report ID 0x30 (Feature, 7B) ---")
    try:
        buf = hid_get_feature(fd, 0x30, 7)
        data = bytes(buf)
        print(f"  Raw ({len(data)}B): {data.hex()}")
    except Exception as e:
        print(f"  Error: {e}")

    # Try Report ID 0x40 (127-byte feature) — large block
    print("\n--- Report ID 0x40 (Feature, 127B) ---")
    try:
        # First try reading with address 0
        buf = hid_get_feature(fd, 0x40, 127)
        data = bytes(buf)
        print(f"  Raw ({len(data)}B): {data[:32].hex()}...")
        if data[1] != 0 or any(b != 0 for b in data[1:8]):
            print(f"  Non-zero data detected!")
    except Exception as e:
        print(f"  Error: {e}")

    # ── Try reading input for any responses ─────────────────────────
    print("\n--- Reading input (2s) ---")
    import time
    import select
    start = time.time()
    while time.time() - start < 2.0:
        readable, _, _ = select.select([fd], [], [], 0.2)
        if readable:
            data = os.read(fd, 512)
            if data:
                rid = data[0]
                print(f"  Input Report ID 0x{rid:02x} ({len(data)}B): {data[:20].hex()}...")

    if args.probe:
        os.close(fd)
        return

    # ── Firmware dump attempt ────────────────────────────────────────
    print(f"\n=== Attempting firmware dump ===")
    print(f"Output: {args.output}")

    firmware = bytearray()
    block_size = 127  # Report ID 0x40 Feature size

    # Strategy: Try sending read commands via Report ID 0x01 (command)
    # and reading blocks via Report ID 0x40/0x42
    #
    # The Windows driver (FusionTouchFirmwareUpdate.dll) likely uses:
    # 1. Send "read" command via Report ID 0x01 Output (60B)
    # 2. Read response via Report ID 0x40 Feature (127B) or Report ID 0x42 Input (127B)
    #
    # Since we don't know the exact protocol, try several approaches:

    # Approach 1: Sequential Feature reads at different offsets
    print("\nApproach 1: Sequential Feature reads (0x40)...")
    for offset in range(0, 0x10000, block_size):
        try:
            # Write address to Feature report
            addr_bytes = struct.pack('<H', offset)
            # Pad the command: [report_id] [addr_lo] [addr_hi] [pad...]
            cmd = addr_bytes + b'\x00' * (block_size - 2)
            hid_set_feature(fd, 0x40, cmd)
            # Read response
            resp = hid_get_feature(fd, 0x40, block_size)
            block = bytes(resp[1:])  # Skip report ID
            if any(b != 0 for b in block):
                firmware.extend(block)
                if len(firmware) <= block_size * 3:
                    print(f"  Offset 0x{offset:04x}: {block[:16].hex()}")
            else:
                # All zeros — might be past the end
                if len(firmware) > 0 and len(firmware) % (block_size * 4) == 0:
                    break
        except Exception as e:
            print(f"  Offset 0x{offset:04x}: {e}")
            break

    if firmware:
        with open(args.output, 'wb') as f:
            f.write(firmware)
        print(f"\nDumped {len(firmware)} bytes to {args.output}")
        print(f"First 64 bytes: {firmware[:64].hex()}")
    else:
        print("\nNo firmware data read. Try approach 2 (command-based).")

        # Approach 2: Send command via Report ID 0x01 then read
        print("\nApproach 2: Command via 0x01, then read 0x42...")
        # Try various command bytes for "read firmware"
        for cmd_byte in [0x01, 0x02, 0x03, 0x10, 0x20, 0x30, 0x52, 0x53, 0x82, 0xA0]:
            try:
                cmd = bytes([cmd_byte]) + b'\x00' * 59
                hid_set_feature(fd, 0x01, cmd)
                time.sleep(0.1)
                # Check for input response
                readable, _, _ = select.select([fd], [], [], 0.3)
                if readable:
                    data = os.read(fd, 512)
                    if data:
                        rid = data[0]
                        print(f"  cmd=0x{cmd_byte:02x}: Report ID 0x{rid:02x} ({len(data)}B): {data[:20].hex()}")
            except Exception as e:
                print(f"  cmd=0x{cmd_byte:02x}: {e}")

    os.close(fd)
    print("\nDone.")

if __name__ == "__main__":
    main()
