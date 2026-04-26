# Yoga Book 9 — Sound Volume Sync (Bass + Tweeter)

## Problem

Volume up/down only affected the tweeter speakers. The bass speakers stayed at full volume regardless of system volume changes, resulting in unbalanced sound.

## Root Cause

The Yoga Book 9 (83KJ) has a **4-speaker system** driven by a **Realtek ALC287** codec:

| Speaker Type   | ALSA Control                     | numid |
|----------------|----------------------------------|-------|
| Tweeter (L/R)  | `Speaker Playback Volume`        | 13    |
| Bass (L/R)     | `Bass Speaker Playback Volume`   | 15    |

The ALSA UCM config (`/usr/share/alsa/ucm2/HDA/HiFi-analog.conf`) defines the `PlaybackVolume` for the Speaker device as only `"Speaker Playback Volume"`. The bass speaker switch is toggled on/off in `EnableSequence`/`DisableSequence`, but its **volume** is never bound to PipeWire's volume control.

So when KDE volume keys or `wpctl` change volume, only the tweeter ALSA control is adjusted. The bass speakers remain at their last manually set value (typically max).

## Fix

A systemd user service that polls the tweeter volume every 500ms and syncs the bass speaker to match.

### Files

- **`yogabook-bass-sync.sh`** — sync script
- **`yogabook-bass-sync.service`** — systemd user service

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
# Check service is running
systemctl --user status yogabook-bass-sync.service

# Change volume and confirm both controls match
wpctl set-volume @DEFAULT_SINK@ 50%
amixer cget numid=13 | grep ": values"   # Speaker (tweeter)
amixer cget numid=15 | grep ": values"   # Bass speaker
```

### Undo

```bash
systemctl --user disable --now yogabook-bass-sync.service
rm ~/.local/bin/yogabook-bass-sync.sh
rm ~/.config/systemd/user/yogabook-bass-sync.service
```

## Why Not WirePlumber or UCM

- **WirePlumber Lua script**: WP's Lua sandbox blocks `io.popen`/`os.execute`, making it impossible to call `amixer` from a script. Using the PipeWire Pod API to manipulate ALSA mixer controls directly would be fragile and complex.
- **UCM product config**: Would be the "proper" upstream fix, but `/usr/share/alsa/ucm2/` configs get overwritten by package updates. Also, wrong syntax can prevent audio from loading entirely.
- **Systemd user service**: Runs in userspace, survives package updates, easily reversible, and if it crashes audio keeps working (just without bass sync).

## Hardware Details

```
Card: sof-hda-dsp (snd_soc_skl_hda_dsp)
Codec: Realtek ALC287 (HDA:10ec0287,17aa390b)
PCI: 00:1f.3 Intel Multimedia audio controller [8086:7728]
Audio stack: PipeWire 1.4.7 + WirePlumber 0.5.10

ALSA mixer controls (relevant):
  numid=17  Master Playback Volume         (0-87)
  numid=13  Speaker Playback Volume         (0-87, stereo)
  numid=15  Bass Speaker Playback Volume    (0-87, stereo)
  numid=14  Speaker Playback Switch         (on/off, stereo)
  numid=16  Bass Speaker Playback Switch    (on/off, stereo)
  numid=6   AMP1 Speaker Playback Volume    (0-448)
  numid=12  AMP2 Speaker Playback Volume    (0-448)
```
