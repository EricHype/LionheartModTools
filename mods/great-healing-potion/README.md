# Great Healing Potion

Adds a genuinely new item -- not a reused/reskinned existing one -- proving out the
full "add a new item with new art" pipeline documented in
`docs/adding-a-new-item.md`. **Confirmed working end-to-end in-game**, including the
custom icon art.

- **New item definition**: `Inventory/Inventory Additions/Miscellaneous/Potions/Great
  Healing`, cloned from the game's own `Extra Healing` potion with scaled-up healing
  (15-18 + 8% max HP normal, 30-36 + 16% max HP with Vampiric Fury) and higher value
  (60) and rarity (Rare). Still removes critical hits on use, like Extra Healing.
- **New icon art**: a gold/amber recolor of the real `Extra Healing` flask icon. Built
  with `mdl16_format.recolor_icon_in_place()` -- decodes the real icon's compressed
  sprite data (reverse-engineered this session, see `docs/mdl16-icon-format.md`),
  transforms only the stored color values, and leaves the original RLE opcode
  structure byte-identical. Confirmed rendering correctly in-game. Building a wholly
  new icon *shape* from scratch remains unsolved (see that doc) -- this mod only
  needed a recolor, which is why it works.

## Requires

`test-pocket` must also be installed and enabled, and must load **before** this mod
(earlier in `mods enabled.json`'s order) -- this mod ships its own copy of `Test
Pocket.zax` with one extra chest-reward action added, so it needs to load after and
win the "last mod wins" conflict, or the necklace gets a bonus potion for free.

## Testing

1. `python modmanager.py enable <game-dir> great-healing-potion` (after test-pocket is
   already enabled)
2. `python modmanager.py build <game-dir>` -- as of this session, `build` also syncs
   the game's loose file mirror automatically (see `SKILL.md`'s "Loose file mirror"
   section); no separate manual step needed.
3. In-game: talk to Quinn the Herbalist in the Gate District, take the new dialogue
   option to the Test Pocket, and open Lucia's chest. You get **both** the Necklace
   and a Great Healing potion.
4. Check the potion's icon in your inventory -- confirmed rendering correctly, gold
   flask art distinct from the original brown Extra Healing icon.

## What this mod's development surfaced (see `SKILL.md` / `docs/mdl16-icon-format.md`)

Getting this one item fully working, especially the custom icon, took far longer than
the mechanic itself and surfaced two real, previously-undocumented bugs in the
project's own tooling:

1. **The loose file mirror bug**: `<game-dir>\data\` is a complete loose copy of
   `data.dat`'s contents that the game reads in *preference* to `data.dat` whenever a
   loose file is present. `modmanager.py build` never touched it, silently making
   rebuilds invisible to the running game. Now fixed -- `build` auto-syncs it.
2. **The `.mdl16` icon RLE codec**: fully decoded, and a production-ready in-place
   recolor path is proven. Building a brand-new icon's RLE stream from scratch is
   still unsolved -- every attempt round-tripped correctly through this project's own
   decoder but rendered corrupted in-game, because the real encoder follows some
   run-selection heuristic (which opcode to pick, how long to make each run) that
   wasn't reverse engineered. Recoloring an existing icon sidesteps needing to know it.
