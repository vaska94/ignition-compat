# Golden-trace harness — exact detour targets and data contracts

Answers the four follow-up questions: the primitive submission contract, the per-step simulation
state, the real resolution ceiling, and hook safety for every proposed VA.
Companion: `_re/notes/06_subsystem_map.md` (subsystem map), `_re/out/functions.csv`.
Claims are **[CERTAIN]** (read from disassembly / relocations) or **[INFERRED]**.
All hook verdicts below come from `_re/tools/hookspec.py`; every one returned **exit 0 = SAFE**.

---

## 1. Primitive submission contract (render3d → rasterizer)

### 1.1 Entry points

Two identical cdecl wrappers, `void submit(void *block)` — argument at `[ebp+8]` loaded into `esi`,
then `call 0x0044F0E9`. **[CERTAIN]** (`0x0043803A`, `0x0040C72A`).

| VA | Convention | Use | Callers |
|---|---|---|---|
| `0x00438030` | cdecl, 1 arg | race view + HUD/overlay | 7 sites (`0x00436990`, `0x0043E6D0`, `0x0043EF30`, `0x00440840`, `0x00445E20`, `0x00445EB0`) |
| `0x0040C720` | cdecl, 1 arg | menu car | 2 sites |
| `0x0044F0E9` | **register**: `pushal`, `esi` = block | the dispatcher itself | the two wrappers |
| `0x0044F070` | **register**: `eax` = width, `ebx` = height | one-time raster init, **not** per draw | `0x00402BEE`, `0x0043E6AE` |

### 1.2 The submit block — a single static 0x20-byte structure at `0x00498730`

Both wrappers pass the *same* block. The dispatcher copies it into absolute globals at
`0x0044F0EA–0x0044F177`. **[CERTAIN]**

| Off | Copied to | Meaning |
|---|---|---|
| +0x00 | `0x0049C9E8` | **primitive list**: pointer to a NULL-terminated array of record pointers |
| +0x04 | `0x0049C9F0` | **framebuffer base** (`0x00453821`: `add eax,[0x49C9F0]` after `row*[0x49CA3C]`) |
| +0x08 | — | second buffer pointer (race passes `0x00563DB0`, at `0x00437A83`) |
| +0x0C | `0x0049C9F8` | **shade / light LUT base** (`0x00451749`: `bl=[rec+0x21]`, `bh=[rec+0x1C]`, `+[0x49C9F8]`, load byte) |
| +0x10 / +0x14 | `0x0049C9FC` / `0x0049CA00` (24.8 copies `0x0049CA1C` / `0x0049CA20`) | clip xmin, ymin |
| +0x18 / +0x1C | `0x0049CA04` / `0x0049CA08` (24.8 copies `0x0049CA34` / `0x0049CA38`) | clip xmax, ymax (file image `0x13F`/`0xC7` = 319/199) |

Clip bounds arrive as **integer pixels**; the dispatcher derives 24.8 copies with `shl 8`, and those
are what the handlers compare vertices against.

### 1.3 The list and the dispatch loop

`0x0044F17D`: `esi = [edi]; edi += 4; if (esi == 0) return 0; jmp [[esi]*4 + 0x004ABE10]`.
**Word 0 of every record is the type = handler selector.** Handlers receive the record pointer in
`edx`/`esi` (`0x0044FF60` reads `[esp+8]` after `push ebp`). **[CERTAIN]**

* The list is a **contiguous, NULL-terminated array of record pointers**, head at `[0x0063C5BC]`,
  rebuilt every frame by the bucket flatten; scratch cursor `[0x0063C600]` (HUD builders write one
  record plus a NULL at `0x00445E2B–0x00445E35`). Primitive count is also at `[0x0063C5F0]+0x68`.
* **A detour therefore captures a whole frame with one pointer and a walk to NULL** — no count needed.

### 1.4 Record layouts

Vertex block, shared by types 0x11 / 0x12 / 0x13 / 0x16 — confirmed from **both** sides: emitter
`0x0044C51F–0x0044C54A` writes, handler `0x0044FF8F–0x00450019` reads. **[CERTAIN]**

| Off | Field | Format |
|---|---|---|
| +0x00 | type / handler selector | dword |
| +0x04 / +0x08 | v0 x, y | **24.8 fixed-point screen coords** |
| +0x0C / +0x10 | v1 x, y | 24.8 |
| +0x14 / +0x18 | v2 x, y | 24.8 |
| +0x1C | pointer to UV array — 3 pairs at uv+0/+4, +8/+0xC, +0x10/+0x14; emitter sets it to `face+0x10` | ptr |
| +0x20 | **texture base pointer** (→ `0x004BA5BC`, texel base in the span loop `0x004558CB`) = `[0x4CDC28-0x14] + [face+0x28]` | ptr |
| +0x24 | secondary texture/stride (→ `0x004BA580`, read at `0x0045590B`) — type 0x12 only | ptr |

Record size is 0x24 for type 0x11 (`add ebp,0x24` at `0x0044C55F`); type 0x12 uses +0x24 so it is
≥ 0x28 **[INFERRED]**. Other types:

| Type | Handler | Notes |
|---|---|---|
| 0x13 flat/gouraud | `0x00454604` | same vertex layout; `+0x1C` **byte** = shade index, `+0x20` dword = colour, packed into `0x004B6510` |
| 0x14 / 0x15 perspective | `0x004516F0` | `[rec+4]` → `0x004B63C4`; **0x15 only** `[rec+0x44]` → `0x004B63C8`, so 0x15 is ≥ 0x48 bytes |
| 7 sprite | `0x00455648` | `+4` = ptr to 8-dword corner/UV block, `+8` = ptr to 4-dword matrix, `+0xC`/`+0x10` = per-step increments; UVs stepped in **16.16** (`imul` / `rol eax,16`) |

**Depth: there is no per-vertex z or w in the record.** The emitter sums the three transformed
vertices' third dword, `sar 4`, biases by the material word `[obj+0x1E]` (0x64/0xD2 → −0x54/−0x5C),
clamps to 0…0x176F and uses that as the **depth-bucket index** (`0x0044C4E5–0x0044C5A0`). Ordering is
bucket-order painter's algorithm — a GPU port must either keep that ordering or synthesise a real
depth value. **[CERTAIN]**

**Shading and translucency.** Lighting is the **SHD index** at record +0x1C (type 0x13) resolved
through the LUT whose base is block +0x0C → `[0x0049C9F8]`. **There is no translucency field in any
primitive record.** The question's premise about "PAN" does not hold: `LEVELS\%s%s.PAN`
(`0x00498A64`) is referenced exactly once, from the race asset loader at `0x00418E81`, and is the
**panorama/horizon bitmap** drawn by the panorama stage (ctx +0x3C → `0x00448E70`), not a blend table.
The 0x40000-byte `conv.tab` LUT at `[0x004CDD18]` is touched only by the texture packer and
downsamplers (`0x004475C0`, `0x00447FB0`, `0x004481F0`, built by `0x00448620`), i.e. it is a
texture-space colour/mip LUT, not a per-pixel blend applied at raster time. **[CERTAIN]**

### 1.5 Projection — where the divide happens

**Vertices reaching the rasterizer are already projected to screen space, as 24.8 integers.**
**[CERTAIN]**

* The perspective divide for geometry happens **before** emission, in the transform stage
  (`0x0044A900`, integer `imul` against the matrix at `0x004CDC60–0x004CDC80` then `div`, e.g.
  `0x0044A9C9`). Emitters only clip-compare and copy; span loops merely `sar 8` to pixels
  (`0x00451763`).
* The **affine** path interpolates u/v linearly across the span.
* The **perspective** path (types 0x14/0x15 → `0x004516F0` → `0x004512E0`/`0x00450F90` →
  `0x00453818`) performs a *per-span* divide **inside** the rasterizer
  (`shr eax,8; mul [0x49CA3C]; add [0x49C9F0]`), with `0x004537DC` as its x87 span filler — this is
  the routine that does `fninit` and leaves the FPU at 64-bit precision (see `06` §4.1).
* Reciprocal table `0x0049D3A8` (`65536/i`) is used only at `0x00450685` and `0x00452EBD`.

### 1.6 Recommended capture

Hook **`0x0044F0E9`** (the dispatcher): it sees the block *and* the list for every path — race, HUD
and menu — in one place. Bracket frames with `0x004466D0` (scene back end) if you want per-frame
grouping, and `0x0044C1F0` if you want the face-walk boundary.

---

## 2. Per-step simulation state

### 2.1 The two per-car arrays

| Array | Base | Stride | Count | Allocation |
|---|---|---|---|---|
| **Car state** | `[0x005DAFFC]` | **0x484C** | `[0x006192F0]` | `0x0041D19D–0x0041D1B2` computes `count*257*18 + count = count*4627`, `shl 2` → `count*0x484C`; pool alloc `0x0045AFB0` from `[0x0055307C]`, stored at `0x0041D1BF` |
| **Aux per-car** | `[0x00563D54]` | **0x1D0** | same | `0x0041D234–0x0041D256` computes `count*29`, `shl 4` → `count*0x1D0`; same pool, stored at `0x0041D256` |

Both are **[CERTAIN]**. The stride is *computed*, never a literal — a byte scan for 0x484C finds
nothing.

### 2.2 Pose fields (all x87 `double`)

Confirmed from the ghost recorder `0x00445C10`, which reads them directly. **[CERTAIN]**

| Offset | Field | Type | Evidence |
|---|---|---|---|
| +0x00 | position **x** | double | `0x00445C39` `fld qword [eax]`, biased by `25600.0` (`0x0047AD38`) when packed |
| +0x08 | position **y** | double | `0x00445C6C`; `+5000.0` (`0x0047AD40`) added when car `+0x354` is set |
| +0x10 | position **z** | double | `0x00445CA1`, biased by `25600.0` |
| +0xF8 | orientation angle 1 (heading) | double | `0x00445D10` pair `+0xF8/+0xFC` → wrap helper `0x00446330`, ×`1024.0` |
| +0x108 | orientation angle 2 | double | `0x00445CCC` pair `+0x108/+0x10C` |
| +0x110 | orientation angle 3 | double | `0x00445D49` pair `+0x110/+0x114` |
| +0x118 | speed | double | written by `0x00422680`, `0x00435910` |

`0x00446330` wraps an angle into range by repeated add/subtract of `[0x0047ADA0]` before the ×1024
scale, so the recorded angle units are (wrapped radians × 1024) truncated to int16.

Writers per step: +0x00 by `0x00422680`/`0x00423AA0`/`0x00424570`/`0x00426B40`; +0x08 by `0x00423AA0`;
+0x10 by `0x00422680`/`0x0042BC80`/`0x00435910` among others; +0xF8 by `0x00422680`; +0x108/+0x110 by
`0x00424570`. Note the ghost recorder samples **car 0 only** (`mov eax,[0x5DAFFC]` with no index).

### 2.3 Velocity lives in the aux array, not the car struct

**Correction to the earlier region report.** `0x0040E6B0` ("car dynamics") receives a pointer to the
**aux** array, not the car struct: the call site `0x004228E9–0x004228F2` pushes
`[0x00563D54] + esi`. **[CERTAIN]**

| Aux offset | Field | Evidence |
|---|---|---|
| +0x68 / +0x70 | integrated position components | `0x00423F46` / `0x00423F5F` `fstp` |
| +0x78 / +0x80 | velocity components | `0x00423F3A` / `0x00423F50` `fld` |

`0x00423F00` is the integrator: `pos += vel * [0x00479BB0]` where **`[0x00479BB0]` = 1/72 exactly**,
guarded by car field `+0x4848`.

### 2.4 What feeds `[esp+4]` at `0x00422680`, and its unit

**[CERTAIN]**, and this is what makes the step 72 Hz rather than the 0.5 constant by itself:

1. `0x00420C00` (the frame gate) computes the frame delta in **ticks of 1/36 s** (`g_nowMs * 0.036`
   minus the last committed value), clamped to 10.8 ticks = 300 ms.
2. The caller stores that double at `0x00417413` → `[esp+0x14]`, then to the globals
   `[0x00563D80]` / `[0x00563D84]` at `0x00417464` / `0x00417469`.
3. Those two dwords are pushed at `0x00417648–0x00417656`, so **`[esp+4]` is a `double` holding dt in
   36 Hz ticks**.
4. `0x00422680` accumulates it into `[0x0054F948]` and loops while the accumulator exceeds `0.5`
   (`0x004235E3` subtracts 0.5, `0x00423607` loops) ⇒ **0.5 tick = 1/72 s**, corroborated
   independently by the 1/72 integrator constant above.
5. Parity `[0x005531B8]` toggles per step: parity 1 runs AI `0x004134E0` and ApplyControls
   `0x00442030` (`0x0042349E`); parity 0 runs the ghost recorder `0x00445C10` in time-trial mode
   (`0x0042358A`) — i.e. both at **36 Hz**.

### 2.5 Suggested per-step trace record

Hook `0x00422680` to observe frame dt, then hook the *inner* step. The cleanest per-step marker is
`0x00423F00` (called once per car per step). Per step, per car `i`, dump:

```
u32 step_seq; f64 acc   = [0x0054F948];  u32 parity = [0x005531B8];
f64 dt_ticks            = [0x00563D80];   // only changes per frame
car = [0x005DAFFC] + i*0x484C;   aux = [0x00563D54] + i*0x1D0;
f64 pos[3] = car[+0x00, +0x08, +0x10];
f64 ang[3] = car[+0xF8, +0x108, +0x110];
f64 speed  = car[+0x118];
f64 auxpos[2] = aux[+0x68, +0x70];   f64 auxvel[2] = aux[+0x78, +0x80];
```

Trace boundaries: `0x0041FE60` resets the accumulators (`[0x0054F948]` at `0x0041FEAF`,
`[0x005531B8]` at `0x0041FEF5`) — use it as trace start; `0x00420870` is race teardown.

---

## 3. Resolution ceiling — measured, hard limits vs mode constants

### 3.1 The `0x0044F070` guard decoded (`RasterInit(eax=width, ebx=height)`)

| VA | Action | Class |
|---|---|---|
| `0x0044F071` | `cmp eax,0x578` (1400) → `jg` fail | **hard** (compare-and-reject) |
| `0x0044F078` | `cmp ebx,0x258` (600) → `jg` fail | **hard** (compare-and-reject) |
| `0x0044F080` / `0x0044F085` | width → `[0x0049CA3C]`, height → `[0x0049CA40]` | — |
| `0x0044F08C–AC` | builds reciprocal table `0x0049D3A8` = `0x10000/i − 1` for i = 1…0x3A9A → **14,998 entries, 59,992 B** | **hard** |
| `0x0044F0AF–C6` | walks the init-callback list `0x004ABE6C` until NULL | — |
| `0x0044F0C8–DC` | builds row-offset table `0x0049CA44`, `mov cx,0x258` → **exactly 600 entries** of byte offsets 0, w, 2w… | **hard** |
| `0x0044F0E2` | failure: `popal; mov eax,-1; ret` | — |

Failure handling differs: the **race** path `0x0043E2A0` pushes `[0x00553000]`/`[0x00563C10]`, checks
the result and on failure logs `0x004995C8` and calls **`exit(1)`** (`0x0043E5EF–0x0043E5FE`); the
**menu** path `0x00402BA0` → `0x00402BE0` **discards** the result (`mov eax,1`, `0x00402BF3`).

### 3.2 Framebuffers — this is the binding constraint

| Buffer | Capacity | Evidence | Class |
|---|---|---|---|
| 3D framebuffer `0x00563DB0` (static BSS) | **488,000 B** | next referenced global above it is `0x005DAFF0`/`0x005DAFF4`/`0x005DAFFC` (camera + car array pointers); no referenced global in between | **hard** |
| 2D/menu framebuffer `[0x004BE840]` | **64,321 B** | `push 0xFB41; call malloc` at `0x004029E9` (320×200 = 64,000 + 321 slack) | **hard** |
| Screenshot buffer | `[0x553000]*[0x563C10] + 0x312`, allocated per shot (`0x00446075–88`) | scales with mode | none |

Required: 320×200 = 64,000 · 640×480 = 307,200 · 800×600 = 480,000 · 1024×768 = 786,432 ·
1920×1080 = 2,073,600.

So the static framebuffer holds 800×600 with 8,000 bytes to spare, **but the guard's own maximum
(1400×600 = 840,000 B) would overrun it by 352,000 bytes straight into the car/camera array
pointers.** The real rule is **width × height ≤ 488,000**. **[CERTAIN]** for the sizes;
**[INFERRED]** that the overrun corrupts those specific pointers.

The menu buffer is safe today only because `[0x004BE730]` (menu mode index) has exactly one writer,
`xor esi,esi` at `0x004029A5` then `mov [0x4BE730],esi` at `0x004029B6` — **permanently mode 0 =
320×200**. The GFX RESOLUTION option writes `[0x00552FC0]` (race only). Any non-zero menu mode would
over-read that 64,321-byte buffer by up to ~415 KB — latent, currently unreachable. **[CERTAIN]**

### 3.3 Per-scanline / per-column tables

| Table | Entries | Bytes | Limit |
|---|---|---|---|
| Row offsets `0x0049CA44` | **600** | 2,400 | **hard: height ≤ 600.** Builder `0x0044F0C9` + 9 span consumers (`0x0044F28D`, `0x0045065F`, `0x00450A4E`, `0x00450B1C`, `0x00450E7E`, `0x004517CC`, `0x004537BF`, `0x0045483D`, `0x00454A3B`); y ≥ 600 reads into the reciprocal table |
| Reciprocal `0x0049D3A8` | **14,998** | 59,992 | **hard: divisor ≤ 14,998**; the table ends exactly at `0x004ABE00`, immediately before the type dispatch table `0x004ABE10`, so overflow indexes the jump tables |
| Scale families `0x004ABEB0`, `0x004ADE18`, `0x004AFE06`, `0x004B1D6E`, `0x004B654C`, `0x004B84B4` | **604** each | 2,416 | **not resolution-gating** |
| Paired `0x004AC824`, `0x004AE78C`, `0x004B077A`, `0x004B26E2`, `0x004B6EC0`, `0x004B8E28` | **1,023** each | 4,092 | **not resolution-gating** |

The scale families are indexed by a *scale quotient*, not a screen coordinate (`0x0044F2AC`:
`eax=[0x004ABEAC]; cdq; idiv ebx` with `ebx` = span extent + 1, then `div cx` → index at
`[0x004AFD8C]`). The index grows as the extent **shrinks**, so higher resolution lowers it; the risk
is extreme minification, not large screens. This supersedes the "~605/1405 entries" figure in `06`.
The dense globals at `0x004B3Cxx`/`0x004B63xx` are scalar span/edge state, not y-indexed arrays.

### 3.4 Fixed-point range

* Screen X/Y reaching the fillers are **24.8** (`sar eax,8` at `0x00452EDA`, and in `code` at
  `0x0064E204`/`0x0064E244`), with range checks such as `cmp eax,0xFFFFC180` (`0x00452EDD`).
* Gradients / 1-over-x are **16.16**, via the `0x10000/x` table.
* Row offsets are plain byte offsets (`ebx += width` per row), not fixed-point.
* 24.8 saturates near ±8.4 M pixels, so **fixed-point range is not the binding limit** — the
  14,998-entry divisor cap and the 600-row table bite first. **[CERTAIN]**

### 3.5 Render-size globals

| Global | Meaning | Class |
|---|---|---|
| `[0x00553000]` / `[0x00563C10]` | render width / height; feed the clear (`0x00418416–39`), present `0x00446410`, screenshot and raster init | **feeds rasterizer bounds** |
| `[0x0049CA3C]` / `[0x0049CA40]` | the rasterizer's own width/height, written only by `0x0044F070` | bounds |
| `[0x004BA6E0/E4/E8/EC]` | DirectDraw display mode w/h/bpp/backbuffers | **mode constants** |
| `[0x004BE730]` | menu mode index, always 0 | **mode constant** |
| `0x0043DEA0` | applies `[0x00552FC0]`, setting both mode and render globals | mode selection |

So `mov [0x004BA6E4],0x258` at `0x004185BF` is indeed just the 800×600 **mode** height, unrelated to
the rasterizer's 600-row limit — but that limit is independently real, in the row table and the guard.

### 3.6 Bottom line

**Unmodified ceiling: width ≤ 1400, height ≤ 600, and width × height ≤ 488,000.** In practice
**800×600 is the maximum usable mode** (480,000 B); 1024×476 or 1400×348 would also pass.
1024×768 and 1920×1080 overflow the static framebuffer into the car/camera pointers.

To exceed it, in this order:

1. Relocate/enlarge the 3D framebuffer away from the static BSS array at `0x00563DB0` (74 references,
   pushed as an immediate).
2. Enlarge the 600-entry row table `0x0049CA44` and re-point its 9 consumers, or raise the `0x0258`
   guard.
3. Raise the `0x0578` width guard.
4. Extend the 14,998-entry reciprocal table, which currently abuts the type dispatch table at
   `0x004ABE10`, and re-point `0x00450684`/`0x00452EBC`, which cache `0x49D3A8 >> 2`.
5. Enlarge the menu buffer `malloc(0xFB41)` before allowing any menu mode above 320×200.

---

## 4. Hook safety — all proposed targets

Every VA below returned **SAFE (exit 0)** from `_re/tools/hookspec.py`; no UNSAFE verdicts, so no
alternatives were needed. "Bytes" is the displaced-byte count the trampoline must relocate.

### 4.1 Render-side (question 1)

| VA | Bytes | Direct calls | Displaced signature | Purpose |
|---|---|---|---|---|
| `0x0044F0E9` | 7 | 2 | `60 89 35 E4 C9 49 00` | **best single capture point** — block + list, all paths |
| `0x00438030` | 6 | 7 | `55 8B EC 83 EC 04` | race/HUD submit |
| `0x0040C720` | 6 | 2 | `55 8B EC 83 EC 04` | menu-car submit |
| `0x0044F070` | 6 | — | `60 3D 78 05 00 00` | raster init / limits |
| `0x004466D0` | 6 | 3 | `53 A1 40 87 49 00` | frame boundary |
| `0x0044C1F0` | 5 | 1 | `83 EC 04 53 56` | face-walk boundary |

### 4.2 Simulation-side (question 2)

| VA | Bytes | Direct calls | Displaced signature | Purpose |
|---|---|---|---|---|
| `0x00422680` | 6 | 1 | `DD 05 48 F9 54 00` | 72 Hz sim driver (frame dt in) |
| `0x00423F00` | 5 | 1 | `8B 44 24 04 53` | per-car integrator — natural per-step marker |
| `0x0040E6B0` | 7 | 1 | `8B 4C 24 04 83 EC 40` | car dynamics (aux pointer arg) |
| `0x00423AA0` | 5 | 1 | `83 EC 10 53 56` | per-car dynamics helper (writes pose) |
| `0x00423EA0` | 5 | 1 | `8B 44 24 04 53` | per-car helper |
| `0x00423F70` | 7 | 1 | `8B 54 24 04 83 EC 18` | per-car relative position |
| `0x00424570` | 6 | 1 | `81 EC 18 02 00 00` | player-car race events (writes +0x108/+0x110) |
| `0x00426B40` | 6 | 1 | `81 EC 6C 01 00 00` | opponent race events |
| `0x00435910` | 5 | 2 | `83 EC 34 33 C9` | respawn / path placement (writes pose) |
| `0x00442030` | 5 | 5 | `83 EC 04 53 56` | ApplyControls (control inputs per step) |
| `0x00435350` | 8 | 1 | `83 EC 08 A1 F0 92 61 00` | 36 Hz housekeeping (accumulator A) |
| `0x00445C10` | 6 | 1 | `53 A1 60 3C 56 00` | ghost recorder (36 Hz sample) |
| `0x00445940` | 8 | 1 | `83 EC 30 A1 60 3C 56 00` | ghost playback |
| `0x0041D190` | 6 | 1 | `81 EC A4 00 00 00` | car/aux array allocation — capture base + count |
| `0x0041FE60` | 9 | 1 | `53 33 C0 56 A3 80 32 55 00` | race reset — trace start marker |
| `0x00420C00` | 6 | 7 | `DB 05 3C 37 49 00` | frame gate (dt source) |
| `0x004172B0` | 8 | 1 | `83 EC 70 A1 94 93 63 00` | per-frame step (frame boundary) |

---

## 5. Corrections this note makes

1. **`0x0040E6B0` takes an aux-array pointer** (`[0x00563D54] + i*0x1D0`), not a car pointer — so the
   "position +0x68/+0x70 from velocity +0x78/+0x80" fields belong to the aux array. The car struct's
   authoritative pose is +0x00/+0x08/+0x10 and +0xF8/+0x108/+0x110.
2. **The resolution ceiling in `06` was understated as a guard.** Both 1400 and 600 are genuine
   compare-and-reject tests, but the **488,000-byte static framebuffer binds first**: max area, not
   max width.
3. **The scale jump tables are 604/1023 entries**, not "~605/1405", and they are indexed by a scale
   quotient rather than a screen coordinate.
4. **There is no translucency field in the primitive format**, and `.PAN` is the panorama bitmap, not
   a blend table.


---

## Correction (verified 2026-09-16): `.PAN` IS a blend table

This note states that `.PAN` is the panorama/horizon bitmap and that no
translucency table is involved. That is wrong. Disassembly at `0x00438359`:

```
0x00438359  mov al,  byte [ebx + edx + 0x190]     ; a source pixel
0x00438360  mov cl,  byte [esi + edx + 0x563DB0]  ; the pixel already in the 3D framebuffer
0x00438367  shl eax, 8
0x0043836B  add eax, ecx                          ; index = (src << 8) | dest
0x0043836D  mov ecx, dword [0x005DFE64]           ; the .PAN buffer (loader in note 04)
0x00438373  mov al,  byte [eax + ecx]             ; combined colour
0x00438376  mov byte [esi + edx + 0x563DAF], al   ; written back to the framebuffer
```

Both halves of the index are **pixel values**, one of them read back out of the
3D framebuffer at `0x00563DB0`, and the result overwrites that pixel. That is a
256x256 colour-combination lookup, i.e. a blend/translucency table. A bitmap
would be indexed by screen position instead. `04_track_formats.md` is correct on
this point; this note was not.

**Consequence for a GPU renderer:** this blend reads the destination pixel and
maps (src, dest) through an arbitrary table. It cannot be expressed as a normal
fixed-function blend. It needs either a shader that samples the current render
target (a copy, or a framebuffer-fetch extension) or a two-pass approach, and
the palette-index framebuffer has to stay indexed for the lookup to mean
anything. Plan for it before choosing the GPU pixel format.
