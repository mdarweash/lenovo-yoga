#!/bin/bash
# Show ABS capabilities and resolution for event12 (INGENIC Finger touch)
# Usage: sudo ./show-touch-caps.sh

for ev in 12 15; do
    name=$(cat /sys/class/input/event${ev}/device/name)
    echo "=== event${ev}: ${name} ==="
    echo "PROP: $(cat /sys/class/input/event${ev}/device/properties)"

    # Show ABS info
    echo "ABS capabilities:"
    for i in /sys/class/input/event${ev}/device/capabilities/abs; do
        echo "  raw: $(cat $i)"
    done

    # Show ABS min/max/resolution
    echo "ABS axes:"
    for f in /sys/class/input/event${ev}/device/id/input115/capabilities/abs; do true; done 2>/dev/null
    if [ -d "/sys/class/input/input" ]; then
        # Find the input number for this event
        input_num=$(readlink /sys/class/input/event${ev} | grep -oP 'input\d+')
        if [ -n "$input_num" ] && [ -d "/sys/class/input/${input_num}" ]; then
            for axis_dir in /sys/class/input/${input_num}/properties/; do true; done 2>/dev/null
        fi
    fi

    # Use evemu to get detailed info if available, otherwise parse /proc
    grep -A 20 "event${ev}" /proc/bus/input/devices | grep -E "B: (ABS|PROP|KEY|EV)"

    # Try to get ABS min/max from the device
    echo "ABS axis details:"
    for axis in X Y MT_POSITION_X MT_POSITION_Y; do
        code=""
        case "$axis" in
            X) code=0 ;;
            Y) code=1 ;;
            MT_POSITION_X) code=53 ;;
            MT_POSITION_Y) code=54 ;;
        esac
        min_f="/sys/class/input/event${ev}/device/capabilities/abs"
        # Check if evemu-describe is available
        if command -v evemu-describe &>/dev/null; then
            evemu-describe /dev/input/event${ev} 2>/dev/null | grep "EV_ABS.*$axis"
        fi
    done
    echo
done

# Also get resolution via ioctl
python3 -c "
import struct, os, fcntl

EVIOCGABS = lambda code: 0x80184540 + code * 8

for ev_num in [12, 15]:
    path = f'/dev/input/event{ev_num}'
    try:
        fd = os.open(path, os.O_RDONLY)
        name = open(f'/sys/class/input/event{ev_num}/device/name').read().strip()
        print(f'=== event{ev_num}: {name} ===')

        abs_names = {
            0: 'ABS_X', 1: 'ABS_Y', 47: 'ABS_MISC',
            53: 'ABS_MT_POSITION_X', 54: 'ABS_MT_POSITION_Y',
            48: 'ABS_MT_SLOT', 57: 'ABS_MT_TRACKING_ID',
            49: 'ABS_MT_TOUCH_MAJOR', 50: 'ABS_MT_TOUCH_MINOR',
            55: 'ABS_MT_TOOL_TYPE', 56: 'ABS_MT_BLOB_ID',
        }

        # Check which ABS codes are supported
        raw = open(f'/sys/class/input/event{ev_num}/device/capabilities/abs').read().strip()
        bm = int(raw, 16)
        supported = [i for i in range(64) if bm & (1 << i)]
        print(f'  Supported ABS codes: {supported}')

        for code in supported:
            if code in abs_names:
                try:
                    buf = fcntl.ioctl(fd, EVIOCGABS(code), struct.pack('iiiiii', 0,0,0,0,0,0))
                    val, min_, max_, fuzz, flat, res = struct.unpack('iiiiii', buf)
                    print(f'  {abs_names[code]}: min={min_} max={max_} fuzz={fuzz} flat={flat} resolution={res}')
                except Exception as e:
                    print(f'  {abs_names[code]}: error: {e}')

        os.close(fd)
        print()
    except Exception as e:
        print(f'  Cannot open {path}: {e}')
        print()
"
