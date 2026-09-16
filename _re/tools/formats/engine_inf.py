#!/usr/bin/env python3
"""CARS/<car>/SOUND/ENGINE.INF reader.

800 bytes = 4 tables x 200 signed bytes (movsx at the consumer).  Loaded whole by
0x004574A0 at 0x0041FC25 and copied byte-by-byte (0x0041FC4F..0x0041FCAC) into
the per-car struct ([0x005DAFFC] + car*0x484C) at +0x658 / +0x720 / +0x7E8 / +0x8B0.

Consumer 0x00445524..0x00445658 (per frame):
    i = clamp(int(car[+0x288]) * 2, 1, 199)
    voice(car[+0x650]).rate   = T0[i]   * 600 + 20000        (sample slot 00_*)
    voice(car[+0x650]).volume = int(T1[i-1] * g * 650.0)       (+0x71F => index i-1)
    voice(car[+0x654]).rate   = T2[i]   * 600 + 20000        (sample slot 01_*)
    voice(car[+0x654]).volume = int(T3[i-1] * g * 650.0)       (+0x8AF => index i-1)
where g is the distance attenuation computed just before.

usage: engine_inf.py FILE [FILE ...]
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from acct import Acct

NAMES = ["pitch0", "volume0", "pitch1", "volume1"]


def parse(b):
    a = Acct(len(b))
    h = {}
    for t, nm in enumerate(NAMES):
        h[nm] = list(struct.unpack_from("<200b", b, 200 * t))
        a.add(200 * t, 200, nm)
    h["_acct"] = a
    return h


def engine_at(h, i):
    i = max(1, min(199, i))
    return {"rate0": h["pitch0"][i] * 600 + 20000, "vol0": h["volume0"][i - 1],
            "rate1": h["pitch1"][i] * 600 + 20000, "vol1": h["volume1"][i - 1]}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        b = open(p, "rb").read()
        h = parse(b)
        print(p, len(b), "bytes:", h["_acct"].summary())
        for nm in NAMES:
            t = h[nm]
            nz = [i for i, v in enumerate(t) if v]
            print(f"  {nm:8s} min={min(t)} max={max(t)} nonzero={nz[0] if nz else '-'}..{nz[-1] if nz else '-'}")
