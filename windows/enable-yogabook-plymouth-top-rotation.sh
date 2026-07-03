#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root."
  exit 1
fi

GRUB_FILE="/etc/default/grub"
BACKUP_FILE="/etc/default/grub.bak.$(date +%Y%m%d-%H%M%S)"
TOKEN="video=eDP-1:rotate=180"
REVERT_HINT="/home/mdarweash/myCommands/yogabook/windows/revert-yogabook-plymouth-top-rotation.sh"

cp "${GRUB_FILE}" "${BACKUP_FILE}"

python3 - <<'PY'
from pathlib import Path
import re

grub = Path("/etc/default/grub")
token = "video=eDP-1:rotate=180"
text = grub.read_text()

match = re.search(r"^GRUB_CMDLINE_LINUX_DEFAULT='([^']*)'$", text, re.M)
if not match:
    raise SystemExit("Could not find GRUB_CMDLINE_LINUX_DEFAULT in /etc/default/grub")

current = match.group(1).split()
if token not in current:
    current.append(token)

replacement = "GRUB_CMDLINE_LINUX_DEFAULT='{}'".format(" ".join(current))
text = re.sub(r"^GRUB_CMDLINE_LINUX_DEFAULT='[^']*'$", replacement, text, flags=re.M)
grub.write_text(text)
PY

update-grub

echo "Backed up ${GRUB_FILE} to ${BACKUP_FILE}"
echo "Added ${TOKEN} to GRUB_CMDLINE_LINUX_DEFAULT"
echo
echo "Reboot to test Plymouth/top-panel boot rotation."
echo "Revert with: ${REVERT_HINT}"
echo "This version only adds a rotation token and does not force a mode or refresh."
