# USB Capture Brief — Yoga Book 9 INGENIC MCU (17ef:6161)

## Goal

Capture **exact byte-level USB traffic** from Windows during touchpad operation so the Linux driver can replicate it. The Linux driver has the OSKP protocol fully decompiled but the MCU reverts from touchpad to touchscreen mode after ~7 seconds. Something in the Windows communication keeps it alive that we haven't identified.

**The #1 question:** What exact USB traffic flows through interface 0 (CDC ACM serial) during steady-state touchpad operation, and does anything differ from what we already replicate on Linux?

---

## Target Device

| Property | Value |
|---|---|
| USB VID:PID | `17ef:6161` |
| Device String | `INGENIC Gadget Serial and keyboard` |
| Speed | High Speed (480 Mbps) |
| Interface 0 | CDC ACM — `usbser.sys` serial control channel (OSKP frames go here) |
| Interface 1 | Vendor Specific (0xFF) — bulk EP 0x81 IN, EP 0x01 OUT |
| Interface 2 | HID Keyboard |
| Interface 3 | HID Multitouch (touch + stylus) |
| Interface 4 | HID Vendor (firmware update) |
| Interface 5 | HID Consumer (media keys) |
| Interface 6 | HID Vendor (status/events) |

---

## Setup

### 1. Install Tools

**Wireshark + USBPcap:**
- Download Wireshark from https://www.wireshark.org/ (USBPcap is bundled in the installer since Wireshark 3.x)
- During install, ensure "USBPcap" component is selected
- After install, verify: open Wireshark → you should see "USBPcapN" interfaces in the interface list

**Alternative (command-line only):**
- Download USBPcap from https://desowin.org/usbpcap/
- Install, then capture with: `USBPcapCMD.exe -d \\.\USBPcap1 -o capture.pcap`

### 2. Verify the Device

Open Device Manager and confirm:
- Under "Ports (COM & LPT)" → there should be a COM port for the INGENIC device (e.g., `COM3`, `USB Serial Device (COM3)`, or `OSK Partner (COMx)`)
- Under "Human Interface Devices" → multiple HID devices for the INGENIC composite device
- Under "Universal Serial Bus devices" → the composite device itself

Run `usbview.exe` (from Windows SDK or standalone) to dump the full descriptor tree for VID 17ef PID 6161. Save the output.

### 3. Confirm YB9.Service.exe is Running

Open Task Manager or `tasklist` and verify:
- `YB9.Service.exe` is running
- `YB9.TouchPad.exe` may or may not be running (it launches on demand when touchpad mode activates)

Check the COM port:
```
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB\VID_17EF&PID_6161&MI_00" /s
```
Note the COM port number assigned to interface 0.

---

## Capture Scenarios

Do each capture as a **separate .pcapng file**. Name them as shown. Start capturing **before** the trigger action and stop **after** the specified duration.

### Capture 1: `boot-to-touchpad.pcapng` — Full Boot Sequence

**Purpose:** See everything from service startup through touchpad activation.

1. Start Wireshark capture on USBPcap interface
2. Stop `YB9.Service.exe` (taskkill /f /im YB9.Service.exe)
3. Wait 3 seconds
4. Start `YB9.Service.exe` (find it in the Lenovo install directory, typically `C:\Program Files\Lenovo\YogaBook9\` or similar)
5. Wait 10 seconds for service init
6. Open the touchpad (bottom screen → tap to show touchpad UI, or launch YB9.TouchPad.exe)
7. Wait 30 seconds of active touchpad
8. Stop capture

### Capture 2: `touchpad-steady-60s.pcapng` — Steady State

**Purpose:** See the exact keepalive cadence and any periodic traffic during normal operation.

1. Start capture
2. Touchpad should already be active (if not, activate it)
3. **Do not touch the screen** — just let it sit idle with touchpad visible
4. Wait 60 seconds
5. Stop capture

This is the **most important capture** — it reveals what keeps the touchpad alive past the 7-second mark.

### Capture 3: `touchpad-active-use-30s.pcapng` — Active Use

**Purpose:** See traffic during actual touchpad interaction.

1. Start capture
2. Touchpad is active
3. Use the touchpad normally for 30 seconds — move cursor, click, right-click, scroll, multi-finger gestures
4. Stop capture

### Capture 4: `touchpad-deactivate.pcapng` — Deactivation

**Purpose:** See what happens when touchpad mode is turned off.

1. Start capture with touchpad active
2. Close the touchpad UI (switch away from touchpad, or dismiss it)
3. Wait 10 seconds
4. Stop capture

### Capture 5: `cdc-raw-com-port.pcapng` — Serial Port Spy (Alternative/Supplementary)

If USBPcap captures are too noisy, try spying on the COM port directly:

1. Close YB9.Service.exe
2. Install a serial port monitor (e.g., Portmon from Sysinternals, or Free Serial Port Monitor)
3. Start the monitor on the COM port assigned to the INGENIC device
4. Start YB9.Service.exe
5. Activate touchpad
6. Let it run 30 seconds
7. Export the log

This shows only the CDC ACM data (interface 0 serial traffic) without USB framing overhead.

### Capture 6: `touchpad-reactivate.pcapng` — Reactivation After Idle

**Purpose:** See if the service re-sends activation commands periodically or reactively.

1. Start capture
2. Touchpad is active — let it sit idle for 15 seconds
3. Move finger on touchpad for 5 seconds
4. Let idle for another 15 seconds
5. Stop capture

---

## What to Note During Capture

For each capture, write down:
1. **Exact COM port number** assigned to the device
2. **Exact YB9.Service.exe file path** and version (right-click → Properties → Details)
3. **Whether bottom screen showed touchpad or normal touchscreen** during each phase
4. **Timestamps** of key events (service start, touchpad appeared, touchpad closed)

---

## Additional Dumps to Collect

### USB Descriptor Dump

Run `usbview.exe`, select the INGENIC device (VID 17ef, PID 6161), File → Save. Save as `usbview-descriptors.txt`.

Or from an admin command prompt:
```
powershell -Command "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like '*17ef*6161*' } | Format-List *"
```

### Registry Dump

```
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB\VID_17EF&PID_6161" /s > reg-ingenic-dump.txt
```

### Service Config

```
sc qc YB9Service
sc qdescription YB9Service
```
Or check Task Scheduler for the YB9.Service startup task.

### COM Port Settings

If you can catch the DCB (Device Control Block) settings:
```
mode COMx
```
where `COMx` is the port number. This shows baud rate, parity, stop bits.

### Windows Driver Files

From `C:\Windows\System32\drivers\` or the INF references:
- `usbser.sys` — the serial driver bound to interface 0
- Any `.inf` files referencing VID_17EF&PID_6161 (search in `C:\Windows\INF\`)

```
findstr /s "17ef" C:\Windows\INF\*.inf
findstr /s "6161" C:\Windows\INF\*.inf
```

---

## Expected OSKP Frame Format (for reference when analyzing)

All control traffic on interface 0 uses this framing:

```
Host → MCU (outgoing):
  Offset 0-3:  Magic "OSKP" (4 bytes: 0x4F 0x53 0x4B 0x50)
  Offset 4-5:  Wire length LE16 = payload_len + 1
  Offset 6:    Type byte (command ID)
  Offset 7+:   Payload

MCU → Host (incoming):
  Offset 0-3:  Header (likely "OSKP")
  Offset 4-5:  Payload length LE16
  Offset 6+:   Payload
```

### Known command types we already replicate:

| Type | Name | Payload | Notes |
|------|------|---------|-------|
| `0x25` | Mode toggle | `[0x00]` or `[0x01]` | 0x01 = touchpad ON |
| `0x26` | Keepalive | 7-byte timestamp | Sent every ~1500ms |
| `0x20` | Sync flag | `[0x01, 0x00]` | |
| `0x21` | Param (multiplexed) | subcmd + data | Subcmd 0x7e, 0x40, 0x41, 0x31, 0x80 |
| `0x27` | Panel touch enable | `[panel, enable]` | |
| `0x28` | Status pair | `[bool, state]` | |
| `0x31` | Geometry rects | 41 or 57 bytes | Touchpad layout rectangles |
| `0xa3` | Post-orientation | `[flag]` | |

### What we're specifically looking for:

1. **Any command type we don't know about** — if you see a type byte in the OSKP header that's not in the table above, that's a discovery
2. **The exact timing and cadence** of frames during steady state — are keepalives (0x26) truly every 1500ms? Is anything else sent periodically?
3. **Interface 1 bulk traffic** — do any bulk transfers happen on EP 0x81/0x01 during touchpad operation? We currently don't know what IF1 is for
4. **Any traffic from MCU → host** that we don't handle on Linux (incoming OSKP responses, especially non-gesture payloads)
5. **SET_LINE_CODING / SET_CONTROL_LINE_STATE** USB control transfers to interface 0 at init time — the exact baud rate and line state flags matter
6. **Whether anything changes in the traffic pattern** when the 7-second mark passes — is there a response from the MCU at that point?

---

## How to Transfer Results

Save all files into a single directory and zip:
```
usb-capture-results.zip
├── boot-to-touchpad.pcapng
├── touchpad-steady-60s.pcapng
├── touchpad-active-use-30s.pcapng
├── touchpad-deactivate.pcapng
├── cdc-raw-com-port.log          (if captured)
├── touchpad-reactivate.pcapng
├── usbview-descriptors.txt
├── reg-ingenic-dump.txt
├── com-port-settings.txt          (output of: mode COMx)
├── notes.txt                      (timestamps, COM port, observations)
```

Transfer the zip to the Linux machine (USB drive, network share, cloud).

---

## Quick-Start Checklist

- [ ] Install Wireshark with USBPcap
- [ ] Verify INGENIC device in Device Manager
- [ ] Find COM port number for interface 0
- [ ] Run `usbview.exe` → save descriptors
- [ ] Dump registry (`reg query`)
- [ ] Run `mode COMx` → save COM settings
- [ ] Capture 1: boot-to-touchpad
- [ ] Capture 2: touchpad-steady-60s (most important)
- [ ] Capture 3: touchpad-active-use-30s
- [ ] Capture 4: touchpad-deactivate
- [ ] Capture 6: touchpad-reactivate
- [ ] Package and transfer
