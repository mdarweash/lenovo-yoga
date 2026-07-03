#!/usr/bin/env bash
# Fix top screen touch/stylus rotation on Lenovo Yoga Book 9 14
# Sets orientationDBus=8 (Qt::InvertedLandscapeOrientation, 180°) via KWin D-Bus
# Also persists to kwinrc so the fix survives reboots
# No sudo needed

set -euo pipefail

ORIENTATION=8  # Qt::InvertedLandscapeOrientation (180°)
VENDOR_DEC=6111   # 0x17ef
PRODUCT_DEC=24929 # 0x6161
DEV_BASE_NAME="INGENIC Gadget Serial and keyboard"

# Device short names for matching and display
DEVICES=("Touchscreen Top" "Stylus Top")

# Ensure D-Bus session is accessible (needed when run via udev/at)
if [ -z "$XDG_RUNTIME_DIR" ]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
fi

echo "=== Lenovo Yoga Book 9 — Fix Top Screen Touch Orientation ==="
echo

# --- Immediate D-Bus fix ---
echo "--- Applying via D-Bus (immediate) ---"
FOUND=0

# Parse device list: output is like 'as 18 "event7" "event1" ...'
DEVICES_LIST=$(busctl --user get-property org.kde.KWin /org/kde/KWin/InputDevice org.kde.KWin.InputDeviceManager devicesSysNames 2>/dev/null \
  | sed 's/^as [0-9]* //' | tr -d '"' | tr ' ' '\n' | grep '^event')

for ev in $DEVICES_LIST; do
  NAME=$(busctl --user get-property org.kde.KWin "/org/kde/KWin/InputDevice/$ev" org.kde.KWin.InputDevice name 2>/dev/null | sed 's/^s //' | tr -d '"')

  for dev in "${DEVICES[@]}"; do
    if [ "$NAME" = "$DEV_BASE_NAME $dev" ]; then
      ORIENT=$(busctl --user get-property org.kde.KWin "/org/kde/KWin/InputDevice/$ev" org.kde.KWin.InputDevice orientationDBus 2>/dev/null || echo "unknown")
      echo "  [FOUND] $ev: $NAME  (current orientation=$ORIENT)"
      echo "    Setting orientationDBus=$ORIENTATION ..."
      busctl --user set-property org.kde.KWin "/org/kde/KWin/InputDevice/$ev" org.kde.KWin.InputDevice orientationDBus i "$ORIENTATION"
      NEW=$(busctl --user get-property org.kde.KWin "/org/kde/KWin/InputDevice/$ev" org.kde.KWin.InputDevice orientationDBus 2>/dev/null || echo "unknown")
      echo "    -> orientation is now $NEW"
      FOUND=1
    fi
  done
done

if [ "$FOUND" -eq 0 ]; then
  echo "  [WARN] No matching devices found via D-Bus. Is KWin running?"
else
  echo "  D-Bus update done."
fi

# --- Persist to kwinrc ---
echo
echo "--- Persisting to kwinrc (survives reboot) ---"

for dev in "${DEVICES[@]}"; do
  FULL_NAME="$DEV_BASE_NAME $dev"
  echo "  Setting Orientation=$ORIENTATION for '$FULL_NAME'"
  kwriteconfig6 --file kwinrc \
    --group Libinput \
    --group "$VENDOR_DEC" \
    --group "$PRODUCT_DEC" \
    --group "$FULL_NAME" \
    --key Orientation "$ORIENTATION"
done

echo "  kwinrc updated."

echo
echo "=== Done ==="
echo "Touch on the top screen should now be aligned correctly."
echo "The fix will also be applied automatically on next login (via kwinrc)."
