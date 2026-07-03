#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root."
  exit 1
fi

RULE_FILE="/etc/udev/rules.d/99-yogabook-top-panel-rotation.rules"

rm -f "${RULE_FILE}"
udevadm control --reload
udevadm trigger -s input || true

echo "Removed ${RULE_FILE}"
echo
echo "Log out and back in or reboot to ensure KDE reloads the input devices."
