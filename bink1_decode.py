"""Decode a single Bink1 (`GrannyBinkTextureEncoding`, Encoding=3) intra-coded video
frame into RGB24 pixels -- used for `Texture.Images[].MIPLevels[].Pixels` payloads in
Lionheart's `.gr2` files, which embed one raw Bink1 video packet per texture (no BIKi
file container: no magic, no frame index, no audio tracks -- just the same bytes
`DecodeFrame` in a real Bink player would feed straight into its plane decoder after
stripping those).

Ported by hand from jmarshall23/libbink (github.com/jmarshall23/libbink, LGPL-2.1+,
itself derived in part from FFmpeg's Bink decoder tables) -- specifically the
container-independent frame/plane decode path (BitReader, Huffman tree read, bundle
readers, block-type dispatch, DCT, YUV->RGB), skipping everything container- or
audio-specific (BIKi header/frame index/audio decode), which doesn't apply to a lone
embedded texture frame. See docs/bink-texture-format.md for the full writeup: how this
was found (Encoding enum), what's a faithful 1:1 port vs. adapted for the "one
standalone frame, no previous frame" case, and open questions (row order not yet
visually confirmed).

Usage:
    from bink1_decode import decode_bink1_frame
    rgb_bytes = decode_bink1_frame(width, height, packet_bytes)  # top-down RGB24
"""
from __future__ import annotations

import bink_data

SOURCE_BLOCK_TYPES = 0
SOURCE_SUB_BLOCK_TYPES = 1
SOURCE_COLORS = 2
SOURCE_PATTERN = 3
SOURCE_X_OFF = 4
SOURCE_Y_OFF = 5
SOURCE_INTRA_DC = 6
SOURCE_INTER_DC = 7
SOURCE_RUN = 8
SOURCE_COUNT = 9

BLOCK_SKIP = 0
BLOCK_SCALED = 1
BLOCK_MOTION = 2
BLOCK_RUN = 3
BLOCK_RESIDUE = 4
BLOCK_INTRA = 5
BLOCK_FILL = 6
BLOCK_INTER = 7
BLOCK_PATTERN = 8
BLOCK_RAW = 9

RUN_LENGTHS = (4, 8, 12, 32)


def _to_int8(b: int) -> int:
    return b - 256 if b >= 128 else b


def _int_log2(value: int) -> int:
    result = 0
    value >>= 1
    while value:
        result += 1
        value >>= 1
    return result


class BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.size_bits = len(data) * 8
        self.bit = 0
        self.failed = False

    def read(self, count: int) -> int:
        if count > 32 or self.bit > self.size_bits or count > self.size_bits - self.bit:
            self.failed = True
            return 0
        value = 0
        data = self.data
        bit = self.bit
        for _ in range(count):
            value = (value << 1) | ((data[bit >> 3] >> (7 - (bit & 7))) & 1)
            bit += 1
        self.bit = bit
        return value

    def peek(self, count: int) -> int:
        old_bit = self.bit
        old_failed = self.failed
        value = self.read(count)
        self.bit = old_bit
        self.failed = old_failed
        return value

    def skip(self, count: int) -> None:
        self.read(count)

    def align32(self) -> None:
        remainder = self.bit & 31
        if remainder:
            self.skip(32 - remainder)


class Tree:
    __slots__ = ("codebook", "symbols")

    def __init__(self):
        self.codebook = 0
        self.symbols = [0] * 16


class Bundle:
    __slots__ = ("length_bits", "huffman", "data", "capacity", "decoded", "current")

    def __init__(self):
        self.length_bits = 0
        self.huffman = Tree()
        self.data = None
        self.capacity = 0
        self.decoded = None
        self.current = None


class Plane:
    __slots__ = ("width", "height", "stride", "pixels")

    def __init__(self, width: int, height: int, stride: int):
        self.width = width
        self.height = height
        self.stride = stride
        self.pixels = bytearray(stride * height)


def _decode_huffman(bits: BitReader, tree: Tree) -> int:
    codebook = tree.codebook
    lens = bink_data.bink_tree_lens[codebook]
    codes = bink_data.bink_tree_bits[codebook]
    for length in range(1, 8):
        code = bits.peek(length)
        for symbol in range(16):
            if lens[symbol] == length and codes[symbol] == code:
                bits.skip(length)
                return tree.symbols[symbol]
    bits.failed = True
    return 0


def _merge(bits: BitReader, dest: list, dest_base: int, src: list, src_base: int, size: int) -> None:
    d = dest_base
    s1 = src_base
    s2 = src_base + size
    n1 = size
    n2 = size
    while n1 and n2:
        if not bits.read(1):
            dest[d] = src[s1]
            d += 1
            s1 += 1
            n1 -= 1
        else:
            dest[d] = src[s2]
            d += 1
            s2 += 1
            n2 -= 1
    while n1 > 0:
        dest[d] = src[s1]
        d += 1
        s1 += 1
        n1 -= 1
    while n2 > 0:
        dest[d] = src[s2]
        d += 1
        s2 += 1
        n2 -= 1


def _read_tree(bits: BitReader, tree: Tree) -> bool:
    if bits.size_bits - bits.bit < 4:
        return False
    tree.codebook = bits.read(4)
    if tree.codebook == 0:
        tree.symbols = list(range(16))
        return not bits.failed

    first = [0] * 16
    if bits.read(1):
        last = bits.read(3)
        for index in range(last + 1):
            tree.symbols[index] = bits.read(4)
            first[tree.symbols[index]] = 1
        value = 0
        while value < 16 and last < 15:
            if not first[value]:
                last += 1
                tree.symbols[last] = value
            value += 1
    else:
        levels = bits.read(2)
        input_arr = list(range(16))
        output_arr = [0] * 16
        for level in range(levels + 1):
            size = 1 << level
            offset = 0
            while offset < 16:
                _merge(bits, output_arr, offset, input_arr, offset, size)
                offset += size << 1
            input_arr, output_arr = output_arr, input_arr
        tree.symbols = list(input_arr[:16])
    return not bits.failed


def _read_bundle_header(bits: BitReader, decoder: "Decoder", source: int) -> bool:
    if source == SOURCE_COLORS:
        for tree_index in range(16):
            if not _read_tree(bits, decoder.color_high[tree_index]):
                return False
        decoder.last_color = 0
    if source != SOURCE_INTRA_DC and source != SOURCE_INTER_DC:
        if not _read_tree(bits, decoder.bundles[source].huffman):
            return False
    decoder.bundles[source].decoded = 0
    decoder.bundles[source].current = 0
    return True


def _begin_bundle_read(bits: BitReader, bundle: Bundle) -> tuple[bool, int]:
    if bundle.decoded is None or bundle.decoded > bundle.current:
        return False, 0
    count = bits.read(bundle.length_bits)
    if not count:
        bundle.decoded = None
    return True, count


def _check_bundle_space(bundle: Bundle, end: int) -> bool:
    return 0 <= end <= bundle.capacity


def _read_runs(bits: BitReader, bundle: Bundle) -> bool:
    ok, count = _begin_bundle_read(bits, bundle)
    if not ok or not count:
        return not bits.failed
    end = bundle.decoded + count
    if not _check_bundle_space(bundle, end):
        return False
    if bits.read(1):
        value = bits.read(4)
        for i in range(bundle.decoded, end):
            bundle.data[i] = value
        bundle.decoded = end
    else:
        while bundle.decoded < end:
            bundle.data[bundle.decoded] = _decode_huffman(bits, bundle.huffman)
            bundle.decoded += 1
    return not bits.failed


def _read_motion_values(bits: BitReader, bundle: Bundle) -> bool:
    ok, count = _begin_bundle_read(bits, bundle)
    if not ok or not count:
        return not bits.failed
    end = bundle.decoded + count
    if not _check_bundle_space(bundle, end):
        return False
    if bits.read(1):
        value = bits.read(4)
        if value and bits.read(1):
            value = -value
        b = value & 0xFF
        for i in range(bundle.decoded, end):
            bundle.data[i] = b
        bundle.decoded = end
    else:
        while bundle.decoded < end:
            value = _decode_huffman(bits, bundle.huffman)
            if value and bits.read(1):
                value = -value
            bundle.data[bundle.decoded] = value & 0xFF
            bundle.decoded += 1
    return not bits.failed


def _read_block_types(bits: BitReader, bundle: Bundle) -> bool:
    ok, count = _begin_bundle_read(bits, bundle)
    if not ok or not count:
        return not bits.failed
    end = bundle.decoded + count
    if not _check_bundle_space(bundle, end):
        return False
    if bits.read(1):
        value = bits.read(4)
        for i in range(bundle.decoded, end):
            bundle.data[i] = value
        bundle.decoded = end
    else:
        last = 0
        while bundle.decoded < end:
            value = _decode_huffman(bits, bundle.huffman)
            if value < 12:
                last = value
                bundle.data[bundle.decoded] = value
                bundle.decoded += 1
            else:
                run = RUN_LENGTHS[value - 12]
                if end - bundle.decoded < run:
                    return False
                for i in range(bundle.decoded, bundle.decoded + run):
                    bundle.data[i] = last
                bundle.decoded += run
    return not bits.failed


def _read_patterns(bits: BitReader, bundle: Bundle) -> bool:
    ok, count = _begin_bundle_read(bits, bundle)
    if not ok or not count:
        return not bits.failed
    end = bundle.decoded + count
    if not _check_bundle_space(bundle, end):
        return False
    while bundle.decoded < end:
        value = _decode_huffman(bits, bundle.huffman)
        value |= _decode_huffman(bits, bundle.huffman) << 4
        bundle.data[bundle.decoded] = value & 0xFF
        bundle.decoded += 1
    return not bits.failed


def _read_colors(bits: BitReader, decoder: "Decoder", bundle: Bundle) -> bool:
    ok, count = _begin_bundle_read(bits, bundle)
    if not ok or not count:
        return not bits.failed
    end = bundle.decoded + count
    if not _check_bundle_space(bundle, end):
        return False
    constant = bits.read(1) != 0
    while True:
        decoder.last_color = _decode_huffman(bits, decoder.color_high[decoder.last_color])
        value = _decode_huffman(bits, bundle.huffman)
        value |= decoder.last_color << 4
        if constant:
            for i in range(bundle.decoded, end):
                bundle.data[i] = value & 0xFF
            bundle.decoded = end
        else:
            bundle.data[bundle.decoded] = value & 0xFF
            bundle.decoded += 1
        if not (bundle.decoded < end):
            break
    return not bits.failed


def _read_dcs(bits: BitReader, bundle: Bundle, signed_values: bool) -> bool:
    ok, count = _begin_bundle_read(bits, bundle)
    if not ok or not count:
        return not bits.failed
    dest = bundle.decoded
    value = bits.read(11 - (1 if signed_values else 0))
    if value and signed_values and bits.read(1):
        value = -value
    if dest >= bundle.capacity:
        return False
    bundle.data[dest] = value
    dest += 1
    count -= 1

    offset = 0
    while offset < count:
        group = min(8, count - offset)
        size = bits.read(4)
        if dest + group > bundle.capacity:
            return False
        for _ in range(group):
            if size:
                delta = bits.read(size)
                if delta and bits.read(1):
                    delta = -delta
                value += delta
            if value < -32768 or value > 32767:
                return False
            bundle.data[dest] = value
            dest += 1
        offset += 8
    bundle.decoded = dest
    return not bits.failed


def _get_value(decoder: "Decoder", source: int) -> int:
    bundle = decoder.bundles[source]
    if bundle.current is None or bundle.current >= bundle.capacity:
        return 0
    if source < SOURCE_X_OFF or source == SOURCE_RUN:
        v = bundle.data[bundle.current]
        bundle.current += 1
        return v
    if source == SOURCE_X_OFF or source == SOURCE_Y_OFF:
        v = _to_int8(bundle.data[bundle.current])
        bundle.current += 1
        return v
    v = bundle.data[bundle.current]
    bundle.current += 1
    return v


def _read_dct_coefficients(bits: BitReader, block: list, coefficient_indices: list) -> tuple[int, int]:
    scan = bink_data.bink_scan
    coefficient_list = [0] * 128
    mode_list = [0] * 128
    list_start = 64
    list_end = 64
    coefficient_count = 0

    for value, mode in ((4, 0), (24, 0), (44, 0), (1, 3), (2, 3), (3, 3)):
        coefficient_list[list_end] = value
        mode_list[list_end] = mode
        list_end += 1

    magnitude_bits = bits.read(4) - 1
    while magnitude_bits >= 0:
        position = list_start
        while position < list_end:
            if not (mode_list[position] | coefficient_list[position]) or not bits.read(1):
                position += 1
                continue

            coefficient = coefficient_list[position]
            mode = mode_list[position]
            if mode == 0 or mode == 2:
                if mode == 0:
                    coefficient_list[position] = coefficient + 4
                    mode_list[position] = 1
                else:
                    coefficient_list[position] = 0
                    mode_list[position] = 0
                    position += 1

                for _group in range(4):
                    if bits.read(1):
                        list_start -= 1
                        coefficient_list[list_start] = coefficient
                        mode_list[list_start] = 3
                    else:
                        if magnitude_bits == 0:
                            value = -1 if bits.read(1) else 1
                        else:
                            value = bits.read(magnitude_bits) | (1 << magnitude_bits)
                            if bits.read(1):
                                value = -value
                        block[scan[coefficient]] = value
                        coefficient_indices[coefficient_count] = coefficient
                        coefficient_count += 1
                    coefficient += 1
            elif mode == 1:
                mode_list[position] = 2
                for _group in range(3):
                    coefficient += 4
                    coefficient_list[list_end] = coefficient
                    mode_list[list_end] = 2
                    list_end += 1
            else:
                if magnitude_bits == 0:
                    value = -1 if bits.read(1) else 1
                else:
                    value = bits.read(magnitude_bits) | (1 << magnitude_bits)
                    if bits.read(1):
                        value = -value
                block[scan[coefficient]] = value
                coefficient_indices[coefficient_count] = coefficient
                coefficient_count += 1
                coefficient_list[position] = 0
                mode_list[position] = 0
                position += 1
        magnitude_bits -= 1

    if bits.failed:
        return -1, coefficient_count
    return bits.read(4), coefficient_count


def _unquantize(block: list, quantizer: list, coefficient_count: int, coefficient_indices: list) -> None:
    scan = bink_data.bink_scan
    block[0] = (block[0] * quantizer[0]) >> 11
    for index in range(coefficient_count):
        scan_index = coefficient_indices[index]
        destination = scan[scan_index]
        block[destination] = (block[destination] * quantizer[scan_index]) >> 11


def _read_residue(bits: BitReader, block: list, masks_remaining: int) -> bool:
    scan = bink_data.bink_scan
    coefficient_list = [0] * 128
    mode_list = [0] * 128
    nonzero = [0] * 64
    list_start = 64
    list_end = 64
    nonzero_count = 0

    for value, mode in ((4, 0), (24, 0), (44, 0), (0, 2)):
        coefficient_list[list_end] = value
        mode_list[list_end] = mode
        list_end += 1

    mask = 1 << bits.read(3)
    while mask:
        for index in range(nonzero_count):
            if bits.read(1):
                position = nonzero[index]
                block[position] += -mask if block[position] < 0 else mask
                masks_remaining -= 1
                if masks_remaining < 0:
                    return not bits.failed

        position = list_start
        while position < list_end:
            if not (coefficient_list[position] | mode_list[position]) or not bits.read(1):
                position += 1
                continue
            coefficient = coefficient_list[position]
            mode = mode_list[position]
            if mode == 0 or mode == 2:
                if mode == 0:
                    coefficient_list[position] = coefficient + 4
                    mode_list[position] = 1
                else:
                    coefficient_list[position] = 0
                    mode_list[position] = 0
                    position += 1
                for _group in range(4):
                    if bits.read(1):
                        list_start -= 1
                        coefficient_list[list_start] = coefficient
                        mode_list[list_start] = 3
                    else:
                        destination = scan[coefficient]
                        nonzero[nonzero_count] = destination
                        nonzero_count += 1
                        block[destination] = -mask if bits.read(1) else mask
                        masks_remaining -= 1
                        if masks_remaining < 0:
                            return not bits.failed
                    coefficient += 1
            elif mode == 1:
                mode_list[position] = 2
                for _group in range(3):
                    coefficient += 4
                    coefficient_list[list_end] = coefficient
                    mode_list[list_end] = 2
                    list_end += 1
            else:
                destination = scan[coefficient]
                nonzero[nonzero_count] = destination
                nonzero_count += 1
                block[destination] = -mask if bits.read(1) else mask
                coefficient_list[position] = 0
                mode_list[position] = 0
                position += 1
                masks_remaining -= 1
                if masks_remaining < 0:
                    return not bits.failed
        mask >>= 1
    return not bits.failed


def _multiply_dct(left: int, right: int) -> int:
    product = (left * right) & 0xFFFFFFFF
    if product >= 0x80000000:
        product -= 0x100000000
    return product >> 11


def _transform_dct(source: list, row: bool) -> list:
    a1c, a2c, a3c, a4c = 2896, 2217, 3784, -5352
    a0 = source[0] + source[4]
    a1 = source[0] - source[4]
    a2 = source[2] + source[6]
    a3 = _multiply_dct(a1c, source[2] - source[6])
    a4 = source[5] + source[3]
    a5 = source[5] - source[3]
    a6 = source[1] + source[7]
    a7 = source[1] - source[7]
    b0 = a4 + a6
    b1 = _multiply_dct(a3c, a5 + a7)
    b2 = _multiply_dct(a4c, a5) - b0 + b1
    b3 = _multiply_dct(a1c, a6 - a4) - b2
    b4 = _multiply_dct(a2c, a7) + b3 - b1

    values = [
        a0 + a2 + b0,
        a1 + a3 - a2 + b2,
        a1 - a3 + a2 + b3,
        a0 - a2 - b4,
        a0 - a2 + b4,
        a1 - a3 + a2 - b3,
        a1 + a3 - a2 - b2,
        a0 + a2 - b0,
    ]
    if row:
        return [(v + 0x7F) >> 8 for v in values]
    return values


def _inverse_dct(block: list) -> None:
    temporary = [0] * 64
    for column in range(8):
        source = [block[row * 8 + column] for row in range(8)]
        if not (source[1] or source[2] or source[3] or source[4] or source[5] or source[6] or source[7]):
            for row in range(8):
                temporary[row * 8 + column] = source[0]
        else:
            dest = _transform_dct(source, False)
            for row in range(8):
                temporary[row * 8 + column] = dest[row]

    for row in range(8):
        dest = _transform_dct(temporary[row * 8:row * 8 + 8], True)
        for column in range(8):
            block[row * 8 + column] = dest[column]


def _idct_put(pixels: bytearray, dest_offset: int, stride: int, block: list) -> None:
    _inverse_dct(block)
    for row in range(8):
        base = dest_offset + row * stride
        for column in range(8):
            pixels[base + column] = block[row * 8 + column] & 0xFF


def _idct_add(pixels: bytearray, dest_offset: int, stride: int, block: list) -> None:
    _inverse_dct(block)
    for row in range(8):
        base = dest_offset + row * stride
        for column in range(8):
            pixels[base + column] = (pixels[base + column] + block[row * 8 + column]) & 0xFF


def _copy_block(dest: bytearray, dest_offset: int, dest_stride: int,
                 src: bytearray, src_offset: int, src_stride: int) -> None:
    for row in range(8):
        d = dest_offset + row * dest_stride
        s = src_offset + row * src_stride
        dest[d:d + 8] = src[s:s + 8]


def _scale_block(source: list, dest: bytearray, dest_offset: int, stride: int) -> None:
    for row in range(8):
        for column in range(8):
            value = source[row * 8 + column]
            base = dest_offset + (row * 2) * stride + column * 2
            dest[base] = value
            dest[base + 1] = value
            dest[base + stride] = value
            dest[base + stride + 1] = value


def _motion_block(decoder: "Decoder", plane: Plane, previous: Plane,
                   block_x: int, block_y: int, dest_offset: int) -> bool:
    source_x = block_x * 8 + _get_value(decoder, SOURCE_X_OFF)
    source_y = block_y * 8 + _get_value(decoder, SOURCE_Y_OFF)
    if source_x < 0 or source_y < 0 or source_x + 8 > previous.width or source_y + 8 > previous.height:
        return False
    _copy_block(plane.pixels, dest_offset, plane.stride,
                previous.pixels, source_y * previous.stride + source_x, previous.stride)
    return True


class Decoder:
    __slots__ = ("bundles", "color_high", "last_color")

    def __init__(self):
        self.bundles = [Bundle() for _ in range(SOURCE_COUNT)]
        self.color_high = [Tree() for _ in range(16)]
        self.last_color = 0


def _decode_plane(decoder: Decoder, bits: BitReader, plane: Plane, previous: Plane,
                   width: int, height: int, chroma: bool) -> bool:
    block_width = (width + 15) >> 4 if chroma else (width + 7) >> 3
    block_height = (height + 15) >> 4 if chroma else (height + 7) >> 3
    logical_width = (width >> 1) if chroma else width
    length_width = max(logical_width, 8)
    aligned_width = (length_width + 7) & ~7

    decoder.bundles[SOURCE_BLOCK_TYPES].length_bits = _int_log2((aligned_width >> 3) + 511) + 1
    decoder.bundles[SOURCE_SUB_BLOCK_TYPES].length_bits = _int_log2((aligned_width >> 4) + 511) + 1
    decoder.bundles[SOURCE_COLORS].length_bits = _int_log2(block_width * 64 + 511) + 1
    dc_bits = _int_log2((aligned_width >> 3) + 511) + 1
    decoder.bundles[SOURCE_INTRA_DC].length_bits = dc_bits
    decoder.bundles[SOURCE_INTER_DC].length_bits = dc_bits
    decoder.bundles[SOURCE_X_OFF].length_bits = dc_bits
    decoder.bundles[SOURCE_Y_OFF].length_bits = dc_bits
    decoder.bundles[SOURCE_PATTERN].length_bits = _int_log2((block_width << 3) + 511) + 1
    decoder.bundles[SOURCE_RUN].length_bits = _int_log2(block_width * 48 + 511) + 1

    for source in range(SOURCE_COUNT):
        if not _read_bundle_header(bits, decoder, source):
            return False

    coordinates = [(index & 7) + (index >> 3) * plane.stride for index in range(64)]

    for block_y in range(block_height):
        if not (_read_block_types(bits, decoder.bundles[SOURCE_BLOCK_TYPES]) and
                _read_block_types(bits, decoder.bundles[SOURCE_SUB_BLOCK_TYPES]) and
                _read_colors(bits, decoder, decoder.bundles[SOURCE_COLORS]) and
                _read_patterns(bits, decoder.bundles[SOURCE_PATTERN]) and
                _read_motion_values(bits, decoder.bundles[SOURCE_X_OFF]) and
                _read_motion_values(bits, decoder.bundles[SOURCE_Y_OFF]) and
                _read_dcs(bits, decoder.bundles[SOURCE_INTRA_DC], False) and
                _read_dcs(bits, decoder.bundles[SOURCE_INTER_DC], True) and
                _read_runs(bits, decoder.bundles[SOURCE_RUN])):
            return False

        block_x = 0
        while block_x < block_width:
            dest_offset = block_y * 8 * plane.stride + block_x * 8
            prev_offset = block_y * 8 * previous.stride + block_x * 8
            block_type = _get_value(decoder, SOURCE_BLOCK_TYPES)

            if ((block_y & 1) or (block_x & 1)) and block_type == BLOCK_SCALED:
                block_x += 1
                continue

            if block_type == BLOCK_SKIP:
                _copy_block(plane.pixels, dest_offset, plane.stride,
                            previous.pixels, prev_offset, previous.stride)
            elif block_type == BLOCK_SCALED:
                unscaled = [0] * 64
                subtype = _get_value(decoder, SOURCE_SUB_BLOCK_TYPES)
                if subtype == BLOCK_RUN:
                    scan = bink_data.bink_patterns[bits.read(4)]
                    scan_i = 0
                    written = 0
                    while True:
                        run = _get_value(decoder, SOURCE_RUN) + 1
                        written += run
                        if written > 64:
                            return False
                        if bits.read(1):
                            value = _get_value(decoder, SOURCE_COLORS)
                            for _ in range(run):
                                unscaled[scan[scan_i]] = value
                                scan_i += 1
                        else:
                            for _ in range(run):
                                unscaled[scan[scan_i]] = _get_value(decoder, SOURCE_COLORS)
                                scan_i += 1
                        if not (written < 63):
                            break
                    if written == 63:
                        unscaled[scan[scan_i]] = _get_value(decoder, SOURCE_COLORS)
                elif subtype == BLOCK_INTRA:
                    coefficients = [0] * 64
                    indices = [0] * 64
                    coefficients[0] = _get_value(decoder, SOURCE_INTRA_DC)
                    quantizer, count = _read_dct_coefficients(bits, coefficients, indices)
                    if quantizer < 0 or quantizer > 15:
                        return False
                    _unquantize(coefficients, bink_data.bink_intra_quant[quantizer], count, indices)
                    _idct_put(unscaled, 0, 8, coefficients)
                elif subtype == BLOCK_FILL:
                    value = _get_value(decoder, SOURCE_COLORS)
                    for row in range(16):
                        base = dest_offset + row * plane.stride
                        for col in range(16):
                            plane.pixels[base + col] = value
                elif subtype == BLOCK_PATTERN:
                    colors = [_get_value(decoder, SOURCE_COLORS), _get_value(decoder, SOURCE_COLORS)]
                    for row in range(8):
                        pattern = _get_value(decoder, SOURCE_PATTERN)
                        for column in range(8):
                            unscaled[row * 8 + column] = colors[pattern & 1]
                            pattern >>= 1
                elif subtype == BLOCK_RAW:
                    for index in range(64):
                        unscaled[index] = _get_value(decoder, SOURCE_COLORS)
                else:
                    return False
                if subtype != BLOCK_FILL:
                    _scale_block(unscaled, plane.pixels, dest_offset, plane.stride)
                block_x += 1
            elif block_type == BLOCK_MOTION:
                if not _motion_block(decoder, plane, previous, block_x, block_y, dest_offset):
                    return False
            elif block_type == BLOCK_RUN:
                scan = bink_data.bink_patterns[bits.read(4)]
                scan_i = 0
                written = 0
                while True:
                    run = _get_value(decoder, SOURCE_RUN) + 1
                    written += run
                    if written > 64:
                        return False
                    if bits.read(1):
                        value = _get_value(decoder, SOURCE_COLORS)
                        for _ in range(run):
                            plane.pixels[dest_offset + coordinates[scan[scan_i]]] = value
                            scan_i += 1
                    else:
                        for _ in range(run):
                            plane.pixels[dest_offset + coordinates[scan[scan_i]]] = _get_value(decoder, SOURCE_COLORS)
                            scan_i += 1
                    if not (written < 63):
                        break
                if written == 63:
                    plane.pixels[dest_offset + coordinates[scan[scan_i]]] = _get_value(decoder, SOURCE_COLORS)
            elif block_type == BLOCK_RESIDUE:
                if not _motion_block(decoder, plane, previous, block_x, block_y, dest_offset):
                    return False
                residue = [0] * 64
                if not _read_residue(bits, residue, bits.read(7)):
                    return False
                for row in range(8):
                    base = dest_offset + row * plane.stride
                    for column in range(8):
                        plane.pixels[base + column] = (plane.pixels[base + column] + residue[row * 8 + column]) & 0xFF
            elif block_type == BLOCK_INTRA:
                coefficients = [0] * 64
                indices = [0] * 64
                coefficients[0] = _get_value(decoder, SOURCE_INTRA_DC)
                quantizer, count = _read_dct_coefficients(bits, coefficients, indices)
                if quantizer < 0 or quantizer > 15:
                    return False
                _unquantize(coefficients, bink_data.bink_intra_quant[quantizer], count, indices)
                _idct_put(plane.pixels, dest_offset, plane.stride, coefficients)
            elif block_type == BLOCK_FILL:
                value = _get_value(decoder, SOURCE_COLORS)
                for row in range(8):
                    base = dest_offset + row * plane.stride
                    for col in range(8):
                        plane.pixels[base + col] = value
            elif block_type == BLOCK_INTER:
                if not _motion_block(decoder, plane, previous, block_x, block_y, dest_offset):
                    return False
                coefficients = [0] * 64
                indices = [0] * 64
                coefficients[0] = _get_value(decoder, SOURCE_INTER_DC)
                quantizer, count = _read_dct_coefficients(bits, coefficients, indices)
                if quantizer < 0 or quantizer > 15:
                    return False
                _unquantize(coefficients, bink_data.bink_inter_quant[quantizer], count, indices)
                _idct_add(plane.pixels, dest_offset, plane.stride, coefficients)
            elif block_type == BLOCK_PATTERN:
                colors = [_get_value(decoder, SOURCE_COLORS), _get_value(decoder, SOURCE_COLORS)]
                for row in range(8):
                    pattern = _get_value(decoder, SOURCE_PATTERN)
                    base = dest_offset + row * plane.stride
                    for column in range(8):
                        plane.pixels[base + column] = colors[pattern & 1]
                        pattern >>= 1
            elif block_type == BLOCK_RAW:
                colors_bundle = decoder.bundles[SOURCE_COLORS]
                if colors_bundle.current is None or colors_bundle.current + 64 > colors_bundle.capacity:
                    return False
                for row in range(8):
                    base = dest_offset + row * plane.stride
                    src = colors_bundle.current + row * 8
                    plane.pixels[base:base + 8] = colors_bundle.data[src:src + 8]
                colors_bundle.current += 64
            else:
                return False
            block_x += 1

    bits.align32()
    return not bits.failed


def _clamp_byte(value: int) -> int:
    if value < 0:
        return 0
    if value > 255:
        return 255
    return value


def _convert_to_rgb(luma: Plane, chroma_u: Plane, chroma_v: Plane, width: int, height: int) -> bytes:
    rgb = bytearray(width * height * 3)
    for y in range(height):
        luma_row = y * luma.stride
        chroma_row = (y >> 1) * chroma_u.stride
        dest_row = y * width * 3
        for x in range(width):
            value_y = luma.pixels[luma_row + x]
            value_u = chroma_u.pixels[chroma_row + (x >> 1)]
            value_v = chroma_v.pixels[chroma_row + (x >> 1)]
            c = value_y - 16
            if c < 0:
                c = 0
            d = value_u - 128
            e = value_v - 128
            dest = dest_row + x * 3
            rgb[dest] = _clamp_byte((298 * c + 409 * e + 128) >> 8)
            rgb[dest + 1] = _clamp_byte((298 * c - 100 * d - 208 * e + 128) >> 8)
            rgb[dest + 2] = _clamp_byte((298 * c + 516 * d + 128) >> 8)
    return bytes(rgb)


def decode_bink1_frame(width: int, height: int, packet: bytes) -> bytes:
    """Decode one standalone Bink1 intra frame (as embedded in a .gr2 Texture, not a
    full .bik file) into top-down packed RGB24 bytes of exactly width*height*3 length.
    Raises ValueError on any decode failure."""
    luma_width = ((width + 7) >> 3) * 8
    luma_height = ((height + 7) >> 3) * 8
    chroma_width = ((width + 15) >> 4) * 8
    chroma_height = ((height + 15) >> 4) * 8

    planes = [
        Plane(luma_width, luma_height, luma_width),
        Plane(chroma_width, chroma_height, chroma_width),
        Plane(chroma_width, chroma_height, chroma_width),
    ]
    # No previous frame for a lone embedded texture -- an all-zero plane matches how
    # the reference decoder behaves for a file's first frame (HasPrevious=false).
    previous_planes = [
        Plane(luma_width, luma_height, luma_width),
        Plane(chroma_width, chroma_height, chroma_width),
        Plane(chroma_width, chroma_height, chroma_width),
    ]

    block_count = ((width + 7) >> 3) * ((height + 7) >> 3)
    bundle_size = block_count * 64

    decoder = Decoder()
    for source in range(SOURCE_COUNT):
        bundle = decoder.bundles[source]
        if source in (SOURCE_INTRA_DC, SOURCE_INTER_DC):
            bundle.data = [0] * (bundle_size // 2)  # int16-element-indexed, matching
            # the reference's byte buffer reinterpreted as int16_t*.
        else:
            bundle.data = bytearray(bundle_size)
        bundle.capacity = len(bundle.data)
        bundle.decoded = None
        bundle.current = None

    bits = BitReader(packet)
    # No leading skip here (unlike the container-format reference decoder, which
    # skips a 32-bit per-frame flags word before the video bitstream) -- empirically
    # confirmed: Granny's embedded Pixels blob starts directly with the plane
    # bitstream, no extra header. See docs/bink-texture-format.md.

    # Plane order Y(0), V(2), U(1) -- matches the reference decoder exactly (not the
    # more intuitive Y,U,V order); ConvertToRGB expects Planes[1]=U, Planes[2]=V.
    if not _decode_plane(decoder, bits, planes[0], previous_planes[0], width, height, False):
        raise ValueError("Bink1 frame decode failed on luma (Y) plane")
    if not _decode_plane(decoder, bits, planes[2], previous_planes[2], width, height, True):
        raise ValueError("Bink1 frame decode failed on chroma (V) plane")
    if not _decode_plane(decoder, bits, planes[1], previous_planes[1], width, height, True):
        raise ValueError("Bink1 frame decode failed on chroma (U) plane")

    return _convert_to_rgb(planes[0], planes[1], planes[2], width, height)
