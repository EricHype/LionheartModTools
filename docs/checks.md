# Engine-rule checks (`lhcheck.py`)

`lhcheck.py` statically checks a mod against the rules the engine enforces. Every rule is here
because a mod broke it and shipped, almost always in a file that parsed, round-tripped and looked
fine. Most of them came from Lionheart Fixt, where they ran as its release gate (`tools/validate.py`)
before they moved here so any mod could use them.

```
python lhcheck.py <mod root> [--vanilla <data.dat.vanilla.bak>] [--skip ID] [--only ID]
python modmanager.py check <mod dir> [--vanilla ...] [--skip ID] [--only ID]
python lhcheck.py --list
```

- **`<mod root>`** is the folder that overlays `data.dat`, the one holding `Levels/`, `Resources/`
  and `Cache/`. You can also pass a modmanager package (`mod.json` beside `files/`), and its
  `files/` is checked.
- **`--vanilla`** defaults to `$LIONHEART_VANILLA`, then to the GOG install's
  `data.dat.vanilla.bak`. Most checks compare against vanilla, so the archive is required.
- **`--exe`** points at `Lionheart.exe` for `class-names`. It defaults to the copy beside the
  archive. If the exe is missing, that check prints a note and is skipped.
- **`--skip` / `--only`** take check IDs from the table below. Both are repeatable and accept
  comma-separated lists. An unknown ID is an error, so a typo can't silently skip nothing.
- **Exit codes:** 0 when clean, 1 on any finding, 2 on a usage error.

The output looks like this:

```
checked 1405 files (41 binary payloads skipped)
all checks passed
```

When there are findings, it prints `N PROBLEM(S):` and one `   - ` line for each.

It runs the whole tree against the whole archive in about 30 seconds. Two runs at once are safe:
the round-trip comparison is done in memory (see `parse` below).

## Inherited breakage is excused

Most mod files are shipped files with something spliced in, so they inherit vanilla's own defects.
Where a check could blame a mod for one of those (dangling node targets, dangling names, the NIS
exits, model/sequence pairs, nameless spawns), it compares against the vanilla copy of the same file
and reports only what is new. A file the mod authored from scratch has no vanilla copy, so
everything in it is judged.

## The checks

The QA column is the Lionheart Fixt `docs/qa.md` case the check implements, where it has one.

### Per file

| ID | QA | Catches | The defect behind it |
|---|---|---|---|
| `encoding` | A0.9 | An empty binary payload (`.mdl16`, `.ogg` and the like). It also runs a latin-1 decode, which can never fail (see Known limits). | |
| `nested-assignment` | A0.18 | A value that is itself `Key=Value`, or a brace as a whole value. | Fixt 0.71.10: a builder wrote `Else=` into the indentation, so `Else=<tab>Dialog Tree File=...` shipped seven dead lines. Of 4,035,942 vanilla `key=value` lines, the only `=`-bearing values are `Action work in progress`, `Text` and `Reply Text` prose, which are exempt. |
| `dialogtree` | A0.1-A0.5 | Missing `CDialogTree` wrapper or brace, mixed or LF line endings, duplicate node IDs, a `Go to node ID` that resolves to no node (matched case-insensitively, as the engine does; 244 vanilla links rely on that), a named `Requirement=` that is no `.can`, reply/goto count mismatch, a reply not preceded by a blank line, a blank line inside a block, and an embedded `Custom Action`/`Custom Requirement` that fails to parse. | The reply separator: without its blank line a reply is swallowed into the one before it and its action never runs (Fixt 0.2.0 to 0.5). A blank line inside a block ends the reply early (Fixt 0.15.0). |
| `nis-exit` | A0.16 | A newly reachable exit, in a tree that runs wholly inside a non-interactive sequence, that never ends the sequence. **The game hangs with the gears spinning.** | Fixt issue #6: `cathartoughs`' `100 Welcome`, a vanilla orphan the mod made reachable. **Blind spot:** a node a *map* opens directly is not an exit to this check. Grep the maps before "fixing" a node by adding actions. |
| `node-refs` | A0.7b | A map, can or tree's `CDisplayDialogTreeAction`/`CDisplayDialogBalloonAction` naming a node that does not match **byte-exactly**, trailing spaces included. A miss is a crash on map entry. | The Goblin Warrens crash. Vanilla node IDs carry trailing spaces, and the map-side lookup is exact (replies and VO resolve trimmed). |
| `parse` | A0.6/A0.7 | A resource file that doesn't parse, or doesn't round-trip byte-exact through `resource_format` ("not in canonical engine formatting"). | Fixt's gate once wrote the round-trip to one fixed temp file, so two concurrent runs read each other's output and invented failures. It is compared in memory now. |
| `class-whitespace` | A0.6 | A class-valued field led by whitespace: `Then=<tab>CMultipleActionsAction`. The engine looks up a class named with the tab and refuses the map. | Fixt 0.9.1, from re-indenting a nested block. The parser keeps the whitespace, so only a byte check sees it. |
| `orphan-block` | A0.17 | `Some Field=` with an empty value, followed by a `{` block: a class name deleted, its block left behind. Fatal at load (*Found unexpected '{' while parsing class ...*). | Fixt 0.60.5's `Fixt Horse Calm.can` crashed the Crossroads. `resource_format` round-trips it happily. |
| `array-count` | A0.6 | An `Array` whose `Item Count` (or `Array Count`) disagrees with its items, or does not come first. | A splice that landed a new item inside the previous item's braces (two Fixt reset relays). And Fixt 0.79.7 inserted ahead of the count: the number was right and the item was dropped (0 of 103,108 vanilla arrays do that). |
| `models` | A0.8 | A `Model=` naming art that doesn't exist (fatal on map entry), a `Cur Sequence` vanilla never pairs with that model, or a model no vanilla map places at all. | Fixt 0.5.0's chest at an invented path, and the same chest set to `Idle` when chests only have Closed/Open/Opening. Barrel Explode is effect art with no Idle, and the undercroft crashed with *Idle is missing*. |

### Whole tree

| ID | QA | Catches | The defect behind it |
|---|---|---|---|
| `quest-states` | | A quest state the mod defines that nothing activates. Also catches a `States` array whose `Item Count` is wrong, duplicate state IDs, and an ID that is not 8 uppercase alphanumerics. Activations are collected from the mod and from vanilla, with the mod's files shadowing their vanilla copies. | Two of Quinn's errands (Fixt 0.4.0) could be offered and accepted but never started. |
| `references` | A0.8 | A path-shaped reference (`Canned Expression=`, `Canned Object=`, `Entity=`, `Dialog Tree File=`, `Quest=`, `Perk To Check For=`, inventory items, `Skill=`, `Race=`, `Derived Character Attribute=`) that resolves to no file. | A can's *name* written in path form evaluates false forever (three Fixt releases). A `.race` naming a missing skill is fatal before the main menu (Fixt 0.19.0's Grace O'Malley). |
| `self-reference` | | A DialogTree naming its own file, or trees forming a loop (vanilla's trees included). The loader hangs: *Got stuck in an infinite loop*. | The fix is indirection through a `CCannedObject` fired by `CUseCannedActionAction`, as Fixt 0.25.2 did at eleven sites. |
| `overridden-drops` | A0.14 | A quest-item drop on a creature can's `Destroyed Script Action` that the spawning generator's `CSetDestroyedScriptActionAction` replaces, so it can never fire. | Fixt 0.10.0's Lava Troll Hide. Vanilla puts the Titan bosses' drops in the generator's `New Destroyed Action`. |
| `class-names` | A0.15 | An `=CSomething` that is not a class the engine registers (the C-prefixed strings in `Lionheart.exe`, plus every class vanilla's data uses). A hard CTD: *Invalid class type*. | Fixt 0.13.0's `CIsQuestStatusCompletedAction`, which survived 27 releases. Quest state IDs (`State=CHF3M8QW`) are excluded. |
| `dangling-names` | | A name-bearing field (`Target Name`, `Entity Name`, `Enemy Name`, `Speaker`, `Door Name` and others) naming something no map or can in the game could be called. The action is inert. | The Montserrat garrison lists, written from filenames rather than the spawns' `User Assigned Name`, sat broken in Fixt for fourteen releases. |
| `empty-blocks` | | `CEntityBehaviorMoveToPos`, `CEntityBehaviorTurnToAngle` or `CNonInteractiveSequence` shipped empty. Vanilla fills them 4735/4735, 1355/1355 and 169/169 times. Maps and trees are both checked. | The Montserrat rout: with no move behaviour the garrison walked through walls and never arrived, and in `Sahar.DialogTree` it was deleted on the spot. |
| `series-flow` | | A relay with `Trigger Only Once=0` that fires itself inline, not across a delay or an After Action. It recurses until the stack overflows. Also a self-chained `CSeriesAction` with a step that neither fires the relay nor shows a conversation, which makes every later step dead. | Fixt's epilogue put the credits ahead of fifteen steps of slides. The dead-step rule covers only relays the mod named (see Configuration), because vanilla steps some series from outside. |
| `nameless-spawns` | | A script aimed at the display name of a spawn that has no `Name`: a generator with a blank `New Name` over a can with a blank `Name`. The lookup reads `Name` only (`0x62bc00`), so the script reaches nobody. | Fixt's English that did not stand down. The repair is to give the generator a `New Name`. |
| `corpse-specifier` | A0.18 | An interaction specifier added to `$Instigator`/`$Trigger` inside a destroyed script. It can only run on a corpse. Vanilla does this 0 times in 336; its 4 specifiers in death scripts all name another object. | Fixt 0.79.9: Sahar was handed her conversation when she died. |
| `repeatable-grants` | A0.19 | A node that gives an item, reachable from the tree's entries without crossing a gate, while another reply into it *is* gated. It can be taken every conversation. | Fixt 0.83.3: Brendan's Serpent's Bile had six gated doors and one open one. Checking incoming edges alone would flag correct trees such as `Magic Machine`, so this check walks reachability instead. |

## A separate tool: `auto_open_risk.py`

```
python auto_open_risk.py <mod root> [--vanilla ...] [--allow allow.json]
```

This tool flags a node opened by a proximity specifier (`GetCloseThenTrigger*`) that carries a
costly reply: item removal, money, pain or quest completion. One dismissive click can then destroy
something. In Fixt 0.57.4 the rotary gear was lost the moment a player walked into the chamber.
The tool reads every map in the archive, so it is slower than lhcheck and is kept separate. Run it
when you add a reply to anything a map opens on its own.

A node whose own text asks the question is a decision rather than a dismissal. Vanilla has one,
Relican's summoning ring, and it is allowed by default. A mod lists its own in a JSON file:

```json
[{"tree": "Relican Summoning Ring", "node": "01 Conversation Start", "why": "its text asks"}]
```

## Configuration, for a mod's own history

`lhcheck.Context(root, vanilla, config={...})` takes three settings. They exist because Fixt needs
them, and the defaults suit a new mod:

- **`chained_relay_prefix`**: the dead-step rule in `series-flow` applies to relays the mod named.
  With a prefix, that means relays whose names start with it (Fixt passes `"Fixt"`). Left unset, it
  means relays absent from the vanilla copy of the same map.
- **`nameless_spawn_skip`**: map paths that `nameless-spawns` leaves alone.
- **`nameless_spawn_excuse_vanilla`**: `True` by default. A mod that repairs vanilla's nameless
  generators itself, as Fixt does, sets it to `False` so a regression is reported.

A mod adds its own checks through `run(ctx, extra=[(id, fn, after_id)])`. Each `fn(ctx)` returns
messages, and `--skip`/`--only` select it like any built-in check. Fixt's `PROBE ` check (a
display-name convention for its deploy-only experiments) is added this way.

## Known limits

- **A mod built on another mod is checked against vanilla only.** References into the base mod
  are reported as unresolved. Probes and kits layered on Fixt hit this.
- **`encoding` can't detect bad text.** A latin-1 decode never fails on any byte sequence, so the
  "not latin-1 clean" branch can't fire. A UTF-8 file passes it.
- **`overridden-drops` matches `Entity=` case-sensitively.** A map naming the can in a different
  case is not seen as a spawner.
- **`repeatable-grants` follows `Go to node ID` case-sensitively**, while the engine resolves it
  without case, so a case-variant link ends the walk early.
- **`corpse-specifier` misses a death script that is itself the `CAddAIAction`.** It looks only
  inside the script's children.

## Tests

`tests/test_lhcheck.py` builds a tiny mod and a stand-in vanilla archive in a temp folder for each
check. It seeds the real defect, asserts the check reports it, then removes the defect and asserts
silence. Two further tests use the real archive and skip without it.

```
python -m unittest tests.test_lhcheck -v
```
