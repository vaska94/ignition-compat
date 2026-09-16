# 08 — Ghost replay (`.GST`) and settings (`ign_win.btz`) file formats

Scope: the two files that carry **user-visible state**, specified tightly enough that a rebuilt
engine stays byte-compatible with files the 1997 binary wrote.

Codecs: `_re/tools/formats/gst.py`, `_re/tools/formats/btz.py`, plot/acceptance test
`_re/tools/formats/gst_plot.py`.

Every claim is tagged **[CERTAIN]** (read directly off instructions or file bytes) or
**[INFERRED]**. VAs are `Ign_win.exe` virtual addresses (ImageBase `0x00400000`, no ASLR).

---

## 0. Headline results

| | result |
|---|---|
| `ign_win.btz` round-trip | **PASS, byte-exact**, on **four** independent real snapshots of the user's live file (1431 bytes each) |
| `.GST` round-trip | **PASS, byte-exact, against a REAL ghost file** — `GHOSTS\MOOSEJAW.GST`, 302,412 bytes, written by the game during this session. See §3.5 |
| `.GST` acceptance test | **PASS** — the decoded path traces the Canada road for 3 laps and closes (`_re/out/tracks/MOOSEJAW_ghost.png`), 10629/10629 samples inside the track bounds |
| `21,600 × 14 bytes` | **CONFIRMED** |
| `36 Hz` sampling (notes/06) | **WRONG — it is 72 Hz.** Now measured empirically at **71.993 Hz** from a real ghost (§3.5). A ghost covers 300 s, not 600 s |
| `10-byte header` (notes/06) | **CONFIRMED** |
| `.GST` coordinates | **raw mesh space**, not engine space — the recorder removes the engine's +25600 bias |

---

## 1. Ghost replay — `GHOSTS\<BASENAME>.GST`

### 1.1 Functions and globals

| role | VA | notes |
|---|---|---|
| writer | `0x004222B0` | `save_file` at `0x004224EF`; also awards championship points |
| reader | `0x00420240` | `load_file` at `0x004202A1`; builds the ghost car object |
| recorder | `0x00445C10` | one sample per sim step, called from `0x004235D8` |
| playback | `0x00445940` | called from `0x004177EE` |
| reset | `0x00404790` | menu "RESET GHOST CAR" — deletes all 7 files via `0x00457660` |
| angle wrap | `0x00446330` | normalises a double to `[0, 2π)` |

| global | meaning |
|---|---|
| `[0x00563D7C]` | sample buffer, allocated `0x49D40` = 21600×14 at `0x0041FFB4` **[CERTAIN]** |
| `[0x00552F54]` | current sample index; reset to 0 at `0x0041FFC5` |
| `[0x005530B8]` | "ghost invalid" flag — set on buffer wrap **and** on track mismatch |
| `[0x00525E44]` | best **total race** time, integer centiseconds (scaled by `0.01` at `0x004222DF`); `0x2DC6C0` = "no time". Full identity not traced — see §4 |
| `[0x005532A0]` | 8-char track basename used to build the filename |
| `[0x00552FC4]` | current track id |
| `[0x00552FFC]` | car descriptor: `[+0]` = car model index, `[+0x3C]` = driver name |

### 1.2 File naming and track binding — **[CERTAIN]**

The name is `sprintf("GHOSTS\\%s.GST", [0x005532A0])` (format string `0x00499164`), used by both
the reader (`0x00420282`) and the writer (`0x004224D1`). `0x004198D0` sets `[0x005532A0]` from the
track id:

| track id | `LEVELS\` dir | ghost basename |
|---|---|---|
| 0 | `CANADA` | `MOOSEJAW` |
| 1 | `USA` | `GOLDRUSH` |
| 2 | `CARIB` | `SNAKEISL` |
| 3 | `BRAZIL` | `LOSTRUIN` |
| 4 | `AUSTRIA` | `YODELPEA` |
| 5 | `ICELAND` | `CAPETHOR` |
| 6 | `JAPAN` | `TOKYOBUL` |

The literal filename table at `0x0047EA50` (stride `0x14`) used by the reset routine is in a
*different* order — `LOSTRUIN, TOKYOBUL, YODELPEA, SNAKEISL, GOLDRUSH, MOOSEJAW, CAPETHOR` — it is
just a delete list, **not** indexed by track id. Don't use it as a mapping.

### 1.3 Layout — total `0x49D4C` = 302,412 bytes **[CERTAIN]**

```
+0x00000  int16   car model index            -> [0x00563D30]; indexes mesh table [eax*4+0x005DB000]
+0x00002  int16   track id (0..6)            compared against [0x00552FC4]; mismatch => rejected
+0x00004  int16   TOTAL RACE TIME, CENTISECONDS (whole 3-lap run, NOT one lap)
                  SIGNED (reader uses movsx at 0x004202ED)
+0x00006  char[4] driver name, NUL-terminated
+0x0000A  21600 records x 14 bytes  (7 x int16 LE)
+0x49D4A  2 bytes, always 00 00
```

Header writes: `0x0042240B` (model), `0x00422415` (track), `0x00422430` (time),
`0x0042244B` (`sprintf(buf+6, "%s", [0x00552FFC]+0x3C)`).

**The header time is the whole 3-lap run, not a lap.** `car0[+0x3A4]` is the total elapsed race
time: `0x00441129`/`0x0044112F` copy both halves of the race-clock double `[0x00553090]` into it when
the car finishes. **A race is always 3 laps** — the limit is hard-coded 3 at `0x004410D5`,
`0x00424A69` and `0x00424C87`, and there is no lap-count field anywhere in the data. Time trial is no
exception; the mode special-cased in the lap/record logic is **mode 3 (PURSUE)**, not the ghost's
mode 2. **[CERTAIN]** An earlier draft of this note called this field a lap time and explained it by
assuming time trial was a single lap; both were **wrong**.

This makes the buffer a real constraint: **21600 samples ÷ 72 Hz = 300 s must cover the entire
3-lap race**, and the shipped default times already reach **263 s** on Tokyo Bullet. A slower run
overflows, sets `[0x005530B8]`, and **no ghost is saved at all**. (That tight ~14 % margin is also
independent corroboration of the 72 Hz reading — at 36 Hz the budget would be a far less pointed
600 s.)

The name field is effectively **3 characters + NUL**: the `sprintf` runs *before* the record copy
loop, which starts writing at `+0x0A`, so bytes `+0x0A` onward are overwritten by record 0. A
longer name is silently truncated on disk. **[CERTAIN]**

The two trailing bytes are **deterministically zero**, not heap garbage: the block comes from the
allocator at `0x0045AFB0`, which zero-fills with `rep stosd` / `rep stosb` (eax = 0) before use.
The record copy loop stops at `+0x49D4A`. **[CERTAIN]**

### 1.4 Record fields — **[CERTAIN]** for offsets and scaling

Written by `0x00445C10` from car 0 (`[0x005DAFFC] + 0`, i.e. always the player):

| off | field | recorder | meaning |
|---|---|---|---|
| +0 | `x` | `trunc(car[0x000] - 25600.0)` @`0x00445C3C` | **raw mesh x** |
| +2 | `y` | `trunc(car[0x008] (+5000.0 if car[0x354]≠0))` @`0x00445C7A` | height |
| +4 | `z` | `trunc(car[0x010] - 25600.0)` @`0x00445CA5` | **raw mesh z** |
| +6 | `a0` | `trunc(wrap(car[0x108]) * 1024)` | angle, radians × 1024 |
| +8 | `a1` | `trunc(wrap(car[0x0F8]) * 1024)` | angle, radians × 1024 |
| +10 | `a2` | `trunc(wrap(car[0x110]) * 1024)` | angle, radians × 1024 |
| +12 | `progress` | `lap*( [0x005287F8] + 10 ) + abs(car[0x364])` | lap/section progress |

Angles: `0x00446330` normalises to `[0, 2π)` (constants `0x0047ADA0` = 2π, `0x0047AD98` = 0.0), then
×1024 (`0x0047AD48`). Stored range is therefore **0 … 6433**, never negative. Playback multiplies by
1/1024 (`0x0047AD00`) and then by 57.29578 × 10 to get deci-degrees for the object rotation fields
`obj[+0x10/+0x14/+0x18]`. **[CERTAIN]**

`progress` is read at `0x0044106E` (`movsx edx, word [buf + i*14 + 0xC]`) and compared with the
player's own progress to drive the "ghost ahead / behind" indicator (`car[0x39C]`, `[0x006035E0]`).
**[CERTAIN]**

### 1.5 Coordinate space — the subtlety that will bite a rebuild **[CERTAIN]**

The engine simulates in **biased** coordinates: `x_engine = x_mesh + 25600`, `z_engine = z_mesh + 25600`,
**y unbiased**. This is already documented in `notes/04` ("World extent"), and an exhaustive scan finds
**34 `add reg, 0x6400` sites** across the SRF / collision / track-loading code. Raw mesh `x, z` are
therefore in `[-25600, 25600]`, and real tracks stay within `|x|,|z| < 8702`.

The double constant `25600.0` at `0x0047AD38` has **exactly two references in the whole image**, both
in the ghost recorder (`0x00445C3C`, `0x00445CA5`), and `5000.0` at `0x0047AD40` has exactly one
(`0x00445C7A`).

⇒ A `.GST` stores **raw mesh coordinates** — the same space as `.PLC` / `.MSH` / `.POS` and as
`_re/out/tracks/*_topdown.png`. Playback re-adds `0x6400` (`0x0044597F`, `0x00445998`) to hand
engine-space coordinates to the object system. **Do not add the bias when plotting.** Adding it twice
was my own first error; the int16 range is the tell — `mesh_x - 25600` would overflow int16 on every
track, while raw mesh coordinates fit comfortably.

The `+5000` on `y` when `car[0x354] ≠ 0` is applied at record time and **never undone** on playback,
so it is baked into the file. What `car[0x354]` signifies is **[INFERRED]** — not traced.

### 1.6 Short laps — the padding quirk **[CERTAIN]**

If the lap ends before the buffer fills (`[0x00552F54]` = n < 21600), the writer pads at
`0x004222F6`–`0x004223E9`: records `n … 21599` receive a copy of record `n-1`. **The pad loop copies
only six of the seven words** (offsets +0, +2, +4, +6, +8, +10). Word +12 (`progress`) is *not*
copied and keeps whatever was already in the buffer — 0 on a fresh race, or a stale value from an
earlier, longer lap in the same session.

⇒ The padded tail's `progress` is **not deterministic across sessions**. A rebuild that zero-fills it
will produce different bytes for the same lap; a rebuild that must be byte-identical has to reproduce
this. Everything else about the tail is deterministic.

### 1.7 Sampling rate — **72 Hz**, correcting notes/06 **[CERTAIN]**

Chain of evidence:

1. `GetFrameDeltaTicks` `0x00420C00`: `fild [0x0049373C]` (ms) `fmul [0x004799B8]` where the constant
   is **0.036** → 36 ticks per second, i.e. **1 tick = 1/36 s**.
2. The sim driver `0x00422680` does `acc += dt; while (acc >= 0.5) { step(); acc -= 0.5; }`
   (`0x0042268D` / `0x004235E3`, constant `0x00479A50` = **0.5** ticks) → a fixed step of **1/72 s**.
3. The recorder call at `0x004235D8` lies **below** `0x004235B2`, which is the *target* of the parity
   branch `jne 0x4235B2` at `0x00423591`. The parity flag `[0x005531B8]` therefore gates only the
   `[0x00563D44]` advance above it — **the recorder runs on both parities**, once per sim step.

⇒ **21,600 samples ÷ 72 Hz = 300 s = 5 minutes**, not the 600 s implied by 36 Hz.
`notes/06` §5 and §2 state 36 Hz and line 124 attributes the recorder to `parity == 0`; both are
incorrect and are superseded here.

**Measured against a real ghost (§3.5): 10,452 samples over a 145.18 s race = 71.993 Hz.** At 36 Hz
the same file would imply a 290.33 s race — wrong by exactly 2×. The 72 Hz reading is now
empirical, not just derived. **[CERTAIN]**

### 1.7b The recording is slightly longer than the timed race

A real ghost contains a little more than the race the header times:

* a few samples of the car **stationary on the grid** before it moves (3 samples = 0.04 s observed);
* a **post-finish tail** — the recorder keeps sampling after the car crosses the line until the
  writer runs (174 samples = 2.42 s observed), so the progress word stops advancing before the
  padding begins.

So `active_samples ÷ 72` **overestimates** the race time (147.62 s vs a 145.18 s header in the
observed file). To recover the timed race, use the span from first motion to the last change in the
progress word — `gst.py`'s `race_span()` does this, and over that span the rate comes out at
71.993 Hz. **[CERTAIN, observed]**

### 1.8 Gating — when a ghost is written and read

**Write** (`0x004222B0`, entered from `0x00437FB7` when the race ends):
`[0x00527F6C] == 2` (time-trial mode) **and** `[0x005530B8] == 0` (ghost valid) **and** the new lap
beats `[0x00525E44] × 0.01`. Failure to write logs `"Error while saving ghostcar!\n"` (`0x004991B0`)
through the disabled logger and calls `exit(1)`. **[CERTAIN]**

**Read** (`0x00420240`, from `0x00418CB0`): requires `[0x00553068] == 1` (GHOST CAR on) and
`[0x00527F6C] == 2`. A failed `load_file` clears `[0x00553068]` and resets `[0x00525E44]` to
`0x2DC6C0`. A **track-id mismatch** jumps to `0x00420844`, which sets `[0x005530B8] = 1`. **[CERTAIN]**

### 1.9 Compatibility traps

1. **Exact length required.** `load_file` (`0x00457420`) does `fread(buf, 1, len, f)` and returns an
   error unless it reads exactly `0x49D4C` bytes (`0x00457478`). A file of 302,410 bytes — header +
   records with no trailer — is **rejected**. A rebuild must emit the full 302,412. **[CERTAIN]**
2. **Race time overflows at 327.68 s.** The time is `trunc(seconds × 100)` in an int16, read back
   with `movsx`. Since it stores a **3-lap** total and the shipped defaults already reach 263 s, this
   is a realistic failure, not a theoretical one. **[CERTAIN]**
2b. **A run longer than 300 s cannot be recorded at all** — the 21600-sample buffer wraps, sets
   `[0x005530B8]`, and the writer then refuses to save. **[CERTAIN]**
3. **A wrong-track ghost disables *saving*, not just playback.** The mismatch path sets
   `[0x005530B8] = 1`, and the writer refuses to save while that flag is set. Since
   `Ghosts/readme.txt` explicitly invites users to *"exchange ghostcar files with other people"*,
   dropping in a mis-named file silently blocks new ghost records for the session. **[CERTAIN]**
4. **Driver name truncates to 3 characters** on disk (§1.3).
5. **Directory case.** The shipped directory is `Ghosts\`, the code builds `GHOSTS\…`. Harmless on
   Windows, fatal on a case-sensitive filesystem — a port must fold case. **[CERTAIN]**
6. The padded tail's `progress` word is session-dependent (§1.6).

---

## 2. Settings and progress — `ign_win.btz`

`0x597` = 1431 bytes, written verbatim from the menu copy `0x004BF580` or the race copy
`0x006393A0` (delta `+0x179E20`). **The file is the only channel between the two copies** — there is
no direct `memcpy` between them, so the front end and the race engine communicate settings purely by
writing and re-reading `ign_win.btz`. A rebuild that keeps the two in sync in memory instead will
diverge from the original's behaviour on any path that skips a save. **[CERTAIN]** Fields are **packed and unaligned** — after the name slots the
dword grid resumes at `+0x0F3`, which is not 4-byte aligned. A naturally-aligned C struct will not
reproduce this file.

### 2.1 Region map — sums to exactly `0x597` **[CERTAIN, verified against two real files]**

| range | content |
|---|---|
| `+0x000 … +0x06B` | 27 × uint32 (grid A, aligned) |
| `+0x06C … +0x0F2` | 9 × `char[15]` name slots |
| `+0x0F3 … +0x17E` | 35 × uint32 (grid B, **unaligned**) |
| `+0x17F … +0x18E` | 16 × uint8 DIK keyboard scancodes |
| `+0x18F … +0x19A` | 3 × uint32 (grid C, unaligned) |
| `+0x19B … +0x1B8` | 2 × `char[15]` slots (observed `"G1"`, `"12"`) |
| `+0x1B9 … +0x1BE` | 6 spare bytes (zero in both samples) |
| `+0x1BF … +0x506` | 14 × `0x3C` leaderboard blocks |
| `+0x507 … +0x596` | 36 × uint32 (grid D, unaligned) |

The 9 name slots at `+0x06C` hold `PL1, BAN, VAC, ENF, RED, BUS, MON, BUG, PL2` — slot 0 is
player 1 and slot 8 is player 2 (`+0x0E4`, matching the known field map); the seven between them
are default AI/car names. **[INFERRED]** for the middle seven.

### 2.2 Leaderboards — `+0x1BF … +0x506` **[CERTAIN]**

14 blocks of `0x3C`: **5 × `char[4]` names, then 5 × float64 times in SECONDS**, sorted ascending.

The compiled-in defaults live at **`0x0047E590`** and are exactly `0x348` = 840 bytes = 14 × 0x3C,
immediately followed in `.data` by the `baltazar\…` / `ign_win.btz` / `GHOSTS\*.GST` string pool.
(`notes/03` describes `0x0047E590` as the defaults for the whole blob; it is in fact only this
leaderboard table.)

**Block index = `track*2` (full race total) and `track*2 + 1` (single best lap).** **[CERTAIN]** —
the per-track stride is `0x78` = 120 = 2 × 0x3C, computed at `0x0043A57A` as
`shl eax,3` → `lea ebp,[eax+eax*2]` → `[ebp + ebp*4 + 0x639593]`, i.e. `track × 120` based at
`+0x1F3`, which is `d[4]` (the slowest entry) of the even block. The even block is fed from the
result struct's total race time `+0x3A4`, the odd block from the three lap times
`+0x3BC/+0x3C4/+0x3CC`. Race ≈ 3 × lap for all seven pairs, i.e. **3 laps on every track**.

Track order is **[CERTAIN]**, confirmed three independent ways — the level/ghost switch at
`0x004198D0`, the name pointer table at `0x0047DFA8`, and the block defaults:

| blocks | track id | level | track name |
|---|---|---|---|
| 0 / 1 | 0 | `CANADA` | MOOSEJAW FALLS |
| 2 / 3 | 1 | `USA` | GOLD RUSH |
| 4 / 5 | 2 | `CARIB` | SNAKE ISLAND |
| 6 / 7 | 3 | `BRAZIL` | LOST RUINS |
| 8 / 9 | 4 | `AUSTRIA` | YODEL PEAKS |
| 10 / 11 | 5 | `ICELAND` | CAPE THOR |
| 12 / 13 | 6 | `JAPAN` | TOKYO BULLET |

(The pointer table has a trailing 8th entry at `0x0047DFC4`, the long form "MOOSEJAW FALLS" — do not
read past index 6.)

**Insertion** (`0x004394F9`–`0x0043AB6C`) is *replace-last + bubble-up*, not shift-down: compare
against `d[4]` and proceed only if **strictly** faster (ties rejected); overwrite slot 4 and its name;
then bubble from index 4 down to 1, swapping the double and the 4-byte name together. Guards: the car
must have finished (`[results+0x528] == 1`), must not already be recorded (8-dword flag array at
`0x00553010`), and **only car 0, plus car 1 when `+0x004 == 1`** — AI cars are never recorded, which
is why only `PL1`/`PL2` ever appear in a real file.

**Units are plain seconds.** The display formatter `0x0040C520` renders `MM:SS:CC`
(`min = (int)(t/60)` capped at 99, `sec = (int)t - 60*min`, `cs = (int)(frac*100)`), so the real
file's `144.898` shows as `02:24:89`.

> **Porting hazard:** the name slot is 4 bytes but the copy is an unbounded `sprintf("%s")`. A name
> longer than 3 characters **overflows into the next slot**. The name-entry UI caps at 3; a
> re-implementation must keep that cap. **[CERTAIN]**

### 2.3 Named fields

Confirmed from `notes/03` plus this work. Offsets are from the blob base.

**Structural key** — the blob contains several **per-car arrays**, index 0 = player 1, 1..7 = AI,
8 = player 2 as edited in the menu (index 1 is player 2 *at race time*). Apply routine `0x0041935C`
fans them out to `carinfo[i]`. This explains most of what previously looked like unstructured runs.

| off | field |
|---|---|
| `+0x000` | **VERSION magic — must be `0x1E` (30)** |
| `+0x004` | human players − 1 (so `==1` ⇒ two players) → `[0x0055306C]` |
| `+0x008` | **game mode** → `[0x00527F6C]` (jump table at `0x00419688`): **0 CHAMPIONSHIP, 1 SINGLE RACE, 2 TIME TRIAL, 3 PURSUE**. Strings at `0x00491E90` / `0x00491E84` / `0x00491E78` / `0x00491E6C`. **Mode 2 = TIME TRIAL is [CERTAIN]** — observed live (§3.4): `+0x008` went to 2 while `+0x153` went to 1, matching the "1 if mode == 2 and single player" rule at `0x00404A0D`. The ghost path gates on mode 2 (`0x004222B8`); the lap/record logic special-cases mode 3 (`0x004410E1`, `0x00424A78`). The GAME MODE page's items carry null blob pointers, so this field is written by the commit routine (`0x00404840`), not bound directly to a widget |
| `+0x00C` | DIFFICULTY → `[0x00563CE8]`. **≥3 (MIRROR) swaps the left/right key bindings** at `0x0041957B` |
| `+0x010` | GHOST CAR → `[0x00553068]` |
| `+0x014` | OBSTACLES → `[0x00553084]` |
| `+0x018` | defaults-only (=1), no reader found — reserved **[INFERRED]** |
| `+0x01C … +0x03C` | **9-entry per-car CAR MODEL array.** `+0x01C` = P1, `+0x020` = slot 1 (P2 at race time), `+0x024..+0x038` identity-filled 2..7 at `0x004048D5`. **[CERTAIN, verified]** |
| `+0x040` / `+0x044` | **P1 / P2 car-select cursors.** They index the **runtime unlocked-car list** at `[0x004BE6E0]`, *not* the model id directly, and the result is stored into `+0x01C` / `+0x020` at `0x004048A3` / `0x004048B7`. That list is rebuilt at `0x004099AF` by skipping `4 − [+0x587]` locked cars, minus one more if `+0x58B` is set — so the same cursor value maps to different models as cars unlock. Both default to **0**. Widget max is 10 (11 models: COO, BAN, VAC, ENF, RED, BUS, MON, BUG, SMO, VEG, IGN). **[CERTAIN, verified]** |
| `+0x048 … +0x068` | **9-entry GEARBOX array** (0=manual, 1=auto). `+0x048` = P1 widget, `+0x068` = P2 widget; defaults 9×1 via `rep stosd` at `0x0040C87C` |
| `+0x06C … +0x0F2` | **9-entry per-car NAME array**, 15-byte stride. `+0x06C` = P1, `+0x0E4` = P2; `+0x07B..+0x0D5` are AI names copied from the car-model table `0x0047E560`/`0x00497200` |
| `+0x0F3` | **championship round index.** In championship mode `[0x00552FC4] = [+0x0F3]`; otherwise resolved through the 4×7 table `0x004984A0[difficulty*7 + round]` (`0x00419326`). Incremented and capped at 6 by the ghost writer at `0x0042253B` |
| `+0x0F7` | CD TRACK (−1 random, 0 default) → `[0x00552E54]`; when 0, the per-track default comes from `0x00497EB8` clamped against the CD track count `[0x0050F070]` |
| `+0x0FB` | CD MUSIC → `[0x00527F30]` |
| `+0x0FF` | SFX VOLUME (0..10) → `[0x00601668]` |
| `+0x103` / `+0x107` | **One setting in two encodings**, not two settings. `+0x103` is the menu widget index (TINY, SMALL, MEDIUM, FULL); `+0x107` is the engine-side value `= 3 − [+0x103]`, so **0 = FULL … 3 = TINY**. Each is recomputed from the other on save/load (`0x004049F5` / `0x004094B1`); in-race hotkeys write `+0x107` via `[0x00563C6C]` (`0x0042105C` / `0x004210C9`, clamped 0..3). **[CERTAIN, verified]** |
| `+0x10B` | RESOLUTION → `[0x00552FC0]` |
| `+0x10F` | PERSP. POLY → `[0x00525E68]` |
| `+0x113` | SKID MARKS → `[0x00552E58]` |
| `+0x117` | SMOKE → `[0x00563BE0]` |
| `+0x11B`, `+0x11F` | defaults-only (=2), no reader found — reserved **[INFERRED]** |
| `+0x123` / `+0x127` | CONTROLS P1 / P2 |
| `+0x12B` / `+0x12F` | AUTO ACC P1 / P2 |
| `+0x133 … +0x14F` | **8-entry per-car STARTING GRID SLOT array.** Read by apply at `0x00419541` → `0x004972B0[i]`; grid geometry at `0x0041D96F` derives `row = slot/2` with a parity-driven lateral offset (staggered 2-wide grid). Rewritten post-race as `slot[i] = 6 − finishing position` at `0x00422594` (a **reverse grid** for the next round), then randomised by pairwise swaps at `0x004225F4` only when mode ≠ 0, cars > 1 and net role == 2. Zeroed by the 8-iteration loop at `0x00404A36`. **[CERTAIN, verified]** |
| `+0x153` | car / player count → `[0x006192F0]`; set to 6, or 2/1 in mode 2, at `0x00404A0D` |
| `+0x15B` | MIP MAPPING → `[0x00527F78]` |
| `+0x15F … +0x17B` | **8-entry CONTROLLER TYPE array** (0 = human, 1 = AI, 2 = network). `+0x15F`/`+0x163` set per player at `0x00404984`/`0x004049A2`; `+0x167..+0x17B` = 6×1 via `rep stosd` at `0x004049C0`. Read at `0x0041951B` → `carinfo[i].+0x04` |
| `+0x17F … +0x186` | **P1** key bindings, action order **TURN LEFT, TURN RIGHT, ACC., BRAKE, GEAR UP, GEAR DOWN, BOOST, REAR VIEW**; defaults LEFT, RIGHT, UP, DOWN, `/`, `.`, R SHIFT, `'` |
| `+0x187 … +0x18E` | **P2** same action order; defaults C, B, F, V, `\`, L SHIFT, Z, S |
| `+0x18F`, `+0x193` | reserved: write-once-zero (`0x0040C9E1`/`0x0040C9E7`), never read |
| `+0x197` | **network/session role** → `[0x00552F10]`: 2 = local, 3 = DirectPlay host, 4 = client (UI: 'WAITING FOR PLAYERS' / 'CONNECTING TO HOST...') |
| `+0x19B`, `+0x1AA` | **vestigial 15-byte string slots** ("G1", "12"): written by the defaults writer, **never read by any instruction** |
| `+0x507 … +0x567` | **25 menu page cursor positions**, `+0x507 + 4i` = saved cursor of page slot *i*. Loaded at `0x004094D8`, saved by the 25-iteration loop at `0x00404806`. Slot table built at `0x0040933A` into `[0x004BE768]`. See the mapping below |
| `+0x57F` | BEST TIMES screen's own track selector (0..6), distinct from `+0x0F3`; read at `0x0040C30B`, scaled ×0x78 |
| `+0x583` | BEST TIMES TOTAL/LAP toggle: 0 ⇒ even (race) block, non-0 ⇒ odd (lap) block |
| `+0x587` | **PROGRESSION COUNTER (0..3, "tiers beaten")**, doing double duty. (a) Max selectable difficulty: written into the DIFFICULTY item's `max` for all 6 languages at `0x00409510`, and `+0x00C` is clamped down to it at `0x004094B9`. (b) Car unlocking: `limit = 4 − [+0x587] − ([+0x58B] ≠ 0 ? 1 : 0)`, and the first `limit` entries of the locked list `{0x0A, 0x09, 0x06, 0x01}` (built at `0x00409613`ff) are excluded from the 11 models. So 7 cars are available at base; car 1 unlocks at `≥1`, car 6 at `≥2`, car 9 at `≥3`, car 10 when `+0x58B == 1`. Raised at `0x00439BC1` as tiers are cleared. **[CERTAIN, verified]** |
| `+0x58B` | **MIRROR / final-tier completion flag.** Set to 1 at `0x00439B99` when round == 6 and difficulty == 3; also unlocks one further car at `0x004099E1`. **[CERTAIN, verified]** |

`+0x587` and `+0x58B` are deliberately **saved and restored around the reset-to-defaults call**
(`0x0040474B`–`0x00404773`), i.e. "RESTORE SETTINGS" preserves unlock progress — independent
confirmation that they are progression state. Championship length by difficulty is NOVICE 5 /
AMATEUR 6 / PRO 7 / MIRROR 7 rounds, from the completion tests on `(round, difficulty)` ∈
`{(4,0),(5,1),(6,2),(6,3)}` at `0x0043958B`. **[INFERRED]**

Note championship **points** are *not* in the blob: they accumulate at
`[0x00563D00][i] += [0x00492A24][finishing position]` in the ghost writer.
| `+0x593` | LANGUAGE: 0 EN, 1 DE, 2 IT, 3 ES, 4 SE, 5 FR. The menu item's `max` is **4**, and the flag-draw loop runs 5 iterations, so **French (5) has complete string tables but is unselectable in the UI** **[INFERRED]** |

**Page cursor slots** (`+0x507 + 4i`). Item counts verified against the 25 page records at
`0x00481628` (stride `0x18`, cursor at `+0x14`):

| slot | off | page | n | identity |
|---|---|---|---|---|
| 0 | `+0x507` | `0x481850` | 5 | MAIN MENU |
| 1 | `+0x50B` | `0x481838` | 7 | OPTIONS |
| 2 | `+0x50F` | `0x481808` | 2 | MULTIPLAYER TYPE → `+0x004` |
| 3 | `+0x513` | `0x4817D8` | 4 | GAME MODE → `+0x008` |
| 4 / 5 | `+0x517` / `+0x51B` | `0x4817C0` / `0x4817A8` | 1 | P1 / P2 CAR SELECT → `+0x040` / `+0x044` |
| 6 | `+0x51F` | `0x481790` | 7 | TRACK SELECT → `+0x0F3` |
| 9 | `+0x52B` | `0x481718` | 6 | GFX OPTIONS |
| 10 | `+0x52F` | `0x481730` | 3 | SOUND OPTIONS |
| 11 / 12 | `+0x533` / `+0x537` | `0x481748` / `0x481760` | 7 | PLAYER 1 / PLAYER 2 OPTIONS |
| 13 / 14 | `+0x53B` / `+0x53F` | `0x4816D0` / `0x4816E8` | 8 | P1 / P2 KEYBOARD CONFIG |
| 15 | `+0x543` | `0x481700` | 3 | GAME OPTIONS |
| 16 | `+0x547` | `0x481820` | 2 | BEST TIMES → `+0x57F`, `+0x583` |
| 21 | `+0x55B` | `0x481868` | 1 | LANGUAGE → `+0x593` |
| 22 / 23 / 24 | `+0x55F` / `+0x563` / `+0x567` | `0x481628` / `0x481640` / `0x481658` | 2 | "QUIT?" / "RESTORE SETTINGS?" / "RESET GHOST CAR?" (row 0 = YES, 1 = NO) |

Slots 7, 8 and 17–20 are unlabelled. The mapping is self-consistent with every observed cursor in the
real file (slot 9 = 5 = SMOKE, the last of 6 GFX items; slot 10 = 1 = SFX VOLUME; the three dialogs
all 1 = NO). An earlier draft of this note mis-paired slots 9, 10, 21 and 24. **[CERTAIN]**

**Menu option value lists** (English, block 1) — useful for naming values rather than indices:
DIFFICULTY `NOVICE, AMATEUR, PRO, MIRROR`; RESOLUTION `320X200, 640X480, 800X600`;
SCREEN SIZE widget `+0x103` = `TINY, SMALL, MEDIUM, FULL`, so engine-side `+0x107` runs
**0 = FULL … 3 = TINY**; CONTROLS `KEYBOARD, JOYSTICK, PEDALS, JOYPAD`; GEARBOX `MANUAL, AUTO`;
CD TRACK `RANDOM(-1), DEFAULT(0), 1..25`; the OFF/ON toggles are 0/1.

The ghost writer also randomly permutes `+0x133…` at `0x004225D0` when `[0x00552F10] == 2` and more
than one car is racing — a tie-break shuffle. **[CERTAIN]**

### 2.4 Version gating and rejection **[CERTAIN]**

* **Menu side** (`0x00409461`): `file_exists` → read 4 bytes → `cmp eax, 0x1E` → on absence or
  mismatch, **skip the load entirely and keep compiled-in defaults**. Silent and safe.
* **Race side** (`0x00419260`): **not** tolerant, and — verified by disassembly — it performs
  **no version check at all**. It `_open`s the file (failure ⇒ log `"Error while trying to read %s\n"`
  + `exit(1)`), takes the **actual file size** via `0x00457630`, and passes *that size* — **not
  `0x597`** — to `load_file` at `0x004192C4`, reading straight over the race blob `0x006393A0`.
  Failure ⇒ `"LOAD ERROR\n"` + `exit(1)`. **[CERTAIN]**
* **Language-only early load** (`0x004029A0`): version-checks, then reads 4 bytes at file offset
  `0x593` into `[0x004BE60C]`.

⇒ A file the game *rejects* is never repaired or rewritten in place — it is simply ignored, and the
next save overwrites it wholesale. There is no partial upgrade path and no backup.

### 2.5 Compatibility traps

1. **Unaligned dword grids** (`+0x0F3`, `+0x18F`, `+0x507`). Reproduce byte offsets, not a struct.
2. **Resizing the blob corrupts adjacent state.** The MSVC static-init guard bytes sit immediately
   after the menu copy at `0x004BFB18`. **[INFERRED]**
3. **Times are float64 seconds**, not centiseconds as in the `.GST` header — the two files disagree
   on units for the same quantity.
4. **The race-side loader kills the process** on a missing or short file (§2.4). A rebuild that
   writes the file lazily, or writes fewer than `0x597` bytes, will produce a silent exit.
5. **The race-side loader is unbounded and unversioned.** It reads `filesize` bytes into the fixed
   race blob at `0x006393A0` with no magic check and no length cap (`0x004192C4`). A file **larger**
   than `0x597` therefore overruns the blob into adjacent globals, and a file with a **wrong version
   byte** is silently accepted by the race engine even though the menu rejected it — so the two
   copies can disagree. Any rebuild must write exactly `0x597` bytes. **[CERTAIN]**
6. **The menu loader discards the read result** (`0x004094A9`) and performs no size check, so a
   *truncated but correctly-versioned* file is **partially applied on top of the defaults** rather
   than rejected. `read_file` also returns `0x7DA` on a short read **without calling `fclose`**, i.e.
   it leaks a `FILE` handle. **[CERTAIN]**
7. **"RESTORE SETTINGS" wipes the leaderboards.** The handler `0x00404740` saves only `+0x587`,
   `+0x58B` and `+0x593`, calls the defaults writer, then restores those three — so all 14 blocks of
   best times are lost while unlock progress and language survive. Its sibling `0x00404790`
   ("RESET GHOST CAR") merely deletes the 7 `.GST` files. **[CERTAIN]**

---

## 3. Verification performed

### 3.1 `ign_win.btz` — PASS

`btz.py roundtrip` parses to fully typed Python values (dword grids, 15-byte name slots, scancodes,
`char[4]`+float64 leaderboards) and re-serialises **without consulting the original bytes**. Result on
two independent real snapshots of the user's live file:

```
byte-exact round-trip: 1431 bytes match     (snapshot 01:27)
byte-exact round-trip: 1431 bytes match     (snapshot 01:34)
```

**No unexplained bytes**: every one of the 1431 is assigned to a region, and the regions sum to
`0x597`. Semantics are known for the fields in §2.3; the remaining dwords are preserved verbatim and
listed as unknown in §4.

### 3.2 Accidental live-diff — behavioural confirmation

The game rewrote `ign_win.btz` between the two snapshots (mtime 01:27:30 → 01:34:54; the user was
playing). Exactly **9 bytes** changed, and they are precisely the fields this document predicts a
championship state change would touch. The counter at `+0x0F3` dropping to 0 and the per-car scores
becoming a clean ascending `0,1,2,3,4,5` reads as a **championship restart**; the other two bytes are
the player changing their **car selection**. **[INFERRED]** for the narrative, **[CERTAIN]** for the
field identities.

| offset | change (01:27 → 01:34) | identified as |
|---|---|---|
| `+0x01C` | 3 → 2 | **P1 car model** (per-car model array, `0x004048A3`) |
| `+0x040` | 2 → 1 | **P1 car-select cursor** — moves in lockstep with `+0x01C` through `[0x004BE6E0]` |
| `+0x0F3` | 1 → 0 | championship round index, `0x0042253B` |
| `+0x133 … +0x147` | `5,3,4,1,0,2` → `0,1,2,3,4,5` | **starting grid order** — a reverse grid reverting to identity |

The cursor and the model moving together (`cursor 2→model 3`, `cursor 1→model 2`) is exactly what
`0x004048A3` produces. Read correctly, the whole diff is one coherent story: the player changed car
and restarted a championship, so the grid reverted from a post-race reverse order to the identity
order. Two earlier drafts of this note were **wrong** about these fields — first calling
`+0x01C`/`+0x040` championship counters, then calling `+0x133…` a championship *score* array; it is
the starting grid, and points live outside the blob entirely.

The file was rewritten a third time at **01:58:23** (md5 `92a90ae9…`), and that diff is a second,
independent confirmation — this time of the *menu page cursor* model:

| offset | change (01:34 → 01:58) | identified as |
|---|---|---|
| `+0x008` | 0 → 1 | GAME MODE |
| `+0x513` | 0 → 1 | **cursor of page slot 3 — the game-mode page**, whose selection drives `+0x008` (CHAMPIONSHIP → SINGLE RACE) |
| `+0x01C` | 2 → 5 | P1 car model |
| `+0x040` | 1 → 4 | P1 car-select cursor |

A setting and the cursor of the page that edits it moved together, in both pairs, with all 14
leaderboard blocks untouched. That is exactly what `+0x507 + 4i` = "saved cursor of page slot *i*"
predicts, and it independently corroborates the `+0x008` binding and the cursor→model indirection.
Across the three snapshots the grid array `+0x133…` reads `[5,3,4,1,0,2,6,7]` then identity
`[0..7]` twice — a reverse grid from a finished race, reverting on championship restart.

### A fourth write — real-data confirmation of the leaderboards

At **02:11:24** the user raced Canada in TIME TRIAL and set a new time. This is the first time the
leaderboard region mutated in real data, and it confirms the decode of §2.2 end to end:

| | before | after |
|---|---|---|
| blk0 (Canada, **race total**) | 144.89, 145.00, 145.64, 151.63, 160.00 | 144.89, 145.00, **145.18**, 145.64, 151.63 |
| blk1 (Canada, **best lap**) | 47.12, 47.45, 47.66, 48.00, 48.42 | 47.12, 47.45, **47.47**, 47.66, 48.00 |

Confirmed by this single event: **exactly the two blocks for track 0 changed** (so block = `2t` / `2t+1`
and the track order are right); the tables stayed **5-deep and ascending**; the new entry entered and
**bubbled up to its sorted position, pushing the previous 5th off the bottom** (`TOM 160.00`
disappeared) — the replace-last + bubble-up algorithm, not a shift-down; **names and doubles moved
together**; only `PL1` was recorded; and the values are **float64 seconds**.

Critically, `145.18 / 47.47 = 3.06` — **real-data proof that a race is 3 laps**, which is exactly the
fact that forced the `.GST` header correction in §1.3.

The same diff also upgrades the game-mode mapping from **[INFERRED]** to **[CERTAIN]**: `+0x008`
moved 1 → 2 with its page cursor `+0x513` in lockstep, and `+0x153` (car count) moved 6 → **1**,
which is precisely the documented "1 if mode == 2 and single player" rule at `0x00404A0D`. So
**mode 2 = TIME TRIAL**.

All four snapshots round-trip byte-exactly. **None of these writes came from this analysis** —
every tool here opened the live file read-only and all output went to `_re/out/` or the scratchpad.
The user is running the game, which the task brief anticipated. Consequence for anyone reading this
note: treat the **defaults writer `0x0040C810`** as authoritative for default values, and treat any
observed-file value as snapshot-dependent.

Both value sets are permutations of `0..5`, consistent with the literal `mov esi, 6; sub esi, ebx`
(`6 − finishing position`) at `0x004225B0`. The *formula* is **[CERTAIN]**; calling it a
"championship score" is **[INFERRED]**.

No leaderboard block changed (no record was beaten), and the keybindings and name slots were
untouched. This is independent, real-world evidence that the field map and the region boundaries are
right — stronger than static inference alone.

### 3.5 `.GST` — VERIFIED AGAINST A REAL GHOST FILE

**The standing caveat in §3.3 is resolved.** While this analysis was running, the user set a new
time on Canada in TIME TRIAL and the game wrote **`Ghosts\MOOSEJAW.GST`** (302,412 bytes, 02:14).
Every prediction in §1 held:

| prediction | observed |
|---|---|
| size exactly `0x49D4C` | 302,412 bytes ✓ |
| trailer always `00 00` | `00 00` ✓ (confirms the allocator zero-fill deduction) |
| name = 3 chars + NUL | raw `b'PL1\0'` ✓ |
| track id → filename | id 0 → `CANADA` / `MOOSEJAW` ✓ |
| header time = **total 3-lap** race | 14518 cs = **145.18 s** ✓ |
| coordinates are **raw mesh** | 10629/10629 samples inside the track bbox (100.0 %) ✓ |
| angles normalised to `[0, 2π)` | all in range, validation PASS ✓ |
| `progress = lap*(nsections+10) + section` | max 567 = 3 × 189 ⇒ nsections = 179 ✓ |
| byte-exact round-trip | **PASS**, 302,412 bytes ✓ |

**Cross-file confirmations**, which are the strongest evidence in this document because they tie
three independently-decoded structures together:

1. The ghost header time **145.18 s** is *exactly* the entry that appeared in leaderboard block 0
   (Canada race total) in the same play session — `.GST` header and `.btz` leaderboard agree to the
   centisecond. This settles that the header holds the **total 3-lap time**.
2. The ghost's car model index **5** equals `+0x01C` (P1 car model) in the settings blob.
3. Splitting the ghost at progress-word lap boundaries gives lap times **48.67 / 47.47 / 49.03 s**
   at 72 Hz. The middle one is *exactly* the **47.47 s** best-lap entry that appeared in leaderboard
   block 1. A single number simultaneously validates the sample rate, the progress formula, the lap
   detection and the leaderboard decode.
4. The three laps sum to 145.17 s against a 145.18 s header.

The rendered overlay `_re/out/tracks/MOOSEJAW_ghost.png` shows the path following the road through
every corner for three laps and closing on itself — the acceptance test defined in §3.3, passed.

### 3.3 `.GST` — pre-verification status (historical)

There are no `.GST` files in this installation (`Ghosts/` contains only `readme.txt`), and **no fake
one was created**. What *was* tested:

* **Serialiser round-trip** on a synthetic in-memory buffer: 302,412 bytes, re-pack identical.
  This exercises `gst.py`'s encoder only and says nothing about whether the layout matches the game.
* **Structural validation fails loudly** on: wrong file length, non-zero trailer, track id outside
  0..6, an angle outside `[0, 2π×1024)`, mesh `x`/`z` outside `[-25600, 25600]`, and a
  non-NUL-terminated name field.
* **`gst_plot.py`'s projection was proved correct** independently of any ghost: it recomputes the
  transform from the level geometry and reproduces all seven existing `*_topdown.png` renders at
  exactly the right pixel dimensions (Austria 1459×1620, Brazil 1600×1275, Canada 1600×1269,
  Carib 1600×1493, Iceland 1600×1295, Japan 1145×1620, USA 1600×1426 — all MATCH).

**The acceptance test, the moment a real `.GST` appears:**

```
gst_plot.py GHOSTS/CAPETHOR.GST
```

It prints the ghost's coordinate range against the track's, reports the percentage of samples inside
the track bounding box (warning below 90 %), and writes an overlay PNG. **A correct decode traces the
road** for one lap and closes on itself. A blob, a diagonal streak, or an off-map path means the field
order, the stride, or the coordinate bias is wrong.

---

## 4. What remains unknown

**`.GST`** — everything in §1 is unverified against real data (§3.3). Specifically uncertain:

* Which of the three angles is yaw / pitch / roll. Only their car-struct sources are known
  (`+0x108` → w3, `+0x0F8` → w4, `+0x110` → w5). **[INFERRED]**
* The meaning of `car[0x354]`, which triggers the `+5000` y bias. **[INFERRED]**
* `[0x005287F8]` (the `nsections` term in `progress`) is not pinned to a named track quantity.
* The exact semantics of the `-1.0` race-clock sentinel (`[0x00552E48]`) that enables recording.

**`ign_win.btz`** — no bytes are unexplained, and after the second pass most fields now have a
confirmed meaning (§2.3). What genuinely remains open:

* **The identity of `[0x00525E44]`** — the best-time global the ghost writer gates on. It is an
  *integer* scaled by `0.01` (`0x479A40`) and compared against the total race time `+0x3A4`, so it is
  a best **total race time** in centiseconds, not a lap time; but it was not traced end to end.
* The caption polarity of `+0x583` (which of TOTAL/LAP prints for 0 vs 1) — cosmetic.
* Menu page slots 7, 8 and 17–20 are unlabelled.

**Proven dead fields.** These are written **once** by the defaults writer `0x0040C810` and **never
read**: `+0x018`, `+0x03C`, `+0x11B`, `+0x11F`, `+0x157`, `+0x18F`, `+0x193`, `+0x19B`, `+0x1AA`.
Also proven dead: **`+0x58F`** (the literal `0x004BFB0F` occurs exactly twice image-wide, both stores
of constant 0 at `0x0040CA2E` and `0x00404A51`, zero reads) and **`+0x56B … +0x57B`** (the literals
`0x004BFAEB/EF/F3/F7/FB` occur nowhere except `cmp ecx, 0x4bfaeb` at `0x004094F6`, which is the
*exclusive end bound* of the 25-entry cursor loop, not a field access).
Established by a whole-image byte scan plus an independent base-register+displacement scan of
`.text`: exactly one reference each (the defaults store), and the **race-copy** equivalents
(`0x006393B8`, `0x006393DC`, `0x006394BB`, `0x006394BF`, `0x006394F7`, `0x0063952F`, `0x00639533`,
`0x0063953B`, `0x0063954A`) have **zero** references. A port must still write them to stay
byte-identical, but nothing consumes them. **[CERTAIN]**
Plausible origins, **[INFERRED]** only: `+0x018` sits right after GHOST CAR / OBSTACLES and defaults
to 1, so probably a cut third race toggle; `+0x11B`/`+0x11F` have the same two-element per-player
shape as CONTROLS / AUTO ACC; `+0x18F`/`+0x193` look like an unreachable third per-car key slot.
* Whether the result-struct field `+0x3A4` is best named "total race time" or "lap time" — the
  leaderboard code treats it as the race total, while the ghost writer compares it against the best
  **lap** `[0x00525E44]`. These reconcile only because the ghost path runs solely in time-trial mode
  (`[0x00527F6C] == 2`), where the race is a single lap. Worth confirming before relying on it.
* The 6 spare bytes at `+0x1B9`. Nothing reads them; they may equally be part of a longer `+0x1AA`
  slot. Writing them as zero reproduces the original exactly.
* Menu **item label text** per widget was not resolved, so a few option fields are identified by
  blob offset and bound widget rather than by their on-screen name.

---

## 5. Rebuild checklist

1. Emit `.GST` at exactly **302,412** bytes with a zeroed 2-byte trailer, or the original will refuse
   to load it.
2. Sample the ghost at **72 Hz** (1/72 s fixed step), 21,600 samples max = 300 s — which must cover
   the **whole 3-lap race**, not one lap. Runs over 300 s cannot be recorded; over 327.68 s the
   header time also wraps negative.
3. Store **raw mesh** coordinates; apply the `+25600` x/z bias only inside the simulation.
4. Normalise angles to `[0, 2π)` **before** scaling by 1024, so the stored value is never negative.
5. Pad short laps by copying words 0–5 of the last real sample, leaving word 6 untouched.
6. Keep `ign_win.btz` at exactly `0x597` bytes with version `0x1E` and the unaligned grids intact;
   write the whole file every time.
7. Fold filename case (`Ghosts\` vs `GHOSTS\`) on case-sensitive filesystems.


---

## Verification against a real ghost (2026-09-16)

The spec in this note was written with no `.GST` in the install. One now exists:
`Ghosts/MOOSEJAW.GST`, produced by a 2:25.18 Time Trial run on Canada.

| Check | Result |
|---|---|
| File size | 302,412 bytes exactly, as specified |
| Byte-exact round-trip via `gst.py` | PASS |
| Structural validation | PASS |
| Header | car 5, track id 0 (Canada), 14518 cs = 2:25.18, driver `PL1` |
| Trailing 2 bytes | `00 00`, as predicted |
| Live samples inside track bounds | 10,629 / 10,629 = 100% |
| Path plotted over the track | follows the drivable surface through every corner and closes the lap |

10,629 samples / 72 Hz = 147.6 s, consistent with the 145.18 s race plus the
run-up. The remaining records are the padded tail, so **sample count, not
record count, is the drive time** - all 21,600 records are non-empty.

**Correction to the save condition.** This run did *not* beat the leaderboard
record for Canada (2:24.90) yet the ghost was still written. So `[0x00525E44]`,
the value compared at `0x004222DF`, is **not** the leaderboard time; it behaves
as a separate best-ghost time which was effectively unset. The structure of the
test (mode == 2, invalid flag clear, `[0x00525E44] * 0.01 > race time`) is
confirmed; only the identity of that global remains open.
