/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * ingenic_oskp.h - OSKP protocol definitions for INGENIC MCU
 */

#ifndef _INGENIC_OSKP_H
#define _INGENIC_OSKP_H

#include "ingenic_mcu.h"

/* OSKP wire frame header */
struct oskp_header {
	u8	magic[4];	/* "OSKP" */
	__le16	wire_len;	/* payload_len + 1 (for type byte) */
	u8	type;		/* command/response type */
	/* payload follows */
} __packed;

/* OSKP geometry payload (41 bytes) */
struct oskp_geometry {
	u8	frame_rect[8];		/* left, top, right, bottom LE16 */
	u8	lbutton_rect[8];
	u8	rbutton_rect[8];
	u8	touchable1_rect[8];
	u8	packed_flags;
	u8	touchable2_rect[8];
} __packed;

#endif /* _INGENIC_OSKP_H */
