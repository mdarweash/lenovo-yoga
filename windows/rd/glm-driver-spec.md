# INGENIC MCU (17ef:6161) — Lenovo Yoga Book 9 Dual-Screen
## Linux Driver Specification

> Reverse-engineered from Windows binaries using Ghidra 12.0 headless decompilation.
> Primary source: `YB9.Service.exe` (597 KB), `PyxisHelperAPI.dll`, `LibStatusManager.dll`.

---

## 1. Device Overview

| Property | Value |
|---|---|
| USB Vendor ID | `0x17ef` (Lenovo) |
| USB Product ID | `0x6161` |
| USB Device | `INGENIC Gadget Serial and keyboard` |
| USB Speed | High Speed (480 Mbps) |
| Firmware Version | 13.36 |
| Interfaces | 7 (composite device) |
| Physical Function | Touchscreen, stylus, keyboard, virtual touchpad for dual-screen laptop |

### USB Interface Map

| IF# | Class | Linux Driver | Windows Driver | Function |
|-----|-------|-------------|----------------|----------|
| 0 | CDC ACM (0x02) | `cdc_acm` (**causes crash**) | `usbser.sys` (OSK Partner) | **MCU serial control channel** |
| 1 | Vendor (0xFF) | none | none | Unknown bulk |
| 2 | HID Boot/Keyboard (0x03) | `usbhid` | HID class | Keyboard |
| 3 | HID Multitouch (0x03) | `hid-multitouch` | Wacom router filter | Touch + Stylus (5 collections) |
| 4 | HID Vendor (0x03) | `hid-generic` | FusionTouchFirmwareUpdate.dll | Firmware update (UP:FECD/0x0080) |
| 5 | HID Consumer (0x03) | `hid-generic` | HID class | Media keys |
| 6 | HID Vendor (0x03) | `hid-generic` | HID class | Vendor status/events |

---

## 2. Transport — Serial over Interface 0

**Critical finding: MCU control uses CDC ACM serial on interface 0, NOT raw HID.**

`YB9.Service.exe` opens `USB\\VID_17EF&PID_6161&MI_00` as a serial port:

1. `SetupDiGetClassDevs` → enumerate HID devices, match device path containing `usb#vid_17ef&pid_6161&mi_00`
2. `CreateFileW(path, GENERIC_READ|GENERIC_WRITE, 0, NULL, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, NULL)`
3. `SetupComm(handle, 0x400, 0x400)` — 1 KB RX/TX buffers
4. `GetCommState` → `SetCommState` — default DCB with `StopBits=0`, parity cleared
5. `PurgeComm(handle, 0x0f)` — clear all buffers
6. `SetCommTimeouts` — read: infinite interval, 0 multiplier, 0 constant; write: 500ms multiplier, 5000ms constant
7. `SetCommMask(handle, 0x81)` — EV_RXCHAR flag
8. Create **read thread** (`FUN_140007430`) and **write thread** (`FUN_140007c20`)
9. **KeepConnectThread** (`FUN_140006eb0`) starts — periodic ping

---

## 3. Wire Protocol

### 3.1 Outgoing Frame Format

```
Offset  Size    Field
0       4       Magic: 0x504B534F ("OSKP" ASCII, little-endian)
4       2       Wire length (LE16) = payload_len + 1
6       1       Type byte (command ID)
7       N       Payload (payload_len bytes)

Total transmitted = payload_len + 7 bytes
```

After each write: `FlushFileBuffers(handle)`.

The write thread dequeues from an internal queue. Each queued item:
- `u32 payload_len`
- `u8 type`
- `u8 payload[payload_len]`

Internal buffer size: `0x208` (520) bytes.

### 3.2 Incoming Frame Format

The read thread uses `WaitCommEvent` + `ReadFile` loop. Records are parsed as:

```
Offset  Size    Field
0       4       Header (inferred "OSKP")
4       2       Payload length (LE16)
6       N       Payload

Next record offset += 6 + payload_len
```

Multiple records may arrive in a single `ReadFile` call. The read thread iterates through them sequentially.

### 3.3 Packet Diagram

```
TX (host → MCU):
┌──────────┬──────────┬──────┬─────────────┐
│  OSKP    │ len+1    │ type │  payload     │
│ 4 bytes  │ 2 bytes  │ 1 B  │  N bytes     │
└──────────┴──────────┴──────┴─────────────┘

RX (MCU → host):
┌──────────┬──────────┬─────────────┐
│  OSKP*   │ len      │  payload     │
│ 4 bytes  │ 2 bytes  │  N bytes     │
└──────────┴──────────┴─────────────┘
```

---

## 4. Command Types (Host → MCU)

### 4.1 `0x25` — Mode Toggle / Touch Enable

| Field | Value |
|-------|-------|
| Type | `0x25` |
| Payload length | 1 |
| Payload | `0x00` or `0x01` (boolean) |

Used in: service init, hide/show keyboard, touch-mode flows.

### 4.2 `0x26` — PingMCU / Keepalive

| Field | Value |
|-------|-------|
| Type | `0x26` |
| Payload length | 7 |
| Payload | `year_lo year_hi month day hour minute second` |

Sent periodically by `KeepConnectThread`. Wire example:
```
"OSKP" + u16(8) + 0x26 + [year_lo year_hi mon day hr min sec]
```

### 4.3 `0x27` — Panel Touch Enable/Disable

| Field | Value |
|-------|-------|
| Type | `0x27` |
| Payload length | 2 |
| Payload byte 0 | Panel index (0=bottom, 1=top) |
| Payload byte 1 | Enable flag (0=disable, 1=enable) |

Source: `CPyxisServiceCenter::EnablePanelTouch`.

### 4.4 `0x21` — Multiplexed Parameter Command

First payload byte is a **subcommand**:

| Subcmd | Len | Layout | Log string |
|--------|-----|--------|------------|
| `0x31` | 5 | `[0x31, oriB, oriC, 0x00, 0x00]` | `[SendScreenOriToMCU] - oriB=%d, oriC=%d` |
| `0x40` | 5 | `[0x40, u16 bX, u16 bY]` | `[SendScreenInfoToMCU] - bX=%d, bY=%d` |
| `0x41` | 5 | `[0x41, u16 cX, u16 cY]` | `[SendScreenInfoToMCU] - cX=%d, cY=%d` |
| `0x80` | 5 | `[0x80, 0x01, 0x01, 0x00, mode]` | `InitOSKParamOnMCU` |

### 4.5 `0x28` — Status Pair

| Field | Value |
|-------|-------|
| Payload length | 2 |
| Byte 0 | Boolean derived from byte 1 |
| Byte 1 | Source state |

Seen in screen/orientation sync.

### 4.6 `0x29` — Layout Index

| Field | Value |
|-------|-------|
| Payload length | 1 |
| Default value | `0x04` |

Keyboard layout / area selection.

### 4.7 `0x4a` — Keyboard Bar Geometry

| Field | Value |
|-------|-------|
| Payload length | 21 (`0x15`) |
| Byte 0 | `0x00` |
| Bytes 1+ | Sequence of LE16 coordinates |
| Log | `[RefreshKBArea] - rt to MCU - bar=(%d,%d,%d,%d), screen=%d` |

### 4.8 `0x4b` — Timing/Threshold Block

| Field | Value |
|-------|-------|
| Payload length | 8 |
| Example | `00 08 b8 0b 10 27 10 27` |

Init and display/keyboard setup.

### 4.9 `0x5a` — Lighter Bar Info

| Field | Value |
|-------|-------|
| Payload length | 9 |
| Byte 0 | Screen index |
| Bytes 1-8 | Four LE16 values |
| Log | `SetLighterInfoToMCU: rtBar={%d,%d,%d,%d}, screenB=%d` |

### 4.10 `0x5b` — Screen/Area Sync

| Field | Value |
|-------|-------|
| Payload length | 2 |
| Byte 0 | `bl` |
| Byte 1 | `dil` |

### 4.11 `0xa3` — Post-Orientation Flag

| Field | Value |
|-------|-------|
| Payload length | 1 |
| Values | `0x00` or `0x01` |

Sent immediately after orientation/mode sync.

---

## 5. Incoming Payloads (MCU → Host)

The read thread classifies incoming payloads:

### 5.1 Gesture Event

If `payload[0] == 0x9a`:
- Internal event type: `0x0d`
- `payload[5]` = gesture ID
- `payload[6]` = screen number
- Log: `[MCU Gesture] - gesture id=%d, screen=%d`

### 5.2 Generic Event

Otherwise:
- Internal event type: `0x05`
- Dispatched to registered sub-apps via named pipe IPC

---

## 6. Screen Modes

From `LibStatusManager.dll` registry read at `HKLM\SOFTWARE\Lenovo\YB9\ScreenMode` (DWORD, valid range 0-8):

| Value | Name | Label | Description |
|-------|------|-------|-------------|
| 0 | `FF_INVALID` | Invalid | — |
| 1 | `FF_PC` | PC mode | Standard laptop (clamshell) |
| 2 | `FF_BOOKLEFT` | Book Left | Book mode, hinge left |
| 3 | `FF_BOOKRIGHT` | Book Right | Book mode, hinge right |
| 4 | `FF_STAND` | Stand | Stand/tent presentation |
| 5 | `FF_TENT` | Tent | Full tent |
| 6 | `FF_TABLETB` | Tablet B Up | Bottom screen up (tablet) |
| 7 | `FF_TABLETC` | Tablet C Up | Top screen up (tablet) |
| 8 | `FF_FLAT` | Flat | Both screens flat open |

---

## 7. Initialization Sequence

From Ghidra decompilation of `FUN_1400066c0` (connect) and `FUN_140006eb0` (keepalive loop):

```
1. FindHidDevice()
   - SetupDiGetClassDevs(GUID_DEVINTERFACE_HID)
   - Enumerate interfaces, match path containing "usb#vid_17ef&pid_6161&mi_00"
   - Get device path via SetupDiGetDeviceInterfaceDetailW

2. CreateFileW(path, GENERIC_READ|GENERIC_WRITE, OPEN_EXISTING, FILE_FLAG_OVERLAPPED)

3. SetupComm(handle, 0x400, 0x400)     // 1KB buffers
   SetCommState(handle, DCB)             // default 8N1, no parity
   PurgeComm(handle, 0x0f)              // clear all buffers
   SetCommTimeouts(handle, timeouts)     // read=infinite, write=500/5000ms
   SetCommMask(handle, 0x81)            // EV_RXCHAR

4. Create overlapped events for read/write

5. Start ReadThread  (FUN_140007430) — WaitCommEvent + ReadFile loop
   Start WriteThread (FUN_140007c20) — dequeue + WriteFile + FlushFileBuffers

6. Send init commands:
   a. type=0x25, payload=[0x00]
   b. type=0x31, payload=[33 zero bytes]

7. Start KeepConnectThread (FUN_140006eb0):
   - Sleep(1500ms) after successful connect
   - Build timestamp packet (7 bytes)
   - Send PingMCU (type=0x26) with timestamp
   - WaitForSingleObject(stop_event, INFINITE)
   - Loop until stopped
```

---

## 8. Error Handling & Reconnection

From `FUN_140007c20` (write thread) decompilation:

- On `WriteFile` failure with `ERROR_IO_PENDING`: wait via `GetOverlappedResult`
- On write failure (up to 3 retries):
  - Log: `Send Failed: %lu for %d times.`
  - After 3rd failure: Log `Send Failed: %lu, MCU dropped.`
  - Call reconnect function (`FUN_1400070e0`)
  - Set reconnect event
- Read thread failure similarly triggers reconnect
- Reconnect: close handle, clean threads, re-enter connection loop

---

## 9. Windows IPC Architecture

`YB9.Service.exe` acts as a central MCU gateway. Other apps communicate via named pipes:

### Named Pipe
- Path: `\\.\pipe\YB9Service`
- Protocol: JSON over pipe

### PyxisHelperAPI.dll Exports (IPC Bridge)

| Export | Purpose |
|--------|---------|
| `EiPostMcuCommand` | Send raw MCU command |
| `EiSetScreenMode` | Set screen mode via RotationManager |
| `EiWriteITS` | Set ITS (Interactive Touch Screen) mode |
| `EiEnableWinOsk` | Enable/disable on-screen keyboard |
| `EiPostJsonCommand` | Send JSON command to service |
| `EiTriggerEvent` | Trigger service event |
| `EiRunAppCommand` | Launch sub-app |
| `EiKillAppCommand` | Kill sub-app |
| `EiAddBlackListCommand` | Add app to blacklist |
| `EiRemoveBlackListCommand` | Remove app from blacklist |

### Registered Sub-Apps

From `FUN_140001030` (app registration table):

| App | Name |
|-----|------|
| Windows Manager | `YB9.WindowsManager.exe` |
| User Center | `YB9.UserCenter.exe` |
| Phantom KB | `YB9.PhantomKB.exe` |
| Touch Pad | `YB9.TouchPad.exe` |
| User Guide | `YB9.UserGuide.exe` |
| Rotation Manager | `YB9.RotationManager.exe` |
| Smart Launcher | `YB9.SmartLauncher.exe` |
| Air Gesture | `YB9.AirGesture.exe` |

### JSON Command Format

Commands from sub-apps arrive as JSON, parsed with nlohmann::json:
```json
{
  "AppName": "RotationManager",
  "type": "ScreenMode",
  "value": "1"
}
```

Dispatch: `CPyxisServiceCenter::ParseJsonCommand` → `CPyxisServiceCenter::ProcessMappedCommand`

---

## 10. Firmware Update Protocol

From `FusionTouchFirmwareUpdate.inf` and DLL analysis:

| Property | Value |
|----------|-------|
| Target | HID usage page `0xFECD`, usage `0x0080` (interface 4) |
| Protocol | Microsoft Component Firmware Update (CFU) over HID |
| Driver type | UMDF (user-mode) |
| Offer file | `lnv_oskprovider.offer.bin` (16 bytes) |
| Payload file | `lnv_oskprovider.payload.bin` (775,706 bytes) |
| Payload magic | `4SCBT` at offset 4 |

CFU flow: Prepare → Write → Complete → Verify. Rejects: CRC error, signature error, version mismatch, swap pending, invalid address.

---

## 11. Linux Driver Implementation

### 11.1 Why `cdc_acm` Crashes the MCU

The Linux `cdc_acm` driver probes interface 0 with USB control transfers (`SET_LINE_CODING`, `SET_CONTROL_LINE_STATE`) that the INGENIC MCU firmware cannot handle. This causes an MCU crash, breaking all touch/stylus/keyboard function.

**Current workaround:** `echo "17ef 6161" > /sys/bus/usb/drivers/cdc_acm/remove_id`

### 11.2 Recommended Approach: Userspace Daemon

A Python/C daemon (`yb9-mcu-daemon`) that:

1. **Claims interface 0** — prevent `cdc_acm` from binding via `remove_id` or udev rule
2. **Opens `/dev/ttyACM*`** directly with serial settings:
   - 8N1, no parity, default baud
   - Read timeout: blocking
   - Write timeout: 5 seconds
3. **Sends init sequence** on startup:
   ```
   TX: OSKP + u16(2) + 0x25 + [0x00]          // init toggle
   TX: OSKP + u16(0x22) + 0x31 + [33 zeros]   // init config
   ```
4. **Starts keepalive thread** — send type `0x26` with timestamp every ~1.5s
5. **Starts read thread** — parse incoming frames, handle:
   - `0x9a` prefix → gesture event (expose via D-Bus or uinput)
   - Other → generic event
6. **Exposes D-Bus/Unix socket API** for:
   - `EnablePanelTouch(panel, enable)` → type `0x27`
   - `SetScreenMode(mode)` → type `0x21` subcommand `0x80`
   - `SetITS(mode)` → type `0x21` subcommand `0x31`
   - Other commands as needed
7. **Handles reconnection** on read/write failure (same as Windows: close, reconnect loop)

### 11.3 Alternative: Kernel `hid-ingenic` Module

A kernel HID driver that:
- Binds to interface 0 as vendor-specific (prevents `cdc_acm`)
- Implements the OSKP serial protocol in kernel
- Exposes sysfs attributes for mode/touch control
- Handles vendor HID reports on interfaces 4 and 6
- More robust but harder to develop/debug

### 11.4 udev Rule

```
# Prevent cdc_acm from claiming INGENIC MCU
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="17ef", ATTR{idProduct}=="6161", ATTR{bInterfaceNumber}=="00", RUN+="/bin/sh -c 'echo 17ef 6161 > /sys/bus/usb/drivers/cdc_acm/remove_id'"
```

---

## 12. Key Files Analyzed

| File | Size | Source | Purpose |
|------|------|--------|---------|
| `YB9.Service.exe` | 597K | Ghidra decompilation | Main MCU service, serial protocol |
| `PyxisHelperAPI.dll` | 202K | strings + exports | IPC bridge (named pipe JSON) |
| `LibStatusManager.dll` | 870K | strings + exports | Screen mode enum, rotation, sensors |
| `FusionTouchFirmwareUpdate.dll` | 233K | INF + strings | CFU firmware update (interface 4) |
| `OSKPartner.inf` | 4.3K | Direct read | Serial port driver binding |
| `WacRouterFilterISD.inf` | — | Direct read | Wacom touch filter (interface 3) |
| `lenovoDriverBus.inf` | — | Direct read | Virtual bus for IPC |

---

## 13. Open Questions

| Item | Status |
|------|--------|
| Exact semantics of `0x28`, `0x29`, `0x4b`, `0xa3` | Unknown — need USB capture or more decompilation |
| `0x21 0x80` mode byte mapping to ScreenMode enum | Inferred but not confirmed |
| Receive-side 4-byte header value | Inferred as `OSKP`, not confirmed |
| Baud rate | Windows uses default usbser (likely 115200 or auto) |
| How `EiSetScreenMode`/`EiWriteITS` map to raw bytes | Routes through IPC → service → serial |
| Gesture ID values and meanings | Need USB capture or more decompilation |
| Interface 1 (Vendor bulk) purpose | Completely unknown |

---

## 14. Minimal Linux Bring-Up Sequence

For a first working driver:

1. Block `cdc_acm` from interface 0 (`remove_id` or udev)
2. Open `/dev/ttyACM*` with 8N1 serial settings
3. Send init: `0x25 [00]` then `0x31 [33 zeros]`
4. Start periodic `0x26` ping with timestamp
5. Parse incoming frames (header + length at +4)
6. Decode gesture packets when payload byte 0 is `0x9a`
7. Expose panel touch via `0x27`
8. Add `0x21` subcommands for geometry/orientation if needed
