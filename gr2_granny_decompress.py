"""Real Granny2 sector decompressor, ported directly from Lionheart's own `granny2.dll`
(32-bit x86, Rad Game Tools) via Ghidra decompilation -- NOT from nwn2mdk/opengr2-c
(see docs/gr2-format.md: that algorithm is for a different, older granny2.dll build and
does not match Lionheart's bitstream).

Traced call chain (function addresses in granny2.dll, Ghidra project `/granny2.dll`):
    FUN_1001c670 (top-level, 3-phase loop)
      -> FUN_1001c080 (per-phase window-set init)         -> Dictionary.__init__ below
      -> FUN_1001c1f0 (per-block decode step)              -> Dictionary.decompress_block below
           -> FUN_1000de50 (weighted-window try-decode)     -> Window.try_decode below
                -> FUN_1000df50 (indexed bucket search)     -> Window._search below
                -> FUN_1000e390 + FUN_1000ddf0 (rebuild)     -> Window._rebuild below
           -> FUN_1000d780 (decode_commit)                  -> Decoder.decode_commit below
                -> FUN_1000d520 (bit-level range-coder commit/renormalize)

This is a carryless (Schindler-style) range coder: `low`/`high` track the current 31-bit
coding sub-interval, `value` tracks the reconstructed position within it, and all three
are renormalized together (shifting in fresh, *bit-reversed* bytes from the compressed
stream) whenever their leading bits agree closely enough. The per-symbol frequency model
(`Window`) is a balanced 16-leaf cumulative-sum tree (updated on every lookup, "Fenwick
tree"-style) with a linear scan inside the located leaf's block, not the flat linear-scan
array nwn2mdk used -- a real, confirmed algorithmic difference, not just a rewrite.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field as _field


def _rev_table(bits: int) -> list[int]:
    return [int(f"{i:0{bits}b}"[::-1], 2) for i in range(1 << bits)]


_REV4 = _rev_table(4)
_REV3 = _rev_table(3)

_MASK32 = 0xFFFFFFFF
_MASK31 = 0x7FFFFFFF


def _rev4(x: int) -> int:
    return _REV4[x & 0xF]


def _rev3(x: int) -> int:
    return _REV3[x & 0x7]


# ---------------------------------------------------------------------------
# Decoder -- the bit-level carryless range coder (FUN_1000d520 / FUN_1000d780
# / the init sequence at the top of FUN_1001c670).
# ---------------------------------------------------------------------------

class Decoder:
    def __init__(self, data: bytes, offset: int):
        self.data = data
        (dword,) = struct.unpack_from("<I", data, offset)
        self.pos = offset + 4          # next dword to read
        self.bit_accum = dword >> 31   # decoder.field2
        self.bit_count = 1             # decoder.field3
        self.high = _MASK31            # decoder.field4
        self.low = 0                   # decoder.field5
        self.value = self._init_value(dword)  # decoder.field6

    @staticmethod
    def _init_value(dword: int) -> int:
        u3 = dword & _MASK31
        v = _rev4((u3 >> 4) & 0xF) | (_rev4(dword & 0xF) << 4)
        v = (v << 4) | _rev4((u3 >> 8) & 0xF)
        v = (v << 4) | _rev4((u3 >> 0xC) & 0xF)
        v = (v << 4) | _rev4((u3 >> 0x10) & 0xF)
        v = (v << 4) | _rev4((u3 >> 0x14) & 0xF)
        v = (v << 4) | _rev4((u3 >> 0x18) & 0xF)
        v = (v << 3) | _rev3((u3 >> 0x1C) & 0x7)
        return v & _MASK31

    def _read_dword(self) -> int:
        chunk = self.data[self.pos:self.pos + 4]
        if len(chunk) < 4:
            chunk = chunk + b"\x00" * (4 - len(chunk))
        self.pos += 4
        (v,) = struct.unpack_from("<I", chunk, 0)
        return v

    def decode_commit(self, val: int, err: int, max_val: int) -> None:
        """Narrow [low, high] to the sub-range for `val` (of `err` width, out of
        `max_val`), then renormalize. Matches FUN_1000d520 exactly (val/err/max_val
        are its param_2/param_3/param_4)."""
        low, high = self.low, self.high
        total = (high - low + 1) & _MASK32
        u7 = ((total * (val + err)) // max_val - 1 + low) & _MASK32   # new high
        u2 = (low + (total * val) // max_val) & _MASK32               # new low
        u5 = self.value

        u3 = (u2 ^ u7) & _MASK32
        if (u3 & 0x40000000) == 0:
            # -- byte-granularity renormalization --
            while (u3 & 0x7F800000) == 0:
                bc = self.bit_count
                u2 = (u2 << 8) & _MASK32
                u7 = ((u7 << 8) | 0xFF) & _MASK32
                if bc < 8:
                    dword = self._read_dword()
                    u4 = ((dword << bc) & _MASK32) | self.bit_accum
                    new_bc = bc + 0x18
                    shift = (8 - bc) & 0x1F
                    self.bit_accum = (dword >> shift) if shift else 0
                else:
                    u4 = self.bit_accum
                    self.bit_accum = u4 >> 8
                    new_bc = bc - 8
                self.bit_count = new_bc
                u5 = (((_rev4(u4 & 0xF) | (u5 << 4)) & _MASK32) << 4 | _rev4((u4 >> 4) & 0xF)) & _MASK32
                u3 = (u2 ^ u7) & _MASK32

            # -- nibble-granularity renormalization (runs at most once) --
            if ((u2 ^ u7) & 0x78000000) == 0:
                bc = self.bit_count
                u2 = (u2 << 4) & _MASK32
                u7 = ((u7 << 4) | 0xF) & _MASK32
                if bc < 4:
                    dword = self._read_dword()
                    u4 = ((dword << bc) & _MASK32) | self.bit_accum
                    self.bit_count = bc + 0x1C
                    shift = (4 - bc) & 0x1F
                    self.bit_accum = (dword >> shift) if shift else 0
                else:
                    u4 = self.bit_accum
                    self.bit_accum = u4 >> 4
                    self.bit_count = bc - 4
                u5 = ((u5 << 4) | _rev4(u4 & 0xF)) & _MASK32

            # -- bit-granularity renormalization --
            u3 = (u2 ^ u7) & _MASK32
            while (u3 & 0x40000000) == 0:
                u2 = (u2 << 1) & _MASK32
                u7 = ((u7 * 2) | 1) & _MASK32
                if self.bit_count == 0:
                    dword = self._read_dword()
                    bit = dword & 1
                    self.bit_accum = dword >> 1
                    self.bit_count = 0x1F
                else:
                    bit = self.bit_accum & 1
                    self.bit_accum = self.bit_accum >> 1
                    self.bit_count -= 1
                u5 = ((u5 * 2) | bit) & _MASK32
                u3 = (u2 ^ u7) & _MASK32

        # -- underflow ("E3 mapping") handling --
        while (u2 & 0x20000000) != 0 and (u7 & 0x20000000) == 0:
            u2 = ((u2 & 0x1FFFFFFF) << 1) & _MASK32
            u7 = ((u7 * 2) | 0x40000001) & _MASK32
            if self.bit_count == 0:
                dword = self._read_dword()
                bit = dword & 1
                self.bit_accum = dword >> 1
                self.bit_count = 0x1F
            else:
                bit = self.bit_accum & 1
                self.bit_accum = self.bit_accum >> 1
                self.bit_count -= 1
            u5 = (((u5 ^ 0x20000000) * 2) | bit) & _MASK32

        self.low = u2 & _MASK31
        self.high = u7 & _MASK31
        self.value = u5 & _MASK31


# ---------------------------------------------------------------------------
# Window -- adaptive frequency model: 16-leaf balanced cumulative-sum tree +
# linear scan within the located block (FUN_1000d820 init, FUN_1000de50
# try-decode, FUN_1000df50 search, FUN_1000e390/FUN_1000ddf0 rebuild).
# ---------------------------------------------------------------------------

_REBUILD_THRESHOLD = 0x3FFF


class Window:
    def __init__(self, size: int):
        """`size` is FUN_1000d820's single `param_4` -- every window in Dictionary is
        created with param_2=0, which (per FUN_1000d820's own logic) makes param_4 do
        double duty as both the structural sizing basis (block width, via
        FUN_1000d890) and count_cap (ushort[0x16], compared against value_count).
        There is no separate max_value vs count_cap distinction in the real code --
        that split was carried over from nwn2mdk and does not apply here."""
        self.size = size
        self.count_cap = size
        self.shift = self._pick_shift(size + 1)  # FUN_1000d820 calls FUN_1000d890(.., param_4+1)
        self.block_width = 1 << self.shift
        # New values are allocated sequentially at positions 1..count_cap, which can
        # exceed the tree's own 16*block_width structural range (that's exactly what
        # _add's "escape_threshold" bypass -- position >= 15*block_width skips the
        # tree entirely -- exists for). Size generously enough to cover both, plus the
        # +1/+2 linear-scan lookahead in _search.
        n_slots = max(16 * self.block_width, self.count_cap + 1) + 2
        self.tree = [0] * 15          # cumulative-sum internal nodes (prefix sums)
        self.weight_total = 0
        self.value_count = 0
        self.values: list[int] = [0] * n_slots
        self.weights: list[int] = [0] * n_slots
        self._add(0, 3)  # FUN_1000d820's trailing FUN_1000d920(window, 0, 0x30003)

    @staticmethod
    def _pick_shift(max_value: int) -> int:
        """Port of FUN_1000d890: choose a block width (as a shift amount) that
        divides `max_value` into <=16 roughly-equal blocks."""
        if max_value < 6:
            return 0xF  # block_width becomes huge; effectively "one block" -- matches
                         # the C function's early-out (0xf/0/0 written for tiny max_value)
        best_shift = 0
        best_waste = 0xFFFFFFFF
        shift = 0
        while True:
            block = 1 << shift
            blocks = (block - 1 + max_value) // block
            if blocks > 0x10:
                blocks = 0x10
            waste = max_value - (blocks - 1) * block
            if waste < block:
                waste = block
            if waste < best_waste:
                best_shift = shift
                best_waste = waste
            if block > max_value:
                break
            shift += 1
            if shift >= 0x10:
                break
        return best_shift

    def _add(self, position: int, delta: int) -> None:
        """Port of FUN_1000d920: weights[position] += delta, propagating into
        `tree` (a plain prefix-sum array: tree[i] = cumulative weight through
        block i) by bumping the *suffix* of nodes from this block's index
        onward -- NOT an ancestor path. Also updates weight_total, which is
        the array's implicit 16th ("past the end") entry."""
        escape_threshold = 15 * self.block_width
        if position >= escape_threshold:
            self.weight_total += delta
            self.weights[position] += delta
            return
        idx = position >> self.shift
        if idx & 1:
            self.tree[idx] += delta
            idx += 1
        for i in range(idx, 15):
            self.tree[i] += delta
        self.weight_total += delta
        self.weights[position] += delta

    def _search(self, target: int) -> tuple[int, int]:
        """Port of FUN_1000df50. Binary-search the prefix-sum tree for the
        block containing `target`, apply that block's implicit "+1" update
        (matching the per-leaf hardcoded pattern in the real function, which
        is exactly _add(block_index, 1) without touching weights[] itself),
        then linear-scan within the block for the exact slot. Returns
        (slot_index, cumsum_before_slot)."""
        a, size, base = 0, 16, 0
        while size > 1:
            half = size // 2
            node = a + half - 1
            threshold = self.tree[node]
            if target < threshold:
                size = half
            else:
                base = threshold
                a += half
                size = half

        if a < 15:
            idx = a
            if idx & 1:
                self.tree[idx] += 1
                idx += 1
            for i in range(idx, 15):
                self.tree[i] += 1
        self.weight_total += 1

        slot = a * self.block_width
        cum = base
        while True:
            nxt = self.weights[slot] + cum
            if target < nxt:
                return slot, cum
            cum2 = self.weights[slot + 1] + nxt
            if target < cum2:
                return slot + 1, nxt
            cum = cum2
            slot += 2

    def _rebuild(self) -> None:
        """Port of FUN_1000e390 + FUN_1000ddf0, verified line-by-line against raw
        disassembly (not just decompilation -- Ghidra's decompiled pseudo-C badly
        mistypes several plain-integer loop counters as pointers here, which is
        actively misleading without cross-checking the real instructions).

        Algorithm, confirmed via disassembly:
        1. Recompute shift/block_width for value_count+1 (NOT the original
           construction-time size -- the block structure is re-tuned to the
           current population every rebuild).
        2. Halve weights[0] (the escape slot) up front, specially.
        3. For i = 1..value_count (a live bound -- shrinks as entries are dropped):
           while weights[i] < 2: either drop it (i >= value_count: zero its value,
           shrink value_count) or compact by copying the *tail* entry
           (weights[value_count]/values[value_count]) into slot i and clearing the
           tail, shrinking value_count -- swap-with-last compaction, not an
           order-preserving shift. Then (whichever exit was taken) halve weights[i]
           and accumulate it into this position's coarse block total, tracking
           the single largest post-halving weight and its index as we go.
        4. If any weight was nonzero, relocate that single largest-weight entry to
           a canonical position (start of its own block, or the escape boundary),
           swapping weights/values and adjusting the two affected block totals.
        5. If weights[0] became exactly 0 and there's still room (value_count !=
           count_cap), reset it to 2 (keeps the "escape to a new symbol" path
           reachable) -- FUN_1000ddf0's own guard, ported into the final tree pass.
        6. Rebuild `tree` as a plain running prefix sum over the 16 block totals;
           weight_total is exactly the 16th (final) entry of that same chain.
        """
        shift = self._pick_shift(self.value_count + 1)
        block_width = 1 << shift
        escape_threshold = 15 * block_width

        def block_of(i: int) -> int:
            return (i >> shift) if i < escape_threshold else 15

        self.weights[0] >>= 1

        value_count = self.value_count
        block_totals = [0] * 16
        # weights[0] (the escape slot) is seeded into block_totals[0] directly --
        # this is an assignment in the disassembly, not folded into the i=1.. loop
        # below, and was missing entirely in an earlier version of this port.
        block_totals[0] += self.weights[0]
        best_weight = 0
        best_index = 1

        i = 1
        while i <= value_count:
            w = self.weights[i]
            while w < 2:
                if i >= value_count:
                    self.values[i] = 0
                    value_count -= 1
                    break
                self.weights[i] = self.weights[value_count]
                self.values[i] = self.values[value_count]
                self.weights[value_count] = 0
                value_count -= 1
                w = self.weights[i]
            w >>= 1
            self.weights[i] = w
            if w > best_weight:
                best_weight = w
                best_index = i
            block_totals[block_of(i)] += w
            i += 1

        self.value_count = value_count
        self.shift = shift
        self.block_width = block_width

        if best_weight != 0:
            target = ((value_count >> shift) << shift) if value_count < escape_threshold else escape_threshold
            if target == 0:
                target = 1
            if best_index != target:
                old_best_w, old_target_w = self.weights[best_index], self.weights[target]
                self.weights[best_index], self.weights[target] = old_target_w, old_best_w
                self.values[best_index], self.values[target] = self.values[target], self.values[best_index]
                block_totals[block_of(target)] += old_best_w - old_target_w
                block_totals[block_of(best_index)] += old_target_w - old_best_w

        if value_count != self.count_cap and self.weights[0] == 0:
            self.weights[0] = 2
            block_totals[0 if escape_threshold != 0 else 15] += 2

        self.tree[0] = block_totals[0]
        for i in range(1, 15):
            self.tree[i] = self.tree[i - 1] + block_totals[i]
        self.weight_total = self.tree[14] + block_totals[15]

    def try_decode(self, decoder: Decoder) -> int:
        """Port of FUN_1000de50. Decodes and returns the byte/symbol value."""
        if self.weight_total > _REBUILD_THRESHOLD:
            self._rebuild()

        total_range = (decoder.high - decoder.low + 1) & _MASK32
        scaled = (((decoder.value - decoder.low + 1) & _MASK32) * self.weight_total - 1) // total_range
        slot, cum_before = self._search(scaled)

        # _search already applied the "+1 to this block" tree/weight_total update;
        # here we only need the plain leaf weight increment (matches the real
        # code's bare `weights[slot] += 1`, done separately from the tree update).
        weight = self.weights[slot]
        decoder.decode_commit(cum_before, weight, self.weight_total - 1)
        self.weights[slot] = weight + 1

        if slot == 0:
            self.value_count += 1
            new_slot = self.value_count
            self._add(new_slot, 2)
            if self.value_count == self.count_cap:
                self._add(0, -self.weights[0])
            # sentinel: caller must decode a fresh value and store it here
            return -new_slot - 1
        return self.values[slot]

    def store_new_value(self, slot_sentinel: int, value: int) -> None:
        slot = -slot_sentinel - 1
        self.values[slot] = value


# ---------------------------------------------------------------------------
# Dictionary -- per-phase set of windows (FUN_1001c080) + per-block decode
# step (FUN_1001c1f0).
# ---------------------------------------------------------------------------

@dataclass
class Parameter:
    decoded_value_max: int
    backref_value_max: int
    decoded_count: int
    offset_hi_size: int  # raw word1>>9 (padding|highbit_count combined, NOT pure highbit_count)
    sizes_count: bytes


def _parse_parameter(data: bytes, offset: int) -> Parameter:
    w0, w1 = struct.unpack_from("<II", data, offset)
    sizes_count = data[offset + 8:offset + 12]
    return Parameter(
        decoded_value_max=w0 & 0x1FF,
        backref_value_max=(w0 >> 9) & 0x7FFFFF,
        decoded_count=w1 & 0x1FF,
        offset_hi_size=w1 >> 9,
        sizes_count=sizes_count,
    )


class Dictionary:
    """Port of FUN_1001c080. Real structure (confirmed via raw disassembly, NOT the
    nwn2mdk-shaped structure this was first modeled on -- see docs/gr2-format.md):
    one single decoded_window (not four, no pos%4 indexing), 65 size_windows (indices
    0..64, matching backref_size+1), and exactly TWO offset windows (not three) whose
    resolved values combine as `offset = hi*4 + lo + 1` -- read directly off the
    memcpy source-address computation in FUN_1001c1f0's disassembly."""

    def __init__(self, param: Parameter):
        self.decoded_size = 0
        self.backref_size = 0

        self.decoded_value_max = param.decoded_value_max
        self.backref_value_max = param.backref_value_max

        self.decoded_window = Window(param.decoded_count)

        self.size_windows: list[Window] = []
        for i in range(4):
            for _ in range(16):
                self.size_windows.append(Window(param.sizes_count[3 - i]))
        self.size_windows.append(Window(param.sizes_count[0]))

        self.offset_lo_size = min(self.backref_value_max, 4)
        self.offset_lo_window = Window(self.offset_lo_size)
        self.offset_hi_window = Window(param.offset_hi_size)


_BACKREF_SIZES = (128, 192, 256, 512)


def decompress_block(dictionary: Dictionary, decoder: Decoder, buf: bytearray, pos: int) -> int:
    def resolve(window: Window, max_val: int) -> int:
        r = window.try_decode(decoder)
        if r < 0:
            v = decode_raw(decoder, max_val)
            window.store_new_value(r, v)
            return v
        return r

    d1 = resolve(dictionary.size_windows[dictionary.backref_size], 65)
    dictionary.backref_size = d1

    if dictionary.backref_size > 0:
        bs = dictionary.backref_size
        backref_size = bs + 1 if bs < 61 else _BACKREF_SIZES[bs - 61]

        lo = resolve(dictionary.offset_lo_window, dictionary.offset_lo_size)
        backref_range = min(dictionary.backref_value_max, dictionary.decoded_size)
        hi = resolve(dictionary.offset_hi_window, (backref_range >> 2) + 1)

        backref_offset = hi * 4 + lo + 1
        dictionary.decoded_size += backref_size

        repeat = backref_size // backref_offset
        remain = backref_size % backref_offset
        src = pos - backref_offset
        for i in range(repeat):
            dst = pos + i * backref_offset
            buf[dst:dst + backref_offset] = buf[src:src + backref_offset]
        dst = pos + repeat * backref_offset
        buf[dst:dst + remain] = buf[src:src + remain]
        return backref_size

    d2 = resolve(dictionary.decoded_window, dictionary.decoded_value_max)
    buf[pos] = d2 & 0xFF
    dictionary.decoded_size += 1
    return 1


def decode_raw(decoder: Decoder, max_val: int) -> int:
    """Equivalent of nwn2mdk's plain Decode(): the *fresh* scaled position within
    [0, max_val), used only for genuinely new symbols (matches the real decoder's
    `decode_commit(decoder, max_val)` idiom -- narrow to [0,max_val) uniformly then
    commit with err=1, exactly mirroring how FUN_1000d780 is used for this in the
    real code when a Window signals a brand-new value)."""
    total_range = (decoder.high - decoder.low + 1) & _MASK32
    scaled = min((((decoder.value - decoder.low + 1) & _MASK32) * max_val - 1) // total_range, max_val - 1)
    decoder.decode_commit(scaled, 1, max_val)
    return scaled


def granny_decompress(raw_file: bytes, data_offset: int, decompressed_length: int,
                       stop0: int, stop1: int) -> bytes:
    if decompressed_length == 0:
        return b""

    params = [_parse_parameter(raw_file, data_offset + i * 12) for i in range(3)]
    decoder = Decoder(raw_file, data_offset + 36)
    steps = (stop0, stop1, decompressed_length)

    buf = bytearray(decompressed_length + 512)
    pos = 0
    for i in range(3):
        dictionary = Dictionary(params[i])
        while pos < steps[i]:
            pos += decompress_block(dictionary, decoder, buf, pos)

    return bytes(buf[:decompressed_length])
