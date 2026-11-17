"""Dump a few real LED-mirror messages (tools/ledmirror.py, as the firmware sends them) for the host's tests.

    python tools/ledmirror_fixture.py SC57.2.bin SC57.2_manifest.json  -> hosts/test/fixtures.json

The messages come from the same emulator the firmware is tested with (tools/emu_ledmirror.py), so hosts/test/proto.test.mjs checks the host's
parser against bytes the device really produces, not against bytes the host wrote itself.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import emu_ledmirror as T  # noqa: E402


def main(img, man):
    e = T.EM(img, man)
    codes = [(i % 0xBF) + 1 for i in range(69)]
    e.set_frame(codes)
    out = {"note": "hosts/test/fixtures.json: real messages from the SC57 LED-mirror build, tools/emu_ledmirror.py", "cases": []}

    def case(name, msgs, words):
        out["cases"].append(dict(name=name, messages=[m[2].hex() for m in msgs], words=words))

    # a full frame (two chunks), on a fresh state block (the first tick signs it; a keepalive before that is lost)
    e.uc.mem_write(T.LM.LM, b"\x00" * T.LM.SIZE)
    e.now(1000); e.tick()
    e.now(1008); e.keepalive(1, 7, 0); e.now(1010); e.tick()
    case("full frame on connect", list(e.out), e.ref())

    # a delta: three LEDs change
    codes[0] = 0x17; codes[33] = 0x49; codes[68] = 0x1A
    e.set_frame(codes)
    e.now(1026); e.tick()
    case("delta, three LEDs", list(e.out), e.ref())

    # nothing changed: no message at all
    e.now(1042); e.tick()
    case("no change, no message", list(e.out), e.ref())

    # a changed frame is lost: the host asks for a full one and gets it
    codes[5] = 0x03
    e.set_frame(codes)
    e.now(1058); e.tick()                       # this delta is "lost" (the fixture's next case is the resync)
    e.now(1074); e.keepalive(1, 8, 0); e.now(1082); e.tick()
    case("full frame on request", list(e.out), e.ref())

    dest = Path(__file__).resolve().parents[1] / "hosts" / "test" / "fixtures.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1) + "\n")
    print(f"{dest.relative_to(dest.parents[2])}: {len(out['cases'])} cases, "
          f"{sum(len(c['messages']) for c in out['cases'])} messages")


if __name__ == "__main__":
    if len(sys.argv) != 3: sys.exit(__doc__)
    main(Path(sys.argv[1]).read_bytes(), json.loads(Path(sys.argv[2]).read_text()))
