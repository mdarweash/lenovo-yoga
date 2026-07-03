#!/usr/bin/env python3
"""
analyze_usbmon.py — Parse a usbmon pcap (Linux USB capture) and extract the
INGENIC 17ef:6161 host->device and device->host URB sequence.

usbmon produces LINKTYPE_USB_LINUX_MMAP (pcap DLT 220) packets: a 64-byte
header followed by the URB setup (for control transfers) and/or data.

Usage:
    python3 analyze_usbmon.py capture.pcap [--target-dev 13]
        [--around EVENT]   # 'activation', 'all', or seconds offset

No external deps — pure stdlib.
"""
import struct
import sys
import argparse
from collections import Counter

# pcap global header
PCAP_MAGIC = 0xa1b2c312
PCAP_MAGIC_NS = 0xa1b23c4a

# usbmon header (struct usbmon_packet), 64 bytes, all big-endian in the wire
# fields per Documentation/usb/usbmon.rst
URB_HDR_FMT = ">llLLLLLLHBBLLLLLLL"
# id(4) bus-ehci? type(1, 'S'/'C'/'E') xfer_type(1) ep(1) devnum(1)
# busnum(2) flag_setup(1) flag_data(1) ts_sec(4) ts_usec(4) status(4)
# length(4) len(4) ... variations exist. Use the documented layout below.

# The exact layout used by Linux (from usbmon binary API, struct mon_bin_hdr):
#  char id_type;           // 0  'S'/'C'/'E'/'F'/'X'  -- but actually: u64 id first
# Real layout (struct mon_bin_hdr in drivers/usb/mon/mon_bin.c):
#   u64 id;             0   (host order — but on x86 little-endian)
#   unsigned char type; 8
#   unsigned char xfer_type; 9
#   unsigned char epnum;     10
#   unsigned char devnum;    11
#   short busnum;            12 (16-bit)
#   char flag_setup;         14
#   char flag_data;          15
#   int64_t ts_sec;          16
#   int64_t ts_usec;         24
#   int32_t status;          32
#   unsigned int length;     36
#   unsigned int len_cap;    40
#   union { setup[8]; ... }; 44..51
#   ... padding to 64
# All fields are LITTLE-ENDIAN on x86.

# struct mon_bin_hdr (drivers/usb/mon/mon_bin.c), all little-endian on x86:
#   u64 id; u8 type; u8 xfer_type; u8 epnum; u8 devnum;
#   u16 busnum; s8 flag_setup; s8 flag_data;
#   s64 ts_sec; s64 ts_usec; s32 status; u32 length; u32 len_cap;
#   u8 setup[8];
# The kernel pads the whole header to a fixed 64 bytes (MON_BIN_HDR_SZ);
# the captured URB data ALWAYS starts at offset 64.
USB_HDR_FMT = "<QBBBBHbbqqiII8s"
USB_HDR_SIZE = 64  # fixed by the kernel, regardless of struct size

XFER_ISO, XFER_INT, XFER_CTRL, XFER_BULK = 0, 1, 2, 3


def read_pcap(path):
    """Yield (ts, linktype, raw_packet) tuples."""
    with open(path, "rb") as f:
        gh = f.read(24)
        if len(gh) < 24:
            raise SystemExit("not a pcap file")
        magic, vmaj, vmin, _, _, snaplen, linktype = struct.unpack("<LHHLLLL", gh)
        if magic == PCAP_MAGIC:
            scale = 1_000_000
        elif magic == PCAP_MAGIC_NS:
            scale = 1_000_000_000
        else:
            raise SystemExit(f"unknown pcap magic {magic:#x}")
        rec_hdr_len = 16
        while True:
            rh = f.read(rec_hdr_len)
            if len(rh) < rec_hdr_len:
                break
            ts_sec, ts_frac, incl_len, orig_len = struct.unpack("<LLLL", rh)
            data = f.read(incl_len)
            if len(data) < incl_len:
                break
            ts = ts_sec + ts_frac / scale
            yield ts, linktype, data


def parse_usbmon(pkt, linktype):
    """Parse one usbmon packet. Return dict or None."""
    if linktype not in (220, 189):  # 220 = USB_LINUX_MMAP, 189 = USB_LINUX
        return None
    # MMAP variant has the same mon_bin_hdr layout; the per-URB data follows.
    if len(pkt) < USB_HDR_SIZE:
        return None
    (id_, type_, xfer, epnum, devnum, busnum,
     flag_setup, flag_data, ts_sec, ts_usec, status,
     length, len_cap, setup) = struct.unpack(USB_HDR_FMT, pkt[:struct.calcsize(USB_HDR_FMT)])

    ep_dir = "IN" if (epnum & 0x80) else "OUT"
    ep_num = epnum & 0x7f
    xfer_name = {XFER_ISO: "ISO", XFER_INT: "INT",
                 XFER_CTRL: "CTRL", XFER_BULK: "BULK"}.get(xfer, f"?{xfer}")

    rec = {
        "id": id_,
        "type": chr(type_) if 32 <= type_ < 127 else f"0x{type_:02x}",
        "xfer": xfer_name,
        "ep": ep_num,
        "dir": ep_dir if xfer != XFER_CTRL else "CTL",
        "devnum": devnum,
        "busnum": busnum,
        "status": status,
        "length": length,        # URB transfer length (requested)
        "len_cap": len_cap,      # captured data length
        "data": pkt[USB_HDR_SIZE:USB_HDR_SIZE + len_cap],
        "setup": setup,
    }
    return rec


def decode_control_setup(setup):
    """Decode an 8-byte USB setup packet."""
    if len(setup) < 8:
        return None
    bmrt, breq, wval, widx, wlen = struct.unpack("<BBHHH", setup[:8])
    dir_bit = (bmrt >> 7) & 1
    type_field = (bmrt >> 5) & 3   # 0=std,1=class,2=vendor
    recip = bmrt & 0x1f           # 0=device,1=interface,2=endpoint,...
    type_name = ["std", "class", "vendor", "reserved"][type_field]
    recip_name = {0: "dev", 1: "iface", 2: "ep"}.get(recip, str(recip))

    # Common class requests for HID / CDC
    req_desc = ""
    if type_name == "class":
        req_desc = {
            0x00: "GET_REPORT", 0x09: "SET_REPORT",
            0x0A: "SET_IDLE", 0x0B: "SET_PROTOCOL", 0x03: "GET_IDLE",
            0x02: "GET_IDLE", 0x01: "GET_REPORT", 0x06: "GET_DESCRIPTOR",
            0x20: "SET_LINE_CODING", 0x21: "GET_LINE_CODING",
            0x22: "SET_CONTROL_LINE_STATE", 0x23: "SEND_BREAK",
        }.get(breq, f"class{breq:#x}")
    elif type_name == "std":
        req_desc = {
            0x05: "SET_ADDRESS", 0x09: "SET_CONFIGURATION",
            0x07: "SET_DESCRIPTOR", 0x0B: "SET_INTERFACE",
            0x01: "CLEAR_FEATURE", 0x03: "SET_FEATURE",
            0x06: "GET_DESCRIPTOR", 0x08: "GET_CONFIGURATION",
        }.get(breq, f"std{breq:#x}")
    return {
        "bmRequestType": f"0x{bmrt:02x}",
        "direction": "D2H" if dir_bit else "H2D",
        "type": type_name,
        "recipient": recip_name,
        "bRequest": f"0x{breq:02x}",
        "req_name": req_desc,
        "wValue": f"0x{wval:04x}",
        "wIndex": widx,
        "wLength": wlen,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap")
    ap.add_argument("--target-dev", type=int, default=None,
                    help="Only show URBs for this devnum (default: auto-detect)")
    ap.add_argument("--host-only", action="store_true",
                    help="Only host->device submissions (type 'S')")
    ap.add_argument("--data-only", action="store_true",
                    help="Only show URBs that carry data")
    ap.add_argument("--max", type=int, default=None,
                    help="Max records to print")
    args = ap.parse_args()

    # First pass: find devnum(s) on bus 3 with vendor 0x17ef via SET_DESCRIPTOR
    # responses; simpler: collect devnums seen and let user filter.
    recs = []
    t0 = None
    for ts, linktype, pkt in read_pcap(args.pcap):
        if t0 is None:
            t0 = ts
        rec = parse_usbmon(pkt, linktype)
        if rec is None:
            continue
        rec["t"] = ts - t0
        recs.append(rec)

    # Auto-detect target devnum: the one with most CTRL setups (the MCU).
    if args.target_dev is None:
        ctrls = Counter(r["devnum"] for r in recs
                        if r["xfer"] == "CTRL" and r["type"] == "S")
        if ctrls:
            target = ctrls.most_common(1)[0][0]
            print(f"# auto-detected target devnum = {target} "
                  f"(most CTRL submissions). Override with --target-dev")
        else:
            target = None
            print("# no CTRL submissions found; showing all devices")
    else:
        target = args.target_dev

    print(f"# total usbmon records: {len(recs)}; filtering devnum={target}\n")

    shown = 0
    for r in recs:
        if target is not None and r["devnum"] != target:
            continue
        if args.host_only and r["type"] != "S":
            continue
        if args.data_only and r["len_cap"] == 0:
            continue

        # For control submissions, decode setup
        setup_str = ""
        if r["xfer"] == "CTRL" and r["type"] == "S":
            dec = decode_control_setup(r["setup"])
            if dec:
                setup_str = (f"  {dec['req_name']} type={dec['type']} "
                             f"recip={dec['recipient']} wVal={dec['wValue']} "
                             f"wIdx={dec['wIndex']} wLen={dec['wLength']}")

        data_str = ""
        if r["len_cap"] > 0:
            d = r["data"][:64]
            data_str = "  data=" + d.hex()
            # Pretty-print OSKP frames
            if d[:4] == b"OSKP":
                wlen = struct.unpack("<H", d[4:6])[0] if len(d) >= 6 else 0
                ftype = d[6] if len(d) >= 7 else 0
                data_str += (f"   [OSKP type=0x{ftype:02x} wlen={wlen} "
                             f"payload={d[7:7+max(0,wlen-1)].hex()}]")
            # HID mode-toggle detection
            elif r["xfer"] in ("INT", "CTRL") and len(d) >= 2 and d[0] == 0x20:
                data_str += f"   [maybe HID toggle 0x20,0x{d[1]:02x}]"

        print(f"{r['t']:9.4f}s [{r['type']}] {r['xfer']:4s} EP{r['ep']:02x}"
              f"{r['dir']:3s} len={r['length']:5d} cap={r['len_cap']:5d}"
              f"{setup_str}{data_str}")
        shown += 1
        if args.max and shown >= args.max:
            break

    print(f"\n# shown {shown} records")


if __name__ == "__main__":
    main()
