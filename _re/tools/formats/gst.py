#!/usr/bin/env python3
"""
gst.py -- reader/writer for Ignition ghost-lap replay files ``GHOSTS\\<TRACK>.GST``.

!! UNVERIFIED AGAINST REAL DATA !!
There are no .GST files in this installation, so every statement below is derived
from the machine code only and has never been checked against a file the game
actually wrote.  The acceptance test is ``gst_plot.py``: decode a real ghost and
draw it over the track render -- a correct decode traces the road.

The file is a verbatim ``fwrite`` of one heap block of 0x49D4C bytes.  The block
is allocated by 0x0045AFB0 which ZERO-FILLS it (rep stosd / rep stosb with
eax = 0), so every byte the game does not explicitly set is 0x00 -- including the
two trailing bytes, which are therefore deterministic and not heap garbage.

    writer   0x004222B0   (save_file at 0x004224EF, name via "GHOSTS\\%s.GST")
    reader   0x00420240   (load_file at 0x004202A1)
    recorder 0x00445C10   (called from 0x004235D8, one sample per 1/72 s sim step)
    playback 0x00445940   (called from 0x004177EE)
    reset    0x00404790   (deletes all 7 .GST files; menu "RESET GHOST CAR")

    sample buffer   [0x00563D7C]  allocated 0x49D40 = 21600 * 14 at 0x0041FFB4
    sample index    [0x00552F54]
    overflow flag   [0x005530B8]  set on wrap AND on a track-id mismatch
    best lap (cs)   [0x00525E44]  default 0x2DC6C0 = "no time"

LAYOUT
    +0x00000  int16   car model index          -> global [0x00563D30]
    +0x00002  int16   track id (0..6)          compared with [0x00552FC4]; mismatch = reject
    +0x00004  int16   TOTAL RACE TIME in CENTISECONDS, i.e. the whole 3-lap run, not one lap
                  (SIGNED; runs >= 327.68 s wrap negative -- a real risk, see below)
    +0x00006  char[4] driver name, NUL-terminated (effectively max 3 chars)
    +0x0000A  21600 records x 14 bytes, 7 x int16 little-endian:
                  w0  x      RAW MESH x (see COORDINATE SPACE below)
                  w1  y      raw y                 (recorder adds 5000 when car[0x354] != 0)
                  w2  z      RAW MESH z
                  w3  angle  radians = w3 / 1024   (normalised to [0, 2*pi) -> 0..6433)
                  w4  angle  radians = w4 / 1024
                  w5  angle  radians = w5 / 1024
                  w6  progress = lap * (nsections + 10) + abs(section)
    +0x49D4A  2 bytes, always 00 00 (allocated, never written)
    total     0x49D4C = 302412 bytes

COORDINATE SPACE
    The engine simulates in BIASED coordinates: x_engine = x_mesh + 25600 and
    z_engine = z_mesh + 25600, with y unbiased (see notes/04 "World extent"; there
    are 34 `add reg, 0x6400` sites across the SRF/collision/track code).  Raw mesh
    x, z therefore live in [-25600, 25600] and real tracks stay within |x|,|z| < 8702.

    The recorder subtracts the 25600.0 constant [0x0047AD38] from x and z
    (0x00445C3C, 0x00445CA5), so a .GST stores RAW MESH coordinates -- exactly the
    space of the .PLC/.MSH/.POS data and of _re/out/tracks/*_topdown.png.  Playback
    adds 0x6400 back (0x0044597F, 0x00445998) to hand engine-space coords to the
    object system.  Use ``Record.mesh`` to plot, ``Record.engine`` to drive a sim.

SHORT LAPS
    A lap that ends before the buffer fills is tail-padded by 0x004222F6..0x004223E9:
    records [n .. 21599] are overwritten with a copy of record [n-1] -- but the loop
    copies only SIX of the seven words (w0..w5).  w6 (progress) is left holding
    whatever was in the buffer, i.e. 0 on a fresh race or a stale value from an
    earlier, longer lap in the same session.  So the padded tail's w6 is NOT
    deterministic across sessions.

usage:
    gst.py dump      <file.GST> [--samples N]
    gst.py roundtrip <file.GST>
    gst.py validate  <file.GST>
"""

import struct
import sys

HEADER_SIZE = 0x0A
RECORD_COUNT = 21600            # 0x5460, from cmp at 0x00445DF4 / 0x004222FB
RECORD_STRIDE = 14              # 7 x int16, from the *7*2 index maths
RECORD_BYTES = RECORD_COUNT * RECORD_STRIDE      # 0x49D40 = 302400
TRAILER_SIZE = 2
SIZE = HEADER_SIZE + RECORD_BYTES + TRAILER_SIZE  # 0x49D4C = 302412

POS_BIAS = 25600                # [0x0047AD38] / 0x6400: engine x,z = mesh x,z + 25600
WORLD_LIMIT = 25600             # raw mesh x,z must lie in [-25600, 25600] (SRF world extent)
AIR_BIAS = 5000                 # [0x0047AD40], added to y when car[0x354] != 0
ANGLE_SCALE = 1024.0            # [0x0047AD48]; playback multiplies by 1/1024 [0x0047AD00]
ANGLE_MAX = 6434                # ceil(2*pi * 1024); wrap helper 0x00446330 -> [0, 2*pi)
NAME_FIELD = 4                  # bytes 6..9; record 0 begins at +0x0A

# Track id -> (level directory, ghost file basename).  From 0x004198D0.
TRACKS = {
    0: ("CANADA",  "MOOSEJAW"),
    1: ("USA",     "GOLDRUSH"),
    2: ("CARIB",   "SNAKEISL"),
    3: ("BRAZIL",  "LOSTRUIN"),
    4: ("AUSTRIA", "YODELPEA"),
    5: ("ICELAND", "CAPETHOR"),
    6: ("JAPAN",   "TOKYOBUL"),
}
BASENAME_TO_ID = {v[1]: k for k, v in TRACKS.items()}
LEVEL_TO_ID = {v[0]: k for k, v in TRACKS.items()}

# A race is ALWAYS 3 laps: the lap limit is hard-coded 3 at 0x004410D5 / 0x00424A69 /
# 0x00424C87 and there is no lap-count field anywhere in the data. Time trial is no
# exception -- the mode special-cased in the lap logic is mode 3 (PURSUE), not mode 2.
# The header time is therefore the total 3-lap run: car0[+0x3A4], which 0x0044112F
# copies wholesale from the race-clock double [0x00553090] when the car finishes.
#
# This makes the buffer size a real constraint: 21600 samples / 72 Hz = 300 s must
# cover the ENTIRE 3-lap race, and the shipped default times already reach 263 s on
# Tokyo Bullet. A slower run overflows, sets [0x005530B8], and NO ghost is saved.
#
# One ghost sample is taken per fixed simulation step.
#
#   GetFrameDeltaTicks 0x00420C00 scales milliseconds by 0.036 [0x004799B8],
#     so 1 "tick" = 1/36 s.
#   The sim driver 0x00422680 accumulates that dt and runs a step while the
#     accumulator >= 0.5 ticks [0x00479A50], i.e. a fixed step of 1/72 s.
#   The recorder call at 0x004235D8 sits below 0x004235B2, which is the target of
#     the parity branch at 0x00423591, so it executes on BOTH parities -- once per
#     sim step, not every other step.
#
# => ghosts are sampled at 72 Hz.  NOTE: notes/06 states 36 Hz; that is incorrect
#    (the parity flag [0x005531B8] gates other per-step work, not the recorder).
SAMPLE_RATE_HZ = 72.0
MAX_DURATION_S = RECORD_COUNT / SAMPLE_RATE_HZ   # 300 s = 5 min at 72 Hz


class GstError(Exception):
    """Raised on any structural surprise -- gst.py fails loudly by design."""


class Record:
    __slots__ = ("x", "y", "z", "a0", "a1", "a2", "progress")

    def __init__(self, x, y, z, a0, a1, a2, progress):
        self.x, self.y, self.z = x, y, z
        self.a0, self.a1, self.a2 = a0, a1, a2
        self.progress = progress

    @property
    def mesh(self):
        """Raw mesh-space position, as stored: the space of .PLC/.MSH/.POS and the
        top-down renders. Y is vertical, ~20 units per metre."""
        return (self.x, self.y, self.z)

    @property
    def engine(self):
        """Engine/simulation space: mesh x,z biased by +25600 (y unbiased). This is
        what the playback routine 0x00445940 reconstructs."""
        return (self.x + POS_BIAS, self.y, self.z + POS_BIAS)

    @property
    def radians(self):
        return (self.a0 / ANGLE_SCALE, self.a1 / ANGLE_SCALE, self.a2 / ANGLE_SCALE)

    def as_tuple(self):
        return (self.x, self.y, self.z, self.a0, self.a1, self.a2, self.progress)

    def __eq__(self, other):
        return isinstance(other, Record) and self.as_tuple() == other.as_tuple()

    def __repr__(self):
        return ("Record(mesh=(%d,%d,%d) rad=(%.3f,%.3f,%.3f) prog=%d)"
                % (self.mesh + self.radians + (self.progress,)))


class Ghost:
    """A fully decoded .GST.  ``pack()`` reproduces the input byte-for-byte."""

    def __init__(self):
        self.car_model = 0
        self.track_id = 0
        self.race_time_cs = 0       # total 3-lap time, centiseconds (header +4)
        self.name_raw = b"\0\0\0\0"
        self.records = []
        self.trailer = b"\0\0"

    # -------------------------------------------------------------- decoding

    @classmethod
    def parse(cls, data, strict=True):
        if len(data) != SIZE:
            raise GstError("expected exactly %d (0x%X) bytes, got %d -- the game's "
                           "load_file (0x00457420) requires a full-length read and "
                           "rejects anything shorter" % (SIZE, SIZE, len(data)))
        self = cls()
        self.car_model, self.track_id, self.race_time_cs = struct.unpack_from("<3h", data, 0)
        self.name_raw = bytes(data[6:6 + NAME_FIELD])
        vals = struct.unpack_from("<%dh" % (RECORD_COUNT * 7), data, HEADER_SIZE)
        self.records = [Record(*vals[i * 7:i * 7 + 7]) for i in range(RECORD_COUNT)]
        self.trailer = bytes(data[HEADER_SIZE + RECORD_BYTES:])
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
        out += struct.pack("<3h", self.car_model, self.track_id, self.race_time_cs)
        if len(self.name_raw) != NAME_FIELD:
            raise GstError("name field must be exactly %d bytes, got %d"
                           % (NAME_FIELD, len(self.name_raw)))
        out += self.name_raw
        if len(self.records) != RECORD_COUNT:
            raise GstError("expected %d records, got %d" % (RECORD_COUNT, len(self.records)))
        flat = []
        for r in self.records:
            flat.extend(r.as_tuple())
        out += struct.pack("<%dh" % (RECORD_COUNT * 7), *flat)
        if len(self.trailer) != TRAILER_SIZE:
            raise GstError("trailer must be exactly %d bytes" % TRAILER_SIZE)
        out += self.trailer
        if len(out) != SIZE:
            raise GstError("packed to %d bytes, expected %d" % (len(out), SIZE))
        return bytes(out)

    def save(self, path):
        with open(path, "wb") as fh:
            fh.write(self.pack())

    # ------------------------------------------------------------ validation

    def validate(self):
        """Structural checks. Fails loudly -- this format is unverified, so anything
        unexpected is far more likely to be a wrong decode than a weird ghost."""
        problems = []
        if self.track_id not in TRACKS:
            problems.append("track id %d is outside the valid range 0..6" % self.track_id)
        if self.trailer != b"\0\0":
            problems.append("trailer is %r, expected 00 00 (the allocator at 0x0045AFB0 "
                            "zero-fills, so these bytes are always zero)" % (self.trailer,))
        if b"\0" not in self.name_raw:
            problems.append("driver name field %r is not NUL-terminated" % (self.name_raw,))
        if self.race_time_cs < 0:
            problems.append("race time %d cs is negative -- int16 overflow; the original "
                            "wraps 3-lap runs of 327.68 s or longer" % self.race_time_cs)
        out = [i for i, r in enumerate(self.records)
               if not (-WORLD_LIMIT <= r.x <= WORLD_LIMIT and -WORLD_LIMIT <= r.z <= WORLD_LIMIT)]
        if out:
            problems.append("%d records have mesh x/z outside [-%d, %d]; first at index %d "
                            "-- the SRF world extent cannot hold them, so the decode is "
                            "probably wrong" % (len(out), WORLD_LIMIT, WORLD_LIMIT, out[0]))
        bad = [i for i, r in enumerate(self.records)
               if not (0 <= r.a0 < ANGLE_MAX and 0 <= r.a1 < ANGLE_MAX and 0 <= r.a2 < ANGLE_MAX)]
        if bad:
            problems.append("%d records have an angle outside [0, 2*pi*1024) = [0,%d); "
                            "first at index %d -- angles are normalised by 0x00446330 so "
                            "this suggests the field order is wrong"
                            % (len(bad), ANGLE_MAX, bad[0]))
        if problems:
            raise GstError("GST validation failed:\n  - " + "\n  - ".join(problems))
        return True

    def warnings(self):
        w = []
        n = self.active_samples()
        if n == RECORD_COUNT:
            w.append("no padded tail detected: the lap either filled the whole %d-sample "
                     "buffer or the recorder wrapped (overflow flag [0x005530B8])" % RECORD_COUNT)
        # Compare against the TIMED span, not the full sample count: a ghost also
        # contains stationary grid samples and a post-finish tail (see race_span).
        rate = self.measured_rate_hz()
        if rate is not None and abs(rate - SAMPLE_RATE_HZ) > 1.0:
            w.append("timed span implies %.3f Hz but the decoder assumes %g Hz -- either the "
                     "sample rate or the lap/progress decode is wrong"
                     % (rate, SAMPLE_RATE_HZ))
        first, last = self.race_span()
        tail = n - last
        if tail > 0:
            w.append("%d samples (%.2f s) recorded after the progress word stopped advancing "
                     "-- expected: the recorder runs on past the finish line until the writer fires"
                     % (tail, tail / SAMPLE_RATE_HZ))
        tail = self.records[n:] if n < RECORD_COUNT else []
        if tail and len({r.progress for r in tail}) > 1:
            w.append("padded tail has varying progress words, as expected: the pad loop "
                     "at 0x004222F6 copies only w0..w5")
        return w

    # ------------------------------------------------------------- accessors

    @property
    def name(self):
        return self.name_raw.split(b"\0")[0].decode("latin-1", "replace")

    @name.setter
    def name(self, text):
        raw = text.encode("latin-1")[:NAME_FIELD - 1]
        self.name_raw = raw + b"\0" * (NAME_FIELD - len(raw))

    @property
    def race_time_s(self):
        """Total 3-lap time in seconds."""
        return self.race_time_cs / 100.0

    # Back-compat aliases: this field was originally mis-documented as a lap time.
    @property
    def lap_time_cs(self):
        return self.race_time_cs

    @property
    def lap_time_s(self):
        return self.race_time_s

    @property
    def level(self):
        return TRACKS.get(self.track_id, ("?", "?"))[0]

    @property
    def basename(self):
        return TRACKS.get(self.track_id, ("?", "?"))[1]

    def filename(self):
        return "GHOSTS\\%s.GST" % self.basename

    def active_samples(self):
        """Heuristic: length of the real lap, i.e. where the padded tail starts.

        The pad loop copies record[n-1] over records[n..21599] (w0..w5 only), so the
        tail is a run of identical position/angle words reaching the very end.  This
        is a HEURISTIC: a car sitting perfectly still at the end of a lap would look
        the same.  Returns RECORD_COUNT when no tail is found."""
        last = self.records[-1]
        key = last.as_tuple()[:6]
        i = RECORD_COUNT - 1
        while i > 0 and self.records[i - 1].as_tuple()[:6] == key:
            i -= 1
        return i if i < RECORD_COUNT - 1 else RECORD_COUNT

    def lap_stride(self):
        """N = nsections + 10, the per-lap increment of the progress word.

        Not stored in the file; recovered as max(progress)/3 because a race is always
        3 laps. Returns None if the run did not complete 3 laps."""
        n = self.active_samples()
        mx = max((r.progress for r in self.records[:n]), default=0)
        return mx // 3 if mx and mx % 3 == 0 else None

    def race_span(self):
        """(first_motion, last_progress_change) -- the TIMED part of the run.

        A ghost brackets the race: a few stationary samples on the grid before the car
        moves, and a post-finish tail while the car rolls on before the writer runs.
        Measuring the rate over this span (rather than over active_samples) reproduces
        the header time; on the reference file it gives 71.993 Hz."""
        n = self.active_samples()
        if n < 2:
            return (0, 0)
        first = 0
        p0 = self.records[0].mesh
        while first < n and self.records[first].mesh == p0:
            first += 1
        last = first
        for i in range(1, n):
            if self.records[i].progress != self.records[i - 1].progress:
                last = i
        return (first, last)

    def lap_boundaries(self):
        """Sample index at which each lap completes, from the progress word."""
        N = self.lap_stride()
        if not N:
            return []
        n = self.active_samples()
        out = []
        for lap in range(1, 4):
            target = lap * N
            idx = next((i for i in range(n) if self.records[i].progress >= target), None)
            out.append(idx)
        return out

    def lap_times(self):
        """Per-lap durations in seconds, derived from the progress word at SAMPLE_RATE_HZ."""
        bounds = self.lap_boundaries()
        if not bounds or any(b is None for b in bounds):
            return []
        prev = self.race_span()[0]
        out = []
        for b in bounds:
            out.append((b - prev) / SAMPLE_RATE_HZ)
            prev = b
        return out

    def measured_rate_hz(self):
        """Sample rate implied by the timed span and the header time (sanity check)."""
        first, last = self.race_span()
        return (last - first) / self.race_time_s if self.race_time_s > 0 else None

    def path_xz(self, limit=None):
        """Raw mesh-space (x, z) samples, ready to plot over a top-down track render."""
        n = limit if limit is not None else self.active_samples()
        return [(r.x, r.z) for r in self.records[:n]]

    # --------------------------------------------------------------- display

    def describe(self, nsamples=8):
        n = self.active_samples()
        L = []
        L.append("%s  (%d bytes)" % (self.filename(), SIZE))
        L.append("  car model index : %d" % self.car_model)
        L.append("  track id        : %d  -> LEVELS\\%s, %s" % (self.track_id, self.level, self.basename))
        L.append("  race time (3 lap): %d cs = %.2f s" % (self.race_time_cs, self.race_time_s))
        L.append("  driver name     : %r (raw %r)" % (self.name, self.name_raw))
        L.append("  trailer         : %s" % self.trailer.hex(" "))
        L.append("  active samples  : %d of %d  (%.2f s at %g Hz)"
                 % (n, RECORD_COUNT, n / SAMPLE_RATE_HZ, SAMPLE_RATE_HZ))
        first, last = self.race_span()
        rate = self.measured_rate_hz()
        L.append("  timed span      : samples %d..%d  -> measured %.3f Hz"
                 % (first, last, rate if rate else float("nan")))
        lt = self.lap_times()
        if lt:
            L.append("  lap times       : %s  (sum %.2f s, header %.2f s)"
                     % (", ".join("%.2f" % t for t in lt), sum(lt), self.race_time_s))
        if self.lap_stride():
            L.append("  lap stride N    : %d  (=> nsections %d)"
                     % (self.lap_stride(), self.lap_stride() - 10))
        xs = [r.x for r in self.records[:n]]
        zs = [r.z for r in self.records[:n]]
        ys = [r.y for r in self.records[:n]]
        if xs:
            L.append("  mesh x range    : %d .. %d   (engine %d .. %d)"
                     % (min(xs), max(xs), min(xs) + POS_BIAS, max(xs) + POS_BIAS))
            L.append("  mesh y range    : %d .. %d" % (min(ys), max(ys)))
            L.append("  mesh z range    : %d .. %d   (engine %d .. %d)"
                     % (min(zs), max(zs), min(zs) + POS_BIAS, max(zs) + POS_BIAS))
            L.append("  progress range  : %d .. %d" % (min(r.progress for r in self.records[:n]),
                                                       max(r.progress for r in self.records[:n])))
        L.append("  first %d records:" % nsamples)
        for r in self.records[:nsamples]:
            L.append("    " + repr(r))
        return "\n".join(L)


def roundtrip(path):
    with open(path, "rb") as fh:
        original = fh.read()
    ghost = Ghost.parse(original, strict=False)
    rebuilt = ghost.pack()
    if rebuilt == original:
        return True, "byte-exact round-trip: %d bytes match" % len(original)
    diffs = [i for i in range(min(len(original), len(rebuilt))) if original[i] != rebuilt[i]]
    msg = ["ROUND-TRIP FAILED: %d differing bytes" % len(diffs)]
    for i in diffs[:40]:
        msg.append("  +0x%05X  orig %02X  rebuilt %02X" % (i, original[i], rebuilt[i]))
    return False, "\n".join(msg)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, path = argv[1], argv[2]
    if cmd == "dump":
        n = int(argv[argv.index("--samples") + 1]) if "--samples" in argv else 8
        ghost = Ghost.load(path, strict=False)
        print(ghost.describe(n))
        try:
            ghost.validate()
            print("  validation      : PASS")
        except GstError as exc:
            print("  validation      : FAIL\n%s" % exc)
        for w in ghost.warnings():
            print("WARNING: %s" % w)
    elif cmd == "roundtrip":
        ok, msg = roundtrip(path)
        print(msg)
        return 0 if ok else 1
    elif cmd == "validate":
        try:
            Ghost.load(path, strict=True)
        except GstError as exc:
            print(exc)
            return 1
        print("OK")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
