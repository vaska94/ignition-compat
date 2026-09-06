# Ignition (`Ign_win.exe`) — complete DirectDraw surface specification

Target: write a drop-in `ddraw.dll` that satisfies exactly this usage on top of D3D11.

Binary: PE32 i386, ImageBase `0x00400000`.
Sections: `.text` `0x00401000‑0x00478B8C`, `.rdata` `0x00479000`, `.data` `0x0047C000‑0x0064BF80` (RW),
`.idata` `0x0064C000`, `STACK` `0x0064D000`, `code` `0x0064E000‑0x0064F597` (RW, hand-written rasterizer),
`.rsrc`, `.reloc`.

Only DDRAW import: `DDRAW.dll!DirectDrawCreate` at IAT slot `0x0064C29C`, thunk `0x0047879C`.

---

## 0. Executive summary

* **One** `IDirectDraw` object, **8-bit palettized only**, **fullscreen exclusive** by default.
* Display modes ever requested: **320x200x8, 640x480x8, 800x600x8** (`DDSCL_ALLOWMODEX` is set, so
  320x200 is a Mode X request).
* Primary surface is a **flipping chain**: `DDSCAPS_PRIMARYSURFACE|DDSCAPS_FLIP|DDSCAPS_COMPLEX`
  with `dwBackBufferCount` taken from a global; every code path in the game sets it to **1**
  (the code refuses > 4).
* The rasterizer never draws into DirectDraw memory. It renders into a **system-memory byte
  buffer in `.data`** and the frame is pushed with a hand-written `rep movsd` copy into a
  `Lock`ed back buffer, then `Flip`.
* Total DirectDraw API surface actually used: **21 vtable slots** across 4 interfaces
  (list in §7).
* There is a windowed code path (`DDSCL_NORMAL` + clipper) but it is **structurally dead**
  (see §5) — it never presents. Default is fullscreen.

---

## 1. Display-init function `0x0045B740`

Registered as driver entry `[0x0050EB6C]` (see §8), invoked through wrapper `0x00456BC0`.
Returns 1 on success, 0 on failure, and pops a `0x94`-byte frame.

### 1.1 Window creation (`0x0045B74F‑0x0045B783`)

```
0045B74F  push 0                       ; lpParam
0045B751  mov  eax,[0x004C5398]        ; hInstance (saved in WinMain @0x004120B2)
0045B756  push eax
0045B757  mov  esi,[0x0064C3E0]        ; USER32!GetSystemMetrics
0045B75D  push 0                       ; hMenu
0045B75F  push 0                       ; hWndParent
0045B761  push 1 / call esi            ; GetSystemMetrics(SM_CYSCREEN)
0045B765  push eax                     ; nHeight
0045B766  push 0 / call esi            ; GetSystemMetrics(SM_CXSCREEN)
0045B76A  push eax                     ; nWidth
0045B76B  push 0                       ; Y
0045B76D  push 0                       ; X
0045B76F  push 0x80080000              ; dwStyle
0045B774  push 0x00493778              ; "Ignition"   (lpWindowName)
0045B779  push 0x00493740              ; "Ignition"   (lpClassName)
0045B77E  push 0x00040000              ; dwExStyle
0045B783  call [0x0064C440]            ; CreateWindowExA
```

* `dwExStyle = WS_EX_APPWINDOW (0x00040000)`
* `dwStyle   = WS_POPUP (0x80000000) | WS_SYSMENU (0x00080000)`
* Size = full desktop, position 0,0.
* HWND stored to **`0x004C539C`** and **`0x00493738`**.
* Followed immediately by `UpdateWindow(hwnd)` and `SetFocus(hwnd)`.

Window class is registered in the WinMain-ish function at `0x004120A0`:
`style = CS_DBLCLKS (8)`, `lpfnWndProc = 0x004122D0`,
`hIcon = LoadIconA(hInst, MAKEINTRESOURCE(0x7F00))`,
`hCursor = LoadCursorA(NULL, IDC_ARROW)`,
`hbrBackground = GDI32!GetStockObject(4 /*BLACK_BRUSH*/)` — the *only* other GDI call in the game.

### 1.2 `DirectDrawCreate`

```
0045B7BD  push 0            ; pUnkOuter
0045B7BF  push 0x00512C50   ; &g_lpDD
0045B7C4  push 0            ; lpGUID = NULL  (primary display device)
0045B7C6  call 0x0047879C   ; -> jmp [0x0064C29C] DDRAW!DirectDrawCreate
```
Failure (`!= DD_OK`) aborts init. Called exactly once in the binary.

### 1.3 `SetCooperativeLevel` (`0x0045B7FA`, the only site)

```
0045B7DC  cmp  dword [0x00493728],0    ; g_fullscreen
0045B7E3  je   0045B7E9
0045B7E5  push 0x53                    ;  fullscreen flags
0045B7E7  jmp  0045B7EB
0045B7E9  push 8                       ;  windowed flags
0045B7EB  mov  eax,[0x004C539C]        ; hwnd
      ... push hwnd ; push this ; call [eax+0x50]
```
* fullscreen: `0x53 = DDSCL_FULLSCREEN(0x01) | DDSCL_ALLOWREBOOT(0x02) | DDSCL_EXCLUSIVE(0x10) | DDSCL_ALLOWMODEX(0x40)`
* windowed:   `0x08 = DDSCL_NORMAL`

`g_fullscreen` = **`0x00493728`**, initialised in the file image to **1**.
Toggled by `0x0040464B/0x004046A0` (Alt-Enter style toggle) and `0x00412416`.

### 1.4 `SetDisplayMode` — 2 call sites, both `SetDisplayMode(w, h, bpp)` (DirectDraw1 signature, 3 args)

```
0045B817  mov eax,[0x004BA6E8]   ; bpp
0045B81C  mov ecx,[0x004BA6E4]   ; height
0045B823  mov edx,[0x004BA6E0]   ; width
      push eax ; push ecx ; push edx ; push this ; call [ebx+0x54]
```
| site | when |
|---|---|
| `0x0045B833` | initial init, fullscreen only |
| `0x0045BE16` | inside the *mode-change* routine `0x0045BD70`, fullscreen only |

Mode globals (all in `.data`, values are literal in every writer — verified by exhaustive xref):

| global | meaning | file-image default | every value ever written |
|---|---|---|---|
| `0x004BA6E0` | width  | `0x280` (640) | `0x140` (320), `0x280` (640), `0x320` (800) |
| `0x004BA6E4` | height | `0x1E0` (480) | `0xC8` (200), `0x1E0` (480), `0x258` (600) |
| `0x004BA6E8` | bpp    | `8` | **always literal `8`** |
| `0x004BA6EC` | dwBackBufferCount | `1` | always `1` at every writer (esi=1); code errors out if `>= 5` |

Writers: `0x00403B46`, `0x00417F07`, `0x00417F50`, `0x0041850A`, `0x00418566`, `0x004185B5`,
`0x00418F89`, `0x0043DEB5`, `0x0043DFED`, `0x0043E11D`.
There is a mode table of `{DWORD w; DWORD h;}` at **`0x0047DC30`** = `{320,200},{640,480},{800,600}`,
indexed by `[0x004BE730]`.

**Conclusion: the shim only ever has to serve 320x200x8, 640x480x8, 800x600x8.**

### 1.5 GDI probe (`0x0045B84B‑0x0045B873`)

```
0045B84B  push 0 / call [0x0064C3E8]   ; GetDC(NULL)
0045B855  push 0xC  / push hdc / call GetDeviceCaps   ; BITSPIXEL
0045B862  push 0xE  / push hdc / call GetDeviceCaps   ; PLANES
0045B867  imul ebx,eax
0045B86B  mov [0x004BAC90],ebx         ; desktop bpp
0045B873  ReleaseDC(NULL,hdc)
```
`0x004BAC90` is **written here and never read anywhere in the binary** — dead. This is the only
`GetDeviceCaps` usage. No `IDirectDrawSurface::GetDC/ReleaseDC` anywhere (slots `+0x44`/`+0x68`
have zero call sites).

### 1.6 Windowed-mode window fixup (`0x0045B879‑0x0045B988`, `g_fullscreen == 0` only)

`GetWindowLongA(GWL_STYLE)`, `&= 0x7FFFFFFF` (drop WS_POPUP), `|= 0x00C60000`
(`WS_CAPTION|WS_THICKFRAME|WS_MINIMIZEBOX`), `SetWindowLongA`;
`SetRect(&rc,0,0,0x280,0x1E0)` → **client area hard-coded 640x480**;
`AdjustWindowRectEx`, `SetWindowPos` x2, `SystemParametersInfoA(SPI_GETWORKAREA=0x30)`,
`GetWindowRect`, final `SetWindowPos`.

---

## 2. Surface creation

A zeroed `DDSURFACEDESC` is built on the stack in both init (`0x0045B98A`) and mode-change
(`0x0045BE27`): `lea edi,[esp+X]; xor eax,eax; mov ecx,0x1B; rep stosd` (27 dwords = **108 bytes**),
then `dwSize = 0x6C`.

Exactly **four** `IDirectDraw::CreateSurface` call sites exist in the whole binary.

### 2.1 Fullscreen primary — flipping chain (`0x0045B9DC` init, `0x0045BE79` mode-change)

```
   ddsd.dwSize            = 0x6C
   ddsd.dwFlags           = 0x21   ; DDSD_CAPS | DDSD_BACKBUFFERCOUNT
   ddsd.dwBackBufferCount = [0x004BA6EC]        ; always 1
   ddsd.ddsCaps.dwCaps    = 0x218  ; DDSCAPS_PRIMARYSURFACE|DDSCAPS_FLIP|DDSCAPS_COMPLEX
   CreateSurface(&ddsd, &g_primaryIface /*0x0050E7A4*/, NULL)
```
Evidence (`0x0045B9C7` / `0x0045B9CF` write `ddsd+0x04` and `ddsd+0x68`):
```
0045B9C7  mov dword [esp+0x4c],0x21          ; ddsd.dwFlags
0045B9CF  mov dword [esp+0xb0],0x218         ; ddsd.ddsCaps.dwCaps
0045B9C2  mov [esp+0x58],eax                 ; ddsd.dwBackBufferCount
0045B9DC  call [eax+0x18]                    ; IDirectDraw::CreateSurface
```
No width/height/pixel-format is supplied — the primary inherits the display mode.

Then back buffers are walked out of the chain with `GetAttachedSurface`
(`0x0045BA94` init, `0x0045BF2B` mode-change):
```
   caps.dwCaps = 4          (DDSCAPS_BACKBUFFER)   for the first hop
   caps.dwCaps = 0x10       (DDSCAPS_FLIP)         for each subsequent hop
   GetAttachedSurface(cur, &caps, &next)
```
Failure raises the MessageBox **"Backbuffer couldn't be obtained"** (`0x004BAC94`).
`dwBackBufferCount >= 5` raises **"The maximum amount of backbuffers is exceeded"** (`0x004BACB4`).

### 2.2 Windowed primary — plain (`0x0045BBB4`)

```
   ddsd.dwFlags        = 0x01     ; DDSD_CAPS
   ddsd.ddsCaps.dwCaps = 0x200    ; DDSCAPS_PRIMARYSURFACE
   CreateSurface(&ddsd, &g_primaryIface, NULL)
```

### 2.3 Windowed offscreen "back buffers" (`0x0045BC6D`, loop, N = `[0x004BA6EC]`)

```
   ddsd.dwFlags        = 0x07     ; DDSD_CAPS|DDSD_HEIGHT|DDSD_WIDTH
   ddsd.dwWidth        = 0x280 (640)     ; literal, NOT the mode width
   ddsd.dwHeight       = 0x1E0 (480)     ; literal
   ddsd.ddsCaps.dwCaps = 0x40     ; DDSCAPS_OFFSCREENPLAIN
```
No `DDSD_PIXELFORMAT` — the surface inherits the desktop format.

### 2.4 Never-created surfaces

There is a third array of 20 surface slots (§3) that is only ever zeroed, null-checked and
released. **No `CreateSurface` targets it.** Dead.

### 2.5 Surface bookkeeping struct (0x30 bytes)

Every DD surface is wrapped in this game struct. Layout recovered from
`0x00456A40` (zero-init), `0x0045BA9F` (init fill) and `0x0045C530` (Lock):

| off | meaning |
|---|---|
| `+0x00` | `in use` flag (1) |
| `+0x04` | `restored` flag (set to 1 after `Restore`) |
| `+0x08` | `is locked` (0/1) |
| `+0x0C` | lock mode: 0 = unlocked, 1 = read-only, 2 = read/write, 3 = write-only |
| `+0x10` | read pointer  (`ddsd.lpSurface` when mode 1 or 2, else 0) |
| `+0x14` | write pointer (`ddsd.lpSurface` when mode 2 or 3, else 0) |
| `+0x18` | `ddsd.lPitch` (bytes) |
| `+0x1C` | width |
| `+0x20` | height |
| `+0x24` | bpp |
| `+0x28` | kind: **0 = primary, 1 = back buffer, 2 = generic offscreen** |
| `+0x2C` | `LPDIRECTDRAWSURFACE` |

Three static arrays of this struct, all stride `0x30`
(zeroed/typed by `0x00456A40`, called at the top of init and of mode-change):

| array | struct bases | count | `+0x28` |
|---|---|---|---|
| back buffers | `0x0050E688` … `0x0050E748` | 5 | 1 |
| primary | `0x0050E778` | 1 | 0 |
| generic offscreen | `0x0050E7A8` … `0x0050EB38` | 20 | 2 |

So: `g_primaryIface = 0x0050E7A4`, `g_backbuf0Iface = 0x0050E6B4`,
back-buffer-0 lpSurface cache = `0x0050E69C`, its pitch cache = `0x0050E6A0`.

---

## 3. Bit depth and palette

**8-bit palettized, exclusively.** `0x004BA6E8` (bpp) is assigned the literal `8` at every one
of its 10 writers; no 16/24/32-bit path exists. The blit helpers are byte-granular.

### 3.1 `CreatePalette` — 2 sites, both fullscreen-only

```
0045BB52  push 0            ; pUnkOuter
          push 0x00512448   ; &g_lpDDPalette
          push 0x00512048   ; lpDDColorArray  (init site)
          push 0x44         ; dwFlags
          push this ; call [ebx+0x14]
0045BFE9  ... same but lpDDColorArray = 0x00512850   (mode-change site)
```
`dwFlags = 0x44 = DDPCAPS_8BIT (0x04) | DDPCAPS_ALLOW256 (0x40)`.

The seed table is built inline just before each call: entry 0 = `{0,0,0,·}`, entries 1..255 =
`{255,255,255,·}` (`peFlags` byte left 0). Buffers are `256 * sizeof(PALETTEENTRY)` = `0x400`:
* `0x00512048 … 0x00512448` (init seed)
* `0x00512850 … 0x00512C50` (mode-change seed)
* `0x00512450 … 0x00512850` (**the live table used by `SetEntries`**)

`g_lpDDPalette = 0x00512448`. `IDirectDrawSurface::SetPalette(primary, pal)` at
`0x0045BD30` (init) and `0x0045C028` (mode-change). **No palette in windowed mode.**

### 3.2 `SetEntries` — the only site, `0x0045C718`, in helper `0x0045C6D0`

```c
BOOL SetGamePalette(const BYTE *rgb768)          // 0x0045C6D0
{
    if (!g_lpDDPalette) return FALSE;
    for (i = 0; i < 256; i++) {                  // expand 3-byte RGB -> PALETTEENTRY
        pe[i].peRed   = rgb768[3*i+0];
        pe[i].peGreen = rgb768[3*i+1];
        pe[i].peBlue  = rgb768[3*i+2];           // peFlags left untouched
    }                                            // pe == 0x00512450
    return SUCCEEDED(pal->SetEntries(0 /*dwFlags*/, 0 /*start*/, 256 /*count*/, pe));
}
```
Call: `push 0x00512450; push 0x100; push 0; push 0; push pal; call [eax+0x18]`.
Values are copied **verbatim** — no `<<2` VGA expansion, so the source is already 0..255.

The 768-byte source buffer is `malloc(0x300)` at `0x00403ECB`, stored in **`0x004BEB38`**.
`sys.col` on disk is **776 bytes = 8-byte header + 256x3 RGB**, matching exactly
(strings `"SYS.COL"`, `"Error while loading SYS.COL"`, `"LEVELS\%s%s.COL"`,
`"baltazar\data\menu.col"`).

Wrapper `0x00456C40` (→`[0x0050EB98]`) is called from ~30 sites all over the game
(fades, level load, menus) — this is the hot palette path.

---

## 4. The present path

### 4.1 Locking wrapper `0x0045C530` — `LockSurface(surf, mode)`

```
0045C5A9  mov dword [esp+0xc],0x6c      ; ddsd.dwSize = 108
   mode 1: Lock(this, NULL, &ddsd, 0x11, NULL)   ; DDLOCK_WAIT|DDLOCK_READONLY
   mode 2: Lock(this, NULL, &ddsd, 0x01, NULL)   ; DDLOCK_WAIT
   mode 3: Lock(this, NULL, &ddsd, 0x21, NULL)   ; DDLOCK_WAIT|DDLOCK_WRITEONLY
0045C650  mov eax,[esp+0x1c]            ; ddsd.lPitch     -> surf[+0x18]
0045C5CD  mov ecx,[esp+0x30]            ; ddsd.lpSurface  -> surf[+0x10]/[+0x14]
0045C65B  mov dword [esi+8],1
```
Before locking it does `IsLost()`; on `DDERR_SURFACELOST (0x887601C2)` it calls `Restore()`
and sets `surf[+0x04] = 1`. `lpDestRect` is **always NULL** (whole-surface lock).
Only `ddsd.lpSurface` and `ddsd.lPitch` are read back — nothing else.

### 4.2 Unlock wrapper `0x0045C680`

```
0045C69B  lea ecx,[esi+0x2c]
0045C69E  push ecx            ; <-- lpSurfaceData argument
0045C69F  mov eax,[ecx]
0045C6A1  push eax            ; this
0045C6A4  call [eax+0x80]     ; IDirectDrawSurface::Unlock
```
**Important for the shim:** the `lpSurfaceData` argument is `&surf->lpDDSurface`, i.e. a pointer
to the game's own struct field, **not** the surface memory and **not** a `RECT*`.
The shim must ignore this parameter entirely. Same at the other site `0x0045C56F`.

### 4.3 System→video copy `0x0045C1D0` (driver slot `[0x0050EB80]`, wrapper `0x00456B70`)

9 cdecl args:
`SwBlt(srcBase, srcPitch, srcX, srcY, widthBytes, rows, dstSurf, dstX, dstY)`

```
0045C24C  ecx = srcBase + srcPitch*srcY + srcX
0045C259  edx = dstSurf[+0x18]                    ; lPitch
0045C26E  ebx = dstSurf[+0x14] + lPitch*dstY + dstX
          ... rep movsd, unrolled 0xA0 bytes/iteration when widthBytes % 160 == 0
```
Lock management: if `dstSurf[+0x0C] == 0` → `Lock(dst, 3)`; if `== 1` (read-only) →
`Unlock` then `Lock(dst, 3)`. After the copy the previous lock state is restored
(`0x0045C35A‑0x0045C3A5`). Byte-granular ⇒ 8bpp.

### 4.4 Flip wrapper `0x0045C4B0` (driver slot `[0x0050EB88]`, wrapper `0x00456C90`)

```
0045C4B8  cmp dword [esi+0x28],1        ; only "back buffer" kind
0045C4D1  eax = esi[+0x2c]
0045C4DC  call [eax+0x38]               ; IDirectDrawSurface::GetCaps(&caps)
0045C4DF  test byte [esp+4],0x10        ; DDSCAPS_FLIP ?
0045C4E4  jne  0045C4ED   (else return 0)
0045C4ED: push 0                        ; dwFlags = 0
          push esi[+0x2c]               ; lpDDSurfaceTargetOverride = the back buffer
          push [0x0050E7A4]             ; this = the PRIMARY
0045C4FC  call [eax+0x2c]               ; IDirectDrawSurface::Flip
0045C4FF  cmp eax,0x8876021C            ; DDERR_WASSTILLDRAWING
0045C504  je  0045C4ED                  ; busy-spin retry
```
Note `Flip` is invoked on the **primary**, with the back buffer as
`lpDDSurfaceTargetOverride`, and `dwFlags = 0` (no `DDFLIP_WAIT`); the game spins on
`DDERR_WASSTILLDRAWING` itself.

### 4.5 The actual per-frame sequence

**Game/3D view — `0x00446410`:**
```c
if (*(DWORD*)0x004949C4 == 1)                 // never true: initialised to 0, never written
    SwBlt(0x00563DB0, W, 0,0, W, H, &g_surf_primary /*0x0050E778*/, 0,0);
else {
    SwBlt(0x00563DB0, W, 0,0, W, H, &g_surf_backbuf0 /*0x0050E688*/, 0,0);
    Flip(&g_surf_backbuf0);                   // 0x0044646A
}
// W = [0x00553000], H = [0x00563C10]
```
**Menu/2D — `0x00403DA0`:** identical, but `srcBase = [0x004BE840]` (heap) and
`W,H` come from the mode table `0x0047DC30[ [0x004BE730] ]`. Ends with `Flip(&g_surf_backbuf0)`.

**Mode-change/reset — `0x00403B10`:** `SetGamePalette([0x004BEB38])`, `Clear(&g_surf_backbuf0)`,
`Flip(&g_surf_backbuf0)`.

So the concrete DirectDraw sequence per presented frame is exactly:

```
[optional] IsLost(backbuf)   -> if DDERR_SURFACELOST: Restore(backbuf)
IDirectDrawSurface::Lock  (backbuf, NULL, &ddsd{dwSize=0x6C}, DDLOCK_WAIT|DDLOCK_WRITEONLY, NULL)
<memcpy rows from system memory using ddsd.lpSurface / ddsd.lPitch>
IDirectDrawSurface::Unlock(backbuf, <garbage ptr>)
IDirectDrawSurface::GetCaps(backbuf, &caps)             ; tests DDSCAPS_FLIP
do { hr = IDirectDrawSurface::Flip(primary, backbuf, 0); } while (hr == DDERR_WASSTILLDRAWING);
```

### 4.6 Framebuffer globals

The hand-written rasterizer in the `code` section (`0x0064E000`) does **not** touch any
DirectDraw surface. A data-reference sweep of that whole section shows it only touches its own
parameter blocks (`0x004BA584‑0x004BA648`, `0x004BADA4‑0x004BAEE8`, `0x0049CA0C…`,
`0x0063F2B0…`) plus self-modification targets inside `0x0064E000‑0x0064F597`.

| global | role |
|---|---|
| **`0x00563DB0`** | static byte array in `.data`: the 3D software framebuffer (pushed as an immediate, so it *is* the buffer, not a pointer). Cleared via `mov edi,0x563DB0` at `0x00418426`. |
| **`0x00553000`** | render width (`0x140` at `0x00417FBC`, `0x00418610`) |
| **`0x00563C10`** | render height (`0xC8` …) |
| **`0x004BE840`** | `malloc`'d 2D/menu framebuffer pointer (assigned `0x004029F6`) |
| `surf[+0x14]` / `surf[+0x18]` | the DD `lpSurface` / `lPitch` cache (e.g. `0x0050E69C` / `0x0050E6A0` for back buffer 0) |

---

## 5. Clipper usage / windowed mode

`IDirectDraw::CreateClipper` — one site, `0x0045BCB1`, **windowed path only**:
```
0045BCA0  push 0            ; pUnkOuter
          push 0x004BAC8C   ; &g_lpDDClipper
          push 0            ; dwFlags
          push this ; call [ebx+0x10]
0045BCE3  call [eax+0x20]   ; IDirectDrawClipper::SetHWnd(0, hwnd)
0045BD06  call [eax+0x70]   ; IDirectDrawSurface::SetClipper(primary, clipper)
```
`g_lpDDClipper = 0x004BAC8C`. Released at `0x0045C07C` (windowed only).

**Windowed mode is dead in practice.** Evidence:
1. `g_fullscreen (0x00493728)` = `1` in the file image.
2. In windowed mode the "back buffers" are `DDSCAPS_OFFSCREENPLAIN`, so
   `GetCaps` in the Flip wrapper does not report `DDSCAPS_FLIP` and `0x0045C4B0` returns 0
   **without presenting anything**.
3. The `BltFast` wrapper `0x0045C160` — the only thing that could blit an offscreen surface to a
   clipped primary — is registered at `[0x0050EB7C]` and reachable through wrapper `0x00456B30`,
   but **`0x00456B30` has zero callers** in the binary.
4. No `IDirectDrawSurface::Blt` (`+0x14`) call site exists at all.

A shim can therefore treat `DDSCL_NORMAL` as "must not crash", not "must render".

---

## 6. GDI usage

Complete list (GDI32 imports are only `GetDeviceCaps` and `GetStockObject`):

| site | call | purpose |
|---|---|---|
| `0x0045B85E`, `0x0045B865` | `GetDeviceCaps(hdc, BITSPIXEL/PLANES)` on `GetDC(NULL)` | product stored to `0x004BAC90`, **never read** |
| `0x0041210C` | `GetStockObject(BLACK_BRUSH)` | `WNDCLASS.hbrBackground` |

**No `IDirectDrawSurface::GetDC` / `ReleaseDC`** (`+0x44`, `+0x68` have zero call sites), no
`FlipToGDISurface`, no GDI drawing onto surfaces.

---

## 7. Complete DirectDraw method list (exhaustive whole-binary scan)

Method resolved from the vtable byte offset of every `call [reg+disp]` in `.text` and `code`,
with the `this` provenance traced back to the loading `mov reg,[global]`. Object globals:
`g_lpDD = 0x00512C50`, `g_lpDDPalette = 0x00512448`, `g_lpDDClipper = 0x004BAC8C`,
`g_primarySurface = [0x0050E7A4]`, back buffers `[0x0050E6B4 + i*0x30]`.
(Indexed indirect calls were checked separately — the only four in the binary,
`0x0044C27E`, `0x0046E192`, `0x0046F163`, `0x0046F19F`, are a jump table and DirectPlay, not DDraw.)

### IDirectDraw (6 methods)

| slot | +off | method | call sites |
|---|---|---|---|
| 2 | `+0x08` | `Release` | `0x0045C130` |
| 4 | `+0x10` | `CreateClipper` | `0x0045BCB1` |
| 5 | `+0x14` | `CreatePalette` | `0x0045BB52`, `0x0045BFE9` |
| 6 | `+0x18` | `CreateSurface` | `0x0045B9DC`, `0x0045BBB4`, `0x0045BC6D`, `0x0045BE79` |
| 20 | `+0x50` | `SetCooperativeLevel` | `0x0045B7FA` |
| 21 | `+0x54` | `SetDisplayMode` | `0x0045B833`, `0x0045BE16` |

Not used: `QueryInterface`, `AddRef`, `Compact`, `DuplicateSurface`, `EnumDisplayModes`,
`EnumSurfaces`, `FlipToGDISurface`, `GetCaps`, `GetDisplayMode`, `GetFourCCCodes`,
`GetGDISurface`, `GetMonitorFrequency`, `GetScanLine`, `GetVerticalBlankStatus`, `Initialize`,
`RestoreDisplayMode`, `WaitForVerticalBlank`.

### IDirectDrawSurface (11 methods)

| slot | +off | method | call sites |
|---|---|---|---|
| 2 | `+0x08` | `Release` | `0x0045BD8F`, `0x0045BDB2`, `0x0045C097`, `0x0045C0C2`, `0x0045C0E5` |
| 7 | `+0x1C` | `BltFast` | `0x0045C1B4` *(unreachable, see §5)* |
| 11 | `+0x2C` | `Flip` | `0x0045C4FC` |
| 12 | `+0x30` | `GetAttachedSurface` | `0x0045BA94`, `0x0045BF2B` |
| 14 | `+0x38` | `GetCaps` | `0x0045C4DC` |
| 24 | `+0x60` | `IsLost` | `0x0045C58F`, `0x0045C74A`, `0x0045C77B`, `0x0045C7B0` |
| 25 | `+0x64` | `Lock` | `0x0045C5C7`, `0x0045C5F4`, `0x0045C61B` |
| 27 | `+0x6C` | `Restore` | `0x0045C59F`, `0x0045C75E`, `0x0045C78B`, `0x0045C7C0` |
| 28 | `+0x70` | `SetClipper` | `0x0045BD06` |
| 31 | `+0x7C` | `SetPalette` | `0x0045BD30`, `0x0045C028` |
| 32 | `+0x80` | `Unlock` | `0x0045C56F`, `0x0045C6A4` |

Not used: `QueryInterface`, `AddRef`, `AddAttachedSurface`, `AddOverlayDirtyRect`, **`Blt`**,
`BltBatch`, `DeleteAttachedSurface`, `EnumAttachedSurfaces`, `EnumOverlayZOrders`,
`GetBltStatus`, `GetClipper`, `GetColorKey`, `GetDC`, `GetFlipStatus`, `GetOverlayPosition`,
`GetPalette`, `GetPixelFormat`, `GetSurfaceDesc`, `Initialize`, `ReleaseDC`, `SetColorKey`,
`SetOverlayPosition`, `UpdateOverlay*`.

### IDirectDrawPalette (2 methods)

| slot | +off | method | call sites |
|---|---|---|---|
| 2 | `+0x08` | `Release` | `0x0045BB02`, `0x0045BDDF`, `0x0045BF99`, `0x0045C112` |
| 6 | `+0x18` | `SetEntries` | `0x0045C718` |

### IDirectDrawClipper (2 methods)

| slot | +off | method | call sites |
|---|---|---|---|
| 2 | `+0x08` | `Release` | `0x0045C07C` |
| 8 | `+0x20` | `SetHWnd` | `0x0045BCE3` |

**Plus the export `DirectDrawCreate`.** That is the entire shim surface: 21 vtable entries +
1 export. All other vtable slots must still exist (correct layout) but may be stubs returning
`E_NOTIMPL` — the game never calls them.

---

## 8. Video-driver dispatch table (context)

`0x0045B690` installs the DirectDraw "driver" into a function-pointer table; `0x00456AF0`
selects it (arg 0 ⇒ DDraw). Wrappers in `0x00456B10‑0x00456CA0` are what the rest of the game calls.

| slot | fn | role | wrapper |
|---|---|---|---|
| `0x0050EB68` | `0x0045B730` | stub → 2 | `0x00456B10` |
| `0x0050EB6C` | `0x0045B740` | **Init** | `0x00456BC0` |
| `0x0050EB70` | `0x0045BD70` | **Change display mode** | `0x00456BE0` |
| `0x0050EB74` | `0x0045C060` | **Shutdown** | `0x00456BF0` |
| `0x0050EB78` | `0x0045C150` | stub → 2 | `0x00456C60` |
| `0x0050EB7C` | `0x0045C160` | BltFast (dead) | `0x00456B30` *(no callers)* |
| `0x0050EB80` | `0x0045C1D0` | **SwBlt (sysmem → locked surface)** | `0x00456B70` |
| `0x0050EB84` | `0x0045C3D0` | Clear surface | `0x00456BB0` |
| `0x0050EB88` | `0x0045C4B0` | **Flip** | `0x00456C90` |
| `0x0050EB8C` | `0x0045C520` | stub → 2 | — |
| `0x0050EB90` | `0x0045C530` | Lock | `0x00456C10` *(no callers)* |
| `0x0050EB94` | `0x0045C680` | Unlock | `0x00456C30` *(no callers)* |
| `0x0050EB98` | `0x0045C6D0` | **SetPalette entries** | `0x00456C40` |
| `0x0050EB9C` | `0x0045C730` | **Restore all lost surfaces** | `0x00456C50` |

`0x0045C730` (RestoreAll): for the primary and each back-buffer/generic slot with `+0x00 == 1`,
`IsLost()`; on `DDERR_SURFACELOST` → `Restore()` and set `+0x04 = 1`.

Mode-change `0x0045BD70` = release primary (fullscreen) + release all 20 generic surfaces +
release palette + re-zero the tables (`0x00456A40`) + `SetDisplayMode` + re-create the flip chain +
re-create palette + `SetPalette` + `RestoreAll` + `ShowWindow(SW_SHOW)`.

Shutdown `0x0045C060` = release clipper (windowed), primary (windowed only — asymmetric with
`0x0045BD70`, likely an original bug), back buffers (windowed only), all 20 generic slots,
palette (fullscreen), then `IDirectDraw::Release` and null `g_lpDD`.

---

## 9. Implementation hazards for the shim

1. **`Unlock`'s second argument is garbage** (`&surf->lpDDSurface`). Never dereference it, never
   treat it as a `RECT*`. Always treat the unlock as a full-surface unlock.
2. **The clear helper `0x0045C3D0` writes out of bounds.** It does
   `((DWORD*)surf[+0x14])[ lPitch*y + x ] = 0` for `x < width`, `y < height`, i.e. it uses a
   *byte* pitch as a *DWORD* index on an 8bpp surface — 4x the stride and 4x the row length.
   For 640x480 that touches ~1.2 MB starting at `lpSurface`. Real hardware absorbed this into
   VRAM. **The shim's staging buffer for a locked surface must be over-allocated** (recommend at
   least `4 * lPitch * height + slack`) or the game will corrupt/fault on the mode-change path
   (`0x00403B31`, guarded by `[0x004BE65C] == 0`, reached after each display-mode switch).
3. **Mode X**: `SetDisplayMode(320, 200, 8)` is requested with `DDSCL_ALLOWMODEX`. The shim must
   accept it and report a sane `lPitch` (320 is what the game's copy loop assumes to be efficient,
   but it reads whatever `lPitch` you report, so any pitch works).
4. `Flip` must return `DD_OK`; if it ever returns `DDERR_WASSTILLDRAWING (0x8876021C)` the game
   busy-spins. Anything else non-zero is treated as failure but the wrapper still returns
   "success" for `hr <= 1`, so avoid returning odd values.
5. `Lock` is always whole-surface (`lpDestRect == NULL`) with `dwSize == 0x6C` (DirectDraw1
   `DDSURFACEDESC`, **not** `DDSURFACEDESC2`). Fill at minimum `lPitch (+0x10)` and
   `lpSurface (+0x24)`.
6. `GetCaps` must set `DDSCAPS_FLIP (0x10)` on back buffers obtained from
   `GetAttachedSurface`, or the game will never present.
7. `GetAttachedSurface(DDSCAPS_BACKBUFFER)` on the primary must work for a chain created with
   `dwBackBufferCount = 1`.
8. `SetEntries(0, 0, 256, PALETTEENTRY[256])` on the primary's attached palette must recolour the
   already-presented frame too (fades are done by repeated `SetEntries` without redrawing —
   ~30 call sites go through `0x00456C40`).
9. Interfaces are queried purely by vtable offset; the shim must use the **DirectDraw1**
   vtable layouts (`IDirectDraw`, `IDirectDrawSurface`) — `DirectDrawCreate` with `lpGUID = NULL`.
