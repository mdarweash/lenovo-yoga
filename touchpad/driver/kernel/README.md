# Yoga Book 9 Touchpad Driver

Kernel driver for the INGENIC MCU virtual touchpad on the Lenovo Yoga Book 9 14IAT9.

## What it does

Activates the bottom screen's virtual touchpad mode by replicating the exact Windows activation sequence discovered via USB captures:

1. **HID output report `[0x20, 0x00]`** → IF2 EP 0x02 (HID Interrupt OUT) — switches MCU to touchpad mode
2. **OSKP `0x20` sync** every 1s → IF1 EP 0x01 (Vendor Specific bulk) — keeps touchpad alive
3. **OSKP `0x31` geometry** → IF1 EP 0x01 — configures touchpad coordinate area

Touch data arrives as standard HID multitouch reports on IF3, handled by the existing `hid-multitouch` kernel driver.

## Why this works (and the old approach didn't)

The old Linux approach sent OSKP `0x25 01` for mode toggle and `0x26` keepalive every 1.5s. USB captures from Windows revealed:

- Windows uses a **HID output report** (not OSKP) to toggle mode
- The keepalive is OSKP `0x20` sync (not `0x26` timestamp) every 1s (not 1.5s)
- None of the commands Linux was sending (`0x25`, `0x26`, `0x21`, `0x27`, `0x28`, `0x4b`, `0xa3`) appear in Windows captures

## Build & Install

```bash
make
sudo make load
```

## Usage

The driver auto-activates touchpad mode when it loads (binds to USB interface 1 of the INGENIC device).

Manual control via sysfs:

```bash
# Check status
cat /sys/bus/usb/drivers/yb9_touchpad/*/activate

# Deactivate (restore touchscreen)
echo 0 | sudo tee /sys/bus/usb/drivers/yb9_touchpad/*/activate

# Reactivate
echo 1 | sudo tee /sys/bus/usb/drivers/yb9_touchpad/*/activate
```

Unload:

```bash
sudo rmmod hid-yb9-touchpad
```

## Phase 1: Userspace Validation Test

Before loading the kernel module, validate the HID toggle mechanism:

```bash
sudo python3 ../test-hid-toggle.py --duration 30
```

This replicates the exact Windows Capture 10 sequence. If touchpad stays active for the full 30s without reverting, the root cause is confirmed.

## USB Interface Map

| IF# | Class | Linux Driver | Role |
|-----|-------|-------------|------|
| 0 | CDC ACM | `cdc_acm` (fails) | MCU serial control |
| 1 | Vendor (0xFF) | **This driver** | OSKP bulk (EP 0x01/0x81) |
| 2 | HID Keyboard | `usbhid` | Keyboard + mode toggle EP 0x02 |
| 3 | HID Multitouch | `hid-multitouch` | Touchscreen/touchpad input |
| 4 | HID Vendor | `hid-generic` | Firmware update |
| 5 | HID Consumer | `hid-generic` | Media keys |
| 6 | HID Vendor | `hid-generic` | Status/events |

## OSKP Commands Used

| Type | Name | Payload | Direction | When |
|------|------|---------|-----------|------|
| `0x20` | Sync/heartbeat | `[0x01, 0x00]` | Host→MCU | Every 1s |
| `0x31` | Geometry rects | 41 bytes | Host→MCU | Activate/deactivate |
| `0x75` | Geometry ACK | MCU→Host | Response to 0x31 |

## Reference

- USB capture analysis: `windows/capture-analysis.md`
- Windows service decompilation: `windows/rd/glm-driver-spec.md`
- Device notes: `../README.md`
