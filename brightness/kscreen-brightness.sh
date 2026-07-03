#!/bin/bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
KWIN_OUTPUT_CONFIG_PATH="${KWIN_OUTPUT_CONFIG_PATH:-${HOME}/.config/kwinoutputconfig.json}"

usage() {
    cat <<'EOF'
Usage:
  ./kscreen-brightness.sh list
  ./kscreen-brightness.sh get [--screen TARGET]
  ./kscreen-brightness.sh set VALUE [--screen TARGET]
  ./kscreen-brightness.sh inc VALUE [--screen TARGET]
  ./kscreen-brightness.sh dec VALUE [--screen TARGET]
  ./kscreen-brightness.sh reset
  ./kscreen-brightness.sh doctor
  ./kscreen-brightness.sh fix-config

Targets:
  all, top, bottom, eDP-1, eDP-2, 1, 2

Values:
  0-100   Brightness percent for KScreen output brightness

Examples:
  ./kscreen-brightness.sh list
  ./kscreen-brightness.sh set 100
  ./kscreen-brightness.sh set 30 --screen bottom
  ./kscreen-brightness.sh inc 10 --screen eDP-2
  ./kscreen-brightness.sh reset
  ./kscreen-brightness.sh doctor
  ./kscreen-brightness.sh fix-config
EOF
}

die() {
    echo "Error: $*" >&2
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

require_session() {
    [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ] || {
        die "no GUI session variables detected; run this from your KDE session"
    }
}

kwin_config_lines() {
    [ -f "$KWIN_OUTPUT_CONFIG_PATH" ] || return 0

    python3 - "$KWIN_OUTPUT_CONFIG_PATH" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text())

for section in data:
    if section.get("name") != "outputs":
        continue
    for output in section.get("data", []):
        connector = output.get("connectorName", "")
        if connector.startswith("eDP-"):
            value = output.get("allowSdrSoftwareBrightness")
            print(f"{connector}|{value!r}")
PY
}

show_kwin_config_status() {
    local found=0
    local connector value

    if [ ! -f "$KWIN_OUTPUT_CONFIG_PATH" ]; then
        echo "KWin config: missing $KWIN_OUTPUT_CONFIG_PATH"
        return
    fi

    echo "KWin config: $KWIN_OUTPUT_CONFIG_PATH"
    while IFS='|' read -r connector value; do
        [ -n "$connector" ] || continue
        found=1
        printf '  %-6s allowSdrSoftwareBrightness=%s\n' "$connector" "$value"
    done < <(kwin_config_lines)

    [ "$found" -eq 1 ] || echo "  no internal eDP outputs found in config"
}

software_brightness_disabled_for_target() {
    local target="$1"
    local connector value

    [ -f "$KWIN_OUTPUT_CONFIG_PATH" ] || return 1

    while IFS='|' read -r connector value; do
        [ -n "$connector" ] || continue
        case "$target" in
            all)
                if [ "$connector" = "eDP-1" ] && [ "$value" != "True" ]; then
                    return 0
                fi
                ;;
            eDP-1)
                if [ "$connector" = "eDP-1" ] && [ "$value" != "True" ]; then
                    return 0
                fi
                ;;
        esac
    done < <(kwin_config_lines)

    return 1
}

require_upper_screen_fix() {
    local target="$1"

    if software_brightness_disabled_for_target "$target"; then
        cat >&2 <<EOF
Error: the upper panel is still blocked by KWin output config.

Run:
  $SCRIPT_DIR/enable-software-brightness.sh apply

Then log out and log back in to KDE Wayland, and retry this command.
EOF
        exit 1
    fi
}

normalize_target() {
    case "${1:-all}" in
        all)
            echo "all"
            ;;
        top|1|eDP-1)
            echo "eDP-1"
            ;;
        bottom|2|eDP-2)
            echo "eDP-2"
            ;;
        *)
            die "unknown target '$1'"
            ;;
    esac
}

read_output_state() {
    kscreen-doctor -o | perl -pe 's/\e\[[0-9;]*m//g' | perl -0ne '
        while (/Output:\s+\d+\s+(eDP-\d+).*?Brightness control:\s+supported,\s+set to\s+(\d+)%\s+and dimming to\s+(\d+)%/sg) {
            print "$1|$2|$3\n";
        }
    '
}

get_brightness() {
    local target="$1"
    local found=0
    local connector brightness dimming

    while IFS='|' read -r connector brightness dimming; do
        [ -n "$connector" ] || continue
        if [ "$target" = "all" ] || [ "$connector" = "$target" ]; then
            found=1
            printf '%-6s brightness=%3s%% dimming=%3s%%\n' "$connector" "$brightness" "$dimming"
        fi
    done < <(read_output_state)

    [ "$found" -eq 1 ] || die "no matching internal outputs found"
}

read_one_brightness() {
    local target="$1"
    local line connector brightness dimming

    while IFS='|' read -r connector brightness dimming; do
        [ "$connector" = "$target" ] || continue
        echo "$brightness"
        return 0
    done < <(read_output_state)

    return 1
}

clamp_percent() {
    local value="$1"
    if [ "$value" -lt 0 ]; then
        echo 0
    elif [ "$value" -gt 100 ]; then
        echo 100
    else
        echo "$value"
    fi
}

set_outputs() {
    local target="$1"
    local value="$2"
    local -a cmd

    cmd=(kscreen-doctor)
    if [ "$target" = "all" ]; then
        cmd+=(output.eDP-1.brightness."$value" output.eDP-2.brightness."$value")
    else
        cmd+=(output."$target".brightness."$value")
    fi

    "${cmd[@]}"
}

show_doctor() {
    get_brightness all
    echo
    show_kwin_config_status
    echo

    if software_brightness_disabled_for_target all; then
        echo "Diagnosis: eDP-1 still has software brightness disabled in KWin."
        echo "Fix: $SCRIPT_DIR/enable-software-brightness.sh apply"
        echo "After that, log out and log back in to KDE Wayland."
    else
        echo "Diagnosis: KWin config is not blocking eDP software brightness."
    fi
}

apply_delta() {
    local mode="$1"
    local target="$2"
    local delta="$3"

    if [ "$target" = "all" ]; then
        local connector current next
        local -a cmd
        cmd=(kscreen-doctor)
        for connector in eDP-1 eDP-2; do
            current=$(read_one_brightness "$connector") || die "could not read brightness for $connector"
            if [ "$mode" = "inc" ]; then
                next=$(( current + delta ))
            else
                next=$(( current - delta ))
            fi
            next=$(clamp_percent "$next")
            cmd+=(output."$connector".brightness."$next")
        done
        "${cmd[@]}"
        return
    fi

    local current next
    current=$(read_one_brightness "$target") || die "could not read brightness for $target"
    if [ "$mode" = "inc" ]; then
        next=$(( current + delta ))
    else
        next=$(( current - delta ))
    fi
    next=$(clamp_percent "$next")
    set_outputs "$target" "$next"
}

cmd="${1:-}"
[ -n "$cmd" ] || {
    usage
    exit 1
}
shift || true

target="all"
args=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --screen)
            [ "$#" -ge 2 ] || die "--screen requires a value"
            target=$(normalize_target "$2")
            shift 2
            ;;
        -h|--help|help)
            usage
            exit 0
            ;;
        *)
            args+=("$1")
            shift
            ;;
    esac
done

require_cmd kscreen-doctor
require_session

case "$cmd" in
    list|get)
        get_brightness "$target"
        ;;
    set)
        [ "${#args[@]}" -eq 1 ] || die "'set' requires one value"
        [[ "${args[0]}" =~ ^[0-9]+$ ]] || die "brightness must be an integer 0-100"
        require_upper_screen_fix "$target"
        value=$(clamp_percent "${args[0]}")
        set_outputs "$target" "$value"
        get_brightness "$target"
        ;;
    inc|dec)
        [ "${#args[@]}" -eq 1 ] || die "'$cmd' requires one value"
        [[ "${args[0]}" =~ ^[0-9]+$ ]] || die "brightness delta must be an integer 0-100"
        require_upper_screen_fix "$target"
        apply_delta "$cmd" "$target" "${args[0]}"
        get_brightness "$target"
        ;;
    reset)
        require_upper_screen_fix all
        set_outputs all 100
        get_brightness all
        ;;
    doctor)
        show_doctor
        ;;
    fix-config)
        exec "$SCRIPT_DIR/enable-software-brightness.sh" apply
        ;;
    *)
        usage
        exit 1
        ;;
esac
