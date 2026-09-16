#!/usr/bin/env python3
"""
compare_frame.py [--frame N] [--level auto]

Diff our re-rendered frame against the pixels the game itself produced.

Until now the renderer has only been judged by eye. This is the real test: the
harness records both the primitive list for a frame and the game's own 8-bit
framebuffer, so the two can be compared index by index.

Pairing matters. The primitive hook runs on submit *entry*, before the frame is
drawn, so the framebuffer captured with frame N still holds frame N-1's result.
Prim frame N is therefore compared against framebuffer frame N+1.
"""
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

G = Path("/mnt/c/Games/IGNITION")
OUT = G / "_re/out/prims"
VENV = Path("/tmp/claude-0/-mnt-c-Games-IGNITION/e483514a-64c5-4fbd-ba08-0c5b533be65e/scratchpad/.venv/bin/python")
LEVELS = ["Canada", "USA", "Carib", "Brazil", "Austria", "Iceland", "Japan"]


def parse_args(argv):
    opts, pos, i = {}, [], 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        else:
            pos.append(argv[i]); i += 1
    return opts, pos


def prim_frames(path):
    d = path.read_bytes()
    rec, uv = struct.unpack_from("<II", d, 8)
    o, out = 16, []
    while o + 8 + 0x20 <= len(d):
        idx, count = struct.unpack_from("<II", d, o)
        block = d[o + 8: o + 8 + 0x20]
        o += 8 + 0x20
        if o + count * (rec + uv) > len(d):
            break
        o += count * (rec + uv)
        out.append((idx, block, count))
    return out


def fb_frames(path):
    d = path.read_bytes()
    fb_bytes, pal_bytes = struct.unpack_from("<II", d, 8)
    o, out = 16, []
    while o + 8 <= len(d):
        idx, n = struct.unpack_from("<II", d, o); o += 8
        pal = d[o:o + pal_bytes]; o += pal_bytes
        fb = d[o:o + n]; o += n
        out.append((idx, pal, fb))
    return out


def detect_level():
    """Pick the level whose exported geometry contains the recorded car path."""
    s = (G / "ign_trace_sim.bin").read_bytes()
    o, xs, zs = 16, [], []
    while o + 20 <= len(s):
        st, n, cb = struct.unpack_from("<III", s, o); o += 20
        car = s[o:o + cb]
        xs.append(struct.unpack_from("<d", car, 0x00)[0] - 25600)
        zs.append(struct.unpack_from("<d", car, 0x10)[0] - 25600)
        o += n * cb
    for lvl in LEVELS:
        obj = G / "_re/out/tracks" / f"{lvl}.obj"
        if not obj.exists():
            continue
        lo = [1e18] * 3; hi = [-1e18] * 3
        for line in obj.open():
            if line.startswith("v "):
                v = [float(t) for t in line.split()[1:4]]
                for i in range(3):
                    lo[i] = min(lo[i], v[i]); hi[i] = max(hi[i], v[i])
        if all(lo[0] <= x <= hi[0] for x in xs) and all(lo[2] <= z <= hi[2] for z in zs):
            return lvl
    return None


def main():
    opts, _ = parse_args(sys.argv[1:])
    n = int(opts.get("frame", 0))
    level = opts.get("level", "auto")
    if level == "auto":
        level = detect_level()
        if not level:
            raise SystemExit("could not tell which track was driven")
        print(f"track detected from the car path: {level}")

    prims = prim_frames(G / "ign_trace_prims.bin")
    fbs = fb_frames(G / "ign_trace_fb.bin")
    if n + 1 >= len(fbs):
        raise SystemExit(f"need framebuffer {n+1}; only {len(fbs)} captured")

    idx, block, count = prims[n]
    x0, y0, x1, y1 = struct.unpack_from("<iiii", block, 0x10)
    W, H = x1 - x0 + 1, y1 - y0 + 1
    print(f"prim frame {idx}: {count} primitives, {W}x{H}")

    print("rendering with prim_render.py ...")
    r = subprocess.run([str(VENV), str(G / "_re/tools/prim_render.py"),
                        str(G / "ign_trace_prims.bin"),
                        "--frame", str(n), "--scale", "1", "--level", level],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:]); print(r.stderr[-2000:])
        raise SystemExit("prim_render.py failed")

    cands = sorted(OUT.glob(f"frame{n}_tex_{W}x{H}_indices.npy")) or \
            sorted(OUT.glob("*_indices.npy"), key=lambda p: p.stat().st_mtime)[-1:]
    if not cands:
        raise SystemExit("prim_render.py produced no index buffer")
    ours = np.load(cands[-1]).reshape(H, W).astype(np.uint8)

    _, pal, fb = fbs[n + 1]
    theirs = np.frombuffer(fb[:W * H], dtype=np.uint8).reshape(H, W)

    same = int((ours == theirs).sum())
    total = W * H
    print(f"\nexact index matches: {same}/{total} = {100*same/total:.2f}%")

    diff = (ours != theirs)
    if diff.any():
        ys, xs = np.nonzero(diff)
        print(f"differing pixels: {diff.sum()}  bbox x[{xs.min()},{xs.max()}] y[{ys.min()},{ys.max()}]")
        try:
            from PIL import Image
            rgb = np.zeros((H, W, 3), np.uint8)
            p = np.frombuffer(pal, np.uint8).reshape(256, 4)
            rgb[...] = p[theirs][..., 2::-1]          # palette is BGRX
            rgb[diff] = (255, 0, 255)                 # magenta = mismatch
            Image.fromarray(rgb).save(OUT / f"compare_frame{n}.png")
            Image.fromarray(p[theirs][..., 2::-1]).save(OUT / f"game_frame{n+1}.png")
            print(f"wrote {OUT}/compare_frame{n}.png (magenta = mismatch) and game_frame{n+1}.png")
        except ImportError:
            pass


if __name__ == "__main__":
    main()
