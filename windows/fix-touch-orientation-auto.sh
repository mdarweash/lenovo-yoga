#!/usr/bin/env bash
# Auto-recovery script for INGENIC touchscreen orientation
# Called by udev rule or systemd when the INGENIC USB device reconnects
# Runs as the desktop user (not root) to access the user D-Bus session

set -euo pipefail

# Find the desktop user (owner of the KDE session)
DESKTOP_USER=$(loginctl list-sessions --no-legend 2>/dev/null | grep -m1 'user' | awk '{print $3}')
if [ -z "$DESKTOP_USER" ]; then
  DESKTOP_USER="mdarweash"
fi

DESKTOP_UID=$(id -u "$DESKTOP_USER" 2>/dev/null || echo 1000)

# Run the fix script as the desktop user
sudo -u "$DESKTOP_USER" \
  XDG_RUNTIME_DIR="/run/user/$DESKTOP_UID" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$DESKTOP_UID/bus" \
  /home/"$DESKTOP_USER"/myCommands/yogabook/windows/fix-touch-orientation.sh \
  >> /tmp/yogabook-touch-fix.log 2>&1

echo "$(date): auto-recovery ran for $DESKTOP_USER" >> /tmp/yogabook-touch-fix.log
