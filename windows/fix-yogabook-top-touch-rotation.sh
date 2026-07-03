#!/usr/bin/env bash
set -euo pipefail

RULE_FILE="/etc/udev/rules.d/99-yogabook-top-panel-rotation.rules"
CAL_MATRIX="-1 0 1 0 -1 1"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root."
  exit 1
fi

cat >"${RULE_FILE}" <<EOF
ACTION=="add|change", SUBSYSTEM=="input", ATTRS{name}=="INGENIC Gadget Serial and keyboard Touchscreen Top", ENV{LIBINPUT_CALIBRATION_MATRIX}="${CAL_MATRIX}"
EOF

udevadm control --reload
udevadm trigger -s input

echo "Installed ${RULE_FILE}:"
sed -n '1,20p' "${RULE_FILE}"
echo
echo "If the top panel is still not aligned immediately, log out and back in or reboot."
