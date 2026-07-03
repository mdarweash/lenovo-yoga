#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root."
  exit 1
fi

INSTALL_DIR="/usr/local/sbin"
WRAPPER="${INSTALL_DIR}/yogabook-brightness-sync.sh"
SERVICE_FILE="/etc/systemd/system/yogabook-brightness-sync.service"
REVERT_HINT="/home/mdarweash/myCommands/yogabook/windows/revert-yogabook-brightness-sync.sh"

mkdir -p "${INSTALL_DIR}"

cat >"${WRAPPER}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

TOP_BRIGHTNESS="/sys/class/backlight/intel_backlight/brightness"
TOP_MAX="/sys/class/backlight/intel_backlight/max_brightness"
BOTTOM_BRIGHTNESS="/sys/class/backlight/card1-eDP-2-backlight/brightness"
BOTTOM_MAX="/sys/class/backlight/card1-eDP-2-backlight/max_brightness"

for file in "${TOP_BRIGHTNESS}" "${TOP_MAX}" "${BOTTOM_BRIGHTNESS}" "${BOTTOM_MAX}"; do
  if [[ ! -r "${file}" ]]; then
    echo "Missing required backlight file: ${file}" >&2
    exit 1
  fi
done

top_max="$(<"${TOP_MAX}")"
bottom_max="$(<"${BOTTOM_MAX}")"

if [[ "${top_max}" != "${bottom_max}" ]]; then
  echo "Backlight maxima differ: top=${top_max}, bottom=${bottom_max}" >&2
  exit 1
fi

sync_once() {
  local bottom_value top_value
  bottom_value="$(<"${BOTTOM_BRIGHTNESS}")"
  top_value="$(<"${TOP_BRIGHTNESS}")"

  if [[ "${bottom_value}" != "${top_value}" ]]; then
    printf '%s\n' "${bottom_value}" >"${TOP_BRIGHTNESS}"
  fi
}

last_seen=""

sync_once

while true; do
  current="$(<"${BOTTOM_BRIGHTNESS}")"
  if [[ "${current}" != "${last_seen}" ]]; then
    sync_once
    last_seen="${current}"
  fi
  sleep 0.2
done
EOF

chmod 0755 "${WRAPPER}"

cat >"${SERVICE_FILE}" <<EOF
[Unit]
Description=Mirror Yoga Book top-panel brightness from lower panel
After=multi-user.target

[Service]
Type=simple
ExecStart=${WRAPPER}
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now yogabook-brightness-sync.service

echo "Installed ${WRAPPER}"
echo "Installed ${SERVICE_FILE}"
echo
echo "The service is now running and will mirror eDP-2 brightness to eDP-1."
echo "Revert with: ${REVERT_HINT}"
