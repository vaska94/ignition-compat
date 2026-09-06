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

## Phase 2 — asset formats

Ship-side data is already on disk in the clear: `.PIC`, `.TEX`, `.MSH`, `.PLC`,
`.COL`, `.SRF`, `.TRI`, `.SHD`, `.POS`, `.PAN`, `.TAB`, `.LFT`, `.FNT`, `.PAT`.
Build readers and viewers. These double as ground truth when reversing the
renderer — if our C decoder and the original agree on every byte of a track,
the format is right.

## Phase 3 — the software rasterizer

The hard part, and the gate on higher internal resolution.

The hot loops live in the PE section named `code` at `0x0064E000`, which is
**writable and executable** and uses **self-modifying immediate operands**.
Static disassembly alone will not tell us what those loops execute. Plan:
instrument the shim to snapshot that section at runtime, capture the variants
the game actually patches in, and reverse each. Validate by rendering a frame
in C and diffing pixel-for-pixel against the original.

Only once this is understood can the resolution be raised — 1997 code routinely
hardcodes buffer strides and relies on 16.16 fixed-point range assumptions that
overflow well before modern resolutions.

## Phase 4 — game logic to C

Physics, AI, race state, menus. Diffed against the running original.

## Phase 5 — native 64-bit build

Drop the shim layer; link the renderer straight to D3D11/D3D12.

---

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
