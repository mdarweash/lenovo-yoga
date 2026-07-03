#!/bin/bash
# Auto-rotate screen based on accelerometer orientation.
# Designed for dual-screen KDE Plasma (Wayland) using kscreen-doctor.
#
# Profiles stored in: ~/.config/auto-rotate-profiles/
#
# Usage:
#   ./auto-rotate.sh save <orientation>   # save current layout for an orientation
#   ./auto-rotate.sh                      # apply profile for current orientation, exit

PROFILE_DIR="$HOME/.config/auto-rotate-profiles"
mkdir -p "$PROFILE_DIR"

# kscreen-doctor numeric codes -> named values
declare -A ROTATION_NAME=(
    [1]=none
    [2]=left
    [4]=inverted
    [8]=right
)

save_profile() {
    local orientation="$1"
    local profile_file="$PROFILE_DIR/$orientation"
    kscreen-doctor --outputs 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -E '(Output:|^	enabled|^	disabled|Scale:|Rotation:|Geometry:|priority|Modes:.*\*)' > "$profile_file"
    echo "Saved current layout as '$orientation' profile to $profile_file"
    cat "$profile_file"
}

apply_profile() {
    local orientation="$1"
    local profile_file="$PROFILE_DIR/$orientation"

    if [[ ! -f "$profile_file" ]]; then
        echo "WARNING: No profile saved for '$orientation'. Run: $0 save $orientation"
        return
    fi

    # Build all kscreen-doctor args, then apply in a single atomic command
    local cmd_args=()
    local output=""
    local primary=""

    while IFS= read -r line; do
        if [[ "$line" == Output:* ]]; then
            output=$(echo "$line" | awk '{print $3}')
        fi
        if [[ "$line" == *priority*1 ]]; then
            primary="$output"
        fi
        if [[ "$line" == *enabled ]]; then
            cmd_args+=("output.$output.enable")
        fi
        if [[ "$line" == *disabled ]]; then
            cmd_args+=("output.$output.disable")
        fi
        if [[ "$line" == *Rotation:* ]]; then
            local code=$(echo "$line" | awk '{print $2}')
            local name="${ROTATION_NAME[$code]:-none}"
            cmd_args+=("output.$output.rotation.$name")
        fi
        if [[ "$line" == *Scale:* ]]; then
            local scale=$(echo "$line" | awk '{print $2}')
            cmd_args+=("output.$output.scale.$scale")
        fi
        if [[ "$line" == *Geometry:* ]]; then
            local pos=$(echo "$line" | awk '{print $2}')
            cmd_args+=("output.$output.position.$pos")
        fi
    done < "$profile_file"

    # Set primary output
    if [[ -n "$primary" ]]; then
        cmd_args+=("output.$primary.primary")
    fi

    if [[ ${#cmd_args[@]} -gt 0 ]]; then
        kscreen-doctor "${cmd_args[@]}"
        echo "$(date '+%H:%M:%S') Applied '$orientation' profile: ${cmd_args[*]}"
    fi
}

# --- Handle subcommands ---
case "${1:-}" in
    save)
        detected=$(/usr/bin/python3 -c "
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
proxy = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None,
    'net.hadess.SensorProxy', '/net/hadess/SensorProxy',
    'net.hadess.SensorProxy', None)
proxy.call_sync('ClaimAccelerometer', None, Gio.DBusCallFlags.NONE, -1, None)
result = proxy.call_sync('org.freedesktop.DBus.Properties.Get',
    GLib.Variant('(ss)', ('net.hadess.SensorProxy', 'AccelerometerOrientation')),
    Gio.DBusCallFlags.NONE, -1, None)
print(result.unpack()[0])
proxy.call_sync('ReleaseAccelerometer', None, Gio.DBusCallFlags.NONE, -1, None)
" 2>/dev/null)

        if [[ -z "$detected" ]]; then
            echo "ERROR: Could not detect orientation from accelerometer"
            exit 1
        fi

        read -rp "Do you want to save current settings to: $detected? [y/N] " answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            save_profile "$detected"
        else
            echo "Cancelled."
        fi
        exit 0
        ;;
    update)
        orientation=$(/usr/bin/python3 -c "
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
proxy = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None,
    'net.hadess.SensorProxy', '/net/hadess/SensorProxy',
    'net.hadess.SensorProxy', None)
proxy.call_sync('ClaimAccelerometer', None, Gio.DBusCallFlags.NONE, -1, None)
result = proxy.call_sync('org.freedesktop.DBus.Properties.Get',
    GLib.Variant('(ss)', ('net.hadess.SensorProxy', 'AccelerometerOrientation')),
    Gio.DBusCallFlags.NONE, -1, None)
print(result.unpack()[0])
proxy.call_sync('ReleaseAccelerometer', None, Gio.DBusCallFlags.NONE, -1, None)
" 2>/dev/null)
        if [[ -n "$orientation" && -f "$PROFILE_DIR/$orientation" ]]; then
            save_profile "$orientation"
        fi
        exit 0
        ;;
esac

# --- Read current orientation and apply matching profile ---
orientation=$(/usr/bin/python3 -c "
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
proxy = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None,
    'net.hadess.SensorProxy', '/net/hadess/SensorProxy',
    'net.hadess.SensorProxy', None)
proxy.call_sync('ClaimAccelerometer', None, Gio.DBusCallFlags.NONE, -1, None)
result = proxy.call_sync('org.freedesktop.DBus.Properties.Get',
    GLib.Variant('(ss)', ('net.hadess.SensorProxy', 'AccelerometerOrientation')),
    Gio.DBusCallFlags.NONE, -1, None)
print(result.unpack()[0])
proxy.call_sync('ReleaseAccelerometer', None, Gio.DBusCallFlags.NONE, -1, None)
" 2>/dev/null)

if [[ -n "$orientation" ]]; then
    apply_profile "$orientation"
else
    echo "ERROR: Could not read sensor orientation"
    exit 1
fi
