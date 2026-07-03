#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./dual-brightness.sh list
  ./dual-brightness.sh get [--screen TARGET]
  ./dual-brightness.sh set VALUE [--screen TARGET]
  ./dual-brightness.sh inc VALUE [--screen TARGET]
  ./dual-brightness.sh dec VALUE [--screen TARGET]

Examples:
  ./dual-brightness.sh list
  ./dual-brightness.sh get
  ./dual-brightness.sh set 40%
  ./dual-brightness.sh set 240 --screen 2
  ./dual-brightness.sh inc 10%
  ./dual-brightness.sh dec 25 --screen eDP-2

Targets:
  1, 2, eDP-1, eDP-2, intel_backlight, card1-eDP-2-backlight

Values:
  N%  Percentage of each target's max brightness
  N   Raw brightness value
EOF
}

die() {
    echo "Error: $*" >&2
    exit 1
}

connector_from_target() {
    case "$1" in
        1|eDP-1|intel_backlight)
            echo "eDP-1"
            ;;
        2|eDP-2|card1-eDP-2-backlight)
            echo "eDP-2"
            ;;
        *)
            die "unknown screen target '$1'"
            ;;
    esac
}

discover_backlights() {
    local drm
    for drm in /sys/class/drm/card*-eDP-*; do
        [ -d "$drm" ] || continue
        [ -f "$drm/status" ] || continue

        local status
        status=$(<"$drm/status")
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

readarray -t BACKLIGHTS < <(discover_backlights)
[ "${#BACKLIGHTS[@]}" -gt 0 ] || die "no connected internal backlights found"

filter_backlights() {
    local target="${1:-}"
    local connector=""
    local entry

    if [ -n "$target" ]; then
        connector=$(connector_from_target "$target")
    fi

    for entry in "${BACKLIGHTS[@]}"; do
        IFS='|' read -r entry_connector entry_name entry_path <<<"$entry"
        if [ -z "$connector" ] || [ "$entry_connector" = "card1-$connector" ] || [ "$entry_connector" = "$connector" ]; then
            printf '%s|%s|%s\n' "$entry_connector" "$entry_name" "$entry_path"
        fi
    done
}

write_value() {
    local path="$1"
    local value="$2"

    if [ -w "$path/brightness" ]; then
        printf '%s\n' "$value" > "$path/brightness"
        return
    fi

    if command -v sudo >/dev/null 2>&1; then
        printf '%s\n' "$value" | sudo tee "$path/brightness" >/dev/null
        return
    fi

    die "cannot write $path/brightness and sudo is unavailable"
}

show_entry() {
    local connector="$1"
    local name="$2"
    local path="$3"
    local current max percent

    current=$(<"$path/brightness")
    max=$(<"$path/max_brightness")
    percent=$(( current * 100 / max ))

    printf '%-12s %-24s current=%-4s max=%-4s %3s%%\n' "$connector" "$name" "$current" "$max" "$percent"
}

apply_value() {
    local mode="$1"
    local amount="$2"
    local target="${3:-}"
    local entry
    local matched=0

    while IFS='|' read -r connector name path; do
        [ -n "$connector" ] || continue
        matched=1

        local current max value delta
        current=$(<"$path/brightness")
        max=$(<"$path/max_brightness")

        case "$mode" in
            set)
                if [[ "$amount" == *% ]]; then
                    value=$(( ${amount%%%} * max / 100 ))
                else
                    value=$amount
                fi
                ;;
            inc|dec)
                if [[ "$amount" == *% ]]; then
                    delta=$(( ${amount%%%} * max / 100 ))
                else
                    delta=$amount
                fi

                if [ "$mode" = "inc" ]; then
                    value=$(( current + delta ))
                else
                    value=$(( current - delta ))
                fi
                ;;
            *)
                die "unsupported mode '$mode'"
                ;;
        esac

        if [ "$value" -lt 0 ]; then
            value=0
        fi
        if [ "$value" -gt "$max" ]; then
            value=$max
        fi

        write_value "$path" "$value"
        show_entry "$connector" "$name" "$path"
    done < <(filter_backlights "$target")

    [ "$matched" -eq 1 ] || die "no matching backlights for target '$target'"
}

cmd="${1:-}"
[ -n "$cmd" ] || {
    usage
    exit 1
}
shift || true

target=""
args=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --screen)
            [ "$#" -ge 2 ] || die "--screen requires a value"
            target="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            args+=("$1")
            shift
            ;;
    esac
done

case "$cmd" in
    list|get)
        matched=0
        while IFS='|' read -r connector name path; do
            [ -n "$connector" ] || continue
            matched=1
            show_entry "$connector" "$name" "$path"
        done < <(filter_backlights "$target")
        [ "$matched" -eq 1 ] || die "no matching backlights for target '$target'"
        ;;
    set|inc|dec)
        [ "${#args[@]}" -eq 1 ] || die "'$cmd' requires exactly one brightness value"
        [[ "${args[0]}" =~ ^[0-9]+%?$ ]] || die "invalid brightness value '${args[0]}'"
        apply_value "$cmd" "${args[0]}" "$target"
        ;;
    *)
        usage
        exit 1
        ;;
esac
