#!/bin/bash
# run-touchpad-test.sh — Run touchpad activation tests with proper USB access.
#
# Usage: sudo ./run-touchpad-test.sh <command> [args...]
#
# Commands:
#   touchpad [extra args]   Activate touchpad with HID-native geometry (3017x1700)
#   scaled [extra args]     Activate with KDE scaled geometry (1800x1125)
#   native [extra args]     Activate with native geometry (2880x1800)
#   hid [extra args]        Activate with HID touchscreen geometry (30182x18864)
#   restore                 Restore normal touchscreen mode
#   diag                    Run diagnostics
#   multi                   Run a batch of geometry/srcId variants with monitoring
#   monitor                 Just monitor event16 and event19 (no activation)
#   setup-udev              Only install the udev rule (no test)
#
# Examples:
#   sudo ./run-touchpad-test.sh touchpad --monitor
#   sudo ./run-touchpad-test.sh touchpad --src-id 2 --monitor
#   sudo ./run-touchpad-test.sh touchpad --no-disable-touch --monitor
#   sudo ./run-touchpad-test.sh multi
#   sudo ./run-touchpad-test.sh restore

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULE_FILE="/etc/udev/rules.d/99-yogabook-ingenic.rules"

ensure_udev() {
    if ! grep -q "bInterfaceNumber==\"01\"" "$RULE_FILE" 2>/dev/null; then
        echo "Adding udev rule for INGENIC interface 1 access..."
        cat >> "$RULE_FILE" << 'UDEV'

# 3. Allow user access to interface 1 (vendor bulk) for touchpad control
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="17ef", ATTR{idProduct}=="6161", ATTR{bInterfaceNumber}=="01", MODE="0666"
UDEV
        echo "udev rule added."
    fi
    udevadm control --reload-rules 2>/dev/null || true
}

block_cdc_acm() {
    # Prevent cdc_acm from probing interface 0 of the INGENIC device.
    # cdc_acm's probe sends SET_LINE_CODING control transfers that crash the MCU.

    # Find the INGENIC device and deauthorize interface 0
    for d in /sys/bus/usb/devices/*/; do
        [ -f "$d/idVendor" ] || continue
        [ "$(cat "$d/idVendor" 2>/dev/null)" = "17ef" ] || continue
        [ -f "$d/idProduct" ] || continue
        [ "$(cat "$d/idProduct" 2>/dev/null)" = "6161" ] || continue
        local devname=$(basename "$d")
        local auth_file="${d}${devname}:1.0/authorized"
        if [ -f "$auth_file" ]; then
            local cur=$(cat "$auth_file" 2>/dev/null)
            if [ "$cur" != "0" ]; then
                echo "0" > "$auth_file" 2>/dev/null && echo "Interface 0 deauthorized (blocks cdc_acm probe)" || echo "WARNING: could not deauthorize interface 0"
            else
                echo "Interface 0 already deauthorized."
            fi
        fi
        # Also try remove_id as backup
        echo "17ef 6161" > /sys/bus/usb/drivers/cdc_acm/remove_id 2>/dev/null || true
        break
    done
}

cd "$SCRIPT_DIR"

# Always block cdc_acm — this is critical to prevent MCU crashes
block_cdc_acm

CMD="${1:-touchpad}"
shift || true

case "$CMD" in
    setup-udev)
        ensure_udev
        block_cdc_acm
        echo "Done. You can now run without sudo (may need to re-plug or reboot once)."
        ;;
    restore)
        echo "Restoring normal touchscreen mode..."
        python3 test-touchpad-activate.py --restore
        ;;
    diag)
        python3 test-touchpad-activate.py --diag
        ;;
    monitor)
        python3 test-touchpad-activate.py --monitor-only
        ;;
    multi)
        echo "============================================"
        echo "  Multi-variant touchpad activation test"
        echo "============================================"
        echo ""
        echo "Each variant will: restore -> activate -> monitor 10s"
        echo "Touch the bottom screen during each monitoring phase!"
        echo ""

        VARIANTS=(
            # Variant 1: HID-native touchpad coords, SrcId=0, default (with 0x27 disable)
            "touchpad --src-id 0"
            # Variant 2: HID-native, SrcId=2
            "touchpad --src-id 2"
            # Variant 3: HID-native, no disable touch (skip 0x27)
            "touchpad --src-id 0 --no-disable-touch"
            # Variant 4: HID-native, no disable touch, SrcId=2
            "touchpad --src-id 2 --no-disable-touch"
            # Variant 5: minimal sequence (README best replay)
            "touchpad --minimal"
            # Variant 6: scaled geometry for comparison
            "scaled --src-id 0"
            # Variant 7: no orientation commands
            "touchpad --src-id 0 --no-ori"
        )

        for i in "${!VARIANTS[@]}"; do
            variant="${VARIANTS[$i]}"
            num=$((i + 1))

            echo ""
            echo "============================================"
            echo "  Variant $num/${#VARIANTS[@]}: $variant"
            echo "============================================"

            # Restore between tests
            echo "--- Restoring... ---"
            python3 test-touchpad-activate.py --restore 2>&1 || true
            sleep 1

            echo ""
            echo "--- Activating ---"
            python3 test-touchpad-activate.py --geometry $variant --monitor 2>&1 || true

            if [ "$num" -lt "${#VARIANTS[@]}" ]; then
                echo ""
                echo "Press Enter to continue to next variant, or Ctrl+C to stop..."
                read -r -t 5 || true
            fi
        done

        echo ""
        echo "--- Final restore ---"
        python3 test-touchpad-activate.py --restore 2>&1 || true
        echo ""
        echo "All variants tested."
        ;;
    *)
        GEO="$CMD"
        echo "Running touchpad test with geometry: $GEO"
        echo "Args: $*"
        echo ""
        python3 test-touchpad-activate.py --geometry "$GEO" "$@"
        ;;
esac
