# Male Weapon Mesh Patch

**There are no missing male meshes.** Weapon models are not gendered: they hang off
`Weapon Attach Dummy` rather than being skinned to the body, and both player composites
point at the *same* shared files under `Models3D/Weapons/`. `WarHammerBetter` and
`ScimitarSpecial` both ship, both load through `gr2_format` with 1 mesh, 1 model and 1
skeleton apiece, and `WarHammerBetter` is the richest weapon mesh in that folder at 53 KB.

What is missing is two entries in a registry. `Human Male.mdl16` lists 88 models,
`Human Female.mdl16` lists 90, and the two it lacks are exactly those meshes. A name that is
not in a composite's registry has nothing to resolve against, so a male character cannot be
asked for either one.

## The format, and why the edit is small

The composite `.mdl16` is a flat sequential stream with no offset table, no size field and
no checksum:

    f32 f32 u32=1  str"Plugin"  str"CXRPGCharacterCompositeGrannyPluginForCModel"
    f32 f32 u32=0  u32 count  count * str      <- the model registry
    u32 14  14 * (str slot type, str default)  <- per-slot default model name
    ...                                        <- attachments, animations, sounds

`str` is a 4-byte little-endian length followed by raw bytes, no terminator. Nothing after
the registry addresses a byte offset, so adding a model is: splice in one length-prefixed
string, bump the count. This patch adds 128 bytes and changes one `u32`, taking the file
from 14751 to 14879 bytes.

The two files disagree about the order of `CrossBow Better` and `CrossBow Special`, which is
how we know the registry's order is not load-bearing. Each new entry still goes where its
sibling sits in the female file, in front of the base weapon of its folder group.

## Build it, do not hand-edit it

    python mods/male-weapon-mesh-patch/build.py

The script reads vanilla, splices back to front, and refuses to write unless all of it
holds: each addition is already registered for the female composite and not for the male
one; the insert anchor is unique; the registry grew by exactly the additions and every other
entry kept its order; the header is unchanged; the bytes after the registry are
byte-identical to vanilla's; and the slot-type block behind it still reads as 14 pairs.

## What it does not prove

That the engine resolves `Override Names` against this registry at all. The evidence is
circumstantial but consistent: the per-slot defaults stored in the same file (`plain`,
`Sylvant2`) are leaf names of registry entries, and the item overrides that work in vanilla
all name leaves too, case-insensitively (`Crossbow` for `CrossBow`, `Khan'ssword` for
`Khan'sSword`). The female composite proves a 90-entry registry loads and that these two
paths are acceptable entries.

**Apply this after the weapon-model probe, not with it.** The playtest kit's scimitar row
exists to show what a male character does when asked for a name its composite lacks: base
mesh, no weapon, or a crash. Install this first and that observation is gone.
