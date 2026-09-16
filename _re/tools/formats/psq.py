#!/usr/bin/env python3
"""
psq.py -- Baltazar/data/Default.psq: playback sequence for test.pfm.

Loader 0x0040CA88 (whole file read into malloc'd [0x004BE630], size in [0x004BE760]).
Consumer 0x0040C740 (once per menu-background tick):
    rec  = psq[seg]                 ; seg = [0x004BE5DC], stride 12
    pfm_frame = rec.start + sub     ; sub = [0x004BE75C]
    sub++ ; if sub == rec.count: sub = 0; seg++; if seg == size/12: seg = 0
    PFM_BuildFrame(pfm_frame)       ; 0x00410440

Record (12 bytes):  u32 start_frame, u32 frame_count, u32 unused (never read; 0 in file)
"""
import struct, sys


def read(path):
    d = open(path, "rb").read()
    n = len(d) // 12
    recs = [struct.unpack_from("<3I", d, i * 12) for i in range(n)]
    return recs, len(d) - n * 12


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else "Baltazar/data/Default.psq"
    recs, rem = read(p)
    tot = 0
    for i, (s, c, u) in enumerate(recs):
        print(f"seg {i:2d}: frames {s:4d}..{s+c-1:4d}  count {c:3d}  third={u}")
        tot += c
    print(f"{len(recs)} segments, {tot} frames per cycle, unaccounted bytes={rem}")
