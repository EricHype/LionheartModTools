# Bloodletter

Adds a unique scimitar, **Bloodletter**, found in Lucia's chest during the Test Pocket
quest (the same chest `great-healing-potion` already extends for the healing potions).

## Mechanics

- **Bleed on hit**: `CPlugInBehaviorStrikeAction` checks for a successful hit
  (`CExpressionHitMargin > 0`), then has a 30% chance (`CIfExpression` wrapping a
  `CRandom` roll, same pattern as the vanilla `Vampirism` weapon addition) to apply a
  `CXRPGDamageOverTime` effect: 2-4 Slashing damage every 5 seconds for 15 seconds
  (3 ticks). Base weapon damage/hit-or-miss is completely untouched -- this addition
  only adds the strike behavior, it doesn't replace anything on the base `Scimitar`.
- **Base item**: `Inventory Items/Scimitar`, chosen over Long/Short Sword because it's
  the vanilla weapon type that actually matches a curved blade.

## The icon: from-scratch art attempted, fell back to a recolor

The original plan was genuinely new art (`new_artwork/sword_render_03.png`, a real
render, not a recolor of an existing icon's shape) via `mdl16_format.encode_icon_rle16`.
This surfaced a real, previously-undocumented requirement -- every real `.mdl16` file
carries an on-disk per-row table that our encoder never generated, whose *absence*
crashes the game on opening inventory and whose *exact content* (never fully reverse
engineered, despite extensive investigation -- see `docs/mdl16-icon-format.md`'s "The
on-disk per-row table" section) governs whether the icon renders correctly. After
confirming the crash fix, testing many table-construction hypotheses against all 264
real icons in the game, and tracing real (if incomplete) rendering code via Ghidra, the
from-scratch icon still never rendered correctly in-game.

**Shipped icon**: a crimson `recolor_icon_in_place()` recolor of the vanilla
`Scimitar.mdl16` icon instead -- the same proven-safe technique used for every other
item in this project. The weapon and its mechanic are completely unaffected by any of
this; only the art fell back to the safe path. `new_artwork/sword_render_03.png` and the
full investigation remain available for a future attempt.

## Requires

`great-healing-potion` must also be installed and enabled, and must load **before**
this mod (this mod ships its own copy of `Test Pocket.zax` with one more chest item
added, so it needs to load after and win the "last mod wins" conflict).

## Testing

1. `python modmanager.py install mods/bloodletter-scimitar <game-dir>` (after
   `great-healing-potion` is already installed/enabled)
2. `python modmanager.py build <game-dir>`
3. In-game: reach the Test Pocket, open Lucia's chest.
4. Check: the chest gives a scimitar named "Bloodletter" alongside the necklace and
   three potions, with a crimson-recolored curved-blade icon. Equip it and land a few
   hits on an enemy to confirm the bleed effect procs (watch for repeated small damage
   ticks after a hit).
