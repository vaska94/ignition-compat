#!/usr/bin/env python3
"""lft.py -- Ignition .LFT bitmap font reader / glyph-sheet renderer.

Layout (parser 0x00456270, wrapper LoadFont(name, flag) 0x00456420):
  +0x000 char[4]  "LFT\\0"               repe cmpsb vs 0x004BA6D4  (err 0x41A)
  +0x004 u16      version = 100 (0x64)  cmp word [eax+4],0x64    (err 0x424)
  +0x006 u16      glyph count           -> font+0x18
  +0x008 u16      space advance         -> font+0x1A (used for ' ' in text width 0x004564D0)
  +0x00A u16      glyph height          -> font+0x1C, desc.h for every glyph
  +0x00C u32[224] glyph data offset, index = char-0x20, 0xFFFFFFFF = no glyph
  +0x38C u8[768]  RGB palette (author tool palette; NEVER read by the EXE)
  +0x68C u16[224] glyph width, index = char-0x20 (copied to font+0x480)
  +0x84C ...      glyph pixels: for glyph i, width[i]*height raw 8-bit indices
                  at 0x84C+offset[i], row-major, pitch = width  (desc +0x14)
Colour 0 is transparent (INFERRED from sprite blitter convention).

Usage: lft.py [--png] [files...]
"""
import struct, sys, os, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import col as colmod

GAME = colmod.GAME
OUT = os.path.join(GAME, "_re/out/fonts")
DATA = 0x84C


def parse_lft(d):
    magic, ver, count, space, h = struct.unpack_from("<4sHHHH", d, 0)
    offs = struct.unpack_from("<224I", d, 0x0C)
    wid = struct.unpack_from("<224h", d, 0x68C)
    pal = d[0x38C:0x68C]
    glyphs = {}
    cover = bytearray(len(d) - DATA)
    anomalies = []
    for i in range(224):
        if offs[i] == 0xFFFFFFFF:
            if wid[i]:
                anomalies.append(f"absent glyph {i+32} has width {wid[i]}")
            continue
        w = wid[i]
        n = w * h if w > 0 else 0
        o = offs[i]
        if w <= 0:
            anomalies.append(f"glyph {chr(i+32)!r} offset {o} width {w} (sentinel/empty)")
        if o + n > len(cover):
            anomalies.append(f"glyph {chr(i+32)!r} runs past EOF")
            continue
        for k in range(o, o + n):
            cover[k] += 1
        glyphs[chr(i + 32)] = (w, d[DATA + o:DATA + o + n])
    gaps = sum(1 for c in cover if c == 0)
    overlaps = sum(1 for c in cover if c > 1)
    rep = dict(file_len=len(d), magic=magic, version=ver, count_field=count, present=len(glyphs),
               space=space, height=h, data_len=len(cover), uncovered_data_bytes=gaps,
               overlapping_bytes=overlaps, anomalies=anomalies,
               ok=(magic == b"LFT\0" and ver == 100 and gaps == 0 and overlaps == 0))
    return glyphs, h, pal, rep


def all_lft():
    return sorted(glob.glob(os.path.join(GAME, "fonts/*.LFT")) + glob.glob(os.path.join(GAME, "Baltazar/data/*.lft")))


def game_palette(path):
    rel = os.path.relpath(path, GAME).replace("\\", "/")
    base = os.path.basename(rel).lower()
    if base in ("small.lft", "mini.lft") or rel.startswith("Baltazar/"):
        return os.path.join(GAME, "sys.col"), "SYS.COL (loaded in 0x00418130 right after SYS.COL)"
    return colmod.level_col("USA"), "level COL (race HUD fonts, loaded with the level @0x0041AC40)"


def sheet(glyphs, h, pal, out, scale=3):
    from PIL import Image, ImageDraw
    items = sorted(glyphs.items())
    cols = 16
    cw = max([w for w, _ in glyphs.values()] + [6]) + 4
    ch = h + 14
    rows = (len(items) + cols - 1) // cols
    im = Image.new("RGB", (cols * cw, rows * ch), (40, 0, 40))
    dr = ImageDraw.Draw(im)
    rgb = [tuple(pal[3 * k:3 * k + 3]) for k in range(256)]
    for n, (c, (w, px)) in enumerate(items):
        x0, y0 = (n % cols) * cw + 2, (n // cols) * ch + 12
        dr.text((x0, y0 - 11), c, fill=(255, 255, 0))
        if w <= 0:
            continue
        for y in range(h):
            for x in range(w):
                v = px[y * w + x]
                if v:
                    im.putpixel((x0 + x, y0 + y), rgb[v])
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    im.save(out)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    png = "--png" in sys.argv
    bad = 0
    files = args or all_lft()
    for f in files:
        d = open(f, "rb").read()
        glyphs, h, pal, rep = parse_lft(d)
        bad += not rep["ok"]
        rel = os.path.relpath(f, GAME)
        print(f"{rel:30s} {rep}")
        if png:
            stem = rel.replace("/", "_").rsplit(".", 1)[0]
            cp, why = game_palette(f)
            sheet(glyphs, h, colmod.palette_for(cp), os.path.join(OUT, stem + ".png"))
            sheet(glyphs, h, pal, os.path.join(OUT, "embedded_palette", stem + "__embedded.png"))
    print(f"{len(files)} LFT files, {len(files)-bad} with glyph data exactly covering the data area")
