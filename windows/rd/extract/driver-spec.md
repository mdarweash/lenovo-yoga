# YB9 MCU Driver Spec

This is the current driver-oriented summary of the Windows MCU stack recovered from the copied Yoga Book 9 binaries.

## 1. Transport

- USB device: `17ef:6161`
- Runtime control interface: `USB\\VID_17EF&PID_6161&MI_00`
- Windows binding: `usbser.sys` via `OSKPartner.inf`
- Transport type: serial, not HID

`YB9.Service.exe` configures the handle with:

- `SetupComm(handle, 0x400, 0x400)`
- `GetCommState`
- `SetCommState`
- `PurgeComm(handle, 0x0f)`
- `SetCommTimeouts`
- `SetCommMask(handle, 0x81)`

The code path does not obviously override baud rate, parity, or byte size beyond basic DCB cleanup, so the practical assumption is "default usbser settings plus custom timeouts and event mask".

## 2. Service framing

The service uses an internal queued item format of:

- `u32 payload_len`
- `u8 type`
- `u8 payload[payload_len]`

The fixed internal buffer size is `0x208` bytes.

## 3. Outgoing wire format

The write thread builds the serial frame as:

- `u32 magic = 0x504b534f` which is ASCII `OSKP`
- `u16 wire_len = payload_len + 1`
- `u8 type`
- `u8 payload[payload_len]`

So the full outgoing frame is:

```text
4 bytes  "OSKP"
2 bytes  little-endian length = 1 + payload_len
1 byte   type
N bytes  payload
```

Total transmitted bytes = `payload_len + 7`.

## 4. Incoming wire format

The read thread parses records using:

- record length at `frame + 4`
- payload start at `frame + 6`
- next record offset += `6 + payload_len`

The code does not explicitly validate the first 4 bytes, but the most likely interpretation is that inbound records also start with the same 4-byte prefix and then use:

```text
4 bytes  inferred header, likely "OSKP"
2 bytes  little-endian payload length
N bytes  payload
```

This is still an inference, but it matches the transmit-side layout and the parser offsets.

## 5. Mandatory init and keepalive

The service-side MCU init function sends these raw items in order:

1. type `0x25`, payload length `1`, payload:

```text
00
```

2. type `0x31`, payload length `0x21`, payload:

```text
33 zero bytes
```

The periodic keepalive is type `0x26` with 7 payload bytes:

- `year_le16`
- `month`
- `day`
- `hour`
- `minute`
- `second`

Wire example:

```text
"OSKP" + u16(8) + 0x26 + [year_lo year_hi mon day hour min sec]
```

## 6. Known raw command types

### 6.1 `0x25`

Single-byte boolean-like payload.

Observed payloads:

- `00`
- `01`

Seen in:

- service init
- hide/show keyboard flows
- touch-mode related flows

Practical interpretation:

- mode toggle or touch-enable state used heavily by Phantom Keyboard and keyboard-hide logic

### 6.2 `0x26`

Timestamp ping / keepalive.

Payload length: `7`

### 6.3 `0x27`

Panel touch enable/disable.

Payload length: `2`

Payload:

- byte 0 = panel index
- byte 1 = enable flag

This comes directly from `CPyxisServiceCenter::EnablePanelTouch`.

### 6.4 `0x21`

Multiplexed parameter/update command. The first payload byte acts as a subcommand.

Observed forms:

- subcommand `0x31`, payload length `5`
  - payload layout:
    - byte 0 = `0x31`
    - byte 1 = orientation value B
    - byte 2 = orientation value C
    - byte 3 = `0x00`
    - byte 4 = `0x00`
  - log string near this path:
    - `"[SendScreenOriToMCU] - oriB=%d, oriC=%d"`

- subcommand `0x40`, payload length `5`
  - payload layout:
    - byte 0 = `0x40`
    - bytes 1..2 = `u16`
    - bytes 3..4 = `u16`
  - log string:
    - `"[SendScreenInfoToMCU] - bX=%d, bY=%d"`

- subcommand `0x41`, payload length `5`
  - payload layout:
    - byte 0 = `0x41`
    - bytes 1..2 = `u16`
    - bytes 3..4 = `u16`
  - log string:
    - `"[SendScreenInfoToMCU] - cX=%d, cY=%d"`

- subcommand `0x80`, payload length `5`
  - observed payload:
    - `80 01 01 00 xx`
  - seen in `InitOSKParamOnMCU`
  - last byte varies with mode/state

### 6.5 `0x28`

Two-byte status pair.

Observed layout:

- byte 0 = boolean derived from byte 1
- byte 1 = source state

Seen in screen/orientation sync flows.

Semantics are not fully named yet.

### 6.6 `0x29`

Single-byte layout/index selection.

Observed default value:

- `0x04`

Seen in keyboard layout / area selection paths.

### 6.7 `0x4a`

Payload length `0x15` (21 bytes).

Observed/logged as part of `RefreshKBArea`:

- log string:
  - `"[RefreshKBArea] - rt to MCU - bar=(%d,%d,%d,%d), screen=%d"`

Observed payload structure:

- byte 0 = `0x00`
- then a sequence of little-endian `u16` coordinates
- final bytes include duplicated `u16` values from the current keyboard-area state

Practical interpretation:

- keyboard bar / area geometry sync

### 6.8 `0x4b`

Payload length `8`.

Observed payload forms:

- `00 08 b8 0b 10 27 10 27`
- first byte can vary in the generic wrapper

Seen near init and display/keyboard setup flows.

Practical interpretation:

- timing or threshold parameter block

### 6.9 `0x5a`

Payload length `9`.

Log string:

- `SetLighterInfoToMCU: rtBar={%d,%d,%d,%d}, screenB=%d`

Observed payload layout:

- byte 0 = screen index
- bytes 1..8 = four `u16` values

### 6.10 `0x5b`

Payload length `2`.

Observed payload:

- byte 0 = `bl`
- byte 1 = `dil`

Seen in another screen/area sync path.

### 6.11 `0xa3`

Single-byte flag.

Observed payloads:

- `00`
- `01`

Seen immediately after orientation / mode synchronization.

Semantics are not yet fully named.

## 7. Incoming payloads

The service treats two incoming classes specially:

- if payload byte 0 is `0x9a`
  - internal event type becomes `0x0d`
  - gesture log uses:
    - `payload[5]` as `gesture_id`
    - `payload[6]` as `screen`

- otherwise
  - internal event type becomes `0x05`

Recovered log:

- `[MCU Gesture] - gesture id=%d, screen=%d`

## 8. Windows ScreenMode enum

`LibStatusManager.dll` provides the clearest recovered mode table used by the higher-level Yoga Book 9 app stack.

Confirmed exports tied to this area:

- `FnAutoAdjustScreenDPI`
- `FnCheckPCMode`
- `FnSetPCMode`
- `FnSetScreenModeByForce`
- `GetSystemInfoLibObject`

Recovered registry path:

- `HKLM\\SOFTWARE\\Lenovo\\YB9\\ScreenMode`

The registry-read helper in `LibStatusManager.dll` opens that key, reads the `ScreenMode` DWORD, and only accepts values `< 9`.

Recovered enum table from adjacent mode-name strings and symbolic `FF_*` labels:

- `0` = `Invalid` / `FF_INVALID`
- `1` = `PC mode` / `FF_PC`
- `2` = `Book Left` / `FF_BOOKLEFT`
- `3` = `Book Right` / `FF_BOOKRIGHT`
- `4` = `Stand` / `FF_STAND`
- `5` = `Tent` / `FF_TENT`
- `6` = `Tablet B Up` / `FF_TABLETB`
- `7` = `Tablet C Up` / `FF_TABLETC`
- `8` = `Flat` / `FF_FLAT`

What this gives the Linux side:

- a concrete 0..8 Windows mode enum instead of a loose list of mode names
- confirmation that `Flat` exists as a first-class Windows mode
- a likely source for the mode value later forwarded into raw MCU sync commands such as `0x21 0x80 ...`, though that raw-byte mapping is still not proven

## 9. Minimal Linux bring-up sequence

For a first working Linux implementation:

1. open the serial function on `MI_00`
2. apply basic serial setup equivalent to the Windows COMM path
3. send:
   - `0x25 [00]`
   - `0x31 [33 zero bytes]`
4. start periodic `0x26` ping
5. implement receive parsing with header skip + length at offset `+4`
6. decode gesture packets when payload byte 0 is `0x9a`
7. expose panel touch enable through `0x27`
8. add `0x21` subcommands for geometry/orientation sync if keyboard or screen-mode behavior depends on them

## 10. What is still optional or uncertain

- exact semantics of `0x28`, `0x29`, `0x4b`, and `0xa3`
- full meaning of the `0x21 0x80 ...` mode byte and how it maps onto the recovered Windows `ScreenMode` enum
- exact receive-side 4-byte header value, though `OSKP` is the strongest inference
- mapping of Windows `EiSetScreenMode` and `EiWriteITS` onto raw serial bytes

Those unknowns matter for feature parity, but not for a first Linux driver that can bring the MCU up safely and parse gestures.
