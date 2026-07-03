#!/bin/bash
# Remove the udev rule and restore touch to working state
set -e

echo "=== Restoring original touchscreen behavior ==="

rm -f /etc/udev/rules.d/90-lenovo-yogabook9i-touch-top-rotate.rules

echo "Removed udev rule."
echo "Reloading udev..."
udevadm control --reload-rules
udevadm trigger
udevadm settle --timeout=5

echo
echo "Rule removed. Please log out and log back in, or reboot, to restore touch."
echo "If touch is still gone after that, reboot the machine."
