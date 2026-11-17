import struct, sys, re
import capstone
sys.path.insert(0, ".")
from ws_dis import GAME, sections, va2off
g = open(GAME, "rb").read()
gb, gs = sections(g)
n, sva, vs, rp, rs = gs[0]
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.skipdata = True
code = g[rp:rp+vs]
vals = {0x320:"800",0x258:"600",0xfffffce0:"-800",0xfffffda8:"-600",0x31f:"799",0x257:"599",0x190:"400",0x12c:"300",0x1b4:"436"}
want = set(sys.argv[1].split(",")) if len(sys.argv)>1 else {"800","600","-800","-600","799","599"}
for ins in md.disasm(code, sva):
    for m in re.finditer(r"0x[0-9a-f]+", ins.op_str):
        v = int(m.group(0),16)
        if v in vals and vals[v] in want and ins.mnemonic not in ("call","jmp") and not ins.mnemonic.startswith("j"):
            # skip memory addresses
            print("%06x  %-6s %s" % (ins.address, ins.mnemonic, ins.op_str))
            break
