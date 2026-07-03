// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * ingenic_touchpad.c - Touchpad mode activation/deactivation for INGENIC MCU
 *
 * Implements the Windows-derived activation sequence:
 * 1. SET_IDLE on IF2 (MCU will STALL - ignore)
 * 2. SET_LINE_CODING on IF0
 * 3. HID SET_REPORT [0x20, 0x00] to IF2 - THE MODE TOGGLE
 * 4. 350ms settle delay
 * 5. OSKP 0x20 sync
 * 6. OSKP 0x31 geometry
 * 7. Start periodic 0x20 sync every 1s
 *
 * Copyright (c) 2024 Muhammad Darweash
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/usb.h>
#include <linux/delay.h>
#include <linux/jiffies.h>

#include "ingenic_mcu.h"
#include "ingenic_oskp.h"
#include "ingenic_touchpad.h"

/*
 * Send HID SET_REPORT to Interface 2 (keyboard) via control endpoint.
 * This is THE mode toggle command - it switches between
 * touchscreen and touchpad mode on the MCU.
 *
 * We use the control endpoint because we don't claim IF2
 * (the keyboard must keep working).
 */
static int ingenic_hid_set_report(struct ingenic_dev *idev, u8 report_id, u8 value)
{
	struct usb_device *udev = idev->udev;
	u8 *buf;
	int retval;

	buf = kmalloc(2, GFP_KERNEL);
	if (!buf)
		return -ENOMEM;

	buf[0] = report_id;
	buf[1] = value;

	/*
	 * HID SET_REPORT via control endpoint:
	 * bmRequestType = 0x21 (Host-to-device, Class, Interface)
	 * bRequest = 0x09 (SET_REPORT)
	 * wValue = 0x0200 (Output report type, Report ID 0)
	 * wIndex = 2 (Interface 2 - keyboard)
	 * wLength = 2
	 */
	retval = usb_control_msg(udev, usb_sndctrlpipe(udev, 0),
				 0x09, /* SET_REPORT */
				 USB_TYPE_CLASS | USB_RECIP_INTERFACE,
				 0x0200, /* Output report, ID 0 */
				 INGENIC_IF_KEYBOARD,
				 buf, 2, USB_CTRL_SET_TIMEOUT);

	kfree(buf);

	if (retval < 0) {
		dev_warn(&idev->intf->dev,
			 "HID SET_REPORT failed: %d\n", retval);
		return retval;
	}

	dev_dbg(&idev->intf->dev,
		"HID SET_REPORT [0x%02x, 0x%02x] -> IF2, ret=%d\n",
		report_id, value, retval);
	return 0;
}

/*
 * Send SET_IDLE to Interface 2 (keyboard).
 * The MCU will STALL this - that's expected and harmless.
 */
static void ingenic_send_set_idle(struct ingenic_dev *idev)
{
	struct usb_device *udev = idev->udev;
	int retval;

	retval = usb_control_msg(udev, usb_sndctrlpipe(udev, 0),
				 0x0A, /* SET_IDLE */
				 USB_TYPE_CLASS | USB_RECIP_INTERFACE,
				 0,     /* Duration 0, Report ID 0 */
				 INGENIC_IF_KEYBOARD,
				 NULL, 0, USB_CTRL_SET_TIMEOUT);

	/* STALL is expected - ignore */
	if (retval == -EPIPE)
		dev_dbg(&idev->intf->dev, "SET_IDL STALL (expected)\n");
	else if (retval < 0)
		dev_dbg(&idev->intf->dev, "SET_IDLE: %d (ignoring)\n", retval);
}

/*
 * Pack a rectangle as 4 LE16 values into buffer.
 */
static void pack_rect(u8 *buf, u16 left, u16 top, u16 right, u16 bottom)
{
	buf[0] = left & 0xFF;
	buf[1] = (left >> 8) & 0xFF;
	buf[2] = top & 0xFF;
	buf[3] = (top >> 8) & 0xFF;
	buf[4] = right & 0xFF;
	buf[5] = (right >> 8) & 0xFF;
	buf[6] = bottom & 0xFF;
	buf[7] = (bottom >> 8) & 0xFF;
}

/*
 * Build the 41-byte geometry payload for OSKP 0x31.
 * Returns payload length or negative error.
 */
int ingenic_build_geometry(struct ingenic_dev *idev, u8 *buf, u16 buf_len)
{
	u16 w = idev->screen_width;
	u16 h = idev->screen_height;
	u16 caption_h, btn_h, side_m, bottom_m, gap, half_btn;

	if (buf_len < GEOMETRY_PAYLOAD_LEN)
		return -ENOMEM;

	caption_h = h * GEOM_CAPTION_FRAC / GEOM_DENOM;
	btn_h = h * GEOM_BTN_FRAC / GEOM_DENOM;
	side_m = w * GEOM_SIDE_FRAC / GEOM_DENOM;
	bottom_m = h * GEOM_BOTTOM_FRAC / GEOM_DENOM;
	gap = w * GEOM_GAP_FRAC / GEOM_DENOM;
	half_btn = (w - 2 * side_m - gap) / 2;

	/* frameRect: entire screen */
	pack_rect(&buf[0], 0, 0, w, h);

	/* LButton rect */
	pack_rect(&buf[8], side_m, h - bottom_m - btn_h,
		  side_m + half_btn, h - bottom_m);

	/* RButton rect */
	pack_rect(&buf[16], side_m + half_btn + gap,
		  h - bottom_m - btn_h,
		  w - side_m, h - bottom_m);

	/* touchableRect1 */
	pack_rect(&buf[24], side_m, caption_h,
		  w - side_m, h - bottom_m - btn_h);

	/* packed flags: SrcId=0, DisableForMini=0 */
	buf[32] = 0x00;

	/* touchableRect2: same as touchableRect1 */
	pack_rect(&buf[33], side_m, caption_h,
		  w - side_m, h - bottom_m - btn_h);

	return GEOMETRY_PAYLOAD_LEN;
}

/*
 * Activate touchpad mode.
 *
 * This replicates the exact Windows activation sequence:
 * 1. SET_IDLE on IF2 (MCU STALLs - expected)
 * 2. SET_LINE_CODING on IF0
 * 3. HID SET_REPORT [0x20, 0x00] to IF2 - THE MODE TOGGLE
 * 4. 350ms settle delay
 * 5. OSKP 0x20 sync [0x01, 0x00]
 * 6. OSKP 0x31 geometry (41 bytes)
 * 7. Start periodic 0x20 sync every 1s
 */
int ingenic_touchpad_activate(struct ingenic_dev *idev)
{
	u8 geo_buf[GEOMETRY_PAYLOAD_LEN];
	int geo_len;
	int retval;
	static const u8 sync_payload[] = { 0x01, 0x00 };

	dev_info(&idev->intf->dev, "activating touchpad mode\n");

	/* Step 1: SET_IDLE on IF2 (expected STALL) */
	ingenic_send_set_idle(idev);

	/* Step 2: CDC init */
	retval = ingenic_cdc_init(idev);
	if (retval)
		dev_warn(&idev->intf->dev, "CDC init failed: %d\n", retval);

	/* Step 3: THE MODE TOGGLE - HID output report to IF2 */
	retval = ingenic_hid_set_report(idev, HID_MODE_TOGGLE_REPORT,
					HID_MODE_TOUCHPAD);
	if (retval) {
		dev_err(&idev->intf->dev,
			"HID mode toggle failed: %d\n", retval);
		return retval;
	}

	/* Step 4: MCU settle time */
	msleep(ACTIVATION_SETTLE_MS);

	/* Step 5: Initial sync */
	retval = ingenic_oskp_send(idev, OSKP_SYNC, sync_payload,
				   sizeof(sync_payload));
	if (retval)
		dev_warn(&idev->intf->dev, "initial sync failed: %d\n", retval);

	/* Step 6: Send geometry */
	geo_len = ingenic_build_geometry(idev, geo_buf, sizeof(geo_buf));
	if (geo_len > 0) {
		retval = ingenic_oskp_send(idev, OSKP_GEOMETRY,
					   geo_buf, geo_len);
		if (retval)
			dev_warn(&idev->intf->dev,
				 "geometry send failed: %d\n", retval);
	}

	/* Step 7: Start periodic sync */
	idev->mode = MODE_TOUCHPAD;
	idev->activated = true;
	ingenic_oskp_start_sync(idev);

	dev_info(&idev->intf->dev, "touchpad mode activated\n");
	return 0;
}

/*
 * Deactivate touchpad mode and return to touchscreen.
 */
int ingenic_touchpad_deactivate(struct ingenic_dev *idev)
{
	u8 zeros[GEOMETRY_PAYLOAD_LEN];
	int retval;

	dev_info(&idev->intf->dev, "deactivating touchpad mode\n");

	/* Stop sync workers */
	ingenic_oskp_stop_sync(idev);

	/* Clear geometry with all-zeros */
	memset(zeros, 0, sizeof(zeros));
	retval = ingenic_oskp_send(idev, OSKP_GEOMETRY, zeros, sizeof(zeros));
	if (retval)
		dev_warn(&idev->intf->dev, "clear geometry failed: %d\n", retval);

	/* Toggle back to touchscreen mode via HID */
	retval = ingenic_hid_set_report(idev, HID_MODE_TOGGLE_REPORT,
					HID_MODE_TOUCHSCREEN);
	if (retval)
		dev_warn(&idev->intf->dev,
			 "HID mode toggle back failed: %d\n", retval);

	idev->mode = MODE_TOUCHSCREEN;
	idev->activated = false;

	dev_info(&idev->intf->dev, "touchpad mode deactivated\n");
	return 0;
}
