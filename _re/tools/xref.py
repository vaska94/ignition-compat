#!/usr/bin/env python3
"""xref.py <target VA> [--calls]  -- find instructions referencing a VA (imm or mem disp)
   or, with --calls, all `call <target>` sites."""
import sys, pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_OPT_SYNTAX_INTEL
from capstone.x86 import X86_OP_MEM, X86_OP_IMM
pe=pefile.PE("Ign_win.exe",fast_load=False); base=pe.OPTIONAL_HEADER.ImageBase
md=Cs(CS_ARCH_X86,CS_MODE_32); md.detail=True; md.syntax=CS_OPT_SYNTAX_INTEL
def sweep(data,va):
    off=0;n=len(data)
    while off<n:
        p=0
        for i in md.disasm(data[off:],va+off):
            p=(i.address-va)-off+i.size; yield i
        off+=p if p else 1
targets=set(int(x,0) for x in sys.argv[1:] if not x.startswith("--"))
onlycalls="--calls" in sys.argv
for s in pe.sections:
    nm=s.Name.rstrip(b'\0').decode()
    if nm not in (".text","code"): continue
    for ins in sweep(s.get_data(), base+s.VirtualAddress):
        hit=False
        for o in ins.operands:
            if o.type==X86_OP_IMM and (o.imm&0xFFFFFFFF) in targets: hit=True
            if o.type==X86_OP_MEM and o.mem.base==0 and o.mem.index==0 and (o.mem.disp&0xFFFFFFFF) in targets: hit=True
        if hit and (not onlycalls or ins.mnemonic=="call"):
            print(f"0x{ins.address:08X} [{nm}] {ins.mnemonic} {ins.op_str}")
