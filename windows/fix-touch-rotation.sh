#!/bin/bash
# Fix top screen touch/stylus rotation on Lenovo Yoga Book 9 14
# Sets orientationDBus=8 (inverted/180°) via KWin D-Bus — no reboot, no sudo needed

echo "=== Finding touch/stylus devices ==="

FOUND=0

for ev in $(busctl --user get-property org.kde.KWin /org/kde/KWin/InputDevice org.kde.KWin.InputDeviceManager.devicesSysNames 2>/dev/null | tr -d '[]" ' | tr ',' '\n'); do
  NAME=$(busctl --user get-property org.kde.KWin /org/kde/KWin/InputDevice/$ev org.kde.KWin.InputDevice name 2>/dev/null | tr -d '"')
  ORIENT=$(busctl --user get-property org.kde.KWin /org/kde/KWin/InputDevice/$ev org.kde.KWin.InputDevice orientationDBus 2>/dev/null)

  if echo "$NAME" | grep -q "Touchscreen Top"; then
    echo "[FOUND] $ev: $NAME  (current orientation=$ORIENT)"
    echo "  Setting orientationDBus=8 (inverted)..."
    busctl --user set-property org.kde.KWin /org/kde/KWin/InputDevice/$ev org.kde.KWin.InputDevice orientationDBus i 8
    NEW=$(busctl --user get-property org.kde.KWin /org/kde/KWin/InputDevice/$ev org.kde.KWin.InputDevice orientationDBus 2>/dev/null)
    echo "  -> orientation is now $NEW"
    FOUND=1
  fi

done

echo
if [ "$FOUND" -eq 0 ]; then
  echo "[FAIL] No Touchscreen Top or Stylus Top devices found."
else
  echo "Done. Test touch on the top screen now."
fi
