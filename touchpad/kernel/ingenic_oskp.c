// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * ingenic_oskp.c - OSKP protocol engine for INGENIC MCU
 *
 * Handles building/parsing OSKP frames, sending commands over bulk EP,
 * parsing MCU responses, and managing periodic sync workers.
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

/*
 * Build an OSKP wire frame.
 * Returns frame length or negative error.
 * Caller must ensure buf is large enough (payload_len + OSKP_HEADER_LEN).
 */
static int oskp_build_frame(u8 type, const void *payload, u16 payload_len,
			    u8 *buf, u16 buf_len)
{
	u16 wire_len;

	if (buf_len < OSKP_HEADER_LEN + payload_len)
		return -ENOMEM;

	wire_len = payload_len + 1; /* +1 for type byte */

	buf[0] = 'O';
	buf[1] = 'S';
	buf[2] = 'K';
	buf[3] = 'P';
	buf[4] = wire_len & 0xFF;
	buf[5] = (wire_len >> 8) & 0xFF;
	buf[6] = type;

	if (payload && payload_len > 0)
		memcpy(&buf[7], payload, payload_len);

	return OSKP_HEADER_LEN + payload_len;
}

/*
 * Send an OSKP command via bulk OUT endpoint.
 */
int ingenic_oskp_send(struct ingenic_dev *idev, u8 type,
		      const void *payload, u16 payload_len)
{
	u8 *buf;
	int frame_len;
	int actual_len;
	int retval;

	buf = kzalloc(OSKP_HEADER_LEN + payload_len, GFP_KERNEL);
	if (!buf)
		return -ENOMEM;

	frame_len = oskp_build_frame(type, payload, payload_len, buf,
				     OSKP_HEADER_LEN + payload_len);
	if (frame_len < 0) {
		kfree(buf);
		return frame_len;
	}

	mutex_lock(&idev->oskp_mutex);

	retval = usb_bulk_msg(idev->udev,
			      usb_sndbulkpipe(idev->udev,
					      idev->ep_out->bEndpointAddress),
			      buf, frame_len, &actual_len,
			      USB_CTRL_SET_TIMEOUT);

	mutex_unlock(&idev->oskp_mutex);

	if (retval) {
		dev_err(&idev->intf->dev,
			"OSKP send type 0x%02x failed: %d\n", type, retval);
	} else if (actual_len != frame_len) {
		dev_warn(&idev->intf->dev,
			 "OSKP short write: %d/%d\n", actual_len, frame_len);
	}

	kfree(buf);
	return retval;
}

/*
 * Process incoming OSKP response from MCU.
 * Called from bulk IN URB callback.
 */
void ingenic_oskp_process_response(struct ingenic_dev *idev,
				   const u8 *data, int len)
{
	u16 wire_len;
	u8 type;
	const u8 *payload;
	u16 payload_len;

	/* Validate magic */
	if (len < OSKP_HEADER_LEN ||
	    data[0] != 'O' || data[1] != 'S' ||
	    data[2] != 'K' || data[3] != 'P') {
		dev_dbg(&idev->intf->dev,
			"invalid OSKP frame magic, len=%d\n", len);
		return;
	}

	wire_len = data[4] | (data[5] << 8);
	type = data[6];
	payload = &data[7];
	payload_len = wire_len > 0 ? wire_len - 1 : 0;

	/* Validate we have enough data */
	if (len < OSKP_HEADER_LEN + payload_len) {
		dev_warn(&idev->intf->dev,
			 "truncated OSKP frame: have %d, need %d\n",
			 len, OSKP_HEADER_LEN + payload_len);
		return;
	}

	switch (type) {
	case OSKP_RESP_FIRMWARE:
		if (payload_len > 0 && payload_len < sizeof(idev->firmware_version)) {
			memcpy(idev->firmware_version, payload, payload_len);
			idev->firmware_version[payload_len] = '\0';
			dev_info(&idev->intf->dev,
				 "firmware: %s\n", idev->firmware_version);
		}
		break;

	case OSKP_RESP_GEOMETRY_ACK:
		dev_dbg(&idev->intf->dev, "geometry ACK\n");
		break;

	case OSKP_RESP_POSITION:
		if (payload_len >= 6) {
			u16 pos = payload[4] | (payload[5] << 8);
			dev_dbg(&idev->intf->dev,
				"position report: %d\n", pos);
			idev->kb_position = pos & 0xFF;
		}
		break;

	default:
		dev_dbg(&idev->intf->dev,
			"unknown OSKP response type 0x%02x len %d\n",
			type, payload_len);
		break;
	}
}

/*
 * Periodic sync worker - sends 0x20 sync every 1 second.
 * This matches the Windows behavior and keeps the MCU alive.
 */
static void ingenic_sync_worker(struct work_struct *work)
{
	struct ingenic_dev *idev = container_of(work, struct ingenic_dev,
					       sync_work.work);
	static const u8 sync_payload[] = { 0x01, 0x00 };
	int retval;

	if (idev->suspended || !idev->activated)
		return;

	retval = ingenic_oskp_send(idev, OSKP_SYNC, sync_payload,
				   sizeof(sync_payload));
	if (retval)
		dev_dbg(&idev->intf->dev, "sync send failed: %d\n", retval);

	/* Reschedule */
	queue_delayed_work(idev->wq, &idev->sync_work,
			   msecs_to_jiffies(SYNC_INTERVAL_MS));
}

/*
 * Initialize OSKP subsystem.
 */
int ingenic_oskp_init(struct ingenic_dev *idev)
{
	idev->wq = create_singlethread_workqueue("ingenic_mcu");
	if (!idev->wq)
		return -ENOMEM;

	INIT_DELAYED_WORK(&idev->sync_work, ingenic_sync_worker);

	return 0;
}

/*
 * Clean up OSKP subsystem.
 */
void ingenic_oskp_cleanup(struct ingenic_dev *idev)
{
	cancel_delayed_work_sync(&idev->sync_work);
	if (idev->wq) {
		destroy_workqueue(idev->wq);
		idev->wq = NULL;
	}
}

/*
 * Start periodic sync.
 */
void ingenic_oskp_start_sync(struct ingenic_dev *idev)
{
	cancel_delayed_work_sync(&idev->sync_work);
	queue_delayed_work(idev->wq, &idev->sync_work,
			   msecs_to_jiffies(SYNC_INTERVAL_MS));
}

/*
 * Stop periodic sync.
 */
void ingenic_oskp_stop_sync(struct ingenic_dev *idev)
{
	cancel_delayed_work_sync(&idev->sync_work);
}

/*
 * Initialize CDC ACM serial line on IF0.
 * Sends SET_LINE_CODING and SET_CONTROL_LINE_STATE.
 * Uses 9600 baud 8N1 - higher baud rates may crash the MCU.
 */
int ingenic_cdc_init(struct ingenic_dev *idev)
{
	struct usb_device *udev = idev->udev;
	struct usb_interface *cdc_intf;
	unsigned char *buf;
	int retval;

	cdc_intf = idev->cdc_intf;
	if (!cdc_intf)
		return -ENODEV;

	buf = kmalloc(7, GFP_KERNEL);
	if (!buf)
		return -ENOMEM;

	/* SET_LINE_CODING: 9600 baud, 8 data bits, no parity, 1 stop bit */
	buf[0] = 0x80;  /* dwDTERate = 9600 = 0x2580 */
	buf[1] = 0x25;
	buf[2] = 0x00;
	buf[3] = 0x00;  /* bCharFormat: 1 stop bit */
	buf[4] = 0x00;  /* bParityType: none */
	buf[5] = 0x08;  /* bDataBits: 8 */

	retval = usb_control_msg(udev, usb_sndctrlpipe(udev, 0),
				 0x20, /* SET_LINE_CODING */
				 USB_TYPE_CLASS | USB_RECIP_INTERFACE,
				 0, cdc_intf->cur_altsetting->desc.bInterfaceNumber,
				 buf, 7, USB_CTRL_SET_TIMEOUT);
	if (retval < 0)
		dev_warn(&idev->intf->dev, "SET_LINE_CODING failed: %d\n",
			 retval);

	/* SET_CONTROL_LINE_STATE: DTR + RTS */
	retval = usb_control_msg(udev, usb_sndctrlpipe(udev, 0),
				 0x22, /* SET_CONTROL_LINE_STATE */
				 USB_TYPE_CLASS | USB_RECIP_INTERFACE,
				 0x03, /* DTR + RTS */
				 cdc_intf->cur_altsetting->desc.bInterfaceNumber,
				 NULL, 0, USB_CTRL_SET_TIMEOUT);
	if (retval < 0)
		dev_warn(&idev->intf->dev, "SET_CONTROL_LINE_STATE failed: %d\n",
			 retval);

	kfree(buf);
	return 0;
}
