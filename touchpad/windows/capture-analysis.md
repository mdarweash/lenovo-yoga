# USB Capture Analysis — Yoga Book 9 INGENIC MCU (17ef:6161)

**Date:** 2026-05-16
**Device:** INGENIC Gadget Serial and keyboard (17ef:6161)
**YB9.Service.exe:** v3.3.4.1, PID 5932
**YB9.TouchPad.exe:** PID 4860

---

## USB Device Topology (as seen in captures)

| USB Address | Interface | Driver/Class | EP | Notes |
|---|---|---|---|---|
| **1.1.x** | INGENIC Interface 0 (MI_00) | CDC ACM / usbser.sys (Lenovo OSK Partner) | 0x84 IN (bulk), 0x00 CTRL | OSKP serial channel |
| **1.2.x** | INGENIC Interface ? | HID or similar | EP 0x80 | Enumerated at boot |
| **1.5.x** | INGENIC Interface 1 (MI_01) | Vendor Specific (0xFF) | EP 0x01 OUT, 0x81 IN (bulk) | **OSKP frames go here** — host sends sync on EP 0x01 |
| **1.4.x** | **Bluetooth adapter** (separate device!) | Wireless Controller (0xE0) | EP 0x81, 0x82, 0x00 | **NOT INGENIC** — Wireshark decodes as HCI/Bluetooth |

### Critical mapping correction:
- **EP 0x01 OUT on address 1.5.1** = INGENIC Interface 1 bulk OUT → OSKP frames sent HERE
- **EP 0x84 IN on address 1.1.4** = INGENIC Interface 0 bulk IN → MCU touch data (281-byte frames)
- **EP 0x81 IN on address 1.4.1** = Bluetooth HCI event endpoint (NOT INGENIC)
- **EP 0x82 IN on address 1.4.2** = Bluetooth ACL data endpoint (NOT INGENIC)

---

## Capture 01: `01-idle-10s.pcapng` — Idle Touchpad (keyboard attached)

**Action:** Touchpad active, keyboard attached, no touching, 10 seconds idle.
**Packets:** ~350

### Host → MCU (EP 0x01 OUT, addr 1.5.1)

All frames identical, sent every **~1000ms** (±10ms):

```
4f 53 4b 50 03 00 20 01 00
```

Decoded:
| Offset | Value | Meaning |
|---|---|---|
| 0-3 | `4F534B50` | Magic "OSKP" |
| 4-5 | `0300` | Wire length LE16 = 3 (payload + 1 type byte) |
| 6 | `20` | **Type 0x20 — Sync flag** |
| 7-8 | `0100` | Payload: `[0x01, 0x00]` |

**Cadence: exactly 1 second intervals.**

No other command types observed during idle:
- ❌ No `0x25` (mode toggle)
- ❌ No `0x26` (keepalive with timestamp)
- ❌ No `0x31` (geometry rects)
- ❌ No `0x21` (param)

### MCU → Host (EP 0x84 IN, addr 1.1.4)

- 281-byte frames periodically (touch sensor data)
- 0-byte URB submissions (polling)

---

## Capture 02: `02-keyboard-detached-10s.pcapng` — Keyboard Detached

**Action:** Keyboard physically detached, 10 seconds capture.
**Packets:** 489

### Host → MCU (EP 0x01 OUT, addr 1.5.1)

**ZERO host-to-device OSKP frames.** The `0x20` sync commands stop completely.

### Key observations:
1. **Full USB re-enumeration** at t=0 — all interfaces get GET_DESCRIPTOR → SET_CONFIGURATION
2. **No OSKP traffic at all** — YB9.Service.exe appears to lose its handle or stops sending when the composite device disconnects
3. **EP 0x84 (1.1.4)** continues sending 281-byte touch data frames periodically
4. **1.4.1 EP 0x81** floods with 31-59 byte frames (this is Bluetooth, not INGENIC)

### Control transfer at t=9.3s
- One host → 1.1.0 control transfer (frame 365) — likely Windows re-probing the CDC ACM interface

---

## Capture 03: `03-keyboard-reattached-15s.pcapng` — Keyboard Reattached

**Action:** Keyboard reattached, 17 seconds capture.
**Packets:** 226 + 507 (two USBPcap interfaces)

### Host → MCU (EP 0x01 OUT, addr 1.5.1)

OSKP `0x20` sync resumes **immediately** — first frame at t=0.017s:

```
4f 53 4b 50 03 00 20 01 00
```

Same pattern as idle: **exactly ~1000ms cadence.**

### Sequence of events on reattach:

| Time | Event | Details |
|---|---|---|
| 0.000s | USB re-enumeration | All 4 devices: 1.1.0, 1.2.0, 1.4.0, 1.5.0 get GET_DESCRIPTOR → SET_CONFIGURATION |
| 0.017s | OSKP sync resumes | First `0x20` on EP 0x01 |
| 0.1–7.2s | Steady state | Sync every ~1s, MCU data on 0x84, BT traffic on 1.4.x |
| ~7.3s | Bluetooth LE setup | HCI commands to 1.4.0 (LE Set Extended Scan Enable, Vendor cmd 0xfc1e, LE Set Extended Scan Params) — **NOT INGENIC** |
| ~7.5s | BT scan results | 18-byte ACL packets on EP 0x82 — Bluetooth LE advertising, **NOT INGENIC** |

### No special activation sequence
- No mode toggle (0x25) sent on reattach
- No keepalive (0x26) sent
- No geometry (0x31) sent
- Just the `0x20` sync immediately and then steady 1s cadence

### Interface 0 CDC ACM (1.1.0) activity
- t=4.45s: One control transfer host→1.1.0 (SET_LINE_CODING or similar — device re-initialization)
- t=1.74s: EP 0x84 returns 0-byte (URB complete with no data)

---

## Key Findings So Far

1. **Only one OSKP command in steady state: `0x20` sync** with payload `[0x01, 0x00]` every ~1000ms
   - This differs from the Linux driver which sends `0x26` keepalive every ~1500ms
   - **The 0x20 sync may be the actual keepalive that maintains touchpad mode**

2. **Keyboard detach kills all OSKP traffic** — the composite device disconnects, YB9.Service loses the handle

3. **No 0x26 keepalive observed at all** during any of the 3 captures — the Windows driver uses `0x20` instead

4. **Interface 1 (Vendor Specific 0xFF) is the actual OSKP transport**, NOT Interface 0 (CDC ACM)
   - EP 0x01 OUT = host→MCU OSKP commands
   - This is a bulk endpoint, not the CDC serial

5. **Interface 0 (CDC ACM) appears to carry only touch sensor data** (281-byte frames on EP 0x84 IN)

---

---

## Capture 04: `04-touchpad-active-10s.pcapng` — Touchpad Active Use

**Action:** Finger moving on touchpad for ~5 seconds, then idle for ~10 seconds.
**Packets:** ~5015

### Host → MCU (EP 0x01 OUT, addr 1.5.1)

**Identical to idle** — only `0x20` sync every ~1000ms:
```
4f 53 4b 50 03 00 20 01 00
```
- No additional commands triggered by touch input
- No `0x26` keepalive, no `0x25` mode toggle, no `0x31` geometry
- Cadence unaffected by touch activity

### MCU → Host Touch Data (EP 0x84 IN, addr 1.5.4)

**This is the corrected endpoint mapping:**
- Touch data comes from **`1.5.4 → EP 0x84`** (INGENIC Interface 1, alt setting 4)
- **NOT** from `1.1.4` as previously noted — `1.1.x` was CDC ACM (Interface 0)

**During active touch:**
- 32-byte frames at **~6ms intervals** (~167 Hz polling rate)
- Continuous stream from t=0.008s to t=14.99s throughout entire capture
- Frame size consistently 32 bytes (touch coordinate + pressure data)

**During idle (Capture 01):**
- **ZERO** frames from `1.5.4` → EP 0x84
- This endpoint is silent when touchpad is not being touched
- This confirms: MCU only sends touch data when fingers are detected

### Key finding: Previous topology was WRONG

| Previous mapping | Corrected mapping |
|---|---|
| EP 0x84 IN on `1.1.4` = touch data | `1.1.4` = CDC ACM (Interface 0) serial, 281-byte OSKP response frames |
| — | EP 0x84 IN on `1.5.4` = **actual touch sensor data**, 32-byte frames |
| `1.4.x` = INGENIC Interface 4 | `1.4.x` = **Bluetooth adapter** (completely separate device, class 0xE0) |

### Address allocation is dynamic

USB device addresses change between captures because of re-enumeration. The mapping below uses the addresses from each capture:

**Captures 01/04 (no reattach):**
- `1.1.x` = INGENIC Interface 0 (CDC ACM / OSK Partner)
- `1.2.x` = INGENIC Interface ? (HID)
- `1.5.x` = INGENIC Interface 1 (Vendor Specific — OSKP + touch)
- `1.4.x` = Bluetooth adapter (NOT INGENIC)

---

## Consolidated Key Findings (Captures 01-04)

### 1. The only steady-state OSKP command is `0x20` sync
- Sent every **~1000ms** on EP 0x01 OUT (Interface 1 bulk)
- Payload: `4f534b500300200100` → OSKP type=0x20, data=`[0x01, 0x00]`
- This is likely the actual keepalive/heartbeat that maintains touchpad mode
- **No `0x26` keepalive has been observed in any capture**
- Touchpad activity does NOT change the command pattern

### 2. Interface 1 (Vendor Specific 0xFF) is the OSKP transport
- EP 0x01 OUT = host→MCU OSKP commands
- EP 0x84 IN = MCU→host touch sensor data (32-byte frames, ~167Hz when touched)
- This is a bulk endpoint pair, not the CDC ACM serial port

### 3. Interface 0 (CDC ACM) role unclear
- 281-byte frames on EP 0x84 (addr 1.1.4) seen in Captures 01/02
- These are NOT touch data (wrong endpoint)
- May be OSKP response frames or firmware status
- Silent in Capture 04 (or address was different)

### 4. Keyboard detach kills everything
- Composite device disconnects → all OSKP traffic stops
- YB9.Service.exe loses the device handle
- MCU continues sending data on its own (no host control)

### 5. No special activation sequence on reattach
- Just `0x20` sync resumes immediately after re-enumeration
- No mode toggle, no geometry setup, no firmware commands

---

---

## Capture 05: `05-keyboard-detect.pcapng` — Keyboard Detection Mechanism

**Action:** Keyboard detached at ~2-3s, waited ~3s, reattached at ~7-8s. 20s capture.
**Packets:** 506

### Key finding: Keyboard detach does NOT cause USB disconnect

Unlike in Capture 02 (where the whole device re-enumerated), this time the USB device stayed connected throughout. The `0x20` sync continued uninterrupted every ~1s for the full 20 seconds.

**Possible explanation:** In Capture 02, the keyboard may have been detached more forcefully or at a different angle, causing the entire USB device to lose contact. In Capture 05, only the magnetic keyboard detached cleanly without disturbing the MCU's USB connection.

### 281-byte HID reports (EP 0x84 IN, addr 1.1.4) show timing gaps

The CDC ACM / HID interface (1.1.4) sends 281-byte reports. Cadence analysis:

| Time | Interval | Event |
|---|---|---|
| 0.46s, 0.97s | ~0.5s | Normal (keyboard attached) |
| **0.97s → 5.05s** | **~4s gap** | Keyboard detached — reports pause |
| 5.05s, 5.55s, 6.06s, 6.57s | ~0.5s | Keyboard detached — reports resume at same cadence |
| **6.57s → 8.62s** | **~2s gap** | Keyboard reattached — reports pause briefly |
| 8.62s → 19.86s | ~0.5s | Normal (keyboard attached again) |

**The 281-byte HID report content likely contains keyboard-attached state.** Unfortunately USBPcap didn't capture raw data for interrupt IN transfers in this capture.

### OSKP sync unaffected

The `0x20` sync on EP 0x01 (addr 1.5.1) continued at ~1s intervals throughout with no change, regardless of keyboard state.

### Conclusion: Keyboard detection mechanism

1. **Physical:** Magnetic sensor detects keyboard attach/detach
2. **MCU:** INGENIC MCU knows keyboard state internally
3. **USB signaling:** 281-byte HID reports on Interface 0 (CDC ACM / HID) carry state to host
4. **YB9.Service:** Reads HID reports → detects keyboard state change → activates/deactivates touchpad mode
5. **USB device stays connected** — no USB disconnect in clean detach scenario

---

---

## Capture 06: `06-keyboard-state-hid.pcapng` — Keyboard State & OSKP Commands

**Action:** Keyboard detached at ~3-4s, reattached at ~8-9s. 15s capture.
**Packets:** 414

### MAJOR DISCOVERY: OSKP `0x31` geometry commands sent on detach/reattach

This is the first capture showing commands other than `0x20` sync!

**Timeline:**

| Time | Frame | Direction | Data | Meaning |
|---|---|---|---|---|
| 0.22s–3.22s | 27,43,61,85 | Host→MCU | `4f534b500300200100` | Normal `0x20` sync every ~1s |
| **3.88s** | **95** | **Host→MCU** | `4f534b502a003100 00000000...00040000000000000000` | **`0x31` Geometry — ALL ZEROS** (keyboard detached!) |
| 3.88s–8.97s | — | — | — | ~5s gap, no sync commands |
| **8.97s** | **222** | Host→MCU | `4f534b500300200100` | `0x20` sync resumes |
| **8.97s** | **225** | **Host→MCU** | `4f534b502a003100 3804400b...180bc404` | **`0x31` Geometry — with actual rectangle data** (keyboard reattached!) |
| **8.97s** | **231** | **MCU→Host** | `4f534b5008007599752400000101` | **`0x75` MCU response** — NEW unknown type! |
| 9.97s–14.97s | 273+ | Host→MCU | `4f534b500300200100` | Normal `0x20` sync every ~1s |

### Decoded OSKP Frame 95 — Geometry Clear (keyboard detached)

```
4F 53 4B 50  — Magic "OSKP"
2A 00        — Length LE16 = 42 bytes (payload + type)
31           — Type 0x31 (Geometry rects)
00           — Subcmd/padding
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 04 00 00 00 00 00 00 00 00
```
All-zero geometry = **touchpad area cleared** when keyboard detaches.

### Decoded OSKP Frame 225 — Geometry Set (keyboard reattached)

```
4F 53 4B 50  — Magic "OSKP"
2A 00        — Length LE16 = 42 bytes
31           — Type 0x31 (Geometry rects)
00           — Subcmd/padding
38 04 40 0B 08 07 7D 03 84 06 9B 05 FE 06 A5 05
84 06 C3 07 FE 06 28 00 60 04 8C 00 C4 04 04 B4
0A 60 04 18 0B C4 04
```
Actual touchpad rectangle coordinates (4 rectangles × 10 bytes each?).

### Decoded OSKP Frame 231 — MCU Response (NEW type 0x75)

```
4F 53 4B 50  — Magic "OSKP"
08 00        — Length LE16 = 8 bytes
75           — Type 0x75 (UNKNOWN — not in our command table!)
99 75 24 00 00 01 01
```
**This is a new OSKP command type never seen before.** Sent by MCU → host in response to the `0x31` geometry command.

### 281-byte HID reports (1.1.4 EP 0x84)

Only 4 reports captured — all after reattach:
- 11.05s, 11.56s, 12.06s, 14.12s
- None during detached period (3.88s–8.97s)
- Suggests MCU stops sending HID reports when keyboard is detached

### Key Findings from Capture 06

1. **YB9.Service detects keyboard detach** → sends `0x31` with zero geometry (clears touchpad)
2. **YB9.Service detects keyboard reattach** → sends `0x31` with real geometry (configures touchpad area)
3. **MCU responds with `0x75`** — a new OSKP response type we didn't know about
4. **`0x20` sync pauses** during detached state (~5s gap from 3.22s to 8.97s)
5. **281-byte HID reports stop** when keyboard is detached
6. **The detection mechanism is:** MCU sends HID reports with keyboard state → YB9.Service reads them → triggers `0x31` geometry clear/set on Interface 1

---

## Updated Command Table

| Type | Name | Payload | Direction | Notes |
|------|------|---------|-----------|-------|
| `0x20` | Sync/heartbeat | `[0x01, 0x00]` | Host→MCU | Every ~1s during active touchpad |
| `0x31` | Geometry rects | 41 bytes | Host→MCU | All zeros = clear; real data = set touchpad area |
| `0x75` | Geometry ACK? | `99 75 24 00 00 01 01` | MCU→Host | **NEW** — response to 0x31 |

### Previously known but not seen in captures yet:

| `0x25` | Mode toggle | `[0x00]` or `[0x01]` | Host→MCU | Not observed — may only occur at boot |
| `0x26` | Keepalive | 7-byte timestamp | Host→MCU | Not observed — may not be used by Windows driver |
| `0x21` | Param | subcmd + data | Host→MCU | Not observed |
| `0x27` | Panel touch enable | `[panel, enable]` | Host→MCU | Not observed |
| `0x28` | Status pair | `[bool, state]` | Host→MCU | Not observed |
| `0xa3` | Post-orientation | `[flag]` | Host→MCU | Not observed |

---

---

## Capture 07: `07-boot-sequence.pcapng` — Service Restart (touchpad still active)

**Action:** Killed YB9.Service, restarted capture, started service, touched touchpad. 30s capture.
**Packets:** ~743

**Result:** Only `0x20` sync from the first frame. No init commands. Touchpad was still active from previous session.

---

## Capture 08: `08-full-boot-sequence.pcapng` — Service Restart After 10s Wait

**Action:** Killed YB9.Service + YB9.TouchPad, waited 10 seconds (MCU timeout attempt), restarted capture, started service, activated touchpad. 30s capture.
**Packets:** ~731

**Result:** Still only `0x20` sync. MCU did NOT time out because **USB connection was never broken**.

### Critical insight: MCU timeout requires USB disconnect

The MCU maintains touchpad mode as long as:
- USB connection stays active (even without host software)
- OR host sends `0x20` sync periodically

The 7-second Linux timeout likely occurs because:
- Linux driver uses CDC ACM (Interface 0) for OSKP, not Interface 1 bulk
- OR Linux sends `0x26` keepalive instead of `0x20` sync
- OR Linux driver opens a different endpoint that the MCU doesn't recognize as a heartbeat

---

## MASTER FINDINGS — All Captures (01-08)

### The Complete OSKP Command Set Observed on Windows

| Type | Name | Payload | Direction | When |
|------|------|---------|-----------|------|
| `0x20` | Sync/heartbeat | `[0x01, 0x00]` (9 bytes total) | Host→MCU | **Every ~1 second** — the ONLY steady-state command |
| `0x31` | Geometry rects | 41 bytes (all zeros = clear, real data = set) | Host→MCU | On keyboard attach/detach |
| `0x75` | Geometry ACK | 7 bytes | MCU→Host | Response to 0x31 (NEW discovery) |

### What Windows DOES NOT send (that Linux does)

| Command | Linux sends | Windows? |
|---------|-------------|----------|
| `0x26` keepalive | Every ~1500ms with timestamp | **NEVER observed** |
| `0x25` mode toggle | At init | **NEVER observed** |
| `0x21` param | Various subcmds | **NEVER observed** |
| `0x27` panel enable | Init | **NEVER observed** |
| `0x28` status pair | Init | **NEVER observed** |

### The Critical Difference: Endpoint and Command

**Windows activation sequence:**
1. **HID output report `[0x20, 0x00]`** to Interface 2 EP 0x02 (HID Interrupt OUT) — switches MCU to touchpad mode
2. **`0x20` OSKP sync** every ~1000ms on Interface 1 EP 0x01 (Vendor Specific bulk) — keeps touchpad alive
3. **`0x31` geometry** on Interface 1 EP 0x01 — configures touchpad area

**Linux may be failing because:**
1. Missing HID mode toggle — Linux sends OSKP `0x25` instead of HID `[0x20, 0x00]` to EP 0x02
2. Wrong keepalive — sends `0x26` instead of `0x20` sync
3. Wrong endpoint — using CDC ACM (Interface 0) instead of Interface 1 bulk EP 0x01
4. Wrong cadence — 1500ms instead of 1000ms

### Recommended Linux Driver Fix

1. **Send HID output report `[0x20, 0x00]`** to Interface 2 EP 0x02 (HID Interrupt OUT) to activate touchpad
2. **Send `0x20` OSKP sync every 1000ms** on Interface 1 EP 0x01 OUT (bulk): `4f534b500300200100`
3. **Send `0x31` geometry** on Interface 1 EP 0x01 with correct rectangle data
4. **Listen for `0x75` response** on EP 0x81 IN
5. Touch data arrives on **EP 0x84 IN** (57-byte frames in touchpad mode)
6. **Listen for `0xa2` position reports** on EP 0x81 IN for keyboard position changes

---

---

## Capture 10: `10-touchpad-activate.pcapng` — THE CRITICAL CAPTURE: Touchpad Activation from Touchscreen Mode

**Action:** Started with touchpad OFF (touchscreen mode), user activated touchpad. 20s capture.
**Packets:** 614

### THIS IS THE MOST IMPORTANT CAPTURE — shows the full activation sequence!

### Timeline

| Time | Frame | Endpoint | Data | Event |
|---|---|---|---|---|
| 0–5.6s | — | — | — | **Silence** — no host→MCU OSKP traffic (touchscreen mode) |
| 1.7s | 57 | `1.1.0` ctrl | SET_IDLE (HID class, IF 4) | Host configures Interface 0 HID |
| 3.2s, 3.7s | 93,109 | `1.1.4` EP 0x84 | 281-byte HID reports | MCU sends keyboard state reports |
| **5.64s** | **157** | `1.5.0` ctrl | SET_IDLE for HID on IF 2 | Host opens Interface 1 endpoints |
| 5.64s | 158 | `1.5.0` | **STALL** | MCU rejects SET_IDLE (vendor interface) |
| 5.64s | 159-160 | `1.5.3` EP 0x83 | Interrupt IN submit | Host starts polling HID EP 0x83 |
| 5.64s | 161 | `1.5.1` EP 0x81 | Bulk IN submit | Host starts polling OSKP response EP |
| 5.64s | 162 | `1.5.2` EP 0x82 | Interrupt IN submit | Host starts CDC ACM notify polling |
| **5.64s** | **163** | `1.5.0` ctrl | **SET LINE CODING** `00c20100000000` (115200 baud) | CDC ACM serial config |
| **5.64s** | **164** | **`1.5.2` EP 0x02** | **HID Data: `2000`** | **THE MODE TOGGLE — HID output report!** |
| **5.64s** | 180-245 | `1.5.4` EP 0x84 | **57-byte frames at ~1ms** | MCU floods touchpad data |
| **5.75s** | **247,249** | `1.5.1` EP 0x81 | `0xa2` position ×2 | MCU position reports |
| **5.99s** | **253** | `1.5.1` EP 0x01 | `0x20` sync | **First OSKP sync** |
| **5.99s** | **255** | `1.5.1` EP 0x81 | `0x75` response | MCU ACK |
| **6.03s** | **257** | `1.5.1` EP 0x01 | `0x31` geometry | Touchpad area configured |
| 7.0s–20s | — | `1.5.1` EP 0x01 | `0x20` sync every ~1s | Steady state |

### THE ACTIVATION SEQUENCE (decoded)

```
1. User taps bottom screen (touchscreen mode) → YB9.Service detects touch in touchpad zone
2. Host sends SET_IDLE + SET LINE CODING to configure INGENIC interfaces
3. Host sends HID report [0x20, 0x00] to Interface 2 EP 0x02 (HID Interrupt OUT)
   → THIS is the "mode toggle" — NOT an OSKP 0x25 command!
4. MCU immediately starts flooding EP 0x84 with 57-byte touch data frames
5. MCU sends 0xa2 position reports on EP 0x81
6. Host sends 0x20 OSKP sync + 0x31 geometry
7. MCU responds with 0x75 ACK
8. Steady state: 0x20 sync every ~1 second
```

### CRITICAL DISCOVERY: The mode toggle is HID, not OSKP!

**Frame 164** — `1.5.2` EP 0x02 (HID Interrupt OUT):
```
HID Data: 20 00
```
This 2-byte HID report `[0x20, 0x00]` sent to **Interface 2** (HID, EP 0x02) is what switches the MCU from touchscreen to touchpad mode!

- The previously known OSKP `0x25` mode toggle was likely a misidentification or Linux-specific
- Windows uses a simple HID output report `[0x20, 0x00]` to activate touchpad mode
- The MCU responds by switching EP 0x84 output from touchscreen data to touchpad data

### Interface topology refined (from this capture)

**Device 1.5 (INGENIC):**
- `1.5.0` = Control endpoint (configurations, CDC ACM SET LINE CODING)
- `1.5.1` = Interface 1 EP 0x01 OUT / EP 0x81 IN (Vendor Specific 0xFF — OSKP commands)
- `1.5.2` = Interface 2 EP 0x02 OUT / EP 0x82 IN (HID — mode toggle `[20 00]`, notifications)
- `1.5.3` = Interface ? EP 0x83 IN (HID Interrupt — unknown, 0-byte polls)
- `1.5.4` = Interface ? EP 0x84 IN (Touch data — 57-byte frames in touchpad mode)

### `0xa2` position reports (Capture 10 version)

```
Frame 247: 4F534B50 0700 A2 E4 46 0C 02 00 00
Frame 249: 4F534B50 0700 A2 E4 46 0C 02 01 00
```
Compared to Capture 09:
```
Frame 489: 4F534B50 0700 A2 75 1B C9 01 00 00
Frame 491: 4F534B50 0700 A2 75 1B C9 01 01 00
```
- Bytes 1-2 change between captures — likely timestamp/counter
- Byte 4 is always `0x00` / `0x01` — **position identifier** (consistent!)

---

## Capture 09: `09-keyboard-positions.pcapng` — Keyboard Position Changes

**Action:** Keyboard position changed multiple times (laptop/tent/tablet modes). 25s capture.
**Packets:** 632

### Timeline

| Time | Frame | Data | Event |
|---|---|---|---|
| 0–13.02s | multiple | `0x20` sync every ~1s | Normal — keyboard in one position |
| **13.07s** | **335** | `0x31` **all-zeros** | **Position change → touchpad cleared** |
| 13–19s | — | **~6s gap, no sync** | Service paused during transition |
| **17.74s** | **489** | `4f534b500700a2751bc9010000` | **MCU→Host `0xa2` — position signal!** |
| **17.74s** | **491** | `4f534b500700a2751bc9010100` | **MCU→Host `0xa2` — second position!** |
| 12.4s, 12.9s | 307,327 | 281-byte HID on `1.1.4` | HID reports right before position change |
| **19.12s** | 527 | `0x20` sync | Sync resumes |
| **19.16s** | **529** | `0x31` **with real geometry** | Touchpad configured for new position |
| 20.2s, 21.2s | 549,561 | `0x20` sync | Normal |
| **22.11s** | **587** | `0x31` **all-zeros** | **Another position change!** |

### NEW OSKP Command: `0xa2` (Keyboard Position Report)

```
Frame 489: 4F 53 4B 50 07 00 A2 75 1B C9 01 00 00
Frame 491: 4F 53 4B 50 07 00 A2 75 1B C9 01 01 00
```

| Field | Value | Meaning |
|---|---|---|
| Magic | `4F534B50` | "OSKP" |
| Length | `0700` | 7 bytes |
| Type | `0xa2` | **NEW: Keyboard position report** |
| Payload byte 0 | `0x75` | Unknown (fixed?) |
| Payload bytes 1-2 | `1BC9` | Could be angle data or timestamp |
| Payload byte 3 | `0x01` | Unknown (fixed?) |
| Payload byte 4 | `0x00` / `0x01` | **Position identifier!** — 0 vs 1 = different keyboard angle |
| Payload byte 5 | `0x00` | Padding/status? |

The `0xa2` frames come from MCU→Host (EP 0x81 IN), sent in pairs with different position values.

### Position Change Sequence (confirmed from 3 events)

```
1. MCU detects position change (magnetic sensor)
2. MCU sends 281-byte HID report on Interface 0
3. MCU sends 0xa2 position frames on Interface 1 EP 0x81
4. YB9.Service detects position change
5. YB9.Service sends 0x31 all-zeros (clears touchpad geometry)
6. YB9.Service pauses 0x20 sync (~6 seconds)
7. YB9.Service sends 0x31 with new geometry (sets touchpad for new position)
8. YB9.Service resumes 0x20 sync
```

### HID reports also stop during transition
- 281-byte reports on `1.1.4` only at 12.4s and 12.9s (right before first position change)
- None after 13s — MCU stops HID reports during position transition

---

## FINAL Updated Command Table (all discoveries)

| Type | Name | Payload | Direction | When |
|------|------|---------|-----------|------|
| `0x20` | Sync/heartbeat | `[0x01, 0x00]` | Host→MCU | Every ~1s — ONLY steady-state command |
| `0x31` | Geometry rects | 41 bytes: zeros=clear, data=set | Host→MCU | Keyboard attach/detach + position change |
| `0x75` | Geometry ACK | `99752400000101` | MCU→Host | Response to 0x31 geometry set (Capture 06) |
| `0xa2` | Position report | `75 1B C9 01 [pos] 00` | MCU→Host | **NEW** — keyboard position change, pos=`0x00` or `0x01` |

### Previously known but NOT observed in any Windows capture:

| `0x25` | Mode toggle | `[0x00]`/`[0x01]` | Host→MCU | NEVER seen — may be Linux-only or decompiled incorrectly |
| `0x26` | Keepalive | 7-byte timestamp | Host→MCU | NEVER seen — Linux sends this but Windows doesn't |
| `0x21` | Param | subcmd + data | Host→MCU | NEVER seen |
| `0x27` | Panel touch enable | `[panel, enable]` | Host→MCU | NEVER seen |
| `0x28` | Status pair | `[bool, state]` | Host→MCU | NEVER seen |
| `0xa3` | Post-orientation | `[flag]` | Host→MCU | NEVER seen |

---

## Pending Captures (lower priority)

- [ ] Deactivate touchpad via UI (not keyboard detach) — may show different commands
- [ ] Capture with USB filter on INGENIC device only (reduce Bluetooth noise)
- [ ] Verify `0x25` mode toggle by causing actual USB reconnect during boot
