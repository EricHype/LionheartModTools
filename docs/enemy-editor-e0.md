# Enemy editor: E0 findings

E0 answered the four questions the enemy editor plan depended on, before any editor is
built. Each answer below says how it was established.

## 1. Animation preview works

**Approach:**
- Export the model with `gr2_to_gltf.export_model`, using the clip list from the model's
  `.mdl16` (see 2).
- Show the result in `viewer/creature.html`: three.js r160, MIT, vendored under
  `viewer/vendor/three`, inside a PySide6 `QWebEngineView`.
- Serve the page and the exported file from a localhost HTTP server, because Chromium
  blocks ES modules loaded from `file://`.
- Python drives the page through `window.preview`: `load`, `play`, `stop`, `pose` and
  `snapshot`.

**How it was checked:** posed frames of the Gray Wolf, the Greater Skeleton and a thug
archer, rendered through the viewer and looked at.

- **Mesh:** textured, upright and grounded, so the Z-up correction is right.
- **Clips:** every one checked moves the way its name says:
  - attacks lunge, thrust or draw the bow;
  - deaths collapse;
  - the wolf's howl sits back with its head raised;
  - walk and run stride and gallop.
- **Weapons:** the archer's bow and the skeleton's sword are part of their meshes and
  show up.

Not done: a side-by-side comparison with the running game.

**Requirements and fixes:**
- **Real display needed.** With `QT_QPA_PLATFORM=offscreen`, Chromium loses its WebGL
  context: GPU backing fails, and SwiftShader renders blank frames. On the normal Windows
  display it works. The editor always runs there, so this only affects automated tests,
  which have to put the window off-screen instead.
- **Framing from the posed figure.** A skinned mesh's stored bounding box is its bind
  pose in mesh space, and framing on it aimed the camera at a humanoid's knees.
  `posedBox()` uses `SkinnedMesh.computeBoundingBox()`, which also keeps walk and run
  cycles in shot.
- **Export cache.** Exports take 5-9 seconds per model, so the editor should cache them,
  keyed by model path, and build each the first time it's shown.

**Coverage:** every model a creature can uses was exported from its `.mdl16` clip list.
- **101 of 101 models** exported with no errors.
- Every `Models3D` path in every `.mdl16` resolved to a file.
- Every listed clip came out as an animation, and every model has its texture.
- Total 590 seconds, about 6 seconds a model.

## 2. Reading a model's animation list

`Model=Characters/Monsters/X` names `Cache/Models/Characters/Monsters/X.mdl16`. Inside it,
every `Models3D/...` path carries a 4-byte length prefix:
- The mesh is the one that resolves to a `.MODEL.GR2`.
- The clips are the ones that resolve to an `.ANIMATION.GR2`.

Reading by length prefix matters: a regex for printable runs merged adjacent strings, and
found one clip per model instead of nine.

**How a sequence name picks a clip:** the clip's name equals the sequence name, or starts
with it. "Attack" matches `Attack01` and `Attack02`; "Fidget" matches `Fidget01-03`.

Checked across all 478 creature cans: every `Sequence Name`, `Cur Sequence` and `Turn
Sequence Name` against the can's own model.

| Result | Uses |
|---|---|
| Exact match | 2,126 |
| Prefix match | 344 |
| `Snapshot` (engine-internal, not a clip) | 49 |
| Horse's `Attack` | 1 |

The Horse's model has only Death, Idle and Walk, which is the known broken case.

**So the model-swap guard is exact:** a model can replace another only if every sequence
the creature calls matches one of its clips.

## 3. Summoning a creature from a conversation

`CCreateEntityFromCanAction` is the action. The developers' own `companion` cheat uses it
(`Resources/Cheat Code Scripts/Give Companion.Cheat.txt`):

```
Action=CCreateEntityFromCanAction
{
New Location=$Instigator
Copy Height=0
Random Location Delta=100
Warp Behavior=
Canned Entity to Create=Monster Cans/Animals/Invulnerable Chicken Companion
New Name=CheatCompanion
}
```

It has 73 uses in the game: item cans, scripts, projectiles and maps. None are in a
conversation, but a reply's `Custom Action` runs any action, and in a reply `$Instigator`
is the player. So a `[TEST] Summon X` reply on the test menu should place the creature
next to the player, on an existing save. **This is unproven until checked in the game.**

## 4. How tougher versions appear

It's not the difficulty setting. `Resources/Dificulty Types/*.dificulty` only scale damage
dealt and taken, and the executable contains no " Tough" or " Super" strings.

Maps name the tiers directly:

| Tier | References | Cans |
|---|---|---|
| Base | 4,957 | 218 |
| Tough | 4,143 | 131 |
| Super | 5,505 | 129 |

Inside a spawner, each `CGeneratorAIGroup` has a `Max Party Mojo`, a measure of party
strength, and lists its own tier. Crossroads' wasp spawner:

| Max Party Mojo | Spawns |
|---|---|
| 2 | 1 Wasp |
| 10 | 2-3 Wasp Tough |
| 25 | 3-4 Wasp Super |

An earlier survey's figure of 60 direct tier references was wrong.

**For the editor:** the three tiers are separate creatures. "Where it appears" should show
which spawner group, at which party strength, picks each one.

## Owed in the game

- A creature summoned by `[TEST] Summon X` appears next to the player and fights.
- A model swap that passes the sequence check animates correctly in the game.
