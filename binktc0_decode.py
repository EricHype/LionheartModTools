"""Decode Granny's "BinkTC0" texture compression format -- the REAL format used by
`Texture.Encoding=3` (GrannyBinkTextureEncoding) in Lionheart's `.gr2` files.

This is NOT the Bink video codec (DCT + Huffman) -- that was a wrong turn (see
bink1_decode.py, kept only as a documented dead end). The real algorithm, found by
reading Granny's own SDK source (github.com/Final-Game-Production-Inc/Granny-3D-SDK,
`source/granny_bink0_compression.cpp`, RAD Game Tools, Granny 2.9.12.0), is a
**wavelet image compressor**: a 4-level 9/7 DWT (falling back to Haar for very small
subbands) with an adaptive arithmetic coder for entropy coding, plus a non-standard
reversible YUV-like color transform (not BT.601). Ported here by hand, translating the
C source's pointer-arithmetic/SIMD-unrolled style into plain Python arrays operating on
clean per-subband buffers rather than replicating the original's in-place sparse-stride
buffer layout (a pure performance trick with no semantic effect once unrolled by hand).

See docs/bink-texture-format.md for the full writeup of how this was found and verified.

Usage:
    from binktc0_decode import decode_binktc0
    rgb_bytes = decode_binktc0(width, height, packet_bytes, has_alpha=False)
"""
from __future__ import annotations

import struct

# ---------------------------------------------------------------------------
# Constants (from granny_compression_tools.cpp)
# ---------------------------------------------------------------------------

BITS_INVERT = (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15)
BITS_INVERT_8 = (0, 4, 2, 6, 1, 5, 3, 7)

_GBITLEVELS = (
    0, 1, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4,
    5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
    6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
    6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    8,
)

BINS = 16
MIN_ZERO_LENGTH = 3
LIT_LENGTH_BITS = 6
ZERO_LENGTH_BITS = 8
LIT_LENGTH_LIMIT = (1 << LIT_LENGTH_BITS) - 1
ZERO_LENGTH_LIMIT = (1 << ZERO_LENGTH_BITS) - 1
EXTRA_LENGTHS = 4
EXTRA_LIT_LENGTHS = (128, 256, 512, 1024)
EXTRA_ZERO_LENGTHS = (512, 1024, 2048, 3072)

SMALLEST_DWT_ROW = 12
SMALLEST_DWT_COL = 10


def get_bit_level(n: int) -> int:
    if n <= 128:
        return _GBITLEVELS[n]
    if n >= 2048:
        if n >= 8192:
            return 15 if n >= 16384 else 14
        return 13 if n >= 4096 else 12
    if n >= 512:
        return 11 if n >= 1024 else 10
    return 9 if n >= 256 else 8


def s16(x: int) -> int:
    x &= 0xFFFF
    return x - 0x10000 if x >= 0x8000 else x


# ---------------------------------------------------------------------------
# VarBits: flat LSB-first bit reader (equivalent to the C macros' "pull LE32
# words, extract low bits first" behavior -- verified equivalent bit-for-bit).
# ---------------------------------------------------------------------------

class VarBits:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, byte_offset: int = 0):
        self.data = data
        self.pos = byte_offset * 8

    def get(self, length: int) -> int:
        value = 0
        data = self.data
        pos = self.pos
        for i in range(length):
            value |= ((data[pos >> 3] >> (pos & 7)) & 1) << i
            pos += 1
        self.pos = pos
        return value

    def get1(self) -> int:
        data = self.data
        pos = self.pos
        bit = (data[pos >> 3] >> (pos & 7)) & 1
        self.pos = pos + 1
        return bit

    def get_signed(self, length: int) -> int:
        v = self.get(length)
        return v - (1 << length) if v & (1 << (length - 1)) else v


# ---------------------------------------------------------------------------
# ArithBits: the raw range-coder state (high/low/codeflow), separate from the
# adaptive probability model below. Matches arithbit.c's remove_symbol/
# ArithBitsGetValue exactly.
# ---------------------------------------------------------------------------

class ArithBits:
    __slots__ = ("vb", "high", "low", "codeflow")

    def __init__(self, data: bytes, byte_offset: int):
        self.vb = VarBits(data, byte_offset)
        self.high = 0x7FFFFFFF
        self.low = 0
        tmp = self.vb.get(31)
        self.codeflow = (
            (BITS_INVERT[tmp & 15] << 27)
            | (BITS_INVERT[(tmp >> 4) & 15] << 23)
            | (BITS_INVERT[(tmp >> 8) & 15] << 19)
            | (BITS_INVERT[(tmp >> 12) & 15] << 15)
            | (BITS_INVERT[(tmp >> 16) & 15] << 11)
            | (BITS_INVERT[(tmp >> 20) & 15] << 7)
            | (BITS_INVERT[(tmp >> 24) & 15] << 3)
            | BITS_INVERT_8[(tmp >> 28) & 7]
        )

    def _remove_symbol(self, start: int, range_: int, scale: int) -> None:
        high = self.high
        low = self.low
        code = self.codeflow

        tmp = (high - low) + 1
        high = low + (tmp * (start + range_)) // scale - 1
        low = low + (tmp * start) // scale

        if ((high ^ low) & 0x40000000) == 0:
            while ((high ^ low) & 0x7F800000) == 0:
                low = (low << 8) & 0xFFFFFFFF
                high = ((high << 8) | 0xFF) & 0xFFFFFFFF
                t = self.vb.get(8)
                code = ((code << 8) | (BITS_INVERT[t & 15] << 4) | BITS_INVERT[t >> 4]) & 0xFFFFFFFF

            if ((high ^ low) & 0x78000000) == 0:
                low = (low << 4) & 0xFFFFFFFF
                high = ((high << 4) | 0xF) & 0xFFFFFFFF
                t = self.vb.get(4)
                code = ((code << 4) | BITS_INVERT[t]) & 0xFFFFFFFF

            while ((high ^ low) & 0x40000000) == 0:
                low = (low << 1) & 0xFFFFFFFF
                high = ((high << 1) | 1) & 0xFFFFFFFF
                code = ((code << 1) | self.vb.get1()) & 0xFFFFFFFF

        while (low & 0x20000000) and not (high & 0x20000000):
            code ^= 0x20000000
            low = (low & 0x1FFFFFFF) << 1
            high = ((high << 1) | 0x40000001) & 0xFFFFFFFF
            code = ((code << 1) | self.vb.get1()) & 0xFFFFFFFF

        self.high = high & 0x7FFFFFFF
        self.low = low & 0x7FFFFFFF
        self.codeflow = code & 0x7FFFFFFF

    def get(self, scale: int) -> int:
        # ArithBitsGet macro: multm164anddiv((codeflow-low)+1, scale, (high-low)+1).
        # codeflow/low/high are U32 in the source, and (codeflow-low) is computed
        # with unsigned wraparound -- codeflow can legitimately sit numerically
        # "below" low (mod 2^32) at this point due to the underflow XOR trick in
        # _remove_symbol, giving a huge-but-valid mt1 in C. Python's plain `-`
        # instead produces a small negative int, which silently gives a wrong
        # (but plausible, still in-range) count once in a long while -- this was
        # the root cause of a decode that stayed correct for thousands of symbols
        # before diverging with no visible desync at the point of the bad read.
        mt1 = ((self.codeflow - self.low) + 1) & 0xFFFFFFFF
        d = (self.high - self.low) + 1
        return (mt1 * scale - 1) // d

    def get_value(self, scale: int) -> int:
        count = self.get(scale)
        self._remove_symbol(count, 1, scale)
        return count


# ---------------------------------------------------------------------------
# Arith: the adaptive 16-bin probability model (radarith.c). One instance per
# context (decode_low uses one; decode_high_1 uses `numl` of them, one per
# quantized-neighbor-magnitude bucket, plus separate lit/zero run-length ones).
# ---------------------------------------------------------------------------

class Arith:
    """Adaptive 16-bin probability model (radarith.c). `totals` is a prefix-sum
    array over 16 bins (totals[i] = cumulative count through bin i); bumping one
    symbol's count therefore requires a SUFFIX update totals[bin..15] += delta --
    the exact same prefix-sum-array pattern as this project's original Granny
    decompressor (Window._add). The C source implements that suffix update via a
    packed-U32-pair fallthrough-switch trick for speed; verified by hand-tracing
    that its net effect (independent of the value's bin parity) is exactly
    totals[bin..15] += delta, so this port just does that directly."""

    __slots__ = (
        "unique_symb", "number", "max_unique", "bin_size", "bin_shift",
        "last_bin_start", "totals", "counts", "values",
    )

    def __init__(self, max_value: int, unique_values: int):
        self.unique_symb = unique_values
        self.number = 0
        self.max_unique = 0
        self.totals = [0] * BINS
        cap = unique_values + 2
        self.counts = [0] * cap
        self.values = [0] * cap
        self._calc_best_shift(unique_values + 1)
        # Arith_open: "add the single code for escape" -- quick_increment_counts(a, 0, 0x30003).
        self._quick_increment_counts(0, 0x30003)

    def _bump_totals_only(self, value: int, delta: int) -> None:
        """The inline do_0_bin/do_2_bin/.../noinc suffix bump inside
        find_pos_from_count_and_add: totals[] only, no counts[] touch
        (counts[pos] is bumped separately, by the caller, after
        ArithBitsRemove -- see decompress_raw). Same packed-pair-with-carry
        semantics as _quick_increment_counts, minus the counts[] write.
        `delta` is a packed amount just like quick_increment_counts's, always
        0x10001 here (the C source's literal in do_0_bin etc) -- NOT a plain
        1, since the low/high 16 bits independently drive the two totals[]
        slots in a pair."""
        delta &= 0xFFFFFFFF
        if value >= self.last_bin_start:
            self.totals[BINS - 1] = (self.totals[BINS - 1] + delta) & 0xFFFF
        else:
            b = value >> self.bin_shift
            if b & 1:
                self.totals[b] = (self.totals[b] + delta) & 0xFFFF
                b += 1
            j = b
            while j < BINS:
                packed = self.totals[j] | (self.totals[j + 1] << 16)
                packed = (packed + delta) & 0xFFFFFFFF
                self.totals[j] = packed & 0xFFFF
                self.totals[j + 1] = (packed >> 16) & 0xFFFF
                j += 2

    def _quick_increment_counts(self, value: int, amount: int) -> None:
        """Direct port of quick_increment_counts(a, value, amount). The C source
        packs totals[] into adjacent U16 pairs and does a REAL 32-bit add with
        carry between the two halves (`((U32*)a->totals)[i] += amount`) -- NOT
        two independent 16-bit lanes. For the common cases (amount's low/high
        16 bits equal, e.g. 0x10001/0x20002/0x30003) that carry never fires
        (totals stay far below 65536) so it behaves like a uniform delta. But
        decrement_counts's packed value is asymmetric by exactly one (e.g.
        0xFFF0FFF1 to remove a count of 15), and there the carry from the low
        half's underflow reliably propagates into the high half, making the
        REAL effect a uniform delta too (both halves change by the low 16
        bits' signed value) -- simulating the packed 32-bit add exactly is
        the only way to replicate this for every totals[] value, not just the
        pair being written to directly."""
        amount &= 0xFFFFFFFF
        if value >= self.last_bin_start:
            self.totals[BINS - 1] = (self.totals[BINS - 1] + amount) & 0xFFFF
        else:
            b = value >> self.bin_shift
            if b & 1:
                self.totals[b] = (self.totals[b] + amount) & 0xFFFF
                b += 1
            j = b
            while j < BINS:
                packed = self.totals[j] | (self.totals[j + 1] << 16)
                packed = (packed + amount) & 0xFFFFFFFF
                self.totals[j] = packed & 0xFFFF
                self.totals[j + 1] = (packed >> 16) & 0xFFFF
                j += 2
        self.counts[value] = (self.counts[value] + amount) & 0xFFFF

    def _calc_best_shift(self, value: int) -> None:
        if value < 6:
            self.bin_size = 0
            self.bin_shift = 15
            self.last_bin_start = 0
            return
        best_max = 0xFFFFFFFF
        best_bin = 0
        for i in range(16):
            size = 1 << i
            bins = (value + size - 1) // size
            if bins > BINS:
                bins = BINS
            m = value - (size * (bins - 1))
            if m < size:
                m = size
            if m < best_max:
                best_bin = i
                best_max = m
            if size > value:
                break
        self.bin_size = 1 << best_bin
        self.bin_shift = best_bin
        self.last_bin_start = (BINS - 1) * self.bin_size

    def _rescale(self) -> None:
        tots = [0] * BINS
        max_val = 0
        pos = 0

        self._calc_best_shift(self.number + 1)

        self.counts[0] >>= 1
        tots[0 if 0 < self.last_bin_start else BINS - 1] = self.counts[0]

        i = 1
        while i <= self.number:
            while self.counts[i] <= 1:
                if i < self.number:
                    self.counts[i] = self.counts[self.number]
                    self.counts[self.number] = 0
                    self.values[i] = self.values[self.number]
                    self.number -= 1
                else:
                    self.counts[i] = 0
                    self.number -= 1
                    i = self.number  # loop condition (i <= number) will now be false
                    break
            if i > self.number:
                break
            self.counts[i] >>= 1
            if self.counts[i] > max_val:
                max_val = self.counts[i]
                pos = i
            tots[(i >> self.bin_shift) if i < self.last_bin_start else BINS - 1] += self.counts[i]
            i += 1

        if max_val:
            j = ((self.number >> self.bin_shift) << self.bin_shift) if self.number < self.last_bin_start else self.last_bin_start
            if j == 0:
                j = 1
            if pos != j:
                old_j_count = self.counts[j]
                self.counts[j] = self.counts[pos]
                tots[j >> self.bin_shift if j < self.last_bin_start else BINS - 1] += self.counts[j] - old_j_count
                tots[pos >> self.bin_shift if pos < self.last_bin_start else BINS - 1] += old_j_count - self.counts[j]
                self.counts[pos] = old_j_count
                self.values[j], self.values[pos] = self.values[pos], self.values[j]

        finish_escape = (self.number != self.unique_symb) and (self.counts[0] == 0)
        if finish_escape:
            self.counts[0] += 2
            tots[0 if 0 < self.last_bin_start else BINS - 1] += 2

        self.totals[0] = tots[0]
        for i in range(1, BINS):
            self.totals[i] = self.totals[i - 1] + tots[i]

    def decompress(self, ab: ArithBits) -> int:
        """Returns the decoded VALUE directly (escape resolution handled internally
        via the passed-in resolver callback pattern isn't needed here -- callers
        that need the raw value/pos distinction use decompress_raw)."""
        pos, was_escape = self.decompress_raw(ab)
        if was_escape:
            raise RuntimeError("decompress() called on a possibly-escaped symbol; use decompress_raw")
        return pos

    def decompress_raw(self, ab: ArithBits) -> tuple[int, bool]:
        """Returns (value_or_number, was_escape). If was_escape, the caller must
        determine the real value out-of-band and call resolve_escape(value)."""
        if self.totals[BINS - 1] >= 16384:
            self._rescale()

        count = ab.get(self.totals[BINS - 1])
        pos, start = self._find_pos_from_count(count)
        # C's find_pos_from_count_and_add bumps totals[bin..15] by 1 as *part of*
        # the search, BEFORE ArithBitsRemove is called (which then reads the
        # already-updated totals[BINS-1]) -- order matters, this isn't just a
        # bookkeeping convenience. counts[pos] itself is only touched afterward,
        # via a separate plain ++.
        self._bump_totals_only(pos, 0x10001)
        ab._remove_symbol(start, self.counts[pos], self.totals[BINS - 1] - 1)
        self.counts[pos] = (self.counts[pos] + 1) & 0xFFFF

        if pos == 0:
            self.number += 1
            self._quick_increment_counts(self.number, 0x20002)
            if self.number == self.unique_symb:
                # decrement_counts(a, 0, a->counts[0]) -> quick_increment_counts(a,
                # 0, ((tmp-1)<<16)|(U16)tmp) where tmp=(U32)(-v).
                v = self.counts[0]
                tmp = (-v) & 0xFFFFFFFF
                amount = (((tmp - 1) & 0xFFFF) << 16) | (tmp & 0xFFFF)
                self._quick_increment_counts(0, amount)
            return (self.number, True)

        return (self.values[pos], False)

    def resolve_escape(self, value: int) -> None:
        self.values[self.number] = value

    def _find_pos_from_count(self, count: int) -> tuple[int, int]:
        # Linear bin search over the (already up-to-date) totals[] prefix-sum
        # array -- equivalent to the C version's hand-unrolled binary search,
        # which exists purely for speed. Bin totals-suffix bump for the found
        # bin happens via the caller's _bump_totals_only(pos, 0x10001) afterward, NOT
        # here (unlike the C version, which interleaves them) -- net effect is
        # identical since this search doesn't itself depend on totals changing.
        totals = self.totals
        b = 0
        while b < BINS - 1 and count >= totals[b]:
            b += 1
        pos = (b << self.bin_shift) if b < BINS - 1 else self.last_bin_start
        s = totals[b - 1] if b > 0 else 0

        while True:
            tmp = s + self.counts[pos]
            if count < tmp:
                return pos, s
            s = tmp
            pos += 1


def trunc_div(a: int, b: int) -> int:
    """C-style truncating-toward-zero integer division (vs. Python's // which
    floors). Used everywhere the source relies on C's division semantics for
    values that can be negative."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _round_shift16(e: int) -> int:
    """The DWT lifting rounding pattern: (e + (32767 ^ (e>>31))) / 65536 in C,
    i.e. round-to-nearest via truncating division with a sign-dependent bias."""
    adj = 32767 if e >= 0 else -32768
    return trunc_div(e + adj, 65536)


def _round_shift1(e: int) -> int:
    """Same pattern for the Haar transform's /2 (bias 1 or -2... actually the C
    source uses `(e + (1 ^ (e>>31))) / 2`: bias is 1 for e>=0, -2 for e<0)."""
    adj = 1 if e >= 0 else -2
    return trunc_div(e + adj, 2)


def _fill_rect(out: list, stride: int, offset: int, width: int, height: int, value: int) -> None:
    v = s16(value)
    for row in range(height):
        base = offset + row * stride
        for col in range(width):
            out[base + col] = v


# ---------------------------------------------------------------------------
# decode_low: the coarsest (DC-like) subband. One adaptive context, simple
# left/left+top-average prediction, no run-length/escape scheme.
# ---------------------------------------------------------------------------

def decode_low(ab: ArithBits, vb: VarBits, out: list, stride: int, offset: int,
                enc_width: int, enc_height: int) -> None:
    if vb.get1():
        prev = vb.get_signed(16)
        _fill_rect(out, stride, offset, enc_width, enc_height, prev)
        return

    max_val = vb.get(16)
    num = max_val + 1
    a = Arith(max_val, num)

    prev = vb.get(16)  # U32-typed in source: no sign extension
    out[offset] = s16(prev)
    pos = offset + 1

    # NOTE: `prev` is the C source's running S32 predictor and must NOT be
    # truncated to 16 bits across iterations (only *storage* into `out[]` is
    # S16) -- an earlier version of this port truncated `prev` itself every
    # iteration, which is only observably wrong once accumulated values drift
    # outside +-32768, i.e. it looked fine on small subbands and only broke
    # larger ones. Confirmed via `out[from_pos]` reads staying correctly S16
    # (since that's what was actually stored) while `prev` carries forward
    # full-width, exactly matching decode_high_1's already-correct handling
    # of its own `prev` (see the `prev = cur` assignment there, untruncated).
    for _ in range(enc_width - 1):
        cur, was_escape = a.decompress_raw(ab)
        if was_escape:
            escaped = ab.get_value(num)
            a.resolve_escape(escaped)
            cur = escaped
        if cur and vb.get1():
            cur = -cur
        prev = cur + prev
        out[pos] = s16(prev)
        pos += 1

    row_start = offset
    for _h in range(enc_height - 1):
        row_start += stride
        pos = row_start
        from_pos = row_start - stride

        cur, was_escape = a.decompress_raw(ab)
        if was_escape:
            escaped = ab.get_value(num)
            a.resolve_escape(escaped)
            cur = escaped
        if cur and vb.get1():
            cur = -cur
        prev = cur + out[from_pos]
        out[pos] = s16(prev)
        pos += 1
        from_pos += 1

        for _ in range(enc_width - 1):
            cur, was_escape = a.decompress_raw(ab)
            if was_escape:
                escaped = ab.get_value(num)
                a.resolve_escape(escaped)
                cur = escaped
            if cur and vb.get1():
                cur = -cur
            prev = cur + trunc_div(prev + out[from_pos], 2)
            out[pos] = s16(prev)
            pos += 1
            from_pos += 1


# ---------------------------------------------------------------------------
# decode_high_1: order-1-predicted detail subbands. Per-pixel adaptive context
# chosen from quantized neighbor magnitude, plus a literal/zero-run-length
# entropy scheme layered on top (most detail coefficients are zero).
# ---------------------------------------------------------------------------

def _decode_symbol(ab: ArithBits, arith: Arith, num: int) -> int:
    cur, was_escape = arith.decompress_raw(ab)
    if was_escape:
        escaped = ab.get_value(num)
        arith.resolve_escape(escaped)
        return escaped
    return cur


def decode_high_1(ab: ArithBits, vb: VarBits, out: list, stride: int, offset: int,
                   enc_width: int, enc_height: int) -> None:
    qlevel = vb.get(16)

    if vb.get1():
        prev = vb.get_signed(16)
        _fill_rect(out, stride, offset, enc_width, enc_height, prev * qlevel)
        return

    max_val = vb.get(16)  # U32-typed: unsigned
    num = max_val + 1
    numl = get_bit_level(max_val * qlevel) + 1

    contexts = [Arith(max_val, num) for _ in range(numl)]
    lits = Arith(LIT_LENGTH_LIMIT, LIT_LENGTH_LIMIT + 1)
    zeros = Arith(ZERO_LENGTH_LIMIT, ZERO_LENGTH_LIMIT + 1)

    above = ab.get_value(num)
    if above:
        if vb.get1():
            above = -above
        above *= qlevel
    out[offset] = s16(above)
    above_left = above
    prev = above

    from_pos = offset
    outp = offset + 1

    h = enc_height

    if enc_width == 1:
        w = 0  # forces the "row advance only" path on first loop entry below
    else:
        w = enc_width - 1

    while True:
        # NOTE: lit_len/zero_len escapes are read via a *plain fixed-width VarBits
        # read* (VarBitsGet(..., LIT_LENGTH_BITS/ZERO_LENGTH_BITS) in the source),
        # NOT an arithmetic-coded ArithBitsGetValue like every other escape in this
        # codec -- confirmed against the real reference decoder's source directly.
        # _decode_symbol (used everywhere else) assumes the latter and is wrong
        # here; this was the actual bug behind every "runs out of bits" failure.
        lit_len, was_escape = lits.decompress_raw(ab)
        if was_escape:
            escaped = vb.get(LIT_LENGTH_BITS)
            lits.resolve_escape(escaped)
            lit_len = escaped
        if lit_len >= (LIT_LENGTH_LIMIT - EXTRA_LENGTHS + 1):
            lit_len = EXTRA_LIT_LENGTHS[lit_len - (LIT_LENGTH_LIMIT - EXTRA_LENGTHS + 1)]

        zero_len, was_escape = zeros.decompress_raw(ab)
        if was_escape:
            escaped = vb.get(ZERO_LENGTH_BITS)
            zeros.resolve_escape(escaped)
            zero_len = escaped
        if zero_len >= (ZERO_LENGTH_LIMIT - EXTRA_LENGTHS + 1):
            zero_len = EXTRA_ZERO_LENGTHS[zero_len - (ZERO_LENGTH_LIMIT - EXTRA_LENGTHS + 1)] + MIN_ZERO_LENGTH - 1
        elif zero_len:
            zero_len += MIN_ZERO_LENGTH - 1

        while lit_len:
            if w <= 1:
                if w:
                    context = get_bit_level((abs(prev * 2) + abs(above_left) + abs(above)) // 4)
                    cur = _decode_symbol(ab, contexts[context], num)
                    if cur:
                        if vb.get1():
                            cur = -cur
                        cur *= qlevel
                    out[outp] = s16(cur)
                    outp += 1
                    lit_len -= 1

                h -= 1
                if h == 0:
                    return
                w = enc_width
                outp += stride - enc_width  # advance from end-of-row to start-of-next-row
                from_pos = outp - stride
                above = out[from_pos]
                from_pos += 1
                above_left = above
                prev = above
            else:
                above_right = out[from_pos]
                context = get_bit_level((abs(prev) + abs(above_left) + abs(above) + abs(above_right)) // 4)
                cur = _decode_symbol(ab, contexts[context], num)
                if cur:
                    if vb.get1():
                        cur = -cur
                    cur *= qlevel
                out[outp] = s16(cur)
                above_left = above
                above = above_right
                prev = cur
                outp += 1
                from_pos += 1
                w -= 1
                lit_len -= 1

        while zero_len:
            if zero_len >= w:
                zero_len -= w
                from_pos += w
                for _ in range(w):
                    out[outp] = 0
                    outp += 1
                h -= 1
                if h == 0:
                    return
                w = enc_width
                outp += stride - enc_width
                from_pos = outp - stride
                above = out[from_pos]
                from_pos += 1
                above_left = above
                prev = above
            else:
                w -= zero_len
                from_pos += zero_len
                for _ in range(zero_len):
                    out[outp] = 0
                    outp += 1
                prev = 0
                above = out[from_pos - 1]
                above_left = out[from_pos - 2]
                zero_len = 0


def read_escapes(ab: ArithBits, count: int) -> None:
    """Consumes the same bits as the reference's row_mask escape scan. The
    resulting mask isn't needed for correctness: the mask only lets the
    reconstruction SKIP a multiply-accumulate for rows the encoder guarantees
    have all-zero high-pass detail already (a pure speed optimization -- using
    the real, already-zero detail coefficients directly gives identical
    results), so we only need to advance the arithmetic decoder correctly."""
    zeros = ab.get_value(count + 1)
    for _ in range(count):
        val = ab.get(count)
        if val >= zeros:
            ab._remove_symbol(zeros, count - zeros, count)
        else:
            ab._remove_symbol(0, zeros, count)


# ---------------------------------------------------------------------------
# Inverse 9/7 DWT and Haar lifting (wavelet.c), with symmetric boundary
# mirroring derived from the reference's manual buffer pre-fill code:
#   L[-k] = L[k]     for k>=1   (mirror about sample 0, edge not repeated)
#   H[-k-1] = H[k]   for k>=0   (mirror about the half-sample point -0.5)
# and assumed symmetric at the right edge for the same reasons (not directly
# read off the reference, which achieves it via pointer-direction-flipping
# rather than explicit indexing -- verify visually once decoding succeeds).
# ---------------------------------------------------------------------------

def _mirror_low(vals: list, i: int) -> int:
    # Right edge is half-sample symmetric (reflects about n-1+0.5, so the
    # last sample effectively repeats: l[n]=l[n-1], l[n+1]=l[n-2], ...) --
    # confirmed against the real reference decoder's iDWTrow via a live
    # instrumented trace (l[16]=L[15], l[17]=L[14] for a 16-sample band),
    # NOT the whole-sample-about-(n-1) convention this used to (wrongly)
    # share with the left edge.
    n = len(vals)
    if i < 0:
        return vals[-i] if -i < n else vals[0]
    if i >= n:
        j = 2 * n - 1 - i
        return vals[j] if 0 <= j < n else vals[-1]
    return vals[i]


def _mirror_high(vals: list, i: int) -> int:
    # Right edge is whole-sample symmetric about n-1 (no repeat: h[n]=h[n-2],
    # h[n+1]=h[n-3], ...) -- confirmed via the same live trace (h[16]=H[14],
    # h[17]=H[13] for a 16-sample band). This is the mirror image of what
    # _mirror_low needs at its right edge; the two were previously swapped.
    n = len(vals)
    if i < 0:
        j = -i - 1
        return vals[j] if 0 <= j < n else vals[0]
    if i >= n:
        j = 2 * n - 2 - i
        return vals[j] if 0 <= j < n else vals[-1]
    return vals[i]


def _dwt97_1d(low: list, high: list) -> list:
    n = len(low)
    out = [0] * (2 * n)
    for i in range(n):
        lm1 = _mirror_low(low, i - 1)
        l0 = _mirror_low(low, i)
        l1 = _mirror_low(low, i + 1)
        l2 = _mirror_low(low, i + 2)
        hm2 = _mirror_high(high, i - 2)
        hm1 = _mirror_high(high, i - 1)
        h0 = _mirror_high(high, i)
        h1 = _mirror_high(high, i + 1)
        h2 = _mirror_high(high, i + 2)

        e = l0 * 51674 - (lm1 + l1) * 2667 - (hm2 + h1) * 1563 + (hm1 + h0) * 24733
        o = (l0 + l1) * 27400 - (lm1 + l2) * 4230 - h0 * 55882 - (hm2 + h2) * 2479 + (hm1 + h1) * 7250

        out[2 * i] = _round_shift16(e)
        out[2 * i + 1] = _round_shift16(o)
    return out


def _haar_1d(low: list, high: list) -> list:
    n = len(low)
    out = [0] * (2 * n)
    for i in range(n):
        l = low[i]
        h = high[i]
        out[2 * i] = _round_shift1(l * 2 + h)
        out[2 * i + 1] = _round_shift1(l * 2 - h)
    return out


def _wavelet_2d(subband: list, width: int, height: int) -> list:
    """subband is a flat width*height array laid out as [[LL, LH], [HL, HH]]
    (each quadrant width/2 x height/2); returns the reconstructed width*height
    pixel array. Row pass then column pass (order matches iDWT2D exactly;
    interleaving those passes in the reference is a pure cache optimization
    with no semantic effect once done in full generality like this)."""
    half_w = width // 2
    half_h = height // 2
    row_fn = _dwt97_1d if width >= SMALLEST_DWT_ROW else _haar_1d
    col_fn = _dwt97_1d if height >= SMALLEST_DWT_COL else _haar_1d

    temp = [0] * (width * height)
    for y in range(height):
        off = y * width
        low = subband[off:off + half_w]
        high = subband[off + half_w:off + width]
        temp[off:off + width] = row_fn(low, high)

    out = [0] * (width * height)
    for x in range(width):
        low = [temp[y * width + x] for y in range(half_h)]
        high = [temp[(half_h + y) * width + x] for y in range(half_h)]
        merged = col_fn(low, high)
        for y in range(height):
            out[y * width + x] = merged[y]
    return out


# ---------------------------------------------------------------------------
# plane_decode: arithmetic/varbits header parsing + 4-level pyramid assembly.
# Unlike the reference (which reconstructs in place using a sparse, widely-
# strided single buffer to avoid extra allocations), this builds each level's
# subbands as clean standalone arrays and tiles them explicitly -- simpler and
# equally correct, since that in-place scheme is purely a memory optimization.
# ---------------------------------------------------------------------------

def plane_decode(comp: bytes, comp_offset: int, width: int, height: int) -> tuple[list, int]:
    sizes = struct.unpack_from("<II", comp, comp_offset)
    ab = ArithBits(comp, comp_offset + 8)
    vb = VarBits(comp, comp_offset + 8 + sizes[0])

    w = width // 16
    h = height // 16
    ll = [0] * (w * h)
    decode_low(ab, vb, ll, w, 0, w, h)

    for _level in range(4):
        next_w, next_h = w * 2, h * 2
        lh = [0] * (w * h)
        hl = [0] * (w * h)
        hh = [0] * (w * h)
        decode_high_1(ab, vb, lh, w, 0, w, h)
        decode_high_1(ab, vb, hl, w, 0, w, h)
        decode_high_1(ab, vb, hh, w, 0, w, h)

        combined = [0] * (next_w * next_h)
        for y in range(h):
            combined[y * next_w: y * next_w + w] = ll[y * w:(y + 1) * w]
            combined[y * next_w + w: y * next_w + next_w] = lh[y * w:(y + 1) * w]
        for y in range(h):
            base = (h + y) * next_w
            combined[base: base + w] = hl[y * w:(y + 1) * w]
            combined[base + w: base + next_w] = hh[y * w:(y + 1) * w]

        ll = _wavelet_2d(combined, next_w, next_h)
        w, h = next_w, next_h

    read_escapes(ab, height)

    return ll, sizes[0] + sizes[1] + 8


# ---------------------------------------------------------------------------
# Color conversion (granny_bink.cpp's YUVtoRGB -- a non-standard, reversible
# transform, not BT.601) and the top-level entry point.
# ---------------------------------------------------------------------------

def _clamp_byte(x: int) -> int:
    if x < 0:
        return 0
    if x > 255:
        return 255
    return x


def decode_binktc0(width: int, height: int, data: bytes, has_alpha: bool = False) -> bytes:
    """Decode a BinkTC0-compressed texture block into packed RGB(A)8 bytes,
    row-major top-down, width*height*(3 or 4) bytes total."""
    plane_count = 4 if has_alpha else 3
    offset = 4  # leading 4 bytes: "decompressed temp mem size" header, unused here
    planes = []
    for _ in range(plane_count):
        plane, consumed = plane_decode(data, offset, width, height)
        planes.append(plane)
        offset += consumed

    y_plane, u_plane, v_plane = planes[0], planes[1], planes[2]
    a_plane = planes[3] if has_alpha else None

    bpp = 4 if has_alpha else 3
    out = bytearray(width * height * bpp)
    for i in range(width * height):
        r = u_plane[i]
        g = y_plane[i]
        b = v_plane[i]
        a = a_plane[i] if a_plane else 255
        g -= trunc_div(r + b, 4)
        r += g
        b += g
        base = i * bpp
        out[base] = _clamp_byte(r)
        out[base + 1] = _clamp_byte(g)
        out[base + 2] = _clamp_byte(b)
        if has_alpha:
            out[base + 3] = _clamp_byte(a)
    return bytes(out)
