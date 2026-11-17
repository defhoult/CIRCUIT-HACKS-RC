// The LED mirror protocol (tools/ledmirror.py), host side.  No DOM: tests/Node import this file too.
//
//   F0 7D 43 54 32 01 SEQ START COUNT n0 n1 n2 n3 n4 n5 ...  F7   full chunk: 6 nibbles per LED
//   F0 7D 43 54 32 02 SEQ START COUNT IDX n0 .. n5 ...        F7   delta: IDX + 6 nibbles per changed LED
//   F0 7D 43 54 32 00 SEQ                                     F7   keepalive
//   F0 7D 43 54 32 01 SEQ                                     F7   keepalive + the next tick sends a full frame
//
// Six nibbles = bits 23..0 of the LED word (R << 16 | G << 8 | B), high nibble first.  SEQ is a 7-bit SysEx data byte (0..127) on both
// sides: the stock router drops any message with a data byte >= 0x80.  The device never replies, never acknowledges and stops streaming
// 2 s after the last keepalive: send one every ~250 ms.
//
// The fifth byte is the command base, and it is also how the device says which firmware it runs: "CT2" (0x32) is the 2026-11-16 build with
// the 7-bit SEQ fix, "CT " (0x20) is the pre-fix build that froze for ~2.7 s every 128 frames.  We accept both, send "CT2", and until the
// first message arrives we keepalive on both so an old device still streams (and then tells us it is old).

export const SIG_V1 = [0xF0, 0x7D, 0x43, 0x54, 0x20];   // "CT " — pre-2026-11-16: 8-bit SEQ, drops messages every 128 frames
export const SIG = [0xF0, 0x7D, 0x43, 0x54, 0x32];      // "CT2" — 2026-11-16: 7-bit SEQ
export const NLED = 69;
export const KEEP_MS = 250;          // well inside the device's 2 s window

export const TYPE_FULL = 1, TYPE_DELTA = 2, TYPE_KEEP = 0;

// -> 1, 2, or 0 if this is not one of our messages
export function sigVersion(msg) {
  if (!msg || msg.length < 5) return 0;
  for (let i = 0; i < 4; i++) if (msg[i] !== SIG[i]) return 0;
  if (msg[4] === SIG[4]) return 2;
  if (msg[4] === SIG_V1[4]) return 1;
  return 0;
}

// The signature(s) to talk to: the device's own once we know it, both until then.
export function targets(devVer) { return devVer ? [devVer] : [1, 2]; }

export function keepalive(type = TYPE_KEEP, seq = 0, ver = 2) {
  return Uint8Array.from([...(ver === 1 ? SIG_V1 : SIG), type & 0xFF, seq & 0x7F, 0xF7]);
}

// A received message -> {ver, type, seq, start, count, items: [[index, word], ...]} or null if it is not ours.
// `start` is the header's START for a full chunk (the first LED if the header is absent) and the first entry's index for a delta.
export function parse(msg) {
  if (!msg || msg.length < 7 || msg[msg.length - 1] !== 0xF7) return null;
  const ver = sigVersion(msg);
  if (!ver) return null;
  const type = msg[5], seq = msg[6];
  if (msg.length === 8) {
    if (type === TYPE_KEEP || type === TYPE_FULL) return { ver, type: TYPE_KEEP, seq, start: 0, count: 0, items: [] };
    return null;
  }
  if (msg.length < 10) return null;
  const start = msg[7], count = msg[8], body = msg.subarray(9, msg.length - 1);
  if (type === TYPE_FULL) {
    if (body.length !== 6 * count) return null;
    const items = [];
    for (let k = 0; k < count; k++) {
      const o = 6 * k;
      items.push([start + k, (body[o] << 20) | (body[o + 1] << 16) | (body[o + 2] << 12) | (body[o + 3] << 8) | (body[o + 4] << 4) | body[o + 5]]);
    }
    return { ver, type, seq, start, count, items };
  }
  if (type === TYPE_DELTA) {
    if (body.length !== 7 * count) return null;
    const items = [];
    for (let k = 0; k < count; k++) {
      const o = 7 * k;
      items.push([body[o], (body[o + 1] << 20) | (body[o + 2] << 16) | (body[o + 3] << 12) | (body[o + 4] << 8) | (body[o + 5] << 4) | body[o + 6]]);
    }
    return { ver, type, seq, start, count, items };
  }
  return null;
}

// The host's copy of the panel: apply messages, notice sequence gaps.
export class Mirror {
  constructor() {
    this.words = new Uint32Array(NLED);
    this.dirty = new Set();
    this.seq = null;              // the next expected sequence number, null until the first frame
    this.gaps = 0;                // how often a frame went missing (a full frame is asked for each time)
    this.frames = 0;
  }

  reset() { this.words.fill(0); this.seq = null; this.dirty = new Set([...Array(NLED).keys()]); }

  // -> {applied, gap, needFull}: `needFull` when the sequence broke (the host should ask for a full frame)
  apply(m) {
    if (!m) return { applied: false, gap: false, needFull: false };
    const gap = this.seq !== null && m.seq !== this.seq;
    if (gap) this.gaps++;
    this.seq = (m.seq + 1) & 0x7F;
    for (const [i, v] of m.items) {
      if (i < NLED && this.words[i] !== v) { this.words[i] = v; this.dirty.add(i); }
    }
    if (m.items.length) this.frames++;
    return { applied: m.items.length > 0, gap, needFull: gap };
  }

  take() { const d = this.dirty; this.dirty = new Set(); return d; }
}

// The LED word -> "#rrggbb" and a 0..1 brightness (the converter has already applied the panel's dimming / pulses).
export function rgb(word) {
  return [(word >> 16) & 0xFF, (word >> 8) & 0xFF, word & 0xFF];
}

export function css(word) {
  const [r, g, b] = rgb(word);
  return `rgb(${r},${g},${b})`;
}

export function luma(word) {
  const [r, g, b] = rgb(word);
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}
