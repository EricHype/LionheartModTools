import struct, sys
import capstone
sys.path.insert(0, ".")
from ws_dis import PATCH, GAME, sections, va2off

TYPES = {0: "W", 1: "H", 2: "float 1.2333 (0x3f9ddddb)", 3: "float 2.0", 4: "W-1", 5: "H-1", 6: "W-400", 7: "H-164",
         8: "H-198", 9: "nop nop", 10: "mov eax,edx + 5 nop", 11: "-W", 12: "-H", 15: "checkbox byte", 16: "H-241",
         17: "0", 18: "400-W", 0xff: "300"}
p = open(PATCH, "rb").read()
pb, ps = sections(p)
g = open(GAME, "rb").read()
gb, gs = sections(g)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
o = va2off(ps, 0x8cc000)
for k in range(0x36c // 12):
    va, t, mode = struct.unpack_from("<III", p, o + 12 * k)
    off = va2off(gs, va)
    sec = [n for n, sva, vs, rp, rs in gs if sva <= va < sva + max(vs, rs)]
    cur = g[off:off + 4]
    # find the instruction containing va: disassemble from a bit earlier
    ctx = ""
    if sec == [".text"]:
        best = None
        for back in range(1, 11):
            for ins in md.disasm(g[off - back:off + 12], va - back, 1):
                if ins.address + ins.size >= va + 1 and ins.address + ins.size <= va + 8 and ins.size > back:
                    best = ins
            if best:
                break
        ctx = "%06x: %s %s" % (best.address, best.mnemonic, best.op_str) if best else ""
    print("%2d VA 0x%06x %-6s mode %d  %-28s now %s  %s" % (k, va, sec[0] if sec else "?", mode, TYPES.get(t, t),
                                                         cur.hex(), ctx))
