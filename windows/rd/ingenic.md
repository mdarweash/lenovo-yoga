# INGENIC MCU (17ef:6161) — Lenovo Yoga Book 9 14
## Reverse Engineering Notes

> for building a Linux driver

---

## 1. Device Overview

| Property | Value |
|---|---|
| **USB Vendor ID** | `0x17ef` (Lenovo) |
| **USB Product ID** | `0x6161` |
| **USB Device** | `INGENIC Gadget Serial and keyboard` |
| **USB Speed** | High Speed (480 Mbps) |
| **USB Version** | 2.00 |
| **Firmware Version** | 13.36 |
| **Serial** | `ingenic` |
| **Interfaces** | 7 (composite device) |
| **MaxPower** | 200mA |

### Physical Function

This internal USB device provides touchscreen, stylus, keyboard, and virtual touchpad for the Lenovo Yoga Book 9 dual-screen laptop. The top screen is physically mounted 180° rotated.

 The MCU handles mode-switching, gesture recognition, and firmware updates via HID.

 |

---

## 2. USB Interface Map

| IF# | Class | EP In | EP Out | Linux Driver | Windows Driver | Function |
|-----|------|-------|--------|---------------|----------------|----------|
| 0 | CDC ACM (0x02) | 0x82 (INT) | - | [none] (blocked) | `usbser.sys` (Lenovo OSK Partner) | Serial / firmware debug |
| 1 | Vendor (0xFF) | 0x01 (BULK) | 0x81 (BULK) | [none] | [none] | Main control channel |
| 2 | HID Boot/Keyboard (0x03) | 0x02 (INT) | 0x83 (INT) | `usbhid` | HID class driver | Keyboard input |
| 3 | HID Multitouch (0x03) | 0x03 (INT) | 0x84 (INT) | `usbhid` → `hid-multitouch` | Wacom driver | Touch + Stylus (all 5 devices) |
| 4 | HID Vendor (0x03) | 0x04 (INT) | 0x85 (INT) | `usbhid` | HID class driver | Vendor control / config |
| 5 | HID Consumer (0x03) | 0x05 (INT) | 0x86 (INT) | `usbhid` | HID class driver | Media keys / consumer controls |
| 6 | HID Vendor (0x03) | 0x06 (INT) | 0x87 (INT) | `usbhid` | HID class driver | Vendor status / events |

---

## 3. HID Report Descriptors (from Linux)

> Parsed from `/sys/bus/usb/devices/3-6/*/report_descriptor`

### Interface 2 — Keyboard (report ID 0x20)
Standard keyboard with keys E0-E7, consumer controls (media keys), and a vendor feature report (ID 0x54, 1 byte, range 0x00-0xFF).

```
Usage Page 0x0C (Consumer), Report IDs 0x41-0x46
Consumer controls (play/pause, scan next track, etc.)
```

Usage Page 0xFEC0 (Vendor), Report IDs 0x01, 0x02, 0x30, 0x40, 0x42
- **Report ID 0x01**: Input 60 bytes, Output 16 bytes
- **Report ID 0x02**: Input 16 bytes, Output 16 bytes
- **Report ID 0x30**: Feature report, 7 bytes
- **Report ID 0x40**: Feature report, 127 bytes (large config block)
- **Report ID 0x42**: Input report, 127 bytes (large status block)

```

Usage Page 0xFF00 (Vendor), Report IDs 0x60, 0x61
- **Report ID 0x60**: Vendor-defined (20 bytes in, 10 bytes out)
- **Report ID 0x61**: Vendor-defined (large block, up to 100 bytes)

```

The 5 interface devices on interface 3:
- `INGENIC ... Touchscreen Top` — top screen touch
- `INGENIC ... Touchscreen Bottom` — bottom screen touch
- `INGENIC ... Stylus Top` — top screen stylus
- `INGENIC ... Stylus Bottom` — bottom screen stylus
- `INGENIC ... Emulated Touchpad` — virtual touchpad (bottom screen)

```

---

## 4. Windows Driver Stack
> What Lenovo installs on Windows to manage this device.
> Source: Windows partition at `/tmp/windows/`

### Core Drivers

| File | Version | Purpose |
|---|---|---|
| `usbser.sys` | 6.1.7601.24494 | Serial port for interface 0 (OSK Partner, v1.1.0.88) |
| `WacHIDRouterISDF.sys` | 8.0.2.19 | Wacom HID router filter (finger), upper filter on HID class |
| `WacHIDRouterISDU.sys` | 8.0.2.19 | Wacom HID router filter (universal), upper filter on HID class |
| `WacRouterFilterISD.sys` | 8.0.2.19 | Wacom router filter |
| `wacompen.sys` | - | Wacom pen driver |
| `lenovoDriverBus.sys` | - | Lenovo driver bus (IPC between drivers and apps) |

### Firmware Update Driver (UMDF)

| File | Version | Purpose |
|---|---|---|
| `FusionTouchFirmwareUpdate.dll` | 1.3.36.66 | UMDF user-mode driver for firmware updates via HID |
| `lnv_oskprovider.offer.bin` | 16 bytes | Firmware offer metadata |
| `lnv_oskprovider.payload.bin` | 758,706 bytes | Firmware payload binary |

The firmware update driver:
- Is a UMDF (User-Mode Driver Framework) driver
- Matches `HID\VID_17EF&UP:FECD_U:0080` (usage page 0xFECD, usage 0x0080)
- This is interface 4's vendor HID page!
- Uses `HidP_*` APIs to send feature reports
- Manages firmware offer/payload protocol

- Source path: `D:\WorkSpace\GenX\Release\1.3.36\fusiontouchfw\ToolsProj\CFU_FusionTouch\`

### Yoga Book 9 Application Suite
Located at `C:\Program Files\Lenovo\YB9App\`:

| Component | Purpose |
|---|---|
| `YB9.Service.exe` | Main service — communicates with MCU via HID |
| `YB9.TouchPad.exe` | Virtual touchpad UI on bottom screen |
| `YB9.PhantomKB.exe` | On-screen keyboard for bottom screen |
| `RotationManager/` | Display rotation management |
| `AirGesture/` | Air gesture recognition |
| `SmartNote/` | Note-taking on bottom screen |
| `SmartLauncher/` | App launcher |
| `WindowsManager/` | Window management across dual screens |
| `UserCenter/` | Settings UI |

---

## 5. MCU Communication Protocol (Windows)
> Extracted from strings in `YB9.Service.exe`

### Connection
The YB9.Service.exe opens a HID device and runs read/write threads:
```
[MCUConnect] - Start connect to MCU
[MCUConnect] - Connect to MCU
[MCUConnect] - Open HID device succ, start read
[MCUConnect] - Open HID device succ, start write
```
The service finds the INGENIC HID device by enumerating HID devices (SetupDiGetClassDevs).
It maintains a keep-alive connection with ping:
```
[MCUConnect] - PingMCU
[MCUConnect] - KeepConnectThread Start
```
### Commands
| Function | Log String |
|---|---|
| Send data to MCU | `Send MCU Data. Id = %d` |
| Push data to MCU | `CPyxisServiceCenter::PushData2Mcu: MCU is not connected yet` |
| Enable/disable touch on panel | `CPyxisServiceCenter::EnablePanelTouch - Panel:%d, Enable:%d Finished` |
| Stop MCU area | `CPyxisServiceCenter::StopMCUArea - MCU is not connected yet` |
| Set screen mode | `SetScreenMode mode: %u, ret = %u` |
| Set ITS mode | `SetITS mode: %u, ret = %u` |
| MCU reset | `Datasize = 0, EMAPPID_MCU_RESET` |
| MCU stop | `Datasize = 0, EMAPPID_MCU_STOP` |
| Gesture received | `[MCU Gesture] - gesture id=%d, screen=%d` |
| Fn key broadcast | `BroadcastFnKeyMsg failed, Appmgr NOT READY.` |

### Message Structure
Commands appear to use an integer App ID system:
- `EMAPPID_MCU_RESET` — reset the MCU
- `EMAPPID_MCU_STOP` — stop the MCU
- Each command has an ID (`Send MCU Data. Id = %d`)

### Touch Panel Control
- `EnablePanelTouch(Panel, Enable)` — enable/disable touch on a specific panel (0=bottom, 1=top)
- `StopMCUArea()` — stop touch on a specific screen area
- `SetScreenMode(mode)` — set the screen display mode
- `SetITS(mode)` — set ITS (Interactive Touch Screen?) mode

### Gesture Handling
- `[MCU Gesture] - gesture id=%d, screen=%d` — the MCU reports gestures with an ID and screen number
- The 4-finger press likely generates a gesture event that Windows handles through the YB9 service

---

## 6. Linux vs Windows — Key Differences

> Why the INGENIC MCU crashes on Linux but not on Windows.

| Aspect | Linux | Windows |
|---|---|---|
| Interface 0 (CDC ACM) | `cdc_acm` probes and sends control transfers → **crashes MCU** | `usbser.sys` handles properly (Lenovo OSK Partner driver) |
| Interface 1 (Vendor bulk) | No driver (`[none]`) | Unknown (likely Lenovo vendor driver via `lenovoDriverBus.sys`) |
| Interface 3 (Multitouch) | `hid-multitouch` sends generic feature report queries | Wacom router filter intercepts and properly handles reports |
| Interface 4 (Vendor HID) | `hid-generic` — no special handling | `FusionTouchFirmwareUpdate.dll` handles firmware protocol |
| Interface 5 (Consumer) | `hid-generic` — no special handling | Wacom driver handles media keys |
| Interface 6 (Vendor) | `hid-generic` — no special handling | Likely Lenovo vendor driver |
| MCU keep-alive | None — MCU gets no ping | `YB9.Service.exe` sends periodic `PingMCU` |
| Touch panel enable | Always on | `EnablePanelTouch(Panel, Enable)` — explicit control |
| Gesture handling | Raw HID events, unhandled vendor reports | `YB9.Service.exe` handles `[MCU Gesture]` events |
| Screen mode | KWin handles | `SetScreenMode` / `SetITS` sent to MCU |

### Crash Triggers
1. **`cdc_acm` probe** (confirmed) — sends USB control transfers that crash the MCU. Mitigated by `remove_id`.
2. **Unhandled vendor HID reports** — when the MCU sends a vendor report (e.g., gesture event on 4-finger press) and gets no proper response, it may crash.
3. **Missing keep-alive ping** — Windows sends periodic `PingMCU`; without it, the MCU may timeout and reset.
4. **Missing `EnablePanelTouch`** — Windows explicitly enables touch panels; without this initialization, the MCU may be in an undefined state.

---

## 7. What Still Needed
> To build a proper Linux driver.
> - **USB traffic capture** from Windows (using USBPcap + Wireshark) showing:
  - Initialization sequence at boot
  - Keep-alive ping packets
  - `EnablePanelTouch` commands
  - `SetScreenMode` commands
  - 4-finger gesture event and response
  - What HID report IDs are used for each command
> - **Reverse engineering `YB9.Service.exe`** to understand:
  - The exact HID report format for `PushData2Mcu`
  - The `PingMCU` packet format
  - The `EnablePanelTouch` command format
  - The `SetScreenMode` / `SetITS` command formats
  - The gesture ID mapping
> - **Testing** on Linux:
  - Write a minimal HID user-space driver that sends the initialization sequence
  - Implement keep-alive pings
  - Handle vendor reports from the MCU
  - Test stability with and without the driver

---

## 8. Known Issues on Linux
> Current state as of this writing.
> - **Fixed**: Touch/stylus orientation for top screen (via KWin D-Bus `orientationDBus=8`)
- **Fixed**: `cdc_acm` probe blocked (reduces crash frequency significantly)
- **Unfixed**: 4-finger press still crashes MCU (no Linux driver to handle vendor reports)
- **Unfixed**: No keep-alive ping (MCU may timeout after extended periods)
- **Unfixed**: No explicit panel touch enable (MCU may be in undefined state)
- **Unfixed**: No screen mode setting (dual-screen mode not communicated to MCU)
- **Unfixed**: Vendor HID reports (interfaces 4, 6) go unhandled
