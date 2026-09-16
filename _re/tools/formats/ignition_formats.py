#!/usr/bin/env python3
"""
ignition_formats.py -- parsers for Ignition (UDS 1997) track / car data files.

Formats covered (spec: _re/notes/04_track_formats.md):
    MSH  mesh bank                LEVELS\\<L>\\<L>.MSH, CARS\\CARS.MSH, baltazar\\data\\menucar.msh
    PLC  object placement list    LEVELS\\<L>\\<L>.PLC, CARS\\CARS.PLC, baltazar\\data\\menucar.plc
    TRI  AI gate records          LEVELS\\<L>\\<L>.TRI
    SRF  surface lookup grid      LEVELS\\<L>\\<L>.SRF
    POS  animation paths          LEVELS\\<L>\\<L>.POS
    SHD / TAB / PAN  256x256 LUTs LEVELS\\<L>\\<L>.SHD/.TAB/.PAN
    AIS  AI tuning text           CARS\\TEST.AIS (not referenced by the EXE)

Every parser returns (obj, acct) where acct is a ByteAccount recording which byte ranges
were consumed by named fields; acct.unaccounted() lists every byte not covered.
All integers are little-endian. Pure Python, no dependencies.
"""
import struct

BIAS = 0x6400          # 25600: added to x and z by the engine for SRF / AI (never to y)


class ByteAccount:
    def __init__(self, size):
        self.size = size
        self.ranges = []           # (start, end, label)

    def take(self, start, length, label):
        self.ranges.append((start, start + length, label))

    def unaccounted(self):
        cov = bytearray(self.size)
        over = 0
        for s, e, _ in self.ranges:
            for i in range(max(0, s), min(e, self.size)):
                if cov[i]:
                    over += 1
                cov[i] = 1
        gaps, i = [], 0
        while i < self.size:
            if not cov[i]:
                j = i
                while j < self.size and not cov[j]:
                    j += 1
                gaps.append((i, j))
                i = j
            else:
                i += 1
        beyond = [(s, e, l) for s, e, l in self.ranges if e > self.size]
        return gaps, over, beyond

    def ok(self):
        g, o, b = self.unaccounted()
        return not g and not o and not b


# --------------------------------------------------------------------------- PLC
def parse_plc(data):
    """u32 count; count x 20-byte records.
    rec: u32 msh_off (in 4-byte words from start of MSH file)
         u32 typeword: bits 0-11 type, bits 12-15 group, bits 16-23 b6, bits 24-31 b7
         i32 x, y, z  (world position of the mesh origin; no rotation)"""
    acct = ByteAccount(len(data))
    n, = struct.unpack_from('<I', data, 0); acct.take(0, 4, 'count')
    recs = []
    for i in range(n):
        o = 4 + 20 * i
        if o + 20 > len(data):
            break
        off, tw, x, y, z = struct.unpack_from('<IIiii', data, o)
        recs.append(dict(index=i, msh_off=off, typeword=tw, type=tw & 0xFFF,
                         group=(tw >> 12) & 0xF, b6=(tw >> 16) & 0xFF, b7=(tw >> 24) & 0xFF,
                         x=x, y=y, z=z))
        acct.take(o, 20, 'rec')
    return dict(count=n, recs=recs), acct


# --------------------------------------------------------------------------- MSH
FACE_SIZE = 44


def parse_mesh_at(data, pos):
    nv, nf = struct.unpack_from('<II', data, pos)
    verts = [struct.unpack_from('<3i', data, pos + 8 + 12 * k) for k in range(nv)]
    fp = pos + 8 + 12 * nv
    faces = []
    for j in range(nf):
        f = struct.unpack_from('<I3I6iI', data, fp + FACE_SIZE * j)
        faces.append(dict(
            word_off=(fp + FACE_SIZE * j - pos) // 4,   # offset in dwords from mesh start (SRF +0x12)
            mode=f[0] & 0xFFFF,                          # render mode (0x11..0x17 observed)
            surface=f[0] >> 16,                          # surface / material id (AI: <40 drivable)
            idx=(f[1], f[2], f[3]),
            uv=((f[4], f[5]), (f[6], f[7]), (f[8], f[9])),   # 8.8 fixed texel coords in a 256x256 page
            tail_lo=f[10] & 0xFFFF,                      # always 0
            page=f[10] >> 16))                           # texture page index into .TEX
    return dict(pos=pos, nv=nv, nf=nf, verts=verts, faces=faces,
                size=8 + 12 * nv + FACE_SIZE * nf)


def parse_msh(data, plc=None):
    """Sequence of mesh objects, each: u32 nv; u32 nf; nv x (i32 x,y,z); nf x 44-byte face.
    face: u16 mode, u16 surface, u32 i0,i1,i2, i32 u0,v0,u1,v1,u2,v2, u16 0, u16 page"""
    acct = ByteAccount(len(data))
    meshes, pos = [], 0
    while pos + 8 <= len(data):
        m = parse_mesh_at(data, pos)
        if pos + m['size'] > len(data):
            break
        acct.take(pos, 8, 'mesh hdr'); acct.take(pos + 8, 12 * m['nv'], 'verts')
        acct.take(pos + 8 + 12 * m['nv'], FACE_SIZE * m['nf'], 'faces')
        meshes.append(m)
        pos += m['size']
    return dict(meshes=meshes, by_word={m['pos'] // 4: m for m in meshes}), acct


# --------------------------------------------------------------------------- TRI
TRI_REC = 500


def parse_tri(data):
    """u32 header (not read by the game); k x 500-byte records, record i belongs to PLC object i.
    Fields the game reads (0x00414E40): +0x00 u8 b0, +0x01 s16 vtx_a, +0x17 s16 vtx_b, +0x6D u8 mode.
    Stored but unread: +0x03 5*i32 (xa,ya,za,0,0), +0x19 5*i32 (xb,yb,zb,0,0), +0x2D 8*f64.
    +0x6E..+0x1F3 are padding (normally 0; some records carry stale non-zero bytes)."""
    acct = ByteAccount(len(data))
    hdr, = struct.unpack_from('<I', data, 0); acct.take(0, 4, 'header')
    k = (len(data) - 4) // TRI_REC
    recs = []
    for i in range(k):
        o = 4 + TRI_REC * i
        r = data[o:o + TRI_REC]
        recs.append(dict(
            index=i, empty=not any(r), b0=r[0],
            vtx_a=struct.unpack_from('<h', r, 1)[0], a=struct.unpack_from('<5i', r, 3),
            vtx_b=struct.unpack_from('<h', r, 0x17)[0], b=struct.unpack_from('<5i', r, 0x19),
            dbl=struct.unpack_from('<8d', r, 0x2D), mode=r[0x6D],
            pad_nonzero=sum(1 for c in r[0x6E:] if c)))
        acct.take(o, TRI_REC, 'rec')
    return dict(header=hdr, count=k, recs=recs), acct


# --------------------------------------------------------------------------- SRF
def parse_srf(data):
    """Header 9*i32: size_z, size_x (world size in 256-unit steps, =200), cell_x, cell_z (=512),
    W, H (=101), nSpan, nListB, nListC.
    W*H cells (12 B): i32 offC, i32 offB, u16 nB, u16 nC   (offsets in bytes into listC / listB)
    nSpan spans (24 B): i32 x0, i32 z0, i32 dxdz_a, i32 dxdz_b (16.16), s16 dz, u16 face_word,
                        i32 obj_off (byte offset into 42-byte runtime world-object pool)
    listB nListB*i32, listC nListC*i32: byte offsets into span array."""
    acct = ByteAccount(len(data))
    h = struct.unpack_from('<9i', data, 0); acct.take(0, 36, 'header')
    # 0x00412FC0: cell = (z / h[2]) * h[4] + x / h[3]  -> h[2] = cell size along z, h[3] along x
    size_z, size_x, cell_z, cell_x, W, H, nS, nB, nC = h
    c0 = 36
    cells = [struct.unpack_from('<iiHH', data, c0 + 12 * i) for i in range(W * H)]
    acct.take(c0, 12 * W * H, 'cells')
    s0 = c0 + 12 * W * H
    spans = [struct.unpack_from('<iiiihHi', data, s0 + 24 * i) for i in range(nS)]
    acct.take(s0, 24 * nS, 'spans')
    b0 = s0 + 24 * nS
    listB = struct.unpack_from('<%di' % nB, data, b0); acct.take(b0, 4 * nB, 'listB')
    l0 = b0 + 4 * nB
    listC = struct.unpack_from('<%di' % nC, data, l0); acct.take(l0, 4 * nC, 'listC')
    return dict(header=h, W=W, H=H, cell_x=cell_x, cell_z=cell_z, cells=cells, spans=spans,
                listB=listB, listC=listC), acct


# --------------------------------------------------------------------------- POS
def parse_pos(data, n_plc):
    """n_plc x i32 index (−1 = no path, else word offset relative to the entry's own index),
    then blocks: i32 count, i32 zero, count x (i32 x,y,z, i32 a,b,c)."""
    acct = ByteAccount(len(data))
    W = len(data) // 4
    idx = struct.unpack_from('<%di' % n_plc, data, 0); acct.take(0, 4 * n_plc, 'index')
    paths, starts = {}, {}
    for i, v in enumerate(idx):
        if v != -1:
            starts[i + v] = i
    p = n_plc
    blocks = []
    while p + 2 <= W:
        cnt, z = struct.unpack_from('<ii', data, 4 * p)
        if cnt < 0 or p + 2 + 6 * cnt > W:
            break
        pts = [struct.unpack_from('<6i', data, 4 * (p + 2) + 24 * k) for k in range(cnt)]
        acct.take(4 * p, 8, 'blk hdr'); acct.take(4 * (p + 2), 24 * cnt, 'points')
        blocks.append(dict(word=p, count=cnt, reserved=z, points=pts, plc=starts.get(p)))
        if p in starts:
            paths[starts[p]] = blocks[-1]
        p += 2 + 6 * cnt
    return dict(index=idx, blocks=blocks, paths=paths, start_words=starts), acct


# --------------------------------------------------------------------------- LUTs
def parse_lut(data):
    acct = ByteAccount(len(data))
    acct.take(0, min(len(data), 65536), 'lut256x256')
    rows = [data[256 * r:256 * r + 256] for r in range(256)]
    return dict(rows=rows), acct


# --------------------------------------------------------------------------- AIS
def parse_ais(data):
    """CRLF text: 'car N' headers followed by 'key<spaces>value' lines."""
    acct = ByteAccount(len(data))
    cars, cur = [], None
    pos = 0
    for raw in data.split(b'\n'):
        line = raw.rstrip(b'\r').decode('latin-1')
        s = line.strip()
        if s.lower().startswith('car '):
            cur = dict(car=int(s.split()[1])); cars.append(cur)
        elif s and cur is not None:
            k, v = s.split(None, 1)
            cur[k] = float(v)
        acct.take(pos, len(raw) + 1, 'line')
        pos += len(raw) + 1
    acct.ranges[-1] = (acct.ranges[-1][0], len(data), 'line')  # last line has no '\n'
    return dict(cars=cars), acct
