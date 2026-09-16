#!/usr/bin/env python3
"""
export_tracks.py -- export every level mesh (and the car banks) to Wavefront OBJ, and render
top-down wireframe PNGs.

Output: _re/out/tracks/<Level>.obj + .mtl, Cars.obj, Menucar.obj, <Level>_topdown.png

Coordinate conversion (see 04_track_formats.md §Axes):
    game space is left-handed, +y DOWN (x right, z "down the map" when viewed from above).
    OBJ (right-handed, +Y up)  =  (x, -y, z).  This single negation also turns the game's
    face winding into OBJ's counter-clockwise-front convention, so indices are kept in order.
Top-down PNG: image column = +x, image row = +z (matches the in-game track-selection maps).

OBJ groups: one 'o' per PLC object, named obj<index>_t<type>_g<group>_b<b6>_<b7>[_dyn]
(_dyn = type >= 100, i.e. not part of the static world / SRF).
Materials: page<NN> (texture page in <Level>.TEX); vt = (u/65536, 1 - v/65536) of that page.

usage: <venv python with pillow> _re/tools/formats/export_tracks.py [--no-png]
"""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ignition_formats as F

GAME = '/mnt/c/Games/IGNITION'
OUT = os.path.join(GAME, '_re', 'out', 'tracks')


def find(d, ext):
    for x in os.listdir(d):
        if x.upper().endswith(ext) and os.path.isfile(os.path.join(d, x)):
            return os.path.join(d, x)


def export_obj(name, plc, msh, tex_name):
    objp = os.path.join(OUT, name + '.obj')
    pages = sorted({f['page'] for m in msh['meshes'] for f in m['faces']})
    with open(os.path.join(OUT, name + '.mtl'), 'w') as mt:
        for p in pages:
            mt.write(f"newmtl page{p:02d}\n# texture page {p} of {tex_name} (256x256, 8-bit, palette from .COL)\n"
                     f"Kd 1 1 1\n\n")
    nv_tot = nt_tot = nf_tot = 0
    with open(objp, 'w') as fh:
        fh.write(f"# Ignition (UDS 1997) {name} -- exported by _re/tools/formats/export_tracks.py\n"
                 f"# OBJ = (x, -y, z) of game units; objects placed by PLC translation (no rotation)\n"
                 f"mtllib {name}.mtl\n")
        for r in plc['recs']:
            m = msh['by_word'][r['msh_off']]
            dyn = '_special' if r['type'] >= 100 else ''
            fh.write(f"o obj{r['index']:03d}_t{r['type']}_g{r['group']}_b{r['b6']}_{r['b7']}{dyn}\n")
            for vx, vy, vz in m['verts']:
                fh.write(f"v {vx + r['x']} {-(vy + r['y'])} {vz + r['z']}\n")
            for f in m['faces']:
                for u, v in f['uv']:
                    fh.write(f"vt {u / 65536:.6f} {1 - v / 65536:.6f}\n")
            cur = None
            for j, f in enumerate(m['faces']):
                if f['page'] != cur:
                    cur = f['page']; fh.write(f"usemtl page{cur:02d}\n")
                a, b, c = (nv_tot + 1 + i for i in f['idx'])
                t = nt_tot + 1 + 3 * j
                fh.write(f"f {a}/{t} {b}/{t + 1} {c}/{t + 2}\n")
            nv_tot += m['nv']; nt_tot += 3 * m['nf']; nf_tot += m['nf']
    return objp, nv_tot, nf_tot


def render_png(name, plc, msh, pos, size=1600):
    from PIL import Image, ImageDraw
    tris = []
    for r in plc['recs']:
        m = msh['by_word'][r['msh_off']]
        for f in m['faces']:
            s = f['surface']
            drive = r['type'] < 100 and s < 40 and s % 10 not in (5, 6, 7)
            tris.append(([(m['verts'][i][0] + r['x'], m['verts'][i][2] + r['z']) for i in f['idx']],
                         drive, r['type'] >= 100))
    xs = [p[0] for t, _, _ in tris for p in t]; zs = [p[1] for t, _, _ in tris for p in t]
    mnx, mxx, mnz, mxz = min(xs), max(xs), min(zs), max(zs)
    sc = (size - 40) / max(mxx - mnx, mxz - mnz)
    W = int((mxx - mnx) * sc) + 40; H = int((mxz - mnz) * sc) + 60
    im = Image.new('RGB', (W, H), 'white'); dr = ImageDraw.Draw(im)
    P = lambda x, z: ((x - mnx) * sc + 20, (z - mnz) * sc + 40)
    for t, drive, dyn in tris:                      # drivable-surface fill first
        if drive:
            dr.polygon([P(*q) for q in t], fill=(255, 190, 190))
    for t, drive, dyn in tris:                      # full wireframe
        pts = [P(*q) for q in t]
        dr.line(pts + [pts[0]], fill=(0, 110, 255) if dyn else ((200, 0, 0) if drive else (90, 90, 90)), width=1)
    if pos:
        for b in pos['blocks']:
            dr.line([P(p[0], p[2]) for p in b['points']], fill=(0, 170, 0), width=2)
    dr.text((20, 8), f"{name}: top-down (+x right, +z down; view from above). grey=static mesh, "
                     f"red=drivable (surface<40), blue=special PLC type>=100 (not in SRF), green=POS paths", fill='black')
    out = os.path.join(OUT, name + '_topdown.png')
    im.save(out)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    png = '--no-png' not in sys.argv
    for d in sorted(glob.glob(GAME + '/LEVELS/*')):
        if not os.path.isdir(d):
            continue
        lv = os.path.basename(d)
        plc, _ = F.parse_plc(open(find(d, '.PLC'), 'rb').read())
        msh, _ = F.parse_msh(open(find(d, '.MSH'), 'rb').read())
        pos, _ = F.parse_pos(open(find(d, '.POS'), 'rb').read(), plc['count'])
        p, nv, nf = export_obj(lv, plc, msh, os.path.basename(find(d, '.TEX')))
        msg = f"{lv}: {p} verts={nv} faces={nf}"
        if png:
            msg += ' png=' + render_png(lv, plc, msh, pos)
        print(msg)
    for name, pf, mf, tex in (('Cars', 'CARS/Cars.plc', 'CARS/Cars.msh', 'Cars.tex'),
                              ('Menucar', 'baltazar/data/Menucar.plc', 'baltazar/data/Menucar.msh', 'Menucar.tex')):
        plc, _ = F.parse_plc(open(os.path.join(GAME, pf), 'rb').read())
        msh, _ = F.parse_msh(open(os.path.join(GAME, mf), 'rb').read())
        p, nv, nf = export_obj(name, plc, msh, tex)
        print(f"{name}: {p} verts={nv} faces={nf}")


if __name__ == '__main__':
    main()
