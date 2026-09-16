#!/usr/bin/env python3
"""metrics_extra.py -- supplemental per-function metrics for rebuild assessment.
Writes _re/out/metrics_extra.json:
  x87_single / x87_double / x87_ext / x87_int : x87 memory operands by width
  fldcw/fninit                                  : FPU state changes
  bulk_fixed : call sites passing BOTH an immediate .data/.rdata address and an
               immediate size >= 0x40 (fixed-address block read/write/copy/clear)
  rep_fixed  : `rep movs/stos` whose esi/edi was loaded with an immediate .data address
  idx_data   : [reg*scale + .data] indexed accesses (arrays laid out in .data)
  ptr_data   : immediates that are .data addresses loaded into a register (address-of)
"""
import json, struct, collections
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from capstone.x86 import X86_OP_MEM, X86_OP_IMM, X86_OP_REG, X86_GRP_FPU

R = "/mnt/c/Games/IGNITION/_re/out/"
db = json.load(open(R + "funcdb.json"))
F = {int(k, 16): v for k, v in db["funcs"].items()}
pe = pefile.PE("/mnt/c/Games/IGNITION/Ign_win.exe", fast_load=True)
SECS = [(s.Name.rstrip(b'\0').decode(), 0x400000 + s.VirtualAddress, s.get_data(), s.Misc_VirtualSize) for s in pe.sections]
def rd(a, n):
    for nm, va, d, vs in SECS:
        if va <= a < va + len(d):
            return d[a - va:a - va + n]
    return b""
def isdata(v):
    return 0x0047C000 <= v < 0x0064BF80 or 0x00479000 <= v < 0x0047B5A0
md = Cs(CS_ARCH_X86, CS_MODE_32); md.detail = True

out = {}
for f, r in F.items():
    blob = rd(f, r["end"] - f)
    m = collections.Counter()
    bulk = []; repfix = []
    recent = []  # last few instructions
    regimm = {}
    for i in md.disasm(blob, f):
        mn = i.mnemonic
        if i.group(X86_GRP_FPU):
            for o in i.operands:
                if o.type == X86_OP_MEM:
                    sz = o.size
                    if mn.startswith("fi"):
                        m["x87_int"] += 1
                    elif sz == 4:
                        m["x87_single"] += 1
                    elif sz == 8:
                        m["x87_double"] += 1
                    elif sz == 10:
                        m["x87_ext"] += 1
            if mn in ("fldcw", "fninit", "finit"):
                m[mn] += 1
            if mn in ("fsin", "fcos", "fsincos", "fpatan", "fsqrt", "fprem", "fprem1", "fyl2x", "fyl2xp1", "f2xm1", "fscale", "frndint"):
                m["x87_transcendental"] += 1
                m["op_" + mn] += 1
        for o in i.operands:
            if o.type == X86_OP_MEM and o.mem.base == 0 and o.mem.index != 0 and isdata(o.mem.disp & 0xFFFFFFFF):
                m["idx_data"] += 1
        if mn == "mov" and len(i.operands) == 2 and i.operands[0].type == X86_OP_REG and i.operands[1].type == X86_OP_IMM:
            v = i.operands[1].imm & 0xFFFFFFFF
            if isdata(v):
                m["ptr_data"] += 1
                regimm[i.reg_name(i.operands[0].reg)] = v
            else:
                regimm.pop(i.reg_name(i.operands[0].reg), None)
        if mn.startswith("rep") and ("movs" in mn or "stos" in mn):
            for reg in ("edi", "esi"):
                if reg in regimm:
                    repfix.append((i.address, mn, reg, regimm[reg]))
        if mn == "call":
            pushes = []
            for p in reversed(recent):
                if p.mnemonic == "push":
                    pushes.append(p)
                    if len(pushes) >= 6:
                        break
                elif p.mnemonic in ("call", "ret", "jmp") or p.mnemonic.startswith("j"):
                    break
            addrs = [p.operands[0].imm & 0xFFFFFFFF for p in pushes if p.operands and p.operands[0].type == X86_OP_IMM and isdata(p.operands[0].imm & 0xFFFFFFFF)]
            sizes = [p.operands[0].imm for p in pushes if p.operands and p.operands[0].type == X86_OP_IMM and 0x40 <= p.operands[0].imm < 0x1000000 and not isdata(p.operands[0].imm & 0xFFFFFFFF)]
            if addrs and sizes:
                t = i.operands[0].imm & 0xFFFFFFFF if i.operands[0].type == X86_OP_IMM else None
                bulk.append((i.address, t, [hex(a) for a in addrs], [hex(s) for s in sizes]))
        recent.append(i)
        if len(recent) > 12:
            recent.pop(0)
    out[hex(f)] = dict(m, bulk_fixed=[(hex(a), hex(t) if t else None, ad, sz) for a, t, ad, sz in bulk],
                       rep_fixed=[(hex(a), mn, reg, hex(v)) for a, mn, reg, v in repfix])
json.dump(out, open(R + "metrics_extra.json", "w"))
tot = collections.Counter()
for v in out.values():
    for k in ("x87_single", "x87_double", "x87_ext", "x87_int", "fldcw", "fninit", "idx_data", "ptr_data", "x87_transcendental", "op_fsin", "op_fcos", "op_fsqrt", "op_fpatan", "op_frndint"):
        tot[k] += v.get(k, 0)
    tot["bulk_sites"] += len(v["bulk_fixed"]); tot["rep_fixed"] += len(v["rep_fixed"])
print(dict(tot))
