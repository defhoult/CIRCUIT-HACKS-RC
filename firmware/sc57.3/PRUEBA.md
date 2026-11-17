# SC57.3 = SC57.2 + the installable CT2 signature (LED mirror over USB MIDI)

| File | `.syx` SHA-256 | Marker |
|---|---|---|
| `firmware/sc57.3/circuittracks-4486-SC57.3.syx` | `d56b1d91cd07b2671e2c59012f5b9a180b4c5a6774528b016603c4a76cb6870d` | `SC57.3` |

**Why the name changed:** the CT2 rebuild was first produced as `SC57.2`, but it kept the same version block (marker `SC57.2`) as the build
already installed. The bootloader **skips an image whose version block matches the installed one**, so three flashes reported success and left
the old build running. `SC57.3` changes the marker (the version block is `0x1C8..0x1EE`, whose tail is the marker), so the bootloader installs
it. The code is otherwise identical to the CT2 `SC57.2`.

**What's new** (2026-11-15 / 16): the Circuit Tracks has no screen; its **69 LEDs are the whole UI**. This build **streams them live over USB MIDI**
so a Mac (and later an Android phone) can draw them as a clean grid — same colour, same brightness, same pulses as the panel.

- **It copies the stock LED converter's output** (`0x0803423C`, 69 words `B,G,R,0`), not the codes before it: the colour already has the
  dimming and the pulse applied. The hook is the converter's only caller (the `bl` at `0x0803418C`, inside the LED task): `c_lm_tick` runs the
  converter once into its own buffer, copies the result to the caller's buffer (the panel's output, byte for byte as before) and mirrors from
  its own copy, never reading the caller's (it may be a peripheral).
- **Its own protocol** (manufacturer id `0x7D`, tag `43 54 32`, "CT2"): full frame (6 nibbles per LED), delta (index + 6 nibbles, only what
  changed; nothing at all when nothing changed) and a host keepalive. An LED changes at most one frame per 16 ms; messages go in chunks of 48
  LEDs (298 B) through the stock SysEx router.
- **It identifies itself**: the tag's fifth byte is `0x32` ("CT2") in this build; the previous one sent `0x20` ("CT "). The page shows
  **`device firmware "CT2" · 7-bit SEQ`** when the flash took and **`"CT" — PRE-FIX`** when it did not. The device still accepts `0x20` on the
  way in, so an older cached page can drive it.
- **Only while the host is there**: with no recent keepalive (**2 s**) nothing is sent, so closing the browser or unplugging the Mac stops it by
  itself. On connecting, a whole frame is sent; the page's **Refresh** asks for another.
- **The unit is unchanged**: same panel, same clock, same foreign SysEx (the keepalive is consumed; any other message goes on its way).
  Its own RAM at `0x2004CA00` (952 B, outside the stock `.bss`), code in the `dspz` gap (596 B at `0x080694B0`). The LED task's stack grows
  by at most 24 B (measured; the build's worst case stays at 1,768 B of 1,792).

## What to test
1. Install and power on: the unit boots and behaves like 5.56 (steady clock, no restarts).
2. On the Mac: `python3 -m http.server 8765 --directory hosts/web`, open **http://localhost:8765** in Chrome, allow MIDI and pick the Circuit
   Tracks port. The whole grid lights up and follows the unit, and the header reads **`device firmware "CT2" · 7-bit SEQ`**. If it reads
   **`"CT" — PRE-FIX`**, the unit is still running the previous build: check that the uploader showed SHA `d56b1d91…` and **bootloader** mode
   (hard-reload the uploader page, it may be a cached whitelist), then flash again. The page is telling the truth about what is running.
3. Compare with the panel: lit pads, the selected track, Play pulsing with the clock, the knobs, Master Filter. Turn pads on / off and change
   view (Preset, Mixer, FX, Patterns): the colours and brightness are the same, including the dimmed ones and the pulses.
4. Play notes and start / stop: the mirror follows the sound with no noticeable lag.
5. Leave the unit untouched for a while (stopped, static panel): the page must **not** dim — the device sends nothing while no LED changes, so
   silence is normal. The dim **stale** state appears only when no port is open or nothing has ever been received.
6. Close the page: traffic stops after 2 s (the unit is unaffected). Open it again: **Refresh**, or wait for the keepalive = a whole frame again.
7. Unplug and replug USB: the page lists the port again and reconnects.
8. Check that nothing else changed: patterns, patches, saving, the card app and the usual SysEx.

## Result on hardware (2026-11-16)

SC57.3 installed and confirmed both by the page and by `tools/probe_ledmirror.py`. `?diag` over 69 s with the sequencer playing:

| Check | Value |
|---|---|
| `device` | **`"CT2" · 7-bit SEQ`** (the old image said `"CT" — PRE-FIX`) |
| `seq gaps` | **0**, across several `127 → 0` wraps |
| `holes over 1.5 s` | **0** |
| `gap max` | 1002 ms — `?diag` forces a full frame every second, so on a still panel that is the probe itself, not a dropout |
| `paint max` | **0.3 ms** over 1233 frames |
| `long tasks` | none |
| `rtt` / `frame` | 4–25 ms / rtt + ~2 ms: one 16 ms LED tick plus USB, CoreMIDI and the page |

The log shows the whole cycle: a full frame every second (the probe), deltas at ~21 ms while something changes, single-LED deltas for a pulsing
button (Play, LED 12), and whole-grid chunks (42 + 8) on a view change.

## Emulation verification
- **`emu_ledmirror.py`** (new, 39 checks): both hooks; the panel buffer comes out **byte for byte identical** to a direct converter call (with a
  garbage state block, with the buffer in a peripheral range and over the 64 random frames); the block signs itself on the first tick; the
  keepalive is consumed and sets `LIVE` / `PORT`; a full frame in 48 + 21 through the stock router with the mask `1 << port` and `SEQ` counting
  up; one LED = a 17 B delta, 60 changes = 48 + 12, nothing changed = not a byte, the same tick twice = one (16 ms), 2 s of silence stop it and
  the next keepalive sends another full frame, type 1 forces it; every message carries the `"CT2"` signature (`0x32`) while the old `"CT "`
  base (`0x20`) is still accepted; a host model fed **only by the messages** = the converter's output over 64 frames (2 full, 63 delta); a
  foreign SysEx (identity request) is forwarded with its registers; `c_lm_tick`'s stack +24 B at most. **The
  emulation found a real bug:** the second chunk of a delta did not rewind the write pointer and repeated the first 48 entries with a stale
  `COUNT` (fixed in `c_lm_flush`).  **The hardware found another (2026-11-16):** `SEQ` was written as a raw 8-bit byte, and the stock router
  drops any message with a data byte >= `0x80` (`0x08020CCE`), so every 128 frames the stream died for ~2.9 s (measured with
  `tools/probe_ledmirror.py`: a 2.93 s hole, `SEQ` 127 -> 0, three times in 20 s).  It is masked to 7 bits now, and two checks cover it: 300
  forced full frames (600 messages) with **no message the router would drop**, and `SEQ` wrapping 127 -> 0 with no jump.  The host masks its
  own keepalive `SEQ` too and expects the 7-bit wrap (`hosts/test/proto.test.mjs`, 10 tests).
- **`emu_ledstack.py`**: 5.56's LED task worst case is unchanged (`1768 / 1720 / 1720`, limit 1,792). The tick's real path (with the router,
  with and without the mirror) measures **1,580 B** with an instrumented call.
- **`hosts/test/proto.test.mjs`** (10 tests): the host parser against **5 real messages** from this build (`tools/ledmirror_fixture.py` →
  `hosts/test/fixtures.json`), plus keepalive, chunks, deltas, foreign or malformed messages, `SEQ` gaps and the two firmware signatures.
- **`hosts/test/webmidi.html`** (in Chrome, no device): the real app over a fake Web MIDI; it lists the port, connects, sends keepalives and
  draws the 69 LEDs as the fixture's words say (68 lit: LED 30, code 31, is black in the stock palette). Run it plain for the pre-fix `"CT "`
  banner and with `?v2` for `"CT2"`.
- `emu_mfxhold.py`, `emu_fastatk.py`, `emu_mzswitch.py`, `emu_morph.py --clear --sus --nocur --four --slow`, `emu_blackbox.py`,
  `emu_blackbox2.py`, `emu_knobhold.py` on the build.
