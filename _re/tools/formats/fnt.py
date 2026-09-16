#!/usr/bin/env python3
"""fnt.py -- fonts/IGNITION.FNT reader / glyph-sheet renderer.

Loaded raw (open/filelength/pool-alloc/read, 0x0041AC40) into [0x00525E58];
drawn only by DrawDebugText 0x004133D0(x, y, str, dst, pitch, font, colourBase),
called from the debug overlay 0x0043E9C0 ("FPS %d", "RECORDING!", ...).

  +0x000 u16      glyph count (56)                 (not read by the drawer)
  +0x002 u16      glyph height (10)                mov ax,[ecx+2]
  +0x004 i16      extra advance (-1)               mov si,[ecx+4]; added per char
  +0x006 u8[192]  glyph width, by glyph index      mov cl,[ecx+eax+6]
  +0x0C6 u8[256]  char -> glyph index, 0xFF = none movsx eax,byte [eax+ecx+0xC6]
  +0x1C6 u32[224] glyph pixel offset               mov eax,[ebx+eax*4+0x1C6]
  +0x546 ...      pixels: width*height bytes/glyph lea ebx,[eax+ebx+0x546]
Pixel v != 0 is drawn as colourBase + v (add cl,al @0x0041347C); 0 = transparent.

Usage: fnt.py [--png]
"""
import struct, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import col as colmod

GAME = colmod.GAME
PATH = os.path.join(GAME, "fonts/IGNITION.FNT")
OUT = os.path.join(GAME, "_re/out/fonts")


def parse_fnt(d):
    count, h, adv = struct.unpack_from("<HHh", d, 0)
    widths = d[6:6 + 192]
    cmap = d[0xC6:0xC6 + 256]
    offs = struct.unpack_from("<224I", d, 0x1C6)
    data = d[0x546:]
    cover = bytearray(len(data))
    glyphs = {}
    for g in range(count):
        w, o = widths[g], offs[g]
        for k in range(o, o + w * h):
            cover[k] += 1
        glyphs[g] = (w, data[o:o + w * h])
    chars = {chr(c): cmap[c] for c in range(256) if cmap[c] != 0xFF}
    rep = dict(file_len=len(d), count=count, height=h, extra_advance=adv,
               widths_beyond_count_zero=all(b == 0 for b in widths[count:]),
               offsets_beyond_count_zero=all(o == 0 for o in offs[count:]),
               mapped_chars=len(chars), data_len=len(data), uncovered=cover.count(0),
               overlap=sum(1 for c in cover if c > 1), pixel_values=sorted(set(data)))
    rep["ok"] = rep["uncovered"] == 0 and rep["overlap"] == 0 and rep["widths_beyond_count_zero"] and rep["offsets_beyond_count_zero"]
    return glyphs, chars, h, rep


def sheet(glyphs, chars, h, out, pal=None, base=0x14, scale=4):
    from PIL import Image, ImageDraw
    inv = {}
    for c, g in chars.items():
        inv.setdefault(g, c)
    cols = 14
    cw, ch = 14, h + 14
    rows = (len(glyphs) + cols - 1) // cols
    im = Image.new("RGB", (cols * cw, rows * ch), (40, 0, 40))
    dr = ImageDraw.Draw(im)
    for g, (w, px) in sorted(glyphs.items()):
        x0, y0 = (g % cols) * cw + 2, (g // cols) * ch + 12
        dr.text((x0, y0 - 11), inv.get(g, "?"), fill=(255, 255, 0))
        for y in range(h):
            for x in range(w):
                v = px[y * w + x]
                if v:
                    if pal:
                        i = (base + v) & 0xFF
                        im.putpixel((x0 + x, y0 + y), tuple(pal[3 * i:3 * i + 3]))
                    else:
                        im.putpixel((x0 + x, y0 + y), [(0, 0, 0), (255, 255, 255), (110, 110, 110)][min(v, 2)])
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    im.save(out)


if __name__ == "__main__":
    d = open(PATH, "rb").read()
    glyphs, chars, h, rep = parse_fnt(d)
    print(rep)
    print("map:", "".join(sorted(chars)))
    if "--png" in sys.argv:
        sheet(glyphs, chars, h, os.path.join(OUT, "fonts_IGNITION_FNT__values.png"))
        pal = colmod.palette_for(colmod.level_col("USA"))
        sheet(glyphs, chars, h, os.path.join(OUT, "fonts_IGNITION_FNT__levelcol_base0x14.png"), pal=pal)
