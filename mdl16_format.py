"""Reader/writer for Lionheart's .mdl16/.frm16 2D sprite format (CStandAloneFrame).

Reverse-engineered from Lionheart.exe's decompilation (Ghidra), cross-validated against
real game files and the community-documented FRM16 format at lionheart.eowyn.cz. See
docs/mdl16-icon-format.md for the full format writeup (byte layout, opcode grammar,
what's proven vs. not, and why) and docs/adding-a-new-item.md for how this fits into
modding an item's icon. Distinct from .gr2/gr2_format.py -- this is the unrelated 2D
icon/sprite format used by inventory windows and world pickups, not 3D character
models.

What this module does, in order of how confident each piece is:
  - decode_icon(): read any real game icon into plain RGBA pixels, for viewing/editing.
    Fully proven -- cross-validated against multiple real files.
  - recolor_icon_in_place(): PROVEN, PRODUCTION-READY. Recolors an existing real icon
    by transforming its stored color values while leaving every opcode/run boundary
    byte-identical to the original. Confirmed correct in-game. Use this for any "same
    shape, different colors" icon (e.g. a reskinned item variant) -- see
    docs/adding-a-new-item.md.
  - encode_icon_rle16() / encode_icon_raw(): build a brand-new icon from scratch (new
    shape/dimensions, not just recolored). NEITHER IS PROVEN WORKING IN-GAME. Both
    produce structurally valid files (round-trip correctly through this module's own
    decoder, and encode_icon_raw is at least accepted by the format's GetPixel/hit-test
    dispatch), but real in-game rendering came out corrupted every time despite
    extensive iteration -- the real encoder is applying some run-selection heuristic
    (which opcode to pick, how long to make each run, whether runs cross row
    boundaries) that was never fully reverse engineered; see encode_icon_rle16's
    docstring for the specific comparison that pinned this down. Treat these as
    experimental starting points for future work, not something to ship from.

File layout, byte offsets relative to the leading magic byte '2' (0x32) -- this magic
byte sits embedded inside a larger serialized object graph (Lionheart's generic
reflection/cache format), not necessarily at file offset 0:
    0      magic byte, always 0x32 ('2')
    1      unknown byte, consistently 0x10 across every sample seen -- not decoded, not
           needed for read or write
    2..3   hotspot X, i16 LE (stored negated relative to the in-memory field)
    4..5   hotspot Y, i16 LE (stored negated relative to the in-memory field)
    6..7   width,  u16 LE
    8..9   height, u16 LE
    10..11 unknown/reserved, always 0x0000 in samples seen
    12..15 flags, u32 LE -- bits 1-2 select compression mode (0=raw, 2=RLE 8bpp
           palette, 4=RLE 16bpp -- the mode every real inventory icon uses); bits
           5-8 (mask 0x1e0) select bit depth for the raw mode (0x20/0x40/0x80/0x100 =
           8/16/24/32bpp)
    16..35 five buffer sizes, u32 LE each (buffers 1-5). Only buffer 1 (main color)
           and, for the RLE-16bpp mode, buffers 4+5 (a highlight/overlay plane) are
           populated by real assets; buffers 2/3 are unused in every sample seen.
    36..   buffer 1's raw bytes (size = first u32 above), followed immediately by any
           other populated buffers in order (2, 3, 4, 5)

Buffer 1 pixel encoding for RLE-16bpp mode (flags & 6 == 4), decoded as one continuous
opcode stream covering exactly width*height pixels in row-major order (NOT restarted
per row -- the per-row byte table the loader builds is for the game's own random-access
GetPixel/hit-test path, not needed for a full sequential decode):
    ctrl byte with bit 7 set   : skip run,    (ctrl & 0x7f) pixels of value 0 (empty)
    ctrl byte with bit 6 clear : short run,   (ctrl & 0x3f) pixels of ONE repeated u16
                                 value (2 more bytes follow the control byte)
    ctrl byte with bit 6 set   : literal run, (ctrl & 0x3f) distinct u16 values follow
Each decoded u16 is RGB565 (bits 11-15 red, 5-10 green, 0-4 blue); value 0 means "no
color here" -- real assets fall back to a secondary highlight plane (buffers 4+5) for
those pixels, not yet implemented here since buffer 1 alone already reconstructs a
complete, recognizable image (confirmed against a real icon).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass


MAGIC = 0x32


@dataclass
class IconHeader:
    offset: int          # byte offset of the magic byte within the file
    hotspot_x: int
    hotspot_y: int
    width: int
    height: int
    flags: int
    buffer_sizes: tuple[int, int, int, int, int]

    @property
    def data_offset(self) -> int:
        return self.offset + 36


def find_header(data: bytes) -> IconHeader | None:
    """Scan for a plausible CStandAloneFrame header. Returns the first match, or None."""
    for off in range(len(data) - 36):
        if data[off] != MAGIC:
            continue
        unk1, hx, hy, w, h = struct.unpack_from("<BhhHH", data, off + 1)
        if unk1 != 0x10 or not (1 <= w <= 512) or not (1 <= h <= 512):
            continue
        (flags,) = struct.unpack_from("<I", data, off + 12)
        sizes = struct.unpack_from("<5I", data, off + 16)
        if sum(sizes) >= len(data) or any(s >= len(data) for s in sizes):
            continue
        return IconHeader(off, hx, hy, w, h, flags, sizes)
    return None


def _rgb565_to_rgb888(v: int) -> tuple[int, int, int]:
    r5 = (v >> 11) & 0x1F
    g6 = (v >> 5) & 0x3F
    b5 = v & 0x1F
    return (r5 * 255 // 31, g6 * 255 // 63, b5 * 255 // 31)


def _decode_rle16_plane(buf: bytes, total_pixels: int) -> list[int]:
    """Decode a continuous RLE-16bpp opcode stream into `total_pixels` u16 values.

    Opcode grammar (confirmed against Lionheart's own disassembly AND independently
    against the community-documented FRM16 format at lionheart.eowyn.cz -- both agree):
      bit7 set        : skip-run,    bits0-6 = number of transparent pixels
      bit7=0, bit6=1  : literal-run, bits0-5 = number of distinct 16bpp pixels following
      bit7=0, bit6=0  : repeat-run,  bits0-5 = repetitions of the ONE 16bpp pixel following
    """
    out: list[int] = []
    i = 0
    while len(out) < total_pixels and i < len(buf):
        ctrl = buf[i]
        if ctrl & 0x80:
            count = ctrl & 0x7F
            out.extend([0] * count)
            i += 1
        elif (ctrl & 0x40) == 0:
            count = ctrl & 0x3F
            if count == 0:
                break
            val = struct.unpack_from("<H", buf, i + 1)[0]
            out.extend([val] * count)
            i += 3
        else:
            count = ctrl & 0x3F
            if count == 0:
                break
            vals = struct.unpack_from(f"<{count}H", buf, i + 1)
            out.extend(vals)
            i += 1 + count * 2
    out = out[:total_pixels]
    out.extend([0] * (total_pixels - len(out)))
    return out


def decode_icon(data: bytes, header: IconHeader | None = None) -> dict:
    """Decode a .mdl16/.frm16 icon's main color plane into RGBA pixel rows.

    Only supports the RLE-16bpp mode (flags & 6 == 4) that every real inventory icon
    in the game uses. Returns a dict with width/height/hotspot and `rows`: a list of
    `height` lists of `width` (r,g,b,a) tuples, row-major, top to bottom.
    """
    header = header or find_header(data)
    if header is None:
        raise ValueError("no CStandAloneFrame header found in data")
    if header.flags & 6 != 4:
        raise NotImplementedError(
            f"only RLE-16bpp icons (flags&6==4) are supported; got flags={header.flags:#x}"
        )
    w, h = header.width, header.height
    buf1_size = header.buffer_sizes[0]
    buf1 = data[header.data_offset: header.data_offset + buf1_size]
    # Continuous decode from byte 0 -- no leading DWORD, no trailing lookup table (a
    # community-documented FRM16 structure with both was tried; empirically checking a
    # real item icon's own bytes showed it doesn't apply here, and our own writer
    # doesn't emit either). Decode just stops once w*h pixels are produced.
    values = _decode_rle16_plane(buf1, w * h)
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            v = values[y * w + x]
            if v == 0:
                row.append((0, 0, 0, 0))
            else:
                r, g, b = _rgb565_to_rgb888(v)
                row.append((r, g, b, 255))
        rows.append(row)
    return {
        "width": w, "height": h,
        "hotspot_x": header.hotspot_x, "hotspot_y": header.hotspot_y,
        "rows": rows,
    }


def _rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    return ((r * 31 // 255) << 11) | ((g * 63 // 255) << 5) | (b * 31 // 255)


def recolor_icon_in_place(data: bytes, color_transform, header: IconHeader | None = None) -> bytes:
    """Recolor a REAL icon file by walking its existing opcode stream and applying
    `color_transform(rgb565_value) -> rgb565_value` to every stored color (skip-run
    pixels have no stored value and are left alone; repeat-run's one value and every
    literal-run value are transformed). Every control byte, run length, and the overall
    file length are left byte-for-byte identical to the input -- only the 2-byte color
    values change.

    THIS IS THE PROVEN, PRODUCTION-READY WAY TO RECOLOR AN ICON. A general "build a
    valid RLE stream from an arbitrary new image" encoder (see encode_icon_rle16) was
    attempted extensively and never got a clean render in-game, despite the opcode
    grammar itself being independently confirmed correct (by this exact function).
    Comparing a real file's opcode stream against encode_icon_rle16's output for the
    same image showed the real encoder uses ~half as many, much longer runs (e.g. one
    62-row icon: 187 real opcodes vs. 343 from encode_icon_rle16, with repeat-run used
    once in the real file vs. 114 times from an eager from-scratch encoder) -- the real
    encoder is applying some run-selection heuristic that was never fully reverse
    engineered. Recoloring in place sidesteps needing to know it: reuse the original,
    already-correct structure, and only touch the color data.

    Limitation: this can only recolor an EXISTING icon (same silhouette/shape as the
    source), not author new shapes or dimensions. For this project's purposes (e.g. a
    reskinned "Great Healing" variant of the existing "Extra Healing" flask), that's
    exactly what's needed.
    """
    header = header or find_header(data)
    if header is None:
        raise ValueError("no CStandAloneFrame header found in data")
    if header.flags & 6 != 4:
        raise NotImplementedError(
            f"only RLE-16bpp icons (flags&6==4) are supported; got flags={header.flags:#x}"
        )
    buf1_off = header.data_offset
    buf1 = bytearray(data[buf1_off: buf1_off + header.buffer_sizes[0]])

    i = 0
    n = len(buf1)
    while i < n:
        ctrl = buf1[i]
        if ctrl & 0x80:
            i += 1
        elif (ctrl & 0x40) == 0:
            count = ctrl & 0x3F
            if count == 0:
                break
            v = struct.unpack_from("<H", buf1, i + 1)[0]
            struct.pack_into("<H", buf1, i + 1, color_transform(v))
            i += 3
        else:
            count = ctrl & 0x3F
            if count == 0:
                break
            for k in range(count):
                off = i + 1 + k * 2
                v = struct.unpack_from("<H", buf1, off)[0]
                struct.pack_into("<H", buf1, off, color_transform(v))
            i += 1 + count * 2

    out = bytearray(data)
    out[buf1_off: buf1_off + header.buffer_sizes[0]] = buf1
    return bytes(out)


def encode_icon_raw(width: int, height: int, rows: list[list[tuple[int, int, int, int]]],
                     hotspot_x: int = 0, hotspot_y: int = 0) -> bytes:
    """Build a standalone .mdl16-style buffer for a new icon using the format's
    UNCOMPRESSED 16bpp raw pixel mode (flags bit2 clear, bpp bits = 0x40).

    CONFIRMED UNSAFE IN PRACTICE: this mode is structurally valid per the format's own
    GetPixel/hit-test dispatch, but crashed the real game immediately on opening the
    inventory screen once an icon using it existed in a player's inventory. No shipped
    asset uses this mode (every real icon is RLE-16bpp), and the UI's actual icon-blit
    code path almost certainly assumes RLE unconditionally rather than branching on
    flags the way the generic CStandAloneFrame vtable methods do. Use
    encode_icon_rle16() instead for anything that will actually be rendered in-game;
    kept here only for reference/format-completeness.

    This produces just the 36-byte header + buffer1 payload -- it is NOT a complete,
    standalone cache-file object graph (that envelope's reflection-field format is not
    reproduced here). Use it to patch the pixel section of a copy of an existing real
    .mdl16/.frm16 file in place (same offset, same header size), not to author a file
    from nothing.
    """
    flags = 0x40  # mode 0 (raw) | bpp 0x40 (16bpp)
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            r, g, b, a = rows[y][x]
            v = _rgb888_to_rgb565(r, g, b) if a >= 128 else 0
            pixels += struct.pack("<H", v)
    sizes = (len(pixels), 0, 0, 0, 0)
    header = struct.pack(
        "<BBhhHHHI5I",
        MAGIC, 0x10, hotspot_x, hotspot_y, width, height, 0, flags, *sizes,
    )
    return header + bytes(pixels)


def _encode_rle16_plane(values: list[int]) -> bytes:
    """Inverse of _decode_rle16_plane -- encodes a flat list of u16 RGB565 values (0 =
    transparent) into a continuous RLE-16bpp opcode stream, using all three opcode
    types. Confirmed correct at the opcode level by a much stronger test than any
    round-trip: editing ONLY the 2-byte color values inside a real shipped icon's
    EXISTING literal-run opcodes, leaving every control byte and run boundary
    byte-identical to the original, rendered perfectly in-game. That isolates the bug
    that plagued earlier attempts to the STRUCTURE this function chooses when building
    a stream from scratch (particularly forcing every row to start a fresh opcode,
    which the real encoder evidently doesn't do -- the untouched original has runs
    crossing row boundaries freely and works fine), not to opcode semantics.
    """
    out = bytearray()
    i = 0
    n = len(values)
    while i < n:
        if values[i] == 0:
            j = i
            while j < n and values[j] == 0 and (j - i) < 127:
                j += 1
            out.append(0x80 | (j - i))
            i = j
            continue
        run_end = i + 1
        while run_end < n and values[run_end] == values[i] and (run_end - i) < 63:
            run_end += 1
        if run_end - i >= 2:
            out.append(run_end - i)  # bits 6,7 clear -> repeat-run
            out += struct.pack("<H", values[i])
            i = run_end
            continue
        j = i
        while j < n and values[j] != 0 and (j - i) < 63:
            if j > i:
                k = j
                while k < n and values[k] == values[j] and (k - j) < 63:
                    k += 1
                if k - j >= 2:
                    break
            j += 1
        count = j - i
        out.append(0x40 | count)
        for k in range(i, j):
            out += struct.pack("<H", values[k])
        i = j
    return bytes(out)


def encode_icon_rle16(width: int, height: int, rows: list[list[tuple[int, int, int, int]]],
                       hotspot_x: int = 0, hotspot_y: int = 0) -> bytes:
    """Build a standalone .mdl16-style buffer for a new icon using the format's real
    RLE-16bpp mode (flags & 6 == 4, bpp bits = 0x40) -- the mode every actual shipped
    icon uses. Encodes buffer 1 only (the main color plane); buffers 2-5 are left empty
    (size 0), matching a plain fully-opaque-or-transparent icon with no secondary
    highlight/alpha overlay.

    Buffer 1 layout: pure continuous RLE pixel data across the WHOLE image (width*height
    pixels), with NO artificial row-boundary constraint -- a run is free to cross from
    the end of one row into the start of the next, exactly like the real, already-
    shipped icon this was validated against (see _encode_rle16_plane's docstring for
    the in-place-edit test that proved this). No leading size DWORD, no trailing
    lookup table (both tried, based on community FRM16 documentation that turned out to
    describe a different use case than item icons; neither helped).

    Produces just the 36-byte header + buffer1 payload, same caveats as
    encode_icon_raw() re: not being a full standalone cache-file object graph -- splice
    this over the pixel section of a copy of a real .mdl16/.frm16 file.
    """
    values = []
    for y in range(height):
        for x in range(width):
            r, g, b, a = rows[y][x]
            values.append(_rgb888_to_rgb565(r, g, b) if a >= 128 else 0)
    buf1 = _encode_rle16_plane(values)

    flags = 4 | 0x40  # mode 4 (RLE) | bpp 0x40 (16bpp)
    sizes = (len(buf1), 0, 0, 0, 0)
    header = struct.pack(
        "<BBhhHHHI5I",
        MAGIC, 0x10, hotspot_x, hotspot_y, width, height, 0, flags, *sizes,
    )
    return header + buf1
