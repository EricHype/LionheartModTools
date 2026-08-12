# Cut and unused content

An inventory of what ships inside `data.dat` but never appears in the game: unreachable
maps, dialogue nobody speaks, characters nobody spawns, quests nothing starts. Every
figure here is a measurement taken with this toolkit's own parsers, not a reading of
filenames.

This is **game content reference**, like `alamut-endings.md` — not a modding technique.
Its use is choosing where to start: finished assets are far cheaper to build a mod on than
new ones.

## Method, and four ways it goes wrong

The approach is: enumerate every file of a kind, then search the whole install for
anything naming it. Files nothing names are candidates. Four traps, each of which produced
a wrong answer on the first attempt here:

1. **Measure vanilla, not your install.** `data.dat` and the loose `data` directory both
   carry whatever mods you have built. Counting there inflates every total and hides
   anything a mod happens to reference. Read `data.dat.vanilla.bak`, the untouched copy
   `modmanager init` sets aside. Doing this wrong reported 341 DialogTree files; there are
   338, and the extra three were this project's own.

2. **Destinations are not only in maps.** `.DialogTree` files carry `CRelocateAction` too.
   Scanning only `.zax` for `New Map Name=` reports the Dream Djinni map and Town Exterior
   map as cut. They are not — Jafar's dialogue relocates to the Djinni map and the Knights
   of Saladin initiation is live shipped content. Scan `.zax`, `.DialogTree` and `.can`,
   for both `New Map Name` and `Other Map Name`.

3. **Path-shaped keys need normalising.** Some `.DialogTree` files sit under
   `data/Levels/` and others under `data/Resources/Levels/`, and references vary in case.
   Splitting on `/Resources/` to build a key silently yields absolute paths for the first
   group, which then match nothing — that alone produced four false "orphans" beginning
   `Generic `.

4. **Substring matches lie.** `barcelonavendor` looks referenced because
   `barcelonavendor beg` is referenced, and that is a different file.

**"Unreferenced" means no data file names it.** It does not always mean cut: the engine
names some things itself. The Alamut endings below are the clear case.

## Maps

200 shipped `.zax`. **31 are unreachable.**

30 are developer scratch: `Test Maps/` subfoldered by developer name (Lars, Simon, Ion,
Dan, James, Zach, Suyo, Bryce, Erik, Ernie), the `Multiplayer/` maps, and
`Erik Test map` / `Layered Character Rendering TestMap` at the root. Mostly small;
`Test Maps/Ion/QA Magic Items` (824 KB, 470 parts) is the exception and is useful as an
item reference.

The 31st is the only real one.

### `Levels/Oupost.zax` — 680 KB, 934 entities

The developers' spelling. A sandbox in the Outpost / Dwarf Region tileset. That tileset
shipped and is used heavily elsewhere — 73 maps, most of the Sewers chain,
`Sewers/04 Hall of Beggars` alone places 1235 pieces of it — so this is not unused art,
it is an unused *layout*.

Rendering it with `zax_render.py` settles what it is: four disconnected islands in empty
space, spanning 6323 × 3954.

| Region | Contents |
|---|---|
| Left half | A coherent dwarf-cavern dungeon — winding passages, chambers, stalactites, something molten. Real level geometry, roughly 2800 × 3000. |
| Top right | A rectangular slab of grass and pine, `Hamlet` art. The wrong biome entirely. |
| Bottom centre | A radiating spoke structure that is not architecture. |
| Bottom right | Corridors, then literal geometry tests: a ring of loose tiles and a stone donut. |

The sprite families say the same thing numerically: 728 `Outpost`, 48 `Hamlet`,
37 `Heart of Fire` — three unrelated biomes in one file.

Named entities: two doors, `Start Here`, `from outside`, `goto 1`, and a stray `fireball`.
No NPCs, dialogue or quests. It also carries 64 `CRenderablePolygon` and 34
`CWayPointsPolygon`. The latter is worth noting: the stalled `CFreeRangePoly`
interaction-zone problem suspects hand-built maps fail precisely for lacking those.

**Mind the spawn.** `Start Here` sits at (4195, 3465) — the bottom-right test corner,
beside the donut. Of the 128 entities within 1000 × 800 of it, 86 are distinct sprites:
a sampler, not a room. Anything arriving there sees the worst of the map. Around
(1950, 1200) is inside the real cavern, with 84 Outpost pieces within 500 units.

`mods/outpost-expedition` makes the map reachable through Quinn and moves the arrival
accordingly.

## Dialogue

338 `.DialogTree` files, 5317 nodes, 10915 replies. **24 are referenced by nothing.**

Separately, 96 replies (0.9%) point at node IDs that do not exist — genuine vanilla bugs,
including a `5 goobye` typo in `Goblin Sapper` that dead-ends a conversation. See
`dialogtree_format.normalise_id` for why the honest figure is 96 and not the 369 a naive
comparison reports.

### Not cut: the 12 Alamut endings

`GoodEndingAllDie`, `EvilEndingAllLive`, `GoodEndingOldManEscapesDaVinciDies` and their
siblings are named by *no data file at all*, not even their own contents. The engine
selects them. They are reachable shipped content — see `alamut-endings.md`. They appear in
the sweep only because it cannot see engine-side references, which is the method's limit.

### Genuinely orphaned — 12 files

| File | Nodes | Replies | What it is |
|---|---|---|---|
| `Wilderness/goblingirl` | 19 | 28 | **The best find.** A fully written character, "Goblin Girl", with real voice — *"You're cute for a... whatever it is you are."* The node IDs describe a design: `1 First time PC enters village`, `2 PC Enters the village again, before completing any quest`, `5 Give me some sugar` → *"you'll have to prove yourself"*. A character and a quest hook with no NPC attached. 2 dangling targets. |
| `Temple District/Temple Entrance Guard` | 11 | 23 | "Guard Pablo at Temple District" — a complete bribe-the-guard interaction, 0 dangling targets. |
| `Gate District/Barcelona Vendor Sympathetic2` | 9 | 21 | A second sympathetic-vendor variant. |
| `Gate District/barcelonavendor` | 7 | 9 | Superseded by `barcelonavendor beg`, which *is* used. |
| `Wilderness/Assassin` | 5 | 17 | |
| `Wilderness/goblinguards` | 4 | 3 | Pairs with the Goblin Girl's village. |
| `Sewers/wereratwarriorcan` | 4 | 7 | |
| `Wilderness/Alpha Wererat` | 4 | 0 | The creature exists — `Wererat boss Super.can` carries `User Assigned Name=Alpha Wererat` — but nothing gives it this dialogue. |
| `La Calle Perdida/MadEnchanter` | 1 | 1 | Stub. |
| `3 Montaillou/knighttemplar` | 1 | 0 | Stub. |
| `7 English Shrine/EnglishKnightTemplarCan1` | 1 | 0 | Stub. |
| `Wilderness/rocktitancanned1` | 1 | 0 | Stub. |

## Characters and monsters

**16 of 247 character templates** are never spawned. The cluster that stands out is an
entire cut opening sequence under `Levels/Start Game/Character Templates/`:

`Assasin Talking`, `Assasins attacking`, `Inquisitor Guards in Forest` (and `2`, `3`),
`Inquisitor going thru forest`, `Slavers standing around`.

Elsewhere: `Sewers/Helpful wererat` — a friendly wererat, a thing the shipped game does not
contain — plus `Sewers/Thief entrance fight winner`, `8 Alamut/Chaos Dragon Final Scene`,
`Wilderness/Bounty Hunter Guard`, `Wilderness/Wererat PRIME Super`,
`Wilderness/shylock goon no talk`, `La Calle Perdida/Inquisitor WipedOut Calle`,
`3 Montaillou/Montaillou Church Knight`, `6 Barcelona Attack/spanish defender`.

**35 of 478 monster cans** are never placed, in two coherent sets plus strays:

- **`English in Caverns of Nostrodomus/` — 14 templates.** `Nos Soldier1`, `2`, `3` and
  `Nos Soldier2 Bow`, each with Tough and Super variants, plus `Nos Ogre2 English` and its
  Tough. A complete, difficulty-tiered English force for the Nostrodomus caverns that no
  map ever fields.
- **The Sewer Thief tier — 11 templates.** `Sewer Theif Boss` (+ Tough, Super),
  `Sewer Theif3 Mace` (+ Tough, Super), `Sewer Theif4 Bow` (+ Tough, Super),
  `Sewer Theif4 Sword Tough` and `Super`. The sewers ship thieves, but not these.
- Strays: `Animals/Rabid Wolf Tough`, `Assasin EarlyLevels`, `Mongol Goblin Grum`,
  `Mongol Goblin Rakeb`, `Mongol Goblin Hat Super`, `Sand Spirit2 Super`,
  `Sewers/Wererat boss Super`, `Sewers/Wererat PRIME Super`,
  `Summoned Cans/Vodyanoi 01`, and `Simon Spell Cast Test` (a developer's).

## Quests

151 quest definitions; **6 are referenced by nothing.** They are not all empty, which is
worth stating carefully because the filenames alone suggest they are:

| Quest file | Display name | States |
|---|---|---|
| `3 Montaillou/Help Andre the Titan with his tasks` | **Help Marcus the Titan with his tasks** | **2 — with real text** |
| `3 Montaillou/Find the portals used by the English forces` | Find the portal used by the English forces | 0 |
| `6 Barcelona Attack/Pursue the retreating English forces...` | Pursue the Retreating Druid Forces and Recover the True Cross | 0 |
| `7 English Shrine/Prevent the Druids from completing the dark rites` | Prevent the Druids from Completing their Dark Rites | 0 |
| `3 Montaillou/Defend Montaillou from the invaders` | *(blank)* | 0 |
| `6 Barcelona Attack/Find Galileo and DaVinci` | *(blank)* | 0 |

**The Titan quest is a real designed quest**, not a stub: two states with written text,
*"Find Marcus' cousin and take sphere from him"* (`ID=7LOVAAS1`) and *"Return Sphere to
Marcus"*. Note the character was renamed — Andre in the filename, Marcus in the content.
The same slippage shows in the Barcelona Attack quest, whose filename says English forces
and whose display name says Druid forces.

The four with a display name but no states were written far enough to appear in a quest
log and no further.

## Items and media

- **Items: none unused.** All 109 files under `Specific Item Cans` are granted somewhere.
- **Music: 2 of 106 unused.** `1 Barcelona/MX_CHAMB_MED` (2.8 MB, the largest unused asset
  in the game) and `1 Barcelona/MX_FACT_KNIGHTTEMPLAR1` — a Knights Templar faction theme,
  where the Saladin equivalents `MX_FACT_KNIGHTSALADIN1/2` are both used.
- **Movies: 1 of 4 unused** — `NVidiaFlash.bik`, a vendor splash. `Intro`, `Black Isle` and
  `Reflexive` all play.

## What is worth building on

1. **`goblingirl`** — 19 nodes of finished, characterful writing with a quest structure
   already implied, needing only an NPC, a placement and wiring. `goblinguards` sits beside
   it, suggesting the village was planned as a set. The cheapest route to a mod that feels
   like it belongs in the game.
2. **The Titan quest** — the only cut quest with authored states. Two objectives and a
   named NPC, already in the quest system's own format.
3. **`Oupost`'s left half** — a large, professionally built dungeon, if you want somewhere
   substantial to put content and would rather not author geometry.
4. **The Nostrodomus English force and the Sewer Thief tier** — 25 balanced,
   difficulty-tiered enemy templates between them, ready to field.
5. **The Start Game sequence** — seven templates describing a cut opening.
