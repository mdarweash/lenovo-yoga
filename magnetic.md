# Yoga Book 9 (83KJ) — Keyboard Hall Effect Sensor Investigation

## Model Info

- **Product**: Yoga Book 9 14IAH10
- **Model number**: 83KJ
- **BIOS**: QECN28WW
- **Kernel**: 6.17.0-19-generic
- **OS**: Ubuntu Linux

## Physical Sensor

The keyboard dock position is detected by a **hall effect sensor**. A magnet is embedded in the keyboard half, and a magnetic field detector (hall sensor) is in the tablet/screen half. The sensor can detect three states:

1. Keyboard detached
2. Keyboard attached to front (laptop mode)
3. Keyboard attached to back (tablet mode)

## Detection Path (intended firmware flow)

```
Hall sensor triggers
    → Embedded Controller (EC) detects magnetic field change
    → EC fires ACPI notify: \_SB.PC00.LPCB.EC0._Q37
    → _Q37 should notify WMI / input subsystem
    → lenovo-ymc driver receives WMI event (GUID 06129D99-6083-4164-81AD-F092F9D773A6)
    → Reports SW_TABLET_MODE on /dev/input/event6
```

## The Bug: ACPI BIOS Error in `_Q37`

The `_Q37` ACPI method has a bug — it references `PNOT` which does not exist, causing the entire method to abort before any notification reaches the OS.

```
ACPI BIOS Error (bug): Could not resolve symbol [\_SB.PC00.LPCB.EC0._Q37.PNOT], AE_NOT_FOUND
ACPI Error: Aborting method \_SB.PC00.LPCB.EC0._Q37 due to previous error (AE_NOT_FOUND)
```

This error appears in `journalctl -k` whenever the hall sensor triggers (i.e., when attaching or detaching the keyboard).

## Verified: Nothing Reaches Linux

All of the following were tested by attaching/detaching the keyboard in both positions while monitoring:

| Signal | Path | Result |
|--------|------|--------|
| ACPI DOCK status | `/sys/bus/acpi/devices/ABCD0000:00/status` | Always 0 |
| USB INGENIC device | `/sys/bus/usb/devices/3-6/` (vendor `17ef:6161`) | Always connected |
| SW_TABLET_MODE | `/dev/input/event6` (lenovo-ymc WMI driver) | Always LAPTOP |
| All GPIOs | 451 GPIOs on `INTC105E:00` (base 512) | No changes |
| All input events | `/dev/input/event0` through `event19` | No events fired |

## Relevant Kernel Drivers

- **`lenovo_ymc`** (`drivers/platform/x86/lenovo/ymc.c`) — WMI Yoga Mode Control driver. Bound to GUID `06129D99-6083-4164-81AD-F092F9D773A6`, reports `SW_TABLET_MODE` on event6. Would work if `_Q37` didn't abort.
- **`ideapad_laptop`** (`drivers/platform/x86/lenovo/ideapad-laptop.c`) — IdeaPad ACPI extras. Bound to `VPC2004:00`. Handles EC events but doesn't receive `_Q37` because it aborts.
- **`yogabook`** (`drivers/platform/x86/lenovo/yogabook.c`) — Dedicated Yoga Book driver for older YB1-X90F/X91F models. Uses a GPIO-based `backside_hall_sw` (GPIO on `INT33FF:02` pin 18). **Not used on the Yoga Book 9** — that model has a different GPIO controller (`INTC105E:00`) and the hall sensor is not exposed as a GPIO.

## WMI Devices on This System

Key GUIDs related to keyboard/mode control:

| GUID | Driver | Purpose |
|------|--------|---------|
| `06129D99-6083-4164-81AD-F092F9D773A6` | `lenovo-ymc` | Yoga Mode Control events |
| `09B0EE6E-C3FD-4243-8DA1-7911FF80BB8C` | (query) | YMC state query (method 0x01, instance 0) |
| `8FC0DE0C-B4E4-43FD-B0F3-8871711C1294` | `ideapad_wmi` | IdeaPad WMI events |

## Why Windows Detects It

Lenovo's Windows driver likely uses a different communication path that bypasses the buggy `_Q37` method — either:
- Direct EC register reads via a proprietary interface
- A different WMI method not used by the Linux driver
- A Lenovo-specific Windows ACPI driver that handles the `_Q37` error gracefully

## Possible Fixes

### 1. BIOS Update
Check [Lenovo's support site](https://support.lenovo.com) for a newer BIOS that fixes the `_Q37`/`PNOT` bug.

### 2. Custom SSDT Overlay (advanced)
Load a patched ACPI table via initrd that fixes `_Q37` by removing the `PNOT` call and adding proper WMI notification. See kernel documentation on `initrd` based ACPI table overrides.

### 3. Direct EC Register Poll (most practical workaround)
1. Load the EC sysfs module: `sudo modprobe ec_sys write_support=1`
2. Dump EC memory: `xxd /sys/kernel/debug/ec/ec0/io`
3. Attach/detach keyboard and dump again to find the changing bit
4. Poll that register directly in a script

### 4. Kernel Bug Report
File a bug at [bugzilla.kernel.org](https://bugzilla.kernel.org) against `platform/x86: Lenovo`. The ACPI error output is already in the kernel log. The driver may need a quirk for the Yoga Book 9 to handle the `_Q37` failure gracefully or use an alternative detection method.
