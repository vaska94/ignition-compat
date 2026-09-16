#!/usr/bin/env python3
"""
hookspec.py <VA> [name] [--exe PATH]

Decide whether a function in Ign_win.exe can be detoured safely, and print the
detour_t initializer for ign_compat/src/common/detour.c.

A 5-byte `jmp` overwrites the start of the function. That is only safe when:
  1. the displaced bytes end on an instruction boundary, inside the function
     (no ret/jmp before 5 bytes are covered);
  2. no displaced instruction is a relative branch or call - it is re-executed
     from the trampoline at another address, where its rel operand would point
     somewhere else;
  3. nothing in the executable branches into, or holds a pointer to, the
     interior of the displaced range - it would land in the middle of our jmp.

Rule 3 uses an exhaustive byte-level scan of every rel8/rel32 branch encoding,
because linear disassembly desynchronises on this binary. Candidates are
confirmed by decoding at the source address, and any survivor blocks the hook:
being conservative costs one manual look, being wrong costs a corrupted game.

Exit status: 0 safe, 1 unsafe, 2 usage error.
"""
import struct
import sys

import pefile
from capstone import CS_ARCH_X86, CS_GRP_BRANCH_RELATIVE, CS_GRP_RET, CS_MODE_32, Cs
from capstone.x86 import X86_OP_IMM

JMP_LEN = 5
SIG_MAX = 32


def load(exe):
    pe = pefile.PE(exe)
    base = pe.OPTIONAL_HEADER.ImageBase
    secs = []
    for s in pe.sections:
        name = s.Name.rstrip(b"\0").decode(errors="replace")
        data = s.get_data()[: max(s.Misc_VirtualSize, 0) or len(s.get_data())]
        secs.append((name, base + s.VirtualAddress, data))
    return secs


def read(secs, va, n):
    for _, sva, data in secs:
        if sva <= va < sva + len(data):
            return data[va - sva: va - sva + n]
    return b""


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    exe = "Ign_win.exe"
    if "--exe" in argv:
        exe = argv[argv.index("--exe") + 1]
        args = [a for a in args if a != exe]
    if not args:
        print(__doc__)
        return 2
    va = int(args[0], 0)
    name = args[1] if len(args) > 1 else f"fn_{va:08X}"

    secs = load(exe)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    code = read(secs, va, SIG_MAX + 16)
    if not code:
        print(f"0x{va:08X} is not inside any section")
        return 2

    problems = []
    taken = []
    total = 0
    for ins in md.disasm(code, va):
        taken.append(ins)
        total += ins.size
        if ins.group(CS_GRP_BRANCH_RELATIVE):
            problems.append(f"displaced instruction is a relative branch/call: "
                            f"0x{ins.address:08X} {ins.mnemonic} {ins.op_str}")
        if total >= JMP_LEN:
            break
        # Only an unconditional transfer ends the function here. A conditional
        # branch can fall through, so keep decoding past it.
        if ins.group(CS_GRP_RET) or ins.mnemonic in ("jmp", "ljmp", "int3", "hlt", "ud2"):
            problems.append(f"function is shorter than {JMP_LEN} bytes: control leaves at "
                            f"0x{ins.address:08X} {ins.mnemonic} after {total} bytes")
            break
    if total < JMP_LEN:
        problems.append(f"could only decode {total} bytes at 0x{va:08X}")
    if total > SIG_MAX:
        problems.append(f"displaced range is {total} bytes, over the {SIG_MAX}-byte limit")

    lo, hi = va + 1, va + total          # interior: strictly after the entry

    # Rule 3a: relative branches anywhere into the interior.
    branch_hits = []
    direct_calls_to_entry = 0
    for sname, sva, data in secs:
        if sname not in (".text", "code"):
            continue
        n = len(data)
        for i in range(n - 1):
            b = data[i]
            tgt = None
            if b in (0xE8, 0xE9) and i + 5 <= n:
                tgt = (sva + i + 5 + struct.unpack_from("<i", data, i + 1)[0]) & 0xFFFFFFFF
                if b == 0xE8 and tgt == va:
                    direct_calls_to_entry += 1
            elif (b == 0xEB or 0x70 <= b <= 0x7F or 0xE0 <= b <= 0xE3):
                tgt = (sva + i + 2 + struct.unpack_from("<b", data, i + 1)[0]) & 0xFFFFFFFF
            elif b == 0x0F and i + 6 <= n and 0x80 <= data[i + 1] <= 0x8F:
                tgt = (sva + i + 6 + struct.unpack_from("<i", data, i + 2)[0]) & 0xFFFFFFFF
            if tgt is None or not (lo <= tgt < hi):
                continue
            src = sva + i
            if lo - 1 <= src < hi:       # our own displaced bytes, already covered by rule 2
                continue
            for ins in md.disasm(data[i:i + 8], src, count=1):
                ops = ins.operands
                if ins.group(CS_GRP_BRANCH_RELATIVE) and ops and ops[0].type == X86_OP_IMM \
                        and (ops[0].imm & 0xFFFFFFFF) == tgt:
                    branch_hits.append(f"0x{src:08X} {ins.mnemonic} 0x{tgt:08X}")

    # Rule 3b: absolute pointers to the interior (jump tables, callbacks).
    pointer_hits = []
    for sname, sva, data in secs:
        if sname in (".reloc", ".rsrc", ".idata"):
            continue
        for i in range(len(data) - 3):
            v = struct.unpack_from("<I", data, i)[0]
            if lo <= v < hi:
                pointer_hits.append(f"{sname}+0x{i:X} (0x{sva + i:08X}) = 0x{v:08X}")

    if branch_hits:
        problems.append("branches into the displaced range (confirmed by decoding at the source):")
        problems.extend("    " + h for h in branch_hits[:20])
    if pointer_hits:
        problems.append("possible absolute pointers into the displaced range "
                        "(data values; some may be coincidental constants):")
        problems.extend("    " + h for h in pointer_hits[:20])

    print(f"hookspec 0x{va:08X} '{name}' : {total} displaced bytes, "
          f"{direct_calls_to_entry} direct call(s) to the entry")
    for ins in taken:
        print(f"    0x{ins.address:08X}  {ins.bytes.hex(' '):<24} {ins.mnemonic} {ins.op_str}")
    if direct_calls_to_entry == 0:
        print("  note: no direct calls land here - confirm this really is a function start "
              "(it may only be reached through a pointer)")

    if problems:
        print("UNSAFE:")
        for p in problems:
            print("  " + p)
        return 1

    sig = ", ".join(f"0x{b:02X}" for b in code[:total])
    print("SAFE. detour_t initializer:")
    print(f'    {{ "{name}", 0x{va:08X}, (const BYTE[]){{ {sig} }}, {total}, NULL /* replacement */ }},')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
