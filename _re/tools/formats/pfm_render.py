#!/usr/bin/env python3
"""
pfm_render.py -- verification renderer for test.pfm (NOT the game's rasterizer).

Reproduces the menu background setup of 0x0040CA60:
  * texture pages: the 8 files of the table at 0x004921A8 (stride 50) loaded back-to-back
    in 64 KiB (256x256) pages, each file truncated to 0x100000 bytes (0x0040CD60):
      canada.tex -> pages 0..15, cars.tex -> 16..22, divspr 23, light 24, smoke 25,
      darksmok 26, boom 27, exsmoke 28
  * table list passed to the script player (0x0040CE00):
      table[0] = first 64 KiB of levels\\canada\\canada.SHD
      table[1] = generated: row 0 identity, row r (r>=1) filled with r
  * palette: baltazar\\data\\menu.col (8-byte header + 768 RGB)
Coordinates: li01 vertices are 1/16-pixel screen positions for a 320x200 target (INFERRED from
value ranges); li02/li03 UVs are 8.8 fixed texels on a 256x256 page (li03 stores integers, the
loader shifts <<8).
Blend hypothesis (INFERRED): table lookups are out = table[src*256 + dst]; with table[1] this is
colour-key transparency on texel 0, with canada.SHD it is shading/translucency.
"""
import sys, os
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__))
import pfm

G = "/mnt/c/Games/IGNITION/"
TEX = ["CANADA", "CARS", "DIVSPR", "LIGHT", "SMOKE", "DARKSMOK", "BOOM", "EXSMOKE"]


def load_pages():
    pages = []
    for n in TEX:
        b = open(G + "Baltazar/data/textures/%s.tex" % n.lower(), "rb").read()[:0x100000]
        npg = (len(b) + 0xFFFF) >> 16
        b = b + bytes(npg * 65536 - len(b))
        for i in range(npg):
            pages.append(np.frombuffer(b[i * 65536:(i + 1) * 65536], np.uint8).reshape(256, 256))
    return pages


def load_tables():
    shd = np.frombuffer(open(G + "LEVELS/Canada/CANADA.SHD", "rb").read()[:65536], np.uint8).reshape(256, 256)
    t1 = np.zeros((256, 256), np.uint8)
    t1[0] = np.arange(256)
    for r in range(1, 256):
        t1[r] = r
    return [shd, t1]


def tri(fb, P, cb):
    """Rasterize triangle P (3x2 float pixel coords); cb(mask_ys, mask_xs, w0,w1,w2)."""
    H, W = fb.shape
    x0, y0 = np.floor(P.min(0)).astype(int)
    x1, y1 = np.ceil(P.max(0)).astype(int)
    x0 = max(x0, 0); y0 = max(y0, 0); x1 = min(x1, W - 1); y1 = min(y1, H - 1)
    if x1 < x0 or y1 < y0:
        return
    ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    px = xs + 0.5; py = ys + 0.5
    (ax, ay), (bx, by), (cx, cy) = P
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if abs(area) < 1e-9:
        return
    w0 = ((bx - px) * (cy - py) - (by - py) * (cx - px)) / area
    w1 = ((cx - px) * (ay - py) - (cy - py) * (ax - px)) / area
    w2 = 1 - w0 - w1
    m = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    if m.any():
        cb(ys[m], xs[m], w0[m], w1[m], w2[m])


def render(r, fi, pages, tables):
    fb = np.zeros((200, 320), np.uint8)
    f = r["frames"][fi]
    v = np.array(f["verts"], np.float64).reshape(-1, 2) / 16.0
    li02 = r["li02"]; li03 = r["li03"]
    for t, recs in f["cmds"]:
        for rec in recs:
            if t in (15, 19):
                col = rec[3]; tb = tables[rec[4]] if t == 19 else None
                def cb(ys, xs, *_):
                    if tb is None:
                        fb[ys, xs] = col
                    else:
                        fb[ys, xs] = tb[col, fb[ys, xs]]
                tri(fb, v[list(rec[:3])], cb)
            elif t in (17, 18):
                e = li02[rec[3]]; pg = pages[e[6] if 0 <= e[6] <= 30 else 0]
                uv = np.array(e[:6], np.float64).reshape(3, 2) / 256.0
                tb = tables[rec[4]] if t == 18 else None
                def cb(ys, xs, w0, w1, w2, uv=uv, pg=pg, tb=tb):
                    u = (w0 * uv[0, 0] + w1 * uv[1, 0] + w2 * uv[2, 0]).astype(int) & 255
                    vv = (w0 * uv[0, 1] + w1 * uv[1, 1] + w2 * uv[2, 1]).astype(int) & 255
                    s = pg[vv, u]
                    fb[ys, xs] = s if tb is None else tb[s, fb[ys, xs]]
                tri(fb, v[list(rec[:3])], cb)
            elif t == 7:
                a, b, u0, v0, u1, v1, pgi, ti = li03[rec[0]]
                pg = pages[pgi]; tb = tables[ti]
                cx, cy = v[rec[3]]
                # size hypothesis: half-extent = a * p / 256 pixels (INFERRED)
                hw = a * rec[1] / 256.0; hh = b * rec[2] / 256.0
                X0 = int(cx - hw); X1 = int(cx + hw); Y0 = int(cy - hh); Y1 = int(cy + hh)
                if X1 <= X0 or Y1 <= Y0:
                    continue
                xs = np.arange(max(X0, 0), min(X1, 320)); ys = np.arange(max(Y0, 0), min(Y1, 200))
                if not len(xs) or not len(ys):
                    continue
                uu = (u0 + (xs - X0) * (u1 - u0) / (X1 - X0)).astype(int) & 255
                vv = (v0 + (ys - Y0) * (v1 - v0) / (Y1 - Y0)).astype(int) & 255
                s = pg[np.ix_(vv, uu)]
                sub = fb[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]
                fb[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1] = tb[s, sub]
    return fb


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else G + "_re/out/images/pfm"
    idx = [int(x) for x in sys.argv[2:]] or [0, 100, 200, 300, 450, 600, 750, 900, 1050, 1123]
    os.makedirs(out, exist_ok=True)
    r = pfm.read(G + "Baltazar/data/test.pfm")
    pages = load_pages(); tables = load_tables()
    pal = list(open(G + "Baltazar/data/Menu.col", "rb").read()[8:8 + 768])
    for fi in idx:
        fb = render(r, fi, pages, tables)
        im = Image.frombytes("P", (320, 200), fb.tobytes()); im.putpalette(pal)
        im.resize((640, 400), Image.NEAREST).save(os.path.join(out, "frame_%04d.png" % fi))
        print("frame", fi, "written")


if __name__ == "__main__":
    main()
