# Playtest Kit

**A testing tool, not content.** It exists because verifying the goblin work in Lionheart
Fixt cost hours of play before the test could even begin: the Goblin Warrens sit behind a
journey a starting character does not survive, and several of the cases to check depend on
world state that only a completed quest chain sets.

Talk to **Farshid ibn Almassizad** in the Barcelona Gate District. His conversation gains
`[TEST KIT] Open the playtest menu`, offering:

| | |
|---|---|
| Send me to the Goblin Warrens (direct) | `CTeleportAction` straight to the map's `Start Here` |
| Send me to the Goblin Warrens (world map) | via a point of interest this mod adds |
| Unlock the wilderness on my world map | Crossroads, Woodcutter Forest, Goblin Warrens |
| Make me stronger | +40,000 experience |
| Heal me | +500 health |
| Raise my standing with the goblins | +1 goblin rank, repeatable |
| The woodcutter is dead / the river dryad is dead | sets the flags the goblin dialogue reads |
| I have already met the goblin girl | sets Fixt's first-meeting flag |

The menu loops, so effects stack — three presses of the rank option reaches Champion, and
does it through the accumulation cascade rather than around it.

## Requires Lionheart Fixt

Two of the things it writes to are Fixt's, not vanilla: `Uber Perks/Goblin Rank` and
`Game Scripting Variables/Met the Goblin Girl`. Install Fixt as well, and **remove this kit
before testing a release for real** — a build that only passes with the kit installed has
not been tested.

## Two decisions worth knowing

**It is hosted on an existing NPC rather than adding one.** New map entities never appear
on a save that has already visited the level, so a new NPC would force a new game —
exactly the cost this is meant to remove. Dialogue changes *do* apply to existing saves,
so this works on the character you already have.

**Every action dispatches from a `.can`.** Inline dialogue Custom Actions silently no-op
for several action classes, `CGiveExperiencePointsToAllPlayersAction` among them; see
SKILL.md. Rather than discover which of the nine are affected one at a time, all of them
go through `CUseCannedActionAction`, which is the known-good route.

## Two travel routes, on purpose

Only one of them is certain, so both ship and the first in-game test decides.

The **world map** route is what the game itself uses, but no vanilla point of interest
targets the Goblin Warrens, so this mod adds one. Every other POI lists its peers in a
`Spawn Points` array and a new one cannot appear in theirs, so arrivals have to fall
through to `Main Spawn Point`.

The **direct** route uses `CTeleportAction`, whose `New Map Name` takes a full map path —
but all four vanilla uses are within a single map, so cross-map travel is plausible rather
than proven.
