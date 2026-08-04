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
