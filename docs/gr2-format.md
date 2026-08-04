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

## What's broken: `gr2_oodle1.py` — wrong algorithm, do not extend it

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
                   -> FUN_1000df50 (0x1000df50)  -- NOT a flat ranges[]/weights[] linear scan
                                                     like nwn2mdk. This is an indexed/tree
                                                     search: 196 lines of cascading 8-way
                                                     conditionals comparing against
                                                     param_1[0]..param_1[7], each guarded by a
                                                     shift amount at param_1[0x13]. Almost
                                                     certainly a Fenwick tree / binary indexed
                                                     tree for O(log n) cumulative-frequency
                                                     lookup (nwn2mdk's O(n) linear scan was
                                                     apparently a simplification specific to
                                                     that build/reverse-engineering effort, not
                                                     the general Granny scheme). NOT YET PORTED.
                   -> FUN_1000e390 (0x1000e390)  -- periodic rebuild (triggered when a counter
                                                     exceeds 0x3fff), 105+ lines of what looks
                                                     like heap/tree rebalancing. NOT YET PORTED.
                   -> FUN_1000d920 (0x1000d920)  -- cumulative-frequency update: a
                                                     switch(uVar3>>1) with fallthrough cases
                                                     0-7 updating param_1[0..7] -- classic
                                                     partial Fenwick/BIT update pattern.
                                                     Small, mechanical, straightforward to port.
                   -> FUN_1000d890 (0x1000d890)  -- bucket-width calculator, small/mechanical.
                -> FUN_1000d780 (0x1000d780)  -- Decode_Commit-equivalent: combines decode+
                                                  commit in one call via 64-bit multiply/divide
                                                  against state fields param_1[4],[5],[6].
                   -> FUN_1000d520 (0x1000d520)  -- the actual bit-level range-coder
                                                     commit/renormalize. Uses an XOR-interval
                                                     comparison (`uVar2 ^ uVar7`) rather than
                                                     nwn2mdk's simple numer/denom division, with
                                                     THREE renormalization granularities
                                                     (byte, then nibble, then bit), each refilling
                                                     from the compressed stream and pushing bits
                                                     into an accumulator via a 256-byte lookup
                                                     table at DAT_100292cc (looks like a nibble-
                                                     reversal or similar bit-twiddling table --
                                                     its actual byte contents haven't been
                                                     dumped yet). This is the crux of the real
                                                     algorithm and the highest-risk piece to
                                                     port correctly. NOT YET PORTED.
Small/mechanical helpers already understood (safe to port quickly when the time comes):
  FUN_1000d7f0, FUN_1000d7d0 -- buffer-size calculators (simple arithmetic, no state)
  FUN_10019370 -- memset
  FUN_1001c340 -- memcpy (backref copy, already implemented correctly in gr2_oodle1.py)
```

## Next steps to actually finish this

1. Reconstruct the real struct layout behind the `int*`/`ushort*` params Ghidra left
   untyped (e.g. what `param_1[0]`..`param_1[0x1c]+` in `FUN_1000df50`/`FUN_1000e390`
   actually represent) — apply a Ghidra structure definition to make the decompilation
   readable before transcribing it, rather than working from raw offsets.
2. Dump the actual bytes of the `DAT_100292cc` lookup table (256 bytes, referenced from
   `FUN_1000d520`) via `mcp__ReVa__get-data` or `read-memory` — needed for a bit-exact port.
3. Port, in order (each is a prerequisite for testing the next): `FUN_1000d920`/
   `FUN_1000d890` (mechanical) → `FUN_1000d520` (the real decoder core — this is where
   correctness actually lives) → `FUN_1000df50` + `FUN_1000e390` (the indexed frequency
   structure) → wire it into a new `FUN_1001c080`/`FUN_1001c1f0`-equivalent replacing
   `gr2_oodle1.py`'s `Dictionary`/`_decompress_block`.
4. Validate the same way this session did for the container format: decompressed sector
   byte-length must match `decompressed_length` exactly, AND (unlike this session's early
   mistake) actually inspect the decoded *content* for plausibility (readable ASCII field
   names, sane float values) before trusting it — a length match alone is not sufficient
   evidence of correctness, as this session's `gr2_oodle1.py` detour demonstrated.
5. Test file: `WereRat.MODEL.GR2`, sector 0 (`compression_type=1`, `compressed_length=10084`,
   `decompressed_length=29448`, `oodle_stop_0=oodle_stop_1=26008`, `data_offset=8692`,
   `fixup_offset=352`, `fixup_size=695`) is the reference case used throughout this
   investigation — its type sector root should decode starting near offset 19472 with a
   small, valid `type_id` (1-22), which is the concrete, checkable sign that decompression
   is finally correct end-to-end.

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
