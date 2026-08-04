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
    def __init__(self, max_value: int, count_cap: int):
        self.max_value = max_value
        self.count_cap = count_cap
        self.shift = self._pick_shift(max_value + 1)  # FUN_1000d820 calls FUN_1000d890(.., param_4+1)
        self.block_width = 1 << self.shift
        n_slots = 16 * self.block_width
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
        """Port of FUN_1000e390 + FUN_1000ddf0: halve all weights, drop entries
        that hit zero, then recompute the 16 block totals and rebuild the tree."""
        block_totals = [0] * 16
        write = 1
        for read in range(1, self.value_count + 1):
            w = self.weights[read] >> 1
            if w == 0:
                continue
            self.weights[write] = w
            self.values[write] = self.values[read]
            write += 1
        for i in range(write, self.value_count + 1):
            self.weights[i] = 0
        self.value_count = write - 1

        total = 0
        for i in range(1, self.value_count + 1):
            total += self.weights[i]
            block_totals[min(i // self.block_width, 15)] += self.weights[i]
        # weights[0] (the "escape"/new-symbol placeholder slot) keeps its own weight
        block_totals[0] += self.weights[0]
        total += self.weights[0]
        self.weight_total = total

        self.tree[0] = block_totals[0]
        for i in range(1, 15):
            self.tree[i] = self.tree[i - 1] + block_totals[i]

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
    highbit_count: int
    sizes_count: bytes


def _parse_parameter(data: bytes, offset: int) -> Parameter:
    w0, w1 = struct.unpack_from("<II", data, offset)
    sizes_count = data[offset + 8:offset + 12]
    return Parameter(
        decoded_value_max=w0 & 0x1FF,
        backref_value_max=(w0 >> 9) & 0x7FFFFF,
        decoded_count=w1 & 0x1FF,
        highbit_count=(w1 >> 19) & 0x1FFF,
        sizes_count=sizes_count,
    )


class Dictionary:
    def __init__(self, param: Parameter):
        self.decoded_size = 0
        self.backref_size = 0

        self.decoded_value_max = param.decoded_value_max
        self.backref_value_max = param.backref_value_max
        self.lowbit_value_max = min(self.backref_value_max + 1, 4)
        self.midbit_value_max = min(self.backref_value_max // 4 + 1, 256)
        self.highbit_value_max = self.backref_value_max // 1024 + 1

        self.lowbit_window = Window(self.lowbit_value_max, self.lowbit_value_max)
        self.highbit_window = Window(self.highbit_value_max, param.highbit_count + 1)
        self.midbit_windows = [Window(self.midbit_value_max, self.midbit_value_max)
                                for _ in range(self.highbit_value_max)]
        self.decoded_windows = [Window(self.decoded_value_max, param.decoded_count)
                                 for _ in range(4)]

        self.size_windows: list[Window] = []
        for i in range(4):
            for _ in range(16):
                self.size_windows.append(Window(64, param.sizes_count[3 - i]))
        self.size_windows.append(Window(64, param.sizes_count[0]))


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
        backref_range = min(dictionary.backref_value_max, dictionary.decoded_size)

        d3 = resolve(dictionary.lowbit_window, dictionary.lowbit_value_max)
        d4 = resolve(dictionary.highbit_window, backref_range // 1024 + 1)
        d5 = resolve(dictionary.midbit_windows[d4], min(backref_range // 4 + 1, 256))

        backref_offset = (d4 << 10) + (d5 << 2) + d3 + 1
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

    i = pos % 4
    d2 = resolve(dictionary.decoded_windows[i], dictionary.decoded_value_max)
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
