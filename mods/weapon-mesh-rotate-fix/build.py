"""Rotate two weapon meshes 180 degrees so their blades hang the right way up.

`ScimitarSpecial` and `BattleAxeBetter` both render with the blade facing up in the hand
instead of down -- confirmed in play, on a male character, with the plain base weapon held
beside each one as a control.

Why the geometry and not a transform: `scale_model.py` already established that the engine
does **not** apply `Model.InitialPlacement` (a scale patched there came back at 1x in game),
so rotating that quaternion would change nothing. Patching the vertices is also the
least-assumption fix -- it needs no bone math and no InverseWorldTransform recompute.

A 180-degree rotation negates two of the three components, so its determinant is +1 and
triangle winding is preserved. That is the whole reason to rotate rather than mirror: a
mirror (negating one component) would invert winding and render the mesh inside out, which
would then need every triangle index reversed as well.

Normals are rotated with the positions. Leaving them would light the blade as though it
still faced the old way.

The axis is Y, the weapon's own length. Every weapon mesh in the game shares one convention:
long axis Y, with the body extending in -Y from a handle near the origin. That holds for all
twelve families, and it holds for both of these meshes too -- so their length direction was
never wrong, and flipping about X or Z would break it. What is wrong is the roll about that
length, which is exactly a 180-degree rotation about Y: it keeps the blade pointing away from
the grip and turns its facing over.

There is a check for this beyond the reasoning. A scimitar's curve makes it strongly
asymmetric in X: the base Scimitar's tip sits at X+17.9 while ScimitarSpecial's sits at
X-29.3. A Y flip negates X, so if the rotation is right the fixed mesh's asymmetry should
come back on the same side as its base weapon's. The build asserts that.
"""
import json
import sys
import zipfile
from pathlib import Path

MODTOOLS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MODTOOLS))
import gr2_format as gf
from gr2_to_gltf import field, _group_repeated_records
from gltf_to_gr2 import repack, _patch_floats

HERE = Path(__file__).parent
VANILLA = Path(r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader\data.dat.vanilla.bak")
PRE = "Resources/Models3D/Weapons/"

AXIS = "Y"
MESHES = [
    "Swords/Scimitar Special/ScimitarSpecial",
    "Misc_NonSwords/Battle Axe Better/BattleAxeBetter",
]
KEEP = {"X": 0, "Y": 1, "Z": 2}[AXIS]
BASE_OF = {
    "ScimitarSpecial": "Swords/Scimitar/Scimitar",
    "BattleAxeBetter": "Misc_NonSwords/Battle Axe/BattleAxe",
}

zf = zipfile.ZipFile(VANILLA)


def flip(v):
    """180 degrees about AXIS: keep that component, negate the other two."""
    return tuple(c if i == KEEP else -c for i, c in enumerate(v))


def rotate(rel):
    raw = zf.read(PRE + rel + ".MODEL.GR2")
    header, offset = gf.parse_header(raw)
    endian = ">" if header.big_endian else "<"
    file_info, offset = gf.parse_file_info(raw, offset, endian)

    sector_infos, sectors = [], []
    for _ in range(file_info.sector_count):
        info, offset = gf.parse_sector_info(raw, offset, endian)
        sector_infos.append(info)
        sectors.append(gf.load_sector(raw, endian, info))

    root_elements, _ = gf.parse_element(
        sectors, file_info.root_ref.sector, file_info.type_ref.sector,
        file_info.root_ref.position, file_info.type_ref.position, header.bits_64, endian)

    sector_data = [bytearray(s.data) for s in sectors]
    touched = set()
    counts = {"Position": 0, "Normal": 0}
    before = {"lo": [1e9] * 3, "hi": [-1e9] * 3}
    after = {"lo": [1e9] * 3, "hi": [-1e9] * 3}

    for model_fields in field(root_elements, "Models").value:
        mb = field(model_fields, "MeshBindings")
        for rec in _group_repeated_records(mb.value, ["Mesh"]):
            mesh = field(rec, "Mesh").value
            vertices = field(field(mesh, "PrimaryVertexData").value, "Vertices").value
            for v in vertices:
                for key in ("Position", "Normal"):
                    e = field(v, key)
                    if e is None:
                        continue
                    old = tuple(x for _, x in e.value) if e.kind == "array" else tuple(e.value)
                    new = flip(old)
                    _patch_floats(sector_data, e, new)
                    touched.add(e.data_sector_id)
                    counts[key] += 1
                    if key == "Position":
                        for a in range(3):
                            before["lo"][a] = min(before["lo"][a], old[a])
                            before["hi"][a] = max(before["hi"][a], old[a])
                            after["lo"][a] = min(after["lo"][a], new[a])
                            after["hi"][a] = max(after["hi"][a], new[a])

    assert counts["Position"], "no vertices found in " + rel
    assert counts["Normal"] == counts["Position"], (
        "%s has %d positions but %d normals -- a partial rotation would light wrong"
        % (rel, counts["Position"], counts["Normal"]))

    dest = HERE / "files" / (PRE + rel + ".MODEL.GR2")
    dest.parent.mkdir(parents=True, exist_ok=True)
    repack(raw, header, file_info, sector_infos, sectors, sector_data, touched, str(dest))

    # -- the bounding box must come back as the same box with two axes negated
    for a in range(3):
        if a == KEEP:
            assert abs(after["lo"][a] - before["lo"][a]) < 1e-4, "kept axis moved"
        else:
            assert abs(after["lo"][a] + before["hi"][a]) < 1e-4, "negated axis is wrong"

    out = dest.read_bytes()
    gf.GrannyFile.load_from_bytes(out)  # it has to parse back
    return rel, len(raw), len(out), counts["Position"], before, after


def tip_x(rel):
    """Where the far tip sits on X, which is the side a curved blade sweeps toward."""
    g = gf.GrannyFile.load_from_bytes(zf.read(PRE + rel + ".MODEL.GR2"))
    pts = []
    for mesh in field(g.root_elements, "Meshes").value:
        for v in field(field(mesh, "PrimaryVertexData").value, "Vertices").value:
            pts.append([x for _, x in field(v, "Position").value])
    return min(pts, key=lambda p: p[1])[0]  # tip = most negative Y


print("180 degrees about %s -- keeping %s, negating the other two" % (AXIS, AXIS))
print()
written = []
for rel in MESHES:
    name, n_in, n_out, nverts, b, a = rotate(rel)
    written.append(PRE + rel + ".MODEL.GR2")
    print("%-24s %d verts  %d -> %d bytes (sectors stored uncompressed)" % (
        rel.split("/")[-1], nverts, n_in, n_out))
    print("   box before  X[%+7.1f,%+7.1f] Y[%+7.1f,%+7.1f] Z[%+7.1f,%+7.1f]" % (
        b["lo"][0], b["hi"][0], b["lo"][1], b["hi"][1], b["lo"][2], b["hi"][2]))
    print("   box after   X[%+7.1f,%+7.1f] Y[%+7.1f,%+7.1f] Z[%+7.1f,%+7.1f]" % (
        a["lo"][0], a["hi"][0], a["lo"][1], a["hi"][1], a["lo"][2], a["hi"][2]))
    base = BASE_OF[rel.split("/")[-1]]
    was, now, want = tip_x(rel), -tip_x(rel), tip_x(base)
    agree = (now * want) > 0
    print("   tip on X: was %+.1f, now %+.1f, base %s is %+.1f  -> %s" % (
        was, now, base.split("/")[-1], want, "same side" if agree else "STILL OPPOSITE"))
    assert agree, "the Y flip does not bring %s onto its base weapon's side" % rel

manifest = json.loads((HERE / "mod.json").read_text(encoding="utf-8"))
manifest["files"] = sorted(written)
(HERE / "mod.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print()
print("%d meshes written, both parse back" % len(written))
