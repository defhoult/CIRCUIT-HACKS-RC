"""Circuit Tracks 4486 - LM1: the official 1.2.1 firmware + the LED mirror, and nothing else.

The user (2026-11-19): «take the firmware back to being largely 'stock' (latest version of course) and only retain the parts of CIRCUIT-HACKS
that we are leveraging».  So this is not another link in the cumulative Hack chain: it starts from the official image (1.2.1, build 4486, the
latest in Components) and adds one module, tools/ledmirror.py, exactly as SC57.3 runs it.

What changes in Novation's image: the hidden 6-byte marker in the version block (0x1E8, so the bootloader agrees to install it) and the two
4-byte jumps the mirror needs: the LED converter call at 0x0803418C (inside the LED task) and the incoming-SysEx entry at 0x0801EB30.  The
mirror's 690 bytes go after the end of the image (0x0805CDDC, where ~140 KB are free).  Nothing else: no DSP change, no SD-card code, no
feature of the Hacks.  The unit behaves as stock; only the LEDs also stream over USB MIDI.

The RAM block is the one SC57.3 proved on hardware: LM = 0x2004CA00, 952 B (above the stock heap limit 0x20044E58, below the deepest the main
stack has been seen: 0x2004FC00), signed "LRM1" so the first tick zeroes it.

    python tools/build_lm1.py [STOCK.bin] [-o build/lm1]

The result is reproducible: `python tools/verify_builds.py lm1` rebuilds it from the official image and compares the .syx SHA-256.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ct_fw  # noqa: E402
import ledmirror as LM  # noqa: E402
import rdiag as RD  # noqa: E402
import build_shared_channels as sc  # noqa: E402

STOCK = ROOT / "stock/circuittracks-firmware-4486-STOCK.bin"
NAME = "LM1"
BASE = ct_fw.APP_BASE
MARK_OFF = sc.MARK_OFF                     # 0x1E8, the tail of the version block (0x1C8, 0x26 bytes)
MOD_START = 0x0805CDDC                     # the official image ends here


def build(stock: bytes):
    """The official image + LM1's marker + the mirror.  Returns (image, report)."""
    assert hashlib.sha256(stock).hexdigest() == ct_fw.STOCK_4486["image_sha256"], \
        "not the official 1.2.1 (build 4486) image: see stock/SHA256SUMS.txt"
    img = bytearray(stock)
    img[MARK_OFF:MARK_OFF + 6] = NAME.encode().ljust(6, b"\x00")[:6]

    while len(img) % 4:                    # install() appends at the file's end: make that the aligned base it is told about
        img += b"\xff"
    at = BASE + len(img)
    assert at == MOD_START, f"the official image should end at {MOD_START:#x}, not {at:#x}"

    report = {}
    labels, code, prev = LM.install(img, report, at)
    report["ledmirror"] = dict(
        arm_start=hex(at),
        labels={k: hex(v) for k, v in labels.items()},
        prev={k: (hex(v) if isinstance(v, int) else v) for k, v in prev.items()},
        listing=sc.disasm(bytes(code), at),
        stock_only=True,
        ram=hex(LM.LM),
        ram_size=hex(LM.SIZE),
        hooks=[hex(LM.LED_HOOK), hex(RD.HOOK)],
    )
    report["D"] = dict(start=hex(BASE + MARK_OFF), marker=NAME)
    img = bytes(img)

    # the guarantee: Novation's bytes change only at the marker and the two hooks; everything else is appended after the official image
    allowed = set(range(MARK_OFF, MARK_OFF + 6))
    allowed |= set(range(LM.LED_HOOK - BASE, LM.LED_HOOK - BASE + 4))
    allowed |= set(range(RD.HOOK - BASE, RD.HOOK - BASE + 4))
    changed = [i for i in range(min(len(stock), len(img))) if stock[i] != img[i]]
    bad = [i for i in changed if i not in allowed]
    assert not bad, f"stock bytes changed outside the marker / hooks: {[hex(BASE + i) for i in bad]}"
    report["stock"] = dict(
        image=hex(len(stock)),
        changed=len(changed),
        appended=len(img) - len(stock),
        marker=img[MARK_OFF:MARK_OFF + 6].decode("ascii"),
    )
    return img, report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stock_bin", type=Path, nargs="?", default=STOCK)
    ap.add_argument("-o", "--outdir", type=Path, default=ROOT / "build" / NAME.lower())
    args = ap.parse_args(argv)

    img, report = build(args.stock_bin.read_bytes())
    syx = ct_fw.encode(img)
    info = ct_fw.decode(syx)
    assert info["image"] == img

    args.outdir.mkdir(parents=True, exist_ok=True)
    (args.outdir / f"circuittracks-4486-{NAME}.bin").write_bytes(img)
    (args.outdir / f"circuittracks-4486-{NAME}.syx").write_bytes(syx)
    man = dict(base="circuittracks-firmware-4486 (1.2.1)", marker=NAME,
               image_size=hex(len(img)), image_sha256=ct_fw.sha256(img),
               syx_sha256=ct_fw.sha256(syx), crc32=hex(info["crc"]), report=report)
    (args.outdir / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")

    s = report["stock"]
    print(f"{NAME}: image {hex(len(img))}, {s['changed']} stock bytes changed + {s['appended']} appended, "
          f"crc {man['crc32']}, syx {man['syx_sha256']}")
    print(f"  {args.outdir}")


if __name__ == "__main__":
    main()
