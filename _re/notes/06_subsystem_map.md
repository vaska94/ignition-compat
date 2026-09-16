# Ignition (`Ign_win.exe`) — complete function inventory, subsystem map, rebuild assessment

Companion data: **`_re/out/functions.csv`** (every function: va, size, subsystem, confidence, callers,
callees, notes), `_re/out/funcdb.json` (raw inventory + call graph + features),
`_re/out/subsystem_stats.json` (aggregates), `_re/out/labels_*.tsv` (per-region classification),
`_re/out/strings.txt`. Tools added: `_re/tools/funcdb.py`, `fq.py`, `metrics_extra.py`,
`anchors.py`, `merge.py`, `bytescan.py`.

Claims are tagged **[CERTAIN]** (read directly from disassembly / relocations / strings) or
**[INFERRED]**. Nothing in the game directory was modified outside `_re/`; the game was not launched.

---

## 0. Headline results

1. **1,253 functions** (was estimated ~532). 909 live, **344 dead**. Game+engine code 772 functions /
   419,749 bytes; MSVC CRT 454 / 56,338; hand-written `code` section 26 / 5,377; plus **one code island
   inside `.data`** (see §1.3).
2. **The simulation is a 72 Hz fixed-step accumulator inside `0x00422680`, not the 36 Hz loop the
   earlier notes identified.** Car dynamics, collision, AI, controls and ghost recording all run there.
   `0x004357A0` ("36 Hz tick") only animates scenery; `0x00435350` is tick housekeeping. **[CERTAIN]**
3. **Ghosts store poses, not inputs** — 21,600 records × 14 bytes (7×int16), sampled at 36 Hz.
   Ghost compatibility is a *file-format* problem, **not** a bit-exact-physics problem. **[CERTAIN]**
4. **No self-modifying code anywhere.** No relocation targets the `code` section, no absolute writes
   into it, no `call $+5/pop` construct. The "self-modifying texel loop" in `00_overview.md` is wrong;
   what exists is a one-time Duff's-device entry-pointer table (`0x0063B520`) and face-handler swapping
   in `0x0044B480`. **[CERTAIN]**
5. **Everything float is x87 `double`.** 6,186 qword vs 1,432 dword x87 memory operands image-wide;
   physics/collision/AI/effects are double-precision with `fsin/fcos/fpatan/fsqrt` (367 transcendental
   ops) plus CRT `asin/acos/atan/atan2/fmod`. Fixed-point (16.16 / 24.8) is confined to the rasterizer
   and sprite/blit paths. **[CERTAIN]**
6. **The game is a global-state machine**: live game code touches **4,970 distinct absolute globals**;
   271 of 570 live functions take no stack arguments at all and communicate only through globals.

---

## 1. Inventory: method, totals, and what was previously missed

### 1.1 Method (`_re/tools/funcdb.py`)

Linear sweep desynchronises on this binary, so the inventory is **relocation-seeded recursive descent**:

1. `.reloc` holds 44,089 HIGHLOW entries — complete ground truth for every absolute pointer
   (function-pointer tables, callbacks, jump tables).
2. **Strong seeds**: PE entry `0x00469950`; every direct `call rel32` target discovered *during
   descent*; `jmp` targets that follow `int3` padding (MSVC never pads inside a function ⇒ tail call;
   10 found, e.g. `0x00418DD0`).
3. **Weak seeds**: reloc targets whose *location* is in `.data`/`.rdata` (menu PAGE hooks, action table
   `0x0047DF70`, gfx driver tables, DirectPlay callbacks) or is an immediate operand in code
   (`push offset WndProc`). A weak seed that is a branch target inside an already-decoded function is
   demoted to an internal label — **88 demoted**, of which **84 belong to the asm dispatcher
   `0x0044F0E9`** (its primitive handlers) . Weak seeds that decode as zero-filled data are rejected
   (`c_dfDIKeyboard` object table `0x00477760`, `DIDATAFORMAT` `0x00478760`, both inside `.text`).
4. **Jump tables**: `jmp [idx*4+T]` and the register-based form `mov ecx,T; jmp [ecx+eax*4]` are read
   through the reloc set — 56 dispatch sites, 10,383 entries, **14 tables living in `.data`**
   (13 owned by `0x0044F0E9`).
5. **Gap scan** over `.text` and `code`: a candidate is accepted only if its entire flow stays inside
   the gap and never touches code owned by another function.
6. **Ownership accounting**: each instruction is owned by the nearest reaching start below it, so
   out-of-line chunks are counted exactly once.

Coverage: `.text` 476,087 of 490,380 bytes owned (remainder = 8,477 `int3` padding bytes, the 4,120-byte
keyboard data format, in-`.text` jump tables, import thunks); `code` 5,377 of 5,527 (rest is
`xchg ebx,ebx`/`nop` alignment).

**Dead-code cross-check [CERTAIN]:** none of the 202 unreferenced game roots is the target of any
relocation, and a raw `E8/E9 rel32` byte scan of all of `.text` finds no call to any of them.

### 1.2 Totals

| Class | Functions | Live | Dead | Bytes (live) |
|---|---|---|---|---|
| Game + engine `0x00401000–0x004690F3` | 772 | 564 | 208 | 379,215 |
| MSVC CRT `0x00469100–0x00478B8C` | 454 | 320 | 134 | 42,332 |
| `code` section `0x0064E000–0x0064F597` | 26 | 24 | 2 | 4,590 |
| `.data` code island `0x00499ABC` | 1 | 1 | 0 | 217 |
| **Total** | **1,253** | **909** | **344** | |

Discovery route for game code: 490 direct-call targets, 22 data-table function pointers, 48 code
immediates, 6 tail-jump targets, 202 unreferenced (gap scan), 1 call into `.data`.

**Two dead counts, both in `functions.csv`:** 344 functions are unreachable in the call graph
(`DEAD(unreachable)` in `notes`). A further **18** are only reachable through function-pointer slots
that nothing live ever calls — the `DrawPolyList` 2D path (`0x00457250`, `0x004572C0`, `0x0045D8A0`,
`0x0045DFB0`, their span helpers and the `code` routines `0x00468030`/`0x00468050`/`0x004680C6`), the
dead gfx2 stubs/viewport slots, and `InitInput_WithTimer 0x00455C60`. Liveness counts them live because
the inventory treats every data-referenced pointer as a root; they are flagged `ev: DEAD` by region
analysis. **362 functions (29 %) are effectively dead.**
Live game functions: median 178 bytes, 38 over 2 KB, largest `0x00404A60` = 19,362 bytes.

CRT boundary **[CERTAIN]**: engine asm ends at `0x004690F2`; `0x00469100` = `fclose`,
`fread 0x00469170`, `calloc 0x004692C0`, `fopen 0x00469390`, `free 0x004693B0`, `malloc 0x00469400`,
`srand 0x004694D0`, `rand 0x004694E0` (LCG constant `0x269EC3` at `0x004694F8`), `_ftol 0x0046950C`,
`sprintf 0x004695C0`, `asin/acos/atan/atan2 0x00469652…0x0046966A`, `_alldiv 0x00469680`,
`_allmul 0x00469730`, `fmod 0x0046991A`, `exit 0x004697E0`, `WinMainCRTStartup 0x00469950`
(math names read from the CRT's own descriptor records, e.g. `"asin"@0x004BBC60`).

### 1.3 Code outside the code sections

* **`0x00499ABC` (217 bytes) executes from `.data`** — a hand-written byte-opcode RLE/delta decoder for
  the `Baltazar\data\Ign*.cdp` intro animations, called once from `0x0041262F`; it dispatches through a
  256-entry table at `0x00499B95` (all relocated) into 12 handlers. `.data` has no execute bit, so this
  is a **second DEP hazard** alongside the `code` section. **[CERTAIN]**
* **`code` section**: 26 routines (10 called from `.text`, 14 internal, 2 dead: `0x0064EC80`,
  `0x0064EDA0`). Only half serve the 3D renderer; the rest serve a second 2D/sprite engine at
  `0x0045CA50`/`0x0045D170`. **[CERTAIN]**

---

## 2. Execution model (this supersedes `notes/02` §5.4)

```
WinMain 0x004120A0 → message pump → FrameTick 0x00412230 (states 0/1/2)
  state 1 → per-frame step 0x004172B0
      0x00420C00  36 FPS gate: dt in "ticks" (1 tick = 1/36 s); if dt < 1 → skip frame body
      0x00420D10  catch-up cap (200 ticks) → 0x0041F8F0 resync
      net: 0x0040E0A0 pack / 0x0040D7A0 receive
  ┌── 0x00422680  SIMULATION  [0x0054F948] += dt;  while (acc > 0.5) { step(); acc -= 0.5; }
  │      → 0.5 tick = 1/72 s fixed step. Per step: per-car race events 0x00424570/0x00426B40,
  │        car dynamics 0x0040E6B0, wheel/corner world positions 0x0040FEE0, car-car collision
  │        0x0040F050, obstacle collision 0x0040F960, positional sound 0x0043E710.
  │        Parity flag [0x005531B8] toggles each step, so these run at 36 Hz:
  │          parity=1 → AI 0x004134E0 + ApplyControls 0x00442030 (0x0042349E)
  │          parity=0 → ghost recorder 0x00445C10, only when race mode [..]==2 (0x0042358A)
  └── per-frame (once): camera 0x0043C910, skid/smoke 0x0042BC20, SND_Service 0x00457890,
         ghost playback 0x00445940, object placement 0x0043D540, present alias 0x0043E6C0
      accumulator A (whole ticks, 36 Hz) 0x004177F8: 0x00435350 housekeeping, hotkeys 0x00420EB0,
         per-car controls 0x00420E60 → 0x00441390 → 0x00442030, damage 0x00442EC0,
         particles 0x00444450, respawn 0x00435910, object table 0x00434190
      accumulator B (whole ticks, 36 Hz) 0x00417946: scenery animation 0x004357A0 only
      render 0x00436990
```

**[CERTAIN]** for the structure (`0x00422686` `fadd [esp+4]`, `0x004235E3` `fsub 0.5`,
`0x00423607` loop-back; constants `0.5`@`0x00479A50`, `1/36`@`0x004792E8`, `1/72`@`0x004792F0`).

Consequences: the simulation is **frame-rate independent in step size** (fixed 1/72 s), but the
*number* of steps per frame is driven by the measured frame dt, so step boundaries — and therefore the
interleaving of per-frame code (rendering, RNG consumers, input latching) with simulation steps —
depend on frame timing. **Physics maths itself never sees dt.** **[CERTAIN]**

---

## 3. Subsystem map

Bytes include dead code; "x87" is x87 instruction count in live functions. Difficulty is rated in §6.

| Subsystem | Fns (live) | Bytes | x87 | Role |
|---|---|---|---|---|
| game_state | 24 (24) | 57,056 | 1,386 | race events, crash/lap/finish, results & championship screens |
| crt | 454 (320) | 56,338 | 597 | MSVC 4.2 runtime |
| render3d | 63 (50) | 54,695 | 1,203 | scene grid, transform, cull, bucket sort, face→primitive emit |
| menu | 52 (51) | 47,513 | 376 | front end, data-driven pages, CDP intro |
| rasterizer | 78 (68) | 41,811 | 345 | asm primitive dispatcher, span fills, `code` section |
| effects | 28 (28) | 39,451 | 1,774 | skids, smoke, dust, sparks, weather, scenery animation |
| hud_2d | 80 (59) | 22,457 | 267 | HUD, fonts/text, sprites, 2D blits |
| audio | 62 (62) | 20,391 | 295 | DirectSound, software mixer, sample loaders, engine/tyre voices |
| loaders | 24 (9) | 17,623 | 0 | TEX/COL/PAN/PIC/SHD/TAB/POS/PLC/MSH + dead 3D toolkit loaders |
| collision | 12 (12) | 16,165 | 1,420 | car↔car, car↔obstacle, world-object proximity |
| textures | 47 (33) | 12,478 | 21 | page packer, mip/downsample, `conv.tab` LUT, animated UVs |
| ai | 12 (5) | 11,537 | 664 | `.TRI` racing lines, AI driver |
| physics | 9 (9) | 11,398 | 813 | car dynamics, gearbox/rpm, car array setup |
| net | 30 (28) | 10,395 | 0 | DirectPlay lobby + in-race packet sync |
| util | 92 (9) | 9,357 | 0 | mostly dead toolkit helpers |
| race_controls | 5 (5) | 6,977 | 99 | input → control block → ApplyControls |
| track | 12 (6) | 6,925 | 155 | section/placed-object tables, SRF grid (+dead tool code) |
| video_ddraw | 37 (32) | 5,620 | 0 | DirectDraw driver + wrappers (already shimmed) |
| sim_tick | 2 (2) | 4,874 | 234 | 72 Hz sim driver `0x00422680`, 36 Hz housekeeping |
| platform | 42 (35) | 4,553 | 5 | WinMain/WndProc/timing/logger/file I/O/pools |
| math | 22 (11) | 4,332 | 325 | matrix/euler/fixed-point helpers (11 dead) |
| camera | 4 (3) | 3,990 | 227 | chase camera, target selection |
| ghost_replay | 5 (5) | 3,690 | 73 | `.GST` record/playback/save/reset |
| main_loop | 7 (7) | 3,661 | 76 | frame dispatcher, 36 FPS gate, accumulators |
| input | 17 (15) | 3,309 | 78 | DirectInput keyboard, WINMM joystick |
| settings | 5 (5) | 2,476 | 7 | `ign_win.btz` blob load/save/apply |
| cd_audio | 19 (12) | 1,709 | 0 | MCI Redbook music |
| screenshot | 1 (1) | 317 | 0 | F12 TGA writer |
| unknown | 8 (3) | 583 | 0 | 2 register-arg 16.16 lookups + stubs |

### 3.1 Per-subsystem detail

**sim_tick** — `0x00422680` (3,989 B) 72 Hz driver; `0x00435350` (885 B) 36 Hz housekeeping (angle
filtering into car `+0x584/+0x58C`, sparks, weather).
Globals: accumulator `[0x0054F948]`, parity `[0x005531B8]`, frame dt `[0x00563D80]` (double).
Out → physics, collision, game_state, effects, ai, race_controls, ghost_replay. In ← main_loop.

**physics** — `0x0041D190` (5,543 B) allocates the car array and initialises cars/wheels from meshes;
`0x0040E6B0` (1,388 B) the per-step car dynamics (slip angles, gravity `9.81`@`0x004792C8`, integrates
position `+0x68/+0x70` from velocity `+0x78/+0x80`); `0x00442670` (1,778 B) gearbox/rpm with ratio table
`0x004929E8`; `0x0040FEE0` wheel/corner world positions.
Key globals: **car array `[0x005DAFFC]`, stride `0x484C`, count `[0x006192F0]`**; current car index
`[0x005285C0]`; per-player records `[0x00552FFC]` stride `0x4C`.
Car fields (CERTAIN): pos x/y/z double `+0x00/+0x08/+0x10`, heading `+0xF8`, speed `+0x118`,
wheel surfaces `+0x150…`, corner offsets `+0x2FC…/+0x31C…`, reset `+0x350`, disable `+0x354…`,
path index `+0x364`, lap `+0x39C`, finished `+0x528`, wheel contact `+0x5D8…`.
Out → math, effects, audio. In ← sim_tick, collision, race_controls.

**collision** — `0x00429A40` (3,718 B) movable collision objects vs car corner points (string
"COLLISION OBJECT"); `0x00427DC0`/`0x00428B90` world-mesh proximity gathering; `0x0040F050` car↔car
(distance vs summed radii at `+0x130`); `0x0040F960` corner points vs obstacles; `0x0040EC20` response.
**Data consulted**: the renderer's placed-object list `[0x0063C5CC]` with integer bounding boxes, the
obstacle list `[0x0054F904]` (count `[0x0054F954]`, built by `0x0041B470`), and — for ground/surface —
the **SRF grid** queried by `0x00412FC0`: parameters `0x004C53A4…0x004C53D4` (cell sizes `0x004C53B8`/
`0x004C53C8`, width `0x004C53CC`, cells `[0x004C53D4]`, 12-byte entries), built by `0x00412670` from
`LEVELS\%s%s.SRF` at race init. **[CERTAIN]** for the grid path, **[INFERRED]** for full semantics.

**ai** — `0x004134E0` (5,749 B) driver: follows the `.TRI` racing line, calls ApplyControls, uses
`rand`; `0x00414E40` (4,147 B) loads `LEVELS\%s%s.TRI` and builds the line tables `[0x0063A018]`
(stride 0x7C) / `[0x0063A024]`, and calls **the binary's only `srand`** (`0x004150BF`).
7 of 12 functions are dead (AI debug display cluster, `ailists.txt` dumper `0x004161C0`).

**render3d** — `0x0041B470` (6,363 B) creates world/car/wheel/shadow/flare objects; `0x00436990`
(5,784 B) the race frame render; `0x0043D540` places car objects; grid traversal `0x00449E70`/
`0x0044A3D0`, per-cell transform/cull `0x0044A900`/`0x0044AE20`, face→primitive emitters
`0x0044E900`/`0x0044E1B0`/`0x0044BB90`/`0x0044D550`, scene back end `0x004466D0`, init `0x004468D0`.
Pipeline **[CERTAIN]**: objects live in a 256-unit spatial grid (`0x00446EB0` insert / `0x00446F30`
move / `0x00447150` remove) → `0x004466D0` runs stages from renderer ctx `[0x0063C5F0]`: camera setup
`0x00448990`, panorama `0x00448E70`, grid traversal + transform/cull into **6,000 depth buckets**
(`[0x0063B5E8]`), face walk `0x0044C1F0` dispatching the face-type byte through the 24-slot table
`0x0049C8E0` into primitive pool `0x0063B600`, then a back-to-front bucket flatten into `[0x0063C5BC]`
(painter's sort) → rasterizer.
Renderer ctx `0x0063C5F0` (0xA8 B): camera pos doubles `+0/+8/+0x10`, angles `+0x18/+0x20/+0x28`,
perspective flag `+0x58`, object count `+0x60`, primitive count `+0x68`, projection `+0x80…+0xA0`.
13 dead functions (an older traversal `0x00449470` and the dead toolkit renderer `0x00463CD0`).

**rasterizer** — `0x0044F0E9` (13,971 B owned, 84 internal handlers) is the hand-written asm
primitive-list dispatcher: `jmp [type*4+0x004ABE10]` for types 0…22; `0x004537DC` (3,285 B) the x87
perspective span filler; textured triangle handlers `0x00452800` (type 0x11), `0x0044FF60` (0x12),
`0x00452050` (0x16), flat/gouraud `0x00454604` (0x13), perspective `0x004516F0` (0x14/0x15); sprite
quad fills `0x0045CA50`/`0x0045D170` (second engine) calling `code` routines `0x0064EA80`/`0x0064EC00`/
`0x0064EC40`/`0x0064EFA0`/`0x0064F0C0`. 12 further `.data` tables in 3 families of 4
(`0x004ABEB0…`, `0x004AFE06…`, `0x004B654C…`, ~605/1,405 entries) select unrolled scaled-sprite blit
loops by integer scale step (604/1023 entries, indexed by a scale quotient — see `07` §3.3).
Limits: `0x0044F070` rejects width > 1,400 and height > 600, but the binding constraint is the
**488,000-byte static framebuffer** at `0x00563DB0`, i.e. width × height ≤ 488,000 (`07` §3).
Entirely integer 16.16/24.8 except the perspective path (single-precision `fld dword` + `fistp`).

**effects** — `0x0042BC80` (9,816 B) wheel skid marks/smoke/dust by surface ("LI_MOVEOBJECT SLADD");
`0x00443640` sparks/debris; `0x00434840` weather (rain, lightning palette flash, thunder);
`0x0042ACB0` scenery smoke; `0x00434190` the 200-slot object dispatcher (stride 0x64,
`0x005DB040–0x005DFE60`, 11 types via table `0x00434354`); spawner `0x00434380`; scenery keyframes
`0x004357A0`. Highest x87 count in the binary (1,774) and heaviest `_ftol` user (464).

**game_state** — `0x00438A60` (14,688 B) results/championship screens; `0x00424570` (9,255 B) player-car
race events (obstacle hits → crash sequences, laps, finish flag, LAP/TRACK RECORD messages in 6
languages); `0x00426B40` same for opponents; `0x004307B0` car-into-water; `0x00435910` respawn;
`0x0042ED60` wreck/explosion; `0x00432040` reset; `0x00440C90` race positions; `0x004383B0` pause menu;
`0x00420EB0` in-race hotkeys (F12, numpad ±, pause).

**menu** — `0x00404A60` (19,362 B) builds the PAGE/ITEM/REC tables at `0x0047DC30–0x00481898` with
~2,850 stores behind MSVC static-init guards at `0x004BFB18`, then loads `ign_win.btz`; `0x00409FB0`
(6,127 B) and `0x00409610` (2,246 B) are the *generic* hooks referenced by all 26 PAGE records (the
earlier "GFX page hooks" reading was wrong); `0x00402C00` the front-end frame function (timers, input,
joystick-as-keys, autosave, CD music scheduler); `0x0040B820` best-times/results tables;
`0x00410AF0`/`0x00410D80` menu draw/input; `0x00410440` the `default.psq` "Script Player" for the menu
car; `0x00499ABC` the CDP decoder.

**hud_2d** — `0x0043EF30` (6,284 B) in-race HUD; `0x0041E990`/`0x0041F410` HUD layout tables per
resolution/screen size; big-font text list `0x00401000`…`0x00401730` (`MenuBkg.dat`); engine text/sprite
API `0x004564D0`/`0x00456660`/`0x004571B0`; `.LFT` font parser `0x00456270`.

**audio** — mixer `0x00466150` (3,778 B) + `0x00465F20`/`0x00465DF0`; DirectSound `0x00467920`,
`0x00467510`, `0x004677F0`; sample loaders `.WAV` `0x004587D0`, Gravis `.PAT` `0x004582B0`,
directory loader `0x00458E00`; voice API `0x00457AA0` (handle = index<<16 | generation, voice table
`0x0063CAA4` stride 0x24); engine/tyre voice updates `0x004452C0`/`0x00444FA0`; positional trigger
`0x0043E710` (23 callers); race bring-up `0x0041F9B0` (ENGINE.INF, ROLL/SKID/COLL/BOOST samples).

**loaders / textures / track** — race asset master `0x00418DD0` (COL/PAN/PIC/SHD/TAB/POS), TEX loader
`0x00419D10` (3,827 B), PLC `0x00419A90`, MSH `0x00419BD0`, fonts `0x0041AC40`, panels `0x0041AF70`,
track path prefix `0x004198D0` (ICELAND/CANADA/USA/CARIB), SRF `0x00412670`, section/object tables
`0x00416250` (4,113 B). Texture page packer `0x004475C0`, downsamplers `0x004481F0`/`0x00447FB0`,
upload `0x00447280`, mip LUT cache `0x00448620` (`.\levels\<hex>conv.tab`, 0x40000 B).

**ghost_replay** — `0x00420240` load `.GST` + create ghost object; `0x004222B0` write on best lap
(also awards championship points and updates the settings blob); `0x00445C10` recorder; `0x00445940`
playback; `0x00404790` menu reset (deletes 7 files). Buffer `[0x00563D7C]`, sample index
`[0x00552F54]`, overflow `[0x005530B8]`, best time `[0x00525E44]`.

**Dead subsystems worth knowing**: a complete unused **3D object toolkit** at `0x0045E760–0x00465B94`
(~106 functions, ~26 KB: `3ds`/`l3d`/`geo` loaders, a text `.GEO` parser, hierarchy parser
`0x00462540`, float matrix/lighting, renderer `0x00463CD0`), a **palette quantizer** `0x00402260–
0x004028A0`, **SRF grid editor/tool code** `0x004127C0–0x00412D70`, the AI debug cluster, the
`DrawPolyList` 2D path, and the `timeSetEvent` input timer. All confirmed unreachable.

---

## 4. Properties that constrain a rebuild (measured)

### 4.1 Floating point: x87 doubles everywhere in game logic

Image-wide x87 memory operands: **6,186 qword (double) vs 1,432 dword (single)**, 1,040 integer
(`fild`/`fistp`), 220 extended (CRT only). Live transcendental x87 ops by subsystem: effects 69,
collision 61, physics 47, ai 35, render3d 33, math 16, game_state 11 — plus CRT `asin` (callers
`0x004043F0`, `0x00424570`, `0x00438210`), `fmod` (`0x00424570`, `0x0042BC80`, `0x00435350`,
`0x00441250`, `0x00443640`, `0x00444450`, `0x00444FA0`) and `atan/atan2`.

* Physics, collision, AI, camera, effects and game logic are **double** (`fld qword`), with results
  converted by **`_ftol` truncation** (172 calls in game_state, 464 in effects, 203 in render3d).
* **Single precision appears only** in render3d's transform stage, the perspective span filler, sprite
  rotation and a few float helpers.
* **Fixed point** (16.16/24.8) is the rasterizer, sprite blits, the SRF grid and 22.10 world
  coordinates for effect objects — never the car simulation.
* **FPU control word**: only `0x004537DC` changes it — `fninit` ×4 and `fldcw` ×6 — setting 24-bit
  precision for span filling and restoring `0x037F` (not the MSVC default `0x027F`) on exit, after a
  second `fninit` that also **discards the caller's x87 stack**. Reachable **only with PERSP. POLY
  enabled** (default off). **[CERTAIN]** for the instructions; **[INFERRED]** that this leaves later
  simulation maths at 64-bit precision instead of 53-bit.

**Implication:** to reproduce original handling exactly you must keep 80-bit x87 intermediate semantics
(or emulate them), and match `_ftol` truncation and the CRT's x87 `asin/atan2/fmod`. SSE doubles will
diverge. But see §5: **ghosts do not depend on this.**

### 4.2 Calling conventions

* **cdecl with omitted frame pointers** is universal in compiled code: only **8** game functions use an
  `ebp` frame; caller-side `add esp,N` after 212 callees, none after 313 (zero-arg).
* **stdcall (`ret N`) only for Win32/DirectPlay callbacks**: WinMain `0x004120A0`, WndProc `0x004122D0`
  (`ret 0x10`), timer callback `0x00456160` (`ret 0x14`), DlgProc `0x0045A3C0`, `0x0045A7A0`,
  `0x0045AC40`, `0x0045ACD0`.
* **Register passing is confined to hand-written asm** — 38 rasterizer functions, 3 audio, 2 in
  `unknown`, and 24 of 26 `code` routines:
  * `0x0044F070`/`0x0044F0E9`: `pushal`, `eax` = width, `ebx` = height, `esi` = parameter block;
    returns `eax` = 0/−1.
  * `code` routines: `esi` = vertex/edge record, `ebx` = output edge record, `eax/ecx/edx` scalars,
    results written to **fixed globals** `0x004BA5C0–0x004BA648` (3D) and `0x004BADF4…`/`0x0063F2B0…`
    (2D engine). Callers read the results back from those addresses.
  * audio resampler `0x00468EE8`/`0x00468FDE`/`0x00469035` (`eax/ebx/esi/edi`), sprite asm
    `0x00465BB5` (`pushad`, block in `esi`), rotate/scale `0x00468C70` (`esi`).
  * `0x00412FC0` passes `esi/ebx/ecx/edx` + `edi=0x0063A170` to the 16.16 lookups `0x00446578`/
    `0x004465E1`.

### 4.3 Global-state density

* **4,970 distinct absolute globals** referenced by live game code; median 4 per function, mean 15.8.
* **271 of 570 live game functions take no stack arguments** yet read/write globals — i.e. roughly half
  the code base is "procedures over a fixed world".
* Worst offenders by median distinct globals per function: sim_tick 32, effects 27, settings 28,
  loaders 25, game_state 25, camera 20, ghost_replay 18.
* The pervasive idiom is `car = [0x005DAFFC] + index*0x484C` recomputed at every use, with `index`
  itself a global (`[0x005285C0]`). Replacing one function at a time is therefore *possible* (the ABI is
  plain cdecl) but each replacement must keep reading/writing the same globals.

### 4.4 Dependence on exact memory layout

**[CERTAIN] unless noted.** These are the places where a reimplementation must replicate byte layout:

1. **Car state array**: stride `0x484C`, hard-coded at every access site (e.g. `0x00417556`), with
   fixed field offsets listed in §3.1. `0x00434380` even reads the second car as `base+0x484C`.
2. **Settings blob `ign_win.btz`**: 0x597 packed bytes with unaligned fields, `fwrite` verbatim from
   `0x004BF580` (menu copy) / `0x006393A0` (race copy); defaults `rep movsd` from `0x0047E590`.
   The MSVC static-init guard bytes sit immediately after the blob at `0x004BFB18` — resizing it
   overlaps them **[INFERRED]**.
3. **Ghost `.GST`**: a raw 0x49D4C-byte dump — 10-byte header (car model word, track id word, lap time
   in centiseconds word — laps > 327.67 s truncate) + 21,600 × 14-byte records (7×int16: x−25600, y,
   z−25600, three angles ×1024, one progress word), tail-padded with the last record
   (`0x004222FB–0x004223E9`).
4. **Menu PAGE/ITEM/REC tables** `0x0047DC30–0x00481898`: unaligned 16-bit fields and pointers at odd
   offsets, patched by absolute address in `0x00404A60`.
5. **Asm parameter blocks at fixed `.data` addresses**: `0x004BA584–0x004BA648`, `0x004BAD28–0x004BAF50`,
   `0x0049CA0C…`, `0x0063F2B0…`; every asm call first copies arguments there and reads results back.
6. **Rasterizer dispatch tables in `.data`**: `0x004ABE10` (types) immediately followed by the init
   callback list at `0x004ABE6C` (terminated by the first NULL), plus 12 overlapping per-scale tables,
   one of them odd-aligned (`0x004AFE06`).
7. **Renderer structures**: 6,000 depth buckets at a hard offset `0x5DBC` inside `[0x0063B5E8]`, grid
   object link field at unaligned `+0x26`, primitive records with fixed offsets (type 0x14 = 0x44 B),
   mesh face records (type byte `+0`, indices `+4/+8/+0xC`, material word `+0x1E`), animated-UV fields
   `+0x10…+0x28` in stride-0x2C face records.
8. **File formats are memory formats**: the SRF file is loaded whole and pointers are built into the
   buffer (`0x00412670`); `.PAT` reads 0x14F bytes straight into a struct.
9. **Network packets are raw global structs** (`0x0063A310…`) sent as-is — wire format = memory layout
   **[INFERRED]**.
10. **83 fixed-address bulk block operations** and 213 `rep movs/stos` with immediate `.data` pointers
    (96 of them in the TEX loader `0x00419D10` alone).
11. **Sprite/HUD scratch at absolute addresses**: `0x00498730` primitive list, `0x004CD8A8` palette
    blend buffer, sprite slots `0x00528848 + idx*0x320`, per-car mesh copies `0x00603AE0 + car*0x1F40`.

### 4.5 Randomness and determinism

* One CRT LCG (`rand` `0x004694E0`, state `0x004BB08C`), seeded **once** at `0x004150BF` with
  `(short)_ftol(*(double*)[0x005DAFFC])` = **car 0's grid x position** — so the seed is a track/grid
  constant, not time-based. **[CERTAIN]**
* `rand` consumers: AI line jitter (`0x004136A8`), menu music, effects/`frand 0x00446180` (28 callers),
  hotkeys, results. **The renderer also consumes ~120 `rand()` calls per rendered frame for 100 frames
  after every texture load/free** (`0x004466D0`, armed at `0x00446C88`/`0x004473B9`). **[CERTAIN]**
* **Therefore the RNG stream is frame-count dependent**, and any sim-side `rand` use (AI jitter, crash
  spawns) is not reproducible across different frame rates. **[INFERRED]** consequence, **[CERTAIN]**
  call sites.
* Ghost recording is unaffected: it samples poses at 36 Hz from the fixed-step simulation.

---

## 5. What must match, and what is free

| Must be behaviour-exact | Why |
|---|---|
| Car dynamics `0x0040E6B0`, gearbox `0x00442670`, control curves `0x00441390`/`0x00442030`, collision response `0x0040EC20`, the 72 Hz step structure | handling feel; players notice small changes |
| `.GST` record format + 36 Hz sampling + the header/track check | ghost files on disk must keep working |
| `ign_win.btz` layout (version byte 0x1E, offsets in `notes/03` C.7c) | saved settings, best times, championship unlocks |
| Asset formats (TEX/PIC/MSH/PLC/SRF/TRI/COL/SHD/PAN/POS/LFT/PAT) | the data is the product |
| Network packet layout (only if LAN play is kept) | wire compatibility with the original |

| Freely replaceable | Why |
|---|---|
| video_ddraw, input, audio output, cd_audio, screenshot, platform glue | already proven by the shim; no gameplay effect |
| rasterizer and render3d internals | output is pixels; nothing reads them back (the `[0x0063C5CC]` object list that collision consults is built by render *setup*, not by the raster stage — keep that boundary) |
| hud_2d, menu, results screens | cosmetic; must only preserve the settings/state they write |
| The 3D toolkit, quantizer, SRF tools, AI debug (344 dead functions) | never executed |

**Bit-exact physics is *not* required for ghosts** (poses are recorded). It would only be required if
you wanted *identical* race outcomes to the original build — a much stronger goal that the x87
double + `fsin/fcos/fpatan` + `_ftol` combination makes expensive.

---

## 6. Rebuild assessment

Route **(a)** = function-by-function decompilation replacing the original in-process (detours hosted by
the existing `ddraw.dll`, fixed addresses, no ASLR). Route **(b)** = clean reimplementation that loads
the original assets.

| Subsystem | (a) in-process replacement | (b) clean reimplementation | Matching requirement |
|---|---|---|---|
| platform, video_ddraw, input, cd_audio, screenshot | **easy** — already done at the API edge | **easy** | none |
| net | easy (isolated, integer, 14 vtable slots) | medium (wire format) | packet layout if interop wanted |
| audio | medium — mixer is integer asm with register args; voice handles encode table indices | medium — needs `.WAV/.PAT` loaders + engine-sound model | audible parity only |
| settings, ghost_replay | **easy** — small, isolated, file-format bound | easy | byte-exact file layout |
| loaders, textures | easy–medium — cdecl, argument-driven, 96 fixed `rep movs` to re-point | medium | must produce identical in-memory tables |
| hud_2d, menu | medium — huge table-building init (`0x00404A60`) writes absolute addresses | medium | preserve settings/state writes |
| game_state | medium — 24 functions but 57 KB and 25 globals each | medium–hard (championship/unlock rules) | results, unlocks, messages |
| camera, race_controls | medium | medium | feel: control curves must match |
| track, collision | medium–hard — SRF grid + object lists, heavy doubles | hard — must re-derive surface query semantics | collision outcomes drive handling |
| ai | medium — 2 live functions carry it, but `.TRI` semantics must be exact | hard | lap times / race difficulty |
| physics, sim_tick | medium (small, cdecl, well-bounded) — **the highest-value target** | hard to match feel exactly | handling; step structure 72/36 Hz |
| effects | medium — 28 functions, 39 KB, heavy `_ftol` | easy–medium (cosmetic) | none (visual) |
| render3d | hard — 50 live functions, ctx struct + grid + buckets, but a clean seam at `0x004466D0` | **medium** — free to rewrite entirely | keep the object list collision reads |
| rasterizer + `code` | **hardest** — 84 handlers inside one 14 KB asm function, register ABI, fixed `.data` parameter blocks, 14 `.data` jump tables | medium — replace wholesale behind the primitive list | pixel parity only if you want screenshot diffs |
| crt | n/a (keep) | n/a | `_ftol`, `rand`, `asin/atan2/fmod` must be replicated if bit-exactness is pursued |

### 6.1 Route-(a) specific constraints (measured)

* Detours are viable almost everywhere: only **5** live non-CRT functions are shorter than 5 bytes
  (`0x00403B00`, `0x00456F50`, `0x004572B0`, `0x00459D30`, `0x00461A80`) and all are followed by `int3`
  padding; only **3** have a branch target inside their first 5 bytes (`0x00409EF0`, `0x00411520`,
  `0x00441210`).
* **The asm dispatcher cannot be detoured per handler** — its 84 handlers are reached through `.data`
  tables, so you must replace the whole of `0x0044F0E9` or rewrite the tables.
* **`code`-section routines must be replaced in groups**: 6 of 26 entries are not padding-preceded and
  some fall through into the next routine.
* Any replacement of an asm routine must keep writing results to the same fixed `.data` parameter
  blocks (§4.4 item 5), because the C callers read them back from absolute addresses.
* Replacements of simulation functions must preserve the *global side effects* (§4.3), not just return
  values — half the code communicates only that way.

### 6.2 Top risks

1. **x87 semantics** — 80-bit intermediates, `fsin/fcos/fpatan/fsqrt`, CRT `asin/atan2/fmod`, `_ftol`
   truncation. Any C rewrite of physics/collision/AI will diverge from the original trajectory.
   Mitigation: keep x87 (`long double`/`-mfpmath=387`) for the sim, or accept divergence and validate
   against *behaviour*, not traces.
2. **The `0x004537DC` FPU control-word/`fninit` hazard** (PERSP. POLY on) — rendering changes x87
   precision and clears the x87 stack under the simulation.
3. **RNG coupling to frame count** via the renderer's warm-up draws (`0x004466D0`).
4. **Fixed `.data` layout** — 4,970 globals, hard-coded strides, unaligned packed records, asm parameter
   blocks at absolute addresses, files loaded as structs.
5. **Two non-executable code regions** (`code` section, `.data` island `0x00499ABC`) — DEP and any
   future rebuild of the PE must preserve them.
6. **Rasterizer resolution limits**: `0x0044F070` rejects width > 1,400 / height > 600, the row table
   `0x0049CA44` has exactly 600 entries, the reciprocal table `0x0049D3A8` caps divisors at 14,998,
   and the static framebuffer `0x00563DB0` holds only 488,000 bytes — so **800×600 is the practical
   maximum** and the real rule is width × height ≤ 488,000. Raising internal resolution needs the
   framebuffer relocated and those tables rebuilt, not a guard tweak (`07` §3).
7. **Mixed ownership of the `code` section** between the 3D renderer and a second 2D sprite engine —
   changing one breaks the other.
8. **Dead code is 27 % of the inventory** — do not spend effort on the 3D toolkit, quantizer, SRF tools
   or `DrawPolyList`; and do not assume an unreferenced function is a hook point.

### 6.3 Recommended order of work

1. **Build a golden-trace harness first.** From the existing `ddraw.dll`, dump the car array
   (`[0x005DAFFC]`, `count*0x484C`) plus `[0x0054F948]`/`[0x005531B8]` once per simulation step, and the
   primitive list handed to `0x0044F0E9` once per frame. Everything below is validated by diffing
   against that. Without it, no replacement of physics or the rasterizer can be trusted.
2. **Asset loaders and formats** (route b's foundation, and they double as format documentation):
   TEX/PIC/MSH/PLC/COL/SRF/TRI/LFT/PAT. Validate by byte-comparing the tables they build against the
   original's memory.
3. **Ghost + settings file I/O** — small, fully specified above, and it locks down user-visible
   compatibility early.
4. **HUD/2D and menu** — large but low-risk, and it removes the biggest chunk of table-driven code from
   the "unknown behaviour" pile.
5. **Rasterizer, then render3d** — in that order, because the rasterizer is the resolution gate and has
   a clean input contract (the primitive list). Replace `0x0044F0E9` wholesale behind that contract;
   keep the `code` section untouched until the 2D engine's users are also replaced.
6. **Effects, camera, game_state** — behaviour-visible but not handling-critical.
7. **Physics, collision, AI, sim_tick last**, with the trace harness and with x87 preserved. Port
   `0x0040E6B0`, `0x00442670`, `0x0040F050`, `0x0040F960`, `0x00412FC0` first as a group, since they are
   small (≈7 KB together) and all called from one driver `0x00422680`.
8. **Only then** consider a native 64-bit build, which is the point where the x87 decision has to be
   made explicitly.


---

## Correction (2026-09-16): SUPERSEDED: ghost sampling rate

This note states that the ghost recorder runs on one parity phase at 36 Hz.
It runs on **both** phases, at the full simulation rate of **72 Hz**, so the
21,600-record buffer holds **300 s**, not 600 s.

Evidence: at `0x0042358A` the parity flag `[0x005531B8]` is tested and `jne`
skips only the counter block at `0x00423593..0x004235A8`; the join point is
`0x004235B2`, and the recorder call `0x004235D8` is below it, reached on either
path. The epilogue at `0x004235DD` subtracts 0.5 from the accumulator and
toggles the parity flag once per step.

See `08_ghost_settings_formats.md` for the full ghost layout.
