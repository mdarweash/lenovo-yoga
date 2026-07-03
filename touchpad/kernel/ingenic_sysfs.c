// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * ingenic_sysfs.c - sysfs interface for INGENIC MCU driver
 *
 * Provides user-space control via /sys/bus/usb/drivers/ingenic_mcu/.../:
 *   touchpad_mode     (RW) - "touchscreen" or "touchpad"
 *   keyboard_state    (RO) - "attached", "detached", "unknown"
 *   firmware_version  (RO) - MCU firmware string
 *   screen_width      (RW) - coordinate space width (default 3017)
 *   screen_height     (RW) - coordinate space height (default 1700)
 *
 * Copyright (c) 2024 Muhammad Darweash
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/usb.h>
#include <linux/device.h>

#include "ingenic_mcu.h"

static struct ingenic_dev *to_idev(struct device *dev)
{
	struct usb_interface *intf = to_usb_interface(dev);
	return usb_get_intfdata(intf);
}

/* touchpad_mode: read */
static ssize_t touchpad_mode_show(struct device *dev,
				  struct device_attribute *attr, char *buf)
{
	struct ingenic_dev *idev = to_idev(dev);

	if (!idev)
		return -ENODEV;

	return sprintf(buf, "%s\n",
		       idev->mode == MODE_TOUCHPAD ? "touchpad" : "touchscreen");
}

/* touchpad_mode: write */
static ssize_t touchpad_mode_store(struct device *dev,
				   struct device_attribute *attr,
				   const char *buf, size_t count)
{
	struct ingenic_dev *idev = to_idev(dev);
	int retval;

	if (!idev)
		return -ENODEV;

	if (sysfs_streq(buf, "touchpad")) {
		if (idev->activated)
			return count;  /* Already in touchpad mode */
		retval = ingenic_touchpad_activate(idev);
		if (retval)
			return retval;
	} else if (sysfs_streq(buf, "touchscreen")) {
		if (!idev->activated)
			return count;  /* Already in touchscreen mode */
		retval = ingenic_touchpad_deactivate(idev);
		if (retval)
			return retval;
	} else {
		return -EINVAL;
	}

	return count;
}
static DEVICE_ATTR_RW(touchpad_mode);

/* keyboard_state: read */
static ssize_t keyboard_state_show(struct device *dev,
				   struct device_attribute *attr, char *buf)
{
	struct ingenic_dev *idev = to_idev(dev);
	const char *state;

	if (!idev)
		return -ENODEV;

	switch (idev->kb_state) {
	case KB_ATTACHED:
		state = "attached";
		break;
	case KB_DETACHED:
		state = "detached";
		break;
	default:
		state = "unknown";
		break;
	}

	return sprintf(buf, "%s\n", state);
}
static DEVICE_ATTR_RO(keyboard_state);

/* firmware_version: read */
static ssize_t firmware_version_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct ingenic_dev *idev = to_idev(dev);

	if (!idev)
		return -ENODEV;

	return sprintf(buf, "%s\n", idev->firmware_version);
}
static DEVICE_ATTR_RO(firmware_version);

/* screen_width: read */
static ssize_t screen_width_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	struct ingenic_dev *idev = to_idev(dev);

	if (!idev)
		return -ENODEV;

	return sprintf(buf, "%d\n", idev->screen_width);
}

/* screen_width: write */
static ssize_t screen_width_store(struct device *dev,
				  struct device_attribute *attr,
				  const char *buf, size_t count)
{
	struct ingenic_dev *idev = to_idev(dev);
	unsigned int val;
	int retval;

	if (!idev)
		return -ENODEV;

	retval = kstrtouint(buf, 10, &val);
	if (retval)
		return retval;

	if (val < 100 || val > 65535)
		return -EINVAL;

	idev->screen_width = val;
	return count;
}
static DEVICE_ATTR_RW(screen_width);

/* screen_height: read */
static ssize_t screen_height_show(struct device *dev,
				  struct device_attribute *attr, char *buf)
{
	struct ingenic_dev *idev = to_idev(dev);

	if (!idev)
		return -ENODEV;

	return sprintf(buf, "%d\n", idev->screen_height);
}

/* screen_height: write */
static ssize_t screen_height_store(struct device *dev,
				   struct device_attribute *attr,
				   const char *buf, size_t count)
{
	struct ingenic_dev *idev = to_idev(dev);
	unsigned int val;
	int retval;

	if (!idev)
		return -ENODEV;

	retval = kstrtouint(buf, 10, &val);
	if (retval)
		return retval;

	if (val < 100 || val > 65535)
		return -EINVAL;

	idev->screen_height = val;
	return count;
}
static DEVICE_ATTR_RW(screen_height);

static struct attribute *ingenic_attrs[] = {
	&dev_attr_touchpad_mode.attr,
	&dev_attr_keyboard_state.attr,
	&dev_attr_firmware_version.attr,
	&dev_attr_screen_width.attr,
	&dev_attr_screen_height.attr,
	NULL,
};
ATTRIBUTE_GROUPS(ingenic);

int ingenic_sysfs_create(struct ingenic_dev *idev)
{
	return sysfs_create_groups(&idev->intf->dev.kobj, ingenic_groups);
}

void ingenic_sysfs_remove(struct ingenic_dev *idev)
{
	sysfs_remove_groups(&idev->intf->dev.kobj, ingenic_groups);
}
