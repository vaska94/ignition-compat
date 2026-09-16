#!/usr/bin/env python3
"""
prim_render.py [ign_trace_prims.bin] [--frame 0] [--scale 1] [--level Canada]

Redraw a captured primitive frame with REAL textures and the level palette.

Companion to trace_prims.py (which draws flat colours). Everything needed is in
the capture plus the shipped assets:

  * vertices        record +0x04..+0x18, 24.8 fixed SCREEN coords (already projected)
  * uv              record +0x1C -> 3 pairs of 8.8 TEXEL coords in a 256x256 page
                    (the harness copies 24 bytes from that pointer into the capture)
  * texture page    record +0x20 = <TEX slot base> + (page << 16)
  * order           list order; the game paints back to front with no depth buffer

The texture pointer is a run-time address, so it is turned back into a file+page
with a table of slot bases observed in the capture (see SLOTS). The RULE is
fixed: pointer = slot_base + page*0x10000, because the emitter computes
    texbase = [0x004CDC14] + dword[face+0x28]      (dword[face+0x28] == page<<16)
and the TEX loader rounds every slot to a 64 KiB multiple.

Output: _re/out/prims/frame<N>_tex_<W>x<H>.png
"""
import os
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

GAME = Path("/mnt/c/Games/IGNITION")
OUT = GAME / "_re/out/prims"

# Run-time TEX slot bases seen in ign_trace_prims.bin (a Canada race).
# Verified: for all 185 level triangles and all 212 car triangles whose UV
# pointer could be matched back into CANADA.MSH / Cars.msh, the mesh face's
# `page` word satisfies  rec[+0x20] - (page<<16) == the base below.
#
# Slot stride = roundup(file size, 64 KiB), so a slot can be one page LARGER than
# the file: Cars.tex is 393280 B = 6 pages + 64 B, which rounds to a SEVEN page
# slot. Pages past the file's end read as zero.
#
# The first two bases are VERIFIED: for all 185 level and all 212 car triangles
# whose UV pointer could be matched back into CANADA.MSH / Cars.msh, the mesh
# face's `page` word satisfies rec[+0x20] - (page<<16) == the base below.
# The GENERAL bases are INFERRED by accumulating slot strides in load order and
# are unverified - no GENERAL texture appears in this capture. A pointer that
# matches no slot is reported as UNRESOLVED rather than silently mis-attributed.
SLOTS = [
    (0x0F930000, "LEVELS/Canada/CANADA.TEX", 16),   # VERIFIED  level slot, arena + 0
    (0x0FA30000, "CARS/Cars.tex", 7),               # VERIFIED  base; 7-page slot
    (0x0FAA0000, "GENERAL/LIGHT.TEX", 1),           # INFERRED
    (0x0FAB0000, "GENERAL/SMOKE.TEX", 1),           # INFERRED
    (0x0FAC0000, "GENERAL/DARKSMOK.TEX", 1),        # INFERRED
    (0x0FAD0000, "GENERAL/BOOM.TEX", 1),            # INFERRED
    (0x0FAE0000, "GENERAL/DIVSPR.TEX", 1),          # INFERRED (order vs EXSMOKE unproven)
    (0x0FAF0000, "GENERAL/EXSMOKE.TEX", 1),         # INFERRED
]

REC_WIN, UV_WIN = 0x48, 24          # capture window sizes
TEXTURED = (0x11, 0x12, 0x16)       # affine texture-mapped triangle records
FLAT = (0x13,)                      # flat, destination-blended triangle records
FLAT_SHADED = (0x0F,)               # flat, shade-LUT-coloured triangle records

# Vertex X offsets within the record. The three rasterizer families use DIFFERENT
# vertex strides - this is the main porting trap. Y always follows X at +4.
#   0x11/0x12/0x13/0x16 : 8-byte vertices
#   0x0F                : 12-byte vertices (third dword written as 0, never read)
VOFF = {
    0x11: (0x04, 0x0C, 0x14), 0x12: (0x04, 0x0C, 0x14),
    0x13: (0x04, 0x0C, 0x14), 0x16: (0x04, 0x0C, 0x14),
    0x0F: (0x04, 0x10, 0x1C),
}


def parse_args(argv):
    opts, pos, i = {}, [], 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    return opts, pos


def load_capture(path):
    d = path.read_bytes()
    if d[:7] != b"IGNPRM1":
        raise SystemExit(f"{path}: not a primitive capture")
    rec, uv = struct.unpack_from("<II", d, 8)
    o, frames = 16, []
    while o + 8 + 0x20 <= len(d):
        idx, count = struct.unpack_from("<II", d, o)
        block = d[o + 8: o + 8 + 0x20]
        o += 8 + 0x20
        if o + count * (rec + uv) > len(d):
            break
        recs = [d[o + i * (rec + uv): o + (i + 1) * (rec + uv)] for i in range(count)]
        o += count * (rec + uv)
        frames.append((idx, block, recs))
    return rec, uv, frames


def load_pages():
    """slot base -> (pages array (n,256,256) uint8, name)

    Texture slots are allocated at run time, so their addresses differ from run
    to run: a base hardcoded from one capture resolves nothing in the next.
    Prefer the bases the harness actually recorded (ign_trace_ctx.bin), and fall
    back to the built-in list only when no capture is present.
    """
    import struct as _struct
    bases = []
    _ctx = GAME / "ign_trace_ctx.bin"
    if _ctx.exists():
        _c = _ctx.read_bytes()
        if _c[:6] == b"IGNCTX":
            bases = sorted({b for b in _struct.unpack_from("<10I", _c, 16 + 0x20) if b})
    if bases:
        print(f"   using captured texture slot bases: " +
              ", ".join(f"0x{b:08X}" for b in bases[:len(SLOTS)]))
    out = {}
    for _i, (base, rel, npages) in enumerate(SLOTS):
        if _i < len(bases):
            base = bases[_i]
        raw = (GAME / rel).read_bytes()
        buf = np.zeros(npages * 65536, np.uint8)      # a partial last page stays 0
        buf[:min(len(raw), buf.size)] = np.frombuffer(raw[:buf.size], np.uint8)
        out[base] = (buf.reshape(npages, 256, 256), rel)
    return out


def load_palette(level):
    col = None
    for p in sorted((GAME / "LEVELS" / level).iterdir()):
        if p.suffix.lower() == ".col":
            col = p.read_bytes()
    if col is None:
        raise SystemExit(f"no .COL in LEVELS/{level}")
    pal = np.frombuffer(col[8:8 + 768], np.uint8).reshape(256, 3).copy()
    return pal


def load_table(level, ext):
    """a 65536-byte 256x256 byte LUT shipped with the level (.pan / .shd / .tab)"""
    for p in sorted((GAME / "LEVELS" / level).iterdir()):
        if p.suffix.lower() == ext:
            return np.frombuffer(p.read_bytes(), np.uint8).copy()
    return None


def resolve_tex(ptr, pages):
    for base, (arr, name) in pages.items():
        page = (ptr - base) >> 16
        if 0 <= page < arr.shape[0] and (ptr - base) & 0xFFFF == 0:
            return arr[page], name, page
    return None, None, None


SAMPLE_OFFSET = float(os.environ.get("IGN_SAMPLE_OFFSET", "0.5"))


def tri_cover(pts, W, H):
    """barycentric coverage of one triangle; returns (x0,y0,slices,l0,l1,l2,mask)"""
    (x0, y0), (x1, y1), (x2, y2) = pts
    den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if den == 0:
        return None
    bx0, bx1 = int(np.floor(min(x0, x1, x2))), int(np.ceil(max(x0, x1, x2)))
    by0, by1 = int(np.floor(min(y0, y1, y2))), int(np.ceil(max(y0, y1, y2)))
    bx0, by0 = max(bx0, 0), max(by0, 0)
    bx1, by1 = min(bx1, W - 1), min(by1, H - 1)
    if bx1 < bx0 or by1 < by0:
        return None
    # Where inside a pixel we sample. 0.5 is the pixel centre, the usual
    # convention for a float rasteriser. The game walks spans in 24.8 fixed
    # point and truncates, which behaves like sampling at the pixel origin, so
    # this is adjustable to test that against the captured framebuffer.
    X = np.arange(bx0, bx1 + 1, dtype=np.float64)[None, :] + SAMPLE_OFFSET
    Y = np.arange(by0, by1 + 1, dtype=np.float64)[:, None] + SAMPLE_OFFSET
    l0 = ((y1 - y2) * (X - x2) + (x2 - x1) * (Y - y2)) / den
    l1 = ((y2 - y0) * (X - x2) + (x0 - x2) * (Y - y2)) / den
    l2 = 1.0 - l0 - l1
    e = -1e-9
    mask = (l0 >= e) & (l1 >= e) & (l2 >= e)
    if not mask.any():
        return None
    return bx0, by0, bx1, by1, l0, l1, l2, mask


def main():
    opts, pos = parse_args(sys.argv[1:])
    scale = float(opts.get("scale", 1.0))
    want = int(opts.get("frame", 0))
    level = opts.get("level", "Canada")
    uvswap = "uvswap" in opts
    flat_mode = opts.get("flat13", "shd")        # shd | pan | color | skip
    # The game paints back to front with no per-primitive depth, so list order IS
    # the depth test. --reverse renders the list backwards; if the result is not
    # visibly wrong, the ordering claim would be untested.
    reverse = "reverse" in opts
    path = Path(pos[0]) if pos else GAME / "ign_trace_prims.bin"

    _, _, frames = load_capture(path)
    if want >= len(frames):
        raise SystemExit(f"frame {want} not captured ({len(frames)} frames)")
    idx, block, recs = frames[want]
    x0, y0, x1, y1 = struct.unpack_from("<iiii", block, 0x10)
    W = max(1, int(round((x1 - x0 + 1) * scale)))
    H = max(1, int(round((y1 - y0 + 1) * scale)))

    pages = load_pages()
    pal = load_palette(level)
    pan = load_table(level, ".pan")
    shd = load_table(level, ".shd")

    fb = np.zeros((H, W), np.uint8)              # 8-bit palette-index framebuffer
    stats = {"textured": 0, "flat": 0, "flat_shaded": 0,
             "no_tex": 0, "degenerate": 0, "offscreen": 0}
    missing = {}
    used = {}

    for r in (reversed(recs) if reverse else recs):
        t = struct.unpack_from("<I", r, 0)[0]
        if t not in VOFF:
            continue
        pts = []
        for off in VOFF[t]:
            fx, fy = struct.unpack_from("<ii", r, off)
            pts.append(((fx / 256.0 - x0) * scale, (fy / 256.0 - y0) * scale))
        cov = tri_cover(pts, W, H)
        if cov is None:
            stats["degenerate" if len(set(pts)) < 3 else "offscreen"] += 1
            continue
        bx0, by0, bx1, by1, l0, l1, l2, mask = cov
        sub = fb[by0:by1 + 1, bx0:bx1 + 1]

        if t in FLAT_SHADED:
            # Type 0x0F: 12-byte vertices, +0x28 u8 colour, +0x2D u8 light.
            # The real pixel is shadeLUT[colour*256 + light], resolved ONCE per
            # triangle (so it is genuinely flat). That LUT is submit-block +0x0C,
            # a run-time pointer whose CONTENTS the capture does not record, so
            # the raw colour index is drawn here as a stand-in.
            sub[mask] = r[0x28]
            stats["flat_shaded"] += 1
            continue

        if t in FLAT:
            # +0x1C = shade/blend row, +0x20 = base of a 256x256 byte table.
            # The per-pixel form is table[(row << 8) | destination_pixel], i.e. it
            # READS THE FRAMEBUFFER. Which table the run-time pointer names cannot
            # be decided from the capture alone, so both candidates are selectable.
            src = struct.unpack_from("<I", r, 0x1C)[0] & 0xFF
            if flat_mode == "skip":
                continue
            table = {"pan": pan, "shd": shd}.get(flat_mode)
            if table is not None:
                sub[mask] = table[(src << 8) + sub[mask].astype(np.int32)]
            else:
                sub[mask] = src
            stats["flat"] += 1
            continue

        texptr = struct.unpack_from("<I", r, 0x20)[0]
        page, name, pidx = resolve_tex(texptr, pages)
        if page is None:
            missing[texptr] = missing.get(texptr, 0) + 1
            stats["no_tex"] += 1
            continue
        used[f"{name}:{pidx}"] = used.get(f"{name}:{pidx}", 0) + 1
        uv = struct.unpack_from("<6i", r, REC_WIN)        # 8.8 texels, (u,v) x3
        if t == 0x16:
            # Type 0x16 packs a 14-bit UV plus a page index in the upper bits:
            # the handler does `and 0x3FFF` then `shl 2`, which lands the value
            # in the same 8.8 units type 0x11 uses directly (0x3FFF<<2 = 255.98).
            uv = [(w & 0x3FFF) << 2 for w in uv]
        u = np.array([uv[0], uv[2], uv[4]], np.float64) / 256.0
        v = np.array([uv[1], uv[3], uv[5]], np.float64) / 256.0
        if uvswap:
            u, v = v, u
        uu = l0 * u[0] + l1 * u[1] + l2 * u[2]
        vv = l0 * v[0] + l1 * v[1] + l2 * v[2]
        iu = np.floor(uu).astype(np.int64) & 0xFF
        iv = np.floor(vv).astype(np.int64) & 0xFF
        sub[mask] = page[iv[mask], iu[mask]]
        stats["textured"] += 1

    OUT.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(pal[fb], "RGB")
    tag = ("_uvswap" if uvswap else "") + ("_reverse" if reverse else "")
    dst = OUT / f"frame{idx}_tex_{W}x{H}{tag}.png"
    img.save(dst)
    print(f"frame {idx}: {len(recs)} records -> {dst}  ({W}x{H})")
    print("  ", stats)
    print("   pages used:", dict(sorted(used.items())))
    if missing:
        print("   UNRESOLVED texture pointers:", {f"{k:08X}": v for k, v in missing.items()})
    # also save the raw index buffer for pixel-level comparison work
    np.save(OUT / f"frame{idx}_tex_{W}x{H}{tag}_indices.npy", fb)


if __name__ == "__main__":
    main()
