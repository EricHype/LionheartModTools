"""Register the two weapon meshes the male player composite is missing.

`WarHammerBetter` and `ScimitarSpecial` are listed in `Human Female.mdl16` and absent from
`Human Male.mdl16` (90 model entries against 88). No art is missing: both GR2s ship, both
load, and both composites point at the *same* shared files under `Models3D/Weapons/`, so
there is no such thing as a male or female war hammer. The gap is two entries in a registry.

The composite .mdl16 is a flat sequential stream with no offset table, no size field and no
checksum:

    f32 f32 u32=1  str"Plugin"  str"CXRPGCharacterCompositeGrannyPluginForCModel"
    f32 f32 u32=0  u32 count  count * str      <- the model registry
    u32 14  14 * (str slot-type, str default)  <- per-slot default model name
    ... attachments, animations, sounds

where `str` is a 4-byte little-endian length followed by the raw bytes, no terminator.
Nothing downstream stores a byte offset, so inserting an entry is: splice in the string and
bump the count. The two files disagree about the order of CrossBow Better and CrossBow
Special, which is how we know the registry's order is not load-bearing.

Each new entry goes where its sibling sits in the female file: the variants come before the
base weapon within a folder group.
"""
import json
import struct
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
VANILLA = Path(r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader\data.dat.vanilla.bak")
REL = "Cache/Models/Characters/Player Characters/Human Male.mdl16"
FEMALE = "Cache/Models/Characters/Player Characters/Human Female.mdl16"

# the entry to add, and the entry it must be inserted in front of
ADDITIONS = [
    ("Models3D/Weapons/Misc_NonSwords/WarHammer Better/WarHammerBetter",
     "Models3D/Weapons/Misc_NonSwords/WarHammer/WarHammer"),
    ("Models3D/Weapons/Swords/Scimitar Special/ScimitarSpecial",
     "Models3D/Weapons/Swords/Scimitar/Scimitar"),
]


def registry(data):
    """(count_offset, count, [(string_offset, path)], end_offset)"""
    first = data.find(b"Models3D/")
    count_at = first - 8
    count = struct.unpack_from("<I", data, count_at)[0]
    p, out = first - 4, []
    for _ in range(count):
        ln = struct.unpack_from("<I", data, p)[0]
        out.append((p, data[p + 4:p + 4 + ln].decode("latin-1")))
        p += 4 + ln
    return count_at, count, out, p


def entry(path):
    return struct.pack("<I", len(path)) + path.encode("latin-1")


zf = zipfile.ZipFile(VANILLA)
orig = zf.read(REL)
count_at, count, before, end = registry(orig)
paths_before = [s for _, s in before]

# -- every addition must already be registered in the female file, and not in the male one
_, fcount, fem, _ = registry(zf.read(FEMALE))
fem_paths = [s for _, s in fem]
for path, anchor in ADDITIONS:
    assert path in fem_paths, "not registered for the female composite either: " + path
    assert path not in paths_before, "already registered: " + path
    assert paths_before.count(anchor) == 1, "cannot pin the insert point: " + anchor

# -- splice back to front so the earlier offsets stay valid
out = orig
for path, anchor in sorted(ADDITIONS, key=lambda a: paths_before.index(a[1]), reverse=True):
    at = next(off for off, s in before if s == anchor)
    out = out[:at] + entry(path) + out[at:]
out = out[:count_at] + struct.pack("<I", count + len(ADDITIONS)) + out[count_at + 4:]

# -- verify: the registry grew by exactly the additions, in order, and nothing else moved
new_at, new_count, after, new_end = registry(out)
paths_after = [s for _, s in after]
assert new_at == count_at and new_count == count + len(ADDITIONS) == len(paths_after)
for path, _ in ADDITIONS:
    assert paths_after.count(path) == 1, path
assert [s for s in paths_after if s not in [p for p, _ in ADDITIONS]] == paths_before
assert out[:count_at] == orig[:count_at], "header changed"
assert out[new_end:] == orig[end:], "the bytes after the registry moved"
assert len(out) - len(orig) == sum(4 + len(p) for p, _ in ADDITIONS)
# -- and the slot-type block right behind it still reads as 14 pairs
p = new_end
assert struct.unpack_from("<I", out, p)[0] == 14
p += 4
for _ in range(14):
    for _ in range(2):
        ln = struct.unpack_from("<I", out, p)[0]
        assert ln <= 200
        p += 4 + ln

dest = HERE / "files" / REL
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_bytes(out)

manifest = json.loads((HERE / "mod.json").read_text(encoding="utf-8"))
manifest["files"] = [REL]
(HERE / "mod.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

print("%s: %d -> %d entries, %d -> %d bytes" % (REL.split("/")[-1], count, new_count, len(orig), len(out)))
for path, anchor in ADDITIONS:
    print("   + %s  (at index %d, in front of %s)" % (path, paths_after.index(path), anchor.split("/")[-1]))
