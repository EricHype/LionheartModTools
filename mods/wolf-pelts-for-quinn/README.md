# Wolf Pelts for Quinn

Adds a small quest to Quinn the herbalist's shop in the Gate District (Barcelona).

## What it does

Talk to Quinn and she'll ask you to bring her three wolf pelts, which she wants to test
for magical corruption. Kill wolves until you have three pelts, then return and turn them
in for a small XP reward. The turn-in option only appears once you actually have the
pelts, and the quest only offers itself once.

## Files changed

- `Resources/Levels/1 Barcelona/Quests/Gate District/Wolf Pelts for Quinn.Quest.txt` (new)
- `Resources/Levels/1 Barcelona/Quests/Gate District/Wolf Pelts for Quinn XP.can` (new)
- `Resources/Levels/1 Barcelona/Dialog/Gate District/Herbalist Dialogue.DialogTree`
  (modified — adds the quest offer and turn-in replies)

## Install

```
python modmanager.py install "mods/wolf-pelts-for-quinn" "<path to game folder>"
python modmanager.py build "<path to game folder>"
```

## Notes

Quinn is an existing NPC and this only edits her dialogue file (no new entities), so there
are no save-compatibility caveats — it works on saves that have already visited the Gate
District, including ones already in progress.
