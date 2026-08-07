# WereRat 2x Scale Test

A validation mod, not a real content mod. It replaces the WereRat character model with
a version scaled up 2x.

History: the first version of this mod scaled mesh vertices via the GR2 ↔ glTF round
trip (Blender edit + `gltf_to_gr2.py`). That loaded correctly at 2x scale but had
stretched textures at joints, because the skeleton's own bind-pose scale wasn't
patched to match. A follow-up attempt tried scaling the armature in Blender, but
Blender folds an armature-object-level scale into each bone's `InverseWorldTransform`
only, leaving each bone's own `Transform` unpatched — an inconsistency likely to look
worse, not better, so that version was never built into this mod.

A third attempt patched `Model.InitialPlacement.scale_shear` directly (a single
whole-model placement transform, untouched skeleton/mesh) -- structurally clean, but
the engine doesn't apply it as a runtime scale at all; the model came back to 1x size
in-game.

Current version (`scale_model.py`, `python scale_model.py <original.gr2> <factor>
<output.gr2>`) bypasses Blender entirely and derives the fix mathematically: scales
every vertex Position by k, scales every bone's own local translation by k (rotation/
scale_shear untouched -- provably produces a rigid, undistorted k-times-bigger
skeleton by induction through the hierarchy), then recomputes each bone's
`InverseWorldTransform` from scratch by composing the patched Transform chain under
`Model.InitialPlacement` and inverting it, rather than guessing at a shortcut formula.
Verified offline: reconstructed world matrices invert to the stored IBM with ~1e-6
error (same as the untouched original's own internal consistency), and every bone's
world position lands exactly on "scaled by k about the InitialPlacement anchor point."
See `docs/gltf-roundtrip.md` for the full writeup.

Install with `modmanager.py install mods/wererat-2x-test <game_dir>`.
