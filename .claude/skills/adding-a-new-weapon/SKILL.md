---
name: adding-a-new-weapon
description: End-to-end recipe for adding a new weapon to Lionheart, Legacy of the Crusader - the InventoryAddition that carries the mechanic, on-hit/proc/damage-over-time patterns, custom .mdl16 icon art, getting it into a chest or drop table, and deploying. Use when creating a magic/unique weapon, adding an on-hit effect, or authoring inventory icon art.
---

# Adding a new weapon

Verified end-to-end by building **Bloodletter** (`mods/bloodletter-scimitar/`): a scimitar
with a 30% chance-on-hit bleed, with genuinely new icon art. Read
`.claude/skills/lionheart-modding/SKILL.md` first for the resource-text grammar, the
`If=` vs `Custom Requirement=` gotcha, and `modmanager.py` mechanics — this skill assumes
those and only covers what is weapon-specific.

## Structure: base item + addition

A weapon is a vanilla **`Inventory Items/<Type>.InventoryItem`** (Scimitar, ShortSword,
LongSword, ...) plus your own **`Inventory/Inventory Additions/Weapons/<Name>.InventoryAddition`**,
which carries the name, description, value, icon, and the actual mechanic. Do not author
a new `.InventoryItem` — pick the vanilla base whose weapon class and animations match.

### The name is auto-composed as `"<Base Item> of <Display Name>"`

This cannot be turned off. `Display Name=Bloodletter` renders as *"Scimitar of
Bloodletter"*, which is wrong-sounding. Vanilla works around it by wording the field as a
suffix: `Goblin Slayer.InventoryAddition` actually has `Display Name=Goblin Slaying`.
**Use a gerund or noun phrase that reads after "of"** — `Bloodletting`, not `Bloodletter`.

## On-hit mechanics

Behavior hangs off `PlugIn Behaviors=Array{PlugIn Behavior=CPlugInBehaviorStrikeAction{...}}`.
Gate on a successful hit with `CExpressionHitMargin > 0` — base damage and hit/miss are
untouched, the addition only *adds* a strike behavior:

```
PlugIn Behavior=CPlugInBehaviorStrikeAction
{
    Strike Action=CIfExpressionAction
    {
        If Expression=CIsGreaterThan
        {
            Operand1=CExpressionHitMargin {}
            Operator=
            Operand2=CConstant { Constant Value=0 }
        }
        Character to get attributes from=$Instigator
        Then=CActionDoDamage
        {
            Character Doing Damage=$instigator
            Character To Damage=$trigger
            Defend Against=0
            No Friendly Fire Check=0
            Damages=Array { Item Count=1, Damage=<a damage class, see below> }
        }
        Else=
    }
}
```

### Damage over time

`CXRPGDamageOverTime{Damage Type=Damage Types/Slashing, Damage Amount=<expr>, Duration=<expr>}`.
The tick interval is **fixed at 5 seconds** and is not a field — `Damage Amount` is
per-tick and `Duration` is total seconds, so `Duration=15` means three ticks. Established
from the vanilla `Poison Touch` / `Poison` arrow additions.

### Chance-based procs

Do **not** reach for a nested `CIfAction` — that runs into the `If=` grammar gotcha. Put
the roll directly inside the numeric field, the way vanilla `Vampirism.InventoryAddition`
does:

```
Damage Amount=CIfExpression
{
    Condition=CIsLessThan
    {
        Operand1=CRandom { Operator=, Minimum=CConstant{Constant Value=0}, Maximum=CConstant{Constant Value=100} }
        Operator=
        Operand2=<named expression "chancetobleed">
    }
    OperatorPart1=
    Value if True =<named expression, or CRandom between two of them>
    OperatorPart2=
    Value if False=CConstant { Constant Value=0 }
}
```

Note the authentic irregular spacing: **`Value if True =`** has a space before `=`,
`Value if False=` does not. Preserve it exactly.

### Named expressions, and interpolating them into the description

Declare tunable numbers once and reference them from both the mechanic and the text:

```
Expressions=CNamedExpressionsArray
{
    Expressions=Array
    {
        Array Count=3
        Array Item=CNamedExpressionsArrayItem { Name=min,  Expression=CConstant{Constant Value=2} }
        Array Item=CNamedExpressionsArrayItem { Name=max,  Expression=CConstant{Constant Value=4} }
        Array Item=CNamedExpressionsArrayItem { Name=chancetobleed, Expression=CConstant{Constant Value=30} }
    }
}
```

Reference one from a field with a self-path back to your own addition:

```
CInventoryAdditionsNamedExpressionExpression
{
    InventoryAddition=Inventory/Inventory Additions/Weapons/<Name>
    Expression Name=chancetobleed
    Value=0
}
```

`Array Count=` here, not `Item Count=` as elsewhere. In `Description=`, `%i[name]%`
interpolates the value, so the tooltip stays correct when you retune a constant.

### Other required fields

`Value` / `Minimum Value` / `Inbetween Value` / `Maximum Value` (gold), `Is Magic=1`,
`Encumbrance`, `Item Grade=Special`, `Addition Rarity=Inventory Item Rarity/3 Rare`,
`Inventory Addition Group=Inventory/Inventory Addition Groups/Weapons/Melee Weapon Additions`,
`On the ground=!Unknown Model`, and `Inventory Window=Items/Inventory Images/Weapons/<Name>`
(Resources-relative, no `Cache/Models/` prefix, no extension). Clone a real weapon
addition and edit it rather than assembling this list by hand.

## The icon

See `docs/mdl16-icon-format.md` for the format. Two paths, both proven in-game:

**Recolor an existing icon** — cheapest, when the silhouette is already right:

```python
import mdl16_format as m
new_bytes = m.recolor_icon_in_place(source_bytes, color_transform)  # length-preserving
```

**Author new art** — from a render or any RGBA pixel source:

```python
import mdl16_format as m
donor = z.read(".../Quest Items/-Deed Silver Mine.mdl16")   # a vanilla buffer-1-only icon
out   = m.build_icon_file(donor, w, h, rows, hotspot_x=w//2, hotspot_y=h//2)
m.verify_icon(out)     # ALWAYS run this before deploying
```

`rows` is `height` lists of `width` `(r,g,b,a)` tuples; `a < 128` means transparent.
Colors quantize to RGB565 (≤8/255 error). Crop tight to the art's bounding box with a
couple of pixels of margin — the equipment slot aspect-fits the icon, so a mostly-empty
canvas just renders smaller. Vanilla weapon icons run roughly 30-85 px wide by ~120 tall.

`verify_icon()` re-parses with the engine's own algorithm and catches every failure mode
seen so far. Do not skip it: a malformed icon crashes the game on opening inventory, and
an in-game test cycle is slow.

## Getting the weapon to the player

Add a `CActionGiveStandardInventoryItem` to an existing chest's action array in the
level's `.zax` (remember to bump the enclosing `Item Count=`):

```
Action=CActionGiveStandardInventoryItem
{
    Who to give to=$instigator
    Delete Trigger=0
    Notify Player=1
    Inventory Item To Give=Inventory Items/Scimitar
    Additions to add=Array
    {
        Item Count=1
        Addition to add=Inventory/Inventory Additions/Weapons/<Name>
    }
}
```

Alternatives (see `docs/adding-a-new-item.md`): add it to an existing drop-can's
`Items=Array{...}`, or to a merchant's `CMerchantAI{Items=Array{...}}`. Editing an
existing chest/drop/shop avoids the save-staleness gotcha entirely, since nothing new is
placed in the `.zax`.

## Deploy and verify

```
python modmanager.py install mods/<id> "<game-dir>"     # MUST rerun after every source edit
python modmanager.py build "<game-dir>"
```

`build` reads from `<game-dir>\mods\installed\<id>\`, not your source tree — skipping
`install` silently builds stale content. Then confirm both the loose mirror and the
archive actually got the bytes (the loose `data\` tree shadows `data.dat`):

```python
src = open("mods/<id>/files/<rel>", "rb").read()
assert open(game + "/data/" + rel, "rb").read() == src
with zipfile.ZipFile(game + "/data.dat") as z: assert z.read(rel) == src
```

Full rebuilds take several minutes — run them in the background and verify structurally
before asking for an in-game test.

## In-game checks

1. The chest gives an item named `<Base> of <Display Name>`.
2. The icon renders whole and centered, in both the chest list and the equipment slot.
3. Equip it and land several hits — a percentage proc needs a few swings before it fires.
   For damage-over-time, watch for repeated small damage numbers after the initial hit.
