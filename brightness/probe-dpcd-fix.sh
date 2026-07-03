#!/bin/bash
# Focused DPCD backlight fix for top panel (eDP-1)
# Tests: (A) force PWM mode via DPCD, (B) Intel DPCD interface
# (C) write to aux0 0x722 with PWM mode explicitly
#
# Run with: sudo bash /home/mdarweash/myCommands/yogabook/brightness/probe-dpcd-fix.sh

set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "Run with: sudo bash $0"
    exit 1
fi

PYTHON=/usr/bin/python3

section() {
    echo
    echo "============================================================"
    echo "  $1"
    echo "============================================================"
}

# -------------------------------------------------------------------
section "0. Current state of aux0 and aux1"
# -------------------------------------------------------------------

for aux in /dev/drm_dp_aux0 /dev/drm_dp_aux1; do
    $PYTHON -c "
import os
fd = os.open('$aux', os.O_RDWR)
os.lseek(fd, 0x721, os.SEEK_SET)
d = os.read(fd, 6)
mode = d[0]
val = d[1] | (d[2] << 8)
mx = d[3] | (d[4] << 8)
print(f'$aux: mode=0x{mode:02x} enabled={bool(mode&1)} aux_mode={bool(mode&2)} brightness={val}/{mx}')
os.close(fd)
"
done

# -------------------------------------------------------------------
section "A. Test 1 — Force PWM mode on aux0, set brightness low"
# -------------------------------------------------------------------

echo "Setting aux0 (eDP-1 top panel) to PWM mode + brightness ~5% for 3 seconds..."
echo ">>> WATCH THE TOP PANEL NOW <<<"
echo

$PYTHON <<'PYEOF'
import os, sys, time

fd = os.open("/dev/drm_dp_aux0", os.O_RDWR)

# Read current state
os.lseek(fd, 0x721, os.SEEK_SET)
d = os.read(fd, 6)
orig_mode = d[0]
orig_val = d[1] | (d[2] << 8)
print(f"Before: mode=0x{orig_mode:02x} brightness={orig_val}")

# Read max
os.lseek(fd, 0x724, os.SEEK_SET)
mx = os.read(fd, 2)
max_val = mx[0] | (mx[1] << 8)
print(f"Max brightness: {max_val}")

# Step 1: Disable backlight first
os.lseek(fd, 0x721, os.SEEK_SET)
os.write(fd, bytes([0x00]))
time.sleep(0.1)

# Step 2: Set brightness to ~5% 
target = max(1, max_val * 5 // 100)
os.lseek(fd, 0x722, os.SEEK_SET)
os.write(fd, bytes([target & 0xFF, (target >> 8) & 0xFF]))
time.sleep(0.1)

# Step 3: Enable backlight in PWM mode (bit 0=1, bit 1=0)
os.lseek(fd, 0x721, os.SEEK_SET)
os.write(fd, bytes([0x01]))
time.sleep(0.1)

# Verify state
os.lseek(fd, 0x721, os.SEEK_SET)
verify = os.read(fd, 6)
v_mode = verify[0]
v_val = verify[1] | (verify[2] << 8)
print(f"Active: mode=0x{v_mode:02x} brightness={v_val}/{max_val}")
print(">>> TOP PANEL SHOULD BE DIMMED NOW — waiting 3 seconds <<<")
sys.stdout.flush()
time.sleep(3)

# Restore original state
os.lseek(fd, 0x721, os.SEEK_SET)
os.write(fd, bytes([orig_mode]))
os.lseek(fd, 0x722, os.SEEK_SET)
os.write(fd, bytes([orig_val & 0xFF, (orig_val >> 8) & 0xFF]))

os.lseek(fd, 0x721, os.SEEK_SET)
verify2 = os.read(fd, 6)
print(f"Restored: mode=0x{verify2[0]:02x} brightness={verify2[1]|(verify2[2]<<8)}")

os.close(fd)
print("Done. Did the TOP panel dim?")
PYEOF

# -------------------------------------------------------------------
section "B. Test 2 — Force PWM mode on aux1, set brightness low"
# -------------------------------------------------------------------

echo "Same test on aux1 (eDP-2 bottom panel) for comparison..."
echo

$PYTHON <<'PYEOF'
import os, sys, time

fd = os.open("/dev/drm_dp_aux1", os.O_RDWR)

os.lseek(fd, 0x721, os.SEEK_SET)
d = os.read(fd, 6)
orig_mode = d[0]
orig_val = d[1] | (d[2] << 8)
max_val = d[3] | (d[4] << 8)
print(f"Before: mode=0x{orig_mode:02x} brightness={orig_val}/{max_val}")

# Disable, set low, enable PWM mode
os.lseek(fd, 0x721, os.SEEK_SET)
os.write(fd, bytes([0x00]))
time.sleep(0.1)

target = max(1, max_val * 5 // 100)
os.lseek(fd, 0x722, os.SEEK_SET)
os.write(fd, bytes([target & 0xFF, (target >> 8) & 0xFF]))
time.sleep(0.1)

os.lseek(fd, 0x721, os.SEEK_SET)
os.write(fd, bytes([0x01]))
time.sleep(0.1)

os.lseek(fd, 0x721, os.SEEK_SET)
v = os.read(fd, 6)
print(f"Active: mode=0x{v[0]:02x} brightness={v[1]|(v[2]<<8)}/{v[3]|(v[4]<<8)}")
print(">>> BOTTOM PANEL SHOULD BE DIMMED — waiting 2 seconds <<<")
sys.stdout.flush()
time.sleep(2)

# Restore
os.lseek(fd, 0x721, os.SEEK_SET)
os.write(fd, bytes([orig_mode]))
os.lseek(fd, 0x722, os.SEEK_SET)
os.write(fd, bytes([orig_val & 0xFF, (orig_val >> 8) & 0xFF]))

os.lseek(fd, 0x721, os.SEEK_SET)
v2 = os.read(fd, 6)
print(f"Restored: mode=0x{v2[0]:02x} brightness={v2[1]|(v2[2]<<8)}")

os.close(fd)
PYEOF

# -------------------------------------------------------------------
section "C. Test 3 — Intel DPCD backlight on aux0"
# -------------------------------------------------------------------

echo "Checking Intel proprietary DPCD backlight registers..."
echo

$PYTHON <<'PYEOF'
import os, sys, time

for aux_path in ["/dev/drm_dp_aux0", "/dev/drm_dp_aux1"]:
    try:
        fd = os.open(aux_path, os.O_RDWR)

        # Intel backlight at 0x300
        os.lseek(fd, 0x300, os.SEEK_SET)
        intel = os.read(fd, 8)
        caps = intel[0]

        print(f"\n{aux_path} Intel BL:")
        print(f"  Raw 0x300: {intel.hex(' ')}")
        print(f"  Caps: 0x{caps:02x}  present={bool(caps & 0x01)}")

        if not (caps & 0x01):
            print(f"  No Intel BL, skipping test")
            os.close(fd)
            continue

        # Read brightness at 0x304
        os.lseek(fd, 0x304, os.SEEK_SET)
        bl_data = os.read(fd, 4)
        cur = bl_data[0] | (bl_data[1] << 8)
        mx = bl_data[2] | (bl_data[3] << 8)
        print(f"  Brightness: {cur}/{mx}")

        if mx == 0:
            print("  Max=0, skipping")
            os.close(fd)
            continue

        # Dim to 10%
        target = max(1, mx // 10)
        os.lseek(fd, 0x304, os.SEEK_SET)
        os.write(fd, bytes([target & 0xFF, (target >> 8) & 0xFF, mx & 0xFF, (mx >> 8) & 0xFF]))
        print(f"  Set to {target}/{mx} — waiting 2 seconds")
        sys.stdout.flush()
        time.sleep(2)

        # Restore
        os.lseek(fd, 0x304, os.SEEK_SET)
        os.write(fd, bytes([cur & 0xFF, (cur >> 8) & 0xFF, mx & 0xFF, (mx >> 8) & 0xFF]))
        print(f"  Restored to {cur}")

        os.close(fd)
    except Exception as e:
        print(f"\n{aux_path}: Error: {e}")

PYEOF

# -------------------------------------------------------------------
section "D. Test 4 — Disable DPCD backlight control entirely on aux0"
# -------------------------------------------------------------------

echo "Disable backlight via DPCD (mode=0) to let GPU PWM take over..."
echo "Then try intel_backlight sysfs write..."
echo

$PYTHON <<'PYEOF'
import os, sys, time

fd = os.open("/dev/drm_dp_aux0", os.O_RDWR)

# Read current
os.lseek(fd, 0x721, os.SEEK_SET)
d = os.read(fd, 6)
orig_mode = d[0]
orig_val = d[1] | (d[2] << 8)
print(f"Before: mode=0x{orig_mode:02x} brightness={orig_val}")

# Disable DPCD backlight entirely
os.lseek(fd, 0x721, os.SEEK_SET)
os.write(fd, bytes([0x00]))

# Verify
os.lseek(fd, 0x721, os.SEEK_SET)
v = os.read(fd, 6)
print(f"DPCD BL disabled: mode=0x{v[0]:02x}")
print()
print("Now writing 50 to /sys/class/backlight/intel_backlight ...")
sys.stdout.flush()

os.close(fd)
PYEOF

echo 50 > /sys/class/backlight/intel_backlight/brightness
echo "brightness = $(cat /sys/class/backlight/intel_backlight/brightness)"
echo ">>> WATCH TOP PANEL — is it dimmed? Waiting 3 seconds... <<<"
sleep 3

echo "Restoring intel_backlight to 496..."
echo 496 > /sys/class/backlight/intel_backlight/brightness

# Re-enable DPCD backlight
$PYTHON -c "
import os, time
fd = os.open('/dev/drm_dp_aux0', os.O_RDWR)
# Re-enable with original mode
os.lseek(fd, 0x721, os.SEEK_SET)
import sys
d = os.read(fd, 6)
os.lseek(fd, 0x722, os.SEEK_SET)
# Set brightness to max
os.write(fd, bytes([0xFF, 0xFF]))
os.lseek(fd, 0x721, os.SEEK_SET)
os.write(fd, bytes([0x01]))  # PWM mode, enabled
print(f'Re-enabled DPCD BL: mode=0x01')
os.close(fd)
"

echo
echo "============================================================"
echo "  DONE"
echo "  Tell me: which test (A, B, C, or D) dimmed the TOP panel?"
echo "============================================================"
