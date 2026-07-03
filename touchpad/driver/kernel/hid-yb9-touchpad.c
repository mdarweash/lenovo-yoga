// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * hid-yb9-touchpad — Yoga Book 9 INGENIC touchpad driver
 *
 * Activates the virtual touchpad on the Yoga Book 9 bottom screen by
 * replicating the exact Windows activation sequence from USB captures.
 *
 * The MCU (17ef:6161) has 7 USB interfaces. This driver:
 *   1. Claims IF1 (Vendor Specific) for OSKP bulk communication
 *   2. Sends HID output report [0x20, 0x00] to IF2 EP 0x02 to toggle mode
 *   3. Sends OSKP 0x20 sync every 1s to keep touchpad alive
 *   4. Sends OSKP 0x31 geometry to configure touchpad area
 *   5. Touch data arrives as standard HID on IF3 (handled by hid-multitouch)
 *
 * Based on USB captures from 2026-05-16 (capture-analysis.md).
 */

#include <linux/module.h>
#include <linux/usb.h>
#include <linux/input.h>
#include <linux/workqueue.h>
#include <linux/mutex.h>
#include <linux/slab.h>

MODULE_AUTHOR("Mahmoud Darweash");
MODULE_DESCRIPTION("Yoga Book 9 INGENIC touchpad driver");
MODULE_LICENSE("GPL");

#define DRIVER_NAME "yb9_touchpad"

/* USB device */
#define YB9_VID		0x17ef
#define YB9_PID		0x6161

/* USB interfaces */
#define IF_OSKP		1	/* Vendor Specific — OSKP bulk */
#define IF_HID_KBD	2	/* HID Keyboard — mode toggle EP 0x02 */

/* Endpoints */
#define EP_OSKP_OUT	0x01
#define EP_OSKP_IN	0x81
#define EP_HID_OUT	0x02	/* HID Interrupt OUT — mode toggle */

/* OSKP frame constants */
#define OSKP_MAGIC	"OSKP"
#define OSKP_HDR_SIZE	6	/* 4 magic + 2 len */
#define OSKP_SYNC_TYPE	0x20
#define OSKP_GEO_TYPE	0x31
#define OSKP_ACK_TYPE	0x75

/* Timing */
#define SYNC_INTERVAL_MS	1000
#define ACTIVATE_DELAY_MS	350

/* Touchpad geometry: 3017 x 1700 (1/10 of touchscreen resolution) */
#define TP_WIDTH	3017
#define TP_HEIGHT	1700
#define GEO_PAYLOAD_LEN	41

/* ── OSKP frame builder ────────────────────────────────────────────── */

struct oskp_frame {
	u8 magic[4];	/* "OSKP" */
	__le16 wire_len; /* payload_len + 1 (type byte) */
	u8 type;
	u8 payload[];
} __packed;

static int oskp_send(struct usb_device *udev, int ep_out,
		     u8 type, const void *payload, size_t payload_len)
{
	struct oskp_frame *frame;
	size_t frame_len = OSKP_HDR_SIZE + 1 + payload_len;
	int actual, ret;

	frame = kmalloc(frame_len, GFP_KERNEL);
	if (!frame)
		return -ENOMEM;

	memcpy(frame->magic, OSKP_MAGIC, 4);
	frame->wire_len = cpu_to_le16(payload_len + 1);
	frame->type = type;
	if (payload && payload_len > 0)
		memcpy(frame->payload, payload, payload_len);

	ret = usb_bulk_msg(udev, usb_sndbulkpipe(udev, ep_out),
			   frame, frame_len, &actual, 5000);
	kfree(frame);
	return ret;
}

static int oskp_send_sync(struct usb_device *udev, int ep_out)
{
	u8 payload[] = { 0x01, 0x00 };
	return oskp_send(udev, ep_out, OSKP_SYNC_TYPE, payload, sizeof(payload));
}

/* ── Touchpad geometry ──────────────────────────────────────────────── */

static void pack_rect(u8 *buf, u16 l, u16 t, u16 r, u16 b)
{
	__le16 *p = (__le16 *)buf;
	p[0] = cpu_to_le16(l);
	p[1] = cpu_to_le16(t);
	p[2] = cpu_to_le16(r);
	p[3] = cpu_to_le16(b);
}

static void build_geometry(u8 *buf)
{
	u16 w = TP_WIDTH, h = TP_HEIGHT;
	u16 cap_h, btn_h, sm, bm, gap, half_btn;

	/* Layout derived from Windows TouchPadMainWindow.xml */
	cap_h = h * 80 / 500;
	btn_h = h * 90 / 500;
	sm = w * 40 / 500;
	bm = h * 40 / 500;
	gap = w * 10 / 500;
	half_btn = (w - 2 * sm - gap) / 2;

	pack_rect(buf + 0,  0, 0, w, h);					/* frameRect */
	pack_rect(buf + 8,  sm, h - bm - btn_h, sm + half_btn, h - bm);	/* LButton */
	pack_rect(buf + 16, sm + half_btn + gap, h - bm - btn_h, w - sm, h - bm); /* RButton */
	pack_rect(buf + 24, sm, cap_h, w - sm, h - bm - btn_h);		/* touchableRect1 */
	buf[32] = 0x00;								/* flags */
	pack_rect(buf + 33, 0, 0, 0, 0);					/* touchableRect2 */
}

/* ── Driver state ──────────────────────────────────────────────────── */

struct yb9_data {
	struct usb_device	*udev;
	struct usb_interface	*if_oskp;	/* IF1 */
	struct input_dev	*input;

	struct delayed_work	sync_work;
	struct work_struct	activate_work;
	struct work_struct	deactivate_work;

	u8			geo[GEO_PAYLOAD_LEN];
	bool			active;

	struct mutex		lock;
};

/* ── Sync keepalive ────────────────────────────────────────────────── */

static void yb9_sync_work(struct work_struct *work)
{
	struct yb9_data *data = container_of(to_delayed_work(work),
					     struct yb9_data, sync_work);

	if (!data->active)
		return;

	if (oskp_send_sync(data->udev, EP_OSKP_OUT)) {
		dev_dbg(&data->if_oskp->dev, "sync send failed\n");
		return;
	}

	schedule_delayed_work(&data->sync_work,
			      msecs_to_jiffies(SYNC_INTERVAL_MS));
}

/* ── Activation ────────────────────────────────────────────────────── */

static void yb9_activate_work(struct work_struct *work)
{
	struct yb9_data *data = container_of(work, struct yb9_data,
					     activate_work);
	struct usb_device *udev = data->udev;
	int ret;

	dev_info(&data->if_oskp->dev, "activating touchpad\n");

	/* Step 1: HID output report [0x20, 0x00] to IF2 EP 0x02.
	 * This is THE mode toggle from Windows Capture 10 Frame 164.
	 * The MCU immediately switches to touchpad mode.
	 */
	{
		u8 hid_report[] = { 0x20, 0x00 };
		int actual;

		/* Send via interrupt OUT transfer (not SET_REPORT control) */
		ret = usb_interrupt_msg(udev,
					usb_sndintpipe(udev, EP_HID_OUT),
					hid_report, sizeof(hid_report),
					&actual, 5000);
		if (ret) {
			dev_warn(&data->if_oskp->dev,
				 "HID toggle via EP 0x02 failed (%d), trying SET_REPORT\n",
				 ret);
			/* Fallback: SET_REPORT control transfer */
			ret = usb_control_msg(udev,
					      usb_sndctrlpipe(udev, 0),
					      0x09,	/* HID SET_REPORT */
					      0x21,	/* Host-to-device, class, interface */
					      0x0200,	/* feature report ID 2 */
					      IF_HID_KBD,
					      hid_report, sizeof(hid_report),
					      5000);
			if (ret < 0) {
				dev_err(&data->if_oskp->dev,
					"SET_REPORT also failed (%d)\n", ret);
				return;
			}
		}
		dev_info(&data->if_oskp->dev,
			 "HID [0x20,0x00] toggle sent OK\n");
	}

	/* Wait for MCU to process and start flooding EP 0x84 */
	msleep(ACTIVATE_DELAY_MS);

	/* Step 2: OSKP 0x20 sync on IF1 EP 0x01 (Capture 10 Frame 253) */
	ret = oskp_send_sync(udev, EP_OSKP_OUT);
	if (ret)
		dev_warn(&data->if_oskp->dev, "initial sync failed (%d)\n", ret);

	msleep(50);

	/* Step 3: OSKP 0x31 geometry on IF1 EP 0x01 (Capture 10 Frame 257) */
	ret = oskp_send(udev, EP_OSKP_OUT, OSKP_GEO_TYPE,
			data->geo, GEO_PAYLOAD_LEN);
	if (ret)
		dev_warn(&data->if_oskp->dev, "geometry send failed (%d)\n", ret);

	mutex_lock(&data->lock);
	data->active = true;
	mutex_unlock(&data->lock);

	/* Start periodic 0x20 sync keepalive */
	schedule_delayed_work(&data->sync_work,
			      msecs_to_jiffies(SYNC_INTERVAL_MS));

	dev_info(&data->if_oskp->dev, "touchpad activated\n");
}

/* ── Deactivation ──────────────────────────────────────────────────── */

static void yb9_deactivate_work(struct work_struct *work)
{
	struct yb9_data *data = container_of(work, struct yb9_data,
					     deactivate_work);

	mutex_lock(&data->lock);
	data->active = false;
	mutex_unlock(&data->lock);

	cancel_delayed_work_sync(&data->sync_work);

	/* Windows deactivation (Capture 06): send 0x31 all-zeros */
	oskp_send(data->udev, EP_OSKP_OUT, OSKP_GEO_TYPE,
		  data->geo, GEO_PAYLOAD_LEN);
	memset(data->geo, 0, GEO_PAYLOAD_LEN);
	oskp_send(data->udev, EP_OSKP_OUT, OSKP_GEO_TYPE,
		  data->geo, GEO_PAYLOAD_LEN);

	dev_info(&data->if_oskp->dev, "touchpad deactivated\n");
}

/* ── Sysfs interface ───────────────────────────────────────────────── */

static ssize_t activate_show(struct device *dev,
			     struct device_attribute *attr, char *buf)
{
	struct usb_interface *intf = to_usb_interface(dev);
	struct yb9_data *data = usb_get_intfdata(intf);

	return sysfs_emit(buf, "%d\n", data->active);
}

static ssize_t activate_store(struct device *dev,
			      struct device_attribute *attr,
			      const char *buf, size_t count)
{
	struct usb_interface *intf = to_usb_interface(dev);
	struct yb9_data *data = usb_get_intfdata(intf);
	bool val;
	int ret;

	ret = kstrtobool(buf, &val);
	if (ret)
		return ret;

	if (val && !data->active)
		schedule_work(&data->activate_work);
	else if (!val && data->active)
		schedule_work(&data->deactivate_work);

	return count;
}
static DEVICE_ATTR_RW(activate);

static struct attribute *yb9_attrs[] = {
	&dev_attr_activate.attr,
	NULL,
};
ATTRIBUTE_GROUPS(yb9);

/* ── USB driver ────────────────────────────────────────────────────── */

static const struct usb_device_id yb9_id_table[] = {
	{ USB_DEVICE(YB9_VID, YB9_PID) },
	{ },
};
MODULE_DEVICE_TABLE(usb, yb9_id_table);

static int yb9_probe(struct usb_interface *intf,
		     const struct usb_device_id *id)
{
	struct usb_device *udev = interface_to_usbdev(intf);
	struct yb9_data *data;

	/* Only bind to interface 1 (Vendor Specific / OSKP bulk) */
	if (intf->altsetting->desc.bInterfaceNumber != IF_OSKP)
		return -ENODEV;

	dev_info(&intf->dev,
		 "Yoga Book 9 INGENIC touchpad driver binding to IF%d\n",
		 intf->altsetting->desc.bInterfaceNumber);

	data = devm_kzalloc(&intf->dev, sizeof(*data), GFP_KERNEL);
	if (!data)
		return -ENOMEM;

	data->udev = usb_get_dev(udev);
	data->if_oskp = intf;
	data->active = false;
	mutex_init(&data->lock);

	INIT_DELAYED_WORK(&data->sync_work, yb9_sync_work);
	INIT_WORK(&data->activate_work, yb9_activate_work);
	INIT_WORK(&data->deactivate_work, yb9_deactivate_work);

	build_geometry(data->geo);

	usb_set_intfdata(intf, data);

	/* Auto-activate on probe */
	schedule_work(&data->activate_work);

	dev_info(&intf->dev, "driver loaded, touchpad will activate\n");
	return 0;
}

static void yb9_disconnect(struct usb_interface *intf)
{
	struct yb9_data *data = usb_get_intfdata(intf);

	if (!data)
		return;

	cancel_work_sync(&data->activate_work);
	cancel_work_sync(&data->deactivate_work);
	cancel_delayed_work_sync(&data->sync_work);

	mutex_lock(&data->lock);
	data->active = false;
	mutex_unlock(&data->lock);

	usb_set_intfdata(intf, NULL);
	usb_put_dev(data->udev);

	dev_info(&intf->dev, "driver unloaded\n");
}

static int yb9_suspend(struct usb_interface *intf, pm_message_t message)
{
	struct yb9_data *data = usb_get_intfdata(intf);

	cancel_delayed_work_sync(&data->sync_work);
	return 0;
}

static int yb9_resume(struct usb_interface *intf)
{
	struct yb9_data *data = usb_get_intfdata(intf);

	if (data->active)
		schedule_work(&data->activate_work);
	return 0;
}

static struct usb_driver yb9_driver = {
	.name		= DRIVER_NAME,
	.id_table	= yb9_id_table,
	.probe		= yb9_probe,
	.disconnect	= yb9_disconnect,
	.suspend	= yb9_suspend,
	.resume		= yb9_resume,
	.dev_groups	= yb9_groups,
	.supports_autosuspend = 0,
};

module_usb_driver(yb9_driver);
