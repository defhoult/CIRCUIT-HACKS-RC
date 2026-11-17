"""Measure the SC57.2 LED stream from this Mac, without the browser: keepalives, full-frame round trips, arrival cadence.

    .venv/bin/python tools/probe_ledmirror.py --seconds 20 --json /tmp/probe.json

Every --probe seconds it sends a "keepalive + full frame" (type 1) and times the reply, which is the device's real latency:
request -> the LED tick -> the stock SysEx router -> USB -> CoreMIDI.  Everything else is logged with its arrival time so the
cadence (smooth 60 Hz vs bursts after a pause) can be read off afterwards.  The port can be shared with a browser: both get
a copy of every message, and the device replies to the port of the last keepalive (the same USB port either way).
"""
from __future__ import annotations
import argparse, json, os, statistics, sys, threading, time
from collections import Counter

import mido

SIG_V1 = (0xF0, 0x7D, 0x43, 0x54, 0x20)          # "CT " — pre-2026-11-16: 8-bit SEQ, drops messages every 128 frames
SIG = (0xF0, 0x7D, 0x43, 0x54, 0x32)             # "CT2" — 2026-11-16: 7-bit SEQ
SIGS = {1: SIG_V1, 2: SIG}
DEV = {1: '"CT" — PRE-FIX (8-bit SEQ)', 2: '"CT2" · 7-bit SEQ'}
NLED = 69


def msg(sig, typ, seq):
    return bytes((*sig, typ, seq & 0x7F, 0xF7))


def parse(data):
    if len(data) < 7 or data[-1] != 0xF7 or tuple(data[:4]) != SIG[:4]:
        return None
    ver = 2 if data[4] == SIG[4] else 1 if data[4] == SIG_V1[4] else 0
    if not ver:
        return None
    t, seq = data[5], data[6]
    if len(data) == 8:
        return {"ver": ver, "type": t, "seq": seq, "start": 0, "count": 0, "len": len(data)}
    if len(data) < 10:
        return None
    return {"ver": ver, "type": t, "seq": seq, "start": data[7], "count": data[8], "len": len(data)}


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = min(len(xs) - 1, int(round((len(xs) - 1) * p / 100)))
    return xs[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--probe", type=float, default=1.0, help="seconds between full-frame round trips")
    ap.add_argument("--keep", type=float, default=0.25, help="seconds between keepalives")
    ap.add_argument("--port", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    name = args.port
    if not name:
        cands = [p for p in mido.get_input_names() if "circuit tracks" in p.lower()]
        if not cands:
            sys.exit("no Circuit Tracks MIDI input: " + repr(mido.get_input_names()))
        name = cands[0]
    out_name = next((p for p in mido.get_output_names() if p == name), mido.get_output_names()[0])

    events, lock = [], threading.Lock()

    def on_message(msg):
        now = time.perf_counter()
        if msg.type != "sysex":
            return
        m = parse(msg.bytes())
        if m:
            with lock:
                events.append((now, m))

    inp = mido.open_input(name, callback=on_message)
    out = mido.open_output(out_name)
    print(f"port {name!r} -> {out_name!r}; {args.seconds:.0f} s, full-frame probe every {args.probe:g} s", flush=True)

    t0 = time.perf_counter()
    probes = []                      # (t_send, seq expected)
    last_keep = last_probe = 0.0
    sigs = [SIG, SIG_V1]             # until the device has spoken we keepalive on both command bases
    while True:
        now = time.perf_counter()
        if now - t0 >= args.seconds:
            break
        with lock:
            dev_ver = events[-1][1]["ver"] if events else 0
        if dev_ver:
            sigs = [SIGS[dev_ver]]
        if now - last_keep >= args.keep:
            for sig in sigs:
                out.send(mido.Message("sysex", data=list(msg(sig, 0, 0)[1:-1])))
            last_keep = now
        if now - last_probe >= args.probe:
            with lock:
                dev_seq = events[-1][1]["seq"] if events else None
            expected = None if dev_seq is None else (dev_seq + 1) & 0x7F      # SEQ is a 7-bit SysEx data byte
            for sig in sigs:
                out.send(mido.Message("sysex", data=list(msg(sig, 1, 0)[1:-1])))
            probes.append((time.perf_counter(), expected))
            last_probe = now
        time.sleep(0.002)
    for sig in sigs:
        out.send(mido.Message("sysex", data=list(msg(sig, 0, 0)[1:-1])))
    time.sleep(0.05)

    with lock:
        ev = [(t - t0, m) for t, m in events]
    dur = max((t for t, _ in ev), default=0.0)

    # ---- cadence
    times = [t for t, _ in ev]
    iv = [(b - a) * 1000 for a, b in zip(times, times[1:])]
    gaps = 0
    for (_, a), (_, b) in zip(ev, ev[1:]):
        if ((a["seq"] + 1) & 0x7F) != b["seq"]:            # 127 -> 0 is the wrap, not a gap
            gaps += 1
    # clumps: messages that arrive within 4 ms of each other = one USB burst
    clumps, cur = [], []
    for t, m in ev:
        if cur and (t - cur[-1][0]) * 1000 > 4:
            clumps.append(cur)
            cur = []
        cur.append((t, m))
    if cur:
        clumps.append(cur)
    clump_sizes = [len(c) for c in clumps]
    big = [c for c in clumps if len(c) >= 3]
    # 100 ms windows with traffic: is the stream smooth or stop-and-go?
    win = Counter(int(t * 10) for t, _ in ev)
    per_win = [win.get(i, 0) for i in range(int(dur * 10) + 1)]

    # ---- round trips
    rtts, frames = [], []
    for t_send, expected in probes:
        if expected is None:
            continue
        after = [(t, m) for t, m in ev if t >= t_send - 0.001]
        first = next(((t, m) for t, m in after if m["seq"] == expected), None)
        if not first:
            rtts.append(None)
            continue
        rtts.append((first[0] - t_send) * 1000)
        # the full frame is 48 + 21: the second chunk closes it
        second = next(((t, m) for t, m in after if m["seq"] == ((expected + 1) & 0x7F)), None)
        if second:
            frames.append((second[0] - t_send) * 1000)
    good = [r for r in rtts if r is not None]

    by_type = Counter(m["type"] for _, m in ev)
    nbytes = sum(m["len"] for _, m in ev)
    dev_ver = ev[-1][1]["ver"] if ev else 0
    # a hole's SEQ pair is its fingerprint: the pre-fix firmware always dropped 127 -> 0
    holes = [{"ms": round((tb - ta) * 1000), "seq": f"{a['seq']}→{b['seq']}"}
             for (ta, a), (tb, b) in zip(ev, ev[1:]) if (tb - ta) * 1000 > 1500]
    summary = {
        "port": name,
        "device": DEV.get(dev_ver, "not heard from yet"),
        "device_ver": dev_ver,
        "seconds": round(dur, 3),
        "messages": len(ev),
        "by_type": dict(by_type),
        "bytes": nbytes,
        "bytes_per_s": round(nbytes / dur, 1) if dur else None,
        "msgs_per_s": round(len(ev) / dur, 1) if dur else None,
        "seq_gaps": gaps,
        "holes": holes,
        "inter_arrival_ms": {
            "p50": pct(iv, 50), "p90": pct(iv, 90), "p95": pct(iv, 95), "p99": pct(iv, 99),
            "max": max(iv) if iv else None, "mean": round(statistics.fmean(iv), 2) if iv else None,
        },
        "rtt_ms": {
            "n": len(good), "missing": sum(1 for r in rtts if r is None),
            "min": min(good) if good else None, "p50": pct(good, 50), "p90": pct(good, 90),
            "p95": pct(good, 95), "max": max(good) if good else None,
            "mean": round(statistics.fmean(good), 2) if good else None,
        },
        "full_frame_ms": {
            "n": len(frames), "p50": pct(frames, 50), "p95": pct(frames, 95), "max": max(frames) if frames else None,
        },
        "clumps": {
            "n": len(clumps), "max_msgs": max(clump_sizes) if clump_sizes else 0,
            "msgs_in_clumps_of_3+": sum(len(c) for c in big), "clumps_of_3+": len(big),
        },
        "windows_100ms": {
            "n": len(per_win), "max": max(per_win) if per_win else 0,
            "empty": sum(1 for v in per_win if v == 0),
            "mean": round(statistics.fmean(per_win), 2) if per_win else None,
        },
        "first_events": [{"t_ms": round(t * 1000, 1), **m} for t, m in ev[:20]],
    }
    print(json.dumps(summary, indent=2))

    # ---- the verdict.  A full-frame probe every second always produces two messages, so even a static panel should never be
    # quiet for long: a hole means the device or the router dropped messages.  The pre-2026-11-16 firmware dropped every message
    # once SEQ reached 0x80 (the stock router refuses data bytes >= 0x80), which is exactly what this catches — and its holes
    # always run 127 -> 0, the fingerprint that no rate change can imitate.
    print(f"\ndevice {DEV.get(dev_ver, 'not heard from yet')}")
    if len(ev) < 2:
        print(f"INCONCLUSIVE: {len(ev)} message(s) in {dur:.0f} s — bootloader mode (flashing), streaming off, or the wrong port?")
    else:
        ok = gaps == 0 and not holes and dev_ver == 2
        detail = f"{gaps} sequence gap(s), {len(holes)} hole(s) over 1.5 s"
        if holes:
            detail += " (" + ", ".join(f"{h['ms']} ms {h['seq']}" for h in holes[:4]) + ")"
        if dev_ver == 1:
            detail = 'pre-fix firmware ("CT", 8-bit SEQ); ' + detail
        print(f"{'PASS' if ok else 'FAIL'}: {detail}")
        if dev_ver == 1:
            print('  <- the device runs the pre-fix firmware ("CT", 8-bit SEQ): flash the CT2 build')
        elif holes:
            print("  <- a hole means dropped messages (the pre-fix build's holes always run 127→0)")
    if summary["msgs_per_s"] and summary["msgs_per_s"] < 12:
        print("note: the panel was mostly static (%.1f msgs/s), so SEQ may not have reached 127: run it with the sequencer playing"
              % summary["msgs_per_s"])
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"summary": summary,
                       "events": [{"t_ms": round(t * 1000, 3), **m} for t, m in ev],
                       "probes": [{"t_ms": round(t * 1000, 1), "expected": e} for t, e in probes]}, f, indent=1)
        print("log ->", args.json)
    # rtmidi can hang in interpreter shutdown while a port is open: close, then leave
    try:
        inp.close()
        out.close()
    finally:
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    main()
