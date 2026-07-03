# YB9 MCU Reverse-Engineering Extract

This directory contains the smaller artifact set most relevant to the Lenovo Yoga Book 9 INGENIC MCU path described in `ingenic.md`.

## Main conclusions

- `YB9.Service.exe` is the runtime MCU service, but its live control channel is not HID. It discovers `USB\\VID_17EF&PID_6161&MI_00`, opens the device through the Windows serial stack (`usbser.sys`), and runs dedicated read/write threads on that handle.
- `PyxisHelperAPI.dll` is the app-facing helper layer. Its exports show the service API surface Lenovo apps use, including `EiPostMcuCommand`, `EiSetScreenMode`, and `EiWriteITS`.
- `LibStatusManager.dll` provides the clearest recovered Windows screen-mode enum. It reads `HKLM\\SOFTWARE\\Lenovo\\YB9\\ScreenMode` and uses a 9-state table: `Invalid`, `PC mode`, `Book Left`, `Book Right`, `Stand`, `Tent`, `Tablet B Up`, `Tablet C Up`, `Flat`.
- `FusionTouchFirmwareUpdate.dll` is a separate UMDF/HID firmware path for `HID\\VID_17EF&UP:FECD_U:0080`. It is useful for update transport, but it is not the normal runtime control loop.
- The Windows app stack already gives enough protocol detail to write an initial Linux driver or userspace daemon for transport, init, keepalive, touch enable, geometry sync, and gesture parsing.

## Most useful files

- `hashes.sha256`
  - Integrity hashes for the copied binaries and firmware blobs.
- `yb9-service.imports.txt`
  - Imports showing `SetupDi*`, serial I/O, file I/O, named pipes, and power/suspend notifications.
- `yb9-service.strings.txt`
  - Filtered strings showing the MCU lifecycle and command names.
- `yb9-service.utf16-strings.txt`
  - Unicode strings including `usb#vid_17ef&pid_6161&mi_00`, `LenovoPyxisUpdaterEvent`, and `ScreenMode`.
- `pyxishelper.exports.txt`
  - Exported helper functions exposed to other Lenovo components.
- `pyxishelper.strings.txt`
  - String confirmation for the helper API names and pipe/service usage.
- `fusiontouch.inf.txt`
  - INF metadata for the firmware update driver.
- `fusiontouch.imports-exports.txt`
  - Imports/exports confirming HID and CM device interface enumeration plus `FxDriverEntryUm`.
- `fusiontouch.strings.txt`
  - ASCII strings for CFU and firmware transport states.
- `fusiontouch.utf16-strings.txt`
  - Unicode config/status keys such as `CurrentFwVersion`, `OfferFwVersion`, and `FirmwareUpdateStatusRejectReason`.
- `lnv_oskprovider.offer.hex.txt`
  - First 64 bytes of the offer blob.
- `driver-spec.md`
  - Driver-oriented summary of the recovered serial transport and MCU protocol.

## Direct evidence

### Runtime transport

`YB9.Service.exe` imports and uses:

- `SetupComm`
- `GetCommState`
- `SetCommState`
- `PurgeComm`
- `SetCommTimeouts`
- `SetCommMask`
- `WaitCommEvent`
- `ClearCommError`
- `ReadFile`
- `WriteFile`

`OSKPartner.inf` in the copied driver store binds `USB\\VID_17EF&PID_6161&MI_00` to `usbser.sys`.

The service also contains the Unicode device path fragment:

- `usb#vid_17ef&pid_6161&mi_00`

This corrects the earlier HID assumption. The runtime MCU path is serial on interface `MI_00`.

### Service API surface

`yb9-service.strings.txt` shows:

- `MCUConnect::FindHidDevice`
- `Open HID device succ, start read`
- `Open HID device succ, start write`
- `PingMCU`
- `Send MCU Data. Id = %d`
- `CPyxisServiceCenter::PushData2Mcu`
- `EnablePanelTouch`
- `StopMCUArea`
- `SetScreenMode`
- `SetITS`
- `[MCU Gesture] - gesture id=%d, screen=%d`

The log strings still say "HID", but the disassembly shows the underlying handle is a serial `CreateFile` handle configured with the COMM APIs above.

### Pyxis helper API

`pyxishelper.exports.txt` shows these exported entry points:

- `EiPostMcuCommand`
- `EiSetScreenMode`
- `EiWriteITS`
- `EiPostJsonCommand`
- `EiTriggerEvent`
- `EiRunUpdater`

This shows Lenovo apps do not normally speak to the MCU directly. They call into `PyxisHelperAPI.dll`, which forwards raw MCU items or higher-level requests into the service stack.

### Firmware update package

`fusiontouch.inf.txt` shows:

- Match ID: `HID\\VID_17EF&UP:FECD_U:0080`
- `Protocol = 1`
- `NumberOfInputReports = 2`
- UMDF service binary: `%12%\\UMDF\\FusionTouchFirmwareUpdate.dll`
- Firmware blobs loaded from the driver store:
  - `lnv_oskprovider.offer.bin`
  - `lnv_oskprovider.payload.bin`

`fusiontouch.imports-exports.txt` and `fusiontouch.utf16-strings.txt` show:

- `HidP_GetCaps`
- `HidP_InitializeReportForID`
- `CM_Get_Device_Interface_ListW`
- `FxDriverEntryUm`
- `CurrentFwVersion`
- `OfferFwVersion`
- `FirmwareUpdateStatus`
- `FirmwareUpdateStatusRejectReason`

This package is specific to firmware transport and status tracking. It is useful for decoding the update path, but it is separate from the normal gesture/control loop.

## What matters for a Linux driver

Use `driver-spec.md` as the current protocol reference. The key recovered facts are:

- transport = serial on `MI_00`
- outgoing frame = `OSKP` + `u16(len)` + `u8 type` + payload
- startup sends type `0x25` then type `0x31`
- keepalive is type `0x26` with local timestamp payload
- panel control is type `0x27`
- Windows mode selection is backed by `ScreenMode` values `0..8` with concrete names from `LibStatusManager.dll`
- Lenovo apps also send raw items for screen geometry, orientation, keyboard area, and touch mode
- incoming gesture packets use payload byte `0x9a` and expose gesture id + screen index

## Remaining gaps

- The service does not validate the inbound 4-byte header in the parser, so the receive-side `OSKP` magic is an inference from the `+4/+6` offsets and the transmit format.
- `EiSetScreenMode` and `EiWriteITS` are partly registry-backed in the service-side code that was traced. The Windows mode enum is now known, but it is not yet mapped one-to-one onto raw serial command bytes.
- Some app-driven raw commands are recovered structurally but not semantically named with full confidence yet, especially `0x28`, `0x29`, `0x4b`, and `0xa3`.

Those gaps block full Windows-feature parity, but they do not block an initial Linux driver bring-up.
