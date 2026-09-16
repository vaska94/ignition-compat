#!/usr/bin/env python3
"""
funcdb.py -- build a complete function inventory + call graph + per-function
feature set for Ign_win.exe, and dump it to _re/out/funcdb.json.

Method (does NOT rely on linear sweep, which desynchronises on this binary):
  1. Parse .reloc (44k HIGHLOW entries): every absolute pointer in the image is
     listed, so function-pointer tables / callbacks / jump tables are exact.
  2. Seeds: PE entry, every reloc target in an executable region whose reloc
     *location* is in data (.data/.rdata) or is an immediate operand
     (push/mov imm) in code, plus jump-table owners' tables.
  3. Recursive descent from seeds. Direct call targets become new seeds.
     `jmp [reg*4+T]` jump tables are read entry-by-entry through the reloc set.
     Iterate to a fixed point, then assign bodies with the final start set
     (a jump/fallthrough into another function start = tail call).
  4. Gap scan: bytes in .text not covered by any function / jump table /
     padding are decoded; plausible code there becomes an "unreferenced" fn.
  5. Features per function: size, instruction count, x87 count, fixed-point
     idioms, absolute globals read/written, indexed global access, strings,
     imports, indirect calls, ret N, entry register reads, stack-arg estimate.

Usage: python funcdb.py            (writes _re/out/funcdb.json)
"""
import struct, json, collections, sys, re
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_OPT_SYNTAX_INTEL, CS_AC_READ, CS_AC_WRITE
from capstone.x86 import (X86_OP_MEM, X86_OP_IMM, X86_OP_REG, X86_GRP_FPU,
                          X86_REG_ESP, X86_REG_EBP, X86_REG_EAX, X86_REG_ECX, X86_REG_EDX,
                          X86_REG_EBX, X86_REG_ESI, X86_REG_EDI)

EXE = "/mnt/c/Games/IGNITION/Ign_win.exe"
OUT = "/mnt/c/Games/IGNITION/_re/out/funcdb.json"

pe = pefile.PE(EXE, fast_load=True)
pe.parse_data_directories(directories=[
    pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
    pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_BASERELOC']])
BASE = pe.OPTIONAL_HEADER.ImageBase
ENTRY = BASE + pe.OPTIONAL_HEADER.AddressOfEntryPoint

SECS = []
for s in pe.sections:
    nm = s.Name.rstrip(b'\0').decode()
    va = BASE + s.VirtualAddress
    vs = max(s.Misc_VirtualSize, s.SizeOfRawData)
    d = s.get_data()
    d = d + b"\0" * (vs - len(d)) if len(d) < vs else d
    SECS.append((nm, va, s.Misc_VirtualSize, d))
SEC = {nm: (va, vs, d) for nm, va, vs, d in SECS}
TEXT_LO, TEXT_HI = SEC['.text'][0], SEC['.text'][0] + SEC['.text'][1]
CODE_LO, CODE_HI = SEC['code'][0], SEC['code'][0] + SEC['code'][1]
DATA_LO, DATA_HI = SEC['.data'][0], SEC['.data'][0] + SEC['.data'][1]
RDATA_LO, RDATA_HI = SEC['.rdata'][0], SEC['.rdata'][0] + SEC['.rdata'][1]
IDATA_LO, IDATA_HI = SEC['.idata'][0], SEC['.idata'][0] + SEC['.idata'][1]

def secname(va):
    for nm, sva, vs, d in SECS:
        if sva <= va < sva + vs:
            return nm
    return None

# Code islands inside non-executable sections, discovered from direct call targets
# (e.g. the CDP intro decoder at 0x00499ABC in .data, called from 0x0041262F).
# Each island spans [target, target+ISLAND_SPAN) clipped to its section.
ISLANDS = []
ISLAND_SPAN = 0x1000
def is_exec(va):
    if TEXT_LO <= va < TEXT_HI or CODE_LO <= va < CODE_HI:
        return True
    return any(lo <= va < hi for lo, hi in ISLANDS)

def is_dataaddr(va):
    return (DATA_LO <= va < DATA_HI) or (RDATA_LO <= va < RDATA_HI)

def read(va, n):
    for nm, sva, vs, d in SECS:
        if sva <= va < sva + vs:
            o = va - sva
            return d[o:o + n]
    return b""

def dword(va):
    b = read(va, 4)
    return struct.unpack("<I", b)[0] if len(b) == 4 else None

def cstr(va, maxn=100):
    b = read(va, maxn)
    if not b:
        return None
    e = b.find(b"\0")
    if e < 3:
        return None
    s = b[:e]
    if all(32 <= c < 127 or c in (9, 10, 13) for c in s):
        return s.decode('latin1')
    return None

# ---------------------------------------------------------------- relocations
RELOC = {}  # location -> target
for blk in pe.DIRECTORY_ENTRY_BASERELOC:
    for e in blk.entries:
        if e.type == 3:
            loc = BASE + e.rva
            RELOC[loc] = dword(loc)

# ---------------------------------------------------------------- imports
IAT = {}
for e in pe.DIRECTORY_ENTRY_IMPORT:
    dll = e.dll.decode()
    for i in e.imports:
        IAT[i.address] = f"{dll}!{i.name.decode() if i.name else '#%d' % i.ordinal}"
THUNK = {}
for loc, tgt in RELOC.items():
    if tgt in IAT and TEXT_LO <= loc < TEXT_HI and read(loc - 2, 2) == b"\xff\x25":
        THUNK[loc - 2] = IAT[tgt]

md = Cs(CS_ARCH_X86, CS_MODE_32)
md.detail = True
md.syntax = CS_OPT_SYNTAX_INTEL

_icache = {}
def insn(va):
    """decode one instruction at va (cached)"""
    if va in _icache:
        return _icache[va]
    b = read(va, 16)
    r = None
    for i in md.disasm(b, va):
        r = i
        break
    _icache[va] = r
    return r

NORETURN = set()   # filled after CRT exit identification (see below)

def jump_table_based(ins, fstart, regconst):
    """`jmp [base + idx*4]` where base was loaded with `mov base, imm32` earlier in the
    same block (the .data CDP decoder at 0x00499AD5 uses `mov ecx,0x499B95`)."""
    ops = ins.operands
    if not ops or ops[0].type != X86_OP_MEM:
        return None
    m = ops[0].mem
    if m.base == 0 or m.index == 0 or m.scale != 4 or m.disp != 0 or m.base not in regconst:
        return None
    T = regconst[m.base]
    tg = []
    loc = T
    for k in range(256):
        if loc in RELOC and is_exec(RELOC[loc]):
            tg.append(RELOC[loc])
        elif tg:
            break
        loc += 4
    return (T, tg) if tg else None

def jump_table(ins, fstart):
    """`jmp [idx*4 + T]` -> (T, targets). T may live in .text or .data/.rdata
    (hand-written asm dispatchers keep tables in .data). Leading slots that are
    not relocated (unused low indices) are skipped."""
    ops = ins.operands
    if not ops or ops[0].type != X86_OP_MEM:
        return None
    m = ops[0].mem
    if m.base != 0 or m.index == 0 or m.scale != 4:
        return None
    T = m.disp & 0xFFFFFFFF
    if not (is_exec(T) or is_dataaddr(T)):
        return None
    tg = []
    loc = T
    k = 0
    while k < 1024:
        if loc in RELOC and is_exec(RELOC[loc]) and abs(RELOC[loc] - fstart) < 0x20000:
            tg.append(RELOC[loc])
        elif not tg and k < 16:
            pass
        else:
            break
        loc += 4
        k += 1
    return (T, tg) if tg else None

# ---------------------------------------------------------------- descent
def explore(start, starts, table_locs):
    """Recursive descent of one function. Returns dict with insn addrs, edges."""
    work = [start]
    seen = set()
    calls = []          # (site, target)
    tails = []          # (site, target)  jmp/fallthrough into another fn start
    icalls = []         # (site, kind, detail)
    tables = []         # (table VA, [targets])
    btargets = set()    # addresses reached as explicit branch / table targets
    calls_tail_pad = []
    bad = False
    while work:
        a = work.pop()
        regconst = {}
        while True:
            if a in seen:
                break
            if a != start and a in starts:
                tails.append((None, a))
                break
            if not is_exec(a) or a in table_locs:
                bad = True
                break
            i = insn(a)
            if i is None:
                bad = True
                break
            seen.add(a)
            mn = i.mnemonic
            nxt = a + i.size
            ops = i.operands
            if mn == "mov" and len(ops) == 2 and ops[0].type == X86_OP_REG and ops[1].type == X86_OP_IMM:
                regconst[ops[0].reg] = ops[1].imm & 0xFFFFFFFF
            if mn in ("ret", "retf", "iret", "iretd", "hlt", "int3"):
                break
            if mn == "call":
                if ops and ops[0].type == X86_OP_IMM:
                    t = ops[0].imm & 0xFFFFFFFF
                    calls.append((a, t))
                    if t in NORETURN:
                        break
                else:
                    o = ops[0]
                    if o.type == X86_OP_MEM:
                        disp = o.mem.disp & 0xFFFFFFFF
                        if o.mem.base == 0 and o.mem.index == 0:
                            if disp in IAT:
                                icalls.append((a, "import", IAT[disp]))
                                if IAT[disp] in ("KERNEL32.dll!ExitProcess",):
                                    break
                            else:
                                icalls.append((a, "global_fnptr", disp))
                        elif o.mem.base == 0:
                            icalls.append((a, "indexed_table", disp))
                        else:
                            icalls.append((a, "reg_disp", o.mem.disp))
                    else:
                        icalls.append((a, "reg", i.op_str))
                a = nxt
                continue
            if mn == "jmp":
                if ops[0].type == X86_OP_IMM:
                    t = ops[0].imm & 0xFFFFFFFF
                    if t in starts and t != start:
                        tails.append((a, t))
                    elif t in THUNK:
                        icalls.append((a, "import_tail", THUNK[t]))
                    elif TEXT_LO <= t < TEXT_HI and t != start and read(t - 1, 1) == b"\xcc" and not (start <= t < start + 16):
                        # MSVC never pads inside a function: a jmp to code that follows
                        # int3 padding is a tail call to a separate function
                        tails.append((a, t)); calls_tail_pad.append((a, t))
                    else:
                        work.append(t); btargets.add(t)
                    break
                jt = jump_table(i, start) or jump_table_based(i, start, regconst)
                if jt:
                    T, tg = jt
                    tables.append((T, tg))
                    for t in tg:
                        work.append(t); btargets.add(t)
                else:
                    o = ops[0]
                    if o.type == X86_OP_MEM and o.mem.base == 0 and o.mem.index == 0 and (o.mem.disp & 0xFFFFFFFF) in IAT:
                        icalls.append((a, "import_tail", IAT[o.mem.disp & 0xFFFFFFFF]))
                    else:
                        icalls.append((a, "jmp_indirect", i.op_str))
                break
            if i.group(7) or mn.startswith("j") or mn in ("loop", "loope", "loopne", "jecxz"):
                # conditional branch
                if ops and ops[0].type == X86_OP_IMM:
                    t = ops[0].imm & 0xFFFFFFFF
                    if t in starts and t != start:
                        tails.append((a, t))
                    else:
                        work.append(t); btargets.add(t)
                a = nxt
                continue
            a = nxt
    return dict(insns=seen, calls=calls, tails=tails, icalls=icalls, tables=tables, bad=bad, btargets=btargets, tailpad=calls_tail_pad)

# identify noreturn CRT exit helpers by pattern later; pre-seed known exit()
NORETURN.update({0x004697E0})

def looks_like_data(insns):
    """zero-filled data decodes as `add byte ptr [eax], al` (00 00). Reject
    candidates where that dominates -- e.g. the c_dfDIKeyboard object table at
    0x00477760 and DIDATAFORMAT at 0x00478760 embedded in .text."""
    n = len(insns)
    if not n:
        return True
    z = sum(1 for a in insns if read(a, 2) == b"\0\0")
    return z * 4 >= n

def data_seeds():
    """reloc targets into exec regions from data sections -> candidate fn ptrs"""
    s = {}
    for loc, t in RELOC.items():
        if not is_exec(t):
            continue
        ln = secname(loc)
        if ln in ('.data', '.rdata', '.idata', '.rsrc'):
            s.setdefault(t, set()).add(("data", loc))
    return s

def code_imm_seeds(insn_addrs):
    """reloc targets into exec regions that are IMMEDIATE operands of decoded code"""
    s = {}
    for a in insn_addrs:
        i = insn(a)
        for o in i.operands:
            if o.type == X86_OP_IMM:
                v = o.imm & 0xFFFFFFFF
                if is_exec(v) and i.mnemonic not in ("call", "jmp") and not i.mnemonic.startswith("j"):
                    # make sure it's relocated (true address, not a constant)
                    for k in range(a, a + i.size - 3):
                        if k in RELOC and RELOC[k] == v:
                            s.setdefault(v, set()).add(("code_imm", a))
                            break
    return s

def fixpoint(starts, seedinfo, table_locs):
    for it in range(40):
        funcs = {}
        new = set()
        for f in sorted(starts):
            r = explore(f, starts, table_locs)
            funcs[f] = r
            for site, t in r["calls"]:
                if is_exec(t) and t not in starts and t not in THUNK:
                    new.add(t)
                    seedinfo[t].add(("call", site))
            for site, t in r["tailpad"]:
                if t not in starts:
                    new.add(t)
                    seedinfo[t].add(("tailjmp", site))
            for T, tg in r["tables"]:
                if is_exec(T):
                    for k in range(len(tg) * 4):
                        table_locs.add(T + k)
        starts |= new
        if not new:
            return funcs
    return funcs

def build():
    seedinfo = collections.defaultdict(set)
    seedinfo[ENTRY].add(("pe_entry", 0))
    table_locs = set()
    starts = {ENTRY}
    weak = data_seeds()
    for t, why in weak.items():
        seedinfo[t] |= why
    demoted = {}
    for rnd in range(10):
        funcs = fixpoint(starts, seedinfo, table_locs)
        grew = False
        for f0, r0 in list(funcs.items()):
            for site, t in r0["calls"]:
                if not is_exec(t) and (DATA_LO <= t < DATA_HI or RDATA_LO <= t < RDATA_HI):
                    hi = (DATA_HI if DATA_LO <= t < DATA_HI else RDATA_HI)
                    ISLANDS.append((t, min(t + ISLAND_SPAN, hi)))
                    starts.add(t); seedinfo[t].add(("call_into_data", site)); grew = True
        if grew:
            print(f"code islands in data sections: {[(hex(a), hex(b)) for a, b in ISLANDS]}", file=sys.stderr)
            funcs = fixpoint(starts, seedinfo, table_locs)
        allins = set()
        for r in funcs.values():
            allins |= r["insns"]
        for t, why in code_imm_seeds(allins).items():
            seedinfo[t] |= why
            weak.setdefault(t, set()).update(why)
        owner = {}
        for f, r in funcs.items():
            for a in r["btargets"]:
                owner.setdefault(a, f)
        added = 0
        for t in sorted(weak):
            if t in starts or t in demoted:
                continue
            if t in owner:
                demoted[t] = owner[t]      # internal label (SEH scope entry, dispatch label)
                continue
            rr = explore(t, starts | {t}, table_locs)
            if rr["bad"] or looks_like_data(rr["insns"]):
                demoted[t] = -1            # pointer to data embedded in an exec section
                continue
            starts.add(t); added += 1
        print(f"round {rnd}: starts={len(starts)} added_weak={added} demoted={len(demoted)}", file=sys.stderr)
        if not added:
            break
    return starts, funcs, seedinfo, table_locs, demoted

starts, funcs, seedinfo, table_locs, demoted_map = build()
demoted = sorted(demoted_map)

def recompute():
    fs = {}
    for f in sorted(starts):
        fs[f] = explore(f, starts, table_locs)
    return fs

# ---------------------------------------------------------------- gap scan
# the coverage map spans .text and the `code` section (virtual concatenation)
CODE_OFF = TEXT_HI - TEXT_LO
covered = bytearray((TEXT_HI - TEXT_LO) + (CODE_HI - CODE_LO))
def cidx(a):
    if TEXT_LO <= a < TEXT_HI: return a - TEXT_LO
    if CODE_LO <= a < CODE_HI: return CODE_OFF + a - CODE_LO
    return None
def cover(a, n):
    for k in range(a, a + n):
        j = cidx(k)
        if j is not None:
            covered[j] = 1
for f, r in funcs.items():
    for a in r["insns"]:
        cover(a, insn(a).size)
for loc in table_locs:
    cover(loc, 1)
# thunk table + DIDATAFORMAT data in .text (0x00477760..0x00478778) handled as data by reloc presence
for loc in THUNK:
    cover(loc, 6)

PAD = {0x90, 0xCC}
def gap_ranges():
    out = []
    for lo, hi, off in ((TEXT_LO, TEXT_HI, 0), (CODE_LO, CODE_HI, CODE_OFF)):
        n = hi - lo
        k = 0
        while k < n:
            if covered[off + k]:
                k += 1
                continue
            s0 = k
            while k < n and not covered[off + k]:
                k += 1
            out.append((lo + s0, lo + k))
    return out

def is_cov2(a):
    j = cidx(a)
    return j is not None and covered[j]
if "--why" in sys.argv:
    for x in sys.argv[sys.argv.index("--why") + 1:]:
        c = int(x, 0)
        r = explore(c, starts | {c}, table_locs)
        gl = [g for g in gap_ranges() if g[0] <= c < g[1]]
        print(f"cand {c:#x} gap={[(hex(a), hex(b)) for a, b in gl]} bad={r['bad']} n={len(r['insns'])}")
        hi = gl[0][1] if gl else c
        for a in sorted(r["insns"]):
            if not (c <= a < hi) or is_cov2(a):
                i = insn(a)
                print(f"   clash {a:#x} {i.mnemonic} {i.op_str} covered={is_cov2(a)}")
                break
        # show where 'bad' came from: last few decoded addresses near table_locs / non-exec
        for a in sorted(r["insns"]):
            i = insn(a)
            if i.mnemonic == "jmp" and i.operands[0].type != X86_OP_IMM:
                print(f"   indirect jmp {a:#x} {i.op_str}")
    sys.exit(0)

unref = []
def is_cov(a):
    j = cidx(a)
    return j is not None and covered[j]
for it in range(1000):
    added = 0
    tried = 0
    for lo, hi in gap_ranges():
        blob = read(lo, hi - lo)
        p = 0
        while p < len(blob) and (blob[p] in PAD or blob[p] == 0 or blob[p:p+2] == b"\x87\xdb" or (p and blob[p-1:p+1] == b"\x87\xdb")):
            p += 1
        if p >= len(blob):
            continue
        cand = lo + p
        if hi - cand < 2:
            continue
        r = explore(cand, starts | {cand}, table_locs)
        if r["bad"] or not r["insns"] or looks_like_data(r["insns"]):
            cover(cand, 1); tried += 1
            continue
        # reject: flow reaches code already owned, or leaves the gap
        clash = any(is_cov(x) or not (cand <= x < hi) for x in r["insns"])
        if clash:
            # mark the leading bytes as examined so the next candidate is tried
            cover(cand, insn(cand).size if insn(cand) else 1)
            tried += 1
            continue
        starts.add(cand)
        seedinfo[cand].add(("gap_unreferenced", 0))
        unref.append(cand)
        for a in r["insns"]:
            cover(a, insn(a).size)
        funcs[cand] = r
        added += 1
    if not added and not tried:
        print(f"gap scan converged after {it} iters", file=sys.stderr)
        break

funcs = recompute()

# ---------------------------------------------------------------- extents
S = sorted(starts)
nextstart = {S[k]: (S[k + 1] if k + 1 < len(S) else None) for k in range(len(S))}
def region_end(f):
    hi = TEXT_HI if TEXT_LO <= f < TEXT_HI else (CODE_HI if CODE_LO <= f < CODE_HI else next((b for a, b in ISLANDS if a <= f < b), f + 1))
    n = nextstart[f]
    return min(n, hi) if n and n <= hi else hi

REG = {X86_REG_EAX: "eax", X86_REG_ECX: "ecx", X86_REG_EDX: "edx", X86_REG_EBX: "ebx", X86_REG_ESI: "esi", X86_REG_EDI: "edi", X86_REG_EBP: "ebp"}
def reg32(r):
    # map sub-registers to 32-bit family
    n = md.reg_name(r)
    fam = {"al": "eax", "ah": "eax", "ax": "eax", "eax": "eax", "cl": "ecx", "ch": "ecx", "cx": "ecx", "ecx": "ecx",
           "dl": "edx", "dh": "edx", "dx": "edx", "edx": "edx", "bl": "ebx", "bh": "ebx", "bx": "ebx", "ebx": "ebx",
           "si": "esi", "esi": "esi", "di": "edi", "edi": "edi", "bp": "ebp", "ebp": "ebp", "sp": "esp", "esp": "esp"}
    return fam.get(n)

FTOL = 0x0046950C
reach = collections.defaultdict(list)
for f in S:
    for a in funcs[f]["insns"]:
        reach[a].append(f)
owner = {}
for a, fl in reach.items():
    below = [f for f in fl if f <= a]
    owner[a] = max(below) if below else min(fl)
owned = collections.defaultdict(int)
owned_n = collections.defaultdict(int)
shared = collections.defaultdict(int)
for a, f in owner.items():
    owned[f] += insn(a).size
    owned_n[f] += 1
    if len(reach[a]) > 1:
        for g in reach[a]:
            if g != f:
                shared[g] += 1
records = {}
for f in S:
    r = funcs[f]
    endr = region_end(f)
    ins_in = sorted(a for a in r["insns"] if f <= a < endr)
    ins_out = sorted(a for a in r["insns"] if not (f <= a < endr))
    end = max((a + insn(a).size for a in ins_in), default=f)
    rec = dict(va=f, end=end, size=end - f, bytes=owned[f], ninsn=owned_n[f], reached_insn=len(r["insns"]),
               outside_chunks=len(ins_out), insns_owned_by_others=shared[f],
               section=secname(f), seeds=sorted({k for k, _ in seedinfo[f]}),
               callees=sorted({t for _, t in r["calls"]}), tails=sorted({t for _, t in r["tails"]}),
               icalls=[(s, k, d) for s, k, d in r["icalls"]], tables=[(T, len(tg)) for T, tg in r["tables"]],
               bad=r["bad"])
    fpu = 0; fixshift = 0; shrd = 0; wideimul = 0; idiv = 0; nsse = 0
    g_read = collections.Counter(); g_write = collections.Counter(); g_addr = collections.Counter()
    g_indexed = collections.Counter(); g_regdisp = collections.Counter()
    strings = {}; retn = set(); esp_args = 0; ebp_args = 0
    frame_ebp = False; code_calls = set(); floatconsts = set()
    add_esp_after = []
    for a in sorted(r["insns"]):
        i = insn(a)
        mn = i.mnemonic
        if i.group(X86_GRP_FPU):
            fpu += 1
        if mn in ("sar", "shr", "shl") and len(i.operands) == 2 and i.operands[1].type == X86_OP_IMM and i.operands[1].imm in (8, 10, 12, 14, 16):
            fixshift += 1
        if mn in ("shrd", "shld"):
            shrd += 1
        if mn in ("imul", "mul") and len(i.operands) == 1:
            wideimul += 1
        if mn in ("idiv", "div"):
            idiv += 1
        if mn in ("ret", "retf") and i.operands:
            retn.add(i.operands[0].imm)
        elif mn == "ret":
            retn.add(0)
        if mn == "call" and i.operands[0].type == X86_OP_IMM:
            t = i.operands[0].imm & 0xFFFFFFFF
            if CODE_LO <= t < CODE_HI and TEXT_LO <= f < TEXT_HI:
                code_calls.add(t)
            nx = insn(a + i.size)
            if nx and nx.mnemonic == "add" and nx.op_str.startswith("esp,") and nx.operands[1].type == X86_OP_IMM:
                add_esp_after.append((t, nx.operands[1].imm))
            elif nx and nx.mnemonic == "pop" and False:
                pass
            else:
                add_esp_after.append((t, 0))
        for o in i.operands:
            if o.type == X86_OP_MEM:
                disp = o.mem.disp & 0xFFFFFFFF
                if o.mem.base == 0 and o.mem.index == 0:
                    if is_dataaddr(disp) or IDATA_LO <= disp < IDATA_HI or CODE_LO <= disp < CODE_HI:
                        if o.access & CS_AC_WRITE:
                            g_write[disp] += 1
                        if o.access & CS_AC_READ or not (o.access & CS_AC_WRITE):
                            g_read[disp] += 1
                        if i.group(X86_GRP_FPU) and RDATA_LO <= disp < RDATA_HI:
                            floatconsts.add(disp)
                        s = cstr(disp)
                        if s and mn not in ("fld", "fmul", "fadd", "fsub", "fdiv", "fcomp", "fcom"):
                            strings[disp] = s
                elif o.mem.base == 0 and o.mem.index != 0:
                    if is_dataaddr(disp) or CODE_LO <= disp < CODE_HI:
                        g_indexed[disp] += 1
                elif o.mem.base != 0 and is_dataaddr(disp):
                    g_regdisp[disp] += 1
                if o.mem.base == X86_REG_EBP and o.mem.disp >= 8:
                    ebp_args = max(ebp_args, o.mem.disp)
            elif o.type == X86_OP_IMM:
                v = o.imm & 0xFFFFFFFF
                if mn in ("call", "jmp") or mn.startswith("j"):
                    continue
                if is_dataaddr(v) or CODE_LO <= v < CODE_HI:
                    # relocated?
                    if any((k in RELOC and RELOC[k] == v) for k in range(a, a + i.size - 3)):
                        g_addr[v] += 1
                        s = cstr(v)
                        if s:
                            strings[v] = s
        if mn == "mov" and i.op_str == "ebp, esp":
            frame_ebp = True
    # entry register reads-before-write (linear from entry until first control transfer)
    reads_before = []
    written = set()
    a = f
    for _ in range(40):
        i = insn(a)
        if i is None:
            break
        rr, ww = i.regs_access()
        rd = {reg32(x) for x in rr} - {None, "esp"}
        wr = {reg32(x) for x in ww} - {None, "esp"}
        if i.mnemonic == "push" and i.operands and i.operands[0].type == X86_OP_REG and reg32(i.operands[0].reg) in ("ebx", "esi", "edi", "ebp"):
            rd = set()
        if i.mnemonic == "xor" and len(i.operands) == 2 and i.operands[0].type == X86_OP_REG and i.operands[1].type == X86_OP_REG and i.operands[0].reg == i.operands[1].reg:
            rd = set()
        if i.mnemonic in ("sub", "and", "or") and len(i.operands) == 2 and i.operands[0].type == X86_OP_REG and i.operands[1].type == X86_OP_REG and i.operands[0].reg == i.operands[1].reg:
            rd = set()
        for x in sorted(rd):
            if x not in written and x not in reads_before and x != "ebp":
                reads_before.append(x)
        written |= wr
        if i.mnemonic in ("ret", "jmp", "call") or i.mnemonic.startswith("j"):
            break
        a += i.size
    # esp-relative arg estimate via linear stack-delta tracking
    delta = 0; pend = {}; maxarg = -1
    for a in ins_in:
        if a in pend:
            delta = pend[a]
        i = insn(a)
        mn = i.mnemonic
        for o in i.operands:
            if o.type == X86_OP_MEM and o.mem.base == X86_REG_ESP and o.mem.index == 0:
                off = o.mem.disp - delta
                if off >= 4 and off < 0x100:
                    maxarg = max(maxarg, (off - 4) // 4)
        if mn == "push":
            delta += 4
        elif mn == "pop":
            delta -= 4
        elif mn in ("pushal", "pushad"):
            delta += 32
        elif mn in ("popal", "popad"):
            delta -= 32
        elif mn in ("pushfd",):
            delta += 4
        elif mn in ("popfd",):
            delta -= 4
        elif mn == "sub" and i.op_str.startswith("esp,") and i.operands[1].type == X86_OP_IMM:
            delta += i.operands[1].imm
        elif mn == "add" and i.op_str.startswith("esp,") and i.operands[1].type == X86_OP_IMM:
            delta -= i.operands[1].imm
        elif (mn.startswith("j") and i.operands and i.operands[0].type == X86_OP_IMM):
            pend.setdefault(i.operands[0].imm & 0xFFFFFFFF, delta)
    if frame_ebp and ebp_args:
        nargs = (ebp_args - 8) // 4 + 1
    else:
        nargs = maxarg + 1
    rec.update(fpu=fpu, fixshift=fixshift, shrd=shrd, wideimul=wideimul, div=idiv,
               g_read=dict(g_read), g_write=dict(g_write), g_addr=dict(g_addr), g_indexed=dict(g_indexed),
               g_regdisp=dict(g_regdisp), strings=strings, retn=sorted(retn), frame_ebp=frame_ebp,
               entry_reg_reads=reads_before, nargs_est=nargs, code_calls=sorted(code_calls),
               floatconsts=sorted(floatconsts), callsite_cleanup=add_esp_after,
               calls_ftol=sum(1 for _, t in r["calls"] if t == FTOL))
    records[f] = rec

# callers
callers = collections.defaultdict(set)
for f, rec in records.items():
    for t in rec["callees"]:
        callers[t].add(f)
    for t in rec["tails"]:
        callers[t].add(f)
for f, rec in records.items():
    rec["callers"] = sorted(callers.get(f, ()))
    rec["seed_detail"] = sorted([(k, v) for k, v in seedinfo[f]])[:12]

out = dict(entry=ENTRY, n=len(records), thunks={hex(k): v for k, v in THUNK.items()},
           table_locs=len(table_locs) // 4, demoted_labels={hex(k): hex(v) for k, v in demoted_map.items()}, unreferenced=[hex(x) for x in unref],
           funcs={hex(f): r for f, r in records.items()})
def conv(o):
    if isinstance(o, dict):
        return {(hex(k) if isinstance(k, int) else k): conv(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [conv(x) for x in o]
    return o
json.dump(conv(out), open(OUT, "w"))
print(f"functions: {len(records)}  unreferenced(gap): {len(unref)}  demoted internal labels: {len(demoted)}", file=sys.stderr)
