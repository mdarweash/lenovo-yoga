#!/bin/bash
# Monitor ALL event devices for touch/abs events
# Usage: sudo ./listen-all-touch.sh
# Touch eDP-1 screen and watch which device fires real events.

sudo python3 -c "
import struct, os, select, time

print('Opening ALL event devices...')
devices = {}
for i in range(20):
    path = f'/dev/input/event{i}'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        name = open(f'/sys/class/input/event{i}/device/name').read().strip()
        devices[fd] = (name, path)
    except Exception:
        pass

fd_list = list(devices.keys())
print(f'Monitoring {len(devices)} devices:')
for fd, (name, path) in sorted(devices.items(), key=lambda x: x[1][1]):
    print(f'  {path}: {name}')
print()
print('Touch eDP-1 (main screen) NOW. Watch for non-zero ABS values.')
print('Press Ctrl+C to stop.')
print('=' * 60)

try:
    while True:
        readable, _, _ = select.select(fd_list, [], [], 0.5)
        for fd in readable:
            try:
                data = os.read(fd, 24)
                if len(data) == 24:
                    tv_sec, tv_usec, ev_type, ev_code, ev_value = struct.unpack('QQHHi', data)
                    name, path = devices[fd]
                    ts = time.strftime('%H:%M:%S')

                    # Show ABS, KEY, and MSC events (skip SYN_REPORT unless there was data)
                    if ev_type == 0x03:  # EV_ABS
                        abs_names = {0:'X',1:'Y',47:'MISC',48:'MT_SLOT',
                                     49:'MT_TOUCH_MAJOR',50:'MT_TOUCH_MINOR',
                                     53:'MT_POSITION_X',54:'MT_POSITION_Y',
                                     55:'MT_TOOL_TYPE',57:'MT_TRACKING_ID',
                                     58:'MT_PRESSURE'}
                        aname = abs_names.get(ev_code, f'?{ev_code:#x}')
                        print(f'  {ts} [{path}] {name}: ABS_{aname} ({ev_code:#04x}) = {ev_value}')
                    elif ev_type == 0x01:  # EV_KEY
                        key_names = {0x14a:'BTN_TOUCH',0x145:'BTN_TOOL_PEN',
                                     0x140:'BTN_TOOL_FINGER',0x141:'BTN_TOOL_RUBBER',
                                     0x110:'BTN_LEFT',0x111:'BTN_RIGHT'}
                        kname = key_names.get(ev_code, f'?{ev_code:#x}')
                        print(f'  {ts} [{path}] {name}: KEY_{kname} ({ev_code:#04x}) = {ev_value}')
                    elif ev_type == 0x04:  # EV_MSC
                        print(f'  {ts} [{path}] {name}: MSC_{ev_code:#04x} = {ev_value}')
                    elif ev_type == 0x00 and ev_code == 0:  # EV_SYN SYN_REPORT
                        pass  # skip to reduce noise
            except OSError:
                pass
except KeyboardInterrupt:
    print()
    print('Stopped.')
"
