# Yoga Book 9 Brightness Notes

This machine exposes two separate internal backlight devices:

- `eDP-1` -> `/sys/class/backlight/intel_backlight`
- `eDP-2` -> `/sys/class/backlight/card1-eDP-2-backlight`

Both panels are connected and enabled in DRM, so if only one screen changes brightness, the command being used is likely only writing to `intel_backlight`.

Use [`dual-brightness.sh`](/home/mdarweash/myCommands/yogabook/brightness/dual-brightness.sh) to control both internal panels at once, or target one panel explicitly.

Use [`probe-panel-brightness.sh`](/home/mdarweash/myCommands/yogabook/brightness/probe-panel-brightness.sh) for an interactive root-only mapping test that briefly dims each backlight path and records which physical panel actually changed.

Use [`kscreen-brightness.sh`](/home/mdarweash/myCommands/yogabook/brightness/kscreen-brightness.sh) from your KDE session when sysfs backlight is ineffective. On this machine, `kscreen-doctor` output brightness is the control path that actually affects the bottom panel and, once KWin software brightness is enabled, the top panel too.

Use [`enable-software-brightness.sh`](/home/mdarweash/myCommands/yogabook/brightness/enable-software-brightness.sh) to inspect or patch `~/.config/kwinoutputconfig.json` and enable `allowSdrSoftwareBrightness` on both internal `eDP-*` outputs. This is the most likely fix for the top panel, because the saved KWin config had it disabled on `eDP-1` and enabled on `eDP-2`.

If the upper screen does not react, run this order:

1. `./kscreen-brightness.sh doctor`
2. `./kscreen-brightness.sh fix-config`
3. Log out and log back in to KDE Wayland
4. `./kscreen-brightness.sh set 30 --screen top`

Examples:

```bash
./dual-brightness.sh list
./dual-brightness.sh set 40%
./dual-brightness.sh set 220 --screen 2
./dual-brightness.sh dec 10% --screen eDP-2
sudo -E bash /home/mdarweash/myCommands/yogabook/brightness/probe-panel-brightness.sh
./kscreen-brightness.sh list
./kscreen-brightness.sh doctor
./kscreen-brightness.sh set 100
./kscreen-brightness.sh set 30 --screen bottom
./kscreen-brightness.sh fix-config
./kscreen-brightness.sh reset
./enable-software-brightness.sh status
./enable-software-brightness.sh apply
```

If direct writes are not permitted for your user, the script falls back to `sudo tee` for the `brightness` sysfs files.
