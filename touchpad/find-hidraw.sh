#!/bin/bash
# Find the correct hidraw device for INGENIC touch (interface 3)
sudo bash -c '
for hr in /dev/hidraw*; do
    info=$(udevadm info -q property "$hr" 2>/dev/null)
    vid=$(echo "$info" | grep "ID_VENDOR_ID=" | cut -d= -f2)
    pid=$(echo "$info" | grep "ID_MODEL_ID=" | cut -d= -f2)
    iface=$(echo "$info" | grep "ID_USB_INTERFACE_NUM" | cut -d= -f2)
    if [ "$vid" = "17ef" ] && [ "$pid" = "6161" ]; then
        echo "$hr  vendor=$vid product=$pid interface=$iface"
    fi
done
'
