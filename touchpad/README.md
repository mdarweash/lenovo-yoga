# Yoga Book 9 Touchpad Findings

Updated: 2026-05-03

## Goal

Make the lower screen switch from plain touchscreen mode to the INGENIC emulated touchpad on Linux.

## Current status

**Touchpad mode activates and works on Linux.** Cursor moves, clicks register, multi-touch works. The MCU reverts to touchscreen mode after ~5-7 seconds. Periodic `0x25 01` re-activation every 3s keeps touchpad alive for 27.5s out of 30s with minor oscillation. The root cause is likely that OSKP frames must go through the CDC ACM serial path (`/dev/ttyACM*`), which is unavailable because `cdc_acm` fails to bind.

### Working test command (periodic re-activation)

```bash
sudo python3 /home/mdarweash/myCommands/yogabook/touch/test-windows-replica.py --duration 30
```

### Restore normal touchscreen

```bash
sudo /home/mdarweash/myCommands/yogabook/touch/run-touchpad-test.sh restore
```

---

## Linux device map

- USB device: `17ef:6161` (`INGENIC Gadget Serial and keyboard`) on USB `3-6`
- Bottom touchscreen: `/dev/input/event16` — HID report ID `0x38`, coordinate space 30182x18864
- Emulated touchpad: `/dev/input/event19` — HID report ID `0x50`, coordinate space 3017x1700
- Top touchscreen: `/dev/input/event15`
- Stylus Top/Bottom: `/dev/input/event17`, `/dev/input/event18`
- Keyboard: `/dev/input/event14`

### USB interface map

| IF# | Class | Linux Driver | Windows Driver | Function |
|-----|-------|-------------|----------------|----------|
| 0 | CDC ACM (0x02) | `cdc_acm` (fails to bind) | `usbser.sys` | MCU serial control channel |
| 1 | Vendor (0xFF) | none | none | OSKP bulk data (EP 0x81 IN, EP 0x01 OUT) |
| 2 | HID Boot/Keyboard (0x03) | `usbhid` | HID class | Keyboard |
| 3 | HID Multitouch (0x03) | `hid-multitouch` | Wacom router filter | Touch + Stylus (5 collections) |
| 4 | HID Vendor (0x03) | `hid-generic` | FusionTouchFirmwareUpdate.dll | Firmware update |
| 5 | HID Consumer (0x03) | `hid-generic` | HID class | Media keys |
| 6 | HID Vendor (0x03) | `hid-generic` | HID class | Vendor status/events |

### hidraw device map (INGENIC 17ef:6161)

| hidraw | IF | Size | Output Reports | Notes |
|--------|-----|------|---------------|-------|
| hidraw4 | IF2 | 167B | Yes (2) | Keyboard |
| hidraw5 | IF3 | 2756B | **No** | Touchscreen/touchpad — no HID output reports needed |
| hidraw6 | IF4 | 122B | Yes (2) | Firmware update |
| hidraw7 | IF5 | 141B | Yes (5) | Media keys |
| hidraw8 | IF6 | 121B | No | Vendor events |

---

## Key discoveries (chronological)

### 1. Touchpad mode toggle: `0x25 01`

Recovered from `YB9.PhantomKB.exe`:

- `0x25 01` = touchpad mode ON
- `0x25 00` = touchpad mode OFF

### 2. Critical: Do NOT send `0x27 0000` (disable panel touch)

The `0x27 0000` command disables the bottom panel's touchscreen entirely. If sent before activating touchpad mode, the MCU routes ALL touch to the touchscreen endpoint, not the touchpad endpoint.

**Fix:** Skip `0x27 0000` entirely. Use `--no-disable-touch` flag. This was the breakthrough that made touch routing work correctly.

### 3. Correct geometry: HID touchpad-native coordinate space `3017 x 1700`

The touchpad HID collection (report ID 0x50) uses exactly 1/10th the resolution of the touchscreen:

| Device | Report ID | X Max | Y Max | Resolution |
|---------|-----------|-------|-------|------------|
| Touchscreen | 0x38 | 30182 | 18864 | 100 units/mm |
| **Touchpad** | **0x50** | **3017** | **1700** | **10 units/mm** |

All previous tests used pixel-based geometries (1800x1125, 2880x1800) that didn't match the MCU's expected coordinate space. The `0x21 40/41` screen info and `0x31` rectangle values must use 3017x1700.

### 4. Touchpad UI layout matches Windows `TouchPadMainWindow.xml`

The `0x31` geometry packet rectangles are derived from the Windows touchpad UI layout:

- `touchpad_main_box` → `frameRect` (full window)
- `caption_hbox` → 80px tall header (~16% of 500px)
- `touch_area` → `touchableRect1` (main tracking area)
- `btn_touchpad_left` / `btn_touchpad_right` → `LButton` / `RButton` (90px tall, ~18%, at bottom)
- Side margins: ~8%, bottom margin: ~8%, button gap: ~2%

### 5. `cdc_acm` probe fails — does NOT crash MCU (revised understanding)

Previous diagnosis said `cdc_acm` crashes the MCU. Updated findings:

- `cdc_acm` fails to bind with error: `Zero length descriptor references (error -22)`
- Cause: Interface 1 has class `0xFF` (Vendor Specific) instead of `0x0A` (CDC Data)
- Since the probe fails on IF1, `cdc_acm` never sends `SET_LINE_CODING` to the MCU at all
- The original MCU crashes were from something else, not `cdc_acm`
- `SET_LINE_CODING` at 9600/19200/38400 baud is safe (tested directly via pyusb)
- `SET_LINE_CODING` at 57600+ baud crashes the MCU (re-enumerates in seconds)
- Linux `cdc_acm` sends 9600 8N1 by default, which would be safe — but it never gets that far

**Current approach:** Deauthorize interface 0 to prevent any driver from claiming it, then manually claim with pyusb:

```bash
echo "17ef 6161" > /sys/bus/usb/drivers/cdc_acm/remove_id
echo 0 > /sys/bus/usb/devices/3-6/3-6:1.0/authorized
```

### 6. SET_LINE_CODING baud rate safety (2026-05-03)

Tested all standard baud rates via pyusb `ctrl_transfer` on interface 0:

| Baud | Result |
|------|--------|
| 9600 | ✅ Safe |
| 19200 | ✅ Safe |
| 38400 | ✅ Safe |
| 57600 | ❌ Crash (re-enumerates) |
| 115200 | ❌ Crash (re-enumerates) |

Windows `usbser.sys` likely sends default DCB (9600 8N1) or skips `SET_LINE_CODING` entirely (DCB matches current settings).

### 7. EP 0x82 interrupt endpoint behavior (2026-05-03)

Interface 0 EP 0x82 (CDC interrupt IN) sends serial state notifications:

- One notification received after `SET_CONTROL_LINE_STATE(DTR=1, RTS=1)`: `a1200000000002000300`
- Parsed as CDC SERIAL_STATE: `bmRequestType=0xa1`, `bRequest=0x20`, `wLength=2`, data=`0x0300`
- UART state: DCD=1, DSR=1 (MCU acknowledges DTR assertion)
- No further notifications received after the initial one
- MCU does not send periodic heartbeats on EP 0x82

### 8. Sending `0x25 01` while already in touchpad mode causes immediate revert (2026-05-03)

If the MCU is already in touchpad mode and receives `0x25 01`, it immediately reverts to touchscreen mode. This was observed in variant 7 of the ack reply tests.

### 9. `0x75` ack reply — no response prevents revert (2026-05-03)

Tested 7 variants of replying to the MCU's `0x75` acknowledgment:
1. Reply with same payload
2. Reply with zeros
3. Reply empty payload `0x75 []` — **caused immediate revert**
4. Reply with status
5. Reply with 1 byte
6. Reply with 7 bytes
7. Re-send `0x25 01` — **caused immediate revert**

None prevented the ~7s revert. Variants 3 and 7 caused immediate revert.

---

## Working activation sequence

```
Phase 1: Init + Toggle
  0x4b  0008b80b10271027        timing/threshold
  0x21  8001010000              init OSK param
  0x28  0000                    status pair
  0x26  <timestamp>             keepalive
  (NO 0x27 — do not disable panel touch)
  0x25  01                      touchpad mode ON
  0x20  0100                    sync flag
  0x21  7e01000000              touchpad-on state sync

  wait 2s for device settle

Phase 2: Config + Geometry
  0x26  <timestamp>             keepalive
  0x21  40c90ba406              screen info bX=3017 bY=1700
  0x21  41c90ba406              screen info cX=3017 cY=1700
  0x21  3100000000              orientation oriB=0 oriC=0
  0xa3  01                      post-orientation flag
  0x31  <41-byte geometry>      short-form rect payload
  0x26  <timestamp>             keepalive
```

---

## MCU response protocol

### Response types observed

| Type | Meaning | Notes |
|------|---------|-------|
| `0x50` | Firmware version | e.g. `1.3.36  16:17:39 Nov 17 2025` |
| `0x75` | Mode acknowledgment | Sent after `0x25 01`, payload `dd510000010101` |
| `0xa2` | Touch/gesture events | 6-byte payload with timestamp counter, screen ID, touch state |

### `0xa2` event format

6 bytes parsed as 3 LE16 values:

- bytes 0-1: timestamp counter (MCU uptime in ms)
- bytes 2-3: event data (screen ID, gesture type)
- bytes 4-5: touch state (0x0001 = touch start, 0x0000 = touch end, 0x0101 = screen 1 touch)

---

## Remaining issue: MCU mode revert after ~5-7 seconds

### The problem

After activation, the MCU stays in touchpad mode for approximately 5-7 seconds, then silently reverts to touchscreen mode. No explicit "mode expired" notification is sent.

### What works (with oscillation)

Sending periodic `0x25 01` re-activation every 3s keeps the touchpad functional for 27.5s out of 30s:

```
Results with 0x25 01 every 3s:
  Touchpad events (event19):    727
  Touchscreen events (event16): 243
  Touchpad window: 2.01s — 29.51s
  ✗ REVERTED at 7.1s (but re-activated)
```

The oscillation happens because `0x25 01` while already in touchpad mode causes a brief revert, then re-activation.

### What was tested and doesn't prevent the revert

| Approach | Result |
|----------|--------|
| `0x26` keepalive every 1500ms alone | ❌ Reverts at ~7s |
| CDC session: SET_LINE_CODING(9600) + DTR + RTS on IF0 | ❌ Reverts at ~4s |
| Reading EP 0x82 interrupt continuously | ❌ No further notifications from MCU |
| Replying to `0x75` ACK (7 variants) | ❌ All failed |
| Periodic geometry re-send (`0x31` + `0x21 7e01`) without `0x25 01` | ❌ Reverts permanently |
| `0x25 01` every 3s | ✅ Touchpad persists with oscillation |
| `0x25 01` every 3s + CDC session + EP 0x82 reader | ✅ Same oscillation |

### Root cause analysis (2026-05-03)

The fundamental issue is that **`/dev/ttyACM*` is unavailable** because `cdc_acm` fails to bind. On Windows, OSKP frames go through `WriteFile(COM port)` → `usbser.sys` → CDC ACM data path. On Linux, we send through raw `pyusb.bulk_write()`.

The MCU firmware likely ties the touchpad mode session to the CDC ACM serial data channel. Raw USB bulk writes don't count as "keepalive" from the MCU's perspective, so the session timer expires.

#### Why `cdc_acm` fails

The USB descriptors declare IF0+IF1 as an IAD (Interface Association Descriptor) — a CDC ACM pair. But IF1 has class `0xFF` (Vendor Specific) instead of `0x0A` (CDC Data). When `cdc_acm` probes, it claims IF0 OK, then fails on IF1 with "Zero length descriptor references". The entire probe fails, releasing IF0 as well. No `/dev/ttyACM*` device is created.

#### What we've replicated from Windows (everything visible in decompilation)

1. ✅ SET_LINE_CODING(9600, 8N1) on IF0
2. ✅ SET_CONTROL_LINE_STATE(DTR=1, RTS=1)
3. ✅ EP 0x82 interrupt IN drain
4. ✅ Init: `0x25 00` + `0x31 [33 zeros]`
5. ✅ KeepConnectThread: `0x26` every 1500ms
6. ✅ Continuous EP 0x81 bulk IN reads (reader thread)
7. ✅ Full touchpad activation sequence
8. ✅ Synchronous writes (pyusb default = FlushFileBuffers equivalent)

#### What's different

| # | Windows | Linux | Impact |
|---|---------|-------|--------|
| A | `usbser.sys` claims IF0+IF1 as CDC ACM pair | pyusb claims IF0 and IF1 separately | MCU may check CDC session state |
| B | OSKP frames go through CDC serial data path | Raw USB bulk writes | MCU may only count CDC-path keepalives |
| C | `/dev/ttyACM*` available | Not available (`cdc_acm` fails) | Can't use serial port API |
| D | `PurgeComm(0x0f)` at init | Not done | Host-side buffer clear, likely irrelevant |
| E | `SetupComm(0x400, 0x400)` buffer sizes | Not done | Host-side only, likely irrelevant |
| F | `SetCommMask(EV_RXCHAR)` | Not done | Host-side only, likely irrelevant |
| G | HID IF3 has no output reports | Same | ✅ Ruled out — not a difference |

### Possible paths forward

1. **Make `cdc_acm` work** — patch USB descriptors or kernel module so IF1 appears as CDC Data (class 0x0A). Then use `/dev/ttyACM*` for OSKP frames. This is the most faithful Windows replication.
2. **Accept periodic re-activation** — use `0x25 01` every 3s + grab event16 to suppress brief touchscreen flashes. Build a production daemon. Works today.
3. **USB capture from Windows** — boot Windows with Wireshark+USBPcap to see exact byte-level USB traffic during touchpad operation. May reveal a hidden difference.

---

## Display configuration (for reference)

- eDP-1 (top): 2880x1800, scale 1.6, rotated 180°, logical 1800x1125
- eDP-2 (bottom): 2880x1800, scale 1.6, normal, logical 1800x1125
- KDE scaling: 1.25
- KWin output scale: 1.6

---

## `0x31` short-form packet layout (41 bytes = 0x29)

Fully solved from `YB9.TouchPad.exe` decompilation:

```
Offset  Size  Field
0       8     frameRect (left, top, right, bottom as LE16)
8       8     LButton
16      8     RButton
24      8     touchableRect1
32      1     packed flags: ((SrcId & 0x3) << 1) | (DisableForMini & 0x1)
33      8     touchableRect2
```

Long form (57 bytes = 0x39) adds `noteRect` (8 bytes) + `noteToolRect` (8 bytes) after touchableRect2.

---

## Windows architecture (from Ghidra decompilation of YB9.Service.exe + YB9.TouchPad.exe)

### Service architecture (YB9.Service.exe, 597KB)

```
YB9.Service.exe
  ├── Opens USB IF0 as serial port (USB\\VID_17EF&PID_6161&MI_00)
  │   ├── SetupComm(0x400, 0x400) — 1KB buffers
  │   ├── SetCommState(default DCB 8N1)
  │   ├── PurgeComm(0x0f) — clear all buffers
  │   ├── SetCommMask(EV_RXCHAR)
  │   └── SetCommTimeouts(read=infinite, write=500/5000ms)
  ├── ReadThread (FUN_140007430)
  │   ├── WaitCommEvent + ReadFile loop
  │   ├── Parses incoming OSKP frames
  │   └── Dispatches: 0x9a → gesture, other → generic event
  ├── WriteThread (FUN_140007c20)
  │   ├── Dequeues from internal queue
  │   ├── WriteFile + FlushFileBuffers
  │   └── 3 retries on failure, then reconnect
  ├── KeepConnectThread (FUN_140006eb0)
  │   ├── Sleep(1500ms) after connect
  │   ├── Builds timestamp: year_lo,year_hi,month,day,hour,minute,second
  │   ├── Sends type 0x26 with timestamp
  │   └── WaitForSingleObject(stop_event, INFINITE)
  └── Init sequence:
      ├── 0x25 [0x00] — init toggle
      └── 0x31 [33 zero bytes] — init config
```

### TouchPad app architecture (YB9.TouchPad.exe, 2.2MB)

```
YB9.TouchPad.exe
  ├── Connects to service via named pipe (\\.\pipe\YB9Service)
  ├── Communication via PyxisHelperAPI.dll exports:
  │   ├── EiPostMcuCommand(struct) — raw MCU command
  │   ├── EiPostJsonCommand(json) — JSON command to service
  │   ├── EiSetScreenMode(mode) — screen mode
  │   ├── EiEnableWinOsk(enable) — on-screen keyboard
  │   └── EiWriteITS(mode) — ITS mode
  ├── TouchPadSetMcuTouchMode — activates touchpad (0x25 01)
  ├── SendRectToMCU — sends geometry (0x31 with rect payload)
  │   └── FUN_140043ab0 — serializer, packs rects as LE16 quadruples
  ├── ResendTouchPadRect — periodically re-sends geometry
  │   └── FUN_140043a20 → FUN_140043880 → FUN_140043ab0
  └── Windows timers (SetTimer/KillTimer) for periodic updates
```

### PyxisHelperAPI.dll IPC bridge

- Named pipe path: `\\.\pipe\%s.yb9`
- `EiPostMcuCommand(param_1)`:
  - Takes a 128-byte struct (copied in 4×32-byte chunks)
  - Writes to pipe via `WriteFile` with header (type=6, length=0x208)
  - Total message: 0x233 bytes
- `EiPostJsonCommand(param_1)`:
  - Takes a JSON string
  - Wraps with AppName="YB9.TouchPad.exe"
  - Sends via named pipe
- Service dispatches:
  - `"TouchControl"` → `EnablePanelTouch` → sends 0x27 [panel, enable]
  - `"ScreenMode"` → screen mode change

### Service command dispatch (from decompilation)

```
JSON type "TouchControl":
  value < 4:
    EnablePanelTouch(panel=0, enable=value & 1)   → 0x27 [0, enable0]
    EnablePanelTouch(panel=1, enable=(value>>1) & 1) → 0x27 [1, enable1]
  value >= 4:
    Log "TouchControl parameter invalid"

EnablePanelTouch (FUN_14001a0b0):
  GetLocalTime → build timestamp
  Build packet: wire_len=2, type=0x27, payload=[panel, enable]
  Enqueue to write thread
```

### Registered sub-apps (service IPC)

| App | Executable |
|-----|-----------|
| Windows Manager | `YB9.WindowsManager.exe` |
| User Center | `YB9.UserCenter.exe` |
| Phantom KB | `YB9.PhantomKB.exe` |
| Touch Pad | `YB9.TouchPad.exe` |
| User Guide | `YB9.UserGuide.exe` |
| Rotation Manager | `YB9.RotationManager.exe` |
| Smart Launcher | `YB9.SmartLauncher.exe` |
| Air Gesture | `YB9.AirGesture.exe` |

---

## File reference

| File | Purpose |
|------|---------|
| `touch/test-windows-replica.py` | Faithful Windows replica test: CDC session + init + keepalive + activation + periodic re-send |
| `touch/test-ack-reply.py` | Tests 7 variants of 0x75 ack reply (all failed) |
| `touch/test-iface0-session.py` | Tests DTR-only on interface 0 (didn't prevent revert) |
| `touch/test-set-line-coding.py` | Tests SET_LINE_CODING baud rates (9600-38400 safe, 57600+ crash) |
| `touch/touchpad-keeper.py` | Event16 grab + periodic re-activation approach (rejected) |
| `touch/yb9-touchpad-daemon.py` | Old experimental daemon with periodic re-activation, state file IPC |
| `touch/test-touchpad-activate.py` | Main activation test script with multiple options |
| `touch/run-touchpad-test.sh` | Wrapper script that handles USB permissions, `cdc_acm` blocking |
| `touch/diag-mcu-responses.py` | MCU response reader diagnostic |
| `touch/diag-mcu-ack.py` | Test MCU acknowledgment handling |
| `windows/rd/yb9_usb.py` | Low-level USB bulk transfer library |
| `windows/rd/yb9-touchpad-mode.sh` | Simple shell wrapper for touchpad on/off |
| `windows/rd/glm-driver-spec.md` | Full Ghidra decompilation spec of YB9.Service.exe |
| `yoga-touch-driver.py` | Userspace HID touch driver (for dual-screen touch) |
| `check-kwin-input.sh` | KWin input device diagnostic |

---

## Key reverse-engineering findings (from Windows decompilation)

### Real touchpad mode toggle

- command type `0x25`, payload length 1
- `0x01` = touchpad mode on, `0x00` = touchpad mode off
- Recovered from `YB9.TouchPad.exe` → `TouchPadSetMcuTouchMode` → `YB9.PhantomKB.exe` → raw MCU command `0x25`

### Touchpad app commands

`YB9.TouchPad.exe` sends these MCU commands:

- `0x20 01 00` — sync flag
- `0x21 7e 01 00 00 00` — touchpad-on state sync
- `0x21 40 <u16 width> <u16 height>` — screen info bX/bY
- `0x21 41 <u16 width> <u16 height>` — screen info cX/cY
- `0x21 31 <oriB> <oriC> 00 00` — orientation sync
- `0xa3 <flag>` — post-orientation flag
- `0x31 <rect payload>` — touchpad geometry (short or long form)

### `0x31` serializer internals

The serializer function at `YB9.TouchPad.exe 0x140043ab0` packs rectangles as four LE16 values (left, top, right, bottom). Each rectangle is 8 bytes.

Argument order into the serializer:

- `rcx` = touchpad object / transport context
- `rdx` = `frameRect`
- `r8` = `LButton`
- `r9` = `RButton`
- stack `+0x20` = `touchableRect1`
- stack `+0x28` = `SrcId`
- stack `+0x30` = `DisableForMini`
- stack `+0x38` = `touchableRect2`
- stack `+0x40` = `noteRect`
- stack `+0x38` = `noteToolRect`

### Cached object layout

Fields in the touchpad object used by the resend wrapper:

- `0x140` = `SrcId`
- `0x144` = `frameRect`
- `0x154` = `LButton`
- `0x164` = `RButton`
- `0x174` = `touchableRect1`
- `0x184` = `touchableRect2`
- `0x194` = `DisableForMini`
- `0x198` = `noteRect`
- `0x1a8` = `noteToolRect`

### Windows touchpad UI controls

From `TouchPadMainWindow.xml`:

- `touchpad_main_box` — full touchpad window (frameRect)
- `caption_hbox` — 80px header (not in rect payload)
- `touch_area` — main finger tracking area (touchableRect1)
- `touchpad_btn_hbox` — 90px button row at bottom
- `btn_touchpad_left` / `btn_touchpad_right` — left/right click zones (LButton/RButton)

### Panel-size fallback constants

From helper at `0x140063d10`:

- width `0x708` = `1800`
- height `0xb40` = `2880`

### Multiple SrcId profiles

- Builder `0x14004c780`: `SrcId = [object+0x3dc]` (dynamic)
- Builder `0x1400635d0`: `SrcId = 2` (hardcoded, long-form with note rects)

### Key log strings from YB9.TouchPad.exe

```
TouchPad::MainWindow.cpp:: Connect MCU Server result = %u
TouchPad::MainWindow.cpp:: Init MCU Comm
TouchPad::MainWindow.cpp:: Connect MCU Server Success
TouchPad::MainWindow.cpp:: Init MCU Three Finger WakeUp Close/Open
TouchPad::MainWindow.cpp:: trigger MCU Three finger wakeup
SendRectToMCU command len = %u
SendRectToMCU frameRect:[%d, %d, %d, %d]
SendRectToMCU LButton:[%d, %d, %d, %d]
SendRectToMCU RButton:[%d, %d, %d, %d]
SendRectToMCU touchableRect1:[%d, %d, %d, %d]
SendRectToMCU touchableRect2:[%d, %d, %d, %d]
SendRectToMCU SrcId=%d
SendRectToMCU DisableForMini=%d
ResendTouchPadRect
TouchPadSetMcuTouchMode
PressCStartTouchpad
ShowTouchPad(false) - Desktop switch
[UAC] enable/disable touchpad1/2
state checked in timer, lock screen.
TouchPad::SendAppCommand Send Message %s
set screen info - %d * %d
EnableTouchPadPosSetting %d at version=[%d,%d,%d]
```

### Key log strings from YB9.Service.exe

```
OSKPD
MCUConnect::KeepConnectThread Start
MCUConnect::KeepConnectThread End
[MCUConnect] - Send Failed: %lu, MCU dropped.
[MCUConnect] - Send MCU Data. Id = %d
[MCUConnect] - PingMCU
TouchPad
PhantomKB
CPyxisServiceCenter::ProcessMappedCommand - TouchControl parameter invalid.
CPyxisServiceCenter::EnablePanelTouch - Panel:%d, Enable:%d Finished
```
