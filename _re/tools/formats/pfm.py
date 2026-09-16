#!/usr/bin/env python3
"""
pfm.py -- Baltazar/data/test.pfm: pre-projected 2D polygon "movie" played by the
"Script Player" (0x0040FFE0) as the animated main-menu background.

File = sequence of chunks {char[4] tag; u32 size; u8 payload[size]} ending with 'lixx' (size 0).
Tags (compared as dwords 0x3030696C.. at 0x00410040):
  li01  per-frame vertex table: size/4 vertices of (i16 x, i16 y); PFM_BuildFrame sign-extends
        each and shifts <<4 (0x00410475)
  li00  per-frame display list (byte stream, 0xFF-terminated), always preceded by its li01
  li02  texture-triangle table, 16-byte records (0x00410230):
          u16 u0,v0,u1,v1,u2,v2 ; u32 page (0..30, else 0) -> texture-page pointer
  li03  sprite table, 16-byte records (0x00410330):
          i16 a,b (<<8) ; u16 u0,v0,u1,v1 (<<8) ; u16 page ; u16 table index (into the
          0-terminated dword list passed as arg3, copied to 0x004C4C68)
  lixx  end

Display-list commands (0x00410496): u8 type, u8 count, then count records:
  type  7 : 8 B  i16 sprite(li03 idx), u16 p, u16 q, i16 vertex        -> sprite
  type 11 : 5 B  i16 v0, i16 v1, u8 colour                              -> line (INFERRED)
  type 13 : 7 B  skipped by the player
  type 15 : 7 B  i16 v0,v1,v2, u8 colour                                -> flat triangle
  type 17 : 8 B  i16 v0,v1,v2, i16 tex(li02 idx)                        -> textured triangle
  type 18 : 9 B  i16 v0,v1,v2, i16 tex, u8 table                        -> textured + table
  type 19 : 8 B  i16 v0,v1,v2, u8 colour, u8 table                      -> flat + table
  type FF : end.  Any other type makes PFM_BuildFrame return 0 (abort).
"""
import struct, sys, collections

REC = {7: 8, 11: 5, 13: 7, 15: 7, 17: 8, 18: 9, 19: 8}


def chunks(d):
    o = 0
    out = []
    while True:
        tag = d[o:o + 4]
        sz = struct.unpack_from("<I", d, o + 4)[0]
        out.append((o, tag, sz))
        o += 8 + sz
        if tag == b"lixx":
            break
    return out, o


def parse_display_list(p):
    """Returns (list of (type, [records...]), bytes consumed)."""
    i = 0
    cmds = []
    while True:
        t = p[i]
        if t == 0xFF:
            return cmds, i + 1
        c = p[i + 1]
        i += 2
        if t not in REC:
            raise ValueError("bad display-list type %d at %d" % (t, i - 2))
        rs = REC[t]
        recs = []
        for k in range(c):
            r = p[i:i + rs]
            if t == 7:
                recs.append(struct.unpack("<hHHh", r))
            elif t == 11:
                recs.append(struct.unpack("<hhB", r))
            elif t == 13:
                recs.append(tuple(r))
            elif t == 15:
                recs.append(struct.unpack("<hhhB", r))
            elif t == 17:
                recs.append(struct.unpack("<hhhh", r))
            elif t == 18:
                recs.append(struct.unpack("<hhhhB", r))
            elif t == 19:
                recs.append(struct.unpack("<hhhBB", r))
            i += rs
        cmds.append((t, recs))


def read(path):
    d = open(path, "rb").read()
    ch, end = chunks(d)
    frames = []
    li02 = li03 = None
    last01 = None
    for o, tag, sz in ch:
        p = d[o + 8:o + 8 + sz]
        if tag == b"li01":
            if sz % 4:
                raise ValueError("li01 size not multiple of 4")
            last01 = struct.unpack("<%dh" % (sz // 2), p)
        elif tag == b"li00":
            cmds, used = parse_display_list(p)
            frames.append(dict(off=o, verts=last01, cmds=cmds, used=used, size=sz))
        elif tag == b"li02":
            li02 = [struct.unpack_from("<6HI", p, i) for i in range(0, sz - sz % 16, 16)]
            li02_rem = sz % 16
        elif tag == b"li03":
            li03 = [struct.unpack_from("<hh4HHH", p, i) for i in range(0, sz - sz % 16, 16)]
            li03_rem = sz % 16
    return dict(data=d, chunks=ch, end=end, frames=frames, li02=li02, li03=li03,
                li02_rem=li02_rem, li03_rem=li03_rem)


def main():
    p = sys.argv[1] if len(sys.argv) > 1 else "Baltazar/data/test.pfm"
    r = read(p)
    d = r["data"]
    tags = collections.Counter(t for _, t, _ in r["chunks"])
    print("chunks:", dict(tags), "end=", r["end"], "file=", len(d), "unaccounted=", len(d) - r["end"])
    print("li02 records:", len(r["li02"]), "rem", r["li02_rem"], " li03 records:", len(r["li03"]), "rem", r["li03_rem"])
    bad = [f for f in r["frames"] if f["used"] != f["size"]]
    print("frames:", len(r["frames"]), " li00 streams with trailing/short bytes:", len(bad))
    th = collections.Counter(); prims = collections.Counter()
    xs = []; ys = []; maxv = 0; maxidx = 0; texmax = -1; spmax = -1; tables = collections.Counter()
    cols = collections.Counter(); pages = collections.Counter()
    for f in r["frames"]:
        v = f["verts"]; nv = len(v) // 2
        xs += v[0::2]; ys += v[1::2]
        for t, recs in f["cmds"]:
            th[t] += 1; prims[t] += len(recs)
            for rec in recs:
                if t in (11,):
                    maxidx = max(maxidx, rec[0], rec[1]); cols[rec[2]] += 1
                    assert max(rec[0], rec[1]) < nv
                elif t in (15, 17, 18, 19):
                    assert max(rec[:3]) < nv, (t, rec, nv)
                    if t in (17, 18): texmax = max(texmax, rec[3])
                    if t == 18: tables[("tex", rec[4])] += 1
                    if t == 19: tables[("flat", rec[4])] += 1; cols[rec[3]] += 1
                    if t == 15: cols[rec[3]] += 1
                elif t == 7:
                    spmax = max(spmax, rec[0]); assert rec[3] < nv
    print("command types:", dict(th)); print("primitives:", dict(prims))
    print("x range", min(xs), max(xs), " y range", min(ys), max(ys))
    print("max li02 idx used", texmax, " max li03 idx used", spmax, " table idx use", dict(tables))
    print("li02 pages", collections.Counter(e[6] for e in r["li02"]))
    print("li03", r["li03"])


if __name__ == "__main__":
    main()
