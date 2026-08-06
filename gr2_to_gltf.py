"""Export a Lionheart `.gr2` character model to glTF 2.0 (mesh + skeleton + basic
materials) so it can be opened and edited in Blender.

Hand-rolled glTF writer (json + struct only, no new dependencies -- consistent with
how gr2_format.py itself was built) covering just the subset of glTF needed here: one
skinned mesh, split into per-material primitives, materials referencing external image
files resolved by filename against the game's real asset tree. See
docs/gr2-format.md's round-trip plan for the confirmed field mapping this relies on.

Known limitation, deliberately not handled here: no coordinate-system conversion is
applied (3ds Max/Granny content is typically Z-up; glTF is Y-up). If the imported
model appears lying on its side in Blender, rotate -90 degrees about X after import --
safer than guessing at an axis-swap here and silently corrupting the data if wrong.

Usage: python gr2_to_gltf.py <file.gr2> <output.gltf>
"""
from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

import gr2_format as gf

# glTF component type / accessor type constants
FLOAT = 5126
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963

GAME_RESOURCES_ROOT = (
    r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader"
    r"\data\Resources"
)


# ---------------------------------------------------------------------------
# Small element-tree helpers (gr2_format.Element lists aren't dicts -- these
# make the field-name-lookup style used throughout this script readable)
# ---------------------------------------------------------------------------

def field(elements: list[gf.Element], name: str) -> gf.Element | None:
    for e in elements:
        if e.name == name:
            return e
    return None


def fields(elements: list[gf.Element], name: str) -> list[gf.Element]:
    return [e for e in elements if e.name == name]


def flat(array_element: gf.Element) -> list[float]:
    """An 'array' Element's value is a list of (kind, value) tuples for f32/i32/u8
    leaves; unwrap to a plain list of numbers."""
    return [v for (_, v) in array_element.value]


# ---------------------------------------------------------------------------
# Minimal math: quaternion -> 3x3 rotation matrix, 3x3 * 3x3, compose to 4x4.
# No numpy -- plain nested lists, row-major internally, serialized column-major
# for glTF at the end.
# ---------------------------------------------------------------------------

def quat_to_mat3(x: float, y: float, z: float, w: float) -> list[list[float]]:
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def mat3_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)]


def transform_to_matrix4_colmajor(transform_value: dict) -> list[float]:
    """Compose Granny's Transform (translation + rotation quaternion + scale_shear
    3x3) into a glTF-style column-major 4x4 matrix: M = T * R * ScaleShear."""
    tx, ty, tz = transform_value["translation"]
    qx, qy, qz, qw = transform_value["rotation"]
    r = quat_to_mat3(qx, qy, qz, qw)
    s = transform_value["scale_shear"]  # 3 rows of 3 floats
    linear = mat3_mul(r, s)
    # column-major 4x4: columns are [linear col0, linear col1, linear col2, translation]
    return [
        linear[0][0], linear[1][0], linear[2][0], 0.0,
        linear[0][1], linear[1][1], linear[2][1], 0.0,
        linear[0][2], linear[1][2], linear[2][2], 0.0,
        tx, ty, tz, 1.0,
    ]


# ---------------------------------------------------------------------------
# Texture resolution: FromFileName is a dead dev-machine path
# (e.g. C:\Icewind Art\Monsters\WereRat\wererat.tga). Resolve by filename only
# against the game's real, loose Resources tree (same directory-walk approach
# scripts/validate_gr2.py uses).
# ---------------------------------------------------------------------------

def build_texture_index(resources_root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for ext in ("*.tga", "*.dds", "*.bmp", "*.png"):
        for p in resources_root.rglob(ext):
            index.setdefault(p.name.lower(), p)
    return index


# ---------------------------------------------------------------------------
# glTF document builder
# ---------------------------------------------------------------------------

class GltfBuilder:
    def __init__(self):
        self.buffer = bytearray()
        self.buffer_views: list[dict] = []
        self.accessors: list[dict] = []
        self.nodes: list[dict] = []
        self.meshes: list[dict] = []
        self.materials: list[dict] = []
        self.images: list[dict] = []
        self.textures: list[dict] = []
        self.skins: list[dict] = []
        self._material_cache: dict[str, int] = {}

    def _add_buffer_view(self, data: bytes, target: int | None) -> int:
        while len(self.buffer) % 4 != 0:
            self.buffer.append(0)
        offset = len(self.buffer)
        self.buffer.extend(data)
        bv: dict = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target is not None:
            bv["target"] = target
        idx = len(self.buffer_views)
        self.buffer_views.append(bv)
        return idx

    def add_accessor(self, values: list, component_type: int, gltf_type: str,
                      target: int | None = None, with_minmax: bool = False) -> int:
        fmt_char = {FLOAT: "f", UNSIGNED_SHORT: "H", UNSIGNED_INT: "I"}[component_type]
        n_comp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[gltf_type]
        flat_vals = values if n_comp == 1 else [c for tup in values for c in tup]
        data = struct.pack("<%d%s" % (len(flat_vals), fmt_char), *flat_vals)
        bv = self._add_buffer_view(data, target)
        count = len(values)
        acc: dict = {"bufferView": bv, "componentType": component_type, "count": count, "type": gltf_type}
        if with_minmax and count:
            if n_comp == 1:
                acc["min"], acc["max"] = [min(values)], [max(values)]
            else:
                acc["min"] = [min(v[i] for v in values) for i in range(n_comp)]
                acc["max"] = [max(v[i] for v in values) for i in range(n_comp)]
        idx = len(self.accessors)
        self.accessors.append(acc)
        return idx

    def add_matrix_accessor(self, matrices: list[list[float]]) -> int:
        flat_vals = [c for m in matrices for c in m]
        data = struct.pack("<%df" % len(flat_vals), *flat_vals)
        bv = self._add_buffer_view(data, None)
        idx = len(self.accessors)
        self.accessors.append({"bufferView": bv, "componentType": FLOAT, "count": len(matrices), "type": "MAT4"})
        return idx

    def get_or_add_material(self, texture_path: Path | None) -> int:
        key = str(texture_path) if texture_path else "__none__"
        if key in self._material_cache:
            return self._material_cache[key]
        material: dict = {"pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}}
        if texture_path is not None:
            img_idx = len(self.images)
            self.images.append({"uri": texture_path.as_uri()})
            tex_idx = len(self.textures)
            self.textures.append({"source": img_idx})
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex_idx}
        idx = len(self.materials)
        self.materials.append(material)
        self._material_cache[key] = idx
        return idx

    def to_json(self) -> dict:
        return {
            "asset": {"version": "2.0", "generator": "LionheartModTools gr2_to_gltf.py"},
            "scene": 0,
            "scenes": [{"nodes": [n for n in range(len(self.nodes)) if self.nodes[n].get("_root")]}],
            "nodes": [{k: v for k, v in n.items() if not k.startswith("_")} for n in self.nodes],
            "meshes": self.meshes,
            "materials": self.materials,
            "images": self.images,
            "textures": self.textures,
            "skins": self.skins,
            "accessors": self.accessors,
            "bufferViews": self.buffer_views,
            "buffers": [{"uri": None, "byteLength": len(self.buffer)}],  # filled in on write
        }


# ---------------------------------------------------------------------------
# Skeleton -> glTF nodes/skin
# ---------------------------------------------------------------------------

def export_skeleton(builder: GltfBuilder, bones: list[list[gf.Element]]) -> tuple[list[int], dict[str, int], int]:
    """`bones` is already a list of per-bone field-lists (see _group_repeated_records).
    Returns (all_node_indices_in_bone_order, bone_name_to_node_index, skin_index)."""
    node_base = len(builder.nodes)
    name_to_index: dict[str, int] = {}
    inverse_bind_matrices: list[list[float]] = []

    for f in bones:
        name = field(f, "Name").value
        transform = field(f, "Transform").value
        matrix = transform_to_matrix4_colmajor(transform)
        builder.nodes.append({"name": name, "matrix": matrix, "children": []})
        name_to_index[name] = node_base + len(name_to_index)
        ibm_flat = flat(field(f, "InverseWorldTransform"))
        inverse_bind_matrices.append(ibm_flat)

    roots = []
    for i, f in enumerate(bones):
        parent_index = field(f, "ParentIndex").value
        node_idx = node_base + i
        if parent_index < 0:
            builder.nodes[node_idx]["_root"] = True
            roots.append(node_idx)
        else:
            builder.nodes[node_base + parent_index]["children"].append(node_idx)

    ibm_accessor = builder.add_matrix_accessor(inverse_bind_matrices)
    joint_nodes = [node_base + i for i in range(len(bones))]
    skin_idx = len(builder.skins)
    builder.skins.append({
        "joints": joint_nodes,
        "inverseBindMatrices": ibm_accessor,
        "skeleton": roots[0] if roots else joint_nodes[0],
    })
    return joint_nodes, name_to_index, skin_idx


# ---------------------------------------------------------------------------
# Mesh -> glTF mesh/primitives
# ---------------------------------------------------------------------------

def _resolve_texture_path(material_fields: list[gf.Element], texture_index: dict[str, Path]) -> Path | None:
    """Material -> Maps (repeated Usage/Map records) -> Map.Texture.FromFileName,
    preferring the 'Diffuse Color' map if present. FromFileName is a dead
    dev-machine path (e.g. C:\\Icewind Art\\...\\wererat.tga); resolve by basename
    against the real, loose Resources tree."""
    maps_field = field(material_fields, "Maps")
    if maps_field is None or not maps_field.value:
        return None
    map_records = _group_repeated_records(maps_field.value, ["Usage"])
    chosen = next((r for r in map_records if field(r, "Usage").value == "Diffuse Color"), None)
    chosen = chosen or (map_records[0] if map_records else None)
    if chosen is None:
        return None
    map_obj = field(chosen, "Map")
    if map_obj is None:
        return None
    texture_fields = field(map_obj.value, "Texture")
    if texture_fields is None:
        return None
    from_file_name = field(texture_fields.value, "FromFileName")
    if from_file_name is None or not from_file_name.value:
        return None
    return texture_index.get(Path(from_file_name.value).name.lower())


def export_mesh(builder: GltfBuilder, mesh_elem: list[gf.Element], bone_name_to_joint: dict[str, int],
                 joint_base: int, texture_index: dict[str, Path]) -> int:
    vertex_data = field(mesh_elem, "PrimaryVertexData").value
    vertices = field(vertex_data, "Vertices").value  # list of lists-of-Elements (array_of_references)

    positions, normals, texcoords, weights, joints_local = [], [], [], [], []
    for v_fields in vertices:
        positions.append(tuple(flat(field(v_fields, "Position"))))
        normals.append(tuple(flat(field(v_fields, "Normal"))))
        texcoords.append(tuple(flat(field(v_fields, "TextureCoordinates0"))))
        # BoneWeights is stored on disk as 4 raw u8 bytes (0-255); glTF's WEIGHTS_0
        # (componentType FLOAT here) expects normalized 0.0-1.0 floats.
        weights.append(tuple(w / 255.0 for w in flat(field(v_fields, "BoneWeights"))))
        joints_local.append(tuple(int(i) for i in flat(field(v_fields, "BoneIndices"))))

    # Local BoneIndices are indices into THIS mesh's own BoneBindings list, not
    # directly into the skeleton -- resolve via BoneName. (BoneBindings is a
    # repeated-record 'reference', same flattening pattern as Bones.)
    local_to_global: list[int] = []
    bb_field = field(mesh_elem, "BoneBindings")
    if bb_field is not None:
        for record in _group_repeated_records(bb_field.value, ["BoneName"]):
            bone_name = field(record, "BoneName").value
            local_to_global.append(bone_name_to_joint.get(bone_name, joint_base))

    joints_global = [
        tuple((local_to_global[i] - joint_base if i < len(local_to_global) else 0) for i in local4)
        for local4 in joints_local
    ]

    pos_acc = builder.add_accessor(positions, FLOAT, "VEC3", ARRAY_BUFFER, with_minmax=True)
    norm_acc = builder.add_accessor(normals, FLOAT, "VEC3", ARRAY_BUFFER)
    uv_acc = builder.add_accessor(texcoords, FLOAT, "VEC2", ARRAY_BUFFER)
    weight_acc = builder.add_accessor(weights, FLOAT, "VEC4", ARRAY_BUFFER)
    joint_acc = builder.add_accessor(joints_global, UNSIGNED_SHORT, "VEC4", ARRAY_BUFFER)

    attributes = {
        "POSITION": pos_acc, "NORMAL": norm_acc, "TEXCOORD_0": uv_acc,
        "WEIGHTS_0": weight_acc, "JOINTS_0": joint_acc,
    }

    topology = field(mesh_elem, "PrimaryTopology").value
    groups_field = field(topology, "Groups")
    groups = _group_repeated_records(groups_field.value, ["MaterialIndex"]) if groups_field else []
    # Indices is a 'reference' of repeated single-Int32 records (like StringOffsets),
    # not an 'array' kind -- each item is its own Element named "Int32".
    indices_flat = [e.value for e in field(topology, "Indices").value]

    # MaterialIndex (per group) indexes into THIS mesh's own MaterialBindings list
    # (same local-binding-table pattern as BoneIndices/BoneBindings above), not the
    # file's top-level Materials[] array directly.
    material_bindings_field = field(mesh_elem, "MaterialBindings")
    material_records = (
        _group_repeated_records(material_bindings_field.value, ["Material"])
        if material_bindings_field else []
    )
    texture_paths: list[Path | None] = []
    for rec in material_records:
        mat = field(rec, "Material")
        texture_paths.append(_resolve_texture_path(mat.value, texture_index) if mat else None)

    primitives = []
    for g in groups:
        material_index = field(g, "MaterialIndex").value
        tri_first = field(g, "TriFirst").value
        tri_count = field(g, "TriCount").value
        idx_slice = indices_flat[tri_first * 3: (tri_first + tri_count) * 3]
        idx_acc = builder.add_accessor(idx_slice, UNSIGNED_INT, "SCALAR", ELEMENT_ARRAY_BUFFER)
        tex_path = texture_paths[material_index] if 0 <= material_index < len(texture_paths) else None
        primitives.append({
            "attributes": attributes,
            "indices": idx_acc,
            "material": builder.get_or_add_material(tex_path),
        })

    mesh_idx = len(builder.meshes)
    builder.meshes.append({"name": field(mesh_elem, "Name").value, "primitives": primitives})
    return mesh_idx


# ---------------------------------------------------------------------------
# Top-level export
# ---------------------------------------------------------------------------

def export_model(gr2_path: str, out_path: str, resources_root: str = GAME_RESOURCES_ROOT) -> None:
    gfile = gf.GrannyFile.load_from_file(gr2_path)
    models_field = field(gfile.root_elements, "Models")
    if models_field is None or not models_field.value:
        raise ValueError("no Models found in this .gr2 file")

    texture_index = build_texture_index(Path(resources_root))
    builder = GltfBuilder()

    for model_fields in models_field.value:
        skeleton = field(model_fields, "Skeleton").value
        bones = [b.value for b in fields(skeleton, "Bones")]
        # Bones is a repeated-record 'reference' like BoneBindings -- but
        # gr2_format groups each record's fields flatly under one 'Bones' element
        # per the dump; re-derive per-bone field groups here defensively.
        bones_field = field(skeleton, "Bones")
        bone_records = _group_repeated_records(bones_field.value, ["Name", "ParentIndex", "Transform",
                                                                     "InverseWorldTransform", "LightInfo",
                                                                     "CameraInfo", "ExtendedData"])

        joint_nodes, bone_name_to_joint, skin_idx = export_skeleton(builder, bone_records)
        joint_base = joint_nodes[0]

        mesh_bindings = field(model_fields, "MeshBindings")
        mesh_node_indices = []
        for mb_record in _group_repeated_records(mesh_bindings.value, ["Mesh"]):
            mesh_elem = field(mb_record, "Mesh").value
            mesh_idx = export_mesh(builder, mesh_elem, bone_name_to_joint, joint_base, texture_index)
            node_idx = len(builder.nodes)
            builder.nodes.append({"mesh": mesh_idx, "skin": skin_idx, "_root": True})
            mesh_node_indices.append(node_idx)

    gltf_json = builder.to_json()
    bin_path = Path(out_path).with_suffix(".bin")
    gltf_json["buffers"] = [{"uri": bin_path.name, "byteLength": len(builder.buffer)}]

    bin_path.write_bytes(bytes(builder.buffer))
    Path(out_path).write_text(json.dumps(gltf_json, indent=2))
    print(f"wrote {out_path} + {bin_path} ({len(builder.buffer)} bytes of buffer data)")


def _group_repeated_records(flat_elements: list[gf.Element], field_names: list[str]) -> list[list[gf.Element]]:
    """gr2_format.py's dump shows repeated struct arrays (Bones, BoneBindings, ...)
    as one flat list of Elements where the same field names repeat in a fixed cycle.
    Split back into one list-of-Elements per record using the first field name as
    the record boundary marker."""
    if not flat_elements:
        return []
    marker = field_names[0]
    records: list[list[gf.Element]] = []
    current: list[gf.Element] = []
    for e in flat_elements:
        if e.name == marker and current:
            records.append(current)
            current = []
        current.append(e)
    if current:
        records.append(current)
    return records


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python gr2_to_gltf.py <file.gr2> <output.gltf>")
        sys.exit(1)
    export_model(sys.argv[1], sys.argv[2])
