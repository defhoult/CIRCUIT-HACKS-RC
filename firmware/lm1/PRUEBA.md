# LM1 = the official 1.2.1 firmware (build 4486) + the LED mirror, and nothing else

| File | SHA-256 | Marker |
|---|---|---|
| `firmware/lm1/circuittracks-4486-LM1.bin` | `01ad3a566fbd5a82a0f6fc827686f5a8c1109956b32191e8f86a038e91194a15` | `LM1` |
| `firmware/lm1/circuittracks-4486-LM1.syx` | `8da279b1095a76a147e7b6b368951490c1b96511688e564b524fcf1c97312dcf` | `LM1` |

Rebuilt by `tools/build_lm1.py` from `stock/circuittracks-firmware-4486-STOCK.bin`
(`b897e0f0…`); `python tools/verify_builds.py lm1` reproduces the `.syx` byte for byte.

## What this build is

Not another link in the CIRCUIT-HACKS chain. On 2026-11-19 the decision was to take the unit **back to stock** (the
latest official version, 1.2.1 / build 4486) and keep **only the one part of the Hacks in use: the LED mirror**. Every
other feature of the mod (morph, pattern mutate, black box, input, the FM extras, sidechain, the custom engines, the
extra views…) is **not** in this build.

Concretely, compared with Novation's own image:

- **10 bytes of the image change:** 3 bytes of the hidden version marker (so the bootloader agrees to install it), the
  `b.w` that takes over the incoming-SysEx entry (`0x0801EB30`), and the `bl` that calls the LED converter
  (`0x0803418C`). Nothing else of Novation's code is touched.
- **692 bytes are appended** after the end of the image (`0x0805CDDC`, where ~140 KB are free).
- Its RAM (952 B at `0x2004CA00`) sits above the stock heap limit (`0x20044E58`) and far below the deepest the main
  stack has been seen (`0x2004FC00`); the block signs itself (`LRM1`) on the first LED tick.

The unit **reports build 4486** to Novation Components: to the outside world it is the official 1.2.1. Only the
hidden marker says `LM1`.

## What the mirror does (unchanged from SC57.3)

The Circuit Tracks has no screen: its **69 LEDs are the UI**. This build copies what the stock LED converter produces
(colour with dimming and pulses already applied) and sends it over USB MIDI, so `hosts/web` can draw the same 69 LEDs
on the Mac: same colour, same brightness, same pulses.

- Full frame (6 nibbles per LED), delta (only what changed) and a host keepalive; chunks of 48 LEDs through the stock
  SysEx router; `SEQ` is 7-bit (`0..127`) because the router drops data bytes ≥ `0x80`.
- The fifth protocol byte is the signature: `0x32` ("CT2", 7-bit `SEQ`) in this build; the page reads it, and still
  accepts the old `0x20` ("CT ") on the way in.
- With no keepalive for 2 s nothing is sent: closing the page or unplugging the Mac stops it by itself.
- Foreign SysEx is untouched; only the mirror's own keepalive is consumed.

The mirror code is `tools/ledmirror.py`, byte for byte the module SC57.3 ran on hardware; this build only changes where
it is installed (after the official image) and which marker it carries.

## What to test on the unit

1. **Install**: open `tools/rc_uploader.html` in Chrome (hard-reload it so its version list is current), pick
   **LM1 — official 1.2.1 + the LED mirror only**, give it your official `.syx` or `.bin`. Put the unit in
   **bootloader** mode (hold **Scales + Preset + Note** while powering on) and send. The page must report the SHA
   `8da279b1…` at the end. During the send the unit asks for confirmation: press **Play**.
2. **It is stock**: power on, and use it exactly as before. The clock is steady, patterns and patches play, the card
   app works, saving works, no restarts. Nothing new on the panel.
3. **The mirror**: on the Mac, `python3 -m http.server 8765 --directory hosts/web`, open **http://localhost:8765** in
   Chrome, allow MIDI and pick the Circuit Tracks port. The whole grid lights up and follows the unit; the header reads
   **`device firmware "CT2" · 7-bit SEQ`**.
4. **Compare with the panel**: lit pads, the selected track, Play pulsing with the clock, the knobs, Master Filter. Turn
   pads on and off and change view (Preset, Mixer, FX, Patterns): the colours and the brightness match, including the
   dimmed ones and the pulses. Play notes and start / stop: the mirror follows the sound with no noticeable lag.
5. **Silence is normal**: leave the unit untouched (stopped, static panel) and the page must **not** dim. The device
   sends nothing while no LED changes.
6. **Close the page**: traffic stops after 2 s and the unit is unaffected. Open it again: **Refresh**, or wait for the
   keepalive, and a whole frame comes back. Unplug and replug USB: the page lists the port again and reconnects.
7. **Nothing else changed**: patterns, patches, the SD card app, the usual SysEx.

## Going back to stock

Pick **Official — Novation unchanged** in the same page (or use Novation Components, 1.2.1). The official image is
installed untouched; the unit is then exactly as it left Novation.

## Verification done here (no hardware needed)

- `emu_ledmirror.py` on this build (39 checks): both hooks; the panel buffer comes out byte for byte identical to a
  direct converter call; the block signs itself on the first tick; keepalive consumed; full frame in 48 + 21 through the
  stock router; one LED = a 17 B delta; nothing changed = not a byte; 2 s of silence stop it and the next keepalive sends
  a full frame; every message carries the `CT2` signature while `CT ` is still accepted; a host model fed only by the
  messages equals the converter's output over 64 frames; foreign SysEx forwarded; the LED task's stack +24 B at most.
- `hosts/test/proto.test.mjs` (10 tests) and `hosts/test/webmidi.html` (in Chrome, no device): parser and page unchanged.
- `tools/verify_builds.py lm1`: rebuilds the `.syx` from the official image and compares the SHA-256.
- `cmp` against the official image: exactly the 10 bytes above differ; the rest is the official file plus the appended
  code.

## Result on hardware (2026-11-19)

Flashed and confirmed on the unit: **LM1 installed, and the unit is stock**.

| Check | Value |
|---|---|
| mirror page | `hosts/web` connects to the `Circuit Tracks MIDI` port and draws the 69 LEDs; colours, brightness and pulses match the panel |
| `device` | **`"CT2" · 7-bit SEQ`** |
| **it is stock** | with the page open, **Mixer** held for ~1 s does **not** jump to Master FX (that is a Hacks feature) — the view stays on Mixer, so the Hack is not in the build |
| `tools/probe_ledmirror.py` (25 s) | **1198 messages**, 47.8/s, **0 `SEQ` gaps**, **0 holes** over 1.5 s; a delta every ~21 ms (p99 23 ms) |
| full frames | 25 requests → 25 answers, each `48 + 21` LEDs, second chunk 1.2 ms after the first; first chunk ~14 ms after the request |

The `.syx` that went in is the one in this folder (SHA-256 `8da279b1…`); the unit still reports build 4486 to Components, as it should.
