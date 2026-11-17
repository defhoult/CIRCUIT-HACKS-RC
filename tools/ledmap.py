"""The panel's 69 LEDs, as data for the hosts (tools/ledmap.py).

The Circuit Tracks has no screen: these 69 LEDs are its whole UI, and `tools/ledmirror.py` streams their exact words (u32 = R << 16 | G << 8 | B,
per LED, in this order) to the host.  The order is the stock converter's (0x0803423C), verified against the LED task and the panel's silkscreen:

    0..31   pads 1..32 (index = pad - 1)
    32..39  the track buttons: Synth 1, Synth 2, MIDI 1, MIDI 2, Drum 1, Drum 2, Drum 3, Drum 4
    40..59  Preset, Note, Velocity, Gate, Pattern Settings, Patterns, Mixer, FX, Record, Play, Scales, Oct ▼, Oct ▲, 1-16 / 17-32, Tempo,
            Clear, Duplicate, Save, Swing, Shift
    60..67  Macro 1..8
    68      Master Filter

`python tools/ledmap.py` writes hosts/web/panel.json (the section layout the host draws, plus one entry per LED).  The host never needs anything else:
a label, a group and a cell are enough to draw a clean functional grid rather than a copy of the panel.
"""
from __future__ import annotations
import json
from pathlib import Path

TRACKS = ["Synth 1", "Synth 2", "MIDI 1", "MIDI 2", "Drum 1", "Drum 2", "Drum 3", "Drum 4"]
FUNCTIONS = ["Preset", "Note", "Velocity", "Gate", "Pattern Settings", "Patterns", "Mixer", "FX",
             "Record", "Play", "Scales", "Oct \u25bc", "Oct \u25b2", "1-16 / 17-32", "Tempo",
             "Clear", "Duplicate", "Save", "Swing", "Shift"]
MACROS = [f"Macro {i + 1}" for i in range(8)] + ["Master Filter"]

# (id, title, kind, columns, indices, hint): the host's grid, top to bottom
SECTIONS = [
    ("tracks", "Tracks", "button", 8, list(range(32, 40)), "the eight track buttons"),
    ("functions", "Functions", "button", 10, list(range(40, 60)), "the transport / view buttons"),
    ("pads", "Pads", "pad", 8, list(range(0, 32)), "pads 1-32"),
    ("macros", "Macros", "knob", 9, list(range(60, 69)), "the eight macros and the master filter"),
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


def panel():
    L = leds()
    assert [x["index"] for x in L] == list(range(69))
    assert len({x["name"] for x in L}) == 69
    for sid, _, _, _, idx, _ in SECTIONS: assert all(L[i]["group"] == sid for i in idx)
    return dict(version=1, protocol="F0 7D 43 54 32 <type> <seq> ... F7", leds=L,
                sections=[dict(id=sid, title=t, kind=k, columns=c, leds=idx, hint=h)
                          for sid, t, k, c, idx, h in SECTIONS])


def main():
    p = panel()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(p, indent=1, ensure_ascii=False) + "\n")
    print(f"{OUT.relative_to(OUT.parents[2])}: {len(p['leds'])} LEDs, {len(p['sections'])} sections")
    for s in p["sections"]: print(f"  {s['id']:10s} {len(s['leds']):2d}  {s['title']}")


if __name__ == "__main__":
    main()
