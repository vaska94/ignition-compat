#!/usr/bin/env python3
"""
cdp.py -- reader/decoder for Ignition's CDP full-screen intro animations
(Baltazar/data/Ign1.cdp, Ign2.cdp, Ign3_0..3.cdp).

Spec recovered from Ign_win.exe:
  open    0x00412580  (checks "CDP\\0", version word == 100, sets pointers)
  decode  0x00412610 -> 0x00499ABC  (hand-written byte-code interpreter living in .data,
                                     256-entry jump table at 0x00499B95)
  palette 0x00412560  (SetGamePalette(file+0x10), called when frameIndex == 2)
  player  0x00402E7E..0x00402EDF (plays the 6 files in table order 0x0047DC50)

Header (16 bytes, little endian):
  +0x00 char[4] "CDP\\0"      magic                         (checked)
  +0x04 u16     100           version                       (checked == 100)
  +0x06 u16     frame count   -> player+0x08
  +0x08 u16     loop flag     -> player+0x0A (1 = loop forever, else stop at end)
  +0x0A u16     width  (320)  -> player+0x0C  (never read by decoder)
  +0x0C u16     height (200)  -> player+0x0E  (never read by decoder)
  +0x0E u16     256           colour count? -- NOT copied, never read
  +0x10 u8[768] RGB palette, 0..255, passed verbatim to SetGamePalette
  +0x310 frame stream: each frame is a byte-code program terminated by 0xFF

Byte codes (dest = linear 320x200 8-bit buffer that persists between frames):
  00..F5  literal pixel value = opcode
  F6 b    literal pixel b (escape for values F6..FF)
  F7/F8/F9/FA   skip 2/3/4/5 pixels
  FB n8   skip n8 pixels (0 = 0)
  FC n16  skip n16 pixels
  FD c8 v8  run: c8 copies of v8 (c8 == 0 means 256; `dec dl; jne`)
  FE n16 v8 run: n16 copies of v8 (n16 == 0 means 65536; `dec dx; jne`)
  FF      end of frame
"""
import struct, sys, os

MAGIC = b"CDP\0"


class CDPError(Exception):
    pass


def parse_header(d):
    if len(d) < 0x310:
        raise CDPError("file shorter than header+palette")
    if d[:4] != MAGIC:
        raise CDPError("bad magic %r" % d[:4])
    ver, frames, loop, w, h, ncol = struct.unpack_from("<6H", d, 4)
    if ver != 100:
        raise CDPError("version %d != 100" % ver)
    pal = d[0x10:0x310]
    return dict(version=ver, frames=frames, loop=loop, width=w, height=h,
                ncolours=ncol, palette=pal)


def decode_frame(d, pos, buf, stats=None):
    """Run one frame program starting at d[pos] into bytearray buf.
    Returns new pos (just past the 0xFF). Mirrors 0x00499ABC exactly, but
    bounds-checks the destination (the original does not)."""
    o = 0
    n = len(buf)
    while True:
        op = d[pos]; pos += 1
        if op <= 0xF5:
            buf[o] = op; o += 1
        elif op == 0xF6:
            buf[o] = d[pos]; pos += 1; o += 1
        elif op <= 0xFA:
            o += op - 0xF5          # F7->2, F8->3, F9->4, FA->5
        elif op == 0xFB:
            o += d[pos]; pos += 1
        elif op == 0xFC:
            o += struct.unpack_from("<H", d, pos)[0]; pos += 2
        elif op == 0xFD:
            c, v = d[pos], d[pos + 1]; pos += 2
            c = c or 256
            buf[o:o + c] = bytes([v]) * c; o += c
        elif op == 0xFE:
            c = struct.unpack_from("<H", d, pos)[0]; v = d[pos + 2]; pos += 3
            c = c or 65536
            buf[o:o + c] = bytes([v]) * c; o += c
        else:  # 0xFF
            if stats is not None:
                stats["max_out"] = max(stats.get("max_out", 0), o)
            return pos
        if stats is not None:
            stats[op] = stats.get(op, 0) + 1
        if o > n:
            raise CDPError("frame writes past %d bytes (o=%d)" % (n, o))


def read(path):
    d = open(path, "rb").read()
    hdr = parse_header(d)
    size = hdr["width"] * hdr["height"]
    buf = bytearray(size)
    pos = 0x310
    frames = []
    stats = {}
    for i in range(hdr["frames"]):
        if pos >= len(d):
            raise CDPError("ran out of data at frame %d" % i)
        start = pos
        pos = decode_frame(d, pos, buf, stats)
        frames.append((start, pos - start, bytes(buf)))
    return hdr, frames, pos, len(d) - pos, stats


def palette_rgb(pal768):
    return list(pal768)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--png", help="output dir for PNG frames")
    ap.add_argument("--every", type=int, default=1)
    a = ap.parse_args()
    for f in a.files:
        hdr, frames, end, rem, stats = read(f)
        print(f"{f}: ver={hdr['version']} frames={hdr['frames']} loop={hdr['loop']} "
              f"{hdr['width']}x{hdr['height']} ncol={hdr['ncolours']} "
              f"stream=0x310..0x{end:x} file={end+rem} unaccounted={rem} "
              f"max_out={stats.get('max_out')}")
        if a.png:
            from PIL import Image
            name = os.path.splitext(os.path.basename(f))[0]
            od = os.path.join(a.png, name)
            os.makedirs(od, exist_ok=True)
            pal = palette_rgb(hdr["palette"])
            for i, (st, ln, px) in enumerate(frames):
                if i % a.every and i != len(frames) - 1:
                    continue
                im = Image.frombytes("P", (hdr["width"], hdr["height"]), px)
                im.putpalette(pal)
                im.save(os.path.join(od, f"{i:03d}.png"))


if __name__ == "__main__":
    main()
