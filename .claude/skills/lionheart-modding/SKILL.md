---
name: lionheart-modding
description: Modding toolkit and reference for Lionheart, Legacy of the Crusader (2003, Reflexive Entertainment). Use when unpacking/repacking data.dat, editing quests/items/dialogue, or debugging why a scripted action does nothing in-game.
---

# Lionheart: Legacy of the Crusader — Modding Reference

Everything below was learned by reverse-engineering `Lionheart.exe` with Ghidra/ReVa and
extensive in-game trial and error while adding a real quest ("Wolf Pelts for Quinn") to
the shipped game. Trust this over intuition — several of these behaviors are counter-
intuitive and cost many test cycles to pin down.

## Tools

`C:\Users\vkays\LionheartModTools\`:
- `resource_format.py` — parser/serializer for the game's brace-delimited resource text
  format (`ClassName { Key=Value ... }`). Byte-identical round-trip on every file tested.
- `archive.py` — unpack/repack `data.dat`.
- `modmanager.py` — package, install, and build mods as lightweight overlays (see
  "Packaging & distributing mods" below). Reuses `archive.py`'s unpack/repack directly.
- `mdl16_format.py` — read, recolor, and author inventory icon art (`.mdl16`/`.frm16`).
  See `docs/mdl16-icon-format.md`, and the `adding-a-new-weapon` skill for a worked
  end-to-end example.
- `examples/` — worked-example scratch files from building the Wolf Pelts for Quinn quest
  (not general-purpose, but useful as reference for the DialogTree splice pattern).

```
python resource_format.py "path\to\some.InventoryItem"   # dump parsed tree as JSON
python archive.py unpack "<game>\data.dat" "<game>\data"
python archive.py repack "<game>\data" "<game>\data.dat" --compression store
```

## data.dat: MUST use store (no compression), never deflate

`data.dat` is a plain ZIP archive (`PK\x03\x04` header) — GOG install unzips to `data\`.

**Critical**: repack with `--compression store` only. Confirmed by decompiling the
central-directory parser in `Lionheart.exe`: it reads each entry's compression-method
field and does `if (method != 0) { fatal error }`. Deflate (method 8) — even though the
exe links zlib and the error text says "or a type of compression supported by the game
engine" — is **never** accepted in practice. Using deflate produces the exact in-game
error: *"...has been created using an unsupported type of compression..."* and the game
won't launch. Always verify after repacking:

```python
import zipfile
with zipfile.ZipFile(path) as zf:
    assert zf.testzip() is None
    assert set(zf.getinfo(n).compress_type for n in zf.namelist()) == {0}
```

Store-mode `data.dat` ends up close to the original's uncompressed size (~1.6GB) — that's
expected and fine (659GB+ free is typical on modern drives).

## Resource text format grammar

```
TypeName
{
    Key=Value
    Key=NestedTypeName
    {
        ...
    }
}
```

- Tabs are indentation only (cosmetic); nesting is brace-delimited.
- Keys may contain spaces, even a trailing space before `=` (e.g. `Value if True =CConstant`).
- An empty value after `=` is valid (`Operator=`).
- `Array` is not special — it's just a TypeName whose fields are `Item Count=N` followed
  by N fields that repeat the same key name (repeated keys are normal, not an error).
- Leaf values are always raw strings (numbers, paths, etc.) — never reformat them.
- File encoding: treat as `latin-1` (byte-preserving) — don't assume UTF-8, don't assume
  a real code page. This guarantees lossless round-trip regardless of actual content.
- Line endings are usually CRLF, but **verify per-file before every edit** — we saw a
  `sed -i` invocation silently convert a file to LF-only mid-session. The game tolerated
  it (loaded fine), but always re-check current bytes (`raw.find(...)`) rather than
  assuming CRLF, or your string-replace will silently match 0 times.
- `.zax` level files use the exact same grammar (root type `CLayerSaveData`) — the parser
  handles them unmodified, including multi-MB files (~60ms parse time).

## DialogTree format (different — NOT pure brace grammar)

`.DialogTree` files are a **hybrid** format: an outer flat list of "Node" records
separated by dashed lines, with embedded brace-objects for conditions/actions. Not
directly parseable by `resource_format.py` as a whole file, but each `Custom Requirement=`
/ `Custom Action=` value is parseable in isolation using it.

```
CDialogTree
{
Name=...
Portrait=...
------------------------------------------------------------
Node ID=<n> <label>
Text=<NPC's line>
Should Have Voiceover=0

Requirement=<label, or !None, or a named .can under a Requirements/ folder>
Custom Requirement=CActionExpression      <- optional inline condition
{
Action=<SomeCondition>{...}
}
Reply Text=<player's line>
Go to node ID=<target node, or blank to end/close>
Custom Action=<ActionType>{...}           <- optional, runs when reply is chosen
Icon=Quest Icon / Speach Skill Icon / Fight Icon / Exit Icon
Is Default Reply=1                        <- marks the fallback/goodbye reply
------------------------------------------------------------
Node ID=<next node>
...
}
```

No indentation inside `.DialogTree` files (flat, left-aligned), unlike other resource
files.

**CRITICAL when creating a brand-new `.DialogTree` from scratch (not editing an existing
one): the `CDialogTree{Name=, Portrait=, Should Have Voiceovers=0, Default Canceled Node
Action=, ---dashes---, ...nodes..., }` root wrapper above is mandatory, including the
final closing `}` after the last node.** Every prior DialogTree edit this session was
*splicing into an existing file* that already had this wrapper, so it never came up — the
first time a new file was built from scratch (Lucia's dialogue) it was written as just the
bare node list with no wrapper at all, and the game's actual error for this was a generic
`"the executable or data file has become corrupted"` message on opening the conversation,
not a helpful parse error. If you see that exact message when talking to an NPC with a
newly-authored DialogTree, check the wrapper first before anything else.

## Adding a brand-new NPC to a level

Four pieces, all confirmed working end-to-end via Marco the Pickpocket:

1. **Character Template**: clone an existing one, e.g.
   `Resources/Levels/<Area>/Character Templates/<Folder>/<Name>.can` — copy a
   similar-complexity existing citizen/NPC template rather than building from scratch.
   Nothing in this file references dialogue or the level at all (see point 3).
2. **DialogTree(s)**: `Resources/Levels/<Area>/Dialog/<Folder>/<Name> Dialogue.DialogTree`
   (see the DialogTree format section above). Use **separate files per branch** (e.g. one
   for "has perk", one for "doesn't") rather than trying to gate replies within a single
   tree — simpler and avoids ever needing to jump to a non-default starting node.
3. **Level wiring** (`Levels/<Area>/<Level>.zax`): this is where the NPC actually gets
   spawned and linked to its dialogue — the Character Template `.can` has **no reference to
   its DialogTree at all**. Add a new top-level `Level Part=CEntityBase` under
   `Tree List=CSortList2D` containing the spawner.
4. **(Optional) Shop wiring**, if the NPC sells things — a *separate* `CEntityBase` with
   `Name=<Something> Inventory` and `Activity=CMerchantAI{Display Name=..., Items=Array{...}}`,
   opened from a DialogTree reply's `Custom Action=CDisplayMerchantWindowAction{Merchant=<Name
   field of the merchant entity>, After Closed Action=}` — confirmed via both the Herbalist's
   and Blacksmith's shops. Don't fire it directly from an interaction-level `CIfAction`; it
   belongs on a reply, after some dialogue.

### The spawner: use `CGeneratorAI`, not `CSimpleGeneratorForCannedEntitiesAI`

This is the single easiest mistake to make, and it fails **silently** (the entity just never
appears, with no error) — `CSimpleGeneratorForCannedEntitiesAI` is the pattern used for
ambient background citizens, not real interactable NPCs. Real, persistent NPCs (confirmed
via Jafar/"Amir") use `CGeneratorAI` with this required structure:

```
Activity=CGeneratorAI
{
    Area=COvalGeneratorArea { Radius=11 }
    Has Started Generating=0
    Groups=Array { Item Count=1, Group=CGeneratorAIGroup
    {
        Max Party Mojo=3
        Quantity to generate min=1
        Quantity to generate max=1
        Things to Generate=Array { Item Count=1, Thing to Generate=CSpawnableCannedEntity
        {
            Weight=1
            Entity=Levels/<Area>/Character Templates/<Folder>/<Name>
        }}
    }}
    New Name=<InstanceName>
    Remove Default AIs=0
    AIs to Add=Array { Item Count=1, AI=CAIInteractionSpecifier
    {
        Interaction Type=Interaction Specifiers/GetCloseThenTalk
        Action=CDisplayDialogTreeAction { Dialog Tree File=Levels/<Area>/Dialog/<Folder>/<Name> Dialogue }
    }}
    Canned AIs to Add=Array { Item Count=0 }
    New Facing Angle=<radians>
    Angle Variation=0
}
```

Wrap the `CEntityBase` around this with `Visible=0, Collideable=0, Stationary=1, Active=1,
Model=Editor/Character Generator Point`, plus `Position X=`/`Position Y=` for where the
character spawns. `After Action=` (death-cleanup, e.g. failing quests tied to the NPC) is
optional — only needed if the NPC has quests keyed to their death, like Jafar does.

**Positioning**: pick coordinates near a confirmed-walkable reference point (an existing
NPC's position, or an `Editor/Spawn Point` entity) rather than guessing blind — bad
positions fail exactly as silently as the wrong generator class does, and the only way to
tell them apart is by comparing against a known-working entity (see the save-staleness
gotcha below, which is *also* an easy way to misdiagnose a bad position as a bad spawner).

### Gating behavior on a perk (or any other condition)

No precedent exists in the shipped game for perk-gated NPC behavior — this pattern is
assembled from separately-confirmed primitives. Branch at the interaction level with
`CIfAction`, using the `CExpressionAction` adapter to turn the `CHasPerkExpression`
*Expression* into a bare *Action* (per the `If=` gotcha above — never
`CActionExpression`-wrapped):

```
Action=CIfAction
{
    If=CExpressionAction
    {
        Expression=CHasPerkExpression { Perks To Check For=Array { Item Count=1, Perk To Check For=Perks/<Name> } }
        Character to get attributes from=$Instigator
    }
    Then=CDisplayDialogTreeAction { Dialog Tree File=Levels/.../<Name> Dialogue Variant }
    Else=CDisplayDialogTreeAction { Dialog Tree File=Levels/.../<Name> Dialogue }
    Return failure if the If failes=0
}
```

### Finding an existing NPC's DialogTree

To find an NPC's actual DialogTree, search the relevant `Levels\<Area>\*.zax` files for
`Dialog Tree File=` near the NPC's name — don't assume it doesn't exist just because the
Character Template `.can` is silent on it.

## CRITICAL: new entities in a `.zax` don't appear on saves that already visited that level

Once a save has entered a level for the first time, that level's **entity list
(who/what exists there)** gets locked into the save's own snapshot. Re-entering the level
on that save (even via a full map transition, e.g. leaving to an adjacent map and walking
back in) does **not** re-derive the entity list from the edited `.zax` file — it restores
whatever was captured the first time that level was ever visited on that save.

This is easy to misdiagnose as a broken entity definition, because **editing an existing
NPC's dialogue *is* picked up fresh on revisit** (dialogue text/files are resolved at
conversation time, not baked into the save snapshot) — so a workflow of "edit dialogue,
revisit, confirm it changed" builds false confidence that the same revisit-test will work
for verifying brand-new entities too. It won't. If you add a new `CGeneratorAI` (or any
new `Level Part`) to a `.zax` and it "doesn't appear" for a tester, first ask whether they
tested on a save that had already visited that level before your edit — if so, that's very
likely the entire explanation, not a construction bug. The only valid test for a new
entity is a save that has **never** entered that level before (or a brand-new game).

(Confirmed the hard way: Marco the pickpocket's `CGeneratorAI` entity was correctly built
on the first real attempt, but appeared to fail three times in a row — including when
placed directly adjacent to Amir/Jafar's own confirmed-working generator — purely because
every test was run on a save that had already visited the Gate District. He appeared
immediately on a fresh new game.)

### How the engine actually enforces this (and why there's no quick workaround)

Save files (`SaveGames\*.sav`) are plain text in the same brace grammar as everything
else and are readable/parseable, but only the *currently loaded* level's own top-level
stats (`Player Health=`, `Map File Name=`, etc.) are plain fields — every level you've
ever left gets serialized into a **binary blob** inside a
`CSwappedLayerFilenameMappingTable{Layer Mapping Array=Array{Layer Mapping=CSwappedLayerFilenameMapping{
Partial Layer Name=..., Current Temp File=TempFile{...raw bytes...}}}}` and reloaded from
that blob on any revisit — never re-parsed from the `.zax`. This is the actual mechanism
behind the gotcha above.

Don't try to route around this by hand-editing a save's `Map File Name=` to jump to an
unvisited level — the player's spawn position for that case isn't stored as a discoverable
plain-text field, so there's no way to guarantee a safe landing spot, and corrupting a
save is a real risk for very little payoff.

Also confirmed **not available, and not patchable** in the retail build: the in-game
editor (`Editor (&F6&)`, `Select Map`, `Load Map` — all present as dead strings in
`Lionheart.exe`, none wired up; guarded by `"CHEAT: Editor tool for testing. Not a real
option in the retail game"`). Pressing F6 or checking the Esc menu does nothing.

This was investigated all the way down via Ghidra/ReVa static analysis (traced the real
Win32 message pump at `FUN_006538d0` → `WM_KEYDOWN` handling → key-code translation
`FUN_00653520` confirms `VK_F6 (0x75)` maps to internal code `0x1c` → the only two places
that could ever consume that code are (1) a listener-dispatch system at `FUN_005a2a20`
whose registration array is initialized and destroyed but **never populated anywhere in
the binary** — permanently zero listeners, dead code — and (2) direct key-state polling
via `FUN_005a28e0`, which has exactly **four callers in the entire executable**, all of
which check only Shift/RShift (`0x2a`/`0x36`) for list multi-select, never `0x1c`).
Conclusion: the actual F6-editor handler was removed from the retail build entirely, not
just hidden behind a flag — only the inert menu-label string survived. There is nothing to
patch; re-enabling the menu item would do nothing since no code anywhere consumes that
keypress. Don't re-attempt this investigation — it's settled.

Dynamic debugging (attaching x32dbg to the running game) was also tried as part of this
and is **not practical** in this environment: the game runs exclusive-fullscreen, and a
paused/attached debugger prevents Windows from switching away from it, causing hangs that
require killing the debugger (which also kills the debuggee, since Windows kills a
debuggee by default when its debugger exits without a clean detach). No windowed-mode
option exists in-game or in the bundled `dxcfg.exe` to work around this. If a similar
question comes up again, prefer static analysis (Ghidra/ReVa) over live debugging for this
particular game.

**The actual practical workflow: staging saves.** Walk to the threshold of whatever area
you're about to add content to once, save there, and reuse that save for every subsequent
test in that zone — this turns "hours of travel to re-test" into "one load + a short walk"
per iteration, with zero risk to the player's real progress.

## Building a brand-new map from scratch

Confirmed possible, with one major caveat below. `Levels/Empty Scratch map.zax` (185
lines) is a leftover dev template — a genuinely minimal, complete `CLayerSaveData` root
structure, safe to clone as a starting point instead of a real level.

**Terrain (`Plasma Ground=CPlasmaTileMap`)**: a classic quad-heightmap. One elevation byte
per grid *vertex*, spaced 64 world-units apart: vertex columns = `Width/64 + 1`, vertex
rows = `Height/64 + 1` (confirmed against Gate District: 4224/64+1=67 columns, matching
its real row byte-length exactly). `Elevations Row N=`, `Light Overlay Row N=`, and
`Fog Of War 3 Row N=` must all have the same row count as this formula implies — **the
scratch template's own `Height=960` field is inconsistent with its actual grid data (only
15 rows exist, implying a real height of 896)**, which caused genuinely broken rendering
(terrain visibly shifting and disappearing as the player moved) until fixed by either
adding the missing row or correcting `Height` to match. Also fix `Blending` (the scratch
template's value, `1.27591e+010`, is obviously uninitialized garbage — use something like
`0.85`) and give it at least one real `Texture 0=` (`Num Textures=0` may itself have
contributed to the broken rendering). No per-cell texture-index layer exists anywhere in
this structure — texture blending across multiple textures is presumably procedural/
height-based and wasn't reverse-engineered; for a first new map, staying at `Num
Textures=1` (one flat, unblended texture) sidesteps this entirely, at the cost of the
ground looking visibly tiled/repetitive rather than naturally blended.

**Spawn point**: a `CEntityBase` with `Activity=CGeneratorAI`... no — simpler:
`Activity=CSpawnPointAI{Spawn Action=CSeriesAction{...}, Facing Angle=...}`, `Model=Editor/Spawn
Point`, named e.g. `Start Here`, registered at the map's root via `Team Info=Array{Team
Info=CTeamInfo{Spawn Point Name=<matching name, lowercase in the scratch template but this
didn't seem to matter — matched by the door/trigger's `New Location=` field against the
entity's own `Name=`, not this Team Info field>}}`.

**Getting there and back**: `CRelocateAction{New Map Name=<Partial File Name of target>,
New Location=<Name of a spawn-point-style entity in that target>, Who To Switch=$Instigator}`
is the whole mechanism — no separate level registry exists, everything resolves by path
string like the rest of this engine. If firing this from inside a DialogTree reply (not a
level trigger), wrap it in `CDelayAction{Next Action=CRelocateAction{...}, Delay=1}` —
confirmed pattern from Jafar's own dialogue (`Levels/1 Barcelona/Dialog/Gate
District/Jafar.DialogTree`), presumably to let the dialogue UI close before the map swap.

**CRITICAL: for the return trip, use a real `CDoorAI` door entity, not a `CFreeRangePoly`
zone trigger.** Every real shop-exit in the shipped game (Blacksmith, Herbalist, etc.) uses
an invisible `CFreeRangePoly` polygon + `Interaction Type=Interaction Specifiers/GetCloseThen
Exit Area` (or `GetCloseThen Continue On` for wilderness-to-wilderness transitions), and this
looks like the obviously-correct pattern to copy. **It does not work when added to
custom/hand-authored content** — confirmed after building it four different ways (multiple
`Interaction Type` variants, both `CEntityBase` and `CFreeRangePoly`, with and without a
`CDelayAction` wrapper) and cross-checking field-for-field against three independent
real, confirmed-working examples: no interaction cursor ever appeared anywhere in the new
map, not even hovering directly over it. The likely cause: `Empty Scratch map.zax` (and
by extension anything cloned from it) shows signs of having never been fully processed by
the original level editor (see the `Blending`/`Height` garbage above) — some baked
navigation/interaction data the polygon-hover system depends on may simply be missing, and
this wouldn't show up as a text-field difference since it might not exist as text at all.

What **does** work: a real `CEntityAnimated` with `Model=<an actual door model>` and
`Activity=Array{Activity=CDoorAI{After Opened=CMultipleActionsAction{Action=Array{Action=
CRelocateAction{...}}}, After Closed=CAddAIAction{...re-adds the open-interaction after
closing...}}, Activity=CAIInteractionSpecifier{Interaction Type=Interaction
Specifiers/GetCloseThen OpenDoor, Action=COpenDoorAction{Door Name=$Trigger}}}` — copy this
whole structure from a real door (e.g. `Herbalist door` in `Levels/1 Barcelona/Gate
District.zax`, `Model=Environments/Rethgorad/Town/WidowsDoor`) rather than a
`CFreeRangePoly` zone. The working theory: the door's actual 3D model provides real
collision geometry for the interaction/hover raycast to hit, whereas a polygon-only zone
apparently doesn't, at least not for anything we've hand-authored. **Prefer real
model-based doors over invisible trigger zones for any new interactive exit**, even though
zones are the pattern the shipped game itself uses everywhere.

**One concrete lead, not yet tested**: every real map carries 1-7 `Level Part=CWayPointsPolygon`
entries (a `Polygon=x, y, x, y, ...` list, `Insertion Priority=Primary`) and a map cloned
from the scratch template has **none**. That is a real, text-level difference — unlike the
"invisible baked data" theory above, it is something you can just add. If polygon-hover
zones are ever worth another attempt, add a `CWayPointsPolygon` covering the area first.
Note this is *not* needed for ordinary collision (see the next section), only a candidate
explanation for the interaction-zone failure.

## Walls, obstacles, and collision

There is **no collision layer and no nav mesh to author**. A wall, rock, tree, or crate is
a plain `Level Part=CEntityBase` under `Tree List=CSortList2D`, identical in shape to any
other entity, with a model and three flags:

```
Level Part=CEntityBase
{
    Name=
    Child List=
    Visible=1
    Collideable=1
    Half Height=0            <- 1 = low cover, 0 = full blocker (set the other to 1)
    Full Height=1
    Tries To Collide=0
    Has Hit Points=0
    Stationary=1
    Active=1
    Is Temporarily Excluded=0
    Is Marked For Deletion=0
    Activity=Array { Item Count=0 }
    Category=
    Team Number=Nutral
    Used In=QuestMode
    Current Target=
    Publisher=
    Model=Environments/Rethgorad/Town/Fence/Fence A
    Position X=120
    Position Y=160
    Rendering Height=0
    Rendering Height Float=0
    Cur Sequence=Idle
}
```

Gate District has 1048 of these and nothing else. `Tree List=CSortList2D` has **no
`Item Count=`** — entries are just repeated `Level Part=` keys, so adding entities means
appending blocks, with no count to keep in sync.

- **Collision comes from the model, not from authored geometry.** Only 10
  `Properties.txt` files exist in the entire game and exactly one has a non-empty
  `Bounds Poly=`, so per-object collision polygons are the rare exception, not the rule.
- **`CWayPointMap` is empty in every map**, real ones included — it holds only
  `MinDistBetweenWayPoints=20` / `MaxDistToConnect=39`, so waypoints are generated at
  load. Nothing to bake. `CWayPointsPolygon` appears 1-7 times per map as a supplementary
  hint and is not required.

### Environment models are sprites, and the letter suffix is the rotation

`Environments/.../Wall 01 A` through `Wall 01 H` are eight pre-rendered facings of the
same asset — `.mdl16` sprites, the same format as inventory icons (`mdl16_format.py`,
`docs/mdl16-icon-format.md`). **There is no rotation field**; you pick the facing by
choosing the letter, and the letter also determines which direction a run tiles in.

### Not every asset is built to tile — check before building a wall out of one

Scanning all 201 shipped `.zax` files for runs of 4+ identical models on a constant step
vector: **the `Fence` set never forms a run.** Only two 3-piece fence chains exist in the
entire game. Fences are scatter decoration, not wall segments, and laying them end-to-end
produces a visible jog at every joint. Assets that genuinely tile, with their measured
step vectors:

| model | run | step | direction |
|---|---|---|---|
| `Mountain/Inside/Walls/Wall 01 A` | 8+ | `(124, -7)` | +X, north face |
| `Mountain/Inside/Walls/Wall 01 E` | 10 | `(121, -7)` | +X, south face |
| `Mountain/Inside/Walls/Wall 01 C` | **21** | `(10, 88)` | +Y, east face |
| `Mountain/Inside/Walls/Wall 01 G` | 12 | `(11, 86)` | +Y, west face |
| `Druid Grove/Walls/StrateWall/StrateWall A` | 6 | `(145, 28)` | outdoor variant |
| `Outpost/Transformed Region/Walls/Wall 02/Wall 02 B` | 7 | `(63, -53)` | |

`Wall 01 A/C/E/G` is a complete four-sided set: A north, C east, E south, G west
(derived by comparing each variant's centroid against the centroid of all pieces in
`06 Chamber of Torment.zax`).

**The perpendicular component is not noise.** `(124, -7)`, not `(124, 0)` — the sprite
depicts a run tilted a few degrees off the world axis, and flattening that to zero
rotates every piece slightly against the run, which reads in-game as "the pieces don't
quite line up". Use the measured vector exactly.

To find the step vector for any asset, look for **collinear chains**, not
nearest-neighbour pairs — scattered decoration produces plenty of plausible-looking pair
offsets that are not tiling vectors. And **exclude your own map from the corpus**: a
deployed work-in-progress sitting in `<game>/data/Levels/` will happily "confirm"
whatever spacing you already guessed.

### Sprite footprint decides clearance, and some props are enormous

World units are roughly 1:1 with sprite pixels, so read the footprint straight off the
sprite header before placing anything:

```python
h = mdl16_format.find_header(open(path,"rb").read());  print(h.width, h.height)
```

`Rethgorad/Town/Rock/Rock B..F` are **311-338 px wide**; `Chests/Chest2` is **74**. A
chest placed 166 units from a rock ends up *inside* it — the rock's half-width alone is
169. Trees are 215-229, `Trees/Tree3` 152, carts ~100-166, benches/cannonballs ~55.

Worth gating placement on an assertion rather than discovering it in-game: for every prop,
require `distance >= own_half_width + other_half_width + margin` against all walls, all
other props, and every entity the player must reach (chest, NPC, door, spawn). That check
caught two bad layouts here before either reached a build.

## Quest mechanics

- `Resources/.../<Name>.Quest.txt`: `CQuestDefinition { Name=..., States=Array {...},
  Sub-Quest of=!None }`. States are optional narrative checkpoints
  (`CQuestStateDefinition{Text=..., ID=<8-char alnum token>}`) shown in the quest log —
  many shipped quests have zero states and rely purely on the status flag.
- **Status** (Active/Completed/Failed, via `CSetQuestSatusToCompletedAction` /
  `CIsQuestCompletedAction`) and **State** (which narrative checkpoint ID is "current",
  via `CActivateQuestStateAction` / `CIsQuestStateTheCurrentStateAction`) are two
  independent axes. Activating a state does not imply completion or vice versa — gate
  reply visibility on both explicitly if you need "given but not yet turned in":
  ```
  Custom Requirement=CAND
  {
  Operand1=CActionExpression { Action=CIsQuestStateTheCurrentStateAction{Quest=..., State=...} }
  Operator=
  Operand2=CActionExpression { Action=CNotAction { Action=CIsQuestCompletedAction{Quest=...} } }
  }
  ```
  (`Operator=` is left blank in every base-game example — this is normal, not a bug.)
- Quest resource paths in `Quest=` fields are `Resources/`-relative with the
  `.Quest.txt` suffix stripped, e.g. `Levels/1 Barcelona/Quests/Gate District/My Quest`.
- Give a quest reply top-level visibility (not buried in a submenu) by duplicating it
  into every one of an NPC's greeting/return-visit node variants — this is the base
  game's own convention (verified: the "wererat cure" quest reply is duplicated
  identically across 6-7 different greeting nodes for the same NPC).

## Checking "has N of an item" — no built-in primitive

`CActionCheckForInventoryItem` / `CActionRemoveInventoryItem` only test/remove a single
unit (presence, not count), even for stackable items (`CInventoryItemPlugInBehaviorMergeMultipleInstances`).
There is no `Desired Minimum Count`-style field for inventory items (that field exists on
`CCheckExistenceAction`, but only for named triggers/flags, never seen used against an
inventory item in the whole game). To require exactly N copies, nest check→remove N times
so each removal decrements the stack and the next check reflects what's left:

```
Custom Action=CIfAction
{
If=CActionCheckForInventoryItem { Who to give check=$instigator, Inventory Item To Check For=<item> }
Then=CMultipleActionsAction { Action=Array { Item Count=2
    Action=CActionRemoveInventoryItem {...}
    Action=CIfAction { <repeat for unit 2, then unit 3, with the real reward in the innermost Then> }
}}
Else=
Return failure if the If failes=0
}
```

This is untested elsewhere in the shipped game (built from confirmed-working primitives,
not copied from precedent) but works correctly in practice.

## CRITICAL gotcha: `If=` wants a bare action, `Custom Requirement=` wants it wrapped

This produces the runtime error *"tried to use a CActionExpression for a If when a
CAction is expected"*:

```
Custom Action=CIfAction
{
If=CActionExpression { Action=CActionCheckForInventoryItem {...} }   <- WRONG in an If= field
```

`CIfAction`'s own `If=` field takes the condition/action type **directly**, no wrapper:

```
If=CActionCheckForInventoryItem {...}                                 <- correct
```

The `CActionExpression{Action=...}` wrapper (and combinators like
`CAND{Operand1=..., Operator=, Operand2=...}`, `CNotAction{Action=...}`) is only for
**`Custom Requirement=`** fields on dialogue replies (which need an "Expression" type, not
a bare "Action"). Mixing these two contexts up is an easy, silent-until-runtime mistake.

## CRITICAL bug: `CGiveExperiencePointsToAllPlayersAction` does nothing when called inline from a DialogTree reply

Extensively confirmed (5+ isolated tests: varying the amount 1/25/100, flat vs. nested
structure, byte-exact copy of a "working" shipped quest's reward block including its
karma-modifier sibling, using `$instigator` vs. free-text for `Get XP Frome`, and a
fully unconditional/unnested standalone test reply) — **none granted any XP** when the
action was invoked as an inline `Custom Action=` on a dialogue reply. Gold
(`CGiveMoneyToAllPlayersAction`) and quest-completion actions in the exact same array
work every time; only this action silently no-ops.

`CGiveExperiencePointsToCharacterAction` is a registered **alias of the same class**, not
a separate implementation — don't expect it to behave differently.

**The fix**: invoke it indirectly via a standalone `.can` file dispatched through
`CUseCannedActionAction`, instead of putting it inline in the dialogue tree. This is the
same dispatch mechanism used for combat-kill XP:

1. Create `Resources/.../SomeName XP.can`:
   ```
   CCannedObject
   {
       Object=CGiveExperiencePointsToAllPlayersAction
       {
           Get XP Frome=$Trigger
           Experience Points To Add=100
       }
       Use=Shared Global Instance
   }
   ```
2. In the dialogue reply's `Custom Action=`:
   ```
   Custom Action=CUseCannedActionAction
   {
   Canned Object=Levels/.../SomeName XP
   }
   ```

Confirmed working in-game. Root cause is almost certainly something about the DialogTree
Custom Action dispatch pathway specifically (not field values/types — a "type 3 =
debug-only field" theory was explored via decompilation and looked plausible but was a
red herring; the canned-action indirection is the real, verified fix). If you hit an
action that silently no-ops from a dialogue Custom Action, try this same indirection
before assuming the field values are wrong.

`CGiveEnoughExperiencePointsToLevelUpAction{Character to give experiecne to=$instigator}`
(note the authentic typo "experiecne") is a simpler, separately-confirmed-working
mechanism if you want a guaranteed level-up rather than a specific point amount — used
repeatedly in the game's own dev cheat scripts.

## Editor-only content — don't mistake it for working mechanisms

Entities with `Model=Editor/...` and `Visible=0` (e.g. `CShowExperiencePoints`,
`CLabelPrinterAI`) are level-editor design-time annotations/audit tooling, not runtime
game logic. A `Dynamic Properties` block with a stray `Experience Points=120`-style field
next to one of these is a designer's bookkeeping note, not evidence of a working
in-game mechanism.

## Standard editing workflow

1. **Back up** `data.dat` once before any mod work (`data.dat.original.bak`).
2. Edit files directly in the unpacked `data\` directory.
3. Before repacking, sanity-check the edited `.DialogTree`/`.txt` file:
   `grep -c '^{$'` should equal `grep -c '^}$'` (brace balance).
4. Confirm `Lionheart.exe` is **not running** (`tasklist | grep -i lionheart`) — repack
   will fail with `PermissionError`/`WinError 5` otherwise, and the game must be fully
   closed (not just showing an error dialog) to release the file lock.
5. Repack: `python archive.py repack <data dir> <data.dat> --compression store`.
6. Validate: entry count, `testzip()`, compress_type set == `{0}`, and spot-check the
   specific strings/values you changed via `zipfile.read()` before telling the user to
   test — repacks of this size take several minutes, so verify structurally before
   spending a test cycle.

## Packaging & distributing mods

`data.dat` can't be redistributed (it's ~1.6GB of the copyrighted game itself), and a
full-file replacement gives no way for two people's mods to coexist. Instead, mods are
lightweight overlays built with `modmanager.py`. Registry lives inside the game directory:

```
<game-dir>\data.dat                  live, built file the game reads
<game-dir>\data.dat.vanilla.bak      pristine original, created once by `init`, never overwritten
<game-dir>\mods\installed\<id>\      installed mod packages
<game-dir>\mods\enabled.json         ordered list of enabled mod ids (last wins on file conflict)
```

A mod package is a folder: `mod.json` (plain JSON metadata — id, name, version, author,
description, explicit `files` list) plus `files/` mirroring the `data.dat` path structure,
containing **only** the files the mod adds or changes.

```
python modmanager.py init <game-dir>              # one-time: back up vanilla, set up registry
python modmanager.py package <edited-dir> <vanilla-dir> <output-dir> --id --name --version --author --description
python modmanager.py install <mod-dir-or-zip> <game-dir>
python modmanager.py list <game-dir>
python modmanager.py enable/disable <id> <game-dir>
python modmanager.py build <game-dir>              # vanilla + enabled mods -> data.dat (store mode, validated)
python modmanager.py restore <game-dir>             # revert to pristine vanilla
```

`build` always starts from a **fresh unpack of `data.dat.vanilla.bak`**, never from the
live `data/` folder or a previous build — this is what makes clean enable/disable/reorder
possible. It validates the built archive (`testzip`, all `compress_type == 0`) before
touching the live `data.dat`, and refuses to run while `Lionheart.exe` is open.

**Gotcha: `build` reads from `<game-dir>\mods\installed\<id>\`, a copy `install` made —
not from the mod source folder.** Editing a file inside a mod's own repo/source
`files/` directory (e.g. iterating on a `.mdl16` icon or tweaking a `.can`) has **no
effect** on the next `build` unless `install` is rerun first to refresh the installed
copy (`shutil.copytree`, full overwrite). `build` succeeding and reporting a normal
sync count gives no signal that this happened — it happily rebuilds from stale
installed content. Rule of thumb while iterating on a mod already installed: `install`
then `build`, every time a source file under `mods/<id>/files/` changes, not just the
first time.

## CRITICAL: `<game-dir>\data\` is a loose mirror that SHADOWS `data.dat` — `build` must sync it

This install has a **complete loose copy of data.dat's entire contents** at `<game-dir>\
data\` (confirmed directly: file count matches data.dat's entry count almost exactly,
~19000 files, and the vast majority carry the game's original 2001 dev-build timestamps —
this is not a subset, it's effectively the whole archive, unpacked once and left in place).

**The game reads from this loose tree in preference to `data.dat` whenever a loose file is
present**, confirmed by direct observation: a `data.dat` rebuild — verified byte-correct via
`zipfile.read()` immediately beforehand — produced **zero** in-game behavior change across
five consecutive rebuild-and-test cycles, because the loose copy of the touched file was
untouched and kept shadowing it. The moment the loose file was manually overwritten with
the same content, the change took effect immediately. For paths with **no** pre-existing
loose file (freshly-authored resources, e.g. a brand-new `.InventoryAddition`), the game
appears to fall back to reading `data.dat` and then **writes its own loose copy** on that
first read — which then shadows all *future* `data.dat` rebuilds the same way, so this
isn't a one-time gap that closes itself; it recurs for every new resource the first time
it's actually read in-game.

This resolves the "Known open issue" that used to be documented here (a ~267-file,
never-root-caused divergence between `data.dat.vanilla.bak` and the live `data\`
directory): the *original* editing workflow documented above — "edit files directly in the
unpacked `data\` directory, then repack" — always kept `data\` and `data.dat` in sync by
construction, because `data.dat` was generated *from* `data\`. `modmanager.py build`
doesn't do that — it unpacks fresh from `vanilla_bak` into an ephemeral scratch dir that's
deleted after repacking, and until this was fixed, never touched `data\` at all. Both
workflows are legitimate ways to end up with a working `data.dat`; only one of them also
keeps the loose mirror in sync, and the game depends on that mirror, not on `data.dat`
directly.

**Fix, now built into `build`**: after a successful repack, `cmd_build` copies every file
touched by an enabled mod from the scratch dir into `<game-dir>\data\` too (only if that
directory exists), so `python modmanager.py build <game-dir>` alone is sufficient again —
no separate manual sync step needed. If you ever bypass `modmanager.py` (hand-edit
`data.dat` with `archive.py` directly, or write a resource via some other script), remember
to `cp` the same file into `<game-dir>\data\<same relative path>` yourself, or the game
will not see it no matter how many times you rebuild `data.dat`.

## Reverse-engineering tips (Ghidra/ReVa)

- Class/action field registration functions are generic and repeated per-class; look for
  the field's own name string (e.g. `"Get XP Frome"`) and use
  `find-constant-uses` on its address if `get-strings`'s `referencingFunctions` comes back
  empty — Ghidra's automatic xref analysis misses strings used as `PUSH` immediates in
  some of this codebase's registration patterns.
- Comparing a known-working action's field registration against a suspect one
  (same registrar function, different type-code parameter) is a good way to find real
  differences, but decompiled parameter semantics are unlabeled guesses — treat
  conclusions from this alone as hypotheses to test in-game, not proven fixes. The
  canned-action-indirection fix above was only confirmed by actual in-game testing after
  the type-code theory failed to pan out.
- **Check for existing community documentation before reverse-engineering a format from
  scratch.** A small but real Lionheart modding community exists; `lionheart.eowyn.cz` is
  a wiki documenting `.zax`/`.way`/`.frm16`/`.seq16` and partial `.mdl16` notes (blocks
  direct `WebFetch`/`curl`, even with a browser user-agent and via web.archive.org — only
  reachable through `WebSearch` result snippets, which is enough to extract real technical
  detail with enough targeted queries). It independently confirmed most of the `.mdl16`
  icon RLE reverse-engineering done via pure Ghidra analysis (see
  `docs/mdl16-icon-format.md`). It also appeared to be wrong in one place — a DWORD+LUT
  structure we measured as absent from item icons — but the wiki was right and our
  measurement was the thing that was broken; that mistake cost a long investigation.
  Cross-check both ways: when community docs and your own byte-level reading disagree,
  suspect your reading too, not just theirs.
