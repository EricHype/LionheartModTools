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
partial `.mdl16` notes). Where the two appeared to disagree, the wiki turned out to be
right and our reading of the binary wrong -- see "Corrections to earlier versions of this
document" below.

Code: `mdl16_format.py`.

## What's proven, in order of confidence

1. **`decode_icon()`** -- read any real shipped icon into plain RGBA pixels, decoding
   row-by-row from the on-disk offset table exactly as the engine does.
2. **`recolor_icon_in_place()`** -- recolor an existing icon (same shape, new palette)
   by transforming only the stored color values in its existing RLE stream, leaving
   every opcode/run boundary byte-identical to the source. **Confirmed correct
   in-game** (shipped in `mods/great-healing-potion/`: a gold-recolored variant of the
   real "Extra Healing" potion flask).
3. **`encode_icon_rle16()` / `build_icon_file()` / `verify_icon()`** -- build a
   brand-new icon (new shape and dimensions, not just recolored) from scratch. This
   failed for a long time; the cause is now found and fixed (see "The per-row offset
   table" below). Gate: decode → re-encode → re-parse under the engine's own algorithm
   is exact on **264/264** vanilla inventory icons, and 69 of those re-encode to the
   same buffer size as the original.
4. **`encode_icon_raw()`** -- the uncompressed 16bpp mode. Structurally valid but
   **confirmed to crash the game**; no shipped asset uses it. Kept for reference only.

### Corrections to earlier versions of this document

Three long-standing conclusions recorded here were **wrong**, and all three had the same
root cause -- a decoder that walked buffer 1 as one continuous stream from byte 0:

- *"Runs freely cross row boundaries"* -- **false.** Rows are strictly opcode-aligned.
- *"Item icons have no leading size DWORD and no per-row lookup table"* -- **false.**
  Both exist, in every file. The community wiki at `lionheart.eowyn.cz` described this
  correctly all along; the contradicting measurement was an artifact of the same bug.
- *"The `ShortSwordSpecial` top-row artifact is unexplained"* -- **solved.** See below.

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
36..   for each populated buffer, in order (1, 2, 3, 4, 5):
         u32 LE  this buffer's own declared size (counted INSIDE that size)
         ...     row 0's opcodes, row 1's opcodes, ... row (height-1)'s
         u32 LE  table[height] -- row offsets, NOT counted in the declared size
       then 8 trailing zero bytes at EOF
```

Verified across all 264 vanilla inventory icons:
`data_offset + sum(buffer_sizes) + (populated_buffers * height * 4) + 8 == file length`,
and `buf1[0:4] == buffer_sizes[0]` in every one. 262 files populate buffers 1/4/5; two
(`Deed Silver Mine`, `Lava Troll Hide`) populate buffer 1 alone -- those two are the
right envelope donors for a from-scratch icon, and are what `build_icon_file()` expects.

Only buffer 1 (main color plane) and, for real assets, buffers 4+5 (a secondary
highlight/overlay plane) are populated; buffers 2/3 are unused in every sample seen.
**Buffers 4+5 must be preserved when patching an existing file** -- zeroing their bytes
while leaving their sizes declared shifts every following offset and crashed the game
outright on opening the inventory screen. When only buffer 1 needs to change, carry
buffers 4/5 (and their row tables) forward unchanged, which is what a recolor needs
anyway. A from-scratch icon should instead declare them empty, as the two vanilla
buffer-1-only icons do.

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

Buffer 4's on-disk layout is the same as buffer 1's: its own `u32` size prefix, its rows,
then its own `u32 table[height]`. It begins after buffer 1's data **plus buffer 1's row
table** -- an earlier attempt to read it forgot the table, landed on those `u32` offsets,
and concluded (wrongly) that buffer 4 "decodes to nothing". `recolor_icon_in_place` still
only touches buffer 1; extending it to buffer 4 is now mechanical but has not been needed.

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

**Runs never cross a row boundary.** Every row starts a fresh opcode and encodes exactly
`width` pixels. This is a hard requirement of the format, not a stylistic preference --
see the next section. (An earlier version of this document claimed the opposite, citing
a skip-run apparently spanning 2.7 rows; that observation came from a decoder walking the
stream continuously from byte 0, which misparsed the leading size DWORD and put
everything after it out of phase.)

## The per-row offset table (this is what blocked from-scratch icons; SOLVED)

Every populated buffer is followed on disk by `height` `u32` values. `table[y]` is the
byte offset, **relative to the start of that buffer**, of row `y`'s first opcode.
`table[0]` is always `4`, because the buffer opens with a `u32` holding its own declared
size. This is not a redundant cache -- it is the only thing that tells the engine where a
row begins, and a from-scratch icon without it crashes the game on opening inventory.

### Why the earlier investigation missed it

`FUN_0055d0a0` (the loader) allocates `height*4` bytes and fills them with `row*width` in
a visible loop. That loop is **dead initialization**: the very next statement hands the
same pointer to the stream-read helper and overwrites it from the file.

```c
*(void **)(in_ECX + 0x18) = pvVar3;                    // row table field
FUN_00553fe0(pvVar3, (uint)*(ushort *)(in_ECX + 0x14) << 2);   // read height*4 bytes
```

`FUN_00553fe0` is the same helper used to read the 36-byte header and each buffer's data.
Both of the loader's branches read the on-disk table; the computed `row*width` values are
never used. An earlier reading of this function stopped at the loop, concluded the table
was computed rather than loaded, and therefore concluded the on-disk bytes must be
consumed by some *other*, unlocated function -- which sent the investigation looking for a
consumer that does not exist.

### How the engine actually reads a pixel

`GetColorAt` (`0x0055ec80`), RLE-16bpp branch (`in_ECX[0xf] & 6 == 4`):

```c
iVar4  = (**(code **)(*in_ECX + 0xd4))();              // the row table
pbVar7 = (byte *)(*(int *)(iVar4 + y * 4) + in_ECX[3]);  // table[y] + buffer base
uVar3  = 0;                                             // x-counter reset to ZERO
do { /* walk opcodes */ } while (uVar5 < width);
```

It seeks to `table[y]`, resets the x-counter, and walks until it has covered `width`
pixels. Two consequences, and they are the whole ballgame:

1. **Rows are strictly opcode-aligned.** A run that crossed a row boundary would leave the
   next row starting mid-run, at the wrong x.
2. **`table[y]` must be byte-exact.** Every previous attempt generated a continuous stream
   and then tried to *reconstruct* offsets into it statistically. Even the best formula
   found (5/264 exact) still pointed into the middle of runs for most rows -- which is
   exactly why the old symptom was "the first few rows look right, then it degrades", and
   why a measurably better formula produced no visible change.

### Verification

`decode_row()` implements the loop above. Applied to all 264 vanilla inventory icons,
starting each row at `table[y]`: every row decodes to exactly `width` pixels and consumes
exactly `table[y+1] - table[y]` bytes. **264/264, zero failures.** The generation side
(`encode_icon_rle16` → `build_icon_file` → `verify_icon`) round-trips all 264 exactly.

There is no formula. The table is a literal index, and the encoder simply records offsets
as it emits rows.

### The `ShortSwordSpecial` top-row artifact, explained

Recoloring rows 0-1 of the `ShortSword` icons used to produce rainbow noise, a black bar,
or wide streaks depending on what those rows were changed *to*, with "leave them
byte-identical" as the only known fix. Root cause: **that band is not art.** It is the
4-byte size prefix being misparsed as opcodes by the old continuous decoder. For
`ShortSwordSpecial` the prefix is `69 0a 00 00`; `0x69` reads as a 41-pixel literal-run,
manufacturing 31 and 20 phantom "opaque pixels" in rows 0-1 that the correct decode shows
as empty. And `recolor_icon_in_place` was **overwriting the size DWORD** (verified:
`690a0000` → `69341234`), corrupting the buffer's declared length.

Both are fixed: the decoder and the recolorer now walk each row from `table[y]`, so
neither can touch the prefix or drift out of phase. The "pass rows 0-1 through unchanged"
workaround used when recoloring these icons is obsolete.

**Why this only broke some icons.** Whether the misparse was harmless came down to the low
byte of the buffer size. The healing potions are 3055 bytes -> `EF 0B 00 00`; `0xEF` has
bit 7 set, so it reads as a 1-byte skip-run of 111 pixels, then `0x0B` reads as an 11-pixel
repeat-run whose color bytes are `00 00` -- a transform that maps 0 to 0 leaves them alone,
and the walker lands on byte 4, back in phase, having done no damage. (That phantom
"111-pixel skip-run crossing 2.7 rows" is the exact observation the old document cited as
proof that runs cross row boundaries.) `ShortSwordSpecial` is 2665 bytes -> `69 0A 00 00`;
`0x69` reads as a 41-pixel *literal* run, so the walker rewrote 41 real color values at the
wrong offsets and never recovered. Same bug, silent on one file and destructive on another,
purely by arithmetic coincidence. All five icons this project has already shipped happen to
fall on the harmless side -- re-verified with `verify_icon()`.

## Building a new icon from scratch

```python
import mdl16_format as M
donor = open(".../Deed Silver Mine.mdl16", "rb").read()   # a buffer-1-only vanilla icon
out   = M.build_icon_file(donor, width, height, rows, hotspot_x, hotspot_y)
M.verify_icon(out)        # raises on anything the engine would choke on
```

`rows` is `height` lists of `width` `(r, g, b, a)` tuples; `a < 128` means transparent.
Colors quantize to RGB565 (≤8/255 per-channel error). Opaque pixels that quantize to
exactly `0` are nudged to `1`, since `0` is the format's transparency sentinel.

The pixel data lives inside a serialized object-graph envelope this module does not
synthesize, so `build_icon_file` keeps a real file's envelope and replaces everything from
the magic byte on. The donor's embedded model-path string does not matter -- the game
locates the file by its filesystem path. Use a **buffer-1-only** donor so the envelope
isn't describing buffers 4/5 the new file won't have; `build_icon_file` enforces this.

Run `verify_icon()` before deploying. It re-parses with the engine's own algorithm and
checks the size prefix, `table[0] == 4`, that each row consumes exactly its declared byte
span, and whole-file size accounting.

## Useful Ghidra addresses (in `Lionheart.exe`)

- `0x006364b0` -- `CStandAloneFrame_Load` (named during this investigation), the
  class's virtual `Load` method, reached only via vtable dispatch (`0x006e0cc0` base,
  slot 71 / offset `0x11c`).
- `0x0055d0a0` -- the raw header/buffer reader called from `Load`. Populates the 36-byte
  header fields and, per populated buffer, reads the buffer's data and then its
  `height*4`-byte row table straight from the stream. **Read past the visible
  `table[row]=row*width` loop**: the very next statement overwrites that array from the
  file via `FUN_00553fe0`. Misreading this cost an entire investigation -- see "Why the
  earlier investigation missed it" above.
- `0x00553fe0` -- the stream-read helper (`dest, byte_count`). Used for the 36-byte
  header, each buffer's data, and each buffer's row table.
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
- `0x00706438` -- string `"~CCharacterInventoryWindow"`, referenced only from
  `FUN_00509ea0` (that class's destructor). Real, confirmed inventory window class.
  Its own vtable (`0x006b27b8`, set first in the destructor) turned out to be a
  **widely-shared generic base** used by dozens of unrelated classes across the whole
  engine, not useful for isolating inventory-specific behavior on its own.
- `0x0050bfc0` -- `CCharacterInventoryWindow`'s constructor/setup method (confirmed via
  a literal `"Close Inventory Button"` string reference inside it). Builds the
  equipment-slot widgets one at a time via repeated calls to `FUN_0050cd70(this, x, y,
  w, h, name_string, flag)` -- e.g. `(0x38, 0x17f, 0x22, 0x20, "Weapon", 0)` for the
  weapon slot. The confirmed starting point for how an equipped item's icon reaches the
  screen.
- `0x0060aea0` -- the scale-to-fit icon copy function (source icon, slot width, slot
  height, destination field) called from the equipment slot's "set displayed item"
  method (`FUN_0050cf20`). Reads the source's width/height via trivial field-offset
  getters, computes an aspect-fit scale, and constructs a fresh raw-pixel-buffer
  destination (`FUN_0055c730`, vtable `0x006d09a8`; `FUN_0055CD70` at vtable offset
  `0x7c` zeroes it). This chain was traced looking for whatever consumed the row table;
  that turned out to be `GetColorAt` itself, so the trace is not needed -- recorded only
  so it isn't re-walked.
