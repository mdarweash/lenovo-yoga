#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./enable-software-brightness.sh status
  ./enable-software-brightness.sh apply

Environment:
  KWIN_OUTPUT_CONFIG_PATH   Override ~/.config/kwinoutputconfig.json

Notes:
  - The default mode is 'apply' for backward compatibility.
  - After enabling software brightness, log out and back in to KDE Wayland.
EOF
}

die() {
    echo "Error: $*" >&2
    exit 1
}

cmd="${1:-apply}"
case "$cmd" in
    status|apply)
        ;;
    -h|--help|help)
        usage
        exit 0
        ;;
    *)
        die "unknown command '$cmd'"
        ;;
esac

cfg="${KWIN_OUTPUT_CONFIG_PATH:-${HOME}/.config/kwinoutputconfig.json}"
[ -f "$cfg" ] || die "missing $cfg"

python_output=$(
    python3 - "$cfg" "$cmd" <<'PY'
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

path = Path(sys.argv[1])
mode = sys.argv[2]
data = json.loads(path.read_text())
outputs = []
changed = []

for section in data:
    if section.get("name") != "outputs":
        continue
    for output in section.get("data", []):
        connector = output.get("connectorName", "")
        if not connector.startswith("eDP-"):
            continue
        before = output.get("allowSdrSoftwareBrightness")
        outputs.append((connector, before))
        if mode == "apply" and before is not True:
            output["allowSdrSoftwareBrightness"] = True
            changed.append((connector, before, True))

if mode == "apply" and changed:
    backup = path.with_name(f"{path.name}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(data, indent=4) + "\n")
    print(f"BACKUP={backup}")

for connector, before in outputs:
    print(f"STATUS|{connector}|{before!r}")

for connector, before, after in changed:
    print(f"CHANGED|{connector}|{before!r}|{after!r}")

print(f"MODE={mode}")
print(f"CHANGED_COUNT={len(changed)}")
print(f"OUTPUT_COUNT={len(outputs)}")
PY
)

backup_path=""
changed_count=0
output_count=0
statuses=()
changes=()

while IFS= read -r line; do
    case "$line" in
        BACKUP=*)
            backup_path="${line#BACKUP=}"
            ;;
        STATUS\|*)
            statuses+=("${line#STATUS|}")
            ;;
        CHANGED\|*)
            changes+=("${line#CHANGED|}")
            ;;
        CHANGED_COUNT=*)
            changed_count="${line#CHANGED_COUNT=}"
            ;;
        OUTPUT_COUNT=*)
            output_count="${line#OUTPUT_COUNT=}"
            ;;
    esac
done <<<"$python_output"

[ "$output_count" -gt 0 ] || die "no internal eDP outputs were found in $cfg"

echo "Config: $cfg"
if [ -n "$backup_path" ]; then
    echo "Backup written to $backup_path"
fi
echo
echo "Current eDP software-brightness policy:"
for entry in "${statuses[@]}"; do
    IFS='|' read -r connector before <<<"$entry"
    printf '  %-6s allowSdrSoftwareBrightness=%s\n' "$connector" "$before"
done

if [ "$cmd" = "apply" ]; then
    echo
    if [ "$changed_count" -gt 0 ]; then
        echo "Updated:"
        for entry in "${changes[@]}"; do
            IFS='|' read -r connector before after <<<"$entry"
            printf '  %-6s %s -> %s\n' "$connector" "$before" "$after"
        done
    else
        echo "No changes were needed."
    fi

    echo
    echo "Next:"
    echo "  1. Log out and log back in to KDE Wayland."
    echo "  2. Run: bash /home/mdarweash/myCommands/yogabook/brightness/kscreen-brightness.sh doctor"
    echo "  3. Test: bash /home/mdarweash/myCommands/yogabook/brightness/kscreen-brightness.sh set 30 --screen top"
fi
