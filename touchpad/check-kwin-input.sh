#!/bin/bash
# Check how KWin sees and maps the INGENIC input devices
# Usage: ./check-kwin-input.sh (no sudo needed)

echo "=== 1. KWin Input Devices ==="
qdbus6 org.kde.KWin /InputDevice org.freedesktop.DBus.Properties.Get org.kde.KWin.InputDevice2 count 2>/dev/null || \
qdbus6 org.kde.KWin /InputDevice org.kde.KWin.InputDevice.count 2>/dev/null || \
echo "  Cannot query KWin input devices"

echo
echo "=== 2. KWin Input Device List ==="
# List all input devices KWin knows about
for path in $(qdbus6 org.kde.KWin /InputDevice 2>/dev/null | grep -oP '/InputDevice/\S+'); do
    name=$(qdbus6 org.kde.KWin "$path" org.freedesktop.DBus.Properties.Get org.kde.KWin.InputDevice2 name 2>/dev/null || \
           qdbus6 org.kde.KWin "$path" org.kde.KWin.InputDevice.name 2>/dev/null)
    if echo "$name" | grep -qi "ingenic\|touch\|stylus"; then
        echo "  DEVICE: $name"
        echo "  PATH: $path"
        # Try to get all properties
        qdbus6 org.kde.KWin "$path" org.freedesktop.DBus.Properties.GetAll org.kde.KWin.InputDevice2 2>/dev/null | grep -E "outputName|enabled|tablet|touchscreen|calibration" || true
        qdbus6 org.kde.KWin "$path" org.freedesktop.DBus.Properties.GetAll org.kde.KWin.InputDevice 2>/dev/null | grep -E "outputName|enabled" || true
        echo
    fi
done

echo "=== 3. KWin Tablet Devices ==="
qdbus6 org.kde.KWin /Tablet 2>/dev/null || echo "  No /Tablet interface"
for path in $(qdbus6 org.kde.KWin /Tablet 2>/dev/null | grep -oP '/Tablet/\S+'); do
    echo "  $path"
    qdbus6 org.kde.KWin "$path" org.freedesktop.DBus.Introspectable.Introspect 2>/dev/null | grep -oP 'property[^"]*"[^"]*"' | head -10
    echo
done

echo "=== 4. kcminputrc Libinput Sections ==="
grep -A 3 "\[Libinput\]" ~/.config/kcminputrc 2>/dev/null || echo "  No Libinput sections"

echo
echo "=== 5. Tablet-specific config ==="
cat ~/.config/kcmtouchpadrc 2>/dev/null | head -30 || echo "  No kcmtouchpadrc"
echo "---"
cat ~/.config/kcmtabletrc 2>/dev/null | head -30 || echo "  No kcmtabletrc"

echo
echo "=== 6. KWin Output config ==="
cat ~/.config/kwinoutputconfig.json 2>/dev/null | python3 -m json.tool 2>/dev/null | head -60 || echo "  No kwinoutputconfig.json"

echo
echo "=== 7. Device properties from sysfs ==="
for ev in 12 15; do
    name=$(cat /sys/class/input/event${ev}/device/name 2>/dev/null)
    prop=$(cat /sys/class/input/event${ev}/device/properties 2>/dev/null)
    echo "  event${ev}: ${name}  PROP=${prop}"
done

echo
echo "=== 8. udev tags ==="
for ev in 12 15; do
    echo "  event${ev}:"
    udevadm info -q property /sys/class/input/event${ev} 2>/dev/null | grep -E "ID_INPUT|LIBINPUT" | sed 's/^/    /'
done
