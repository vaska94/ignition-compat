#!/usr/bin/env python3
"""RIFF WAVE reader with full byte accounting (chunks + pad bytes + LIST/INFO
sub-chunks, cue points, smpl loops).  `engine_view()` reproduces loader
0x004587D0: only 'fmt ', 'data', 'smpl', 'fact' are interpreted, every other
chunk is fseek()-skipped; requires wFormatTag==1 and nChannels==1; 8-bit data
is sign-flipped (xor 0x80, 0x00459530); loop = first smpl loop if
cSampleLoops != 0, else [0, length).

usage: wav.py FILE [FILE ...]
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from acct import Acct


def parse(b):
    a = Acct(len(b))
    h = {"chunks": []}
    a.add(0, 12, "RIFF header")
    h["riff_id"], h["riff_size"], h["wave_id"] = b[0:4], struct.unpack_from("<I", b, 4)[0], b[8:12]
    o = 12
    end = min(len(b), 8 + h["riff_size"])
    while o + 8 <= end:
        cid = b[o:o + 4]
        cl = struct.unpack_from("<I", b, o + 4)[0]
        c = {"id": cid.decode("latin1"), "offset": o, "size": cl}
        a.add(o, 8, f"{c['id']}.hdr")
        body = b[o + 8:o + 8 + cl]
        if cid == b"fmt ":
            (c["format_tag"], c["channels"], c["rate"], c["avg_bps"], c["block_align"],
             c["bits"]) = struct.unpack_from("<HHIIHH", body, 0)
            a.add(o + 8, 16, "fmt.pcm")
            if cl > 16:
                a.add(o + 24, cl - 16, "fmt.extra")
        elif cid == b"data":
            h["data_offset"], h["data_size"] = o + 8, cl
            a.add(o + 8, cl, "data")
        elif cid == b"smpl":
            v = struct.unpack_from("<9I", body, 0)
            c.update(dict(zip(["manufacturer", "product", "sample_period", "midi_unity_note",
                               "midi_pitch_fraction", "smpte_format", "smpte_offset", "num_loops",
                               "sampler_data"], v)))
            a.add(o + 8, 36, "smpl.hdr")
            c["loops"] = []
            for i in range(c["num_loops"]):
                lv = struct.unpack_from("<6I", body, 36 + 24 * i)
                c["loops"].append(dict(zip(["id", "type", "start", "end", "fraction", "play_count"], lv)))
                a.add(o + 8 + 36 + 24 * i, 24, f"smpl.loop{i}")
            rest = 36 + 24 * c["num_loops"]
            if cl > rest:
                a.add(o + 8 + rest, cl - rest, "smpl.sampler_data")
        elif cid == b"cue ":
            n = struct.unpack_from("<I", body, 0)[0]
            c["points"] = [dict(zip(["id", "position", "chunk", "chunk_start", "block_start", "sample_offset"],
                                    struct.unpack_from("<II4sIII", body, 4 + 24 * i)))
                           for i in range(n)]
            a.add(o + 8, cl, "cue")
            c["cue_bytes_expected"] = 4 + 24 * n
        elif cid == b"LIST":
            c["list_type"] = body[:4].decode("latin1")
            c["items"] = []
            p = 4
            while p + 8 <= cl:
                sid = body[p:p + 4].decode("latin1")
                sl = struct.unpack_from("<I", body, p + 4)[0]
                c["items"].append((sid, body[p + 8:p + 8 + sl].rstrip(b"\0").decode("latin1", "replace")))
                p += 8 + sl + (sl & 1)
            c["list_parsed_to"] = p
            a.add(o + 8, cl, "LIST")
        elif cid == b"fact":
            c["sample_count"] = struct.unpack_from("<I", body, 0)[0]
            a.add(o + 8, cl, "fact")
        else:
            a.add(o + 8, cl, c["id"])
        h["chunks"].append(c)
        o += 8 + cl
        if cl & 1:
            a.add(o, 1, "pad")
            o += 1
    h["end"] = o
    h["_acct"] = a
    return h


def chunk(h, cid):
    for c in h["chunks"]:
        if c["id"] == cid:
            return c
    return None


def engine_view(h):
    fmt = chunk(h, "fmt ")
    if not fmt or fmt["format_tag"] != 1 or fmt["channels"] != 1:     # 0x00458B63..0x00458BA7
        return None
    bits = (fmt["bits"] + 7) & 0xF8                                   # 0x00458BCE..0x00458BD6
    length = h["data_size"] * 8 // bits                               # 0x00458BC6..0x00458BE1
    sm = chunk(h, "smpl")
    if sm and sm["num_loops"]:                                        # 0x00458BE4..0x00458C0C
        ls, ll = sm["loops"][0]["start"], sm["loops"][0]["end"] - sm["loops"][0]["start"]
    else:
        ls, ll = 0, length
    if ll == 0:                                                       # 0x00458C37..0x00458C41
        ll = length - ls
    return {"length_frames": length, "loop_start": ls, "loop_length": ll, "bits": bits,
            "rate": fmt["rate"], "sign_flip": bits == 8}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        b = open(p, "rb").read()
        h = parse(b)
        print(p, len(b), "bytes:", h["_acct"].summary())
        for c in h["chunks"]:
            print("  ", c)
        print("  engine:", engine_view(h))
