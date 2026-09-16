#!/usr/bin/env python3
"""dwref.py <VA> [<VA> ...] [--range N]

Exhaustive byte-level reference scan: finds every occurrence of the
little-endian dword VA (or VA..VA+N-1 with --range N, to catch struct-field
access like [G+4]) in every PE section, and prints the instruction that
contains it (disassembly re-anchored 1..7 bytes before the dword, so linear
sweep desynchronisation cannot hide a hit).
"""
import sys, struct, pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_OPT_SYNTAX_INTEL

pe = pefile.PE("/mnt/c/Games/IGNITION/Ign_win.exe", fast_load=True)
base = pe.OPTIONAL_HEADER.ImageBase
secs = [(s.Name.rstrip(b"\0").decode(), base + s.VirtualAddress, s.get_data()) for s in pe.sections]
md = Cs(CS_ARCH_X86, CS_MODE_32); md.syntax = CS_OPT_SYNTAX_INTEL

def insn_at(data, sva, off):
    cands = []
    for k in range(1, 8):
        st = off - k
        if st < 0: continue
        for ins in md.disasm(data[st:st + 16], sva + st):
            if st + ins.size >= off + 4:
                cands.append(ins)
            break
    # prefer the longest plausible decode that ends exactly after the dword or covers it
    return cands[0] if cands else None

rng = 1
if "--range" in sys.argv:
    rng = int(sys.argv[sys.argv.index("--range") + 1], 0)
vals = [int(a, 0) for a in sys.argv[1:] if not a.startswith("--") and a != str(rng)]
for v0 in vals:
    for v in range(v0, v0 + rng):
        pat = struct.pack("<I", v)
        for nm, sva, data in secs:
            i = data.find(pat)
            while i >= 0:
                ins = insn_at(data, sva, i) if nm in (".text", "code") else None
                t = f"0x{ins.address:08X}  {ins.mnemonic} {ins.op_str}" if ins else ""
                print(f"0x{v:08X} @0x{sva+i:08X} [{nm}] {t}")
                i = data.find(pat, i + 1)
