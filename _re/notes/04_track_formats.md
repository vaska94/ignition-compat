# Ignition (`Ign_win.exe`, UDS / Virgin 1997) — track geometry & track/car data formats

Scope: `.MSH .PLC .TRI .SRF .POS .SHD .TAB .PAN` (level + car variants) and `CARS\TEST.AIS`.
PIC/TEX/COL/fonts/audio belong to the images/audio notes and are only cross-referenced here.
Nothing in the game directory was modified; the game was not run.

Confidence tags: **CERTAIN** = verified in disassembly or by exact byte accounting over all files;
**INFERRED** = consistent with all data/code seen, but not proven by a code path.

## 0. Results at a glance

* **Every byte of every in-scope file is accounted for** (61 files: 7 levels × 8 formats, `Cars.msh/plc`,
  `Menucar.msh/plc`, `TEST.AIS`) — 0 unaccounted, 0 overlapping, 0 past-EOF bytes. Report:
  `_re/out/tracks/validation.txt` (regenerate with `python3 _re/tools/formats/validate_all.py`).
* Cross-format checks that pass on all 7 levels: every PLC offset hits a mesh start (1:1),
  every face index < nverts, every SRF span (78 792 spans in total) resolves to
  *(j-th static PLC object, face at the stored word offset, vertex of that face after PLC translation
  + 0x6400)* with **zero mismatches**, every POS index entry hits a path block.
* OBJ exports of all tracks + both car banks and top-down PNGs: `_re/out/tracks/`.
  The Canada and Japan renders are clean closed circuits, and Japan/Carib match the in-game
  track-selection map sprites (used to fix handedness, §2).
* Data-quality findings: **TRI files are stale** relative to MSH/PLC (stored coordinates don't match
  in 0–100 % of records per level; the game ignores them and recomputes), and **`Iceland.tri` is short**
  (233 records for 247 PLC objects) — the game reads it with an unchecked `fread`, so the last 14
  records are uninitialised heap at run time (§5).
* `CARS\TEST.AIS` is **never opened** by this build (§9).

Tools (all under `_re/tools/formats/`):

| script | purpose |
|---|---|
| `ignition_formats.py` | parsers + `ByteAccount` byte-coverage tracker for every format |
| `validate_all.py` | runs all parsers over all files, byte accounting + cross-validation, PASS/FAIL table |
| `export_tracks.py` | OBJ/MTL per level and car bank, top-down PNGs (needs pillow: venv python) |
| `refscan.py` | exhaustive byte-level dword reference scanner (`--str TEXT` resolves strings first) |

(Note: `_re/tools/bytescan.py` now belongs to another agent — it overwrote an earlier copy of mine
with a different CLI. Use `refscan.py` for the commands quoted here.)

---

## 1. Loaders

Every level asset is loaded by the same idiom — sprintf a path, `_open(path, _O_BINARY)`,
`_filelength`, allocate from the level pool, one `_read` of the whole file, `_close`. The data is then
used **in place** (only SRF gets pointer fix-ups). Example, MSH (**CERTAIN**):

```
0x00419BD9  push 0x552f40            ; level file stem
0x00419BDE  push 0x553790            ; level dir
0x00419BE3  push 0x498ba4            ; "LEVELS\%s%s.MSH"
0x00419BE9  call 0x4695c0            ; sprintf
0x00419BF5  push 0x8000              ; _O_BINARY (read-only)
0x00419BFB  call 0x470e90            ; _open   (fail -> "Error while trying to read %s" + exit(1))
0x00419C26  call 0x4787c0            ; _filelength(fd)
0x00419C38  call 0x45ae10            ; pool_alloc([0x55307c], len)
0x00419C40  mov  [0x525e60], eax     ; g_levelMesh
0x00419C61  call 0x46abd0            ; _read(fd, buf, len)
0x00419C6A  call 0x46a9d0            ; _close
```

String references were found by exhaustive byte scan (`refscan.py --str`), not by linear disassembly.

| file | path string (VA) | ref site | loader function | buffer global | notes |
|---|---|---|---|---|---|
| `.COL` | `LEVELS\%s%s.COL` `0x00498A94` | `0x00418DE4` | `0x00418DD0` | `0x0054F900` | not in scope |
| `.PAN` | `LEVELS\%s%s.PAN` `0x00498A64` | `0x00418E81` | `0x00418DD0` | `0x005DFE64` | alloc len+0x10000, ptr rounded up to a 64 K boundary |
| `.PIC` | `LEVELS\%s%s.PIC` `0x00498A54` | `0x00418F40` | `0x00418DD0` → `0x004574A0` | `0x00525E94` | not in scope |
| `.SHD` | `LEVELS\%s%s.SHD` `0x00498A2C` | `0x00418FEB` | `0x00418DD0` | `0x00527F74`, copied to `0x0063B5F0` | 64 K aligned |
| `.TAB` | `LEVELS\%s%s.TAB` `0x00498A1C` | `0x004190B6` | `0x00418DD0` | `0x00563BE4` | 256-aligned |
| `.PLC` | `LEVELS\%s%s.PLC` `0x00498B88` | `0x00419AA4` | `0x00419A90` (called `0x00419170`) | `0x0054F9CC` | |
| `CARS.PLC` | `CARS\CARS.PLC` `0x00498B78` | `0x00419B37` | `0x00419A90` | `0x00552FC8` | |
| `.MSH` | `LEVELS\%s%s.MSH` `0x00498BA4` | `0x00419BE4` | `0x00419BD0` (called `0x0041917F`) | `0x00525E60` | |
| `CARS.MSH` | `%sCARS.MSH` `0x00498B98` + `CARS\` `0x00494A30` | `0x00419C7C` | `0x00419BD0` | `0x00527F34` | |
| `.TEX` | `LEVELS\%s%s.TEX` `0x00498BD4` | `0x00419D29` | `0x00419D10` | | not in scope |
| `.POS` | `LEVELS\%s%s.POS` `0x00498A0C` | `0x004191AC` | `0x00418DD0` | `0x00553064` | |
| `.TRI` | `LEVELS\%s%s.TRI` `0x00493C80` | `0x004151CE` | `0x00414E40` (called `0x00418C49`) via `load_file` `0x00457420` | `0x00639C0C` (+ ptr table `0x00639C08`) | fixed-size read, see §5 |
| `.SRF` | `LEVELS\%s%s.SRF` `0x004989FC` | `0x00418CCE` | `0x00412670` (called `0x00418CE6`) via `0x004574A0` | `0x004937BC` + field globals `0x004C53A4..D4` | pointer fix-ups; 2nd string ref `0x0041B86B` only sprintf's |
| menu car | `baltazar\data\menucar.msh/.plc` `0x00492138/54` | `0x0040CB56..0x0040CBD7` | menu | | same MSH/PLC formats |
| `TEST.AIS` | `CARS\TEST.AIS` `0x00493CDC` | data ptr `0x00494AD4` only | **none** | | §9 |

`0x00418DD0` is one function that loads, in order: COL, PAN, PIC, (sets 320×200×8), SHD, TAB,
then calls `0x00419A90` (PLC + CARS.PLC), `0x00419BD0` (MSH + CARS.MSH), `0x00419D10` (TEX), loads POS,
then `0x0041AC40`, `0x0041AF70`. **CERTAIN.**

---

## 2. Axes, units, numeric representation

* **Y is the vertical axis — CERTAIN.** The ground/surface lookup (`0x00412FC0`, §6) is keyed by (x, z)
  only; the engine adds the 0x6400 bias to x and z but never to y
  (`0x0041538B add eax,0x6400` for x, `0x004153A2` y without bias, `0x004153CE add ecx,0x6400` for z).
* **+Y points DOWN — INFERRED (strong).** On 21 of the 22 car-body meshes in `Cars.msh`/`Menucar.msh`
  the vertex slice at minimum y is shorter than the slice at maximum y: roof x-length typically
  18–36 (up to 103 on the long vehicles), floor x-length 53–133. The one exception is `Cars.msh`
  obj 20 (type 60). Level data agrees: terrain has a long negative-y tail (mountains) and
  vertical cliff/waterfall sheets rise from y≈0 to y≈−1120 (Canada objs 99/109/110).
  Contradicting observation: in `CARS.PLC` the wheel origins sit 14 units *lower in y* than the body
  origin, which would put the hubs at roof height if y is down. Those positions look like a showroom
  layout, so this evidence is weak.
* **Handedness: left-handed — INFERRED (strong).** A top-down render with column = +x and row = +z
  (viewed from −y, i.e. from above) matches the in-game track-selection maps in
  `baltazar\data\Trk_spr.pic` (decoded by the images agent: `_re/out/images/pic/Baltazar_data_Trk_spr.png`).
  Japan (bow-knot on the left, diagonal double-back towards lower-left) and Carib (triangle on the
  left, loop upper right) match. The mirrored candidates do not. So, looking from above,
  +x = east/right and +z = south/down the map, with +y down. This is consistent with the winding: all 66 closed car meshes have negative
  signed volume in raw coordinates; after the OBJ conversion (x, −y, z) the faces become CCW-front.
  The projection code itself was not located, so this is not CERTAIN.
* **Units:** plain 32-bit integers, not fixed-point, for vertices, PLC positions and POS points.
  Scale (INFERRED): a car body is ≈78 × 37 × 23 units and a road gate (TRI edge-to-edge) is ≈300
  units wide, i.e. very roughly 20 units per metre.
* **World extent:** SRF covers 101 × 512 = 51 712 units per side, and the engine works in biased
  coordinates x+25600 / z+25600, so raw x, z ∈ [−25600, 25600]. Tracks stay within |x|, |z| < 8 702.
  (CERTAIN for the bias and grid arithmetic; the extent reading is INFERRED.)
* **Fixed-point:** SRF edge slopes are 16.16 (CERTAIN: `imul; sar eax,0x10` at `0x004465A2`, built by
  the 16.16 divide helper `0x00412640`). Mesh UVs are most likely 8.8 texel coordinates in a 256×256
  page (INFERRED: range 0..0xFE14 = 254.08).

**OBJ conversion used by `export_tracks.py`: `OBJ = (x, −y, z)`, face index order unchanged.**

---

## 3. PLC — object placement list (level, `CARS.PLC`, `menucar.plc`)

```
u32 count
count × 20-byte record:
  +0x00 u32 msh_off   offset of this object's mesh, in 4-byte words from the start of the .MSH file
  +0x04 u32 typeword  bits 0-11 type | bits 12-15 group | bits 16-23 b6 | bits 24-31 b7
  +0x08 i32 x
  +0x0C i32 y         world position of the mesh origin (translation only, no rotation)
  +0x10 i32 z
```

Size = 4 + 20·count exactly for all 9 files (**CERTAIN**).

Field decode at level start (`0x0041B360`, **CERTAIN**). The typeword is split into a side table
`[0x00552E40]` (12 B per object) and then masked in place to its low 12 bits:

```
0x0041B3AC  mov ebx, [eax+edx-0xc]      ; typeword (rec+4)
0x0041B3B2  and eax, 0xf000 / shr eax, 0xc   -> table+0 (group)
0x0041B3C0  shr ebx, 0x18                     -> table+8 (b7)
0x0041B3C3  and eax, 0xff0000 / shr eax,0x10  -> table+4 (b6)
0x0041B3E4  and [eax+edx-0xc], 0xfff          ; rec+4 = type
```

Mesh pointer is `MSH + msh_off*4` (`0x00415177 mov eax,[edx+ebp+4]; lea eax,[edi+eax*4]`), position
is rec+8..+16 (`0x00415169` / `0x0041B7C6 mov eax,[eax+ebp+0xc]; add eax,0x6400`). **CERTAIN.**

**Type semantics** (world build `0x0041B756` and the special-object pass `0x0041B89D`, **CERTAIN**
for the tests, INFERRED for the names):

| type | handling |
|---|---|
| `< 100` | static world object: `0x00446D90(slot, mesh, 0, type, [0x553784][i], -1, group)` creates a 42-byte world object (`+4` mesh ptr, `+0x1E` type, `+0x22` bounding radius computed when −1 is passed, `+0x24` group, `+0x26` child link). **Only these are indexed by SRF.** |
| `≥ 100` | special object, second pass: |
| 150–199, 300–349, 357 | only created when `[0x00553084]` ≠ 0 |
| 350, 351 | skipped when they have a POS path and `[0x00553084]` = 0 |

`[0x00553084]` is loaded from race-settings blob +0x14, which note 03 identifies as OBSTACLES
(`0x004193EF mov [0x553084], edx` with `edx = [0x6393b4]`). It is forced to 1 when `[0x527F6C]` == 0
(`0x00419417`), where `[0x527F6C]` is derived from blob +0x08 (game mode switch at `0x004192EC`).
**CERTAIN.**

Observed types: static 0..~110; special 150–154, 200, 300–304, 350/351/357, 400–413.
Group nibble values 0–3 appear in Japan/USA/Canada (e.g. `0x2002` = type 2 group 2).
b6/b7: small integers (0–15). Their meaning is **unknown**.

**Cars:** `CARS.PLC` has 55 records with type = `car*10 + part` (part 0 = body with 32–70 verts,
parts 1–4 = the four 12-vertex wheels), giving 11 cars (0..10). group = b6 = b7 = 0. INFERRED from data;
the consumer `0x0041D2B6..` was not traced. `menucar.plc` holds 11 records, one high-poly whole-car
mesh each, type = car index 0..10.

---

## 4. MSH — mesh bank

A plain concatenation of mesh objects, with no file header. PLC `msh_off` values point at the object
starts; objects are contiguous and the last one ends exactly at EOF in all 9 files. **CERTAIN.**

```
mesh object:
  +0x00 u32 nv
  +0x04 u32 nf
  +0x08 nv × { i32 x, i32 y, i32 z }                 local coordinates
  +0x08+12·nv  nf × 44-byte face:
      +0x00 u16 mode      render-routine selector (17,18,19,21,22,23 observed)
      +0x02 u16 surface   surface/material id
      +0x04 u32 i0, i1, i2     vertex indices (< nv in 100 % of 58 978 faces)
      +0x10 i32 u0, v0, u1, v1, u2, v2   texture coords (8.8, 0..0xFE14)
      +0x28 u16 zero      always 0
      +0x2A u16 page      texture page (0..15 levels, 0..5 cars)
```

Evidence:
* Layout of vertex/face arrays — **CERTAIN**: `0x00415183 add eax,8` (verts at +8),
  `0x0041519B lea eax,[ecx+eax*4]` with `eax = nv*3`, `+8` → faces at `8+12·nv`; face stride 0x2C
  (`0x00415F5C add ecx,0x2c`; rasteriser `0x0044C1A2 add edi,0x2c`); indices at +4/+8/+0xC
  (`0x00415FA6 cmp [edi+4],edx`, `[edi+8]`, `[edi+0xC]`).
* `mode` low byte selects the draw routine — **CERTAIN**:
  `0x0044C27B movsx ebp, byte ptr [eax]; call [ebp*4 + 0x49C8E0]`, with a mode-0x11 fast path
  `0x0044C1B1 cmp cl, 0x11`. Which routine does what (flat/gouraud/textured/transparent) is **unknown**.
* `surface` — **CERTAIN** that the AI treats `surface < 40 && surface % 10 ∉ {5,6,7}` specially
  (`0x00415F11 mov eax,[eax+ecx]; shr eax,0x10; cmp eax,0x28; jge skip; idiv 10; cmp edx,5/6/7`).
  Rendering exactly those faces in red produces the racing line, so this is the **drivable road
  surface** set (INFERRED). Common values: 0–24 roads/tarmac variants, 78/79/80 terrain,
  90 rock, 110, 131, 300–305.
* `page` and UV semantics — INFERRED: page range 0..15 matches the 16 × 65536-byte blocks of level
  `.TEX` and 0..5 matches `Cars.tex`; UVs span the 8.8 range of a 256×256 page.
  Partial last pages — **CERTAIN** (corrected 2026-09-16; an earlier draft of this note wrongly concluded
  that TEX is not a plain page concatenation). TEX *is* a plain run of 256×256 pages whose last page may
  be partial, as `05_image_font_audio_formats.md` §TEX states. `Iceland.tex` is 1 065 088 B
  (16.25 × 65536: 16 full pages plus 64 rows) and `iceland.msh` uses page 16 on 4 faces, whose texel rows
  reach at most 62. `JAPAN.TEX` is 15.75 × 65536 B (15 full pages plus 192 rows) and every `JAPAN.MSH`
  face on page 15 stays at or below row 190. Checked with an independent MSH parser over all 7 levels:
  byte-exact, no out-of-range vertex indices, and no UV beyond texel 254, consistent with 8.8 UVs.
* Byte accounting — **CERTAIN**: all 9 MSH files are fully covered by headers + verts + faces.

Counts: Austria 321 objects / 8 085 faces, Brazil 315 / 6 271, Canada 282 / 6 258,
Carib 343 / 10 826, Iceland 247 / 5 340, Japan 451 / 11 294, USA 223 / 6 702,
Cars 55 / 1 602, Menucar 11 / 1 602.

---

## 5. TRI — AI gate records

```
u32 header                   (NOT read by the game)
k × 500-byte record, record i belongs to PLC object i:
  +0x00 u8   b0            copied to AI node +1
  +0x01 s16  vtx_a         vertex index in object i's mesh (gate end A)
  +0x03 5×i32  xa, ya, za, 0, 0      cached world coords of A (x,z carry the +0x6400 bias) — unread
  +0x17 s16  vtx_b         vertex index (gate end B)
  +0x19 5×i32  xb, yb, zb, 0, 0      cached coords of B — unread
  +0x2D 8×f64              unread (values within ±π: probably angles)
  +0x6D u8   mode          copied to AI node +0x4A; 1 and 2 select gate-splitting code paths
  +0x6E..+0x1F3            padding (0, except stale bytes in 5–20 % of records)
```

Loader `0x00414E40` — **CERTAIN**:

```
0x004151E5  mov eax,[ebp] ; nPLC   -> eax*500 + 4
0x004151FD  call 0x469400          ; malloc(4 + 500*nPLC)   -> [0x639C0C]
0x00415233..0x00415258              ; ptrtab[i] = buf + 4 + i*500   -> [0x639C08]
0x0041526F  call 0x457420           ; load_file("LEVELS\..TRI", buf, 4+500*nPLC, 0)  (result ignored)
```

The size read is **derived from the PLC count**, not from the file. Every dereference of `[0x639C08]`
in the image (exhaustive scan: `0x00415223…0x00415E59`) reads only `+0x00`, `+0x01`, `+0x17`, `+0x6D`.
The coordinates are rebuilt from the live mesh:

```
0x00415342  movsx ecx, word ptr [eax+1]      ; vtx_a
0x00415346  movsx edx, word ptr [eax+0x17]   ; vtx_b
0x00415373  mov eax,[verts+ecx*12]; add eax,[plc.x]; add eax,0x6400 -> node+2
0x004153A2  ... y (no bias) -> node+6 ; 0x004153CE z + 0x6400 -> node+0xA
0x00415487  mov al, byte ptr [edx+0x6d]; cmp al,1 / cmp al,2   ; gate split modes
```

AI nodes are 0x4B bytes and are built in the order of the list `[0x00552F60]` (count `[0x005287F8]`),
which is constructed elsewhere, around `0x00417252`.

Byte accounting: 4 + 500·k exact for all 7 files (**CERTAIN**).
Consistency (validation.txt): cached x/z match mesh+PLC in Canada 170/254 records, Brazil 216/302,
Carib 224/334, Japan 234/325, USA 152/223, **Austria 0/318, Iceland 0/205** (TRI files date from
Apr–May 1997, MSH/PLC from Jul–Aug). Harmless, because the game ignores the cached values.
Out-of-range vertex indices occur (8–69 per level), presumably on objects that are not in the AI order
list (INFERRED).

**Iceland defect — CERTAIN in code:** `Iceland.tri` has 233 records but `Iceland.plc` has 247 objects.
`load_file` (`0x00457420`) does `fopen "rb"` / `fseek` / `fread(buf,1,len)`, and on a short read
returns `0x7DA` **without `fclose`** (`0x0045747C`). The caller ignores the result, so records 233–246
are uninitialised `malloc` memory at run time. Impact depends on whether those objects are in the
AI order list (INFERRED low).

`b0`, the 8 doubles and `mode` values > 2 (e.g. 46/47 in Carib, 255 in Japan) are **unexplained**.

---

## 6. SRF — surface lookup grid (ground / collision query)

```
+0x00  9×i32 header: size_z, size_x, cell_z, cell_x, W, H, nSpan, nListB, nListC
       (all levels: 200, 200, 512, 512, 101, 101, …)
+0x24  W×H × 12-byte cell (row-major, row = z):
         +0 i32 offC   byte offset into listC
         +4 i32 offB   byte offset into listB
         +8 u16 nB
         +A u16 nC     (empty cells: nB = nC = 0, offsets are garbage)
       nSpan × 24-byte span:
         +0x00 i32 x0      biased x (x+0x6400) of the span apex vertex
         +0x04 i32 z0      biased z of the apex
         +0x08 i32 dxdz_a  16.16 slope of edge A
         +0x0C i32 dxdz_b  16.16 slope of edge B   (dxdz_a <= dxdz_b)
         +0x10 s16 dz      z extent (>0 spans run +z, <0 spans run −z)
         +0x12 u16 face    face offset in 4-byte words from the owning mesh start
         +0x14 i32 obj     byte offset into the runtime 42-byte world-object pool (index = obj/42)
       nListB × i32  byte offsets into the span array (spans with dz <= 0)
       nListC × i32  byte offsets into the span array (spans with dz >= 0)
```

Size = 36 + 12·W·H + 24·nSpan + 4·(nListB + nListC) exactly for all 7 files (**CERTAIN**).

Loader/fix-ups `0x00412670` — **CERTAIN**:

```
0x004126AF  lea edi,[eax+0x24]            ; cells            -> [0x4C53D4]
0x004126D6  imul edi,ebx ; ebx=W*H*12 ... ; spans = +0x24+12WH  -> [0x4C53A8]
0x004126EC  lea eax,[ebx+eax*8]            ; listB = spans+24*nSpan -> [0x4C53A4]
0x004126FA  lea eax,[ebp+edx*4]            ; listC = listB+4*nListB -> [0x4C53C0]
0x0041270B..  cell+0 += listC ; cell+4 += listB
0x00412756..  span+0x14 += arg2 ([0x63C5DC], world-object pool base)
0x00412775..  every listB/listC entry += spans
```

Query `0x00412FC0(x, ?, z)` with the asm scanners `0x00446578` / `0x004465E1` — **CERTAIN**:

```
0x00412FC6  idiv [0x4C53C8]  ; z / cell_z
0x00412FD6  imul ecx,[0x4C53CC] ; * W
0x00412FE1  idiv [0x4C53B8]  ; + x / cell_x
0x00413017  mov esi,[cell]    ; cx = word [cell+0xA]  -> 0x446578 (needs z0 <= z <= z0+dz)
0x0041303A  mov esi,[cell+4]  ; cx = word [cell+8]    -> 0x4465E1 (needs z0+dz <= z <= z0)
0x0044659E  imul eax,[esi+8]; sar eax,0x10; add eax,[esi]  ; xa = x0 + dxdz_a*(z-z0)
0x004465AB  imul edi,[esi+0xc]; sar edi,0x10; add edi,[esi] ; xb ; require xa <= x <= xb
0x004465B9  out[0] = span.obj ; out[4] = obj->mesh + span.face*4   ; -> (object, face) hits
```

So SRF is a pre-rasterised index of every static triangle's footprint on the x/z plane. Each triangle
is split at its middle vertex into up to two scanline spans (builder `0x00412D70`). A query returns all
(world object, face) pairs under a point, and the height then comes from the face itself.

The builder/writer (`0x004129D0`, `0x004127C0`, `0x00412B50`, `0x00412D30`, `0x00412D70`) is compiled
in but **unreferenced**: an exhaustive rel32 call/jmp scan and an absolute-dword scan find 0 references
to `0x004129D0` and `0x004127C0`. It is leftover level-tool code. **CERTAIN.**

Cross-validation (**CERTAIN**, 78 792 of 78 792 spans across 7 levels): for every span,
obj/42 = j gives the j-th PLC record with type < 100; `face` is a valid face start in that record's
mesh; and (x0, z0) equals one of that face's vertices + PLC (x, z) + 0x6400. The SRF files are
therefore current with the shipped MSH/PLC, and PLC placement has no rotation. Grid arithmetic:
W = size_x·256/cell_x + 1 (builder `0x00412A02..0x00412A17`; the factor 256 is INFERRED).
Every listB span has dz ≤ 0 and every listC span has dz ≥ 0. ΣnB = nListB and ΣnC = nListC,
so no span offset is shared between cells.

---

## 7. POS — animation paths for special objects

```
nPLC × i32 index     −1 = object has no path; else word offset of its block, relative to the entry's own index
blocks (contiguous, starting at word nPLC):
  i32 count
  i32 reserved       (always 0)
  count × { i32 x, i32 y, i32 z, i32 a, i32 b, i32 c }     24 bytes per point
```

Consumer `0x004356D0` — **CERTAIN**:

```
0x004356E9  mov eax,[POS + i*4]; cmp eax,-1; je next
0x004356FA  add eax,ecx ; lea esi,[POS + eax*4]    ; block = POS + (index[i]+i)*4
0x0043571B  mov eax,[esi] ; count
0x00435726  lea edi,[edx+0x18] ...                 ; point[k].xyz -= point[k+1].xyz (in place)
```

Also tested by the special-object pass (`0x0041B91C cmp [POS+i*4],-1`) and at `0x0042AA4C`.

Byte accounting exact, with every index entry hitting a block start and blocks tiling to EOF on all
7 levels (**CERTAIN**). Paths: Austria 21, Brazil 4, Canada 9, Carib 8, Iceland 2, Japan 18, USA 11.
They are owned by PLC types 200, 300–304, 351, 357, 400–408 (trains, boats, planes and similar —
INFERRED). x/y/z are absolute world coordinates (paths overlay the geometry correctly in the PNGs).
a/b/c: a steps by a constant (e.g. 0, 65, 130 …, or 46, 93 …), probably an orientation or a time
parameter. **Unknown.**

---

## 8. SHD / TAB / PAN — 256×256 byte look-up tables

All 21 files are exactly 65 536 B with no header (**CERTAIN**).

| file | per level | consumer | structure | meaning |
|---|---|---|---|---|
| `.PAN` | 7 distinct | `0x00438359`: `al=src; shl eax,8; add ecx=screen pixel; al=[PAN+eax+ecx]` (**CERTAIN** index form `PAN[fg*256+bg]`); also `[0x63C5F4]` | 144 all-zero rows (fg 16–159) | colour blend / translucency table (INFERRED) |
| `.SHD` | 7 distinct | table argument to polygon routine `0x0044CB20` (`0x0044CAC2`), stored in draw command +0x20 (`0x0044D062`) | 35 zero rows (32, 94–96, 124–127, 186–191, 222–223, 237–255); row 0 ≈ identity | shading / fog LUT (INFERRED); index order not proven |
| `.TAB` | **identical in all 7 levels** (same MD5, dated 1996-09) | installed as rasteriser parameter `[0x0049873C]` (`0x00437A89`, `0x0043C37E`, `0x0043E618`, `0x0044137F`); the menu installs `baltazar\data\menu.tab` in the same slot (`0x0040C622`) | `TAB[r][0] = TAB[r][255] = r`, row 0 all zero | palette-independent remap (unknown) |

**`.TAB` is authored data, not the mip cache — CERTAIN.** The cache described in note 03 is
`.\levels\<hex>conv.tab`, 0x40000 B, named with a radix-16 conversion (`0x00448681 push 0x10;
call 0x478920`) and written by `0x00448620`. The level `.TAB` is 0x10000 B, read-only from
`LEVELS\%s%s.TAB` at `0x004190B6`, and no reference to its buffer `0x00563BE4` exists inside
`0x00448620`.

---

## 9. AIS — `CARS\TEST.AIS`

CRLF text, 7 blocks `car N` (1..7), each with the same 9 `key value` lines:
`acc, carebed, caresight, careother, maintain, aggr` (fractions .20–.95), `sight` (6–7),
`curveinflu, slopeinflu` (fractions). Fully parsed; every byte is part of a line (**CERTAIN**).

**Not used by this build — CERTAIN.** The string `CARS\TEST.AIS` (`0x00493CDC`) is referenced only by the
data pointer at `0x00494AD4`, and an exhaustive byte scan finds **zero** references to `0x00494AD4`.
None of the key names (`carebed`, `aggr`, …) exists anywhere in the EXE. The neighbouring
`ailists.txt` (`0x00493CCC`) is used only by the dead function `0x004160DE` (note 03).
Treat AIS as design data for the AI tuning a rebuild may want. AI parameters in the shipped game come
from elsewhere: a per-car table of 72-byte double records at `0x00493960`, indexed by car id in
`0x00414EB6..0x0041504D`.

---

## 10. Per-file results

All PASS, with 0 unaccounted bytes (full text in `_re/out/tracks/validation.txt`):

| level | MSH | PLC | TRI | SRF | POS | SHD | TAB | PAN |
|---|---|---|---|---|---|---|---|---|
| Austria | PASS | PASS | PASS (stale) | PASS | PASS | PASS | PASS | PASS |
| Brazil  | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Canada  | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Carib   | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Iceland | PASS | PASS | PASS (233 of 247 records, stale) | PASS | PASS | PASS | PASS | PASS |
| Japan   | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| USA     | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |

`CARS/Cars.msh` PASS, `CARS/Cars.plc` PASS, `baltazar/data/Menucar.msh` PASS,
`baltazar/data/Menucar.plc` PASS, `CARS/TEST.AIS` PASS.

Exports in `_re/out/tracks/`: `<Level>.obj/.mtl` for all 7 levels, `Cars.obj`, `Menucar.obj`
(object names `obj<i>_t<type>_g<group>_b<b6>_<b7>[_special]`, one `usemtl pageNN` per texture page),
and `<Level>_topdown.png` for all 7 levels. The PNGs show grey = static wireframe, red = drivable surface,
blue = special type ≥ 100, green = POS paths. Re-parse check: vertex/uv references all in range,
vt = 3·f in every OBJ.

---

## 11. Open questions

1. PLC `group` nibble, `b6`, `b7` — group ends up in world object +0x24; b6/b7 only in `[0x552E40]`.
   Not traced further.
2. Face `mode` → rasteriser routine mapping (jump table `0x0049C8E0`), and the surface-id table
   (physics, sound, skid behaviour).
3. TRI `b0`, the 8 doubles, and `mode` values other than 0/1/2; where the AI order list `[0x552F60]`
   comes from (`0x00417252`).
4. POS `a/b/c` fields and the playback rate.
5. SHD row/column semantics and TAB meaning. These need the `code`-section rasteriser.
6. Confirm +y-down and handedness from the projection code (not located; conclusion rests on car shape,
   the track-selection map match, and winding).
7. Car part-type consumer `0x0041D2B6..` (how bodies and wheels are assembled).
8. Special types 150–154 / 200 / 400–413: which are obstacles, animated props, sprites (Canada 409–413
   are single-triangle objects at y = +1123..1174, i.e. below ground if y is down) or triggers.
