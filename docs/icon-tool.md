# Changing an item's art

`icontool.py` lets you repaint any item's picture in the paint program you already use. You
export the picture as a PNG, paint over it, and import it back into your mod. The tool does
the file-format work and the checking.

You need Python 3 and the game installed with a mod set up (see the README). You don't need
to know anything about the file formats.

## The three steps

**1. Find the item.** Use the name the game shows, or a word from it:

```
python icontool.py find mancha
```
```
Gauntlets of La Mancha                     item            Inventory/Specific Item Cans/Unique Magic Items/Gauntlets of La Mancha
```

**2. Export its art.**

```
python icontool.py export "Gauntlets of La Mancha" --mod <your mod folder>
```

This writes one PNG for each picture the item has, into the current folder (`--out <folder>`
puts them somewhere else):

- `Gauntlets of La Mancha - inventory.png`: the picture in the inventory and the shop.
- `Gauntlets of La Mancha - ground.png`: the small sprite you see when it lies on the floor.

The PNGs are **four times the real size**, so there's room to paint. Each pixel of the real
picture is a 4x4 block. Transparent areas are really transparent.

Use `--mod` when you're working on a mod, Lionheart Fixt included. It exports the mod's
version of the item, which may already differ from the original game.

**3. Paint it, then import it.** Paint in any program that saves PNG: Paint, paint.net,
GIMP, Krita, Photoshop. Then:

```
python icontool.py import "Gauntlets of La Mancha" "Gauntlets of La Mancha - inventory.png" --mod <your mod folder>
```

The import tells you what it did and writes a preview next to your PNG, named
`... (as the game will show it).png`. **Look at the preview.** It is decoded from the file
the game will load, so it shows what you'll see in the game.

Add `--dry-run` to check everything without writing anything.

For the ground sprite, add `--art ground`. Some base items, like the plain Short Sword,
have a different inventory picture for each quality grade. For those, use `--art Basic`,
`--art Better` or `--art Special`; the export names each grade's PNG.

## Rules for painting

**Keep the canvas size.** Don't crop or resize the PNG. The tool divides it by 4 to get
the real size, and refuses a size that doesn't divide evenly.

**Inventory pictures can change shape.** You can paint outside the old outline or erase
parts of it.

- **Same outline:** if you only repainted colours inside the outline, the original file is
  recoloured and keeps its soft, half-transparent edges.
- **New outline:** if you changed the outline, or painted fine detail the original file
  can't store, the picture is rebuilt from your painting. New pictures can't have
  half-transparent pixels, so each one becomes either solid or clear. The import says how
  many. This is how Lionheart Fixt's hand-painted La Mancha gauntlets were made, and they
  look right in the game.

**Ground sprites can't change shape.** A ground sprite rebuilt from scratch draws fine and
**can't be picked up**: the cursor spins instead of offering the pickup. So ground art is
always repainted inside its original outline. Paint outside the outline is ignored, and the
import says how many pixels that was. The format stores runs of pixels as one colour, so
very fine detail on a ground sprite gets blended; the import says how many pixels were
affected.

**Transparency:** paint transparent areas as transparent (no background layer), not black.
The tool reads ordinary 8-bit PNGs. If your program saves 16-bit PNGs, the tool says so and
asks you to save an 8-bit copy.

## What the tool takes care of

- **Shared pictures.** Many items show the same picture: the plain gauntlets, five magic
  gauntlet types and the Gauntlets of La Mancha all use one file. If you repainted that file,
  all seven would change. So when other items, creatures or maps use the picture, the
  import makes a copy for your item alone and points your item at the copy. Only the
  picture lines in the item change.
- **The original game is never changed.** Everything is written into the mod you name with
  `--mod`, and listed in its `mod.json`.
- **Checking.** Every picture is checked the way the game reads it before anything is
  written. A broken inventory picture would crash the game when the inventory opens.
- **Scripts that make art.** Lionheart Fixt builds some pictures with scripts in its `tools/`
  folder. If one of those writes the file you just replaced, the import names it.
  Rerunning that script would put the old art back, so tell whoever maintains it.

## Seeing it in the game

Install the mod the usual way, then look at the item in the inventory and on the ground:

```
python modmanager.py install <your mod folder> "<game folder>"
```

For Lionheart Fixt, use Fixt's own build.

**Not tested in the game yet:** when a ground sprite is shared, the import gives your item
its own copy under a new file name. Recolouring a ground sprite is proven in the game;
giving it a new file name hasn't been tried yet. The import says so when it happens. Drop
the item and check that you can pick it back up.

## For maintainers

- The tool is `icontool.py`; the format work is in `mdl16_format.py`.
- `decode_icon_composite` reads both colour planes and the alpha plane. The alpha layout
  is measured: one byte per pixel that a plane-5 run covers.
- `recolor_icon_in_place` now passes the plane and the run length to callbacks that ask
  for them. One- and two-argument callbacks behave as before.
- `tests/test_icontool.py` includes the gate: export then import without painting is
  byte-identical for all 428 item sprites in the game.
