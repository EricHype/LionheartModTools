# The `.mdl16`/`.frm16` 2D sprite format (`CStandAloneFrame`)

Not related to `.gr2` (`docs/gr2-format.md`) despite the shared `.mdl16` extension --
character `.mdl16` files (see `docs/adding-a-new-character.md`) are a small manifest of
string paths to real `.gr2` assets; this is a completely different, binary-encoded 2D
sprite/icon format, backed by the game's `CStandAloneFrame` class. It backs both
inventory/UI icons (`.mdl16`, under `Cache/Models/...`) and UI icon caches (`.frm16`) --
same format, two extensions.

Reverse-engineered primarily from `Lionheart.exe`'s own decompilation (Ghidra), and
independently cross-checked against the fan wiki at `lionheart.eowyn.cz` (a long-running
Lionheart modding community reference covering `.zax`/`.way`/`.frm16`/`.seq16`, with
partial `.mdl16` notes) -- the two sources agree almost everywhere, which is itself
useful confirmation; see "Where the two sources disagreed" below for the one place they
didn't turn out to both apply.

Code: `mdl16_format.py`.

## What's proven, in order of confidence

1. **`decode_icon()`** -- read any real shipped icon into plain RGBA pixels. Fully
   proven, cross-validated against multiple real files by visual inspection.
2. **`recolor_icon_in_place()`** -- recolor an existing icon (same shape, new palette)
   by transforming only the stored color values in its existing RLE stream, leaving
   every opcode/run boundary byte-identical to the source. **Confirmed correct
   in-game** (shipped in `mods/great-healing-potion/`: a gold-recolored variant of the
   real "Extra Healing" potion flask). This is the production-ready way to give a new
   item distinct art today.
3. **`encode_icon_rle16()` / `encode_icon_raw()`** -- build a brand-new icon (new
   shape, not just recolored) from scratch. **Not proven working.** Both produce
   structurally valid files that round-trip correctly through this module's own
   decoder, but every attempt rendered visibly corrupted in-game across many iterations
   (see "Why 'build from scratch' doesn't work yet" below). Kept as a documented,
   honest dead end for whoever picks this up next, not something to build on blindly.

## File layout

The magic byte sits embedded inside a larger serialized object graph (the game's
generic reflection/cache format -- the same system used elsewhere for `.can`/`.zax`
class registration), not necessarily at file offset 0. Byte offsets below are relative
to that magic byte:

```
0      magic byte, always 0x32 ('2')
1      unknown byte, consistently 0x10 across every sample seen
2..3   hotspot X, i16 LE (stored negated relative to the in-memory field)
4..5   hotspot Y, i16 LE (stored negated relative to the in-memory field)
6..7   width,  u16 LE
8..9   height, u16 LE
10..11 unknown/reserved, always 0x0000 in every sample seen
12..15 flags, u32 LE -- bits 1-2 select compression mode (0=raw/uncompressed,
       2=RLE 8bpp palette, 4=RLE 16bpp -- the mode every real inventory icon
       uses); bits 5-8 (mask 0x1e0) select bit depth for the raw mode
       (0x20/0x40/0x80/0x100 = 8/16/24/32bpp)
16..35 five buffer sizes, u32 LE each (buffers 1-5)
36..   buffer 1's raw bytes, followed immediately by any other populated
       buffers in order (2, 3, 4, 5)
```

Only buffer 1 (main color plane) and, for real assets, buffers 4+5 (a secondary
highlight/overlay plane) are populated; buffers 2/3 are unused in every sample seen.
**Buffers 4+5 must be preserved when patching an existing file** -- zeroing them out
(as an early attempt did) crashed the game outright on opening the inventory screen,
with no error dialog. The exact mechanism was never pinned down (the function that
reads them, `FUN_0055ec80`, does null-check before touching them, so something else in
the real render path apparently doesn't), but the fix is simple: always carry the
original buffers 4/5 forward unchanged when only buffer 1 needs to change, which is
exactly what a recolor needs anyway.

## Buffer 1's RLE-16bpp opcode grammar (flags & 6 == 4)

One continuous stream of the following three opcode types, covering exactly
`width*height` pixels in row-major order. Confirmed two ways: independently by reading
the community wiki's description, and far more rigorously by editing only the 2-byte
color values inside a real shipped icon's *existing* opcodes (leaving every control
byte and run boundary untouched) and confirming it rendered correctly in-game --
that's a much stronger proof than a round-trip through our own decoder, since it
validates against the *real* renderer, not our reconstruction of it.

```
bit7 set        : skip-run,    bits0-6 = number of transparent pixels (1-127)
bit7=0, bit6=1  : literal-run, bits0-5 = number of distinct 16bpp pixels following (1-63)
bit7=0, bit6=0  : repeat-run,  bits0-5 = repetitions of the ONE 16bpp pixel following (1-63)
```

Each stored 16-bit pixel is RGB565 (bits 11-15 red, 5-10 green, 0-4 blue); value 0 means
"transparent" and is the one value that can't be distinguished from a legitimately
near-black opaque pixel -- when generating new pixel data, nudge any opaque color that
quantizes to exactly 0 to the nearest nonzero value.

Runs are **not** bounded by row width and are **not** padded to any fixed per-row byte
budget -- a single skip-run in a real file was seen spanning 111 pixels (2.7 rows) in
one continuous opcode. The loader does build an in-memory `table[row] = row*width`
array (`FUN_0055d0a0`), which looked at first like a per-row byte-offset table for
random access, but re-decoding a real file using that formula as a row-start byte
offset produced garbage -- that table is either used for something other than general
rendering, or not used by the main render path at all. Don't rely on it.

## Where the two sources (Ghidra vs. community wiki) disagreed

The community wiki describes each "layer" as pixel data followed by a leading 4-byte
size DWORD and a trailing per-row lookup table (LUT), sized `2*height` bytes. That
turned out to be **true for some other FRM16 use case (map textures/portraits/UI
elements) but not for item icons specifically**: directly inspecting a real item icon's
buffer 1 bytes showed its leading 4 bytes equal buffer 1's *total* length (the same
number already present in the outer header, just duplicated -- matching the wiki's own
aside that "the same number is already in the header, so its usefulness is unclear"),
and that continuous RLE decoding of everything after those 4 bytes consumes every
remaining byte exactly, leaving no room for any trailing LUT. Both the DWORD and the
LUT were implemented at various points during this investigation and neither improved
(and the DWORD attempt didn't hurt, being a redundant no-op; the wrong-sized LUT did
actively make things worse by inflating buffer 1's declared length). **Item icons: no
DWORD, no LUT needed** -- pure continuous RLE data is sufficient, matching what
`recolor_icon_in_place` and `decode_icon` both assume.

## Why "build from scratch" doesn't work yet

The opcode grammar above is independently confirmed correct (see the in-place-edit
proof). Yet every attempt at generating a *new* RLE stream from an arbitrary image --
various combinations of row-clean vs. continuous opcode boundaries, with and without
the repeat-run opcode, with and without a size DWORD/LUT -- rendered visibly corrupted
in-game (streaking, shearing, wrong colors leaking outside the art's silhouette),
despite every single attempt round-tripping perfectly through this module's own
decoder. That combination -- opcode semantics provably correct, self-consistent
round-trip, yet wrong in the real renderer -- points at the real encoder applying some
run-selection heuristic that determines *which* opcode to pick and *how long* to make
each run, which was never reverse engineered.

Direct evidence: comparing a real icon's actual opcode stream against
`encode_icon_rle16`'s output for the same underlying image (a 41x62 icon):

| | real file | our from-scratch encoder |
|---|---|---|
| total opcodes | 187 | 343 |
| skip-runs | 124 (avg len 9.8) | 66 (avg len 17.4) |
| repeat-runs | 1 (len 11) | 114 (avg len 2.5) |
| literal-runs | 62 (avg len 23.1) | 163 (avg len 6.8) |

The real encoder uses roughly half as many opcodes, with much longer, more efficient
runs, and the repeat-run opcode is essentially unused (once, in an entire image) --
while a straightforward greedy encoder (favor a repeat-run wherever 2+ consecutive
pixels match) uses it constantly, fragmenting what should be long literal or skip runs
into many short, choppy ones. Whatever the real encoder's actual selection logic is
(possibly something structural from the original art pipeline, like preserving
scanline boundaries from the source TGA, or a cost-based optimal-RLE choice), a naive
greedy encoder doesn't reproduce it, and the real in-game renderer -- unlike this
project's own decoder -- is apparently sensitive to that difference in some way that
was never isolated.

**If picking this up again**: the highest-value next step is probably finding the
*actual* render/blit function (still not located -- `FUN_0055ec80` and
`FUN_0055f2a0`/`FUN_00560f90` are per-pixel color-getter and hit-test helpers
respectively, not the bulk blit used by the inventory UI) rather than continuing to
guess at encoder heuristics from the outside.

## Useful Ghidra addresses (in `Lionheart.exe`)

- `0x006364b0` -- `CStandAloneFrame_Load` (named during this investigation), the
  class's virtual `Load` method, reached only via vtable dispatch (`0x006e0cc0` base,
  offset `0x11c`).
- `0x0055d0a0` -- the raw header/buffer reader called from `Load`. Populates the
  36-byte header fields and allocates+reads each buffer, plus builds the
  `table[row]=row*width` array discussed above.
- `0x0055ec80` -- confirmed real per-pixel color decoder (`GetColorAt`-equivalent):
  walks the RLE opcode stream and, on a match, expands RGB565 via lookup tables
  (`DAT_00702600` for 5-bit, `DAT_00702688` for 6-bit channels) before calling
  `FUN_005e8320(r,g,b,a)`. This is where the opcode grammar above was ultimately
  confirmed from disassembly (cross-checked against the community wiki afterward).
- `0x0055f2a0`, `0x00560f90` -- hit-test/opacity-test helpers with the same opcode
  walking logic but no real color extraction (only ever return `0`/`0xffffffff`/a
  delegate call) -- a genuine dead end hit early in this investigation, worth knowing
  about so it isn't re-explored.
- `0x006e0cc0` -- `CStandAloneFrame`'s vtable base (76 entries).
