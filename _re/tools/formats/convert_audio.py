#!/usr/bin/env python3
"""Convert / validate every Ignition audio asset.

  * every .PAT and .XI sample -> 16-bit PCM mono WAV in _re/out/audio/ at the
    rate the ENGINE uses (PAT header rate; XI forced 22050), with a `smpl` loop
    when the source file declares a loop, plus a .json sidecar holding the
    file-declared loop and the engine-effective loop region.
  * every .WAV and ENGINE.INF: parsed, byte-accounted, engine view listed.
  * sanity metrics (lag-1 autocorrelation, zero-crossing rate, spectral
    flatness) for the correct decode and for a deliberately wrong decode.

Run from the game directory:  python _re/tools/formats/convert_audio.py
Writes _re/out/audio/_report.txt and _re/out/audio/_index.json.
"""
import os, sys, json, struct, hashlib, math
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pat, xi, wav, engine_inf

ROOT = "/mnt/c/Games/IGNITION"
OUT = os.path.join(ROOT, "_re", "out", "audio")
SKIP = ("_re", "dist", "ign_compat")


def files():
    res = []
    for d, dirs, fs in os.walk(ROOT):
        rel = os.path.relpath(d, ROOT)
        if rel.split(os.sep)[0] in SKIP:
            dirs[:] = []
            continue
        for f in fs:
            e = f.rsplit(".", 1)[-1].lower() if "." in f else ""
            if e in ("pat", "xi", "wav") or f.upper() == "ENGINE.INF":
                res.append(os.path.join(rel, f))
    return sorted(res, key=str.lower)


def metrics(x):
    f = x.astype(np.float64) / 32768.0
    m = {"n": int(len(f)), "peak": round(float(np.abs(f).max()), 4) if len(f) else 0,
         "rms": round(float(np.sqrt(np.mean(f * f))), 4) if len(f) else 0,
         "dc": round(float(f.mean()), 4) if len(f) else 0}
    if len(f) > 2 and f.std() > 0:
        m["ac1"] = round(float(np.corrcoef(f[:-1], f[1:])[0, 1]), 4)
        m["zcr"] = round(float(np.mean(np.signbit(f[1:]) != np.signbit(f[:-1]))), 4)
        N = 1024
        k = len(f) // N
        if k >= 1:
            fr = f[: k * N].reshape(k, N) * np.hanning(N)
            P = (np.abs(np.fft.rfft(fr, axis=1)) ** 2).mean(axis=0)[1:] + 1e-20
            m["flatness"] = round(float(np.exp(np.mean(np.log(P))) / np.mean(P)), 4)
    return m


def write_wav(path, x, rate, loop=None, unity=60):
    data = x.astype("<i2").tobytes()
    body = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body += b"data" + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")
    if loop:
        start, end_incl, ltype = loop
        smpl = struct.pack("<9I", 0, 0, int(round(1e9 / rate)), max(0, min(127, unity)), 0, 0, 0, 1, 0)
        smpl += struct.pack("<6I", 0, ltype, start, end_incl, 0, 0)
        body += b"smpl" + struct.pack("<I", len(smpl)) + smpl
    with open(path, "wb") as fp:
        fp.write(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)


def outname(rel):
    return rel.rsplit(".", 1)[0].replace(os.sep, "_").replace("/", "_")


def main():
    os.makedirs(OUT, exist_ok=True)
    rep, index, names = [], {}, {}
    counts = {"pat": [0, 0], "xi": [0, 0], "wav": [0, 0], "inf": [0, 0]}
    for rel in files():
        p = os.path.join(ROOT, rel)
        b = open(p, "rb").read()
        ext = "inf" if rel.upper().endswith("ENGINE.INF") else rel.rsplit(".", 1)[-1].lower()
        ent = {"file": rel, "size": len(b), "md5": hashlib.md5(b).hexdigest()}
        if ext == "pat":
            h = pat.parse(b)
            s = next(pat.samples(h))
            ev = pat.engine_view(h)
            x = pat.decode(s)
            alt = pat.decode(s, force_unsigned=not bool(s["modes"] & pat.MODE_UNSIGNED))
            fl = s["modes"]
            bps = 2 if fl & pat.MODE_16BIT else 1
            file_loop = None
            if fl & pat.MODE_LOOP:
                file_loop = (s["loop_start"] // bps, s["loop_end"] // bps - 1, 1 if fl & pat.MODE_BIDIR else 0)
            unity = int(round(69 + 12 * math.log2(s["root_frequency"] / 440000.0))) if s["root_frequency"] else 60
            ent.update(format="GF1 patch", modes=f"0x{fl:02X}", rate=s["sample_rate"], bits=8 * bps,
                       unsigned=bool(fl & pat.MODE_UNSIGNED), file_loop_flag=bool(fl & pat.MODE_LOOP),
                       bidir_flag=bool(fl & pat.MODE_BIDIR),
                       file_loop_bytes=[s["loop_start"], s["loop_end"]], root_frequency_mHz=s["root_frequency"],
                       samples_in_file=sum(1 for _ in pat.samples(h)))
        elif ext == "xi":
            h = xi.parse(b)
            s = h["sample_list"][0]
            ev = xi.engine_view(h)
            x = xi.decode(s)
            alt = xi.decode(s, delta=False)
            bps = 2 if s["type"] & 0x10 else 1
            lt = s["type"] & 3
            file_loop = None
            if lt and s["loop_length"]:
                file_loop = (s["loop_start"] // bps, (s["loop_start"] + s["loop_length"]) // bps - 1, 1 if lt == 2 else 0)
            unity = 60 - s["relative_note"]
            ent.update(format="FT2 XI", version=f"0x{h['version']:04X}", tracker=h["tracker_name"].decode("latin1").strip(),
                       rate=22050, bits=8 * bps, loop_type=lt, relative_note=s["relative_note"],
                       finetune=s["finetune"], file_loop_bytes=[s["loop_start"], s["loop_length"]],
                       samples_in_file=h["num_samples"], sample_name=s["name"].rstrip(b"\0").decode("latin1"))
        elif ext == "wav":
            h = wav.parse(b)
            ev = wav.engine_view(h)
            fmt = wav.chunk(h, "fmt ")
            sm = wav.chunk(h, "smpl")
            ent.update(format="RIFF WAVE", riff_size_ok=h["riff_size"] + 8 == len(b),
                       chunks=[f"{c['id']}:{c['size']}" for c in h["chunks"]],
                       fmt=[fmt["format_tag"], fmt["channels"], fmt["rate"], fmt["bits"]],
                       smpl_loops=(sm["loops"] if sm else None))
        else:
            h = engine_inf.parse(b)
            ev = {nm: [min(h[nm]), max(h[nm])] for nm in engine_inf.NAMES}
            ent.update(format="ENGINE.INF")
        ent["accounting"] = h["_acct"].summary()
        ent["engine"] = ev
        counts[ext][0] += 1
        if ent["accounting"] == "fully accounted":
            counts[ext][1] += 1
        if ext in ("pat", "xi"):
            nm = outname(rel)
            if nm in names:
                nm += "_" + ext
            names[nm] = rel
            loop = file_loop
            write_wav(os.path.join(OUT, nm + ".wav"), x, ent["rate"], loop, unity)
            ent["output"] = f"_re/out/audio/{nm}.wav"
            ent["wav_smpl_loop_frames_inclusive"] = list(loop) if loop else None
            ent["engine_loop_region_frames"] = [ev["loop_start"], ev["loop_start"] + ev["loop_length"]]
            ent["metrics"] = metrics(x)
            ent["metrics_wrong_decode"] = metrics(alt)
            with open(os.path.join(OUT, nm + ".json"), "w") as fp:
                json.dump(ent, fp, indent=1, default=str)
        index[rel] = ent
        line = f"{ext.upper():4s} {rel:48s} {len(b):7d}B  {ent['accounting']}"
        if ext in ("pat", "xi"):
            m, w = ent["metrics"], ent["metrics_wrong_decode"]
            line += (f" | {ent['bits']}b {ent['rate']}Hz frames={ev['length_frames']} "
                     f"fileloop={ent['wav_smpl_loop_frames_inclusive']} engineloop={ent['engine_loop_region_frames']}"
                     f" | ac1={m.get('ac1')} zcr={m.get('zcr')} flat={m.get('flatness')} peak={m['peak']}"
                     f" || wrong: ac1={w.get('ac1')} zcr={w.get('zcr')} flat={w.get('flatness')}")
        elif ext == "wav":
            line += f" | fmt={ent['fmt']} chunks={' '.join(ent['chunks'])} engine={ev}"
        else:
            line += f" | {ev}"
        rep.append(line)
    rep.append("")
    for k, (n, ok) in counts.items():
        rep.append(f"{k.upper()}: {n} files, {ok} fully accounted")
    open(os.path.join(OUT, "_report.txt"), "w").write("\n".join(rep) + "\n")
    json.dump(index, open(os.path.join(OUT, "_index.json"), "w"), indent=1, default=str)
    print("\n".join(rep))


if __name__ == "__main__":
    main()
