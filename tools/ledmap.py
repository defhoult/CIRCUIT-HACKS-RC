"""The panel's 69 LEDs, as data for the hosts (tools/ledmap.py).

The Circuit Tracks has no screen: these 69 LEDs are its whole UI, and `tools/ledmirror.py` streams their exact words (u32 = R << 16 | G << 8 | B,
per LED, in this order) to the host.  The order is the stock converter's (0x0803423C), verified against the LED task and the panel's silkscreen:

    0..31   pads 1..32 (index = pad - 1)
    32..39  the track buttons: Synth 1, Synth 2, MIDI 1, MIDI 2, Drum 1, Drum 2, Drum 3, Drum 4
    40..59  Preset, Note, Velocity, Gate, Pattern Settings, Patterns, Mixer, FX, Record, Play, Scales, Oct ▼, Oct ▲, 1-16 / 17-32, Tempo,
            Clear, Duplicate, Save, Projects, Shift
    60..67  Macro 1..8
    68      Master Filter

`python tools/ledmap.py` writes hosts/web/panel.json.  It carries two things: one entry per LED (index, label, name, group) and the panel's
*rows* in the order the hardware prints them (the knobs on top, then the two button rows, then the pads between the transport buttons).  The
host draws that layout, so a key sits where the hardware's key sits and the same cell is still looked up by LED index.  The secondary words
under a key (View Lock, Expand, …) and the words printed above the top row (Tap, Click, …) are silkscreen, not state: they never change.
"""
from __future__ import annotations
import json
from pathlib import Path

TRACKS = ["Synth 1", "Synth 2", "MIDI 1", "MIDI 2", "Drum 1", "Drum 2", "Drum 3", "Drum 4"]
FUNCTIONS = ["Preset", "Note", "Velocity", "Gate", "Pattern Settings", "Patterns", "Mixer", "FX",
             "Record", "Play", "Scales", "Oct \u25bc", "Oct \u25b2", "1-16 / 17-32", "Tempo",
             "Clear", "Duplicate", "Save", "Projects", "Shift"]
MACROS = [f"Macro {i + 1}" for i in range(8)] + ["Master Filter"]

# The 8 macros' captions are the patch's macro names on the mock-up.  The unit does not report them, so they are a fixed legend (change these
# strings if the patch you play names its macros differently); the master filter is silkscreened.
KNOB_CAPS = ["Oscillator", "Oscillator Mod", "Amp Envelope", "Filter Envelope",
             "Filter Frequency", "Resonance", "Modulation", "FX", "Master Filter"]


def _k(led, text=None, **kw):
    d = {"led": led}
    if text is not None: d["text"] = text
    d.update(kw)
    return d


# The panel, in silkscreen order.  `kind` says how a row is drawn: `knobs` = nine circles with a caption under each, `keys` = a side key, eight
# keys and a side key, `main` = a stack of four keys, the pad grid, a stack of four keys.  `text` overrides the LED's label where the panel
# prints it on two lines; `above` is the word printed above a top-row key; `caption` is the second word printed on a key (or under a knob).
ROWS = [
    dict(id="macros", kind="knobs",
         items=[_k(60 + i, str(i + 1), caption=KNOB_CAPS[i]) for i in range(8)]
               + [_k(68, icon="filter", caption="Master Filter")]),
    dict(id="functions", kind="keys",
         left=[_k(50, "Scales")],
         center=[_k(51, "Oct \u25bc"), _k(52, "Oct \u25b2"), _k(53, "1-16\n17-32"),
                 _k(54, "Tempo\nSwing", above="Tap"), _k(55, "Clear", above="Click"),
                 _k(56, "Duplicate", above="Mutate"), _k(57, "Save", above="Setup"),
                 _k(58, "Projects", above="Packs")],
         right=[_k(59, "Shift")]),
    dict(id="tracks", kind="keys",
         left=[_k(40, "Preset")],
         center=[_k(32 + i, TRACKS[i]) for i in range(8)],
         right=[_k(45, "Patterns", caption="View Lock")]),
    dict(id="main", kind="main",
         left=[_k(41, "Note", caption="Expand"), _k(42, "Velocity", caption="Fixed"),
               _k(43, "Gate", caption="Micro Step"), _k(44, "Pattern Settings", caption="Probability")],
         center=list(range(32)),
         right=[_k(46, "Mixer"), _k(47, "FX", caption="Side Chain"),
                _k(48, "REC", caption="Quantise"), _k(49, "PLAY")]),
]

OUT = Path(__file__).resolve().parents[1] / "hosts" / "web" / "panel.json"


def leds():
    out = []
    for i in range(32): out.append(dict(index=i, label=str(i + 1), name=f"Pad {i + 1}", group="pads"))
    for i, n in enumerate(TRACKS): out.append(dict(index=32 + i, label=n, name=n, group="tracks"))
    for i, n in enumerate(FUNCTIONS): out.append(dict(index=40 + i, label=n, name=n, group="functions"))
    for i, n in enumerate(MACROS): out.append(dict(index=60 + i, label=n.replace("Macro ", "K").replace("Master Filter", "Filter"),
                                                   name=n, group="macros"))
    return out


def _indices(node):
    """every `led` in the layout, in reading order"""
    if isinstance(node, dict):
        if "led" in node: return [node["led"]]
        return [i for v in node.values() for i in _indices(v)]
    if isinstance(node, list): return [i for v in node for i in _indices(v)]
    if isinstance(node, int): return [node]
    return []


def panel():
    L = leds()
    assert [x["index"] for x in L] == list(range(69))
    assert len({x["name"] for x in L}) == 69
    seen = _indices(ROWS)
    assert sorted(seen) == list(range(69)), f"the layout must place every LED once: {sorted(set(range(69)) - set(seen))} missing"
    for r in ROWS:
        for k in ("left", "center", "right"):
            for it in r.get(k, []):
                if isinstance(it, int): assert L[it]["group"] == "pads"     # the pad block: plain indices
                else: assert L[it["led"]]["group"] in ("pads", "tracks", "functions", "macros")
    return dict(version=2, protocol="F0 7D 43 54 32 <type> <seq> ... F7", leds=L, rows=ROWS)


def main():
    p = panel()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(p, indent=1, ensure_ascii=False) + "\n")
    print(f"{OUT.relative_to(OUT.parents[2])}: {len(p['leds'])} LEDs, {len(p['rows'])} rows")
    for r in p["rows"]: print(f"  {r['id']:10s} {r['kind']:6s} {len(_indices(r)):2d} LEDs")


if __name__ == "__main__":
    main()
