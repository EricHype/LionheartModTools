# Adding a new item to the game

Same split as characters (see `docs/adding-a-new-character.md`): a **game-logic
identity** (name, stats, on-use effect, value) and a **visual appearance** (world
pickup model, inventory icon). This doc covers items specifically. The `.mdl16`
2D-sprite icon codec is fully decoded (see `mdl16_format.py`) — reusing an existing icon
(Recipe A) is the zero-effort path, recoloring one is Recipe B, and authoring genuinely
new icon art is Recipe C. All three are proven in-game.

## The two resource types, and which one you want

- **`Inventory Items/*.InventoryItem`** — a *base* item type (`Potion`, `ShortSword`,
  `Scroll`, `Amulet`, ...). ~100 of these ship with the game. Defines stacking
  behavior, encumbrance, slot type, and (for equippables) 3D display model. On its own
  a base type usually has **no interesting effect** — `Potion.InventoryItem` just
  stacks and can be picked up/dropped.
- **`Inventory/Inventory Additions/**/*.InventoryAddition`** — a *magic modifier*
  layered onto a base item. This is where the actual behavior lives. Confirmed by
  reading `Healing.InventoryAddition` (the real "potion of healing" item) end to end:
  - `PlugIn Behaviors=Array{PlugIn Behavior=CPlugInBehaviorUseAction{Use Action=...}}`
    — a full action-tree, using the **same expression/action classes already
    documented in `SKILL.md`** for quest scripting (`CIfExpressionAction`,
    `CGiveHealthToCharacterAction`, `CSpawnEffectAction`, `CPlaySoundAction`, etc.). No
    new system to learn — if you've written a quest condition/action in this project
    before, you can write an item's on-use effect.
  - `Display Name=Healing`, `Description=When consumed, this potent draft instantly
    heals wounds.`, `Value=CConstant{Constant Value=10}`, `Is Magic=1`.
  - `Inventory Window=Items/Inventory Images/Misc Items/Potion Healing` — the
    **inventory icon**, referenced by path to an existing `Cache/Models/...frm16`
    entry. Point this at any existing icon path and you inherit a working icon with
    zero sprite/`.mdl16` work.
  - `On the ground=Items/PickUps/Misc Items/Potion_PU` — the **world pickup**
    appearance (what you see lying on the ground before picking it up), also a path
    reference to an existing `.mdl16` resource. Same reuse shortcut applies.

If your new item is a straightforward variant of something that already has the
mechanic you want (a new potion, a new scroll, a new magic bonus on a weapon), clone an
`.InventoryAddition`, not an `.InventoryItem` — it's the smaller, more self-contained
file and already has the full pattern (effect + name + description + icon + value) in
one place.

## Recipe A — new item, existing icon and world model (fully achievable, zero binary work)

1. Pick a real `.InventoryAddition` close to what you want (e.g. `Healing.
   InventoryAddition` for a consumable-with-effect, or a weapon-slot addition for a
   magic weapon bonus) and read it with `python resource_format.py <path>` or a direct
   `zipfile.read` — same pattern used throughout this project.
2. Clone it under a new name/path. Change `Display Name=`, `Description=`, `Value=`,
   and the actual effect logic inside `Use Action=` (swap the amounts/expressions, or
   replace the whole action tree with a different effect built from the same
   action/expression vocabulary).
3. **Leave `Inventory Window=` and `On the ground=` pointing at existing paths** (reuse
   `Potion Healing`'s icon, or any other existing icon under `Items/Inventory Images/
   ...` — list candidates with a zip listing of that folder). Zero binary work.
4. Wire it into the game so a player can actually get it — two proven paths, both just
   `.can` edits (same class of edit as `CGeneratorAI` spawner work for characters):
   - **As a monster/chest drop**: create a small "drop can" (`CCannedObject{Object=
     CInventoryItemGeneratorBasicItem{Item=Inventory Items/<base type>} }` or
     `CInventoryItemGeneratorAdditionalMagic{...Addition=<your new .InventoryAddition
     path>...}`, matching `Healing Potion.can`'s / `Potion Extra Healing.can`'s shape),
     then point an existing enemy's or chest's drop-table field at it (confirmed field
     names via `Wererat PRIME.can`: things like `...Drop Items Cans/Spirit Energy/5Huge
     Spirit Charge Drop Action`) — or simpler still, add your new item straight into an
     **existing** drop-can's `Items=Array{...}` list alongside what's already there, so
     no enemy template needs editing at all.
   - **As a merchant's shop stock**: `SKILL.md` already documents `CMerchantAI{Items=
     Array{...}}` for NPC vendors — add your item there directly.
5. Package/test exactly like any other mod: `modmanager.py install`/`build`, remember
   new-entity-in-a-`.zax` caveats don't apply here since you're editing an existing
   enemy's/merchant's drop or stock list, not placing a new world entity.

## Recipe B — new item, recolored icon (proven working, still zero shape-authoring)

Same as Recipe A, except step 3: instead of pointing `Inventory Window=` at an
*existing* icon path unchanged, point it at a **new** `Cache/Models/...` path whose
bytes you build with `mdl16_format.recolor_icon_in_place()`:

```python
import mdl16_format as m, zipfile, colorsys

with zipfile.ZipFile(r"<game-dir>\data.dat") as zf:
    source = zf.read("Cache/Models/Items/Inventory Images/Misc Items/Potion Extra Healing.mdl16")

def color_transform(rgb565):
    if rgb565 == 0:
        return 0  # leave transparency alone
    r, g, b = m._rgb565_to_rgb888(rgb565)
    h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
    nr, ng, nb = colorsys.hsv_to_rgb(target_hue, min(1, s*1.3+0.1), min(1, v*1.08))
    return m._rgb888_to_rgb565(round(nr*255), round(ng*255), round(nb*255))

new_icon_bytes = m.recolor_icon_in_place(source, color_transform)
# write new_icon_bytes to your mod's files/Cache/Models/Items/Inventory Images/Misc
# Items/<New Name>.mdl16, and point your .InventoryAddition's Inventory Window= at the
# matching Resources-relative path (no extension, no "Cache/Models/" prefix)
```

This produces a same-shape, different-palette icon (confirmed rendering correctly
in-game — a gold-recolored "Great Healing" variant of the "Extra Healing" flask). It
cannot change the icon's silhouette or dimensions — for that, see Recipe C.

## Recipe C — new item, genuinely new icon art

For a silhouette that doesn't exist in the game yet. Confirmed in-game with
`mods/bloodletter-scimitar/` (a sword icon built from a render).

```python
import mdl16_format as m, zipfile

with zipfile.ZipFile(r"<game-dir>\data.dat") as zf:
    donor = zf.read("Cache/Models/Items/Inventory Images/Quest Items/-Deed Silver Mine.mdl16")

# rows: `height` lists of `width` (r, g, b, a) tuples; a < 128 means transparent
out = m.build_icon_file(donor, width, height, rows, hotspot_x=width//2, hotspot_y=height//2)
m.verify_icon(out)      # ALWAYS, before deploying -- a malformed icon crashes the game
```

- Crop tight to the art's bounding box with a couple of pixels of margin. The slot
  aspect-fits the icon, so a mostly-empty canvas just renders smaller.
- Colors quantize to RGB565 (≤8/255 per-channel error).
- The donor supplies the serialized object-graph envelope, which this module doesn't
  synthesize. It must be a **buffer-1-only** vanilla icon (`Deed Silver Mine` or
  `Lava Troll Hide` — the only two); `build_icon_file` enforces this. Its embedded
  model-path string doesn't matter, the game resolves by filesystem path.

This was blocked for a long time by the format's per-row offset table. It is solved —
see `docs/mdl16-icon-format.md`, "The per-row offset table" — and `verify_icon()` checks
every failure mode that showed up along the way.

**A genuinely new world-pickup model** (`On the ground=`) is a different, 3D format and
is not covered by any of this; reuse an existing one, or recolor it the same way.

## Testing gotchas

Same as character work — see `docs/adding-a-new-character.md`'s "Testing gotchas"
section (save-compatibility for new `.zax` entities doesn't apply to this recipe since
nothing new is placed in a `.zax`; `data.dat` repack/compression rules still do).
