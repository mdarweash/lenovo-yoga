#!/bin/bash

# Yoga Book 9 (83KJ) keyboard dock monitor
# Monitors ALL input events + kernel log + udev simultaneously

echo "=== Yoga Book 9 Keyboard Dock Monitor ==="
echo "Listening for ALL input events... Ctrl+C to stop"
echo ""

# Show all input devices for reference
echo "Input devices being monitored:"
for i in /dev/input/event*; do
    n=$(basename "$i")
    name=$(cat "/sys/class/input/${n}/device/name" 2>/dev/null)
    echo "  $n: $name"
done
echo ""
echo "Now attach/detach the keyboard in both positions."
echo "Watch for any events below:"
echo "=========================================="

# Monitor ALL input events using evtest-style raw reading via python
python3 -c "
import struct, os, select, fcntl, array

# Open all event devices
devices = {}
for i in range(20):
    path = f'/dev/input/event{i}'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        name = open(f'/sys/class/input/event{i}/device/name').read().strip()
        devices[fd] = (name, path)
    except (OSError, FileNotFoundError):
        pass

if not devices:
    print('No input devices could be opened.')
    exit(1)

print(f'Monitoring {len(devices)} input devices...')
print()

# Build fd list for select
fd_list = list(devices.keys())
EV_SYN = 0x00
EV_KEY = 0x01
EV_SW = 0x05
SW_TABLET_MODE = 0x01
SW_LID = 0x00

# Also read current switch states
for fd, (name, path) in devices.items():
    try:
        buf = array.array('b', [0] * 256)
        fcntl.ioctl(fd, 0x8040451b, buf)  # EVIOCGSW
        for bit in range(16):
            if buf[0] & (1 << bit):
                print(f'  INIT: {name} SW bit {bit} = 1')
    except:
        pass

print()
print('Waiting for events...')
print()

import time
while True:
    readable, _, _ = select.select(fd_list, [], [], 0.5)
    for fd in readable:
        try:
            data = os.read(fd, 24)
            if len(data) == 24:
                tv_sec, tv_usec, ev_type, ev_code, ev_value = struct.unpack('QQHHi', data)
                name, path = devices[fd]
                ts = time.strftime('%H:%M:%S')

                if ev_type == EV_SW:
                    sw_names = {
                        0x00: 'SW_LID',
                        0x01: 'SW_TABLET_MODE',
                        0x02: 'SW_MICROPHONE_INSERT',
                        0x03: 'SW_HEADPHONE_INSERT',
                        0x04: 'SW_RFKILL_ALL',
                        0x05: 'SW_RADIO',
                        0x06: 'SW_MICROPHONE_MUTE',
                        0x07: 'SW_LINEOUT_INSERT',
                        0x08: 'SW_JACK_PHYSICAL_INSERT',
                        0x09: 'SW_VIDEOOUT_INSERT',
                        0x0a: 'SW_CAMERA_LENS_COVER',
                        0x0b: 'SW_KEYPAD_SLIDE',
                        0x0c: 'SW_FRONT_PROXIMITY',
                        0x0d: 'SW_ROTATE_LOCK',
                        0x0e: 'SW_LINEIN_INSERT',
                        0x0f: 'SW_MUTE_DEVICE',
                    }
                    sw_name = sw_names.get(ev_code, f'SW_{ev_code:#x}')
                    state = 'ON' if ev_value else 'OFF'
                    print(f'  {ts} [{path}] {name}: {sw_name} = {state}')
                elif ev_type == EV_KEY:
                    print(f'  {ts} [{path}] {name}: KEY_{ev_code:#x} = {ev_value}')
                elif ev_type == EV_SYN:
                    pass  # suppress SYN events
                else:
                    print(f'  {ts} [{path}] {name}: type={ev_type:#x} code={ev_code:#x} value={ev_value}')
        except OSError:
            pass
" &
PY_PID=$!

# Also monitor kernel log for any ACPI/EC events
journalctl -kf --no-pager 2>/dev/null | \
    grep --line-buffered -iE "usb|INGENIC|_Q3|acpi.*error|acpi.*event|ec0|tablet|dock|hall|yoga|keyboard|mode" &
KERN_PID=$!

# Status line every 5s
while true; do
    sleep 5
    ts=$(date +%H:%M:%S)
    usb="?"
    [ -d "/sys/bus/usb/devices/3-6" ] && usb=$(cat /sys/bus/usb/devices/3-6/idVendor 2>/dev/null)
    echo "  --- $ts USB=$usb ---"
done

trap "kill $PY_PID $KERN_PID 2>/dev/null; wait 2>/dev/null; echo ''; echo 'Stopped.'" EXIT
