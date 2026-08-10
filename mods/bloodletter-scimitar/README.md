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

## The icon: the first genuinely new art in this project

The icon is built from `new_artwork/sword_render_03.png` -- a real render, not a recolor
of an existing icon's silhouette. It is a 23x112 tight crop of the blade, encoded with
`mdl16_format.encode_icon_rle16()` and wrapped by `build_icon_file()` using
`Deed Silver Mine.mdl16` (a vanilla buffer-1-only icon) as the object-graph envelope.

From-scratch art was blocked for a long time by the `.mdl16` per-row offset table. That
is now solved -- the table is a literal byte index and rows are opcode-aligned; see
`docs/mdl16-icon-format.md`, "The per-row offset table". Regenerating this icon:

```
python -c "import mdl16_format as M; ..."   # see that doc's "Building a new icon" section
```

Always run `mdl16_format.verify_icon()` on the result before deploying -- it re-parses
the file with the engine's own algorithm and catches every failure mode found so far.

## Requires

`great-healing-potion` must also be installed and enabled, and must load **before**
this mod (this mod ships its own copy of `Test Pocket.zax` with one more chest item
added, so it needs to load after and win the "last mod wins" conflict).

## Testing

1. `python modmanager.py install mods/bloodletter-scimitar <game-dir>` (after
   `great-healing-potion` is already installed/enabled)
2. `python modmanager.py build <game-dir>`
3. In-game: reach the Test Pocket, open Lucia's chest.
4. Check: the chest gives a scimitar displayed as "Scimitar of Bloodletting" alongside
   the necklace and three potions, showing the custom blade icon (whole sword, centered,
   not clipped). Equip it and land a few hits on an enemy to confirm the bleed effect
   procs (watch for repeated small damage ticks after a hit).
