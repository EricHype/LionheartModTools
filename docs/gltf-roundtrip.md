# GR2 ↔ glTF round trip — editing character models in Blender

**Status: the mesh/skeleton edit pipeline works, confirmed in-game.** A real edit
(uniformly scaling the WereRat model 2x in Blender) was exported, patched back into a
valid `.gr2`, built into `data.dat`, and loaded correctly by the actual game at the
correct scale — for a *static, unanimated* edit. This also resolved the one open
unknown from the original plan: a placeholder `crc32=0` in the patched file did **not**
block loading — the game accepted it.

**Whole-creature uniform scale turned out not to need `.gr2` patching at all** — see
"Whole-model uniform scale" below: four increasingly elaborate `.gr2`-internal attempts
were dead ends, and the likely real answer is a plain-text sidecar field the engine
already uses (pending in-game confirmation). That detour is worth reading for the
`Curve2` format discovery it produced (`docs/gr2-format.md`), which matters for real
mesh/rigging/animation editing — the actual point of this pipeline — even though it
wasn't the fix for uniform scale specifically.

See `docs/gr2-format.md` for the underlying container/decompression format this all
builds on. This doc covers the tools built on top of it.

## The pipeline

```
.gr2 --[gr2_to_gltf.py]--> .gltf/.bin --[Blender, edit]--> .gltf/.bin or .glb
                                                                  |
                                                          [gltf_to_gr2.py]
                                                                  |
                                                                  v
                                                          patched .gr2
```

- **`gr2_to_gltf.py`**: exports a `Model` (skeleton + skinned mesh) to glTF 2.0. Hand-
  rolled writer (`json`/`struct` only, no new dependencies). Confirmed field mapping
  (bones, vertices, materials) is documented in the mapping comments at the top of the
  file. No coordinate-system conversion is applied (Granny/3ds Max content is Z-up;
  glTF is Y-up) — the model will appear lying on its side in Blender. **Don't rotate or
  apply a transform to the object to compensate** — orbit the viewport camera instead.
  Any rotation baked into the exported vertex data (via "Apply Transform") would not
  match what the importer expects when patching back, since the importer assumes
  vertex positions map straight back into the original file's coordinate space.
- **`gltf_to_gr2.py`**: patches an edited glTF/GLB back into a copy of the *original*
  `.gr2` file, by overwriting known-offset leaf bytes (vertex positions/normals/UVs/
  weights, bone translation) rather than serializing a new file from scratch. Supports
  both `.gltf`+`.bin` and single-file `.glb` (Blender's default export format).
  Same-topology only (vertex/triangle/bone counts must be unchanged) — see "Known
  limitations" below.
- **`scale_model.py`**: standalone tool, bypasses Blender/glTF entirely — patches a
  `.gr2` directly to uniformly scale a whole model by a factor. See "Whole-model
  uniform scale" below; **does not currently produce a correct in-game result** for
  animated characters, kept for reference/reuse once curve-scaling is added.

## Verification performed

1. **Zero-edit round trip**: exported `WereRat.MODEL.GR2` to glTF, immediately patched
   it back with no changes, and diffed the full decoded element tree (24,510 compared
   slots) against the original — zero mismatches. Re-confirmed after the skeleton-scale
   fix below (same result: 0 mismatches).
2. **Real edit, offline verification**: scaled the mesh 2x in Blender, patched it back,
   reloaded through `gr2_format.py`, and confirmed vertex positions were exactly 2.0x
   the original values.
3. **Real edit, in-game**: built the patched file into a test mod
   (`mods/wererat-2x-test`), rebuilt `data.dat`, and confirmed in-game that wererats
   load at the correct 2x scale without a "corrupted" error or crash (with the
   joint-stretching artifact described below).
4. **Skeleton scale fix, synthetic test**: hand-scaled one bone node's `matrix` 2x in
   a copy of the exported glTF (Blender not available to drive programmatically) and
   ran it through the fixed patcher. Confirmed the target bone's `Transform.scale_shear`
   diagonal came out at exactly 2.0 (was ~1.0) with translation unchanged, and that
   sibling/child bones were untouched — the decomposition and patching logic works.
   **Not yet tested against a real Blender-driven armature scale** — see limitations.

## Bugs found and fixed while building this

- **`BoneWeights` is 4 raw `u8` bytes on disk (0-255), not floats.** The exporter now
  normalizes to 0.0-1.0 for glTF's `WEIGHTS_0` (float) accessor; the importer
  denormalizes back to `u8` on patch. Missing this the first time caused a 4-byte-float
  write into a 4-byte-total field, corrupting the adjacent `BoneIndices` and `Normal`
  fields for every vertex (caught by the zero-edit round-trip diff before it ever
  reached a real edit).
- **`.glb` (binary glTF) support**: Blender's default glTF export is a single packed
  `.glb`, not the `.gltf`+`.bin` pair the importer originally assumed. Added a GLB
  container parser (12-byte header + JSON chunk + BIN chunk).
- **Skeleton scale wasn't patched.** The importer used to only patch bone
  *translation*, so a mesh scale edit doubled vertex positions but left the skeleton's
  own bind-pose scale unchanged, causing stretched textures at joints in the 2x test
  (skinning math blends bone matrices against vertex positions, and the two were at
  different scales). Fixed by decomposing each edited glTF joint node's 4x4 matrix
  back into Granny's translation/rotation-quaternion/scale_shear representation
  (`decompose_matrix4_colmajor`, Gram-Schmidt on the linear part, preserves shear
  rather than just per-axis scale length) and patching the full `Transform`, plus
  patching `InverseWorldTransform` from the glTF skin's `inverseBindMatrices`
  accessor. See "Known limitations" for what's still unverified about this fix.
- **Existing game textures now show up in Blender — this used to be a real, understood
  gap, now solved.** Textures are NOT separate loose or cached files at all
  (`.frm16`/`.mdl16` under `data/Cache` turned out to be UI and terrain caches, and a
  tiny path-index for models, respectively, unrelated to model textures). Real texture
  pixel data is embedded directly in the `.gr2` itself
  (`Textures[].Images[].MIPLevels[].Pixels`, self-describing like everything else).
  Sampled 230 textures across 60 real character models and **every one** uses
  `Encoding=3` (`GrannyBinkTextureEncoding` — a wavelet + adaptive-arithmetic-coded
  still-image format internally called "BinkTC0", unrelated to the Bink *video* codec
  despite the shared name prefix; see `docs/bink-texture-format.md` for the full format
  writeup and the reference-decoder-verified port, `binktc0_decode.py`). `gr2_to_gltf.py`
  now decodes both `Encoding=3` and the plain-raw `Encoding=1` case and embeds the result
  as a PNG data URI. Verified against 40 random real textures (no crashes, no failures)
  plus visual spot-checks; two apparently-blank results turned out to be genuine
  flat-black placeholder textures in the game data (confirmed by running Granny's own
  compiled reference decoder on the same bytes, not just assumed). Doesn't affect the
  patch-back step either way (original `Material`/`Texture` references in an edited
  `.gr2` are untouched by the patcher) — this was purely a Blender-viewing gap, now
  closed.

## Whole-model uniform scale — three failed attempts, and why (debugging record)

Goal: make a whole creature (WereRat) bigger/smaller uniformly, all tested via the same
loop — patch a `.gr2`, drop it into `mods/wererat-2x-test`, rebuild `data.dat`, load
in-game. Every attempt below loaded without crashing/"corrupted" errors; the failures
were all visual (wrong shape), only visible in-game. Attempt 1 is an incomplete but
usable baseline (imperfect, not broken); **attempts 2-4 were dead ends**, each for a
different reason — worth keeping as a record so the same three don't get re-tried:

1. **Scale mesh vertex positions only** (the very first working test, see "Verification
   performed" above). Loaded at correct 2x scale but with **stretched textures at
   joints** — the skeleton's own bind-pose scale stayed at 1x while the mesh was 2x, so
   skinning blends a 1x bone matrix against 2x vertex data.
2. **Scale the armature object in Blender**, re-export, patch bone `Transform`/
   `InverseWorldTransform` from the edited glTF (the fix described in "Bugs found and
   fixed" above). Turned out **worse**, not better: Blender folds an armature-object-
   level scale into each bone's exported `InverseWorldTransform` only, via the glTF
   skin's `inverseBindMatrices` — it does **not** touch any joint node's own local
   `matrix`. So the patcher (which reads per-bone `Transform` from each joint node)
   found nothing to patch there, and just faithfully copied Blender's now-inconsistent
   IBM through. Transform stayed 1x, IBM implied 2x — an actual mismatch, not just an
   imprecise one, hence a worse-looking result. **Lesson: don't trust that editing an
   armature's object-level transform in Blender produces per-bone data consistent with
   itself on export** — verify what actually changed in the exported JSON before
   assuming it did the expected thing.
3. **Patch `Model.InitialPlacement.scale_shear` directly** (`scale_model.py`, bypassing
   Blender/glTF entirely) — structurally the cleanest idea, since it's one Transform for
   the whole model instance, untouched skeleton/mesh, nothing to be inconsistent with.
   Verified offline to patch exactly the one field. **Had zero effect in-game** — the
   model came back at 1x size. The engine doesn't apply `InitialPlacement.scale_shear`
   as a runtime scale (plausibly because a `TrackGroup` in the currently-playing
   animation carries its *own* `InitialPlacement`, used instead — see
   `Idle.ANIMATION.GR2`'s `TrackGroups[0].InitialPlacement` field, not investigated
   further). **Lesson: a field existing and looking structurally right for a purpose
   doesn't mean the engine actually reads it that way — verify in-game, don't infer from
   the schema.**
4. **Mathematically-derived skeleton scale** (`scale_model.py`, rewritten): scale every
   vertex position by k, scale every bone's own local `Transform.translation` by k
   (rotation/scale_shear untouched — provably produces a rigid, undistorted k-times
   skeleton by induction through the parent/child hierarchy), then *recompute*
   `InverseWorldTransform` from scratch by composing the patched chain under
   `Model.InitialPlacement` and inverting it (not a shortcut formula — an earlier
   shortcut attempt, "just scale IBM's translation column by k," was verified wrong
   because `InitialPlacement`'s own nonzero translation breaks that shortcut; see
   `scale_model.py`'s docstring for the full derivation). Verified offline to
   extremely high precision: reconstructed world matrices invert to the stored IBM with
   ~1e-6 error, matching the untouched original file's own internal consistency, and
   every bone's world position lands exactly on "scaled by k about the
   `InitialPlacement` anchor point." **Still messed up in-game — worse than attempt 1.**
   Root cause (confirmed via the `Curve2` investigation in `docs/gr2-format.md`): this
   creature's bone positions are driven every frame by separate `ANIMATION.GR2` files'
   `PositionCurve` keyframe data, which completely overrides whatever the model file's
   own bind-pose `Transform` says, the instant any animation plays (which is immediately
   — idle loops constantly). So the skeleton edit was correct for a *static* bind pose
   that the game never actually uses as-is at runtime; the mismatch between "vertices +
   IBM computed for a 2x pose" and "actual bone position driven back to 1x by animation
   curves" produced a worse, chaotic-looking result than attempt 1's simple, single-axis
   mismatch. **Lesson: for any character that plays animations (i.e. basically all of
   them), the skeleton's own `Transform` is not the runtime source of truth for bone
   position — don't scale it expecting the effect to stick.**

**Likely the actual answer, pending in-game confirmation**: not by patching the `.gr2`
at all. Every model has a sidecar `<ModelName>.MODEL.TXT` file (plain text, next to the
`.GR2`) with a `Render Scaling` field, and it's not a dead value: base `WereRat.MODEL.TXT`
has `Render Scaling=1`, `AlphaWereRat.MODEL.TXT` (a visibly bigger variant) has `1.15`,
the PRIME boss variant has `1.5` — tracking their real in-game size tiers. Set to `2` on
an otherwise completely unpatched `WereRat.MODEL.GR2` and built into `mods/wererat-2x-test`
(swapped from the earlier `.gr2`-patching version) — **not yet confirmed in-game**. If it
works, this makes attempts 1-4 above (and the `Curve2`-scaling follow-up) unnecessary
**for pure uniform scaling specifically**; the `.gr2` curve/skeleton patching work isn't
wasted regardless, since it's what actually enables editing mesh shape/rigging/animation
content, which is the real goal this whole pipeline exists for — uniform scale was
always just a convenient test case, not the point.

## Known limitations (real, not yet addressed)

- **Non-uniform skeleton edits** (reposing/reshaping individual bones, not just a single
  global scale factor) still hit the same animation-curve-override problem attempt 4
  found — `Render Scaling` only helps for a single uniform whole-model factor, not
  per-bone changes. Not yet attempted; would need the `Curve2` patching path.
- **Same topology only.** Adding/removing vertices or triangles isn't supported — the
  patcher errors out if vertex count changes. Real retopology support needs a general
  sector/fixup-table rebuild, not just byte patching.
- **`crc32` is written as a placeholder 0.** Empirically confirmed not to block loading
  for this test case, but not fully understood (e.g. whether it's validated under any
  other conditions). Worth real investigation if patched files start failing to load
  for a different reason.
