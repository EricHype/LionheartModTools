---
name: lionheart-modding
description: Modding toolkit and reference for Lionheart, Legacy of the Crusader (2003, Reflexive Entertainment). Use when unpacking/repacking data.dat, editing quests/items/dialogue, or debugging why a scripted action does nothing in-game.
---

# Lionheart: Legacy of the Crusader — Modding Reference

Everything below was learned by reverse-engineering `Lionheart.exe` with Ghidra/ReVa and
extensive in-game trial and error while adding a real quest ("Wolf Pelts for Quinn") to
the shipped game. Trust this over intuition — several of these behaviors are counter-
intuitive and cost many test cycles to pin down.

## Tools

`C:\Users\vkays\LionheartModTools\`:
- `resource_format.py` — parser/serializer for the game's brace-delimited resource text
  format (`ClassName { Key=Value ... }`). Byte-identical round-trip on every file tested.
- `archive.py` — unpack/repack `data.dat`.
- `modmanager.py` — package, install, and build mods as lightweight overlays (see
  "Packaging & distributing mods" below). Reuses `archive.py`'s unpack/repack directly.
- `examples/` — worked-example scratch files from building the Wolf Pelts for Quinn quest
  (not general-purpose, but useful as reference for the DialogTree splice pattern).

```
python resource_format.py "path\to\some.InventoryItem"   # dump parsed tree as JSON
python archive.py unpack "<game>\data.dat" "<game>\data"
python archive.py repack "<game>\data" "<game>\data.dat" --compression store
```

## data.dat: MUST use store (no compression), never deflate

`data.dat` is a plain ZIP archive (`PK\x03\x04` header) — GOG install unzips to `data\`.

**Critical**: repack with `--compression store` only. Confirmed by decompiling the
central-directory parser in `Lionheart.exe`: it reads each entry's compression-method
field and does `if (method != 0) { fatal error }`. Deflate (method 8) — even though the
exe links zlib and the error text says "or a type of compression supported by the game
engine" — is **never** accepted in practice. Using deflate produces the exact in-game
error: *"...has been created using an unsupported type of compression..."* and the game
won't launch. Always verify after repacking:

```python
import zipfile
with zipfile.ZipFile(path) as zf:
    assert zf.testzip() is None
    assert set(zf.getinfo(n).compress_type for n in zf.namelist()) == {0}
```

Store-mode `data.dat` ends up close to the original's uncompressed size (~1.6GB) — that's
expected and fine (659GB+ free is typical on modern drives).

## Resource text format grammar

```
TypeName
{
    Key=Value
    Key=NestedTypeName
    {
        ...
    }
}
```

- Tabs are indentation only (cosmetic); nesting is brace-delimited.
- Keys may contain spaces, even a trailing space before `=` (e.g. `Value if True =CConstant`).
- An empty value after `=` is valid (`Operator=`).
- `Array` is not special — it's just a TypeName whose fields are `Item Count=N` followed
  by N fields that repeat the same key name (repeated keys are normal, not an error).
- Leaf values are always raw strings (numbers, paths, etc.) — never reformat them.
- File encoding: treat as `latin-1` (byte-preserving) — don't assume UTF-8, don't assume
  a real code page. This guarantees lossless round-trip regardless of actual content.
- Line endings are usually CRLF, but **verify per-file before every edit** — we saw a
  `sed -i` invocation silently convert a file to LF-only mid-session. The game tolerated
  it (loaded fine), but always re-check current bytes (`raw.find(...)`) rather than
  assuming CRLF, or your string-replace will silently match 0 times.
- `.zax` level files use the exact same grammar (root type `CLayerSaveData`) — the parser
  handles them unmodified, including multi-MB files (~60ms parse time).

## DialogTree format (different — NOT pure brace grammar)

`.DialogTree` files are a **hybrid** format: an outer flat list of "Node" records
separated by dashed lines, with embedded brace-objects for conditions/actions. Not
directly parseable by `resource_format.py` as a whole file, but each `Custom Requirement=`
/ `Custom Action=` value is parseable in isolation using it.

```
CDialogTree
{
Name=...
Portrait=...
------------------------------------------------------------
Node ID=<n> <label>
Text=<NPC's line>
Should Have Voiceover=0

Requirement=<label, or !None, or a named .can under a Requirements/ folder>
Custom Requirement=CActionExpression      <- optional inline condition
{
Action=<SomeCondition>{...}
}
Reply Text=<player's line>
Go to node ID=<target node, or blank to end/close>
Custom Action=<ActionType>{...}           <- optional, runs when reply is chosen
Icon=Quest Icon / Speach Skill Icon / Fight Icon / Exit Icon
Is Default Reply=1                        <- marks the fallback/goodbye reply
------------------------------------------------------------
Node ID=<next node>
...
}
```

No indentation inside `.DialogTree` files (flat, left-aligned), unlike other resource
files.

## Where an NPC's dialogue actually gets linked (not where you'd guess)

The reusable `Character Templates/<Name>.can` file (the NPC's AI/stats template) has
**no reference to its DialogTree at all**. The link lives in the **level's own `.zax`
placement data**: a `CGeneratorAI` spawns the entity from the Character Template, and a
`CAIInteractionSpecifier` activity on that spawn wires up the conversation:

```
Activity=CGeneratorAI
{
    Groups=Array { Group=CGeneratorAIGroup { Things to Generate=Array {
        Thing to Generate=CSpawnableCannedEntity { Entity=Levels/.../Character Templates/<Name> }
    }}}
    New Name=<InstanceName>
    AIs to Add=Array { AI=CAIInteractionSpecifier {
        Action=... CDisplayDialogTreeAction { Dialog Tree File=Levels/.../Dialog/<Folder>/<Name> Dialogue }
    }}
}
```

To find an NPC's actual DialogTree, search the relevant `Levels\<Area>\*.zax` files for
`Dialog Tree File=` near the NPC's name — don't assume it doesn't exist just because the
Character Template `.can` is silent on it.

## Quest mechanics

- `Resources/.../<Name>.Quest.txt`: `CQuestDefinition { Name=..., States=Array {...},
  Sub-Quest of=!None }`. States are optional narrative checkpoints
  (`CQuestStateDefinition{Text=..., ID=<8-char alnum token>}`) shown in the quest log —
  many shipped quests have zero states and rely purely on the status flag.
- **Status** (Active/Completed/Failed, via `CSetQuestSatusToCompletedAction` /
  `CIsQuestCompletedAction`) and **State** (which narrative checkpoint ID is "current",
  via `CActivateQuestStateAction` / `CIsQuestStateTheCurrentStateAction`) are two
  independent axes. Activating a state does not imply completion or vice versa — gate
  reply visibility on both explicitly if you need "given but not yet turned in":
  ```
  Custom Requirement=CAND
  {
  Operand1=CActionExpression { Action=CIsQuestStateTheCurrentStateAction{Quest=..., State=...} }
  Operator=
  Operand2=CActionExpression { Action=CNotAction { Action=CIsQuestCompletedAction{Quest=...} } }
  }
  ```
  (`Operator=` is left blank in every base-game example — this is normal, not a bug.)
- Quest resource paths in `Quest=` fields are `Resources/`-relative with the
  `.Quest.txt` suffix stripped, e.g. `Levels/1 Barcelona/Quests/Gate District/My Quest`.
- Give a quest reply top-level visibility (not buried in a submenu) by duplicating it
  into every one of an NPC's greeting/return-visit node variants — this is the base
  game's own convention (verified: the "wererat cure" quest reply is duplicated
  identically across 6-7 different greeting nodes for the same NPC).

## Checking "has N of an item" — no built-in primitive

`CActionCheckForInventoryItem` / `CActionRemoveInventoryItem` only test/remove a single
unit (presence, not count), even for stackable items (`CInventoryItemPlugInBehaviorMergeMultipleInstances`).
There is no `Desired Minimum Count`-style field for inventory items (that field exists on
`CCheckExistenceAction`, but only for named triggers/flags, never seen used against an
inventory item in the whole game). To require exactly N copies, nest check→remove N times
so each removal decrements the stack and the next check reflects what's left:

```
Custom Action=CIfAction
{
If=CActionCheckForInventoryItem { Who to give check=$instigator, Inventory Item To Check For=<item> }
Then=CMultipleActionsAction { Action=Array { Item Count=2
    Action=CActionRemoveInventoryItem {...}
    Action=CIfAction { <repeat for unit 2, then unit 3, with the real reward in the innermost Then> }
}}
Else=
Return failure if the If failes=0
}
```

This is untested elsewhere in the shipped game (built from confirmed-working primitives,
not copied from precedent) but works correctly in practice.

## CRITICAL gotcha: `If=` wants a bare action, `Custom Requirement=` wants it wrapped

This produces the runtime error *"tried to use a CActionExpression for a If when a
CAction is expected"*:

```
Custom Action=CIfAction
{
If=CActionExpression { Action=CActionCheckForInventoryItem {...} }   <- WRONG in an If= field
```

`CIfAction`'s own `If=` field takes the condition/action type **directly**, no wrapper:

```
If=CActionCheckForInventoryItem {...}                                 <- correct
```

The `CActionExpression{Action=...}` wrapper (and combinators like
`CAND{Operand1=..., Operator=, Operand2=...}`, `CNotAction{Action=...}`) is only for
**`Custom Requirement=`** fields on dialogue replies (which need an "Expression" type, not
a bare "Action"). Mixing these two contexts up is an easy, silent-until-runtime mistake.

## CRITICAL bug: `CGiveExperiencePointsToAllPlayersAction` does nothing when called inline from a DialogTree reply

Extensively confirmed (5+ isolated tests: varying the amount 1/25/100, flat vs. nested
structure, byte-exact copy of a "working" shipped quest's reward block including its
karma-modifier sibling, using `$instigator` vs. free-text for `Get XP Frome`, and a
fully unconditional/unnested standalone test reply) — **none granted any XP** when the
action was invoked as an inline `Custom Action=` on a dialogue reply. Gold
(`CGiveMoneyToAllPlayersAction`) and quest-completion actions in the exact same array
work every time; only this action silently no-ops.

`CGiveExperiencePointsToCharacterAction` is a registered **alias of the same class**, not
a separate implementation — don't expect it to behave differently.

**The fix**: invoke it indirectly via a standalone `.can` file dispatched through
`CUseCannedActionAction`, instead of putting it inline in the dialogue tree. This is the
same dispatch mechanism used for combat-kill XP:

1. Create `Resources/.../SomeName XP.can`:
   ```
   CCannedObject
   {
       Object=CGiveExperiencePointsToAllPlayersAction
       {
           Get XP Frome=$Trigger
           Experience Points To Add=100
       }
       Use=Shared Global Instance
   }
   ```
2. In the dialogue reply's `Custom Action=`:
   ```
   Custom Action=CUseCannedActionAction
   {
   Canned Object=Levels/.../SomeName XP
   }
   ```

Confirmed working in-game. Root cause is almost certainly something about the DialogTree
Custom Action dispatch pathway specifically (not field values/types — a "type 3 =
debug-only field" theory was explored via decompilation and looked plausible but was a
red herring; the canned-action indirection is the real, verified fix). If you hit an
action that silently no-ops from a dialogue Custom Action, try this same indirection
before assuming the field values are wrong.

`CGiveEnoughExperiencePointsToLevelUpAction{Character to give experiecne to=$instigator}`
(note the authentic typo "experiecne") is a simpler, separately-confirmed-working
mechanism if you want a guaranteed level-up rather than a specific point amount — used
repeatedly in the game's own dev cheat scripts.

## Editor-only content — don't mistake it for working mechanisms

Entities with `Model=Editor/...` and `Visible=0` (e.g. `CShowExperiencePoints`,
`CLabelPrinterAI`) are level-editor design-time annotations/audit tooling, not runtime
game logic. A `Dynamic Properties` block with a stray `Experience Points=120`-style field
next to one of these is a designer's bookkeeping note, not evidence of a working
in-game mechanism.

## Standard editing workflow

1. **Back up** `data.dat` once before any mod work (`data.dat.original.bak`).
2. Edit files directly in the unpacked `data\` directory.
3. Before repacking, sanity-check the edited `.DialogTree`/`.txt` file:
   `grep -c '^{$'` should equal `grep -c '^}$'` (brace balance).
4. Confirm `Lionheart.exe` is **not running** (`tasklist | grep -i lionheart`) — repack
   will fail with `PermissionError`/`WinError 5` otherwise, and the game must be fully
   closed (not just showing an error dialog) to release the file lock.
5. Repack: `python archive.py repack <data dir> <data.dat> --compression store`.
6. Validate: entry count, `testzip()`, compress_type set == `{0}`, and spot-check the
   specific strings/values you changed via `zipfile.read()` before telling the user to
   test — repacks of this size take several minutes, so verify structurally before
   spending a test cycle.

## Packaging & distributing mods

`data.dat` can't be redistributed (it's ~1.6GB of the copyrighted game itself), and a
full-file replacement gives no way for two people's mods to coexist. Instead, mods are
lightweight overlays built with `modmanager.py`. Registry lives inside the game directory:

```
<game-dir>\data.dat                  live, built file the game reads
<game-dir>\data.dat.vanilla.bak      pristine original, created once by `init`, never overwritten
<game-dir>\mods\installed\<id>\      installed mod packages
<game-dir>\mods\enabled.json         ordered list of enabled mod ids (last wins on file conflict)
```

A mod package is a folder: `mod.json` (plain JSON metadata — id, name, version, author,
description, explicit `files` list) plus `files/` mirroring the `data.dat` path structure,
containing **only** the files the mod adds or changes.

```
python modmanager.py init <game-dir>              # one-time: back up vanilla, set up registry
python modmanager.py package <edited-dir> <vanilla-dir> <output-dir> --id --name --version --author --description
python modmanager.py install <mod-dir-or-zip> <game-dir>
python modmanager.py list <game-dir>
python modmanager.py enable/disable <id> <game-dir>
python modmanager.py build <game-dir>              # vanilla + enabled mods -> data.dat (store mode, validated)
python modmanager.py restore <game-dir>             # revert to pristine vanilla
```

`build` always starts from a **fresh unpack of `data.dat.vanilla.bak`**, never from the
live `data/` folder or a previous build — this is what makes clean enable/disable/reorder
possible. It validates the built archive (`testzip`, all `compress_type == 0`) before
touching the live `data.dat`, and refuses to run while `Lionheart.exe` is open.

**Known open issue**: during development, `data.dat.vanilla.bak` (backed up on day one of
a modding session) was found to differ from the live, hand-edited `data/` directory on
~267 files that were never intentionally touched, for a reason that was never root-caused
(GOG background verification/sync was suspected but not confirmed). `package`'s automated
diff will surface all of these as "changed" if your `vanilla-dir` doesn't match your
`edited-dir`'s true baseline — if `package` reports far more files than you actually
touched, don't trust it blindly; spot-check a few unexpected entries, or fall back to
hand-assembling `files/` + `mod.json` from the specific files you know you changed (as was
done for the first Wolf Pelts for Quinn package). In-game testing confirmed the divergence
itself wasn't functionally harmful, but its cause is still unknown.

## Reverse-engineering tips (Ghidra/ReVa)

- Class/action field registration functions are generic and repeated per-class; look for
  the field's own name string (e.g. `"Get XP Frome"`) and use
  `find-constant-uses` on its address if `get-strings`'s `referencingFunctions` comes back
  empty — Ghidra's automatic xref analysis misses strings used as `PUSH` immediates in
  some of this codebase's registration patterns.
- Comparing a known-working action's field registration against a suspect one
  (same registrar function, different type-code parameter) is a good way to find real
  differences, but decompiled parameter semantics are unlabeled guesses — treat
  conclusions from this alone as hypotheses to test in-game, not proven fixes. The
  canned-action-indirection fix above was only confirmed by actual in-game testing after
  the type-code theory failed to pan out.
