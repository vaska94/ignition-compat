# Ignition (`Ign_win.exe`, 1997) — Audio / Input / Timing specification

Target: `/mnt/c/Games/IGNITION/Ign_win.exe`, PE32 i386 GUI, ImageBase `0x00400000`,
symbols stripped, MSVC 4.x/5.x. Sections: `.text` 0x00401000 (0x77B8C),
`.rdata` 0x00479000, `.data` 0x0047C000, `.idata` 0x0064C000, `code` 0x0064E000.

Purpose of this document: enough detail to reimplement `dsound.dll` / `dinput.dll`
shims and to fix frame pacing on modern Windows, **natively** — not via a Glide
wrapper.

Every claim is tagged **[CERTAIN]** (read directly from disassembly) or
**[INFERRED]** (deduced, plausible but not directly proven).

---

## TL;DR — what a shim author needs to know

1. **DirectSound.** The game asks for `DSSCL_WRITEPRIMARY` **first** (0x004679EF)
   and, if that succeeds, writes PCM straight into a `DSBCAPS_PRIMARYBUFFER`.
   That is the Vista+ killer. **Make `SetCooperativeLevel(…, 4)` fail** and the
   game falls back to a perfectly ordinary secondary streaming buffer.
2. The fallback buffer is **one 1-second `DSBCAPS_LOCSOFTWARE` ring**, format
   negotiated as **22050 Hz / 16-bit / mono**, refilled in fixed **10 ms chunks**
   at the game's own write cursor, ~120 ms ahead of the play cursor. The game
   **software-mixes all 6 voices itself** — a shim needs no mixing, no 3D, no
   per-buffer volume/pan/frequency.
3. **Do not report `DSCAPS_EMULDRIVER`** — it triples the write-ahead to 370 ms.
4. **DirectInput 3 (`0x0300`), keyboard only, buffered only**, 32-event buffer,
   `DISCL_FOREGROUND | DISCL_NONEXCLUSIVE`, `c_dfDIKeyboard`. `GetDeviceState` is
   never used. **Any init failure kills the process** — there is no Win32
   keyboard fallback at all.
5. **The `timeSetEvent` multimedia timer is dead code** — the guard flag can
   never be set. The process is **single-threaded and never sleeps**; input
   polling and audio refill both run from the main loop.
6. **Timing is not the problem people assume.** The simulation is a fixed
   **36 Hz** accumulator, dt never enters the physics, dt is clamped to 300 ms,
   catch-up is capped at 200 ticks, and there is no `rdtsc`/calibration/16-bit
   tick counter anywhere. Game speed is already correct on modern hardware.
7. **Joystick is the legacy WINMM API, not DirectInput**: `joyGetDevCapsA` for IDs
   0 and 1 probed once at startup, then `joyGetPosEx` with
   `dwFlags = JOY_RETURNALL | JOY_USEDEADZONE`, axes mapped to ±100 by
   `raw*200/(max-min) - 100` (note: `min` is never subtracted), 5 buttons usable
   because of a loop bug, of which only 2 are consumed. In menus the joystick is
   injected as synthetic DIK key presses.
8. **What is actually broken on modern hardware:** the game burns 100 % of a core
   spinning to wait out its own 36 FPS gate (no `Sleep` is imported at all), and
   it spins unbounded on `DDERR_WASSTILLDRAWING` from `Flip`. Both have one-line
   fixes (§5.5).

## 0. Import surface (ground truth)

| DLL | Imported symbols | IAT slot | thunk |
|---|---|---|---|
| `DSOUND.dll` | `DirectSoundCreate` | 0x0064C2B8 | 0x004787A2 |
| `DINPUT.dll` | `DirectInputCreateA` | 0x0064C2A4 | 0x00478778 |
| `DDRAW.dll` | `DirectDrawCreate` | 0x0064C29C | 0x0047879C |
| `DPLAYX.dll` | ordinals `#1`, `#2` | 0x0064C2B0 / 0x0064C2AC | 0x004787AE / 0x004787A8 |
| `WINMM.dll` | `joyGetPosEx`, `joyGetDevCapsA`, `mciSendStringA`, `timeKillEvent`, `timeBeginPeriod`, `timeSetEvent`, `timeEndPeriod`, `timeGetTime` | 0x0064C460..0x0064C47C | 0x00478790.. |

**No** `DirectSoundCreate8`, no `DirectInput8Create`, no `dsound3d`. One
`DirectSoundCreate`, one `DirectInputCreateA`. **[CERTAIN]**

---

# 1. DirectSound

## 1.1 Module map

| VA | Role |
|---|---|
| `0x00467920` | `DS_Init()` — the DirectSound bring-up (the `DirectSoundCreate` call is at 0x004679E2) |
| `0x004673D0` | `DS_ClearBuffer()` — Stop + Lock whole buffer + zero-fill + Unlock |
| `0x00467510` | `DS_BeginWrite()` — GetCurrentPosition, decide whether a refill is due, Lock one chunk, return descriptor |
| `0x004677F0` | `DS_EndWrite()` — Unlock and advance the game's own write cursor |
| `0x004678B0` | `DS_Shutdown()` — Release secondary, primary, IDirectSound |
| `0x004577A0` | `SND_Init(cfg*)` — service layer: stores config, calls `DS_Init`, then mixer init `0x00465DF0` |
| `0x00457890` | `SND_Service()` — the pump: `while ((d = DS_BeginWrite())) { Mix(d, state); DS_EndWrite(); }` |
| `0x00465F20` | `Mix(lockDesc, engineState)` — software mixer front end |
| `0x00466150` | mixer render inner (`render(dest, numSamples, sampleOffset)`) |

## 1.2 Interface globals

| Global | Type |
|---|---|
| `0x0063C614` | `LPDIRECTSOUND` |
| `0x0063C660` | `LPDIRECTSOUNDBUFFER` — **the PRIMARY buffer** (always created) |
| `0x0063C640` | `LPDIRECTSOUNDBUFFER` — the **secondary streaming buffer** (only in the fallback path; stays NULL in write-primary mode) |

The `HWND` passed to `IDirectSound::SetCooperativeLevel` is `[0x00493738]`; DirectInput uses `[0x004C539C]`. Both are the **same** window handle, stored side by side right after `CreateWindowExA` (0x0045B789 / 0x0045B78E). **[CERTAIN]**

The earlier note that these were "two streaming/double buffers" is **wrong**:
they are primary + secondary, and only one of them is ever streamed to.
**[CERTAIN]** — proved by `SetFormat` being called on 0x0063C660 (0x00467B1E)
and by the `DSBCAPS_PRIMARYBUFFER` desc that creates it (0x00467A82).

## 1.3 `DS_Init` — cooperative level: **the game asks for DSSCL_WRITEPRIMARY first**

```
0x004679D9  push 0                       ; pUnkOuter
0x004679DB  push 0x63c614                ; ppDS
0x004679E0  push 0                       ; lpGuid = NULL (default device)
0x004679E2  call DSOUND!DirectSoundCreate
0x004679E7  test eax,eax
0x004679E9  jne  0x467f0e                ; any failure -> return 0 (sound off)

0x004679EF  push 4                       ; DSSCL_WRITEPRIMARY
0x004679F1  mov  eax,[0x493738]          ; hWnd
0x004679F7  mov  ecx,[0x63c614]
0x00467A00  call [eax+0x18]              ; IDirectSound::SetCooperativeLevel
0x00467A03  test eax,eax
0x00467A05  je   0x467d2d                ; SUCCESS -> WRITE-PRIMARY PATH

0x00467A0B  push 3                       ; DSSCL_EXCLUSIVE
0x00467A1C  call [eax+0x18]              ; SetCooperativeLevel
0x00467A1F  test eax,eax
0x00467A21  jne  0x467f0e                ; failure -> return 0 (sound off)
                                          ; SUCCESS -> SECONDARY-BUFFER PATH
```
**[CERTAIN]**. `hWnd` global is `0x00493738`.

So: **yes — this is the classic Vista+ breakage.** The game prefers
`DSSCL_WRITEPRIMARY` + a `DSBCAPS_PRIMARYBUFFER` buffer that it `Lock()`s and
writes PCM into directly, and only falls back to a secondary streaming buffer if
`SetCooperativeLevel(DSSCL_WRITEPRIMARY)` returns a failure HRESULT.
There is **no config switch** gating this; it is unconditional.

`0x0063C634` is set to 4 at 0x004679A7 and then **never read** (dead).

## 1.4 Format negotiation — 6 candidate formats, tried in order

The prologue (0x00467920..0x0046799A) builds three parallel 6-entry stack arrays.
Decoded (entry-ESP-relative; body ESP = S0−0x10): **[CERTAIN]**

| idx | channels | sample rate | bits |
|---|---|---|---|
| 0 | 1 (mono) | 22050 | 16 |
| 1 | 2 (stereo) | 22050 | 16 |
| 2 | 1 (mono) | 44100 | 16 |
| 3 | 1 (mono) | 22050 | 8 |
| 4 | 2 (stereo) | 22050 | 8 |
| 5 | 2 (stereo) | 44100 | 8 |

The loop (`xor edi,edi` @0x00467A8D … `inc edi; cmp edi,6; jl 0x467a8f` @0x00467C08)
builds a **16-byte `PCMWAVEFORMAT`** (not a full 18-byte `WAVEFORMATEX` — only
4 dwords are zeroed at 0x00467AA0, and `cbSize` is never written) on the stack at
`S0+0x00`:

```
wFormatTag      = 1 (WAVE_FORMAT_PCM)        0x00467AD1
nChannels       = tbl_ch[i]                  0x00467ABB
nSamplesPerSec  = tbl_rate[i]                0x00467ADC
nAvgBytesPerSec = nBlockAlign * rate         0x00467B12
nBlockAlign     = (bits*channels + 7) / 8    0x00467AEC
wBitsPerSample  = tbl_bits[i]                0x00467AC2
```
and calls **`IDirectSoundBuffer::SetFormat(primary, &wfx)`** at **0x00467B1E**
(write-primary path: **0x00467E2B**). First format that returns `DS_OK` wins.

**Practical consequence:** on any sane device the negotiated format is
**22050 Hz, 16-bit, MONO** (index 0). A shim should let index 0 succeed.

Derived globals after negotiation **[CERTAIN]**:

| Global | Value |
|---|---|
| `0x0063C620` | nSamplesPerSec |
| `0x0063C624` | wBitsPerSample |
| `0x0063C628` | nChannels − 1 (0 = mono, 1 = stereo) |
| `0x0063C62C` | samples per 10 ms = rate / 100 (integer divide) |
| `0x0063C630` | (bits/8) − 1 |
| `0x00520444` | nBlockAlign |
| `0x00520448` | actual buffer size in bytes (from `DSBCAPS.dwBufferBytes`) |
| `0x00520418` | **write-ahead distance in bytes** |
| `0x00520434` | the game's own running write cursor (bytes) |
| `0x0052042C` | 1 = write-primary mode, 0 = secondary mode |
| `0x004BAD48` | "buffer is playing" flag |
| `0x004BAD4C` | "sound initialised" flag |

## 1.5 Every `CreateSoundBuffer` call — decoded `DSBUFFERDESC`

**(a) 0x00467A82 — primary buffer, secondary-streaming path** **[CERTAIN]**
```
dwSize        = 0x14 (20)            0x00467A4D
dwFlags       = 0x00000001           0x00467A66   DSBCAPS_PRIMARYBUFFER
dwBufferBytes = 0                    0x00467A6E
dwReserved    = 0
lpwfxFormat   = NULL                 0x00467A76
ppDSBuffer    = &g_0x0063C660 ; pUnkOuter = NULL
```

**(b) 0x00467BF6 — the streaming SECONDARY buffer** **[CERTAIN]**
```
dwSize        = 0x14 (20)            0x00467BDC
dwFlags       = 0x000000E8           0x00467BE4
                = DSBCAPS_LOCSOFTWARE (0x08)
                | DSBCAPS_CTRLFREQUENCY (0x20)
                | DSBCAPS_CTRLPAN (0x40)
                | DSBCAPS_CTRLVOLUME (0x80)
dwBufferBytes = nAvgBytesPerSec      0x00467BD3   -> EXACTLY 1.0 SECOND
lpwfxFormat   = &wfx (the negotiated PCMWAVEFORMAT)  0x00467BD7
ppDSBuffer    = &g_0x0063C640 ; pUnkOuter = NULL
```
Note: `CTRLVOLUME/CTRLPAN/CTRLFREQUENCY` are requested but
`SetVolume`/`SetPan`/`SetFrequency` are **never called anywhere in the binary** —
volume/pan are done in the software mixer. `DSBCAPS_LOCSOFTWARE` is explicitly
requested, so the game never wants a hardware voice.
No `DSBCAPS_GLOBALFOCUS` / `STICKYFOCUS` → **audio dies on focus loss**.
No `DSBCAPS_GETCURRENTPOSITION2` → it accepts the legacy (emulated-driver-fudged)
play cursor semantics.

**(c) 0x00467D92 — primary buffer, WRITE-PRIMARY path** **[CERTAIN]**
```
dwSize        = 0x14                 0x00467D5D
dwFlags       = 0x00000001           0x00467D76   DSBCAPS_PRIMARYBUFFER
dwBufferBytes = 0                    0x00467D7E
lpwfxFormat   = NULL                 0x00467D86
ppDSBuffer    = &g_0x0063C660
```
In this path `g_0x0063C640` stays NULL and **all PCM is written straight into
the primary buffer**.

## 1.6 Capability queries

* `IDirectSoundBuffer::GetCaps` — 0x00467C3F (secondary), 0x00467E8E (primary).
  `DSBCAPS.dwSize = 0x14`; only `dwBufferBytes` (offset +8) is read →
  `0x00520448`. **[CERTAIN]**
* `IDirectSound::GetCaps` — **0x00467C9B**, secondary path only.
  `DSCAPS.dwSize = 0x60 (96)`. Only one bit is tested:
  ```
  0x00467CA2  test byte ptr [esp+0x9c], 0x20     ; DSCAPS.dwFlags & DSCAPS_EMULDRIVER
  ```
  **[CERTAIN]** — this selects the latency fudge (below).

## 1.7 Write-ahead (latency) computation — **important for a shim**

`g_latency = [0x0063C638]` comes from the caller's config struct (see §1.11);
the live value is **10**. `chunkBytes = samplesPer10ms * nBlockAlign` ≈ 10 ms.

| Path | write-ahead `[0x00520418]` | VA |
|---|---|---|
| secondary, `DSCAPS_EMULDRIVER` set (or `GetCaps` failed) | `(g_latency + 27) * chunkBytes` | 0x00467C5D–0x00467C76 |
| secondary, real HAL (`EMULDRIVER` clear) | `(g_latency + 2) * chunkBytes` | 0x00467CAC–0x00467CC0 |
| write-primary | `g_latency * chunkBytes` (no fudge) | 0x00467EA7–0x00467EBD |

then clamped: `writeAhead = min(writeAhead, bufferBytes − chunkBytes)`
(0x00467CC5–0x00467CDF / 0x00467EC2–0x00467ED1). **[CERTAIN]**

With the live config (10) and 22050/16/mono (`chunkBytes` = 220 × 2 = 440 B ≈ 9.977 ms):

* real HAL: write-ahead = 12 × 440 = **5280 B ≈ 120 ms**
* emulated: write-ahead = 37 × 440 = **16280 B ≈ 369 ms**
* write-primary: 10 × 440 = **4400 B ≈ 100 ms**

**Shim guidance:** do *not* report `DSCAPS_EMULDRIVER`, or you triple the audio
latency to ~370 ms.

## 1.8 Both buffers are started immediately

```
0x00467D00  Play(primary,   0, 0, DSBPLAY_LOOPING)   ; secondary path
0x00467D11  Play(secondary, 0, 0, DSBPLAY_LOOPING)
0x00467EF2  Play(primary,   0, 0, DSBPLAY_LOOPING)   ; write-primary path
```
`dwFlags = 1 = DSBPLAY_LOOPING` in every `Play` call in the binary. **[CERTAIN]**

## 1.9 The streaming loop — `DS_BeginWrite` (0x00467510)

Both branches (`0x00467529…` write-primary on `g_0x0063C660`, `0x00467688…`
secondary on `g_0x0063C640`) are byte-for-byte the same logic on a different
buffer pointer. Reconstructed C **[CERTAIN]**:

```c
void* DS_BeginWrite(void)              // returns &g_lockDesc (0x0063C650) or NULL
{
    if (g_soundInit /*0x4BAD4C*/ != 1) return NULL;
    LPDIRECTSOUNDBUFFER b = g_writePrimary /*0x52042C*/ ? g_pPrimary : g_pSecondary;

    if (!g_playing /*0x4BAD48*/) { b->Play(0,0,DSBPLAY_LOOPING); g_playing = 1; }

    b->GetCurrentPosition(&g_play /*0x520438*/, &g_write /*0x520420*/);

    DWORD T = g_play + g_writeAhead;                 // 0x520420 reused as target
    if (T >= g_bufBytes) T -= g_bufBytes;            // single wrap only
    DWORD chunk = g_samplesPer10ms * g_blockAlign;   // ~10 ms
    DWORD E = T + chunk;                             // 0x52041C
    DWORD W = g_lastWrite;                           // 0x520434

    if (g_bufBytes > E) {                            // chunk does not straddle end
        if (!(W >= E || T > W)) return NULL;
    } else {                                         // straddles: E -= bufBytes
        E -= g_bufBytes;
        if (W < E)  return NULL;
        if (!(T > W)) return NULL;
    }

    HRESULT hr = b->Lock(W, chunk, &p1,&b1, &p2,&b2, 0 /*dwFlags*/);
    if (hr == 0x88780096 /*DSERR_BUFFERLOST*/) {     // 0x00467617 / 0x00467776
        b->Restore();                                // 0x00467626 / 0x00467785
        b->Play(0,0,DSBPLAY_LOOPING);                // 0x00467638 / 0x00467797
        return NULL;
    }
    if (hr != DS_OK) return NULL;

    g_lockDesc.p1 = p1;                    // 0x0063C650
    g_lockDesc.p2 = p2;                    // 0x0063C654
    g_lockDesc.n1 = b1 / g_blockAlign;     // 0x0063C658  (SAMPLES, not bytes)
    g_lockDesc.n2 = b2 / g_blockAlign;     // 0x0063C65C
    return &g_lockDesc;
}
```

Net effect: **refill exactly one ~10 ms chunk per call, at the game's own write
cursor `W`, whenever `W` is circularly behind `T = (playCursor + writeAhead) mod
bufferBytes`.** `dwWriteCursor` passed to `Lock` is `W`, `dwWriteBytes` is
always `chunk` — a **fixed 10 ms lock granularity**.

`DS_EndWrite` (0x004677F0) **[CERTAIN]**:
```c
b->Unlock(p1, b1, p2, b2);                       // 0x00467828 / 0x0046786A
g_lastWrite += g_samplesPer10ms * g_blockAlign;  // 0x00467837 / 0x00467879
while (g_lastWrite >= g_bufBytes) g_lastWrite -= g_bufBytes;
```

## 1.10 What drives the refill — **the main loop, NOT the multimedia timer**

```
SND_Service (0x00457890):
  0x004578A5  call DS_BeginWrite            (0x00467510)
  0x004578B4  call Mix(desc, 0x0063CAA0)    (0x00465F20)
  0x004578BC  call DS_EndWrite              (0x004677F0)
  0x004578C1  call DS_BeginWrite            ; loop until it returns NULL
```
`SND_Service` has exactly **one** live caller: **0x0041776C**, inside the
per-frame game-step function **0x004172B0**, which is called from the platform
main loop at **0x00412284**. (0x00403E79 and 0x00411639 are unreferenced
`jmp 0x457890` linker aliases — dead.) **[CERTAIN]**

> **This is a big deal for modern Windows.** Audio refill is *frame-rate
> coupled*. Any hitch longer than the write-ahead (≈120 ms on a real HAL) causes
> an audible repeat/underrun, and the audio thread has no independent clock.
> The multimedia timer (`timeSetEvent`) does **not** touch audio — see §2.

## 1.11 Service layer + software mixer

`SND_Init(cfg)` @ 0x004577A0 takes a pointer to:
```c
struct SndCfg { int latency10ms; int masterVolume; short numVoices; };
```
* live call site **0x0041F9DD** (function 0x0041F9B0, failure prints
  `"Error while initiating sound"` @0x004990C4): `{ 10, [0x00527F20]=100, (short)[0x0054F958]=6 }`
  → **latency = 10 (×10 ms), volume = 100, 6 mixer voices**. **[CERTAIN]**
  (Defaults assigned at 0x00419487 / 0x00419491.)
* second call site **0x00403BE6** with `{5, 100, 2}` — its result is compared
  against 2, which `SND_Init` never returns, so that path is effectively dead.
  **[INFERRED]**

`Mix()` @0x00465F20 renders the **whole locked region**, handling the
split/wrapped lock:
```
render(desc->p1, desc->n1, 0);                 // 0x00465F5F
if (desc->p2 && desc->n2)
    render(desc->p2, desc->n2, desc->n1);      // 0x00465F83
```
The mixer is a **pure software mixer over a linked list of voices**
(next-pointer at +0x20; list walker at 0x00467120, voice table at
`state+0x2404`, count at `state+0x2804`; engine state block at **0x0063CAA0**).
Mixer init 0x00465DF0 requires `2 <= numVoices <= 256`, allocates a 0x10200-byte
scratch buffer, and caches the negotiated format into 0x005203D0..0x00520414.
Format-specialised inner loops are selected via a jump table at **0x00467014**.
**[CERTAIN]** that it is a software mixer; the DSP detail was not fully reversed.

**Conclusion for §1: the game software-mixes all 6 voices into ONE streaming
PCM buffer and hands DirectSound nothing but raw PCM.** A shim needs no mixing,
no 3D, no per-buffer volume — just one linear ring buffer with an honest play
cursor.

## 1.12 Music is MCI CD-audio, not DirectSound

9 `mciSendStringA` call sites (0x00457B21, 0x00457D1B, 0x00457D83, 0x00457FA4,
0x00457FD1, 0x0045801B, 0x004580B1, 0x004580D1). Command strings in `.data`
at 0x004BA7B8..0x004BA99C: `open cdaudio`, `close cdaudio`,
`set cdaudio time format msf`, `status cdaudio number of tracks`,
`status cdaudio media present`, `status cdaudio mode` (compared against
`"playing"` @0x004BA850), `status cdaudio current track`,
`status cdaudio position`, `status cdaudio length`,
`play cdaudio from XX:XX:XX to XX:XX:XX`, `play cdaudio to XX:XX:XX`,
`seek cdaudio to 00:00:00`, `stop cdaudio`, `set cdaudio audio all on|off`,
`set cdaudio door open`. **[CERTAIN]**

On modern Windows there is no CD in the drive; the music will simply never
start. Redirecting music requires intercepting `mciSendStringA`, not DirectSound.

## 1.13 Complete DirectSound API surface (every call site)

**`DSOUND!DirectSoundCreate`** — 1 call site: **0x004679E2**,
args `(NULL, &g_0x0063C614, NULL)`.

### `IDirectSound`
| Slot | Method | Call sites | Args |
|---|---|---|---|
| 2 | `Release` | 0x0046790D | — |
| 3 | `CreateSoundBuffer` | **0x00467A82**, **0x00467BF6**, **0x00467D92** | see §1.5 |
| 4 | `GetCaps` | **0x00467C9B** | `DSCAPS.dwSize = 0x60`; only `DSCAPS_EMULDRIVER` tested |
| 6 | `SetCooperativeLevel` | **0x00467A00** (`DSSCL_WRITEPRIMARY`=4), **0x00467A1C** (`DSSCL_EXCLUSIVE`=3) | hWnd = `[0x00493738]` |

Never called: `QueryInterface`, `AddRef`, `DuplicateSoundBuffer`, `Compact`,
`GetSpeakerConfig`, `SetSpeakerConfig`, `Initialize`.

### `IDirectSoundBuffer`
| Slot | Method | Call sites |
|---|---|---|
| 2 | `Release` | 0x004678DE (secondary), 0x004678F8 (primary) |
| 3 | `GetCaps` | **0x00467C3F** (secondary), **0x00467E8E** (primary) — `dwSize=0x14`, reads `dwBufferBytes` |
| 4 | `GetCurrentPosition` | **0x0046755F** (primary), **0x004676BE** (secondary) — both out-pointers are non-NULL (`&[0x520438]`, `&[0x520420]`) so the shim **must write both**, but the **write-cursor value is discarded**: `[0x520420]` is overwritten one instruction later with `playCursor + writeAhead` (0x00467562–0x00467573 / 0x004676C1–0x004676D2). Only the *play* cursor matters. |
| 11 | `Lock` | **0x00467426**, **0x004674B4** (whole-buffer clear); **0x0046760F**, **0x0046776E** (10 ms refill) — `dwFlags = 0` always |
| 12 | `Play` | 0x00467540, 0x00467638, 0x0046769F, 0x00467797, 0x00467D00, 0x00467D11, 0x00467EF2 — always `(0, 0, DSBPLAY_LOOPING)` |
| 14 | `SetFormat` | **0x00467B1E**, **0x00467E2B** — primary only, 16-byte `PCMWAVEFORMAT` |
| 18 | `Stop` | 0x004673F1, 0x0046747F |
| 19 | `Unlock` | 0x00467471, 0x004674FF, 0x00467828, 0x0046786A |
| 20 | `Restore` | **0x00467626**, **0x00467785** — only on `DSERR_BUFFERLOST` |

Never called: `QueryInterface`, `AddRef`, `GetFormat`, `GetVolume`, `GetPan`,
`GetFrequency`, `GetStatus`, `Initialize`, `SetCurrentPosition`, `SetVolume`,
`SetPan`, `SetFrequency`.

**HRESULT contract the shim must honour** **[CERTAIN]**:
* `DirectSoundCreate`, `SetCooperativeLevel(EXCLUSIVE)`, `CreateSoundBuffer`,
  `GetCaps` (both), `Lock` are checked with `test eax,eax` → must return **exactly 0**.
* `SetFormat` non-zero simply advances to the next candidate format.
* `Lock` returning **0x88780096** (`DSERR_BUFFERLOST`) triggers `Restore()` + `Play()`.
  Any other non-zero silently skips the frame's audio.
* `Unlock`, `Stop`, `Play`, `Release` return values are ignored.

## 1.14 DirectSound breakage on modern Windows and the minimal fixes

1. **`DSSCL_WRITEPRIMARY` is the preferred path (0x004679EF).** On Vista+ the
   in-box `dsound.dll` accepts any cooperative level and hands out a dummy
   primary buffer; `SetFormat`/`Lock` on it do nothing useful, and
   `GetCurrentPosition` on a primary never advances — so `DS_BeginWrite` would
   never fire, or would write into a void. **Result: silence.** **[INFERRED
   for the exact Vista+ behaviour; CERTAIN that the game takes that path first.]**
   *Minimal fix:* the shim's `SetCooperativeLevel` returns a failure HRESULT
   (e.g. `DSERR_INVALIDPARAM`) for `dwLevel == 4`. One line; the game then takes
   the fully-supported secondary path.
2. **Do not set `DSCAPS_EMULDRIVER`** in `IDirectSound::GetCaps` (0x00467C9B) —
   it triples write-ahead from 120 ms to 370 ms.
3. **No `DSBCAPS_GLOBALFOCUS`** → the shim should keep rendering (or the game
   goes silent) when the window loses focus. Ignoring focus in the shim is safer
   than the original behaviour.
4. **Refill is main-loop-coupled** (§1.10). A shim that buffers ≥120 ms
   internally, or a frame-rate cap that guarantees the loop runs ≥ ~20 Hz, is
   needed to avoid stutter.
5. `DS_ClearBuffer` (0x00467441 / 0x004674CF) fills the buffer with **0x00**,
   which is silence for 16-bit PCM but **full-scale DC for 8-bit PCM**. Only
   matters if the shim forces an 8-bit format — so let 22050/16/mono succeed.
6. `g_lastWrite` (0x00520434) is **never re-synchronised** to the play cursor
   after `Restore()`, so a lost-buffer event leaves a permanent phase offset.
   Cheap fix in a shim: never report `DSERR_BUFFERLOST`.

---

# 2. The WINMM multimedia timer (`timeSetEvent`) — **dead code in the shipped build**

`timeSetEvent` is called exactly once, at **0x00455DFE**, inside
`InitInput_WithTimer` @ **0x00455C60**:

```
0x00455DD8  push 1              ; fuEvent  = TIME_PERIODIC
0x00455DDA  push 0              ; dwUser   = 0
0x00455DE2  push 0x456160       ; lpTimeProc
0x00455DEC  push eax            ; uResolution = arg3
0x00455DF3  mov  [0x4ba65c],1   ; g_timerMode = 1
0x00455DFD  push eax            ; uDelay      = arg3   (uDelay == uResolution)
0x00455DFE  call [0x64c474]     ; WINMM!timeSetEvent
0x00455E05  mov  [0x4ba658],eax ; g_timerId
```
Callback **0x00456160** (`ret 0x14`, correct `LPTIMECALLBACK` stdcall shape):
```c
PollInput();                       // 0x00455EF0
if (g_hook /*0x50E678*/) g_hook(); // optional user hook, always NULL (0x00455AB7)
```
So the timer, had it run, would poll the **DirectInput keyboard** — it has
nothing to do with audio.

**But it never runs.** **[CERTAIN]**, by exhaustive xref:

* `0x00455C60` has exactly one caller: **0x00455ECD**, inside `ReInitInput`
  @0x00455EB0, guarded by `cmp dword ptr [0x4ba65c], 0 / je` (0x00455EBB).
* `[0x004BA65C]` is written in only two places: `mov [0x4ba65c], 1` at
  **0x00455DF3** — inside `0x00455C60` itself — and `mov [0x4ba65c], eax` with
  `eax == 0` at **0x00455C46**, the tail of `InitInput_NoTimer` @0x00455AC0.
* Its file-image initial value is **0** (file offset 0xB8C5C).
* No data reference to `0x00455C60` exists (no function-pointer table entry).

Therefore `[0x004BA65C]` can never become 1, `0x00455C60` is unreachable,
`timeSetEvent`/`timeKillEvent` are never executed, and callback 0x00456160 is
dead. **A `winmm` shim does not need `timeSetEvent` at all.**

> Reconciliation note: because the timer never arms, there is **no second thread** in
> this process at all (`CreateThread` is not imported either), so none of the
> `PollInput` / audio state needs synchronisation — which is just as well, since the
> binary imports no synchronisation primitives whatsoever.

Consequence: **keyboard polling is main-loop driven**, exactly like audio.
`PollInput` @0x00455EF0 live call sites: **0x00417417** (top of the per-frame
step function 0x004172B0), 0x004183C7, 0x00402BCF, 0x00402DC4, 0x00403CA6,
0x00403CAB, 0x00403CB0, 0x00403CC4. **[CERTAIN]**

Key auto-repeat (0x00455F0E–0x00455F4E, run immediately before
`GetDeviceData`) integrates against the **global millisecond tick
`[0x0049373C]`**, which the platform timing layer refreshes once per frame
(written at 0x00412252 / 0x0041227F / 0x0041229D). So auto-repeat resolution
equals the frame period. **[CERTAIN]**

---

# 3. DirectInput

Summary: **DirectInput 3 (`dwVersion = 0x0300`), keyboard only, buffered only.**
No mouse, no DirectInput joystick, no `GetDeviceState`, and **no Win32 keyboard
fallback whatsoever**.

## 3.1 Objects

| Global | Type |
|---|---|
| `0x0050DF60` | `LPDIRECTINPUTA` |
| `0x0050E26C` | `LPDIRECTINPUTDEVICEA` (system keyboard) |
| `0x0050E270` | `BOOL g_acquired` |

## 3.2 Init sequence (duplicated verbatim in two functions)

`InitInput_NoTimer` @ **0x00455AC0** (the live one) and `InitInput_WithTimer`
@ 0x00455C60 (dead, §2) contain identical DirectInput bodies. **[CERTAIN]**

```
0x00455AF8  push 0                 ; punkOuter = NULL
0x00455AF9  mov  edx,[0x4c5398]    ; hInstance (from WinMain, stored at 0x004120B2)
0x00455B03  push 0x50df60          ; ppDI
0x00455B0C  push 0x300             ; dwVersion = DIRECTINPUT_VERSION 0x0300
0x00455B19  push edx
0x00455B1A  call 0x478778          ; DINPUT!DirectInputCreateA
```
(second copy at **0x00455CBA**, `push 0x300` at 0x00455CAC)

| Step | VA | Argument detail |
|---|---|---|
| `CreateDevice` | **0x00455B3F**, 0x00455CDF | `rguid` = a 16-byte **stack copy** of the GUID at `0x00479548` = `{6F1D2B61-D5A0-11CF-BFC7-444553540000}` = **`GUID_SysKeyboard`**; device → `0x0050E26C` |
| `SetDataFormat` | **0x00455B5A**, 0x00455CFA | immediate `0x00478760` — a static `DIDATAFORMAT` embedded in `.text`: `dwSize=24, dwObjSize=16, dwFlags=2 (DIDF_RELAXIS), dwDataSize=256, dwNumObjs=256, rgodf=0x00477760`. All 256 `DIOBJECTDATAFORMAT` entries verified: `{pguid=0x00479518 (GUID_Key {55728220-D33C-11CF-BFC7-444553540000}), dwOfs=i, dwType=DIDFT_OPTIONAL\|DIDFT_MAKEINSTANCE(i)\|DIDFT_BUTTON, dwFlags=0}` → a verbatim copy of **`c_dfDIKeyboard`** |
| `SetCooperativeLevel` | **0x00455B79**, 0x00455D19 | `hwnd = [0x004C539C]` (from `CreateWindowExA` @0x0045B789), `dwFlags = 6 = DISCL_FOREGROUND \| DISCL_NONEXCLUSIVE` |
| `SetProperty` | **0x00455B97**, 0x00455D37 | `rguidProp = (GUID*)1 = DIPROP_BUFFERSIZE`; `DIPROPDWORD = { dwSize=20, dwHeaderSize=16, dwObj=0, dwHow=DIPH_DEVICE(0), dwData=`**`32`**` }` |
| `Acquire` | **0x00455BAD**, 0x00455D4D | — |

**Buffer size = 32 events.** Every one of the above is checked with
`test eax,eax` → the shim must return **exactly 0**; any non-zero aborts init,
and (see §3.5) the process then exits. **[CERTAIN]**

## 3.3 Polling — buffered, `GetDeviceData` only

`PollInput` @ **0x00455EF0**:
```
0x00455F04  mov  dword ptr [esp+8], 0x20   ; dwInOut = 32
0x00455F50  lea  eax,[esp+0x10]            ; pdwInOut
0x00455F54  push 0                         ; dwFlags = 0  (NOT DIGDD_PEEK — data is consumed)
0x00455F56  lea  ecx,[esp+0x18]            ; rgdod = 32*16-byte STACK buffer
0x00455F64  push 0x10                      ; cbObjectData = 16
0x00455F67  call [eax+0x28]                ; IDirectInputDevice::GetDeviceData
```
`GetDeviceState` (slot 9) is **never called anywhere in the binary**. **[CERTAIN]**

Error handling **[CERTAIN]**:
```
0x00455F6A  cmp eax, 0x8007001e     ; DIERR_INPUTLOST
0x00455F6F  jne 0x455fa3
0x00455F71  mov [0x50e270], 0       ; g_acquired = 0
0x00455F83  call [ebx+0x1c]         ; Acquire
0x00455F88  jl  0x45605d            ; still lost -> give up this frame
0x00455F8E  mov [0x50e270], 1       ; reacquired, but returns WITHOUT reading data
0x00455FA3  test eax,eax
0x00455FA5  je  <process>           ; DI_OK
0x00455FA7  cmp eax, 1
0x00455FAA  jne 0x456053            ; anything else -> silently drop this frame
```
Only `DIERR_INPUTLOST` triggers re-`Acquire`. `DI_OK (0)` and
`DI_BUFFEROVERFLOW (1)` are both treated as "there is data". Any other HRESULT
(including `DIERR_NOTACQUIRED`) is silently dropped with **no recovery** — a
shim that returns `DIERR_NOTACQUIRED` makes the game permanently deaf.

Per-entry consumption (0x00455FC7–0x0045604D): reads only
`dwOfs & 0xFF` (the DIK scancode) and `dwData & 0x80` (key-down bit).
`dwSequence` and `dwTimeStamp` are **never read**, so a shim may leave them zero.

State arrays, all indexed by DIK code **[CERTAIN]**:

| Global | Size | Meaning |
|---|---|---|
| `0x0050DE60` | 256 B | `keyDown[dik]` |
| `0x0050E068` | 256 B | `keyPressedEdge[dik]` |
| `0x0050E168` | 256 B | de-dup latch for the edge flag |
| `0x0050DF68` | 256 B | `keyRepeatFired[dik]` |
| `0x0050E278` | 256 DW | per-key repeat accumulator (ends at 0x0050E678) |

On key-down two hooks run: `0x004560C0(1, dik)` (dispatch to `[0x0050DE14]`,
always NULL) and `0x004560E0(1, dik)` — a **cheat-code recorder** that maps
`dik <= 0x53` through the byte table at `0x004BA660` into a 32-byte ring at
`0x0050DE18`, later `strstr`-searched at 0x00456120.

Repeat parameters: initial delay `[0x0050DF64]`, repeat interval
`[0x0050E274]`, previous tick `[0x0050E268]`, current tick `[0x0049373C]`.

## 3.3b Key auto-repeat is broken by an argument-passing bug **[CERTAIN]**

`InitInput_NoTimer` @0x00455AC0 reads two `int` parameters (`sub esp,0x24` +
`push edi` ⇒ they live at `[esp+0x2c]` / `[esp+0x30]`) and stores them as the
auto-repeat **initial delay** `[0x0050DF64]` and **repeat interval**
`[0x0050E274]` (0x00455C21–0x00455C3A). But **both live call sites pass nothing**:

```
0x0041241B  call 0x456bc0
0x00412420  call 0x455ac0        ; no pushes, no `add esp` afterwards
...
0x0041250A  call 0x456bc0
0x0041250F  test eax,eax / je
0x00412513  call 0x455ac0        ; no pushes, no `add esp` afterwards
```
(Only the dead `ReInitInput` path at 0x00455EDB pushes 2 args and does `add esp,8`.)

So the repeat delay/interval are read from whatever the caller's stack happens to
hold — in practice a code address (~0x0041xxxx ≈ 4.3 million "ms"), so the
repeat comparison at 0x00455F32 never fires and **key auto-repeat is effectively
dead**. Harmless for driving; it means a shim need not emulate DirectInput
repeat semantics at all.

## 3.4 Teardown / re-init

`ShutdownInput` @ **0x00455E20** (null-safe, idempotent):
`Unacquire` (0x00455E31) → device `Release` (0x00455E4F) → `IDirectInput::Release`
(0x00455E6D) → `timeKillEvent` (0x00455E89, dead branch).
Callers: 0x004123F5 (WndProc Alt+Enter), 0x00412555 (quit path), 0x00455EA0.

`ReInitInput` @ **0x00455EB0** re-runs the whole create sequence. It is invoked
around display-mode changes (0x0040465F, 0x004046AA) and Alt+Enter, so the shim
**must support repeated full create/release cycles**. **[CERTAIN]**

## 3.5 No fallback — DirectInput failure is fatal

* Every init failure returns 0; `InitInputSubsystem` @0x00412500 propagates it to
  WinMain @0x00412175 (`test eax,eax; je 0x41217E` → `xor eax,eax; ret 0x10`) —
  **the process exits before the message loop starts**.
* USER32 imports contain **no** `GetAsyncKeyState`, `GetKeyboardState`,
  `GetKeyState`, `MapVirtualKey`, `ToAscii` or `keybd_event`.
* WndProc @0x004122D0 routes `WM_KEYDOWN (0x100)` to 0x004123D0 =
  `xor eax,eax; ret 0x10` — **swallowed**. `WM_SYSKEYUP` is handled only for
  `VK_RETURN` (Alt+Enter fullscreen toggle, which tears down and re-inits DI).

**[CERTAIN]**

## 3.6 Complete DirectInput API surface

**`DINPUT!DirectInputCreateA`** — call sites **0x00455B1A**, 0x00455CBA (dead);
args `(g_hInstance[0x4C5398], 0x00000300, &g_pDI[0x50DF60], NULL)`.

### `IDirectInputA`
| Slot | Method | Call sites |
|---|---|---|
| 2 | `Release` | 0x00455E6D |
| 3 | `CreateDevice` | **0x00455B3F**, 0x00455CDF |

### `IDirectInputDeviceA`
| Slot | Method | Call sites |
|---|---|---|
| 2 | `Release` | 0x00455E4F |
| 6 | `SetProperty` | **0x00455B97**, 0x00455D37 |
| 7 | `Acquire` | 0x00455BAD, 0x00455D4D, **0x00455F83** |
| 8 | `Unacquire` | 0x00455E31 |
| 10 | `GetDeviceData` | **0x00455F67** |
| 11 | `SetDataFormat` | **0x00455B5A**, 0x00455CFA |
| 13 | `SetCooperativeLevel` | **0x00455B79**, 0x00455D19 |

Never called: `QueryInterface`, `AddRef`, `EnumDevices`, `GetDeviceStatus`,
`RunControlPanel`, `Initialize`, `GetCapabilities`, `EnumObjects`, `GetProperty`,
`GetDeviceState`, `SetEventNotification`, `GetObjectInfo`, `GetDeviceInfo`.
(Stub the slots anyway; the vtable layout must be exact.)

**Return-value contract** **[CERTAIN]**:
`DirectInputCreateA`, `CreateDevice`, `SetDataFormat`, `SetCooperativeLevel`,
`SetProperty` → must be **exactly 0** (`test eax,eax`).
`Acquire` → checked with `jl`, so any `HRESULT >= 0` is accepted.
`GetDeviceData` → return `0` or `1`; `0x8007001E` triggers re-Acquire; anything
else silently discards input.

---

# 4. Cross-cutting process facts (relevant to "make it run on modern Windows")

* **Single-threaded, never sleeps.** KERNEL32 imports contain **no `Sleep`, no
  `CreateThread`, no `WaitForSingleObject`, no `SetThreadPriority`, no
  `Sleep`/`SwitchToThread` equivalent**. Everything — input polling, audio
  refill, simulation, rendering — happens on one thread inside the main loop.
  **[CERTAIN]** (full KERNEL32/USER32 import list enumerated.)
  Consequence: the frame pacer, whatever it is, can only be a spin or a blocking
  `GetMessage`; on a modern CPU a spin pegs one core at 100%.
* **The frame dispatcher** at 0x00412230–0x004122C9 is a 3-state machine on
  `[0x00493734]` (0 = enter, 1 = run, 2 = leave). In **every** state it first does
  `[0x0049373C] = GetTimeMs()` (`call 0x00412460`) and then dispatches:
  state 0 → `0x00417270`, state 1 → **`0x004172B0`** (the per-frame step that
  polls input at 0x00417417 and pumps audio at 0x0041776C), state 2 → 0x00417E90
  + `timeEndPeriod(1)` at **0x004122B7**. **[CERTAIN]**
* **`timeBeginPeriod(1)` / `timeEndPeriod(1)`** are the only WINMM timer calls
  actually executed (0x0041216A / 0x004122B7). **[CERTAIN]**
* **DEP hazard (not audio/input, but a hard blocker):** the PE has an extra
  section literally named **`code`** at **0x0064E000** (0x1597 bytes) holding
  hand-written assembly (span/texture inner loops at 0x0064E000, 0x0064E200,
  0x0064E240, 0x0064E270, 0x0064E380 — ~40 `call` sites from 0x00450100–0x00452E00).
  Its section characteristics are **`0xC0000040` = READ | WRITE, *without*
  `IMAGE_SCN_MEM_EXECUTE`**, and `DllCharacteristics = 0x0000` (no
  `NX_COMPAT`). Under the default 32-bit DEP OptIn policy this still runs, but
  with system DEP set to "on for all programs" the first call into 0x0064E000
  faults. **[CERTAIN]** for the flags; **[INFERRED]** for the runtime outcome.
  *Fix:* set the EXEC bit on that section header (or `VirtualProtect` it from a
  shim's `DllMain`), and/or add a DEP exclusion.
  Refinement: an exhaustive operand sweep of `.text` **and** `code` for any
  instruction referencing `[0x0064E000, 0x0064F597)` — as an absolute memory
  displacement *or* as an immediate — found **only `call` targets, no writes and
  no `mov reg, 0x0064Exxx`**. So (contrary to the working assumption in
  `00_overview.md`) this segment does not appear to be self-modified from
  compiled code; it is simply hand-written assembly that the linker emitted into
  a segment declared as *initialized data*, which is why it lacks the EXEC bit.
  `00_overview.md` currently describes it as "writable + executable" — the actual
  section characteristics are `0xC0000040`, i.e. **READ|WRITE, not EXECUTE**.
* `MessageBoxA`/`DialogBoxParamA` are used for the setup/error dialogs; no
  registry APIs at all, so all configuration lives in the game's own files.

---

# 5. Timing and frame pacing

## 5.1 Clock initialisation (inside WinMain @0x004120A0)

```
0x00412140  push 0x4c5348
0x00412145  call KERNEL32!QueryPerformanceFrequency   ; LARGE_INTEGER at 0x004C5348/0x4C534C
0x0041214B  cmp  eax,1 / sbb eax,eax / inc eax        ; eax = (ok ? 1 : 0)
0x00412151  mov  [0x00493720], eax                    ; g_hasQPC
0x00412156  cmp  eax,1 / jne 0x412168
0x0041215B  push 0x4c5368
0x00412160  call KERNEL32!QueryPerformanceCounter     ; epoch at 0x004C5368/0x4C536C
0x00412166  jmp  0x412170
0x00412168  push 1
0x0041216A  call WINMM!timeBeginPeriod                 ; ONLY on the no-QPC fallback
```
**[CERTAIN]**. Note `timeBeginPeriod(1)` is called **only when QPC is unavailable** —
i.e. never, on any modern machine. `timeEndPeriod(1)` @0x004122B7 is guarded
symmetrically (`mov eax,[0x493720]; test eax,eax; jne skip`).

| Global | Meaning |
|---|---|
| `0x00493720` | `g_hasQPC` (1/0) |
| `0x004C5348` (64-bit) | QPC frequency |
| `0x004C5368` (64-bit) | QPC epoch captured at startup |
| `0x004C5378` (64-bit) | QPC scratch for the current read |
| `0x004937B8` | previous raw ms sample |
| `0x004937AC` | **accumulated game-time ms** (the value actually returned) |
| `0x004C5384` | `g_appActive` (set from `WM_ACTIVATEAPP`) |
| `0x0049373C` | the current frame's timestamp in ms |

## 5.2 `GetTimeMs()` @ 0x00412460 — **[CERTAIN]**

```c
DWORD GetTimeMs(void)
{
    DWORD now;
    if (g_hasQPC == 1) {
        QueryPerformanceCounter(&qpcNow);                   // 0x00412471
        int64 d = qpcNow - qpcEpoch;                        // 64-bit sub/sbb
        int64 p = __allmul(d, 1000);                        // call 0x00469730
        now = (DWORD)__alldiv(p, qpcFreq);                  // call 0x00469680
    } else {
        now = timeGetTime();                                // 0x004124B7
    }
    DWORD prev = g_prevRaw ? g_prevRaw : now;               // first call: prev = now
    if (g_appActive)                                        // 0x004124D5
        g_gameMs += (now - prev);                           // 0x004124E1..E5
    g_prevRaw = now;                                        // 0x004124EC
    return g_gameMs;                                        // 0x004937AC
}
```

Notable, and *good* news for a modern port:
* Exact 64-bit `__allmul`/`__alldiv` — **no divide by a measured/calibrated
  value, no truncation to 16 bits, no floating-point time conversion**.
  Overflow of `delta*1000` needs ≈29 years at a 10 MHz QPC.
* The clock is **pause-aware**: it only accumulates while `WM_ACTIVATEAPP` says
  the app is active, so alt-tab does not warp the simulation.
* The returned value is a 32-bit ms counter that wraps after ≈49.7 days.
* **No `rdtsc`, no CPU-speed calibration loop anywhere.** (KERNEL32 imports have
  no `Sleep`; there is no busy-wait calibration.)

## 5.3 The main loop @ 0x0041218A–0x00412215 — **[CERTAIN]**

```c
ESI=PeekMessageA  EDI=GetMessageA  EBP=TranslateMessage  EBX=DispatchMessageA
for (;;) {                                             // 0x004121A2
    if (g_appActive /*0x4C5384*/) {
        if (PeekMessageA(&msg, NULL, 0, 0, PM_NOREMOVE /*0*/)) {   // 0x004121B8
            if (!GetMessageA(&msg, NULL, 0, 0)) return msg.wParam; // WM_QUIT -> 0x0041220A
            TranslateMessage(&msg); DispatchMessageA(&msg);
        } else {
            if (!FrameTick())      /* 0x00412230 */                // 0x004121DF
                Shutdown();        /* 0x00412530 */
        }
    } else {                                            // 0x004121EF  (INACTIVE)
        if (!GetMessageA(&msg, NULL, 0, 0)) return msg.wParam;     // BLOCKS here
        TranslateMessage(&msg); DispatchMessageA(&msg);
    }
}
```

## 5.3b Frame pacing — a **36 FPS gate**, implemented by spinning — **[CERTAIN]**

The main loop itself has no limiter, but the per-frame function gates its own
body. `GetFrameDeltaTicks()` @ **0x00420C00**:

```
0x00420C00  fild qword? dword [0x0049373C]   ; g_nowMs
0x00420C09  fmul qword [0x004799B8]          ; * 0.036   -> nowTicks   (36 ticks/s)
0x00420C16  fst  qword [esp+0xc]             ; keep nowTicks
0x00420C1A  fsub qword [0x00603AD0]          ; dt = nowTicks - lastCommittedTicks
0x00420C20  fcom qword [0x004799C0]          ; vs 1.0
0x00420C2F  jne  0x420cfe                    ; dt < 1.0 -> RETURN WITHOUT COMMITTING
0x00420C35  fld  qword [0x004799C8]          ; 36.0
0x00420C3B  fdiv qword [esp+4]               ; FPS = 36 / dt   (dt >= 1.0, so no /0)
0x00420C4C  mov  [0x00603AD0], nowTicks      ; commit
```
and its caller inside the per-frame step:
```
0x0041740E  call 0x00420C00
0x00417413  fstp qword [esp+0x14]            ; dt
0x00417417  call 0x00455EF0                  ; PollInput  <-- BEFORE the gate
0x00417433  fld1
0x00417435  fcomp qword [esp+0x14]           ; 1.0 vs dt
0x0041743E  je   0x4179d5                    ; dt < 1.0 tick -> SKIP the whole frame body
```
So the render + simulation block runs at most **36 times per second** (1 tick =
1/0.036 = 27.778 ms). The cap is real, but it is enforced by **returning early
and looping again immediately** — the process never yields, so the CPU spins at
~10^5 iterations/s to wait out 27.8 ms.

Note the gate sits in the in-race branch (`0x004173FB cmp [0x00525E5C],1`);
menu/front-end animation is time-based on its own accumulators
(0x00403DF0 at 36 Hz, 0x004115B0 at 40 Hz).

**Presentation:** `IDirectDrawSurface::Flip` **is** used, at **0x0045C4FC**,
with `dwFlags = 0` (no `DDFLIP_WAIT`) and an **unbounded busy retry**:
```
0x0045C4ED  push 0                     ; dwFlags = 0
0x0045C4FC  call [eax+0x2c]            ; IDirectDrawSurface::Flip
0x0045C4FF  cmp  eax, 0x8876021c       ; DDERR_WASSTILLDRAWING
0x0045C504  je   0x45c4ed              ; spin
```
`IDirectDraw::WaitForVerticalBlank` (slot 22 / +0x58) is **never called anywhere**
— no dispatch site with displacement +0x58 exists in the binary. **[CERTAIN]**

`FrameTick` @0x00412230 is a 3-state dispatcher on `[0x00493734]`; in every
state it first refreshes `[0x0049373C] = GetTimeMs()`, then in the running state
(`1`) calls the per-frame step **0x004172B0** (which polls input at 0x00417417
and pumps audio at 0x0041776C).

## 5.4 Simulation timestep — fixed 36 Hz accumulator @0x00417946 — **[CERTAIN]**

```
0x00417946  eax = [0x0049373C] - [0x00525E48]      ; ms since session base
0x00417957  fild  dword [esp+0x10]                 ; (double)dtMs
0x0041795B  fmul  qword [0x004797E8]               ; * 0.036          <-- 36 ticks / 1000 ms
0x00417961  fsub  qword [0x00563D48]               ; - already-consumed total
0x00417967  fadd  qword [0x00563BD0]               ; + accumulator
0x0041796D  fcom  qword [0x004797C8]               ; vs 1.0
0x00417973  fstp  qword [0x00563BD0]
0x0041797E  jne   0x4179a0                         ; accumulator < 1.0 -> no tick this frame
0x00417980: do { PhysicsTick();                    ; call 0x004357A0
0x00417985     accumulator -= 1.0;                 ; fld1 / fsubr / fstp
0x0041799E   } while (accumulator >= 1.0);
0x004179A0  [0x00563D48] = dtMs * 0.036            ; remember consumed total (no drift)
```
Constants read from `.rdata`: `[0x004797C8] = 1.0`, `[0x004797E8] = 0.036`.

So the **simulation runs at a fixed 36 Hz, fully decoupled from rendering**, with
a catch-up `while` loop and a drift-free "consumed total" bookkeeping. Gameplay
speed is therefore **already correct on a fast CPU** — this game does not suffer
the classic "runs too fast" problem.

## 5.4b Second accumulator, dt clamp and catch-up cap — **[CERTAIN]**

There are **two** whole-tick accumulators in the per-frame step:

* **A @ 0x004177F8** — fed by the *clamped* dt; drains by calling the vehicle
  integrator `0x00435350` (whose maths uses only fixed constants at 0x0047A4D8 /
  0x0047A4E0 — **dt never enters the integration**).
* **B @ 0x00417946** — fed by *absolute* elapsed time
  (`g_nowMs − [0x00525E48]`) minus the consumed total `[0x00563D48]`, so it is
  self-correcting and drift-free; drains by calling `0x004357A0`.

Guards that already exist in the original code:

| Guard | VA | Value |
|---|---|---|
| dt floor when `dt == 0` | 0x00420CC2–0x00420CD4 | 0.01 ticks |
| dt clamp | 0x00420CE1–0x00420CF6 | **10.8 ticks = 300 ms** (`[0x004799F0]`) |
| catch-up cap / hard resync | 0x00420D10 (called at 0x00417475) | **200 ticks = 5.556 s**; on overrun `0x0041F8F0` sets `[0x00525E48] = g_nowMs` |
| average-FPS `idiv` zero guard | 0x00420BBA | frameCount == 0 → 1 |

So the "spiral of death" I flagged earlier is **already mitigated** by the
300 ms dt clamp and the 200-tick resync. Accumulator A is bounded to ≤ 10
iterations/frame; accumulator B is bounded by the resync.

Relevant constants in `.rdata`: `0.036` (36 Hz, four copies: 0x00479110,
0x004797E8, 0x004799B8, 0x004799A0-region), `0.04` (40 Hz front-end, 0x004793A8),
`1.0` (0x004797C8, 0x004799C0), `36.0` (0x004799C8), `27.7778` (0x004799D0),
`0.001` (0x004799D8), `0.01` (0x004799E8), `10.8` (0x004799F0),
`200.0` / `5555.5556` (0x00479A08 / 0x00479A10).

## 5.5 What actually breaks on modern hardware, and the minimal fixes

The timing model is unusually robust for 1997. Game **speed is correct** on fast
hardware — fixed 36 Hz simulation, dt never enters the physics, dt clamped,
catch-up capped, no calibration, no `rdtsc`, no 16-bit tick counter, no
divide-by-zero. The real problems are all about *yielding*.

| # | Problem | Evidence | Smallest fix |
|---|---|---|---|
| 1 | **100 % CPU burn.** The main loop spins ~10^5 iterations/s doing a QPC, a graphics call, an input poll and an audio pump, just to wait out 27.8 ms until the 36 FPS gate opens. `Sleep` is not imported and the string `"Sleep"` does not occur in the binary; there are no synchronisation objects at all. | 0x004121A2–0x004121ED, gate at 0x0041743E | Hook the **`PeekMessageA` IAT slot `0x0064C44C`** (loaded into ESI at 0x0041218A) with a stub that does `Sleep(1)` and tail-calls the real one. One IAT slot, no code cave. Also call `timeBeginPeriod(1)` from the shim (the game skips it whenever QPC exists — 0x00412156). |
| 2 | **Unbounded `DDERR_WASSTILLDRAWING` spin on `Flip`.** | 0x0045C4ED–0x0045C504 | One byte: file offset **0x5B8EE**, `0x00` → `0x01`, turning `push 0` into `push 1` = `DDFLIP_WAIT`, so the driver blocks instead of the game spinning. (Verified: `6A 00` lives at file 0x5B8ED for VA 0x0045C4ED.) |
| 3 | **Input polling and audio pumping happen before the frame gate**, so they run at spin rate (~10^5/s) rather than 36/s: `0x00417417 call 0x00455EF0` (PollInput) sits above the `0x0041743E` gate, and `SND_Service` is reached at 0x0041776C. That means ~10^5 `IDirectInputDevice::GetDeviceData` and `IDirectSoundBuffer::GetCurrentPosition` calls per second. | 0x00417417, 0x0041776C | Fixed for free by (1). Shims should make `GetCurrentPosition` / `GetDeviceData` cheap and lock-free. |
| 4 | **Audio has no independent clock.** The `timeSetEvent` timer is dead code (§2), so a main-loop stall stalls audio, and the write-ahead is only ~120 ms (§1.7). | §1.10, §2 | Raise `[0x0063C638]` (the `latency×10 ms` config, currently 10) or buffer inside the DirectSound shim. |
| 5 | **`timeBeginPeriod(1)` is skipped whenever QPC exists** (0x00412156 → 0x00412168 not taken), so any `Sleep`-based pacer you add gets the default ~15.6 ms tick. | 0x00412168 | `timeBeginPeriod(1)` in the shim's `DllMain`. |
| 6 | 32-bit ms wrap: `__alldiv`'s result is truncated to EAX at 0x004124B5 and `[0x004937AC]` is never reset → wraps at ≈49.7 days of process uptime. | 0x004124B5 | Ignore, or keep EDX. |
| 7 | `QueryPerformanceFrequency`'s **BOOL** is checked (0x0041214B) but the returned frequency value is not; a zero frequency would fault in `__alldiv`. Cannot happen on real Windows. | 0x0041214B | Optional: also test `[0x004C5348] \| [0x004C534C]`. |
| 8 | `[0x0049373C]` fed to `cdq`/`idiv` at 0x00410C67 (a widget blink period) could hit `INT_MIN / -1` after ≈24.9 days. Theoretical. | 0x00410C67 | Mask with `0x7FFFFFFF`. |

**Explicitly ruled out** (checked, and safe) **[CERTAIN]**: no `rdtsc`/`cpuid` in
code — the six `0F 31` byte pairs in the file are all inside `.reloc`
(file ≥ 0xC4800), verified; no CPU-speed calibration loop; no division by a
measured value other than the QPC frequency; no 16- or 8-bit truncation of any
time value or delta; no divide-by-zero on `dt` (`fcom 1.0 / jne` at 0x00420C2F
guarantees `dt >= 1.0` before the `fdiv` at 0x00420C3B); no time-based spin loop
(no backward branch encloses any of the three `GetTimeMs` call sites — 0x0041224D,
0x0041227A, 0x00412298, all inside `FrameTick`); and no frame-rate-dependent
physics.

---

# 6. Joystick — legacy WINMM multimedia API (no DirectInput joystick)

Two independent passes (mine and a dedicated agent's) agree on everything below.

## 6.1 Data model — **[CERTAIN]**

```c
struct JoyDev {                       // 0x44 bytes; array of 2 at 0x004BA9F0
    int   present;    // +0x00  0x4BA9F0 / 0x4BAA34 — joyGetDevCapsA succeeded
    int   enabled;    // +0x04  0x4BA9F4 / 0x4BAA38 — derived from the control-method option
    int   mode;       // +0x08  0x4BA9F8 / 0x4BAA3C — set to 1 in init, never written again
    int   nButtons;   // +0x0C  0x4BA9FC / 0x4BAA40 — init 4, never updated from JOYCAPS
    short x,y,z,r,u,v;// +0x10..+0x1A — scaled to -100..+100
    short pov;        // +0x1C  raw JOYINFOEX.dwPOV
    short caps;       // +0x1E  JOYCAPS.wCaps
    char  button[32]; // +0x20..+0x3F
    short maxButtons; // +0x40  JOYCAPS.wMaxButtons  (written, NEVER read)
};
JOYCAPSA  g_caps  @ 0x0050F5A8            // 404 bytes, ONE shared scratch for both probes
JOYINFOEX g_ji[2] @ 0x0050F740 / 0x0050F774   // stride 0x34
double    g_scale[6][2]: R@0x50F548 X@0x50F558 Y@0x50F568 Z@0x50F578 U@0x50F588 V@0x50F598
DWORD     g_btnMask[32] @ 0x004BAA78 = 1<<0 .. 1<<31
```

## 6.2 `joyGetDevCapsA` — init, run **once** at startup — **[CERTAIN]**

```
0x0045972A  push 0x194            ; cbjc = 404 = sizeof(JOYCAPSA)
0x00459731  push 0x50f5a8         ; pjc
0x00459736  mov  esi,[0x64c464]   ; WINMM!joyGetDevCapsA  (indirect via register —
0x0045973D  call esi              ;  this is why a naive IAT scan missed it)
...
0x00459897  push 0x194 / push 0x50f5a8 / push 1 / call esi   ; uJoyID = 1
```
* Exactly **two device IDs, 0 and 1**. No `joyGetNumDevs`, no 0..15 loop, no re-enumeration.
* Both probes reuse the **same** 404-byte buffer; probe 0's data is consumed before probe 1 runs.
* Fields **read**: `wXmin/wXmax` (0x50F5CC/D0), `wYmin/wYmax` (D4/D8), `wZmin/wZmax` (DC/E0),
  `wRmin/wRmax` (0x50F5F0/F4), `wUmin/wUmax` (F8/FC), `wVmin/wVmax` (0x50F600/604),
  `wCaps` (0x50F608), `wMaxButtons` (0x50F614 — cached but never read back).
* Fields **never read**: `wMid`, `wPid`, `szPname`, **`wNumButtons`**, **`wPeriodMin`**,
  **`wPeriodMax`**, **`wMaxAxes`**, **`wNumAxes`**, `szRegKey`, `szOEMVxD`.
  (So the classic `wPeriodMin` hazard does not apply here.)
* Ranges are collapsed immediately into one scale factor per axis:
```
0x004597A6  fsubp st(1)                  ; wXmax - wXmin
0x004597A8  fdivr qword ptr [0x47aee0]   ; [0x0047AEE0] = 200.0
0x004597AE  fstp  qword ptr [0x50f558]   ; scaleX = 200.0 / (wXmax - wXmin)
```
Init is reached once via `FrameTick` state 0 → 0x00417270 → 0x00417EA0 → 0x004180FA.
**No hot-plug support.**

## 6.3 `joyGetPosEx` — poll — **[CERTAIN]**

```
0x00459A29  mov  edi, 0x50f744           ; &g_ji[0].dwFlags   (base = 0x0050F740)
0x00459A4A  cmp  dword [esi+0x4ba9f8],1  ; JoyDev.mode
0x00459A51  mov  dword [edi], 0x8ff      ; dwFlags
0x00459A57  je   0x459a5f
0x00459A59  mov  dword [edi], 0          ; DEAD — mode is always 1
0x00459A75  lea  ecx,[edi-4] / push ecx / push eax  ; eax = uJoyID (0 or 1)
0x00459A7A  call dword ptr [0x64c460]    ; WINMM!joyGetPosEx
```
* **`dwFlags = 0x8FF = JOY_RETURNALL (0xFF) | JOY_USEDEADZONE (0x800)`.**
  `JOY_RETURNCENTERED (0x400)`, `JOY_RETURNRAWDATA (0x100)` and the `JOY_CAL_*` bits are
  **not** set. The `dwFlags = 0` branch is unreachable.
* `dwSize = 0x34 (52)` is written **once** at init (0x004596AB), never refreshed per poll.
* The whole thing is a loop over 2 devices: `edi += 0x34`, `esi += 0x44`, `ebx += 8`,
  `cmp ebx,0x10 / jl` (0x00459CD4–0x00459CE0).

### Axis conversion — **[CERTAIN]**

| JOYINFOEX field | off | gate (`JoyDev.caps`) | scale | destination |
|---|---|---|---|---|
| `dwXpos` | +0x08 | always | 0x50F558 | `.x` 0x4BAA00 |
| `dwYpos` | +0x0C | always | 0x50F568 | `.y` 0x4BAA02 |
| `dwZpos` | +0x10 | `&0x01` JOYCAPS_HASZ | 0x50F578 | `.z` 0x4BAA04 |
| `dwRpos` | +0x14 | `&0x02` HASR | 0x50F548 | `.r` 0x4BAA06 |
| `dwUpos` | +0x18 | `&0x04` HASU | 0x50F588 | `.u` 0x4BAA08 |
| `dwVpos` | +0x1C | `&0x08` HASV | 0x50F598 | `.v` 0x4BAA0A |
| `dwPOV`  | +0x28 | `&0x10` HASPOV | — | `.pov` 0x4BAA0C, **raw** |
| `dwButtons` | +0x20 | — | bit masks | `.button[]` |

```
0x00459AB0  fild  qword ptr [esp+0x10]      ; (double)dwXpos
0x00459AB4  fmul  qword ptr [ebx+0x50f558]  ; * 200.0/(max-min)
0x00459ABA  fsub  qword ptr [0x47aee8]      ; - 100.0        [0x0047AEE8] = 100.0
0x00459AC0  call  0x0046950C                ; __ftol (truncate toward zero)
0x00459AC5  mov   word ptr [esi+0x4baa00], ax
0x00459ACC  cmp ax,0xff9c ... 0x00459ADD cmp ax,0x64   ; clamp to [-100,+100]
```
**`axis = clamp( trunc( raw * 200.0/(max-min) - 100.0 ), -100, +100 )`**

Note `min` is **never subtracted** — the formula is only correct when `wXmin == 0`.

### Return handling — **[CERTAIN]**

| HRESULT | meaning | game response |
|---|---|---|
| `0` | JOYERR_NOERROR | process the data |
| `0xA7` (167) | JOYERR_UNPLUGGED | abort whole poll, return **5000** (0x1388) |
| `0x0B` (11) | MMSYSERR_INVALPARAM | abort, return **5010** (0x1392) |
| `0x06` | MMSYSERR_NODRIVER | abort, return **5020** (0x139C) |
| anything else | — | **falls through and reprocesses the stale buffer** (0x00459AA1) |

Both callers ignore the return value entirely.

## 6.4 Buttons — 32-bit mask table, but a loop bug caps it at 5 — **[CERTAIN]**

```
0x00459CB6  mov eax, dword ptr [edi+0x1c]  ; JOYINFOEX.dwButtons
0x00459CB9  and eax, dword ptr [ecx]       ; g_btnMask[b] = 1<<b   (0x004BAA78..0x004BAAF8)
0x00459CBB  cmp eax,1 / sbb al,al / inc al ; al = (bit != 0)
0x00459CC0  add edx, 0x44                  ; BUG: bound pointer walks by sizeof(JoyDev)
0x00459CC8  mov byte ptr [ebp+esi+0x4baa10], al
0x00459CD0  cmp dword ptr [edx], ebp / jg  ; dereferences a wandering address
```
The termination test should read `JoyDev.nButtons` but instead reads
`0x004BA9FC + (b+1)*0x44`. Against the shipped `.data` those slots hold
`4, 8, 0x100000, "1.00", "995-", 0` → the loop runs exactly **5 iterations**;
`button[5..31]` are never updated. Memory-safe (the `jae 0x4BAAF8` guard caps the
mask index at 32, and the byte writes stay inside the 32-byte array), but
**buttons 6+ on a modern pad are unreachable**. Only `button[0]` and `button[1]`
are consumed anywhere.

## 6.5 How the joystick reaches gameplay — **[CERTAIN unless noted]**

**Menus** (`0x00402C00`, called from 0x0041730B; polls both devices) — the joystick is
purely a **synthetic keyboard**: it writes `1`s into the same 256-byte DIK-indexed
edge array the DirectInput reader uses (`0x0050E068`, pointer mirrored at 0x004C50E0):

| input | injected DIK |
|---|---|
| button 0 | `0x1C` DIK_RETURN (0x00402CED) |
| button 1 | `0x01` DIK_ESCAPE (0x00402D14) |
| x < −25 / > +25 | `0xCB` DIK_LEFT / `0xCD` DIK_RIGHT (0x00402D49 / 57) |
| y < −25 / > +25 | `0xC8` DIK_UP / `0xD0` DIK_DOWN (0x00402D90 / 9E) |

Edge latches per device at 0x004BE830 / 0x004BE838 (buttons) and
0x004BE5E0 / 0x004BE5E8 (axes). The DI reader only *sets* bytes in that array, so it
never clobbers injected joystick presses.

**In race** (`0x00441390`, called from 0x0041789D / 0x00417A02) the joystick bypasses
the key array and feeds the control block directly:
```
0x00441417  fmul [0x47a9c0]   ; 0.01
0x0044141D  fmul [0x47a9c8]   ; pi/2
0x00441423  fcos
0x00441425  fmul [0x47a9d0]   ; -100.0
0x0044142B  fadd [0x47a9d8]   ; +100.0
```
**steer = sign(x) · 100 · (1 − cos(|x| · π/200))** — a soft-centre cosine curve, ±100 at
full deflection but only ±7.6 at 25 % deflection. No additional dead zone.

| control method (`settings[p]+0x08`) | throttle | brake | buttons |
|---|---|---|---|
| 0 KEYBOARD | — | — | — |
| 1 JOYSTICK | `−y` when `y ≤ 0` | `+y` when `y > 0` | btn0 → action A, btn1 → action B |
| 2 PEDALS | `−y` clamped ≥ 0 | `+y` clamped ≥ 0 | same |
| 3 JOYPAD | `button[0] × 100` | `button[1] × 100` | `y < −50` → A, `y > +50` → B |

Dispatch at 0x00441ACE → `ApplyControls(throttle, brake, steer, gear)` @0x00442030 →
`ctrl[11][4]` @0x005531D0. "Auto accelerate" (`settings[p]+0x18 == 1`) forces
`throttle = 100` when brake is 0 (0x00441A8A). Action A = car `+0x52C`
(probably reverse-gear latch, **[INFERRED]**), action B = car `+0x5A4`
(read only by the camera routine 0x00436DA0 → probably look-back, **[INFERRED]**).

## 6.6 Calibration and configuration — **[CERTAIN]**

* **The game performs no calibration of its own.** Every write to the 12 scale doubles
  and every `JOYCAPS` read lives inside the init function; nothing outside
  0x00402C00 / 0x00441390 even reads the axis words.
* The "calibration" screens are **instruction text only** — the strings at
  0x0047D8B0..0x0047D8E0 render as *"PLEASE CALIBRATE / THE JOYSTICK IN / THE
  WINDOWS 95 / CONTROL PANEL."* An abandoned in-game wizard's strings
  (*"PUSH THE JOYSTICK TO ALL ITS MAXIMUM POSITIONS…"*, *"TURN THE WHEEL…"*) exist
  but have no code reference. There is no `WinExec`/`CreateProcess`/`ShellExecute`
  import, so the game cannot even launch the control panel.
* **Enable flags** (`JoyDev.enabled`, 0x004BA9F4 / 0x004BAA38): init leaves device 0
  enabled and device 1 disabled; recomputed in the menu (0x0040A10F, `enabled =
  ctlMethod > 0` per player) and at race start (0x00420061: both devices only if
  **both** players use method 1 or 3).
* Control-method enum: `0 = KEYBOARD, 1 = JOYSTICK, 2 = PEDALS, 3 = JOYPAD`
  (strings at 0x0048030E/33E/36E/39E; menu vars 0x004BF6A3 / 0x004BF6A7).
* **Persistence is a flat file, not the registry** — no `ADVAPI32` import at all and
  no `*PrivateProfile*`. A 0x597-byte block (0x004BF580 menu mirror / 0x006393A0
  game mirror) is `fwrite`'d verbatim to **`ign_win.btz`** (save 0x00457590 `"wb"`,
  load 0x00457420). Control method at block offset **0x123** (P1) / **0x127** (P2),
  AUTO-ACC at 0x12B/0x12F, language at 0x593.

## 6.7 No windowed joystick messages — **[CERTAIN]**

The complete `WINMM.dll` import list is `joyGetPosEx`, `joyGetDevCapsA`,
`mciSendStringA`, `timeKillEvent`, `timeBeginPeriod`, `timeSetEvent`,
`timeEndPeriod`, `timeGetTime`. There is **no** `joySetCapture`,
`joyReleaseCapture`, `joySetThreshold`, `joyGetPos` or `joyGetNumDevs`, and the
WndProc (0x004122D0) handles no `MM_JOY*` message. Polling only.

## 6.8 Joystick hazards on modern Windows

| # | Hazard | Evidence | Note / fix |
|---|---|---|---|
| 1 | `wXmax == wXmin` → `fdivr 200.0` yields `+INF`; `__ftol(INF)` returns 0 low-word, so the axis is silently **stuck at centre** rather than crashing (x87 exceptions are masked by the MSVC default control word). | 0x004597A8 | Shim/wrapper should never report a degenerate range. **[INFERRED]** for the masked-FPU outcome. |
| 2 | **`min` is never subtracted.** Any driver reporting non-zero `wXmin` gives a biased axis that the ±100 clamp turns into permanent full-lock. Modern winmm reports 0..65535, so it happens to work. | 0x00459AB4–0x00459ABA | Report `min == 0`. |
| 3 | `JOY_USEDEADZONE (0x800)` defers to the **legacy** calibration/dead-zone record under `HKCU\System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick`. The game provides none and only tells the user to open the Win95 control panel. | 0x00459A51 | Either write a sane legacy calibration record, or intercept `joyGetPosEx` and clear `JOY_USEDEADZONE`. |
| 4 | **IDs 0 and 1 only, probed once at startup.** A pad plugged in later, or one that lands on ID 2+, is invisible for the session. | 0x0045973C / 0x00459897 | A `winmm` shim can remap the user's pad onto ID 0. |
| 5 | Button loop bug caps sampling at **5 buttons**; `wNumButtons` / `wNumAxes` are never read at all. | 0x00459CC0 / 0x00459CD0 | Only buttons 0 and 1 are used anyway; map the pad's primary buttons there. |
| 6 | Unrecognised `joyGetPosEx` errors are ignored and **stale data is reprocessed**; recognised errors abort the poll for *both* devices. | 0x00459AA1, 0x00459A82–9B | Return only 0 / 0xA7 / 0x0B / 0x06 from a shim. |
| 7 | `dwSize` is set once at init — corrupting 0x0050F740 / 0x0050F774 makes every later call fail permanently. | 0x004596AB | Informational. |
| 8 | **Menu joystick polling is ungated by the 36 FPS limiter** (the gate lives in the in-race branch, entered at `0x004173FB cmp [0x00525E5C],1`), so `joyGetPosEx` runs at main-loop spin rate in menus and the ±25 edge-latch cursor repeat becomes frame-rate dependent. | 0x0041730B, 0x004173FB | Fixed by the `Sleep(1)` pacer in §5.5 #1. |
