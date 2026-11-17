"""Recover the widescreen patcher's hand-written EXE writes and show each against the game's original code."""
import re
import struct
import sys

import capstone

sys.path.insert(0, ".")
from ws_dis import PATCH, GAME, sections, va2off

BUF = 0x5ea  # buffer at [ebp-0x5ea], 30 bytes


def main():
    p = open(PATCH, "rb").read()
    pb, ps = sections(p)
    g = open(GAME, "rb").read()
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = False
    start, end = 0x40d9a0, 0x40fe00
    o = va2off(ps, start)
    ins = list(md.disasm(p[o:o + end - start], start))
    buf = {}
    sym = {}
    regs = {}
    pending = None  # (offset)
    writes = []
    for i in ins:
        m, ops = i.mnemonic, i.op_str
        mm = re.match(r"(byte|word|dword) ptr \[ebp - (0x[0-9a-f]+)\], (.+)$", ops)
        if m == "mov" and mm:
            size = {"byte": 1, "word": 2, "dword": 4}[mm.group(1)]
            k = int(mm.group(2), 16)
            src = mm.group(3)
            if BUF - 30 < BUF - (BUF - k) and BUF - 30 <= k <= BUF:
                pos = BUF - k
                if src.startswith("0x") or src.isdigit() or src.startswith("-"):
                    v = int(src, 0) & ((1 << (8 * size)) - 1)
                    for b in range(size):
                        buf[pos + b] = (v >> (8 * b)) & 0xff
                        sym.pop(pos + b, None)
                else:
                    for b in range(size):
                        buf[pos + b] = 0
                        sym[pos + b] = regs.get(src.split()[-1] if " " in src else src, src)
            continue
        # track simple register arithmetic for symbolic values
        r = re.match(r"(e[a-d]x|e[sd]i|[a-d][lh]), (.+)$", ops)
        if r and m in ("mov", "sub", "add", "lea", "neg"):
            dst, src = r.group(1), r.group(2)
            if m == "mov":
                regs[dst] = {"dword ptr [ebp - 0x18]": "W", "dword ptr [ebp - 0x1c]": "H"}.get(src, regs.get(src, src))
            elif m in ("sub", "add"):
                regs[dst] = "(%s%s%s)" % (regs.get(dst, dst), "-" if m == "sub" else "+", src)
            if dst in ("eax",):
                regs["al"] = regs[dst]
        if m == "rep stosd" or (m == "rep stosb"):
            buf.clear(); sym.clear()
        mm = re.match(r"dword ptr \[esp \+ 4\], (0x[0-9a-f]+)$", ops)
        if m == "mov" and mm:
            pending = int(mm.group(1), 16)
            continue
        mm = re.match(r"dword ptr \[esp \+ 8\], (0x[0-9a-f]+|\d+)$", ops)
        if m == "mov" and mm and pending is not None and pending > 0x1000:
            cand = pending
        if m == "call" and ops == "0x4e8f38":
            seek = pending
            pending = None
            writes.append([seek, None, dict(buf), dict(sym)])
        if m == "mov":
            mm = re.match(r"dword ptr \[esp \+ 8\], (0x[0-9a-f]+|\d+)$", ops)
            if mm and writes and writes[-1][1] is None:
                writes[-1][1] = int(mm.group(1), 0)
        if m == "call" and ops == "0x4ea05c":
            # buffer reset happens after each write in the source
            buf = {}
            sym = {}
    for seek, n, b, s in writes:
        if seek is None or n is None:
            continue
        data = bytes(b.get(k, 0) for k in range(n))
        va = seek + 0x400000
        symtxt = ",".join("%d=%s" % (k, s[k]) for k in sorted(s) if k < n)
        print("=== VA 0x%06x  file 0x%06x  %d bytes  %s  %s" % (va, seek, n, data.hex(" "), ("SYM " + symtxt) if symtxt else ""))
        orig = g[seek:seek + max(n, 8) + 8]
        print("  orig:")
        for k in md.disasm(orig, va):
            if k.address >= va + n + 4:
                break
            print("     %06x  %-6s %s" % (k.address, k.mnemonic, k.op_str))
        print("  new:")
        for k in md.disasm(data, va):
            print("     %06x  %-6s %s" % (k.address, k.mnemonic, k.op_str))


if __name__ == "__main__":
    main()
