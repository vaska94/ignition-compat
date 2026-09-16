#!/usr/bin/env python3
"""
btz.py -- reader/writer for Ignition's settings/progress blob ``ign_win.btz``.

The file is a verbatim ``fwrite`` of a 0x597-byte packed C struct.  There are two
in-memory copies of it in the image:

    menu copy  base 0x004BF580   (front end)
    race copy  base 0x006393A0   (race engine)      delta = +0x179E20

and the on-disk file is byte-identical to either one.  Loader/writer VAs:

    file_exists()      0x004576B0
    load_file()        0x00457420   (fopen "rb"; fseek; fread; MUST read exactly len)
    save_file()        0x00457590   (fopen "wb"; fwrite; fclose)
    menu-side load     0x00409461 .. 0x004094A4   (tolerant: version != 0x1E -> keep defaults)
    race-side load     0x00419260                 (intolerant: failure -> exit(1))
    language-only load 0x004029A0                 (version check, then 4 bytes at file offset 0x593)
    writers            0x004030B5 / 0x0040315F (menu autosave)
                       0x0042266E (leaving a race)
                       0x0042095B (race-module teardown)

Field layout is UNALIGNED: after the 15-byte name slots at +0x06C the dword grid
resumes at +0x0F3, which is not 4-byte aligned.  That is why the struct must be
reproduced byte-for-byte rather than rebuilt from a naturally-aligned C struct.

Region map (derived from the real file; the region sizes sum to exactly 0x597):

    +0x000 .. +0x06B   27 x uint32   (grid A, aligned)
    +0x06C .. +0x0F2    9 x char[15] driver/player name slots
    +0x0F3 .. +0x17E   35 x uint32   (grid B, UNALIGNED)
    +0x17F .. +0x18E   16 x uint8    DIK keyboard scancodes
    +0x18F .. +0x19A    3 x uint32   (grid C, UNALIGNED)
    +0x19B .. +0x1BE   2 x char[15] + 6 spare bytes
    +0x1BF .. +0x506   14 x leaderboard block of 0x3C
                       (5 x char[4] name, then 5 x float64 time in SECONDS, ascending)
    +0x507 .. +0x596   36 x uint32   (grid D, UNALIGNED)

usage:
    btz.py dump      <file>
    btz.py roundtrip <file>
    btz.py set       <file> <offset|name> <value> [-o out]
"""

import struct
import sys

SIZE = 0x597
VERSION_MAGIC = 0x1E

# ---------------------------------------------------------------- region model

#   name,       start,  end,    kind
REGIONS = [
    ("grid_a",  0x000, 0x06C, "u32"),
    ("names",   0x06C, 0x0F3, "str15"),
    ("grid_b",  0x0F3, 0x17F, "u32"),
    ("keys",    0x17F, 0x18F, "u8"),
    ("grid_c",  0x18F, 0x19B, "u32"),
    ("mid",     0x19B, 0x1B9, "str15"),
    ("spare",   0x1B9, 0x1BF, "raw"),
    ("boards",  0x1BF, 0x507, "board"),
    ("grid_d",  0x507, 0x597, "u32"),
]

BOARD_STRIDE = 0x3C     # 5 x char[4] + 5 x double
BOARD_COUNT = 14        # 7 tracks x 2 tables
BOARD_ENTRIES = 5

# ---------------------------------------------------------------- named fields
# offset -> (name, note).  Evidence VAs are given as menu-copy addresses
# (0x004BF580 + offset).  Anything not listed here is preserved verbatim but
# has no confirmed meaning.

FIELDS = {
    0x000: ("VERSION", "must be 0x1E (30) or the menu loader ignores the file"),
    0x004: ("TWO_PLAYER", "==1 -> two players; -> global 0x0055306C"),
    0x008: ("GAME_MODE", "0=CHAMPIONSHIP 1=SINGLE RACE 2=TIME TRIAL 3=PURSUE "
                         "-> global 0x00527F6C (jump table 0x00419688); ghosts require mode 2"),
    0x00C: ("DIFFICULTY", "NOVICE/AMATEUR/PRO/MIRROR; >=3 swaps L/R keys at 0x0041957B"),
    0x010: ("GHOST_CAR", "0=off 1=on -> global 0x00553068"),
    0x014: ("OBSTACLES", "0=off 1=on -> global 0x00553084"),
    0x01C: ("CAR_MODEL_P1", "element 0 of the 9-entry car-model array +0x01C..+0x03C"),
    0x020: ("CAR_MODEL_S1", "element 1 = player 2 at race time"),
    0x040: ("CARSEL_CURSOR_P1", "car-select cursor -> +0x01C via [0x004BE6E0] (0x004048A3)"),
    0x044: ("CARSEL_CURSOR_P2", "car-select cursor -> +0x020 (0x004048B7)"),
    0x048: ("GEARBOX_P1", "1=AUTO; element 0 of the 9-entry gearbox array +0x048..+0x068"),
    0x068: ("GEARBOX_P2", "1=AUTO; element 8 of that array"),
    0x0F3: ("CHAMP_ROUND", "championship round index; -> [0x00552FC4] direct, or via "
                           "0x004984A0[difficulty*7+round]; bumped/capped at 6 by 0x0042253B"),
    0x0F7: ("CD_TRACK", "-1=RANDOM, 0=DEFAULT -> global 0x00552E54"),
    0x0FB: ("CD_MUSIC", "0=off 1=on -> global 0x00527F30"),
    0x0FF: ("SFX_VOLUME", "0..10 -> global 0x00601668"),
    0x103: ("SCREEN_SIZE_WIDGET", "menu index; +0x107 = 3 - this (0x004049F5)"),
    0x107: ("SCREEN_SIZE", "effective = 3 - [+0x103] -> globals 0x00527F28 / 0x00563C6C"),
    0x10B: ("RESOLUTION", "0=320x200 -> global 0x00552FC0"),
    0x10F: ("PERSP_POLY", "0=OFF -> global 0x00525E68"),
    0x113: ("SKID_MARKS", "1=ON -> global 0x00552E58"),
    0x117: ("SMOKE", "1=ON -> global 0x00563BE0"),
    0x123: ("CONTROLS_P1", "0=KEYBOARD"),
    0x127: ("CONTROLS_P2", "0=KEYBOARD"),
    0x12B: ("AUTO_ACC_P1", ""),
    0x12F: ("AUTO_ACC_P2", ""),
    0x133: ("GRID_SLOT_0", "element 0 of the 8-entry per-car STARTING GRID SLOT array "
                           "+0x133..+0x14F; rewritten post-race as (6 - finishing position) "
                           "at 0x00422594. Championship POINTS are not in the blob."),
    0x153: ("CAR_COUNT", "-> global 0x006192F0; 6 normally, 2/1 in mode 2 (0x00404A0D)"),
    0x15B: ("MIP_MAPPING", "0=OFF -> global 0x00527F78"),
    0x15F: ("CTRL_TYPE_0", "element 0 of the 8-entry controller-type array +0x15F..+0x17B "
                           "(0=human 1=AI 2=network) -> carinfo[i].+0x04 at 0x0041951B"),
    0x197: ("NET_ROLE", "-> global 0x00552F10: 2=local, 3=DirectPlay host, 4=client"),
    0x57F: ("BESTTIMES_TRACK", "BEST TIMES screen track selector 0..6 (0x0040C30B)"),
    0x583: ("BESTTIMES_MODE", "0=race/total block, non-0=lap block"),
    0x587: ("PROGRESSION", "0..3 tiers beaten: caps DIFFICULTY (0x00409510/0x004094B9) and "
                           "unlocks cars (skips 4-[+0x587] entries at 0x004099CB)"),
    0x58B: ("MIRROR_DONE", "set at 0x00439B99 on round 6 + difficulty 3; unlocks one more car"),
    0x593: ("LANGUAGE", "0=EN 1=DE 2=IT 3=ES 4=SE 5=FR (menu max is 4, so FR is unselectable)"),
}

LANGUAGES = ["English", "German", "Italian", "Spanish", "Swedish", "French"]

KEY_ACTIONS = ["TURN LEFT", "TURN RIGHT", "ACC.", "BRAKE",
               "GEAR UP", "GEAR DOWN", "BOOST", "REAR VIEW"]

# Leaderboard block index = track*2 (race total) and track*2+1 (best lap).
# Track order verified three ways: 0x004198D0, the name table at 0x0047DFA8, and the defaults.
TRACKS = [("CANADA", "MOOSEJAW FALLS"), ("USA", "GOLD RUSH"), ("CARIB", "SNAKE ISLAND"),
          ("BRAZIL", "LOST RUINS"), ("AUSTRIA", "YODEL PEAKS"), ("ICELAND", "CAPE THOR"),
          ("JAPAN", "TOKYO BULLET")]


def board_label(index):
    """Human label for leaderboard block `index` (0..13)."""
    track, kind = divmod(index, 2)
    if track >= len(TRACKS):
        return "block %d (out of range)" % index
    return "%s (%s) %s" % (TRACKS[track][1], TRACKS[track][0], "best lap" if kind else "race total")


def format_time(seconds):
    """Reproduce the game's MM:SS:CC display formatter at 0x0040C520."""
    m = min(int(seconds // 60), 99)
    s = int(seconds) - 60 * m
    cs = int((seconds - int(seconds)) * 100)
    return "%.2d:%.2d:%.2d" % (m, s, cs)

# Arrays rather than scalars; documented here so `describe()` can label them.
ARRAYS = {
    "car_model":  (0x01C, 9, 4, "per-car car model index (0=P1, 1=P2-at-race, 2..7=AI, 8=P2-menu)"),
    "gearbox":    (0x048, 9, 4, "per-car gearbox, 0=manual 1=auto"),
    "grid_slot":  (0x133, 8, 4, "per-car starting grid slot (reverse grid after a race)"),
    "ctrl_type":  (0x15F, 8, 4, "per-car controller type, 0=human 1=AI 2=network"),
    "page_cursor": (0x507, 25, 4, "saved menu page cursor positions (page slot i)"),
}

# +0x17F..+0x186 = player 1, +0x187..+0x18E = player 2.
KEY_DEFAULT_NAMES_P1 = ["LEFT", "RIGHT", "UP", "DOWN", "/", ".", "R SHIFT", "'"]
KEY_DEFAULT_NAMES_P2 = ["C", "B", "F", "V", "\\", "L SHIFT", "Z", "S"]

NAME_SLOTS = {
    0x06C: "PLAYER1_NAME",
    0x0E4: "PLAYER2_NAME",
}

# The 16 DIK scancodes at +0x17F.  Compiled-in defaults are
# CB CD C8 D0 35 34 36 28 2E 30 21 2F 2B 2A 2C 1F.
KEY_COUNT = 16

BY_NAME = {v[0]: k for k, v in FIELDS.items()}
BY_NAME.update({v: k for k, v in NAME_SLOTS.items()})


class BtzError(Exception):
    pass


class Board:
    """One leaderboard table: 5 names + 5 times (seconds, ascending)."""

    __slots__ = ("names", "times")

    def __init__(self, names, times):
        self.names = names      # list[bytes], each exactly 4 bytes
        self.times = times      # list[float]

    @classmethod
    def unpack(cls, buf, off):
        names = [bytes(buf[off + i * 4: off + i * 4 + 4]) for i in range(BOARD_ENTRIES)]
        times = [struct.unpack_from("<d", buf, off + 20 + i * 8)[0]
                 for i in range(BOARD_ENTRIES)]
        return cls(names, times)

    def pack(self):
        out = bytearray()
        for n in self.names:
            if len(n) != 4:
                raise BtzError("board name slot must be exactly 4 bytes, got %r" % (n,))
            out += n
        for t in self.times:
            out += struct.pack("<d", t)
        if len(out) != BOARD_STRIDE:
            raise BtzError("board packed to %d bytes, expected %d" % (len(out), BOARD_STRIDE))
        return bytes(out)

    def display(self):
        return [(self.names[i].split(b"\0")[0].decode("latin-1", "replace"), self.times[i])
                for i in range(BOARD_ENTRIES)]

    def sorted_ascending(self):
        return all(self.times[i] <= self.times[i + 1] for i in range(BOARD_ENTRIES - 1))


class Btz:
    """Fully-decoded ign_win.btz.  ``pack()`` reproduces the input byte-for-byte."""

    def __init__(self):
        self.grid_a = []        # 27 uint32
        self.names = []         # 9 x bytes(15)
        self.grid_b = []        # 35 uint32
        self.keys = []          # 16 uint8
        self.grid_c = []        # 3 uint32
        self.mid = []           # 2 x bytes(15)
        self.spare = b""        # 6 raw bytes
        self.boards = []        # 14 Board
        self.grid_d = []        # 36 uint32

    # -------------------------------------------------------------- decoding

    @classmethod
    def parse(cls, data, strict=True):
        if len(data) != SIZE:
            raise BtzError("expected %d (0x%X) bytes, got %d" % (SIZE, SIZE, len(data)))
        self = cls()
        buf = memoryview(data)
        for name, start, end, kind in REGIONS:
            n = end - start
            if kind == "u32":
                setattr(self, name, list(struct.unpack_from("<%dI" % (n // 4), buf, start)))
            elif kind == "u8":
                setattr(self, name, list(buf[start:end]))
            elif kind == "str15":
                setattr(self, name, [bytes(buf[o:o + 15]) for o in range(start, end, 15)])
            elif kind == "raw":
                setattr(self, name, bytes(buf[start:end]))
            elif kind == "board":
                self.boards = [Board.unpack(buf, o) for o in range(start, end, BOARD_STRIDE)]
        if strict:
            self.validate()
        return self

    @classmethod
    def load(cls, path, strict=True):
        with open(path, "rb") as fh:
            return cls.parse(fh.read(), strict=strict)

    # -------------------------------------------------------------- encoding

    def pack(self):
        out = bytearray()
        for name, start, end, kind in REGIONS:
            if len(out) != start:
                raise BtzError("region %s starts at 0x%X, builder is at 0x%X"
                               % (name, start, len(out)))
            n = end - start
            val = getattr(self, name)
            if kind == "u32":
                if len(val) != n // 4:
                    raise BtzError("%s: expected %d dwords, got %d" % (name, n // 4, len(val)))
                out += struct.pack("<%dI" % (n // 4), *val)
            elif kind == "u8":
                if len(val) != n:
                    raise BtzError("%s: expected %d bytes, got %d" % (name, n, len(val)))
                out += bytes(val)
            elif kind == "str15":
                if len(val) != n // 15:
                    raise BtzError("%s: expected %d slots, got %d" % (name, n // 15, len(val)))
                for s in val:
                    if len(s) != 15:
                        raise BtzError("%s: name slot must be exactly 15 bytes" % name)
                    out += s
            elif kind == "raw":
                if len(val) != n:
                    raise BtzError("%s: expected %d bytes, got %d" % (name, n, len(val)))
                out += val
            elif kind == "board":
                if len(val) != BOARD_COUNT:
                    raise BtzError("boards: expected %d, got %d" % (BOARD_COUNT, len(val)))
                for b in val:
                    out += b.pack()
        if len(out) != SIZE:
            raise BtzError("packed to %d bytes, expected %d" % (len(out), SIZE))
        return bytes(out)

    def save(self, path):
        with open(path, "wb") as fh:
            fh.write(self.pack())

    # ------------------------------------------------------------ validation

    def validate(self):
        if self.version != VERSION_MAGIC:
            raise BtzError("version magic is %d (0x%X), expected %d -- the game would "
                           "IGNORE this file and keep compiled-in defaults"
                           % (self.version, self.version, VERSION_MAGIC))
        for i, b in enumerate(self.boards):
            for n in b.names:
                if n[3:4] != b"\0":
                    raise BtzError("board %d: name slot %r is not NUL-terminated" % (i, n))
        return True

    def warnings(self):
        """Non-fatal oddities worth reporting."""
        w = []
        if self.version != VERSION_MAGIC:
            w.append("version magic %d != %d -> game ignores the file" % (self.version, VERSION_MAGIC))
        for i, b in enumerate(self.boards):
            if not b.sorted_ascending():
                w.append("board %d times are not ascending: %r" % (i, b.times))
        lang = self.get(0x593)
        if lang > 5:
            w.append("LANGUAGE index %d is out of the known range 0..5" % lang)
        for i, s in enumerate(self.names):
            if b"\0" not in s:
                w.append("name slot %d is not NUL-terminated: %r" % (i, s))
        return w

    # -------------------------------------------------------- field accessors

    @property
    def version(self):
        return self.grid_a[0]

    def _locate(self, off):
        """Map a blob offset to (region_name, index) for the dword grids."""
        for name, start, end, kind in REGIONS:
            if kind == "u32" and start <= off < end:
                if (off - start) % 4:
                    raise BtzError("offset 0x%X is not on the %s dword grid (base 0x%X)"
                                   % (off, name, start))
                return name, (off - start) // 4
        raise BtzError("offset 0x%X is not inside a dword grid" % off)

    def get(self, off):
        region, idx = self._locate(off)
        return getattr(self, region)[idx]

    def set(self, off, value):
        region, idx = self._locate(off)
        getattr(self, region)[idx] = value & 0xFFFFFFFF

    def get_signed(self, off):
        v = self.get(off)
        return v - 0x100000000 if v & 0x80000000 else v

    def name(self, off):
        """Read one of the 15-byte name slots by blob offset."""
        if not (0x06C <= off < 0x0F3) or (off - 0x06C) % 15:
            raise BtzError("0x%X is not a name-slot offset" % off)
        return self.names[(off - 0x06C) // 15].split(b"\0")[0].decode("latin-1", "replace")

    def set_name(self, off, text):
        if not (0x06C <= off < 0x0F3) or (off - 0x06C) % 15:
            raise BtzError("0x%X is not a name-slot offset" % off)
        raw = text.encode("latin-1")[:14]
        self.names[(off - 0x06C) // 15] = raw + b"\0" * (15 - len(raw))

    # --------------------------------------------------------------- display

    def describe(self):
        L = []
        L.append("ign_win.btz  (%d bytes, version magic 0x%02X)" % (SIZE, self.version))
        L.append("")
        L.append("-- named fields --")
        for off in sorted(FIELDS):
            nm, note = FIELDS[off]
            try:
                v = self.get_signed(off)
            except BtzError:
                continue
            L.append("  +0x%03X  %-16s = %-8d %s" % (off, nm, v, note))
        L.append("")
        L.append("-- name slots (9 x 15 bytes at +0x06C) --")
        for i, s in enumerate(self.names):
            off = 0x06C + i * 15
            tag = NAME_SLOTS.get(off, "")
            L.append("  +0x%03X  slot%-2d %-14r %s" % (off, i, s.split(b"\0")[0], tag))
        L.append("")
        L.append("-- keyboard bindings (16 DIK scancodes at +0x17F) --")
        L.append("  " + " ".join("%02X" % k for k in self.keys))
        L.append("")
        L.append("-- misc slots at +0x19B --")
        for i, s in enumerate(self.mid):
            L.append("  +0x%03X  %r" % (0x19B + i * 15, s.split(b"\0")[0]))
        L.append("  +0x1B9  spare %s" % self.spare.hex(" "))
        L.append("")
        L.append("-- leaderboards (14 x 0x3C at +0x1BF; 5 names + 5 times in seconds) --")
        for i, b in enumerate(self.boards):
            off = 0x1BF + i * BOARD_STRIDE
            ent = ", ".join("%s %s" % (n, format_time(t)) for n, t in b.display())
            L.append("  blk%-2d +0x%03X  %-28s %s" % (i, off, board_label(i), ent))
        L.append("")
        L.append("-- non-zero unnamed dwords --")
        for rname, start, end, kind in REGIONS:
            if kind != "u32":
                continue
            for j, v in enumerate(getattr(self, rname)):
                off = start + j * 4
                if v and off not in FIELDS:
                    L.append("  +0x%03X  = %d" % (off, v))
        return "\n".join(L)


# -------------------------------------------------------------------- helpers

def roundtrip(path):
    """Parse then re-serialise; returns (ok, message)."""
    with open(path, "rb") as fh:
        original = fh.read()
    blob = Btz.parse(original, strict=False)
    rebuilt = blob.pack()
    if rebuilt == original:
        return True, "byte-exact round-trip: %d bytes match" % len(original)
    diffs = [i for i in range(min(len(original), len(rebuilt))) if original[i] != rebuilt[i]]
    msg = ["ROUND-TRIP FAILED: %d differing bytes (len %d -> %d)"
           % (len(diffs), len(original), len(rebuilt))]
    for i in diffs[:40]:
        msg.append("  +0x%03X  orig %02X  rebuilt %02X" % (i, original[i], rebuilt[i]))
    return False, "\n".join(msg)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, path = argv[1], argv[2]
    if cmd == "dump":
        blob = Btz.load(path, strict=False)
        print(blob.describe())
        for w in blob.warnings():
            print("WARNING: %s" % w)
    elif cmd == "roundtrip":
        ok, msg = roundtrip(path)
        print(msg)
        return 0 if ok else 1
    elif cmd == "set":
        if len(argv) < 5:
            print("usage: btz.py set <file> <offset|name> <value> [-o out]")
            return 2
        key, value = argv[3], int(argv[4], 0)
        off = BY_NAME[key] if key in BY_NAME else int(key, 0)
        out = argv[argv.index("-o") + 1] if "-o" in argv else path
        blob = Btz.load(path, strict=False)
        blob.set(off, value)
        blob.save(out)
        print("set +0x%03X = %d -> %s" % (off, value, out))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
