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
- **`mods/`** — real, working mods built with this toolkit (see below).
- **`docs/`** — reference material on specific game systems (e.g. the ending-branch
  structure) that's useful context but not itself a modding how-to.
- **`.claude/skills/lionheart-modding/SKILL.md`** — the actual modding reference: file
  formats, gotchas, confirmed working patterns for adding NPCs/quests/dialogue/maps, and
  the mistakes that cost real debugging time so you don't have to repeat them. Start here
  if you want to build something new.
- **`examples/`** — worked-example scratch files from building the first quest, kept as a
  reference for the DialogTree-splicing pattern.

## Requirements

- Windows, with the game installed (tested against the GOG release).
- Python 3.
- The game closed whenever you repack `data.dat` — the file is locked while running, and
  writing to it while the game holds it open will fail.

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
anything else with a fatal error — see `SKILL.md` for why.

## Mods included

| Mod | What it does |
|---|---|
| [`wolf-pelts-for-quinn`](mods/wolf-pelts-for-quinn/) | A new quest: Quinn the herbalist asks you to bring three wolf pelts to test for magical corruption. |
| [`marco-the-pickpocket`](mods/marco-the-pickpocket/) | A new NPC near the Gate District blacksmith. Reacts to whether you have the Thief perk — either a warning about the streets at night, or a shop stocked with rogue-friendly gear. |
| [`test-pocket`](mods/test-pocket/) | *(work in progress)* A brand-new standalone map, reachable through Quinn's shop, with its own NPC and a fetch quest that turns into a fight. |

Each mod's own README has install notes and anything specific to that mod (e.g. save
requirements for newly-added content — see below).

## A gotcha worth knowing before you install anything

New entities (NPCs, generators, triggers) added to a level do **not** appear on a save
that has already visited that level — the game locks in a level's entity list the first
time you ever enter it on a given save. Editing existing NPCs' dialogue *does* refresh on
revisit; only brand-new entities are affected. If content from one of these mods doesn't
show up, try a save that's never been to that specific area, or a new game. Full
explanation and the mechanism behind it is in `SKILL.md`.

## Building your own mod

1. Read `SKILL.md` — it covers the resource format, the DialogTree format, quest
   mechanics, adding NPCs, adding maps, and the specific bugs/gotchas already found the
   hard way.
2. Unpack, edit, repack (see above), testing in-game as you go.
3. Once it works, package it: create `mods/<your-mod-id>/mod.json` (see an existing mod
   for the schema) and copy only the files you actually changed into
   `mods/<your-mod-id>/files/`, mirroring their path under `data\`.
4. `modmanager.py install` it into your local game to confirm the packaged version still
   works, same as any other mod.
