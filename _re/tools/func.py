#!/usr/bin/env python3
"""func.py <VA> [--back N] -- disassemble the enclosing function of VA.
Finds the function start by scanning back for a prologue after int3/ret padding,
then disassembles until the matching ret / end heuristics."""
import sys, pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_OPT_SYNTAX_INTEL
from capstone.x86 import X86_OP_MEM, X86_OP_IMM

VT = {
 "IDirectDraw": ["QueryInterface","AddRef","Release","Compact","CreateClipper",
   "CreatePalette","CreateSurface","DuplicateSurface","EnumDisplayModes","EnumSurfaces",
   "FlipToGDISurface","GetCaps","GetDisplayMode","GetFourCCCodes","GetGDISurface",
   "GetMonitorFrequency","GetScanLine","GetVerticalBlankStatus","Initialize",
   "RestoreDisplayMode","SetCooperativeLevel","SetDisplayMode","WaitForVerticalBlank"],
 "IDDS": ["QueryInterface","AddRef","Release","AddAttachedSurface",
   "AddOverlayDirtyRect","Blt","BltBatch","BltFast","DeleteAttachedSurface",
   "EnumAttachedSurfaces","EnumOverlayZOrders","Flip","GetAttachedSurface","GetBltStatus",
   "GetCaps","GetClipper","GetColorKey","GetDC","GetFlipStatus","GetOverlayPosition",
   "GetPalette","GetPixelFormat","GetSurfaceDesc","Initialize","IsLost","Lock","ReleaseDC",
   "Restore","SetClipper","SetColorKey","SetOverlayPosition","SetPalette","Unlock",
   "UpdateOverlay","UpdateOverlayDisplay","UpdateOverlayZOrder"],
 "IDSound": ["QueryInterface","AddRef","Release","CreateSoundBuffer","GetCaps",
   "DuplicateSoundBuffer","SetCooperativeLevel","Compact","GetSpeakerConfig",
   "SetSpeakerConfig","Initialize"],
 "IDSBuf": ["QueryInterface","AddRef","Release","GetCaps","GetCurrentPosition",
   "GetFormat","GetVolume","GetPan","GetFrequency","GetStatus","Initialize","Lock","Play",
   "SetCurrentPosition","SetFormat","SetVolume","SetPan","SetFrequency","Stop","Unlock",
   "Restore"],
 "IDInput": ["QueryInterface","AddRef","Release","CreateDevice","EnumDevices",
   "GetDeviceStatus","RunControlPanel","Initialize"],
 "IDIDev": ["QueryInterface","AddRef","Release","GetCapabilities","EnumObjects",
   "GetProperty","SetProperty","Acquire","Unacquire","GetDeviceState","GetDeviceData",
   "SetDataFormat","SetEventNotification","SetCooperativeLevel","GetObjectInfo",
   "GetDeviceInfo","RunControlPanel","Initialize"],
}
def slot_names(disp):
    if disp % 4: return ""
    i = disp//4
    out=[f"{k}::{v[i]}" for k,v in VT.items() if i < len(v)]
    return "  ; " + " | ".join(out) if out else ""

pe = pefile.PE("/mnt/c/Games/IGNITION/Ign_win.exe", fast_load=False)
base = pe.OPTIONAL_HEADER.ImageBase
iat = {}
for e in pe.DIRECTORY_ENTRY_IMPORT:
    d = e.dll.decode()
    for i in e.imports:
        iat[i.address] = f"{d}!{i.name.decode() if i.name else '#%d'%i.ordinal}"
img = {}
for s in pe.sections:
    img[s.Name.rstrip(b'\0').decode()] = (base+s.VirtualAddress, s.get_data())
def read(va,n):
    for nm,(sva,data) in img.items():
        if sva <= va < sva+len(data): return data[va-sva:va-sva+n]
    return b""
def cstr(va,maxn=90):
    b=read(va,maxn)
    if not b: return None
    e=b.find(b"\x00")
    if e<3: return None
    s=b[:e]
    if all(32<=c<127 or c==9 for c in s) and len(s)>=4: return s.decode()
    return None
md = Cs(CS_ARCH_X86, CS_MODE_32); md.detail=True; md.syntax=CS_OPT_SYNTAX_INTEL
tva,tdata = img[".text"]
thunks={}
for ins in md.disasm(tdata,tva):
    if ins.mnemonic=="jmp" and ins.operands and ins.operands[0].type==X86_OP_MEM:
        m=ins.operands[0].mem
        if m.base==0 and m.index==0 and (m.disp&0xFFFFFFFF) in iat:
            thunks[ins.address]=iat[m.disp&0xFFFFFFFF]

va0 = int(sys.argv[1],0)
back = 0x400
if "--back" in sys.argv: back=int(sys.argv[sys.argv.index("--back")+1],0)
# find function start: scan back for 55 8B EC (push ebp;mov ebp,esp) or after cc/90 padding
blob = read(va0-back, back)
start=None
for off in range(len(blob)-3, -1, -1):
    a = va0-back+off
    if blob[off:off+3]==b"\x55\x8b\xec":
        # preceded by ret/int3/nop/jmp?
        p = read(a-1,1)
        if p and p[0] in (0xCC,0x90,0xC3,0xC2,0xE9,0x00):
            start=a; break
if start is None:
    for off in range(len(blob)-3,-1,-1):
        a=va0-back+off
        if blob[off:off+3]==b"\x55\x8b\xec": start=a; break
if start is None: start=va0
maxn = int(sys.argv[2]) if len(sys.argv)>2 and not sys.argv[2].startswith("-") else 400
print(f";; function start guess = 0x{start:08X}  (query 0x{va0:08X})")
data = read(start, maxn*10)
n=0; depth=0
for ins in md.disasm(data,start):
    ann=""
    ops=ins.operands
    if ops:
        o=ops[0]
        if o.type==X86_OP_IMM and ins.mnemonic in ("call","jmp"):
            t=o.imm&0xFFFFFFFF
            if t in thunks: ann="  ; -> "+thunks[t]
        elif o.type==X86_OP_MEM and ins.mnemonic=="call":
            m=o.mem
            if m.base==0 and (m.disp&0xFFFFFFFF) in iat: ann="  ; "+iat[m.disp&0xFFFFFFFF]
            elif m.base!=0 and 0<=m.disp<=0x200: ann=f"  ; VTBL[{m.disp//4}]"+slot_names(m.disp)
    if not ann:
        for o in ops:
            if o.type==X86_OP_IMM:
                s=cstr(o.imm&0xFFFFFFFF)
                if s: ann=f'  ; "{s}"'; break
            if o.type==X86_OP_MEM and o.mem.base==0 and o.mem.index==0:
                s=cstr(o.mem.disp&0xFFFFFFFF)
                if s: ann=f'  ; "{s}"'; break
    mark = " <<<<" if ins.address==va0 else ""
    print(f"0x{ins.address:08X}  {ins.bytes.hex():<18} {ins.mnemonic:<7} {ins.op_str}{ann}{mark}")
    n+=1
    if n>=maxn: break
