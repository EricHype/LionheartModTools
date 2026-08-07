"""Uniformly scale a whole Model (skeleton + mesh) by a factor k, patching the .gr2
directly -- no Blender/glTF round trip involved.

History: two earlier approaches failed.
  1. Scaling mesh vertex positions alone worked in-game but stretched textures at
     joints, because the skeleton's own bind pose stayed at 1x while the mesh was 2x.
  2. Scaling the armature object in Blender turned out to fold the scale into each
     bone's InverseWorldTransform only (via Blender's glTF export), leaving each
     bone's own Transform completely unpatched -- an inconsistent pair.
  3. Patching Model.InitialPlacement.scale_shear (a single whole-model transform)
     was structurally the cleanest idea, but empirically the engine doesn't apply it
     as a runtime scale -- confirmed in-game: model came back to 1x size.

This version instead derives the correct per-bone edit mathematically. For a skeleton
where each bone's world transform is World[bone] = World[parent] @ Local[bone], scaling
every bone's LOCAL TRANSLATION by k -- and leaving each bone's own rotation and
scale_shear completely untouched -- produces, by induction through the hierarchy, a
world-space result where every bone's linear (rotation+scale_shear) part is UNCHANGED
and every bone's world translation is exactly k times the original. That's a perfectly
rigid, undistorted k-times-bigger skeleton with no shape distortion at any joint.

For InverseWorldTransform, a shortcut formula (just scale its stored translation column
by k) turned out to be WRONG: verified numerically that the actual bind-pose world
transform each bone's IBM inverts is `Model.InitialPlacement @ (chain of bone Locals)`,
not the bone chain alone -- confirmed by reconstructing world matrices from Transform
data and checking they invert to the file's stored IBM (near-zero error on the
untouched original; this is how the "does the file even work this way" question was
answered). InitialPlacement has a nonzero translation, so scaling a bone's own local
translation by k does NOT make its world translation simply k times the original --
it's k times the original *offset from the anchor point* (InitialPlacement's
translation), which is the physically correct "scale the rig about its ground/root
anchor" behavior, but doesn't reduce to any single scalar multiply on the stored
inverse. So instead of a shortcut, InverseWorldTransform is recomputed directly: walk
the (already-patched) Transform chain from InitialPlacement down through each bone's
parent, compose the world matrix, invert it affinely, and write that as the new IBM.
This is guaranteed self-consistent by construction, using the same reconstruction this
module already validated against the original file's own numbers.

Combined with scaling every vertex Position by k (already proven correct in the first
test above), this scales the whole model self-consistently with no glTF/Blender
involvement at all.

Usage: python scale_model.py <original.gr2> <factor> <output.gr2>
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import gr2_format as gf
from gr2_to_gltf import field, _group_repeated_records, quat_to_mat3, mat3_mul
from gltf_to_gr2 import repack, _patch_transform, _patch_floats


def _mat4_from_transform(t: dict) -> list[list[float]]:
    r = quat_to_mat3(*t["rotation"])
    s = t["scale_shear"]
    linear = mat3_mul(r, s)
    tx, ty, tz = t["translation"]
    return [
        [linear[0][0], linear[0][1], linear[0][2], tx],
        [linear[1][0], linear[1][1], linear[1][2], ty],
        [linear[2][0], linear[2][1], linear[2][2], tz],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _mat4_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def _mat4_inverse_affine(m: list[list[float]]) -> list[list[float]]:
    """Inverse of an affine [A | t] matrix: [A^-1 | -A^-1 t]. A is inverted via the
    3x3 adjugate/determinant (fine here -- A is always a well-conditioned
    rotation * scale_shear, never near-singular for real bone data)."""
    a, b, c = m[0][0], m[0][1], m[0][2]
    d, e, f = m[1][0], m[1][1], m[1][2]
    g, h, i = m[2][0], m[2][1], m[2][2]
    t = [m[0][3], m[1][3], m[2][3]]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    inv = [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]
    it = [-(inv[r][0] * t[0] + inv[r][1] * t[1] + inv[r][2] * t[2]) for r in range(3)]
    return [
        [inv[0][0], inv[0][1], inv[0][2], it[0]],
        [inv[1][0], inv[1][1], inv[1][2], it[1]],
        [inv[2][0], inv[2][1], inv[2][2], it[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _mat4_to_flat_colmajor(m: list[list[float]]) -> list[float]:
    return [m[r][c] for c in range(4) for r in range(4)]


def scale_model(original_gr2_path: str, factor: float, output_gr2_path: str) -> None:
    raw = Path(original_gr2_path).read_bytes()
    header, offset = gf.parse_header(raw)
    endian = ">" if header.big_endian else "<"
    file_info, offset = gf.parse_file_info(raw, offset, endian)

    sector_infos: list[gf.SectorInfo] = []
    sectors: list[gf.Sector] = []
    for _ in range(file_info.sector_count):
        info, offset = gf.parse_sector_info(raw, offset, endian)
        sector_infos.append(info)
        sectors.append(gf.load_sector(raw, endian, info))

    root_elements, _ = gf.parse_element(
        sectors, file_info.root_ref.sector, file_info.type_ref.sector,
        file_info.root_ref.position, file_info.type_ref.position, header.bits_64, endian,
    )

    sector_data = [bytearray(s.data) for s in sectors]
    touched_sectors: set[int] = set()

    models_field = field(root_elements, "Models")
    for model_fields in models_field.value:
        ip = field(model_fields, "InitialPlacement").value
        ip_mat = _mat4_from_transform(ip)

        skeleton = field(model_fields, "Skeleton").value
        bones_field = field(skeleton, "Bones")
        bone_records = _group_repeated_records(
            bones_field.value,
            ["Name", "ParentIndex", "Transform", "InverseWorldTransform", "LightInfo", "CameraInfo", "ExtendedData"],
        )

        new_local: list[list[list[float]]] = [None] * len(bone_records)  # type: ignore[list-item]
        for i, bone in enumerate(bone_records):
            transform_elem = field(bone, "Transform")
            t = transform_elem.value
            new_translation = (t["translation"][0] * factor, t["translation"][1] * factor, t["translation"][2] * factor)
            _patch_transform(sector_data, transform_elem, new_translation, t["rotation"], t["scale_shear"])
            touched_sectors.add(transform_elem.data_sector_id)
            new_local[i] = _mat4_from_transform(
                {"translation": new_translation, "rotation": t["rotation"], "scale_shear": t["scale_shear"]}
            )

        parent_idx = [field(bone, "ParentIndex").value for bone in bone_records]
        world: list[list[list[float]] | None] = [None] * len(bone_records)

        def compute_world(i: int) -> list[list[float]]:
            if world[i] is not None:
                return world[i]  # type: ignore[return-value]
            p = parent_idx[i]
            base = ip_mat if p < 0 else compute_world(p)
            w = _mat4_mul(base, new_local[i])
            world[i] = w
            return w

        for i, bone in enumerate(bone_records):
            new_world = compute_world(i)
            new_ibm = _mat4_inverse_affine(new_world)
            ibm_elem = field(bone, "InverseWorldTransform")
            struct.pack_into("<16f", sector_data[ibm_elem.data_sector_id], ibm_elem.offset, *_mat4_to_flat_colmajor(new_ibm))
            touched_sectors.add(ibm_elem.data_sector_id)

        mesh_bindings_field = field(model_fields, "MeshBindings")
        for mb_record in _group_repeated_records(mesh_bindings_field.value, ["Mesh"]):
            mesh_elem = field(mb_record, "Mesh").value
            vertex_data = field(mesh_elem, "PrimaryVertexData").value
            vertices = field(vertex_data, "Vertices").value
            for v_fields in vertices:
                pos_elem = field(v_fields, "Position")
                pos = [x for _, x in pos_elem.value] if pos_elem.kind == "array" else list(pos_elem.value)
                pos_scaled = tuple(p * factor for p in pos)
                _patch_floats(sector_data, pos_elem, pos_scaled)
                touched_sectors.add(pos_elem.data_sector_id)

    repack(raw, header, file_info, sector_infos, sectors, sector_data, touched_sectors, output_gr2_path)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    scale_model(sys.argv[1], float(sys.argv[2]), sys.argv[3])
