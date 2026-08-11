# A GUI map editor for Lionheart — design notes

Status: **phase 0 built and validated** (`zax_render.py`); the editor itself is still
design only. This records why a visual map editor is now
tractable, what it should and shouldn't try to do, and the order to build it in.

Context: the retail game's own editor was stripped from the build (see the
`lionheart-modding` skill — the F6 handler is gone, not hidden), so hand-editing `.zax`
text is currently the only way to author a map. That works, but placing scenery by
computing tiling vectors and clearance radii in a Python REPL is slow and error-prone;
three bad layouts were caught by assertions during the Test Pocket arena work, and one
(a chest placed inside a rock) only surfaced in-game.

## Why this is feasible now

Four things are already true, measured rather than assumed:

**1. Byte-identical round-trip on real maps.** All 14 shipped `.zax` files tested parse
and reserialize byte-for-byte through `resource_format.py`, including Gate District at
2.3MB / 1139 entities in 0.06s. An editor can load a large map, change one entity, and
write it back with provably zero collateral damage. This is the property the whole idea
rests on.

**2. World coordinates are screen pixels, 1:1.** There is no isometric projection to
reverse-engineer — the iso look is baked into pre-rendered sprite art. Confirmed
empirically: the chest-inside-rock bug was predicted correctly by comparing a sprite's
half-width in pixels (169) against world distance (166 units). Placement math in world
units and pixel math over sprites are the same math.

**3. Sprites decode fast enough for live preview.** 17ms per environment sprite; a whole
map's distinct models (215 for Gate District) is ~4s one-time, then cacheable. 4787
environment sprites exist in total. This only became possible when the `.mdl16` per-row
offset table was cracked — before that `decode_icon()` was wrong on every file in the
game (see `mdl16-icon-format.md`).

**4. The validation rules already exist.** The clearance and corner-closure checks
written as throwaway assertions during the arena work are exactly what the GUI should
surface continuously.

## The rendering model

```
screen_x = entity.Position X - sprite.hotspot_x      (hotspot as stored in the file)
screen_y = entity.Position Y - sprite.hotspot_y
draw order: ascending Position Y  (painter's algorithm)
```

**Hotspot convention** — the stored hotspot is roughly the sprite's centre for
ground-standing objects, which is what makes the subtraction above the natural reading:

| sprite | size | hotspot | as fraction of w/h |
|---|---|---|---|
| Tree1 A | 229x191 | (131, 100) | 0.57, 0.52 |
| Rock B | 311x494 | (195, 252) | 0.63, 0.51 |
| Chest2 | 74x74 | (46, 39) | 0.62, 0.53 |
| Wall 01 A | 130x174 | (65, 137) | 0.50, 0.79 |
| Fence A | 157x117 | (99, **-23**) | 0.63, **-0.20** |

Note the loader *negates* the hotspot into memory (`*(short *)(in_ECX + 10) = -local_20`
in `FUN_0055d0a0`), and `GetColorAt` then subtracts the in-memory value. Using the
in-memory (negated) form for blitting would place the anchor outside the sprite entirely
for Rock B, so **as-stored is the correct form for rendering**. Fence A's negative Y is
unexplained and is the one loose end.

**Depth sorting** is by Y, per the container class name `CSortList2D`. Model
`Properties.txt` files carry a `Sorting Y` offset, but it is almost never used — of the
10 `Properties.txt` files in the entire game, 8 have `Sorting Y=0` and only two are
nonzero (23 and 51). Treat it as an optional per-model bias, not a core mechanism.

**This whole model needs exactly one confirmation test** before building UI on it: render
a shipped map and compare against an in-game screenshot of the same area. That is the
purpose of phase 0.

## Phase 0 — read-only renderer (do this first)

A single command: `.zax` in, PNG out. No UI.

It exercises the entire risky core — sprite decode, hotspot convention, depth sort,
coordinate mapping — against ground truth that already exists (screenshots of shipped
maps). If the PNG matches the game, everything after it is UI work over a proven engine.
If it doesn't, that is discovered in an afternoon rather than after building an editor.

It is also independently useful: reviewing a map without launching the game, and
diffing two versions of a map visually.

## Phase 0 result

Done. `zax_render.py` renders a `.zax` to PNG using stdlib only (`zlib`/`struct`), reusing
`resource_format.py` and `mdl16_format.py`. Gate District: 607 renderable entities, 215
distinct sprites, 0 skipped, 4.5s at `--scale 0.25`.

**The rendering model in this document is confirmed correct** -- and independently
verified against ground truth since: a full-scale render of `Test Pocket.zax`, which uses
`Num Textures=1` and so has no procedural-texture confound, matches an in-game screenshot
of the same arena closely (same flagstone ground, same wall layout, same prop placement).
The output is also recognisably Gate District — continuous fortified walls, the gatehouse, red-roofed buildings, roads
curving between them, with sensible occlusion. So `pos - hotspot_as_stored` and
painter's-algorithm-by-Y are right, and phase 1 can be built on them.

Terrain is absent as designed, which reads as flat dark background.

One real bug surfaced: `find_header`'s plausibility bound rejected every sprite over
512px — 321 of them, including the Cathedral (1707x1709) and Main Gate. Fixed by keying
on buffer 1's size prefix rather than dimensions; see that function's docstring for the
two approaches that were tried and rejected. `Fence A`'s negative-Y hotspot remains open
(neither test map uses that asset).

## Terrain

`Plasma Ground=CPlasmaTileMap`. Three earlier claims in this document were wrong and are
corrected here; terrain is considerably more tractable than first assessed.

### The texture art is the simplest format in the game

All 217 files under `Cache/Textures/**.frm16` are **raw uncompressed 16bpp RGB565**,
128x128 (one is 32x32): `flags=0x40`, meaning mode bits `0x40 & 6 == 0` (raw) and depth
bits `0x40` (16bpp). Buffer 1 is exactly `width * height * 2` bytes — no RLE, no row
table, no opcode grammar. `decode_icon()` rejects them today only because it implements
the RLE-16bpp path and raises `NotImplementedError` on everything else.

Each texture has a `Textures/<name>.TXT` sidecar (`CGroundTextureFrame`) carrying
`Damage`, `Damage Type` and `Surface Type` — gameplay properties, nothing needed for
rendering.

### There are exactly two data layers

Per 64-unit grid vertex, so `Width/64 + 1` columns by `Height/64 + 1` rows:

| layer | bytes per vertex | meaning |
|---|---|---|
| `Elevations Row N` | 1 | height, 0-255 |
| `Light Overlay Row N` | **3** | per-vertex RGB light; 128,128,128 is neutral |

`Light Overlay` being 3 bytes wide is easy to misread as something else — an earlier
draft of this document guessed it might be a texture blend index. It is vertex colour.

Also corrected: **192 of 201 shipped maps have real elevation variation** (full 0-255
range), not the handful first assumed. Terrain is not mostly flat.

### The one genuine unknown

**There is no per-cell texture index anywhere in the structure** — verified on a
9-texture map, which still has only the two layers above. Yet maps declare up to 28
textures (`Num Textures` across shipped maps runs 0-28; only 25 maps use exactly 1). So
texture *selection* must be procedural, which is what the class name is telling us —
"plasma".

The texture *names* are the strongest clue. Gate District declares 21, and they are
grouped families with numbered variants:

```
grnd3, grnd3_1, grnd3_2      grnd1, grnd1_1, grnd1_2
grnd5, grnd5_1, grnd5_2      grnd4, grnd4_1, grnd4_2
grnd2, grnd2_1               RethrGrass2 .. RethrGrass6
```

That is the classic anti-tiling pattern: several near-identical variants of one ground
type, shuffled per tile to break up visible repetition, alongside genuinely different
ground types (dirt families vs grass). It fits "plasma" — variant choice is *generated*,
which is exactly why no per-cell index is authored in the file.

Two candidate mechanisms, both untested:

1. **Elevation bands select the family**, `Blending` softens transitions, and a hash or
   noise over tile coordinates picks the variant within a family. Elevation spanning the
   full 0-255 range across 192 maps is consistent with this.
2. Family assignment is also procedural (pure plasma/noise), and elevation is only
   height.

Against (1): Calle Perdida has 168 distinct elevation values for 9 textures, so elevation
is clearly not a direct index — at most a banded one.

### SOLVED: elevation is the index, blended between four corners

The elevation byte **is** the texture selector, and the earlier "disproven" verdict was a
flawed test, not a wrong hypothesis.

**Proof from the binary.** In the deserialiser the elevation rows are read straight into
the plane at object offset `0x2c40`:

```
0F AF 8E B8 2D 00 00    IMUL ECX, [ESI+0x2db8]     ; count = grid_w * grid_h
51 6A 00 6A 00          PUSH count, 0, 0
8D 8E 40 2C 00 00       LEA  ECX, [ESI+0x2c40]     ; <- the plane
E8 7C 4E F7 FF          CALL 0x0055ec50
```

and `FUN_005ed3e0` samples that same plane (`vtable +0xf4`) to choose a tile's texture.

**Why the first test failed.** The engine does not pick one texture per cell.
`FUN_005ed990` reads the tile's **four corner values** and interpolates across the tile,
so the index varies per pixel and regions melt into one another. Filling each 64px cell
flat with a single index makes *any* mapping look like blocky noise. The mapping was fine;
the fill method was wrong. Rendering the same crop with four-corner blending produces
smooth organic regions — plaza, grass, dirt, flagstone — matching the character of
`bugs/Screen Shot 05.TGA`.

**The implemented model**, now the renderer's default (`--texture-mode elevation`):

```
index(vertex)   = elevation_byte * Num Textures // 256
index(pixel)    = bilinear blend of the tile's four corner indices
texel           = textures[round(index(pixel))] sampled at (x % 128, y % 128)
out             = texel * light / 128        (light also bilinear, see above)
```

The scaling step is the one part still inferred rather than read out of the binary. The
engine indexes a 256-entry table of 28-byte records at object offset `0x1040` (confirmed
by arithmetic: `0x2c40 - 0x1040 = 256 * 0x1c`), and what populates that table was never
found — `Texture N` parsing writes into a separate 4-byte-stride list at `0x1038`. So
`* Num Textures // 256` is a stand-in for whatever expands the declared textures across
those 256 slots. It produces plausible output; it is not proven exact.

### What the Ghidra dig established

`CPlasmaTileMap` was traced in some depth. The texture-selection logic was **not** found,
but the surrounding structure is now mapped, which should make a future attempt much
cheaper.

**Object layout** (class size `0x2dfc`, vtable `0x006da568`, descriptor `DAT_00807ca4`):

| offset | contents |
|---|---|
| `0x1038` | texture list (count via vtable `+0x88`, item N via `+0x04`) |
| `0x103c` | `Blending` float, default 0.25 |
| `0x2c80` `0x2cc0` `0x2d00` `0x2d40` | four `CStandAloneFrame` data planes |
| `0x2d80` `0x2d81` | fog-enabled / lighting-enabled flags |
| `0x2dac` `0x2db0` | tile size in pixels |
| `0x2db4` `0x2db8` | grid cols / rows |
| `0x2dec` | destination surface |

**Only three data layers deserialize** (confirmed in `FUN_005e9c90`): elevation at 1
byte/cell, light overlay at 3 bytes/cell, fog at 1 byte/cell. There is definitively no
texture-index plane in the file *or* in the loaded object.

**The tile pipeline that was found is lighting and fog, not texturing:**

```
FUN_005ebb70   tile loop over visible grid cells
  -> FUN_005eba30   per-tile lighting: fetch 4 corner colours; flat-shade if all equal
                    (0x808080 = neutral, skip), else Gouraud via FUN_005eac20
    -> FUN_005ebdd0 fog overlay, gated on the 0x2d81 flag; also 4-corner, with a
                    "uniform and < 0x40" early-out
      -> FUN_005ebf10 fixed-point span rasteriser, applies the colour LUT
```

The constructor (`FUN_005e8c40`) builds a 64K-entry RGB565 colour-grading LUT at
`DAT_007e7c88` (saturation/contrast constants `DAT_00715248` / `DAT_0071524c`) which that
rasteriser consumes. All of this runs *over* ground that has already been textured
somewhere else. That "somewhere else" is the remaining gap.

Useful side effect: lighting is per-tile Gouraud between four corner colours, which is
what the renderer's bilinear light modulation already approximates. That part is right.

**Also disproven: the raw elevation byte is not a texture index either.** Herbalist map
declares `Num Textures=2` but has elevation values up to 61, and only 12% of Gate
District's cells hold a value below its texture count.

### The texturing pass, traced

Found it. The full chain, from the world renderer down:

```
FUN_005a88f0 / FUN_005a9540   world draw
  -> FUN_005ea350             ground pass  (dispatches on DAT_00711168)
    -> FUN_005ea3f0           clip to visible tiles, loop them
      -> FUN_005ed730         per-tile, with a CACHE at 0x2dcc
                              index: ((grid_rows * mode + tileY) * grid_cols + tileX)
         -> FUN_005ed3e0      compose tile (cache miss only)
            fast path: one texture, blitted directly
            -> FUN_005ed990   slow path: four-corner blend
  -> FUN_005ebb70             lighting + fog pass, over the textured result
```

**Tiles are cached**, composed once on demand and reused — which is why the composer is
reached through a cache-miss branch rather than called every frame.

**A fifth data plane exists at `0x2c40`**, and its per-vertex byte is the texture
selector. `FUN_005ed3e0`:

```c
idx = plane_0x2c40.get(tileX, tileY);            // vtable +0xf4
// fast path taken only if all four corners agree and the entry is simple:
if (entry[idx].f18 == 0 for all four corners && entry[idx].f00 equal for all four)
    blit entry[idx].texture;
else
    FUN_005ed990(...);                            // blend
```

**The entry table is 256 entries of 28 bytes at `0x1040`** — confirmed by arithmetic:
`0x2c40 - 0x1040 = 0x1C00 = 256 * 0x1c`, i.e. it runs exactly up to the planes. Field
`+0x00` is the texture, `+0x18` a flag. So the selector is a **byte indexing a 256-slot
table**, not a value scaled against `Num Textures`.

`FUN_005ed990` (the common case) reads the four corner bytes and calls
`FUN_0055c8b0(dx, dy, value)` at the tile's four corners, then interpolates across the
tile. So **each pixel gets a bilinearly blended index between its four corner values** —
which is precisely the smooth ground transition the screenshot shows, and why any
per-cell nearest-index approach looks blocky no matter what the mapping is.

### The one remaining unknown

**What fills plane `0x2c40`.** Only three planes deserialize from the file (elevation,
light, fog) but five are constructed, so at least one is computed at load. `FUN_005ee850`,
which runs immediately after deserialization, turned out to allocate the tile cache and a
per-cell 12-byte state array at `0x2dd0` — not the selector plane.

If `0x2c40` simply held the raw elevation byte, Gate District would index empty slots for
88% of its cells (21 textures, elevation values up to 255), so either the plane is derived
from elevation rather than equal to it, or the table is populated far more densely than
one slot per declared texture. Resolving that is the whole remaining question.

Leads not yet followed: what writes `0x2c40` (its setter is vtable `+0xf8`, matching the
plane writes seen in the field parser); `FUN_005ea790`, the alternate ground-pass branch
taken when `DAT_00711168` is set; and `FUN_005ede70`, the final blend call in the slow
path, which may reveal how an index maps to a texture sample.

### Where else to pick this up
1. **Whether the light overlay carries it.** It is 3 bytes/vertex and assumed pure RGB,
   but only the neutral value 128 was verified; a channel could be doing double duty.
2. Accepting single-texture ground indefinitely. For a placement editor this costs
   little, and a from-scratch map uses `Num Textures=1` where the render is already
   exact.

### What to build, and where to stop

1. **Add raw-16bpp decoding** to `mdl16_format.decode_icon()`. No unknowns.
2. **Tile `Texture 0`** across the canvas and modulate by the light overlay, bilinear
   between vertices. This alone replaces the flat background with real ground.
3. **Then one experiment**: render a multi-texture map under the elevation-band
   hypothesis and compare to an in-game screenshot. If the bands line up, terrain is
   solved. If not, stop — approximate ground is sufficient for a placement editor, where
   the point is spatial context rather than fidelity.

Worth keeping in perspective: a map authored from scratch should use `Num Textures=1`
anyway (the standing recommendation in the `lionheart-modding` skill, made while blending
was unknown). At one texture the procedural question does not arise and rendering is
exact. This work mainly improves *viewing shipped maps*.

## Phase 1 — entity placement

The 90% case. Everything a scenery pass needs:

- **Palette** of the 4787 environment sprites, searchable, grouped by directory
  (`Rethgorad/Town/...`, `Mountain/Inside/Walls/...`).
- **Place / drag / delete** entities, with the sprite drawn where it will actually land.
- **Snap to measured tiling vectors** for assets that tile — `Wall 01 A` at `(124,-7)`,
  `C` at `(10,88)`, etc. The vectors are derivable from shipped maps by finding collinear
  runs (method and caveats in the `lionheart-modding` skill). Assets that do *not* tile
  (the whole `Fence` set) should be marked as such in the palette so nobody tries to build
  a wall out of them again.
- **Property panel**: `Collideable`, `Half Height` / `Full Height`, `Visible`, `Name`,
  `Model`. A small, well-understood field set — copy the rest from a template entity.
- **Live validation overlay** — the real differentiator:
  - footprint circles from actual sprite dimensions
  - overlap warnings against other props, walls, and any entity that must stay reachable
  - corner-gap detection on wall runs
  - off-map coordinates
- **Export** writing only the `Tree List` entries that changed, leaving the rest of the
  file byte-identical.

## Explicitly out of scope for v1

- **Terrain *editing*.** Rendering it is now in scope (see "Terrain" below); authoring
  heightmaps and texture sets is not. Note the scratch template ships an inconsistent
  `Height` vs its actual row count, and a garbage `Blending` value.
- **Interaction zones.** `CFreeRangePoly` hover does not work in hand-authored maps — four
  construction variants were tried and none produced an interaction cursor. A GUI must not
  offer a tool whose output silently doesn't work. Place model-based doors instead. The
  untested lead is that cloned maps lack the `CWayPointsPolygon` entries every real map
  has (1-7 per map).
- **Quest / dialogue / AI scripting.** Different problem, well served by text editing.

## Architecture

The real fork is the UI layer. The backend is settled either way: reuse
`resource_format.py`, `mdl16_format.py`, and `modmanager.py` directly.

**Option A — PySide6 + `QGraphicsScene`.** Pan, zoom, z-ordering, rubber-band selection
and an undo stack come for free, and it handles thousands of items. Scene management is
the bulk of the work and Qt has already solved it. Cost: a heavy dependency in a project
that is currently pure-stdlib plus Pillow-free.

**Option B — local web app.** Flask/FastAPI backend, browser canvas frontend, sprites
served as PNG on demand. Richest widgets, no GUI toolkit to fight, trivially
cross-platform, easy to screenshot and share. Cost: client/server plumbing, and
hand-rolling the scene management Qt gives away.

**Recommendation: A**, for a single-user desktop tool that must stay responsive while
panning a 4096x960 canvas with a thousand sprites. Option B becomes more attractive if
sharing maps or running the tool elsewhere ever matters.

Either way, phase 0 has no UI and commits to neither.

## Gotchas the tool must surface

These are all documented in the `lionheart-modding` skill and all cost real debugging time:

- **New entities do not appear on a save that already visited the level.** Warn on export.
  This failure looks exactly like a broken edit.
- **`build` reads from `mods/installed/`, not the mod source** — the tool should run
  `install` then `build`, never `build` alone.
- **The loose `data\` mirror shadows `data.dat`** — handled by `modmanager build`, but
  anything bypassing it must sync too.
- **Don't build while the game is running.** `build` now keeps a completed archive and
  resumes, but the tool should just refuse and say so.
- **Line endings and encoding**: `.zax` is `latin-1`, and line endings vary per file.
  Always read/write bytes and preserve what was there.

## Open questions

1. Fence A's negative Y hotspot — does the convention above hold for every sprite, or is
   there a second case? Phase 0 answers this.
2. Does anything read `Rendering Height` / `Rendering Height Float`? Every scenery entity
   examined has both at 0.
3. `CUnderConstructionLayerPart` (15 instances in Gate District) and `CRenderablePolygon`
   (8) are unexamined. Probably editor leftovers, worth confirming before an editor
   silently drops or mangles them.
4. Is the tiling-vector snap worth deriving automatically from shipped maps at startup, or
   should it be a small hand-curated table? Automatic derivation is what found the correct
   vectors originally, but it needs the corpus filtered to exclude work-in-progress maps.
