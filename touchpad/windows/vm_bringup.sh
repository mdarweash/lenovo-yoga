#!/usr/bin/env bash
#
# vm_bringup.sh — Start the win11-yb9 VM with the INGENIC device passed through
# and confirm it boots. Run as root. Idempotent + verbose.
#
#   sudo bash vm_bringup.sh
#
set -uo pipefail

VM=win11-yb9

echo "=========================================================="
echo " STEP 1: load usbmon"
echo "=========================================================="
if lsmod | grep -q '^usbmon'; then
    echo "  already loaded"
else
    modprobe usbmon && echo "  loaded" || { echo "  FAILED"; exit 1; }
fi
ls -d /sys/kernel/debug/usb/usbmon/* 2>/dev/null | head || echo "  (debugfs usbmon dirs only visible to root)"

echo
echo "=========================================================="
echo " STEP 2: confirm INGENIC device still on host"
echo "=========================================================="
DEV=""
for d in /sys/bus/usb/devices/*; do
    if [ -r "$d/idVendor" ] && \
       [ "$(cat "$d/idVendor" 2>/dev/null)" = "17ef" ] && \
       [ "$(cat "$d/idProduct" 2>/dev/null)" = "6161" ]; then
        DEV="$d"
        echo "  found: $DEV  busnum=$(cat $d/busnum) devnum=$(cat $d/devnum)"
        break
    fi
done
[ -z "$DEV" ] && { echo "  NOT FOUND on host (already passed to VM?)"; }

echo
echo "=========================================================="
echo " STEP 3: confirm qcow2 readable by libvirt-qemu"
echo "=========================================================="
QCOW="/run/media/mdarweash/9f7cca73-b295-4908-94d7-21f59f2ebd18/VirtualBox/windowss11_kvm/win11.qcow2"
if [ -r "$QCOW" ]; then
    echo "  qcow2 readable as root: $(ls -la "$QCOW" | awk '{print $1,$3,$4,$5}')"
else
    echo "  QCOQ2 NOT READABLE — check mount/ACLs"
    exit 1
fi

echo
echo "=========================================================="
echo " STEP 4: start VM (or report already running)"
echo "=========================================================="
STATE=$(virsh domstate "$VM" 2>/dev/null | tr -d '[:space:]')
echo "  current state: $STATE"
if [ "$STATE" = "running" ]; then
    echo "  already running — leaving as-is"
elif [ "$STATE" = "shut off" ]; then
    echo "  starting..."
    virsh start "$VM" && echo "  start issued" || { echo "  START FAILED"; exit 1; }
else
    echo "  unexpected state; trying start anyway"
    virsh start "$VM" || { echo "  START FAILED"; exit 1; }
fi

echo
echo "=========================================================="
echo " STEP 5: wait for VM to settle (up to 120s)"
echo "=========================================================="
for i in $(seq 1 24); do
    sleep 5
    STATE=$(virsh domstate "$VM" 2>/dev/null | tr -d '[:space:]')
    echo "  t=$((i*5))s  state=$STATE"
    [ "$STATE" = "running" ] || { echo "  VM not running anymore"; break; }
    # Give it the full window to come up; the device passthrough happens at boot.
done

echo
echo "=========================================================="
echo " STEP 6: post-boot status"
echo "=========================================================="
echo "--- VM state ---"
virsh domstate "$VM"
echo "--- VM info ---"
virsh dominfo "$VM" 2>/dev/null | grep -E "Name|State|CPU|Memory|Autostart"
echo "--- INGENIC device still on host? (if gone, it's in the VM) ---"
HOST_DEV=""
for d in /sys/bus/usb/devices/*; do
    if [ -r "$d/idVendor" ] && \
       [ "$(cat "$d/idVendor" 2>/dev/null)" = "17ef" ] && \
       [ "$(cat "$d/idProduct" 2>/dev/null)" = "6161" ]; then
        HOST_DEV="$d"
        echo "  STILL ON HOST: $d"
    fi
done
[ -z "$HOST_DEV" ] && echo "  not on host bus -> likely captured by VM (good)"
echo
echo "=========================================================="
echo " If the VM is running, open virt-manager or a console and"
echo " confirm Windows boots and YB9.Service.exe starts."
echo " THEN run capture_usbmon.sh once touchpad is up."
echo "=========================================================="
