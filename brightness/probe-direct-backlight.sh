#!/bin/bash
# Probe direct backlight control for top panel (eDP-1)
# Fully automated — just run and watch the screen.
# Run with: sudo bash /home/mdarweash/myCommands/yogabook/brightness/probe-direct-backlight.sh

set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "Run with: sudo bash $0"
    exit 1
fi

BL="/sys/class/backlight/intel_backlight"
MAX=$(cat "$BL/max_brightness")
CUR=$(cat "$BL/brightness")
AUX="/dev/drm_dp_aux0"
I2C_DEV="/dev/i2c-14"

section() {
    echo
    echo "============================================================"
    echo "  $1"
    echo "============================================================"
}

# -------------------------------------------------------------------
section "1. SYSFS intel_backlight (current=$CUR/$MAX)"
# -------------------------------------------------------------------

echo "Writing 50 to intel_backlight (~10%) for 2 seconds..."
echo 50 > "$BL/brightness"
sleep 2
echo "Restoring to $CUR..."
echo "$CUR" > "$BL/brightness"
echo "Did the TOP panel briefly dim? Check above for DPCD/DDC results next."

# -------------------------------------------------------------------
section "2. DPCD backlight (eDP-1 via /dev/drm_dp_aux0)"
# -------------------------------------------------------------------

/usr/bin/python3 <<'PYEOF'
import os, sys

aux_path = "/dev/drm_dp_aux0"

try:
    fd = os.open(aux_path, os.O_RDWR)
except OSError as e:
    print(f"Cannot open {aux_path}: {e}")
    print("Skipping DPCD test.")
    sys.exit(0)

def dpcd_read(addr, length):
    os.lseek(fd, addr, os.SEEK_SET)
    return os.read(fd, length)

def dpcd_write(addr, data):
    os.lseek(fd, addr, os.SEEK_SET)
    return os.write(fd, data)

try:
    # DPCD rev
    rev = dpcd_read(0x000, 1)
    print(f"DPCD revision: 0x{rev[0]:02x}")

    # Backlight capability (DPCD 0x700-0x725)
    bl_caps = dpcd_read(0x700, 16)
    print(f"BL caps (0x700-0x70f): {bl_caps.hex(' ')}")
    cap_byte = bl_caps[0]
    print(f"  0x700 brightness cap: 0x{cap_byte:02x}")
    if cap_byte & 0x01: print("    Bit 0: PWM brightness")
    if cap_byte & 0x02: print("    Bit 1: AUX-set brightness (DPCD)")
    if cap_byte & 0x04: print("    Bit 2: Brightness callback")

    bl_mode = dpcd_read(0x721, 4)
    print(f"  0x721-724 (BL mode+val): {bl_mode.hex(' ')}")
    mode_byte = bl_mode[0]
    print(f"    Mode: 0x{mode_byte:02x}  enabled={bool(mode_byte & 1)}  aux_mode={bool(mode_byte & 2)}")
    cur_brightness = bl_mode[1] | (bl_mode[2] << 8)
    print(f"    Brightness value: {cur_brightness}")

    bl_max = dpcd_read(0x724, 2)
    max_b = bl_max[0] | (bl_max[1] << 8)
    print(f"    Max brightness: {max_b}")

    print()
    print("--- Setting DPCD brightness to 0 for 2 seconds ---")
    # Enable backlight + AUX mode, then set brightness to 0
    new_mode = mode_byte | 0x01 | 0x02
    dpcd_write(0x721, bytes([new_mode]))
    dpcd_write(0x722, bytes([0x00, 0x00]))

    import time; time.sleep(2)

    print("Restoring...")
    dpcd_write(0x722, bytes([cur_brightness & 0xFF, (cur_brightness >> 8) & 0xFF]))

    # Verify
    verify = dpcd_read(0x722, 2)
    print(f"Verified brightness: {verify[0] | (verify[1] << 8)}")

except Exception as e:
    print(f"DPCD test error: {e}")

os.close(fd)
PYEOF

# -------------------------------------------------------------------
section "3. I2C DDC/CI (via /dev/i2c-14 = AUX A)"
# -------------------------------------------------------------------

/usr/bin/python3 <<'PYEOF'
import os, sys, struct

i2c_path = "/dev/i2c-14"

try:
    fd = os.open(i2c_path, os.O_RDWR)
except OSError as e:
    print(f"Cannot open {i2c_path}: {e}")
    print("Skipping DDC/CI test.")
    sys.exit(0)

import fcntl

I2C_SLAVE = 0x0703
DDC_ADDR = 0x37

def ddc_checksum(data):
    s = 0x6E ^ 0x51
    for b in data:
        s ^= b
    return s & 0xFF

try:
    fcntl.ioctl(fd, I2C_SLAVE, DDC_ADDR)
    print(f"I2C slave set to 0x{DDC_ADDR:02x} (DDC/CI)")

    # Read brightness: VCP 0x10
    req = bytes([0x82, 0x01, 0x00, 0x10])
    chk = ddc_checksum(req)
    packet = bytes([0x51]) + req + bytes([chk])
    os.write(fd, packet)
    resp = os.read(fd, 12)
    print(f"DDC response: {resp.hex(' ')}")

    if len(resp) >= 8 and resp[0] == 0x6E:
        cur_val = (resp[6] << 8) | resp[7]
        max_val = (resp[4] << 8) | resp[5]
        print(f"Current brightness: {cur_val} / {max_val}")

        print()
        print("--- Setting DDC brightness to 0 for 2 seconds ---")
        set_req = bytes([0x84, 0x03, 0x00, 0x10, 0x00, 0x00])
        set_packet = bytes([0x51]) + set_req + bytes([ddc_checksum(set_req)])
        os.write(fd, set_packet)

        import time; time.sleep(2)

        print(f"Restoring to {cur_val}...")
        restore = bytes([0x84, 0x03, 0x00, 0x10, (cur_val >> 8) & 0xFF, cur_val & 0xFF])
        restore_packet = bytes([0x51]) + restore + bytes([ddc_checksum(restore)])
        os.write(fd, restore_packet)
    else:
        print("No valid DDC/CI response (panel may not support DDC/CI)")

except Exception as e:
    print(f"DDC/CI test error: {e}")

os.close(fd)
PYEOF

# -------------------------------------------------------------------
echo
echo "============================================================"
echo "  DONE — tell me which test(s) made the TOP panel dim."
echo "============================================================"
