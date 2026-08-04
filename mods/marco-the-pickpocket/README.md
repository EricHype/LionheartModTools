# Marco the Pickpocket

Adds a new NPC, Marco, near the blacksmith's shop in the Gate District (Barcelona).

## What it does

Marco reacts to whether your character has the **Thief** perk:

- **Without the perk**: he warns you that the streets can be dangerous once night falls.
- **With the perk**: he greets you as a fellow member of the underground and opens a shop
  stocked with gear suited to a rogue playstyle — light armor, a cloak, boots, a short
  sword, a club, skeleton keys, some fenced jewelry, a Sneak-boosting potion, and a couple
  of item slots that scale with your character's level (the same mechanism the Blacksmith
  uses for his own randomized stock).

## Files changed

- `Resources/Levels/1 Barcelona/Character Templates/Gate District/Marco.can` (new)
- `Resources/Levels/1 Barcelona/Dialog/Gate District/Marco Dialogue.DialogTree` (new — no
  perk)
- `Resources/Levels/1 Barcelona/Dialog/Gate District/Marco Dialogue Thief.DialogTree` (new
  — has perk)
- `Levels/1 Barcelona/Gate District.zax` (modified — adds Marco's spawn point and his
  shop's inventory entity)

## Install

```
python modmanager.py install "mods/marco-the-pickpocket" "<path to game folder>"
python modmanager.py build "<path to game folder>"
```

## Save compatibility

Marco is a **brand-new entity** in an existing level. New entities don't appear on a save
that has already visited that level (the game locks in each level's entity list on first
visit) — only editing existing NPCs' dialogue refreshes on revisit. If Marco doesn't show
up, use a save that's never entered the Gate District, or start a new game. See `SKILL.md`
in the repo root for the full explanation.
