#!/usr/bin/env bash
#
# capture_usbmon.sh — Capture host-side USB traffic of the INGENIC 17ef:6161
# device while it is passed through to the Windows VM.
#
# Captures usbmon bus 3 (where 3-6 sits) to a timestamped pcap, plus a
# t0 marker. Must run as root.
#
# Usage:
#   sudo ./capture_usbmon.sh [duration_seconds] [label]
#
# Examples:
#   sudo ./capture_usbmon.sh 60 steady-idle      # touchpad up, no touching
#   sudo ./capture_usbmon.sh 30 activation        # capture right as you toggle
#   sudo ./capture_usbmon.sh 45 active-use        # move finger around
#
set -euo pipefail

DURATION="${1:-60}"
LABEL="${2:-capture}"
OUTDIR="$(dirname "$(readlink -f "$0")")/captures"
mkdir -p "$OUTDIR"

# Find the bus for 17ef:6161
BUS=""
for d in /sys/bus/usb/devices/*; do
    if [ -r "$d/idVendor" ] && \
       [ "$(cat "$d/idVendor" 2>/dev/null)" = "17ef" ] && \
       [ "$(cat "$d/idProduct" 2>/dev/null)" = "6161" ]; then
        BUS="$(cat "$d/busnum" 2>/dev/null)"
        DEVNUM="$(cat "$d/devnum" 2>/dev/null)"
        break
    fi
done

if [ -z "$BUS" ]; then
    echo "ERROR: 17ef:6161 not found on any USB bus." >&2
    echo "Is it passed through to the VM right now? If so, usbmon won't see it" >&2
    echo "as a host device — capture the host controller bus instead." >&2
    exit 1
fi

# Load usbmon if needed
if ! lsmod | grep -q '^usbmon'; then
    modprobe usbmon || true
fi

INTERFACE="usbmon${BUS}"
OUT="${OUTDIR}/${LABEL}-$(date +%Y%m%d-%H%M%S).pcap"

echo "============================================================"
echo " Device: 17ef:6161 on USB bus ${BUS}, devnum ${DEVNUM}"
echo " Capture iface: ${INTERFACE}"
echo " Output: ${OUT}"
echo " Duration: ${DURATION}s"
echo "============================================================"
echo
echo ">>> START the touchpad action NOW (or confirm it's already active) <<<"
echo

# Capture. tcpdump on usbmon writes pcap DLT 220.
timeout "${DURATION}" tcpdump -i "${INTERFACE}" -w "${OUT}" -U -s 0 \
    'usb.device_address == '"${DEVNUM}" 2> "${OUT%.pcap}.log" || true

echo
echo "============================================================"
echo "Capture complete: ${OUT}"
ls -la "${OUT}"
echo "============================================================"
echo
echo "Analyze with:"
echo "  python3 $(dirname "$0")/analyze_usbmon.py ${OUT}"
