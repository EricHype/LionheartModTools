# Test Pocket *(work in progress)*

A brand-new, standalone map that doesn't correspond to any existing area in the shipped
game — built from scratch to prove that adding entirely new spaces is possible, not just
new content inside existing ones.

## What it does

Talk to Quinn the herbalist in the Gate District and select "Take me to the test pocket."
to travel there. It's a small, mostly flat, single-texture space (see "Known limitations"
below) with a real door back to Quinn's shop.

It's populated with its first quest: **Lucia** stands next to a locked chest she says she
can't bring herself to open. Open the chest to retrieve a necklace and bring it to her —
she puts it on, and after a short exchange, transforms into a hostile wererat and attacks.

## Files changed

- `Levels/1 Barcelona/Test Pocket.zax` (new — the map itself: terrain, spawn point, door,
  Lucia's two forms, the chest)
- `Resources/Levels/1 Barcelona/Dialog/Gate District/Herbalist Dialogue.DialogTree`
  (modified — adds the travel option to Quinn)
- `Resources/Levels/1 Barcelona/Character Templates/Test Pocket/Lucia.can` (new, human
  form)
- `Resources/Levels/1 Barcelona/Character Templates/Test Pocket/Lucia Wererat.can` (new,
  monster form)
- `Resources/Levels/1 Barcelona/Dialog/Test Pocket/Lucia Dialogue.DialogTree` (new)
- `Resources/Levels/1 Barcelona/Quests/Test Pocket/Lucia's Necklace.Quest.txt` (new)
- `Resources/Levels/1 Barcelona/Quests/Test Pocket/Lucia's Necklace XP.can` (new)

## Install

```
python modmanager.py install "mods/test-pocket" "<path to game folder>"
python modmanager.py build "<path to game folder>"
```

## Save compatibility

Same caveat as any mod adding new entities: `Test Pocket.zax` is a level nobody's save has
ever visited, so it always loads fresh the first time — no special save requirements for
reaching it. Once you're in it, though, Lucia and the chest are new entities *within* that
level, so if you've already visited Test Pocket on a save before this update, you'll need
a save that's never entered Test Pocket at all (or a new game) to see them.

## The reactivity experiment

Lucia carries a live test of whether **a brand-new `Game Scripting Variable` works without
sweeping the whole archive**. That question gates a lot of Lionheart Fixt: the game tracks
choices as derived character attributes (`Herbalist Dead`, `Goblin Kill Counter`,
`FACTION LEADERS KILLED`), but the full attribute list is written out inline in all 1698
`.can` templates and in every `.zax`, so it is not obvious that a new one can simply be
dropped in.

Three files, mirroring the shipped structures exactly:

| File | Role |
|---|---|
| `.../Game Scripting Variables/Fixt Reactivity Test.DerivedCharacterAttribute` | the variable, shaped like `Herbalist Dead` |
| `Resources/Dialog/Requirements/Fixt Reactivity Test IS.can` | the gate, shaped like `Faction/Templar IS` |
| Lucia's `.DialogTree` | sets it on one reply, reads it on another |

**To run it.** Talk to Lucia and ask *"Who are you?"*. Her introduction offers
*"Remember me, Lucia."* — choosing it raises the flag and returns to the start. Re-open the
conversation: a reply reading *"You said you would remember me."* should now be there, and
should have been absent before.

| Outcome | What it means |
|---|---|
| Reply absent, then present | It works. New variables are free; no sweep needed. |
| Reply never appears | The engine did not pick the attribute up. Fixt needs a bulk edit or another mechanism. |
| Reply present from the start | The gate reads a missing attribute as satisfied — the comparison is not safe to build on. |
| Dialogue fails to open | One of the two new resource files does not parse. |

Try it on an **existing save first** — that is the harder case, because the character was
created before the attribute existed. If it fails there, retry on a new character; the
difference tells us whether the problem is the archive or the save.

## Known limitations (why this is still WIP)

- **The ground is flat and uses a single, unblended texture**, so it looks visibly tiled
  rather than like natural terrain. The game blends multiple ground textures together
  procedurally in every shipped level, but that mechanism wasn't reverse-engineered —
  see `SKILL.md` for what was and wasn't figured out about `CPlasmaTileMap`.
- **No level-editor-baked navigation data.** The map was cloned from the game's own
  leftover dev scratch template rather than a fully-processed real level, which is why an
  early attempt at an invisible zone-based exit never worked at all (no interaction cursor
  ever appeared) — the working exit is a real door object instead. Full writeup in
  `SKILL.md`.
- Small and sparsely populated by design — this is a proof of concept for "can a genuinely
  new map exist and be interactive," not a finished area.
