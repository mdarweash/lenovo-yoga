#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root."
  exit 1
fi

WRAPPER="/usr/local/sbin/yogabook-sddm-xsetup.sh"
CONF_FILE="/etc/sddm.conf.d/10-yogabook-top-rotation.conf"

rm -f "${CONF_FILE}" "${WRAPPER}"

echo "Removed ${CONF_FILE}"
echo "Removed ${WRAPPER}"
echo
echo "Reboot or run: systemctl restart sddm"
