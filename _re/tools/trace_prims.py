#!/usr/bin/env python3
"""
trace_prims.py [ign_trace_prims.bin] [--scale 3] [--frame 0]

Read a captured frame of primitives and redraw it, at any resolution.

This is the renderer contract under test. The game hands its rasterizer a
NULL-terminated list of records whose vertices are ALREADY screen-space, in
24.8 fixed point, with no depth value - it paints back to front. So redrawing a
frame needs no 3D maths at all: scale the coordinates and fill the triangles in
the order given.

If the output resembles the game's own frame for the same moment, the contract
is right and a GPU renderer can be built on it. Scaling past 1.0 is the whole
point: the original is capped by a 488,000-byte static framebuffer, and this
path has no such limit.
"""
import struct
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    raise SystemExit("needs pillow: <venv>/bin/pip install pillow")

REC, UV = 0x48, 24   # captured window; a type 0x11 record is really 0x24 bytes


def parse_args(argv):
    """Options take a value, so "--frame 0" must not leave "0" looking like a filename."""
    opts, pos, i = {}, [], 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    return opts, pos


def load(path):
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


def main():
    opts, pos = parse_args(sys.argv[1:])
    scale = float(opts.get("scale", 3.0))
    want = int(opts.get("frame", 0))
    path = Path(pos[0]) if pos else Path("/mnt/c/Games/IGNITION/ign_trace_prims.bin")
    if not path.exists():
        raise SystemExit(f"{path} not found - set TRACE_PRIM_FRAMES in ign_compat.ini and race")

    rec_len, uv_len, frames = load(path)
    print(f"{path.name}: {len(frames)} frame(s)")
    for idx, block, recs in frames:
        clip = struct.unpack_from("<iiii", block, 0x10)
        types = {}
        for r in recs:
            types[struct.unpack_from("<I", r, 0)[0]] = types.get(struct.unpack_from("<I", r, 0)[0], 0) + 1
        print(f"  frame {idx}: {len(recs)} primitives, clip {clip}, "
              f"types " + ", ".join(f"0x{t:X}x{n}" for t, n in sorted(types.items())))

    if want >= len(frames):
        raise SystemExit(f"frame {want} not captured")
    idx, block, recs = frames[want]
    x0, y0, x1, y1 = struct.unpack_from("<iiii", block, 0x10)
    w, h = max(1, int((x1 - x0 + 1) * scale)), max(1, int((y1 - y0 + 1) * scale))
    img = Image.new("RGB", (w, h), (0, 0, 0))
    dr = ImageDraw.Draw(img)

    drawn = 0
    tex_key = {}
    for r in recs:
        t = struct.unpack_from("<I", r, 0)[0]
        if t not in (0x11, 0x12, 0x13, 0x16):
            continue
        # three vertices, x/y each 24.8 fixed, at +0x04 .. +0x18
        pts = []
        for v in range(3):
            fx, fy = struct.unpack_from("<ii", r, 4 + v * 8)
            pts.append(((fx / 256.0 - x0) * scale, (fy / 256.0 - y0) * scale))
        # Colour by which texture the triangle uses, so the structure of the
        # scene is visible. Texture bases are 64 KB-aligned pointers, so their
        # low byte is always zero - keying on that painted everything one shade.
        tex = struct.unpack_from("<I", r, 0x20)[0]
        key = tex_key.setdefault(tex, len(tex_key))
        col = ((key * 97) % 256, (key * 57 + 80) % 256, (key * 151 + 40) % 256)
        dr.polygon(pts, fill=col)
        drawn += 1

    out = Path("/mnt/c/Games/IGNITION/_re/out/prims")
    out.mkdir(parents=True, exist_ok=True)
    dst = out / f"frame{idx}_x{scale:g}.png"
    img.save(dst)
    print(f"\nredrew {drawn} triangles at {w}x{h} (scale {scale:g}) using "
          f"{len(tex_key)} distinct textures -> {dst}")
    print("compare against the game's own frame from DUMP_FRAME for the same moment")


if __name__ == "__main__":
    main()
