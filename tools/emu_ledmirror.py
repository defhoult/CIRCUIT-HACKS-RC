"""SC57.2 tests: the LED mirror (tools/ledmirror.py) on the RAM dump, called exactly as the LED tick calls it.

    python tools/emu_ledmirror.py SC57.2.bin SC57.2_manifest.json
  * the hooks: 0x0803418C (the converter call inside the LED tick) = bl c_lm_tick, 0x0801EB30 = b.w c_lm_in;
  * the panel path is untouched: `out` gets, byte for byte, what a direct converter call gives (the converter is run once, into the module's
    CUR, and copied out): with a garbage state block, a real peripheral out buffer, and every frame of the randomised run;
  * the state block signs itself on the first tick (MAGIC), zeroed, nothing streamed without a keepalive;
  * a keepalive is consumed (never forwarded), sets LIVE / PORT, and the next tick sends a full frame in chunks of 48 (48 + 21) through the
    stock router with the router object and the mask 1 << port, SEQ counting up;
  * a change sends a delta (index + 6 nibbles), unchanged LEDs nothing at all, >= 48 changes split in 48 + the rest, the same tick twice sends
    once (16 ms), 2 s of silence stops the stream, a keepalive after it sends a full frame again, type 1 forces one;
  * the command base is the build's signature: every message says "CT2" (0x32), while the incoming hook still accepts the pre-fix "CT " (0x20);
  * the host's model, fed only by the messages, equals the direct converter's output over 64 random frames (mixed full / delta traffic);
  * a foreign SysEx (identity request) is forwarded to whatever was at the hook (here blackbox's c_bbsx) with its registers restored;
  * the stack: c_lm_tick's frames on top of the converter's stay under 48 B (the LED task has 3 KB and a 1792 B limit: docs/NOTAS.md).
"""
from __future__ import annotations
import gc, json, struct, sys
from pathlib import Path
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_SP
sys.path.insert(0, str(Path(__file__).resolve().parent))
import emu_sc05 as T  # noqa: E402
import drums18  # noqa: E402
import ledmirror as LM  # noqa: E402
import rdiag as RD  # noqa: E402

MGR = 0x20040B10                                  # the LED manager object (the tick's argument and the converter's ctrl)
FRAME = 0x20061200                                # 69 halfwords: the code frame the converter reads
OUT = 0x20061400                                  # the caller's out: 69 words (the panel's bytes)
REF = 0x20061600                                  # a direct converter call's output, to compare with
PERIPH = 0x40000100                               # an out buffer in a device range (the panel may be one)
TOP = 0x2006F000                                  # emu_sc05's stack top for a call: depth = TOP - the lowest SP
SIG_V1 = (0xF0, 0x7D, 0x43, 0x54, 0x20)          # "CT " — the pre-fix build's command base, still accepted on the way in
SIG = (0xF0, 0x7D, 0x43, 0x54, 0x32)             # "CT2" — this build's signature
MSG, BUF = 0x20060000, 0x20060100


def un6(n):
    v = 0
    for x in n: v = (v << 4) | x
    return v


def parse(msg):
    assert msg[:4] == bytes(SIG[:4]) and msg[4] in (SIG[4], SIG_V1[4]) and msg[-1] == 0xF7, msg.hex()
    typ, seq, start, cnt, body = msg[5], msg[6], msg[7], msg[8], msg[9:-1]
    if typ == 1:
        assert len(body) == 6 * cnt, (len(body), cnt)
        return typ, seq, [(start + k, un6(body[6 * k:6 * k + 6])) for k in range(cnt)]
    assert typ == 2, typ
    assert len(body) == 7 * cnt, (len(body), cnt)
    return typ, seq, [(body[7 * k], un6(body[7 * k + 1:7 * k + 7])) for k in range(cnt)]


class EM(T.E):
    def __init__(self, img, man):
        gc.collect()
        super().__init__(img)
        rep = man["report"]["ledmirror"]
        self.lab = {k: int(v, 16) for k, v in rep["labels"].items()}
        self.cont = int(rep["prev"]["cont"], 16)
        self.out, self.fwd = [], []
        self.bad = []                    # messages the stock router would drop: a data byte >= 0x80 (0x08020CCE)
        def router(uc, a, s, d):
            m = uc.reg_read(UC_ARM_REG_R2); bp, ln = struct.unpack("<II", bytes(uc.mem_read(m, 8)))
            msg = bytes(uc.mem_read(bp, ln))
            if msg[:1] != b"\xf0" or msg[-1:] != b"\xf7" or any(b >= 0x80 for b in msg[1:-1]):
                self.bad.append(msg)     # the stock router validates and drops the whole message
            else:
                self.out.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1), msg))
            uc.reg_write(UC_ARM_REG_R0, 0); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)
        def fwd(uc, a, s, d):
            self.fwd.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)
        self.uc.hook_add(UC_HOOK_CODE, router, begin=RD.ROUTER, end=RD.ROUTER)
        self.uc.hook_add(UC_HOOK_CODE, fwd, begin=self.cont, end=self.cont)

    # ---- time
    def now(self, t=None):
        if t is None: return struct.unpack("<I", bytes(self.uc.mem_read(LM.HAL_TICK, 4)))[0]
        self.uc.mem_write(LM.HAL_TICK, struct.pack("<I", t))

    # ---- the LED tick's converter call, as the module sees it
    def tick(self, out=OUT, frame=FRAME):
        self.out.clear(); self.bad.clear()
        self.call(self.lab["c_lm_tick"], MGR, out, frame, maxins=500_000)

    # ---- a direct converter call: what the panel would get
    def ref(self, frame=FRAME, out=REF):
        self.call(LM.CONVERTER, MGR, out, frame, maxins=200_000)
        return list(struct.unpack("<69I", bytes(self.uc.mem_read(out, 276))))

    def set_frame(self, codes):
        self.uc.mem_write(FRAME, b"".join(struct.pack("<H", c) for c in codes))

    def st(self, off, n=4):
        return int.from_bytes(bytes(self.uc.mem_read(LM.LM + off, n)), "little")

    # ---- the stack: the lowest SP while `fn` runs (the LED task's budget: docs/NOTAS.md)
    def depth(self, fn):
        lo = [1 << 32]
        def hk(uc, a, s, d):
            sp = uc.reg_read(UC_ARM_REG_SP)
            if sp < lo[0]: lo[0] = sp
        h = self.uc.hook_add(UC_HOOK_CODE, hk)
        try: fn()
        finally: self.uc.hook_del(h)
        return TOP - lo[0]

    # ---- the host: a keepalive / full request
    def keepalive(self, typ=0, seq=1, port=0, ver=2):
        self.fwd.clear(); self.out.clear()
        data = list(SIG if ver == 2 else SIG_V1) + [typ, seq, 0xF7]
        self.uc.mem_write(BUF, bytes(data)); self.uc.mem_write(MSG, struct.pack("<II", BUF, len(data)))
        self.call(RD.HOOK, port, MSG, 0, maxins=200_000)


def main(img, man):
    fails = 0
    def check(c, m):
        nonlocal fails
        print(("ok  : " if c else "FAIL: ") + m); fails += 0 if c else 1
    L = {k: int(v, 16) for k, v in man["report"]["ledmirror"]["labels"].items()}
    check(drums18.decode_bw(bytearray(img), LM.LED_HOOK) == L["c_lm_tick"], "0x0803418C (the LED tick's converter call): bl c_lm_tick")
    check(drums18.decode_bw(bytearray(img), RD.HOOK) == L["c_lm_in"], "0x0801EB30 (the incoming SysEx dispatcher): b.w c_lm_in")
    e = EM(img, man)
    router_obj = e.r32(struct.unpack_from("<I", img, RD.ROUTER_LIT - 0x08010000)[0])
    codes = [(i % 0xBF) + 1 for i in range(69)]
    e.set_frame(codes)

    # ---- the first tick: the block signs itself, the panel still gets the converter's bytes, nothing is streamed
    e.uc.mem_write(LM.LM, b"\xAA" * LM.SIZE)
    ref = e.ref()
    e.uc.mem_write(OUT, b"\x55" * 276)
    e.tick()
    check(e.st(LM.O_MAGIC) == LM.MAGIC, f"the first tick signs the state block (MAGIC {LM.MAGIC:#x})")
    check(e.st(LM.O_LIVE) == 0 and e.st(LM.O_LAST) == 0 and e.st(LM.O_FULL, 1) == 0 and e.st(LM.O_SEQ, 1) == 0,
          "signed zeroed: LIVE, LAST, FULL, SEQ = 0")
    check(list(struct.unpack("<69I", bytes(e.uc.mem_read(OUT, 276)))) == ref, "the panel buffer = a direct converter call, word for word")
    check(bytes(e.uc.mem_read(LM.LM + LM.O_PREV, 276)) == bytes(276), "PREV zeroed")
    check(e.out == [], "no keepalive yet: nothing streamed")
    e.tick()
    check(e.out == [], "still nothing on the next tick")

    # ---- a keepalive: consumed, and the next tick sends the whole frame
    e.now(1000)
    e.keepalive(0, 3, 1)
    check(e.fwd == [] and e.out == [], "a keepalive is consumed (not forwarded), no reply")
    check(e.st(LM.O_LIVE) == 1000 and e.st(LM.O_PORT, 1) == 1 and e.st(LM.O_FULL, 1) == 1,
          "LIVE = the tick, PORT = 1, FULL set (first connect)")
    e.now(1008)
    e.tick()
    got = [parse(m[2]) for m in e.out]
    check(len(got) == 2 and got[0][0] == 1 and got[1][0] == 1, f"a full frame in 2 chunks: {[g[0] for g in got]}")
    check([len(m[2]) for m in e.out] == [10 + 6 * 48, 10 + 6 * 21], f"the lengths: {[len(m[2]) for m in e.out]} (48 + 21 LEDs)")
    check(all(m[0] == router_obj and m[1] == 2 for m in e.out), "through the stock router: its object, mask 1 << port")
    check([t[1] for t in got] == [0, 1], f"SEQ 0, 1 ({[t[1] for t in got]})")
    words = {}
    for _, _, items in got:
        words.update(items)
    check([words[i] for i in range(69)] == ref, "the 69 words = the converter's output")
    check(e.st(LM.O_SEQ, 1) == 2 and e.st(LM.O_FULL, 1) == 0, "SEQ now 2, FULL cleared")
    check(bytes(e.uc.mem_read(LM.LM + LM.O_PREV, 276)) == bytes(e.uc.mem_read(LM.LM + LM.O_CUR, 276)), "PREV = CUR")

    # ---- rate limit, and no message at all when nothing changed
    e.tick()
    check(e.out == [], "the same tick twice: rate limited (no second frame)")
    e.now(1024); e.tick()
    check(e.out == [], "16 ms later, nothing changed: not a single byte")

    # ---- one LED changes: one delta
    codes[5] = 0x20; e.set_frame(codes); ref = e.ref()
    e.now(1040); e.tick()
    got = [parse(m[2]) for m in e.out]
    check(len(got) == 1 and got[0][0] == 2 and got[0][2] == [(5, ref[5])], f"one LED: delta, index 5, {ref[5]:#08x}")
    check(len(e.out[0][2]) == 10 + 7, f"delta length {len(e.out[0][2])} = 10 + 7")

    # ---- 60 LEDs change: 48 + 12
    for i in range(60): codes[i] = 0x7F
    e.set_frame(codes); ref = e.ref()
    e.now(1056); e.tick()
    got = [parse(m[2]) for m in e.out]
    used = [i for _, _, items in got for i, _ in items]
    check(len(got) == 2 and [len(g[2]) for g in got] == [48, 12] and sorted(used) == list(range(60)),
          f"60 changes: 48 + 12 in two deltas ({[len(g[2]) for g in got]})")
    words = {}
    for _, _, items in got: words.update(items)
    check(all(words[i] == ref[i] for i in range(60)), "the delta values = the converter's")

    # ---- type 1: a full frame on request, even while streaming
    e.now(1072); e.keepalive(1, 4, 0)
    check(e.st(LM.O_FULL, 1) == 1, "keepalive type 1 asks for a full frame")
    e.now(1080); e.tick()
    got = [parse(m[2]) for m in e.out]
    words = {}
    for _, _, items in got: words.update(items)
    check(len(got) == 2 and len(words) == 69 and [words[i] for i in range(69)] == ref and e.out[0][1] == 1,
          "the full frame again, now on port 0 (mask 1)")

    # ---- 2 s of silence stops the stream
    e.now(3081); e.tick()
    check(e.out == [], "3 s after the keepalive: silent")
    e.now(3090); e.keepalive(0, 5, 0)
    check(e.st(LM.O_FULL, 1) == 1, "a keepalive after silence asks for a full frame")
    e.now(3098); e.tick()
    check(len(e.out) == 2, "and the full frame goes out")

    # ---- the command base: this build signs itself "CT2" (0x32); the incoming hook still accepts the pre-fix "CT " (0x20), so an
    # older cached page can drive a new device.  (The host uses the signature to tell which firmware is really running.)
    check(all(m[2][4] == SIG[4] for m in e.out), 'every message carries the "CT2" signature (0x32)')
    e.now(4000); e.keepalive(0, 8, 0, ver=1)
    check(e.fwd == [] and e.st(LM.O_LIVE) == 4000, 'a pre-fix "CT " keepalive (0x20) is still consumed: LIVE = 4000')
    e.now(4008); e.keepalive(1, 9, 0, ver=1)
    check(e.fwd == [] and e.st(LM.O_FULL, 1) == 1, 'a pre-fix "CT " type 1 is still a full-frame request')

    # ---- the panel may be a peripheral: the module never reads `out`
    e.uc.mem_write(PERIPH, b"\x00" * 276)
    e.uc.mem_write(LM.LM, b"\xAA" * LM.SIZE)                     # signed again: a fresh boot, the whole path once more
    e.now(10_000); e.tick(out=PERIPH)
    check(list(struct.unpack("<69I", bytes(e.uc.mem_read(PERIPH, 276)))) == ref,
          "out in a device range: the converter's bytes, written blind")

    # ---- a foreign SysEx goes on, registers restored
    e.now(20_000); e.keepalive(0, 6, 0)
    e.fwd.clear(); e.out.clear()
    foreign = [0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]
    e.uc.mem_write(BUF, bytes(foreign)); e.uc.mem_write(MSG, struct.pack("<II", BUF, len(foreign)))
    e.call(RD.HOOK, 1, MSG, 0, maxins=200_000)
    check(e.fwd == [(1, MSG, 0)] and e.out == [], f"an identity request: forwarded to c_bbsx with (port, msg, r2) = {e.fwd}")
    for bad, what in (([0xF0, 0x7D, 0x43, 0x54, 0x20, 0x00, 0x01, 0x00, 0xF7], "9 bytes"),
                      ([0xF0, 0x7D, 0x43, 0x54, 0x21, 0x00, 0x01, 0xF7], "another command")):
        e.fwd.clear(); e.uc.mem_write(BUF, bytes(bad)); e.uc.mem_write(MSG, struct.pack("<II", BUF, len(bad)))
        e.call(RD.HOOK, 0, MSG, 0, maxins=200_000)
        check(len(e.fwd) == 1, f"a near miss ({what}): forwarded, not consumed")

    # ---- the randomised run: the host's model, fed only by the messages, = the converter, every frame
    import random
    rnd = random.Random(20261114)
    e.now(100_000)
    t, model, nfull, ndelta = 100_000, [0] * 69, 0, 0
    ok = True
    for n in range(64):
        if n % 8 == 0:                                            # a keepalive every 8 frames (~128 ms)
            e.keepalive(1 if n == 0 else 0, n & 0xFF, n & 1)
        k = rnd.randrange(0, 70)
        for _ in range(k):
            i = rnd.randrange(0, 69)
            codes[i] = rnd.choice([0x01, 0x02, 0x05, 0x09, 0x17, 0x1A, 0x2C, 0x41, 0x7F, 0xBF, 0xC5, 0xD3, 0xF0, 0x10A])
        e.set_frame(codes)
        ref = e.ref()
        t += 17; e.now(t); e.tick()
        panel = list(struct.unpack("<69I", bytes(e.uc.mem_read(OUT, 276))))
        if panel != ref: ok = False; print(f"      frame {n}: the panel bytes differ"); break
        for _, seq, msg in e.out:
            typ, seq2, items = parse(msg)
            if typ == 1: nfull += 1
            else: ndelta += 1
            for i, v in items: model[i] = v
        if model != ref: ok = False; print(f"      frame {n}: the host's model differs"); break
    check(ok and nfull > 0 and ndelta > 0, f"64 random frames: the host's model tracks the converter ({nfull} full, {ndelta} delta messages)")

    # ---- 300 frames: the sequence number is a SysEx data byte, so it must stay < 0x80 and wrap cleanly (the stock router drops
    # any message with a data byte >= 0x80: a raw 8-bit SEQ froze the stream for ~3 s every 128 frames on hardware, 2026-11-16)
    e.now(300_000); e.keepalive(1, 5, 0); e.now(300_008); e.tick()
    seqs, t, drops = [], 300_024, 0
    for k in range(300):
        e.uc.mem_write(LM.LM + LM.O_FULL, b"\x01")       # a full frame per tick: two messages, both through c_lm_hdr
        e.now(t); e.uc.mem_write(LM.LM + LM.O_LIVE, struct.pack("<I", t))   # the 2 s keepalive window
        e.tick(); t += 24
        seqs += [parse(m[2])[1] for m in e.out]
        drops += len(e.bad)
    bad = [(a, b) for a, b in zip(seqs, seqs[1:]) if b != ((a + 1) & 0x7F)]
    check(drops == 0 and len(seqs) >= 600, f"300 full frames: every message is valid SysEx for the stock router ({drops} dropped, {len(seqs)} sent)")
    check(not bad and max(seqs) <= 127 and any(a == 127 and b == 0 for a, b in zip(seqs, seqs[1:])),
          f"SEQ is a 7-bit data byte (max {max(seqs)}) and wraps 127 -> 0 with no jump ({bad[:3]})")

    # ---- the stack: the tick runs on the LED task (3 KB, limit 1792 B); our frames must be a handful of bytes at most
    e.uc.mem_write(LM.LM, b"\xAA" * LM.SIZE); e.now(200_000); e.tick()      # a fresh block: signed, streaming off
    base = e.depth(lambda: e.call(LM.CONVERTER, MGR, REF, FRAME, maxins=200_000))
    off = e.depth(e.tick)
    e.now(200_008); e.keepalive(1, 7, 0); e.now(200_016)
    full = e.depth(e.tick)
    e.now(200_032); quiet = e.depth(e.tick)
    e.set_frame([(c + 1) % 0xBF + 1 for c in codes]); e.now(200_048)
    delta = e.depth(e.tick)
    worst = max(off, full, quiet, delta)
    check(worst - base <= 48,
          f"the stack: the converter alone {base} B; c_lm_tick {off} off / {full} full / {quiet} unchanged / {delta} delta "
          f"(+{worst - base} B, never more than 48)")

    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    img = Path(sys.argv[1]).read_bytes(); man = json.loads(Path(sys.argv[2]).read_text())
    sys.exit(main(img, man))
