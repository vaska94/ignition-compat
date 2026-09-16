#!/usr/bin/env python3
"""menubkg.py -- menubkg.dat reader / renderer.

Loader 0x00401000 (called once from menu init 0x004180E2, i.e. while SYS.COL
is the active palette): fopen("MenuBkg.dat","rb"), calloc(0x9358,1),
fread(buf,0x9358,1) -> [0x004BCD50]. No header; fixed size 37720 bytes.

Consumer 0x004011C0 / blitters 0x004016D0 (row fill) and 0x00401730
(transparent tile blit, 0 = skip, optional colour remap via 0x004BCC48):
  +0x0000 .. +0x933F  37696 bytes = 124 cells of 16x19 = 304 bytes each
                      (tile offsets used by the code are all multiples of
                      0x130: 0x130, 0x260, 0x390, 0x4C0+0x260*n, 0x23A0-0x260*n,
                      0x44E0, 0x6880, 0x8E80, 0x8FB0, 0x90E0, 0x9210);
                      blits are 0x20 (32) wide x 0x13 (19) tall, i.e. two
                      adjacent cells; the blitter starts at src+(h-1)*w and
                      walks rows with `sub eax,ecx`, so rows are stored
                      bottom-up relative to the screen.
  +0x9340 .. +0x9357  24 bytes: palette index for each of 24 background
                      scanline bands (0x004011F2: mov al,[eax+edi+0x9340], edi<0x18)

Usage: menubkg.py [--png]
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import col as colmod

GAME = colmod.GAME
PATH = os.path.join(GAME, "menubkg.dat")
OUT = os.path.join(GAME, "_re/out/images/menubkg")
CELL_W, CELL_H = 16, 19


def parse(d):
    tiles = d[:0x9340]
    bands = d[0x9340:0x9358]
    rep = dict(file_len=len(d), tile_bytes=len(tiles), cells=len(tiles) // (CELL_W * CELL_H),
               cell_remainder=len(tiles) % (CELL_W * CELL_H), band_bytes=list(bands),
               remainder=len(d) - 0x9358)
    rep["ok"] = len(d) == 0x9358 and rep["cell_remainder"] == 0
    return tiles, bands, rep


if __name__ == "__main__":
    d = open(PATH, "rb").read()
    tiles, bands, rep = parse(d)
    print(rep)
    if "--png" in sys.argv:
        from PIL import Image
        pal = colmod.palette_for(os.path.join(GAME, "sys.col"))
        os.makedirs(OUT, exist_ok=True)
        rows = len(tiles) // CELL_W
        # raw strip, 16 wide (file order), and flipped so each cell reads screen-up
        im = Image.frombytes("P", (CELL_W, rows), bytes(tiles)); im.putpalette(pal)
        im.resize((CELL_W * 4, rows * 1), Image.NEAREST)
        im.save(os.path.join(OUT, "menubkg_strip16_fileorder.png"))
        # pairs of cells as 32x19 tiles, rows flipped vertically (as the blitter draws them)
        cells = [tiles[i:i + 304] for i in range(0, len(tiles), 304)]
        n = len(cells) // 2
        per_row = 16
        sheet = Image.new("P", (per_row * 34, ((n + per_row - 1) // per_row) * 21), 0); sheet.putpalette(pal)
        for k in range(n):
            a, b = cells[2 * k], cells[2 * k + 1]
            t = Image.frombytes("P", (32, 19), bytes(a + b))  # contiguous 608 bytes as 32 wide
            t = t.transpose(Image.FLIP_TOP_BOTTOM)
            sheet.paste(t, ((k % per_row) * 34, (k // per_row) * 21))
        sheet = sheet.resize((sheet.width * 3, sheet.height * 3), Image.NEAREST)
        sheet.save(os.path.join(OUT, "menubkg_tiles32x19_flipped_x3.png"))
        bandim = Image.new("P", (64, 24)); bandim.putpalette(pal)
        for y, c in enumerate(bands):
            for x in range(64):
                bandim.putpixel((x, y), c)
        bandim.resize((256, 96), Image.NEAREST).save(os.path.join(OUT, "menubkg_bands.png"))
