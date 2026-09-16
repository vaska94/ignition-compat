#!/usr/bin/env python3
"""tex.py -- Ignition .TEX reader / PNG converter.

Format: headerless raw 8-bit palette indices, concatenated 256x256 texture
pages (65536 bytes each), row-major, no compression.

Loader (0x00419D10 sizing pass, 0x0041A4CC.. read pass): every TEX file is
read whole (_open O_BINARY, _filelength, _read) into one big texture arena;
each file's slot is its length rounded UP to a multiple of 0x10000
(`add eax,0xFFFF / and eax,0xFFFF0000` @0x00419D73, 0x00419DEC, 0x00419F00 ...).
Slot bases: level TEX [0x005530F0], CARS.TEX [0x005530F4], then GENERAL
LIGHT/SMOKE/DARKSMOK/BOOM/... [0x005530F8..0x00553110].
A file whose length is not a multiple of 65536 therefore has a partially
filled last page (tail bytes = first rows of that page; the rest of the slot
is whatever the arena held).

Usage: tex.py [--png] [files...]
"""
import sys, os, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import col as colmod

GAME = colmod.GAME
OUT = os.path.join(GAME, "_re/out/images/tex")
PAGE = 256


def parse_tex(d):
    full, tail = divmod(len(d), PAGE * PAGE)
    pages = [d[i * 65536:(i + 1) * 65536] for i in range(full)]
    t = d[full * 65536:]
    last_nz = max((j for j, b in enumerate(t) if b), default=-1)
    rows = (last_nz + 1 + PAGE - 1) // PAGE          # whole rows holding texel data
    pad = t[rows * PAGE:]
    rep = dict(file_len=len(d), full_pages=full, tail_bytes=tail,
               partial_page_rows=rows, zero_padding_bytes=len(pad),
               padding_all_zero=(pad.count(0) == len(pad)),
               accounted=full * 65536 + rows * PAGE + len(pad),
               remainder=len(d) - (full * 65536 + rows * PAGE + len(pad)))
    return pages, t[:rows * PAGE], rep


def all_tex():
    fs = glob.glob(os.path.join(GAME, "LEVELS/*/*.[Tt][Ee][Xx]")) + glob.glob(os.path.join(GAME, "GENERAL/*.TEX"))
    fs += [os.path.join(GAME, "CARS/Cars.tex"), os.path.join(GAME, "Baltazar/data/Menucar.tex")]
    fs += glob.glob(os.path.join(GAME, "Baltazar/data/textures/*.tex"))
    return sorted(set(fs))


def game_palette(path):
    rel = os.path.relpath(path, GAME).replace("\\", "/")
    if rel.startswith("LEVELS/"):
        return colmod.level_col(rel.split("/")[1]), "own level COL"
    if rel.startswith("Baltazar/"):
        return os.path.join(GAME, "Baltazar/data/Menu.col"), "menu.col (frontend 3D menu)"
    if rel.startswith("CARS/"):
        return colmod.level_col("USA"), "any level COL (cars use shared indices 160-255)"
    return colmod.level_col("Canada"), "level COL of the running track (GENERAL effects; Canada used here)"


def save_pages(pages, tail, pal, stem, sheet_cols=4):
    from PIL import Image
    os.makedirs(os.path.join(OUT, stem), exist_ok=True)
    imgs = []
    for i, p in enumerate(pages):
        im = Image.frombytes("P", (PAGE, PAGE), bytes(p)); im.putpalette(pal)
        im.save(os.path.join(OUT, stem, f"page{i:02d}.png")); imgs.append(im)
    if tail:
        rows = (len(tail) + PAGE - 1) // PAGE
        t = bytes(tail) + bytes(rows * PAGE - len(tail))
        im = Image.frombytes("P", (PAGE, rows), t); im.putpalette(pal)
        im.save(os.path.join(OUT, stem, f"page{len(pages):02d}_partial_{len(tail)}B.png")); imgs.append(im)
    n = len(imgs)
    cols = min(sheet_cols, n); rws = (n + cols - 1) // cols
    sheet = Image.new("P", (cols * (PAGE + 4), rws * (PAGE + 4)), 0); sheet.putpalette(pal)
    for i, im in enumerate(imgs):
        sheet.paste(im, ((i % cols) * (PAGE + 4), (i // cols) * (PAGE + 4)))
    sheet.save(os.path.join(OUT, stem + "__sheet.png"))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    png = "--png" in sys.argv
    for f in args or all_tex():
        d = open(f, "rb").read()
        pages, tail, rep = parse_tex(d)
        rel = os.path.relpath(f, GAME)
        cp, why = game_palette(f)
        print(f"{rel:38s} {rep}  palette={os.path.basename(cp)} ({why})")
        if png:
            save_pages(pages, tail, colmod.palette_for(cp), rel.replace("/", "_").rsplit(".", 1)[0])
