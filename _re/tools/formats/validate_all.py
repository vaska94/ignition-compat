#!/usr/bin/env python3
"""
validate_all.py -- run every track-format parser over every level + car file, account for every
byte, and cross-validate between formats. Prints PASS/FAIL per file and writes the same report
to _re/out/tracks/validation.txt.

usage (from anywhere):  python3 _re/tools/formats/validate_all.py
"""
import os, sys, glob, io, struct
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ignition_formats as F

GAME = '/mnt/c/Games/IGNITION'
OUT = os.path.join(GAME, '_re', 'out', 'tracks')
buf = io.StringIO()
fails = []


def log(*a):
    s = ' '.join(str(x) for x in a)
    print(s); buf.write(s + '\n')


def find(d, ext):
    for x in os.listdir(d):
        if x.upper().endswith(ext) and os.path.isfile(os.path.join(d, x)):
            return os.path.join(d, x)


def verdict(path, acct, extra_ok=True, note=''):
    gaps, over, beyond = acct.unaccounted()
    ok = acct.ok() and extra_ok
    g = sum(e - s for s, e in gaps)
    msg = f"{'PASS' if ok else 'FAIL'}  {os.path.relpath(path, GAME):32s} {acct.size:8d} B  unaccounted={g} overlap={over} past_eof={len(beyond)}"
    if note:
        msg += '  | ' + note
    log(msg)
    if not ok:
        fails.append(path)
    return ok


def check_mesh_bank(plc_path, msh_path, tex_path=None):
    plc, ap = F.parse_plc(open(plc_path, 'rb').read())
    msh, am = F.parse_msh(open(msh_path, 'rb').read())
    offs = [r['msh_off'] for r in plc['recs']]
    ok_plc = (len(plc['recs']) == plc['count'] and all(o in msh['by_word'] for o in offs)
              and len(set(offs)) == len(offs) and len(offs) == len(msh['meshes']))
    verdict(plc_path, ap, ok_plc,
            f"n={plc['count']} all msh_off hit a mesh start={all(o in msh['by_word'] for o in offs)} "
            f"unique={len(set(offs)) == len(offs)} meshes={len(msh['meshes'])} types<100={sum(1 for r in plc['recs'] if r['type'] < 100)}")
    bad_idx = bad_tail = 0
    pages = Counter(); modes = Counter()
    nf = 0
    for m in msh['meshes']:
        for f in m['faces']:
            nf += 1
            if not all(0 <= i < m['nv'] for i in f['idx']):
                bad_idx += 1
            if f['tail_lo'] != 0:
                bad_tail += 1
            pages[f['page']] += 1; modes[f['mode']] += 1
    npages = None
    if tex_path:
        npages = os.path.getsize(tex_path) // 65536
    page_ok = npages is None or max(pages) < max(npages, 17)
    verdict(msh_path, am, bad_idx == 0 and bad_tail == 0,
            f"meshes={len(msh['meshes'])} faces={nf} bad_vertex_idx={bad_idx} tail!=0={bad_tail} "
            f"pages 0..{max(pages)} (TEX holds {npages} full 256x256 pages) modes={dict(sorted(modes.items()))}")
    return plc, msh


def main():
    os.makedirs(OUT, exist_ok=True)
    consts = Counter()
    for d in sorted(glob.glob(GAME + '/LEVELS/*')):
        if not os.path.isdir(d):
            continue
        lv = os.path.basename(d)
        log(f"\n=== {lv} ===")
        plc, msh = check_mesh_bank(find(d, '.PLC'), find(d, '.MSH'), find(d, '.TEX'))
        recs = plc['recs']

        # ---- TRI
        p = find(d, '.TRI')
        tri, at = F.parse_tri(open(p, 'rb').read())
        vbad = 0; xz_ok = 0; nonempty = 0; stale = 0
        for r in tri['recs']:
            if r['empty']:
                continue
            nonempty += 1
            pr = recs[r['index']] if r['index'] < len(recs) else None
            if pr is None:
                vbad += 1; continue
            m = msh['by_word'][pr['msh_off']]
            if not (0 <= r['vtx_a'] < m['nv'] and 0 <= r['vtx_b'] < m['nv']):
                vbad += 1; continue
            va = m['verts'][r['vtx_a']]
            if va[0] + pr['x'] + F.BIAS == r['a'][0] and va[2] + pr['z'] + F.BIAS == r['a'][2]:
                xz_ok += 1
            else:
                stale += 1
        k_ok = tri['count'] == plc['count']
        verdict(p, at, True,
                f"header={tri['header']} records={tri['count']} (PLC {plc['count']}{'' if k_ok else ' MISMATCH: game reads 4+500*nPLC bytes, file short'}) "
                f"non-empty={nonempty} vtx-index out of range={vbad} stored xz==mesh+plc+bias: {xz_ok} (stale {stale}) "
                f"mode@0x6D={dict(Counter(r['mode'] for r in tri['recs']).most_common(4))} recs with non-zero pad={sum(1 for r in tri['recs'] if r['pad_nonzero'])}")

        # ---- SRF
        p = find(d, '.SRF')
        srf, asr = F.parse_srf(open(p, 'rb').read())
        h = srf['header']; consts[h[:6]] += 1
        nS = len(srf['spans'])
        offs_ok = all(o % 24 == 0 and 0 <= o < 24 * nS for o in srf['listB'] + srf['listC'])
        cell_bad = 0; empty = 0; sumB = sumC = 0
        for offC, offB, nB, nC in srf['cells']:
            if nB == 0 and nC == 0:
                empty += 1; continue
            sumB += nB; sumC += nC
            if nB and (offB % 4 or offB // 4 + nB > len(srf['listB'])):
                cell_bad += 1
            if nC and (offC % 4 or offC // 4 + nC > len(srf['listC'])):
                cell_bad += 1
        dz_B = Counter('pos' if srf['spans'][o // 24][4] > 0 else 'nonpos' for o in srf['listB'])
        dz_C = Counter('neg' if srf['spans'][o // 24][4] < 0 else 'nonneg' for o in srf['listC'])
        # spans -> runtime world objects: index = obj_off/42, j-th PLC object with type<100
        static = [r for r in recs if r['type'] < 100]
        span_ok = span_bad = 0
        for x0, z0, sa, sb, dz, fw, oo in srf['spans']:
            j = oo // 42
            if oo % 42 or j >= len(static):
                span_bad += 1; continue
            pr = static[j]; m = msh['by_word'][pr['msh_off']]
            face = next((f for f in m['faces'] if f['word_off'] == fw), None)
            if face is None:
                span_bad += 1; continue
            vx = [m['verts'][i][0] + pr['x'] + F.BIAS for i in face['idx']]
            vz = [m['verts'][i][2] + pr['z'] + F.BIAS for i in face['idx']]
            if any(x0 == vx[k] and z0 == vz[k] for k in range(3)):
                span_ok += 1
            else:
                span_bad += 1
        grid_ok = (h[4] == h[1] * 256 // h[2] + 1) and (h[5] == h[0] * 256 // h[3] + 1)
        verdict(p, asr, offs_ok and cell_bad == 0 and grid_ok,
                f"hdr={h[:6]} spans={nS} listB={len(srf['listB'])} (sum nB {sumB}) listC={len(srf['listC'])} (sum nC {sumC}) "
                f"empty cells={empty}/{len(srf['cells'])} bad cells={cell_bad} list offsets valid={offs_ok} "
                f"dz sign B={dict(dz_B)} C={dict(dz_C)} span->(static obj,face,vertex) match={span_ok} mismatch={span_bad}")

        # ---- POS
        p = find(d, '.POS')
        pos, apo = F.parse_pos(open(p, 'rb').read(), plc['count'])
        refs = [i + v for i, v in enumerate(pos['index']) if v != -1]
        starts = {b['word'] for b in pos['blocks']}
        idx_ok = all(r in starts for r in refs) and len(refs) == len(pos['blocks'])
        res_ok = all(b['reserved'] == 0 for b in pos['blocks'])
        types = Counter(recs[b['plc']]['type'] for b in pos['blocks'] if b['plc'] is not None)
        verdict(p, apo, idx_ok and res_ok,
                f"paths={len(pos['blocks'])} index->block ok={idx_ok} reserved==0 {res_ok} "
                f"points={sum(b['count'] for b in pos['blocks'])} PLC types with paths={dict(types)}")

        # ---- LUTs
        for ext in ('.SHD', '.TAB', '.PAN'):
            p = find(d, ext)
            lut, al = F.parse_lut(open(p, 'rb').read())
            verdict(p, al, al.size == 65536, 'plain 256x256 byte table')

    log("\n=== CARS / menu car ===")
    check_mesh_bank(GAME + '/CARS/Cars.plc', GAME + '/CARS/Cars.msh', GAME + '/CARS/Cars.tex')
    check_mesh_bank(GAME + '/baltazar/data/Menucar.plc', GAME + '/baltazar/data/Menucar.msh',
                    GAME + '/baltazar/data/Menucar.tex')
    p = GAME + '/CARS/TEST.AIS'
    ais, aa = F.parse_ais(open(p, 'rb').read())
    keys = {tuple(sorted(k for k in c if k != 'car')) for c in ais['cars']}
    verdict(p, aa, len(keys) == 1, f"cars={len(ais['cars'])} identical key set={len(keys) == 1} keys={sorted(next(iter(keys)))}")

    log("\nSRF header constants across levels:", dict(consts))
    log(f"\nRESULT: {'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
    with open(os.path.join(OUT, 'validation.txt'), 'w') as fh:
        fh.write(buf.getvalue())


if __name__ == '__main__':
    main()
