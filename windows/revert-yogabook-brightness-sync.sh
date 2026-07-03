#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root."
  exit 1
fi

WRAPPER="/usr/local/sbin/yogabook-brightness-sync.sh"
SERVICE_FILE="/etc/systemd/system/yogabook-brightness-sync.service"

if systemctl list-unit-files yogabook-brightness-sync.service >/dev/null 2>&1; then
  systemctl disable --now yogabook-brightness-sync.service || true
fi

rm -f "${SERVICE_FILE}" "${WRAPPER}"

systemctl daemon-reload

echo "Removed ${WRAPPER}"
echo "Removed ${SERVICE_FILE}"
