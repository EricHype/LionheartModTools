"""Minimal 8-bit PNG read/write (RGB, and RGBA for icons), with no third-party imaging library.

Lives on its own because several tools want it and they should not have to care about each
other's constraints: `binktc0_encode` can only run under a 32-bit interpreter, and importing
a PNG reader from it drags that restriction into tools that never touch granny2.dll.

Deliberately small. `read_rgb` handles 8-bit truecolour RGB (colour type 2, non-interlaced), which is
what every tool here produces and what paint.net writes by default. Anything else raises
rather than guessing.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path


def read_rgb(path) -> tuple[int, int, bytearray]:
    """-> (width, height, RGB8 bytes, row-major top-down)."""
    d = Path(path).read_bytes()
    assert d[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG: %s" % path
    i, idat, w, h = 8, b"", None, None
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        tag, body = d[i + 4:i + 8], d[i + 8:i + 8 + ln]
        if tag == b"IHDR":
            w, h, bd, ct, _, _, inter = struct.unpack(">IIBBBBB", body[:13])
            assert (bd, ct) == (8, 2), \
                "expected 8-bit RGB (depth 8, colour type 2), got depth %d type %d" % (bd, ct)
            assert inter == 0, "interlaced PNGs are not supported"
        elif tag == b"IDAT":
            idat += body
        i += 12 + ln
    raw, stride = zlib.decompress(idat), w * 3
    out, prev, p = bytearray(), bytearray(stride), 0
    for _ in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f:
            for k in range(stride):
                a = line[k - 3] if k >= 3 else 0
                b = prev[k]
                c = prev[k - 3] if k >= 3 else 0
                if f == 1:
                    line[k] = (line[k] + a) & 255
                elif f == 2:
                    line[k] = (line[k] + b) & 255
                elif f == 3:
                    line[k] = (line[k] + ((a + b) >> 1)) & 255
                elif f == 4:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    line[k] = (line[k] + (a if (pa <= pb and pa <= pc)
                                          else (b if pb <= pc else c))) & 255
                else:
                    raise ValueError("unknown PNG filter %d" % f)
        out += line
        prev = line
    return w, h, out


def write_rgb(path, width: int, height: int, rgb) -> None:
    rows = b"".join(b"\x00" + bytes(rgb[y * width * 3:(y + 1) * width * 3])
                    for y in range(height))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b""))


def scale_nearest(width: int, height: int, rgb, factor: int) -> tuple[int, int, bytearray]:
    """Nearest-neighbour upscale, for looking at small icons without interpolation
    inventing colour the original does not contain."""
    out = bytearray()
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row += rgb[(y * width + x) * 3:(y * width + x) * 3 + 3] * factor
        out += row * factor
    return width * factor, height * factor, out


# ---------------------------------------------------------------------------
# RGBA: what an icon round-trip needs, since icons have transparency
# ---------------------------------------------------------------------------

# channels per pixel for each 8-bit PNG colour type this reads
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def read_rgba(path) -> tuple[int, int, bytearray]:
    """-> (width, height, RGBA8 bytes, row-major top-down), from any non-interlaced 8-bit
    PNG: greyscale, RGB, palette (with or without a tRNS transparency chunk),
    greyscale+alpha or RGBA. Covers what Paint, paint.net, GIMP, Krita and Photoshop
    write by default. 16-bit and interlaced images raise with a message saying how to
    re-save them, rather than being guessed at."""
    d = Path(path).read_bytes()
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s is not a PNG file" % path)
    i, idat, plte, trns = 8, b"", b"", b""
    w = h = ct = None
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        tag, body = d[i + 4:i + 8], d[i + 8:i + 8 + ln]
        if tag == b"IHDR":
            w, h, bd, ct, _, _, inter = struct.unpack(">IIBBBBB", body[:13])
            if bd != 8 or ct not in _CHANNELS:
                raise ValueError(
                    "%s is a %d-bit PNG (colour type %d); re-save it as an ordinary 8-bit "
                    "RGBA PNG" % (path, bd, ct))
            if inter:
                raise ValueError("%s is interlaced; re-save it with interlacing off" % path)
        elif tag == b"PLTE":
            plte = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        i += 12 + ln
    bpp = _CHANNELS[ct]
    raw, stride = zlib.decompress(idat), w * bpp
    lines, prev, p = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f:
            for k in range(stride):
                a = line[k - bpp] if k >= bpp else 0
                b = prev[k]
                c = prev[k - bpp] if k >= bpp else 0
                if f == 1:
                    line[k] = (line[k] + a) & 255
                elif f == 2:
                    line[k] = (line[k] + b) & 255
                elif f == 3:
                    line[k] = (line[k] + ((a + b) >> 1)) & 255
                elif f == 4:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    line[k] = (line[k] + (a if (pa <= pb and pa <= pc)
                                          else (b if pb <= pc else c))) & 255
                else:
                    raise ValueError("unknown PNG filter %d" % f)
        lines.append(line)
        prev = line
    out = bytearray()
    for line in lines:
        for x in range(w):
            px = line[x * bpp:(x + 1) * bpp]
            if ct == 6:
                out += px
            elif ct == 2:
                out += px + b"\xff"
            elif ct == 0:
                out += bytes((px[0], px[0], px[0], 255))
            elif ct == 4:
                out += bytes((px[0], px[0], px[0], px[1]))
            else:
                n = px[0]
                out += plte[n * 3:n * 3 + 3] + bytes((trns[n] if n < len(trns) else 255,))
    return w, h, out


def write_rgba(path, width: int, height: int, rgba) -> None:
    rows = b"".join(b"\x00" + bytes(rgba[y * width * 4:(y + 1) * width * 4])
                    for y in range(height))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b""))
