#!/bin/bash

set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "Run with sudo:"
    echo "  sudo -E bash $0"
    exit 1
fi

timestamp=$(date +%Y%m%d-%H%M%S)
report="/tmp/yogabook-brightness-report-${timestamp}.txt"

user_name="${SUDO_USER:-${USER:-}}"
user_home=""
if [ -n "$user_name" ]; then
    user_home=$(getent passwd "$user_name" | cut -d: -f6 || true)
fi

log() {
    echo "$*" | tee -a "$report"
}

section() {
    echo | tee -a "$report"
    echo "== $* ==" | tee -a "$report"
}

read_value() {
    local path="$1"
    [ -f "$path" ] && cat "$path"
}

discover_backlights() {
    local drm
    for drm in /sys/class/drm/card*-eDP-*; do
        [ -d "$drm" ] || continue
        [ -f "$drm/status" ] || continue

        local status
        status=$(cat "$drm/status")
        [ "$status" = "connected" ] || continue

        local child
        for child in "$drm"/*; do
            [ -d "$child" ] || continue
            [ -f "$child/brightness" ] || continue
            [ -f "$child/max_brightness" ] || continue
            printf '%s|%s|%s\n' "${drm##*/}" "${child##*/}" "$child"
        done
    done
}

show_backlight_state() {
    local connector="$1"
    local name="$2"
    local path="$3"
    local brightness actual max

    brightness=$(read_value "$path/brightness")
    actual=$(read_value "$path/actual_brightness")
    max=$(read_value "$path/max_brightness")

    log "$connector $name brightness=$brightness actual=$actual max=$max path=$path"
}

prompt_choice() {
    local prompt="$1"
    local answer

    while true; do
        read -r -p "$prompt" answer
        case "${answer,,}" in
            top|bottom|both|none)
                echo "${answer,,}"
                return
                ;;
            *)
                echo "Type one of: top, bottom, both, none"
                ;;
        esac
    done
}

write_brightness() {
    local path="$1"
    local value="$2"
    printf '%s\n' "$value" > "$path/brightness"
}

save_kwin_config() {
    if [ -z "$user_home" ]; then
        log "Could not determine user home from SUDO_USER."
        return
    fi

    local cfg="$user_home/.config/kwinoutputconfig.json"
    if [ -f "$cfg" ]; then
        section "KWin Output Config"
        cat "$cfg" | tee -a "$report" >/dev/null
    else
        log "No $cfg"
    fi
}

section "Report File"
log "$report"

section "Environment"
log "date=$(date --iso-8601=seconds)"
log "user_name=$user_name"
log "user_home=$user_home"
log "display=${DISPLAY:-}"
log "wayland_display=${WAYLAND_DISPLAY:-}"
log "dbus_session_bus_address=${DBUS_SESSION_BUS_ADDRESS:-}"

section "Detected Backlights"
mapfile -t backlights < <(discover_backlights)
if [ "${#backlights[@]}" -eq 0 ]; then
    log "No connected eDP backlights found."
    exit 1
fi

for entry in "${backlights[@]}"; do
    IFS='|' read -r connector name path <<<"$entry"
    show_backlight_state "$connector" "$name" "$path"
done

save_kwin_config

declare -A original
declare -A floor_value

section "Interactive Mapping"
log "The script will dim one backlight path at a time for 3 seconds."
log "Watch the screens and answer which physical panel changes: top, bottom, both, none."

for entry in "${backlights[@]}"; do
    IFS='|' read -r connector name path <<<"$entry"
    current=$(cat "$path/brightness")
    max=$(cat "$path/max_brightness")
    original["$path"]="$current"

    test_value=$(( max / 10 ))
    if [ "$test_value" -lt 1 ]; then
        test_value=1
    fi
    floor_value["$path"]="$test_value"

    echo
    log "Testing $connector / $name"
    log "Setting brightness to $test_value for 3 seconds, then restoring to $current"
    write_brightness "$path" "$test_value"
    sleep 3
    observed=$(prompt_choice "Which panel changed for $name? [top/bottom/both/none]: ")
    write_brightness "$path" "$current"
    sleep 1
    log "Observation for $name: $observed"
    show_backlight_state "$connector" "$name" "$path"
done

section "Cross Check"
log "Setting all detected backlights to their 30% value for 3 seconds."
for entry in "${backlights[@]}"; do
    IFS='|' read -r connector name path <<<"$entry"
    write_brightness "$path" "${floor_value[$path]}"
done
sleep 3
observed=$(prompt_choice "When all backlights were dimmed, which panels changed? [top/bottom/both/none]: ")
log "Observation for all-backlights test: $observed"
for entry in "${backlights[@]}"; do
    IFS='|' read -r connector name path <<<"$entry"
    write_brightness "$path" "${original[$path]}"
done

section "Final Backlight State"
for entry in "${backlights[@]}"; do
    IFS='|' read -r connector name path <<<"$entry"
    show_backlight_state "$connector" "$name" "$path"
done

section "Next Step"
log "Share this report file:"
log "$report"
