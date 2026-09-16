#!/usr/bin/env python3
"""strref.py <substring> [...] [--ctx N]

Exhaustive byte-level string reference scanner for Ign_win.exe (no linear
disassembly involved, so it cannot desynchronise):
  1. find every occurrence of each substring (case-insensitive) in any section,
     back up to the start of the containing C string, and compute its VA;
  2. scan every section byte-by-byte for the little-endian dword of that VA
     (and of VA-1 .. VA-4 is NOT done: only exact starts are reported);
  3. for each hit inside .text/code, print the instruction that contains it by
     trying disassembly starting 1..7 bytes before the dword.
"""
import sys, pefile, struct
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_OPT_SYNTAX_INTEL

EXE = "/mnt/c/Games/IGNITION/Ign_win.exe"
pe = pefile.PE(EXE, fast_load=True)
base = pe.OPTIONAL_HEADER.ImageBase
secs = []
for s in pe.sections:
    secs.append((s.Name.rstrip(b"\0").decode(), base + s.VirtualAddress, s.get_data()))
md = Cs(CS_ARCH_X86, CS_MODE_32); md.syntax = CS_OPT_SYNTAX_INTEL

def containing_insn(data, sva, off):
    """Find an instruction starting at off-k (k=1..7) that covers the dword at off."""
    best = None
    for k in range(1, 8):
        st = off - k
        if st < 0: continue
        for ins in md.disasm(data[st:st + 16], sva + st):
            if st + ins.size >= off + 4:
                best = ins
            break
        if best: break
    return best

def refs(va):
    pat = struct.pack("<I", va)
    out = []
    for nm, sva, data in secs:
        i = data.find(pat)
        while i >= 0:
            out.append((nm, sva, data, i))
            i = data.find(pat, i + 1)
    return out

args = [a for a in sys.argv[1:] if not a.startswith("--")]
for needle in args:
    nb = needle.lower().encode()
    seen = set()
    for nm, sva, data in secs:
        low = data.lower()
        i = low.find(nb)
        while i >= 0:
            st = i
            while st > 0 and 32 <= data[st - 1] < 127:
                st -= 1
            if (nm, st) not in seen:
                seen.add((nm, st))
                e = data.find(b"\0", st)
                s = data[st:e].decode(errors="replace")
                va = sva + st
                rr = refs(va)
                print(f'"{s}"  @0x{va:08X} [{nm}]  refs={len(rr)}')
                for rnm, rsva, rdata, off in rr:
                    loc = rsva + off
                    txt = ""
                    if rnm in (".text", "code"):
                        ins = containing_insn(rdata, rsva, off)
                        if ins:
                            txt = f"0x{ins.address:08X}  {ins.mnemonic} {ins.op_str}"
                    print(f"    ref @0x{loc:08X} [{rnm}]  {txt}")
            i = low.find(nb, i + 1)
