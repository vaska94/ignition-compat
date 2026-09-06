#!/usr/bin/env python3
"""
disasm.py <VA|0xVA> [count] [--exe PATH]

Disassemble Ign_win.exe at a virtual address with annotations:
  * IAT calls resolved to DLL!Symbol
  * import jump-thunks resolved through to the real symbol
  * COM vtable dispatch `call [reg+disp]` annotated with candidate method names
    for IDirectDraw / IDirectDrawSurface / IDirectDrawPalette / IDirectSound /
    IDirectSoundBuffer / IDirectInput / IDirectInputDevice / IDirectPlay2
  * string references (data pointers into .data/.rdata that look like C strings)
"""
import sys, pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_OPT_SYNTAX_INTEL
from capstone.x86 import X86_OP_MEM, X86_OP_IMM

VT = {
 "IDirectDraw": ["QueryInterface","AddRef","Release","Compact","CreateClipper",
   "CreatePalette","CreateSurface","DuplicateSurface","EnumDisplayModes","EnumSurfaces",
   "FlipToGDISurface","GetCaps","GetDisplayMode","GetFourCCCodes","GetGDISurface",
   "GetMonitorFrequency","GetScanLine","GetVerticalBlankStatus","Initialize",
   "RestoreDisplayMode","SetCooperativeLevel","SetDisplayMode","WaitForVerticalBlank"],
 "IDirectDrawSurface": ["QueryInterface","AddRef","Release","AddAttachedSurface",
   "AddOverlayDirtyRect","Blt","BltBatch","BltFast","DeleteAttachedSurface",
   "EnumAttachedSurfaces","EnumOverlayZOrders","Flip","GetAttachedSurface","GetBltStatus",
   "GetCaps","GetClipper","GetColorKey","GetDC","GetFlipStatus","GetOverlayPosition",
   "GetPalette","GetPixelFormat","GetSurfaceDesc","Initialize","IsLost","Lock","ReleaseDC",
   "Restore","SetClipper","SetColorKey","SetOverlayPosition","SetPalette","Unlock",
   "UpdateOverlay","UpdateOverlayDisplay","UpdateOverlayZOrder"],
 "IDirectDrawPalette": ["QueryInterface","AddRef","Release","GetCaps","GetEntries",
   "Initialize","SetEntries"],
 "IDirectSound": ["QueryInterface","AddRef","Release","CreateSoundBuffer","GetCaps",
   "DuplicateSoundBuffer","SetCooperativeLevel","Compact","GetSpeakerConfig",
   "SetSpeakerConfig","Initialize"],
 "IDirectSoundBuffer": ["QueryInterface","AddRef","Release","GetCaps","GetCurrentPosition",
   "GetFormat","GetVolume","GetPan","GetFrequency","GetStatus","Initialize","Lock","Play",
   "SetCurrentPosition","SetFormat","SetVolume","SetPan","SetFrequency","Stop","Unlock",
   "Restore"],
 "IDirectInput": ["QueryInterface","AddRef","Release","CreateDevice","EnumDevices",
   "GetDeviceStatus","RunControlPanel","Initialize"],
 "IDirectInputDevice": ["QueryInterface","AddRef","Release","GetCapabilities","EnumObjects",
   "GetProperty","SetProperty","Acquire","Unacquire","GetDeviceState","GetDeviceData",
   "SetDataFormat","SetEventNotification","SetCooperativeLevel","GetObjectInfo",
   "GetDeviceInfo","RunControlPanel","Initialize"],
 "IDirectPlay2": ["QueryInterface","AddRef","Release","AddPlayerToGroup","Close",
   "CreateGroup","CreatePlayer","DeletePlayerFromGroup","DestroyGroup","DestroyPlayer",
   "EnumGroupPlayers","EnumGroups","EnumPlayers","EnumSessions","GetCaps",
   "GetGroupData","GetGroupName","GetMessageCount","GetPlayerAddress","GetPlayerCaps",
   "GetPlayerData","GetPlayerName","GetSessionDesc","Initialize","Open","Receive",
   "Send","SetGroupData","SetGroupName","SetPlayerData","SetPlayerName","SetSessionDesc"],
}

def slot_names(disp):
    if disp % 4: return ""
    i = disp // 4
    out = [f"{k}::{v[i]}" for k, v in VT.items() if i < len(v)]
    return "  ; " + " | ".join(out) if out else ""

exe = "Ign_win.exe"
if "--exe" in sys.argv:
    exe = sys.argv[sys.argv.index("--exe") + 1]
pe = pefile.PE(exe, fast_load=False)
base = pe.OPTIONAL_HEADER.ImageBase
iat = {}
for e in pe.DIRECTORY_ENTRY_IMPORT:
    d = e.dll.decode()
    for i in e.imports:
        iat[i.address] = f"{d}!{i.name.decode() if i.name else '#%d' % i.ordinal}"

img = {}
for s in pe.sections:
    img[s.Name.rstrip(b'\x00').decode()] = (base + s.VirtualAddress, s.get_data())

def read(va, n):
    for nm, (sva, data) in img.items():
        if sva <= va < sva + len(data):
            return data[va - sva: va - sva + n]
    return b""

def cstr(va, maxn=72):
    b = read(va, maxn)
    if not b: return None
    e = b.find(b"\x00")
    if e < 1: return None
    s = b[:e]
    if all(32 <= c < 127 or c in (9,) for c in s) and len(s) >= 4:
        return s.decode()
    return None

# import thunks: `jmp [iat]` -> symbol
md = Cs(CS_ARCH_X86, CS_MODE_32); md.detail = True; md.syntax = CS_OPT_SYNTAX_INTEL
thunks = {}
tva, tdata = img[".text"]
for ins in md.disasm(tdata, tva):
    if ins.mnemonic == "jmp" and ins.operands and ins.operands[0].type == X86_OP_MEM:
        m = ins.operands[0].mem
        if m.base == 0 and m.index == 0 and (m.disp & 0xFFFFFFFF) in iat:
            thunks[ins.address] = iat[m.disp & 0xFFFFFFFF]

va = int(sys.argv[1], 0)
count = int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else 90
data = read(va, count * 12)
n = 0
for ins in md.disasm(data, va):
    ann = ""
    ops = ins.operands
    if ops:
        o = ops[0]
        if o.type == X86_OP_IMM and ins.mnemonic in ("call", "jmp"):
            t = o.imm & 0xFFFFFFFF
            if t in thunks: ann = "  ; -> " + thunks[t]
        elif o.type == X86_OP_MEM and ins.mnemonic == "call":
            m = o.mem
            if m.base == 0 and (m.disp & 0xFFFFFFFF) in iat:
                ann = "  ; " + iat[m.disp & 0xFFFFFFFF]
            elif m.base != 0 and 0 <= m.disp <= 0x200:
                ann = f"  ; VTBL[{m.disp//4}]" + slot_names(m.disp)
    if not ann:
        for o in ops:
            if o.type == X86_OP_IMM:
                s = cstr(o.imm & 0xFFFFFFFF)
                if s: ann = f'  ; "{s}"'; break
            if o.type == X86_OP_MEM and o.mem.base == 0 and o.mem.index == 0:
                s = cstr(o.mem.disp & 0xFFFFFFFF)
                if s: ann = f'  ; "{s}"'; break
    print(f"0x{ins.address:08X}  {ins.bytes.hex():<20} {ins.mnemonic:<7} {ins.op_str}{ann}")
    n += 1
    if n >= count: break
