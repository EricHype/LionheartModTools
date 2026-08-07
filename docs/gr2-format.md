# GR2 (Granny3D) model format — SOLVED

Lionheart's `.gr2` character model/animation files (real Granny3D meshes, not sprites)
can now be read end-to-end: container format, real decompression, and the self-describing
element tree all work. **Every one of the 1968 `.gr2` files shipped with the game loads
successfully** (`scripts/validate_gr2.py`, see "Validation" below). Content correctness was
verified in depth against
`Resources/Models3D/Enemies/Wererats/Models/Wererat/WereRat.MODEL.GR2`: it decodes to the
genuine model data — exporter info (`'Granny Standard Exporter, SDK version 2.1.0.3'`),
original source paths (`'C:\Icewind Art\Monsters\WereRat\Anims\Final\WereRat_Model.max'`
— this asset was originally built with Icewind Dale-engine tooling, reused for
Lionheart), a real 441-field skeleton with actual bone names (`Bip01`, `Bip01 Pelvis`,
`Bip01 Tail1`..`Tail4`, `Bip01 R Thigh`, finger bones, etc.), a vertex buffer with real
bone-weighted vertices, and a 3099-entry triangle index buffer.

## How to use it

`gr2_format.py`'s `decompress_sector` now calls the real decompressor
(`gr2_granny_decompress.granny_decompress`) directly, so the whole pipeline works as a
single call:

```
python gr2_format.py <file.gr2>
```

dumps header/file_info/sector info and the full element tree. Or from Python:

```python
import gr2_format as gf

gf_file = gf.GrannyFile.load_from_file("SomeModel.gr2")
gf.dump_elements(gf_file.root_elements)
```

## `gr2_format.py` — container format

Header → file_info → sector table → fixup/pointer tables → self-describing element tree.
Modeled on `opengr2-rs` (github.com/NoFr1ends/opengr2-rs). Validated against
`opengr2-rs`'s own test fixtures (`prova.gr2`, `test1.gr2` — reproduces their test
assertions exactly) and against real Lionheart files. The element tree walker needs no
Lionheart-specific knowledge — field names and structure come from the file itself.

## `gr2_granny_decompress.py` — the real decompressor

A from-scratch Python port of Lionheart's actual `granny2.dll` decompression algorithm,
traced via Ghidra disassembly of the DLL shipped with the game (32-bit x86,
`C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader\granny2.dll`,
imported into a Ghidra project as `/granny2.dll`). It is **not** related to `nwn2mdk` or
`opengr2-c`'s "Oodle1" algorithm (see "dead end" section below) — Rad Game Tools changed
the coder's internals between the `granny2.dll` build Neverwinter Nights 2 shipped with
and the one Lionheart shipped with, so that algorithm was never viable here.

It's a carryless (Schindler-style) range coder: `low`/`high` track the current 31-bit
coding sub-interval, `value` tracks the reconstructed stream position within it, and all
three renormalize together (shifting in fresh, bit-reversed bytes) via three
granularities (byte/nibble/bit) plus a separate underflow ("E3 mapping") loop. The
per-symbol frequency model (`Window`) is a 16-block **prefix-sum array** (`tree[i]` =
cumulative weight through block `i`) searched via binary search and updated via a
suffix-range add (`Window._add`, port of `FUN_1000d920`) — not a Fenwick/ancestor-path
tree, despite superficially looking like one. `Dictionary` (port of `FUN_1001c080`) has
one literal-byte window (not four, no `pos%4` indexing), 65 backref-size windows
(indices 0-64, matching `backref_size+1`), and exactly **two** backref-offset windows
whose values combine as `offset = hi*4 + lo + 1` — nwn2mdk's three-window
`(hi<<10)+(mid<<2)+lo+1` scheme does not apply to this build.

Key call-chain (addresses in `granny2.dll`, still importable/re-analyzable at
`/granny2.dll` in the Ghidra project):

```
FUN_1001c670 (top-level 3-phase loop)     -> granny_decompress()
  FUN_1001c080 (per-phase window-set init) -> Dictionary.__init__()
  FUN_1001c1f0 (per-block decode step)     -> decompress_block()
    FUN_1000de50 (window try-decode)       -> Window.try_decode()
      FUN_1000df50 (block search)          -> Window._search()
      FUN_1000e390 + FUN_1000ddf0 (rebuild) -> Window._rebuild()
      FUN_1000d920 (prefix-sum update)      -> Window._add()
      FUN_1000d890 (block-width picker)     -> Window._pick_shift()
    FUN_1000d780 (decode_commit)            -> decode_raw() / inline in try_decode()
      FUN_1000d520 (bit-level renormalize)  -> Decoder.decode_commit()
```

### Bugs found and fixed during the port (kept here as a debugging-methodology record)

Getting this bit-exact took several rounds of "run it, notice it's still wrong, find a
concrete reason why" — worth keeping as a record since the failure modes were subtle and
each one taught something about how to debug this class of algorithm:

1. **Missing init-time weight bump.** `FUN_1000d820` (window init) ends with
   `FUN_1000d920(window, 0, 0x30003)` — add 3 (packed into both 16-bit halves of the
   delta) to slot 0. Without it, every window starts at `weight_total=0`, causing a
   `ZeroDivisionError` on the first decode.
2. **Wrong tree-update model.** The 15-node array is a prefix-sum array (confirmed by
   `FUN_1000ddf0`'s explicit `tree[i] = tree[i-1] + block_total[i]`), so a weight change
   needs a *suffix-range* update (`tree[block_index..14]`), not the ancestor-path update
   a textbook Fenwick tree would need. Fixed by implementing `Window._add()` as a direct
   port of `FUN_1000d920`'s `switch`-with-fallthrough.
3. **Wrong window count/shape (the big one).** The port was originally modeled
   structurally on nwn2mdk: four `pos%4`-indexed literal windows, and three windows
   (`lowbit`/`highbit`/`midbit_windows[highbit]`) combining as
   `offset = (hi<<10)+(mid<<2)+lo+1` for backref distances. Neither matches the real
   code. Found by reading `FUN_1001c1f0`'s raw disassembly (not just its decompilation)
   and noticing the backref memcpy's source-address computation only subtracts *two*
   register-derived values, not three — meaning only two `FUN_1000de50` calls determine
   the offset, not three. This was **the actual root cause** of the corruption; the two
   fixes above were real but insufficient on their own. Also discovered in the same pass:
   there is exactly one literal-byte window (no positional indexing at all), and every
   window in this Dictionary is created with `FUN_1000d820`'s `param_2` argument fixed at
   0, which (per that function's own logic) makes "max value" and "count cap" the *same*
   value everywhere — a distinction nwn2mdk's API has that this code doesn't.
4. **Validation technique that actually worked**: cross-checking suspect components
   against independent from-scratch references settled things pure reasoning couldn't.
   A "bit-only" decoder (renormalizing strictly one bit at a time, no byte/nibble
   shortcuts) matching the optimized decoder exactly across 30 blocks ruled out the
   renormalization core. A naive O(n) linear scan over `Window.weights[]` matching
   `Window._search()`'s result on every call ruled out the tree/search logic. Both
   pointed at the *glue code* (`decompress_block`) as the remaining suspect, which is
   where bug #3 actually was.
5. **Lesson reconfirmed**: a length match is not a correctness signal (this was already
   learned once this session with the wrong nwn2mdk-based algorithm, then had to be
   relearned — both buggy versions of this *correct* algorithm also produced right-length,
   wrong-content output before the real bug was found). The only trustworthy check is
   content plausibility: byte-value distribution, ASCII field names, and ultimately
   parsing the full element tree and confirming real, sensible structure (a monotonically
   increasing `StringOffsets` array was the first unambiguous proof of correctness).
6. **Null String pointers.** A `String` field (type_id 8) with no fixup entry is a
   legitimate null/absent string (e.g. an optional `Name` left blank), not an error.
   `opengr2-rs`'s own reference parser doesn't handle this either (it `.unwrap()`s and
   would panic) — found via the broad validation pass below, since `WereRat.MODEL.GR2`
   never happened to exercise a null string.
7. **`VariantReference` (type_id 1) byte width.** `opengr2-rs` treats this as zero-width.
   Wrong: empirically confirmed (by cross-referencing the fixup table against a real
   `ANIMATION.GR2`'s `TransformTrack` array — consecutive struct `Name` fields turned out
   to be exactly 64 bytes apart, not 4) that it occupies 5 pointer-sized slots (20 bytes on
   32-bit). `WereRat.MODEL.GR2` never exercised a *populated* instance of this field (only
   ever saw it as an always-null trailing `ExtendedData`, where the byte-width bug is
   invisible), so this only surfaced once validation expanded to animation files. Originally
   left as an opaque raw blob rather than decoded; **solved later, see "`Curve2` / animation
   curve format" below** — it isn't opaque at all, and the 20-byte figure was just this
   game's fixed field layout, not a hardcoded constant.
8. **Rebuild's block 0 contribution.** `Window._rebuild()` (port of `FUN_1000e390` +
   `FUN_1000ddf0`) never added the halved `weights[0]` (the escape slot) into
   `block_totals[0]` — the real code does this as a direct assignment before the main
   `i=1..value_count` loop, not folded into it, and it's easy to miss on a first pass.
   Undetectable with small test files: `_rebuild` only runs once a window's `weight_total`
   exceeds `0x3fff`, which `WereRat.MODEL.GR2`'s sectors never reached. Found by extending
   the "naive linear scan over `Window.weights[]` must match `Window._search()`'s result"
   cross-check (bug #3's methodology) to run past a rebuild point instead of stopping after
   30 blocks — it diverged on the very first search call against a freshly-rebuilt window.

## Dead end: `gr2_oodle1.py` (nwn2mdk-derived) — do not use or extend

Kept in the repo only as a documented dead end. This is a faithful port of the "Oodle1"
decompressor from `opengr2-c`/`nwn2mdk` (github.com/Arbos/nwn2mdk, MPL-2.0, a Neverwinter
Nights 2 modding project). It runs without crashing and produces plausible-*looking*
output, but decodes real Lionheart sectors to ~94% repeated garbage bytes. Confirmed via
disassembly that Lionheart's `granny2.dll` build uses a structurally different range coder
than the one NWN2's older `granny2.dll` build used — this was never fixable by iterating
on the algorithm itself, only by re-deriving the real one from Lionheart's own DLL (which
`gr2_granny_decompress.py` now does).

## `Curve2` / animation curve format — SOLVED

`PositionCurve`/`OrientationCurve`/`ScaleShearCurve` (the `VariantReference`/`Curve2`
fields inside each bone's `TransformTrack` in an `ANIMATION.GR2` file) were originally
treated as an opaque 20-byte blob (bug #7 above) on the theory that Granny's real
`Curve2` wrapper supports many internal sub-formats (constant/Bezier/compressed
keyframes/etc.) that would need real reverse-engineering to decode, the same class of
problem the compression algorithm was. **That theory was wrong.** `type_id == 1` is not
special or opaque at all — like every other field in this format, its `type_info`
carries a real `children_offset` pointing at an actual field list, and (the one thing
that *is* different from a plain `type_id == 2` "reference") that field list is laid out
**inline** at the current `data_offset`, not behind a pointer indirection. Once
`parse_element` is called recursively against that inline field list instead of reading
5 raw pointer-sized slots, the structure falls straight out with no guessing:

```
{ Degree: i32, Knots: Real32[], Controls: Real32[] }
```

Confirmed identical across every `TransformTrack` in every animation file checked
(`Idle`, `Walk`, `Death`, `Attack01`, `GetHit`, across multiple `Shared Animations`
numbered variants and per-model animation files) — **this game's asset pipeline only
ever emits this one plain keyframe representation**, never Granny's compressed/constant/
Bezier `Curve2` sub-formats. So no format-dispatch-by-byte-value logic is needed here at
all; if some future file ever turns out to use a different `Curve2` sub-format, it would
simply show up as a different field list at `children_offset`, and `parse_element`
handles that generically already.

Semantics (inferred from real data, not from a spec — Granny's SDK docs for the exact
field meanings aren't public):
- `Knots`: one float per keyframe, the time values.
- `Controls`: flat float array, `dimension` values per keyframe where
  `dimension = len(Controls) / len(Knots)` (3 for `PositionCurve`/`ScaleShearCurve`, 4
  for `OrientationCurve` — a quaternion). Not stored as an explicit field; has to be
  derived from the two array lengths.
- `Degree`: interpolation degree between keyframes (0 seen most often in real data —
  step/near-constant; higher values presumably mean smoother interpolation, not
  confirmed in depth).
- **An empty curve (`len(Knots) == 0`) means "not animated on this track"** — the bind
  pose value from the model file's own `Skeleton.Bones[].Transform` is used unchanged
  for the whole clip. This matters a lot for editing: most `ScaleShearCurve`s on a
  typical skeleton are empty (scale is rarely animated), but `PositionCurve`/
  `OrientationCurve` are usually populated for any bone that actually moves during the
  clip.
- Every individual `Knots`/`Controls` float is parsed as a normal leaf `Element` with
  its own `data_sector_id`/`offset` (same mechanism already built for vertex/bone
  patching in `gltf_to_gr2.py`) — so editing keyframe values in place is already
  supported by existing infrastructure, no new patch machinery needed.

Discovered while investigating why patching `Skeleton.Bones[].Transform` to scale a
whole creature up broke skinning in-game (see `docs/gltf-roundtrip.md`) — animation
playback overrides bind-pose bone transforms with unscaled `PositionCurve` data every
frame, which is what made that approach fail chaotically rather than just imprecisely.

## Validation

`scripts/validate_gr2.py` batch-loads every `.gr2` file under a directory tree and reports
pass/fail, grouped by failure kind. Run it as `python scripts/validate_gr2.py` (defaults to
the game's `data/Resources` folder; `--limit N` to cap the file count, `--verbose` for full
tracebacks per failure). Starting from a single validated file (`WereRat.MODEL.GR2`), a
first 30-file sample immediately surfaced bugs #6 and #7 above (String/VariantReference),
and a 400-file sample surfaced bug #8 (rebuild) — every `MODEL.GR2` passed throughout, only
`ANIMATION.GR2` files (which exercise `TransformTrack`/`VariantReference` structure and, in
larger files, the rebuild threshold) exposed the remaining gaps. After fixing all three:
**1968/1968 — every `.gr2` file shipped with the game loads successfully.**

Re-run after the `Curve2` fix above (which changed `VariantReference` from an 20-byte
opaque skip to a fully recursive parse of every keyframe float in every animation file):
still **1968/1968**, confirming no regression. Notably slower this time — about 24
minutes instead of near-instant — since animation curve data is now genuinely parsed
field-by-field instead of skipped; this is expected, not a performance bug.

## Not yet done

- No writer/encoder exists yet for the *container* — this is read-only in the sense that
  no matching *compressor* exists. Per earlier analysis, authoring new content likely
  doesn't need one, since the format supports uncompressed sectors natively
  (`compression_type=0`); the round-trip pipeline in `docs/gltf-roundtrip.md` already
  exploits exactly this to patch and repack real files.
- Understanding what the actual mesh/skeleton data *means* well enough to author fully
  new content (new characters, not just edits) is a separate, substantial project.
- `Curve2`'s `Degree` field's precise interpolation semantics beyond "0 = near-constant"
  aren't confirmed in depth (not yet needed for anything attempted so far).
