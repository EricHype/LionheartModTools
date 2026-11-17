import struct, sys
import capstone

PATCH = r"C:\Users\vkays\Downloads\lionheart_widescreen\LionheartWidescreenPatch.exe"
GAME = r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader\Lionheart.exe"


def sections(raw):
    pe = struct.unpack_from("<I", raw, 0x3c)[0]
    nsec = struct.unpack_from("<H", raw, pe + 6)[0]
    optsz = struct.unpack_from("<H", raw, pe + 20)[0]
    base = struct.unpack_from("<I", raw, pe + 24 + 28)[0]
    out = []
    for i in range(nsec):
        o = pe + 24 + optsz + 40 * i
        name = raw[o:o + 8].rstrip(b"\0").decode()
        vsize, va, rsize, rptr = struct.unpack_from("<IIII", raw, o + 8)
        out.append((name, base + va, vsize, rptr, rsize))
    return base, out


def va2off(secs, va):
    for n, sva, vs, rp, rs in secs:
        if sva <= va < sva + max(vs, rs):
            return rp + va - sva
    return None


def off2va(secs, off):
    for n, sva, vs, rp, rs in secs:
        if rp <= off < rp + rs:
            return sva + off - rp
    return None


if __name__ == "__main__":
    p = open(PATCH, "rb").read()
    pb, ps = sections(p)
    print("patch sections", ps)
    g = open(GAME, "rb").read()
    gb, gs = sections(g)
    print("game sections", gs, len(g))
    start, end = int(sys.argv[1], 16), int(sys.argv[2], 16)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    o = va2off(ps, start)
    for ins in md.disasm(p[o:o + (end - start)], start):
        print("%08x  %-8s %s" % (ins.address, ins.mnemonic, ins.op_str))
