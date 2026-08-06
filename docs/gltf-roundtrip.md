# GR2 ↔ glTF round trip — editing character models in Blender

**Status: the core pipeline works, confirmed in-game.** A real edit (uniformly scaling
the WereRat model 2x in Blender) was exported, patched back into a valid `.gr2`, built
into `data.dat`, and loaded correctly by the actual game at the correct scale. This
also resolved the one open unknown from the original plan: a placeholder `crc32=0` in
the patched file did **not** block loading — the game accepted it.

See `docs/gr2-format.md` for the underlying container/decompression format this all
builds on. This doc covers the two new tools built on top of it.

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

## Verification performed

1. **Zero-edit round trip**: exported `WereRat.MODEL.GR2` to glTF, immediately patched
   it back with no changes, and diffed the full decoded element tree (24,510 compared
   slots) against the original — zero mismatches.
2. **Real edit, offline verification**: scaled the mesh 2x in Blender, patched it back,
   reloaded through `gr2_format.py`, and confirmed vertex positions were exactly 2.0x
   the original values.
3. **Real edit, in-game**: built the patched file into a test mod
   (`mods/wererat-2x-test`), rebuilt `data.dat`, and confirmed in-game that wererats
   load at the correct 2x scale without a "corrupted" error or crash.

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

## Known limitations (real, not yet addressed)

- **Skeleton scale isn't patched.** The importer only patches bone *translation*, not
  the full Transform (rotation/scale) or `InverseWorldTransform`. This is why the 2x
  scale test showed stretched textures at joints: vertex positions doubled, but the
  skeleton's own bind-pose scale didn't, so the skinning math (blending bone matrices
  against vertex positions) works with mismatched scales, most visible exactly where
  multiple bones blend. A whole-mesh uniform scale edit is a bad match for the current
  patcher for this reason. Reshaping/moving vertices without changing overall
  proportions, or editing weights/UVs, doesn't hit this problem. Properly supporting
  scale edits needs full Transform (translation+rotation+scale_shear) patching, and
  ideally decomposing an edited glTF node matrix back into Granny's translation/
  rotation-quaternion/scale_shear-matrix representation rather than reusing the raw
  matrix.
- **Same topology only.** Adding/removing vertices or triangles isn't supported — the
  patcher errors out if vertex count changes. Real retopology support needs a general
  sector/fixup-table rebuild, not just byte patching.
- **Animation curves are still opaque.** `PositionCurve`/`OrientationCurve`/
  `ScaleShearCurve` (type_id 1, `VariantReference`) are read as raw, undecoded bytes.
  Animation editing isn't possible yet.
- **Texture linking is best-effort.** The game ships no loose source textures (only
  compiled cache formats, same finding as this session's earlier FRM16 investigation),
  so exported glTF meshes have no material image — Blender shows them untextured. This
  doesn't affect the patch-back step (original `Material`/`Texture` references in the
  `.gr2` are untouched by the patcher), only the Blender viewing/editing experience.
- **`crc32` is written as a placeholder 0.** Empirically confirmed not to block loading
  for this test case, but not fully understood (e.g. whether it's validated under any
  other conditions). Worth real investigation if patched files start failing to load
  for a different reason.
