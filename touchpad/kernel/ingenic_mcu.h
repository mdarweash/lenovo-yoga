/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * ingenic_mcu.h - Shared definitions for INGENIC MCU driver
 *                 for Lenovo Yoga Book 9 dual-screen mode control
 *
 * Copyright (c) 2024 Muhammad Darweash
 */

#ifndef _INGENIC_MCU_H
#define _INGENIC_MCU_H

#include <linux/usb.h>
#include <linux/workqueue.h>
#include <linux/mutex.h>
#include <linux/completion.h>

/* USB device identification */
#define INGENIC_VENDOR_ID	0x17ef
#define INGENIC_PRODUCT_ID	0x6161

/* Interface indices */
#define INGENIC_IF_CDC		0  /* CDC ACM - serial control */
#define INGENIC_IF_OSKP		1  /* Vendor 0xFF - OSKP bulk transport */
#define INGENIC_IF_KEYBOARD	2  /* HID Boot Keyboard */
#define INGENIC_IF_MULTITOUCH	3  /* HID Multitouch */

/* OSKP protocol constants */
#define OSKP_MAGIC		"OSKP"
#define OSKP_MAGIC_LEN		4
#define OSKP_HEADER_LEN		7  /* magic(4) + length(2) + type(1) */

/* OSKP command types */
#define OSKP_SYNC		0x20  /* Heartbeat/sync - Windows uses this every 1s */
#define OSKP_MODE_TOGGLE	0x25  /* Mode toggle (decompiled, not in captures) */
#define OSKP_KEEPALIVE		0x26  /* Keepalive with timestamp (Linux-only) */
#define OSKP_GEOMETRY		0x31  /* Set/clear touchpad geometry rectangles */

/* OSKP response types */
#define OSKP_RESP_FIRMWARE	0x50  /* Firmware version string */
#define OSKP_RESP_GEOMETRY_ACK	0x75  /* Geometry acknowledge */
#define OSKP_RESP_POSITION	0xa2  /* Position/gesture event */

/* HID mode toggle - THE critical command */
#define HID_MODE_TOGGLE_REPORT	0x20
#define HID_MODE_TOUCHPAD	0x00
#define HID_MODE_TOUCHSCREEN	0x01

/* Timing constants */
#define SYNC_INTERVAL_MS	1000   /* 0x20 sync every 1s (Windows behavior) */
#define ACTIVATION_SETTLE_MS	350    /* MCU settle time after HID toggle */

/* Coordinate space defaults */
#define DEFAULT_SCREEN_WIDTH	3017
#define DEFAULT_SCREEN_HEIGHT	1700

/* Geometry constants (from Windows TouchPadMainWindow.xml) */
#define GEOM_CAPTION_FRAC	80   /* caption_h = height * 80/500 */
#define GEOM_BTN_FRAC		90   /* btn_h = height * 90/500 */
#define GEOM_SIDE_FRAC		40   /* side margin = width * 40/500 */
#define GEOM_BOTTOM_FRAC	40   /* bottom margin = height * 40/500 */
#define GEOM_GAP_FRAC		10   /* gap = width * 10/500 */
#define GEOM_DENOM		500

#define GEOMETRY_PAYLOAD_LEN	41

/* CDC ACM line coding for 9600 8N1 */
#define CDC_BAUD_9600		9600
#define CDC_BAUD_115200		115200

/* Touchpad mode states */
enum ingenic_mode {
	MODE_TOUCHSCREEN = 0,
	MODE_TOUCHPAD,
};

/* Keyboard state */
enum ingenic_kb_state {
	KB_UNKNOWN = 0,
	KB_ATTACHED,
	KB_DETACHED,
};

/* Per-device context */
struct ingenic_dev {
	struct usb_device	*udev;
	struct usb_interface	*intf;		/* Our matched interface (IF1) */
	struct usb_interface	*cdc_intf;	/* IF0 - claimed by us */

	/* Endpoints on IF1 */
	struct usb_endpoint_descriptor *ep_out;  /* EP 0x01 OUT */
	struct usb_endpoint_descriptor *ep_in;   /* EP 0x81 IN */

	/* URBs */
	struct urb		*bulk_in_urb;
	unsigned char		*bulk_in_buf;
	dma_addr_t		bulk_in_dma;
	size_t			bulk_in_size;

	/* OSKP state */
	struct mutex		oskp_mutex;	/* Serialize OSKP sends */
	struct delayed_work	sync_work;	/* Periodic 0x20 sync */
	struct workqueue_struct *wq;

	/* Device state */
	enum ingenic_mode	mode;
	enum ingenic_kb_state	kb_state;
	bool			activated;	/* True if touchpad mode is active */
	bool			suspended;

	/* Firmware info */
	char			firmware_version[128];

	/* Geometry */
	u16			screen_width;
	u16			screen_height;

	/* Keyboard position (0 or 1) */
	u8			kb_position;
};

/* Main driver functions */
int ingenic_probe(struct usb_interface *intf, const struct usb_device_id *id);
void ingenic_disconnect(struct usb_interface *intf);
int ingenic_suspend(struct usb_interface *intf, pm_message_t message);
int ingenic_resume(struct usb_interface *intf);
int ingenic_reset_resume(struct usb_interface *intf);

/* OSKP functions (ingenic_oskp.c) */
int ingenic_oskp_init(struct ingenic_dev *idev);
void ingenic_oskp_cleanup(struct ingenic_dev *idev);
int ingenic_oskp_send(struct ingenic_dev *idev, u8 type, const void *payload, u16 payload_len);
void ingenic_oskp_process_response(struct ingenic_dev *idev, const u8 *data, int len);
void ingenic_oskp_start_sync(struct ingenic_dev *idev);
void ingenic_oskp_stop_sync(struct ingenic_dev *idev);
int ingenic_cdc_init(struct ingenic_dev *idev);

/* Touchpad functions (ingenic_touchpad.c) */
int ingenic_touchpad_activate(struct ingenic_dev *idev);
int ingenic_touchpad_deactivate(struct ingenic_dev *idev);
int ingenic_build_geometry(struct ingenic_dev *idev, u8 *buf, u16 buf_len);

/* Sysfs functions (ingenic_sysfs.c) */
int ingenic_sysfs_create(struct ingenic_dev *idev);
void ingenic_sysfs_remove(struct ingenic_dev *idev);

#endif /* _INGENIC_MCU_H */
