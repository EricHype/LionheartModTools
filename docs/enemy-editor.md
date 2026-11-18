# The enemy editor

`enemyedit.py` lets you change Lionheart's creatures without opening the game's files. You
can change their stats, give them another creature's look, watch every animation their
model has, and make new creatures. Everything you save goes into your own mod.

```
python enemyedit.py
```

It shares the game folder, the original-files backup and your mod with the item editor,
so if the item editor works, this does too.

**Status:** milestones E2 and E3 of the plan are done. The window, the preview and all the
cards work. Still to come:
- "Spawn it in the game" and "Where it appears" (E4);
- the double-click launcher (E5).

## Finding a creature

The list on the left holds every creature, grouped as the game files group them:
Animals, Undead, Thugs, Toulouse and so on.

**Tough** and **Super** are separate creatures, not a setting. The game spawns them for
stronger parties: one wasp spawner, for example, makes a single Wasp for a weak party, 2-3
Wasp Tough for a stronger one and 3-4 Wasp Super for the strongest. So changing a Gray Wolf
doesn't change a Gray Wolf Tough.

## The preview

The 3D view shows the creature's model. Drag to turn it and scroll to zoom. Pick an
animation below it and choose **Loop**, the speed, or **Play again**.

The first time a model is shown, the editor prepares it, which takes about six seconds.
After that it's instant.

## The cards

- **Basics:** the name the combat log uses, and the level. The experience a creature gives
  is shown but can't be changed yet: its file names two numbers, and which one a kill pays
  hasn't been checked in the game.
- **Stats:**
  - hit points, armour class, resistances (percentages; a minus number is a weakness) and
    skills;
  - the seven attributes;
  - **Add a resistance, stat or skill...** adds one the creature doesn't have.
- **Attacks:** for each of its attacks:
  - the damage type and range, and its reach;
  - strike effects: a chance on a hit to deal extra damage, at once or over time, built
    as the item editor builds them;
  - an archer's or caster's shots get their damage from an item (an arrow or projectile).
    The card names it, and the item editor changes it (tick "Also show creature weapons").
  - Strike effects on a creature's own attack haven't been checked in the game yet.
- **Behaviour:** the can editor's Combat AI panel:
  - who it attacks and from how far;
  - how far it chases;
  - whether it answers calls for help;
  - its attack style and distances.
  It refuses the three attack styles that crash the game. Retreat is shown greyed out,
  because the engine never runs it.
- **Drops:**
  - the drop recipes it uses when the player kills it, such as a gold pile at 1 in 2 or
    a spirit health drop. Each is one of the game's 66 shared recipes, shown with the
    line its author wrote. Change, remove or add them.
  - Items its death action makes directly (a wolf's pelt) are listed but not changed here.
  - A few creatures have no place in their death action for drops, and the editor says so
    instead of guessing.
- **Sounds:** what it plays when hurt, when it dies and while it's active. **Play** plays
  one; **Change...** picks from the game's 1,436 sounds and plays each as you pick.
- **Model:**
  - its model, its animations (click one to play it), and the animations this creature
    uses;
  - **Change the model...** lists every creature model. It only lets you pick one that has
    every animation this creature uses: the game's unused Horse calls an attack its model
    doesn't have, and that kind of mismatch breaks a creature.

**Shared stats.** Many creatures share their stats with others: the Gray Wolf's are also
the Horse's. Before your first stat change, the editor asks whether to give the creature
its own copy, so only it changes. **Give it its own stats** does the same at any time.

**Size** belongs to the model, not the creature, so it can't be changed for one creature.

## Making a new enemy

Press **Make a new enemy from this...** and give it a name. The copy gets its own stats
from the start, so changing it never changes the original. A new creature isn't in the
game until something spawns it.

## Saving and testing

**Save into my mod** writes the creature (and its stats, if you changed them) into your
mod and runs the engine checks.

**A saved game keeps the creatures it has already spawned**, and each map's creatures from
your first visit. Your change shows on creatures spawned after you install the mod, and in
a new game.

## For maintainers

- `enemyedit_core.py` is the core: a creature is its can plus its race, and both round-trip
  byte-identically.
- `enemyedit_preview.py` holds the model cache, the localhost viewer server and the
  preview widget.
- `viewer/creature.html` is the three.js page.
- E0's findings, which these rest on, are in `docs/enemy-editor-e0.md`.
- Tests: `tests/test_enemyedit_core.py` and `tests/test_enemyedit_window.py`. The window
  tests run headless; the 3D view needs the real display, because WebGL loses its context
  under Qt's offscreen platform.
