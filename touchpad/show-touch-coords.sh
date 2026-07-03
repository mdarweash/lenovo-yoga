#!/bin/bash
# Show live X,Y touch coordinates from event12
# Usage: sudo ./show-touch-coords.sh
# Touch ONE screen and note the coordinates, then the OTHER screen.

sudo python3 -c "
import struct, os, select, time, fcntl

EV_ABS = 3
EV_KEY = 1
EV_SYN = 0
SYN_REPORT = 0
ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39
BTN_TOUCH = 0x14a

path = '/dev/input/event12'
fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

# Get ABS info
EVIOCGABS = lambda code: 0x80184540 + code * 8

print('=== ABS Axis Info ===')
for code, name in [(0,'X'), (1,'Y'), (0x35,'MT_X'), (0x36,'MT_Y'), (0x39,'MT_TRACKING_ID')]:
    try:
        buf = fcntl.ioctl(fd, EVIOCGABS(code), struct.pack('iiiiii', 0,0,0,0,0,0))
        val, min_, max_, fuzz, flat, res = struct.unpack('iiiiii', buf)
        print(f'  {name}: value={val} min={min_} max={max_} fuzz={fuzz} flat={flat} res={res}')
    except Exception as e:
        print(f'  {name}: error {e}')

print()
print('=== Live Touch Coordinates ===')
print('Touch the TOP screen (eDP-1) first, note the Y range.')
print('Then touch the BOTTOM screen (eDP-2), note the Y range.')
print('Press Ctrl+C to stop.')
print()

try:
    x_val, y_val, mtx_x, mtx_y, tid = None, None, None, None, None
    while True:
        readable, _, _ = select.select([fd], [], [], 0.5)
        if readable:
            data = os.read(fd, 24)
            if len(data) == 24:
                tv_sec, tv_usec, ev_type, ev_code, ev_value = struct.unpack('QQHHi', data)
                if ev_type == EV_ABS:
                    if ev_code == ABS_X:
                        x_val = ev_value
                    elif ev_code == ABS_Y:
                        y_val = ev_value
                    elif ev_code == ABS_MT_POSITION_X:
                        mtx_x = ev_value
                    elif ev_code == ABS_MT_POSITION_Y:
                        mtx_y = ev_value
                    elif ev_code == ABS_MT_TRACKING_ID:
                        tid = ev_value
                elif ev_type == EV_KEY and ev_code == BTN_TOUCH:
                    state = 'DOWN' if ev_value else 'UP'
                    print(f'  BTN_TOUCH {state}')
                elif ev_type == EV_SYN and ev_code == SYN_REPORT:
                    print(f'  X={x_val} Y={y_val}  MT_X={mtx_x} MT_Y={mtx_y}  TID={tid}')
except KeyboardInterrupt:
    print()
    print('Stopped.')
"
