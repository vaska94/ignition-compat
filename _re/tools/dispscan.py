#!/usr/bin/env python3
"""dispscan.py <disp> [disp ...] -- list every instruction in .text/code whose memory
operand has one of the given displacements with a base register (struct field access).
Uses resync-on-failure linear sweep; results are candidates (verify by context)."""
import sys, pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_OPT_SYNTAX_INTEL
from capstone.x86 import X86_OP_MEM
pe=pefile.PE("/mnt/c/Games/IGNITION/Ign_win.exe",fast_load=True); base=pe.OPTIONAL_HEADER.ImageBase
md=Cs(CS_ARCH_X86,CS_MODE_32); md.detail=True; md.syntax=CS_OPT_SYNTAX_INTEL
ds=set(int(x,0) for x in sys.argv[1:])
def sweep(data,va):
    off=0
    while off<len(data):
        p=0
        for i in md.disasm(data[off:],va+off):
            p=(i.address-va)-off+i.size; yield i
        off+=p if p else 1
for s in pe.sections:
    nm=s.Name.rstrip(b'\0').decode()
    if nm not in(".text","code"): continue
    for i in sweep(s.get_data(),base+s.VirtualAddress):
        for o in i.operands:
            if o.type==X86_OP_MEM and o.mem.base!=0 and (o.mem.disp&0xFFFFFFFF) in ds:
                print(f"0x{i.address:08X} {i.mnemonic} {i.op_str}"); break
