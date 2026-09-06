# Ignition (`Ign_win.exe`, UDS / Virgin 1997) — startup, CD check, DirectPlay

Analysis target: `/mnt/c/Games/IGNITION/Ign_win.exe`
PE32 i386 GUI, ImageBase `0x00400000`, linker 4.20 (MSVC 4.2), entry `0x00469950`.
**No bytes of the EXE were modified.** Every patch below is a *proposal* only.

Address conversion for `.text` (VA `0x00401000`, raw `0x400`): **`file_off = VA − 0x400C00`**.
Section table:

| Section | VA | VirtSize | Raw off | Characteristics | Notes |
|---|---|---|---|---|---|
| `.text`  | `0x00401000` | `0x77B8C` | `0x400`   | `0x60000020` CODE/EXEC/READ | |
| `.rdata` | `0x00479000` | `0x25A0`  | `0x78000` | `0x40000040` | |
| `.data`  | `0x0047C000` | `0x1CFF80`| `0x7A600` | `0xC0000040` | raw ends at VA `0x004BCE00`; **everything ≥ that is BSS/zero** |
| `.idata` | `0x0064C000` | `0xC54`   | `0xBB400` | `0xC0000040` | |
| `STACK`  | `0x0064D000` | `0x200`   | `0xBC200` | `0xC0000040` | |
| `code`   | `0x0064E000` | `0x1597`  | `0xBC400` | `0xC0000040` | **real x86 code but NOT marked executable** |
| `.rsrc`  | `0x00650000` | `0x6DAC`  | `0xBDA00` | `0x40000040` | |
| `.reloc` | `0x00657000` | `0x16FD4` | `0xC4800` | `0x42000040` | |

`OPTIONAL_HEADER.DllCharacteristics = 0x0000` → **no `IMAGE_DLLCHARACTERISTICS_NX_COMPAT`**.
The section literally named `code` at `0x0064E000` contains hand-written assembly:

```
0x0064E000  bb84a54b00      mov ebx, 0x4ba584
0x0064E005  8a0d10a64b00    mov cl,  byte ptr [0x4ba610]
0x0064E00B  a084a54b00      mov al,  byte ptr [0x4ba584]
0x0064E022  8b530c          mov edx, dword ptr [ebx+0xc]
0x0064E02B  2bd0            sub edx, eax
0x0064E031  7507            jne 0x64e03a
```
(a fixed-point edge-setup / rasteriser routine), and **`.text` calls into it directly** — a
byte-level `E8` scan finds 70–102 call sites (the higher figure includes possible false positives
from data misread as code) reaching **10 distinct entry points**: `0x0064E000`, `0x0064E200`,
`0x0064E240`, `0x0064E270`, `0x0064E380`, `0x0064EA80`, `0x0064EC00`, `0x0064EC40`, `0x0064EFA0`,
`0x0064F0C0` — e.g. `0x00450106 → 0x0064E000`, `0x00450150 → 0x0064E240`,
`0x00450429 → 0x0064E380`. There are also ~350 base relocations targeting those pages, so it is a
properly relocated code section that is merely mis-flagged. Its characteristics are
`IMAGE_SCN_CNT_INITIALIZED_DATA|READ|WRITE` — **no `MEM_EXECUTE` (0x20000000) and no
`CNT_CODE` (0x00000020)**. The section header is at file offset `0x240`; `DllCharacteristics`
is the `00 00` at file offset `0xDE`.
*Consequence (inference, high confidence):* under the default Windows DEP policy (`OptIn`) a 32-bit
image without `/NXCOMPAT` is exempt, so this works. If the machine has DEP set to
**"Turn on DEP for all programs"** (`AlwaysOn` / `OptOut` without an exclusion) the process will
take an access violation the first time it enters that section. Adding an explicit DEP exclusion
(or setting `MEM_EXECUTE` on that section header) is the fix.

---

# Executive summary / practical checklist

Ordered by what will actually stop the game on Windows 10/11:

1. **`dplayx.dll` missing → the EXE will not even load.** Static ordinal-only import, no
   delay-load directory, no `LoadLibrary` fallback (§B.1). Fix: enable *Windows Features →
   Legacy Components → DirectPlay*, or drop a 2-export stub `dplayx.dll` next to the EXE (§B.8).
   Single-player never calls into it.
2. **8-bpp DirectDraw exclusive mode.** All three display modes are palettized 8-bit (§C.7a);
   `IDirectDraw::SetDisplayMode(w,h,8)` fails on Win8+. A DirectDraw wrapper is very likely needed.
3. **Silent failures.** Nearly every startup error is `sprintf` into a buffer nobody reads +
   `exit()` (§C.7). To get diagnostics, set `[0x004BAC88] = 1` (file `0xB9288`) and
   `[0x004BAC8A] = 1` (file `0xB928A`) so the game logs to `Default.fil`.
4. **DEP.** The section named `code` at `0x0064E000` holds hand-written asm that `.text` calls into
   102 times, yet it is not marked executable (see the section table above). Default `OptIn` DEP is
   fine; a machine set to `AlwaysOn` will crash the game.
5. **The CD is a non-issue.** No protection check exists at all; the "PLEASE INSERT THE IGNITION
   CD" screen is unreachable dead code, and CD music is optional and self-disabling (§A).
   Patches are proposed only in case MCI stalls (§A.6).
6. A missing sound card is harmless — `DirectSoundCreate` failure just sets a "mute" flag
   (§C.6a). DirectInput failure is fatal-and-silent (§C.6).
7. Working directory must be the game directory — all data paths are relative
   (`baltazar\data\…`, `LEVELS\%s%s.COL`), there is no `SetCurrentDirectoryA` import, and the
   installer's registry key is never read (§C.9).
8. Settings/progress live in **`ign_win.btz`** in the game directory (§C.7c) — absent on a fresh
   install, which is also why the game runs in English (§C.8). The game writes it (and
   `GHOSTS\*.GST`, `IGN%d.TGA` screenshots, `.\levels\<hex>conv.tab`, `Default.fil`) into its own
   directory with no fallback, so a read-only install directory both loses settings **and** makes
   the race-side loader `0x00419260` call `exit(1)` silently.
9. Good news for the readme's crash advice: `PERSP. POLY` (`0x00525E68`, struct `+0x10F`) and
   `MIP MAPPING` (`0x00527F78`, `+0x15B`) both **default to OFF**, and the default resolution is
   320×200 (§C.7b).

### Hard vs soft startup failures

| Fatal (aborts, silently) | Non-fatal |
|---|---|
| `RegisterClassA`; every step of `0x0045B740` incl. **`DirectDrawCreate`**, `SetCooperativeLevel`, `SetDisplayMode`, `CreateSurface`, `CreatePalette`/`SetPalette`, clipper setup | everything in `0x0045B170` (its return value is ignored) |
| every step of `0x00455AC0` except `Acquire`, incl. **`DirectInputCreateA`**, `CreateDevice`, `SetDataFormat`, `SetCooperativeLevel`, `SetProperty` | `IDirectInputDevice::Acquire` |
| `0x00417EA0` mode-set + asset loads → `exit(0)` | **`DirectSoundCreate`** on the `0x00403BC0` path → runs mute |
| `0x0041F9B0` sound bring-up → `exit(1)` | |
| `0x00419260` missing/short `ign_win.btz` → `exit(1)` | menu-side settings load `0x00404A60` (falls back to defaults) |

---

# A. The CD / media check

## A.0 Headline result

**There is no CD copy-protection gate in `Ign_win.exe`. Nothing needs to be patched to make the
game launch without a CD in the drive.**

Everything CD-related in this binary is *Redbook audio music playback*, and every playback path is
guarded by a "media present" flag that simply goes to 0 when there is no disc. In addition, the
"PLEASE INSERT THE IGNITION CD" screen that the shipped `readme.txt` complains about **is present
in the code but is unreachable in this build** — the init routine that selects that screen state
immediately advances past it, unconditionally (proof in §A.4).

Certainty: **high** for "no gate"; the reference scans below were done by exhaustive byte-level
search of every section for the little-endian dword of each global, not by a linear disassembly
sweep (which desynchronises on this binary — verified: a naive sweep misses the `CreateFileA`
call site at `0x004710F6`).

## A.1 Which Win32 APIs could implement a disc check — and which are actually imported

Full import list (from `IMAGE_DIRECTORY_ENTRY_IMPORT`):

* `KERNEL32.dll` — 64 names, all standard MSVC-CRT + file I/O.
* `USER32.dll`, `GDI32.dll` (only `GetDeviceCaps`, `GetStockObject`).
* `WINMM.dll` — `joyGetPosEx, joyGetDevCapsA, mciSendStringA, timeKillEvent, timeBeginPeriod, timeSetEvent, timeEndPeriod, timeGetTime`
* `DINPUT.dll` — `DirectInputCreateA`
* `DDRAW.dll` — `DirectDrawCreate`
* `DSOUND.dll` — `DirectSoundCreate`
* `DPLAYX.dll` — ordinals `#2`, `#1`

**Not imported at all:** `GetDriveTypeA`, `GetLogicalDrives`, `GetVolumeInformationA`,
`DeviceIoControl`, anything from `ADVAPI32` (so no registry either).
`FindFirstFileA` is imported but its only real call site is `0x0046A1B0` = the CRT `_findfirst`
(it sets `errno` at `0x004BB0B0` from `GetLastError`). `CreateFileA` has exactly one use, inside
the CRT `_open` at `0x004710F6` (`mov ebp,[0x64C3BC]; call ebp`).
`GetModuleFileNameA` is used only by the CRT (`0x0046E632`).

⇒ **The only mechanism the binary has for noticing a disc is MCI (`mciSendStringA`).**
Certainty: high (import table is definitive).

## A.2 All `mciSendStringA` call sites and their command strings

Eight real call sites (a ninth "site" reported by naive scanners, `0x0047878A`, is the
`jmp [IAT]` import thunk, not a call).

| VA | file off | command string (VA) | text |
|---|---|---|---|
| `0x00457B21` | `0x56F21` | `0x004BA858` | `status cdaudio mode` |
| `0x00457D1B` | `0x5711B` | `0x004BA8DC` / `0x004BA8F8` | `set cdaudio audio all on` / `... all off` |
| `0x00457D83` | `0x57183` | `0x004BA7E0` | `seek cdaudio to 00:00:00` (patched in place) |
| `0x00457FA4` | `0x573A4` | `0x004BA828` | `play cdaudio from XX:XX:XX to XX:XX:XX` (patched in place) |
| `0x00457FD1` | `0x573D1` | `0x004BA934` | `stop cdaudio` |
| `0x0045801B` | `0x5741B` | stack copy of `0x004BA944` | `play cdaudio to XX:XX:XX` |
| `0x004580B1` | `0x574B1` | `0x004BA98C` | `close cdaudio` |
| `0x004580D1` | `0x574D1` | `0x004BA99C` | `set cdaudio door open` |

Plus the ones issued through the shared `esi = [0x64C468]` register inside two multi-command
functions (same import, `call esi`):

* `0x00457B68`, `0x00457B97`, `0x00457BE2`, `0x00457C32`, `0x00457C7E` — inside `cd_update_status`
* `0x00457DB9`, `0x00457DE7`, `0x00457E32` — inside `cd_read_toc`
* `0x00458058`, `0x00458073`, `0x0045808E` — inside `cd_open`

Full string table (all live in writable `.data`, because several are format templates that the
code overwrites in place):

```
0x004BA7B8  "status cdaudio position track XXX"
0x004BA7E0  "seek cdaudio to 00:00:00"
0x004BA800  "status cdaudio position track XXX"
0x004BA828  "play cdaudio from XX:XX:XX to XX:XX:XX"
0x004BA858  "status cdaudio mode"          (compared against "playing" @0x004BA850)
0x004BA86C  "status cdaudio current track"
0x004BA88C  "status cdaudio position"
0x004BA8A4  "status cdaudio length"
0x004BA8BC  "status cdaudio media present"
0x004BA8DC  "set cdaudio audio all on"
0x004BA8F8  "set cdaudio audio all off"
0x004BA914  "status cdaudio number of tracks"
0x004BA934  "stop cdaudio"
0x004BA944  "play cdaudio to XX:XX:XX"
0x004BA960  "set cdaudio time format msf"
0x004BA97C  "open cdaudio"
0x004BA98C  "close cdaudio"
0x004BA99C  "set cdaudio door open"
```

Reply buffer is always `0x0050EF70` (256 bytes), callback always `NULL`
(`push 0; push 0x100; push 0x50EF70; push <cmd>; call [0x64C468]`).

**Completeness check.** The `mciSendStringA` IAT slot `0x0064C468` is referenced exactly **12**
times in code = 8 direct `call [0x64C468]` + 3 `mov esi,[0x64C468]` (in `cd_update_status`
`0x00457B58`, `cd_read_toc` `0x00457DA4`, `cd_open` `0x00458048`) + 1 `jmp [0x64C468]` import
thunk. And each of the 18 `cdaudio` command strings is referenced exactly once from code (twice
for `status cdaudio length` and `set cdaudio audio all on`), all inside
`0x00457B10 … 0x004580D1`. **There is no hidden MCI usage anywhere else in the image.**

## A.3 The CD-audio module (VA `0x00457B10` … `0x004581B0`)

| VA | file | name (mine) | what it does | callers |
|---|---|---|---|---|
| `0x00457B10` | `0x56F10` | `cd_is_playing()` | `status cdaudio mode`, `strcmp(buf,"playing")`; returns 1 if playing | `0x00403304`, `0x00417A2E`, `0x00457C4E` |
| `0x00457B50` | `0x56F50` | `cd_update_status()` | `media present`→`[0x50EF4C]`; `length`→`[0x4BA7B4]` (+ `[0x50EF50]` "disc changed"); `position`, `current track`, `position track NNN` | 9 sites (`0x00403312`, `0x0040363A`, `0x00417B89`, `0x004184C0`, `0x00418D8F`, `0x00421775`, `0x00421837`, `0x00441C73`, `0x00441D15`) |
| `0x00457CF0` | `0x570F0` | `cd_set_volume_onoff(v)` | `set cdaudio audio all on/off` (0 ⇒ off) | `0x004035D6`, `0x00418109` |
| `0x00457D30` | `0x57130` | `cd_seek_track(n)` | — | **none** |
| `0x00457D60` | `0x57160` | `cd_seek_msf(msf)` | formats into `seek cdaudio to 00:00:00` | `0x00457D49` |
| `0x00457DA0` | `0x571A0` | `cd_read_toc()` | `number of tracks`→`[0x50F070]`, per-track start MSF→`[0x50F074+4n]`, lengths→`[0x50F2CC+4n]` | 9 sites (paired with `cd_update_status`) |
| `0x00457EB0` | `0x572B0` | `cd_play_whole_disc()` | — | **none** |
| `0x00457ED0` | `0x572D0` | `cd_play_track(n)` | bounds-checks `1 ≤ n ≤ [0x50F070]` | `0x0040335E`, `0x00403686`, `0x00417A3D`, `0x00417BA3`, `0x00418DA9`, `0x004218C5`, `0x0043B082`, `0x00441DA5` |
| `0x00457F10` | `0x57310` | `cd_play_track_range(a,b)` | — | **none** |
| `0x00457F60` | `0x57360` | `cd_play_from_len(start,len)` | builds `play cdaudio from … to …` | internal (3) |
| `0x00457FC0` | `0x573C0` | `cd_stop()` | `stop cdaudio` | `0x004038F6`, `0x00420891`, `0x00420B71` |
| `0x00457FE0` | `0x573E0` | `cd_play_to(msf)` | — | **none** |
| `0x00458040` | `0x57440` | `cd_open()` | `open cdaudio`; `set cdaudio audio all on`; `set cdaudio time format msf` | **`0x004180FF` only** (inside menu init `0x00417EA0`) |
| `0x004580A0` | `0x574A0` | `cd_close()` | `close cdaudio` | `0x00420B7B` (shutdown) |
| `0x004580C0` | `0x574C0` | `cd_door_open()` | `set cdaudio door open` | **none** (dead) |
| `0x004580E0` / `0x00458160` / `0x004581B0` | | MSF string ⇄ packed-int helpers | | |

`cd_update_status` is the only thing that ever sets the media flag:

```
0x00457B63  68bca84b00   push  0x4ba8bc            ; "status cdaudio media present"
0x00457B68  ffd6         call  esi                 ; mciSendStringA
0x00457B6A  85c0         test  eax, eax
0x00457B6C  740e         je    0x457b7c
0x00457B6E  33c0         xor   eax, eax            ; MCI error ->
0x00457B71  c7054cef50000000...  mov dword [0x50ef4c], 0     ;   media_present = 0
0x00457B7B  c3           ret
0x00457B7C  c7054cef50000100...  mov dword [0x50ef4c], 1     ; media_present = 1
```

Exhaustive byte-scan of **every** reference to `0x0050EF4C` (media_present) in the image:

```
0x00403318  0x0040363F  0x00417B8F  0x004184C6  0x00418D95
0x0042177C  0x0042183E  0x00441C79  0x00441D1B      (reads)
0x00457B73  0x00457B7E                              (the two writes above)
```

Each read is the same idiom — *"if a disc is present, refresh the TOC and start a music track"*:

```
0x00417B89  e8c2ff0300   call  0x457b50            ; cd_update_status
0x00417B8E  a14cef5000   mov   eax, [0x50ef4c]
0x00417B93  83f801       cmp   eax, 1
0x00417B96  7513         jne   0x417bab            ; no disc -> just skip, no error
0x00417B98  e803020400   call  0x457da0            ; cd_read_toc
0x00417B9D  a1542e5500   mov   eax, [0x552e54]
0x00417BA3  e828030400   call  0x457ed0            ; cd_play_track
```

⇒ **Redbook music and "is there a disc" use the same and only MCI path; there is no separate,
protection-flavoured MCI use.** Answer to task A(iii): yes, CD audio goes through
`mciSendStringA`, and it is already fully optional.

The in-race music scheduler is at `0x004032EA`: every ~2000 ms it calls `cd_is_playing()`, and if
nothing is playing it re-reads status and picks a track — either random
(`rand() * num_tracks / 0x8000 + 1`) or the fixed selection in `[0x004BF677]`
(`-1` ⇒ random, `0` ⇒ track 7, else that track). With no disc, `num_tracks` stays 0 and
`cd_play_track` returns 0 immediately (`cmp eax,[0x50F070] / jg fail`).

## A.4 The "PLEASE INSERT THE IGNITION CD" screen — present but unreachable

The message is a 6-language table, 2 lines × 0x32 bytes, language stride `0x64`, indexed by
`[0x00639933]`:

```
lang 0 EN  0x00495990 "PLEASE INSERT THE IGNITION CD" / 0x004959C2 "AND PRESS RETURN."
lang 1 DE  0x004959F4 "BITTE LEGEN SIE DIE BLEIFUSS FUN" / 0x00495A26 "CD EIN UND DRÜCKEN SIE RETURN."
lang 2 IT  0x00495A58 "INSERISCI IL CD DI IGNITION" / 0x00495A8A "E PREMI INVIO."
lang 3 ES  0x00495ABC (English placeholder)
lang 4 SE  0x00495B20 (English placeholder)
lang 5 FR  0x00495B84 "INSEREZ LE CD-ROM FUN TRACKS ET" / 0x00495BB6 "APPUYEZ SUR LA TOUCHE ENTREE."
```

Renderer: `0x004182D0` (file `0x177D0`), reached only from `0x004172C2`:

```
0x004172B0  83ec70       sub  esp, 0x70            ; <- menu state machine
0x004172B3  a194936300   mov  eax, [0x639394]      ; menu_state
0x004172B8  83f802       cmp  eax, 2
...
0x004172C0  7505         jne  0x4172c7
0x004172C2  e809100000   call 0x4182d0             ; draw INSERT-CD screen
```

`0x004182D0` itself does **not** verify anything: it draws the two lines, flips, then

```
0x004183CC  6a1c         push 0x1c                 ; DIK_RETURN
0x004183CE  e89ddc0300   call 0x456070             ; key_is_down(scancode)  [reads 0x50DE60+sc]
0x004183D6  83f801       cmp  eax, 1
0x004183D9  7505         jne  0x4183e0
0x004183DB  e8e0000000   call 0x4184c0             ; <- advance
0x004183E0  6a01         push 1                    ; DIK_ESCAPE -> quit
```

and `0x004184C0` advances **unconditionally**, disc or not:

```
0x004184C0  e88bf60300   call 0x457b50             ; cd_update_status
0x004184C5  a14cef5000   mov  eax, [0x50ef4c]
0x004184CA  83f801       cmp  eax, 1
0x004184CD  7505         jne  0x4184d4             ; no disc -> just skip the TOC read
0x004184CF  e8ccf80300   call 0x457da0             ; cd_read_toc
0x004184D4  b908000000   mov  ecx, 8               ; copy 2 track lengths to 0x4949E0
...
0x004184EC  b801000000   mov  eax, 1
0x004184F1  a3dc305500   mov  [0x5530dc], eax
0x004184F6  a394936300   mov  [0x639394], eax      ; menu_state = 1  (proceed)
0x004184FB  c3           ret
```

**And the same `0x004184C0` is called during init, immediately after the state is set to 2:**

```
0x00417270  c705949363000200...  mov dword [0x639394], 2   ; "show INSERT-CD screen"
0x0041727A  33c0                 xor eax, eax
...                                                        ; zero a handful of menu globals
0x0041729A  e8010c0000           call 0x417ea0             ; big menu/gfx init (incl. cd_open)
0x0041729F  e81c120000           call 0x4184c0             ; <-- sets menu_state = 1 again
0x004172A4  b801000000           mov eax, 1
0x004172A9  c3                   ret
```

Raw bytes confirm it (file `0x16670`):
`c705 9493 6300 0200 0000 33c0 … e801 0c00 00 e8 1c12 0000 b801 0000 00c3`.

Exhaustive byte-scan for the dword `0x00639394` in the whole image finds only nine references,
all in `.text`, and only three of them are stores:
`0x00417272` (=2, the one above), `0x004172ED/0x00417324/0x00417366/0x00417D0A/0x004183FC`
(=0 or ESI/EAX), and `0x004184F7` (=1). There is no other producer of state 2.

⇒ **`0x004182D0` is dead code in this build.** The screen never renders, so the user never has to
press Return and never sees the message that the readme apologises for. (The readme presumably
covers the DOS build and/or an earlier revision; this Aug-29-1997 Win32 build carries the internal
version string `"Compilation 0.91.0"` at `0x004BAB20`, which suggests a re-release/compilation
build — inference.)

`0x00417270` is itself called exactly once, from the app-state pump:

```
0x00412230  8b0d34374900  mov  ecx, [0x493734]     ; app_state: 0=boot 1=run 2=quit
0x00412236  85c9          test ecx, ecx
0x00412238  7532          jne  0x41226c
0x0041223A  a1b0374900    mov  eax, [0x4937b0]
...
0x0041224D  e80e020000    call 0x412460            ; timer init
0x00412257  e814500000    call 0x417270            ; <- menu + CD init
0x0041225C  c70534374900 01000000  mov dword [0x493734], 1
```

## A.5 What actually happens on a modern PC with no disc

1. `cd_open()` at `0x004180FF` issues `open cdaudio`. With no CD-ROM device (or no MCI CD driver)
   `mciSendStringA` returns a non-zero error immediately and `cd_open` returns 0. **The return
   value is discarded** (`call 0x458040` at `0x004180FF` — nothing tests `eax`).
2. `cd_set_volume_onoff(0xFF)` at `0x00418109` → error, ignored.
3. `cd_update_status()` → `status cdaudio media present` errors → `[0x50EF4C] = 0`.
4. Every music site sees `media_present != 1` and skips. `[0x50F070]` (track count) stays 0, so
   even the unguarded `cd_play_track` calls bail out on their bounds check.
5. Menu state is already 1 → main menu.

**Residual risk (inference):** `mciSendStringA("open cdaudio")` is a *synchronous* call. On a
machine where an MCI CD device exists but is slow/stuck (virtual drive, disc spin-up, another app
holding the device — exactly the readme's complaint), this call can block for seconds. It happens
on the first menu frame, so the symptom would be a hang at startup rather than an error.

## A.6 Proposed patches (NOT applied)

None are required to launch. They are listed for defence-in-depth / to eliminate the MCI stall.

**P1 — never touch the MCI CD driver at all (recommended if MCI stalls).**
Stub `cd_open()` to `xor eax,eax; ret`.

| | |
|---|---|
| VA | `0x00458040` |
| file offset | `0x57440` |
| original bytes | `56 6A 00` (`push esi` / `push 0`) |
| patched bytes | `33 C0 C3` (`xor eax,eax` / `ret`) |

Context (unchanged surroundings):
```
0x00458040  56           push esi                 <-- patch here
0x00458041  6a00         push 0
0x00458043  6800010000   push 0x100
0x00458048  8b3568c46400 mov  esi, [0x64c468]
0x0045804E  6870ef5000   push 0x50ef70
0x00458053  687ca94b00   push 0x4ba97c            ; "open cdaudio"
0x00458058  ffd6         call esi
```
Safe: the function is entered only at `0x00458040` (single caller `0x004180FF`, no internal jump
targets from outside), and its return value is discarded.

**P2 — force "no disc" so the whole music layer is inert without ever issuing MCI commands.**
Stub `cd_update_status()` so it clears the media flag and returns 0.

| | |
|---|---|
| VA | `0x00457B50` |
| file offset | `0x56F50` |
| original bytes (13) | `56 6A 00 68 00 01 00 00 8B 35 68 C4 64` |
| patched bytes (13) | `C7 05 4C EF 50 00 00 00 00 00 33 C0 C3` |

which assembles to `mov dword ptr [0x0050EF4C], 0` / `xor eax,eax` / `ret`.

**P2b — companion for P2** (stops the 2-second retry loop from polling MCI):
stub `cd_is_playing()` to *claim* something is playing so callers never try to start a track.

| | |
|---|---|
| VA | `0x00457B10` |
| file offset | `0x56F10` |
| original bytes (6) | `6A 00 68 00 01 00` |
| patched bytes (6) | `B8 01 00 00 00 C3` (`mov eax,1` / `ret`) |

**P3 — belt-and-braces: make the (already unreachable) INSERT-CD state never be selected.**

| | |
|---|---|
| VA | `0x00417276` (immediate of `mov dword [0x00639394], 2` at `0x00417270`) |
| file offset | `0x16676` |
| original byte | `02` |
| patched byte | `01` |

This is a **no-op in practice** (`0x004184C0` overwrites the state 2 instructions later); include it
only if you want the intent to be explicit in the binary.

**Patch-safety verification.** For each of P1/P2/P2b I collected *every* `E8/E9 rel32`,
`0F 8x rel32`, `EB/7x rel8` branch target plus every absolute `.text`-range dword in the image and
checked whether any of them lands inside the bytes being overwritten:

```
cd_open+1..+0x1a            : CLEAN (no branch/pointer targets inside)
cd_update_status+1..+0xd    : CLEAN
cd_is_playing+1..+6         : CLEAN
```

(the scan deliberately over-collects, so "clean" is a strong result). The replacement bytes
re-assemble as intended:

```
0x00458040 33c0                 xor eax, eax
0x00458042 c3                   ret
0x00458043 6800010000           push 0x100          <- now dead, never reached

0x00457B50 c7054cef500000000000 mov dword ptr [0x50ef4c], 0
0x00457B5A 33c0                 xor eax, eax
0x00457B5C c3                   ret

0x00457B10 b801000000           mov eax, 1
0x00457B15 c3                   ret
```

**Do NOT** patch `cd_play_track` / `cd_stop` etc. — they are already safe no-ops when the TOC is
empty, and blanking them would only hide legitimate music on machines that *do* have the disc.

---

# B. DirectPlay networking

## B.0 Headline result

`DPLAYX.dll` is a **hard static import bound by ordinal**, with **no delay-load directory and no
dynamic loading anywhere in the image** — so on a Windows 8/10/11 box where the *Legacy Components →
DirectPlay* optional feature is not installed, `Ign_win.exe` fails at the **loader** stage
(`STATUS_DLL_NOT_FOUND`, "dplayx.dll was not found") before a single instruction of the program runs.
This is the first thing that must be fixed.

The good news: **single-player never executes a single DirectPlay instruction**, so a
2-export stub `dplayx.dll` is enough to get the whole game running.

## B.1 Proof that the import is static and unconditional

PE data directories present: IMPORT (RVA `0x24C000`), RESOURCE, BASERELOC, DEBUG, IAT.
**No `IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT`, no BOUND_IMPORT.**

The `DPLAYX.dll` `IMAGE_IMPORT_DESCRIPTOR` is at VA `0x0064C08C` / file `0xBB48C`:

```
OriginalFirstThunk = RVA 0x0024C0C4   TimeDateStamp = 0 (unbound)   ForwarderChain = 0
Name               = RVA 0x0024CC48 -> "DPLAYX.dll" (VA 0x0064CC48, file 0xBC048)
FirstThunk (IAT)   = RVA 0x0024C2AC -> VA 0x0064C2AC (file 0xBB6AC)

INT/IAT entries (identical):
  0x80000002  ordinal 2 = DirectPlayEnumerate
  0x80000001  ordinal 1 = DirectPlayCreate
  0x00000000  terminator
```

Linker thunks (these are what naive scanners report as "call sites"):

```
0x004787A8  ff25acc26400  jmp [0x64c2ac]   ; DPLAYX #2 DirectPlayEnumerate
0x004787AE  ff25b0c26400  jmp [0x64c2b0]   ; DPLAYX #1 DirectPlayCreate
```

Real calls: `0x0045A696 → 0x004787A8` and `0x0045A851 → 0x004787AE`. Nothing else.

There are exactly **two** real `LoadLibraryA`/`GetProcAddress` users, both MSVC CRT boilerplate:

* `0x00472240` = `__crtMessageBoxA`: `LoadLibraryA("user32.dll")` then
  `GetProcAddress` for `"MessageBoxA"`, `"GetActiveWindow"`, `"GetLastActivePopup"`.
* `0x0046BC40` = `_check_processor_feature`: `GetModuleHandleA("KERNEL32")` +
  `GetProcAddress("IsProcessorFeaturePresent")`.

The only `*.dll` string in the whole image outside `.idata` is `"user32.dll"` (`0x0047B410`).
**Certainty: high.**

## B.2 Interface identity: `IDirectPlay2A`

`DirectPlayCreate` returns a v1 object which is immediately `QueryInterface`'d:

```
0x0045A851  e858df0100     call 0x4787ae            ; DirectPlayCreate(guidSP, &lpDP1, NULL)
0x0045A85A  7c23           jl   0x45a87f
0x0045A865  6890b54700     push 0x47b590            ; IID
0x0045A86D  ff10           call [eax]               ; QueryInterface
0x0045A88D  ff5608         call [esi+8]             ; lpDP1->Release()
```

`0x0047B590` (file `0x7A590`) = `80 05 46 9D 22 A8 CF 11 96 0C 00 80 C7 53 4E 82`
= **`{9D460580-A822-11CF-960C-0080C7534E82}` = `IID_IDirectPlay2A`** (verified by hexdump).

## B.3 Every DirectPlay call site

| VA | file | slot | method | context |
|---|---|---|---|---|
| `0x0045A696` | `0x59A96` | — | `DirectPlayEnumerate` | `WM_INITDIALOG`, callback `0x0045A7A0`, ctx `&hDlg` |
| `0x0045A851` | `0x59C51` | — | `DirectPlayCreate` | `MakeDP2(guidSP,&out)` |
| `0x0045A86D` | `0x59C6D` | 0 | `QueryInterface` | → `IID_IDirectPlay2A` |
| `0x0045A88D` | `0x59C8D` | 2 | `Release` | drops v1 object |
| `0x0045AC25` | `0x5A025` | 13 | `EnumSessions` | `sd.dwSize=0x50`, `guidApplication`, timeout 0, cb `0x0045AC40`, flags 1 |
| `0x0045AACB` | `0x59ECB` | 24 | `Open` | `DPOPEN_CREATE` (host) |
| `0x0045A924` | `0x59D24` | 24 | `Open` | `DPOPEN_JOIN` (client) |
| `0x0045AB24` | `0x59F24` | 6 | `CreatePlayer` | host |
| `0x0045A9A5` | `0x59DA5` | 6 | `CreatePlayer` | client |
| `0x00459EEB` | `0x592EB` | 12 | `EnumPlayers` | every net frame, `dwFlags = 0x10` |
| `0x00459F23` | `0x59323` | 14 | `GetCaps` | `DPCAPS{dwSize=0x28}` → host flag `[0x50F860]`, `dwMaxBufferSize` `[0x50F864]`, baud, latency, timeout |
| `0x0045A0CA` | `0x594CA` | 19 | `GetPlayerCaps` | per player, every frame |
| `0x0045A18E` | `0x5958E` | 26 | `Send` | `idTo == 0` ⇒ broadcast, `dwFlags = 8` |
| `0x0045A253` | `0x59653` | 25 | `Receive` | flags 1 (`DPRECEIVE_ALL`) / 2 (`DPRECEIVE_TOPLAYER`) |
| `0x00459E3A` / `0x0045A643` | `0x5923A` / `0x59A43` | 9 | `DestroyPlayer` | teardown |
| `0x00459E46` / `0x0045A64F` | `0x59246` / `0x59A4F` | 4 | `Close` | teardown |
| `0x00459E52` / `0x0045A65B` | `0x59252` / `0x59A5B` | 2 | `Release` | teardown, then `[0x4BAB04] = 0` |

Callbacks: `0x0045A7A0` (`LPDPENUMDPCALLBACK`, `ret 0x14`), `0x0045AC40`
(`LPDPENUMSESSIONSCALLBACK2`, `ret 0x10`), `0x0045ACD0` (`LPDPENUMPLAYERSCALLBACK2`, `ret 0x14`,
caps at 23 players).

Only **two** DirectPlay system messages are handled (`0x0045A2B0`): `0x31` `DPSYS_SESSIONLOST`
and `0x101` `DPSYS_HOST`; everything else is ignored (the game re-enumerates players every frame
instead).

## B.4 Service-provider filter — IPX only

```
0x0045A7A8  8b18           mov  ebx, [eax]                 ; hDlg from context
0x0045A7AA  8b742414       mov  esi, [esp+0x14]            ; lpguidSP
0x0045A7AE  813e00c45b68   cmp  dword ptr [esi], 0x685bc400
0x0045A7B4  756a           jne  0x45a820                   ; <- skip everything that isn't IPX
```

`0x685BC400` is `Data1` of `DPSPGUID_IPX {685BC400-9D2C-11CF-A9CD-00AA006886E3}` (inference on the
name; the constant is verbatim). TCP/IP, modem and serial providers are silently dropped, so the
"Type of Connection" combo can only ever list IPX.

*Patch point if you want all providers offered:* **VA `0x0045A7B4` / file `0x00059BB4`,
`75 6A` → `90 90`** (or change the compared constant at VA `0x0045A7B0` / file `0x00059BB0`).

## B.5 Session / player structures

`DPSESSIONDESC2` global at `0x0050F938` (BSS). Host fill:
`dwSize=0x50`, `dwFlags=0x44` (`MIGRATEHOST|KEEPALIVE`, inference),
`guidApplication = *(GUID*)[0x004C53A0]`, `dwMaxPlayers = 23`,
`lpszSessionNameA = GetDlgItemTextA(hDlg, 1006, …)`.
Join fill: `dwSize` + `guidInstance` (from `CB_GETITEMDATA(1004)`) only.

The application GUID is at `.rdata` **VA `0x00479558`, file `0x00078558`** (verified by hexdump:
`a4 fd 29 1d 5e 95 d0 11 98 33 00 40 05 23 df fd`) =
**`{1D29FDA4-955E-11D0-9833-00400523DFFD}`**, and the pointer to it is installed at the top of
WinMain: `0x004120A7  mov dword ptr [0x4c53a0], 0x479558`.

`DPNAME` is filled from `GetDlgItemTextA(hDlg, 1005, …)` — **but control 1005 does not exist in
any of the six dialog templates**, so every player is created with an empty short name. (This is
also why `"%s HAS QUIT THE GAME"` would print an empty name.)

Player tables (all BSS): `0x004BAB04` = `LPDIRECTPLAY2A`, `0x004BAB00` = session-alive flag,
`0x004BAB08` = dialog re-entrancy guard, `0x0050F874` = own DPID, `0x0050F9E4` = net mode
(0 none / 1 searched / 2 cancelled / 3 host / 4 client), 23-slot arrays at `0x0050F7B0`,
`0x0050F878`, `0x0050F9F0`, `0x005102F0`.

## B.6 The network dialog (`RT_DIALOG` id 102)

Six language variants, identical control set:

| lang | LCID | res VA | file | caption |
|---|---|---|---|---|
| German | 1031 | `0x006501E0` | `0xBDBE0` | `Bleifuss Fun Netzwerk-Spiel` |
| English (US) | 1033 | `0x006504F0` | `0xBDEF0` | `Ignition network game` |
| French | 1036 | `0x006507D8` | `0xBE1D8` | `Démarrer partie en réseau` |
| Italian | 1040 | `0x00650B18` | `0xBE518` | `Partita in rete di Ignition` |
| Swedish | 1053 | `0x00650E3C` | `0xBE83C` | `Ignition nätverksspel` |
| Spanish | 3082 | `0x00656AA4` | `0xC44A4` | `Ignition: Juego en Red` |

Controls: **1003** combo = connection type (item data = 16-byte SP GUID), **1004** combo =
sessions (item data = `guidInstance`), **1006** edit = session name, **1002** `Exit`,
**1007** `Join`, **1008** `Search`, **1009** `Start`.

Dialog proc `0x0045A3C0`; opened by `DialogBoxParamA` at `0x00459E00` inside `0x00459D40`.

## B.7 DirectPlay is never touched at startup

Full upward trace:

```
DirectPlayEnumerate 0x0045A696 <- 0x0045A680 (WM_INITDIALOG) <- DlgProc 0x0045A3C0
DirectPlayCreate    0x0045A851 <- 0x0045A830 MakeDP2 <- OnHost 0x0045A9D0 / OnSearch 0x0045AB50
                                                     <- DlgProc WM_COMMAND 1009 / 1008
DlgProc installed only at 0x00459DF6 (DialogBoxParamA @0x00459E00, template 102)
  <- 0x00459D40  <- 0x00404673  <- 0x00404610
  <- menu-action pointer table 0x0047DF70 entry [2], installed at 0x0040958B
     (mov dword ptr [0x4c50dc], 0x47df70), dispatched at 0x00410EEC
```

The per-frame network state machine `0x0040CEC0` begins `cmp [esi],3 / jne` … `cmp eax,4 / jne`,
so every `Send`/`Receive`/`EnumPlayers` path is unreachable unless net mode is 3 or 4, which only
the dialog's Start/Join buttons set. **Certainty: high.**

## B.8 Verdict — is a stub `dplayx.dll` enough?

**Yes, for everything except starting a network game.**

Stub requirements:

* Export **ordinal 1** and **ordinal 2** (names irrelevant — imports are ordinal-only and unbound;
  `TimeDateStamp = 0` so no bound-import timestamp matching is needed).
* `__stdcall` with correct callee cleanup:
  `#1 DirectPlayCreate(LPGUID, LPDIRECTPLAY*, IUnknown*)` → `ret 12`;
  `#2 DirectPlayEnumerate(LPDPENUMDPCALLBACK, LPVOID)` → `ret 8`.
  **Getting `ret n` wrong corrupts the caller's stack** — both call sites assume callee cleanup.
* Return any `HRESULT < 0` (e.g. `DPERR_UNAVAILABLE = 0x887700FA`); the code only tests `jl`.

Behaviour with such a stub:

1. Startup / menu / single-player: **completely unaffected**.
2. `NETWORK GAME` opens dialog 102; combo 1003 stays empty.
3. `Exit` → safe. `Search` → safe (`CB_GETCURSEL(1003)` returns `-1`, `0x0045AB74 je 0x45AC28`
   returns before `DirectPlayCreate`). `Join` → safe (button stays disabled).
4. **`Start` (1009) → null-pointer crash.** `0x0045A5BD` sets net mode = 3 *unconditionally*
   before calling `OnHost`, which bails out; the next frame reaches `Net_RefreshPlayers`:

```
0x00459ED1  8b0d04ab4b00  mov ecx, dword ptr [0x4bab04]    ; NULL
0x00459ED7  c7050cf8500000000000  mov dword ptr [0x50f80c], 0
0x00459EE1  68d0ac4500    push 0x45acd0
0x00459EE6  8b01          mov eax, dword ptr [ecx]          ; *** ACCESS VIOLATION ***
0x00459EEB  ff5030        call dword ptr [eax + 0x30]       ; IDirectPlay2::EnumPlayers
```

*Hardening patch (optional, NOT applied):* at **VA `0x0045A5BD` / file `0x000599BD`** the
instruction is `C7 05 E4 F9 50 00 03 00 00 00` (`mov dword [0x50F9E4], 3`); changing the `03` at
**file `0x000599C3`** to `02` makes the Start button behave like Cancel, so a stub can never crash.

## B.9 What a *functional* replacement would need

Ordinal 2 must call the callback at least once with a `lpguidSP` whose `Data1 == 0x685BC400`,
otherwise the combo stays empty and the dialog is unusable (unless you also apply the B.4 patch).
The v1 object needs only slots 0/1/2. The `IDirectPlay2A` vtable must be a full 32 entries so the
offsets line up, but only **14** are ever invoked, in this order:

`DirectPlayEnumerate` → `DirectPlayCreate` → `QueryInterface(IID_IDirectPlay2A)` → `Release`(v1) →
`EnumSessions`(13) → `Open`(24) → `CreatePlayer`(6) → then per frame
`EnumPlayers`(12), `GetCaps`(14) *(must set `DPCAPS_ISHOST` correctly — this is how the game knows
it is the host)*, `GetPlayerCaps`(19), `Send`(26), `Receive`(25) *(must return
`DPERR_NOMESSAGES 0x887700BE` when empty; must deliver system messages with `*lpidFrom == 0` and a
leading dword of `0x31`/`0x101`)* → teardown `DestroyPlayer`(9), `Close`(4), `Release`(2).

Error codes the game specifically decodes: `DPERR_BUSY 0x8877010E`, `DPERR_SENDTOOBIG 0x887700E6`,
`DPERR_INVALIDOBJECT 0x88770082`, `DPERR_INVALIDPLAYER 0x88770096`,
`DPERR_BUFFERTOOSMALL 0x8877001E`, `DPERR_NOMESSAGES 0x887700BE`, `E_INVALIDARG 0x80070057`.

Hard limits baked into the game: **23 players max**, **0x400-byte receive buffer**
(callers at `0x0040CF48` / `0x0040D148`), typical packet sizes 8 and 0xF4 bytes.

---

# C. Startup and configuration flow

## C.1 Entry → WinMain

`0x00469950` is stock MSVC `WinMainCRTStartup`:
`GetVersion` → heap init → `GetCommandLineA` (`0x004699D5`) → `__setenvp`/`__setargv` →
skip argv[0] → `GetStartupInfoA` → call WinMain:

```
0x00469A89  50            push eax                 ; nCmdShow
0x00469A8A  56            push esi                 ; lpCmdLine
0x00469A8B  6a00          push 0                   ; hPrevInstance
0x00469A8D  6a00          push 0
0x00469A8F  ff1528c36400  call [GetModuleHandleA]
0x00469A95  50            push eax                 ; hInstance
0x00469A96  e80586faff    call 0x4120a0            ; <-- WinMain
```

**WinMain = `0x004120A0`.**

## C.2 Command-line switches: there are none

`WinMain` reads only `[esp+4]` (= `hInstance`, stored to `[0x004C5398]`) and returns `ret 0x10`.
`lpCmdLine` at `[esp+0x0C]` is never touched. The raw command line pointer is cached at
`0x0064BF74`; an exhaustive scan finds only 5 references, all inside the CRT
(`0x004699DB`, `0x004699EE`, `0x00469A10` in the startup stub, `0x0046E638`/`0x0046E648` in
`__crtGetModuleFileName`/`_setargv`). **Certainty: high — the game parses no switches.**

## C.3 WinMain body

```
0x004120C2  LoadCursorA(NULL, IDC_ARROW)                  -> [0x004C5360]
0x00412127  RegisterClassA(class "Ignition" @0x00493740, wndproc 0x004122D0,
                           style 8 = CS_DBLCLKS? , hbrBackground = GetStockObject(4))
            failure -> return 0  (silent exit)
0x00412145  QueryPerformanceFrequency -> [0x004C5348]; [0x00493720] = 1 if available
            else timeBeginPeriod(1) at 0x0041216A
0x00412170  call 0x0045B170        ; subsystem init (see C.5)
0x00412175  call 0x00412500        ; DirectDraw/DirectSound/DirectInput bring-up
            returns 0 -> WinMain returns 0  == SILENT EXIT
0x0041218A  message pump: PeekMessageA / GetMessageA / TranslateMessage / DispatchMessageA
            when idle: call 0x00412230 (app pump);  returns 0 -> call 0x00412530
                       (PostMessageA(hwnd, WM_CLOSE, 0, 0))
```

`0x00412500`:
```
0x00412502  call 0x00456AF0(0)      ; install the graphics back-end function table
0x0041250A  call 0x00456BC0         ; gfx->init()  == 0x0045B740 (window + DirectDrawCreate)
            eax == 0 -> return 0
0x00412513  call 0x00455AC0         ; DirectInput keyboard
            eax == 0 -> return 0
0x0041251C  [0x004C5390] = 1 ; return 1
```

The graphics layer is dispatched through a small function-pointer table in
`0x0050EB68 … 0x0050EBA4`, installed by `0x0045B690` (from `0x00456AF0(0)`) and `0x00456E60`:

```
0x0045B690  mov [0x50eb68], 0x0045B730   ; get_mode_count / caps
0x0045B69A  mov [0x50eb6c], 0x0045B740   ; init  -> CreateWindowExA + DirectDrawCreate
0x0045B6A4  mov [0x50eb70], 0x0045BD70   ; shutdown
0x0045B6AE  mov [0x50eb74], 0x0045C060   ; …
            … through [0x50eb9c] = 0x0045C730
0x00456E60  mov [0x50eba0], 0x00456F20   ; init2 (tail-called by 0x00456BC0)
0x00456E6A  mov [0x50eba4], 0x00456F30
```

so `0x00456BC0` literally is
`if (!(*[0x50EB6C])()) return 0;  goto *[0x50EBA0];`
— i.e. **`0x0045B740` is the function that fails when DirectDraw is unavailable.**

## C.4 Window + DirectDraw creation (`0x0045B740`)

```
0x0045B783  CreateWindowExA(0x40000, "Ignition", "Ignition", 0x80080000,
                            0,0, GetSystemMetrics(0), GetSystemMetrics(1),
                            NULL, NULL, hInstance, NULL)       -> [0x004C539C] / [0x00493738]
            NULL -> return 0
0x0045B7AA  UpdateWindow ; 0x0045B7B7 SetFocus
0x0045B7C6  call 0x0047879C          ; thunk -> DDRAW!DirectDrawCreate(NULL, &g_0x00512C50, NULL)
            HRESULT != 0 -> return 0   == SILENT EXIT
0x0045B7FA  IDirectDraw::SetCooperativeLevel(hwnd,
                 [0x00493728] ? 0x53 : 0x08)
                 ; 0x53 = EXCLUSIVE|FULLSCREEN|ALLOWMODEX|ALLOWREBOOT, 0x08 = NORMAL
            != 0 -> return 0
0x0045B833  IDirectDraw::SetDisplayMode(w,h,bpp) from [0x004BA6E0]/[0x004BA6E4]/[0x004BA6E8]
            != 0 -> return 0
```

Full failure map of `0x0045B740` — **every one of these aborts WinMain**, and only two of them
say anything:

| VA | step | on failure |
|---|---|---|
| `0x0045B74A` | `0x00456A40` clears the three 0x30-byte surface-slot arrays `0x0050E688`/`0x0050E778`/`0x0050E7A8` | cannot fail |
| `0x0045B783` | `CreateWindowExA` | NULL → return 0, silent |
| **`0x0045B7C6`** | **`DirectDrawCreate`** | HRESULT ≠ 0 → return 0, **silent** |
| `0x0045B7FA` | `SetCooperativeLevel` (`0x53` fullscreen / `8` windowed) | ≠ 0 → return 0, silent |
| `0x0045B833` | `SetDisplayMode` | ≠ 0 → return 0, silent |
| `0x0045B9DC` | `CreateSurface` (primary) | ≠ 0 → return 0, silent |
| `0x0045BA28` | back-buffer count ≥ 5 | **MessageBoxA** "The maximum amount of backbuffers is exceeded" |
| `0x0045BA94` | `GetAttachedSurface` | **MessageBoxA** "Backbuffer couldn't be obtained" |
| `0x0045BB52` / `0x0045BD30` | `CreatePalette` (0x44 entries) / `SetPalette` | ≠ 0 → return 0, silent |
| `0x0045BCB1` / `..E3` / `0x0045BD06` | windowed `CreateClipper` / `SetHWnd` / `SetClipper` | ≠ 0 → return 0, silent |

`[0x00493728]` ("fullscreen") is initialised to **1** in `.data` (file `0x91D28`), so the game goes
exclusive-fullscreen by default.

## C.5 `0x0045B170` (called before the DirectX bring-up)

```
0x0045B170  call 0x0045B4F0   ; prints the engine banner "Lisa 2 Development System,
                              ; Compilation 0.91.0" / "Copyright (c) UDS, 1995-1996" to stdout
0x0045B175  call 0x0045AD50   ; zero the 256-slot named-object table 0x0063C6A0..0x0063CAA0 and
                              ; register slot 0 "DEFAULT" (0x0045AD80, malloc 0x140) — returns 1
                              ; even if the malloc fails
0x0045B17A  call 0x0045B1F0   ; build the 200-entry keyboard/event free-slot ring at
                              ; 0x00511230 / 0x005116E0 — cannot fail
0x0045B17F  call 0x00455AB0   ; [0x0050DE14] = 0, [0x0050E678] = 0  (input state reset)
0x0045B184  call 0x0045E610   ; broadcast the 16-byte template at 0x0051FD00 into ~13 blit/clip/
                              ; colour-key structs — cannot fail
0x0045B18B  call 0x00456AF0(0); install the gfx driver vtables (0x0045B690 + 0x00456E60)
            mov eax, 1 / ret  ; returns 1 unconditionally — and WinMain ignores the result anyway
```

**Nothing in `0x0045B170` can abort startup.**

## C.6 DirectInput (`0x00455AC0`) — a hard-fail path

```
0x00455B0C  push 0x300                      ; DIRECTINPUT_VERSION 3.0
0x00455B1A  call 0x00478778                 ; DINPUT!DirectInputCreateA(hInst,0x300,&[0x50DF60],NULL)
            != 0 -> return 0                ; == silent exit via 0x00412500
0x00455B3F  IDirectInput::CreateDevice(GUID_SysKeyboard @0x00479548, &[0x50E26C], NULL)
            != 0 -> return 0
0x00455B5A  IDirectInputDevice::SetDataFormat(c_dfDIKeyboard @0x00478760)
            != 0 -> return 0
...
```
followed by `SetCooperativeLevel(6)` at `0x00455B79` and `SetProperty(DIPROP_BUFFERSIZE, 0x20)`
at `0x00455B97` — **all of these are hard failures**. Only `Acquire` at `0x00455BAD` is soft (it
just clears `[0x0050E270]`). A second `DirectInputCreateA` at `0x00455CBA` (routine `0x00455C60`,
called from `0x00455ECD`) re-creates the device.
Keyboard state lands in the byte array at `0x0050DE60` (indexed by DIK scancode) that
`key_is_down()` at `0x00456070` reads; edge-triggered variants at `0x00456080` (`0x0050E068`)
and `0x004560A0` (`0x0050DF68`) clear the byte after reading.

## C.6a DirectSound — **not** a fatal path

`DirectSoundCreate` has exactly one call site, `0x004679E2` (file `0x66DE2`), inside the audio-init
function `0x00467920`:

```
0x004679DB  6814c66300   push 0x63c614            ; &g_lpDS
0x004679E0  6a00         push 0                   ; lpGuid = NULL
0x004679E2  e8bb0d0100   call 0x4787a2            ; DSOUND!DirectSoundCreate
0x004679E7  85c0         test eax, eax
0x004679E9  0f851f050000 jne  0x467f0e            ; failure -> return 0
```
and its caller treats a 0 return as "no sound, carry on":
```
0x004577E1  e83a010100   call 0x467920
0x004577E6  85c0         test eax, eax
0x004577E8  7512         jne  0x4577fc
0x004577EA  b801000000   mov  eax, 1              ; still report success
0x004577F0  c70540ed5000 01000000  mov dword [0x50ed40], 1   ; g_sound_disabled = 1
0x004577FB  c3           ret
```
Every sound entry point then short-circuits on `cmp dword ptr [0x50ed40], 1` (e.g. `0x00457AA8`,
`0x00457AF0`, `0x00457920`). **A `DirectSoundCreate` failure is harmless — the game runs mute.**

*Caveat:* `0x004577A0` can still return 0 for other reasons — it returns 0 immediately if
`[0x004BA7A4] != -1` (sound already initialised) or if the internal setup `0x00458260` fails —
and the *second* caller, `0x0041F9DD` inside `0x0041F9B0` (in-race audio bring-up, reached from
`0x00418BE0`), treats that as fatal:

```
0x0041F9DD  e8be7d0300   call 0x4577a0
0x0041F9E5  85c0         test eax, eax
0x0041F9E7  7517         jne  0x41fa00
0x0041F9E9  68c4904900   push 0x4990c4      ; "Error while initiating sound\n"
0x0041F9EE  e8cdbb0300   call 0x45b5c0      ; logger (disabled by default)
0x0041F9F6  6a01         push 1
0x0041F9F8  e8e39d0400   call 0x4697e0      ; exit(1)  -- silent
```

so "no sound card" is safe, but a double-init or an allocation failure kills the process silently.
Graphics and DirectInput failures are fatal-and-silent unconditionally.

## C.7 Error reporting — why failures are silent

Only **five** real `MessageBoxA` call sites exist (a sixth "site", `0x00477708`, is the import
thunk), and they show only **two** distinct strings, both from the DirectDraw surface-creation
code, none of them on the critical launch path:

| VA | file | string |
|---|---|---|
| `0x0045BA39` | `0x5AE39` | `The maximum amount of backbuffers is exceeded` (`0x004BACB4`) |
| `0x0045BB79` | `0x5AF79` | `Backbuffer couldn't be obtained` (`0x004BAC94`) |
| `0x0045BC34` | `0x5B034` | `The maximum amount of backbuffers is exceeded` |
| `0x0045BED3` | `0x5B2D3` | `The maximum amount of backbuffers is exceeded` |
| `0x0045C009` | `0x5B409` | `Backbuffer couldn't be obtained` |

Every *other* fatal startup error is routed through the game's own logger `0x0045B5C0`, which
begins:

```
0x0045B5C0  81ecc8000000  sub  esp, 0xc8
0x0045B5C6  803d88ac4b0000 cmp byte [0x4bac88], 0
0x0045B5CD  7509           jne 0x45b5d8
0x0045B5CF  33c0           xor eax, eax
0x0045B5D1  81c4c8000000   add esp, 0xc8
0x0045B5D7  c3             ret                    ; logging disabled -> print nothing
```

`[0x004BAC88]` is **0** in the file image (file offset `0xB9288`), and the enabling routine
`0x0045B550` (which also opens `Default.fil` with mode `"wb"`) is only reached if the flag is
already set. So the following messages are compiled in but **never displayed**; the game just
calls `exit()` (`0x004697E0`) and the process disappears with no window and no message:

| string VA | text | site |
|---|---|---|
| `0x00498844` | `Cannot set graphics mode.\n` | `0x00417F2E`, `0x00417F82` (in menu init `0x00417EA0`) |
| `0x004989DC` | `Cannot use this graphics mode.\n` | `0x00418540`, `0x00418593`, `0x004185E7` (`0x00418500`) |
| `0x00498860` | `Could not get a font handle (%d).\n` | `0x00418297` |
| `0x004989A8` | `Error while loading N_SYSGFX.PIC\n` | `0x0041814B` |
| `0x00498974` | `Error while loading N_SYSG_2.PIC\n` | `0x00418178` |
| `0x0049894C` | `Error while loading SYS.COL\n` | `0x004181B7` |
| `0x00498EEC` | `Error while creating lens flaires` | |
| `0x00498F38` | `Error while creating the world` | |
| `0x00498FA8` | `Error while creating the weather` | |

Several of these sites do not even use the logger — they `sprintf` the text into a global scratch
buffer and raise a flag, e.g.

```
0x00418540  68dc894900   push 0x4989dc      ; "Cannot use this graphics mode.\n"
0x00418545  68f0356000   push 0x6035f0      ; g_error_text
0x0041854A  e871100500   call 0x4695c0      ; sprintf
0x00418552  33c0         xor  eax, eax
0x00418554  893570f95400 mov  [0x54f970], esi   ; g_error_flag = 1
0x0045855B  5e/c3        pop esi / ret 0
```

Exhaustive byte-scan: `0x0054F970` (`g_error_flag`) has **11 stores and zero loads** anywhere in
the image, and `0x006035F0` (`g_error_text`) appears only ever as a *pushed argument* (10 sites,
all `push 0x6035f0`) — nothing ever renders it. So the composed error text is thrown away.

**This is the single most important diagnostic fact for getting the game running on Windows 10/11:
almost every startup failure is a silent `exit()` with the reason written to a buffer nobody
reads.** If you want visibility while debugging,
set `[0x004BAC88] = 1` (and `[0x004BAC8A] = 1` to enable the `Default.fil` sink) — both are in the
BSS-free part of `.data`, i.e. patchable in the file: `0x004BAC88` → file `0xB9288`,
`0x004BAC8A` → file `0xB928A`.

## C.7a Display modes — the game is 8-bit palettized only

`0x00418500` (file `0x17900`) is the mode setter, driven by `[0x00552FC0]`:

```
[0x552FC0]==0 : [0x4BA6E0]=0x140 (320)  [0x4BA6E4]=0xC8  (200)  [0x4BA6E8]=8  [0x4BA6EC]=1
[0x552FC0]==1 : 0x280 (640)             0x1E0 (480)            8             1
[0x552FC0]==2 : 0x320 (800)             0x258 (600)            8             1
```
and the menu-init probe at `0x00417F01`/`0x00417F50` tries 640×480×8 then 320×200×8. Every path
sets **bpp = 8**; there is no 16-bit path in this build (the readme's promised 3dfx patch never
shipped). `.data` defaults at file `0xB8CE0`: `80 02 00 00 | E0 01 00 00 | 08 00 00 00 | 01 00 00 00`
= 640×480×8, 1 back buffer.

`[0x00552FC0]` is also switchable **live, in-game**, by the in-race key handler around
`0x00420F80`: `DIK_ADD` (numpad `+`, scancode `0x4E`) increments it and `DIK_SUBTRACT`
(numpad `−`, `0x4A`) decrements it, clamped to `0..2`, followed by
`call 0x0043DEA0 / 0x0041F3A0 / 0x0041F410` to rebuild the renderer:

```
0x00420F82  6a4e         push 0x4e                 ; DIK_ADD
0x00420F84  e8e7500300   call 0x456070             ; key_is_down
0x00420FAA  a1c02f5500   mov  eax, [0x552fc0]
0x00420FAF  03c6         add  eax, esi             ; +1
0x00420FB6  83f802       cmp  eax, 2
0x00420FBB  c705c02f5500 02000000  mov dword [0x552fc0], 2   ; clamp
```
(the same handler block also polls `0x19` = `P`, `0x58` = `F12`, `0x0D` = `=`, `0x0C` = `-`.)

*Consequence (inference, high confidence):* Windows 8 and later no longer expose 8-bpp palettized
primary surfaces through DirectDraw exclusive mode, so `IDirectDraw::SetDisplayMode(w,h,8)` at
`0x0045B83x` will fail and the game will take the silent-exit path. A DirectDraw wrapper
(DDrawCompat / dgVoodoo2 / cnc-ddraw) that emulates 8-bpp modes is very likely required on top of
anything else.

## C.7b The options menus — string inventory

The English menu-label pool is a flat, NUL-separated string block in `.data` starting around
`0x00491D80` (file `0x8F980`); the other five languages have parallel blocks
(`0x0049156x` ES, `0x0049180x` IT, `0x0049110x` FR, …). The menu itself is data-driven: 0x18-byte
item records in `.data` (e.g. the GFX page around `0x0047F6A0`–`0x0047F7A0`) that embed the label
pointer at an unaligned offset together with 16-bit layout/behaviour fields — which is why no
`.text` instruction directly names most option strings.

Recovered option labels (English):

```
0x00491DAC OPTIONS          0x00491E3C GAME OPTIONS     0x00491E30 GFX OPTIONS
0x00491E20 SOUND OPTIONS    0x00491E0C PLAYER 1 OPTIONS 0x00491DF8 PLAYER 2 OPTIONS
0x00491DE4 RESTORE SETTINGS 0x00491DD4 RESET GHOST CAR  0x00491DA0 BEST TIMES

GFX page:
0x00491FE0 PERSP. POLY      0x00491FEC MIP MAPPING      0x00491FCC SMOKE
0x00491FD4 SKID MARKS       0x00492018 SCREEN SIZE      0x0049203C RESOLUTION
0x00491FF8 FULL / 0x00492000 MEDIUM / 0x00492008 SMALL / 0x00492010 TINY
0x00492024 800X600 / 0x0049202C 640X480 / 0x00492034 320X200
0x00492054 ON / 0x00492058 OFF

Sound page:
0x00491FC0 CD MUSIC   0x00491FB4 SFX VOLUME   0x00491F7C CD TRACK
0x00491F74 RANDOM     0x00491F6C DEFAULT      0x00491F30..0x00491F68 "11".."25"

Game page:
0x00492084 DIFFICULTY (0x00492070 PRO / 0x00492074 AMATEUR / 0x0049207C NOVICE)
0x00492048 OBSTACLES  0x0049205C GHOST CAR  0x00492068 MIRROR  0x00491F20 GEARBOX
0x00491F10 AUTO / 0x00491F18 MANUAL   0x00491ED0 AUTO ACC

Controls page:
0x00491F04 CONTROLS  0x00491EF8 KEYBOARD  0x00491EEC JOYSTICK  0x00491EE4 PEDALS
0x00491EDC JOYPAD    0x00491EC0 KEYBOARD CONFIG  0x00491EB0 JOYSTICK CALIB
0x00491EA0 PEDALS CALIB
0x004920D4 TURN LEFT / 0x004920C8 TURN RIGHT / 0x004920C0 ACC. / 0x004920B8 BRAKE
0x004920B0 GEAR UP / 0x004920A4 GEAR DOWN / 0x0049209C BOOST / 0x00492090 REAR VIEW

Modes: 0x00491E90 CHAMPIONSHIP, 0x00491E84 SINGLE RACE, 0x00491E78 TIME TRIAL,
       0x00491E6C PURSUE MODE, 0x00491E60 SPLITSCREEN, 0x00491DC4 SINGLE PLAYER,
       0x00491DB4 MULTI PLAYER
Also: 0x004920EC "SELECT YOUR LANGUAGE"  (drawn by 0x0040A8C9/0x0040A8E4)
```

`PERSP. POLY` and `MIP MAPPING` therefore both exist as toggles in the Win32 build (the readme's
"turn off the perspective corrected polygons in the gfx menu" advice applies here), and
`SCREEN SIZE` / `RESOLUTION` correspond to the `[0x00552FC0]` selector described above.

### Menu table structure and the option → variable mapping

The menu is fully data-driven, in three struct types (verified against the draw routine
`0x00410AF0`, file `0xFEF0`, and the input/toggle handler `0x00410D80`, file `0x10180`, whose
jump table is at `0x004114E0`):

* **PAGE** (0x18 B): `{ on_activate, on_key, ctx, count, ITEM*, cursor }`.
  The GFX page is at **`0x00481718`** (file `0x7FD18`): hooks `0x00409FB0` / `0x00409610`,
  ctx `0x0047DD70`, **count = 6**, items at `0x0047F900`.
* **ITEM** (stride **0x1C**): `{ int32 *pValue; int32 min; int32 max; REC *list; int16 action;
  int32 arg; void *extra; int16 enabled }` — **this is where the backing variable pointer lives**
  (my earlier 0x18-byte reading of the tables was wrong).
* **REC** (stride 0x18, 0x30 per logical line): label at `list + sel*0x18`, value *v* at
  `list + 0x30 + v*0x30 + sel*0x18`; string pointer at REC+0x0E (unaligned — which is why the
  label addresses showed up at odd offsets).

There are **six complete per-language copies** of the ITEM+REC tables, all pointing at the *same*
variables. GFX ITEM arrays: EN `0x0047F900` (file `0x7DF00`), DE `0x004828A0`, IT `0x00485808`,
ES `0x00488770`, SE `0x0048B6D8`, FR `0x0048E640` — stride `0x2F68`. Inc/dec arms live at
`0x00410FFE` (dec+clamp), `0x00411038` (dec+wrap), `0x004111E0` (inc+wrap), `0x00411222`
(inc+clamp).

**GFX OPTIONS — exactly six entries, with their variables and defaults:**

| Option | menu var | struct off | live/race global | values | default |
|---|---|---|---|---|---|
| RESOLUTION | `0x004BF68B` | `+0x10B` | **`0x00552FC0`** | 320X200 / 640X480 / 800X600 | **0 = 320×200** |
| SCREEN SIZE | `0x004BF683` | `+0x103` | `0x00527F28` | TINY / SMALL / MEDIUM / FULL | 3 = FULL |
| **MIP MAPPING** | **`0x004BF6DB`** | `+0x15B` | **`0x00527F78`** | OFF / ON | **0 = OFF** |
| **PERSP. POLY** | **`0x004BF68F`** | `+0x10F` | **`0x00525E68`** | OFF / ON | **0 = OFF** |
| SKID MARKS | `0x004BF693` | `+0x113` | `0x00552E58` | OFF / ON | 1 = ON |
| SMOKE | `0x004BF697` | `+0x117` | `0x00563BE0` | OFF / ON | 1 = ON |

**There is no detail-level or draw-distance option in this build** — the GFX page has exactly six
items in all six languages; the performance knob is SCREEN SIZE (viewport shrink).

How the two readme-relevant toggles are consumed:

```
; PERSP. POLY -> renderer context field +0x58
0041B486  a1685e5200    mov eax, [0x525e68]
0041B48B  8b0df0c56300  mov ecx, [0x63c5f0]        ; renderer ctx
0041B491  894158        mov [ecx+0x58], eax

; MIP MAPPING -> picks the texture uploader
0041B4F6  a1787f5200    mov eax, [0x527f78]
0041B4FB  85c0          test eax, eax
0041B4FD  0f85...       jne 0x41b537               ; 0 -> call 0x447280 (plain upload)
0041B53C  83f801        cmp eax, 1
0041B54F  e8...         call 0x448620              ; mip-chain builder + .\levels\<hex>conv.tab cache
```

So **the readme's advice ("turn off the perspective corrected polygons in the gfx menu") maps to
`[0x00525E68] = 0` / struct `+0x10F`, and it is already the default** — a fresh install starts with
PERSP. POLY off, MIP MAPPING off, and 320×200.

Other pages: **SOUND** — CD MUSIC (`0x004BF67B`, def 1), SFX VOLUME (`0x004BF67F`, 0..10, def 10),
CD TRACK (`0x004BF677`, min −1, RANDOM/DEFAULT/1..25, def 0). **GAME** — DIFFICULTY
(`0x004BF58C`, def 0; its `max` ships as 0 and is raised to 3 at runtime by `0x00409F49`, i.e.
unlock-gated), GHOST CAR (`0x004BF590`), OBSTACLES (`0x004BF594`).
**PLAYER 1/2** — NAME, GEARBOX (`0x004BF5C8`/`0x004BF5E8`), CONTROLS (`0x004BF6A3`/`0x004BF6A7`),
AUTO ACC (`0x004BF6AB`/`0x004BF6AF`), plus the calibration sub-pages.

## C.7c Settings persistence — `ign_win.btz`

There is no INI and no registry, but settings **are** persisted, in a flat binary blob file
**`ign_win.btz`** in the working directory (string at `.data` `0x0047E940`, file `0x7CF40`;
also stored as a pointer variable at `0x00494AD8`, file `0x930D8`). It is **not** present on a
fresh install, which is why the game starts with defaults.

File helpers (all wrap the CRT `fopen` at `0x00469390`):

| VA | file | signature | mode string |
|---|---|---|---|
| `0x004576B0` | `0x56AB0` | `int file_exists(const char *name)` → 1 / `0x7EF` | `"r"` `0x004BA794` |
| `0x00457420` | `0x56820` | `int load_file(const char *name, void *buf, size_t len, long ofs)` | `"rb"` `0x0047C040` |
| `0x00457590` | `0x56990` | `int save_file(const char *name, const void *buf, size_t len)` | `"wb"` `0x0049C9C4` |

Two `0x597`-byte records with (apparently) parallel layouts are involved:

| blob base | loaded at | saved at |
|---|---|---|
| `0x004BF580` | `0x0040949F` (`load_file("ign_win.btz", 0x4BF580, 0x597, 0)`) | `0x004030B0` (`save_file("ign_win.btz", 0x4BF580, 0x597)`) |
| `0x006393A0` | `0x004192BE` (name from `[0x00494AD8]`) | `0x00420951` and `0x0042266A` |

Load, with a version gate:

```
0x00409461  6840e94700  push 0x47e940            ; "ign_win.btz"
0x00409468  e843e20400  call 0x4576b0            ; file_exists
0x00409470  85c0        test eax, eax
0x00409472  7438        je   0x4094ac            ; absent -> keep compiled-in defaults
0x00409478  6a00/6a04   push 0 / push 4
0x0040947D  6840e94700  push 0x47e940
0x00409482  e899df0400  call 0x457420            ; read 4-byte header
0x0040948E  83f81e      cmp  eax, 0x1e           ; version must be 30
0x00409491  7519        jne  0x4094ac            ; wrong version -> ignore file
0x00409495  6897050000  push 0x597
0x0040949A  6880f54b00  push 0x4bf580
0x0040949F  6840e94700  push 0x47e940
0x004094A4  e877df0400  call 0x457420            ; load the settings blob
```

Save (from the options screen), showing two fields being folded into the blob first:

```
0x00422649  a16c3c5600            mov  eax, [0x563c6c]
0x0042264E  8b0dc02f5500          mov  ecx, [0x552fc0]      ; resolution index
0x00422654  6897050000            push 0x597
0x00422659  a3a7946300            mov  [0x6394a7], eax      ; blob + 0x107
0x0042265E  68a0936300            push 0x6393a0
0x00422663  890dab946300          mov  [0x6394ab], ecx      ; blob + 0x10B  <- resolution
0x00422669  6840e94700            push 0x47e940
0x0042266E  e81d4f0300            call 0x457590             ; save_file
```

Useful offsets inside the blobs (VA − blob base):

| global | blob | offset | meaning |
|---|---|---|---|
| `0x006394AB` | `0x006393A0` | `+0x10B` | screen-resolution index (mirror of `[0x00552FC0]`) |
| `0x006394A7` | `0x006393A0` | `+0x107` | mirror of `[0x00563C6C]` |
| `0x00639933` | `0x006393A0` | `+0x593` | **language index** |
| `0x00639497` | `0x006393A0` | `+0xF7`  | music-track timer |
| `0x004BF677` | `0x004BF580` | `+0xF7`  | CD music track selection (`-1` random, `0` → track 7) |

### Complete list of everything the game writes to disk

| File | Writer VA (file) | Size | Trigger |
|---|---|---|---|
| **`ign_win.btz`** | `0x00402C00` (`0x2000`), writes at `0x004030B5` / `0x0040315F` | `0x597` | menu autosave (0.01 s countdown at `[0x004BE8C0]` armed by the menu-confirm callback `0x004045B0`; commit routine `0x00404800`) |
| **`ign_win.btz`** | `0x004225D0` (`0x219D0`), write at `0x0042266E` | `0x597` | leaving a race |
| **`ign_win.btz`** | `0x00420870` (`0x1FC70`), write at `0x0042095B` | `0x597` | race-module teardown |
| `GHOSTS\%s.GST` | `0x004222B0` (`0x216B0`), write at `0x004224EF` | `0x49D4C` | new best ghost lap |
| `IGN%d.TGA` | `0x00446040` (`0x45440`), write at `0x00446165` | viewport + `0x312`-byte header (18-byte TGA header + 768-byte palette) | **F12** (scancode `0x58`, polled at `0x00420F55`), counter `[0x004949C8]` |
| `.\levels\<hex>conv.tab` | `0x00448620` (`0x47A20`), write at `0x00448823` | `0x40000` | mipmap/blend LUT cache, only when **MIP MAPPING** is ON (name built by concatenation with `".tab"` @ `0x0049C9C8`) |
| `Default.fil` | `0x0045B550` / `0x0045B5E3` | — | debug log, disabled by default |
| *(dead)* `ailists.txt` | `0x004161CE` | — | function `0x004160DE` has no callers |

Everything else opens read-only: `_open` at `0x00470E90` is called from 37 sites and **every one**
passes `oflag = 0x8000` (`_O_BINARY`, i.e. `_O_RDONLY`). `fwrite` (`0x00469B20`) has exactly two
callers (`0x0044883E`, `0x004575BF`). `CreateFileA`/`WriteFile`/`SetFilePointer`/`SetEndOfFile`/
`DeleteFileA`/`MoveFileA` are referenced only from CRT internals; `remove()`/`rename()` have zero
callers.

### The saved struct (0x597 bytes, packed — fields are unaligned)

Defaults are written by `0x0040C810` (file `0xBC10`), called from `0x0040475D` and `0x00409434`.

| Off | Meaning | Menu-copy VA (`0x004BF580 +`) | Race-copy VA (`0x006393A0 +`) | Default |
|---|---|---|---|---|
| `+0x000` | **version magic — must be `0x1E`** | `0x004BF580` | `0x006393A0` | `0x1E` |
| `+0x00C` | DIFFICULTY (NOVICE/AMATEUR/PRO/MIRROR) | `0x004BF58C` | — | 0 |
| `+0x010` | GHOST CAR | `0x004BF590` | — | 1 |
| `+0x014` | OBSTACLES | `0x004BF594` | — | 1 |
| `+0x048` / `+0x068` | GEARBOX P1 / P2 | `0x004BF5C8` / `0x004BF5E8` | — | 1 (AUTO) |
| `+0x06C` / `+0x0E4` | player 1 / 2 name (0x78 B) | | | `"PL1"` / `"PL2"` |
| `+0x0F7` | CD TRACK (−1 = RANDOM, 0 = DEFAULT) | `0x004BF677` | `0x00639497` | 0 |
| `+0x0FB` | CD MUSIC | `0x004BF67B` | | 1 |
| `+0x0FF` | SFX VOLUME (0..10) | `0x004BF67F` | | 10 |
| `+0x107` | SCREEN SIZE (stored inverted) | `0x004BF687` | `0x006394A7` | 0 |
| **`+0x10B`** | **RESOLUTION selector** → `[0x00552FC0]` | `0x004BF68B` | `0x006394AB` | **0 = 320×200** |
| **`+0x10F`** | **PERSP. POLY** → `[0x00525E68]` | `0x004BF68F` | | **0 = OFF** |
| `+0x113` | SKID MARKS → `[0x00552E58]` | `0x004BF693` | | 1 = ON |
| `+0x117` | SMOKE → `[0x00563BE0]` | `0x004BF697` | | 1 = ON |
| `+0x123` / `+0x127` | CONTROLS P1 / P2 | `0x004BF6A3` / `0x004BF6A7` | | 0 (KEYBOARD) |
| `+0x12B` / `+0x12F` | AUTO ACC P1 / P2 | `0x004BF6AB` / `0x004BF6AF` | | 0 |
| **`+0x15B`** | **MIP MAPPING** → `[0x00527F78]` | `0x004BF6DB` | | **0 = OFF** |
| `+0x17F`..`+0x18E` | 16 keyboard bindings (DIK scancodes) | | | `CB CD C8 D0 35 34 36 28 2E 30 21 2F 2B 2A 2C 1F` |
| `+0x1BB`..`+0x233` | best-lap / ghost tables (managed by `0x0043A500`) | | | 0 |
| `+0x587` / `+0x58B` | championship unlock markers | | | 0 |
| **`+0x593`** | **LANGUAGE index** (0 = EN … 5 = FR) | `0x004BFB13` | `0x00639933` | 0 |

Delta between the two copies is a constant **`+0x179E20`**. The apply routine `0x0041935C` fans the
race copy out into live globals (`+0x004`→`0x0055306C`, `+0x0F3`→`0x00552FC4`, `+0x0F7`→`0x00552E54`,
`+0x0FB`→`0x00527F30`, `+0x0FF`→`0x00601668`, `+0x107`→`0x00527F28` & `0x00563C6C`,
`+0x10B`→`0x00552FC0`, `+0x153`→`0x006192F0`, `+0x197`→`0x00552F10`).

> **Failure mode worth knowing:** the *race-side* loader `0x00419260` is **not** tolerant — if
> `_open("ign_win.btz")` fails it logs `"Error while trying to read %s\n"` through the disabled
> logger and calls **`exit(1)` silently**; a short read logs `"LOAD ERROR\n"` and also `exit(1)`.
> In normal play the file is always created by a menu confirm before a race starts, so this is
> only reachable if the directory is read-only (e.g. under `C:\Program Files` with virtualisation
> off) or the file is corrupt. The *menu-side* loader `0x00404A60` is tolerant (missing file or
> version ≠ `0x1E` ⇒ keep compiled-in defaults).

## C.8 Localisation

The language selector is `[0x00639933]`. It lies **above** the end of `.data`'s raw content
(`0x004BCE00`), i.e. in BSS, so it starts at 0. An exhaustive byte-scan of the image finds ~120
references to that dword and **every one of them is a load** (`A1` = `mov eax,[imm32]`,
`8B 15` / `8B 0D` = `mov edx/ecx,[imm32]`); there is no store anywhere.
A stronger check — scanning the code for **any** store instruction
(`A2/A3`, `C7 05`, `C6 05`, `88/89 xx`, `66 A3`, `FF 05/0D`) whose absolute operand falls anywhere
in `0x00639928 … 0x0063993F` — finds exactly one hit, `0x00439B99` writing `0x0063992B`, which does
not overlap `0x00639933`.

**Resolved (see §C.7c):** `0x00639933` is the *race-engine* copy of the setting at struct offset
`+0x593`, so it is populated purely as a side effect of
`readFileAt("ign_win.btz", 0x006393A0, 0x597, 0)` — a bulk read, not an instruction. The value is
chosen in the front end, which writes the **menu** copy at **`0x004BFB13`** (= `0x004BF580 + 0x593`)
via the `"SELECT YOUR LANGUAGE"` screen (`0x004920EC`, drawn at `0x0040A8C9`/`0x0040A8E4`), and it
reaches the race engine only through the file. There is also an early language-only load at
`0x004029A0` (file `0x1DA0`) that version-checks and then reads **4 bytes at file offset `0x593`**
into `[0x004BE60C]`.

⇒ On a **fresh install** (`ign_win.btz` absent, or its 4-byte version header ≠ 30) the blob stays
zeroed and the game runs as **English (index 0)**, with all six language string tables
(EN/DE/IT/ES/SE/FR) and the six-language `.rsrc` dialogs compiled in. *Certainty: high that no
absolute-addressed store exists and that the variable lives inside the persisted blob; the exact
menu item that sets it was not traced.*

## C.9 Registry / INI: none

No INI file and no registry: settings live in `ign_win.btz` (§C.7c).
No `ADVAPI32` import ⇒ no registry access at all **by the game**.
(The registry *is* used by the shipped uninstaller: `xruds137.exe` imports
`ADVAPI32!RegOpenKeyExA/RegDeleteValueA` and contains the strings `Software\UDS\Ignition`,
`Ignition Path`, `Ignition`; `remove.exe` just `WinExec`s it after copying it to `%TEMP%`.
So `HKLM\Software\UDS\Ignition\Ignition Path` is written by the installer and deleted by the
uninstaller, and is never read at runtime — the game always uses paths relative to its CWD.) No `.ini`, `.cfg`, `GetPrivateProfile*` strings
anywhere in the image. `"Default.fil"` (`0x004BAB88`) is the *debug log* filename, not a settings
file (it is opened with mode `"wb"` at `0x0045B574` / `0x0045B639` and only written through the
`printf`-style logger).
