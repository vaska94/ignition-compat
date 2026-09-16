# Ignition modernisation — roadmap

Approach: **drop-in shims first, decompile incrementally.** Every phase ends
with something that runs, and the shimmed original is the reference we diff
decompiled subsystems against.

---

## Phase 1 — make it launch and present on Windows 11

Replace the legacy DirectX edge. `Ign_win.exe` stays byte-identical; the shims
are found because the application directory is searched before `System32` for
these DLLs (none of them are KnownDLLs).

| Component | Status | Notes |
|---|---|---|
| `dplayx.dll` stub | **built** | ordinals #1/#2. Unblocks the PE loader — nothing else can run until this resolves. |
| `d3d11_present` backend | **built** | 8bpp stays on the GPU: `R8_UNORM` frame + 256×1 palette texture, lookup in the pixel shader. Borderless windowed, never exclusive. |
| `ddraw.dll` shim | **done** | 21 methods across 4 interfaces; D3D11 backend, sharp-bilinear scaling. |
| `dsound.dll` shim | **not needed** | Audio works as-is; the game falls back off `DSSCL_WRITEPRIMARY` on its own. |
| `dinput.dll` shim | **not needed** | System `dinput.dll` handles DirectInput 3 keyboard fine. |
| CD check bypass | **not needed** | There is no copy protection; MCI is optional music only. |
| Joystick crash | **done** | IAT hook — the legacy joystick API was AV-ing in `winmmbase.dll`. |
| Frame pacing | **done** | Sim was already a correct fixed 36 Hz; fixed the 100% CPU spin. |
| Fullscreen | **done** | True borderless 1920x1080; removed the Windows `640X480` compat shim. |

**Status: complete.** Boots, races, sound, input, windowed and borderless
fullscreen, stable framerate, ~20% of one core instead of 100%.

## Phase 2 — asset formats — **complete**

Every shipped asset is decoded, byte for byte, and cross-checked by rendering
it: 26 pictures, 23 texture files (256x256 pages, partial last page), 18 fonts,
41 GUS patches, 8 FT2 instruments, 81 WAVs, 6 intro movies, and all 61 track
and car data files across the 7 levels. Readers live in `_re/tools/formats/`,
specs in `_re/notes/04` and `_re/notes/05`, output in `_re/out/`.

Two traps for a rebuild: the game ignores the headers inside its own picture
files and hard-codes image sizes and HUD rectangles in the EXE; and the
main-menu background is not a video but a pre-projected list of textured
triangles, so it can be redrawn at any resolution.

## Phase 3 — the trace harness

Nothing below this line is verifiable without it. Using the detour engine
(`ign_compat/src/common/detour.c`, verified standalone) hosted in `ddraw.dll`,
capture per frame the primitive list handed to the rasterizer, and per
simulation step the car array at `[0x005DAFFC]`. That recording is the
reference every replacement is diffed against.

## Phase 4 — the renderer, and with it high resolution

The rasterizer is 84 handlers inside one 14 KB assembly function dispatched
through `.data` tables, so it cannot be detoured piecemeal. It gets replaced
wholesale behind the primitive-list contract, then the transform and clip stage
above it. This is the resolution gate: the software path has fixed-size screen
tables, and widescreen additionally needs the view frustum widened.

**Status.** The primitive contract is decoded and measured against the game's
own output: 58.82% exact palette-index match on a real race frame, with 83.8%
of the remaining differences being <= 1 pixel shifts and every texture pointer
resolving. That is structural confirmation; the residue is scan-conversion
convention. Tools: `_re/tools/prim_render.py`, `_re/tools/compare_frame.py`.

## Phase 5 — user-visible compatibility, then the frontend

Ghost and settings file I/O first, because ghosts store **poses, not inputs**,
which makes replay compatibility a file-format matter rather than a
bit-exact-physics one. Then HUD, 2D and the menu - large, low risk, and where
the pre-projected menu background can be redrawn sharply.

**Status.** Both formats are specified in `_re/notes/08_ghost_settings_formats.md`,
with readers and writers in `_re/tools/formats/{btz,gst}.py`.

* Settings (`ign_win.btz`, 1431 bytes): **verified**, byte-exact round-trip on
  three independent real snapshots, including one the game wrote during the
  analysis. Every byte is assigned to a region; several regions still lack a
  confirmed *meaning*.
* Ghosts (`.GST`, 302,412 bytes): **verified** against a real file
  (`Ghosts/MOOSEJAW.GST`, a 2:25.18 Canada run). Exact size, byte-exact
  round-trip, header time matching the race, and all 10,629 live samples inside
  the track bounds - plotted, the path follows the drivable surface through
  every corner and closes the lap. 21,600 records at **72 Hz** = 300 s, stored
  in raw mesh space with no +25600 bias; unused tail is padded by repeating the
  last sample, so sample count, not record count, gives the drive time.
* **Save condition, corrected:** a ghost is written only in mode 2 (TIME
  TRIAL), with the invalid flag clear, and when the race time beats
  `[0x00525E44]` x 0.01 s. That value is **not** the leaderboard record - a
  2:25.18 run saved while the board held 2:24.90 - so it is a separate
  best-ghost time. Identity still unconfirmed.

**Rebuild trap:** the settings *file* is the only channel between the menu copy
of the blob (`0x004BF580`) and the race copy (`0x006393A0`) - nothing copies
between them in memory, which is why the race engine writes all 0x597 bytes
back on exit. A rebuild that "helpfully" keeps the two in sync in RAM will
diverge from the original on any path that skips a save.

Compatibility traps to honour in a rebuild: the loader demands *exactly*
302,412 bytes; lap times are int16 centiseconds and overflow past 327.68 s; a
wrong-track ghost silently disables *saving* new records; the shipped directory
is `Ghosts\` while the code builds `GHOSTS\`, which is fatal on a
case-sensitive filesystem, so this bites on the Linux target.

## Phase 6 — simulation

Physics, collision, AI and the 72 Hz fixed-step driver at `0x00422680`, ported
as one group behind the trace harness. All of it is x87 double precision with
transcendentals; rewriting it in C or SSE will drift from the original
trajectories, so the x87 behaviour is kept here rather than reasoned about.

Two hazards recorded from the mapping: a single CRT random generator is shared
between the renderer, AI and effects and is stepped by drawing, so replacing
the renderer perturbs the simulation unless the draw-side calls are preserved;
and `0x004537DC` reprograms the x87 control word, reachable only with
perspective-correct polygons enabled.

## Phase 7 — native 64-bit build

Only possible once no original x86 code remains in the process. The x87
precision decision has to be made explicitly here, not inherited by accident.

## Layout

```
_re/                      reverse-engineering workspace
  00_overview.md          confirmed binary facts
  api_surface.txt         IAT + COM vtable scan output
  notes/                  per-subsystem findings
  tools/                  capstone-based analysis scripts
ign_compat/               the compatibility layer
  src/common/             D3D11 presenter, logging
  src/ddraw|dsound|dinput|dplayx/
  build.sh                32-bit mingw-w64 build
```

Build: `./ign_compat/build.sh` → `ign_compat/build/*.dll`.
Deploy: copy the DLLs next to `Ign_win.exe`. Set `IGN_COMPAT_LOG=1` to trace.
