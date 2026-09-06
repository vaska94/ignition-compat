#!/usr/bin/env python3
"""
Ignition (UDS/Virgin 1997) - Win32 API + COM vtable surface scanner.

Disassembles Ign_win.exe, resolves every IAT call site to its imported symbol,
and collects indirect `call [reg+disp]` sites (COM vtable dispatch) so we can
recover exactly which DirectDraw/DirectSound/DirectInput methods the game uses.
"""
import sys, collections
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_OPT_SYNTAX_INTEL
from capstone.x86 import X86_OP_MEM, X86_OP_IMM, X86_OP_REG

EXE = sys.argv[1] if len(sys.argv) > 1 else "Ign_win.exe"
pe = pefile.PE(EXE, fast_load=False)
base = pe.OPTIONAL_HEADER.ImageBase

# ---- IAT map: absolute VA of thunk slot -> "DLL!Name" -------------------------
iat = {}
for entry in pe.DIRECTORY_ENTRY_IMPORT:
    dll = entry.dll.decode(errors="replace")
    for imp in entry.imports:
        nm = imp.name.decode(errors="replace") if imp.name else f"#{imp.ordinal}"
        iat[imp.address] = f"{dll}!{nm}"

# ---- section image ------------------------------------------------------------
secs = []
for s in pe.sections:
    name = s.Name.rstrip(b"\x00").decode(errors="replace")
    secs.append((name, base + s.VirtualAddress, s.get_data()))

md = Cs(CS_ARCH_X86, CS_MODE_32)
md.detail = True
md.syntax = CS_OPT_SYNTAX_INTEL

iat_calls   = collections.defaultdict(list)   # symbol -> [call site VAs]
vtbl_calls  = collections.defaultdict(list)   # (reg, disp) -> [(va, prev_ctx)]
globals_ptr = collections.defaultdict(list)   # global VA -> [(disp, va)]  COM obj in global

CODE_SECS = {".text", "code"}

def sweep(md, data, va):
    """Linear disassembly that resumes one byte past any undecodable spot."""
    off = 0
    n = len(data)
    while off < n:
        produced = 0
        for ins in md.disasm(data[off:], va + off):
            produced = (ins.address - va) - off + ins.size
            yield ins
        off += produced if produced else 1


for name, va, data in secs:
    if name not in CODE_SECS:
        continue
    # linear sweep with resume-on-bad-byte so one bad opcode can't truncate us
    recent = []
    for ins in sweep(md, data, va):
        recent.append(ins)
        if len(recent) > 8:
            recent.pop(0)
        if ins.mnemonic not in ("call", "jmp"):
            continue
        if len(ins.operands) != 1:
            continue
        op = ins.operands[0]
        if op.type != X86_OP_MEM:
            continue
        m = op.mem
        # absolute [disp32]  -> IAT thunk or function pointer table
        if m.base == 0 and m.index == 0:
            tgt = m.disp & 0xFFFFFFFF
            if tgt in iat:
                iat_calls[iat[tgt]].append((ins.address, name))
        # [reg+disp] -> COM vtable dispatch (this->lpVtbl->Method)
        elif m.base != 0 and m.index == 0:
            reg = ins.reg_name(m.base)
            disp = m.disp
            if 0 <= disp <= 0x400:
                vtbl_calls[disp].append((ins.address, name))
                # walk back to find which global held the interface pointer
                for p in reversed(recent[:-1]):
                    if p.mnemonic == "mov" and len(p.operands) == 2:
                        d, s = p.operands
                        if d.type == X86_OP_REG and s.type == X86_OP_MEM \
                           and s.mem.base == 0 and s.mem.index == 0:
                            globals_ptr[s.mem.disp & 0xFFFFFFFF].append((disp, ins.address))
                            break

print("=" * 78)
print("IMPORTED API CALL SITES  (symbol : #sites)")
print("=" * 78)
INTEREST = ("DDRAW", "DSOUND", "DINPUT", "DPLAY", "WINMM", "GDI32")
for sym, sites in sorted(iat_calls.items(), key=lambda kv: -len(kv[1])):
    tag = "  <<<" if any(sym.upper().startswith(i) for i in INTEREST) else ""
    print(f"{sym:<48} {len(sites):>4}   first@0x{sites[0][0]:08X}{tag}")

print()
print("=" * 78)
print("COM VTABLE DISPATCH SITES  (vtable slot offset : #sites)")
print("=" * 78)
for disp, sites in sorted(vtbl_calls.items()):
    print(f"  +0x{disp:02X}  (slot {disp//4:>3})   {len(sites):>4} sites   e.g. 0x{sites[0][0]:08X}")

print()
print("=" * 78)
print("GLOBALS HOLDING COM INTERFACE POINTERS (candidate objects)")
print("=" * 78)
for g, uses in sorted(globals_ptr.items(), key=lambda kv: -len(kv[1])):
    if len(uses) < 2:
        continue
    slots = sorted({d for d, _ in uses})
    print(f"  g_0x{g:08X}  {len(uses):>4} calls  slots: {', '.join('+0x%02X' % s for s in slots[:18])}")
