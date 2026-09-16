#!/usr/bin/env python3
"""
gst_plot.py -- overlay a decoded ghost lap on a level's top-down track render.

THIS IS THE ACCEPTANCE TEST for the .GST decode in gst.py.  The ghost format has
never been checked against real data (there are no .GST files in this install).
A correct decode must trace the road: the ghost polyline should follow the red
drivable surface of the track render for one full lap and close back on itself.
If it draws a blob, a diagonal streak, or wanders off the map, the field order,
the +25600 bias, or the record stride is wrong.

The projection is reproduced EXACTLY from _re/tools/formats/export_tracks.py's
render_png(), so the overlay lines up with the existing <Level>_topdown.png:

    tris   = every (x, z) triangle of every PLC-placed mesh
    mnx/mnz = min over all those vertices
    sc      = (1600 - 40) / max(x_extent, z_extent)
    pixel   = ((x - mnx) * sc + 20, (z - mnz) * sc + 40)

Ghost coordinates need NO adjustment: a .GST stores RAW MESH x/z (the recorder
subtracts the engine's +25600 bias at 0x00445C3C / 0x00445CA5), which is precisely
the space the .PLC/.MSH vertices above are in.  See gst.py "COORDINATE SPACE".

usage:
    gst_plot.py <file.GST> [level] [-o out.png] [--all-samples] [--size 1600]

    level defaults to the level named by the ghost's own track-id field.
    Pass a level explicitly (AUSTRIA, BRAZIL, CANADA, CARIB, ICELAND, JAPAN, USA)
    to plot a ghost against a track it does not claim to belong to.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gst as gstmod                                  # noqa: E402
import ignition_formats as F                          # noqa: E402

GAME = "/mnt/c/Games/IGNITION"
TRACKS_OUT = os.path.join(GAME, "_re", "out", "tracks")
RENDER_SIZE = 1600        # must match export_tracks.render_png's `size`


def find_level_dir(level):
    root = os.path.join(GAME, "LEVELS")
    for d in sorted(os.listdir(root)):
        if d.upper() == level.upper() and os.path.isdir(os.path.join(root, d)):
            return os.path.join(root, d), d
    raise SystemExit("no LEVELS directory matching %r (have: %s)"
                     % (level, ", ".join(sorted(os.listdir(root)))))


def find(d, ext):
    for x in os.listdir(d):
        if x.upper().endswith(ext) and os.path.isfile(os.path.join(d, x)):
            return os.path.join(d, x)
    raise SystemExit("no %s in %s" % (ext, d))


def track_projection(level_dir, size=RENDER_SIZE):
    """Recompute export_tracks.render_png's transform from the level geometry."""
    with open(find(level_dir, ".PLC"), "rb") as fh:
        plc, _ = F.parse_plc(fh.read())
    with open(find(level_dir, ".MSH"), "rb") as fh:
        msh, _ = F.parse_msh(fh.read())
    xs, zs = [], []
    for r in plc["recs"]:
        m = msh["by_word"][r["msh_off"]]
        for f in m["faces"]:
            for i in f["idx"]:
                v = m["verts"][i]
                xs.append(v[0] + r["x"])
                zs.append(v[2] + r["z"])
    mnx, mxx = min(xs), max(xs)
    mnz, mxz = min(zs), max(zs)
    sc = (size - 40) / max(mxx - mnx, mxz - mnz)
    W = int((mxx - mnx) * sc) + 40
    H = int((mxz - mnz) * sc) + 60

    def P(x, z):
        return ((x - mnx) * sc + 20, (z - mnz) * sc + 40)

    return P, (W, H), (mnx, mxx, mnz, mxz)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        return 2
    path = args[0]
    out = argv[argv.index("-o") + 1] if "-o" in argv else None
    size = int(argv[argv.index("--size") + 1]) if "--size" in argv else RENDER_SIZE
    all_samples = "--all-samples" in argv

    ghost = gstmod.Ghost.load(path, strict=False)
    print(ghost.describe(4))
    try:
        ghost.validate()
        print("  structural validation: PASS")
    except gstmod.GstError as exc:
        print("  structural validation: FAIL")
        print(exc)
        print("  (plotting anyway so you can see what the decode produced)")

    level = args[1] if len(args) > 1 else ghost.level
    if level in ("?", None):
        raise SystemExit("ghost has an unknown track id %d; pass a level name explicitly"
                         % ghost.track_id)
    level_dir, level_name = find_level_dir(level)
    print("\nplotting against LEVELS/%s" % level_name)

    P, (W, H), (mnx, mxx, mnz, mxz) = track_projection(level_dir, size)

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise SystemExit("Pillow is required: pip install pillow")

    base = os.path.join(TRACKS_OUT, "%s_topdown.png" % level_name)
    if os.path.exists(base):
        im = Image.open(base).convert("RGB")
        if im.size != (W, H):
            print("WARNING: %s is %dx%d but this projection computes %dx%d -- the render "
                  "was made with different settings, so the overlay may be offset."
                  % (os.path.basename(base), im.size[0], im.size[1], W, H))
    else:
        print("WARNING: %s not found; drawing on a blank canvas "
              "(run export_tracks.py to generate it)" % base)
        im = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(im)

    n = gstmod.RECORD_COUNT if all_samples else ghost.active_samples()
    pts = ghost.path_xz(n)
    gx = [p[0] for p in pts]
    gz = [p[1] for p in pts]

    print("\n-- decode sanity --")
    print("  track world x %8.0f .. %-8.0f   z %8.0f .. %.0f" % (mnx, mxx, mnz, mxz))
    print("  ghost world x %8d .. %-8d   z %8d .. %d"
          % (min(gx), max(gx), min(gz), max(gz)))
    inside = sum(1 for x, z in pts if mnx <= x <= mxx and mnz <= z <= mxz)
    pct = 100.0 * inside / len(pts)
    print("  ghost samples inside the track bounding box: %d/%d (%.1f%%)" % (inside, len(pts), pct))
    if pct < 90.0:
        print("  >> SUSPECT DECODE: most of the ghost falls outside the track. Check the")
        print("     record stride, the field order, or whether the +%d engine bias has"
              % gstmod.POS_BIAS)
        print("     been applied twice (a .GST already stores raw mesh coordinates).")
    else:
        print("  >> plausible: the ghost lies on the track. Confirm visually that it")
        print("     follows the road rather than cutting across it.")

    # polyline, coloured from green (lap start) to magenta (lap end)
    px = [P(x, z) for x, z in pts]
    for i in range(len(px) - 1):
        t = i / max(1, len(px) - 2)
        col = (int(255 * t), int(200 * (1 - t)), int(255 * t))
        dr.line([px[i], px[i + 1]], fill=col, width=3)
    if px:
        x0, y0 = px[0]
        dr.ellipse([x0 - 7, y0 - 7, x0 + 7, y0 + 7], outline=(0, 0, 0), width=3)
        dr.text((x0 + 10, y0 - 6), "lap start", fill=(0, 0, 0))
    dr.text((20, 22), "ghost: %s  %s  %.2fs  %d samples  (green=start -> magenta=end)"
            % (os.path.basename(path), ghost.name, ghost.lap_time_s, len(pts)), fill=(160, 0, 160))

    if out is None:
        out = os.path.join(TRACKS_OUT, "%s_ghost.png"
                           % os.path.splitext(os.path.basename(path))[0])
    im.save(out)
    print("\nwrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
