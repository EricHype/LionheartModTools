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

### The confirmed buffer 1/4/5 compositing algorithm (from decompiling `FUN_0055ec80`)

`FUN_0055ec80` is the real per-pixel color getter (see "Useful Ghidra addresses"
below) -- the actual function the game calls to answer "what color is the pixel at
(x, y)". Its RLE-16bpp branch (`in_ECX[0xf] & 6 == 4`), read in full, is:

1. Walk buffer 1's opcode stream (as documented above) to get a raw RGB565 value at
   (x, y). This is exactly the decode this module already implements.
2. **Only if that value is exactly 0** (buffer 1 transparent at this pixel), fall back
   to buffers 4/5:
   - Buffer 4 is walked using the identical 3-opcode grammar as buffer 1 -- logically a
     full second color plane.
   - Buffer 5 supplies a parallel per-pixel *alpha* byte, sharing buffer 4's run
     boundaries but storing 1 byte instead of a 2-byte color at each position.
   - The code first probes for buffer 5's alpha byte at (x, y). If it's 0, the pixel
     stays transparent (same as if buffers 4/5 didn't exist). If nonzero, it walks
     buffer 4 a second time to pull its own RGB565 color there, and the final pixel is
     buffer 4's color at buffer 5's alpha.
3. If buffer 1 was nonzero, buffers 4/5 are never consulted for that pixel at all.

So buffers 4/5 form a genuine second sprite layer -- its own shape, colors, and alpha
-- used to fill in wherever buffer 1 leaves gaps. That part is solid, straight from the
decompiled logic, not inferred from in-game behavior.

**What this does NOT yet explain**: buffer 4's *on-disk* bytes don't behave like buffer
1's do when walked the same way. Tested directly on `ShortSwordSpecial.mdl16`'s buffer 4
(277 bytes): decoding it with this module's existing continuous-stream walker (the same
one proven correct for buffer 1) terminates almost immediately and produces an
all-transparent result across the entire icon -- while the very same 277 bytes, read as
a plain array of `u32` values, form a suspiciously clean, monotonically increasing
sequence (4, 5, 6, 11, 16, 23, 32, 41, 52, 65, 78, 91, 106, 123, 140, 157, ...) that
looks far more like a row-offset table than opcode data. Buffer 1's own per-row offset
table is *computed at load time* by scanning its (plain, continuous) bytes -- the
working theory is buffer 4 might not follow that same pattern, and its on-disk bytes
might already, in the file, be something closer to a pre-built table rather than a
directly-walkable opcode stream, with the real per-pixel data organized differently
(possibly folded into buffer 5). **Not resolved.** Practical consequence: this module
does not attempt to recolor buffer 4 (see `recolor_icon_in_place`'s docstring) --
its original colors are left as-is wherever it's actually visible.

### Buffers 4/5 empirically confirmed INACTIVE for `ShortSwordSpecial.mdl16` -- the real artifact cause is still unknown

Found while recoloring `ShortSwordSpecial.mdl16` (source icon for the `ratsbane-sword`
mod's custom weapon art). All three vanilla `ShortSword` icon tiers (`ShortSword`,
`ShortSwordBetter`, `ShortSwordSpecial`) have a small band at the very top of buffer 1
(rows 0-1 of the decoded image) that decodes to a handful of chaotic, unrelated-looking
opaque colors, separated from the actual blade silhouette by several fully-transparent
rows -- easy to mistake for decode noise or leftover garbage, since it's small, sits
disconnected from the rest of the art, and blends into the vanilla palette well enough
that it's invisible in normal play.

Recoloring that band (via `recolor_icon_in_place`, same technique used successfully on
the three healing-potion icons) turned out to have real, *unpredictable* consequences,
each attempt producing a different visible defect in-game:

| buffer 1 rows 0-1 set to... | in-game result |
|---|---|
| recolored to the new hue (same treatment as the rest of the icon) | a band of rainbow-colored noise streaking out past the blade's silhouette |
| fully transparent (value 0) | a small solid black bar + dotted shape rendered at the same position |
| a flat, uniform fill of the new hue | the SAME bar shape, but much wider -- extending most of the way across the tooltip panel, well past the icon's own width |
| byte-identical to the source (untouched) | **clean -- no artifact at all** |

The leading theory during this investigation was that buffers 4/5 explained it (see
above). **That theory was tested directly and disproven**: decoding `ShortSwordSpecial`'s
actual buffer 4 (with the same walker later confirmed correct for buffer 1's own
sequential decode) produces zero visible content anywhere in the entire icon, rows 0-1
included -- so buffers 4/5 cannot be the source of the rainbow/bar/streak artifacts
above; the compositing algorithm never even reaches them for this file (buffer 1 was
nonzero at nearly every one of those pixels, and where it *was* zero, buffer 4 had
nothing to contribute). **The real cause of those three defects remains unexplained.**
What *is* proven is the practical fix, empirically: leave whatever pixels fall in that
band byte-for-byte identical to the source (`recolor_icon_in_place`'s `color_transform`
callback can take an optional `pixel_index` second argument for exactly this -- pass
through `v` unchanged for positions in the affected band, transform normally everywhere
else).

**If picking this up again**: with buffers 4/5 ruled out, the remaining candidates are
(a) something else in the tooltip/comparison UI reading garbage or being sensitive to
this specific icon's exact byte values in a way unrelated to `CStandAloneFrame`
rendering at all, or (b) a genuine bug/edge case in the RLE16 decode itself for this
specific opcode pattern that only manifests for certain color values, not others. Worth
checking whether every real weapon icon has this same disconnected top band (all three
`ShortSword` tiers do) and, if so, what it's actually *for* -- it's very small, always
present, and (now confirmed) not what buffers 4/5's highlight system is for, so its
purpose is still unknown. Resolving buffer 4's on-disk layout (previous section) is a
separate, likely more valuable thread if picking this up again, since it blocks fully
recoloring any icon that has an active (non-empty) buffer 4/5 highlight -- this
particular sword just doesn't happen to have one.

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

**If picking this up again**: `FUN_0055ec80` (the per-pixel color getter) has now been
read in full -- see the buffer 1/4/5 compositing section above -- and it confirmed the
opcode grammar but, being a per-pixel query function, doesn't reveal the encoder's
run-selection heuristic (it only ever answers "what color is pixel (x,y)", never writes
anything). The actual bulk blit/render function used by the inventory UI to draw a whole
icon is still not located. It's not reached by any direct (non-virtual) call to
`FUN_0055ec80` -- the only direct callers found are a tiny caching wrapper
(`0x0061ad80`, part of a `CStandAloneFrame`-derived class at vtable `0x006de7e0`, itself
only ever constructed as what looks like an unrelated global singleton, not a per-icon
instance -- a dead end, see below) -- so the real blit almost certainly calls it (or the
base class's own version) through a vtable pointer, which doesn't show up in a static
call-site search. Finding it would need either locating where inventory-slot UI code
constructs/holds `CStandAloneFrame`-family objects specifically for item icons (as
opposed to the singleton chased in this session), or a virtual-call-aware search for the
vtable offset `0xf0` (GetColorAt's slot, 60) across candidate caller functions.

## Useful Ghidra addresses (in `Lionheart.exe`)

- `0x006364b0` -- `CStandAloneFrame_Load` (named during this investigation), the
  class's virtual `Load` method, reached only via vtable dispatch (`0x006e0cc0` base,
  slot 71 / offset `0x11c`).
- `0x0055d0a0` -- the raw header/buffer reader called from `Load`. Populates the
  36-byte header fields and allocates+reads each buffer, plus builds the
  `table[row]=row*width` array discussed above.
- `0x0055ec80` -- confirmed real per-pixel color decoder (`GetColorAt`-equivalent,
  vtable slot 60 / offset `0xf0`): walks the RLE opcode stream and, on a match, expands
  RGB565 via lookup tables (`DAT_00702600` for 5-bit, `DAT_00702688` for 6-bit
  channels) before calling `FUN_005e8320(r,g,b,a)`. Read in full this session (not just
  disassembled) -- this is the function the "confirmed buffer 1/4/5 compositing
  algorithm" section above comes from. Takes `(x, y)` as `param_1, param_2` and the
  object (`this`) as `in_ECX`; `in_ECX[0xf] & 6` selects the mode (0/2/4 = raw/palette/
  RLE16, matching the header `flags` field), `in_ECX[3]`/`in_ECX[0xd]`/`in_ECX[0xb]` are
  buffer 1/4/5's data-base pointers respectively, `in_ECX[0xe]`/`in_ECX[0xc]` their
  row-offset tables.
- `0x0055f2a0`, `0x00560f90` -- hit-test/opacity-test helpers with the same opcode
  walking logic but no real color extraction (only ever return `0`/`0xffffffff`/a
  delegate call) -- a genuine dead end hit early in this investigation, worth knowing
  about so it isn't re-explored.
- `0x006e0cc0` -- `CStandAloneFrame`'s vtable base. Read directly via `read-memory`
  (76 slots, `0x006e0cc0`-`0x006e0ecc`): slot 0 = destructor-ish, slots 58-64 cluster
  around the per-pixel accessors (58/59 unknown, 60 = GetColorAt, 61 = hit-test #1
  (`0x0055f2a0`), 62 = unknown, 63 = shared/unknown, 64 = unknown), 71 = `Load`.
- `0x006de7e0` -- a SECOND vtable, belonging to a class that derives from (or shares
  the same virtual layout as) `CStandAloneFrame`: identical to the base in most slots,
  but overrides exactly 58/59/60/61/62/64/71 with thin caching wrappers (each checks a
  global "already cached?" flag `DAT_0080a680`, otherwise calls `FUN_00619880()` then
  falls through to the base class's own implementation via a direct, non-virtual call).
  Chased this looking for the bulk icon blit; turned out to be a dead end -- the objects
  using this vtable are constructed inside a shared static-initializer function
  (`FUN_006191a0`) alongside several unrelated global objects, and the one write to its
  tracked pointer (`DAT_0080a6a0`) found is a shutdown/teardown routine
  (`FUN_00618fd0`), not a per-icon draw call. Most likely some other always-present
  singleton UI element (cursor, loading spinner, etc.) that happens to reuse
  `CStandAloneFrame`'s interface for an unrelated purpose -- not inventory icons.
