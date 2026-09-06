# Ignition (UDS / Virgin Interactive, 1997) — modernisation notes

## Binary under analysis

`Ign_win.exe` — PE32, i386, Windows GUI subsystem, linked 1997-08-28 19:33:05.
ImageBase `0x00400000`, entry `0x00469950`, symbols stripped, MSVC-era CRT.

| Section  | VA         | Size      | Notes |
|----------|------------|-----------|-------|
| `.text`  | `0x401000` | `0x77B8C` | main code (~490 KB) |
| `.rdata` | `0x479000` | `0x25A0`  | |
| `.data`  | `0x47C000` | `0x40E00` | |
| `.idata` | `0x64C000` | `0xC54`   | imports |
| `STACK`  | `0x64D000` | `0x200`   | |
| `code`   | `0x64E000` | `0x1597`  | read/write (NOT executable) hand-written asm |
| `.rsrc`  | `0x650000` | `0x6DAC`  | dialogs (EN / DE "Bleifuss Fun" / FR) |
| `.reloc` | `0x657000` | `0x16FD4` | |

## The single most important finding

**There is no Direct3D.** The import table contains no `d3dim`, `d3drm`, `d3d8`
or `d3d9`. Ignition is a *software rasterizer* — the 3D pipeline is hand-written
x86 in `.text`, with the hot inner loops living in the separate writable `code`
section (16.16 fixed-point arithmetic, self-modifying loop constants; this is
the classic 1997 "patch the immediate operands of the texel loop" trick).

DirectX is used only at the edges:

| DLL       | Imports | Role |
|-----------|---------|------|
| `DDRAW`   | `DirectDrawCreate` | get the framebuffer onto the screen |
| `DSOUND`  | `DirectSoundCreate` | audio output |
| `DINPUT`  | `DirectInputCreateA` | keyboard |
| `DPLAYX`  | ordinals `#1`, `#2` (`DirectPlayCreate`, `DirectPlayEnumerate`) | LAN play |
| `WINMM`   | `timeGetTime`, `timeSetEvent`, `timeBeginPeriod`, `joyGetPosEx`, `joyGetDevCapsA`, `mciSendStringA` | timing, joystick, CD audio + CD check |
| `GDI32`   | `GetDeviceCaps`, `GetStockObject` | window setup only |

So "port to modern DirectX" does **not** mean rewriting a 3D renderer against
D3D11 — it means replacing the DirectDraw/DirectSound/DirectInput/DirectPlay
edge, and leaving the (still perfectly fast) software rasterizer alone.

## Recovered COM interface usage

Derived by scanning every `call [reg+disp]` and grouping by the global that
held the interface pointer, then matching the slot set against the known
DirectX vtable layouts.

| Global | Interface | Slots observed |
|--------|-----------|----------------|
| `0x00512C50` | `IDirectDraw`        | Release, CreateClipper, CreatePalette, CreateSurface, SetCooperativeLevel, SetDisplayMode |
| `0x0050E7A4` | `IDirectDrawSurface` | Flip, IsLost, Restore, SetClipper, SetPalette |
| `0x0063C614` | `IDirectSound`       | Release, CreateSoundBuffer, GetCaps, SetCooperativeLevel |
| `0x0063C640` | `IDirectSoundBuffer` | Release, GetCaps, GetCurrentPosition, Lock, Play, Stop, Unlock, Restore |
| `0x0063C660` | `IDirectSoundBuffer` | (same set — second mixing buffer) |
| `0x0050E26C` | `IDirectInputDevice` | Release, SetProperty, Acquire, Unacquire, GetDeviceData, SetDataFormat, SetCooperativeLevel |
| `0x004BAB04` | `IDirectPlay*`       | wide slot spread |

Entry-point call sites: `DirectDrawCreate` @ `0x0045B7C6` (once),
`DirectSoundCreate` @ `0x004679E2` (once), `DirectPlayEnumerate` @ `0x0045A696`,
`DirectPlayCreate` @ `0x0045A851`.

## Why it breaks on Windows 10/11

1. **`dplayx.dll` is a static import.** DirectPlay is an off-by-default optional
   feature on Windows 8+. If it is absent the *loader* fails the process before
   a single instruction of game code runs.
2. **8-bit palettised exclusive-fullscreen DirectDraw.** Modern drivers and DWM
   handle legacy palettised mode-sets poorly — palette corruption, black
   screens, failed mode-sets.
3. **CD presence check** via `mciSendStringA` — no disc, no launch.
4. **Frame pacing** written for a ~166 MHz Pentium.

## Tooling in this tree

* `_re/tools/scan_api.py` — whole-binary IAT + COM-vtable surface scanner.
* `_re/tools/disasm.py <VA> [n]` — annotated disassembly; resolves import
  thunks and labels `call [reg+disp]` with candidate DirectX method names.
* Run with the venv interpreter that has capstone + pefile.
