# LionheartModTools

Reverse-engineered modding toolkit for **Lionheart: Legacy of the Crusader** (2003,
Reflexive Entertainment / Black Isle). Everything here was built by unpacking the game's
own `data.dat` archive, reading its plain-text resource format, and cross-referencing
against decompiled game logic (Ghidra/ReVa) where the text alone wasn't enough. There's no
official modding support or SDK for this game — this project exists to establish one.

## What's here

- **`resource_format.py`** — parser/serializer for the game's brace-delimited resource
  text format (`ClassName { Key=Value ... }`). Byte-identical round-trip.
- **`archive.py`** — unpack/repack `data.dat`.
- **`modmanager.py`** — package, install, enable/disable, and build mods as layered
  overlays on top of a pristine vanilla backup.
- **`mdl16_format.py`** — read, recolor, and author inventory icon art (`.mdl16`/`.frm16`
  2D sprites). Authoring genuinely new icon art works; see below.
- **`gr2_format.py`** — the 3D character/model format (`.gr2`), with glTF round-tripping.
- **`mapedit.py`** — a GUI map editor: place scenery, lay wall runs, paint ground
  textures, and deploy to the game. See below.
- **`mapedit_core.py`** / **`zax_render.py`** — the editor's headless half. Loading and
  editing a `.zax`, the sprite catalogue, placement validation, and a `.zax`-to-PNG
  renderer that needs no GUI at all.
- **`script_schema.py`** — what the game's entity scripts are made of: the action classes,
  their fields, defaults and value sets, all derived from the shipped maps. Headless, and
  useful on its own for reading or generating scripts. `mapedit_script.py` is the editor
  dock over it.
- **`dialogtree_format.py`** — reader/writer for `.DialogTree`, the one format
  `resource_format.py` can't handle. Byte-identical round-trip on all 341 shipped files.
  `dialogedit.py` is the visual editor over it; see below.
- **`mods/`** — real, working mods built with this toolkit (see below).
- **`docs/`** — format writeups and how-tos: adding a new item, adding a new character,
  the `.mdl16` icon format, the `.gr2` model format, the map/terrain format and editor
  design, and background on specific game systems like the ending-branch structure.
- **`.claude/skills/`** — the modding references, written as skills so an agent picks them
  up automatically. `lionheart-modding` is the main one: file formats, gotchas, confirmed
  working patterns for NPCs/quests/dialogue/maps, and the mistakes that cost real
  debugging time so you don't repeat them. `adding-a-new-weapon` is a focused end-to-end
  recipe. **Start with `lionheart-modding` if you want to build something new.**
- **`examples/`** — worked-example scratch files from building the first quest, kept as a
  reference for the DialogTree-splicing pattern.

## Requirements

- Windows, with the game installed (tested against the GOG release).
- Python 3.
- The game closed whenever you repack `data.dat` — the file is locked while running, and
  writing to it while the game holds it open will fail.

Everything is stdlib-only except the map editor's window, which needs PySide6
(`pip install -r requirements.txt`). The editor's headless half — loading maps, validation,
PNG rendering — has no third-party dependencies either.

## Quick start: installing a mod

```
cd LionheartModTools
python modmanager.py init "<path to game folder>"
python modmanager.py install "mods/<mod-name>" "<path to game folder>"
python modmanager.py build "<path to game folder>"
```

`init` takes a one-time backup of your vanilla `data.dat` (so it can always be restored)
and sets up the mod registry. `install` registers a mod and enables it. `build` rebuilds
`data.dat` fresh from the vanilla backup plus every enabled mod, in load order — it never
layers on top of a previous build, so re-running it is always safe.

To go back to an unmodified game at any point:

```
python modmanager.py restore "<path to game folder>"
```

## Quick start: manual editing

For direct experimentation instead of the mod-package workflow:

```
python archive.py unpack "<game>\data.dat" "<game>\data"
# ...edit files under <game>\data...
python archive.py repack "<game>\data" "<game>\data.dat" --compression store
```

**`--compression store` is not optional.** The shipped exe's archive parser rejects
anything else with a fatal error — see the `lionheart-modding` skill for why.

## Mods included

| Mod | What it does |
|---|---|
| [`wolf-pelts-for-quinn`](mods/wolf-pelts-for-quinn/) | A new quest: Quinn the herbalist asks you to bring three wolf pelts to test for magical corruption. |
| [`marco-the-pickpocket`](mods/marco-the-pickpocket/) | A new NPC near the Gate District blacksmith. Reacts to whether you have the Thief perk — either a warning about the streets at night, or a shop stocked with rogue-friendly gear. |
| [`great-healing-potion`](mods/great-healing-potion/) | Three new healing potions above vanilla's Extra Healing, each with its own recolored icon. |
| [`ratsbane-sword`](mods/ratsbane-sword/) | Lucia Wererat drops a unique short sword that deals bonus disease damage against wererats. |
| [`bloodletter-scimitar`](mods/bloodletter-scimitar/) | A unique scimitar with a 30% chance on hit to inflict a bleeding wound. Ships the project's first genuinely new icon art. |
| [`test-pocket`](mods/test-pocket/) | *(work in progress)* A brand-new standalone map, reachable through Quinn's shop, with its own NPC and a fetch quest that turns into a fight. |

Each mod's own README has install notes and anything specific to that mod (e.g. save
requirements for newly-added content — see below). Some mods depend on another being
installed and loaded first; their READMEs say so.

## A gotcha worth knowing before you install anything

New entities (NPCs, generators, triggers) added to a level do **not** appear on a save
that has already visited that level — the game locks in a level's entity list the first
time you ever enter it on a given save. Editing existing NPCs' dialogue *does* refresh on
revisit; only brand-new entities are affected. If content from one of these mods doesn't
show up, try a save that's never been to that specific area, or a new game. Full
explanation and the mechanism behind it is in the `lionheart-modding` skill.

## Custom art

Inventory icons (`.mdl16`/`.frm16`) are fully decoded, and you can author new ones:

```python
import mdl16_format as m
out = m.build_icon_file(donor_bytes, width, height, rows)   # rows: (r,g,b,a) tuples
m.verify_icon(out)      # always, before deploying
```

`recolor_icon_in_place()` is the cheaper path when an existing silhouette is already
right. Either way run `verify_icon()` first — it re-parses the file the way the engine
does, and a malformed icon crashes the game on opening the inventory screen.

This took a while to get right. Each buffer carries an on-disk table of per-row byte
offsets, and the engine decodes every row by seeking to `table[y]` and resetting its
x-counter, so rows are strictly opcode-aligned and those offsets have to be exact. Full
writeup, including the decompiled evidence and how the earlier attempts went wrong, is in
[`docs/mdl16-icon-format.md`](docs/mdl16-icon-format.md).

3D character models (`.gr2`) round-trip through glTF for editing in Blender — confirmed
in-game for static mesh edits; animated edits aren't proven yet. See
[`docs/gr2-format.md`](docs/gr2-format.md) and
[`docs/gltf-roundtrip.md`](docs/gltf-roundtrip.md).

## The map editor

The game's own editor was stripped from the retail build — the F6 handler is gone, not
hidden — so hand-editing `.zax` text was the only way to author a map. `mapedit.py` is a
replacement:

```
pip install -r requirements.txt
python mapedit.py "mods/test-pocket/files/Levels/1 Barcelona/Test Pocket.zax"
```

It opens the map with its ground and all 4787 placeable environment sprites, and edits the
file in place under `mods/`, never the installed game.

| | |
|---|---|
| **Palette** | Every environment sprite, searchable, with previews. Pieces that tile are marked with their step; scatter-only families (the whole `Fence` set) are flagged so nobody builds a wall out of them again. |
| **Place / drag / delete** | Drag from the palette or click to place; rubber-band select; `Delete` or `Backspace` to remove. Full undo. |
| **Pan** (`H`) | Left-drag to move around. Hold `Space` to pan without leaving whatever tool you're in — middle-drag works too. |
| **Wall Run** (`R`) | Drag to lay a whole run of a tiling piece in one undo step. Start on an existing piece to extend it; drag along a run with holes in it to fill only the holes. |
| **Terrain Paint** (`T`) | Paint ground textures onto the terrain grid. `[` and `]` resize the brush. |
| **Eyedropper** (`I`, or hold `Alt`) | Click anything on the map to select its model in the palette. |
| **Entity Script** | Edit the action tree on the selected chest, NPC, generator or door — see below. |
| **Open dialogue** (`Ctrl+D`) | Double-click an NPC to open the `.DialogTree` its scripts point at, in the dialogue editor. |
| **Validation** | Live list of missing sprites, overlapping footprints, off-map coordinates, and gaps in wall runs — the checks that used to be throwaway assertions. |
| **Deploy** (`Ctrl+B`) | Save, then `modmanager` install + build, with a progress bar. |

Markers for spawn points, doors, generators and chests are drawn faded with name labels,
because you place props *relative* to them — a chest ended up inside a rock once for
exactly this reason.

It deliberately does not author interaction zones: `CFreeRangePoly` hover has never worked
in a hand-built map, and a tool must not offer something whose output silently does
nothing. Quest and DialogTree *files* are also outside it — those are separate files with
their own structure, and text editing serves them well.

### Entity scripts

What a chest gives you, when an NPC turns hostile, what a door does when opened — all of
it is a tree of `C*Action` nodes hanging off the entity. There are 125 such classes in the
shipped game and about 44,600 nodes, with real control flow: conditionals, sequences,
delays, randomisation.

The **Entity Script** dock shows that tree for whatever is selected, in plain language:

```
Activity
  Activity: CAIInteractionSpecifier
    Action: Do all of these  --  6 action(s)
      Action  (6)
        Action: Play an animation  --  animate Opening
        Action: Give an item  --  give Inventory Items/Necklace
        Action: Give an item  --  give Inventory Items/Scimitar
          Additions to add  (1)
            Addition to add: Inventory/.../Weapons/Bloodletter
```

Click a row to edit its fields; **Add / Delete / Up / Down** restructure it; order in an
array is execution order. It all goes through the same undo stack as the rest of the
editor.

`script_schema.py` holds the curated part: 20 action classes, covering **61% of every
action node in the game**, labelled by what they do rather than by class name — "Turn
hostile" rather than `CGoToCombatAction`. Every field list, ordering, default and choice
list in it was derived by sweeping all 201 shipped maps, not guessed. The other 104
classes are not hidden; they render with their raw fields still editable, so nothing in a
map is unreachable.

Two invariants the dock maintains, both established from that same sweep:

- **`Array` nodes declare their own length**, in `Item Count` or `Array Count`, and it is
  exact in 107,670 of 107,802 cases. Every structural edit keeps it in step. The 132
  exceptions declare no count at all and are fixed-length stat tables (one entry per
  attribute, skill or damage type) — so a count is never *added* where the game ships
  without one.
- **Arrays that hold item paths reject actions.** `Additions to add` is a list of
  inventory-addition strings, not actions; putting a node there would write an object
  where the engine reads a string.

The load/save contract holds throughout: loading a map and saving it unedited is
byte-identical, and changing one field changes exactly one line.

For a map render without the GUI:

```
python zax_render.py "<map>.zax" out.png --scale 0.25
```

Design notes, the terrain format, and the reasoning behind the editor's less obvious
behaviour are in [`docs/map-editor-design.md`](docs/map-editor-design.md).

## The dialogue editor

Dialogue is a graph — NPC lines as nodes, player replies as edges — and a graph is the one
shape a text editor shows badly. `dialogedit.py` draws it:

```
python dialogedit.py                      # opens the first dialogue under mods/
python dialogedit.py "<path>.DialogTree"
```

It also opens from the map editor: double-click an NPC, or `Ctrl+D`. An NPC's dialogue
isn't a field on the entity — it's buried in a `CDisplayDialogTreeAction` somewhere down
the action tree — so the editor digs the reference out and resolves it the way the game
would: the mod owning the map first, then any other mod, then the installed game.

A **Files** dock lists every `.DialogTree` your mods ship, grouped by mod, with a filter
box. Click a node to edit its line and its replies; rewire a reply from a dropdown or by
clicking its new target in the graph. Green is the entry node, amber means nothing links
to it. Left-drag pans, the wheel zooms, `Ctrl+L` re-layouts, and there's full undo.

You can also write new dialogue, not just edit it: `Ctrl+N` adds a node, `Ctrl+R` adds a
reply to the selected one, `Ctrl+Shift+Del` deletes a node — with a warning naming how
many replies would be left dangling. **Renaming** a node is a field at the top of the Node
dock, and it retargets every reply in the file that points there, because a rename that
leaves the links behind is precisely how the game ended up with 84 broken ones. What it
cannot follow is a reference from *outside* the file: map scripts name nodes directly
through `CDisplayDialogTreeAction`, so renaming a tree's entry node means checking the map
that opens it. Duplicate and empty IDs are refused outright — matching is by name, so a
duplicate makes every link to either node ambiguous.

Files inside the installed game open **read-only** — the toolchain layers mods over a
pristine backup, so writing into the install would corrupt what every rebuild restores
from. Copy one into a mod's `files/` tree to edit it.

The **Problems** dock lists replies pointing at nodes that don't exist, and nodes nothing
links to, each clickable to jump there. That is worth having: in-game a dangling reply
just refuses to advance the conversation, and says nothing about why.

### Seeing what you changed

Problems only catches edits that break something, and the dangerous ones don't. A reply
retargeted to a node that *exists* is a valid file and a broken conversation — no
validator can flag it. So every edit is surfaced three ways:

- The **Edits** dock lists them in order, naming what changed and to what:
  `Retarget "Not yet. I'll return when..." in 1 Conversation Start: 1 Conversation Start
  -> 10 Transformation`. It's the undo stack, so undo and redo move through it.
- The **status bar** says the same thing as each edit lands.
- **The graph marks it**: a blue dot on every node changed since the file was opened, and
  a thicker blue edge on every link added or retargeted.

The marks are derived by comparing against the file as loaded, not tracked as you go — so
an undo clears them, a redo restores them, and editing a value back to what it was leaves
nothing marked. They persist across a save, because the question they answer is "what
have I changed this session", which is what you want to review before deploying.

This exists because of a real incident: a stray mouse wheel over a reply's target dropdown
retargeted "Not yet, I'll return when I have it" to the quest's payoff node. Nothing said
so, and the file was saved. The wheel is now ignored unless the box is focused
(`qtwidgets.NoScrollComboBox`, used by the script dock too), but the guard only covers the
bug that was found — the three displays cover the next one.

### Two things about the format

Both were established by measuring the 341 shipped files, and both are things a naive
reader gets wrong.

**Nothing in a `.DialogTree` is indented** — including inside the brace blocks embedded in
`Custom Action` and `Custom Requirement`. So a line reading `Node ID=3 Angry` is a new
dialogue node at brace depth 0 and a field of an embedded action at depth 5, textually
identical. Not hypothetical: `Node ID` occurs 5323 times at depth 0 and 4 times deeper.
`dialogtree_format.py` tracks brace depth; a line splitter invents four phantom nodes.

**Node IDs are matched case-insensitively and trimmed.** Compared exactly, 369 replies in
78 shipped files point at nothing — including the Goodbye reply of the first NPC in the
game, which plainly works. 242 differ only in case (`10 Goodbye` vs `10 goodbye`), 31 only
in trailing space. The leading number is *not* the key either, tempting as it looks: 534
numbers are reused within their own file (`1 Conversation Start Male` / `Female` /
`Angry`). The full normalised string is unique across all 341 files with no collisions.

Under the correct rule, 84 replies in vanilla are genuinely broken (0.8%) — including a
`5 goobye` typo in Goblin Sapper that dead-ends the conversation. (It reads 96 if you
count the twelve replies whose target is a single space. They aren't broken: 2263 replies,
21% of the game, legitimately end a conversation with an empty target, and a space does
the same thing.)

One more rule, found while adding reply authoring: **a blank line goes before every reply
and nowhere else.** No node in the corpus ends on a blank — 3510 of 3510 end on a key.
Writing the separator after a reply instead of before produces a layout that appears
nowhere in the game's own files.

The round-trip is byte-identical across all 341 shipped files, which was the gate for
building anything on top of it.

## Building your own mod

1. Read [`.claude/skills/lionheart-modding/SKILL.md`](.claude/skills/lionheart-modding/SKILL.md)
   — it covers the resource format, the DialogTree format, quest mechanics, adding NPCs,
   adding maps, and the specific bugs/gotchas already found the hard way. For a weapon
   specifically, [`adding-a-new-weapon`](.claude/skills/adding-a-new-weapon/SKILL.md) is a
   complete worked recipe.
2. Unpack, edit, repack (see above), testing in-game as you go. If the mod touches a map,
   use `mapedit.py` rather than hand-editing `.zax` text.
3. Once it works, package it: create `mods/<your-mod-id>/mod.json` (see an existing mod
   for the schema) and copy only the files you actually changed into
   `mods/<your-mod-id>/files/`, mirroring their path under `data\`.
4. `modmanager.py install` it into your local game to confirm the packaged version still
   works, same as any other mod.
