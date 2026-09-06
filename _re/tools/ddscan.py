#!/usr/bin/env python3
"""Whole-binary COM vtable call-site scanner with object-provenance tracking."""
import sys, collections, pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_OPT_SYNTAX_INTEL
from capstone.x86 import X86_OP_MEM, X86_OP_IMM, X86_OP_REG

pe = pefile.PE("Ign_win.exe", fast_load=False)
base = pe.OPTIONAL_HEADER.ImageBase
iat = {}
for e in pe.DIRECTORY_ENTRY_IMPORT:
    d = e.dll.decode()
    for i in e.imports:
        iat[i.address] = f"{d}!{i.name.decode() if i.name else '#%d'%i.ordinal}"

secs = [(s.Name.rstrip(b'\0').decode(), base+s.VirtualAddress, s.get_data()) for s in pe.sections]
md = Cs(CS_ARCH_X86, CS_MODE_32); md.detail=True; md.syntax=CS_OPT_SYNTAX_INTEL

def sweep(data, va):
    off=0; n=len(data)
    while off<n:
        produced=0
        for ins in md.disasm(data[off:], va+off):
            produced=(ins.address-va)-off+ins.size
            yield ins
        off += produced if produced else 1

# Record: for every call [reg+disp], walk back up to 12 instructions to find
# the last write to `reg` and the last `push reg` (the `this` arg).
results=[]
for name, va, data in secs:
    if name not in (".text","code"): continue
    win=[]
    for ins in sweep(data, va):
        win.append(ins)
        if len(win)>14: win.pop(0)
        if ins.mnemonic!="call" or len(ins.operands)!=1: continue
        op=ins.operands[0]
        if op.type!=X86_OP_MEM: continue
        m=op.mem
        if m.base==0 and m.index==0:
            continue
        if m.index!=0: continue
        disp=m.disp
        if not (0<=disp<=0x200): continue
        reg=ins.reg_name(m.base)
        # find `mov reg, [X]` that loaded the vtable ptr  => X is the `this`
        thisexpr=None; pushes=[]
        for p in reversed(win[:-1]):
            if p.mnemonic=="push":
                pushes.append(p.op_str)
            if thisexpr is None and p.mnemonic=="mov" and len(p.operands)==2:
                d,s=p.operands
                if d.type==X86_OP_REG and p.reg_name(d.reg)==reg and s.type==X86_OP_MEM:
                    # s is [thisreg] or [thisreg+0]
                    if s.mem.base!=0 and s.mem.disp==0 and s.mem.index==0:
                        thisreg=p.reg_name(s.mem.base)
                        # now find what loaded thisreg
                        thisexpr="["+thisreg+"]"
                        for q in reversed(win[:win.index(p)]):
                            if q.mnemonic in ("mov","lea") and len(q.operands)==2:
                                d2,s2=q.operands
                                if d2.type==X86_OP_REG and q.reg_name(d2.reg)==thisreg:
                                    thisexpr=q.op_str.split(",",1)[1].strip()
                                    break
                    elif s.mem.base==0 and s.mem.index==0:
                        thisexpr="glob:0x%08X"%(s.mem.disp&0xFFFFFFFF)
        results.append((ins.address, name, disp, reg, thisexpr, list(reversed(pushes))))

by=collections.defaultdict(list)
for r in results: by[r[2]].append(r)
for disp in sorted(by):
    print(f"=== slot +0x{disp:02X} (index {disp//4}) : {len(by[disp])} sites")
    for va,sec,d,reg,te,pushes in by[disp]:
        print(f"    0x{va:08X} [{sec}] this<-{te}   pushes={pushes[-6:]}")
