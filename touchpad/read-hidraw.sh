#!/bin/bash
# Read raw HID reports from the INGENIC touch interface
# This bypasses the kernel hid-multitouch driver to see what the device actually sends
# Usage: sudo ./read-hidraw.sh

echo "=== Finding INGENIC hidraw devices ==="
for hr in /dev/hidraw*; do
    if [ -e "$hr" ]; then
        info=$(udevadm info -q property "$hr" 2>/dev/null)
        if echo "$info" | grep -q "17ef.*6161"; then
            iface=$(echo "$info" | grep "ID_USB_INTERFACE_NUM" | cut -d= -f2)
            echo "  $hr  interface=$iface"
            echo "    $(echo "$info" | grep "ID_USB_DRIVER" | cut -d= -f2)"
        fi
    fi
done

echo
echo "=== Reading raw HID reports from interface 3 (touch interface) ==="
echo "Touch the screens NOW. Ctrl+C to stop."
echo

# Find the hidraw device for interface 3
HIDRAW=""
for hr in /dev/hidraw*; do
    if [ -e "$hr" ]; then
        iface=$(udevadm info -q property "$hr" 2>/dev/null | grep "ID_USB_INTERFACE_NUM" | cut -d= -f2)
        driver=$(udevadm info -q property "$hr" 2>/dev/null | grep "ID_USB_DRIVER" | cut -d= -f2)
        if [ "$iface" = "03" ]; then
            HIDRAW="$hr"
            break
        fi
    fi
done

if [ -z "$HIDRAW" ]; then
    echo "ERROR: Could not find hidraw device for interface 3"
    echo "Trying all hidraw devices..."
    HIDRAW="/dev/hidraw5"
fi

echo "Using: $HIDRAW"
echo

# Read raw HID reports
sudo python3 -c "
import os, select, time

path = '$HIDRAW'
try:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
except Exception as e:
    print(f'Cannot open {path}: {e}')
    exit(1)

print(f'Reading from {path}...')
print('Touch the screens and watch for data.')
print()

count = 0
max_reports = 200
try:
    while count < max_reports:
        readable, _, _ = select.select([fd], [], [], 0.5)
        if readable:
            data = os.read(fd, 256)
            if len(data) > 0:
                count += 1
                ts = time.strftime('%H:%M:%S')
                hex_str = ' '.join(f'{b:02x}' for b in data)
                print(f'  [{count:3d}] {ts} len={len(data):3d}: {hex_str}')
except KeyboardInterrupt:
    pass

os.close(fd)
print(f'\nTotal reports received: {count}')
"
