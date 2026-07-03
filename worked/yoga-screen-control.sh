#!/bin/bash
# Save and apply screen layouts by name.
# Profiles stored in: ~/.config/auto-rotate-profiles/
#
# Usage:
#   ./yoga-screen-control.sh save <name>   # save current layout as <name>
#   ./yoga-screen-control.sh <name>        # apply saved profile <name>
#   ./yoga-screen-control.sh list           # list saved profiles

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
    local name="$1"
    local profile_file="$PROFILE_DIR/$name"
    kscreen-doctor --outputs 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -E '(Output:|^	enabled|^	disabled|Scale:|Rotation:|Geometry:|priority|Modes:.*\*)' > "$profile_file"
    echo "Saved current layout as '$name' to $profile_file"
    cat "$profile_file"
}

apply_profile() {
    local profile_name="$1"
    local profile_file="$PROFILE_DIR/$profile_name"

    if [[ ! -f "$profile_file" ]]; then
        echo "ERROR: No profile saved as '$profile_name'. Run: $0 save $profile_name"
        exit 1
    fi

    local cmd_args=()
    local output=""

    while IFS= read -r line; do
        if [[ "$line" == Output:* ]]; then
            output=$(echo "$line" | awk '{print $3}')
        fi
        if [[ "$line" == *priority*1 ]]; then
            cmd_args+=("output.$output.primary")
        fi
        if [[ "$line" == *enabled ]]; then
            cmd_args+=("output.$output.enable")
        fi
        if [[ "$line" == *disabled ]]; then
            cmd_args+=("output.$output.disable")
        fi
        if [[ "$line" == *Rotation:* ]]; then
            local code=$(echo "$line" | awk '{print $2}')
            local rot="${ROTATION_NAME[$code]:-none}"
            cmd_args+=("output.$output.rotation.$rot")
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

    if [[ ${#cmd_args[@]} -gt 0 ]]; then
        kscreen-doctor "${cmd_args[@]}"
        echo "Applied '$profile_name' profile"
    fi
}

list_profiles() {
    if [[ -z "$(ls -A "$PROFILE_DIR" 2>/dev/null)" ]]; then
        echo "No saved profiles."
        return
    fi
    echo "Saved profiles in $PROFILE_DIR:"
    for f in "$PROFILE_DIR"/*; do
        echo "  $(basename "$f")"
    done
}

# --- Handle subcommands ---
case "${1:-}" in
    save)
        if [[ -z "${2:-}" ]]; then
            echo "Usage: $0 save <name>"
            exit 1
        fi
        save_profile "$2"
        ;;
    list)
        list_profiles
        ;;
    *)
        if [[ -z "${1:-}" ]]; then
            echo "Usage: $0 save <name> | <name> | list"
            exit 1
        fi
        apply_profile "$1"
        ;;
esac
#killall kwin_wayland
