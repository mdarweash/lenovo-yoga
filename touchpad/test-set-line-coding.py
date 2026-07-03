#!/usr/bin/env python3
"""
test-set-line-coding.py — Test which SET_LINE_CODING parameters crash the MCU.

Sends SET_LINE_CODING via pyusb control transfer on interface 0,
then checks if the MCU is still responsive.

Usage:
    sudo python3 test-set-line-coding.py [baud_rate]
    
    If no baud_rate given, tests common rates: 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600
"""
import sys, os, time, struct
import usb.core, usb.util

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "windows", "rd"))
from yb9_usb import VID, PID, send_frame, wait_for_device, release_device, EP_IN, get_device

IFACE0 = 0
SET_LINE_CODING = 0x20
SET_CONTROL_LINE_STATE = 0x22

def build_line_coding(baud=9600, stop_bits=0, parity=0, data_bits=8):
    """Build 7-byte SET_LINE_CODING payload."""
    return struct.pack("<IBBB", baud, stop_bits, parity, data_bits)

def send_set_line_coding(dev, baud, stop_bits=0, parity=0, data_bits=8):
    """Send SET_LINE_CODING control transfer."""
    data = build_line_coding(baud, stop_bits, parity, data_bits)
    try:
        dev.ctrl_transfer(0x21, SET_LINE_CODING, 0, IFACE0, data)
        return True
    except usb.core.USBError as e:
        return False

def send_set_control_line_state(dev, dtr=True, rts=True):
    """Send SET_CONTROL_LINE_STATE."""
    value = (1 if dtr else 0) | (2 if rts else 0)
    try:
        dev.ctrl_transfer(0x21, SET_CONTROL_LINE_STATE, value, IFACE0, b"")
        return True
    except usb.core.USBError:
        return False

def check_mcu_alive(timeout=3):
    """Check if MCU responds to a keepalive by trying to read a response."""
    try:
        # Send a keepalive
        from datetime import datetime
        now = datetime.now()
        ts = struct.pack("<HBBBBB", now.year, now.month, now.day, now.hour, now.minute, now.second)
        send_frame(0x26, ts.hex())
        time.sleep(0.5)
        # Try to read response
        dev = get_device()
        try:
            data = dev.read(EP_IN, 512, timeout=int(timeout * 1000))
            return True
        except Exception:
            # No response, but USB still works
            return True  # USB still up = MCU alive
    except Exception:
        return False

def wait_for_reenumeration(old_dev, timeout=10):
    """Wait for MCU to re-enumerate after crash."""
    print("    Waiting for re-enumeration...", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        try:
            new_dev = usb.core.find(idVendor=VID, idProduct=PID)
            if new_dev is not None and new_dev != old_dev:
                print(f" back in {time.time()-start:.1f}s")
                return new_dev
            # Also check if old dev still works
            if new_dev is not None:
                print(f" back in {time.time()-start:.1f}s")
                return new_dev
        except Exception:
            pass
        time.sleep(0.5)
    print(" TIMEOUT")
    return None

def test_baud(baud, stop_bits=0, parity=0, data_bits=8):
    """Test a specific SET_LINE_CODING configuration."""
    params = f"{baud} {data_bits}{parity}{stop_bits}"
    print(f"\n--- Testing {params} ---")
    
    # Find device
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("  Device not found!")
        return None
    
    # Claim interface 0
    try:
        if dev.is_kernel_driver_active(IFACE0):
            dev.detach_kernel_driver(IFACE0)
        usb.util.claim_interface(dev, IFACE0)
    except Exception as e:
        print(f"  Cannot claim interface 0: {e}")
        return None
    
    # Send SET_LINE_CODING
    print(f"  Sending SET_LINE_CODING({params})...", end="", flush=True)
    try:
        ok = send_set_line_coding(dev, baud, stop_bits, parity, data_bits)
        if ok:
            print(" sent OK")
        else:
            print(" USB error (may have crashed)")
    except Exception as e:
        print(f" exception: {e}")
    
    # Check if MCU survived
    time.sleep(1.0)
    alive = check_mcu_alive(timeout=2)
    
    # Release interface
    try:
        usb.util.release_interface(dev, IFACE0)
    except Exception:
        pass
    
    if alive:
        print(f"  ✓ MCU ALIVE after {params}")
        return True
    else:
        print(f"  ✗ MCU CRASHED with {params}")
        # Wait for re-enumeration
        new_dev = wait_for_reenumeration(dev, timeout=10)
        return False

def main():
    if len(sys.argv) > 1:
        bauds = [int(sys.argv[1])]
    else:
        bauds = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
    
    print("SET_LINE_CODING crash test")
    print(f"Will test baud rates: {bauds}")
    print("Interface 0 must be authorized for this to work.")
    
    # First check device is accessible
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("ERROR: Device not found")
        sys.exit(1)
    print(f"Device found: {dev}")
    
    results = {}
    for baud in bauds:
        result = test_baud(baud)
        results[baud] = result
        if result is None:
            print("  Skipping remaining tests (device unavailable)")
            break
        time.sleep(2)  # settle between tests
    
    print("\n\n=== Results ===")
    for baud, result in results.items():
        if result is True:
            print(f"  {baud:>7d}: ✓ ALIVE")
        elif result is False:
            print(f"  {baud:>7d}: ✗ CRASHED")
        else:
            print(f"  {baud:>7d}: ? SKIPPED")
    
    safe = [b for b, r in results.items() if r is True]
    if safe:
        print(f"\nSafe baud rates: {safe}")
    else:
        print("\nNo safe baud rates found!")

if __name__ == "__main__":
    main()
