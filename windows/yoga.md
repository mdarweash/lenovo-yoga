# Lenovo Yoga Book 9 14IAH10 Linux Findings

## System

- Model: `Yoga Book 9 14IAH10`
- DMI product code: `83KJ`
- BIOS: `QECN28WW`
- Original Ubuntu kernel: `6.17.0-19-generic`
- Tested mainline kernel: `6.19.10-061910-generic`
- Desktop/session: `KDE Plasma` on `Wayland`

## Hardware Identity

The lower/dual-screen input device enumerates on Linux as:

- USB vendor/product: `17ef:6161`
- Manufacturer: `INGENIC`
- Product: `Gadget Serial and keyboard`

Observed input devices after upgrading to `6.19.10`:

- `INGENIC Gadget Serial and keyboard Touchscreen Top`
- `INGENIC Gadget Serial and keyboard Touchscreen Bottom`
- `INGENIC Gadget Serial and keyboard Stylus Top`
- `INGENIC Gadget Serial and keyboard Stylus Bottom`
- `INGENIC Gadget Serial and keyboard Emulated Touchpad`

## Internet / Upstream Findings

### Kernel support

The important Yoga Book 9i fixes are in newer stable kernels, not in the original `6.17` Ubuntu kernel.

Relevant areas:

- `hid-multitouch` quirks for Yoga Book 9i generation devices
- touch/stylus separation for top vs bottom panel
- filtering of a bad firmware report that can break later touch behavior
- separate audio fixes exist for newer Yoga Book generations

Recommendation reached during investigation:

- Prefer `6.19.y` stable over `6.17`
- `6.19.10` was used successfully here

### Windows architecture

Windows does not implement the Yoga Book experience with generic drivers alone.

From the Windows partition:

- `USB\\VID_17EF&PID_6161&MI_00` is installed as `Lenovo OSK Partner`
- a separate Lenovo virtual HID driver is installed as `Lenovo Virtuals HID Device`
- Lenovo ships app/service components including:
  - `YB9.Service.exe`
  - `YB9.TouchPad.exe`
  - `UserCenter`
  - `GraphicsOsk`

Conclusion:

- Windows uses a combination of:
  - vendor service talking to MCU/HID
  - virtual HID injection
  - UI/app logic for keyboard/touchpad features
- Full Windows parity on Linux will require userspace work, not only kernel support

## Kernel Upgrade Result

After installing `6.19.10`:

- touch became more stable
- Linux exposed separate top/bottom touchscreens and stylus devices correctly
- this confirmed the kernel upgrade materially improved hardware support

## Display / Output Layout Found

KDE output config showed:

- `eDP-1`
  - UUID: `12196955-2478-4f91-9cff-b057c973e11e`
  - transform: `Rotated180`
- `eDP-2`
  - UUID: `74699a3e-6add-4245-868a-659673ac9b3b`
  - transform: `Normal`

Interpretation:

- top screen is `eDP-1`
- bottom screen is `eDP-2`

## Touchscreen Mapping Issue

### Symptom

After the kernel upgrade:

- both panels produced touch on the top screen

### Root cause

KDE had written inconsistent touchscreen mapping:

- `OutputName` was correct
- `OutputUuid` was swapped between `eDP-1` and `eDP-2`

### Fix

`~/.config/kcminputrc` was corrected so that:

- `Touchscreen Top -> eDP-1 -> 12196955-2478-4f91-9cff-b057c973e11e`
- `Touchscreen Bottom -> eDP-2 -> 74699a3e-6add-4245-868a-659673ac9b3b`

### Result

- touch mapping became correct

## Stylus Mapping Issue

### Symptom

Stylus was present in Linux but not working correctly on both panels.

### Root cause

KDE had no saved panel mapping entries for:

- `Stylus Top`
- `Stylus Bottom`

### Fix

Added stylus mappings to `~/.config/kcminputrc`:

- `Stylus Top -> eDP-1 -> 12196955-2478-4f91-9cff-b057c973e11e`
- `Stylus Bottom -> eDP-2 -> 74699a3e-6add-4245-868a-659673ac9b3b`

## Top Screen Flipped But Touch Not Flipped

### Symptom

- top panel display is inverted
- top-panel touch initially was not inverted to match
- bottom panel was fine

### Fix approach

Created a root-run udev rule script to apply a libinput calibration matrix only to:

- `Touchscreen Top`
- `Stylus Top`

Calibration matrix used:

```text
-1 0 1 0 -1 1
```

This rotates the top input 180 degrees to match the top display orientation.

Script:

- `fix-yogabook-top-touch-rotation.sh`

## Login Screen / Plymouth Rotation

### Login screen

Problem:

- top screen at login screen was flipped incorrectly

Fix:

- implemented an SDDM X11 display hook using `xrandr`

Script:

- `install-yogabook-sddm-top-rotation.sh`

Revert:

- `revert-yogabook-sddm-top-rotation.sh`

Result:

- `login screen [OK]`

### Plymouth

Problem:

- top screen still flipped during Plymouth / boot splash

Attempted fix:

- added kernel command-line rotation token through GRUB
- safer final token used:

```text
video=eDP-1:rotate=180
```

Scripts:

- `enable-yogabook-plymouth-top-rotation.sh`
- `revert-yogabook-plymouth-top-rotation.sh`
- `install-yogabook-brightness-sync.sh`
- `revert-yogabook-brightness-sync.sh`

Result:

- `plymouth [NO]`

Conclusion:

- SDDM/login can be fixed reliably from userspace
- Plymouth early boot rotation was not fixed by the tested kernel command-line method on this machine

## Scripts Created

In `/home/mdarweash/myCommands/yogabook/windows`:

- `fix-yogabook-top-touch-rotation.sh`
- `install-yogabook-sddm-top-rotation.sh`
- `revert-yogabook-sddm-top-rotation.sh`
- `enable-yogabook-plymouth-top-rotation.sh`
- `revert-yogabook-plymouth-top-rotation.sh`

## Current Working State

Working:

- newer kernel installed
- touch more stable
- top/bottom touch mapping fixed
- stylus mapping configured
- login screen top-panel orientation fixed

Not solved:

- Plymouth top-panel rotation
- Windows-style virtual keyboard / floating touchpad feature parity on Linux
- Plasma/Wayland brightness changes affecting only the lower panel until a sync workaround is installed

## Wayland Brightness Note

On this machine under Plasma Wayland, Linux exposes two backlight controls:

- `intel_backlight` for `eDP-1` (top panel)
- `card1-eDP-2-backlight` for `eDP-2` (bottom panel)

Observed issue:

- Plasma brightness control was changing only the lower panel

Practical workaround added in this directory:

- `install-yogabook-brightness-sync.sh`
- `revert-yogabook-brightness-sync.sh`

The install script creates a small root `systemd` service that mirrors the bottom-panel brightness value onto the top panel so existing brightness keys continue to work acceptably on Wayland.

## Practical Conclusion

Linux is now usable on this Yoga Book with meaningful improvements after moving from `6.17` to `6.19.10`.

What was achieved:

- stable separate top/bottom touch devices
- correct KDE mapping for touch and stylus
- corrected login-screen orientation

What remains outside simple kernel-only fixes:

- Plymouth splash orientation
- Lenovo-specific virtual keyboard/touchpad UX from Windows
