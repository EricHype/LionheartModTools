# Adding a new character to the game

"A new character" is really two independent things that combine: a **game-logic
identity** (name, stats, AI behavior, dialogue, quest hooks) and a **visual appearance**
(mesh, texture, animations). Each half already has its own deep-dive reference — this
doc is the bridge between them, plus the one missing piece neither covered yet: how a
Character Template's `Model=` field actually resolves to real `.gr2` files.

- Game-logic identity: `.claude/skills/lionheart-modding/SKILL.md`'s **"Adding a
  brand-new NPC to a level"** section (Character Template `.can` + `DialogTree` +
  `CGeneratorAI` spawner in a `.zax`) — confirmed working end-to-end via Marco the
  Pickpocket and Lucia (the "Test Pocket" mod). Don't duplicate that work here, follow
  it directly.
- Visual pipeline internals: `docs/gr2-format.md` (container format), `docs/gltf-
  roundtrip.md` (mesh/skeleton edit pipeline via Blender), `docs/bink-texture-
  format.md` (texture codec).

## What's achievable today vs. not

**Fully supported, proven in-game:**
- A new NPC identity wearing an **existing, unmodified** model+animation set. This is
  all Lucia Wererat and Marco the Pickpocket are — clone a `.can`, point `Model=` at an
  existing `Characters/...` resource, wire it into a `.zax` via `CGeneratorAI`. Zero
  `.gr2` work needed.
- **Same-topology edits** to an existing model's mesh/skeleton/texture (reshape,
  rescale, retexture-prep, reweight) via the GR2↔glTF pipeline, patched back over the
  *original* file path. This changes that model for **every** character that
  references it (e.g. `wererat-2x-test` scales every wererat, not one instance) — good
  for "reskin this whole creature type," not "one unique-looking individual" — unless
  you target an already-separate variant path (see "Recipe B" below).
- **Viewing** a model's animation clips in Blender (`gr2_to_gltf.py` auto-discovers and
  exports every associated `.ANIMATION.GR2` file as glTF `animations[]` — see "How
  `Model=` actually resolves" below for the two directory layouts this handles).

**Not yet supported (real gaps, not attempted):**
- **Editing an animation clip and patching it back.** `gltf_to_gr2.py` only patches
  `MODEL.GR2` (mesh + bind-pose skeleton) today. Animation curves are exported (read)
  via `gr2_to_gltf.py` but there's no writer for `.ANIMATION.GR2` yet.
- **Authoring a brand-new `.mdl16` manifest** so a genuinely new, distinct model
  coexists alongside the original under its own `Characters/...` path (see below) — the
  binary format has been read and partially understood from real examples, but no
  encoder exists and round-tripping it hasn't been attempted.
- **New mesh topology** (adding/removing vertices or triangles) — `gltf_to_gr2.py`
  requires unchanged vertex/triangle/bone counts; retopology needs a general
  sector/fixup-table rebuild.
- **Encoding a new texture as BinkTC0** — decoding only (`binktc0_decode.py`). Not a
  real blocker: `Texture.Encoding=1` (raw, uncompressed) is a legitimate alternative
  this format already supports for writing new content, and the existing extractor
  reads it back fine.

## How `Model=` actually resolves to `.gr2` files

A Character Template's `Model=Characters/Monsters/Wererat` field is **not** a direct
path to a `.gr2`. It's a `Resources/`-relative path (no extension) that resolves to a
`Cache/Models/<same path>.mdl16` entry **inside `data.dat`** (not a loose file — use
`zipfile`/`archive.py` to read it). `.mdl16` is a compact binary manifest, mostly
length-prefixed strings, readable without a full parser:

```python
import zipfile
with zipfile.ZipFile(r"<game>\data.dat") as zf:
    data = zf.read("Cache/Models/Characters/Monsters/Wererat.mdl16")
```

Confirmed content for `Characters/Monsters/Wererat`, byte-inspected directly (not a
written spec — treat as "known from one real example," not exhaustive):
- The base model path, no extension: `Models3D/Enemies/Wererats/Models/Wererat/WereRat`
  (resolves to `WereRat.MODEL.GR2` next to a `WereRat.MODEL.TXT` sidecar — see
  `docs/gltf-roundtrip.md`'s `Render Scaling` note).
- A list of animation clip paths, also extensionless:
  `Models3D/Enemies/Wererats/Shared Animations/Attack01`, `.../Idle`, `.../GetHit`,
  etc. — note these sit in a directory shared across that creature's variant folders,
  *not* next to `WereRat.MODEL.GR2` itself (only `Walk.ANIMATION.GR2` is a direct
  sibling of the model there). This split-directory layout affects ~4% of models
  (checked across 200 real `MODEL.GR2` files) at an *inconsistent* depth — e.g.
  `Wererat/WereRat.MODEL.GR2` → `Shared Animations` two levels up, but
  `Black Wolf/BlackWolf.MODEL.GR2` → only one level up (no intermediate `Models`
  folder for wolves). `gr2_to_gltf.py`'s animation auto-discovery
  (`_discover_animation_paths`) walks up looking for a `Shared Animations` sibling
  rather than assuming a fixed depth, specifically because of this.
- "Character Slot Types" entries (`Arm`, `Hand`, `Head`, `Weapon`, `Body`, ...) —
  equipment/attachment slots, with `Body` mapped to `WereRat` here (a material/skin
  selector, not independently investigated further).

**The base game already ships multiple `.mdl16` variants for at least this one
creature type** — found by listing `data.dat`'s `Cache/Models/Characters/Monsters/`
entries:

| `.mdl16` file | Model path it points at |
|---|---|
| `Wererat.mdl16` | `Models3D/Enemies/Wererats/Models/Wererat/WereRat` |
| `Wererat variant.mdl16` | `Models3D/Enemies/Wererats/Models/Wererat variant/WereRat` |
| `Wererat boss.mdl16` | `Models3D/Enemies/Wererats/Models/Wererat Alpha/AlphaWereRat` |
| `Wererat PRIME.mdl16` | `Models3D/Enemies/Wererats/Models/Wererat PRIME boss/AlphaWereRat` |

These are the same size tiers `Render Scaling` tracks (base/Alpha/PRIME) — the game
already has, and presumably already references from some existing `.can` template,
distinct model folders for "bigger/tougher wererat." **This matters a lot for Recipe B
below**: it means a visually-distinct variant of an existing creature type can very
likely be built *without* writing a new `.mdl16` at all, just by checking whether your
creature type already ships one of these alternates and pointing your new `.can`'s
`Model=` at it instead of the base path.

## Recipe A — new identity, existing look

Fully proven, zero `.gr2` work. Follow `.claude/skills/lionheart-modding/SKILL.md`'s
**"Adding a brand-new NPC to a level"** section directly: clone a Character Template
`.can`, point its `Model=` at any existing `Characters/...` path, author (or reuse) a
`DialogTree`, and wire a `CGeneratorAI` spawner into the target `.zax`. This is exactly
what Lucia Wererat and Marco the Pickpocket both are.

## Recipe B — new identity, modified/reskinned look

For a creature that should look different from the vanilla version. Two variants,
depending on whether you found a spare `.mdl16` alternate for your creature type (see
table above):

**B1 — a spare variant path exists (preferred, keeps ordinary instances untouched):**
1. Point your new `.can`'s `Model=` at the alternate path (e.g.
   `Characters/Monsters/Wererat boss` instead of `Characters/Monsters/Wererat`).
2. Edit *that* variant's `.gr2` (e.g. `Wererat Alpha/AlphaWereRat.MODEL.GR2`) via the
   pipeline below — ordinary wererats using the base path are untouched.

**B2 — no spare variant (edits affect every instance of that creature type):**
1. Export the model to glTF: `python gr2_to_gltf.py <Model>.MODEL.GR2 out.gltf`
   (animations auto-attach from sibling `.ANIMATION.GR2` files).
2. Edit in Blender — reshape/reweight/retexture-prep, **keeping vertex, triangle, and
   bone counts unchanged** (same-topology only, see `docs/gltf-roundtrip.md`). Don't
   apply a coordinate-system-compensating rotation to the object (see that doc's Z-up
   note). Export back to `.gltf`/`.glb`.
3. Patch back over a **copy** of the original: `python gltf_to_gr2.py
   <original>.MODEL.GR2 edited.glb <output>.MODEL.GR2`.
4. For a whole-model uniform size change specifically, prefer the `WereRat.MODEL.TXT`
   sidecar's `Render Scaling` field (see `docs/gltf-roundtrip.md`) over patching the
   `.gr2` at all — simpler, and doesn't interact with the animation-curve-override
   problem that whole-model `.gr2` scaling hit.
5. For a new texture: either keep the original `Encoding=3` texture untouched (if not
   changing appearance), or replace it with a new `Encoding=1` (raw) texture — write
   `Width`/`Height`/`Layout.BytesPerPixel` and raw pixel bytes directly (no encoder
   needed, this path is natively supported by the format).
6. Package as a mod (`files/` mirroring the `Resources/` path of whatever you patched)
   and install/build: `python modmanager.py install <mod-dir> <game-dir>` then
   `python modmanager.py build <game-dir>`.

## Testing gotchas (shared with any new-entity mod work)

- New entities placed in a `.zax` don't appear on saves that have already visited that
  level — use a save that's never entered the target level, or a fresh game. See
  SKILL.md's "CRITICAL: new entities in a `.zax`..." section for the full mechanism and
  why there's no workaround.
- Confirm `Lionheart.exe` is not running before repacking/building — the file lock
  causes `PermissionError`/`WinError 5` otherwise.
- `data.dat` must be repacked with `--compression store` — see SKILL.md.
