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
| **Wall Run** (`R`) | Drag to lay a whole run of a tiling piece in one undo step. Start on an existing piece to extend it; drag along a run with holes in it to fill only the holes. |
| **Terrain Paint** (`T`) | Paint ground textures onto the terrain grid. `[` and `]` resize the brush. |
| **Eyedropper** (`I`, or hold `Alt`) | Click anything on the map to select its model in the palette. |
| **Entity Script** | Edit the action tree on the selected chest, NPC, generator or door — see below. |
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
