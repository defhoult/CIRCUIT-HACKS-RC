// The host's protocol module (hosts/web/proto.js) against real firmware bytes (fixtures.json, from tools/ledmirror_fixture.py).
//     node --test hosts/test/

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { SIG, SIG_V1, NLED, keepalive, sigVersion, targets, parse, Mirror, rgb, css, luma } from '../web/proto.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(readFileSync(join(here, 'fixtures.json'), 'utf8'));
const bytes = (hex) => Uint8Array.from(Buffer.from(hex, 'hex'));

test('keepalive: F0 7D 43 54 32 type seq F7, SEQ masked to 7 bits', () => {
  assert.deepEqual([...keepalive(0, 5)], [0xF0, 0x7D, 0x43, 0x54, 0x32, 0x00, 0x05, 0xF7]);
  // a SysEx data byte must be < 0x80 or the device's stock router drops the whole message
  assert.deepEqual([...keepalive(1, 0xFF)], [0xF0, 0x7D, 0x43, 0x54, 0x32, 0x01, 0x7F, 0xF7]);
  // the pre-fix command base, for a unit that has not been reflashed yet
  assert.deepEqual([...keepalive(0, 5, 1)], [0xF0, 0x7D, 0x43, 0x54, 0x20, 0x00, 0x05, 0xF7]);
});

test('the fifth byte is the firmware signature: "CT2" = 7-bit SEQ, "CT " = pre-fix', () => {
  const m2 = Uint8Array.from([...SIG, 1, 3, 0, 1, 0, 0, 0, 0, 0, 0, 0xF7]);
  const m1 = m2.slice(); m1[4] = SIG_V1[4];
  assert.equal(sigVersion(m2), 2);
  assert.equal(sigVersion(m1), 1);
  assert.equal(parse(m2).ver, 2);
  assert.equal(parse(m1).ver, 1, 'a pre-fix message still parses, so an old device is seen, not ignored');
  assert.deepEqual(targets(0), [1, 2], 'before the device speaks: keepalive on both bases');
  assert.deepEqual(targets(1), [1]);
  assert.deepEqual(targets(2), [2]);
  const other = m2.slice(); other[4] = 0x21;
  assert.equal(sigVersion(other), 0, 'an unknown base is not ours');
  assert.equal(parse(other), null);
});

test('the real messages: a full frame, a delta, silence, a resync; the panel tracks the device', () => {
  const m = new Mirror();
  let gaps = 0, messages = 0;
  for (const c of fixtures.cases) {
    const want = c.words.map((w) => w >>> 0);
    for (const hx of c.messages) {
      const p = parse(bytes(hx));
      assert.ok(p, `${c.name}: ${hx.slice(0, 24)}… parses`);
      const r = m.apply(p);
      if (r.gap) gaps++;
      messages++;
    }
    assert.deepEqual([...m.words], want, `${c.name}: the host's panel = the device's`);
  }
  assert.equal(messages, 5, 'two chunks + one delta + two chunks');
  assert.equal(gaps, 1, 'the dropped delta is noticed as one sequence gap');
  assert.equal(m.frames, 5, 'every message with entries counts as a frame (2 + 1 + 2)');
});

test('a full chunk places its LEDs at START.., six nibbles high first', () => {
  const msg = Uint8Array.from([...SIG, 1, 9, 60, 2, 0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0xA, 0xB, 0xC, 0xD, 0xE, 0xF, 0xF7]);
  const p = parse(msg);
  assert.equal(p.type, 1);
  assert.deepEqual(p.items, [[60, 0x123456], [61, 0xABCDEF]]);
  const m = new Mirror();
  m.apply(p);
  assert.equal(m.words[60], 0x123456);
  assert.equal(m.words[61], 0xABCDEF);
  assert.equal(m.words[0], 0);
});

test('a delta carries its own indices', () => {
  const p = parse(Uint8Array.from([...SIG, 2, 3, 7, 2, 40, 0xF, 0xF, 0x0, 0x0, 0x0, 0x0, 68, 0x0, 0x0, 0xF, 0xF, 0x0, 0x0, 0xF7]));
  assert.equal(p.type, 2);
  assert.deepEqual(p.items, [[40, 0xFF0000], [68, 0x00FF00]]);
  const m = new Mirror();
  m.apply(p);
  assert.equal(m.words[40], 0xFF0000);
});

test('anything that is not ours, or is malformed, is ignored', () => {
  const ok = [...SIG, 1, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0xF7];
  assert.equal(parse(Uint8Array.from([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])), null, 'another manufacturer');
  assert.equal(parse(Uint8Array.from([0x90, 60, 100])), null, 'a note');
  assert.equal(parse(Uint8Array.from([...SIG, 1, 1, 0, 1, 0, 0, 0])), null, 'no F7');
  assert.equal(parse(Uint8Array.from(ok.slice(0, 14))), null, 'a short body');
  assert.equal(parse(Uint8Array.from([...ok.slice(0, 8), 9, 0xF7])), null, 'an unknown type');
  const bad = ok.slice(); bad[8] = 2;                       // COUNT says 2, the body has 1
  assert.equal(parse(Uint8Array.from(bad)), null, 'COUNT vs the body');
  assert.equal(parse(Uint8Array.from([...SIG, 0, 1, 0xF7])).type, 0, 'a keepalive');
});

test('a sequence gap is reported once and resets the expectation', () => {
  const m = new Mirror();
  const at = (seq) => Uint8Array.from([...SIG, 1, seq, 0, 1, 0, 0, 0, 0, 0, 0, 0xF7]);
  assert.ok(parse(at(4)), 'the test frame parses');
  assert.equal(m.apply(parse(at(4))).gap, false, 'the first frame sets the expectation');
  assert.equal(m.apply(parse(at(5))).gap, false);
  const r = m.apply(parse(at(9)));
  assert.equal(r.gap, true);
  assert.equal(r.needFull, true);
  assert.equal(m.gaps, 1);
  assert.equal(m.apply(parse(at(10))).gap, false, 'and it continues from there');
});

test('SEQ is 7-bit: 127 -> 0 is the wrap, not a gap', () => {
  const m = new Mirror();
  const at = (seq) => Uint8Array.from([...SIG, 1, seq, 0, 1, 0, 0, 0, 0, 0, 0, 0xF7]);
  for (const seq of [125, 126, 127, 0, 1, 2]) assert.equal(m.apply(parse(at(seq))).gap, false, `seq ${seq}`);
  assert.equal(m.gaps, 0, 'no false gaps at the wrap (they would ask for a full frame every 128 messages)');
});

test('colour: the word is R << 16 | G << 8 | B', () => {
  assert.deepEqual(rgb(0x123456), [0x12, 0x34, 0x56]);
  assert.equal(css(0xFF0000), 'rgb(255,0,0)');
  assert.equal(css(0), 'rgb(0,0,0)');
  assert.ok(luma(0xFFFFFF) > 0.99 && luma(0x000000) === 0);
});

test('the whole panel is 69 LEDs, and every fixture word is 24-bit', () => {
  assert.equal(NLED, 69);
  for (const c of fixtures.cases) {
    assert.equal(c.words.length, 69, c.name);
    for (const w of c.words) assert.ok(w >= 0 && w <= 0xFFFFFF, `${w.toString(16)} is a 24-bit colour`);
  }
});
