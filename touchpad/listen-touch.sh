#!/bin/bash
# Listen for touch events on INGENIC input devices
# Usage: sudo ./listen-touch.sh
# Touch the screen and observe which device fires events.

sudo python3 -c "
import struct, os, select, time

devices = {}
for n in [12, 13, 14, 15]:
    p = f'/dev/input/event{n}'
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
        name = open(f'/sys/class/input/event{n}/device/name').read().strip()
        devices[fd] = (name, p)
    except Exception as e:
        print(f'Cannot open {p}: {e}')

fd_list = list(devices.keys())
print(f'Monitoring {len(devices)} devices:')
for fd, (name, path) in devices.items():
    print(f'  {path}: {name}')
print()
print('Touch the screen NOW. Ctrl+C to stop early.')
print('========================================')

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
                    if ev_type == 3:
                        print(f'  {ts} [{path}] {name}: ABS_{ev_code:#06x} = {ev_value}')
                    elif ev_type == 1:
                        print(f'  {ts} [{path}] {name}: KEY_{ev_code:#06x} = {ev_value}')
                    elif ev_type == 0 and ev_code == 0:
                        print(f'  {ts} [{path}] {name}: --- SYN_REPORT ---')
            except OSError:
                pass
except KeyboardInterrupt:
    print()
    print('Stopped.')
"
