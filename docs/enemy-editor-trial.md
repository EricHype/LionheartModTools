# Enemy editor: user trial

A script for watching someone who has never seen the editor try it. Hand them only the
tasks below, not [the guide](enemy-editor.md). Don't help unless they're stuck for more
than a couple of minutes, and note where they hesitate, what they click first, and any
word they ask about.

They need Lionheart installed and a save they don't mind playing on.

## Tasks for the tester

1. Open the enemy editor.
2. Find the Grey Wolf and watch it attack.
3. Make the Grey Wolf harder to kill.
4. Make the Grey Wolf sometimes drop gold.
5. Make a new enemy from the Grey Wolf that looks like a bear. Give it a name of your own.
6. Fight your new enemy in the game.
7. Get your mod ready to give to a friend.

## What each task tests

| Task | Done when | Watch for |
|---|---|---|
| 1 | The window is open | Do they find `Enemy Editor.bat`? Is the first-run wait or the game-folder question confusing? |
| 2 | An attack animation plays | Do they find the animation list under the preview? |
| 3 | Hit points raised and saved | The "stats are shared with: Horse" question: do they understand it, and which answer do they pick? |
| 4 | A gold drop on the Drops card, saved | Are the drop recipe names readable? |
| 5 | A new creature with the Bear's model, saved | Do they find **Make a new enemy from this** and the Model card? Does the refused-model message make sense if they pick one? |
| 6 | The creature appears beside them in the game | **Spawn it in the game**, **Install my mod in the game**, then Raymundo's menu. Did it appear? |
| 7 | The test menu is taken out | Do they notice the orange notice at the top and use **Test menu...**? |

Task 6 is also the first in-game check that summoning from a conversation works. If
nothing appears, note it: that's an editor bug, not the tester's mistake.

## Afterwards, ask

- Which card was hardest to understand? (The Behaviour card is expected to be; how badly?)
- Was anything you changed not what you expected in the game?
- What did you want to change that you couldn't find?

## Checks to make in the same session

These need the game, and the trial is a good time for them:

- **Experience:** kill a Grey Wolf with the combat log open and note the experience it pays.
  39 means the creature's own value counts, 250 means the death action's. The Basics card
  shows experience read-only until this is known.
- **Strike effects:** use **Add a strike effect...** on the wolf's bite (on the Attacks card) and check it fires in the game.
