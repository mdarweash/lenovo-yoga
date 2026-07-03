#!/usr/bin/env python3
"""Fetch and parse HID report descriptor from INGENIC USB device via pyusb."""

import sys

USAGE_PAGES = {
    0x0: "Undefined", 0x1: "Generic Desktop", 0x2: "Simulation",
    0x4: "Physical", 0x7: "Keyboard/Keypad", 0x8: "LED",
    0x9: "Button", 0xC: "Consumer", 0xD: "Digitizer",
    0xFF00: "Vendor Defined",
}
USAGE_NAMES_D = {
    0x0: "Undefined", 0x1: "Digitizer", 0x2: "Pen", 0x3: "Touch Screen",
    0x4: "Touch Pad", 0x5: "Whiteboard", 0x20: "Stylus", 0x22: "Finger",
    0x30: "Tip Pressure", 0x32: "In Range", 0x33: "Touch", 0x34: "Un-touch",
    0x37: "Data Valid", 0x38: "Transducer Index", 0x41: "Azimuth",
    0x42: "Altitude", 0x43: "Tip Switch", 0x44: "Secondary Tip Switch",
    0x45: "Barrel Switch", 0x46: "Eraser", 0x47: "Tablet Pick",
    0x51: "Contact Identifier", 0x52: "Contact Count", 0x53: "Contact Count Maximum",
}
USAGE_NAMES_GD = {
    0x0: "Undefined", 0x1: "Pointer", 0x2: "Mouse", 0x30: "X", 0x31: "Y",
    0x32: "Z", 0x33: "Rx", 0x34: "Ry", 0x35: "Rz", 0x36: "Slider",
    0x37: "Dial", 0x38: "Wheel", 0x39: "Hat Switch",
}
COLLECTION_TYPES = {
    0: "Physical", 1: "Application", 2: "Logical", 3: "Report",
    4: "Named Array", 5: "Usage Switch", 6: "Usage Modifier",
}

def get_usage_name(page, usage):
    if page == 0x0D:
        return USAGE_NAMES_D.get(usage, f"Digitizer:{usage:#x}")
    elif page == 0x01:
        return USAGE_NAMES_GD.get(usage, f"GD:{usage:#x}")
    pname = USAGE_PAGES.get(page, f"Page:{page:#x}")
    return f"{pname}:Usage:{usage:#x}"

def parse_hid_descriptor(data):
    i = 0
    indent = 0
    usage_page = 0
    lines = []

    while i < len(data):
        b = data[i]
        item_type = (b >> 2) & 0x3
        item_tag = (b >> 4) & 0xF
        item_size = b & 0x3
        if item_size == 3:
            item_size = 4
        i += 1
        if i + item_size > len(data):
            break
        value = 0
        for j in range(item_size):
            value |= data[i + j] << (8 * j)
        i += item_size
        prefix = "  " * indent

        if item_type == 0:  # Main
            if item_tag == 0xA:
                ctype = COLLECTION_TYPES.get(value, str(value))
                cname = ctype if value <= 6 else get_usage_name(usage_page, value)
                lines.append(f"{prefix}Collection({cname})")
                indent += 1
            elif item_tag == 0xC:
                indent = max(0, indent - 1)
                lines.append(f"{prefix}End Collection")
            elif item_tag in (0x8, 0x9, 0xB):
                names = {0x8: "Input", 0x9: "Output", 0xB: "Feature"}
                flags = []
                flags.append("Data" if value & 1 else "Constant")
                if value & 2: flags.append("Variable")
                if value & 4: flags.append("Relative")
                if value & 0x10: flags.append("No Null")
                if value & 0x20: flags.append("Volatile")
                if value & 0x100: flags.append("No Preferred")
                lines.append(f"{prefix}  {names[item_tag]}({', '.join(flags)})")
        elif item_type == 1:  # Global
            if item_tag == 0x0:
                usage_page = value
                pname = USAGE_PAGES.get(value, f"{value:#x}")
                lines.append(f"{prefix}Usage Page({pname})")
            elif item_tag in (0x1, 0x2):
                name = "Logical Minimum" if item_tag == 1 else "Logical Maximum"
                lines.append(f"{prefix}{name}({value})")
            elif item_tag in (0x4, 0x5):
                name = "Physical Minimum" if item_tag == 4 else "Physical Maximum"
                lines.append(f"{prefix}{name}({value})")
            elif item_tag == 0x7:
                lines.append(f"{prefix}Report Size({value})")
            elif item_tag == 0x8:
                lines.append(f"{prefix}Report ID({value})")
            elif item_tag == 0x9:
                lines.append(f"{prefix}Report Count({value})")
            elif item_tag == 0x6:
                usage_page = value
                lines.append(f"{prefix}Usage Page({value:#06x})")
        elif item_type == 2:  # Local
            if item_tag == 0x0:
                lines.append(f"{prefix}Usage({get_usage_name(usage_page, value)})")
            elif item_tag == 0x1:
                lines.append(f"{prefix}Usage Minimum({get_usage_name(usage_page, value)})")
            elif item_tag == 0x2:
                lines.append(f"{prefix}Usage Maximum({get_usage_name(usage_page, value)})")
    return lines

def main():
    import usb.core

    dev = usb.core.find(idVendor=0x17EF, idProduct=0x6161)
    if dev is None:
        print("Device 17ef:6161 not found")
        sys.exit(1)

    # Interface 3 is the touch interface, report descriptor is 2756 bytes
    desc_len = 2756
    print(f"HID Report Descriptor length: {desc_len} bytes (from lsusb)")
    print()

    # Detach kernel driver to allow direct access
    try:
        if dev.is_kernel_driver_active(3):
            dev.detach_kernel_driver(3)
            print("Detached kernel driver from interface 3")
    except Exception as e:
        print(f"Note: {e}")

    # Fetch the report descriptor directly via USB control transfer
    desc_data = dev.ctrl_transfer(
        0x81,                   # bmRequestType: USB_DIR_IN | USB_TYPE_CLASS | USB_RECIP_INTERFACE
        0x06,                   # bRequest: GET_DESCRIPTOR
        0x2200,                 # wValue: HID report descriptor type (0x22) << 8
        3,                      # wIndex: interface number
        desc_len,               # length
        timeout=5000,
    )

    print(f"Received {len(desc_data)} bytes\n")

    # Print raw bytes
    print("Raw descriptor bytes:")
    for i in range(0, len(desc_data), 16):
        chunk = desc_data[i:i+16]
        hex_str = " ".join(f"{b:02x}" for b in chunk)
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {i:04x}: {hex_str:<48s} {ascii_str}")

    # Parse
    print("\n=== Parsed HID Report Descriptor ===\n")
    for line in parse_hid_descriptor(desc_data):
        print(line)

if __name__ == "__main__":
    main()
