# Ignition (`Ign_win.exe`, 1997) — the rasterizer primitive format

The decoder contract a GPU renderer is written against. Everything below is either read from the
disassembly of `Ign_win.exe` (PE32 i386, ImageBase `0x00400000`, MD5 `527bc475783319ecdd8adae1f97f6759`)
or measured against the real capture `ign_trace_prims.bin` (a Canada race, 800×600, hooked at
`0x0044F0E9`).

Tags: **[CERTAIN]** = read from disassembly or proven by byte-exact matching against shipped assets;
**[INFERRED]** = consistent with all evidence but not proven.

Supersedes `07_trace_harness_targets.md` §1.4 where they disagree; the corrections are listed in §10.
Nothing in the game directory was modified and the game was not launched.

---

## 0. Summary

* The rasterizer is a **register-convention asm dispatcher** at `0x0044F0E9` that walks a
  NULL-terminated array of record pointers and jumps through `[type*4 + 0x004ABE10]`.
* **Valid primitive types are `0x00`–`0x16` only.** `0x17`–`0x1F` in the same table are *module init
  callbacks*, not primitives. An out-of-range type is a wild jump; an unassigned type in range hits a
  stub that **aborts the whole display list**. **[CERTAIN]**
* There are **three different vertex strides** (8, 12 and 16 bytes) across the types. This is the main
  porting trap.
* Vertices arrive **already projected**, as 24.8 fixed screen coordinates. There is **no depth value in
  any record**; list order is the depth test (painter's, back to front).
* UVs are **8.8 fixed texel coordinates inside a 256×256 page**, `(u, v)` in that order, and texel
  addressing **wraps mod 256 for free**.
* A texture pointer is `slot_base + (page << 16)`, where the slot base comes from the loaded `.TEX`
  arena. Verified byte-exactly against `CANADA.MSH` and `Cars.msh`.
* Three distinct per-pixel write-back forms exist: opaque, **shade-LUT** (`LUT[colour*256 + light]`,
  resolved once per primitive) and **blend-LUT** (`LUT[src*256 + dst]`, which **reads the destination
  pixel every pixel**). The last one is what a GPU port has to design around.

---

## 1. The submit block (0x20 bytes) **[CERTAIN]**

Passed in `esi`; the dispatcher copies it into absolute globals at `0x0044F0EA`–`0x0044F177`.

| Off | → global | Meaning | Value in the capture |
|---|---|---|---|
| +0x00 | `0x0049C9E8` | pointer to the NULL-terminated array of record pointers | `0x10DDF874` |
| +0x04 | `0x0049C9F0` | **framebuffer base**, 8bpp palette indices | `0x00563DB0` |
| +0x08 | — | **never read** | `0x00563DB0` |
| +0x0C | `0x0049C9F8` | **64 KiB shade/colour-ramp LUT base**, indexed `(colour<<8) | light` — this is the **`.TAB`** slot | `0x0F8A7000` race / `0x03100000` menu |
| +0x10 / +0x14 | `0x0049C9FC` / `0x0049CA00` | clip minX / minY, **integer pixels, inclusive** | 0, 0 |
| +0x18 / +0x1C | `0x0049CA04` / `0x0049CA08` | clip maxX / maxY, inclusive | 799, 599 |

The dispatcher derives `<<8` (24.8) copies at `0x0049CA1C`–`0x0049CA38`; those are what the handlers
compare vertices against. Mode-set `0x0044F070` fills `0x0049CA3C` = pitch, `0x0049CA40` = height,
`0x0049CA44[600]` = `y*pitch`, `0x0049D3A8[15002]` = `65536/(n+1) − 1`.

**The +0x0C table is the level `.TAB`.** The submit block is the static structure at `0x00498730`, so
its +0x0C field *is* the global `0x0049873C` that `04_track_formats.md` §8 independently identified as
"installed as rasteriser parameter", written by `0x00437A89` / `0x0043C37E` / `0x0043E618` /
`0x0041137F` from `LEVELS\%s%s.TAB`, with the menu installing `baltazar\data\menu.tab` in the same slot
(`0x0040C622`). That also explains the menu value `0x03100000`. **[CERTAIN]**

Note the race value `0x0F8A7000` is **not** 64 KiB-aligned, so the pointer stored is the table base
**plus a row offset** (`+0x7000` = row 0x70) — i.e. a global light/fog level selected per frame.
**[INFERRED]**: distinguishing "TAB is at `0x0F8A7000`" from "TAB is at `0x0F8A0000` and row 0x70 is
selected" needs the run-time value of `[0x00563BE4]` (see §8).

---

## 2. The dispatch table at `0x004ABE10` **[CERTAIN]**

File offset `0xAA410`. `0x0044F18F: jmp [eax*4 + 0x004ABE10]`, with **no bounds check**.

| Type | Handler | What it is |
|---|---|---|
| 0x00 | `0x00454548` | clear clip rect (solid fill) |
| 0x01 | `0x00450630` | shaded point |
| 0x02 | `0x00453790` | blended point |
| 0x03–0x06 | `0x0044F19A` | **abort stub** |
| 0x07 | → `0x00455648` | sprite (expands to two type-0x12 triangles) |
| 0x08–0x0A | `0x0044F19A` | **abort stub** |
| 0x0B | `0x00451747` | flat line |
| 0x0C | `0x004549A3` | gouraud line, opaque |
| 0x0D | `0x0044F1FB` | gouraud line, blended |
| 0x0E | `0x0044F19A` | **abort stub** |
| 0x0F | `0x00450693` | **flat-shaded filled triangle** |
| 0x10 | `0x00452ECB` | gouraud triangle |
| 0x11 | → `0x00452800` | affine textured triangle, **opaque** |
| 0x12 | → `0x0044FF60` | affine textured triangle, **blended** |
| 0x13 | → `0x00454604` | **flat blended** triangle |
| 0x14 | → `0x004516F0` | perspective textured, opaque |
| 0x15 | → `0x004516F0` | perspective textured, blended (same thunk, branches on record+0) |
| 0x16 | → `0x00452050` | affine textured, **packed/paged UV encoding** |
| 0x17–0x1F | various | **module init callbacks — NOT primitives** |
| 0x20 | `0x00000000` | NULL, terminates the init list |

**The abort stub is not a no-op:**

```
0044f19a  popa
0044f19b  mov eax,0xffffffff
0044f1a0  ret                      ; returns -1 from the dispatcher
```

It **discards the rest of the display list**. The normal exit (`0x0044F196`) is `popa; xor eax,eax; ret`.
A port must reproduce "unknown opcode ⇒ the frame stops here", not "skip it".

**`0x17`–`0x1F` are init callbacks**, walked by the mode-set routine:

```
0044f0af  mov  edi,0x4abe6c          ; == 0x4ABE10 + 0x17*4
0044f0b4  cmp  dword [edi],0x0
0044f0bd  call dword [edi]           ; eax = pitch, ebx = height
0044f0c3  add  edi,4 ; jmp back
```

They end in `ret`, so dispatching one as a record type would return into the dispatcher's `pusha`
block. They install pitch-dependent constants, pre-scaled reciprocal bases, the lazily-allocated edge
buffers (shared guard `0x004B3CE4`) and, for `0x1F`, the x87 24-bit precision mode plus
`0x004B6318 = 4096.0f` used by the perspective subdivision.

---

## 3. Record layouts — every type, every field

Screen X/Y are **24.8 fixed** throughout. "min size" is the highest byte the handler actually reads,
rounded up; "stride" is what the emitter advances by where that is known.

### 3.1 The 8-byte-vertex triangle family — 0x11 / 0x12 / 0x13 / 0x16

| Off | 0x11 | 0x12 | 0x13 | 0x16 |
|---|---|---|---|---|
| +0x00 | type | type | type | type |
| +0x04 / +0x08 | v0 x,y | v0 | v0 | v0 |
| +0x0C / +0x10 | v1 x,y | v1 | v1 | v1 |
| +0x14 / +0x18 | v2 x,y | v2 | v2 | v2 |
| +0x1C | **UV array ptr** (6 dwords) | same | **u8 shade row** (= `dword[face+0x10]`, the first UV slot reused) | **UV array ptr**, packed encoding |
| +0x20 | **texture base ptr** | same | **`.SHD` page base** (= `[0x0063B5F0]`) | **texture base ptr** |
| +0x24 | — | **u32 blend LUT ptr** | — | — |
| **size** | **0x24** | **0x28** | **0x24** | **0x24** |

Emitter strides confirm the sizes: `0x0044E106: add edi,0x24` (0x11/0x16),
`0x0044CE72: add edi,0x28` (0x12). **[CERTAIN]**

Capture agreement, frame 0 (597 records): for every type-0x11 and type-0x13 record the dword at +0x24
is the *next* record's type word, and for every type-0x12 record it is a pointer and the next type word
is at +0x28. **[CERTAIN]**

All four share a prologue that min/maxes the three X's and Y-sorts the vertices, storing *dword
indices* (1, 3, 5) for top/middle/bottom. Index `i` ⇒ X at `[rec + i*4]`, Y at `[rec + i*4 + 4]`, UV
pair at `[uv + i*4 − 4]` and `[uv + i*4]` — which is what makes the UV array order `u0,v0,u1,v1,u2,v2`.
Sorted data is fanned into the parameter block at `0x004BA584` (+0x00..+0x14 sorted X/Y, +0x18..+0x2C
their U/V, +0x38 texture base), then `call 0x0064E000` computes gradients and `0x0064E200`/`0x0064E240`
(or the clipped `0x0064E270`/`0x0064E380`) walk the edges into two buffers:
`0x0063B518` = 0x968 = 600×4 + 8 (left: X only) and `0x0063B51C` = 0x1C28 = 600×12 + 8
(right: V 16.16, U 16.16, X). **[CERTAIN]**

**What differs:**

* **0x11 → `0x004559A8`, opaque.** Inner loop `0x004559C5`: `mov al,[ebx]; mov [edi+esi],al`.
* **0x12 → `0x004558C0`, blended.** Also does `0x00450087: mov edi,[edx+0x24]` → `[0x004BA580]`, and
  the inner loop `0x0045590B`–`0x00455928` is
  ```
  mov eax, ds:0x4ba580          ; 64 KiB blend LUT
  mov al,  byte [edi+esi*4]     ; AL = destination pixel
  mov ah,  byte [ebx]           ; AH = texel
  mov dh,  byte [eax]           ; LUT[texel*256 + dst]
  mov byte [edi+esi*4+4], dh
  ```
* **0x16 → same opaque emitter as 0x11**; the *only* difference is that each of the six UV loads is
  masked: `0x004521B0: mov edi,[edx+eax*4-4]; and edi,0x3FFF; shl edi,2`. So **0x16 is a UV encoding,
  not a shading mode** (§4.3).
* **0x13 → `0x00454604`**, private span routine `0x004547C8`. `0x0045460B` merges +0x1C and +0x20 into
  `0x004B6510` **once** per triangle and never steps it ⇒ **flat, not gouraud**. Per-pixel at
  `0x00454892`: `mov al,[edx]; mov dl,[esi+ecx]; mov [esi+ecx+1],al` ⇒ `dst = LUT[level*256 + dst]`.

### 3.2 The 12-byte-vertex family — 0x0B, 0x0C, 0x0D, 0x0F

The third dword of each vertex is written but **never read** (a Z that the rasterizer ignores).
For 0x0F the builder at `0x00440B1A` explicitly zeroes it (`0x00440B4A`, `0x00440B79`). **[CERTAIN]**

| Type | Size | Fields |
|---|---|---|
| 0x0B flat line | 0x24 | v0 `+0x04/+0x08`, v1 `+0x10/+0x14`; `+0x1C` u8 LUT page, `+0x21` u8 light; opaque |
| 0x0C gouraud line | 0x28 | as above + `+0x20`/`+0x24` colour0/colour1 24.8; `+0x1C` **u8 page** → `shadeLUT[page][c>>8]` |
| 0x0D gouraud line, blended | 0x28 | same layout, but `+0x1C` is a **full dword blend-LUT pointer** ⇒ `dst = LUT[level][dst]` |
| **0x0F flat triangle** | **0x30** | v0 `+0x04/+0x08`, v1 `+0x10/+0x14`, v2 `+0x1C/+0x20`; **`+0x28` u8 colour**, **`+0x2D` u8 light** |

Type 0x0F's colour is resolved **once** (`0x004509E4`–`0x004509FE`: `shadeLUT[colour*256 + light]`,
byte-replicated ×4 into `0x004AFDC8`) — genuinely flat. It rejects `dy > 15000` and clamps X to
±32000 because the span X compares are **16-bit** (`cmp si, word [0x0049C9FC]`). **[CERTAIN]**

> Note the trap at +0x1C: in 0x0C it is a *byte page*, in 0x0D a *dword pointer*.

Capture agreement: the three type-0x0F records in frames 1–2 are exactly
`0F | (x,y,0)×3 | colour | 0`, with colour `0xC4` and `0xD7` (HUD/system palette range) and light 0,
and the next record's type word sits at +0x30. **[CERTAIN]**

### 3.3 The 16-byte-vertex family — 0x10 gouraud triangle · size 0x38 **[CERTAIN]**

Vertices at `+0x04 + 0x10*j`: `+0x00` X, `+0x04` Y, `+0x08` unused, `+0x0C` **intensity** (`shl 8` to
24.8, interpolated across the triangle). `+0x34` dword used as a **byte** = shade page. Per-pixel LUT
lookup then opaque store. Self-contained; does not use `0x004BA584` or the `code`-section routines.

### 3.4 Perspective textured — 0x14 (0x44 B) / 0x15 (0x48 B) **[CERTAIN]**

`+0x04` texture ptr · **12-byte vertices at +0x08** (X,Y,Z ×3) · **8-byte UVs at +0x2C** ·
`+0x44` blend LUT ptr (**0x15 only**). The shared thunk re-reads record+0:

```
4516fe  cmp dword [ecx],0x15
451703  mov dword ds:0x4b63cc,1        ; blend flag
45170d  mov eax,[ecx+0x44]             ; blend LUT, 0x15 only
```

Span loop `0x00453CC8` (0x14, opaque, 4 texels packed per dword store) or `0x00454104` (0x15, blended).
Perspective-correct but **re-divided only every 16 pixels** (`0x004B6318 = 4096.0f`), affine between.
Neither type appears in the capture — the PERSP. POLY option is off by default.

### 3.5 Sprite — type 0x07, record 0x14 B (0x44 B allocated) **[CERTAIN]**

`+0x04` → 0x20-byte descriptor · `+0x08` → 0x10-byte 2×2 matrix (16.16) · `+0x0C`/`+0x10` screen X/Y
translate (24.8). Descriptor: `+0x00/+0x04` pivot U,V · `+0x08/+0x0C` uMin,vMin · `+0x10/+0x14`
uMax,vMax · `+0x18` texture ptr · `+0x1C` blend LUT ptr.

It is **not a blitter**: it transforms four corners and emits **two type-0x12 triangles** via
`call 0x0044FF60` (`0x0045583D`, `0x004558B7`) through scratch at `0x004BA468`. There is **no colour
key** — sprite transparency is entirely the blend LUT.

The record is **self-referential**: the emitter (`0x0044D0F0`) writes `+0x04 = record+0x14` and
`+0x08 = record+0x34`, so the "descriptor" and the "matrix" are embedded in the 0x44-byte record
itself rather than being separate objects. Its texture pointer is built from **`[face+0x18]`**, not
`[face+0x28]` as for the triangle types. **[CERTAIN]**

### 3.6 Points and clear

* **0x00 clear** (0x0C B): `+0x04` u8 LUT page, `+0x08` colour 24.8; fills the clip rect with
  `LUT[page][colour>>8]`.
* **0x01 shaded point** (0x18 B): `+0x04` X, `+0x08` Y, `+0x10` u8 colour, `+0x15` u8 light.
* **0x02 blended point** (0x14 B): `+0x04` X, `+0x08` Y, `+0x0C` u8 src, `+0x10` blend LUT ptr.

---

## 4. The UV format

### 4.1 The rule **[CERTAIN]**

Record `+0x1C` points at **six dwords** laid out `u0, v0, u1, v1, u2, v2`. Each is **8.8 fixed point in
texel units of a 256×256 page** — equivalently 16.16 normalised over the page, which is the same
number. They are **not** normalised to [0,1) in any other sense and are **not** scaled by a page size.

Texel address in the span loop is formed by keeping `bl` = integer U and `bh` = integer V against a
64 KiB-aligned base:

> **address = (texbase & 0xFFFF0000) | (V_int << 8) | U_int**

so **V is the row, U is the column, and addressing wraps mod 256 in both axes for free**. A GPU port
should use `wrap` addressing, not `clamp`. **[CERTAIN]**

### 4.2 The worked example from the capture

Record 0 of frame 0, six dwords at its UV pointer:

```
000001EB 0000806B 000001EB 00008C00 00003E00 00008C00
```

⇒ `(u0,v0) = (1.92, 128.42)`, `(u1,v1) = (1.92, 140.00)`, `(u2,v2) = (62.00, 140.00)` texels.

Read as 16.16 these would be 0.007/0.5/… of a page, i.e. the identical texels — the two readings differ
only in naming. Across all 585 textured records in frame 0 the components span raw `0x5C`–`0xFE00`
= **0.36–254.00 texels**, never ≥ 0x10000 and never negative, exactly the 8.8 range that
`04_track_formats.md` measured in the mesh files. **[CERTAIN]**

The `(u,v)` order (rather than `(v,u)`) is settled independently by rendering: drawing the capture with
u and v swapped produces sheared garbage, and with the order as stored produces the correct scene
(`_re/out/prims/frame0_tex_800x600_uvswap.png` vs `frame0_tex_800x600.png`).

### 4.3 Type 0x16's packed variant **[CERTAIN]**

The 0x16 handler masks every UV load: `and 0x3FFF; shl 2`. So a 0x16 UV dword carries a **14-bit UV in
its low bits** (which after `<<2` lands in the same 8.8 units) **plus a page index in the upper bits** —
the emitter side derives a texture base from `sar …,0xE` of the same dwords (`0x0044EF49`,
`0x0044EF51`). Naming it an atlas/paged mode is **[INFERRED]**; the masking and shift are CERTAIN.

No type-0x16 record appears in this capture.

---

## 5. Texture identification

### 5.1 The rule **[CERTAIN]**

```
texture_pointer = tex_slot_base + (page << 16)
```

because the emitter computes `texbase = *([0x004CDC28] − 0x14) + dword[face + 0x28]`, and a mesh face's
dword at +0x28 **is** `page << 16`. (There is **no** global at `0x004CDC14` — an exhaustive byte scan
finds zero hits for that address. `[0x004CDC28]` holds a pointer `P` into the *middle* of a per-object
mip table: `P−0x14` is mip 0, `P−0x10…P−0x04` are mips 1–4. Its single writer is `0x0044C254`, fed from
`[0x0063C5C8][object[+0x08]]`, where `object[+0x08]` is a texture **slot index** 0–8.)  Every `.TEX`
slot is rounded up to a 64 KiB multiple (the u16 at +0x28 is always zero and the u16 at +0x2A is the page — verified
over every face of `CANADA.MSH` and `Cars.msh`). Every `.TEX` slot is rounded up to a 64 KiB multiple
by the loader (`0x00419D73` etc.), and a page is 256×256 bytes of palette indices.

### 5.2 Verification against the capture **[CERTAIN]**

For each captured record I matched its 24 captured UV bytes back into the shipped mesh files, which
recovers the run-time mesh base and hence the exact source face:

| Mesh | run-time base | records matched | `face.mode` → `rec.type` | slot base implied by `tex − (page<<16)` |
|---|---|---|---|---|
| `CANADA.MSH` | `0x0F8B70AC` | 185 | 17→0x11 (181), 18→0x12 (4) | `0x0F930000` on **all 185** |
| `CARS/Cars.msh` | `0x0F90F300` | 212 | 17→0x11 (212) | `0x0FA30000` on **all 212** |

Zero mismatches. This proves the page rule and gives the two slot bases.

It does **not** prove "record type = mesh face mode" in general, even though it holds for both modes
seen here (17→0x11, 18→0x12). The face-mode table at `0x0049C8E0` shows the mapping is many-to-many:
mode 18, 19, 22 and 23 all emit **0x15 when the perspective branch is taken and 0x12 otherwise**, mode
20 emits 0x13, and mode 21 emits 0x14 or 0x11. Two of the table's slots (17 and 21) are even
**rewritten at run time** by `0x0044B480` according to the perspective and MIP MAPPING options. The
capture only ever shows the non-perspective branch because PERSP. POLY defaults off. **[CERTAIN]** `0x0FA30000 − 0x0F930000 = 0x100000` = `roundup(CANADA.TEX)` exactly.

The remaining 188 textured records point their UV arrays into **EXE BSS** (`0x00553878`–`0x0055F5C0`,
all with car page 4) rather than into `Cars.msh`: these are per-car **run-time mesh copies** (the
damage/deformation copies), whose UV bytes still match `Cars.msh` content at several distinct bases.
So a port must not assume the UV pointer aims at file-backed data. **[CERTAIN]** for the addresses,
**[INFERRED]** that deformation is the reason.

### 5.3 The slot table for this capture

There are **nine** slots, selected by the drawn object's `object[+0x08]`. Crucially the **slot index
order is not the address order** — `DIVSPR` is index 2 but is allocated last but one: **[CERTAIN]**

| idx | base global | file | this capture |
|---|---|---|---|
| 0 | `0x005530F0` | `LEVELS\<lvl>\<lvl>.TEX` (arena base) | `0x0F930000` **verified**, 16 pages |
| 1 | `0x005530F4` | `CARS.TEX` | `0x0FA30000` **verified**, 7-page slot (file = 6 pages + 64 B) |
| 2 | `0x005530F8` | `GENERAL\DIVSPR.TEX` | unused |
| 3 | `0x005530FC` | `GENERAL\LIGHT.TEX` | unused |
| 4 | `0x00553100` | `GENERAL\SMOKE.TEX` | unused |
| 5 | `0x00553104` | `GENERAL\DARKSMOK.TEX` | unused |
| 6 | `0x00553108` | `GENERAL\BOOM.TEX` | unused |
| 7 | `0x0055310C` | `GENERAL\EXSMOKE.TEX` | unused |
| 8 | `0x00553110` | spare 64 KiB page | unused |

Address order is `F0 < F4 < FC < 100 < 104 < 108 < 10C < F8 < 110`, so a pointer must be resolved by
**taking the largest slot base ≤ the pointer in ascending address order**, not by index. Two non-arena
bases must be excluded first: `[0x0063B5F0]` (the level `.SHD` page, which type 0x13 uses) and
`[0x0063C5B4]` (a synthetic flat-colour page built by `0x00402940`).

`0x0FA30000 − 0x0F930000 = 0x100000` = `roundup(CANADA.TEX)` exactly, which is what cross-validates the
two verified bases. `[0x0054F974]` holds the level slot's rounded size and is read at `0x0041B561` as
`>>8` = a count of 256-pixel rows — independent confirmation that a `.TEX` is 256 px wide and a page is
`256×256 = 0x10000`. **[CERTAIN]**

Pages actually used by frame 0 — level 0,1,2,4,6,11,12,13 and cars 0,1,2,3,4 — all exist in the
shipped files (Canada has 16 pages, Cars 6). **[CERTAIN]**

### 5.4 What the pointers do *not* resolve to

The type-0x12 records carry `0x0FBC0000` at +0x24. That is **768 KiB past the end of the whole TEX
arena**, so it is *not* a texture page — consistent with the disassembly, where +0x24 is a **blend LUT**
pointer. Likewise the type-0x13 table pointer `0x0F890000` and the submit block's `0x0F8A7000` are
table allocations, not textures. Resolving *which* table each is needs a run-time dump (§8).

---

## 6. Shading and blending — what a GPU must reproduce

Three write-back forms exist, and every primitive type selects exactly one. **[CERTAIN]**

| Form | Per-pixel | Used by |
|---|---|---|
| **opaque** | `dst = texel` (or a constant) | 0x11, 0x16, 0x14, 0x0B |
| **shade LUT** | `dst = LUT[colour*256 + light]` | 0x00, 0x01, 0x0C, **0x0F**, 0x10 |
| **blend LUT** | `dst = LUT[src*256 + dst]` ← **reads the framebuffer** | 0x02, 0x0D, **0x12**, **0x13**, 0x15, 0x07 |

### 6.1 The shade LUT (submit block +0x0C → `[0x0049C9F8]`)

A 64 KiB table indexed `(colour << 8) | light`, i.e. **colour selects the row, light the column**.
For the flat types (0x00, 0x0F, 0x13's sibling forms) it is evaluated **once per primitive**, not per
pixel — only the gouraud types (0x0C, 0x10) interpolate the *light* term across the primitive, in 24.8.
So "gouraud" here means interpolating an index into a colour ramp, not interpolating RGB. **[CERTAIN]**

Two consequences for a GPU port: the ramp is an arbitrary 256×256 byte table (not a multiply), and the
interpolated quantity is an 8-bit index, so it must be interpolated and then *looked up*, never blended
in RGB space.

**Type 0x11 carries no shade field at all** — its record is exactly 0x24 bytes with every dword
accounted for — so ordinary textured world geometry is written **raw, unshaded**. This is corroborated
by the re-render: drawing raw texels reproduces the scene's colours correctly (§7).

### 6.2 Blend LUTs and `.PAN`

The blend form indexes a 64 KiB table with `(source << 8) | destination`, which is exactly the `.PAN`
indexing already confirmed at `0x00438359`, and it **reads the destination pixel**. Per-primitive
translucency therefore *does* exist — contradicting `07` §1.4's original claim — and it arrives as a
**pointer in the record** (0x12 +0x24, 0x13 +0x20, 0x15 +0x44, sprite descriptor +0x1C), not as a flag.

Structure of `Canada.pan` (all 7 levels are similar): rows 16–159 are **all zero**, rows 160–255 are
**constant** (mostly `row = row`, i.e. opaque, with a few remaps), and rows 0–15 are genuine
destination-dependent ramps. Since level textures only use indices 0–159 and HUD/system art uses
160–255, the source index of a blend is meaningful in the 0–15 band (translucency levels) and the
160–255 band (opaque system colours). **[CERTAIN]** for the table contents; **[INFERRED]** for the
interpretation.

### 6.3 The requirement list for a GPU implementation

1. The render target must stay an **8-bit palette-index** buffer, because the blend form looks up
   `(src, dst)` as *indices*. Converting to RGB before blending changes the result.
2. Blending needs **destination read-back** — a framebuffer-fetch extension, a render-target copy, or a
   two-pass split. It cannot be expressed as any fixed-function blend mode.
3. Texture sampling is **nearest, 8bpp indexed, wrap mod 256**, from 256×256 pages.
4. Shading is a **table lookup on an interpolated index**, resolved once per primitive except for the
   two gouraud types.
5. **Order is the depth test** — no depth buffer, no per-primitive depth (§7.2).
6. Unknown opcode ⇒ **abandon the rest of the list** (§2).

### 6.4 Emitter-side rules a port must also honour **[CERTAIN]**

These happen *before* a record exists, so they are invisible in the capture but decide what is in it:

* **Backface cull** — 2D cross product of the screen-space edges, each term pre-shifted `sar 4` to avoid
  overflow (`0x0044C4A2`–`0x0044C4D3`), XORed with `[0x0049C9A8]` (a sign flip, 0 in the shipped file —
  presumably set for mirrored views), then `test ebp,ebp; jle` ⇒ reject (`0x0044C4FB`).
* **Near reject** — `cmp edi,0x258; jle` ⇒ reject (`0x0044C4EF`): drop the face when `z0+z1+z2 ≤ 600`.
* **Clipping: there is none — only rejection.** A fast path checks vertex 0 against the clip rect
  (`0x0044C30E`–`0x0044C344`); otherwise a bounding-box trivial reject runs against the 24.8 clip
  globals. Partially off-screen triangles are emitted **with out-of-range coordinates** and clipped by
  the rasterizer's edge walkers. The capture confirms it: frame 0's vertices span x −305…916 and
  y −82…765 against an 800×600 clip rect.
* **Depth bucketing** — `depth = (z0+z1+z2) >> 4`, biased by the object material word `[obj+0x1E]`
  (subtract 0x54 when it is 0xD2, else 0x5C, when ≥ 0x64; type 0x13 subtracts 0x4C), clamped to
  0…0x176F, into 0x1770 buckets at `[0x0063B5E8]`.
* **Within one bucket the push is LIFO** (`0x0044C5BC`), so the flattened list runs *reverse emission
  order* inside a bucket. A port that re-sorts primitives must preserve this, not just the bucket order.
* **Mip selection** exists for the perspective/mip modes: `0x0044D7A7`–`0x0044D8F9` compares screen-space
  against texel-space triangle area and picks mip −5/−4/−3 from the table `[0x004CDC28 + mip*4]`.
  Mips 1–4 are separately `calloc`'d (`0x004473C4`) and are **not** in the TEX arena.

---

## 7. The re-render

`_re/tools/prim_render.py` (beside the existing flat-colour `trace_prims.py`) redraws a captured frame
with the real `.TEX` pages and the level `.COL` palette, at any scale.

### 7.1 Outputs

| File | What |
|---|---|
| `_re/out/prims/frame0_tex_800x600.png` | frame 0 at native 800×600 |
| `_re/out/prims/frame0_tex_2400x1800.png` | frame 0 at 3× — the whole point: the original is capped by a 488 000-byte static framebuffer, this path is not |
| `_re/out/prims/frame0_tex_800x600_uvswap.png` | control: u/v swapped (garbage), which is what proves the UV order |
| `_re/out/prims/frame0_tex_800x600_reverse.png` | control: list order reversed |
| `_re/out/prims/frame{0,1,2}_tex_*_indices.npy` | the raw 8-bit index buffers, for future pixel-level comparison |

### 7.2 Honest assessment

**Yes — it is a coherent 3D scene of the Canada starting grid.** At 2400×1800 the frame reads as a
top-down view of six clearly identifiable vehicles (yellow pickup, red-cross ambulance, school bus,
police car, dump truck, red sports car) in two columns of three, standing on a dirt road with painted
verges, wheat fields either side with correct row texturing, rock faces top-right, and the
black-and-white start/finish banner with the red maple leaf across the road — which matches the Canada
track art in `_re/shot_fs2-web.jpg`. Palette, texture pages and UV orientation are all right; nothing is
sheared, mirrored or mis-paged.

Caveats, stated honestly:

* **137 of 597 triangles do not appear at 800×600** (45 at 2400×1800). None is off-screen or
  degenerate: they are **sub-pixel** (52 have area < 0.5 px², the median triangle is 13.8 px²) and my
  point-in-triangle sampler drops primitives that cover no pixel centre. The game's edge-walker would
  still emit a thin span for some of them. This is a limitation of the *checking tool*, not of the
  format, but it means the tool is not yet pixel-exact.
* **The flat blended triangles (type 0x13, the 12 car shadows) are resolved.** The emitter writes
  `record+0x20 = [0x0063B5F0]` (`0x0044D062`), which is the level **`.SHD`** page, and
  `record+0x1C = dword[face+0x10]` — the first "texture coord" slot reused as a shade row. So
  `0x0F890000` **is** Canada's `.SHD` buffer and the shadow is `dst = SHD[0x28*256 + dst]`. Row 0x28 of
  `canada.shd` is a very dark ramp, so the shadow darkens the ground rather than replacing it, which is
  what the game shows. Rendering 0x28 as a raw palette index instead gives an obviously wrong khaki
  block, so that reading is excluded. The committed images use the `.SHD` path.
* Type 0x0F is drawn with its raw colour index rather than `shadeLUT[colour][light]`, because the LUT
  contents are not captured.
* No perspective (0x14/0x15), sprite (0x07), line or point record appears in this capture, so those
  paths of the spec are code-derived only and **untested against data**.

### 7.3 Order dependence, verified rather than asserted

Rendering the same list backwards (`--reverse`) changes only **1.6 % of pixels** — but those pixels are
the entire foreground: all six cars, all twelve shadows and the start banner vanish under the ground
polygons. Painter's order is load-bearing and must be preserved. **[CERTAIN]**

---

## 8. What the current harness does not capture, and why it is needed

| # | Field | Where it lives | Why a faithful renderer needs it |
|---|---|---|---|
| 1 | **The texture arena contents** | `[0x005530F0]` base, slot sizes `[0x0054F974]`, `[0x005530F4]`, `[0x005530FC]`, `[0x00553100]`, `[0x00553104]` | Pages are resolved today by *guessing* a base from matched mesh bytes. Dumping the slot base globals once per level makes pointer → (file, page) exact instead of inferred, and is the only way to resolve GENERAL/effect textures. |
| 2 | **The palette at draw time** | `[0x0054F900] + 8` (level COL), plus the live `PALETTEENTRY` array set by `0x00456C40` | The game fades the palette (lightning, race start, menus). A frame captured mid-fade cannot be coloured correctly from the on-disk `.COL`. |
| 3 | **The three 64 KiB LUTs, with their base addresses** | `.PAN` `[0x005DFE64]`, `.SHD` `[0x00527F74]`, `.TAB` `[0x00563BE4]` | The `.SHD` and `.TAB` identities are now settled from the emitter side, but the **blend** LUTs are not: type 0x12's `+0x24` (`0x0FBC0000` here), type 0x15's `+0x44` and the sprite descriptor's `+0x1C` are still raw run-time addresses, and `[0x00563BE4]` is needed to decide whether the submit block stores `.TAB` or `.TAB + row*256`. Dump the three base pointers **and** 64 KiB of contents each. |
| 4 | **Per-primitive fields beyond 0x24 bytes** | the record itself | The harness copies a fixed 0x48-byte window, which is fine for 0x11 but **overruns into the next record** and would truncate 0x14 (0x44) / 0x15 (0x48) / 0x10 (0x38) / 0x0F (0x30). Copy **per-type sizes** from §3. |
| 5 | **The UV pointer dereference is unguarded** | record +0x1C | For type 0x13 that field is a *blend level* (`0x28`), not a pointer; the harness dereferenced it anyway and recorded zeros. Dereference +0x1C only for 0x11/0x12/0x16, and capture the pointed-at 24 bytes only then. |
| 6 | **The frame's own framebuffer** | `0x00563DB0`, `width*height` bytes, after the list completes | Without it there is no ground truth. With it, the re-render can be diffed pixel-for-pixel at 800×600, which is the only way to prove the decoder rather than eyeball it. Capture as raw indices, before any palette conversion. |
| 7 | **The sprite and perspective paths** | any frame containing type 0x07 / 0x14 / 0x15 | Capture a frame with PERSP. POLY enabled and one with smoke/flare effects; §3.4 and §3.5 are currently untested against real data. |
| 8 | **The submit block's +0x08** | second buffer pointer | Never read by the rasterizer, but knowing what the caller intends it for would close an open question. |

---

## 9. Open questions

1. Which table the **blend**-LUT pointers name (type 0x12 `+0x24` = `0x0FBC0000`, type 0x15 `+0x44`, the
   sprite descriptor `+0x1C`). The shade path is now resolved — 0x13 uses `.SHD`, the submit block uses
   `.TAB` — but the blend tables are still raw run-time addresses (§8 item 3).
2. Whether the submit block's `+0x0C` stores `.TAB` itself or `.TAB + row*256` (§1).
3. Type 0x16's exact base arithmetic. The emitter builds it *from the UVs*:
   `a = u0>>14, b = v0>>14`, `base = miptable[a] + (((page<<16) + (b<<14)) << 2)`, taken only when
   mip = −5 and `[P] ≠ [P−0x14]` — a 4× detail-texture path. Mechanism **[CERTAIN]**, purpose
   **[INFERRED]**.
4. The `.PAN` overlay at `0x00438359` — what its source buffer `[ebx+edx+0x190]` is, and whether any
   *primitive* path ever reaches `.PAN` (as opposed to `.SHD`).

---

## 10. Corrections this note makes to `07_trace_harness_targets.md`

1. **Type 0x0F is a flat-shaded filled triangle (0x30 bytes)**, not a state/clear/control record.
2. **Type 0x13 is flat *blended*, not flat/gouraud** — its `+0x1C` is a blend level and `+0x20` a blend
   LUT pointer; nothing is interpolated.
3. **Per-primitive translucency exists.** `07` §1.4 says "there is no translucency field in any
   primitive record"; in fact four record types carry a blend-LUT pointer, and the sprite path relies on
   it for transparency. (`07`'s own appended Correction already established that `.PAN` is a blend
   table; this note shows how primitives reach one.)
4. **Only 0x00–0x16 are primitive types.** `0x17`–`0x1F` in the same table are init callbacks, and the
   unassigned in-range types abort the display list rather than skipping the record.
5. **Type 0x12's `+0x24` is a blend LUT, not a "secondary texture/stride"** — the captured value lies far
   outside the texture arena.
6. **Three vertex strides exist (8/12/16 bytes)**, so "the vertex block shared by 0x11/0x12/0x13/0x16"
   does not generalise to the other triangle types.
7. **There is no global at `0x004CDC14`.** `07` §1.4 describes the texture base as
   `[0x4CDC28-0x14] + [face+0x28]`, which reads like a global one dword below `0x004CDC28`. In fact
   `[0x004CDC28]` holds a *pointer* `P` into the middle of a per-object mip table and the base is
   `*(P − 0x14)` = mip 0. An exhaustive byte scan finds zero references to the address `0x004CDC14`.
8. **Record type is not the mesh face `mode`.** It happens to match for modes 17 and 18, which is all
   this capture contains, but four modes map to the same pair of types and two table slots are
   rewritten at run time by the perspective and MIP MAPPING options (§5.2).
9. **The shade tables are identified**: type 0x13's `+0x20` is the level `.SHD` page (`[0x0063B5F0]`)
   and the submit block's `+0x0C` is the `.TAB` slot (`0x0049873C`). `07` left both unnamed.


---

## Ground-truth comparison against the game's own framebuffer (2026-09-16)

The harness now records the game's 8-bit framebuffer (`0x00563DB0`) alongside
the primitive list, so the decode can be measured instead of eyeballed. Note
the hook runs on submit *entry*, so the framebuffer captured with frame N holds
frame N-1's result: prim frame N pairs with framebuffer N+1.

Captured during a real race on Canada, 800x600, 611 primitives in frame 0:

| Measure | Value |
|---|---|
| Exact palette-index match, whole frame | **58.82%** |
| Exact match excluding HUD regions | 61.82% |
| Mismatches explained by a <= 1 pixel shift | **83.8%** |
| Mismatching pixels inside HUD regions | 45,963 |
| Records textured / flat / unresolved | 459 / 12 / **0** |

**Interpretation.** Geometry, texture pages, UV orientation, palette and draw
order are correct: the scene matches structurally and every texture pointer
resolves. The residual is per-pixel, concentrated on high-frequency textures
(the wheat field, the rock face) and flat areas match - the signature of a
sub-texel sampling difference against the game's fixed-point edge walker, not a
structural error. The HUD can never match from this list: it arrives in later
submits carrying 1-2 primitives each.

**Texture slot bases must be captured, not inferred.** They are allocated at
run time and differed between two runs of the same level (`0x0F930000` vs
`0x0F5E0000`). A renderer using bases hardcoded from an earlier capture
resolves nothing and outputs an empty frame; this is exactly what happened
before `ign_trace_ctx.bin` was wired in.

**Pixel-exactness is a verification goal, not a shipping one.** A GPU renderer
at a higher resolution cannot be pixel-identical to an 800x600 software
rasterizer by definition. Closing the remaining gap means reproducing the
fixed-point scan converter, which belongs with decompiling the rasterizer
rather than with the high-resolution work.

### Refuted: the residual is not a half-pixel sampling offset

Hypothesis: the game walks spans in 24.8 fixed point and truncates, so it
effectively samples at the pixel origin while `prim_render.py` samples at the
pixel centre, and removing that half-pixel would close the gap.

Measured against the captured framebuffer (frame 0, Canada, 800x600):

| Sample point inside the pixel | Exact index match |
|---|---|
| 0.5, pixel centre (current) | **58.82%** |
| 0.0, pixel origin | 36.91% |

Sampling at the origin is markedly *worse*, so the convention is not the cause.
`prim_render.py` keeps 0.5; the offset is now settable via the
`IGN_SAMPLE_OFFSET` environment variable for further experiments.

Texel rounding is **also not** the cause: `prim_render.py` already floors,
`np.floor(uu) & 0xFF`, which is the same truncation the game performs. Both of
the cheap global-knob explanations are therefore ruled out by measurement.

What remains is the expensive one: the game accumulates u and v as fixed-point
increments *along each span*, truncating at every step, while the tool
evaluates barycentric coordinates in float64 independently per pixel. The
tool's value is exact; the game's drifts. Accumulated drift of a single texel
is invisible on flat surfaces and obvious on a 256-texel striped wheat field -
which is precisely the distribution observed. No global offset or rounding mode
can reproduce it; only re-implementing the span walker can, and that is part of
decompiling the rasterizer rather than of the high-resolution work.

**Stopping point.** The decode is confirmed structurally: every texture pointer
resolves, pages and palette are right, draw order is verified by reversal, and
83.8% of residual pixels are within one pixel. Chasing the rest buys fidelity
to an 800x600 software rasterizer that a higher-resolution GPU renderer cannot
be identical to anyway.
