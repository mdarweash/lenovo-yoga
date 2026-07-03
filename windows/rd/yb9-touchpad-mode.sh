#!/bin/bash
# yb9-touchpad-mode.sh — Control virtual touchpad on Lenovo Yoga Book 9
#
# Talks directly to INGENIC MCU via USB bulk transfers (interface 1),
# bypassing the cdc_acm serial driver entirely.
#
# Source: Ghidra decompilation of YB9.Service.exe + YB9.TouchPad.exe

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
    cat <<'EOF'
Usage: sudo ./yb9-touchpad-mode.sh <command> [args]

Commands:
  touchpad [on|off]   Enable/disable virtual touchpad (default: on)
  init                Send init sequence + keepalive
  keepalive           Send one keepalive ping
  raw <type> <hex>    Send arbitrary OSKP frame
  test                Send init and read response
EOF
    exit 0
}

[ $# -lt 1 ] && usage
CMD="$1"; shift

python3 -c "
import sys, time
sys.path.insert(0, '$SCRIPT_DIR')
from yb9_usb import *

cmd = '$CMD'
arg = '$1' if len(sys.argv) > 1 else ''

if cmd == 'touchpad':
    mode = 0 if arg in ('off', '0') else 1
    print(':: Sending init sequence...')
    send_frame(0x25, '00')
    send_frame(0x31, '00' * 66)  # 33 zero bytes
    send_frame(0x26, get_timestamp())
    if mode:
        print(':: Enabling virtual touchpad')
        send_frame(0x21, '7e010000')
    else:
        print(':: Disabling virtual touchpad (keyboard mode)')
        send_frame(0x21, '7e000000')
    print(':: Done.')

elif cmd == 'init':
    print(':: Sending init sequence...')
    send_frame(0x25, '00')
    send_frame(0x31, '00' * 66)
    send_frame(0x26, get_timestamp())
    print(':: Init complete.')

elif cmd == 'keepalive':
    send_frame(0x26, get_timestamp())
    print(':: Keepalive sent.')

elif cmd == 'raw':
    send_frame(int(arg, 16), sys.argv[2] if len(sys.argv) > 2 else '')
    print(f':: Sent type=0x{int(arg,16):02x}')

elif cmd == 'test':
    print(':: Sending init + keepalive, then reading for 2s...')
    send_frame(0x25, '00')
    send_frame(0x31, '00' * 66)
    send_frame(0x26, get_timestamp())
    print(':: Reading response...')
    dev = get_device()
    try:
        data = dev.read(0x81, 512, timeout=2000)
        print(f':: Response ({len(data)} bytes): {data.hex()}')
    except Exception as e:
        print(f':: No response: {e}')

else:
    usage()
"
