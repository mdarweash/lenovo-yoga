# Yoga Book 9 — Sound Volume Fix

## Problem

Volume keys (Fn+Up/Down) only change the tweeter speakers. The bass speakers stay at full volume regardless, making sound unbalanced at anything below max.

## Root Cause

The Yoga Book 9 (83KJ) has a **4-speaker system** driven by a **Realtek ALC287** codec:

| Speaker Type   | ALSA Control (card 0)            | numid |
|----------------|----------------------------------|-------|
| Master         | `Master Playback Volume`         | 17    |
| Tweeter (L/R)  | `Speaker Playback Volume`        | 13    |
| Bass (L/R)     | `Bass Speaker Playback Volume`   | 15    |

All three have range 0–87 with proper dB scaling (–65.25dB to 0dB, 0.75dB steps).

The ALSA UCM config (`/usr/share/alsa/ucm2/HDA/HiFi-analog.conf`) defaults `spkvol` to `"Speaker"`, so KDE volume keys change `Speaker` (tweeter only) via ACP. `Bass Speaker` is toggled on/off in the EnableSequence but its **volume** is never bound.

## Fix (26.04+)

Change the UCM default from `Speaker` to `Master`. Master controls the overall output to both tweeter and bass, so a single volume change affects everything.

```bash
sudo sed -i 's/^Define.spkvol "Speaker"/Define.spkvol "Master"/' /usr/share/alsa/ucm2/HDA/HiFi-analog.conf
systemctl --user restart wireplumber
```

This survives reboots but **gets overwritten** by `alsa-ucm-conf` package updates. Re-apply after upgrades.

### Verify

```bash
# Check that Fn+Up now changes Master
amixer -c 0 cget numid=17 | grep ": values"
```

Press Fn+Up/Down and confirm the Master value changes.

### Undo

```bash
sudo sed -i 's/^Define.spkvol "Master"/Define.spkvol "Speaker"/' /usr/share/alsa/ucm2/HDA/HiFi-analog.conf
systemctl --user restart wireplumber
```

## Fix (25.10) — Bass Sync Service

On 25.10 (and as a fallback), a systemd user service polls the tweeter volume every 500ms and syncs the bass speaker to match.

> **Important:** The script uses `amixer -c 0` to target the real SOF hardware card. Plain `amixer` defaults to the PipeWire PulseAudio wrapper which only exposes a broken `Master` control (0–65536, no dB scaling).

### Install

```bash
cp yogabook-bass-sync.sh ~/.local/bin/
cp yogabook-bass-sync.service ~/.config/systemd/user/
chmod +x ~/.local/bin/yogabook-bass-sync.sh
systemctl --user daemon-reload
systemctl --user enable --now yogabook-bass-sync.service
```

### Verify

```bash
systemctl --user status yogabook-bass-sync.service
amixer -c 0 cget numid=13 | grep ": values"   # Speaker (tweeter)
amixer -c 0 cget numid=15 | grep ": values"   # Bass speaker
```

### Undo

```bash
systemctl --user disable --now yogabook-bass-sync.service
rm ~/.local/bin/yogabook-bass-sync.sh
rm ~/.config/systemd/user/yogabook-bass-sync.service
```

## Gotchas

- **`amixer` vs `amixer -c 0`**: On 26.04, plain `amixer` targets the PipeWire PulseAudio wrapper card (4 controls, 0–65536 range, no dB). Always use `amixer -c 0` for the real SOF hardware (57+ controls, 0–87, proper dB scaling).
- **UCM override in `/etc/` doesn't work**: The UCM include path `/HDA/HiFi-analog.conf` resolves from `/usr/share/alsa/ucm2/` regardless. Must edit the system file.
- **Package updates**: `alsa-ucm-conf` updates overwrite the fix. Re-apply the sed command.

## Hardware Details

```
Card: sof-hda-dsp (snd_soc_skl_hda_dsp)
Codec: Realtek ALC287 (HDA:10ec0287,17aa390b)
PCI: 00:1f.3 Intel Multimedia audio controller [8086:7728]
Audio stack: PipeWire 1.6.2 + WirePlumber 0.5.13

ALSA mixer controls (card 0, relevant):
  numid=17  Master Playback Volume         (0-87, dB: -65.25 to 0, step 0.75)
  numid=13  Speaker Playback Volume         (0-87, stereo, same dB)
  numid=15  Bass Speaker Playback Volume    (0-87, stereo, same dB)
  numid=18  Master Playback Switch
  numid=14  Speaker Playback Switch         (on/off, stereo)
  numid=16  Bass Speaker Playback Switch    (on/off, stereo)
  numid=6   AMP1 Speaker Playback Volume    (0-448)
  numid=12  AMP2 Speaker Playback Volume    (0-448)
```
