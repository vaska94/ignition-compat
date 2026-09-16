#!/usr/bin/env python3
"""bytescan.py <VA> [VA ...] [--sec .text,code]
Exhaustive byte-level scan of the whole PE image (all sections by default) for the
little-endian dword of each VA.  Immune to linear-disassembly desync.
Also: --off <fileoff> converts a raw file offset to a VA first."""
import sys, struct, pefile
pe = pefile.PE("/mnt/c/Games/IGNITION/Ign_win.exe", fast_load=True)
base = pe.OPTIONAL_HEADER.ImageBase
def off2va(o):
    for s in pe.sections:
        if s.PointerToRawData <= o < s.PointerToRawData + s.SizeOfRawData:
            return base + s.VirtualAddress + o - s.PointerToRawData
secs = None
args = sys.argv[1:]
if "--sec" in args:
    i = args.index("--sec"); secs = set(args[i+1].split(",")); del args[i:i+2]
tg = []
it = iter(args)
for a in it:
    if a == "--off":
        o = int(next(it), 0); v = off2va(o); print(f"file 0x{o:X} -> VA 0x{v:08X}"); tg.append(v)
    else:
        tg.append(int(a, 0))
for v in tg:
    pat = struct.pack("<I", v); hits = []
    for s in pe.sections:
        nm = s.Name.rstrip(b"\0").decode()
        if secs and nm not in secs: continue
        d = s.get_data(); j = d.find(pat)
        while j >= 0:
            hits.append(f"0x{base+s.VirtualAddress+j:08X}[{nm}]"); j = d.find(pat, j+1)
    print(f"0x{v:08X}: {len(hits)} refs: " + " ".join(hits))
