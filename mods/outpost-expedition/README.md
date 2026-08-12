# Outpost Expedition

Makes the game's unused **Oupost** map reachable.

`Levels/Oupost.zax` ships with Lionheart and nothing links to it: 934 entities of Outpost
/ Dwarf Region level with working doors and a spawn point, and no NPCs, dialogue or
quests. It is the only non-test map in the game that is unreachable — see
[`docs/cut-content.md`](../../docs/cut-content.md) for how that was established.

Quinn the Herbalist gains a dialogue option, **"Take me to the old outpost."**, which
takes you there. A door beside the arrival point brings you back to her shop.

## What it changes

| File | Change |
|---|---|
| `Levels/Oupost.zax` | The developers' map, unmodified except for one added return door, and the spawn point moved (see below). |
| `Resources/Levels/1 Barcelona/Dialog/Gate District/Herbalist Dialogue.DialogTree` | The new reply, in all six conversation branches. |

## Requires `test-pocket`, and must load after it

Both mods ship Quinn's dialogue, and the last enabled mod shipping a path **wins
outright** — there is no merging. This copy is built on top of test-pocket's, so its
"Take me to the test pocket" option survives. Install this one second; `modmanager list`
should show it later in the order.

Building from vanilla instead would silently delete the other mod's option.

## Two deliberate choices

**The arrival point was moved.** The map's own `Start Here` sits at (4195, 3465), in the
bottom-right corner — which the render shows is a scratch area next to a stone donut and a
ring of loose tiles. Of the 128 entities near it, 86 are distinct sprites: a sampler, not
a room. The arrival is now (1950, 1200), inside the coherent cavern complex in the left
half, with 84 Outpost pieces within 500 units. The return door follows to (2020, 1200).

That is a four-line change to the file — two X/Y pairs. Nothing else about the map is
touched.

**The return door is a `WidowsDoor`** — a wooden town door, which looks wrong in a stone
outpost. It is structurally a copy of Test Pocket's Quinn Door, which is proven to work
in-game; the sprite was chosen for reliability, not for fit. Swapping it for one of the
`Environments/Outpost/Dwarf Region/Entrance/` doors is an easy improvement.

## Known rough edges

The map is a developer sandbox, not a finished level. Beyond the cavern complex you will
find a slab of grass and pine trees (wrong biome entirely), a radiating spoke structure,
and the geometry tests mentioned above, all separated by large empty gaps. Nothing has
been removed — the map is presented as the developers left it.

## Install

```
python modmanager.py install "mods/outpost-expedition" "<game dir>"
python modmanager.py build "<game dir>"
```

No save requirements: the outpost is a level no save has visited, so the
new-entities-on-old-saves limitation does not apply, and editing an existing NPC's
dialogue refreshes on revisit.
