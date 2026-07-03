// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * ingenic_main.c - Linux kernel driver for INGENIC MCU on Lenovo Yoga Book 9
 *
 * Controls dual-screen mode switching (touchscreen/touchpad) via USB HID
 * and OSKP protocol. Matches on Interface 1 (Vendor Specific 0xFF).
 *
 * Copyright (c) 2024 Muhammad Darweash
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/usb.h>
#include <linux/version.h>

#include "ingenic_mcu.h"

MODULE_AUTHOR("Muhammad Darweash");
MODULE_DESCRIPTION("INGENIC MCU driver for Lenovo Yoga Book 9 touchpad mode");
MODULE_LICENSE("GPL");

/* Forward declaration needed for usb_driver_claim_interface in probe */
struct usb_driver ingenic_usb_driver;

/* USB ID table - match on Interface 1 (Vendor Specific 0xFF) */
static struct usb_device_id ingenic_id_table[] = {
	{ USB_DEVICE_AND_INTERFACE_INFO(INGENIC_VENDOR_ID, INGENIC_PRODUCT_ID,
					0xFF, 0x01, 0x01) },
	{ },
};
MODULE_DEVICE_TABLE(usb, ingenic_id_table);

static void ingenic_bulk_in_callback(struct urb *urb)
{
	struct ingenic_dev *idev = urb->context;
	int status = urb->status;
	int retval;

	switch (status) {
	case 0:
		/* Success - process the OSKP response */
		if (urb->actual_length >= OSKP_HEADER_LEN) {
			ingenic_oskp_process_response(idev, urb->transfer_buffer,
						      urb->actual_length);
		}
		break;
	case -ENOENT:
	case -ESHUTDOWN:
	case -ECONNRESET:
		/* URB was killed - don't resubmit */
		return;
	case -EPIPE:
		dev_warn(&idev->intf->dev, "bulk IN stall, clearing\n");
		usb_clear_halt(idev->udev, usb_rcvbulkpipe(idev->udev,
				idev->ep_in->bEndpointAddress));
		break;
	default:
		dev_dbg(&idev->intf->dev, "bulk IN status %d\n", status);
		break;
	}

	/* Resubmit the URB to keep reading */
	usb_fill_bulk_urb(idev->bulk_in_urb, idev->udev,
			  usb_rcvbulkpipe(idev->udev,
					  idev->ep_in->bEndpointAddress),
			  idev->bulk_in_buf, idev->bulk_in_size,
			  ingenic_bulk_in_callback, idev);
	retval = usb_submit_urb(idev->bulk_in_urb, GFP_ATOMIC);
	if (retval && retval != -ENODEV)
		dev_err(&idev->intf->dev, "bulk IN resubmit failed: %d\n",
			retval);
}

static int ingenic_find_endpoints(struct ingenic_dev *idev)
{
	struct usb_host_interface *alts;
	struct usb_endpoint_descriptor *ep;
	int i;

	alts = idev->intf->cur_altsetting;

	if (alts->desc.bNumEndpoints < 2) {
		dev_err(&idev->intf->dev, "not enough endpoints on IF1\n");
		return -ENODEV;
	}

	for (i = 0; i < alts->desc.bNumEndpoints; i++) {
		ep = &alts->endpoint[i].desc;

		if (usb_endpoint_is_bulk_out(ep)) {
			idev->ep_out = ep;
			dev_dbg(&idev->intf->dev, "found bulk OUT EP 0x%02x\n",
				ep->bEndpointAddress);
		} else if (usb_endpoint_is_bulk_in(ep)) {
			idev->ep_in = ep;
			dev_dbg(&idev->intf->dev, "found bulk IN EP 0x%02x\n",
				ep->bEndpointAddress);
		}
	}

	if (!idev->ep_out || !idev->ep_in) {
		dev_err(&idev->intf->dev, "missing required bulk endpoints\n");
		return -ENODEV;
	}

	return 0;
}

static int ingenic_alloc_urbs(struct ingenic_dev *idev)
{
	idev->bulk_in_size = usb_endpoint_maxp(idev->ep_in);
	idev->bulk_in_urb = usb_alloc_urb(0, GFP_KERNEL);
	if (!idev->bulk_in_urb)
		return -ENOMEM;

	idev->bulk_in_buf = usb_alloc_coherent(idev->udev, idev->bulk_in_size,
					       GFP_KERNEL, &idev->bulk_in_dma);
	if (!idev->bulk_in_buf) {
		usb_free_urb(idev->bulk_in_urb);
		idev->bulk_in_urb = NULL;
		return -ENOMEM;
	}

	return 0;
}

static void ingenic_free_urbs(struct ingenic_dev *idev)
{
	if (idev->bulk_in_urb) {
		usb_kill_urb(idev->bulk_in_urb);
		usb_free_urb(idev->bulk_in_urb);
		idev->bulk_in_urb = NULL;
	}
	if (idev->bulk_in_buf) {
		usb_free_coherent(idev->udev, idev->bulk_in_size,
				  idev->bulk_in_buf, idev->bulk_in_dma);
		idev->bulk_in_buf = NULL;
	}
}

static int ingenic_start_bulk_in(struct ingenic_dev *idev)
{
	usb_fill_bulk_urb(idev->bulk_in_urb, idev->udev,
			  usb_rcvbulkpipe(idev->udev,
					  idev->ep_in->bEndpointAddress),
			  idev->bulk_in_buf, idev->bulk_in_size,
			  ingenic_bulk_in_callback, idev);
	idev->bulk_in_urb->transfer_dma = idev->bulk_in_dma;
	idev->bulk_in_urb->transfer_flags |= URB_NO_TRANSFER_DMA_MAP;

	return usb_submit_urb(idev->bulk_in_urb, GFP_KERNEL);
}

int ingenic_probe(struct usb_interface *intf, const struct usb_device_id *id)
{
	struct usb_device *udev = interface_to_usbdev(intf);
	struct ingenic_dev *idev;
	struct usb_interface *cdc_intf;
	int retval;

	dev_info(&intf->dev, "INGENIC MCU probe (interface %d)\n",
		 intf->cur_altsetting->desc.bInterfaceNumber);

	idev = kzalloc(sizeof(*idev), GFP_KERNEL);
	if (!idev)
		return -ENOMEM;

	idev->udev = udev;
	idev->intf = intf;
	idev->mode = MODE_TOUCHSCREEN;
	idev->kb_state = KB_UNKNOWN;
	idev->screen_width = DEFAULT_SCREEN_WIDTH;
	idev->screen_height = DEFAULT_SCREEN_HEIGHT;
	mutex_init(&idev->oskp_mutex);

	usb_set_intfdata(intf, idev);

	/* Find IF1 bulk endpoints */
	retval = ingenic_find_endpoints(idev);
	if (retval)
		goto err_free;

	/* Claim IF0 (CDC ACM) to prevent broken cdc_acm from binding */
	cdc_intf = usb_ifnum_to_if(udev, INGENIC_IF_CDC);
	if (cdc_intf) {
		retval = usb_driver_claim_interface(&ingenic_usb_driver,
						    cdc_intf, idev);
		if (retval) {
			dev_warn(&intf->dev,
				 "failed to claim CDC IF0: %d\n", retval);
			/* Non-fatal - we can still work without it */
		} else {
			idev->cdc_intf = cdc_intf;
			dev_info(&intf->dev, "claimed CDC interface 0\n");
		}
	}

	/* Allocate URBs */
	retval = ingenic_alloc_urbs(idev);
	if (retval)
		goto err_release_cdc;

	/* Initialize OSKP subsystem */
	retval = ingenic_oskp_init(idev);
	if (retval)
		goto err_free_urbs;

	/* Initialize CDC serial line */
	retval = ingenic_cdc_init(idev);
	if (retval)
		dev_warn(&intf->dev, "CDC init failed (non-fatal): %d\n",
			 retval);

	/* Start reading MCU responses */
	retval = ingenic_start_bulk_in(idev);
	if (retval)
		goto err_oskp_cleanup;

	/* Create sysfs attributes */
	retval = ingenic_sysfs_create(idev);
	if (retval)
		goto err_oskp_cleanup;

	dev_info(&intf->dev, "INGENIC MCU driver initialized\n");
	return 0;

err_oskp_cleanup:
	ingenic_oskp_cleanup(idev);
err_free_urbs:
	ingenic_free_urbs(idev);
err_release_cdc:
	if (idev->cdc_intf)
		usb_driver_release_interface(&ingenic_usb_driver,
					     idev->cdc_intf);
err_free:
	usb_set_intfdata(intf, NULL);
	kfree(idev);
	return retval;
}

void ingenic_disconnect(struct usb_interface *intf)
{
	struct ingenic_dev *idev = usb_get_intfdata(intf);

	if (!idev)
		return;

	dev_info(&intf->dev, "INGENIC MCU disconnect\n");

	/* Deactivate touchpad if active */
	if (idev->activated)
		ingenic_touchpad_deactivate(idev);

	ingenic_sysfs_remove(idev);
	ingenic_oskp_cleanup(idev);
	ingenic_free_urbs(idev);

	if (idev->cdc_intf)
		usb_driver_release_interface(&ingenic_usb_driver,
					     idev->cdc_intf);

	usb_set_intfdata(intf, NULL);
	kfree(idev);
}

int ingenic_suspend(struct usb_interface *intf, pm_message_t message)
{
	struct ingenic_dev *idev = usb_get_intfdata(intf);

	if (!idev)
		return 0;

	dev_info(&intf->dev, "suspending\n");

	idev->suspended = true;
	ingenic_oskp_stop_sync(idev);
	usb_kill_urb(idev->bulk_in_urb);

	if (idev->activated)
		ingenic_oskp_send(idev, OSKP_GEOMETRY, NULL, 0);

	return 0;
}

int ingenic_resume(struct usb_interface *intf)
{
	struct ingenic_dev *idev = usb_get_intfdata(intf);

	if (!idev)
		return 0;

	dev_info(&intf->dev, "resuming\n");

	idev->suspended = false;

	ingenic_cdc_init(idev);
	ingenic_start_bulk_in(idev);

	if (idev->activated) {
		ingenic_touchpad_activate(idev);
	}

	return 0;
}

int ingenic_reset_resume(struct usb_interface *intf)
{
	return ingenic_resume(intf);
}

/* USB driver definition */
struct usb_driver ingenic_usb_driver = {
	.name		= "ingenic_mcu",
	.id_table	= ingenic_id_table,
	.probe		= ingenic_probe,
	.disconnect	= ingenic_disconnect,
	.suspend	= ingenic_suspend,
	.resume		= ingenic_resume,
	.reset_resume	= ingenic_reset_resume,
};

module_usb_driver(ingenic_usb_driver);
