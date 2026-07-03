#!/bin/bash

REPO="https://github.com/mdarweash/lenovo-yoga.git"
REPO_DIR="$HOME/.local/share/lenovo-yoga"
SCRIPT_SRC="$REPO_DIR/sound/yogabook-bass-sync.sh"
SERVICE_SRC="$REPO_DIR/sound/yogabook-bass-sync.service"
SCRIPT_DST="$HOME/.local/bin/yogabook-bass-sync.sh"
SERVICE_DST="$HOME/.config/systemd/user/yogabook-bass-sync.service"

# Colors
C_GREEN="\033[0;32m"
C_YELLOW="\033[1;33m"
C_RED="\033[0;31m"
C_CYAN="\033[0;36m"
C_BOLD="\033[1m"
C_RESET="\033[0m"
FAIL=0

log_step() {
    echo -e "\n${C_BOLD}${C_CYAN}>>${C_RESET} ${C_BOLD}$1${C_RESET}"
}

log_ok() {
    echo -e "   ${C_GREEN}✓${C_RESET} $1"
}

log_warn() {
    echo -e "   ${C_YELLOW}!${C_RESET} $1"
}

log_fail() {
    echo -e "   ${C_RED}✗${C_RESET} $1"
    FAIL=1
}

log_info() {
    echo -e "   $1"
}

# ── Clone / Update ───────────────────────────────────────────────

log_step "Fetching lenovo-yoga repository"

if [ -d "$REPO_DIR/.git" ]; then
    log_info "Repository exists at $REPO_DIR, pulling latest..."
    git -C "$REPO_DIR" pull --ff-only
    log_ok "Repository updated"
else
    log_info "Cloning $REPO ..."
    mkdir -p "$(dirname "$REPO_DIR")"
    git clone "$REPO" "$REPO_DIR"
    log_ok "Repository cloned to $REPO_DIR"
fi

# ── Install ──────────────────────────────────────────────────────

log_step "Installing bass speaker volume sync"

mkdir -p "$(dirname "$SCRIPT_DST")"
mkdir -p "$(dirname "$SERVICE_DST")"

if [ -f "$SCRIPT_DST" ]; then
    log_warn "Existing script found at $SCRIPT_DST — replacing"
else
    log_info "Copying script to $SCRIPT_DST"
fi
cp "$SCRIPT_SRC" "$SCRIPT_DST"
chmod +x "$SCRIPT_DST"
log_ok "Script installed"

if [ -f "$SERVICE_DST" ]; then
    log_warn "Existing service file found — replacing"
else
    log_info "Copying service to $SERVICE_DST"
fi
cp "$SERVICE_SRC" "$SERVICE_DST"
log_ok "Service file installed"

systemctl --user daemon-reload
log_ok "systemd daemon reloaded"

if systemctl --user is-enabled yogabook-bass-sync.service &>/dev/null; then
    systemctl --user restart yogabook-bass-sync.service
    log_ok "Service restarted"
else
    systemctl --user enable --now yogabook-bass-sync.service
    log_ok "Service enabled and started"
fi

# ── Test ─────────────────────────────────────────────────────────

log_step "Testing volume sync"

SINK_ID="@DEFAULT_AUDIO_SINK@"

if ! wpctl inspect "$SINK_ID" &>/dev/null; then
    log_fail "No default audio sink available"
    log_info "Make sure PipeWire is running and a Speaker profile is active"
    exit 1
fi
SINK_DESC=$(wpctl inspect "$SINK_ID" 2>/dev/null | grep -oP 'node\.description = "\K[^"]+' | head -1)
log_ok "Default sink: $SINK_DESC"

# Save current volume (strip MUTED suffix if present)
ORIG_VOL=$(wpctl get-volume "$SINK_ID" 2>/dev/null | grep -oP '^Volume: \K[\d.]+')
log_info "Current volume: ${ORIG_VOL}"

# Unmute in case it was muted
wpctl set-mute "$SINK_ID" 0

# Test 1: set to 30%
log_info "Setting volume to 30%..."
wpctl set-volume "$SINK_ID" 30%
sleep 2

SPK=$(amixer -c 0 cget numid=13 2>/dev/null | grep -oP ': values=\K[0-9,]+')
BAS=$(amixer -c 0 cget numid=15 2>/dev/null | grep -oP ': values=\K[0-9,]+')

if [ "$SPK" = "$BAS" ]; then
    log_ok "30% — Speaker: $SPK  Bass: $BAS  (match)"
else
    log_fail "30% — Speaker: $SPK  Bass: $BAS  (mismatch!)"
fi

# Test 2: set to 70%
log_info "Setting volume to 70%..."
wpctl set-volume "$SINK_ID" 70%
sleep 2

SPK=$(amixer -c 0 cget numid=13 2>/dev/null | grep -oP ': values=\K[0-9,]+')
BAS=$(amixer -c 0 cget numid=15 2>/dev/null | grep -oP ': values=\K[0-9,]+')

if [ "$SPK" = "$BAS" ]; then
    log_ok "70% — Speaker: $SPK  Bass: $BAS  (match)"
else
    log_fail "70% — Speaker: $SPK  Bass: $BAS  (mismatch!)"
fi

# Restore original volume
log_info "Restoring original volume: ${ORIG_VOL}"
wpctl set-volume "$SINK_ID" "${ORIG_VOL}"

# ── Status ───────────────────────────────────────────────────────

log_step "Service status"
systemctl --user status yogabook-bass-sync.service --no-pager 2>/dev/null | sed 's/^/   /'

# ── Result ───────────────────────────────────────────────────────

if [ "$FAIL" -eq 0 ]; then
    echo -e "\n${C_GREEN}${C_BOLD}All tests passed!${C_RESET} Bass speaker volume sync is installed and running."
else
    echo -e "\n${C_RED}${C_BOLD}Some tests failed.${C_RESET} Check the output above."
fi
echo -e "   To undo: ${C_YELLOW}systemctl --user disable --now yogabook-bass-sync.service${C_RESET}"

exit $FAIL
