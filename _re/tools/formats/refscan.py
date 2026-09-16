#!/usr/bin/env python3
"""refscan.py [--str TEXT]... [VA]...   (track-formats agent's copy; see also _re/tools/)
Exhaustive byte-level scan of every section of Ign_win.exe for the little-endian dword of
each VA. No disassembly involved, so it cannot desynchronise. Prints hit VA, section and the
3 preceding bytes (68=push imm32, A1/A3=mov eax,[m]/mov [m],eax, B8..BF=mov r,imm32 ...).
--str TEXT resolves every occurrence of the NUL-terminated string TEXT to its VA first."""
import sys, struct, pefile
pe = pefile.PE("/mnt/c/Games/IGNITION/Ign_win.exe", fast_load=True)
base = pe.OPTIONAL_HEADER.ImageBase
secs = [(s.Name.rstrip(b'\0').decode(), base + s.VirtualAddress, s.get_data()) for s in pe.sections]
targets, args, i = [], sys.argv[1:], 0
while i < len(args):
    if args[i] == "--str":
        t = args[i+1].encode() + b"\0"
        for nm, sva, d in secs:
            p = d.find(t)
            while p >= 0:
                targets.append(sva + p); print(f'string "{args[i+1]}" @ 0x{sva+p:08X} [{nm}]')
                p = d.find(t, p + 1)
        i += 2
    else:
        targets.append(int(args[i], 0)); i += 1
for t in targets:
    pat = struct.pack("<I", t); n = 0
    for nm, sva, d in secs:
        p = d.find(pat)
        while p >= 0:
            print(f"  ref 0x{t:08X}: at 0x{sva+p:08X} [{nm}] pre={d[max(0,p-3):p].hex()}"); n += 1
            p = d.find(pat, p + 1)
    if n == 0: print(f"  ref 0x{t:08X}: NONE")
