# BinkTC0 texture format — SOLVED

`Texture.Encoding=3` (`GrannyBinkTextureEncoding`) is the compression used by the vast
majority of real Lionheart textures (230/230 sampled). It decodes correctly now:
`binktc0_decode.py`'s `decode_binktc0()` is **verified bit-exact** against a compiled
copy of Granny's own reference decoder, for all three (Y/U/V) planes and the full
end-to-end pipeline, on a real game texture (`WereRat.MODEL.GR2`'s diffuse map).

## How to use it

```python
from binktc0_decode import decode_binktc0

rgb_bytes = decode_binktc0(width, height, packet_bytes, has_alpha=False)
# rgb_bytes is width*height*3 (or *4 with has_alpha=True) packed RGB(A)8, row-major top-down
```

`packet_bytes` is `Texture.Images[].MIPLevels[].Pixels` read directly off the decoded
GR2 element tree — no container/header stripping needed beyond what `decode_binktc0`
already does internally (it skips the leading 4-byte "decompressed temp mem size" word).

**Not yet wired up**: `gr2_to_gltf.py`'s `_extract_texture_png` currently only handles
`Encoding=1` (raw uncompressed pixels) and returns `None` otherwise — meaning it produces
no image for any real game texture today. Calling `decode_binktc0` from there for
`Encoding=3` is the obvious next step, not yet done.

## What this format actually is

It is **not** the Bink video codec (DCT + Huffman, used for `.bik` video files) — that
was this investigation's first, wrong turn (see "Dead end" below). The real algorithm
was found by reading Granny's own SDK source directly:
`github.com/Final-Game-Production-Inc/Granny-3D-SDK`, `source/granny_bink0_compression.cpp`
+ `granny_compression_tools.cpp` (RAD Game Tools, Granny 2.9.12.0 — the *actual* shipped
version, confirmed via `CompileAssert(ProductBuildNumber==12)` in the source itself).

It's a **wavelet still-image compressor** ("BinkTC" = Bink Texture Compression, an
internal Granny format that just happens to share a name prefix with the unrelated Bink
video codec):

- **4-level 9/7 discrete wavelet transform** (CDF 9/7 lifting, the same wavelet family
  JPEG2000 uses), falling back to a Haar transform when a subband dimension drops below
  `SMALLEST_DWT_ROW=12` / `SMALLEST_DWT_COL=10`.
- **Adaptive arithmetic coding** for entropy coding of wavelet coefficients, with a
  literal/zero-run-length scheme layered on top (most high-frequency coefficients are
  zero, so runs of zero are coded far more cheaply than individual zero symbols).
- A **non-standard, reversible YUV-like color transform** (`granny_bink.cpp`'s
  `YUVtoRGB`) applied after all three planes decode — not BT.601 or any standard
  matrix; see `decode_binktc0`'s tail for the exact integer math
  (`g -= (r+b)//4; r += g; b += g`, truncating division).

Top-level structure (`decode_binktc0` → `plane_decode` × 3, once per Y/U/V plane):

1. Skip the leading 4-byte "decompressed temp mem size" header.
2. For each plane: an 8-byte `sizes` header, then an `ArithBits` stream and a `VarBits`
   stream (two interleaved bit readers over the *same* compressed bytes — the arithmetic
   stream starts right after the header, the VarBits stream starts `sizes[0]` bytes
   later).
3. `decode_low` decodes the base `width/16 × height/16` LL band (simple order-1
   prediction, no run-length scheme — small enough not to need one).
4. Four levels of `decode_high_1` × 3 (LH/HL/HH detail bands at each doubling
   resolution, 16→32→64→128→256 for a 256×256 texture) — order-1 context-adaptive
   prediction plus the literal/zero-run-length entropy scheme.
5. Each level's LL+LH+HL+HH combine via `_wavelet_2d` (row-pass then column-pass
   inverse lifting) into the next level's LL, cascading up to the full-resolution plane.
6. `read_escapes` consumes a final row-mask scan (a pure encoder-side speed hint for the
   last-level row transform — see "row_mask" below; not needed for correctness here).

## Debugging methodology: compile the real reference decoder

Early attempts to verify this port used only internal self-consistency checks (hand-built
encoders round-tripping through the decoder, structural write-tracking, etc.) — these all
passed while the decoder was still subtly wrong, because a self-consistent round-trip
test using *your own* (buggy) encoding convention on both sides proves nothing about
matching the *true* encoder. The bugs below were only found by getting real ground truth:

1. Fetched every header `granny_bink0_compression.cpp` and `granny_compression_tools.cpp`
   need (`granny_types.h`, `rrCore.h`, `granny_pixel_layout.h`, ~20 more) directly from
   the same GitHub repo — all exist there unmodified.
2. Compiled them with MinGW-w64 g++ (`-DBUILDING_GRANNY_STATIC=1`), with a ~20-line test
   harness (`test_binktc0.cpp`) providing minimal `CallAllocateCallback`/
   `CallDeallocateCallback`/`rrlog2` stubs (matching the real declarations' `extern "C"`
   linkage exactly) to satisfy the linker outside the full Granny runtime.
3. Extracted a real texture's raw compressed bytes
   (`Textures[0].Images[].MIPLevels[].Pixels`) to a file, fed it to the compiled
   reference decoder, and diffed its output against the Python port at every
   granularity: raw `decode_low`/`decode_high_1` band output, post-wavelet
   per-level reconstruction, and the final full plane.
4. For the deepest bug (see #4 below), added live instrumentation *inside* a debug copy
   of the reference source (`granny_bink0_compression_dbg.cpp`) — printing internal
   `ArithBits`/`Arith` state at an exact call index — since the bug was invisible in any
   output comparison and only showed up in the arithmetic coder's internal `totals[]`
   bookkeeping several thousand symbols before it finally produced a wrong decoded value.

This ground-truth-diffing approach is dramatically more reliable than internal
consistency checks for anything involving a stateful, adaptive entropy coder — a wrong
assumption about the coder's internals can stay silently "consistent" for a very long
time before it finally produces a visibly wrong symbol.

## Bugs found (in the order they were found)

### 1. Wrong algorithm family entirely (`bink1_decode.py`)

The Bink *video* codec (DCT + Huffman, port of `bink1_decode.py` + `bink_data.py`, ~700
lines) is a completely different codec family from BinkTC0 (wavelet + arithmetic coding).
This was the first, wrong hypothesis — kept as a dead end, not deleted, matching this
project's convention for `gr2_oodle1.py`. Do not extend it; it doesn't apply here.

### 2. `Arith.decompress_raw` ordering bug

`find_pos_from_count_and_add` in the C source bumps `totals[bin..15]` as *part of*
finding the symbol's position, **before** `ArithBitsRemove` is called (which then reads
the *already-updated* `totals[BINS-1]` as its scale argument). The original port bumped
totals *after* calling `_remove_symbol`, using a stale (off-by-one) scale value. Fixed by
reordering: bump totals first, then call `_remove_symbol`, then do the *separate* plain
`counts[pos] += 1` (which really does happen after, in both C and Python).

### 3. `decode_low`'s running predictor truncated too early

The C source's running predictor `prev` (a 32-bit accumulator) must **not** be truncated
to 16 bits between iterations — only the value actually *stored* into the output array
gets truncated (`out[pos] = s16(prev)`, but `prev` itself carries forward at full width).
The original port did `prev = s16(cur + prev)`, over-truncating every iteration. Only
manifests once accumulated values drift outside ±32768, which is why it looked fine on
small test cases.

### 4. `lits`/`zeros` escape values use plain `VarBits`, not arithmetic coding

Every escape in the codec resolves via `ArithBitsGetValue(ab, num)` (a uniform
arithmetic-coded decode) — **except** the literal-run-length and zero-run-length
contexts (`lits`/`zeros`, used only in `decode_high_1`'s outer loop), whose escapes
resolve via a **plain fixed-width `VarBitsGet`** (`LIT_LENGTH_BITS=6` /
`ZERO_LENGTH_BITS=8`) read from the *separate* VarBits stream. This is invisible from
reading the macro/header layer — only found by reading the raw `decode_high_1` C
function body line-by-line and noticing
`VarBitsGet(escaped, U32, *vb, LIT_LENGTH_BITS)` where every other escape site instead
calls `ArithBitsGetValue`. This was the fix that got the decoder from "crashes almost
immediately" to "decodes the first 66% of a detail band correctly."

### 5. Wavelet right-edge mirror formulas were swapped between low/high bands

`_mirror_low`/`_mirror_high`'s left-edge behavior (whole-sample symmetric about index 0,
no repeat: `l[-k] = l[k]`) was already correct. The **right edge** uses a *different*
convention for each band, and the two were swapped:

- **Low band**: half-sample symmetric about `n-1+0.5` — `l[n-1+k] = l[n-k]`, which
  *does* repeat the last sample (`l[n] = l[n-1]`, `l[n+1] = l[n-2]`, ...).
- **High band**: whole-sample symmetric about `n-1`, no repeat — `h[n-1+k] = h[n-1-k]`
  (`h[n] = h[n-2]`, `h[n+1] = h[n-3]`, ...).

Found by live-instrumenting the reference `iDWTrow`'s boundary "remnants" loop (the
`if (xlin == hin) { ...; next = -next; }` pointer-bounce trick) with prints of its
sliding-window buffer contents at the last few iterations, then matching those printed
values back to specific source-array indices. Guessing a single "symmetric" formula for
both edges/both bands (the original assumption) is wrong; the two edges and two bands
each need their own rule, verified against the real pointer arithmetic rather than
inferred from the lifting math alone.

### 6. The real root cause of the last remaining corruption: `totals[]` isn't two independent 16-bit lanes

This was the deepest and last bug, only found via live C-side state tracing (see
"Debugging methodology" above). `quick_increment_counts` in the C source packs the
16-entry `totals[]` prefix-sum array into adjacent `U16` pairs and updates them via a
**genuine 32-bit addition with carry propagation between the two halves**:
`((U32*)a->totals)[i] += amount`. The original port modeled this as two independent
16-bit lanes (`totals[even] += lo16(amount)`, `totals[odd] += hi16(amount)`), which is
only correct when the addition never overflows 16 bits within either half — true for the
common cases (`amount = 0x10001`/`0x20002`/`0x30003`, i.e. equal low/high halves, so it
doesn't matter whether the halves are independent or not) but **false** for
`decrement_counts` (used once per context, exactly when all of that context's unique
symbols have been seen and the escape code can finally be retired): its packed value is
deliberately asymmetric by exactly one (e.g. `0xFFF0FFF1` to remove a count of 15), and
the low half's underflow reliably carries into the high half. The *real*, carry-inclusive
effect turns out to be a uniform delta across the whole affected range (confirmed via a
live trace: `totals` before `[15,21,32,40,...,76,76,76,76]`, amount `0xFFF0FFF1`, totals
after `[0,6,17,25,...,61,61,61,61]` — every single entry moved by exactly -15, not the
alternating -15/-16 the two-independent-lanes model predicts).

Fixed by rewriting the totals-update path (`Arith._quick_increment_counts`,
`_bump_totals_only`) to simulate the actual packed 32-bit add with carry — pack the pair,
add the full 32-bit amount, unpack — rather than updating each half independently.

**A regression introduced while fixing #6**: the plain "+1 per decoded symbol" suffix
bump must be called with the *packed* constant `0x10001` (matching the C source's literal
in its `do_0_bin`/`do_2_bin`/... fallthrough labels), not a bare `1` — passing `1` only
bumps the low half of whichever pair it lands in, silently corrupting `totals[]` into a
non-monotonic array (`[10, 7, 10, 7, ...]` was the actual observed symptom) that then
produces out-of-range positions in the bin search a few symbols later. Caught immediately
by `decode_low` throwing `IndexError` once the packed-add rewrite landed, since it has a
much smaller alphabet than the per-pixel contexts and hits the corrupted state almost
immediately.

## Verification status

Confirmed **bit-exact** against the compiled reference decoder, for
`WereRat.MODEL.GR2`'s diffuse texture (256×256, `Encoding=3`):

- All 4 wavelet levels' raw `decode_low`/`decode_high_1` band output: exact match.
- Full per-level wavelet reconstruction (all 4 levels): exact match.
- Full Y, U, and V planes end-to-end: **0/65536 mismatches each**.
- Total bytes consumed across all three `plane_decode` calls: exactly matches the
  packet's actual byte length (13300/13300) — confirms no header/offset drift either.
- `decode_binktc0()` (the full public entry point, including the color transform) runs
  end-to-end without error and produces correctly-sized output.

Not yet separately verified: a second texture (to rule out anything specific to this
one file's data distribution — e.g. it happens to hit the `decrement_counts` bug, but a
flatter texture might not exercise that path at all and could still hide a different
bug), and visual/numeric confirmation of the final RGB output against the reference's
own converted RGB (only the pre-color-transform Y/U/V planes were diffed).
