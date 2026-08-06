# WereRat 2x Scale Test

A validation mod, not a real content mod. It replaces the WereRat character model with
a version scaled up 2x, produced entirely through the new GR2 ↔ glTF round-trip
pipeline: decoded the original `.gr2`, exported to glTF, scaled 2x in Blender, patched
back into a valid `.gr2` by `gltf_to_gr2.py`.

Purpose: confirm the game actually accepts and correctly loads a `.gr2` file produced
by this pipeline, not just that our own reader can parse it back. Confirmed working —
wererats load in-game at the correct 2x scale. Known issue: textures appear stretched
at the joints, because this patcher doesn't yet propagate mesh scale to the skeleton's
own bind-pose scale (see `docs/gltf-roundtrip.md` for the full writeup).

Install with `modmanager.py install mods/wererat-2x-test <game_dir>`.
