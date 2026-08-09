"""Export a Lionheart `.gr2` character model to glTF 2.0 (mesh + skeleton + basic
materials) so it can be opened and edited in Blender.

Hand-rolled glTF writer (json + struct/zlib/base64 only, no new dependencies --
consistent with how gr2_format.py itself was built) covering just the subset of glTF
needed here: one skinned mesh, split into per-material primitives, materials with
textures decoded directly from the .gr2's own embedded pixel data (Textures[].Images[]
.MIPLevels[].Pixels -- raw uncompressed RGB/RGBA, self-described the same way as
everything else in this format) and re-encoded as PNG data URIs. See
docs/gr2-format.md's round-trip plan for the confirmed field mapping this relies on.

Known limitation, deliberately not handled here: no coordinate-system conversion is
applied (3ds Max/Granny content is typically Z-up; glTF is Y-up). If the imported
model appears lying on its side in Blender, rotate -90 degrees about X after import --
safer than guessing at an axis-swap here and silently corrupting the data if wrong.

Usage: python gr2_to_gltf.py <file.gr2> <output.gltf>
"""
from __future__ import annotations

import base64
import json
import math
import struct
import sys
import zlib
from pathlib import Path

import gr2_format as gf
from binktc0_decode import decode_binktc0

# glTF component type / accessor type constants
FLOAT = 5126
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963


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


def ref_floats(reference_element: gf.Element) -> list[float]:
    """A dynamic-array 'reference' Element (e.g. Curve2's Knots/Controls) has a list
    of real Elements (one per item, kind='f32') as its value -- unlike a fixed-size
    'array' Element (see `flat`), which stores (kind, value) tuples instead."""
    return [e.value for e in reference_element.value]


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
# Texture decoding: `Texture.FromFileName` is a dead dev-machine path (e.g.
# C:\Icewind Art\Monsters\WereRat\wererat.tga) -- the game ships no loose source
# textures matching it at all. The real pixel data lives embedded in the .gr2
# itself: Texture.{Width,Height,Encoding,Layout,Images[].MIPLevels[].Pixels},
# self-described the same way as everything else in this format.
#
# Confirmed by sampling 230 textures across 60 real character models: every
# single one uses Encoding=3 (Granny's `GrannyBinkTextureEncoding`, the
# "BinkTC0" wavelet+arithmetic-coded still-image format -- see
# docs/bink-texture-format.md; NOT the unrelated Bink video codec despite the
# name), never Encoding=1 (`GrannyRawTextureEncoding`, plain uncompressed bytes
# matching `Layout`). Both are handled here now, via binktc0_decode.py for
# Encoding=3. When *authoring new* texture content there's no need to match
# Encoding=3 either -- writing Encoding=1 (raw) remains a legitimate,
# presumably-loadable alternative this format explicitly supports, and this
# same extractor reads that back too.
# Re-encoded as a minimal hand-rolled PNG (zlib for the DEFLATE stream, no
# filtering beyond "none" per scanline) and embedded as a glTF data URI --
# avoids a second output file and an external imaging library dependency.
# ---------------------------------------------------------------------------

def _png_encode(width: int, height: int, pixels: bytes, bytes_per_pixel: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    color_type = {3: 2, 4: 6, 1: 0}.get(bytes_per_pixel, 2)  # 2=RGB, 6=RGBA, 0=greyscale
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    stride = width * bytes_per_pixel
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: none
        raw += pixels[y * stride:(y + 1) * stride]
    idat = zlib.compress(bytes(raw), 6)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _extract_texture_png(texture_fields: list[gf.Element]) -> bytes | None:
    """texture_fields is the field list of a resolved Texture element (Width, Height,
    Encoding, Layout, Images, ...). Returns PNG bytes, or None if no pixel data is
    present or the pixel data can't be decoded."""
    width_e = field(texture_fields, "Width")
    height_e = field(texture_fields, "Height")
    images_e = field(texture_fields, "Images")
    if width_e is None or height_e is None or images_e is None or not images_e.value:
        return None

    bytes_per_pixel = 3
    layout_e = field(texture_fields, "Layout")
    if layout_e is not None and isinstance(layout_e.value, list):
        bpp_e = field(layout_e.value, "BytesPerPixel")
        if bpp_e is not None:
            bytes_per_pixel = bpp_e.value

    # Images/MIPLevels are 'reference' kind (single instance) -- .value IS that
    # instance's own field list directly, not a list of per-item field lists.
    mip_levels_e = field(images_e.value, "MIPLevels")
    if mip_levels_e is None or not isinstance(mip_levels_e.value, list):
        return None
    pixels_e = field(mip_levels_e.value, "Pixels")
    if pixels_e is None or not pixels_e.value:
        return None

    width, height = width_e.value, height_e.value
    raw = bytes(e.value for e in pixels_e.value)

    encoding_e = field(texture_fields, "Encoding")
    encoding = encoding_e.value if encoding_e is not None else 1

    if encoding == 3:  # GrannyBinkTextureEncoding ("BinkTC0") -- see docs/bink-texture-format.md
        # decode_binktc0 divides width/height by 16 internally (4 wavelet levels);
        # the real codec pads to a multiple of 16 before compressing, so anything
        # that isn't one here is a texture shape this decoder doesn't handle yet.
        if width % 16 != 0 or height % 16 != 0:
            return None
        try:
            pixels = decode_binktc0(width, height, raw, has_alpha=(bytes_per_pixel == 4))
        except Exception:
            return None
        return _png_encode(width, height, pixels, 4 if bytes_per_pixel == 4 else 3)

    if encoding == 1:  # GrannyRawTextureEncoding -- plain uncompressed bytes matching Layout
        expected = width * height * bytes_per_pixel
        if len(raw) < expected:
            return None
        return _png_encode(width, height, raw[:expected], bytes_per_pixel)

    return None


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
        self.animations: list[dict] = []
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

    def get_or_add_material(self, texture_key: str | None, png_bytes: bytes | None) -> int:
        key = texture_key or "__none__"
        if key in self._material_cache:
            return self._material_cache[key]
        material: dict = {"pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}}
        if png_bytes is not None:
            img_idx = len(self.images)
            data_uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
            self.images.append({"uri": data_uri})
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
            "animations": self.animations,
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

def _resolve_texture_fields(material_fields: list[gf.Element]) -> list[gf.Element] | None:
    """Material -> Maps (repeated Usage/Map records) -> Map.Texture, preferring the
    'Diffuse Color' map if present. Returns the resolved Texture element's own field
    list (Width/Height/Layout/Images/...), which _extract_texture_png reads directly
    -- FromFileName is only used here as a display/cache key, not a filesystem path
    (it's a dead dev-machine path; the real pixels are embedded, see
    _extract_texture_png)."""
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
    return texture_fields.value


def export_mesh(builder: GltfBuilder, mesh_elem: list[gf.Element], bone_name_to_joint: dict[str, int],
                 joint_base: int) -> int:
    vertex_data = field(mesh_elem, "PrimaryVertexData").value
    vertices = field(vertex_data, "Vertices").value  # list of lists-of-Elements (array_of_references)

    positions, normals, texcoords, weights, joints_local = [], [], [], [], []
    for v_fields in vertices:
        positions.append(tuple(flat(field(v_fields, "Position"))))
        normals.append(tuple(flat(field(v_fields, "Normal"))))
        texcoords.append(tuple(flat(field(v_fields, "TextureCoordinates0"))))
        # Some static props (seen on several weapon meshes) use a simpler vertex
        # format with no BoneWeights/BoneIndices at all -- rigidly bound to a single
        # bone (the model's own root), not per-vertex skinned. Treat that as a full
        # rigid weight to joint 0 rather than crashing; this keeps the mesh correctly
        # positioned via its skeleton's sole bone without needing an unskinned
        # (skin-less) primitive code path.
        weights_field = field(v_fields, "BoneWeights")
        indices_field = field(v_fields, "BoneIndices")
        if weights_field is None or indices_field is None:
            weights.append((1.0, 0.0, 0.0, 0.0))
            joints_local.append((0, 0, 0, 0))
        else:
            # BoneWeights is stored on disk as 4 raw u8 bytes (0-255); glTF's
            # WEIGHTS_0 (componentType FLOAT here) expects normalized 0.0-1.0 floats.
            weights.append(tuple(w / 255.0 for w in flat(weights_field)))
            joints_local.append(tuple(int(i) for i in flat(indices_field)))

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
    material_textures: list[list[gf.Element] | None] = []
    for rec in material_records:
        mat = field(rec, "Material")
        material_textures.append(_resolve_texture_fields(mat.value) if mat else None)

    primitives = []
    for g in groups:
        material_index = field(g, "MaterialIndex").value
        tri_first = field(g, "TriFirst").value
        tri_count = field(g, "TriCount").value
        idx_slice = indices_flat[tri_first * 3: (tri_first + tri_count) * 3]
        idx_acc = builder.add_accessor(idx_slice, UNSIGNED_INT, "SCALAR", ELEMENT_ARRAY_BUFFER)
        tex_fields = material_textures[material_index] if 0 <= material_index < len(material_textures) else None
        tex_key = None
        png_bytes = None
        if tex_fields is not None:
            from_file_name = field(tex_fields, "FromFileName")
            tex_key = from_file_name.value if from_file_name and from_file_name.value else None
            png_bytes = _extract_texture_png(tex_fields)
            tex_key = tex_key or (f"<embedded-{id(tex_fields)}>" if png_bytes else None)
        primitives.append({
            "attributes": attributes,
            "indices": idx_acc,
            "material": builder.get_or_add_material(tex_key, png_bytes),
        })

    mesh_idx = len(builder.meshes)
    builder.meshes.append({"name": field(mesh_elem, "Name").value, "primitives": primitives})
    return mesh_idx


# ---------------------------------------------------------------------------
# Animation (.ANIMATION.GR2) -> glTF animations[]
#
# Structure (see docs/gr2-format.md's "Curve2 / animation curve format" section):
# root.Animations[] -> {Name, Duration, TimeStep, TrackGroups[]} -> TrackGroups[]
# -> {Name, TransformTracks (a 'reference' whose .value is the SAME flat repeated-
# record layout _group_repeated_records already handles) -> per-bone
# {Name, PositionCurve, OrientationCurve, ScaleShearCurve}}. Each curve is
# {Degree, Knots (keyframe times), Controls (flat values, `dimension` floats per
# keyframe where dimension = len(Controls)/len(Knots) -- 3 for Position/ScaleShear,
# 4 for Orientation quaternion, same xyzw order as bind-pose Transform.rotation).
# An empty curve (no Knots) means that channel isn't animated on this clip -- the
# bind-pose Transform value applies for the whole clip, so no glTF channel is
# emitted for it (glTF nodes already carry the bind-pose matrix as their default).
# ---------------------------------------------------------------------------

def export_animation(builder: GltfBuilder, anim_path: str, bone_name_to_joint: dict[str, int],
                      clip_name: str | None = None) -> None:
    gfile = gf.GrannyFile.load_from_file(anim_path)
    animations_field = field(gfile.root_elements, "Animations")
    if animations_field is None or not animations_field.value:
        return

    for anim in animations_field.value:
        name = clip_name or field(anim, "Name").value or Path(anim_path).stem
        track_groups_field = field(anim, "TrackGroups")
        if track_groups_field is None:
            continue

        samplers: list[dict] = []
        channels: list[dict] = []

        for tg in track_groups_field.value:
            tt_field = field(tg, "TransformTracks")
            if tt_field is None or not tt_field.value:
                continue
            for bone_track in _group_repeated_records(tt_field.value, ["Name"]):
                bone_name = field(bone_track, "Name").value
                joint_idx = bone_name_to_joint.get(bone_name)
                if joint_idx is None:
                    continue  # track for a bone not present in this model's skeleton

                for curve_field_name, path, dim in (
                    ("PositionCurve", "translation", 3),
                    ("OrientationCurve", "rotation", 4),
                    ("ScaleShearCurve", "scale", 3),
                ):
                    curve = field(bone_track, curve_field_name)
                    if curve is None:
                        continue
                    knots_field = field(curve.value, "Knots")
                    controls_field = field(curve.value, "Controls")
                    if knots_field is None or not knots_field.value:
                        continue  # not animated on this track -- bind pose stands
                    knots = ref_floats(knots_field)
                    controls = ref_floats(controls_field)
                    if len(controls) != dim * len(knots):
                        # Rare (seen on a handful of bones' ScaleShearCurve): a full
                        # 9-float 3x3 scale_shear per keyframe (real shear present,
                        # not just uniform scale) rather than the usual 3 floats --
                        # glTF's "scale" channel only supports a VEC3 scale factor,
                        # no shear. Skip rather than emit a mismatched sampler
                        # (sampler input/output counts must match, or importers like
                        # Blender's choke) -- the node's bind-pose scale_shear stands.
                        continue
                    values = [tuple(controls[i:i + dim]) for i in range(0, len(controls), dim)]

                    time_acc = builder.add_accessor(knots, FLOAT, "SCALAR", with_minmax=True)
                    value_acc = builder.add_accessor(values, FLOAT, "VEC4" if dim == 4 else "VEC3")
                    degree = field(curve.value, "Degree").value
                    sampler_idx = len(samplers)
                    samplers.append({
                        "input": time_acc,
                        "output": value_acc,
                        "interpolation": "STEP" if degree == 0 else "LINEAR",
                    })
                    channels.append({
                        "sampler": sampler_idx,
                        "target": {"node": joint_idx, "path": path},
                    })

        if channels:
            builder.animations.append({"name": name, "samplers": samplers, "channels": channels})


# ---------------------------------------------------------------------------
# Top-level export
# ---------------------------------------------------------------------------

def _discover_animation_paths(gr2_path: str) -> list[str]:
    """Two layouts exist on disk for where a model's animations live (checked across
    200 real MODEL.GR2 files): ~83% keep *.ANIMATION.GR2 siblings right next to the
    model (e.g. Assassin.MODEL.GR2 next to Idle.ANIMATION.GR2, ...), but ~4% (e.g.
    WereRat) instead keep only a couple of model-specific clips (Walk) alongside the
    model and put the rest in a `Shared Animations` directory one level up, shared
    across that model's variant folders -- confirmed by cross-referencing the
    Characters/*.mdl16 manifest's animation path list against what's actually on disk
    (see docs/adding-a-new-character.md). Check both."""
    model_dir = Path(gr2_path).parent
    seen: set[str] = set()
    paths: list[str] = []
    # The depth from the model file up to its "Shared Animations" sibling isn't
    # consistent: WereRat.MODEL.GR2 lives at .../Wererats/Models/Wererat/, two levels
    # below .../Wererats/Shared Animations/ (an extra "Models" layer), but
    # BlackWolf.MODEL.GR2 lives at .../Wolves/Black Wolf/, only one level below
    # .../Wolves/Shared Animations/ (no "Models" layer) -- walk up looking for it
    # instead of assuming a fixed depth.
    search_dirs = [model_dir]
    ancestor = model_dir
    for _ in range(3):
        ancestor = ancestor.parent
        search_dirs.append(ancestor / "Shared Animations")
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for pattern in ("*.ANIMATION.GR2", "*.ANIMATION.gr2"):
            for p in sorted(directory.glob(pattern)):
                if str(p) not in seen:
                    seen.add(str(p))
                    paths.append(str(p))
    return paths


def export_model(gr2_path: str, out_path: str, anim_paths: list[str] | None = None) -> None:
    """anim_paths: explicit list of .ANIMATION.GR2 files to attach as glTF animation
    clips. If None (the default), auto-discovers them -- see
    _discover_animation_paths. Pass [] to skip animation export entirely."""
    gfile = gf.GrannyFile.load_from_file(gr2_path)
    models_field = field(gfile.root_elements, "Models")
    if models_field is None or not models_field.value:
        raise ValueError("no Models found in this .gr2 file")

    if anim_paths is None:
        anim_paths = _discover_animation_paths(gr2_path)

    builder = GltfBuilder()

    for model_fields in models_field.value:
        skeleton_field = field(model_fields, "Skeleton")
        if skeleton_field is None or not skeleton_field.value:
            raise ValueError(f"Model {field(model_fields, 'Name').value!r} has no Skeleton "
                              f"(some props/gizmos are meshless or use a different rig scheme)")
        skeleton = skeleton_field.value
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
        for mb_record in _group_repeated_records(mesh_bindings.value if mesh_bindings else [], ["Mesh"]):
            mesh_elem = field(mb_record, "Mesh").value
            mesh_idx = export_mesh(builder, mesh_elem, bone_name_to_joint, joint_base)
            node_idx = len(builder.nodes)
            builder.nodes.append({"mesh": mesh_idx, "skin": skin_idx, "_root": True})
            mesh_node_indices.append(node_idx)

        for anim_path in anim_paths:
            clip_name = Path(anim_path).name.split(".")[0]  # "Idle.ANIMATION.GR2" -> "Idle"
            try:
                export_animation(builder, anim_path, bone_name_to_joint, clip_name=clip_name)
            except Exception as ex:
                print(f"warning: skipping animation {anim_path}: {ex}", file=sys.stderr)

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
