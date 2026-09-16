#!/usr/bin/env python3
"""merge.py -- merge funcdb.json + metrics_extra.json + labels.tsv (anchors) +
labels_F*.tsv (region classification) into:
  _re/out/functions.csv       va,size,subsystem,confidence,callers,callees,notes
  _re/out/subsystem_stats.json per-subsystem aggregates used by notes/06
Liveness is recomputed: live = reachable (call/tail edges) from the PE entry,
from data-referenced function pointers, and from code immediates inside live code."""
import json, glob, collections, csv, os, statistics
R = "/mnt/c/Games/IGNITION/_re/out/"
db = json.load(open(R + "funcdb.json"))
F = {int(k, 16): v for k, v in db["funcs"].items()}
M = {int(k, 16): v for k, v in json.load(open(R + "metrics_extra.json")).items()}
H = lambda x: int(x, 16) if isinstance(x, str) else x

CRT_NAMES = {
 0x0046950C: "_ftol (sets/restores x87 CW for truncation)", 0x004695C0: "sprintf", 0x004693B0: "free",
 0x004697E0: "exit", 0x00469400: "malloc", 0x00469390: "fopen", 0x00469100: "fclose", 0x004692C0: "calloc",
 0x0046A9D0: "_close", 0x00470E90: "_open", 0x00469170: "fread", 0x0046ABD0: "_read", 0x004694E0: "rand (LCG, holdrand 0x4BB08C)",
 0x004694D0: "srand", 0x00469B20: "fwrite", 0x00469CA0: "strstr", 0x0046A1B0: "_findfirst", 0x0046A300: "_findnext",
 0x0046A440: "_findclose", 0x0046991A: "x87 math intrinsic wrapper (_CI* transcendental)", 0x00469652: "x87 math intrinsic wrapper (_CI*)",
 0x00469680: "_alldiv", 0x00469730: "_allmul", 0x00469950: "WinMainCRTStartup", 0x00472240: "__crtMessageBoxA",
 0x0046BC40: "_check_processor_feature", 0x00469840: "doexit", 0x004694C0: "free wrapper", 0x004694B0: "malloc wrapper (_nh_malloc)",
}

labels = {}
def load(path, prio):
    if not os.path.exists(path):
        return
    for ln in open(path, encoding="utf-8", errors="replace"):
        if ln.startswith("#") or not ln.strip():
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) < 2 or not p[0].strip().lower().startswith("0x"):
            continue
        try:
            va = int(p[0].strip(), 16)
        except ValueError:
            continue
        rec = dict(sub=p[1].strip(), role=(p[2].strip() if len(p) > 2 else ""),
                   conf=(p[3].strip() if len(p) > 3 else "INFERRED"), ev=(p[4].strip() if len(p) > 4 else ""), prio=prio, src=os.path.basename(path))
        if va not in labels or labels[va]["prio"] <= prio:
            labels[va] = rec
load(R + "labels.tsv", 1)
for p in sorted(glob.glob(R + "labels_F*.tsv")) + sorted(glob.glob(R + "labels_Z*.tsv")):
    load(p, 2)
for va, nm in CRT_NAMES.items():
    if va in F:
        labels[va] = dict(sub="crt", role=nm, conf="CERTAIN" if va in (0x004694E0, 0x004694D0, 0x00469950, 0x004697E0) else "INFERRED",
                          ev="CRT idiom/imports", prio=3, src="merge")

# ---------------------------------------------------------------- liveness
edges = collections.defaultdict(set)
for f, r in F.items():
    for t in r["callees"] + r["tails"]:
        t = H(t)
        if t in F:
            edges[f].add(t)
imm_refs = collections.defaultdict(set)   # function -> functions whose address it loads as immediate
for f, r in F.items():
    for k, v in r["seed_detail"]:
        if k == "code_imm":
            site = H(v)
            owner = max((g for g in F if g <= site < max(F[g]["end"], g + 1)), default=None)
            if owner is not None:
                imm_refs[owner].add(f)
roots = {db["entry"]} | {f for f, r in F.items() if "data" in r["seeds"]}
live = set()
work = list(roots)
while work:
    f = work.pop()
    if f in live:
        continue
    live.add(f)
    work.extend(edges[f] | imm_refs[f])
# pointer tables that are only referenced from dead data are still counted live (conservative)

def sub(f):
    if f in labels:
        return labels[f]["sub"]
    if f >= 0x0064E000:
        return "rasterizer"
    if f >= 0x00469100:
        return "crt"
    return "unlabelled"

rows = []
for f in sorted(F):
    r = F[f]; m = M.get(f, {})
    lb = labels.get(f, dict(sub=sub(f), role="", conf="LOW", ev=""))
    callers = [H(c) for c in r["callers"]]
    callees = sorted({H(c) for c in r["callees"]} | {H(c) for c in r["tails"]})
    notes = []
    if lb["role"]: notes.append(lb["role"])
    if f not in live: notes.append("DEAD(unreachable)")
    if r["fpu"]: notes.append(f"x87={r['fpu']} (sgl={m.get('x87_single',0)} dbl={m.get('x87_double',0)} int={m.get('x87_int',0)})")
    if r["fixshift"] + r["shrd"]: notes.append(f"fixshift={r['fixshift']+r['shrd']}")
    regs = [x for x in r["entry_reg_reads"] if x in ("eax", "ecx", "edx", "ebx", "esi", "edi")]
    if regs: notes.append("regargs=" + "/".join(regs))
    if any(r["retn"]): notes.append(f"stdcall ret{max(r['retn'])}")
    if r["code_calls"]: notes.append("calls `code`")
    if m.get("fldcw") or m.get("fninit"): notes.append(f"FPU-CW fldcw={m.get('fldcw',0)} fninit={m.get('fninit',0)}")
    if m.get("bulk_fixed"): notes.append(f"fixed-addr block ops={len(m['bulk_fixed'])}")
    if lb.get("ev"): notes.append("ev: " + lb["ev"])
    rows.append(dict(va=f"0x{f:08X}", size=r["bytes"], subsystem=lb["sub"], confidence=lb["conf"],
                     callers=f"{len(callers)}:" + ";".join(f"{c:X}" for c in callers[:8]),
                     callees=f"{len(callees)}:" + ";".join(f"{c:X}" for c in callees[:8]),
                     notes=" | ".join(notes)))
with open(R + "functions.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["va", "size", "subsystem", "confidence", "callers", "callees", "notes"])
    w.writeheader()
    w.writerows(rows)

# ---------------------------------------------------------------- aggregates
S = collections.defaultdict(lambda: dict(n=0, live=0, bytes=0, live_bytes=0, fns=[], fpu=0, sgl=0, dbl=0, ext=0, xint=0, ftol=0,
                                         fixshift=0, wideimul=0, regpass=0, stdcall=0, fpu_fns=0, glob=[], noarg_glob=0,
                                         gwrites=0, idx=0, bulk=0, rep=0, conf=collections.Counter(), out=collections.Counter(),
                                         inn=collections.Counter(), gl=collections.Counter(), fldcw=0))
gl_users = collections.defaultdict(set)
for f, r in F.items():
    for k in ("g_read", "g_write", "g_addr", "g_indexed"):
        for a in r[k]:
            gl_users[H(a)].add(sub(f) if f not in labels else labels[f]["sub"])
for f, r in F.items():
    s = labels[f]["sub"] if f in labels else sub(f)
    m = M.get(f, {})
    a = S[s]
    a["n"] += 1; a["bytes"] += r["bytes"]
    a["conf"][labels[f]["conf"] if f in labels else "LOW"] += 1
    if f in live:
        a["live"] += 1; a["live_bytes"] += r["bytes"]
    a["fns"].append((r["bytes"], f, labels.get(f, {}).get("role", ""), f in live))
    if f not in live:
        continue
    a["fpu"] += r["fpu"]; a["fpu_fns"] += 1 if r["fpu"] else 0
    a["sgl"] += m.get("x87_single", 0); a["dbl"] += m.get("x87_double", 0); a["ext"] += m.get("x87_ext", 0); a["xint"] += m.get("x87_int", 0)
    a["ftol"] += r["calls_ftol"]; a["fixshift"] += r["fixshift"] + r["shrd"]; a["wideimul"] += r["wideimul"]
    a["fldcw"] += m.get("fldcw", 0) + m.get("fninit", 0)
    if any(x in ("eax", "ecx", "edx", "ebx", "esi", "edi") for x in r["entry_reg_reads"]): a["regpass"] += 1
    if any(r["retn"]): a["stdcall"] += 1
    g = set(map(H, r["g_read"])) | set(map(H, r["g_write"])) | set(map(H, r["g_addr"])) | set(map(H, r["g_indexed"]))
    a["glob"].append(len(g))
    if r["nargs_est"] <= 0 and g: a["noarg_glob"] += 1
    a["gwrites"] += sum(r["g_write"].values()); a["idx"] += m.get("idx_data", 0)
    a["bulk"] += len(m.get("bulk_fixed", [])); a["rep"] += len(m.get("rep_fixed", []))
    for gg in g:
        a["gl"][gg] += 1
    for t in edges[f]:
        ts = labels[t]["sub"] if t in labels else sub(t)
        if ts != s:
            a["out"][ts] += 1
            S[ts]["inn"][s] += 1
out = {}
for s, a in S.items():
    fns = sorted(a["fns"], reverse=True)
    excl = [(g, c) for g, c in a["gl"].most_common(400) if gl_users[g] == {s}][:12]
    shared = [(g, c, sorted(gl_users[g])) for g, c in a["gl"].most_common(12)]
    out[s] = dict(n=a["n"], live=a["live"], dead=a["n"] - a["live"], bytes=a["bytes"], live_bytes=a["live_bytes"],
                  largest=[(f"0x{f:08X}", b, role, lv) for b, f, role, lv in fns[:8]],
                  fpu=a["fpu"], fpu_fns=a["fpu_fns"], x87_single=a["sgl"], x87_double=a["dbl"], x87_ext=a["ext"], x87_int=a["xint"],
                  ftol_calls=a["ftol"], fixshift=a["fixshift"], wideimul=a["wideimul"], fpu_cw_changes=a["fldcw"],
                  regpass_fns=a["regpass"], stdcall_fns=a["stdcall"],
                  median_globals=(statistics.median(a["glob"]) if a["glob"] else 0), mean_globals=(round(sum(a["glob"]) / len(a["glob"]), 1) if a["glob"] else 0),
                  noarg_global_fns=a["noarg_glob"], global_writes=a["gwrites"], indexed_data=a["idx"], bulk_fixed=a["bulk"], rep_fixed=a["rep"],
                  confidence=dict(a["conf"]), calls_out=a["out"].most_common(10), called_from=a["inn"].most_common(10),
                  exclusive_globals=[(f"0x{g:08X}", c) for g, c in excl],
                  top_globals=[(f"0x{g:08X}", c, u) for g, c, u in shared])
json.dump(out, open(R + "subsystem_stats.json", "w"), indent=1)
tot_live = len(live)
print(f"functions={len(F)} live={tot_live} dead={len(F)-tot_live} labelled={sum(1 for f in F if f in labels)}")
for s in sorted(out, key=lambda s: -out[s]["bytes"]):
    o = out[s]
    print(f"{s:14s} n={o['n']:4d} live={o['live']:4d} bytes={o['bytes']:7d} live_b={o['live_bytes']:7d} fpu={o['fpu']:5d} "
          f"sgl/dbl/int={o['x87_single']}/{o['x87_double']}/{o['x87_int']} fix={o['fixshift']} reg={o['regpass_fns']} "
          f"medG={o['median_globals']} noargG={o['noarg_global_fns']} bulk={o['bulk_fixed']} rep={o['rep_fixed']}")
