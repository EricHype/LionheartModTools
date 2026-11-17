# The item editor

`itemedit.py` lets you change Lionheart's items without opening any of the game's files.
You can change an item's name, description, price, damage and bonuses, make new items,
and repaint their art. Everything you save goes into your own mod.

You need Python 3 with PySide6, and the game installed with `modmanager.py init` run once
(see the README).

```
python itemedit.py
```

## Your mod

The first time, the editor asks for a mod. A mod is a folder with a `mod.json` file and a
`files` folder. Press **New mod...**, give it a name and pick where to put it. The editor
remembers it next time. **Choose my mod...** switches to another one.

## Finding an item

The list on the left holds every item in the game, grouped:

- **Items**: the plain swords, armour, rings, potions, gems and so on.
- **Magic: ...**: what a random magic item adds to a plain one. "of Poison Resistance" turns
  a Helmet into a "Helmet of Poison Resistance". They're grouped by what they go on.
- **Unique items**: the named one-of-a-kind items, like the Gauntlets of La Mancha.
- **Quest items** and **Keys**.

Type in the search box to narrow the list. Items from your mod say "(in my mod)".

## Changing an item

Click an item. Its cards appear in the middle, and on the right is **How the game shows
it**: a preview of the tooltip you'd see in the game. It updates as you edit. The preview
is close to the game's, but not exact.

- **Basics**: the name, the description, the weight and the price. In a description,
  `%s[damage]` is replaced by the item's damage, such as "Slashing Damage 1-5".
- **Damage**: the damage type and its range. Every melee weapon also gets a bonus from the
  wielder's skill and weapon specialization. The card shows how big that bonus can get and
  keeps it as it is.
- **Bonuses while worn or held**: each stat the item raises, and by how much. Tick **%**
  for a percentage. **Add a bonus...** opens a searchable list of every stat. Use a minus
  sign for a penalty.
- **Art**: the item's pictures. See "Changing the art" below.

Press Enter or click elsewhere to finish typing in a box. **Ctrl+Z** undoes, and
**Ctrl+Y** redoes.

When you're done, press **Save into my mod**. The first time you save an item from the
game, the editor reminds you that the change applies to every copy of that item in the
game.

### Magic

Magic has two extra things:

- **How rare**: how often the game picks this magic when it makes a random magic item.
- **How strong it rolls**: when the game makes the magic, it rolls an amount between
  **Weakest** and **Strongest**. The numbers in its description and its bonuses come from
  that roll, and so does the price it adds to the item.

Above the preview, pick the roll and the item to show the magic on. "Strongest" on
"Helmet" shows the Helmet at the top roll.

## Making a new item

Click the item that's closest to what you want, then press **Make a new item from
this...**. Give it a name, change it however you like, and save it.

- Copying a plain item, like a gem, makes a new kind of object.
- Copying a unique makes a new unique.
- Copying a magic addition makes new magic. Its name is what follows "of": "Frost" makes
  "Helmet of Frost".

The editor takes care of the hidden places where an item names itself, so the copy reads
its own numbers and never the original's.

**A new item isn't in the game until something gives it to the player.** Put it in a
chest or a shop with the map editor.

## Changing the art

On the **Art** card:

1. Press **Export pictures to paint...** and pick a folder. Each picture is saved as a PNG
   at four times its size.
2. Paint over it in any program. Keep the canvas size, and leave transparent areas
   transparent.
3. Press **Import a painted picture...** and pick the PNG. The editor checks it first and
   tells you what it will do.

If other items share the picture, your item gets its own copy, so only your item changes.
The rules for painting are in [icon-tool.md](icon-tool.md).

## Problems

The **Problems** box on the right lists things that would look wrong or do nothing in the
game, for example:

- a description showing an amount the magic doesn't have;
- a damage minimum above its maximum;
- rolls out of order.

You can still save with problems, but the editor asks first.

## Testing in the game

Install your mod the usual way:

```
python modmanager.py install <your mod folder> "<game folder>"
```

**A saved game keeps its own copy of every item the player already has**, and of every
item lying in a map they've visited. Your change shows up on items found after you
install the mod, and in a new game. It doesn't show up on ones already picked up.

## What it doesn't do yet

- Damage over time, wands, potions and other "when used" effects are shown but can't be
  edited yet.
- Damage and bonuses that come from a formula (a few special items have them) are left
  as they are.
- It can't place items in the world. Use the map editor for that.

## For maintainers

- `itemedit_core.py` is the headless core, and `itemedit.py` is the PySide6 window.
- Tests: `tests/test_itemedit_core.py` (including byte-identical round-trip of every item
  file) and `tests/test_itemedit_window.py` (drives the window offscreen).
- Coverage over the shipped items:
  - **Bonuses:** 460 of 477 are editable. 278 are fixed numbers, and 182 come from the
    roll and are edited through the roll card. The other 17 are formulas.
  - **Damage:** 79 of 125 entries are editable ranges. 17 come from the roll, and 18 are
    damage over time. The other 11 are formulas.
- Edits go through `QUndoStack` as whole-file snapshots. Item files are a few KB, so every
  kind of edit undoes the same way.
