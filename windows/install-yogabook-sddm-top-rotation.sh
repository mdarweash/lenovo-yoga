#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root."
  exit 1
fi

INSTALL_DIR="/usr/local/sbin"
WRAPPER="${INSTALL_DIR}/yogabook-sddm-xsetup.sh"
CONF_DIR="/etc/sddm.conf.d"
CONF_FILE="${CONF_DIR}/10-yogabook-top-rotation.conf"
REVERT_HINT="/home/mdarweash/myCommands/yogabook/windows/revert-yogabook-sddm-top-rotation.sh"

mkdir -p "${INSTALL_DIR}" "${CONF_DIR}"

cat >"${WRAPPER}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [[ -x /usr/share/sddm/scripts/Xsetup ]]; then
  /usr/share/sddm/scripts/Xsetup || true
fi

if command -v xrandr >/dev/null 2>&1; then
  xrandr --output eDP-1 --rotate inverted --output eDP-2 --rotate normal || true
fi
EOF

chmod 0755 "${WRAPPER}"

cat >"${CONF_FILE}" <<EOF
[X11]
DisplayCommand=${WRAPPER}
EOF

echo "Installed ${WRAPPER}"
echo "Installed ${CONF_FILE}"
echo
echo "SDDM will use the custom X11 display hook on the next restart."
echo "Reboot or run: systemctl restart sddm"
echo "Revert with: ${REVERT_HINT}"
