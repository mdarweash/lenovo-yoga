#!/bin/bash
# Comprehensive DPCD backlight probe for top panel (eDP-1)
# Tests ALL AUX devices, PWM-mode DPCD control, Intel DPCD interface,
# and reads i915 debugfs for backlight register state.
#
# Run with: sudo bash /home/mdarweash/myCommands/yogabook/brightness/probe-dpcd-backlight.sh

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
section "1. i915 debugfs — backlight register state"
# -------------------------------------------------------------------

for card in /sys/kernel/debug/dri/card*/i915_display_info; do
    [ -f "$card" ] || continue
    echo "=== $(dirname "$card" | xargs basename) ==="
    grep -i -E 'backlight|bl |panel |pipe |encoder|connector|eDP|backlight:' "$card" 2>/dev/null || echo "(no backlight info)"
    echo "---"
    # Also get the full backlight section if present
    grep -A5 -i 'backlight' "$card" 2>/dev/null | head -40
    echo
done

# -------------------------------------------------------------------
section "2. i915 display info — CRTC/pipe backlight"
# -------------------------------------------------------------------

for card in /sys/kernel/debug/dri/card*/i915_pipe_A; do
    [ -f "$card" ] || continue
    echo "=== $(echo "$card" | grep -o 'card[0-9]*') pipe A ==="
    cat "$card" 2>/dev/null | head -30
done

# -------------------------------------------------------------------
section "3. Enumerate AUX devices — map to connectors"
# -------------------------------------------------------------------

# Try to figure out which AUX maps to which connector
for aux_dev in /sys/class/drm_dp_aux/drm_dp_aux*; do
    [ -d "$aux_dev" ] || continue
    echo "$(basename "$aux_dev") -> $(readlink -f "$aux_dev/device" 2>/dev/null || echo '?')"
done

# Brute-force: try all /dev/drm_dp_aux* devices
echo
echo "Reading DPCD backlight registers from all AUX devices..."
for aux in /dev/drm_dp_aux*; do
    $PYTHON <<PYEOF
import os, sys
aux = "$aux"
try:
    fd = os.open(aux, os.O_RDWR)
    # DPCD revision
    os.lseek(fd, 0x000, os.SEEK_SET)
    rev = os.read(fd, 1)
    # DPCD 0x700-0x72f (backlight capability + state)
    os.lseek(fd, 0x700, os.SEEK_SET)
    bl = os.read(fd, 0x30)
    
    print(f"\n{aux}: DPCD rev=0x{rev[0]:02x}")
    print(f"  0x700 caps: 0x{bl[0]:02x}  pwm={bool(bl[0]&1)} aux_set={bool(bl[0]&2)} callback={bool(bl[0]&4)}")
    print(f"  0x701 freq: 0x{bl[1]:02x}")
    
    # VESA backlight
    os.lseek(fd, 0x721, os.SEEK_SET)
    mode_data = os.read(fd, 6)
    mode = mode_data[0]
    val = mode_data[1] | (mode_data[2] << 8)
    mx = mode_data[3] | (mode_data[4] << 8)
    print(f"  0x721 mode: 0x{mode:02x} enabled={bool(mode&1)} aux_mode={bool(mode&2)}")
    print(f"  0x722 val: {val}  0x724 max: {mx}")
    
    # Intel-specific backlight (0x0300-0x0307)
    os.lseek(fd, 0x300, os.SEEK_SET)
    intel_bl = os.read(fd, 8)
    print(f"  Intel 0x300-307: {intel_bl.hex(' ')}")
    intel_caps = intel_bl[0]
    print(f"    Intel caps: 0x{intel_caps:02x}  bl_present={bool(intel_caps&0x01)} aux_enable={bool(intel_caps&0x02)}")
    print(f"    Intel freq=0x{intel_bl[1]:02x}{intel_bl[2]:02x}  brightness={intel_bl[4]|(intel_bl[5]<<8)}")
    
    # Also check 0x310-0x312 (Intel backlight state)
    os.lseek(fd, 0x310, os.SEEK_SET)
    intel_state = os.read(fd, 4)
    print(f"  Intel 0x310-313: {intel_state.hex(' ')}")
    
    os.close(fd)
except Exception as e:
    print(f"\n{aux}: Error: {e}")
PYEOF
done

# -------------------------------------------------------------------
section "4. DPCD PWM-mode brightness test on ALL AUX devices"
# -------------------------------------------------------------------

echo "Will briefly dim each panel via DPCD PWM mode."
echo "Watch the TOP panel and note which AUX device number dims it."
echo

for aux in /dev/drm_dp_aux*; do
    echo "--- Testing $aux ---"
    $PYTHON <<PYEOF
import os, sys, time

aux = "$aux"
try:
    fd = os.open(aux, os.O_RDWR)
    
    # Read current state
    os.lseek(fd, 0x721, os.SEEK_SET)
    mode_data = os.read(fd, 6)
    orig_mode = mode_data[0]
    orig_val = mode_data[1] | (mode_data[2] << 8)
    
    print(f"  Current: mode=0x{orig_mode:02x} brightness={orig_val}")
    
    # TEST A: VESA PWM mode — enable backlight, keep PWM mode (NOT aux mode)
    # Set bit 0 (enable), clear bit 1 (PWM mode, not AUX)
    new_mode = (orig_mode | 0x01) & ~0x02
    os.lseek(fd, 0x721, os.SEEK_SET)
    os.write(fd, bytes([new_mode]))
    
    # Set brightness to ~10% of max
    # Read max first
    os.lseek(fd, 0x724, os.SEEK_SET)
    mx = os.read(fd, 2)
    max_val = mx[0] | (mx[1] << 8)
    target = max(1, max_val // 10)
    
    os.lseek(fd, 0x722, os.SEEK_SET)
    os.write(fd, bytes([target & 0xFF, (target >> 8) & 0xFF]))
    
    print(f"  Set PWM mode: brightness={target}/{max_val}")
    sys.stdout.flush()
    time.sleep(2)
    
    # Restore
    os.lseek(fd, 0x721, os.SEEK_SET)
    os.write(fd, bytes([orig_mode]))
    os.lseek(fd, 0x722, os.SEEK_SET)
    os.write(fd, bytes([orig_val & 0xFF, (orig_val >> 8) & 0xFF]))
    
    # Verify
    os.lseek(fd, 0x721, os.SEEK_SET)
    verify = os.read(fd, 6)
    v_mode = verify[0]
    v_val = verify[1] | (verify[2] << 8)
    print(f"  Restored: mode=0x{v_mode:02x} brightness={v_val}")
    
    os.close(fd)
    
except Exception as e:
    print(f"  Error: {e}")
PYEOF
done

# -------------------------------------------------------------------
section "5. Intel-specific DPCD backlight test on ALL AUX devices"
# -------------------------------------------------------------------

echo "Testing Intel proprietary DPCD backlight interface (0x300-0x307)."
echo

for aux in /dev/drm_dp_aux*; do
    echo "--- Intel BL on $aux ---"
    $PYTHON <<PYEOF
import os, sys, time

aux = "$aux"
try:
    fd = os.open(aux, os.O_RDWR)
    
    # Read Intel backlight registers
    os.lseek(fd, 0x300, os.SEEK_SET)
    intel = os.read(fd, 8)
    caps = intel[0]
    
    if not (caps & 0x01):
        print(f"  Intel BL not present (caps=0x{caps:02x}), skipping")
        os.close(fd)
        continue
    
    # Read current brightness
    os.lseek(fd, 0x304, os.SEEK_SET)
    cur_data = os.read(fd, 4)
    cur_bl = cur_data[0] | (cur_data[1] << 8)
    cur_max = cur_data[2] | (cur_data[3] << 8)
    
    print(f"  Intel BL present! caps=0x{caps:02x} brightness={cur_bl}/{cur_max}")
    
    if cur_max == 0:
        print("  Max brightness is 0, skipping test")
        os.close(fd)
        continue
    
    # Set to ~10%
    target = max(1, cur_max // 10)
    
    # Read 0x310 state
    os.lseek(fd, 0x310, os.SEEK_SET)
    state = os.read(fd, 4)
    print(f"  State 0x310: {state.hex(' ')}")
    
    # Write new brightness
    os.lseek(fd, 0x304, os.SEEK_SET)
    os.write(fd, bytes([target & 0xFF, (target >> 8) & 0xFF, cur_max & 0xFF, (cur_max >> 8) & 0xFF]))
    
    print(f"  Set Intel BL to {target}/{cur_max}")
    sys.stdout.flush()
    time.sleep(2)
    
    # Restore
    os.lseek(fd, 0x304, os.SEEK_SET)
    os.write(fd, bytes([cur_bl & 0xFF, (cur_bl >> 8) & 0xFF, cur_max & 0xFF, (cur_max >> 8) & 0xFF]))
    
    # Verify
    os.lseek(fd, 0x304, os.SEEK_SET)
    verify = os.read(fd, 4)
    v_bl = verify[0] | (verify[1] << 8)
    print(f"  Restored: brightness={v_bl}")
    
    os.close(fd)
    
except Exception as e:
    print(f"  Error: {e}")
PYEOF
done

# -------------------------------------------------------------------
section "6. Raw AUX register dump (0x700-0x72F for each AUX)"
# -------------------------------------------------------------------

for aux in /dev/drm_dp_aux*; do
    echo "--- $aux BL registers ---"
    $PYTHON <<PYEOF
import os
aux = "$aux"
try:
    fd = os.open(aux, os.O_RDWR)
    for offset in range(0x700, 0x730, 16):
        os.lseek(fd, offset, os.SEEK_SET)
        data = os.read(fd, 16)
        hex_str = ' '.join(f'{b:02x}' for b in data)
        print(f"  0x{offset:03x}: {hex_str}")
    os.close(fd)
except Exception as e:
    print(f"  Error: {e}")
PYEOF
done

# -------------------------------------------------------------------
section "7. Kernel i915 backlight type"
# -------------------------------------------------------------------

# Check what backlight type i915 chose
for f in /sys/class/backlight/intel_backlight/type; do
    echo "intel_backlight type: $(cat "$f")"
done

# Check if there's a dpcd_backlight module param
echo "i915 enable_dpcd_backlight param:"
cat /sys/module/i915/parameters/enable_dpcd_backlight 2>/dev/null || echo "(cannot read)"

# Check VBT/parsed panel type
echo
echo "i915 VBT info (if available):"
for card in /sys/kernel/debug/dri/card*; do
    [ -d "$card" ] || continue
    for vbt in "$card"/i915_vbt; do
        [ -f "$vbt" ] || continue
        echo "=== $(basename "$card") ==="
        grep -i -E 'backlight|panel|bl |type| pwm' "$vbt" 2>/dev/null | head -20
    done
done

echo
echo "============================================================"
echo "  DONE — tell me which test(s) made the TOP panel dim."
echo "  Also report the AUX device number if test 4 or 5 worked."
echo "============================================================"
