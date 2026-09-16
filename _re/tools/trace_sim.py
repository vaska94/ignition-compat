#!/usr/bin/env python3
"""
trace_sim.py [trace.bin] [--level Canada]

Read a simulation recording made by gametrace.c and find the car pose fields in
it, without being told where they are.

The car struct is 0x484C bytes and we only know it holds position and
orientation somewhere. Rather than wait for that to be reverse-engineered, this
brute-forces it: every 8-byte-aligned offset is read as a double across all
recorded steps, and scored on whether it behaves like a world coordinate --
inside the track's bounding box (taken from the exported OBJ), moving, and
moving smoothly rather than jumping.

A correct x/z pair, plotted against the track outline, has to trace the road.
"""
import struct
import sys
from pathlib import Path

OUT = Path("/mnt/c/Games/IGNITION/_re/out")


def parse_args(argv):
    """Options take a value, so "--frame 0" must not leave "0" looking like a filename."""
    opts, pos, i = {}, [], 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    return opts, pos


def track_bounds(level):
    obj = OUT / "tracks" / f"{level}.obj"
    if not obj.exists():
        return None
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for line in obj.open():
        if line.startswith("v "):
            xyz = [float(v) for v in line.split()[1:4]]
            for i, v in enumerate(xyz):
                lo[i] = min(lo[i], v)
                hi[i] = max(hi[i], v)
    return lo, hi


def read_trace(path):
    d = path.read_bytes()
    if d[:7] != b"IGNSIM1":
        raise SystemExit(f"{path}: not a simulation recording")
    stride, car_bytes = struct.unpack_from("<II", d, 8)
    o = 16
    steps = []
    while o + 20 <= len(d):
        step, n, cb = struct.unpack_from("<III", d, o)
        acc = struct.unpack_from("<d", d, o + 12)[0]
        o += 20
        if cb != car_bytes or n == 0 or o + n * cb > len(d):
            break
        steps.append((step, n, acc, [d[o + i * cb: o + (i + 1) * cb] for i in range(n)]))
        o += n * cb
    return stride, car_bytes, steps


def main():
    opts, pos = parse_args(sys.argv[1:])
    level = opts.get("level", "Canada")
    path = Path(pos[0]) if pos else Path("/mnt/c/Games/IGNITION/ign_trace_sim.bin")
    if not path.exists():
        raise SystemExit(f"{path} not found - enable TRACE_SIM_STEPS in ign_compat.ini and play a race")

    stride, car_bytes, steps = read_trace(path)
    print(f"{path.name}: {len(steps)} steps, {steps[0][1] if steps else 0} cars, "
          f"{car_bytes} of {stride} bytes per car recorded")
    if len(steps) < 20:
        raise SystemExit("too few steps recorded to analyse")
    print(f"accumulator over first 5 steps: {[round(s[2], 4) for s in steps[:5]]}")

    b = track_bounds(level)
    if not b:
        raise SystemExit(f"no exported track for {level}")
    lo, hi = b
    print(f"{level} bounds  x[{lo[0]:.0f},{hi[0]:.0f}]  y[{lo[1]:.0f},{hi[1]:.0f}]  z[{lo[2]:.0f},{hi[2]:.0f}]")

    # Known offsets from the subsystem map (ghost recorder 0x00445C10): position
    # x/y/z are doubles at +0x00/+0x08/+0x10, speed at +0x118. The car struct
    # stores x and z biased by +25600 - the same bias the ghost packer removes -
    # so world coordinates are the stored value minus BIAS.
    BIAS = 25600.0

    def pose(car):
        x, y, z = (struct.unpack_from("<d", car, o)[0] for o in (0x00, 0x08, 0x10))
        return x - BIAS, y, z - BIAS

    ncars = steps[0][1]
    print("\nper car (x,z de-biased by %.0f):" % BIAS)
    best, best_dist = 0, -1.0
    for c in range(ncars):
        track = [pose(s[3][c]) for s in steps]
        spd = [struct.unpack_from("<d", s[3][c], 0x118)[0] for s in steps]
        dist = sum(abs(track[i + 1][0] - track[i][0]) + abs(track[i + 1][2] - track[i][2])
                   for i in range(len(track) - 1))
        inside = all(lo[0] <= x <= hi[0] and lo[2] <= z <= hi[2] for x, _, z in track)
        print(f"  car {c}: x[{min(t[0] for t in track):8.1f},{max(t[0] for t in track):8.1f}] "
              f"z[{min(t[2] for t in track):8.1f},{max(t[2] for t in track):8.1f}] "
              f"speed max {max(spd):7.2f}  travelled {dist:8.1f}  "
              f"{'inside bounds' if inside else 'OUTSIDE BOUNDS'}")
        if dist > best_dist:
            best, best_dist = c, dist
    print(f"  -> car {best} moved most ({best_dist:.1f} units)")

    moving = [s[3][best] for s in steps]
    xs = [pose(c)[0] for c in moving]
    zs = [pose(c)[2] for c in moving]
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (900, 900), "white")
        d = ImageDraw.Draw(img)
        sx = 860 / max(1e-9, hi[0] - lo[0]); sz = 860 / max(1e-9, hi[2] - lo[2])
        sc = min(sx, sz)
        pts = [(20 + (x - lo[0]) * sc, 20 + (z - lo[2]) * sc) for x, z in zip(xs, zs)]
        d.line(pts, fill="red", width=2)
        out = OUT / "tracks" / f"{level}_traced_path_car{best}.png"
        img.save(out)
        print(f"  path of car 0 plotted over {level} bounds -> {out}")
    except ImportError:
        pass
    car0 = [st[3][0] for st in steps]
    cands = []
    for off in range(0, car_bytes - 8, 4):
        try:
            vals = [struct.unpack_from("<d", c, off)[0] for c in car0]
        except struct.error:
            continue
        if any(v != v or abs(v) == float("inf") for v in vals):
            continue
        span = max(vals) - min(vals)
        if span == 0:
            continue
        # plausible as a world coordinate on some axis?
        axes = [i for i in range(3) if lo[i] - 500 <= min(vals) and max(vals) <= hi[i] + 500]
        if not axes:
            continue
        steps_d = [abs(vals[i + 1] - vals[i]) for i in range(len(vals) - 1)]
        biggest = max(steps_d)
        median = sorted(steps_d)[len(steps_d) // 2]
        # a real coordinate moves steadily; a reused scratch field jumps
        if median > 0 and biggest < median * 50:
            cands.append((off, axes, min(vals), max(vals), median, biggest))

    print(f"\n{len(cands)} offset(s) behave like a world coordinate for car 0:")
    for off, axes, mn, mx, med, big in cands[:24]:
        print(f"  +0x{off:04X}  axes {axes}  range [{mn:10.1f},{mx:10.1f}]  "
              f"median step {med:7.3f}  max step {big:8.3f}")
    if not cands:
        print("  none - the pose may be fixed-point, or outside the recorded prefix "
              "(raise TRACE_CAR_BYTES)")
    print("\nNext: pick the pair whose plot traces the road, then confirm against "
          "the subsystem map's field offsets.")


if __name__ == "__main__":
    main()
