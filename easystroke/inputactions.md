# InputActions Configuration

Config location: `~/.config/inputactions/config.yaml`

KWin effect plugin: `kwin_gestures`

---

## Rebuilding / Reinstalling

The installer is at `~/apps/inputactions/inputactions-installer.sh`.

**Important gotchas:**

1. **Anaconda shadows `pkg-config`** — Anaconda's `pkg-config` at `~/anaconda3/bin/pkg-config` doesn't know about system libraries like `libevdev`. The build will fail with `None of the required 'libevdev' found` even though `libevdev-dev` is installed. Fix by putting `/usr/bin` first in PATH.

2. **Don't use `~/` in the script when running with `sudo`** — `sudo` sets `$HOME` to `/root`, so `~/apps/...` resolves to `/root/apps/...` which doesn't exist. Use the absolute path `/home/mdarweash/apps/...` instead.

3. **The moc warning is harmless** — `"main.moc" included but does not contain a Q_OBJECT...` can be ignored.

### Quick rebuild

```bash
# One-liner (if not sudo):
PATH=/usr/bin:$PATH bash /home/mdarweash/apps/inputactions/inputactions-installer.sh --kwin

# Or use the helper script:
sudo /home/mdarweash/myCommands/yogabook/easystroke/install-inputactions.sh
```

Both handle the pkg-config fix. The installer prompts for sudo during `make install`.

---

## Enabling the KWin Effect

After install, enable permanently:

```bash
kwriteconfig6 --file kwinrc --group Plugins --key kwin_gesturesEnabled true
qdbus6 org.kde.KWin /KWin reconfigure
```

To check if it's enabled:

```bash
kreadconfig6 --file kwinrc --group Plugins --key kwin_gesturesEnabled
```

To check if the plugin file is installed:

```bash
ls /usr/lib/x86_64-linux-gnu/qt6/plugins/kwin/effects/plugins/kwin_gestures.so
```

---

## Touchpad Gestures

| Gesture | Fingers | Direction | Trigger | Action |
|---------|---------|-----------|---------|--------|
| Swipe   | 3       | Right     | on end  | Launch `dolphin` |

---

## Mouse Gestures

All mouse gestures are triggered by holding the **forward mouse button** (side button) while drawing a stroke pattern.

### Window Management

| # | Stroke (base64) | Keys Sent | Description |
|---|-----------------|-----------|-------------|
| 1 | `MmQAzjEAZAA=` | `Ctrl+Super+Up` | Maximize window |
| 2 | `MAAAMTNkZAA=` | `Ctrl+Super+Down` | Minimize window |
| 3 | `ADIAACgyKABkMWQA` | `Ctrl+Super+Right` | Tile window right |
| 4 | `ZDEAYwAyZAA=` | `Ctrl+Super+Left` | Tile window left |

### Virtual Desktop Switching

| # | Stroke (base64) | Keys Sent | Description |
|---|-----------------|-----------|-------------|
| 5 | `ZFoAszs4KLAKFVe4AAlkAA==` | `Ctrl+Alt+Super+1` | Switch to desktop 1 |
| 6 | `AFIA7zwtO+1kEWQA` | `Ctrl+Alt+Super+2` | Switch to desktop 2 |
| 7 | `ZAYATiM4PUcZRUlMAF1kAA==` | `Ctrl+Alt+Super+3` | Switch to desktop 3 |
| 8 | `AAAAHBYaGBYyMzITTUZKHmNkZAA=` | `Ctrl+Alt+Super+4` | Switch to desktop 4 |

### Window Control

| # | Stroke (base64) | Keys Sent | Description |
|---|-----------------|-----------|-------------|
| 9 | `GxgAFDYsDws9LhIDSjAY/lIvHPZaLSDoXygj3mIiJtNkGSq9Yhcrnl4XLVVHIjlJMzhHUxFKWFkLTFulBUteswFHYcgAQWQA` | `Alt+F4` | Close window |

---

## Raw Config

```yaml
touchpad:
  gestures:
    - type: swipe
      direction: right
      fingers: 3
      actions:
        - on: end
          command: dolphin

mouse:
  gestures:
    - type: stroke
      strokes: [ 'MmQAzjEAZAA=' ]
      mouse_buttons: [ forward ]
      actions:
        - on: end
          input:
            - keyboard: [ +leftctrl, +leftmeta, up, -leftctrl, -leftmeta ]

    - type: stroke
      strokes: [ 'MAAAMTNkZAA=' ]
      mouse_buttons: [ forward ]
      actions:
        - on: end
          input:
            - keyboard: [ +leftctrl, +leftmeta, down, -leftctrl, -leftmeta ]

    - type: stroke
      strokes: [ 'ADIAACgyKABkMWQA' ]
      mouse_buttons: [ forward ]
      actions:
        - on: end
          input:
            - keyboard: [ +leftctrl, +leftmeta, right, -leftctrl, -leftmeta ]

    - type: stroke
      strokes: [ 'ZDEAYwAyZAA=' ]
      mouse_buttons: [ forward ]
      actions:
        - on: end
          input:
            - keyboard: [ +leftctrl, +leftmeta, left, -leftctrl, -leftmeta ]

    - type: stroke
      strokes: [ 'ZFoAszs4KLAKFVe4AAlkAA==' ]
      mouse_buttons: [ forward ]
      actions:
        - on: end
          input:
            - keyboard: [ +leftctrl, +leftalt, +leftmeta, 1, -leftctrl, -leftalt, -leftmeta ]

    - type: stroke
      strokes: [ 'AFIA7zwtO+1kEWQA' ]
      mouse_buttons: [ forward ]
      actions:
        - on: end
          input:
            - keyboard: [ +leftctrl, +leftalt, +leftmeta, 2, -leftctrl, -leftalt, -leftmeta ]

    - type: stroke
      strokes: [ 'ZAYATiM4PUcZRUlMAF1kAA==' ]
      mouse_buttons: [ forward ]
      actions:
        - on: end
          input:
            - keyboard: [ +leftctrl, +leftalt, +leftmeta, 3, -leftctrl, -leftalt, -leftmeta ]

    - type: stroke
      strokes: [ 'AAAAHBYaGBYyMzITTUZKHmNkZAA=' ]
      mouse_buttons: [ forward ]
      actions:
        - on: end
          input:
            - keyboard: [ +leftctrl, +leftalt, +leftmeta, 4, -leftctrl, -leftalt, -leftmeta ]

    - type: stroke
      strokes: [ 'GxgAFDYsDws9LhIDSjAY/lIvHPZaLSDoXygj3mIiJtNkGSq9Yhcrnl4XLVVHIjlJMzhHUxFKWFkLTFulBUteswFHYcgAQWQA' ]
      mouse_buttons: [ forward ]
      actions:
        - on: end
          input:
            - keyboard: [ +leftalt, F4, -leftalt ]
```

---

## Key Format Reference

The keyboard input sequences use the following format:

- `+key` = key press
- `-key` = key release
- Bare key name (e.g. `up`, `down`, `1`, `F4`) = key press and release

| Key Name | Corresponding Key |
|----------|-------------------|
| `leftctrl` | Left Control |
| `leftalt` | Left Alt |
| `leftmeta` | Left Super/Windows |
| `up` | Arrow Up |
| `down` | Arrow Down |
| `left` | Arrow Left |
| `right` | Arrow Right |
| `F4` | Function key F4 |
| `1`-`4` | Number keys |
