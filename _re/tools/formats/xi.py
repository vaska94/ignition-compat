#!/usr/bin/env python3
"""FastTracker 2 Extended Instrument (.XI, version 0x0102) reader.

Layout: 0x128-byte instrument header, u16 sample count @0x128, N x 40-byte
sample headers from 0x12A, then each sample's delta-coded PCM in order.
`engine_view()` reproduces loader 0x004584D0, which fseek()s straight to 0x12A,
reads ONE 40-byte sample header and the PCM that immediately follows it,
delta-decodes it (0x00458609..0x00458649) and forces the rate to 22050 Hz.

usage: xi.py FILE [FILE ...]
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from acct import Acct


def parse(b):
    a = Acct(len(b))
    h = {}

    def f(d, off, fmt, name):
        n = struct.calcsize("<" + fmt)
        v = struct.unpack_from("<" + fmt, b, off)
        d[name] = v[0] if len(v) == 1 else list(v)
        a.add(off, n, name)

    f(h, 0x000, "21s", "magic")                 # "Extended Instrument: "
    f(h, 0x015, "22s", "instrument_name")
    f(h, 0x02B, "B", "eof_1A")                  # 0x1A
    f(h, 0x02C, "20s", "tracker_name")
    f(h, 0x040, "H", "version")                 # 0x0102
    f(h, 0x042, "96B", "note_sample_map")
    f(h, 0x0A2, "24H", "volume_envelope")       # 12 (x,y) pairs
    f(h, 0x0D2, "24H", "panning_envelope")
    for i, nm in enumerate(["vol_points", "pan_points", "vol_sustain", "vol_loop_start", "vol_loop_end",
                            "pan_sustain", "pan_loop_start", "pan_loop_end", "vol_type", "pan_type",
                            "vib_type", "vib_sweep", "vib_depth", "vib_rate"]):
        f(h, 0x102 + i, "B", nm)
    f(h, 0x110, "H", "vol_fadeout")
    f(h, 0x112, "22s", "reserved")
    f(h, 0x128, "H", "num_samples")
    h["sample_list"] = []
    o = 0x12A
    for i in range(h["num_samples"]):
        s = {"header_offset": o}
        f(s, o + 0, "I", "length")        # bytes (loader [esp+0xC]  -> smp+4)
        f(s, o + 4, "I", "loop_start")    # bytes (           +0x10 -> smp+8)
        f(s, o + 8, "I", "loop_length")   # bytes (           +0x14 -> smp+0xC)
        f(s, o + 12, "B", "volume")
        f(s, o + 13, "b", "finetune")
        f(s, o + 14, "B", "type")         # bits0-1 loop (0 none,1 fwd,2 pingpong), bit4 16-bit ([esp+0x1A])
        f(s, o + 15, "B", "panning")
        f(s, o + 16, "b", "relative_note")
        f(s, o + 17, "B", "reserved")
        f(s, o + 18, "22s", "name")
        o += 40
        h["sample_list"].append(s)
    for i, s in enumerate(h["sample_list"]):
        s["data_offset"] = o
        s["_pcm"] = b[o:o + s["length"]]
        a.add(o, min(s["length"], len(b) - o), f"smp{i}.pcm")
        o += s["length"]
    h["_acct"] = a
    return h


def decode(s, delta=True):
    """int16 numpy array; delta=False interprets the stored bytes raw (sanity test)."""
    import numpy as np
    raw = s["_pcm"]
    if s["type"] & 0x10:
        d = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.int64)
        x = np.cumsum(d) if delta else d
        return ((x + 0x8000) % 0x10000 - 0x8000).astype(np.int16)
    d = np.frombuffer(raw, dtype=np.int8).astype(np.int64)
    x = np.cumsum(d) if delta else d
    x = (x + 0x80) % 0x100 - 0x80                            # 8-bit wraparound exactly like `add dl,[edi]`
    return (x << 8).astype(np.int16)


def engine_view(h):
    s = h["sample_list"][0]
    bits = 16 if s["type"] & 0x10 else 8                     # 0x004585C6..0x004585D5
    length, ls, ll = s["length"], s["loop_start"], s["loop_length"]
    if bits == 16:                                           # 0x004585DF..0x00458606
        length, ls, ll = int(length / 2), int(ls / 2), int(ll / 2)
    if ll == 0:                                              # 0x00458654..0x00458660
        ll = length - ls
    return {"length_frames": length, "loop_start": ls, "loop_length": ll, "bits": bits,
            "rate": 22050, "loop_type_ignored": s["type"] & 3}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        b = open(p, "rb").read()
        h = parse(b)
        print(p, len(b), "bytes:", h["_acct"].summary())
        for s in h["sample_list"]:
            print("  sample", {k: v for k, v in s.items() if not k.startswith("_")})
        print("  engine:", engine_view(h))
