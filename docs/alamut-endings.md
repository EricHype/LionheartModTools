# Alamut ending branches

Reference documentation of the game's actual branching-ending system, mapped by grepping
`Dialog Tree File=` / `Node ID=` pairs out of the relevant `.zax` files and cross-checking
which DialogTree files are actually reachable. This is **game content reference**, not a
modding technique — see `.claude/skills/lionheart-modding/SKILL.md` for how-to-mod
knowledge.

## Summary

The finale is genuinely reactive along two axes:

1. **Companion survival / outcome** — who lived, who died, whether the Old Man escaped,
   whether it's a "Good" or "Evil" ending. This is baked into human-readable `Node ID=`
   names in the DialogTree files (e.g. `Good Ending Old Man Escapes DaVinci Dies`), which
   is why this doc can be built accurately without fully reverse-engineering the trigger
   logic — the devs' own naming does most of the work.
2. **Player's specialized magic school** — Elemental / Beastial / Demonic — which
   determines which of three near-identical "Spirit Ending" DialogTree files narrates the
   Old-Man-escapes branches.

It is **not** reactive to anything from the Beggars/Thieves Guild questline — confirmed by
grepping every ending-related DialogTree file for "Beggar"/"Thieves Guild"/"Thief Guild":
zero matches.

There are three layers of content, in the order the player experiences them:

1. **`Levels/8 Alamut/08 Final Encounter.zax`** — in-combat-level narration (dialogue
   balloons) reacting to how the fight itself concluded.
2. **Three separate "END GAME" epilogue maps** — `END GAME Calle Perdida.zax`,
   `END GAME Nostrodomus Demesne.zax`, `END GAME Siege Map.zax` — each plays a slice of a
   shared closing narration depending on the outcome.
3. A set of **11 unused/dead ending DialogTree files** that exist in the data but are
   referenced nowhere in the shipped content (see below) — don't try to hook into these.

## Layer 1: Final Encounter reactions (`08 Final Encounter.zax`)

All shown via `CDisplayDialogBalloonAction` (floating dialogue, not a full conversation
window). Branch outcome comes from the `Node ID=` name; which file plays depends on
context (magic school for the three Spirit files, or straight companion-reaction files).

| Line | DialogTree file | Node ID (= outcome) |
|---|---|---|
| 3441 | `Elemental Spirit Ending` | 30 — Good Ending, Old Man Escapes, DaVinci or Galileo Dies |
| 3526 | `Beastial Spirit Ending` | 30 — Good Ending, Old Man Escapes, DaVinci or Galileo Dies |
| 3601 | `Demonic Spirit Ending` | 30 — Good Ending, Old Man Escapes, DaVinci or Galileo Dies |
| 3642 | `Galileo Ending` | 40 — Good Ending, Old Man Escapes, DaVinci Dies |
| 3774 | `Galileo Ending` | 30 — Good Ending, Old Man Escapes |
| 3791 | `DaVinci Ending` | 40 — Good Ending, Old Man Escapes, All Live |
| 3968 | `DaVinci Ending` | 120 — Evil Ending, Old Man Escapes |
| 4064 | `DaVinci Ending` | 121 — Evil Ending, Old Man Escapes (variant 2) |
| 5532 | `Elemental Spirit Ending` | 30 — (same as above) |
| 5617 | `Beastial Spirit Ending` | 30 — (same as above) |
| 5692 | `Demonic Spirit Ending` | 30 — (same as above) |
| 5733 | `DaVinci Ending` | 50 — Good Ending, Old Man Escapes, Galileo Dies |
| 5919 | `DaVinci Ending` | 10 — Evil Ending, All Die |
| 7599 | `DaVinci Ending` | 100 — Respond, ask about Tank (not a true ending branch) |
| 8272 | `Elemental Spirit Ending` | 20 — Good Ending, Old Man Escapes, All Die |
| 8357 | `Beastial Spirit Ending` | 20 — (same) |
| 8432 | `Demonic Spirit Ending` | 20 — (same) |
| 27556 | `Galileo Ending` | 10 — Good Ending, All Survive |
| 27573 | `DaVinci Ending` | 30 — Good Ending, All Survive or Galileo Dies |
| 27679 | `Elemental Spirit Ending` | 40 — Player wins outright |
| 27764 | `Beastial Spirit Ending` | 40 — (same) |
| 27839 | `Demonic Spirit Ending` | 40 — (same) |
| 28029 | `DaVinci Ending` | 30 — (repeat of above) |
| 28120–28280 | Elemental/Beastial/Demonic Spirit Ending | 40 — Player wins outright (repeat) |
| 28504 | `DaVinci Ending` | 20 — Evil Ending, All Live (variant 1) |
| 28600 | `DaVinci Ending` | 21 — Evil Ending, All Live (variant 2) |
| 28982 | `Galileo Ending` | 15 — Galileo Talk Amazed |
| 28995 | `DaVinci Ending` | 15 — DaVinci Talk Amazed (variant 1) |
| 29001 | `DaVinci Ending` | 16 — DaVinci Talk Amazed (variant 2) |
| 29102–29262 | Elemental/Beastial/Demonic Spirit Ending | 40 — Player wins outright (repeat) |
| 30062 | `Galileo Ending` | 20 — DaVinci dies |
| 30157–30317 | Elemental/Beastial/Demonic Spirit Ending | 40 — Player wins outright (repeat) |
| 30548 | `DaVinci Ending` | 10 — Evil Ending, All Die (repeat) |
| 30905 | `Galileo Ending` | 11 — Galileo Talk EVIL |
| 30918 | `DaVinci Ending` | 17 — DaVinci Talk Evil (variant 1) |
| 30926 | `DaVinci Ending` | 18 — DaVinci Talk Evil (variant 2) |
| 31357 | `Elemental Spirit Ending` | 10 — Good Ending, All Die |
| 31442 | `Beastial Spirit Ending` | 10 — (same) |
| 31517 | `Demonic Spirit Ending` | 10 — (same) |

**Pattern**: whenever an ending calls for the "Old Man escapes" narration and depends on
the player's magic school, the same Node ID (10/20/30/40) is duplicated identically across
`Elemental Spirit Ending`, `Beastial Spirit Ending`, and `Demonic Spirit Ending` — i.e.
these three files are near-mirrors of each other, just reskinned for which spirit/school
the player leans into. `Galileo Ending` and `DaVinci Ending` instead react to whether each
specific companion is alive.

## Layer 2: END GAME epilogue maps

Three separate maps, each apparently corresponding to a different overall story path
(evil/undead-aligned, mystic/Nostrodomus-aligned, and the heroic siege defense
respectively — inferred from map names and content, not fully verified):

**`END GAME Calle Perdida.zax`** (undead/Wielder-flavored location — has its own large set
of unrelated NPC dialogue too, e.g. Lord Relican, CedricAlsen, the Inquisitors; only one
ending reference):
- Line 21958: `Nostrodomus Ending`, Node `20 Evil Ending All Live or Die`

**`END GAME Nostrodomus Demesne.zax`**:
- Lines 3177/3255/3337/3569: `Nostrodomus Ending`, Node `10 Base Start Nostrodomus`
  (shown 4 times — almost certainly 4 different trigger points leading to the same intro
  node, not 4 different branches)

**`END GAME Siege Map.zax`**:
- Lines 8102→8124→8142→8202: `Lord Javier Ending Movie`, Nodes `10 Base Start` →
  `20 Continue` → `30 Continue` → `40 End` — a linear 4-node cutscene, likely a shared
  base narration shown before branching.
- Line 17812: `Nostrodomus Ending`, Node `30 Good Ending All Die or Davinci or Galileo`
- Line 18351: `Nostrodomus Ending`, Node `50 Good Ending Old Man Escapes All Live or Die`
- Line 18395: `Nostrodomus Ending`, Node `40 Good Ending All Survive`

So `Nostrodomus Ending` is the shared closing-narration DialogTree, and different nodes
within it get shown depending on which of the three epilogue maps loads and what happened
in the Final Encounter — `Lord Javier Ending Movie` is a separate linear intro cutscene
specific to the Siege Map path.

## Dead/unused ending content — don't bother with these

These DialogTree files exist in `Levels/8 Alamut/Dialog/` but are referenced **nowhere**
in any `.zax` file or other DialogTree in the shipped game (confirmed via exhaustive grep
across `Levels/` and `Resources/`). They read like an earlier iteration of the ending
system that got superseded by the Layer 1/2 system above:

- `GoodEndingAllDie.DialogTree`
- `GoodEndingAllSurvive.DialogTree`
- `GoodEndingDavinciDies.DialogTree`
- `GoodEndingGalileoDies.DialogTree`
- `GoodEndingOldManEscapesAllDie.DialogTree`
- `GoodEndingOldManEscapesAllLive.DialogTree`
- `GoodEndingOldManEscapesDaVinciDies.DialogTree`
- `GoodEndingOldManEscapesGalileoDies.DialogTree`
- `EvilEndingAllDie.DialogTree`
- `EvilEndingAllLive.DialogTree`
- `GoodEnding PLAYER TALK ENDING.DialogTree`
- `Evil Ending PLAYER TALK ENDING.DialogTree`

## If we ever want to hook a guild-choice reaction into this

Per the earlier discussion: don't try to add a new branch axis into the `.zax` trigger
logic (large, already-complex, real risk of breaking existing endings). The safer route is
adding one `Custom Requirement=`-gated aside line to each of the *actually-live* ending
files (`Elemental/Beastial/Demonic Spirit Ending`, `Galileo Ending`, `DaVinci Ending`,
`Nostrodomus Ending`, `Lord Javier Ending Movie` — 7 files, not the 11 dead ones above),
independent of the existing branch logic — same low-risk dialogue-edit pattern used for the
wererat cure fix.
