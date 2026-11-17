// Demo mode (`?demo`): a fake device, so the panel can be looked at without hardware.  It builds real protocol messages (the same bytes
// tools/ledmirror.py sends) and feeds them through the same parser and renderer as the MIDI path, deltas included.

import { SIG, SIG_V1, NLED, TYPE_FULL, TYPE_DELTA } from './proto.js';

// `?demo&old` pretends to be the pre-2026-11-16 firmware, so the host's "PRE-FIX" warning can be seen without hardware.
const SIGX = new URLSearchParams(location.search).has('old') ? SIG_V1 : SIG;

const PAD_COLS = [0xFF2A1A, 0x17FF3A, 0x1A47FF, 0xFFC21A, 0xB02AFF, 0x1AFFD0, 0xFF3A8A, 0x3AE0FF];

function message(type, seq, start, count, body) {
  const out = new Uint8Array(10 + body.length);
  out.set([...SIGX, type, seq & 0x7F, start, count], 0);   // SEQ is a 7-bit SysEx data byte
  out.set(body, 9);
  out[out.length - 1] = 0xF7;
  return out;
}

function nibbles(w) {
  const b = [];
  for (let s = 20; s >= 0; s -= 4) b.push((w >> s) & 0xF);
  return b;
}

export function start(handle) {
  const words = new Uint32Array(NLED);
  const level = new Float32Array(32);
  let seq = 0, t0 = performance.now(), pos = 0, tick = 0;

  for (let i = 0; i < 32; i++) words[i] = 0x101010;
  for (let i = 32; i < 40; i++) words[i] = i === 32 ? 0x1A47FF : 0x0A0A0A;
  words[40] = 0xFFFFFF; words[45] = 0x1AFFD0; words[46] = 0x1AFFD0;
  words[47] = 0xFFC21A; words[59] = 0xFF2A1A;
  for (let i = 60; i < 68; i++) words[i] = PAD_COLS[i - 60] & 0x606060;
  words[68] = 0x9A9A9A;

  const scale = (c, k) => {
    const r = Math.round(((c >> 16) & 0xFF) * k), g = Math.round(((c >> 8) & 0xFF) * k), b = Math.round((c & 0xFF) * k);
    return (r << 16) | (g << 8) | b;
  };

  const pump = () => {
    const t = (performance.now() - t0) / 1000;
    const changed = new Set();

    // a hit walking the pads, each fading out
    pos = (pos + 1) % 32;
    level[pos] = 1;
    for (let i = 0; i < 32; i++) {
      if (level[i] <= 0.02) { level[i] = 0; continue; }
      level[i] *= 0.86;
      const w = level[i] ? scale(PAD_COLS[i % 8], level[i]) : 0x101010;
      if (words[i] !== w) { words[i] = w; changed.add(i); }
    }

    // Play pulses, the master filter sweeps
    const pulse = Math.round(255 * (0.35 + 0.65 * (0.5 + 0.5 * Math.sin(t * 5))));
    const w49 = (pulse << 8) | pulse;
    if (words[49] !== w49) { words[49] = w49; changed.add(49); }
    const k = Math.round(255 * (0.5 + 0.5 * Math.sin(t * 0.8)));
    const w68 = k << 16 | k << 8 | k;
    if (words[68] !== w68) { words[68] = w68; changed.add(68); }

    if (tick++ % 90 === 0) {                                  // a full frame now and then, like the device
      const a = [], b = [];
      for (let i = 0; i < 48; i++) a.push(...nibbles(words[i]));
      for (let i = 48; i < NLED; i++) b.push(...nibbles(words[i]));
      handle(message(TYPE_FULL, seq, 0, 48, a)); seq = (seq + 1) & 0x7F;
      handle(message(TYPE_FULL, seq, 48, NLED - 48, b)); seq = (seq + 1) & 0x7F;
    } else if (changed.size) {                                // deltas, in chunks of 48 like the firmware
      const list = [...changed].sort((x, y) => x - y);
      for (let s = 0; s < list.length; s += 48) {
        const part = list.slice(s, s + 48), body = [];
        for (const i of part) body.push(i, ...nibbles(words[i]));
        handle(message(TYPE_DELTA, seq, part[0], part.length, body)); seq = (seq + 1) & 0x7F;
      }
    }
    setTimeout(pump, 33);
  };
  pump();
}
