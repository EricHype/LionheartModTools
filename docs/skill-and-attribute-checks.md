# Authoring skill and attribute checks

How to make a conversation read the player's build. Everything here was measured against
`data.dat.vanilla.bak` or confirmed in-game while building Lionheart Fixt 0.1.0; where
something is inferred rather than proven, it says so.

The short version: a check is a **`.can` file holding a boolean expression**, referenced by
**name** from a `Requirement=` line on a dialogue reply. If the expression is false the
reply is not shown. That is the whole mechanism -- there is no separate "skill check"
system, no roll, no degrees of success.

---

## The five things a check can read

| Kind | Expression operand | Example path |
|---|---|---|
| Skill | `CVariableSkill` | `Skills/Thieving/Speech`, `Skills/Thought` |
| SPECIAL attribute | `CVariableCharacterAttribute` | `Character Attributes/(PE) Perception` |
| Derived attribute | `CVariableDerivedCharacterAttribute` | `Derived Character Attributes/Karma` |
| Perk | `CHasPerkExpression` | `Perks/!Event Title Perks/Thief Friend` |
| Trait | `CHasTrait` | `Traits/Racial/Demokin/Vampiric Fury` |

Quest state, inventory and world flags are **actions**, not expressions, and need the
wrapper described under *Composites* below.

### The skill trees

Six thieving skills and four magic/combat trees. There is no Repair, Doctor, Science,
Gamble, Outdoors or Throwing -- those are Fallout, not Lionheart.

```
Skills/Thieving/Speech          Skills/Fight
Skills/Thieving/Barter          Skills/Divine
Skills/Thieving/Sneak           Skills/Thought
Skills/Thieving/Lockpick Disarm Traps    Skills/Tribal
Skills/Thieving/Find Traps Secret Doors
Skills/Thieving/Diplomacy
```

---

## The anatomy of a `.can` gate

Tabs for indentation, CRLF line endings, latin-1. A skill threshold:

```
CCannedObject
{
	Object=CIsGreaterThanOrEqual
	{
		Operand1=CVariableSkill
		{
			Skill=Skills/Thieving/Speech
		}
		Operator=
		Operand2=CConstant
		{
			Constant Value=45
		}
	}
	Use=Shared Global Instance
}
```

An attribute threshold swaps the operand:

```
		Operand1=CVariableCharacterAttribute
		{
			Character Attribute=Character Attributes/(PE) Perception
		}
```

A derived attribute uses `CVariableDerivedCharacterAttribute` with the same
`Character Attribute=` field name (not `Derived character attribute=` -- that spelling is
the *write* field, see *Reading vs writing* below).

**`Operator=` is always blank.** Every shipped comparison leaves it empty; it is not a
missing value.

Comparison classes: `CIsGreaterThan`, `CIsGreaterThanOrEqual`, `CIsLessThan`,
`CIsLessThanOrEqual`, `CIsEqualTo`, `CIsNotEqualTo`.

**Verify formatting for free**: `resource_format.py` round-trips every shipped `.can`
byte-identically, so parse your file and re-serialise it -- if the bytes change, your
formatting is off.

```python
import resource_format as rf
node = rf.parse_resource_text(open(path, encoding="latin-1").read())
rf.write_resource_file(node, tmp)      # compare tmp against path
```

---

## Referencing a gate from a reply

In a `.DialogTree` (flat, no indentation), `Requirement=` takes the `.can` file's **stem**,
without path or extension:

```
Requirement=PE 7+
Reply Text=You have been sounding that wall for a weak course.
Go to node ID=15 spotted the sap
Icon=Attribute Icon
```

### Name resolution is global, but prove it before relying on a new folder

Bare names resolve across **at least fifteen different directories**, including the
`Resources/Dialog/Requirements/**` tree and per-level
`Resources/Levels/*/Dialog/Requirements/` folders. It is clearly a global search by stem.

Two cautions that cost real time:

- **No shipped DialogTree uses a path-qualified `Requirement=`.** Zero. If bare-name
  resolution ever fails you, a path is *not* a known-good fallback.
- Some folders have **no shipped bare reference at all** -- notably
  `Requirements/Derived Attributes/` and `Requirements/Skills/Magic Thought/`, because
  nothing in the game ever used the gates in them. Referencing those by bare name is
  unproven. The safe move is to author your own copy into a folder with demonstrated
  bare-name use, such as `Requirements/Attributes/` (16 distinct vanilla names) or
  `Requirements/Faction/` (8).
- **Stems must be unique game-wide**, since resolution ignores directories.

### Ordering

Replies render in file order, so put the gated reply **above** the ungated one it competes
with. The player sees the special option first, and the plain option still sits underneath.

### Icons

| Icon | Use |
|---|---|
| `Speach Skill Icon` | skill checks (Speech, Barter, Tribal, Thought...) |
| `Attribute Icon` | SPECIAL and derived-attribute checks |
| `Barter Skill Icon` | Barter specifically, where the scene is a haggle |
| `Quest Icon` | faction/quest-state gated replies |
| `Fight Icon`, `Exit Icon` | combat and goodbye replies |

---

## Composites: AND, OR, NOT

`COR` and `CAND` take `Operand1` / `Operator=` / `Operand2`. The cleanest form references
other gates rather than inlining, via `CUseCannedExpression` with a `Resources/`-relative
path minus the extension:

```
CCannedObject
{
	Object=COR
	{
		Operand1=CUseCannedExpression
		{
			Canned Expression=Dialog/Requirements/Attributes/IN 9+
		}
		Operator=
		Operand2=CUseCannedExpression
		{
			Canned Expression=Dialog/Requirements/Skills/Speech/Speech moreequal 75
		}
	}
	Use=Shared Global Instance
}
```

`CExpressionNot{Operand1=...}` negates an expression. `CNotAction{Action=...}` negates an
action.

### Mixing in quest state, inventory and flags

Those are **actions**, so they need `CActionExpression` to become expressions, and
`CAndAction` to combine:

```
Object=CActionExpression
{
	Action=CAndAction
	{
		Action=Array
		{
			Item Count=3
			Action=CWasQuestEverActivatedAction { Quest=Levels/.../Some Quest }
			Action=CExpressionAction
			{
				Expression=CIsGreaterThanOrEqual { ...a skill test... }
				Character to get attributes from=$Instigator
			}
			Action=CNotAction { Action=CIsQuestCompletedAction { Quest=... } }
		}
		Early Exit=0
	}
}
```

Note `CExpressionAction` is the adapter in the other direction -- expression *into* an
action list -- and it needs `Character to get attributes from=$Instigator` or it has no
one to measure.

### The `If=` trap

`CIfAction`'s `If=` field takes a **bare action**, never an `CActionExpression` wrapper:

```
If=CActionCheckForInventoryItem { ... }          correct
If=CActionExpression { Action=C... }             runtime error
```

The wrapper is only for `Custom Requirement=` fields, which need an Expression. Getting
this backwards produces *"tried to use a CActionExpression for a If when a CAction is
expected"* at runtime, not at load.

---

## Inline checks with `Custom Requirement=`

A one-off condition can go straight on the reply instead of into a `.can`:

```
Requirement=!None
Custom Requirement=CActionExpression
{
Action=CIsQuestStateTheCurrentStateAction
{
Quest=Levels/1 Barcelona/Quests/Gate District/Spy for Hrubjub the Goblin
State=LD8PLRXA
}
}
Reply Text=I have news. The gate is weakly guarded.
Go to node ID=100 completed quest
```

`Requirement=` and `Custom Requirement=` **stack** -- both must pass. Use the named `.can`
for anything reusable and the inline form for scene-specific state.

---

## Derived attributes worth knowing about

### The perk-substitution sockets

Three derived attributes under `Perk and Trait Support` are **pass-throughs over a SPECIAL
stat**, and exist so a writer can gate on a concept rather than a number:

| Derived | Reads | Nothing writes it |
|---|---|---|
| `Outwit` | `(IN) Intelligence` | yes |
| `Fast Talk` | `(IN) Intelligence` | yes |
| `Schmooze` | `(CH) Charisma` | yes |

Because they live under `Perk and Trait Support`, a perk can in principle add to them --
which means gating on `Outwit >= 7` rather than `IN 7+` leaves a socket for a later perk to
grant the reading without touching the stat. That is the "smart enough, **or** observant
enough" shape. Nothing in the shipped game writes to any of them, so the perk half is
untested; the gate half works exactly like the raw attribute today.

Use the raw attribute where no perk should ever substitute -- lifting a beam is `ST`, not
cleverness about strength.

### Reading vs writing

Two different field names, and confusing them is why an early survey of this project
concluded "nothing writes Karma" when 213 sites do:

```
Character Attribute=Derived Character Attributes/Karma                      <- READ
Derived character attribute to modify=Derived Character Attributes/Karma    <- WRITE
```

### Faction rank

Faction membership is just a derived attribute counter. Each `.Faction` record grants `+1`
to its own rank with `Allow Accumulation=1` and `Modification is permanent=1`, so ranks
climb 1 -> 2 -> 3 and **tier benefits stack**. The shipped gates are:

```
<Faction> IS          Rank > 0
<Faction> Midlevel    Rank > 1
<Faction> Highlevel   Rank > 2
<Faction> NOT         Rank == 0
```

A faction cannot be lost: there is no leave, clear or demote action in the entire archive,
zero null-faction assignments, and zero negative rank writes. Plan exclusivity as *closed
content* (quests that fail each other), not as demotion.

---

## What the shipped game actually gates on

Measured across all vanilla dialogue. This is the context for "is my check unusual?"

| Subject | Gate files | Uses |
|---|---|---|
| Speech | 51 | 376 |
| Inquisition / Templar / Wielder / Saladin rank | 18 | 566 |
| Barter | 51 | 146 |
| `(IN)` | 21 | 63 |
| `(PE)` | 6 | 37 |
| `(CH)` | 13 | 32 |
| `(ST)` | 13 | 27 |
| Karma | 78 | 17 |
| Sneak | 5 | 3 |
| Fight / Divine / Thought / Tribal | 1 each | 1 each -- all four in the same expression |
| **Lockpick** | **18** | **0** |
| **Schmooze** | **7** | **0** |
| **Outwit** | **6** | **0** |
| **`(AG)` / `(EN)` / `(LK)`** | **5 each** | **0** |

**46 finished requirement files that nothing in Lionheart reads.** Agility, Endurance and
Luck have never gated a line of dialogue in the shipped game. If you want a check that
costs no new files, start there.

Two shipped files are *named* for Outwit and test Speech instead
(`Grumdjun Dryad talked to NOT killed Player high Outwit.can` and its twin) -- fossils of
checks the developers intended and did not finish. Do not trust a `.can` by its filename.

### Skills the world tests without any `.can`

Lockpick and Find Traps have zero dialogue gates but are **heavily used engine mechanics**,
so "0 uses" in the table above does not mean unused:

- **Lockpick**: `Is Locked=1` plus a per-object `Lock Pick Adjustment` (5169 locks, 4003
  tuned, roughly `-300` to `+100`). Barcelona alone has 52 locks, 51 individually tuned.
- **Find Traps**: `CAISecretReveal` with a per-secret `Skill Adjustment`, across 443 sites.

The gap for those two is social recognition -- nobody in 137 Barcelona conversations
remarks on you being a burglar -- not mechanical purpose.

---

## Traits and perks

44 traits ship and **none is gated by any conversation**. `CHasTrait` works (a healing
potion uses it), so trait-gated dialogue is available and entirely unused.

Perk checks use `CHasPerkExpression` and need the `CExpressionAction` adapter when used in
an `If=`:

```
If=CExpressionAction
{
	Expression=CHasPerkExpression
	{
		Perks To Check For=Array
		{
			Item Count=1
			Perk To Check For=Perks/!Event Title Perks/Thief Friend
		}
	}
	Character to get attributes from=$Instigator
}
```

### Title perks are the "you earned something" idiom

`Resources/Perks/!Event Title Perks/` holds 13 award perks (`Thief Friend`, `Goblin
Champion`, `Necromancer`...). Their `Requirements` block is a deliberately unsatisfiable
`0 >= 1`, which makes them impossible to choose at level-up and grantable only by script:

```
Action=CGiveCharacterPerkAction
{
Character to give perk to=$Instigator
Perk to give=Perks/!Event Title Perks/<name>
}
```

This matters because **no faction join in the shipped game announces itself** -- the
Templar, Wielder and Inquisition assignments fire XP and quest actions and nothing else.
If you want the player to *see* that something happened, a title perk is the game's own
answer.

---

## Design rules that survived contact with the game

- **A check adds a route; it never removes one.** Gating an existing reply silently
  deletes content for anyone below the threshold. Add a new reply above it instead. This
  also makes the change testable: a low-stat character must still finish the scene.
- **Test with two characters.** A build that passes everything cannot show you whether the
  vanilla path survived, which is the only failure mode this rule can produce.
- **Pick thresholds off the shipped distribution.** Speech gates cluster at 15-55 for
  ordinary scenes; 75-95 is endgame. `7+` is the usual "notably high" SPECIAL bar and `8+`
  the demanding one. `80` is the only magic-school threshold the game ships.
- **Gate on the fiction, not the stat.** The best checks let a build notice something true
  that was always there -- a sapper measuring a wall, a warning that names one danger twice
  -- rather than handing out a skeleton key.

---

## Verifying before you play

Static checks that catch nearly everything, in rough order of value:

1. Every `Go to node ID` resolves to a real node. Node lookup **is case-insensitive** --
   244 vanilla links rely on it -- so compare case-folded or you will chase 244 phantoms.
2. Every named `Requirement=` resolves to a real `.can` stem, yours or vanilla's.
3. Reply count equals `Go to node ID` count per file.
4. Every embedded `Custom Action` / `Custom Requirement` parses with `resource_format`.
5. Non-dialogue resources round-trip byte-identically.
6. Files stay latin-1 and pure CRLF.

**Negative-test the validator itself.** Feed it a known-bad file and confirm it fails; a
checker that passes everything is worse than none.

And after deploying, read the bytes back out of **both** `data.dat` and the loose `data\`
mirror -- the mirror shadows the archive, so a correct `data.dat` with a stale mirror is a
mod that does nothing.
