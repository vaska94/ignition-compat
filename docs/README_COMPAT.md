# Ignition — modern Windows compatibility layer

Runs the original 1997 `Ign_win.exe` on Windows 10/11 by replacing its
DirectDraw path with Direct3D 11. **The game executable is not modified.**

## Install / uninstall

Copy `ddraw.dll` and `ign_compat.ini` next to `Ign_win.exe`. To uninstall,
delete them — the game reverts to the system DirectDraw exactly as before.

`dplayx.dll` is optional: only needed on machines where the DirectPlay optional
feature is absent, which otherwise stops the EXE loading at all. It disables
LAN play.

## ign_compat.ini

```ini
[ignition]
WINDOWED=0        ; 0 = borderless fullscreen, 1 = resizable window
WINDOW_SCALE=2    ; window size multiplier when WINDOWED=1
SCALING=aspect    ; aspect | integer | stretch
FILTER=sharp      ; sharp | point | linear
VSYNC=1
PIXEL_FORMAT=auto ; auto | p8 | rgb565 | xrgb888   (auto is correct)
BLOCK_JOYSTICK=1  ; required: the legacy joystick API crashes winmmbase
BLOCK_MCI=1       ; disables CD redbook music (optional anyway)
CPU_FIX=1         ; stops the main loop pinning a core
TRACE=0           ; 1 = log every DirectDraw call (slow)
```

`FILTER=sharp` keeps nearest-neighbour crispness while removing the uneven
pixel widths a non-integer upscale produces (640→1440 is 2.25×).
`SCALING=integer` gives pixel-exact output with wider borders.

## What it fixes

| Problem | Cause | Fix |
|---|---|---|
| Black screen | 8-bit exclusive-fullscreen DirectDraw | never change the display mode; scale on the GPU |
| Silent exit at startup | game calls `GetDC` on a surface | implemented over a DIB section |
| Grey / quarter-width image | engine writes 8-bit indices into a 32bpp stride | allocate by mode bpp, interpret as P8 |
| Crash entering a race | `joyGetDevCapsA`/`joyGetPosEx` AV in `winmmbase.dll` | IAT-hooked to report no joystick |
| 100% CPU on one core | main loop spins; `Sleep` is never imported | `PeekMessageA` hook yields when idle |
| Window stuck at 640×480 | Windows `640X480` compatibility shim | removed (see below) |

## Windows compatibility shims

The registry had `~ DWM8And16BitMitigation 640X480 WIN95` for this exe. The
`640X480` layer makes Windows report a 640×480 desktop, which prevents real
fullscreen. It has been removed; the original value is saved in
`_re/compat_layers_backup.txt`. To restore it:

```
HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers
  "C:\Games\IGNITION\Ign_win.exe" = "~ DWM8And16BitMitigation 640X480 WIN95"
```

## Notes

* There is **no CD copy protection** in this game — verified exhaustively. It
  runs with no disc; only redbook music is lost.
* The menu is hardcoded to 640×480 whatever the in-game resolution setting is,
  so its background art is always a low-res upscale.
* Startup failures in this game are silent by design (the error logger is
  compiled out and it calls `exit()`), so set `TRACE=1` when diagnosing.

Build from source: `./ign_compat/build.sh` (needs `i686-w64-mingw32-gcc`).
