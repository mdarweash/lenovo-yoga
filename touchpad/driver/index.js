/**
 * yb9-touchpad — Yoga Book 9 virtual touchpad driver for Linux.
 *
 * Replicates the exact Windows activation sequence from USB captures (2026-05-16):
 *
 * Activation:
 *   1. SET_IDLE on IF2 (STALLed by MCU, but Windows sends it)
 *   2. Start polling EP 0x83 (IF2 HID interrupt IN)
 *   3. Start polling EP 0x81 (IF1 OSKP bulk IN)
 *   4. Start polling EP 0x82 (IF0 CDC interrupt IN)
 *   5. SET_LINE_CODING on IF0 (9600 baud — safe; Windows uses 115200)
 *   6. HID output report [0x20, 0x00] to IF2 EP 0x02 OUT — THE MODE TOGGLE
 *   7. 0x20 OSKP sync on IF1
 *   8. 0x31 geometry on IF1
 *   9. 0x20 OSKP sync every ~1s (steady-state keepalive)
 *
 * Deactivation (from keyboard detach capture):
 *   1. 0x31 all-zeros on IF1 (clear geometry)
 *   2. Stop 0x20 sync
 *
 * Usage:
 *   sudo node index.js activate [duration]
 *   sudo node index.js daemon
 *   sudo node index.js deactivate
 *   sudo node index.js status
 */

'use strict';

const usb = require('usb');
const { EventEmitter } = require('events');
const fs = require('fs');

// ─── Constants ────────────────────────────────────────────────────────────────

const VID = 0x17ef;
const PID = 0x6161;

const IF0 = 0;  // CDC ACM — serial control channel
const IF1 = 1;  // Vendor Specific (0xFF) — OSKP bulk EP 0x01 OUT / EP 0x81 IN
const IF2 = 2;  // HID Keyboard — EP 0x02 OUT / EP 0x83 IN

const EP1_OUT = 0x01;
const EP1_IN  = 0x81;
const EP2_OUT = 0x02;
const EP2_IN  = 0x83;   // IF2 HID interrupt IN (keyboard data)
const EP0_INT = 0x82;   // IF0 CDC interrupt IN (serial state)

const SET_LINE_CODING = 0x20;
const SET_CONTROL_LINE_STATE = 0x22;
const SET_IDLE = 0x0a;

const SYNC_INTERVAL_MS = 1000;  // Windows sends 0x20 sync every ~1s

// ─── OSKP Frame Builder ──────────────────────────────────────────────────────

function buildOSKP(type, payload = Buffer.alloc(0)) {
  const frame = Buffer.alloc(7 + payload.length);
  frame.write('OSKP', 0, 'ascii');
  frame.writeUInt16LE(payload.length + 1, 4);
  frame[6] = type;
  if (payload.length > 0) payload.copy(frame, 7);
  return frame;
}

// ─── OSKP Frame Parser ───────────────────────────────────────────────────────

function* parseOSKPFrames(buf) {
  let pos = 0;
  while (pos + 6 <= buf.length) {
    if (buf.toString('ascii', pos, pos + 4) === 'OSKP') {
      const wireLen = buf.readUInt16LE(pos + 4);
      const total = 6 + wireLen;
      if (pos + total > buf.length) break;
      yield { type: buf[pos + 6], payload: buf.slice(pos + 7, pos + 6 + wireLen) };
      pos += total;
    } else {
      pos++;
    }
  }
}

// ─── Geometry (from Windows captures + decompilation) ─────────────────────────

function buildTouchpadGeometry(w, h) {
  const packRect = (l, t, r, b) => {
    const buf = Buffer.alloc(8);
    buf.writeUInt16LE(l, 0); buf.writeUInt16LE(t, 2);
    buf.writeUInt16LE(r, 4); buf.writeUInt16LE(b, 6);
    return buf;
  };

  const capH = Math.floor(h * 80 / 500);
  const btnH = Math.floor(h * 90 / 500);
  const sm = Math.floor(w * 40 / 500);
  const bm = Math.floor(h * 40 / 500);
  const gap = Math.floor(w * 10 / 500);
  const halfBtn = Math.floor((w - 2 * sm - gap) / 2);

  return Buffer.concat([
    packRect(0, 0, w, h),
    packRect(sm, h - bm - btnH, sm + halfBtn, h - bm),
    packRect(sm + halfBtn + gap, h - bm - btnH, w - sm, h - bm),
    packRect(sm, capH, w - sm, h - bm - btnH),
    Buffer.from([0x00]),
    packRect(0, 0, 0, 0),
  ]);
}

// ─── Input device helpers ─────────────────────────────────────────────────────

function findEventDevice(nameFragment) {
  const entries = fs.readdirSync('/sys/class/input/')
    .filter(e => e.startsWith('event'))
    .sort((a, b) => parseInt(a.slice(5)) - parseInt(b.slice(5)));
  for (const entry of entries) {
    try {
      const name = fs.readFileSync(`/sys/class/input/${entry}/device/name`, 'utf8').trim();
      if (name.toLowerCase().includes(nameFragment.toLowerCase()))
        return { path: `/dev/input/${entry}`, name, num: parseInt(entry.slice(5)) };
    } catch {}
  }
  return null;
}

/**
 * Find the hidraw device for a given USB interface number of the INGENIC device.
 */
function findHidrawForInterface(ifNum) {
  const hidBusPath = '/sys/bus/hid/devices/';
  try {
    for (const entry of fs.readdirSync(hidBusPath)) {
      // Entry format: 0003:17EF:6161.xxxx
      if (!entry.includes('17EF:6161')) continue;
      const devPath = fs.realpathSync(`${hidBusPath}${entry}`);
      // Look for :1.N in the path (configuration 1, interface N)
      const match = devPath.match(/:1\.(\d+)/);
      if (match && parseInt(match[1]) === ifNum) {
        // Find hidraw child
        const hidrawDir = `${hidBusPath}${entry}/hidraw/`;
        const hidraws = fs.readdirSync(hidrawDir).filter(e => e.startsWith('hidraw'));
        if (hidraws.length > 0) return `/dev/${hidraws[0]}`;
      }
    }
  } catch {}
  return null;
}

function flushStuckTouches() {
  for (const frag of ['Touchscreen Bottom', 'Emulated Touchpad']) {
    const dev = findEventDevice(frag);
    if (!dev) continue;
    try {
      const fd = fs.openSync(dev.path, fs.constants.O_WRONLY | fs.constants.O_NONBLOCK);
      const mk = (type, code, val) => { const b = Buffer.alloc(24); b.writeUInt16LE(type, 16); b.writeUInt16LE(code, 18); b.writeInt32LE(val, 20); return b; };
      for (const ev of [mk(0x03, 0x39, -1), mk(0x01, 0x14a, 0), mk(0x00, 0x00, 0)])
        fs.writeSync(fd, ev);
      fs.closeSync(fd);
    } catch {}
  }
}

function dumpInterfaces(device) {
  console.log('[diag] USB device interfaces:');
  const cfg = device.configDescriptor;
  for (const altSet of cfg.interfaces) {
    const desc = altSet[0]; // first alternate setting
    const i = desc.bInterfaceNumber;
    console.log(`[diag]   IF${i}: class=0x${desc.bInterfaceClass.toString(16)} subclass=0x${desc.bInterfaceSubClass.toString(16)} proto=${desc.bInterfaceProtocol}`);
    for (const ep of desc.endpoints) {
      const dir = ep.bEndpointAddress & 0x80 ? 'IN' : 'OUT';
      const type = ['CONTROL', 'ISOCHRONOUS', 'BULK', 'INTERRUPT'][ep.bmAttributes & 0x03] || 'UNKNOWN';
      console.log(`[diag]     EP 0x${ep.bEndpointAddress.toString(16).padStart(2, '0')} ${dir} ${type} maxpkt=${ep.wMaxPacketSize}`);
    }
  }
}

// ─── Touchpad Driver ─────────────────────────────────────────────────────────

class TouchpadDriver extends EventEmitter {
  constructor(opts = {}) {
    super();
    this.device = null;
    this.iface0 = null;
    this.iface1 = null;
    this.iface2 = null;
    this.ep1Out = null;
    this.ep1In = null;
    this.ep2Out = null;
    this.ep2In = null;
    this.ep0Int = null;
    this.syncTimer = null;
    this.active = false;
    this.geo = buildTouchpadGeometry(3017, 1700);
    // Optional: claim IF0 (CDC ACM) in addition to IF1 and IF2
    this.claimIF0 = !!opts.claimIF0;
    this.claimIF2 = !!opts.claimIF2;
  }

  async open() {
    this.device = usb.findByIds(VID, PID);
    if (!this.device) throw new Error(`Device ${VID.toString(16)}:${PID.toString(16)} not found`);
    console.log(`[open] Found device ${VID.toString(16)}:${PID.toString(16)}`);
    this.device.open();
    console.log('[open] USB device opened');

    dumpInterfaces(this.device);

    // ── IF0: CDC ACM control channel (optional) ───────────────────────
    this.iface0 = null;
    this.iface0Claimed = false;
    this.ep0Int = null;
    if (this.claimIF0) {
      try {
        this.iface0 = this.device.interface(IF0);
        const kernelActive0 = this.iface0.isKernelDriverActive();
        console.log(`[open] IF0 kernel driver active: ${kernelActive0}`);
        if (kernelActive0) {
          this.iface0.detachKernelDriver();
          console.log('[open] IF0 kernel driver detached');
        }
        this.iface0.claim();
        this.iface0Claimed = true;
        this.ep0Int = this.iface0.endpoint(EP0_INT);
        console.log('[open] IF0 (CDC ACM) claimed, EP 0x82 endpoint obtained');

        const lineCoding = Buffer.alloc(7);
        lineCoding.writeUInt32LE(9600, 0);
        lineCoding[4] = 0; lineCoding[5] = 0; lineCoding[6] = 8;
        try {
          await this.device.controlTransfer(0x21, SET_LINE_CODING, 0, IF0, lineCoding);
          console.log('[open]   SET_LINE_CODING(9600, 8N1) OK');
        } catch (e) {
          console.log(`[open]   SET_LINE_CODING STALLed (${e.message})`);
        }
        try {
          await this.device.controlTransfer(0x21, SET_CONTROL_LINE_STATE, 0x03, IF0, Buffer.alloc(0));
          console.log('[open]   SET_CONTROL_LINE_STATE(DTR=1, RTS=1) OK');
        } catch (e) {
          console.log(`[open]   SET_CONTROL_LINE_STATE failed: ${e.message}`);
        }
        if (this.ep0Int) {
          this.ep0Int.on('data', data => console.log(`[open]   EP0x82 data: ${data.toString('hex')}`));
          this.ep0Int.on('error', e => console.log(`[open]   EP0x82 error: ${e.message}`));
          try { this.ep0Int.startPoll(1, 10); console.log('[open]   EP0x82 polling started'); } catch (e) { console.log(`[open]   EP0x82 startPoll failed: ${e.message}`); }
        }
      } catch (e) {
        console.log(`[open] IF0 claim failed: ${e.message}`);
        this.iface0 = null;
      }
    } else {
      console.log('[open] IF0 skipped (use --if0 to enable)');
    }

    // ── IF1: Vendor Specific — OSKP bulk ──────────────────────────────
    console.log('[open] Claiming IF1 (Vendor Specific / OSKP bulk)...');
    this.iface1 = this.device.interface(IF1);
    const kernelActive1 = this.iface1.isKernelDriverActive();
    console.log(`[open] IF1 kernel driver active: ${kernelActive1}`);
    if (kernelActive1) {
      this.iface1.detachKernelDriver();
      console.log('[open] IF1 kernel driver detached');
    }
    this.iface1.claim();
    this.ep1Out = this.iface1.endpoint(EP1_OUT);
    this.ep1In  = this.iface1.endpoint(EP1_IN);
    console.log(`[open] IF1 claimed. EP 0x01 OUT: ${this.ep1Out ? 'OK' : 'MISSING'}, EP 0x81 IN: ${this.ep1In ? 'OK' : 'MISSING'}`);

    if (this.ep1Out) {
      console.log(`[open]   EP 0x01 OUT descriptor: type=${this.ep1Out.transferType}, maxpkt=${this.ep1Out.descriptor.wMaxPacketSize}`);
    }

    // Read OSKP responses on EP 0x81
    let ep81Count = 0;
    this.ep1In.on('data', data => {
      ep81Count++;
      if (data.length > 0) {
        console.log(`[ep81] RAW (${data.length}B) #${ep81Count}: ${data.toString('hex')}`);
        for (const f of parseOSKPFrames(data)) this.emit('oskp', f);
      }
    });
    this.ep1In.on('error', e => console.log(`[ep81] error: ${e.message}`));
    try {
      this.ep1In.startPoll(3, 512);
      console.log('[open] EP 0x81 IN polling started (3 deep, 512B)');
    } catch (e) {
      console.log(`[open] EP 0x81 IN startPoll failed: ${e.message}`);
    }

    // ── IF2: HID Keyboard — optional, NOT needed for touchpad activation ──
    // Python test proves: only IF1 is needed. Claiming IF2 may interfere.
    this.iface2 = null;
    this.iface2Claimed = false;
    this.ep2Out = null;
    this.ep2In = null;
    if (this.claimIF2) {
      console.log('[open] Claiming IF2 (HID Keyboard) -- optional...');
      try {
        this.iface2 = this.device.interface(IF2);
        const kernelActive2 = this.iface2.isKernelDriverActive();
        console.log(`[open] IF2 kernel driver active: ${kernelActive2}`);
        if (kernelActive2) {
          console.log('[open] IF2 — detaching usbhid (keyboard will pause)');
          this.iface2.detachKernelDriver();
        }
        this.iface2.claim();
        this.iface2Claimed = true;

        this.ep2Out = this.iface2.endpoint(EP2_OUT);
        this.ep2In  = this.iface2.endpoint(EP2_IN);
        console.log(`[open] IF2 claimed. EP 0x02 OUT: ${this.ep2Out ? 'OK' : 'MISSING'}, EP 0x83 IN: ${this.ep2In ? 'OK' : 'MISSING'}`);

        if (this.ep2Out) {
          console.log(`[open]   EP 0x02 OUT descriptor: type=${this.ep2Out.transferType}, maxpkt=${this.ep2Out.descriptor.wMaxPacketSize}`);
        }

        try {
          console.log('[open]   Sending SET_IDLE to IF2...');
          await this.device.controlTransfer(0x21, SET_IDLE, 0, IF2, Buffer.alloc(0));
          console.log('[open]   SET_IDLE on IF2 OK');
        } catch (e) {
          console.log(`[open]   SET_IDLE STALLed (${e.message}) — expected per Windows captures`);
        }

        let ep83Count = 0;
        this.ep2In.on('data', data => {
          ep83Count++;
          if (data.length > 0) console.log(`[ep83] HID (${data.length}B) #${ep83Count}: ${data.toString('hex')}`);
        });
        this.ep2In.on('error', e => console.log(`[ep83] error: ${e.message}`));
        try {
          this.ep2In.startPoll(2, 12);
          console.log('[open] EP 0x83 IN polling started (2 deep, 12B)');
        } catch (e) {
          console.log(`[open] EP 0x83 IN startPoll failed: ${e.message}`);
        }
      } catch (e) {
        console.log(`[open] IF2 claim FAILED: ${e.message}`);
        this.iface2 = null;
      }
    } else {
      console.log('[open] IF2 skipped (not claimed — leaves usbhid bound, matching Python test behavior)');
    }

    console.log('[open] All interfaces claimed. Ready for activation.');
  }

  async sendOSKP(type, payload = Buffer.alloc(0)) {
    const frame = buildOSKP(type, payload);
    console.log(`[send] OSKP EP 0x01 OUT (${frame.length}B): ${frame.toString('hex')}`);
    const result = await this.ep1Out.transferAsync(frame);
    console.log(`[send]   transfer result: ${JSON.stringify(result)}`);
    return result;
  }

  /**
   * Send HID output report to IF2 EP 0x02 OUT (interrupt transfer).
   * MCU only accepts mode toggle via EP 0x02, not SET_REPORT.
   */
  async sendHIDReport(data) {
    if (!this.ep2Out) throw new Error('EP 0x02 OUT not available — IF2 not claimed');
    console.log(`[send] HID EP 0x02 OUT (${data.length}B): ${data.toString('hex')}`);
    const result = await this.ep2Out.transferAsync(data);
    console.log(`[send]   HID transfer result: ${JSON.stringify(result)}`);
    return result;
  }

  /**
   * Activate touchpad — proven sequence from Python test-windows-replica.py.
   *
   * All commands go through OSKP on IF1 bulk EP 0x01 OUT.
   * Keepalive is 0x26 with timestamp every 1.5s (matching YB9.Service KeepConnectThread).
   *
   * Sequence (from test-windows-replica.py lines 291-313):
   *   Phase 1 — Init:
   *     0x4b  timing/threshold
   *     0x21  8001010000  (init OSK param)
   *     0x28  0000        (status pair)
   *     0x26  timestamp   (keepalive)
   *     0x27  0000        (disable bottom panel touch)
   *     0x25  01          (touchpad mode ON)
   *     0x20  0100        (sync flag)
   *     0x21  7e01000000  (state sync)
   *   Phase 2 — Config:
   *     0x21  40 + dims   (screen info bX/bY)
   *     0x21  41 + dims   (screen info cX/cY)
   *     0x21  31 + ori    (orientation)
   *     0xa3  01          (post-orientation flag)
   *     0x31  geometry    (rectangles)
   *     0x26  timestamp   (final keepalive)
   *   Steady state:
   *     0x26  timestamp every 1.5s
   *     0x25 01 + 0x21 7e + 0x31 geometry every ~3s (periodic refresh)
   */
  async activate() {
    console.log('[activate] === TOUCHPAD ACTIVATION START ===');

    // ── Pre-flight check ───────────────────────────────────────────────
    console.log(`[activate] Pre-flight: ep1Out=${!!this.ep1Out}, ep1In=${!!this.ep1In}`);

    const tsBefore = findEventDevice('Touchscreen Bottom');
    const tpBefore = findEventDevice('Emulated Touchpad');
    console.log(`[activate] Input devices BEFORE activation:`);
    console.log(`[activate]   Touchscreen: ${tsBefore ? tsBefore.path + ' (' + tsBefore.name + ')' : 'NOT FOUND'}`);
    console.log(`[activate]   Touchpad:    ${tpBefore ? tpBefore.path + ' (' + tpBefore.name + ')' : 'NOT FOUND'}`);

    const W = 3017, H = 1700;

    // ── Phase 1: Init + Toggle ─────────────────────────────────────────

    console.log('[activate] Phase 1: Init commands...');
    await this.sendOSKP(0x4b, Buffer.from([0x00, 0x08, 0xb8, 0x0b, 0x10, 0x27, 0x10, 0x27]));
    console.log('[activate]   0x4b timing/threshold');
    await this._delay(50);

    await this.sendOSKP(0x21, Buffer.from([0x80, 0x01, 0x01, 0x00, 0x00]));
    console.log('[activate]   0x21 init OSK param');
    await this._delay(50);

    await this.sendOSKP(0x28, Buffer.from([0x00, 0x00]));
    console.log('[activate]   0x28 status pair');
    await this._delay(50);

    await this.sendOSKP(0x26, this._timestampBytes());
    console.log('[activate]   0x26 keepalive (timestamp)');
    await this._delay(100);

    // Disable bottom panel touchscreen
    await this.sendOSKP(0x27, Buffer.from([0x00, 0x00]));
    console.log('[activate]   0x27 disable bottom panel touch');
    await this._delay(50);

    // Enable touchpad mode
    await this.sendOSKP(0x25, Buffer.from([0x01]));
    console.log('[activate]   0x25 01 — touchpad mode ON');
    await this._delay(50);

    await this.sendOSKP(0x20, Buffer.from([0x01, 0x00]));
    console.log('[activate]   0x20 sync flag');
    await this._delay(50);

    await this.sendOSKP(0x21, Buffer.from([0x7e, 0x01, 0x00, 0x00, 0x00]));
    console.log('[activate]   0x21 7e state sync');
    await this._delay(50);

    // ── Phase 2: Config + Geometry ─────────────────────────────────────

    console.log('[activate] Phase 2: Config + geometry...');
    // Screen info bX, bY
    const screen40 = Buffer.alloc(5);
    screen40[0] = 0x40; screen40.writeUInt16LE(W, 1); screen40.writeUInt16LE(H, 3);
    await this.sendOSKP(0x21, screen40);
    console.log(`[activate]   0x21 40 screen info (${W}x${H})`);
    await this._delay(50);

    // Screen info cX, cY
    const screen41 = Buffer.alloc(5);
    screen41[0] = 0x41; screen41.writeUInt16LE(W, 1); screen41.writeUInt16LE(H, 3);
    await this.sendOSKP(0x21, screen41);
    console.log(`[activate]   0x21 41 screen info (${W}x${H})`);
    await this._delay(50);

    // Orientation sync
    await this.sendOSKP(0x21, Buffer.from([0x31, 0x00, 0x00, 0x00, 0x00]));
    console.log('[activate]   0x21 orientation');
    await this._delay(50);

    // Post-orientation flag
    await this.sendOSKP(0xa3, Buffer.from([0x01]));
    console.log('[activate]   0xa3 post-orientation flag');
    await this._delay(50);

    // Geometry
    await this.sendOSKP(0x31, this.geo);
    console.log(`[activate]   0x31 geometry (${this.geo.length}B)`);
    await this._delay(50);

    // Final keepalive
    await this.sendOSKP(0x26, this._timestampBytes());
    console.log('[activate]   0x26 keepalive (timestamp)');

    // ── Steady state: 0x26 keepalive + periodic refresh ───────────────
    let tick = 0;
    this.syncTimer = setInterval(() => {
      tick++;
      this.sendOSKP(0x26, this._timestampBytes()).then(() => {
        console.log(`[keepalive] #${tick} 0x26 OK`);
      }).catch(e => {
        console.error(`[keepalive] #${tick} FAILED: ${e.message}`);
      });

      // Every 3rd cycle (~4.5s): re-affirm touchpad mode + geometry
      if (tick % 3 === 0) {
        this.sendOSKP(0x25, Buffer.from([0x01])).catch(() => {});
        this.sendOSKP(0x21, Buffer.from([0x7e, 0x01, 0x00, 0x00, 0x00])).catch(() => {});
        this.sendOSKP(0x31, this.geo).catch(() => {});
        console.log(`[keepalive] #${tick} periodic refresh sent`);
      }
    }, 1500);

    this.active = true;
    this.emit('activated');

    // Final status
    const tsFinal = findEventDevice('Touchscreen Bottom');
    const tpFinal = findEventDevice('Emulated Touchpad');
    console.log(`[activate] === ACTIVATION COMPLETE ===`);
    console.log(`[activate] Input devices FINAL:`);
    console.log(`[activate]   Touchscreen: ${tsFinal ? tsFinal.path + ' (' + tsFinal.name + ')' : 'NOT FOUND'}`);
    console.log(`[activate]   Touchpad:    ${tpFinal ? tpFinal.path + ' (' + tpFinal.name + ')' : 'NOT FOUND'}`);
    console.log(`[activate] Keepalive: 0x26 every 1.5s, periodic refresh every ~4.5s.`);
  }

  _timestampBytes() {
    const now = new Date();
    const buf = Buffer.alloc(7);
    buf.writeUInt16LE(now.getFullYear(), 0);
    buf[1] = now.getMonth() + 1;
    buf[2] = now.getDate();
    buf[3] = now.getHours();
    buf[4] = now.getMinutes();
    buf[5] = now.getSeconds();
    return buf;
  }

  /**
   * Deactivate touchpad — restore normal touchscreen mode.
   *
   * From Python test --restore sequence:
   *   0x25 00        (touchpad off)
   *   0x27 0001      (re-enable bottom panel touch)
   *   0x21 7e00...   (state sync off)
   */
  async deactivate() {
    console.log('[deactivate] Deactivating...');
    if (this.syncTimer) { clearInterval(this.syncTimer); this.syncTimer = null; }

    try { await this.sendOSKP(0x25, Buffer.from([0x00])); } catch {}
    console.log('[deactivate]   0x25 00 (touchpad off)');

    try { await this.sendOSKP(0x27, Buffer.from([0x00, 0x01])); } catch {}
    console.log('[deactivate]   0x27 0001 (re-enable bottom panel touch)');

    try { await this.sendOSKP(0x21, Buffer.from([0x7e, 0x00, 0x00, 0x00, 0x00])); } catch {}
    console.log('[deactivate]   0x21 7e state sync off');

    this.active = false;
    this.emit('deactivated');
    console.log('[deactivate] Touchpad deactivated.');
  }

  async close() {
    if (this.syncTimer) { clearInterval(this.syncTimer); this.syncTimer = null; }

    // Stop polling IF2 EP 0x83
    if (this.ep2In) { try { this.ep2In.stopPoll(); } catch (e) { console.log(`[close] EP 0x83 stopPoll: ${e.message}`); } }

    // Stop polling IF1 EP 0x81
    if (this.ep1In) { try { this.ep1In.stopPoll(); } catch (e) { console.log(`[close] EP 0x81 stopPoll: ${e.message}`); } }

    // Stop polling IF0 EP 0x82
    if (this.ep0Int) { try { this.ep0Int.stopPoll(); } catch (e) { console.log(`[close] EP 0x82 stopPoll: ${e.message}`); } }

    if (this.active) { try { await this.deactivate(); } catch {} }

    // Release IF0
    if (this.iface0Claimed && this.iface0) {
      try {
        this.device.controlTransfer(0x21, SET_CONTROL_LINE_STATE, 0x00, IF0, Buffer.alloc(0));
      } catch {}
      try { this.iface0.release(true); console.log('[close] IF0 released'); } catch (e) { console.log(`[close] IF0 release: ${e.message}`); }
    }

    // Release IF1
    if (this.iface1) {
      try {
        this.iface1.release(true, () => { try { this.iface1.attachKernelDriver(); } catch {} });
        console.log('[close] IF1 released');
      } catch (e) { console.log(`[close] IF1 release: ${e.message}`); }
    }

    // Release IF2 (only if we claimed it via libusb)
    if (this.iface2Claimed && this.iface2) {
      try {
        this.iface2.release(true, () => { try { this.iface2.attachKernelDriver(); console.log('[close] IF2 kernel driver re-attached'); } catch {} });
        console.log('[close] IF2 released');
      } catch (e) { console.log(`[close] IF2 release: ${e.message}`); }
    }

    try { this.device.close(); console.log('[close] USB device closed'); } catch (e) { console.log(`[close] device close: ${e.message}`); }

    this.device = null;
    this.iface0 = null;
    this.iface1 = null;
    this.iface2 = null;
    this.ep1Out = null;
    this.ep1In = null;
    this.ep2Out = null;
    this.ep2In = null;
    this.ep0Int = null;
    this.active = false;
  }

  _delay(ms) { return new Promise(r => setTimeout(r, ms)); }
}

// ─── CLI ──────────────────────────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);
  const cmd = args.find(a => !a.startsWith('--')) || 'activate';
  const opts = {
    claimIF0: args.includes('--if0'),
    claimIF2: args.includes('--if2'),
  };
  const driver = new TouchpadDriver(opts);

  let shuttingDown = false;
  const shutdown = async () => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log('\n[main] Shutting down...');
    try { await driver.close(); } catch {}
    flushStuckTouches();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  driver.on('oskp', ({ type, payload }) => {
    console.log(`[oskp] MCU -> 0x${type.toString(16).padStart(2, '0')} (${payload.length}B) ${payload.toString('hex')}`);
  });

  switch (cmd) {
    case 'activate':
    case 'daemon': {
      await driver.open();
      await driver.activate();

      if (cmd === 'daemon') {
        console.log('[main] Daemon mode. Ctrl+C to stop.');
        await new Promise(() => {});
      } else {
        const duration = parseInt(args.find(a => /^\d+$/.test(a))) || 30;
        const ts = findEventDevice('Touchscreen Bottom');
        const tp = findEventDevice('Emulated Touchpad');
        if (ts) console.log(`[monitor] Touchscreen: ${ts.path}`);
        if (tp) console.log(`[monitor] Touchpad:    ${tp.path}`);
        console.log(`[main] Holding for ${duration}s. Touch the bottom screen!`);
        await new Promise(r => setTimeout(r, duration * 1000));
        await shutdown();
      }
      break;
    }
    case 'deactivate': {
      await driver.open();
      await driver.deactivate();
      await driver.close();
      flushStuckTouches();
      break;
    }
    case 'status': {
      const ts = findEventDevice('Touchscreen Bottom');
      const tp = findEventDevice('Emulated Touchpad');
      console.log('Input devices:');
      console.log(`  Touchscreen: ${ts ? ts.path + ' (' + ts.name + ')' : 'NOT FOUND'}`);
      console.log(`  Touchpad:    ${tp ? tp.path + ' (' + tp.name + ')' : 'NOT FOUND'}`);
      const hidraw2 = findHidrawForInterface(IF2);
      console.log(`  IF2 hidraw:  ${hidraw2 || 'NOT FOUND'}`);
      for (const e of fs.readdirSync('/sys/class/hidraw/').sort()) {
        try {
          const link = fs.readlinkSync(`/sys/class/hidraw/${e}/device`);
          if (link.includes('17EF:6161')) {
            const iface = link.match(/:1\.(\d+)/)?.[1] || '?';
            console.log(`  /dev/${e}:  IF${iface}`);
          }
        } catch {}
      }
      break;
    }
    default:
      console.log('Usage: sudo node index.js [--if0] [--if2] <activate|daemon|deactivate|status> [duration]');
      process.exit(1);
  }
}

module.exports = { TouchpadDriver, buildOSKP, buildTouchpadGeometry, parseOSKPFrames };

if (require.main === module) {
  main().catch(err => { console.error(`Fatal: ${err.message}`); process.exit(1); });
}
