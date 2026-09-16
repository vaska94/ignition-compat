#!/usr/bin/env python3
"""Gravis Ultrasound GF1 patch (.PAT) reader, as used by Ign_win.exe.

Standard GF1PATCH110 layout (129-byte file header, 63-byte instrument header,
47-byte layer header, 96-byte sample header, then that sample's PCM).  The
reader walks every instrument/layer/sample generically and accounts for every
byte.  `engine_view()` reproduces exactly what the game's loader 0x004582B0
derives from the file (it reads ONLY the first 0x14F bytes of headers and the
first sample's data).

usage: pat.py FILE [FILE ...]
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from acct import Acct

MODE_16BIT, MODE_UNSIGNED, MODE_LOOP, MODE_BIDIR, MODE_REVERSE, MODE_SUSTAIN, MODE_ENVELOPE, MODE_CLAMPED = (
    1, 2, 4, 8, 16, 32, 64, 128)


class Reader:
    def __init__(self, b, acct, prefix=""):
        self.b, self.a, self.prefix = b, acct, prefix

    def field(self, d, off, fmt, name):
        n = struct.calcsize("<" + fmt)
        v = struct.unpack_from("<" + fmt, self.b, off)
        d[name] = v[0] if len(v) == 1 else list(v)
        if fmt.endswith("s"):
            d[name] = v[0]
        self.a.add(off, n, self.prefix + name)
        return d[name]


def parse(b):
    a = Acct(len(b))
    r = Reader(b, a)
    h = {}
    o = 0
    r.field(h, 0x00, "12s", "magic")            # "GF1PATCH110\0"   (loader strcmp @0x0045832A)
    r.field(h, 0x0C, "10s", "gravis_id")        # "ID#000002\0"
    r.field(h, 0x16, "60s", "description")
    r.field(h, 0x52, "B", "instruments")
    r.field(h, 0x53, "B", "voices")
    r.field(h, 0x54, "B", "channels")
    r.field(h, 0x55, "H", "waveforms")
    r.field(h, 0x57, "H", "master_volume")
    r.field(h, 0x59, "I", "data_size")
    r.field(h, 0x5D, "36s", "reserved")
    o = 0x81
    h["instrument_list"] = []
    for ii in range(h["instruments"]):
        r.prefix = f"inst{ii}."
        ins = {"offset": o}
        r.field(ins, o + 0, "H", "id")
        r.field(ins, o + 2, "16s", "name")
        r.field(ins, o + 18, "I", "size")
        r.field(ins, o + 22, "B", "layers")
        r.field(ins, o + 23, "40s", "reserved")
        o += 63
        ins["layer_list"] = []
        for li in range(ins["layers"]):
            r.prefix = f"inst{ii}.layer{li}."
            lay = {"offset": o}
            r.field(lay, o + 0, "B", "layer_duplicate")
            r.field(lay, o + 1, "B", "layer")
            r.field(lay, o + 2, "I", "size")
            r.field(lay, o + 6, "B", "samples")
            r.field(lay, o + 7, "40s", "reserved")
            o += 47
            lay["sample_list"] = []
            for si in range(lay["samples"]):
                r.prefix = f"inst{ii}.layer{li}.smp{si}."
                s = {"offset": o}
                r.field(s, o + 0, "7s", "wave_name")
                r.field(s, o + 7, "B", "fractions")       # low nibble: loop-start frac, high: loop-end frac
                r.field(s, o + 8, "I", "data_size")       # bytes     (loader: [esp+0x10F] -> smp+4)
                r.field(s, o + 12, "I", "loop_start")     # bytes     ([esp+0x113] -> smp+8)
                r.field(s, o + 16, "I", "loop_end")       # bytes     ([esp+0x117]; smp+0xC = end-start)
                r.field(s, o + 20, "H", "sample_rate")    # Hz        ([esp+0x11B] -> smp+0x54)
                r.field(s, o + 22, "I", "low_frequency")  # milli-Hz
                r.field(s, o + 26, "I", "high_frequency")
                r.field(s, o + 30, "I", "root_frequency")
                r.field(s, o + 34, "h", "tune")
                r.field(s, o + 36, "B", "balance")
                r.field(s, o + 37, "6B", "envelope_rate")
                r.field(s, o + 43, "6B", "envelope_offset")
                r.field(s, o + 49, "3B", "tremolo_speed_rate_depth")
                r.field(s, o + 52, "3B", "vibrato_speed_rate_depth")
                r.field(s, o + 55, "B", "modes")          # bit0 16-bit, bit1 unsigned ([esp+0x13E])
                r.field(s, o + 56, "h", "scale_frequency")
                r.field(s, o + 58, "H", "scale_factor")
                r.field(s, o + 60, "36s", "reserved")
                o += 96
                s["data_offset"] = o
                a.add(o, s["data_size"], r.prefix + "pcm")
                s["_pcm"] = b[o:o + s["data_size"]]
                o += s["data_size"]
                lay["sample_list"].append(s)
            ins["layer_list"].append(lay)
        h["instrument_list"].append(ins)
    h["_acct"] = a
    return h


def samples(h):
    for ins in h["instrument_list"]:
        for lay in ins["layer_list"]:
            for s in lay["sample_list"]:
                yield s


def decode(s, force_unsigned=None):
    """Return int16 numpy array. force_unsigned overrides the modes bit (for sanity tests)."""
    import numpy as np
    m = s["modes"]
    uns = bool(m & MODE_UNSIGNED) if force_unsigned is None else force_unsigned
    raw = s["_pcm"]
    if m & MODE_16BIT:
        x = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<u2" if uns else "<i2").astype(np.int32)
        if uns:
            x -= 0x8000
    else:
        x = np.frombuffer(raw, dtype=np.uint8 if uns else np.int8).astype(np.int32)
        if uns:
            x -= 0x80
        x <<= 8
    return x.astype(np.int16)


def engine_view(h):
    """What loader 0x004582B0 stores in the 0x60-byte sample struct (first sample only)."""
    s = next(samples(h))
    bits = 16 if s["modes"] & MODE_16BIT else 8           # 0x00458408..0x0045841B
    length, ls = s["data_size"], s["loop_start"]
    ll = s["loop_end"] - s["loop_start"]                  # 0x004583FA..0x00458405
    if bits == 16:                                        # 0x00458429..0x00458450 (sar 1)
        length, ls, ll = int(length / 2), int(ls / 2), int(ll / 2)
    if ll == 0:                                           # 0x0045846F..0x0045847B
        ll = length - ls
    return {"length_frames": length, "loop_start": ls, "loop_length": ll, "bits": bits,
            "rate": s["sample_rate"], "sign_flip": bool(s["modes"] & MODE_UNSIGNED)}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        b = open(p, "rb").read()
        h = parse(b)
        print(p, len(b), "bytes:", h["_acct"].summary())
        for s in samples(h):
            print("  sample", {k: v for k, v in s.items() if not k.startswith("_") and k != "reserved"})
        print("  engine:", engine_view(h))
