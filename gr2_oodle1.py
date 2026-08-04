"""Oodle1 decompression, ported from `opengr2-c`'s `libopengrn/oodle1.c` + `compression.c`.

That C implementation is itself derived from nwn2mdk (Neverwinter Nights 2's community
reverse-engineering project, MPL-2.0): https://github.com/Arbos/nwn2mdk. It's a custom
adaptive arithmetic (range) coder: a small LZ-style dictionary coder with several
frequency-adaptive "weigh window" contexts (one for literal bytes, several for backref
distance/length fields).

Ported by hand (no C/Rust toolchain available) -- structurally a 1:1 translation of the
C structs/functions (`TDecoder`, `TWeighWindow`, `TDictionary`, `TParameter`) to avoid
introducing new bugs by "improving" the algorithm along the way.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field as _field


# ---------------------------------------------------------------------------
# TParameter -- a 12-byte bitfield-packed header, 3 of these precede the coded stream.
#   word0: decoded_value_max:9, backref_value_max:23   (LSB-first, matches x86 bitfield layout)
#   word1: decoded_count:9, padding:10, highbit_count:13
#   bytes: sizes_count[4]
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


# ---------------------------------------------------------------------------
# TDecoder -- the arithmetic (range) decoder core.
# ---------------------------------------------------------------------------

class Decoder:
    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos
        self.numer = data[pos] >> 1
        self.denom = 0x80
        self.next_denom = 0

    def _byte(self, i: int) -> int:
        idx = self.pos + i
        return self.data[idx] if idx < len(self.data) else 0

    def decode(self, max_val: int) -> int:
        while self.denom <= 0x800000:
            self.numer = (self.numer << 8) & 0xFFFFFFFF
            self.numer |= (self._byte(0) << 7) & 0x80
            self.numer |= (self._byte(1) >> 1) & 0x7F
            self.pos += 1
            self.denom = (self.denom << 8) & 0xFFFFFFFF

        self.next_denom = self.denom // max_val
        return min(self.numer // self.next_denom, max_val - 1)

    def commit(self, max_val: int, val: int, err: int) -> int:
        self.numer -= self.next_denom * val
        if val + err < max_val:
            self.denom = self.next_denom * err
        else:
            self.denom -= self.next_denom * val
        return val

    def decode_commit(self, max_val: int) -> int:
        return self.commit(max_val, self.decode(max_val), 1)


# ---------------------------------------------------------------------------
# TWeighWindow -- an adaptive frequency table used as a coding context.
# ---------------------------------------------------------------------------

@dataclass
class WeighWindow:
    count_cap: int
    ranges: list[int] = _field(default_factory=list)
    weights: list[int] = _field(default_factory=list)
    values: list[int] = _field(default_factory=list)
    weight_total: int = 4
    thresh_increase: int = 4
    thresh_increase_cap: int = 128
    thresh_range_rebuild: int = 8
    thresh_weight_rebuild: int = 256


def weigh_window_init(max_value: int, count_cap: int) -> WeighWindow:
    ww = WeighWindow(count_cap=count_cap + 1)
    ww.ranges = [0, 0x4000]
    ww.weights = [4]
    ww.values = [0]
    ww.thresh_weight_rebuild = max(256, min(32 * max_value, 15160))
    if max_value > 64:
        ww.thresh_increase_cap = min(2 * max_value, ww.thresh_weight_rebuild // 2 - 32)
    else:
        ww.thresh_increase_cap = 128
    return ww


def _max_element(arr: list[int], length: int, offset: int) -> int:
    m = 0
    index = offset
    for i in range(offset, length):
        if arr[i] > m:
            m = arr[i]
            index = i
    return index


def _rebuild_ranges(ww: WeighWindow) -> None:
    if len(ww.ranges) != len(ww.weights) + 1:
        ww.ranges = [0] * (len(ww.weights) + 1)

    range_weight = (8 * 0x4000) // ww.weight_total
    range_start = 0
    for i in range(len(ww.weights)):
        ww.ranges[i] = range_start
        range_start += (ww.weights[i] * range_weight) // 8
    ww.ranges[len(ww.ranges) - 1] = 0x4000

    if ww.thresh_increase > ww.thresh_increase_cap // 2:
        ww.thresh_range_rebuild = ww.weight_total + ww.thresh_increase_cap
    else:
        ww.thresh_increase *= 2
        ww.thresh_range_rebuild = ww.weight_total + ww.thresh_increase


def _rebuild_weights(ww: WeighWindow) -> None:
    weight_total = 0
    for i in range(len(ww.weights)):
        ww.weights[i] //= 2
        weight_total += ww.weights[i]
    ww.weight_total = weight_total

    i = 1
    while i < len(ww.weights):
        while i < len(ww.weights) and ww.weights[i] == 0:
            ww.weights[i] = ww.weights[-1]
            ww.values[i] = ww.values[-1]
            ww.weights.pop()
            ww.values.pop()
        i += 1

    it = _max_element(ww.weights, len(ww.weights), 1)
    if it < len(ww.weights):
        ww.weights[it], ww.weights[-1] = ww.weights[-1], ww.weights[it]
        ww.values[it], ww.values[-1] = ww.values[-1], ww.values[it]

    if len(ww.weights) < ww.count_cap and ww.weights[0] == 0:
        ww.weights[0] = 1
        ww.weight_total += 1


def _try_decode(ww: WeighWindow, decoder: Decoder) -> tuple[int | None, int]:
    """Returns (new_value_index_or_None, value). A non-None index means the caller
    must decode the actual value and store it at ww.values[index]."""
    if ww.weight_total >= ww.thresh_range_rebuild:
        if ww.thresh_range_rebuild >= ww.thresh_weight_rebuild:
            _rebuild_weights(ww)
        _rebuild_ranges(ww)

    value = decoder.decode(0x4000)
    rangeit = len(ww.ranges) - 1
    for i in range(len(ww.ranges)):
        if ww.ranges[i] > value:
            rangeit = i
            break
    if rangeit == 0:
        rangeit = 0
    rangeit -= 1

    decoder.commit(0x4000, ww.ranges[rangeit], ww.ranges[rangeit + 1] - ww.ranges[rangeit])

    index = rangeit
    ww.weights[index] += 1
    ww.weight_total += 1

    if index > 0:
        return None, ww.values[index]

    if len(ww.weights) >= len(ww.ranges) and decoder.decode_commit(2) == 1:
        index = len(ww.ranges) + decoder.decode_commit(len(ww.weights) - len(ww.ranges) + 1) - 1
        ww.weights[index] += 2
        ww.weight_total += 2
        return None, ww.values[index]

    ww.values.append(0)
    ww.weights.append(2)
    ww.weight_total += 2

    if len(ww.weights) == ww.count_cap:
        ww.weight_total -= ww.weights[0]
        ww.weights[0] = 0

    return len(ww.values) - 1, 0


# ---------------------------------------------------------------------------
# TDictionary -- the LZ-style block decompressor built on top of the weigh windows.
# ---------------------------------------------------------------------------

_BACKREF_SIZES = (128, 192, 256, 512)


class Dictionary:
    def __init__(self, param: Parameter):
        self.decoded_size = 0
        self.backref_size = 0

        self.decoded_value_max = param.decoded_value_max
        self.backref_value_max = param.backref_value_max
        self.lowbit_value_max = min(self.backref_value_max + 1, 4)
        self.midbit_value_max = min(self.backref_value_max // 4 + 1, 256)
        self.highbit_value_max = self.backref_value_max // 1024 + 1

        self.lowbit_window = weigh_window_init(self.lowbit_value_max - 1, self.lowbit_value_max)
        self.highbit_window = weigh_window_init(self.highbit_value_max - 1, param.highbit_count + 1)

        self.midbit_windows = [
            weigh_window_init(self.midbit_value_max - 1, self.midbit_value_max)
            for _ in range(self.highbit_value_max)
        ]

        self.decoded_windows = [
            weigh_window_init(self.decoded_value_max - 1, param.decoded_count)
            for _ in range(4)
        ]

        self.size_windows: list[WeighWindow] = []
        for i in range(4):
            for _ in range(16):
                self.size_windows.append(weigh_window_init(64, param.sizes_count[3 - i]))
        self.size_windows.append(weigh_window_init(64, param.sizes_count[0]))


def _decompress_block(dictionary: Dictionary, decoder: Decoder, buf: bytearray, pos: int) -> int:
    d1_idx, d1_val = _try_decode(dictionary.size_windows[dictionary.backref_size], decoder)
    if d1_idx is not None:
        d1_val = decoder.decode_commit(65)
        dictionary.size_windows[dictionary.backref_size].values[d1_idx] = d1_val
    dictionary.backref_size = d1_val

    if dictionary.backref_size > 0:
        backref_size = (
            dictionary.backref_size + 1 if dictionary.backref_size < 61
            else _BACKREF_SIZES[dictionary.backref_size - 61]
        )
        backref_range = min(dictionary.backref_value_max, dictionary.decoded_size)

        d3_idx, d3_val = _try_decode(dictionary.lowbit_window, decoder)
        if d3_idx is not None:
            d3_val = decoder.decode_commit(dictionary.lowbit_value_max)
            dictionary.lowbit_window.values[d3_idx] = d3_val

        d4_idx, d4_val = _try_decode(dictionary.highbit_window, decoder)
        if d4_idx is not None:
            d4_val = decoder.decode_commit(backref_range // 1024 + 1)
            dictionary.highbit_window.values[d4_idx] = d4_val

        d5_idx, d5_val = _try_decode(dictionary.midbit_windows[d4_val], decoder)
        if d5_idx is not None:
            d5_val = decoder.decode_commit(min(backref_range // 4 + 1, 256))
            dictionary.midbit_windows[d4_val].values[d5_idx] = d5_val

        backref_offset = (d4_val << 10) + (d5_val << 2) + d3_val + 1

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
    d2_idx, d2_val = _try_decode(dictionary.decoded_windows[i], decoder)
    if d2_idx is not None:
        d2_val = decoder.decode_commit(dictionary.decoded_value_max)
        dictionary.decoded_windows[i].values[d2_idx] = d2_val

    buf[pos] = d2_val & 0xFF
    dictionary.decoded_size += 1
    return 1


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def oodle1_decompress(raw_file: bytes, data_offset: int, decompressed_length: int,
                       oodle_stop_0: int, oodle_stop_1: int) -> bytes:
    """Decompress an Oodle1-compressed GR2 sector.

    `raw_file`/`data_offset` (rather than a pre-sliced buffer) so the decoder's
    intentional lookahead reads (it reads up to 1 byte past its logical stream
    position) land on real subsequent file bytes, matching how the reference C
    implementation reads out of a single whole-file buffer.
    """
    if decompressed_length == 0:
        return b""

    params = [_parse_parameter(raw_file, data_offset + i * 12) for i in range(3)]

    decoder = Decoder(raw_file, data_offset + 36)
    steps = (oodle_stop_0, oodle_stop_1, decompressed_length)

    # The final block of a phase can overshoot its target boundary (block sizes are
    # quantized -- 2..61, or 128/192/256/512 -- and don't always land exactly on the
    # boundary), so decode into a padded buffer and truncate to the declared length.
    buf = bytearray(decompressed_length + 512)
    pos = 0

    for i in range(3):
        dictionary = Dictionary(params[i])
        while pos < steps[i]:
            pos += _decompress_block(dictionary, decoder, buf, pos)

    return bytes(buf[:decompressed_length])
