#!/bin/bash
# Install the INGENIC MCU driver via DKMS
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODULE_NAME="ingenic-mcu"
MODULE_VERSION="1.0.0"

echo "=== INGENIC MCU Driver Installer ==="

# Check for build dependencies
check_deps() {
    local missing=()
    for cmd in make gcc dkms; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done

    if [ ! -d /lib/modules/$(uname -r)/build ]; then
        missing+=("linux-headers-$(uname -r)")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        echo "Missing dependencies: ${missing[*]}"
        echo "Install them first, e.g.:"
        echo "  sudo apt install ${missing[*]}"
        exit 1
    fi
}

# Test build first
test_build() {
    echo "Testing build..."
    make -C "$SCRIPT_DIR" clean
    make -C "$SCRIPT_DIR"
    echo "Build successful."
    make -C "$SCRIPT_DIR" clean
}

# DKMS install
dkms_install() {
    echo "Installing via DKMS..."
    sudo cp -r "$SCRIPT_DIR" /usr/src/${MODULE_NAME}-${MODULE_VERSION}
    sudo dkms add ${MODULE_NAME}/${MODULE_VERSION}
    sudo dkms build ${MODULE_NAME}/${MODULE_VERSION}
    sudo dkms install ${MODULE_NAME}/${MODULE_VERSION}
}

# Udev rules
install_udev() {
    echo "Installing udev rules..."
    sudo cp "$SCRIPT_DIR/99-ingenic-mcu.rules" /etc/udev/rules.d/
    sudo udevadm control --reload-rules
}

# Load module
load_module() {
    echo "Loading module..."
    sudo modprobe ingenic_mcu
    echo "Module loaded. Check dmesg for output."
}

check_deps
test_build
dkms_install
install_udev
load_module

echo ""
echo "=== Installation complete ==="
echo "Control touchpad mode via:"
echo "  echo touchpad > /sys/bus/usb/drivers/ingenic_mcu/*/touchpad_mode"
echo "  echo touchscreen > /sys/bus/usb/drivers/ingenic_mcu/*/touchpad_mode"
