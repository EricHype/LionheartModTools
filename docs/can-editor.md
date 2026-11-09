# The can editor

`canedit.py` edits creature cans (`.can`): the template a generator spawns a monster
from. It exists so a human can change an enemy's AI without hand-editing 1,600 lines of
brace-delimited text, and without naming a class that crashes the game.

```
python canedit.py                                         # pick from the Cans list
python canedit.py "Monster Cans/English Enemies/Soldier1"  # a vanilla can, read-only
python canedit.py "<mod>\files\Resources\...\X.can" --mod "<mod>"
```

The engine facts below were read in Ghidra and are recorded in Lionheart Fixt's
[`docs/dormant-engine.md`](https://github.com/EricHype/LionheartFixt/blob/main/docs/dormant-engine.md)
and the "Characters and their behaviour" section of its
[`docs/engine-notes.md`](https://github.com/EricHype/LionheartFixt/blob/main/docs/engine-notes.md).
Read those before building behaviour the shipped game never uses.

## Layout

| | |
|---|---|
| **Cans** (left) | Every `.can` your mods ship, grouped by mod, then all 1698 vanilla cans. Filter terms match in any order, so `archer goblin` finds `Mongol Goblin Archer`. Enter or double-click opens. |
| **Creature** (top) | Where the can came from, and its `User Assigned Name`, `Race`, `Category`, `Team Number` and `Model`, editable. `User Assigned Name` is the name the combat log prints and what an unnamed spawn is called. |
| **Combat AI** (centre) | Labelled controls over the can's `CSkeletonAI`. See below. |
| **Can tree** (right) | The map editor's Entity Script dock, hosted over the whole can. Everything the panel does not cover is reachable here, with the same Add / Delete / Up / Down and the same rules. |
| **Used by** (bottom) | Every map and generator part that spawns this can, from an `Entity=` line in vanilla's maps plus the open mod's. |
| **Edits** (bottom) | The undo stack, readable: `Acquire within (px): 550 -> 650`. |

Panel edits and tree edits share one undo stack, so `Ctrl+Z` walks back through both in
the order they were made.

## Opening and saving

- **A vanilla can opens read-only.** You can edit it freely in memory; **Save As into a
  mod** writes it to `<mod>\files\Resources\...` at the can's own resource path and adds
  that path to the mod's `mod.json` `files` list if it is missing. The manifest keeps its
  own line endings. Afterwards the open document is that file, and Save writes to it.
- **A can inside a mod opens writable.** Save writes it in place.
- **Nothing is ever written inside the installed game.** A file there opens read-only,
  and every save refuses a target inside the game folder. The vanilla archive is read
  from `data.dat.vanilla.bak`.
- **Save a Copy** writes anywhere else without changing what is open, which is what you
  want for a scratch diff.

The load/save contract is the map editor's: a can saved unedited is byte-identical (all
1698 shipped cans), and one field edit changes exactly one line. Files stay CRLF and
latin-1.

## Editing one creature, or one map

**A can is shared.** Editing it changes that creature in every map that spawns it, which
for `Soldier1` is 160 generator entries in 17 maps. The Used by list is there so you see
that before saving. If you want a change in one map only, leave the can alone and give
that generator an AI in its `AIs to Add` (the map editor's Entity Script dock).

Used by searches `Entity=` lines only. A can created by a script or cloned at run time is
not listed, so an empty list does not prove nothing spawns it.

## The Combat AI panel

A creature's behaviour is one `CSkeletonAI` in its `Activity` array: a bundle of
targeting, leash, group call, attack AI and transitions. 470 of the 478 vanilla cans under
`Monster Cans/` have exactly one. The other eight have none, and the panel says why:
seven dragons run `CAIDragon`, and `Mongol Bounty Hunter Attacker` ships with no AI at all.

Each control's tooltip says where its default came from: **engine** (the registration, read
in Ghidra) or **observed** (the most common value across every vanilla can, with the set
of values seen). Fields the engine notes do not explain are labelled by their own name,
not by a guess at what they do.

| Group | Fields |
|---|---|
| **Targeting** | `Valid Targets` (empty means it sees no one), `Max Distance` (acquire range, engine default 550), `Vision Cone`, `Target shooter if hit`, `Never Unacquire`, `Sneak Adjustment`, and the two `Must ...` filters. The first three live in the nested `Target Type` block. |
| **Leash** | `Max dist from home` (registered under Pursue) and `Max distance from home` (under Investigate), and `Allowed To Roam Anywhere`. |
| **Group** | `Respond to calls for reinforcements`: the listener switch for `CCallForReinforcementsAction`. A creature answers only while in Patrol and only when the caller's target is one of its own Valid Targets. |
| **Attack** | The attack AI class, `Max Dist` (shown as Attack Max Dist: past it the creature drops to Pursue; engine default 200), line of sight, and on `CNormalAttackAI` `Minimum Attack Distance`, `Move Buffer Zone`, `Use Primary Shoot Behavior`, and the `Shoot Completed` attack bank as a list. |
| **Transitions** | The `Transition` array: trigger, from state, to state and action for each. Add, Remove, and the preset below. |
| **Retreat** | Shown, disabled, labelled inert. |

Numbers are checked before they are written: typing `far` into a distance changes nothing
and says so.

### Standing ground

`Minimum Attack Distance` 0 is the engine's own "Do not move while attacking", and vanilla
writes that label, never the number, in 202 of 701 blocks. Typing 0 writes the label too.
The creature then stands, turns and fires, and keeps the protection against losing a shot
mid-swing that `CStandStillAttackAI` lacks. It suits archers and casters; a melee creature
set this way is useless against a target that keeps its distance.

### There is no Max Attack Range

`CNormalAttackAI` registers no `Max Attack Range` field. The string is registered only by
`CPlugInBehaviorAttack`, and no shipped can writes it. An archer's reach is the skeleton's
`Max Dist` (350 to 400 on vanilla bows, 70 to 85 on most melee). `dormant-engine.md` says
otherwise and is wrong on this point.

### Call for help when it first attacks

One click appends this to the `Transition` array, in the shape vanilla uses for its war
cries:

```
Array Item=CSkeletonTransition
{
	Trigger=Once Per New Target
	From State=Any
	To State=Attack
	Action=CCallForReinforcementsAction
	{
		Requester Name=$Trigger
		Max Distance=500
	}
	IsDone=0
}
```

It fires the first time the creature switches to Attack on each new target, and pulls idle
allies within 500 px into the fight. Only allies with `Respond to calls for
reinforcements=1` answer, and only for a target they would pick themselves: a Templar
(targets `Enemy`) ignores an English soldier's call to attack the player. Nothing in
vanilla ever sends this call, so it has not been seen working in play.

## Safety rules

**Classes that crash.** `CAttackAI`, `CChargeAttackAI` and `CStrafeAttackAI` are abstract.
Naming one shows the game's DataCrash box and exits. The class picker lists them, disabled,
with the reason. **Every save scans the whole can** (block types and plain values, with
case folded and the leading `C` optional, the way the engine looks names up) and refuses to
write if any of them appears, naming where.

**The class picker** offers `CNormalAttackAI`, `CStandStillAttackAI`, and an empty
`Attack AI=`, which is the shipped way to stop a creature fighting while it still moves.
Switching builds a valid block for the new class:

- to `CStandStillAttackAI`: only what it reads, the current `Shoot Behavior` and
  `Turn Behavior`, copied across;
- back to `CNormalAttackAI`: the block you switched away from this session, attack bank
  and distances included, or a stock vanilla block carrying the current Shoot and Turn
  Behavior.

`CStandGroundAttackAI` is not offered. It is an alias of `CNormalAttackAI` and loads as
one, so choosing it would only rename the line. A can already using an alias or another
class shows it as the current entry and leaves it alone.

`CStandStillAttackAI` turns and fires and never moves, but it still drops to Pursue when
the target passes Attack Max Dist, and a shot can be cut off. Prefer standing ground
above unless you need what it drops.

**Retreat is inert.** `Retreat when`, `Retreat Start Action` and `Has Started Retreat`
parse, but the engine never enters the Retreat state: all 31 callers of the state setter
pass another state. The panel shows them disabled so a can's values are visible, not so
they can be changed.

## Tests

```
python -m unittest tests.test_canedit -v
```

Needs `data.dat.vanilla.bak`; the window tests also need PySide6 and run offscreen. They
check the round-trip of every vanilla can, that a one-field edit is a one-line diff, that a
can naming an abstract class is refused, the `CSkeletonAI` sweep over `Monster Cans/`,
`mod.json` registration in both line-ending styles, and the panel's edits and undo.

`python can_schema.py` re-runs the sweep the observed defaults came from and prints any
that have drifted.
