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
  keeps it as it is. **Add extra damage...** adds damage of another type that lands
  whenever the weapon's own blow does. It can hit everything, or only the undead, goblins,
  dragons or titans. No original item uses dragons or titans yet, so test those in the
  game.
- **When a strike lands** (weapons only): a chance to deal damage on a hit, either at once
  or over time like poison or bleeding. Over time, the damage lands once every 5 seconds
  for as long as it lasts, so the duration is a multiple of 5. The card shows the total
  and the damage a second, and says whether that's within the original game's range
  (0.20 to 1.20 a second). Hitting again restarts the effect; it doesn't add a second
  one.
- **Bonuses while worn or held**: each stat the item raises, and by how much. Tick **%**
  for a percentage. **Add a bonus...** opens a searchable list of every stat. Use a minus
  sign for a penalty.
- **When used** (potions, scrolls, books): how long the effect lasts, the name shown on
  screen while it's active, and the stats it changes. **Add a stat change...** adds
  another. The duration is stored twice, once for the effect and once for the on-screen
  icon, and the editor sets both. Two Fire Protection items in the original game have
  them out of step, and the Problems box says so. Books and the Potion of Luck change
  stats permanently. Healing, mana and the other scripted effects are described but
  can't be edited yet.
- **Wands** are described, not edited. In the original game, wands never drop, they only
  work when the game's loot generator makes them, and their charges never run down.
  Lionheart Fixt's engine notes cover this in detail.
- **Art**: the item's pictures. See "Changing the art" below.

Press Enter or click elsewhere to finish typing in a box. **Ctrl+Z** undoes, and
**Ctrl+Y** redoes.

**Describe what you add.** The game shows only the description you write; it never
lists extra damage or strike effects by itself. Both cards suggest a sentence, and
**Add this to the description** adds it.

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
- To make a new potion or scroll, copy one that does something close, then change its
  stats and how long it lasts. The editor doesn't add "when used" effects to items that
  don't have one: whether the game uses an item up when it's used isn't known for every
  kind of item, and a permanent bonus on an item that isn't used up could be used again
  and again.

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

- Healing, mana and other scripted "when used" effects can't be edited yet, and wands
  aren't editable at all (see above).
- Most of the original game's strike effects are one-off scripts (fear, freezing,
  healing the wielder). The card describes them and can remove them, but can't change
  them.
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
- A new strike effect has the same tree as the Bloodletter mod's working bleed, with plain
  numbers where Bloodletter reads named amounts; a test holds it to that. Damage over time
  stores the total (per tick x duration / 5), as the Fixt engine notes establish.
- Edits go through `QUndoStack` as whole-file snapshots. Item files are a few KB, so every
  kind of edit undoes the same way.
