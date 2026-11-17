"""LED mirror (SC57.2): stream the panel's 69 LEDs to a host over the USB MIDI SysEx port, in real time.

The Circuit Tracks has no screen: its 69 LEDs (32 pads, 8 track buttons, 19 function buttons, 8 macros, the master filter) are the whole UI.  This
module copies exactly what the stock LED path sends to the panel: it hooks the RGB converter call inside the LED tick (0x0803418C: `bl 0x0803423C`,
ctrl / out / frame in r0 / r1 / r2), runs the stock converter into a RAM buffer, copies that buffer to the caller's `out` (so the panel gets its
stock bytes, unchanged), and mirrors the same 69 words (B, G, R, 0 per LED, i.e. u32 = R << 16 | G << 8 | B) to the host.

Hooking the converter's output (not the 69 halfword code frame) is what makes the mirror exact: dimming, pulses and the palette stay the stock's,
and the host only has to draw RGB.  The panel path itself is unchanged: the converter runs exactly once per tick and its 276-byte result is copied
word for word to where the stock expected it (which may be a peripheral, so the copy does not read `out`).

Host protocol (all SysEx, manufacturer 7D, command byte 0x20, no reply from the device):

    F0 7D 43 54 32 01 SEQ START COUNT n0 n1 n2 n3 n4 n5 ... F7     full: 6 nibbles per LED, START..START + COUNT - 1
    F0 7D 43 54 32 02 SEQ START COUNT IDX n0 .. n5 ... F7          delta: IDX + 6 nibbles per changed LED
    F0 7D 43 54 32 00 SEQ F7                                       keepalive (streaming on, no forced frame)
    F0 7D 43 54 32 01 SEQ F7                                       keepalive + the next tick sends a full frame

The fifth byte is the command base and doubles as the build's signature: "CT2" (0x32) from 2026-11-16 on, "CT " (0x20) before it.  It is the one
byte that tells the host which firmware is really running, without touching the reported version number (Identify still says 4486): the pre-fix
build is the only one that emits 0x20.  The incoming hook accepts both, so an older cached page still drives a new device.

Nibbles are bits 23..20, 19..16, 15..12, 11..8, 7..4, 3..0 of the word = R high, R low, G high, G low, B high, B low.  The host must send a
keepalive at least every 2 s; streaming stops 2 s after the last one (so an unplugged host costs nothing).  A full frame goes out on the first tick
after a keepalive that follows silence or a gap of >= 2 s, on request, and when the host finds a SEQ gap.  Frames are rate limited to one per
16 ms (~60 Hz) and chunked to 48 LEDs because the router rejects messages longer than 0x168 = 360 bytes (346 max here).

The incoming hook (0x0801EB30) sits in front of the current incoming-SysEx dispatcher (blackbox's c_bbsx, fwid's c_fwin or rdiag's c_rdin) and
forwards everything that is not one of the four messages above, restoring the registers and its prologue exactly as the module in front did.

RAM (0x2004CA00, 952 bytes; free: fmmap's table ends at 0x2004C900, fm8store would start at 0x2004D000 and is not in this chain):

    +0x000 LIVE   u32  the last keepalive (HAL tick ms); 0 = never
    +0x004 LAST   u32  the last frame sent
    +0x008 FULL   u8   1 = the next tick sends every LED
    +0x009 PORT   u8   the MIDI port the last keepalive came from (the reply mask)
    +0x00A SEQ    u8   the next frame's sequence number (0..127: a SysEx data byte must be < 0x80, or the stock router drops
                       the whole message, which froze the stream for ~3 s every 128 frames until 2026-11-16)
    +0x00B START  u8   the first LED of the delta being built
    +0x00C MSG    368  the SysEx being built
    +0x180 CUR    276  the stock converter's output for this tick (69 x B, G, R, 0)
    +0x2A0 PREV   276  the frame last sent (the delta reference)
    +0x3B4 MAGIC  u32  "LRM1": the block is not in the stock's .bss, so the first tick zeroes it
"""
from __future__ import annotations
import struct
import ct_fw
import rdiag as RD
import drums18
from thumb import (Asm, h, bl, b_w, b_cond, b, ldr_imm, ldrb_imm, strb_imm, str_imm, strh_imm, movs_imm, cmp_imm,
                   cmp_reg, mov_reg, adds_imm8, subs_imm8, adds_reg, subs_reg, lsls_imm, lsrs_imm, ands_reg,
                   lsls_reg, push_list, pop_list, movw, movt, str_sp, add_sp_imm, sub_sp_imm, raw)

CONVERTER = 0x0803423C                            # the stock LED code frame -> 69 B, G, R, 0 words: 0x0803423C(ctrl, out, frame)
LED_HOOK = 0x0803418C                             # the converter call inside the LED tick (0x08033E1A): bl 0x0803423C

LM = 0x2004CA00
O_LIVE, O_LAST, O_FULL, O_PORT, O_SEQ, O_START = 0x000, 0x004, 0x008, 0x009, 0x00A, 0x00B
O_MSG, O_CUR, O_PREV, O_MAGIC = 0x00C, 0x180, 0x2A0, 0x3B4
MSG_N, SIZE = 368, 0x3B8
MAGIC = 0x314D524C                                # "LRM1"
NLED = 69
CHUNK = 48                                        # 7 * 48 + 10 = 346 <= 0x168 (the router's limit)
KEEP_MS = 2000
RATE_MS = 16
HAL_TICK = 0x2000081C                             # the HAL's uwTick (ms), as blackbox uses

SIG = (0xF0, 0x7D, 0x43, 0x54, 0x32)              # SysEx, manufacturer 7D, command base "CT2" (the pre-2026-11-16 build sent "CT ", 0x20)
SIG_V1 = 0x20                                     # still accepted: an older page can drive a new device


def ldr_w(rt, rn, imm): return h(0xF8D0 | rn) + h(rt << 12 | imm)       # LDR.W Rt,[Rn,#imm12]
def str_w(rt, rn, imm): return h(0xF8C0 | rn) + h(rt << 12 | imm)       # STR.W Rt,[Rn,#imm12]


def pushw(regs, lr=False): return h(0xE92D) + h(sum(1 << r for r in regs) | (1 << 14 if lr else 0))
def popw(regs, pc=False, lr=False): return h(0xE8BD) + h(sum(1 << r for r in regs) | (1 << 15 if pc else 0) | (1 << 14 if lr else 0))


def arm_code(base, router_ptr, cont):
    a = Asm(base)
    E = a.emit

    def imm32(rd, v):
        E(movw, rd, v & 0xFFFF); E(movt, rd, (v >> 16) & 0xFFFF)

    # ---- c_lm_tick (r0 = ctrl, r1 = out, r2 = the code frame): the stock converter, the panel's bytes, the mirror
    a.label("c_lm_tick")
    E(raw, pushw([4, 5, 6, 7], lr=True))                                        # 5 words: the LED task's stack is tight (see docs/NOTAS.md)
    E(mov_reg, 4, 0); E(mov_reg, 5, 1); E(mov_reg, 6, 2)                        # ctrl, out, frame
    imm32(7, LM)
    E(mov_reg, 0, 4); imm32(1, LM + O_CUR); E(mov_reg, 2, 6)
    E(bl, CONVERTER)                                                            # the stock converter -> CUR
    imm32(0, LM + O_CUR); E(mov_reg, 1, 5); E(movs_imm, 2, NLED)                # CUR -> the caller's out, word for word
    a.label("lt_cp")
    E(ldr_imm, 3, 0, 0); E(adds_imm8, 0, 4); E(str_imm, 3, 1, 0); E(adds_imm8, 1, 4); E(subs_imm8, 2, 1)
    E(b_cond, "lt_cp", "ne")
    # the block is not zeroed by the stock's startup: sign it once
    E(raw, ldr_w(0, 7, O_MAGIC)); imm32(1, MAGIC); E(cmp_reg, 0, 1); E(b_cond, "lt_rdy", "eq")
    imm32(0, LM + O_PREV); E(movs_imm, 1, 0); E(movs_imm, 2, NLED)
    a.label("lt_iz")
    E(str_imm, 1, 0, 0); E(adds_imm8, 0, 4); E(subs_imm8, 2, 1); E(b_cond, "lt_iz", "ne")
    imm32(0, LM); E(str_imm, 1, 0, 0); E(str_imm, 1, 0, 4); E(str_imm, 1, 0, 8)  # LIVE, LAST, FULL/PORT/SEQ/START
    imm32(1, MAGIC); E(raw, str_w(1, 0, O_MAGIC)); E(b, "lt_out")
    a.label("lt_rdy")
    E(ldr_imm, 0, 7, O_LIVE); E(cmp_imm, 0, 0); E(b_cond, "lt_out", "eq")       # no keepalive yet
    imm32(1, HAL_TICK); E(ldr_imm, 1, 1, 0)                                     # r1 = now
    E(subs_reg, 2, 1, 0); E(movw, 3, KEEP_MS); E(cmp_reg, 2, 3); E(b_cond, "lt_out", "hs")   # silent for >= 2 s
    E(ldr_imm, 3, 7, O_LAST); E(subs_reg, 2, 1, 3); E(cmp_imm, 2, RATE_MS); E(b_cond, "lt_out", "lo")
    E(str_imm, 1, 7, O_LAST)
    E(ldrb_imm, 0, 7, O_FULL); E(cmp_imm, 0, 0); E(b_cond, "lt_d", "eq")
    E(movs_imm, 0, 0); E(strb_imm, 0, 7, O_FULL); E(bl, "c_lm_full"); E(b, "lt_pv")
    a.label("lt_d")
    E(bl, "c_lm_delta")
    a.label("lt_pv")
    imm32(0, LM + O_CUR); imm32(1, LM + O_PREV); E(movs_imm, 2, NLED)           # PREV = CUR
    a.label("lt_pc")
    E(ldr_imm, 3, 0, 0); E(adds_imm8, 0, 4); E(str_imm, 3, 1, 0); E(adds_imm8, 1, 4); E(subs_imm8, 2, 1)
    E(b_cond, "lt_pc", "ne")
    a.label("lt_out")
    E(raw, popw([4, 5, 6, 7], pc=True))

    # ---- c_lm_hdr (r0 = type, r1 = START, r2 = COUNT): the header of MSG; clobbers r0, r3, r12
    a.label("c_lm_hdr")
    E(mov_reg, 12, 0); imm32(3, LM)
    E(strb_imm, 1, 3, O_MSG + 7); E(strb_imm, 2, 3, O_MSG + 8)                  # START, COUNT
    E(movw, 0, 0x7DF0); E(strh_imm, 0, 3, O_MSG + 0)                            # F0 7D
    E(movw, 0, 0x5443); E(strh_imm, 0, 3, O_MSG + 2)                            # 43 54
    E(movs_imm, 0, SIG[4]); E(strb_imm, 0, 3, O_MSG + 4)                        # the command base, "CT2": which build this is
    E(mov_reg, 0, 12); E(strb_imm, 0, 3, O_MSG + 5)                             # TYPE
    E(ldrb_imm, 0, 3, O_SEQ); E(strb_imm, 0, 3, O_MSG + 6); E(adds_imm8, 0, 1)
    E(lsls_imm, 0, 0, 25); E(lsrs_imm, 0, 0, 25)                                # SEQ 0..127: the stock router drops
    E(strb_imm, 0, 3, O_SEQ)                                                    # any SysEx data byte >= 0x80
    E(raw, h(0x4770))

    # ---- c_lm_nib (r0 = word, r1 = write pointer) -> r1 advanced by 6: the six nibbles, high to low
    a.label("c_lm_nib")
    E(push_list, [4, 5, 6], lr=True)
    E(movs_imm, 5, 15); E(movs_imm, 4, 6)
    a.label("ln_l")
    E(mov_reg, 6, 0); E(lsrs_imm, 6, 6, 20); E(ands_reg, 6, 5); E(strb_imm, 6, 1, 0); E(adds_imm8, 1, 1)
    E(lsls_imm, 0, 0, 4); E(subs_imm8, 4, 1); E(b_cond, "ln_l", "ne")
    E(pop_list, [4, 5, 6], pc=True)

    # ---- c_lm_send (r0 = length): MSG to the port of the last keepalive through the stock router
    a.label("c_lm_send")
    E(push_list, [4, 5, 6], lr=True); E(mov_reg, 5, 0); imm32(4, LM)
    E(sub_sp_imm, 8); E(mov_reg, 0, 4); E(adds_imm8, 0, O_MSG); E(str_sp, 0, 0); E(str_sp, 5, 4)
    E(mov_reg, 2, 13)                                                           # r2 = &{buf, len}
    E(ldrb_imm, 1, 4, O_PORT); E(movs_imm, 6, 1); E(lsls_reg, 6, 1); E(mov_reg, 1, 6)   # mask = 1 << port
    imm32(0, router_ptr); E(ldr_imm, 0, 0, 0)
    E(bl, RD.ROUTER)
    E(add_sp_imm, 8); E(pop_list, [4, 5, 6], pc=True)

    # ---- c_lm_fchunk (r0 = START, r1 = COUNT; r7 = LM): one full-frame chunk
    a.label("c_lm_fchunk")
    E(push_list, [4, 5, 6], lr=True); E(mov_reg, 5, 1); E(mov_reg, 1, 0); E(mov_reg, 2, 5); E(movs_imm, 0, 1)
    E(bl, "c_lm_hdr")
    E(mov_reg, 6, 7); E(adds_imm8, 6, O_MSG); E(adds_imm8, 6, 9)                # wp = MSG + 9
    imm32(3, O_CUR); E(mov_reg, 4, 7); E(adds_reg, 4, 4, 3); E(lsls_imm, 0, 1, 2); E(adds_reg, 4, 4, 0)
    a.label("lf_l")
    E(ldr_imm, 0, 4, 0); E(mov_reg, 1, 6); E(bl, "c_lm_nib"); E(mov_reg, 6, 1)
    E(adds_imm8, 4, 4); E(subs_imm8, 5, 1); E(b_cond, "lf_l", "ne")
    E(movs_imm, 0, 0xF7); E(strb_imm, 0, 6, 0)                                  # F7
    imm32(0, LM + O_MSG); E(subs_reg, 0, 6, 0); E(adds_imm8, 0, 1); E(bl, "c_lm_send")
    E(pop_list, [4, 5, 6], pc=True)

    # ---- c_lm_full (r7 = LM): every LED, in chunks
    a.label("c_lm_full")
    E(push_list, [4, 5, 6], lr=True); E(movs_imm, 4, 0)
    a.label("lfw_l")
    E(movs_imm, 5, NLED); E(subs_reg, 5, 5, 4); E(cmp_imm, 5, CHUNK); E(b_cond, "lfw_c", "ls")
    E(movs_imm, 5, CHUNK)
    a.label("lfw_c")
    E(mov_reg, 0, 4); E(mov_reg, 1, 5); E(bl, "c_lm_fchunk"); E(adds_reg, 4, 4, 5)
    E(cmp_imm, 4, NLED); E(b_cond, "lfw_l", "lo")
    E(pop_list, [4, 5, 6], pc=True)

    # ---- c_lm_flush (r5 = COUNT, r6 = wp, r7 = LM): close and send the delta being built, wp back to the body start
    a.label("c_lm_flush")
    E(push_list, [0], lr=True)
    E(ldrb_imm, 1, 7, O_START); E(mov_reg, 2, 5); E(movs_imm, 0, 2); E(bl, "c_lm_hdr")
    E(movs_imm, 0, 0xF7); E(strb_imm, 0, 6, 0)
    imm32(0, LM + O_MSG); E(subs_reg, 0, 6, 0); E(adds_imm8, 0, 1); E(bl, "c_lm_send")
    E(mov_reg, 6, 7); E(adds_imm8, 6, O_MSG); E(adds_imm8, 6, 9)               # the next chunk starts over
    E(pop_list, [0], pc=True)

    # ---- c_lm_delta (r7 = LM): the LEDs whose word changed since PREV
    a.label("c_lm_delta")
    E(push_list, [4, 5, 6], lr=True); E(movs_imm, 4, 0); E(movs_imm, 5, 0)
    E(mov_reg, 6, 7); E(adds_imm8, 6, O_MSG); E(adds_imm8, 6, 9)
    a.label("ld_l")
    E(lsls_imm, 1, 4, 2)
    imm32(0, O_CUR); E(mov_reg, 2, 7); E(adds_reg, 2, 2, 0); E(adds_reg, 2, 2, 1)
    imm32(3, O_PREV); E(mov_reg, 0, 7); E(adds_reg, 0, 0, 3); E(adds_reg, 0, 0, 1)
    E(ldr_imm, 3, 2, 0); E(ldr_imm, 0, 0, 0); E(cmp_reg, 3, 0); E(b_cond, "ld_n", "eq")
    E(cmp_imm, 5, 0); E(b_cond, "ld_s", "ne"); E(strb_imm, 4, 7, O_START)
    a.label("ld_s")
    E(strb_imm, 4, 6, 0); E(adds_imm8, 6, 1); E(mov_reg, 0, 3); E(mov_reg, 1, 6); E(bl, "c_lm_nib"); E(mov_reg, 6, 1)
    E(adds_imm8, 5, 1); E(cmp_imm, 5, CHUNK); E(b_cond, "ld_n", "ne")
    E(bl, "c_lm_flush"); E(movs_imm, 5, 0)
    a.label("ld_n")
    E(adds_imm8, 4, 1); E(cmp_imm, 4, NLED); E(b_cond, "ld_l", "lo")
    E(cmp_imm, 5, 0); E(b_cond, "ld_x", "eq"); E(bl, "c_lm_flush")
    a.label("ld_x")
    E(pop_list, [4, 5, 6], pc=True)

    # ---- c_lm_in: the hooked incoming SysEx dispatcher (r0 = port, r1 = msg {buf, len}, r2)
    a.label("c_lm_in")
    E(push_list, [0, 1, 2, 3, 4, 5, 6], lr=True)
    E(mov_reg, 6, 0); E(ldr_imm, 4, 1, 0); E(ldr_imm, 5, 1, 4)
    E(cmp_imm, 5, 8); E(b_cond, "ln_fwd", "ne")
    for i, v in enumerate(SIG[:4]):
        E(ldrb_imm, 3, 4, i); E(cmp_imm, 3, v); E(b_cond, "ln_fwd", "ne")
    E(ldrb_imm, 3, 4, 4)                                                        # the command base: CT2, or CT (the first build)
    E(cmp_imm, 3, SIG[4]); E(b_cond, "ln_ok", "eq")
    E(cmp_imm, 3, SIG_V1); E(b_cond, "ln_fwd", "ne")
    a.label("ln_ok")
    E(ldrb_imm, 3, 4, 7); E(cmp_imm, 3, 0xF7); E(b_cond, "ln_fwd", "ne")
    E(ldrb_imm, 3, 4, 5)                                                        # TYPE 0 = keepalive, 1 = keepalive + full
    imm32(4, LM); imm32(5, HAL_TICK); E(ldr_imm, 5, 5, 0)                       # r5 = now
    E(cmp_imm, 3, 1); E(b_cond, "ln_full", "eq")
    E(ldr_imm, 0, 4, O_LIVE); E(cmp_imm, 0, 0); E(b_cond, "ln_full", "eq")
    E(subs_reg, 1, 5, 0); E(movw, 2, KEEP_MS); E(cmp_reg, 1, 2); E(b_cond, "ln_set", "lo")
    a.label("ln_full")
    E(movs_imm, 0, 1); E(strb_imm, 0, 4, O_FULL)
    a.label("ln_set")
    E(str_imm, 5, 4, O_LIVE); E(strb_imm, 6, 4, O_PORT)
    E(pop_list, [0, 1, 2, 3, 4, 5, 6], pc=True)
    a.label("ln_fwd")
    E(raw, popw([0, 1, 2, 3, 4, 5, 6], lr=True))
    if cont is None:
        E(raw, RD.PROLOGUE); E(b_w, RD.HOOK_CONT)
    else:
        E(b_w, cont)
    return a.assemble()


def install(img, report, base_addr):
    """Append the code (the caller moves it into the hole / gap) and patch the two hooks."""
    BASE = ct_fw.APP_BASE
    assert bytes(img[LED_HOOK - BASE:LED_HOOK - BASE + 4]) == bl(LED_HOOK, CONVERTER), \
        img[LED_HOOK - BASE:LED_HOOK - BASE + 4].hex()
    cur = bytes(img[RD.HOOK - BASE:RD.HOOK - BASE + 4])
    if cur == RD.PROLOGUE:
        cont, who = None, "stock"
    else:
        cont = drums18.decode_bw(img, RD.HOOK)
        known = {}
        for mod, lab in (("blackbox", "c_bbsx"), ("fwid", "c_fwin"), ("rdiag", "c_rdin")):
            if lab in report.get(mod, {}).get("labels", {}):
                known[int(report[mod]["labels"][lab], 16)] = lab
        assert cont in known, f"incoming SysEx head {cont:#x} is not one of {sorted(hex(a) for a in known)}"
        who = known[cont]
    router_ptr = struct.unpack_from("<I", img, RD.ROUTER_LIT - BASE)[0]
    code, labels = arm_code(base_addr, router_ptr, cont)
    img += code
    while len(img) % 4:
        img += bytes([0xFF])
    img[LED_HOOK - BASE:LED_HOOK - BASE + 4] = bl(LED_HOOK, labels["c_lm_tick"])
    img[RD.HOOK - BASE:RD.HOOK - BASE + 4] = b_w(RD.HOOK, labels["c_lm_in"])
    return labels, code, dict(cont=cont or RD.HOOK_CONT, cont_name=who, led_hook=hex(LED_HOOK))
