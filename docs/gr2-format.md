# GR2 (Granny3D) model format — investigation checkpoint

Goal: read Lionheart's `.gr2` character model/animation files (real Granny3D meshes,
not sprites — see `Resources/Models3D/Enemies/Wererats/Models/Wererat/WereRat.MODEL.GR2`
for the file used throughout this investigation) well enough to eventually understand
and author new character content. This doc is a checkpoint — the container format is
fully working; decompression is not, and picking it back up needs real disassembly
work, not more guessing. Read this before spending more time on it.

## What's done and working: `gr2_format.py`

A from-scratch Python port of the GR2 container format (header → file_info → sector
table → fixup/pointer tables → self-describing element tree), modeled on
`opengr2-rs` (github.com/NoFr1ends/opengr2-rs, a generic/game-agnostic GR2 parser).
**Fully validated**: running it against `opengr2-rs`'s own bundled test fixtures
(`prova.gr2`, `test1.gr2`) reproduces their test assertions exactly (header size/format,
FileInfo total_size/crc32/sector_count/type_ref/root_ref, and a correctly-walked element
tree with real field names like `ArtToolInfo`, `ExporterName`, and actual mesh vertex
data — `Position`/`Normal`/`TextureCoordinates0` arrays). Also correctly parses the
header/file_info/sector table of the real `WereRat.MODEL.GR2` (byte-exact against
hand-verification done earlier in this investigation) and correctly decodes its one
*uncompressed* sector (sector 5, `compression_type=0`).

Run it directly: `python gr2_format.py <file.gr2>` dumps header/file_info/sector info and
the root element tree (see `dump_elements()` at the bottom of the file).

**This part needs no further work.** The element tree walker is fully generic/self-
describing (field names come from the file itself via the type sector) — once real
decompressed bytes are available for a sector, `parse_element()` already turns them into
named, typed values with zero additional Lionheart-specific work.

## Current status (second checkpoint): `gr2_granny_decompress.py` — close, not correct yet

A second, from-scratch port of the *real* algorithm (traced via Ghidra disassembly of
Lionheart's actual `granny2.dll`, not `nwn2mdk`) now exists in `gr2_granny_decompress.py`.
It runs to completion without crashing and produces the right *length* output, and two
real, confirmed bugs from the first attempt at this port are already fixed:

1. **Missing init-time weight bump.** `FUN_1000d820` (window init) ends with a call
   `FUN_1000d920(window, 0, 0x30003)` — a packed-delta Fenwick-style update meaning
   "add 3 to slot 0" (0x30003 = 3 packed into both 16-bit halves, matching the same
   packed-pair-add trick used everywhere in this code). Without it, every fresh window
   starts at `weight_total=0`, which causes a `ZeroDivisionError` on the very first
   decode. Fixed by having `Window.__init__` end with `self._add(0, 3)`.
2. **Wrong tree-update model.** The 15-node array searched in `FUN_1000df50` is a plain
   **prefix-sum array** (`tree[i]` = cumulative weight through block `i`), confirmed
   unambiguously by `FUN_1000ddf0`'s rebuild code (`tree[i] = tree[i-1] + block_total[i]`,
   an explicit running sum). Updating it after a block's weight changes therefore requires
   bumping a **suffix range** `tree[block_index..14]` (see `FUN_1000d920`'s
   `switch(idx>>1)` with intentional fallthrough through all remaining cases) — not an
   "ancestor path" the way a textbook Fenwick tree would. My first attempt at this
   (incrementing only the O(log n) nodes touched during the binary-search descent) was a
   plausible-looking guess that turned out to be mathematically wrong, and it eventually
   ran the search off the end of the weights array. Fixed by adding `Window._add()`
   (a direct port of `FUN_1000d920`) and having `Window._search()` call it for the found
   block once identified, instead of mutating nodes during descent.

**Still wrong**: tested against `WereRat.MODEL.GR2` sector 0, the corruption pattern
changed (was ~94% repeated `0xDD` with the *wrong* nwn2mdk-derived algorithm; is now 87%
repeated `0xFE` with this real-algorithm port) but the sector is still overwhelmingly one
repeated byte, and `type_ref` position 19472 still does not decode to a small, valid
`type_id`. Something is still desyncing the arithmetic decoder, almost certainly in the
bit-level renormalization core (`Decoder.decode_commit` in `gr2_granny_decompress.py`,
ported from `FUN_1000d520`) since that's the most intricate, least-checkable-by-static-
reasoning piece — three renormalization granularities (byte/nibble/bit) plus a separate
underflow ("E3 mapping") loop, all mutating shared decoder state.

**Where to pick this up:**
- Add tracing (a debug harness for exactly this already exists in this session's shell
  history / was written ad hoc — recreate it): monkeypatch `Decoder.decode_commit` to log
  `(val, err, max_val)`, the interval width immediately after narrowing but *before*
  renormalization, and the interval width and `decoder.pos` immediately after. Watch for
  whether the *byte/nibble/bit* renormalization loops are firing the right *number of
  times* relative to how far the pre-renorm width falls below the granularity thresholds
  (`0x7F800000`/`0x78000000`/`0x40000000`) — a loop that exits one iteration too early or
  late would under- or over-consume bits without crashing, exactly matching the observed
  symptom (decoder limps along self-consistently but drifts from the true bitstream).
- Specifically re-verify the **nibble-granularity block runs at most once** (it's an
  `if`, not a `while`, in the real disassembly — already implemented that way in
  `decode_commit`, but worth re-confirming against the raw disassembly at
  `0x1000d5cd`-ish, since this is an easy thing to mistranscribe as a loop).
- Re-verify the **decoder init sequence** (`Decoder.__init__`/`_init_value`) against raw
  disassembly one more time — it was derived carefully (see the resolved stack-offset
  ambiguity note below) but has never been independently cross-checked against a second
  source, unlike the container format which had `opengr2-rs`'s own test fixtures to
  validate against. There is no equivalent "known good" fixture for this real algorithm.
- Consider re-deriving `Window._rebuild()` (port of `FUN_1000e390`/`FUN_1000ddf0`) more
  rigorously — it was implemented as a *reasoned simplification*, not a line-by-line
  transcription, unlike everything else in this file. It's gated behind
  `weight_total > 0x3fff` so it likely isn't the cause of the *early* (byte ~1) corruption
  already observed, but it will matter once decode gets further and needs re-verification
  before being trusted.
- The Ghidra project (`/granny2.dll`) is still imported and analyzed; useful addresses for
  a fresh pass: `FUN_1000d520` (0x1000d520, decoder core), `Decoder.__init__`'s source at
  the top of `FUN_1001c670` (0x1001c670, lines ~22-40 in the decompilation — note Ghidra
  splits what is really one contiguous stack struct into separately-named locals
  `apuStack_28[3]`/`local_18`/`local_14`/`local_10`/`local_c`/`local_8`/`local_4`; get the
  disassembly with `includeDisassembly: true` to see the real `[ESP+N]` offsets and
  resolve this ambiguity, which is how the current init sequence was derived).

## Earlier, wrong attempt: `gr2_oodle1.py` — wrong algorithm entirely, do not extend it

This file is a faithful, carefully cross-checked port of the "Oodle1" decompressor from
`opengr2-c` (github.com/arves100/opengr2 in C) and `nwn2mdk`
(github.com/Arbos/nwn2mdk, MPL-2.0, the Neverwinter Nights 2 modding project both `opengr2-c`
and `opengr2-rs` cite as their source for this algorithm). It runs without crashing and
produces plausible-*looking* output (sensible block-size sequences), but the actual
decoded bytes are wrong — tested against `WereRat.MODEL.GR2` sector 0 (should contain
readable field names like "ArtToolInfo"), it produces ~94% repeated `0xDD` bytes with
zero readable ASCII anywhere. The corruption starts almost immediately (within the first
few decoded blocks), not just at a stream boundary, so it's a real algorithmic mismatch,
not a rounding/truncation bug.

**Root cause, confirmed via Ghidra disassembly of the real `granny2.dll` shipped with
Lionheart** (`C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the
Crusader\granny2.dll`, 32-bit x86, imported into the Ghidra project as `/granny2.dll`):
Lionheart's actual decoder (`FUN_1001c670` and its callees, traced below) is a
**different, more sophisticated range coder** than the one `nwn2mdk` reverse-engineered.
Rad Game Tools evidently changed the coder's internals between the `granny2.dll` build
NWN2 shipped with and the one Lionheart shipped with (different years, different SDK
versions) — so `gr2_oodle1.py`'s algorithm was never going to work here, regardless of
which on-disk `compression_type` it's applied to. (Side note, also confirmed via
disassembly: on-disk `compression_type` 1 and 2 both route through the *same* decoder
function in Lionheart's DLL — there is no separate "Oodle0 vs Oodle1" algorithm split;
that naming in `opengr2-rs`'s enum is misleading for this build.)

**Do not try to fix `gr2_oodle1.py` by tweaking it further.** It needs to be replaced by
a faithful port of the real algorithm below.

## The real algorithm — traced via Ghidra, not yet ported

Ghidra project: `/granny2.dll` (already imported + analyzed — `analyze-program` was run,
so functions/strings/xrefs are populated; no need to re-import). Call chain from the
public API down to the real decompressor:

```
_GrannyReadEntireFile@4 (0x10026900)
  -> FUN_10012370 (0x10012370)
    -> FUN_10012510 (0x10012510)
      -> FUN_10011ba0 (0x10011ba0)   -- per-section driver; reads SectorInfo.compression_type
                                          directly (offset 0 of the 44-byte/0x2c section struct,
                                          confirming the struct layout in gr2_format.py is right),
                                          calls FUN_10013750 when compression_type != 0
        -> FUN_10013750 (0x10013750)  -- also reachable directly as _GrannyDecompressData@28
             dispatch: type 0 -> FUN_10019400 (plain memcpy, NOT decompression-specific --
                                  it's a generic memcpy used 20+ places in the DLL)
                       type 1 -> FUN_1001c670 (the real decoder)
                       other  -> error "Unrecognized compression"
          -> FUN_1001c670 (0x1001c670)   -- top-level 3-phase loop, structurally matches
                                             nwn2mdk's gr2_decompress: 3x 12-byte TParameter
                                             blocks (36-byte prefix, same 9-bit/23-bit bitfield
                                             layout already confirmed bit-exact in gr2_oodle1.py's
                                             _parse_parameter), then iterates 3 phases bounded by
                                             stop0/stop1/decompressed_size.
             -> FUN_1001c080 (0x1001c080)  -- per-phase dictionary/window-set init
             -> FUN_1001c1f0 (0x1001c1f0)  -- per-block decode step (literal vs backref;
                                               backref copy confirmed to be FUN_1001c340, a
                                               plain memcpy with the same "fixed source, tiled
                                               forward" pattern already implemented correctly
                                               in gr2_oodle1.py's _decompress_block)
                -> FUN_1000de50 (0x1000de50)  -- weighted-window "try decode" (returns either
                                                  an already-known value, or a sentinel meaning
                                                  "decode a fresh value and store it" -- same
                                                  shape as nwn2mdk's try_decode, DIFFERENT
                                                  internal data structure, see below)
                   -> FUN_1000df50 (0x1000df50)  -- binary search over a 16-block prefix-sum
                                                     array (NOT a flat ranges[]/weights[]
                                                     linear scan like nwn2mdk, and NOT a
                                                     Fenwick tree despite superficially looking
                                                     like one -- see "current status" above).
                                                     Ported as Window._search().
                   -> FUN_1000e390 (0x1000e390)  -- periodic rebuild (triggered when
                                                     weight_total exceeds 0x3fff). Ported as
                                                     Window._rebuild(), but as a *reasoned
                                                     simplification* rather than a faithful
                                                     transcription -- needs re-verification
                                                     (see "current status" above).
                   -> FUN_1000d920 (0x1000d920)  -- prefix-sum suffix-range update (adds a
                                                     delta to one weight slot and propagates
                                                     it through tree[block_index..14]).
                                                     Ported as Window._add().
                   -> FUN_1000d890 (0x1000d890)  -- picks a block-width/shift so 16 blocks
                                                     roughly cover max_value+1 (note: called
                                                     with max_value+1, not max_value --
                                                     confirmed via disassembly). Ported as
                                                     Window._pick_shift().
                -> FUN_1000d780 (0x1000d780)  -- Decode_Commit-equivalent: combines decode+
                                                  commit in one call via 64-bit multiply/divide
                                                  against state fields param_1[4],[5],[6].
                                                  Ported as decode_raw() (for brand-new
                                                  symbols) and inline in Window.try_decode()
                                                  (for the weighted-window case).
                   -> FUN_1000d520 (0x1000d520)  -- the actual bit-level range-coder
                                                     commit/renormalize. Uses an XOR-interval
                                                     comparison (`uVar2 ^ uVar7`) rather than
                                                     nwn2mdk's simple numer/denom division, with
                                                     THREE renormalization granularities
                                                     (byte, then nibble, then bit), each refilling
                                                     from the compressed stream and pushing bits
                                                     into an accumulator via a 256-byte lookup
                                                     table at DAT_100292cc (confirmed to be
                                                     exactly a 4-bit reversal table -- computed
                                                     programmatically in the port, no need to
                                                     hardcode it). Ported as
                                                     Decoder.decode_commit() -- **this is the
                                                     prime suspect for the remaining bug**, see
                                                     "current status" above.
Small/mechanical helpers, ported and not suspected of any bugs:
  FUN_1000d7f0, FUN_1000d7d0 -- buffer-size calculators (unused directly in the Python port,
                                 since Python lists don't need pre-sized buffers)
  FUN_10019370 -- memset (unused directly, same reason)
  FUN_1001c340 -- memcpy (backref copy, ported into decompress_block())
```

## Next steps to actually finish this

See "Current status" above for the concrete next debugging steps (tracing renormalization
loop entry/exit counts, re-verifying the decoder init sequence and the rebuild function).
Once the decoder is believed fixed, validate the same way this session did for the
container format: decompressed sector byte-length must match `decompressed_length`
exactly, AND (unlike this session's early mistake with `gr2_oodle1.py`) actually inspect
the decoded *content* for plausibility (readable ASCII field names, sane float values)
before trusting it — a length match alone is not sufficient evidence of correctness.

Test file: `WereRat.MODEL.GR2`, sector 0 (`compression_type=1`, `compressed_length=10084`,
`decompressed_length=29448`, `oodle_stop_0=oodle_stop_1=26008`, `data_offset=8692`,
`fixup_offset=352`, `fixup_size=695`) is the reference case used throughout this
investigation — its type sector root should decode starting near offset 19472 with a
small, valid `type_id` (1-22), which is the concrete, checkable sign that decompression
is finally correct end-to-end. As of this checkpoint it decodes to a repeated `0xFE` byte
there, not a valid type_id.

## Ruled out / not worth retrying

- Calling `granny2.dll` directly via `ctypes` from Python: the DLL is 32-bit x86, this
  machine's Python is 64-bit, and a 64-bit process cannot load a 32-bit DLL. Would need a
  separate 32-bit Python install (not done; deprioritized in favor of the from-scratch
  port, since the user wants an independent implementation, not a runtime DLL dependency).
- Trusting `opengr2-c`'s choice to route on-disk `compression_type` 1 and 2 through the
  same decoder: this turned out to be *correct* (confirmed via disassembly — both really
  do hit the same function in Lionheart's DLL), so that was never the bug. The bug was
  trusting `nwn2mdk`'s specific algorithm implementation to be the same one Lionheart's
  `granny2.dll` build uses. It isn't.
