#!/bin/bash
# Dump HID report descriptor from hidraw5 (INGENIC touch, interface 3)
# Usage: sudo ./show-hid-descriptor.sh

sudo python3 -c "
import os, fcntl, array

path = '/dev/hidraw5'
fd = os.open(path, os.O_RDONLY)

# HIDIOCGRDESCSIZE = 0x80044801
buf = array.array('i', [0])
fcntl.ioctl(fd, 0x80044801, buf, True)
desc_size = buf[0]
print(f'HID Report Descriptor size: {desc_size} bytes')
print()

# HIDIOCGRDESC = 0x80484802
desc = array.array('B', [0] * desc_size)
fcntl.ioctl(fd, 0x80484802, desc, True)
os.close(fd)

# Print raw bytes
print('Raw descriptor bytes:')
for i in range(0, desc_size, 16):
    chunk = desc[i:i+16]
    hex_str = ' '.join(f'{b:02x}' for b in chunk)
    ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
    print(f'  {i:04x}: {hex_str:<48s} {ascii_str}')

# Basic HID report descriptor parser
print()
print('=== Parsed HID Report Descriptor ===')
print()

# HID Item types
TYPE_MAIN = 0
TYPE_GLOBAL = 1
TYPE_LOCAL = 2

# Main items
MAIN_ITEMS = {0x8: 'Input', 0x9: 'Output', 0xA: 'Collection', 0xB: 'Feature', 0xC: 'End Collection'}

# Global items
GLOBAL_ITEMS = {0x0: 'Usage Page', 0x1: 'Logical Minimum', 0x3: 'Logical Maximum',
                0x4: 'Physical Minimum', 0x5: 'Physical Maximum', 0x7: 'Report Size',
                0x8: 'Report ID', 0x9: 'Report Count', 0xA: 'Push', 0xB: 'Pop',
                0x6: 'Usage Page (extended)', 0x2: 'Usage (extended)'}

# Local items
LOCAL_ITEMS = {0x0: 'Usage', 0x1: 'Usage Minimum', 0x2: 'Usage Maximum'}

USAGE_PAGES = {0x0: 'Undefined', 0x1: 'Generic Desktop', 0x2: 'Simulation',
               0x4: 'Physical', 0x6: 'Generic Device Controls', 0x7: 'Keyboard/Keypad',
               0x8: 'LED', 0x9: 'Button', 0xA: 'Ordinal', 0xB: 'Telephony',
               0xC: 'Consumer', 0xD: 'Digitizer', 0xE: 'Haptics', 0xF: 'Physical Interface',
               0x10: 'Unicode', 0x11: 'Eye & Head Trackers', 0x12: 'Monitor',
               0x13: 'Monitor Enumerated', 0x14: 'VESA Virtual Controls',
               0x80: 'Monitor', 0x81: 'Monitor Enumerated', 0x82: 'VESA Virtual Controls',
               0x83: 'Monitor Allied', 0x84: 'Monitor Allied Enumerated',
               0xFF00: 'Vendor Defined', 0xFF01: 'Vendor Defined 1'}

USAGE_NAMES_D = {0x0: 'Undefined', 0x1: 'Digitizer', 0x2: 'Pen', 0x3: 'Touch Screen',
                 0x4: 'Touch Pad', 0x5: 'Whiteboard', 0x20: 'Stylus', 0x21: 'Puck',
                 0x22: 'Finger', 0x23: 'Device Settings', 0x30: 'Tip Pressure',
                 0x31: 'Barrel Pressure', 0x32: 'In Range', 0x33: 'Touch',
                 0x34: 'Un-touch', 0x35: 'Tap', 0x36: 'Quality', 0x37: 'Data Valid',
                 0x38: 'Transducer Index', 0x39: 'Tablet Function Keys',
                 0x3A: 'Program Change Keys', 0x3B: 'Battery Strength',
                 0x3C: 'Invert', 0x3D: 'X Tilt', 0x3E: 'Y Tilt',
                 0x40: 'Azimuth', 0x41: 'Altitude', 0x42: 'Twist',
                 0x43: 'Tip Switch', 0x44: 'Secondary Tip Switch',
                 0x45: 'Barrel Switch', 0x46: 'Eraser', 0x47: 'Tablet Pick'}

USAGE_NAMES_GD = {0x0: 'Undefined', 0x1: 'Pointer', 0x2: 'Mouse', 0x30: 'X',
                  0x31: 'Y', 0x32: 'Z', 0x33: 'Rx', 0x34: 'Ry', 0x35: 'Rz',
                  0x36: 'Slider', 0x37: 'Dial', 0x38: 'Wheel',
                  0x39: 'Hat Switch', 0x3A: 'Counted Buffer', 0x3B: 'Byte Count',
                  0x3C: 'Motion Wakeup', 0x3D: 'Start', 0x3E: 'Select',
                  0x40: 'Vx', 0x41: 'Vy', 0x42: 'Vz', 0x43: 'Vbrx',
                  0x44: 'Vbry', 0x45: 'Vbrz', 0x46: 'Vno', 0x47: 'Feature Notification'}

COLLECTION_TYPES = {0: 'Physical', 1: 'Application', 2: 'Logical', 3: 'Report', 4: 'Named Array',
                    5: 'Usage Switch', 6: 'Usage Modifier'}

def get_usage_name(page, usage):
    if page == 0x0D:
        return USAGE_NAMES_D.get(usage, f'Digitizer:{usage:#x}')
    elif page == 0x01:
        return USAGE_NAMES_GD.get(usage, f'GD:{usage:#x}')
    else:
        pname = USAGE_PAGES.get(page, f'Page:{page:#x}')
        return f'{pname}:Usage:{usage:#x}'

i = 0
indent = 0
current_usage_page = 0
report_sizes = {}  # report_id -> total bits

while i < desc_size:
    b = desc[i]
    item_type = (b >> 2) & 0x3
    item_tag = (b >> 4) & 0xF
    item_size = b & 0x3

    if item_size == 3:
        item_size = 4

    i += 1
    if i + item_size > desc_size:
        break

    value = 0
    for j in range(item_size):
        value |= desc[i + j] << (8 * j)
    i += item_size

    prefix = '  ' * indent

    if item_type == TYPE_MAIN:
        tag_name = MAIN_ITEMS.get(item_tag, f'Main:{item_tag:#x}')
        if item_tag == 0xA:  # Collection
            ctype = COLLECTION_TYPES.get(value, f'{value:#x}')
            ctype_name = get_usage_name(current_usage_page, value) if value > 6 else ctype
            print(f'{prefix}Collection({ctype_name})')
            indent += 1
        elif item_tag == 0xC:  # End Collection
            indent = max(0, indent - 1)
            print(f'{prefix}End Collection')
        elif item_tag == 0x8:  # Input
            flags = []
            if value & 0x01: flags.append('Data')
            else: flags.append('Constant')
            if value & 0x02: flags.append('Variable')
            if value & 0x04: flags.append('Relative')
            if value & 0x10: flags.append('No Null')
            if value & 0x20: flags.append('Volatile')
            if value & 0x80: flags.append('Buffered Bytes')
            if value & 0x100: flags.append('No Preferred')
            flagstr = ", ".join(flags)
            print(f'{prefix}  Input({flagstr})')
        elif item_tag == 0x9:  # Output
            flags = []
            if value & 0x01: flags.append('Data')
            else: flags.append('Constant')
            if value & 0x02: flags.append('Variable')
            if value & 0x04: flags.append('Relative')
            flagstr = ", ".join(flags)
            print(f'{prefix}  Output({flagstr})')
        elif item_tag == 0xB:  # Feature
            flags = []
            if value & 0x01: flags.append('Data')
            else: flags.append('Constant')
            if value & 0x02: flags.append('Variable')
            flagstr = ", ".join(flags)
            print(f'{prefix}  Feature({flagstr})')
    elif item_type == TYPE_GLOBAL:
        tag_name = GLOBAL_ITEMS.get(item_tag, f'Global:{item_tag:#x}')
        if item_tag == 0x0:  # Usage Page
            current_usage_page = value
            pname = USAGE_PAGES.get(value, f'{value:#x}')
            print(f'{prefix}Usage Page({pname})')
        elif item_tag == 0x1:  # Logical Minimum
            print(f'{prefix}Logical Minimum({value})')
        elif item_tag == 0x3:  # Logical Maximum
            print(f'{prefix}Logical Maximum({value})')
        elif item_tag == 0x4:  # Physical Minimum
            print(f'{prefix}Physical Minimum({value})')
        elif item_tag == 0x5:  # Physical Maximum
            print(f'{prefix}Physical Maximum({value})')
        elif item_tag == 0x7:  # Report Size
            print(f'{prefix}Report Size({value})')
        elif item_tag == 0x8:  # Report ID
            print(f'{prefix}Report ID({value})')
        elif item_tag == 0x9:  # Report Count
            print(f'{prefix}Report Count({value})')
        elif item_tag == 0x6:  # Usage Page (extended)
            current_usage_page = value
            print(f'{prefix}Usage Page({value:#06x})')
        elif item_tag == 0x2:  # Usage (extended)
            print(f'{prefix}Usage({get_usage_name(current_usage_page, value)})')
        else:
            print(f'{prefix}{tag_name}({value})')
    elif item_type == TYPE_LOCAL:
        tag_name = LOCAL_ITEMS.get(item_tag, f'Local:{item_tag:#x}')
        if item_tag == 0x0:  # Usage
            print(f'{prefix}Usage({get_usage_name(current_usage_page, value)})')
        elif item_tag == 0x1:  # Usage Minimum
            print(f'{prefix}Usage Minimum({get_usage_name(current_usage_page, value)})')
        elif item_tag == 0x2:  # Usage Maximum
            print(f'{prefix}Usage Maximum({get_usage_name(current_usage_page, value)})')
        else:
            print(f'{prefix}{tag_name}({value})')
    else:
        print(f'{prefix}Reserved(type={item_type}, tag={item_tag:#x}, size={item_size}, value={value:#x})')
"
