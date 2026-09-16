# Ignition (`Ign_win.exe`, 1997) — image, palette, font, audio and frontend-data formats

Scope: everything that is not track geometry (MSH/TRI/SRF/PLC/POS/SHD/TAB/PAN/AIS belong to the geometry note).
Binary: PE32 i386, ImageBase `0x00400000`, MD5 `527bc475783319ecdd8adae1f97f6759`. No bytes of the game were modified,
the game was not launched.

Tags: **[CERTAIN]** = read from disassembly and/or exact byte accounting over every file;
**[INFERRED]** = deduced / consistent with evidence but not proven in code.

---

## 0. Summary table

| format | files | what it is | compression | byte accounting | loader / consumer | palette |
|---|---|---|---|---|---|---|
| `.COL` | 9 | **Autodesk Animator Pro COL** (8-byte hdr `0x308, 0xB123, 0`) + 768 RGB (0..255) | none | 9/9 exact | `0x004574A0` whole-file / `0x00457420` at +8 / `0x00418DD0` | — |
| `.PIC` | 26 | **Autodesk Animator Pro PIC** (magic `0x9500`): 64-byte hdr, colour-map chunk, byte-pixel chunk | none | 26/26 exact | pixels always read at **+0x34E**, chunks never parsed | see §2.4 |
| `.TEX` | 23 | raw 8-bit indices, **256×256 pages** concatenated | none | 23/23 exact (incl. 64/128/192-byte zero pads) | `0x00419D10` + `0x0041A4CC`; 64 KiB slot rounding | level COL / menu.col |
| `menubkg.dat` | 1 | 124 cells of 16×19 + 24 band colours | none | exact | `0x00401000` / `0x004011C0` | SYS.COL |
| `.LFT` | 18 | "Font 1.00.2" bitmap font: hdr, 224 offsets, tool palette, 224 widths, raw glyphs | none | 13 exact; 5 have 90 B orphaned glyph (`|`) | `0x00456420` → `0x00456270` | SYS.COL (menu) / level COL (HUD) |
| `IGNITION.FNT` | 1 | debug font: widths, char map, offsets, 2-bit-valued glyphs | none | exact | `0x0041AC40`, drawn by `0x004133D0` | colour base + value |
| `.CDP` | 6 | intro movies, byte-code delta frames | RLE/skip byte code | 6/6 exact | `0x00412580` / `0x00412610` | own embedded |
| `.PSQ` | 1 | PFM segment playlist | none | exact | `0x0040CA88` / `0x0040C740` | — |
| `.PFM` | 1 | pre-projected 2D polygon movie (menu background) | none | exact | `0x0040CADE` / `0x0040FFE0` | menu.col |
| `.PAT` | 41 | **GUS GF1 patch** (1 instr/1 layer/1 sample, 8-bit unsigned, 11025/22050/22100 Hz) | none | 41/41 exact | dir scan `0x00458E00` → `0x004582B0` | — |
| `.XI` | 8 | **FastTracker 2 XI v0x0102**, 8-bit signed delta | delta | 7/8 exact (01_ME_JO.XI: 1 trailing byte never read) | `0x004584D0` (delta decode `0x00458609`) | — |
| `.WAV` | 81 | RIFF PCM, 8-bit mono (11025/22050, one 14385 Hz) | none | 81/81 exact | `0x004587D0` | — |
| `ENGINE.INF` | 11 | 4 × 200 signed bytes: pitch0, vol0, pitch1, vol1 vs revs | none | 11/11 exact | per-frame `0x00445524` | — |

Tools (all run from the game dir with the venv interpreter):
`_re/tools/strref.py` (string → exhaustive dword reference scan), `_re/tools/dwref.py` (global → exhaustive reference scan),
`_re/tools/formats/{col,pic,tex,lft,fnt,menubkg,palette_test}.py`, plus the audio and frontend readers listed in §7/§8.

---

## 1. Shared file-loading primitives [CERTAIN]

| VA | behaviour |
|---|---|
| `0x004574A0` `LoadWholeFile(name)` | exists-check `0x004576B0`, size `0x00457630`, pool alloc `0x0045AE10`, `fopen(name,"rb")` `0x00469390`, `fread` `0x00469170`, `fclose` `0x00469100`; returns buffer or 0 and sets `[0x004BAB34]` = `0x7EE/0x7F8/0x802/0x7D0/0x7DA` |
| `0x00457420` `LoadFileAt(name, buf, size, offset)` | `fopen`, `fseek(offset)` (`0x00469D20`), `fread(buf,1,size)` |
| raw idiom (level/HUD assets) | `_open(name, 0x8000 /*_O_BINARY*/)` `0x00470E90` → `_filelength` `0x004787C0` → pool alloc `0x0045AE10([0x0055307C], len)` → `_read` `0x0046ABD0` → `_close` `0x0046A9D0`; open failure → `"Error while trying to read %s"` + `exit(1)` |
| `0x00456C40` `SetGamePalette(rgb768)` | wrapper → `0x0045C6D0` (see note 01 §3.2) |

---

## 2. Palettes (`.COL`) and PIC images

### 2.1 COL layout — Autodesk Animator Pro COL [CERTAIN]

| off | type | value (all 9 files) | used by game |
|---|---|---|---|
| 0x000 | u32 | `0x00000308` = file size (776) | no |
| 0x004 | u16 | `0xB123` (Animator Pro COL magic) | no |
| 0x006 | u16 | 0 (version) | no |
| 0x008 | u8[768] | RGB × 256, components 0..255, copied verbatim into `PALETTEENTRY` | **yes** |

Byte accounting (`col.py`): 9/9 files = 8 + 768, 0 remainder.

Consumers:
```
0x004181A6  call 0x4574a0            ; LoadWholeFile("SYS.COL") -> [0x563BFC]      (menu init 0x00417EA0)
0x00418111  mov eax,[0x563bfc] / add eax,8 / push eax / call 0x456c40   ; SetGamePalette(sys.col+8)
0x004029C9  push 8 / push 0x300 / push buf / push "baltazar\data\menu.col" / call 0x457420  ; frontend
0x00402A1A  call 0x456c40            ; SetGamePalette(menu.col)
0x00418DE3  sprintf("LEVELS\%s%s.COL") -> _open/_read -> [0x54F900]                (level load)
0x00441360  mov eax,[0x54f900] / add eax,8 / push eax / call 0x456c40   ; SetGamePalette(level COL)
            (also 0x0043490D; fades at 0x00430617.. build from [0x54F900]+8)
```

### 2.2 Palette structure across the 9 COL files [CERTAIN] (`palette_test.py`)

* Indices **160–228 and 230–255 are byte-identical in all 9 COL files** (the shared "system/car/HUD" range).
* Indices **0–159 are level-specific**; index **229 differs in every file**.
* `sys.col`, `Menu.col` and `CANADA.COL` are the *same* palette: 255/256 entries identical pairwise
  (each differs from the others in one entry). So SYS/menu art is authored on the Canada palette.
* Index usage confirms the split: every level `.TEX` uses only 0–159; `CARS/Cars.tex` uses 0 + 160–255;
  HUD/system PICs use 160–255 plus a handful of low indices (0–14).
* Index **229 (0xE5)** is the most frequent glyph value in the LFT fonts (e.g. small.LFT: 1965 of 6888 pixels)
  and appears in `menubkg.dat` band colours — it behaves as a per-palette "don't-care"/key colour. **[INFERRED]**:
  an exhaustive byte scan for `cmp r8,0xE5` / `cmp byte [..],0xE5` in `.text`/`code` found **no** literal
  compare, so if 229 is a transparency key it is passed as data (sprite desc `+0x0C`?), not hard-coded. Open.

**Wrong-palette test [CERTAIN numbers].** Rendering TEX page 8 and the level PIC with a foreign COL vs its own COL gives a mean
absolute RGB error of **25–116** per channel (e.g. Austria TEX with sys.col 97.3, Japan PIC with Brazil.col 69.5); with
the own COL it is 0 by construction, and for Canada sys.col/Menu.col give 0.0 (same palette). Side-by-side controls:
`_re/out/images/palette_test/*_own_vs_*.png` (e.g. Japan TEX with sys.col: obviously wrong — green/cyan trucks, garbage signs).

### 2.3 PIC layout — Autodesk Animator Pro PIC [CERTAIN]

64-byte header:

| off | type | meaning | game reads it? |
|---|---|---|---|
| 0x00 | u32 | file size | no |
| 0x04 | u16 | magic `0x9500` | no |
| 0x06 | u16 | width | no (see below) |
| 0x08 | u16 | height | no |
| 0x0A | i16 | x origin (Animator screen position, e.g. bilar.pic = (121,−277)) | no |
| 0x0C | i16 | y origin | no |
| 0x0E | u32 | user id (0) | no |
| 0x12 | u8 | bits per pixel (8) | no |
| 0x13 | u8[45] | reserved, all zero | no |

Chunks `{u32 size incl. 6-byte header; u16 type}`:

| off (all files) | chunk | contents |
|---|---|---|
| 0x040 | type 0 colour map, size 0x308 | u16 version 0 + 768 RGB (the artist's palette; many entries zero) |
| 0x348 | type 1 byte pixels, size w·h+6 | w·h bytes, row-major, **top row first** |
| **0x34E** | | first pixel |

Byte accounting (`pic.py`): 26/26 files = 0x40 + 0x308 + (w·h+6), 0 remainder, reserved bytes all zero.
No compression anywhere.

**The EXE never parses the header or the chunks** [CERTAIN]:
```
; level PIC (0x00418650): copy 320x200 from +0x34E into the 3D framebuffer, then set the level palette
0x00418668  mov edx,[0x525e94]
0x00418671  mov dl,[edx+ecx+0x34d]        ; ecx = 1..w*h  -> pixel index (ecx-1) at +0x34E
0x00418678  mov [ecx+0x563daf],dl         ; -> 0x00563DB0 framebuffer
0x00418680  call 0x441360                 ; SetGamePalette(level COL)
; frontend (0x00403FCF..): hard-coded w*h and fixed offset
0x00403FCF  push 0x34e / push 0x1e786 / push [0x4be84c] / push "baltazar\data\bilar.pic" / call 0x457420
            ; 0x1E786 = 186*671 ; trk_spr 0x1374F=115*693 ; ign_logo 0x2685=173*57 ; car_sel 0x49B6=255*74 ; flaggor 0x978C=61*636
; sprite cut from N_SYSGFX (0x004041D8): 4x3 grid of 51x44 icons in the 256-wide sheet
0x004041E6  add eax,0x34e ; + col*0x33 + row*0x2C00  -> desc+0x10 (pixel ptr)
            desc+0x04=0x33 (w)  desc+0x08=0x2C (h)  desc+0x14=0x100 (pitch = sheet width)
            desc+0x18=0x1900  desc+0x1C=0x1600 (hot-spot, 8.8 fixed = 25,22)
0x00404228  call 0x456d40                 ; CreateSprite(desc) -> driver [0x50EBC0] = 0x45C830 -> 0x461360
```
So a rebuild must carry the sprite rectangles (from the EXE) — the PIC file is just a pixel sheet.

Sprite descriptor (0x40-byte object copied by `0x00461360`, `rep movsd ecx=0x10`) [CERTAIN for fields shown]:
`+0x00` type (0), `+0x04` width, `+0x08` height, `+0x0C` 0 in all callers seen (colour key? [INFERRED]),
`+0x10` pixel pointer, `+0x14` pitch, `+0x18` hot-spot x (8.8), `+0x1C` hot-spot y (8.8).

### 2.4 Which PIC is loaded where, and with which palette

| file | loader site | buffer global | consumer | palette on screen |
|---|---|---|---|---|
| `n_Sysgfx.pic` 256×412 | `0x0041813A` LoadWholeFile (menu init `0x00417EA0`, `exit(1)` on failure) | `[0x0054F998]` | sprites `0x00404138..`, `0x0041EE68..0x0041F108` | SYS.COL in menus [CERTAIN: `0x0041811A`], level COL in race [INFERRED] — same colours in 160–255 |
| `n_Sysg_2.pic` 242×172 | `0x00418167` | `[0x00553088]` | `0x0041F155..0x0041F1D0` | as above |
| `LEVELS\%s%s.PIC` 320×200 | `0x00418F52` | `[0x00525E94]` | `0x00418650` full-screen blit (race loading screen [INFERRED]), freed `0x004208BC` | **own level COL** [CERTAIN] |
| `s_pangfx.pic` / `n_pangfx.pic` | `0x0041AF70` / `0x0041AFF3` raw read | `[0x00563C08]` / `[0x00563C00]` | HUD builder `0x0041E9A0..0x0041F800` | level COL [INFERRED from load context] |
| `h_pan1.pic` / `h_pan2.pic` | `0x0041B06F` / `0x0041B0EB` | `[0x00601660]` / `[0x00601670]` | same | level COL |
| `s_signs.pic` / `n_signs.pic` / `h_signs.pic` | `0x0041B167` / `0x0041B1E3` / `0x0041B25F` | `[0x0054F990]` / `[0x0054F910]` / `[0x0054F950]` | same | level COL |
| `pokal.pic` 207×426 | `0x0041B2DB` | `[0x0060166C]` | trophies | level COL |
| `baltazar\data\{bilar,trk_spr,ign_logo,car_sel,flaggor}.pic` | `0x00403FDA..0x00404055` (LoadFileAt +0x34E) | `[0x004BE84C]`,`[0x004BE820]`,`[0x004BEB48]`,`[0x004BF560]`,`[0x004BE8A8]` | frontend | menu.col [CERTAIN: `0x00402A1A`] |
| `Baltazar/data/{democar,demotrk,Conspr,MARK}.pic` | — | — | **no filename string in the EXE** (exhaustive case-insensitive `.pic` string scan) → unused by this build [CERTAIN] | rendered with menu.col |

`S_`/`N_`/`H_` prefixes = small/normal/high variants for the 320×200 / 640×480 / 800×600 modes (mode table
`0x0047DC30`) **[INFERRED]** from the sizes (S_Signs 56², N_Signs 112², H_signs 256²) and identical contents at different scales.

Embedded colour maps: for level PICs the embedded map equals the level COL on all used indices (e.g. JAPAN.PIC 242/242,
USA 196/196, CARIB 234/235 — the miss is index 229). For HUD/system/frontend sheets the embedded map equals
Menu.col/sys.col on ~90 % of used indices (misses are 229 and low indices). The game ignores it either way.

### 2.5 PIC verification (looked at every PNG)

`_re/out/images/pic/*.png` (game palette) and `_re/out/images/pic/embedded_palette/*__embedded.png`. **All 26 decode to clean images**:
* 7 level PICs: in-game screenshots of each track (snow Iceland, jungle Brazil, river Canada, Carib beach, Austria bridge, neon Japan, desert USA).
* N/S/H_pangfx, H_pan1/2: HUD sheet — TOTAL TIME / LAP TIME / POS, tacho, turbo gauge, car icons, start lights, HI/LO gear lever.
* N/S/H_signs: arrow road signs, WRONG WAY. Pokal: gold/silver/bronze trophies.
* N_sysgfx: 12 round character icons, 1–6 digits, FUN TRACKS / GAME OVER buttons, Virgin Interactive and UDS logos. N_sysg_2: flags, laurel 1/2/3, PURSUIT CHAMPION.
* Baltazar: 11 car stat cards (bilar/democar, the latter with NOT AVAILABLE overlays), 7 round track maps (trk_spr/demotrk), language flags, IGNITION logo, buttons, MARK (small dot).
Contact sheets: `_re/out/images/_contact_pic_{levels,hud_sys,baltazar}.png`.

---

## 3. Textures (`.TEX`)

### 3.1 Layout

Headerless, uncompressed 8-bit palette indices, **256×256 pages** (65 536 B each), concatenated, row-major, top row first.
* **[CERTAIN]** every TEX slot is sized in 64 KiB units: `add eax,0xFFFF / and eax,0xFFFF0000` at `0x00419D73` (level), `0x00419DEC` (CARS),
  `0x00419F00`, `0x0041A012`, `0x0041A126`, … (GENERAL LIGHT/SMOKE/DARKSMOK/BOOM/…), and the PFM loader addresses the Baltazar
  copies as `texPage[i]` = consecutive 64 KiB pages (frontend note §8, `0x0040CD60`).
* **[INFERRED, strongly]** the 256 width: 64 KiB pages are the only square power-of-two layout, every page rendered at 256 wide is a coherent
  image made of 64×64 / 128×128 / 256×256 tiles with no shear, and the PFM renderer (which uses 8.8 UVs ≤ 254.1 into these pages) produces
  correctly-textured frames.

Loader `0x00419D10` (sizing pass) + `0x0041A4CC..` (read pass):
```
0x00419D28  sprintf("LEVELS\%s%s.TEX") ; _open(,0x8000) ; _filelength
0x00419D73  lea ebp,[eax+0xffff] / and ebp,0xffff0000 / mov [0x54f974],ebp   ; level slot size
0x00419D9B  "%s\CARS.TEX" with "CARS\"  -> [0x5530F4] slot base
0x00419DF9  "GENERAL\" + "LIGHT" + ".TEX" -> [0x5530FC]; SMOKE -> [0x553100]; DARKSMOK -> [0x553104]; BOOM -> ...
0x0041A4CC  add every slot base to the arena base [0x5530F0]
0x0041A5AD  _read(fd, [0x5530F0], levelSize)   ; level TEX at arena+0
0x0041A61B  _read(fd, [0x5530F4], carsSize)    ; CARS.TEX next page boundary, then GENERAL\*.TEX
```
A file whose length is not a multiple of 65 536 leaves a partly-filled last page.

### 3.2 Byte accounting (`tex.py`) [CERTAIN]

| file | size | full pages | tail |
|---|---|---|---|
| `LEVELS/Brazil`, `Canada`, `USA` | 1 048 576 | 16 | — |
| `LEVELS/Austria/AUSTRIA.TEX` | 1 048 768 | 16 | 192 B, all zero |
| `LEVELS/Carib/CARIB.TEX` | 1 048 704 | 16 | 128 B, all zero |
| `LEVELS/Iceland/Iceland.tex` | 1 065 088 | 16 | **64 full rows** of page 16 + 128 zero B |
| `LEVELS/Japan/JAPAN.TEX` | 1 032 192 | 15 | **192 full rows** of page 15 |
| `CARS/Cars.tex` = `Baltazar/data/Menucar.tex` (identical MD5) | 393 280 | 6 | 64 zero B |
| `Baltazar/data/textures/cars.tex` (≠ CARS/Cars.tex) | 393 280 | 6 | 64 zero B |
| `Baltazar/data/textures/canada.tex` (≠ LEVELS, 81 142 B differ) | 1 048 768 | 16 | 192 zero B |
| `GENERAL/{BOOM,DIVSPR,EXSMOKE,LIGHT,SMOKE}.TEX` + Baltazar copies | 65 536 | 1 | — |
| `GENERAL/DARKSMOK.TEX` + Baltazar copy | 32 768 | 0 | 124 rows + 1 024 zero B (a half page) |

Every byte is either page texels, whole tail rows, or trailing zero padding (64/128/192 B — an exporter artefact [INFERRED]). 0 unexplained.
Identical pairs: GENERAL vs `Baltazar/data/textures` for boom/darksmok/exsmoke/light/smoke; divspr differs.

### 3.3 Palette association

| TEX | palette | evidence |
|---|---|---|
| `LEVELS/<lvl>/*.TEX` | own `<lvl>.COL` | [CERTAIN] same level load path sets `[0x54F900]` palette; indices used are exactly the level range 0–159; foreign COL → error 37–104 |
| `CARS/Cars.tex` | whichever level COL is active | [CERTAIN] uses only 0 + 160–255, identical in all COLs |
| `GENERAL/*.TEX` | active level COL | [CERTAIN] loaded in the same arena; **but** LIGHT (46–63,129–143), SMOKE (46–49,155–158), DARKSMOK (37–44) use level-specific indices → their colour changes per track or they are drawn through a SHD/TAB shade table as intensities [INFERRED, open] |
| `Baltazar/data/textures/*`, `Menucar.tex` | menu.col (= Canada palette) | frontend PFM renderer, see §8 |

### 3.4 Verification

`_re/out/images/tex/<file>/pageNN.png` (+ `pageNN_partial_*`), per-file sheets `<file>__sheet.png`,
contact sheets `_re/out/images/_contact_tex_{levels,other}.png`. **All 23 files are plausible**: Canada — log cabins, red barns
with moose head, maple-leaf banners, river water, rock faces, wheat; Japan — neon signs; Iceland — snow/ice rock;
Carib — water, palms, signage; USA — wooden mine planks; Cars — car body/grille/lamp textures (school bus, police lights, flames);
GENERAL — explosion frames, smoke puffs, sparks/lens flares arranged in 64×64 cells. No page looks like noise or sheared.

---

## 4. `menubkg.dat` [CERTAIN layout, INFERRED semantics of individual cells]

Loader `0x00401000` (single caller `0x004180E2`, inside menu init `0x00417EA0`, i.e. under SYS.COL):
```
0x00401073  push "MenuBkg.dat" / call fopen(,"rb")
0x00401088  push 1 / push 0x9358 / call calloc          -> [0x4BCD50]
0x004010A3  fread(buf, 0x9358, 1, f) ; fclose
0x004010B6  build 256-entry remap 0x4BCC48 = identity, then pairs c1->f5 c3->bd c4->be d5->b4 d6->bb fd->bc c0->bd c2->f4
```

| off | size | contents |
|---|---|---|
| 0x0000 | 37 696 | **124 cells × 304 B = 16×19 px** (offsets used by `0x004011C0` are all multiples of 0x130: `0x130, 0x260, 0x390, 0x4C0+0x260·n, 0x23A0−0x260·n, 0x44E0, 0x6880, 0x8E80, 0x8FB0, 0x90E0, 0x9210`); blits are `push 0x20 / push 0x13` = 32×19 (two adjacent cells) |
| 0x9340 | 24 | palette index per background band: `mov al,[eax+edi+0x9340]` (edi < 0x18) → `0x004016D0` row fill |

Blitter `0x00401730(src, w, h, dst, pitch)`: starts at `src + (h−1)·w` and walks rows upward (`sub eax,ecx`) while writing downward,
skips value 0 (`test dl,dl / je`), optionally remaps through `0x4BCC48` (highlight state `[0x4BCD54]`).
So cells are stored bottom-up relative to the screen.

Accounting: 37 696 + 24 = 37 720 = file size, 0 remainder. Render: `_re/out/images/menubkg/menubkg_tiles32x19_flipped_x3.png`
(cells paired as 32×19, vertically flipped) shows a **coherent animation of a red rounded button end-cap** (the selection "pill" sliding);
the first two and last two pairs are distinct 16-wide pieces. `menubkg_bands.png`, `menubkg_strip16_fileorder.png`.

---

## 5. LFT bitmap fonts ("Font 1.00.2")

### 5.1 Layout [CERTAIN]

| off | type | meaning | code |
|---|---|---|---|
| 0x000 | char[4] | `LFT\0` | `0x00456285 mov edi,0x4BA6D4 ; mov ecx,4 ; repe cmpsb` (err `0x41A`) |
| 0x004 | u16 | version = 100 | `0x004562B2 cmp word [eax+4],0x64` (err `0x424`) |
| 0x006 | u16 | glyph count (see anomalies) | `→ font+0x18` |
| 0x008 | u16 | space advance | `→ font+0x1A`; used for `' '` in text width `0x00456614` |
| 0x00A | u16 | glyph height | `→ font+0x1C`; `desc.h` for every glyph (`0x004563AC movsx ecx,word [edx+0xa]`) |
| 0x00C | u32[224] | glyph data offset relative to 0x84C, index = char − 0x20, `0xFFFFFFFF` = absent | `0x00456381 cmp dword [ebx],-1` |
| 0x38C | u8[768] | RGB palette of the authoring tool — **never read** | (no access between 0x0C+0x380 and 0x68C) |
| 0x68C | i16[224] | glyph width, index = char − 0x20 | `0x00456325 lea edi,[ecx+0x68c]` ; copied ×224 to font+0x480 |
| 0x84C | … | glyph pixels: width·height raw bytes each, row-major, pitch = width | `0x00456363 add eax,0x84c` |

```
0x0045639D  mov byte [ecx+esi+0x63f2fe],1      ; glyph present flag (index char-0x20)
0x004563A5  movsx eax,word [edi]  -> desc+4  (width)
0x004563AC  movsx ecx,word [edx+0xa] -> desc+8 (height)
0x004563C0  mov eax,[ebx] / add eax,[0x84C base] -> desc+0x10 (pixels)
0x004563DE  mov [esp+0x38],ecx     -> desc+0x14 = width (pitch)
0x004563E2  call 0x456d40          ; CreateSprite(0, &desc)
```
Font table: 30 slots × 0x640 B at `0x0063F2E0` (err `0x406` when full). Text width `0x004564D0`: present char adds
`width[c] + font+0x10` (letter spacing); space adds header `+8`.

Load sites: menu init `0x004181D0..0x00418263` (`LoadFont(name,0)` `0x00456420`) — red_dark `[0x552FD0]`, red_lite `[0x552FD4]`,
bluedark `[0x552FD8]`, bluelite `[0x552FDC]`, red_grey `[0x552FE0]`, bluegrey `[0x552FE4]`, mini `[0x552FE8]`, small `[0x552FEC]`;
race HUD `0x0041ACE1..0x0041AF1F` — `FONTS\` + `yellow_s/pos_s/speed_s/yellow/pos/speed/yellow_h/pos_h/speed_h.lft`
(S/normal/H resolution sets [INFERRED], same as PIC prefixes).

### 5.2 Byte accounting (`lft.py`) [CERTAIN]

* 13/18 files: glyph ranges tile the data area exactly (no gaps, no overlaps).
* `bluegrey, bluelite, red_dark, red_grey, red_lite`: **90 orphaned bytes** at data+7200..7289 = the 6×15 `|` glyph; its
  offset entry was cleared to −1 while the pixels stayed (`bluedark.lft` still lists it: count 66, `|` at 7200, width 6). Header count still says 66.
* `small.LFT` / `mini.LFT`: entry for `j` has offset = end of data (6888 / 2536) and **negative width** (−14 / −6) → a zero-pixel
  sentinel; the loader still passes it to CreateSprite with a negative width [CERTAIN that it does; effect unknown].
* Glyph repertoire is game-specific: slots `a`–`j` hold accented capitals (À Ä Ö ~ Ó Ñ É Ü ¿ ¡), `{` `}` hold `(` `)`, `|` holds `i`.
* Pixel statistics: value **229** is the dominant "background" in every font (see §2.2); value 0 also appears in small/POS.

### 5.3 Palette and verification

Menu fonts (red_*/blue_*, small, mini) are loaded right after SYS.COL → SYS.COL [CERTAIN load order; INFERRED on-screen];
HUD fonts (speed/pos/yellow ×3) are loaded with the level → level COL [INFERRED]. Glyphs use 160–255 + 0–6, so any COL gives the same colours
except index 229.
Glyph sheets (`_re/out/fonts/<file>.png`, game palette, value 0 left transparent; `embedded_palette/` variants) — **all 18 legible**:
blue/red menu alphabets with drop shadow (229 = blue/red plate behind), small/mini yellow alphabets, 7-segment SPEED digits,
POS `1st..6th` numerals, YELLOW lap-time digits, RESULT table font.

---

## 6. `fonts/IGNITION.FNT` — debug font [CERTAIN]

Loaded raw at `0x0041AC40` (`FONTS\` + `%s\IGNITION.FNT`) into `[0x00525E58]`; drawn only by `DrawDebugText` `0x004133D0(x, y, str, dst, pitch, font, colourBase)`,
called 14× from the debug overlay `0x0043E9C0` (`"FPS %d"`, `"RECORDING!"`, …) with colourBase `0x14`.

| off | type | meaning | code |
|---|---|---|---|
| 0x000 | u16 | glyph count (56) | not read |
| 0x002 | u16 | height (10) | `0x004133F0 mov ax,[ecx+2]` |
| 0x004 | i16 | extra advance per char (−1) | `0x004133E6 mov si,[ecx+4]` |
| 0x006 | u8[192] | width per glyph index (56 used, rest 0) | `0x00413435 mov cl,[ecx+eax+6]` |
| 0x0C6 | u8[256] | char → glyph index (0xFF none; indexed by *signed* char) | `0x0041342A movsx eax,byte [eax+ecx+0xc6]` |
| 0x1C6 | u32[224] | pixel offset per glyph (56 used, rest 0) | `0x00413439 mov eax,[ebx+eax*4+0x1c6]` |
| 0x546 | 4050 B | pixels, width·10 per glyph, values 0/1/2 | `0x00413448 lea ebx,[eax+ebx+0x546]` |

Draw: `if (v) dst = colourBase + v` (`0x0041347C add cl,al`). Accounting: 0x546 + Σw·h (405·10 = 4050) = 5400 exactly, no gaps/overlaps.
Mapped: space, `!#%&()*+,-./0-9:;<=>?A-Z`. Sheets: `_re/out/fonts/fonts_IGNITION_FNT__values.png`, `…__levelcol_base0x14.png` — legible.

---

## 7. Audio: PAT / XI / WAV / ENGINE.INF

Scope: every `.PAT` (41), `.XI` (8), `.WAV` (81) and `ENGINE.INF` (11) under `CARS/*/SOUND`,
`GENERAL/SOUND/*`, `LEVELS/*/SOUND`, `Baltazar/data/Sound/*`. The format RIFF `smpl` loops,
FT2 XI and GUS GF1 were identified from their structure (magic plus exact field layout plus byte accounting),
and the fields the engine actually uses were confirmed from the loader disassembly.

Tags: **[CERTAIN]** = read from disassembly or exact byte accounting; **[INFERRED]** = deduction.

### A.0 Summary

| format | what it is | encoding | engine rate | loop source | byte accounting | loader |
|---|---|---|---|---|---|---|
| `.PAT` | Gravis Ultrasound GF1 patch `GF1PATCH110`/`ID#000002`, Sound Forge 3.0 export; 1 instrument / 1 layer / 1 sample in all 41 | 8-bit **unsigned** PCM (modes bit1) in all 41; 16-bit path exists | header `sample_rate` (11025 / 22050 / 22100) | `loop_start`/`loop_end` bytes, loop length = end−start; mode flags (loop, bidir) **ignored** | **41/41 fully accounted** | `0x004582B0` |
| `.XI` | FastTracker 2 Extended Instrument v`0x0102` (7x "FastTracker v2.00", 1x "WAV_2_XI (c) by MAZ") | 8-bit signed **delta** PCM in all 8; 16-bit delta path exists | **forced 22050** (relnote/finetune ignored) | sample header loop start/length; loop type bits **ignored** | **7/8**: `01_ME_JO.XI` has **1 trailing byte (0x80 at 0x1AFE)** | `0x004584D0` |
| `.WAV` | RIFF WAVE PCM, mono, 8-bit, all 81 | unsigned 8-bit (engine xors 0x80) | `fmt` rate (11025 x44, 22050 x36, 14385 x1) | first `smpl` loop if any, else whole sample | **81/81 fully accounted** (incl. pad bytes, LIST/cue/smpl) | `0x004587D0` |
| `.RW8` | raw signed 8-bit (no files ship) | signed 8-bit | 22050 | whole sample | n/a | `0x004586A0` |
| `ENGINE.INF` | 4 x 200 signed-byte tables: pitch0, volume0, pitch1, volume1 vs engine revs | raw | — | — | **11/11 fully accounted** (800 = 4x200) | `0x004574A0` (generic whole-file load) at `0x0041FC25` |

No format is compressed. The only transform is XI delta coding, implemented at `0x00458609`, plus sign flips.

### A.1 How sounds are found: directory scan, category/group/slot  **[CERTAIN]**

String → code references (exhaustive byte scan, `_re/tools/bytescan.py`):
`.WAV`@`0x004BA9D4`→`0x00459001`; `.PAT`@`0x004BA9CC`→`0x0045904D`; `.RW8`@`0x004BA9C4`→`0x00459088`;
`.XI`@`0x004BA9C0`→`0x004590C3` (the second hit, `0x00459706`, is an unrelated
`mov [edx+0x4ba9c0]` into a neighbouring table); `GF1PATCH110`@`0x004BA9B4`→`0x004582B1/BC`;
`\*.*`@`0x004BA9DC`→`0x00458E07`, `0x00459147`; `%sSOUND\ROLL|SKID|COLL|BOOST|DIV`, `LEVELS\%sSOUND`,
`%s%s\SOUND`, `%s%s\SOUND\ENGINE.INF`, `"Error while loading sound"`@`0x00499098` → all in `0x0041F9B0`;
`baltazar\data\sound\`@`0x0047E964`→`0x00403D10`.

`LoadSoundDir(path, cat, group)` = **`0x00458E00`**:
```
0x00458E2E  cmp ebp,7 / jg fail                 ; cat   0..7
0x00458E44  cmp [esp+0x254],0x7f / jg fail      ; group 0..127
0x00458E5B  call 0x458d20                        ; free that (cat,group) first
0x00458EBC  call 0x46a1b0                        ; _findfirst(path\*.*)
0x00458ED4  test byte [esp+0x130],0x16 / jne next ; skip hidden|system|subdir
0x00458EE2  cmp byte [esp+0x144],'0' ... '9'     ; name must start with a digit
0x00458F11..0x00458F2E  ebx = ebx*10 + (c-'0')   ; leading decimal = SLOT number
0x00458FB0..  ext = substring from first '.'; call 0x478a40 (upper-case)
            ; repe cmpsb vs ".WAV"(5) ".PAT"(5) ".RW8"(5) ".XI"(4)
0x0045902A  call 0x4587d0(fullpath, cat, group, slot)   ; WAV
0x00459069  call 0x4582b0(...)                           ; PAT
0x004590A4  call 0x4586a0(...)                           ; RW8
0x004590DF  call 0x4584d0(...)                           ; XI
            ; a loader returning != 1 => LoadSoundDir returns 0
```
Unknown extensions (e.g. `ENGINE.INF`, which does not start with a digit anyway) are skipped. Caller
`0x0041F9B0` treats a 0 return as fatal: `push 0x499098 ("Error while loading sound"); call 0x45b5c0; push 1; call 0x4697e0 (exit)`.

| call site | path | cat | group |
|---|---|---|---|
| `0x0041FA20` | `GENERAL\SOUND\ROLL` | 0 | 0 |
| `0x0041FA63` | `GENERAL\SOUND\SKID` | 0 | 1 |
| `0x0041FAA6` | `GENERAL\SOUND\COLL` | 0 | 2 |
| `0x0041FAE9` | `GENERAL\SOUND\BOOST` | 0 | 3 |
| `0x0041FB2C` | `GENERAL\SOUND\DIV` | 0 | 4 |
| `0x0041FB6F` | `LEVELS\<track>\SOUND` | 1 | 0 |
| `0x0041FBD5` (loop over `[0x006192F0]` participants) | `CARS\<model>\SOUND` (model name table `0x00494A70`, 9-byte stride: COOPER, PORSCHE, JEEP, COP, MUSTANG, SCHOOL, VAN, VW, TRUCK, DODGE, NASCAR) | 2 | **participant index** |
| `0x00459140(path, cat)` from `0x00403D1A` | `baltazar\data\sound\` — enumerates **sub-directories** (`attrib & 0x10`, not starting with `.`), group = leading digits of dir name (`0_grp0` → 0), then `0x00458E00(path\dir, cat, group)` at `0x004592DA` | 0 | from dir name |

Registry `0x00459380(cat, group, slot, sample)`: `[0x0050F528 + cat*4]` → malloc(0x200) = 128 group
pointers (`0x00459404`) → each group malloc(0x40) = **16 slot pointers** (`0x0045947B`). The existing
sample in a slot is freed and replaced. **The slot number is never range-checked**, so a file named `16_*` or higher
would write past the 16-entry block. Numbering gaps are legal (Canada has no `05_`, Japan no `07_`).
Playing an empty slot fails quietly: lookup `0x00459330` returns 0 → `0x004579EA` returns 0.

### A.2 In-memory sample object (common to all loaders)  **[CERTAIN]**

`0x00459360(nbytes)`: `p = malloc(nbytes + 0x60); p->data = p + 0x60; p->[+8] = 0`.

| off | meaning | set by |
|---|---|---|
| `+0x00` | data pointer (`this+0x60`) | `0x00459370` |
| `+0x04` | length in **frames** | loaders |
| `+0x08` | loop start (frames) | loaders |
| `+0x0C` | loop length (frames); **0 → replaced by `length − loopStart`** | loaders + `0x0045846F`/`0x00458654`/`0x00458C37`/`0x00458783` |
| `+0x10..+0x4F` | 64-byte "activity" table: mean \|x[n]−x[n+1]\| over each 1/64 of the sample, normalised to ≤255 | `0x00459580` |
| `+0x50` | bits per sample (8/16) | loaders |
| `+0x54` | native rate (Hz) | loaders |
| `+0x58` | `2^32 / blockLen` (`0x00458240`: `div` of 1:0 by n) | `0x004595B1` |
| `+0x5C` | normalisation: `8 − shift` | `0x00459674` |
| `+0x60…` | PCM, **signed** after load | |

The activity table is only read by the mixer's voice-priority pass (`0x00467276..0x004672B4`:
`table[pos*[+0x58]] * rate >> [+0x5C] * (vol>>10) >> 8 * (pan+0x80)`, then a minimum search at
`0x004672F1`). **[INFERRED]** it picks the quietest voice to steal when all 6 are busy.

### A.3 GF1 `.PAT` layout and engine use

File layout (all offsets absolute; valid for the 1-instrument/1-layer/1-sample files that ship; the
reader `pat.py` walks counts generically):

| off | size | field | shipped values | engine |
|---|---|---|---|---|
| 0x00 | 12 | magic `"GF1PATCH110\0"` | all | **checked**: 12-byte copy of `0x004BA9B4` vs buffer, strcmp loop `0x0045832A` |
| 0x0C | 10 | `"ID#000002\0"` | all | – |
| 0x16 | 60 | description (`"This patch saved with Sound Forge 3.0."`) | | – |
| 0x52 | 1 | instruments | 1 | ignored |
| 0x53 | 1 | voices | 14 | – |
| 0x54 | 1 | channels | 1 | – |
| 0x55 | 2 | waveforms | 1 | ignored |
| 0x57 | 2 | master volume | 0xFFFF | – |
| 0x59 | 4 | data size (junk-ish) | | – |
| 0x5D | 36 | reserved | | – |
| 0x81 | 2 | instrument id | 0 | – |
| 0x83 | 16 | instrument name `"Unnamed Patch"` | | – |
| 0x93 | 4 | instrument size | | – |
| 0x97 | 1 | layers | 1 | ignored |
| 0x98 | 40 | reserved | | – |
| 0xC0 | 1 | layer duplicate | 0 | – |
| 0xC1 | 1 | layer | 0 | – |
| 0xC2 | 4 | layer size | | – |
| 0xC6 | 1 | samples | 1 | ignored |
| 0xC7 | 40 | reserved | | – |
| 0xEF | 7 | wave name `"PATCH"` | | – |
| 0xF6 | 1 | loop fractions (4+4 bits) | 0 | ignored |
| 0xF7 | 4 | **data size, bytes** | | `[esp+0x10F]` → alloc, fread, `+0x04` |
| 0xFB | 4 | **loop start, bytes** | | `[esp+0x113]` → `+0x08` |
| 0xFF | 4 | **loop end, bytes** | | `[esp+0x117]`; `+0x0C = end − start` |
| 0x103 | 2 | **sample rate** | 11025/22050/22100 | `[esp+0x11B]` → `+0x54` |
| 0x105 | 4 | low freq (mHz) | 8176 | – |
| 0x109 | 4 | high freq (mHz) | 12544000 | – |
| 0x10D | 4 | root freq (mHz) | 130817..277184 | **ignored** |
| 0x111 | 2 | tune | 0 | – |
| 0x113 | 1 | balance | 7 | – |
| 0x114 | 6 | envelope rates | | – |
| 0x11A | 6 | envelope offsets | | – |
| 0x120 | 3 | tremolo speed/rate/depth | 0 | – |
| 0x123 | 3 | vibrato speed/rate/depth | 0 | – |
| 0x126 | 1 | **modes**: b0 16-bit, b1 unsigned, b2 loop, b3 bidir, b4 reverse, b5 sustain, b6 envelope, b7 clamped | 0x62 / 0x66 / 0x6E | only **b0** (`0x00458408`) and **b1** (`0x00458453`) |
| 0x127 | 2 | scale frequency | 60 | – |
| 0x129 | 2 | scale factor | 1024 | – |
| 0x12B | 36 | reserved | | – |
| 0x14F | data size | PCM | | fread straight after the 0x14F-byte header block |

Loader `0x004582B0` **[CERTAIN]**:
```
0x004582E2  fopen(path,"rb")                   ; mode string @0x0047C040 = "rb"
0x00458309  fread(hdr, 0x14F, 1, fp)           ; ALL headers in one gulp, first sample only
0x00458370  ebx = [esp+0x10F]  ; data_size
0x00458378  call 0x459360(ebx) ; alloc
0x004583A1  fread(data, 1, ebx, fp) ; must return ebx
0x004583ED  mov [esi+4],ebx ; [esi+8]=loop_start ; [esi+0xc]=loop_end-loop_start
0x00458408  mov al,[esp+0x13e] ; and eax,1 ; lea ecx,[eax*8+8] ; mov [esi+0x50],ecx
0x0045841E  mov ax,[esp+0x11b] ; mov [esi+0x54],eax
0x00458429  if bits==16: len, loopStart, loopLen /= 2        (cdq/sub/sar 1)
0x00458453  test byte [esp+0x13e],2 ; je ; call 0x459530    ; unsigned -> xor 0x80 / xor 0x8000
0x00458467  call 0x459580                                    ; activity table
0x0045846F  if loopLen==0: loopLen = len - loopStart
0x00458497  call 0x459380(cat, group, slot, smp)
```
Consequences **[CERTAIN]**: (a) multi-sample or multi-layer patches would silently use sample 0.
(b) The loop/bidir/sustain/envelope flags are ignored. Whether a voice loops is decided by the **play call**
(§A.6), and the loop *region* is always defined, because a non-looped file gets loop = whole sample.
(c) Sound Forge stored a full loop as `end = size−1`, so the engine's loop region is `[0, size−1)` and the
last byte never plays while looping. (d) `root_frequency` is ignored, so `SKID/03_s_san.pat` and
`04_s_sno.pat` (identical PCM and loop, differing only in root frequency 158745 vs 250088 mHz) play
identically in-engine unless their play calls pass different rates. **[INFERRED]** for the last point.

### A.4 FT2 `.XI` layout and engine use

| off | size | field | engine |
|---|---|---|---|
| 0x000 | 21 | `"Extended Instrument: "` | **not checked** |
| 0x015 | 22 | instrument name | – |
| 0x02B | 1 | 0x1A | – |
| 0x02C | 20 | tracker name | – |
| 0x040 | 2 | version 0x0102 | – |
| 0x042 | 96 | note→sample map | – |
| 0x0A2 | 48 | volume envelope (12 x u16 x,y) | – |
| 0x0D2 | 48 | panning envelope | – |
| 0x102..0x10F | 14 x u8 | vol/pan point counts, sustain, loop start/end, types; vibrato type/sweep/depth/rate | – |
| 0x110 | 2 | volume fadeout | – |
| 0x112 | 22 | reserved | – |
| 0x128 | 2 | number of samples (all 1) | **ignored** |
| 0x12A | 4 | **sample length, bytes** | `+0x04` |
| 0x12E | 4 | **loop start, bytes** | `+0x08` |
| 0x132 | 4 | **loop length, bytes** | `+0x0C` |
| 0x136 | 1 | volume (64) | – |
| 0x137 | 1 | finetune (−27 / 17) | **ignored** |
| 0x138 | 1 | **type**: b0-1 loop (0 none, 1 fwd, 2 ping-pong), b4 16-bit | only **b4** |
| 0x139 | 1 | panning (128) | – |
| 0x13A | 1 | relative note | **ignored** |
| 0x13B | 1 | reserved | – |
| 0x13C | 22 | sample name (e.g. `" FORD_P~1.WAV"`) | – |
| 0x152 | length | delta-coded PCM | fread + decode |

Loader `0x004584D0` **[CERTAIN]**:
```
0x004584F7  push 0 / push 0x12a / push edi / call 0x469d40   ; fseek(fp, 0x12A, SEEK_SET)
0x00458521  fread(hdr, 0x28, 1, fp)
0x00458549  alloc(length) ; fread(data, 1, length)
0x004585C6  mov al,[esp+0x1a] ; and al,0x10 ; shr al,1 ; movsx ; add eax,8 ; -> +0x50
0x004585D8  mov dword [esi+0x54], 0x5622          ; 22050 Hz, hard-coded
0x004585DF  if 16-bit: len, loopStart, loopLen /= 2
0x00458611  8-bit delta:  xor edx,edx ; L: add dl,[edi] ; mov [edi],dl ; inc ; loop
0x0045862B  16-bit delta: add dx,[ecx] ; mov [ecx],dx ; add ecx,2 ; loop
0x0045864C  call 0x459580 ; if loopLen==0: loopLen = len - loopStart ; register
```
No sign flip: FT2 deltas decode to signed PCM. Byte accounting: 7 files are exact. `GENERAL/SOUND/COLL/01_ME_JO.XI`
(written by WAV_2_XI, not FT2) is 6911 bytes = 0x152 + 6572 + **1 stray byte `0x80` at 0x1AFE**, which the
engine never reads.

### A.5 `.WAV` and `.RW8`

Loader `0x004587D0` **[CERTAIN]**:
```
0x00458818  fread(riffhdr, 1, 12, fp) == 12
0x0045883B  cmp [esp+0x18],'RIFF' ; cmp [esp+0x20],'WAVE'
            loop while consumed < riffSize-4:
0x00458877    fread(ckhdr, 8, 1)
0x0045888C    'data' -> alloc(size); fread(data,1,size)
0x00458899    'fmt ' -> fread 16 bytes; fseek(size-16, SEEK_CUR)
0x004588A2    'smpl' -> fread min(size,0x54); fseek rest
0x004588AD    'fact' -> fread 4; fseek rest
0x004588B8    other  -> fseek(size, SEEK_CUR)
0x00458A05    consumed += size; if consumed odd: fseek(1)            ; RIFF pad byte
0x00458B63  require 'fmt ' seen ; 0x00458B83 wFormatTag==1 ; 0x00458BA1 nChannels==1
0x00458BBF  +0x54 = nSamplesPerSec
0x00458BC6  bits = (wBitsPerSample+7)&0xF8 ; +0x04 = dataBytes*8/bits ; +0x50 = bits
0x00458BE4  if smpl && cSampleLoops!=0: +0x08 = loop[0].dwStart ; +0x0C = loop[0].dwEnd - dwStart
            else: +0x08 = 0 ; +0x0C = length
0x00458C1F  if bits==8: call 0x459530   ; unsigned -> signed (xor 0x80)
```
Loop length is `dwEnd − dwStart`, but `smpl` `dwEnd` is inclusive, so it is one frame short. No shipped
WAV has a `smpl` loop, so this does not matter for the shipped data.

Census **[CERTAIN]**: 81 files, all `WAVE_FORMAT_PCM`, mono, 8-bit. Rates: 11025 Hz x44 (all LEVELS),
22050 Hz x36, 14385 Hz x1 (`GENERAL/SOUND/Boost/09_schoo.wav`). Chunk sequences: `fmt data LIST(INFO)` x58,
`fmt data LIST(INFO) cue LIST(adtl)` x20 (Sound Forge regions/labels), `fmt data smpl` x2
(`Boost/00_boost.wav`, `COLL/04_EXPLO.wav`; `smpl` with 0 loops, unity notes only), `fmt data` x1
(`Baltazar/data/Sound/0_grp0/3_intro.wav`, 808 499 PCM bytes). The RIFF size equals file size − 8 in all;
odd data chunks carry the pad byte. **All 81 fully accounted.** The engine uses only `fmt` + `data`.

`.RW8` loader `0x004586A0` **[CERTAIN]**: `_open(path, O_BINARY 0x8000)`, `_filelength` (`0x004787C0`),
`_close`, alloc, `fopen "rb"`, fread whole file; length = file size, loop `[0,len)`, 8-bit, 22050 Hz,
**no sign flip**, so the raw data must be signed. No `.RW8` ships.

### A.6 Playback semantics that the formats rely on  **[CERTAIN]**

Play request block (filled by `0x0043E710`, submitted to `0x004579B0`):
`+0 cat, +4 group, +8 slot, +0xC loopCount, +0x10 volume (= ftol(arg5 × distance gain)), +0x14 rate override, +0x18 pan`.
`0x004579B0` takes a voice from the 0x24-byte pool at `0x0063CAA4`:
```
0x00457A14  voice.sample = smp ; voice.pos = voice.frac = 0 ; voice.vol(+0xC) = req.vol
0x00457A2D  voice.rate(+0x10) = req.rate ? req.rate : smp[+0x54]      ; native rate if 0
0x00457A4F  clamp rate to 0x27100 (160000)
0x00457A40  voice.loopCount(+0x14) = req[+0xC]
0x00457A92  handle = (voiceIndex<<16) | serial(word [0x50ED48+2*i])
```
Mixer advance `0x004671E2`:
```
call 0x465d90(rate, &pos, &frac, outRate)
0x004671FE  if (loopCount) while (pos >= loopStart+loopLen) { pos -= loopLen; if(--loopCount==0) break; }
0x0046721F  if (pos >= smp.length) voice.rate = 0      ; voice ends; list walker 0x467136 frees it
```
So **the files' loop flags are irrelevant**. Looping is `loopCount` from the caller (−1 ≈ forever, used by
engine voices at `0x0041FD46`), over the region `[loopStart, loopStart+loopLen)`. **Ping-pong loops are not
supported**: 15 PATs (modes 0x6E: the 10 car PATs, `COLL/02_gniss`, `SKID/03_s_san`, `SKID/04_s_sno`,
`Austria/01_Klock`, `Austria/03_Kano`) and `SKID/02_s_mud.xi` declare bidirectional loops, but all play forward.
**[INFERRED]** `+0x10` is in Hz, consistent with the default being the sample's native rate and the
160 kHz clamp; the step routine `0x00465D90` was not fully reversed.

### A.7 `ENGINE.INF`

Load **[CERTAIN]**: `sprintf("%s%s\SOUND\ENGINE.INF", "CARS\", model)` at `0x0041FC18`;
`0x004574A0` = whole-file load into a malloc'd buffer (error codes 0x7EE/0x7F8/0x802/0x7D0 in `[0x4BAB34]`); failure prints `0x00498A3C` and exits.
Copy loop `0x0041FC4F..0x0041FCAC` (`cmp eax,0xC8`): file[0..199] → car`+0x658`, file[200..399] → `+0x720`,
file[400..599] → `+0x7E8`, file[600..799] → `+0x8B0` (car struct = `[0x005DAFFC] + i*0x484C`).
Buffer freed via `0x0045B000`.

Two looping voices are started per car **[CERTAIN]**, handles stored at car`+0x650`/`+0x654`:
`0x0041FD51 play(cat 2, group car, slot 0, loop −1, 0x10000, rate 5000, 0, x, y)` and `0x0041FDA7` (slot 1).

Per-frame consumer **[CERTAIN]** `0x00445524..0x00445658`:
```
0x00445524  fld [car+0x288] ; ftol ; esi = eax*2 ; clamp esi to [1,199]
0x00445554  movsx ecx, byte [car + esi + 0x658]     ; T0[i]
            ebp = ecx*600 + 20000                    ; lea chain 5*8*3*5 = 600, +0x4E20
0x00445582  voice(car+0x650).rate = ebp
0x004455A1  movsx eax, byte [car + esi + 0x71F]     ; T1[i-1]  (0x71F = 0x720-1)
            voice(car+0x650).vol = ftol(T1 * g * 650.0)          ; [0x47ACF0] = 650.0
0x004455DC  movsx edx, byte [car + esi + 0x7E8]     ; T2[i]
0x0045561A  voice(car+0x654).rate = T2*600 + 20000
0x00445639  movsx eax, byte [car + esi + 0x8AF]     ; T3[i-1]
0x00445658  voice(car+0x654).vol = ftol(T3 * g * 650.0)
```
`g` = `(1500 − sqrt(dx²+dy²)) × (1/1500)` clamped at 0 (`0x004454A2..0x004454E0`, constants `0x47ACD8`=1500.0,
`0x47ACE0`=1/1500), so T=100 at distance 0 gives 65000 ≈ 0x10000. **The volume tables are percent.**
The pitch table maps 0..100 to 20 000..80 000 (Hz, [INFERRED unit]), i.e. 0.91x..3.63x for a 22 050 Hz sample.
The volume tables are read one index lower than the pitch tables (CERTAIN, looks like an original off-by-one).

Index source **[CERTAIN code / INFERRED name]**: car`+0x288` is an "engine revs" double, updated at
`0x00442BB3..0x00442D54` as `x += (target − x) × [0x47AAA0]`, with target = `speed / gearRatio[[car+0x27C]]`
(`fild [gear*0x20 + 0x492998]; fdivp`). Index = revs × 2, so revs ≈ 0..100.

Shipped tables **[CERTAIN]** (signed bytes, in practice 0..100). There are 7 distinct files:
{JEEP, School, Truck, Van} identical; {Dodge, Mustang} identical; Cop, COOPER, Nascar, PORSCHE, VW unique.
The common shape is a cross-fade. `pitch0` rises (e.g. Cop 14→100) and `volume0` holds 100, then fades to 0 by index
117..145. `pitch1`/`volume1` are 0 at low revs and rise from index ~16..67 / ~53..57 (low-rev sample `00_*`,
high-rev sample `01_*`). PORSCHE is the exception: `volume0` never drops below 74.

### A.8 Tools and outputs

* `_re/tools/bytescan.py` — exhaustive little-endian dword scan (desync-proof reference counts).
* `_re/tools/dispscan.py` — every `[reg+disp]` access for given displacements (struct-field xrefs).
* `_re/tools/formats/acct.py` (byte accounting), `pat.py`, `xi.py`, `wav.py`, `engine_inf.py` — each has
  `parse()` with full accounting and `engine_view()` that mirrors the loader arithmetic above. The CLI
  prints both.
* `_re/tools/formats/convert_audio.py` — runs everything and writes `_re/out/audio/`:
  49 × `<dir_path>_<name>.wav` (16-bit PCM mono, at the **engine** rate) + 49 × `.json` sidecar (file-declared
  loop, engine loop region, metrics), `_report.txt`, `_index.json`. A `smpl` chunk (inclusive end,
  type 0 fwd / 1 ping-pong) is written for the 23 sources that declare a loop (16 PAT, 7 XI).
  All 49 outputs re-parse as fully accounted, 16-bit, mono, correct rate and frame count, with loops inside the sample.

Examples:

| source → output | bits/rate (engine) | frames | declared loop (frames, incl. end) | engine loop region |
|---|---|---|---|---|
| `CARS/Cop/SOUND/00_a.pat` → `CARS_Cop_SOUND_00_a.wav` | 8-bit unsigned → 22050 Hz | 38912 | 0..38910 ping-pong (modes 0x6E) | [0, 38911) forward |
| `GENERAL/SOUND/SKID/01_s_gra.pat` | 8u / 22050 | 41217 | 9471..16893 fwd (0x66) | [9471, 16894) |
| `GENERAL/SOUND/Boost/03_siren.pat` | 8u / 22100 | 15777 | none (0x62) | [0, 15777) |
| `LEVELS/Austria/Sound/01_Klock.pat` | 8u / 11025 | 24256 | 0..24254 ping-pong | [0, 24255) |
| `GENERAL/SOUND/ROLL/00_R_ASF.XI` → `GENERAL_SOUND_ROLL_00_R_ASF.wav` | 8-bit delta / 22050 (forced) | 59435 | 21680..57372 fwd | [21680, 57373) |
| `GENERAL/SOUND/SKID/02_s_mud.xi` | 8d / 22050 | 11163 | 3728..10751 ping-pong | [3728, 10752) forward |
| `GENERAL/SOUND/COLL/01_ME_JO.XI` | 8d / 22050 | 6572 | none | [0, 6572) |

Not-noise check: lag-1 autocorrelation (ac1), zero-crossing rate, and spectral flatness (1024-bin Hann, 1 = white
noise). Each correct decode was compared with a deliberately wrong one (PAT: signedness flag inverted; XI: no delta decoding).
**The correct decode has lower flatness in 49/49 files and higher ac1 in 47/49.** The two ac1 exceptions are
noise-like sounds (`COLL/02_gniss.pat` flatness 0.68 vs 0.91; `USA/07_gam.pat` 0.046 vs 0.405), where flatness still
separates clearly. Examples: `Cop/00_a.pat` ac1 0.996 / flat 0.007 (wrong 0.897 / 0.185);
`ROLL/01_R_GRA.XI` 0.860 / 0.227 (raw deltas 0.156 / 0.810); `COLL/01_ME_JO.XI` 0.9997 / 0.0001 (0.891 / 0.107);
`ROLL/00_R_ASF.XI` 0.991 / 0.0009 (0.949 / 0.054).

Duplicates **[CERTAIN, md5]**: `00_a.pat` is identical for JEEP/School/Truck (Van's differs, quieter, peak 0.34);
`01_a.pat` is identical for JEEP/School/Truck/Van; `01_a.wav` is identical for Dodge/Mustang/Nascar.
**[INFERRED]** `ROLL/01..04` XI are the same sample: same size, header and decoded metrics.

### A.9 Open questions

1. **Which in-game sounds loop.** File flags are ignored and the loop count comes from each `0x0043E710` caller (~60 sites)
   or other `0x004579B0` users. Only the engine voices (−1) were checked. This needs a per-site table of
   (cat, group, slot, loopCount, rate override).
2. The unit of voice `+0x10` (Hz assumed) and how `0x00465D90` converts it to a step at the 22 050 Hz output.
3. The physical meaning and scale of car `+0x288` (revs), and the gear-ratio table `0x00492998`. Belongs with the physics notes.
4. The 16-bit PAT/XI/WAV paths and the `.RW8` path are implemented but no shipped asset exercises them.
   WAV loops come from `smpl` in frames, while PAT/XI halve byte counts. Both are consistent but untested by data.
5. Whether a rebuild should reproduce the original quirks: forward-only playback of ping-pong loops, loop lengths
   one frame short (PAT `end−start`, WAV inclusive `dwEnd`), and ENGINE.INF volume tables read at `i−1`.
6. Menu sounds (`Baltazar/data/Sound/0_grp0`, cat 0 group 0) occupy the same (cat, group) as race `GENERAL\SOUND\ROLL`.
   Each `LoadSoundDir` frees its target group first (`0x00458E5B`), so whichever loads last wins. The menu→race ordering was not traced.

---

## 8. Frontend data: CDP / PSQ / PFM

All three live in `Baltazar/data/` and belong to the front end: **CDP** is the full-screen intro
movie format (Virgin / UDS / Ignition logo sequences). **PFM + PSQ** make up the animated
**main-menu background**: a pre-projected 2D polygon "movie" of a Canada-track fly-through that the
software rasterizer re-renders every tick. Byte accounting is exact for all 8 files:
**0 unaccounted bytes**.

Tags: **[CERTAIN]** = disassembly and/or exact byte accounting; **[INFERRED]** = deduced.

### CDP — intro animations (`Ign1.cdp`, `Ign2.cdp`, `Ign3_0..3.cdp`)

**Loader / player.**
- Filename table at `0x0047DC50`: 6 `char*`, in the order ign1, ign2, ign3_0, ign3_1, ign3_2, ign3_3. Its only code reference is `0x00409B01`. **[CERTAIN]**
- Load loop `0x00409AFF..0x00409B39`: `size = FileSize(name)` (`0x00457630`), `malloc(size)`, `ReadFile(name, buf, size, 0)` (`0x00457420`). Each whole file goes into `[0x004BE880 + 4*i]`. They are freed at `0x0040373B`. **[CERTAIN]**
- Player struct at `0x004BE5B8` (all offsets relative to it):
  - `+0x00` file buffer
  - `+0x04` destination = `[0x004BE840]` (the 320×200 menu framebuffer)
  - `+0x08` frames
  - `+0x0A` loop flag
  - `+0x0C` width
  - `+0x0E` height
  - `+0x10` palette ptr
  - `+0x14` u16 current frame
  - `+0x16` open flag
  - `+0x1A` first-frame ptr
  - `+0x1E` current-frame ptr
- `CDP_Open` `0x00412580` checks the magic and version and fills the struct. **[CERTAIN]**
- `CDP_DecodeFrame` `0x00412610` → `0x00499ABC`. This is hand-written x86 **stored in `.data`** (the section is RW, not X, just like the `code` section). It is a byte-code interpreter with a 256-entry jump table at `0x00499B95`. **[CERTAIN]**
- The main loop at `0x00402E7E` decodes one frame per tick. When the decoder returns 0 it moves on to the next file index `[0x004BE56C]` and re-opens; after file 6 the intro ends. **[CERTAIN]**
- When the frame index `+0x14 == 2`, `0x00412560` calls `SetGamePalette(file+0x10)`, so the palette is applied after the second frame. **[CERTAIN]**
- Frame pacing: the accumulator `[0x004BE560] += dt` is compared with `2.393103448` (`[0x00479078]`). dt is `[0x0049373C]` ms × `[0x00479110]` (`0x00403DF0`). Result ≈ one frame per 2.39 dt units. **[INFERRED]** that this is ~15 fps; the unit of dt was not pinned down.

**Header** (little-endian):

| off | type | value in files | meaning |
|---|---|---|---|
| 0x00 | char[4] | `CDP\0` | magic, checked byte by byte (`0x00412598`) **[CERTAIN]** |
| 0x04 | u16 | 100 | version, must be 100 (`0x004125AB`) **[CERTAIN]** |
| 0x06 | u16 | 150 / 129 / 97 / 60 / 40 / 46 | frame count → struct+0x08 **[CERTAIN]** |
| 0x08 | u16 | 0 | loop flag → +0x0A. At the end of the stream the decoder rewinds to frame 0 and returns 1 if flag == 1, otherwise 0 (stop) **[CERTAIN]** |
| 0x0A | u16 | 320 | width → +0x0C. Never read by the decoder **[CERTAIN]** |
| 0x0C | u16 | 200 | height → +0x0E. Never read **[CERTAIN]** |
| 0x0E | u16 | 256 | not copied, not read. Colour count? **[INFERRED]** |
| 0x10 | u8[768] | | RGB palette 0..255, used verbatim (no `<<2`) **[CERTAIN]** |
| 0x310 | … | | frame stream **[CERTAIN]** |

**Frame byte code** (from `0x00499AD8..0x00499B94`). The output is one linear 64000-byte buffer that **persists between frames**: skips leave the previous pixels in place, so the format is delta-coded. The decoder does not check bounds. **[CERTAIN]**

| opcode | operands | action | handler |
|---|---|---|---|
| `00..F5` | — | write the opcode itself as the pixel | `0x00499B54` |
| `F6` | u8 v | write v (escape for values F6..FF) | `0x00499AD8` |
| `F7 F8 F9 FA` | — | skip 2 / 3 / 4 / 5 pixels | `0x00499AE4..` |
| `FB` | u8 n | skip n | `0x00499B08` |
| `FC` | u16 n | skip n | `0x00499B18` |
| `FD` | u8 c, u8 v | run of c×v (c = 0 means 256: `dec dl; jne`) | `0x00499B2B` |
| `FE` | u16 c, u8 v | run of c×v (c = 0 means 65536) | `0x00499B3E` |
| `FF` | — | end of frame: save ptr, frame++, handle rewind/loop | `0x00499B5D` |

```
00499B2B  mov dx,[esi] / add esi,2
00499B31  mov [edi],dh / inc edi / dec dl / jne 0x499b31      ; FD: count=dl, value=dh
00499B3E  mov dx,[esi] / add esi,2 / mov al,[esi] / inc esi
00499B47  mov [edi],al / inc edi / dec dx / jne 0x499b47       ; FE: count16, value
00499B5D  mov edi,[0x499f95] / mov [edi+0x1e],esi / inc word [edi+0x14]
          cmp ax=[edi+8],[edi+0x14] / jne ret1 ; rewind; cmp word [edi+0xa],1 ; je ret1 ; ret0
```

**Byte accounting** (`_re/tools/formats/cdp.py`): all 6 files decode exactly `header.frames` frames, and each stream ends precisely at EOF.

| file | frames | stream end | unaccounted | max write |
|---|---|---|---|---|
| Ign1 | 150 | 0x775A3 = 488867 | 0 | 64000 |
| Ign2 | 129 | 0xCCE12 = 839186 | 0 | 64000 |
| Ign3_0 | 97 | 0xA6704 = 681732 | 0 | 64000 |
| Ign3_1 | 60 | 0xBFCF1 = 785649 | 0 | 64000 |
| Ign3_2 | 40 | 0xBB6B3 = 767667 | 0 | 64000 |
| Ign3_3 | 46 | 0x53B73 = 342899 | 0 | 64000 |

No frame writes past 64000 pixels. Frame 0 of every file is `FE 00FA vv` (fill all 64000) followed by empty `FF` frames. **[CERTAIN]**

**Palette:** each CDP uses its **own embedded palette**. **[CERTAIN]**

**Visual check:** every frame is a clean image, not noise. `_re/out/images/cdp/<name>/NNN.png` holds all 522 frames; `<name>_sheet.png` are contact sheets.
- Ign1: green car flash, then the Virgin Interactive logo.
- Ign2: tyres bouncing, then the UDS logo.
- Ign3_0..2: car crash with smoke on a white set.
- Ign3_3: the IGNITION logo.

Backgrounds are near-white because palette index 10 = (251,251,251), which is what frame 0 fills with.

### PSQ — `Default.psq` (playback order for test.pfm)

**Loader.** `0x0040CA88`: the whole file goes into `[0x004BE630]`, with its size in `[0x004BE760]`. **[CERTAIN]**

**Consumer.** `0x0040C740`, called once per menu-background tick from `0x0040A474`. The tick fires when accumulator `[0x004BF570] += [0x004BEB40]` reaches `2.1176470588` (`[0x004791C8]` = 36/17). **[CERTAIN]** for the code; the effective rate ≈ 17 fps is **[INFERRED]**.

```c
rec = &psq[seg*3];                 // seg = [0x4BE5DC], dword stride 4 -> 12-byte records
frame = rec[0] + sub;              // sub = [0x4BE75C]
if (++sub == rec[1]) { sub = 0; if (++seg == psqSize/12) seg = 0; }
primList = PFM_BuildFrame(frame);  // 0x00410440 -> [0x498730]; render via 0x0040C720 -> 0x0044F0E9
```

| off | type | meaning |
|---|---|---|
| 0 | u32 | first PFM frame of segment **[CERTAIN]** |
| 4 | u32 | frame count **[CERTAIN]** |
| 8 | u32 | never read (all 0 in the file) **[CERTAIN]** (only `+0` and `+4` are dereferenced; `[0x004BE630]` has 4 refs: alloc, load, use, free) |

**Accounting:** 288 = 24 × 12 records, 0 remainder. The segments cover PFM frames 0..1123 exactly once, but reorder some of them: 251–256 / 243–250 / 227–242, 567–611 / 509–566, and 681–705 / 673–680. Dump: `_re/out/data/default_psq.txt`. **[CERTAIN]**

### PFM — `test.pfm` (pre-projected menu-background polygon movie)

**Loader.** `0x0040CADE` reads the whole file into `[0x004BE5F8]`. It then calls `ScriptPlayer_Init(pfm, 0x004BE660 /*texture page ptrs*/, 0x004BE860 /*table list*/)` at `0x0040FFE0`. That function creates a memory pool named `"Script Player"`, and copies the 0-terminated table list (max 16) into `0x004C4C68`. **[CERTAIN]**

**Chunk container** (walk `0x0041003E..0x004100B2`): records of `{char[4] tag; u32 size; u8 data[size]}`, next = `this + 8 + size`, until the tag is `lixx`. Tags are compared as dwords (`cmp eax,0x3130696C` = `li01`). An unknown tag makes init return −1. **[CERTAIN]**

| tag | count | payload | first-pass handler |
|---|---|---|---|
| `li01` | 1124 | per-frame vertex table: `size/4` × (i16 x, i16 y) | `0x00410210`: tracks max(size×2) |
| `li00` | 1124 | per-frame display list (byte stream, `FF`-terminated) | `0x004101F0`: tracks max(size×6); frames++ |
| `li02` | 1 (25552 B) | 1597 × 16-byte texture-triangle records | `0x00410230` |
| `li03` | 1 (944 B) | 59 × 16-byte sprite records | `0x00410330` |
| `lixx` | 1 (size 0) | end | |

**Frame index.** `0x00410170` builds the frame index `[0x004C4C50]` (12 B/frame). Each `li01` sets `(ptr, size/4)`; the next `li00` stores `{li00 ptr, li01 ptr, li01 size/4}`. The file strictly alternates `li01, li00`, frame 0..1123. **[CERTAIN]**

**PFM_BuildFrame(frame)** `0x00410440`:
- Copies `2 × nverts` i16 values, sign-extended and `<<4`, into `[0x004C4C60]` (`0x00410475`).
- Walks the display list: `u8 type, u8 count`, then `count` records. `type−7` goes through the index table `0x0041090C` = `[0,7,7,7,1,7,2,7,3,7,4,5,6]` and jump table `0x004108EC`. Any other type returns 0.
- Output: a 0-terminated array of primitive pointers `[0x004C4C5C]`. Each primitive starts with a dword `type`, and these are the engine's global primitive IDs. The renderer `0x0044F17D` dispatches `jmp [type*4+0x004ABE10]`: 7→`0x00455648`, 11→`0x00451747`, 13→`0x0044F1FB`, 15→`0x00450693`, 17→`0x00452800`, 18→`0x00452050`, 19→`0x00454604`. **[CERTAIN]**

| type | rec size | fields | primitive built | meaning |
|---|---|---|---|---|
| 7 | 8 | i16 li03 idx, u16 p, u16 q, i16 vertex | `+4 = &li03[idx]`, `+0x14 = p<<8`, `+0x20 = q<<8`, x, y | sprite / billboard (p,q = scale **[INFERRED]**) |
| 11 | 5 | i16 v0, i16 v1, u8 colour | 2 vertices + colour | line **[INFERRED]**; not used in the file |
| 13 | 7 | — | skipped (`edi += 7*n`) | not used in the file |
| 15 | 7 | i16 v0,v1,v2, u8 colour | | flat triangle |
| 17 | 8 | i16 v0,v1,v2, i16 li02 idx | +mat ptr, +page ptr | textured triangle |
| 18 | 9 | i16 v0,v1,v2, i16 li02 idx, u8 table | +`table[idx]` | textured triangle through lookup table |
| 19 | 8 | i16 v0,v1,v2, u8 colour, u8 table | +colour, +`table[idx]` | flat triangle through lookup table |

**li02 record** (16 B, `0x00410284..0x00410312`): `u16 u0,v0,u1,v1,u2,v2; u32 page`. The page is clamped: `>30` or `<0` → 0. It is resolved to `texPage[page]` and stored in a 0x1C-byte material. **[CERTAIN]** UVs are 8.8 fixed-point texels (max 65044 ≈ 254.1). **[INFERRED]**, confirmed by the render.

**li03 record** (16 B, `0x00410381..0x00410431`): `i16 a, i16 b` (<<8, sprite half-extent/anchor), `u16 u0,v0,u1,v1` (<<8, texel rect), `u16 page` (→ `texPage[page]`, no clamp), `u16 table` (→ `tableList[idx]`). Stored as 32-byte entries. **[CERTAIN]** for the layout; the a/b meaning is **[INFERRED]**. All 59 use pages 23–28 and table 0.

**Resources bound by the loader** (`0x0040CD60`, `0x0040CE00`):
- **Texture pages** `[0x004BE660+4i]`: the 8 files named at `0x004921A8` (stride 50 B), loaded back-to-back in **64 KiB = 256×256 pages** into `malloc(0x1DFFFF)` aligned to 64K. Each file is truncated to `0x100000` bytes, then the list is 0-terminated. **[CERTAIN]**
  - `baltazar\data\TEXTURES\canada.tex` → pages 0–15 (1,048,768 B; the last 192 B are dropped)
  - `cars.tex` → 16–22
  - `divspr` → 23
  - `light` → 24
  - `smoke` → 25
  - `darksmok` → 26
  - `boom` → 27
  - `exsmoke` → 28
  - These are the **Baltazar copies**, not the level/GENERAL/CARS ones: `textures/cars.tex` ≠ `CARS/Cars.tex`, `textures/divspr.tex` ≠ `GENERAL/DIVSPR.TEX`, and `canada.tex` differs from `LEVELS/Canada/CANADA.TEX` in 81,142 bytes. boom/exsmoke/light/smoke/darksmok are identical. **[CERTAIN]** (cmp)
- **Table list** `[0x004BE860]`: **[CERTAIN]** for construction.
  - `[0]` = first 64 KiB of `levels\canada\canada.SHD`
  - `[1]` = generated 256×256 table: row 0 = identity, row r = all r (`0x0040CE3D..0x0040CE74`)
  - `[2]` = 0
  - Reading, **[INFERRED]** (not traced into the span routines, but consistent with the render and with SHD row 0 ≈ identity): out = `table[src*256 + dst]`. With table 1 this is **colour-key transparency on texel 0** (type 18/table 1 = 17,589 uses: trees, fences). With canada.SHD it is **shading/translucency** (type 19/table 0 = 75,772 uses: car shadows; type 18/table 0 = 1,012).
- **Palette:** `baltazar\data\menu.col` (string `0x0047E94C`, refs `0x004029D2`, `0x00403F63`). `Menu.col` differs from `sys.col` and from `LEVELS/Canada/CANADA.COL` in only 3 bytes each, and from every other level COL in ~470 bytes. So the PFM is drawn with the Canada palette. **[CERTAIN]** for the byte comparison; **[INFERRED]** that the menu background is drawn under menu.col.
- **Vertex space:** i16 in **1/16 pixel on a 320×200 target**. 92 % of referenced vertices fall in [0,5120)×[0,3200); the rest are off-screen, including ±32767 sentinels, and are clipped by the renderer. The clip rect is `modeW−1, modeH−1` from the mode table `0x0047DC30` (`0x0040C7E5`). **[INFERRED]** from ranges, confirmed by the render.

**Byte accounting** (`_re/tools/formats/pfm.py`): chunk walk ends at 5,831,571 = file size (**0 unaccounted**). 1124/1124 `li00` streams are consumed exactly up to and including their `FF` (0 short, 0 trailing). `li02` = 1597×16 rem 0; `li03` = 59×16 rem 0. Every vertex index is < that frame's vertex count, max li02 idx = 1596, max li03 idx = 58. **[CERTAIN]**

Primitive totals: type17 339,953; type19 75,772; type7 26,666; type18 18,601; type15 1,946; types 11 and 13 unused.

**Visual check** (`_re/tools/formats/pfm_render.py`, a numpy verification rasterizer, not the game's): `_re/out/images/pfm/frame_{0000,0100,0200,0300,0450,0600,0750,0900,1050,1123}.png`, 320×200 upscaled ×2 with menu.col. They show a **clean overhead Canada-track fly-through**: a yellow school bus chasing a police car across wheat fields, dirt road, rocks, pines and a start banner. Textures are aligned and colours are correct, so the chunk layout, vertex scale, UV fixed-point, page order and palette are all right. The sprite size rule (half-extent = a·p/256) is a guess; sprites are few and small, so the frames do not validate it.

Summary dumps: `_re/out/data/test_pfm_summary.txt`, `_re/out/data/cdp_summary.txt`.

### Open questions
- CDP header +0x0E (=256) is never read; its meaning is unconfirmed.
- The exact dt unit behind both frame-pacing accumulators (`[0x004BEB40]`, `[0x004BE578]`), i.e. the true CDP/PFM fps. Rough values are ~15 and ~17 fps if dt is in 36 Hz ticks.
- Table-blend order (`table[src*256+dst]` vs `[dst*256+src]`) and sprite scaling (type 7 p,q; li03 a,b) need confirmation inside span routines `0x00452050`, `0x00454604`, `0x00455648`.
- Type 11 (line) and type 13 (skipped, 7 B) are supported by the parser but absent from test.pfm, so their semantics are unverified.
- Menu PFM exporter/tool provenance: the li00/li01 split suggests a Lisa-style offline renderer dump. The string `"Lisa 2 Development System"` is in the EXE; not investigated.

---

## 9. Output inventory

* `_re/out/images/pic/` — 26 PNG (game palette) + `embedded_palette/` 26 PNG
* `_re/out/images/tex/` — per-page PNG for 23 files, `__sheet.png` per file
* `_re/out/images/palette_test/` — own-vs-wrong COL controls, 7 levels × (TEX page, PIC)
* `_re/out/images/menubkg/` — tile sheet, bands, raw strip
* `_re/out/images/_contact_*.png` — contact sheets used for visual review
* `_re/out/fonts/` — 18 LFT sheets (+ embedded-palette variants), 2 FNT sheets
* `_re/out/images/cdp/`, `_re/out/images/pfm/`, `_re/out/data/` — frontend data (§8)
* `_re/out/audio/` — WAV conversions (§7)

## 10. Open questions (images / palettes / fonts)

1. Transparency key of the generic sprite blitter: 0 (proven for menubkg and FNT) or 229 (statistics)? Trace the draw slot behind the
   sprite object (`0x00461360` creates, draw via driver table `0x0050EBA0..0x0050EBEC`) and desc `+0x0C`.
2. GENERAL effect textures use level-specific indices — confirm whether they are drawn through the SHD/TAB shade tables (geometry note owns those files).
3. Exact on-screen meaning of each `menubkg.dat` cell and of the first/last 16-wide cells.
4. `S_`/`N_`/`H_` = 320/640/800 variants: confirm by the selector that picks the buffer from `[0x004BE730]`.
5. Rectangles for every HUD sprite are hard-coded in `0x0041E9A0..0x0041F800` / `0x00404138..`; they were not all tabulated.
