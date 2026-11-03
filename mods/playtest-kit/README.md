# Playtest Kit

**A testing tool, not content.** It exists because verifying the goblin work in Lionheart
Fixt cost hours of play before the test could even begin: the Goblin Warrens sit behind a
journey a starting character does not survive, and several of the cases to check depend on
world state that only a completed quest chain sets.

## Switching it on and off

Fixt edits Lope's and Jafar's trees too and wins the load order, so a kit built from a
snapshot of those files vanishes the moment Fixt ships a new copy. `playtest_kit.py` in the
tools root rebuilds the kit's two trees from whatever Fixt currently has in `files/` (or
vanilla), splices the hook in, and puts the kit last in the load order:

    python playtest_kit.py on       # menu on Lope and Jafar, over current Fixt
    python playtest_kit.py off      # kit disabled, data.dat rebuilt without it
    python playtest_kit.py status

Run `on` again after any Fixt change to those two trees. `off` before testing a release.

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

## Weapon models under test

The newest menu node, `9010`, probes one unproven engine behaviour: whether an
`InventoryAddition` can override the **Weapon** slot's granny model. Vanilla proves an
addition can override `Arm` (34 shield additions) and `Head` (10 helmet additions), and the
player composite already registers every unused `Better` and `Special` weapon mesh, but
nothing in the shipped game overrides `Weapon` from an addition. Wiring the magic-weapon
appearance properly means per-base addition groups and a few hundred generated files, so it
is worth a single press of a button first.

Four probe additions sit in `Inventory Additions/Weapons/Model Test *`. Each carries the
override plugin plus +10 Fire Resistance, so the tester can tell the addition attached even
if the mesh does not change, and each sits in group `Inventory/!None` so none of them leak
into the random magic pool. They arrive named *Long Sword of the Special Mesh* and so on,
since the engine composes `"<Base Item> of <Display Name>"` and will not be talked out of
it.

| Reply | Expected |
|---|---|
| Long Bow, plain | the base `Bow-LP` mesh, as the control |
| Long Bow, Better | `BowBetter`, visibly not the control |
| Long Sword, plain | the base `LongSword` mesh |
| Long Sword, Better | `LongSwordBetter` |
| Long Sword, Special | `LongSwordSpecial` |
| Scimitar, plain | the base `Scimitar` mesh |
| Scimitar, Special | `ScimitarSpecial` on a **female** character only |

All 22 unused grade meshes were loaded through `gr2_format` before the probe was written:
every one carries 1 mesh, 1 model, 1 skeleton and its own textures, several of them richer
than the base weapon they would replace (`CrossBowBetter` is 95 KB against the base
crossbow's, `WarHammerBetter` 53 KB). None of them is a stub, so a failed probe is the
override's fault and not the art's.

### The buckler

Node `9017`. Bucklers are merchant-only by design, which is good for the game and awkward for
testing, so the kit hands them over directly: a plain one and a plain Small Shield side by
side, because the design rests on them being different objects rather than different tiers;
a Better-grade magic buckler, which should show the **base** mesh since no Better buckler art
was ever drawn; and a Special-grade one, which should show `BucklerSpecial`.

The last row rolls ten shields from `All Shields and Buckler`, the merchant pool, which is the
only row that exercises the placement rather than a grant. Roughly one buckler in four.

For the row that decides the design -- whether an unarmed attack can crit at all -- equip the
buckler, put the weapon away, and punch things for a while. Nothing in the game's files
answers it.

### Rolling, which is a different test from granting

The last reply on the probe index, `Roll magic weapons from the real pools`, is the only row
in this kit that exercises an addition **pool**. Every other reply, here and in the item
menus, uses `CActionGiveStandardInventoryItem` and names its addition by path -- which
bypasses the base weapon's pool entirely, so a grant row looks correct even if every pool
were empty.

`CGenerateInventoryItemAction` runs the real pipeline instead: a base weapon is picked from a
canned list, then additions are rolled against that base item's own `Inventory Addition
Group` at the given item level. Shape copied from vanilla rather than invented -- four
dialogue trees generate from a reply, `shakespeare` and `Generic Drop Gold Dialog` among
them, all with `Location=$Trigger`, which resolves to the NPC. The weapons land at the
merchant's feet.

Ten at a time, because one roll proves little: if the pools are broken you want a pile of
plain weapons to be obvious at a glance rather than a coin flip. The very-high-mojo rows use
level 7 (`Shopkeeper Generation Very High Mojo`), which biases toward expensive additions and
so toward Special grades, which is how you reach the Special meshes without farming.

**This row stays trustworthy with the kit installed.** The kit's own additions sit in group
`Inventory/!None` and its only other footprint is two dialogue trees, so it adds nothing to
any weapon's pool and cannot flatter the result. Turn the kit off before signing a release
off regardless.

**Equip them, do not just look at the inventory.** The icon already tiers off `Item Grade`
in vanilla, so the inventory picture changes whether or not the probe works. The claim under
test is the mesh in the character's hand.

**The scimitar row is the interesting failure.** `ScimitarSpecial` and `WarHammerBetter` are
registered in `Human Female.mdl16` and absent from `Human Male.mdl16` (90 model entries
against 88). A male character asking for that mesh is the real-world missing-name case, and
what happens then -- base mesh, no weapon, or a crash -- is worth seeing once.

`mods/male-weapon-mesh-patch/` closes that gap in 128 bytes, but **run the probe before
installing it**, or the observation is gone.

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
