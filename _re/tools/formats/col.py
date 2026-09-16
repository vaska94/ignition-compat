#!/usr/bin/env python3
r"""col.py -- Ignition palette (.COL) reader.

Format = Autodesk Animator Pro COL file (standard):
  +0x000 u32  file size            (always 0x308 = 776)
  +0x004 u16  magic                (0xB123)
  +0x006 u16  version              (0)
  +0x008 u8[768] RGB triplets, 8 bits per component (0..255), index 0..255

The game only ever uses bytes 8..775:
  * SYS.COL        -> whole-file load 0x004574A0, SetGamePalette(buf+8) @0x0041811A
  * LEVELS\*.COL   -> whole-file load @0x00418DD0, buf in [0x0054F900],
                      SetGamePalette([0x0054F900]+8) @0x00441369 / 0x0043490D
  * menu.col       -> LoadFileAt(name, buf, 0x300, 8) @0x004029D6 / 0x00403F67

Usage: col.py [files...]   (default: every COL in the game) -> byte accounting report.
"""
import struct, sys, os, glob

GAME = "/mnt/c/Games/IGNITION"
COL_MAGIC = 0xB123


def parse_col_bytes(d):
    """Return (rgb768 bytes, report dict). Raises ValueError on malformed data."""
    if len(d) < 8:
        raise ValueError("short COL")
    size, magic, ver = struct.unpack_from("<IHH", d, 0)
    rgb = d[8:8 + 768]
    rep = dict(size_field=size, magic=hex(magic), version=ver, file_len=len(d),
               accounted=8 + len(rgb), remainder=len(d) - 8 - len(rgb))
    if magic != COL_MAGIC or size != len(d) or len(rgb) != 768:
        rep["ok"] = False
    else:
        rep["ok"] = rep["remainder"] == 0
    return rgb, rep


def read_col(path):
    return parse_col_bytes(open(path, "rb").read())


def palette_for(path):
    """RGB768 list suitable for PIL putpalette."""
    rgb, rep = read_col(path)
    if not rep["ok"]:
        raise ValueError(f"{path}: {rep}")
    return list(rgb)


def all_cols():
    fs = [os.path.join(GAME, "sys.col"), os.path.join(GAME, "Baltazar/data/Menu.col")]
    fs += sorted(glob.glob(os.path.join(GAME, "LEVELS/*/*.[Cc][Oo][Ll]")))
    return fs


# Convenience lookup: palette file per level directory name
def level_col(level):
    c = glob.glob(os.path.join(GAME, "LEVELS", level, "*.[Cc][Oo][Ll]"))
    if len(c) != 1:
        raise FileNotFoundError(level)
    return c[0]


if __name__ == "__main__":
    files = sys.argv[1:] or all_cols()
    bad = 0
    for f in files:
        _, rep = read_col(f)
        bad += not rep["ok"]
        print(f"{os.path.relpath(f, GAME):34s} {rep}")
    print(f"{len(files)} COL files, {len(files)-bad} fully accounted (8 hdr + 768 RGB), {bad} anomalies")
