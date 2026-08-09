# Healing Potion Tiers

Adds three genuinely new items -- not reused/reskinned existing ones -- proving out
the full "add a new item with new art" pipeline documented in
`docs/adding-a-new-item.md`. **Confirmed working end-to-end in-game**, including all
three custom icons. (Mod id/folder stayed `great-healing-potion` from when this
started as a single item; it now covers three.)

- **Great Healing** -- `Inventory/Inventory Additions/Miscellaneous/Potions/Great
  Healing`. 15-18 + 8% max HP normal, 30-36 + 16% max HP with Vampiric Fury. Value 60,
  rarity Rare. Gold/amber recolored icon.
- **Superior Healing** -- 22-26 + 11% max HP normal, 44-52 + 22% max HP with Vampiric
  Fury. Value 150, rarity Very Rare. Blue/silver recolored icon.
- **Supreme Healing** -- 32-38 + 15% max HP normal, 64-76 + 30% max HP with Vampiric
  Fury. Value 350, rarity Unique. Violet recolored icon.

All three are cloned from the game's own `Extra Healing` potion (same use-effect
structure: overheal check, Vampiric Fury branch, heal, remove critical hits, spell
effect + sound) with scaled-up numbers and higher value/rarity per tier.

**Icon art**: each is a distinct-hue recolor of the real `Extra Healing` flask icon,
built with `mdl16_format.recolor_icon_in_place()` -- decodes the real icon's
compressed sprite data (reverse-engineered this session, see
`docs/mdl16-icon-format.md`), transforms only the stored color values, and leaves the
original RLE opcode structure byte-identical. Confirmed rendering correctly in-game.
Building a wholly new icon *shape* from scratch remains unsolved (see that doc) --
these only needed a recolor, which is why they work.

## Requires

`test-pocket` must also be installed and enabled, and must load **before** this mod
(earlier in `mods enabled.json`'s order) -- this mod ships its own copy of `Test
Pocket.zax` with extra chest-reward actions added, so it needs to load after and win
the "last mod wins" conflict, or the necklace gets three bonus potions for free.

## Testing

1. `python modmanager.py enable <game-dir> great-healing-potion` (after test-pocket is
   already enabled)
2. `python modmanager.py build <game-dir>` -- `build` also syncs the game's loose file
   mirror automatically (see `SKILL.md`'s "Loose file mirror" section); no separate
   manual step needed.
3. In-game: talk to Quinn the Herbalist in the Gate District, take the new dialogue
   option to the Test Pocket, and open Lucia's chest. You get the Necklace plus all
   three potions, with the chest's opening animation playing normally.
4. Check each potion's icon in your inventory -- gold, blue, and violet flasks,
   distinct from the original brown Extra Healing icon and from each other.

## What this mod's development surfaced (see `SKILL.md` / `docs/mdl16-icon-format.md`)

Getting the first item fully working, especially the custom icon, took far longer than
the mechanic itself and surfaced two real, previously-undocumented bugs in the
project's own tooling (both now fixed):

1. **The loose file mirror bug**: `<game-dir>\data\` is a complete loose copy of
   `data.dat`'s contents that the game reads in *preference* to `data.dat` whenever a
   loose file is present. `modmanager.py build` never touched it, silently making
   rebuilds invisible to the running game. Now fixed -- `build` auto-syncs it.
2. **The `.mdl16` icon RLE codec**: fully decoded, and a production-ready in-place
   recolor path is proven (used for all three potions here). Building a brand-new
   icon's RLE stream from scratch is still unsolved -- every attempt round-tripped
   correctly through this project's own decoder but rendered corrupted in-game,
   because the real encoder follows some run-selection heuristic (which opcode to
   pick, how long to make each run) that wasn't reverse engineered. Recoloring an
   existing icon sidesteps needing to know it.

Extending from one potion to three was comparatively trivial once the pipeline was
proven -- clone the `.InventoryAddition`, recolor the icon with a different hue, add
one more chest give-action. The only new bug hit along the way was self-inflicted:
an early debugging simplification had stripped the chest's opening animation
(`CPlayAnimationAction`) down to bare item-give actions to isolate an unrelated issue,
and it was never restored until the chest stopped visibly opening -- restored as a
plain first action in the sequence.
