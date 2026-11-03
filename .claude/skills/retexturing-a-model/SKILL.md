---
name: retexturing-a-model
description: Give a Lionheart 3D model new art by repainting its texture - decode the skin out of the .gr2, recolour it, re-encode through granny2.dll, splice it back, register the new model and point items at it. Use when a model needs art it does not have, when an item tier is sharing a skin with another tier, or when authoring a variant of an existing weapon, shield or armour model.
---

# Retexturing a model

Verified end-to-end by authoring **`BucklerBetter`** for Lionheart Fixt 0.91.0: the buckler
shipped with two skins for three item grades, so the middle one was painted. Read
`.claude/skills/lionheart-modding/SKILL.md` first for resource-text grammar and
`modmanager.py`; this skill is only the art path.

**Reach for this when art does not exist.** If the art *does* exist and is merely unwired,
that is a one-field change and this whole workflow is the wrong tool -- check
`tools/orphan_art.py` in Lionheart Fixt first, which finds assets the game ships and nothing
references.

## The one thing that blocks everything

**The encode needs a 32-bit interpreter.** `granny2.dll` is 32-bit; a 64-bit python raises
`OSError: [WinError 193] %1 is not a valid Win32 application` on load. On this machine:

    %LOCALAPPDATA%\Programs\Python\Python312-32\python.exe

`binktc0_encode` refuses to import under a 64-bit build and says so. Everything *else* in the
pipeline -- decode, recolour, splice, registry, wiring -- runs fine under 64-bit, so the usual
shape is a one-line shell-out to the 32-bit interpreter for the encode step alone.

## The pipeline

### 1. Get the existing skin out

Textures live inside the `.gr2` in BinkTC0 (`Encoding=3`). The path is
`Textures > Images > MIPLevels > Pixels`, and the item types differ at each level, so read it
rather than guessing:

```python
import gr2_format as gf, gr2_to_gltf
from binktc0_decode import decode_binktc0

def one(e, n):                      # an array_of_references item is a list of Elements;
    return gr2_to_gltf.field(       # a plain reference is a single Element
        e if isinstance(e, list) else [e], n)

g   = gf.GrannyFile.load_from_bytes(raw)
tex = one(g.root_elements, "Textures").value[0]
w, h = one(tex, "Width").value, one(tex, "Height").value
px  = one(one(one(tex, "Images").value, "MIPLevels").value, "Pixels")
payload = bytes(e.value for e in px.value)    # items are Elements named UInt8
rgb = decode_binktc0(w, h, payload, has_alpha=False)
```

`gf.dump_elements`, or a small recursive printer, is worth running once on a new model rather
than assuming this shape holds.

### 2. Recolour it

**Measure the tiers you are sitting between before choosing numbers.** Lionheart's item grades
differ in *brightness* as well as hue, so a transform that preserves luminance and moves only
chroma produces something that reads as charcoal. The buckler's shipped skins:

| | mean L | mean RGB |
|---|---|---|
| base | 50.7 | (63, 47, 37) weathered copper |
| Special | 82.6 | (104, 80, 39) gold |

Pick a **target mean luminance** and solve for the gain, rather than guessing a multiplier.
Keep a little of the original chroma (~25%) so the metal does not read flat, and leave
near-black pixels exactly as they are -- that is the backdrop, and touching it shows up as a
halo.

A desaturated metal needs **more** luminance than a saturated one to read as metal at all, so
a steel tier legitimately comes out optically brighter than a gold tier. The tier signal is
the material, not the brightness.

### 3. Encode

```
python312-32 binktc0_encode.py new_skin.png payload.binktc0
```

or `from binktc0_encode import encode_binktc0` under a 32-bit interpreter. It takes RGB and
widens to RGBA internally, which matters: **three-byte input encodes to nearly twice the size
and decodes at the wrong hue**, even though the texture's own `Layout` says `BytesPerPixel=3`.

Expect the payload to grow -- you are recompressing an image that has been through the codec
once. 13,304 -> 16,580 bytes on a 256x256 is normal.

### 4. Splice it into a copy of the .gr2

`Pixels` is a reference-to-array: a `u32` count at the element's own offset, and a pointer
immediately after it recorded in that sector's fixup table at `offset + 4`.

In every model seen so far the pixels **own their sector outright** -- it holds nothing else
and no other pointers -- so the new payload can simply replace that sector's contents and only
the count needs changing, with `dst_offset` staying 0. **Assert that rather than assuming it**,
because the alternative is silently clobbering a neighbour:

```python
ptr = sectors[px.data_sector_id].pointer_table[px.offset + 4]
assert ptr.dst_offset == 0 and len(data[ptr.dst_sector]) == old_n, "sector is not exclusively ours"
data[ptr.dst_sector] = bytearray(payload)
struct.pack_into("<I", data[px.data_sector_id], px.offset, len(payload))
repack(raw, header, file_info, infos, sectors, data,
       {ptr.dst_sector, px.data_sector_id}, out_path)
```

If an assertion ever fails, fall back to appending the payload to the sector and updating the
fixup's `dst_offset` instead -- `Pointer` is a frozen dataclass, so replace the table entry
rather than mutating it.

Give the new model its own `MODEL.TXT`, cloned from the source's with a distinct `Short Name`.

### 5. Register the model, or it renders nothing

A mesh name absent from a character composite's registry renders **nothing** -- no fallback, no
error, an empty hand. Both player composites are flat binary streams: a `u32` count, then that
many `u32`-length-prefixed path strings, no offset table, no size field, no checksum. Splice
the string in and bump the count. Lionheart Fixt's `tools/build_buckler_better.py` has the
function; `Human Male.mdl16` *and* `Human Female.mdl16` both need it.

### 6. Point items at it

Items and additions name a model by its **file leaf**, case-insensitively, in
`Override Names`. Beware: `Character Slot Types/<Slot>=` occurs **twice** in these blocks, once
under `Override Models` which the engine ignores and once under `Override Names` which renders.
Pin the one you want by splitting on the block header, never by taking the first match.

### 7. Inventory icons are a different format

If the model gains a tier, its icon usually should too, or the item looks right in the hand and
wrong in the pack. Icons are `.mdl16`, not `.gr2`, and
`mdl16_format.recolor_icon_in_place(data, transform)` rewrites only the stored colour values,
leaving every control byte, run length and the file length byte-identical. That is the right
tool when the silhouette is already correct. The transform works in **RGB565**. Run
`verify_icon()` afterwards; a malformed icon crashes the game on opening the inventory.

## Verifying

**Round-trip, do not inspect.** Decode the payload you just produced and compare it to the
source pixels. A correct encode returns mean absolute error around **0.07** and a maximum of
about **2** out of 255. A large error or a hue shift means RGB went in where RGBA was wanted.

Then decode the texture back out of the **written .gr2**, and again out of the built
`data.dat`, and compare to the source art each time. Those are three different failure
surfaces -- the encode, the splice, and the packaging -- and each one has its own way of going
wrong quietly.

Reload the written file and check the `granny_file_info` counts against the original's:
textures, materials, skeletons, vertex datas, topologies, meshes, models. **A file that loads
with no skeleton is worse than no file.** Ignore `crc32`; the repacker writes 0 and
`GrannyFileCRCIsValid` returns false for the shipped meshes too.

## Dead ends, so they are not re-walked

**The hue remapper is not a shortcut.** `Hue Remap Textures=1` plus a `CHueRemapHolder` does
nothing for a monster or an item. All 54 models that set the flag are player characters.

**`GrannyWriteDataTreeToFile` does not work from here.** It would let the library re-serialise
the whole tree and skip the splice. All 48 combinations of argument order and of the two
readings of `GrannyFileInfoType` fault, and they fault writing the file back *unmodified*, so
the signature is wrong rather than the data. `@16` rules out the five-argument form in later
SDKs.

**There is no hand-written encoder and there does not need to be.** `granny2.dll` does it.
