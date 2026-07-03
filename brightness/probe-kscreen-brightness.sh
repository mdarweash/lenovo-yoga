#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./probe-kscreen-brightness.sh info
  ./probe-kscreen-brightness.sh probe
  ./probe-kscreen-brightness.sh reset

Modes:
  info   Save current KScreen/KWin output state to /tmp
  probe  Interactively test KScreen per-output brightness controls
  reset  Force both internal panels to the same KScreen brightness values
EOF
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
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

timestamp=$(date +%Y%m%d-%H%M%S)
report="/tmp/yogabook-kscreen-report-${timestamp}.txt"

log() {
    echo "$*" | tee -a "$report"
}

section() {
    echo | tee -a "$report"
    echo "== $* ==" | tee -a "$report"
}

run_and_log() {
    local label="$1"
    shift

    section "$label"
    log "COMMAND: $*"
    "$@" 2>&1 | tee -a "$report"
}

ensure_session() {
    [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ] || {
        echo "No GUI session variables detected. Run this from your logged-in KDE session." >&2
        exit 1
    }
}

ensure_outputs_exist() {
    local out
    out=$(kscreen-doctor -o 2>/dev/null || true)
    echo "$out" | grep -q 'eDP-1' || {
        echo "eDP-1 not found in kscreen-doctor output." >&2
        exit 1
    }
    echo "$out" | grep -q 'eDP-2' || {
        echo "eDP-2 not found in kscreen-doctor output." >&2
        exit 1
    }
}

restore_nominal_values() {
    kscreen-doctor \
        output.eDP-1.brightness.100 \
        output.eDP-2.brightness.100 \
        output.eDP-1.sdr-brightness.496 \
        output.eDP-2.sdr-brightness.496
}

probe_brightness() {
    local output="$1"

    section "Probe $output brightness"
    log "Setting $output brightness to 30 for 3 seconds"
    kscreen-doctor \
        output.eDP-1.brightness.100 \
        output.eDP-2.brightness.100 \
        "output.${output}.brightness.30"
    sleep 3
    observation=$(prompt_choice "Which panel changed for ${output} brightness? [top/bottom/both/none]: ")
    log "Observation: $observation"
    restore_nominal_values | tee -a "$report"
    sleep 1
}

probe_sdr_brightness() {
    local output="$1"

    section "Probe $output sdr-brightness"
    log "Setting $output sdr-brightness to 200 for 3 seconds"
    kscreen-doctor \
        output.eDP-1.brightness.100 \
        output.eDP-2.brightness.100 \
        output.eDP-1.sdr-brightness.496 \
        output.eDP-2.sdr-brightness.496 \
        "output.${output}.sdr-brightness.200"
    sleep 3
    observation=$(prompt_choice "Which panel changed for ${output} sdr-brightness? [top/bottom/both/none]: ")
    log "Observation: $observation"
    restore_nominal_values | tee -a "$report"
    sleep 1
}

mode="${1:-}"
[ -n "$mode" ] || {
    usage
    exit 1
}

require_cmd kscreen-doctor
ensure_session
ensure_outputs_exist

section "Report File"
log "$report"

section "Environment"
log "date=$(date --iso-8601=seconds)"
log "display=${DISPLAY:-}"
log "wayland_display=${WAYLAND_DISPLAY:-}"
log "xdg_session_type=${XDG_SESSION_TYPE:-}"
log "dbus_session_bus_address=${DBUS_SESSION_BUS_ADDRESS:-}"

case "$mode" in
    info)
        run_and_log "KScreen Outputs" kscreen-doctor -o
        run_and_log "KScreen JSON" kscreen-doctor -j
        if [ -f "$HOME/.config/kwinoutputconfig.json" ]; then
            run_and_log "KWin Output Config" cat "$HOME/.config/kwinoutputconfig.json"
        fi
        ;;
    probe)
        run_and_log "KScreen Outputs Before" kscreen-doctor -o
        probe_brightness eDP-1
        probe_brightness eDP-2
        probe_sdr_brightness eDP-1
        probe_sdr_brightness eDP-2
        run_and_log "KScreen Outputs After" kscreen-doctor -o
        ;;
    reset)
        run_and_log "KScreen Outputs Before" kscreen-doctor -o
        section "Reset Action"
        log "Forcing both panels to brightness=100 and sdr-brightness=496"
        restore_nominal_values | tee -a "$report"
        run_and_log "KScreen Outputs After" kscreen-doctor -o
        ;;
    -h|--help|help)
        usage
        exit 0
        ;;
    *)
        usage
        exit 1
        ;;
esac

section "Done"
log "Report saved to:"
log "$report"
