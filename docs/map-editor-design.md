# A GUI map editor for Lionheart — design notes

Status: **phase 1 complete** — every item below is implemented in `mapedit.py` over
`mapedit_core.py`, with `zax_render.py` (phases 0/0.5) still available as a headless
renderer. This records why a
visual map editor is now tractable, what it should and shouldn't try to do, and the order
to build it in.

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

## Terrain — SOLVED

`Plasma Ground=CPlasmaTileMap`. Ground now renders correctly, confirmed against an
in-game screenshot. This section is the reference; the investigation that produced it,
including two wrong turns worth not repeating, is at the end.

### The model

```
grid              (Width/64 + 1) x (Height/64 + 1) vertices, 64 world units apart
index(vertex)   = Elevations byte * Num Textures // 256
index(pixel)    = bilinear blend of the tile's four corner indices
texel           = textures[round(index(pixel))] sampled at (x % 128, y % 128)
light(pixel)    = bilinear blend of the tile's four corner Light Overlay RGB
out             = clamp(texel * light / 128)
```

The two data layers, per vertex:

| layer | bytes/vertex | meaning |
|---|---|---|
| `Elevations Row N` | 1 | **texture index** (see below), 0-255 |
| `Light Overlay Row N` | 3 | per-vertex RGB light; 128,128,128 is neutral |

A third layer, `Fog Of War N Row`, deserialises but is runtime state, not authored art.

**"Elevations" is a misnomer** — the byte is the ground-texture selector, not a height.
Proven from the binary: the deserialiser reads those rows straight into the plane at
object offset `0x2c40`

```
0F AF 8E B8 2D 00 00    IMUL ECX, [ESI+0x2db8]     ; count = grid_w * grid_h
51 6A 00 6A 00          PUSH count, 0, 0
8D 8E 40 2C 00 00       LEA  ECX, [ESI+0x2c40]     ; <- the selector plane
E8 7C 4E F7 FF          CALL 0x0055ec50
```

and `FUN_005ed3e0` samples that same plane (vtable `+0xf4`) to choose each tile's
texture. Nothing in the class treats it as a height.

**Blending across four corners is not optional.** `FUN_005ed990` reads a tile's four
corner values and interpolates across the tile, so the index varies per *pixel*.
Rendering one texture per cell makes any mapping look like blocky noise regardless of
whether the mapping is right.

### The texture art

All 217 files under `Cache/Textures/**.frm16` are **raw uncompressed 16bpp RGB565**,
128x128 (one is 32x32): `flags=0x40` — mode bits `0x40 & 6 == 0` (raw), depth bits `0x40`
(16bpp). Buffer 1 is exactly `width * height * 2` bytes; no RLE, no row table, no opcode
grammar. `mdl16_format.decode_icon()` handles this mode as of phase 0.5.

Each has a `Textures/<name>.TXT` sidecar (`CGroundTextureFrame`) with `Damage`,
`Damage Type`, `Surface Type` — gameplay properties, irrelevant to rendering.

Texture lists are grouped families with numbered variants (`grnd3, grnd3_1, grnd3_2`,
`RethrGrass2..6`), the classic anti-tiling pattern. `Num Textures` runs 0-28 across
shipped maps; only 25 maps use exactly 1.

### The one inferred step

`index = elevation * Num Textures // 256` is a **stand-in, not read from the binary**.

The engine indexes a 256-entry table of 28-byte records at object offset `0x1040`
(confirmed by arithmetic: `0x2c40 - 0x1040 = 256 * 0x1c`, i.e. the table runs exactly up
to the planes). Field `+0x00` is the texture, `+0x18` a flag. What *populates* those 256
slots was never found — `Texture N` parsing writes into a different 4-byte-stride list at
`0x1038`. So the scaling above stands in for whatever expands the declared textures
across the slots.

It produces output matching the screenshot's character. If ground boundaries ever look
consistently offset from the real game, this is the thing to fix.

### `CPlasmaTileMap` object layout

Class size `0x2dfc`, vtable `0x006da568`, descriptor `DAT_00807ca4`.

| offset | contents |
|---|---|
| `0x1038` | texture list (count via vtable `+0x88`, item N via `+0x04`) |
| `0x103c` | `Blending` float, default 0.25 |
| `0x1040` | 256 x 28-byte texture-entry table (population unknown) |
| `0x2c40` | **texture-selector plane** (fed by `Elevations Row N`) |
| `0x2c80` `0x2cc0` `0x2d00` `0x2d40` | further `CStandAloneFrame` data planes |
| `0x2d80` `0x2d81` | fog-enabled / lighting-enabled flags |
| `0x2dac` `0x2db0` | tile size in pixels |
| `0x2db4` `0x2db8` | grid cols / rows |
| `0x2dcc` | composed-tile cache |
| `0x2dd0` | per-cell 12-byte state array |
| `0x2dec` | destination surface |

### The render pipeline

```
FUN_005a88f0 / FUN_005a9540   world draw
  -> FUN_005ea350             ground pass (dispatches on DAT_00711168)
    -> FUN_005ea3f0           clip to visible tiles, loop
      -> FUN_005ed730         per-tile, backed by the cache at 0x2dcc
         -> FUN_005ed3e0      compose tile (cache miss only)
            fast path         all four corners agree -> blit one texture
            -> FUN_005ed990   slow path: four-corner blend (the common case)
  -> FUN_005ebb70             lighting + fog, over the textured result
    -> FUN_005eba30           per-tile shading: flat if the four corner colours
                              match (0x808080 neutral), else Gouraud
      -> FUN_005ebdd0         fog overlay, gated on the 0x2d81 flag
        -> FUN_005ebf10       fixed-point span rasteriser
```

The constructor (`FUN_005e8c40`) builds a 64K-entry RGB565 colour-grading LUT at
`DAT_007e7c88` (constants `DAT_00715248` / `DAT_0071524c`) that the rasteriser consumes.
Tiles are composed once and cached, which is why the composer sits behind a cache-miss
branch rather than running every frame.

### Two wrong turns, and why

Both cost real time and both are easy to re-enter.

**1. "Light Overlay might be a blend index."** It is 3 bytes/vertex, which invites the
guess. It is vertex colour.

**2. "Elevation is not the texture index" — asserted, wrongly, on a bad test.** Rendering
`elev * n // 256` one-texture-per-cell produced blocky noise, and that was recorded as
disproving the hypothesis. The mapping was right; the *fill method* was wrong, because
the engine blends four corners per tile. The correct conclusion from that evidence was
"this test is invalid", not "this hypothesis is dead".

What made it seductive: plotting the elevation grid collapsed to texture *families*
(`grnd` vs `RethrGrass`) produced coherent regions matching real map features — but a
two-bucket plot looks convincing for almost any mapping, and it hid that the
within-family index is scattered cell to cell.

The general lesson, and the reason this is written down: when a test refutes a
hypothesis, check that the test exercises the mechanism the hypothesis describes. Here
the blending detail had already been discovered and written into this very document two
commits before it was connected back to the failed test.

### Authoring ground (done once, for Test Pocket)

Because the elevation byte *is* the texture index, authoring ground means writing
elevation bytes — no separate paint layer exists. The recipe, as used on
`mods/test-pocket/`:

1. Declare the textures light-to-dark in `Texture 0..N-1`. **Order matters**: adjacent
   indices are what a blend passes *through*, so a light base at 0 and a worn variant at
   2 get a soft edge for free via index 1. Ordering by name instead gives hard seams.
2. Write each vertex's byte as the centre of its index band, `i * (256//N) + (256//N)//2`,
   so blending between neighbours lands cleanly rather than on a band boundary.
3. Keep the row lengths identical — one hex byte pair per grid vertex,
   `(Width/64 + 1)` per row.

Two traps hit while doing this:

- **Pick textures by measured luminance, not by name.** The Rethgorad palette is
  uniformly dark and warm: `grnd1` ("dirt") is lum 28 and renders essentially black,
  while `grnd5` is the brightest ground in the set at lum 103. `RethrGrass5` is the only
  genuinely green one (hue 126); the other `RethrGrass*` are brown. A first pass chose by
  name and produced a black ring around the arena.
- **Clear inherited junk.** Test Pocket carried an 18-cell patch of elevation 142 from
  the scratch template. Invisible at `Num Textures=1` (everything maps to index 0), it
  would have appeared as a stray texture patch the moment more textures were declared.

The script is kept at `scratchpad/author_ground.py` in the session that produced it; it
is short enough to rewrite from this description.

### Ground truth for future changes

`bugs/Screen Shot 05.TGA` — Gate District main gate, 800x600 RLE 24-bit TGA (the game's
own screenshot format; `Screen Shot NN.TGA` files land in the game directory).

Compare against the same crop of a full-scale render:

```
python zax_render.py "<data>/Levels/1 Barcelona/Gate District.zax" out.png
# then crop world x1500-2500, y1900-2650  (Main Gate is at 2150,2401)
```

`Test Pocket.zax` is the other useful reference: `Num Textures=1`, so its render is exact
and isolates the renderer from any texture-selection question.

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

- **Terrain *editing*.** Rendering it is solved and implemented (see "Terrain" above); authoring
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

1. **What populates the 256-entry texture table at `0x1040`** — the one inferred step in
   an otherwise-confirmed terrain model. See "Terrain / The one inferred step".
2. Fence A's negative Y hotspot — does the convention hold for every sprite, or is there
   a second case? Phase 0 rendered two maps correctly without hitting it, since neither
   uses that asset, so it remains open but is evidently not common.
3. Does anything read `Rendering Height` / `Rendering Height Float`? Every scenery entity
   examined has both at 0.
4. `CUnderConstructionLayerPart` (15 instances in Gate District) and `CRenderablePolygon`
   (8) are unexamined. Probably editor leftovers, worth confirming before an editor
   silently drops or mangles them.
5. Is the tiling-vector snap worth deriving automatically from shipped maps at startup, or
   should it be a small hand-curated table? Automatic derivation is what found the correct
   vectors originally, but it needs the corpus filtered to exclude work-in-progress maps.
