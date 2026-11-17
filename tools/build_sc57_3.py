"""Circuit Tracks 4486 - SC57.3 = SC57.2 (LED mirror over USB MIDI) with the CT2 signature and a new version-block marker.

SC57.2's CT2 rebuild kept the marker "SC57.2", so the bootloader skipped it: it installs only when the version block (0x1C8, 0x26 bytes,
whose tail is the 6-byte marker) differs from the installed image.  Three flashes therefore reported success and left the old build running
(the device kept emitting "CT " and its 127 -> 0 holes).  A new name changes the block, which is what the repo's own verify_builds.py
requires (SC54.1 / SC32D.2 hit the same thing).  The code is otherwise identical to SC57.2: same hooks, same 7-bit SEQ, same CT2 signature.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_sc57 as S  # noqa: E402
import linein3  # noqa: E402
import morphz  # noqa: E402
import tabpack  # noqa: E402

NAME = "SC57.3"
B = S.B


def extra(img, report):
    import dspz
    import mfxhold
    import ledmirror
    S.extra(img, report)
    dspz.add(img, report, "mfxhold", mfxhold)
    dspz.add(img, report, "ledmirror", ledmirror)


if __name__ == "__main__":
    tabpack.use()
    import patmut
    patmut.use_v2()
    S.S.S.S.S.S.S.S.S.S.S.room()
    S.S.S.S.S.S.S.S.S.S.cpu()
    morphz.use_four()
    morphz.use_slow()
    morphz.use_rrfix()
    morphz.use_newstop()
    morphz.use_fastatk()
    import fmmap
    fmmap.use_lrram()
    patmut.use_lrram()
    import knobhold
    knobhold.use_freeze()
    import blackbox
    blackbox.use_v2()
    b5 = B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B.B9.B8.B7.B6.B5.B4.B2.B1.B.B.b33.b32.b31.b30.b29.b28.b27.b26.b25.b24.b223.b22.b182.b18.b6.b5
    linein3.main(NAME, b5.build, b5.s15, sys.argv[1:], extra=extra)
