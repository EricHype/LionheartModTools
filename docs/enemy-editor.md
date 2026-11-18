# The enemy editor

`enemyedit.py` lets you change Lionheart's creatures without opening the game's files. You
can change their stats, give them another creature's look, watch every animation their
model has, and make new creatures. Everything you save goes into your own mod.

## Starting it

Double-click **`Enemy Editor.bat`** in the LionheartModTools folder.

It shares everything with the item editor: the setup, the game folder, the backup of the
game's original files and your mod. If the item editor already opens, this opens straight
away. If not, the first run sets itself up as `Item Editor.bat` does (about 100 MB, once,
and it needs Python 3.9 or newer); see [the item editor's guide](item-editor.md#starting-it).

If the 3D view says this computer can't draw 3D inside the editor, everything else still
works; only the preview is missing. Updating the graphics driver usually fixes it.

If something goes wrong, the editor shows the error and saves a copy in
`%LOCALAPPDATA%\LionheartModTools\itemedit-error.log` (the same log the item editor
uses), which you can send to whoever helps you.

From a terminal, `python enemyedit.py` does the same, with `--game` and `--mod` to skip
the questions and `--open <creature file>` to open one creature.

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

## Where it appears

Beside the preview, **Where it appears** lists:
- the maps whose spawners make this creature, and the party strengths it's chosen for;
- anything else that creates it by name;
- its Tough or Super versions, each a separate creature.

Developer test maps are listed separately, because players never visit them. A new
creature shows "Nothing in the game spawns this creature" until you place it with the map
editor.

## Spawning it in the game

Press **Spawn it in the game...**. The editor saves the creature if needed and adds a
**Summon:** reply to the test menu in your mod, the same menu the item editor uses:

1. Press **Install my mod in the game** at the top.
2. In the game, talk to **Raymundo the Bowyer**, the bow seller in the Barcelona Gate
   District.
3. Choose **[TEST] Show me the items I am testing.**, then **Summon: <name>**. It appears
   beside you, ready to fight.

It works on a save you already have. Summoning from a conversation uses the same action as
the developers' own "companion" cheat, but it hasn't been checked inside a conversation in
the game yet.

The test menu stays in your mod until you take it out with the **Test menu...** button at
the top. Take it out before you share the mod.

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
- `enemyedit_reach.py` scans the maps' spawners for **Where it appears**.
- E0's findings, which these rest on, are in `docs/enemy-editor-e0.md`.
- The script for a first user trial is `docs/enemy-editor-trial.md`.
- Tests: `tests/test_enemyedit_core.py`, `tests/test_enemyedit_reach.py` and
  `tests/test_enemyedit_window.py`. The window
  tests run headless; the 3D view needs the real display, because WebGL loses its context
  under Qt's offscreen platform.
