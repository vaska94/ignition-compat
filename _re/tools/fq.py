#!/usr/bin/env python3
"""fq.py -- query _re/out/funcdb.json (built by funcdb.py)

  fq.py card <va> [...]         full feature card for a function
  fq.py region <lo> <hi>        one line per function in [lo,hi)
  fq.py gx <global> [...]       functions reading/writing/addressing a global
                                (exact VA, or lo-hi range like 0x552F00-0x553100)
  fq.py callers <va>            transitive callers up to depth 4
  fq.py str <substring>         functions referencing strings containing text
Optional labels file _re/out/labels.tsv (va<TAB>subsystem<TAB>role) is shown
when present, so partial classification is visible in every listing.
"""
import json, sys, os, collections
R = "/mnt/c/Games/IGNITION/_re/out/"
db = json.load(open(R + "funcdb.json"))
F = {int(k, 16): v for k, v in db["funcs"].items()}
L = {}
if os.path.exists(R + "labels.tsv"):
    for ln in open(R + "labels.tsv"):
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 2 and p[0].startswith("0x"):
            L[int(p[0], 16)] = (p[1], p[2] if len(p) > 2 else "")

def H(x): return int(x, 16) if isinstance(x, str) else x
def lab(f):
    return f"[{L[f][0]}:{L[f][1][:40]}]" if f in L else ""

def imports(r):
    return sorted({d.split("!")[1] for s, k, d in r["icalls"] if k.startswith("import")})

def short(r, n=3):
    g = collections.Counter()
    for k in ("g_read", "g_write", "g_addr"):
        for a, c in r[k].items():
            g[a] += c
    return [a for a, _ in g.most_common(n)]

def line(f):
    r = F[f]
    s = list(r["strings"].values())[:2]
    return (f"{f:#010x} {r['bytes']:6d}b fpu={r['fpu']:<3d} fx={r['fixshift']+r['shrd']:<3d} "
            f"in={len(r['callers']):<3d} out={len(r['callees']):<3d} {'/'.join(x[0] for x in r['seeds'])[:6]:6s} "
            f"ret={r['retn']} reg={''.join(x[1] for x in r['entry_reg_reads'])} "
            f"imp={imports(r)[:3]} g={short(r)} s={[x[:28] for x in s]} {lab(f)}")

def card(f):
    r = F[f]
    print(line(f))
    print("  seeds:", r["seed_detail"][:6])
    print("  callers:", [f"{H(c):#x}{lab(H(c))}" for c in r["callers"][:20]])
    print("  callees:", [f"{H(c):#x}{lab(H(c))}" for c in r["callees"][:30]], " tails:", r["tails"][:5])
    print("  code_calls:", r["code_calls"], " icalls:", [(s, k, d) for s, k, d in r["icalls"]][:12])
    print("  reads:", sorted(r["g_read"].items(), key=lambda x: -x[1])[:25])
    print("  writes:", sorted(r["g_write"].items(), key=lambda x: -x[1])[:25])
    print("  addr-of:", sorted(r["g_addr"].items(), key=lambda x: -x[1])[:15])
    print("  indexed:", list(r["g_indexed"].items())[:12], " regdisp:", list(r["g_regdisp"].items())[:8])
    print("  strings:", list(r["strings"].values())[:12])
    print(f"  fpu={r['fpu']} ftol={r['calls_ftol']} fixshift={r['fixshift']} shrd={r['shrd']} wideimul={r['wideimul']} div={r['div']} "
          f"floatconsts={r['floatconsts'][:8]} nargs_est={r['nargs_est']} ebpframe={r['frame_ebp']} tables={r['tables']}")

def inrange(a, spec):
    if "-" in spec:
        lo, hi = spec.split("-")
        return int(lo, 16) <= a < int(hi, 16)
    return a == int(spec, 16)

cmd = sys.argv[1]
if cmd == "card":
    for x in sys.argv[2:]:
        card(int(x, 16)); print()
elif cmd == "region":
    lo, hi = int(sys.argv[2], 16), int(sys.argv[3], 16)
    for f in sorted(F):
        if lo <= f < hi:
            print(line(f))
elif cmd == "gx":
    for spec in sys.argv[2:]:
        print("==", spec)
        for f in sorted(F):
            r = F[f]
            hits = []
            for k in ("g_read", "g_write", "g_addr", "g_indexed", "g_regdisp"):
                n = sum(c for a, c in r[k].items() if inrange(int(a, 16), spec))
                if n: hits.append(f"{k[2:]}={n}")
            if hits:
                print(f"  {f:#010x} {r['bytes']:6d}b {' '.join(hits)} {lab(f)}")
elif cmd == "callers":
    f0 = int(sys.argv[2], 16)
    def up(f, d, seen):
        if d > 4 or f in seen: return
        seen.add(f)
        for c in F[f]["callers"]:
            print("  " * d + f"{H(c):#x} {lab(H(c))}")
            up(H(c), d + 1, seen)
    up(f0, 0, set())
elif cmd == "str":
    t = sys.argv[2].lower()
    for f in sorted(F):
        ss = [s for s in F[f]["strings"].values() if t in s.lower()]
        if ss:
            print(f"{f:#010x} {ss[:4]} {lab(f)}")
