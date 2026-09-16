#!/usr/bin/env python3
"""palette_test.py -- which palette belongs to which image?

1. Palette relationships: per-index equality between every pair of COL files.
2. For each level, render one TEX page and the level PIC with (a) its own COL,
   (b) every other COL; report the mean RGB error vs. the own-COL render and
   write side-by-side control PNGs to _re/out/images/palette_test/.
3. For every PIC: how many *used* pixel indices have identical RGB in the
   embedded (Animator) colour map and in each candidate COL.
"""
import os, sys, glob, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import col as colmod
from PIL import Image
import numpy as np

GAME = colmod.GAME
OUT = os.path.join(GAME, "_re/out/images/palette_test")
os.makedirs(OUT, exist_ok=True)

cols = {os.path.basename(p): np.frombuffer(colmod.read_col(p)[0], np.uint8).reshape(256, 3) for p in colmod.all_cols()}
names = list(cols)

print("== identical palette entries (count of 256) ==")
print(" " * 12 + "".join(f"{n[:8]:>9s}" for n in names))
for a in names:
    print(f"{a[:11]:12s}" + "".join(f"{int((cols[a] == cols[b]).all(1).sum()):9d}" for b in names))


def render(idx, pal):
    return pal[idx]


def err(a, b):
    return float(np.abs(a.astype(int) - b.astype(int)).mean())


print("\n== level TEX page 8 / level PIC rendered with each COL: mean abs RGB error vs own COL ==")
for lvl in ["Austria", "Brazil", "Canada", "Carib", "Iceland", "Japan", "USA"]:
    own = os.path.basename(colmod.level_col(lvl))
    tex = glob.glob(os.path.join(GAME, "LEVELS", lvl, "*.[Tt][Ee][Xx]"))[0]
    page = np.frombuffer(open(tex, "rb").read()[8 * 65536:9 * 65536], np.uint8).reshape(256, 256)
    picf = glob.glob(os.path.join(GAME, "LEVELS", lvl, "*.PIC"))[0]
    pd = open(picf, "rb").read()
    pic = np.frombuffer(pd[0x34E:0x34E + 64000], np.uint8).reshape(200, 320)
    ref_t, ref_p = render(page, cols[own]), render(pic, cols[own])
    row_t = [f"{n[:8]}={err(render(page, cols[n]), ref_t):5.1f}" for n in names if n != own]
    row_p = [f"{n[:8]}={err(render(pic, cols[n]), ref_p):5.1f}" for n in names if n != own]
    print(f"{lvl:8s} TEX: {' '.join(row_t)}")
    print(f"{'':8s} PIC: {' '.join(row_p)}")
    wrong = "sys.col" if lvl != "Canada" else "JAPAN.COL"
    strip = np.concatenate([ref_t, np.zeros((256, 8, 3), np.uint8), render(page, cols[wrong])], 1)
    Image.fromarray(strip).save(os.path.join(OUT, f"{lvl}_TEXpage08_own_vs_{wrong.replace('.', '_')}.png"))
    strip = np.concatenate([ref_p, np.zeros((200, 8, 3), np.uint8), render(pic, cols[wrong])], 1)
    Image.fromarray(strip).save(os.path.join(OUT, f"{lvl}_PIC_own_vs_{wrong.replace('.', '_')}.png"))

print("\n== PIC used indices: # whose RGB equals each COL (embedded colour map vs file) ==")
import pic as picmod
for f in picmod.all_pics():
    d = open(f, "rb").read()
    w, h, pal, px, rep = picmod.parse_pic(d)
    emb = np.frombuffer(pal, np.uint8).reshape(256, 3)
    used = sorted(set(px))
    counts = {n: sum(1 for i in used if (emb[i] == cols[n][i]).all()) for n in names}
    best = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
    print(f"{os.path.relpath(f, GAME):30s} used={len(used):3d} best={best}")
