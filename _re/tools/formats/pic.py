#!/usr/bin/env python3
"""pic.py -- Ignition .PIC reader / PNG converter.

Format = Autodesk Animator Pro PIC (standard, uncompressed):

  64-byte file header
    +0x00 u32  file size
    +0x04 u16  magic 0x9500
    +0x06 u16  width
    +0x08 u16  height
    +0x0A i16  x origin  (Animator screen position; not used by the game)
    +0x0C i16  y origin  (not used by the game)
    +0x0E u32  user id   (0)
    +0x12 u8   bits per pixel (8)
    +0x13 u8[45] reserved (0)
  chunks, each: u32 size (incl. 6-byte chunk header), u16 type
    type 0  colour map : u16 version(0) + 768 RGB   (size 0x308)
    type 1  byte pixels: width*height bytes, row-major, top row first
                         (size width*height+6)

In every shipped file the layout is fixed: header 0x40, colour chunk at 0x40,
pixel chunk header at 0x348, first pixel at 0x34E. The EXE never parses the
chunks: it loads the whole file and indexes pixels at +0x34E directly
(0x00418671 level PIC, 0x004041E6/0x0041EED1 N_SYSGFX sprites) or reads
w*h bytes at file offset 0x34E with hard-coded w*h (0x00403FCF.. frontend).
The embedded colour map is ignored by the game.

Usage: pic.py [--png] [files...]
"""
import struct, sys, os, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import col as colmod

GAME = colmod.GAME
OUT = os.path.join(GAME, "_re/out/images/pic")


def parse_pic(d):
    rep = {"file_len": len(d)}
    if len(d) < 0x40:
        raise ValueError("short PIC")
    size, magic, w, h, x, y, uid, depth = struct.unpack_from("<IHHHhhIB", d, 0)
    reserved = d[0x13:0x40]
    rep.update(size_field=size, magic=hex(magic), w=w, h=h, x=x, y=y, user_id=uid,
               depth=depth, reserved_zero=(reserved.count(0) == len(reserved)))
    pos = 0x40
    chunks = []
    pal = None
    pixels = None
    while pos + 6 <= len(d):
        csize, ctype = struct.unpack_from("<IH", d, pos)
        if csize < 6 or pos + csize > len(d):
            break
        body = d[pos + 6:pos + csize]
        if ctype == 0:
            ver = struct.unpack_from("<H", body, 0)[0]
            pal = body[2:2 + 768]
            chunks.append(("colormap", pos, csize, ver))
        elif ctype == 1:
            pixels = body
            chunks.append(("pixels", pos, csize, None))
        else:
            chunks.append((f"unknown{ctype}", pos, csize, None))
        pos += csize
    rep["chunks"] = chunks
    rep["remainder"] = len(d) - pos
    rep["ok"] = (magic == 0x9500 and size == len(d) and depth == 8 and rep["reserved_zero"]
                 and pixels is not None and len(pixels) == w * h and rep["remainder"] == 0
                 and pal is not None and len(pal) == 768)
    rep["pixel_offset"] = next((c[1] + 6 for c in chunks if c[0] == "pixels"), None)
    return w, h, pal, pixels, rep


def all_pics():
    fs = glob.glob(os.path.join(GAME, "*.pic")) + glob.glob(os.path.join(GAME, "Baltazar/data/*.[pP][iI][cC]"))
    fs += glob.glob(os.path.join(GAME, "LEVELS/*/*.PIC"))
    return sorted(set(fs))


def game_palette(path):
    """Palette the game has active when this PIC is on screen (see notes 05 §PIC)."""
    rel = os.path.relpath(path, GAME).replace("\\", "/")
    base = os.path.basename(rel).lower()
    if rel.startswith("LEVELS/"):
        return colmod.level_col(rel.split("/")[1]), "level COL (0x00418650 blits it after SetGamePalette(level COL) 0x00441360)"
    if rel.startswith("Baltazar/"):
        return os.path.join(GAME, "Baltazar/data/Menu.col"), "menu.col (frontend, SetGamePalette @0x00402A1A)"
    if base in ("n_sysgfx.pic", "n_sysg_2.pic"):
        return os.path.join(GAME, "sys.col"), "SYS.COL (loaded together @0x00418130, SetGamePalette @0x0041811A)"
    # in-race HUD sheets: drawn while the level palette is active
    return colmod.level_col("USA"), "a level COL (HUD sheet; indices 160-228/230-255 are identical in every COL)"


def to_png(w, h, pixels, pal, out):
    from PIL import Image
    im = Image.frombytes("P", (w, h), bytes(pixels))
    im.putpalette(list(pal))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    im.save(out)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    png = "--png" in sys.argv
    files = args or all_pics()
    bad = 0
    for f in files:
        d = open(f, "rb").read()
        w, h, pal, px, rep = parse_pic(d)
        bad += not rep["ok"]
        rel = os.path.relpath(f, GAME)
        print(f"{rel:32s} {w}x{h} org=({rep['x']},{rep['y']}) chunks={[(c[0], hex(c[1]), c[2]) for c in rep['chunks']]} rem={rep['remainder']} ok={rep['ok']}")
        if png:
            stem = rel.replace("/", "_").rsplit(".", 1)[0]
            cp, why = game_palette(f)
            to_png(w, h, px, colmod.palette_for(cp), os.path.join(OUT, stem + ".png"))
            to_png(w, h, px, pal, os.path.join(OUT, "embedded_palette", stem + "__embedded.png"))
    print(f"{len(files)} PIC files, {len(files)-bad} fully accounted, {bad} anomalies")
