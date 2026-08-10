"""Reader/writer for Lionheart's .mdl16/.frm16 2D sprite format (CStandAloneFrame).

Reverse-engineered from Lionheart.exe's decompilation (Ghidra), cross-validated against
real game files and the community-documented FRM16 format at lionheart.eowyn.cz. See
docs/mdl16-icon-format.md for the full format writeup (byte layout, opcode grammar,
what's proven vs. not, and why) and docs/adding-a-new-item.md for how this fits into
modding an item's icon. Distinct from .gr2/gr2_format.py -- this is the unrelated 2D
icon/sprite format used by inventory windows and world pickups, not 3D character
models.

What this module does:
  - decode_icon(): read any real game icon into plain RGBA pixels, for viewing/editing.
  - recolor_icon_in_place(): recolor an existing real icon by transforming its stored
    color values while leaving every opcode/run boundary byte-identical to the original.
    Confirmed correct in-game. Use this for any "same shape, different colors" icon --
    see docs/adding-a-new-item.md. The color_transform callback can optionally take a
    second (pixel_index) argument to vary behavior by position.
  - encode_icon_rle16() + build_icon_file(): author a brand-new icon (new shape and
    dimensions, not just recolored), and verify_icon() to check it before deploying.
  - encode_icon_raw(): the uncompressed mode. CONFIRMED TO CRASH THE GAME; no shipped
    asset uses it. Reference only.

THE THING THAT USED TO BREAK FROM-SCRATCH ICONS, and the one rule to not get wrong:
every buffer carries an on-disk table of `height` u32 row offsets, and the engine
decodes each row by seeking to table[y] and resetting its x-counter to zero. So rows are
strictly opcode-aligned (no run may cross a row boundary) and table[y] must be
byte-exact. Earlier versions of this module encoded one continuous stream and tried to
reconstruct the offsets afterwards, which is why every from-scratch icon rendered
correctly for a few rows and then fell apart. See docs/mdl16-icon-format.md, "The
per-row offset table".

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
    36..   for each populated buffer, in order: a u32 holding that buffer's own declared
           size (counted inside it), then each row's opcodes, then -- outside the
           declared size -- a u32 table[height] of row offsets from the buffer's start.
           Then 8 zero bytes at EOF.

Buffer 1 pixel encoding for RLE-16bpp mode (flags & 6 == 4). Each row is decoded
independently, starting at table[y] and covering exactly `width` pixels; no run crosses
a row boundary:
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
    # Bit replication, so that _rgb888_to_rgb565 is an exact inverse. (An earlier
    # `c * 255 // max` form was NOT invertible -- e.g. r5=1 -> 8 -> back to 0 -- which
    # silently shifted colors on every decode/re-encode round-trip.)
    r5 = (v >> 11) & 0x1F
    g6 = (v >> 5) & 0x3F
    b5 = v & 0x1F
    return ((r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2))


def read_row_table(data: bytes, header: IconHeader, buffer_index: int = 0) -> list[int]:
    """Read a buffer's on-disk per-row offset table: `height` u32 values sitting
    immediately after that buffer's declared bytes.

    `table[y]` is the byte offset, relative to the START of the buffer (not the file), of
    row y's first opcode. `table[0]` is always 4, because every buffer begins with a
    4-byte u32 holding its own declared size before row 0's opcodes start.

    This is exactly the array the game loads into the object and that GetColorAt
    (FUN_0055ec80) indexes per row -- see decode_plane_rows.
    """
    off = header.data_offset + sum(header.buffer_sizes[:buffer_index + 1])
    end = off + header.height * 4
    if end > len(data):
        raise ValueError(f"row table for buffer {buffer_index + 1} runs past end of file")
    return list(struct.unpack_from(f"<{header.height}I", data, off))


def decode_row(buf: bytes, start: int, width: int) -> tuple[list[int], int]:
    """Decode exactly `width` pixels of one row, starting at byte `start` of `buf`.

    Returns (u16 RGB565 values, bytes consumed). Mirrors GetColorAt's RLE-16bpp loop
    (FUN_0055ec80 at 0x0055ec80) exactly: it seeks to rowtable[y], resets its x-counter
    to 0, and walks opcodes until x reaches the image width. Rows are therefore strictly
    opcode-aligned -- no run may cross a row boundary. Verified against all 264 vanilla
    inventory icons: every row decodes to exactly `width` pixels and consumes exactly
    `table[y+1] - table[y]` bytes.

    Opcode grammar (confirmed against Lionheart's own disassembly AND independently
    against the community-documented FRM16 format at lionheart.eowyn.cz -- both agree):
      bit7 set        : skip-run,    bits0-6 = number of transparent pixels
      bit7=0, bit6=1  : literal-run, bits0-5 = number of distinct 16bpp pixels following
      bit7=0, bit6=0  : repeat-run,  bits0-5 = repetitions of the ONE 16bpp pixel following
    """
    out: list[int] = []
    i = start
    while len(out) < width:
        if i >= len(buf):
            raise ValueError(f"row starting at {start} ran off the end of the buffer")
        ctrl = buf[i]
        if ctrl & 0x80:
            count = ctrl & 0x7F
            out.extend([0] * count)
            i += 1
        elif (ctrl & 0x40) == 0:
            count = ctrl & 0x3F
            if count == 0:
                raise ValueError(f"zero-length repeat-run at byte {i}")
            out.extend([struct.unpack_from("<H", buf, i + 1)[0]] * count)
            i += 3
        else:
            count = ctrl & 0x3F
            if count == 0:
                raise ValueError(f"zero-length literal-run at byte {i}")
            out.extend(struct.unpack_from(f"<{count}H", buf, i + 1))
            i += 1 + count * 2
    if len(out) != width:
        raise ValueError(f"row starting at {start} overran the row: {len(out)} > {width} px")
    return out, i - start


def decode_plane_rows(buf: bytes, table: list[int], width: int, height: int) -> list[list[int]]:
    """Decode a whole plane row-by-row via its on-disk offset table, the way the engine
    does. Returns `height` lists of `width` u16 RGB565 values.
    """
    return [decode_row(buf, table[y], width)[0] for y in range(height)]


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
    # Decode row-by-row from the on-disk offset table, exactly as the engine does. An
    # earlier version decoded continuously from byte 0, which misparsed the 4-byte size
    # prefix at the head of the buffer as opcodes -- that produced a wrong result on
    # 264/264 real icons (subtly enough to pass visual inspection, but it is what
    # manufactured the phantom "chaotic band" in the ShortSword icons' top rows).
    values = decode_plane_rows(buf1, read_row_table(data, header), w, h)
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            v = values[y][x]
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
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def recolor_icon_in_place(data: bytes, color_transform, header: IconHeader | None = None) -> bytes:
    """Recolor a REAL icon file by walking its existing opcode stream and applying
    `color_transform(rgb565_value) -> rgb565_value` to every stored color (skip-run
    pixels have no stored value and are left alone; repeat-run's one value and every
    literal-run value are transformed). Every control byte, run length, and the overall
    file length are left byte-for-byte identical to the input -- only the 2-byte color
    values change.

    This is the cheapest way to give an item distinct art when the existing silhouette
    is already right: reuse the original file's structure, touch only the color data, so
    nothing about row alignment or the offset table can go wrong. To author a NEW shape
    or new dimensions, use encode_icon_rle16() + build_icon_file() instead.

    Only operates on buffer 1. Buffer 4 (a second color plane, with buffer 5 as its
    parallel alpha mask, consulted only where buffer 1 decodes to 0 -- see
    docs/mdl16-icon-format.md) is left untouched. Its on-disk layout is now understood
    and mirrors buffer 1's, so extending this to buffer 4 would be mechanical, but no
    icon this project has recolored has needed it.
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

    # pixel_index (y*width + x) is passed to color_transform as a second, OPTIONAL
    # argument (tried first, falling back to the single-argument form) so a caller can
    # vary the recolor by position -- a gradient, or repainting only part of the
    # silhouette. It used to also be needed to work around the ShortSword "artifact
    # rows"; that was this function corrupting the buffer's size prefix, and is fixed.
    import inspect
    _wants_index = len(inspect.signature(color_transform).parameters) >= 2

    def _apply(v, idx):
        return color_transform(v, idx) if _wants_index else color_transform(v)

    # Walk each row from its own table offset rather than continuously from byte 0. That
    # makes it structurally impossible to touch the 4-byte size prefix or to drift out of
    # opcode phase -- both of which the old continuous walker did on every real file.
    table = read_row_table(data, header)
    width = header.width
    for y in range(header.height):
        i = table[y]
        x = 0
        while x < width:
            ctrl = buf1[i]
            if ctrl & 0x80:
                x += ctrl & 0x7F
                i += 1
            elif (ctrl & 0x40) == 0:
                count = ctrl & 0x3F
                v = struct.unpack_from("<H", buf1, i + 1)[0]
                struct.pack_into("<H", buf1, i + 1, _apply(v, y * width + x))
                x += count
                i += 3
            else:
                count = ctrl & 0x3F
                for k in range(count):
                    off = i + 1 + k * 2
                    v = struct.unpack_from("<H", buf1, off)[0]
                    struct.pack_into("<H", buf1, off, _apply(v, y * width + x + k))
                x += count
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


def _encode_rle16_row(values: list[int]) -> bytes:
    """Encode ONE row of u16 RGB565 values (0 = transparent) into an RLE-16bpp opcode
    stream covering exactly `len(values)` pixels.

    Rows are encoded independently and no run crosses a row boundary -- that is a hard
    requirement of the format, not a stylistic choice: the engine seeks to rowtable[y]
    and decodes each row from a reset x-counter (see decode_row). Getting this wrong is
    what broke every previous from-scratch icon attempt.
    """
    # Repeat-run threshold: a real reference file (Potion Extra Healing, 41x62) uses
    # repeat-run exactly ONCE in its entire 187-opcode stream (length 11), while
    # literal-run is used 62 times (avg length 23.1) -- the real encoder overwhelmingly
    # prefers long literal-runs and treats repeat-run as an edge case for unusually long
    # flat color fields. Matching that keeps our output shaped like real files. It is
    # only cosmetic now: with per-row addressing the engine cannot care which opcode mix
    # a row uses, as long as the row's pixel count and byte length are exact.
    REPEAT_THRESHOLD = 12

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
        if run_end - i >= REPEAT_THRESHOLD:
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
                if k - j >= REPEAT_THRESHOLD:
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
    """Build the 36-byte header + buffer 1 + its row table for a new icon, using the
    format's real RLE-16bpp mode (flags & 6 == 4, bpp bits = 0x40) -- the mode every
    actual shipped icon uses. Encodes buffer 1 only (the main color plane); buffers 2-5
    are left empty, matching the two vanilla icons that ship that way (Deed Silver Mine,
    Lava Troll Hide) -- no secondary highlight/alpha overlay.

    Buffer 1 layout, matching all 264 vanilla icons exactly:
        u32   buffer 1's own declared size (counted inside that size)
        rows  each row's opcodes, exactly `width` pixels, no run crossing a row boundary
    followed immediately (outside the declared size) by:
        u32   table[height], table[y] = byte offset of row y's opcodes from buffer start

    Use build_icon_file() to wrap this into a complete, loadable file.
    """
    if len(rows) != height or any(len(r) != width for r in rows):
        raise ValueError(f"rows must be {height} lists of {width} pixels")

    row_blobs = []
    for y in range(height):
        values = []
        for r, g, b, a in rows[y]:
            v = _rgb888_to_rgb565(r, g, b) if a >= 128 else 0
            # 0 is the format's "transparent" sentinel, so nudge a legitimately opaque
            # near-black pixel off it rather than punching a hole in the art.
            if v == 0 and a >= 128:
                v = 1
            values.append(v)
        row_blobs.append(_encode_rle16_row(values))

    table = []
    off = 4  # row 0 starts just past buffer 1's own size u32
    for blob in row_blobs:
        table.append(off)
        off += len(blob)
    buf1 = struct.pack("<I", off) + b"".join(row_blobs)

    flags = 4 | 0x40  # mode 4 (RLE) | bpp 0x40 (16bpp)
    sizes = (len(buf1), 0, 0, 0, 0)
    header = struct.pack(
        "<BBhhHHHI5I",
        MAGIC, 0x10, hotspot_x, hotspot_y, width, height, 0, flags, *sizes,
    )
    return header + buf1 + struct.pack(f"<{height}I", *table)


def build_icon_file(donor: bytes, width: int, height: int,
                    rows: list[list[tuple[int, int, int, int]]],
                    hotspot_x: int = 0, hotspot_y: int = 0) -> bytes:
    """Assemble a complete, loadable .mdl16 file for brand-new art.

    The pixel data sits inside a larger serialized object-graph envelope (Lionheart's
    generic reflection/cache format) that this module does not synthesize, so a real
    file is used as the envelope: everything before its magic byte is kept verbatim,
    everything from the magic byte on is replaced. The 8 zero bytes every real icon ends
    with are preserved. The donor's embedded model-path string is left as-is -- proven
    harmless, since the game locates the file by its filesystem path (every icon this
    project has shipped is a copied envelope).

    Pass a donor that ships with buffer 1 ONLY -- `Deed Silver Mine.mdl16` or
    `Lava Troll Hide.mdl16` -- so the envelope isn't describing buffers 4/5 that the new
    file won't have.
    """
    dh = find_header(donor)
    if dh is None:
        raise ValueError("donor has no CStandAloneFrame header")
    if dh.buffer_sizes[1:] != (0, 0, 0, 0):
        raise ValueError(
            "donor must be a buffer-1-only icon (try 'Deed Silver Mine.mdl16'); "
            f"got buffer sizes {dh.buffer_sizes}"
        )
    body = encode_icon_rle16(width, height, rows, hotspot_x, hotspot_y)
    return donor[:dh.offset] + body + b"\x00" * 8


def verify_icon(data: bytes) -> dict:
    """Re-parse a .mdl16 file the way the engine does and assert it is well-formed.

    Raises ValueError on anything the engine would choke on; otherwise returns the
    decoded pixel rows plus some stats. Run this on any generated icon BEFORE deploying
    -- in-game verification is slow, and every failure mode found so far shows up here.
    """
    header = find_header(data)
    if header is None:
        raise ValueError("no CStandAloneFrame header found")
    if header.flags & 6 != 4:
        raise ValueError(f"not RLE-16bpp: flags={header.flags:#x}")
    w, h = header.width, header.height
    buf1_size = header.buffer_sizes[0]
    buf1 = data[header.data_offset: header.data_offset + buf1_size]

    declared = struct.unpack_from("<I", buf1, 0)[0]
    if declared != buf1_size:
        raise ValueError(f"buffer 1 size prefix {declared} != declared size {buf1_size}")

    table = read_row_table(data, header)
    if table[0] != 4:
        raise ValueError(f"table[0] must be 4 (past the size prefix), got {table[0]}")

    values = []
    for y in range(h):
        row, used = decode_row(buf1, table[y], w)
        expected_end = table[y + 1] if y + 1 < h else buf1_size
        if table[y] + used != expected_end:
            raise ValueError(
                f"row {y} consumed {used} bytes ending at {table[y] + used}, "
                f"but the next row starts at {expected_end}"
            )
        values.append(row)

    populated = sum(1 for s in header.buffer_sizes if s)
    expected_len = header.data_offset + sum(header.buffer_sizes) + populated * h * 4 + 8
    if expected_len != len(data):
        raise ValueError(f"file size {len(data)} != expected {expected_len}")

    return {
        "width": w, "height": h, "rows": values,
        "buf1_size": buf1_size,
        "opaque_pixels": sum(1 for r in values for v in r if v),
    }
