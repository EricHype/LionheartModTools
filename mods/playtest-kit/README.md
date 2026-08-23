# Playtest Kit

**A testing tool, not content.** It exists because verifying the goblin work in Lionheart
Fixt cost hours of play before the test could even begin: the Goblin Warrens sit behind a
journey a starting character does not survive, and several of the cases to check depend on
world state that only a completed quest chain sets.

Talk to **Merchant Lope** or **Jafar** in the Barcelona Gate District. Their conversations gain
`[TEST KIT] Open the playtest menu`, offering:

| | |
|---|---|
| Send me to the goblin camp gate | `CTeleportAction` to Mongol Camp, where the entrance encounter is |
| Send me straight into the Warrens | the same, one map further in &mdash; **skips the gate** |
| Send me to the Goblin Warrens (world map) | via a point of interest this mod adds |
| Unlock the wilderness on my world map | Crossroads, Woodcutter Forest, Goblin Warrens |
| Make me stronger | +40,000 experience |
| Heal me | +500 health |
| Raise my standing with the goblins | dispatches Fixt's own `Advance Goblin Rank` cascade, so a press does exactly what a service does |
| The woodcutter is dead / the river dryad is dead | sets the flags the goblin dialogue reads |
| I have already met the goblin girl | sets Fixt's first-meeting flag |

It also reads state back. The `(reading)` lines appear only when their condition
holds, so which ones show *is* the measurement -- goblin rank, the greeting flags, and
whether the Blooded disease resistance survives promotion to Champion, which is the one
observation that settles whether the three faction tiers stack or replace each other.

The menu loops, so effects stack — three presses of the rank option reaches Champion, and
does it through the accumulation cascade rather than around it.

## Travel goes to the gate, not past it

Landing inside the Warrens skips the entrance encounter on Mongol Camp, where you talk your
way past the guard and he deactivates `entrance guards attack relay`. Skip it and the camp
never learns you are welcome, so it turns on you the moment you walk back out -- which looks
like a mod bug and is not one. The gate is a piece of the content under test, not travel
time, and it is one screen from the Warrens.

The direct option is still there for when the Warrens itself is what you are testing and the
camp's mood does not matter. The menu says what it skips.

## Requires Lionheart Fixt

Two of the things it writes to are Fixt's, not vanilla: `Uber Perks/Goblin Rank` and
`Game Scripting Variables/Met the Goblin Girl`. Install Fixt as well, and **remove this kit
before testing a release for real** — a build that only passes with the kit installed has
not been tested.

## Two decisions worth knowing

**It is hosted on existing NPCs rather than adding one.** New map entities never appear on
a save that has already visited the level, so a new NPC would force a new game — exactly
the cost this is meant to remove. Dialogue changes *do* apply to existing saves, so this
works on the character you already have.

**Two hosts, because the first attempt picked badly.** It originally hung off Farshid, who
turns out to be a Knight of Saladin sparring when the game opens: the map routes you to a
node reading *"<The knight ignores you, his attention is fixed on his opponent.>"* until
the Saladin initiation. The splice was correct; the menu was simply never shown. A merchant
always trades and Jafar is central to the opening, so between them one is always reachable.
The check that would have caught it — that the menu sits on the node the *map* opens, not
merely somewhere in the file — is now part of validating the kit.

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
