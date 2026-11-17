# Circuit Tracks LED mirror — host apps

The Circuit Tracks has no screen: its **69 LEDs are the whole UI**. Firmware **SC57.3** streams them over USB MIDI, and these hosts draw them on
the panel's own layout — same colour, same brightness, same pulses — without pretending to be the instrument: the keys are flat, a lit LED is
filled with its colour, and nothing on the page is clickable.

```
   Circuit Tracks (SC57.3)                      Mac / Android
   ┌──────────────────────┐   USB MIDI    ┌───────────────────────┐
   │ LED tick             │  SysEx 0x7D   │ hosts/web/ (renderer) │
   │  0x0803423C converter│ ────────────► │  proto.js  parse      │
   │  → 69 × B,G,R words  │  0x43 0x54    │  Mirror    state      │
   └──────────────────────┘  0x32 …        │  app.js    panel      │
                                           └───────────────────────┘
```

## 1. Flash the firmware

`build/sc57.3/circuittracks-4486-SC57.3.syx` (or `firmware/sc57.3/`, same bytes, SHA-256 `d56b1d91…`) — see `firmware/sc57.3/PRUEBA.md`.
Without SC57.3 the page has nothing to show: the stock firmware does not stream. **Do not use `sc57.2/`:** it kept the old version block and
the bootloader skips it.

After flashing, open the page: the device line must read **`device firmware "CT2" · 7-bit SEQ`**. If it says **`"CT" — PRE-FIX`**, the unit is still
running the previous build (the flash did not take). The command base is the firmware's own signature — see section 4.

## 2. Serve the page

Web MIDI needs a secure context and ES modules need HTTP, so serve the folder (don't open `index.html` from `file://`):

```sh
python3 -m http.server 8765 --directory hosts/web
```

Then open **<http://localhost:8765/>** in **Chrome** or **Edge** (Safari and Firefox have no Web MIDI), press **Allow** when it asks for MIDI
access, and click the MIDI port named after the Circuit Tracks. The grid fills in immediately and follows the hardware.

No hardware? Add **`?demo`** — a fake device drives the same renderer with the same protocol bytes. Add **`&old`** to make the fake device
identify itself as the pre-fix build, which shows the red warning the page gives when the flash did not take:

```
http://localhost:8765/?demo
http://localhost:8765/?demo&old
```

## 3. What you see

The page draws the panel in the order it is printed on the hardware (the mock-up the user supplied, 2026-11-17), so a key is where your
finger expects it, but each key is still looked up by LED index:

| Row | What is there | Indices |
|---|---|---|
| Knobs | macros 1-8 (a caption under each) and the master filter, with the filter's wave mark | 60-68 |
| Functions | Scales · Oct ▼ · Oct ▲ · 1-16 / 17-32 · Tempo / Swing · Clear · Duplicate · Save · Projects · Shift, with the silkscreen above (Tap, Click, Mutate, Setup, Packs) | 50-59 |
| Tracks | Preset · Synth 1 / 2 · MIDI 1 / 2 · Drum 1-4 · Patterns (View Lock) | 40, 32-39, 45 |
| Main | Note (Expand) · Velocity (Fixed) · Gate (Micro Step) · Pattern Settings (Probability), pads 1-32, Mixer · FX (Side Chain) · REC (Quantise) · PLAY | 41-44, 0-31, 46-49 |

A lit cell is filled with the LED's own colour and glows; a dark one is a key in the page's colour, and the label flips to dark type on a bright
colour. The two-line words on a key and the words above the top row are silkscreen, not state — they never change. The knob captions are a
fixed legend too: the unit does not report its patches' macro names, so `KNOB_CAPS` in `tools/ledmap.py` decides what they say.

The panel dims and marks itself **stale** when there is no stream at all — no port open, or a port that has not delivered a single frame
(stock firmware, or the wrong port) — and when the MIDI port disappears while in use. It deliberately does **not** dim on silence: the device
sends nothing while no LED changes and never acknowledges a keepalive, so a static panel is silent by design. **Refresh** asks for a full
frame; **Disconnect** stops the keepalives.

The whole panel is measured in 1/1046 of its own width, so it scales as one picture and is capped to fit the window's height (no scrolling on a
laptop). Below ~680 px it keeps a 620 px minimum and the page scrolls sideways rather than shrinking the words to nothing; phone tuning waits
for the Android shell.

## 4. The protocol (firmware → host)

All messages are SysEx with the manufacturer id `0x7D` (educational / private) and the tag `0x43 0x54 0x32` ("CT2"). The fifth byte is also the
**firmware signature**: `0x32` ("CT2") is this build; the pre-2026-11-16 build sent `0x20` ("CT "). The host accepts both and says which one it
is talking to, so a flash that did not take is visible immediately.

```
F0 7D 43 54 32 01 SEQ START COUNT n0 n1 n2 n3 n4 n5 …                       F7   full chunk
F0 7D 43 54 32 02 SEQ START COUNT IDX n0 n1 n2 n3 n4 n5 …                   F7   delta
```

* Six nibbles per LED = bits 23..0 of the word `R<<16 | G<<8 | B`, high nibble first. The firmware sends the **stock converter's own output**,
  so dimming and pulses arrive already applied.
* A full chunk is 48 LEDs (298 B, under the router's `0x169` limit), so a frame is `48 + 21` in two messages. A delta carries only the LEDs
  that changed (7 bytes each), split at 48 too; a tick with no change sends nothing.
* Changes are throttled to one frame per 16 ms; the first frame after a connect is always full.

Host → device (one message every 250 ms; no reply, no ACK):

```
F0 7D 43 54 32 00 SEQ F7       keepalive
F0 7D 43 54 32 01 SEQ F7       keepalive + send a full frame on the next tick
```

The device also accepts the old `0x20` base, so an older cached page can still drive a new unit. Until the first message arrives the host
sends keepalives on both bases; after that it talks to the base the device used.

The device streams only while a keepalive is recent (**2 s**), so unplugging the Mac, closing the page or killing the browser stops the traffic
by itself. The `SEQ` byte counts up **0..127** (a SysEx data byte must be < `0x80`: the stock router drops the whole message otherwise) and the
host expects the 7-bit wrap; a real gap makes it ask for a full frame.

## 5. Tests

The host parser and state are tested against **bytes captured from the real firmware** (not hand-written ones):

```sh
python3 tools/ledmirror_fixture.py build/sc57.3/circuittracks-4486-SC57.3.bin build/sc57.3/manifest.json
node --test hosts/test/proto.test.mjs
```

`hosts/test/fixtures.json` holds five real messages (full frame, delta, silence, resync) and the expected 69 words. The firmware side is
`tools/emu_ledmirror.py` (39 checks, including a 64-frame randomised run where a host model fed only by the messages must equal the converter,
600 messages that must all be valid for the stock router, and the "CT2" signature with the old "CT " base still accepted).

The **transport path** (ports, connect, keepalives, incoming messages, the grid itself, the firmware banner) is tested in a browser without
hardware: `hosts/test/webmidi.html` runs the real app from `../web/` over a fake `navigator.requestMIDIAccess`, feeds it the same five real
messages with a forced command base, and checks every one of the 69 cells against the fixture's words plus the `#dev` banner:

```sh
# port 8766 so it does not collide with the app on 8765 (section 2)
python3 -m http.server 8766 --directory hosts
# Chrome: http://localhost:8766/test/webmidi.html       -> "CT "  (PRE-FIX banner)
#         http://localhost:8766/test/webmidi.html?v2    -> "CT2" (7-bit SEQ banner)
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --dump-dom \
  --virtual-time-budget=6000 http://localhost:8766/test/webmidi.html?v2 | grep -o "<title>[^<]*</title>"
```

(The fixture's frame is 69 codes `(i % 0xBF) + 1`, and one of them — LED 30, code 31 — is black in the stock palette, so 68 cells lit is
correct: the test compares each cell with the expected word rather than counting to 69.)

## 6. Diagnostics

When it feels laggy or flaky, measure instead of guessing.

**In the page:** open **<http://localhost:8765/?diag>**, connect, reproduce the problem, then **Copy log** (or **Download**). It shows, live:

| Line | What it means |
|---|---|
| `rtt` | full-frame round trip: request → the device's LED tick → the router → USB → CoreMIDI → the page. Last / average / max, and how many probes were answered |
| `frame` | the same probe until the **last** chunk of the 69 LEDs arrives |
| `msgs/s`, `B/s` | traffic, over the last 5 s |
| `seq gaps` | messages the host never got (a real gap, not the 7-bit wrap) |
| `device` | which firmware the unit is running, from the command base it sends: `"CT2" · 7-bit SEQ` or `"CT" — PRE-FIX` |
| `gap` | the longest silence between messages, and how many holes over 1.5 s. `?diag` forces a full frame every second, so on a still panel this reads about **1 s** and that is the probe, not a dropout; a real freeze is a hole over 1.5 s. A hole that runs **127→0** is the pre-fix firmware's `SEQ` wrap; the line lists each hole's `SEQ` pair |
| `paint` | time spent in the page's own drawing code (per frame / worst) |
| `long tasks` | main-thread stalls over 50 ms |
| `tab` / `hidden` | whether the tab was **background/occluded** — Chrome defers painting and throttles timers there, which looks exactly like lag |

The log is JSON: the summary plus every message's arrival time, so clumping, holes and reconnects can be read off afterwards.

**Outside the browser:** `tools/probe_ledmirror.py` speaks the same protocol from Python (`mido`), so it separates the device and USB from
Chrome:

```sh
.venv/bin/python tools/probe_ledmirror.py --seconds 20 --json /tmp/probe.json
```

It sends keepalives (on both command bases until the device identifies itself), asks for a full frame every second and reports the round trip,
the arrival cadence, sequence gaps, the firmware signature and every hole with its `SEQ` pair, then prints a one-line **PASS / FAIL** verdict.
This is what found the 2.9 s freeze every 128 frames (`SEQ` >= `0x80` dropped by the stock router, fixed 2026-11-16). **Run it with the
sequencer playing**: a static panel only produces ~5 messages/s, so `SEQ` takes a minute to reach 127 and a short run proves nothing. The port
can be shared with the browser, but for a clean baseline close the page (otherwise the page's own full-frame requests make the round-trip
figures meaningless).

The page and the probe both stamp the **host build** (`Host 2026-11-17 · panel layout · 7-bit SEQ · CT2` in the footer, and `host` in the `?diag` JSON), so an old
cached page is obvious. The page also resynchronises the moment the tab becomes visible again: a background or occluded tab has its timers
frozen, so the keepalives stop, the device pauses 2 s later, and without this the picture would stay stale until some delta happened to arrive.

## 7. Files

| File | Purpose |
|---|---|
| `web/index.html` | the page |
| `web/app.js` | transport (Web MIDI / Android bridge), panel DOM, keepalives, stats, the `?diag` measurements |
| `web/proto.js` | protocol parser, `Mirror` state, colour helpers — DOM-free, used by the tests |
| `web/demo.js` | the `?demo` fake device (it builds real protocol messages) |
| `web/style.css` | the panel: layout in `u` (= 1/1046 of its width), the key and container colours |
| `web/panel.json` | the 69 LEDs and the panel's rows, generated by `tools/ledmap.py` |
| `test/proto.test.mjs` | Node tests |
| `test/fixtures.json` | real messages, generated by `tools/ledmirror_fixture.py` |
| `test/webmidi.html` | the app over a fake Web MIDI, checked in a browser (no hardware needed) |

## 8. Android (next)

Android WebView has no Web MIDI, so the phone shell hosts the same renderer and bridges `android.media.midi` into it. `app.js` already detects
`window.CircuitBridge` and uses it instead of Web MIDI:

| The renderer calls | The shell must answer |
|---|---|
| `CircuitBridge.listPorts()` | JSON string: `[{"id":"…","name":"Circuit Tracks …"}, …]` |
| `CircuitBridge.open(id)` | open the port and start `MidiReceiver` callbacks |
| `CircuitBridge.send(base64)` | write those bytes to the port's `MidiOutputPort` |
| `CircuitBridge.name(id)` | the port's name, for the status line |
| `CircuitBridge.close()` | close input / output |

and the shell calls into the WebView:

| The shell calls | Effect |
|---|---|
| `window.__midiMessage(base64)` | one received SysEx message |
| `window.__midiPorts()` | MIDI devices changed: re-list them |
| `window.__midiGone()` | the device disappeared |

The same USB MIDI interface is used, so the phone needs a USB-C OTG adapter or the Circuit Tracks' USB connection through a hub.
