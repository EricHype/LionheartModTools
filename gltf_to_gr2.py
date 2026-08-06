"""Patch an edited glTF (exported by gr2_to_gltf.py, then edited in Blender with the
SAME vertex count / triangle topology / bone count preserved) back into a valid
Lionheart `.gr2` file.

Strategy (see docs/gr2-format.md's round-trip plan): load the ORIGINAL `.gr2` and keep
its decompressed sector bytes + fixup tables. Only overwrite the specific leaf bytes
(vertex positions/normals/UVs/weights/indices, bone transforms) that changed, at their
known offsets (tracked by gr2_format.py's Element.offset/data_sector_id). The type tree
and fixup tables are never touched -- only sectors that got new content are switched to
compression_type=0 (uncompressed) and the file is repacked around their new size;
untouched sectors are copied byte-for-byte from the original file.

Known gap, not yet solved: FileInfo.crc32 is left as a best-effort placeholder (0).
Whether/how the real granny2.dll validates it on load is not yet confirmed -- needs
investigation (see docs/gr2-format.md) before trusting a patched file in-game.

Usage: python gltf_to_gr2.py <original.gr2> <edited.gltf> <output.gr2>
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import gr2_format as gf
from gr2_to_gltf import field, fields, flat, _group_repeated_records, FLOAT, UNSIGNED_SHORT, UNSIGNED_INT

_COMP_FMT = {FLOAT: "f", UNSIGNED_SHORT: "H", UNSIGNED_INT: "I"}
_TYPE_N = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(path: Path) -> tuple[dict, bytes]:
    """Parse a binary glTF (.glb) container: 12-byte header, then a JSON chunk and
    an optional BIN chunk. Blender's glTF exporter defaults to this single-file form."""
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError(f"{path}: not a .glb file (bad magic {magic!r})")
    offset = 12
    json_chunk = None
    bin_chunk = b""
    while offset < length:
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        chunk_data = data[offset + 8: offset + 8 + chunk_len]
        if chunk_type == 0x4E4F534A:  # 'JSON'
            json_chunk = json.loads(chunk_data.decode("utf-8"))
        elif chunk_type == 0x004E4942:  # 'BIN\0'
            bin_chunk = chunk_data
        offset += 8 + chunk_len
    if json_chunk is None:
        raise ValueError(f"{path}: no JSON chunk found")
    return json_chunk, bin_chunk


def read_gltf_or_glb(path: Path) -> tuple[dict, bytes]:
    if path.suffix.lower() == ".glb":
        return read_glb(path)
    gltf_json = json.loads(path.read_text())
    bin_path = path.parent / gltf_json["buffers"][0]["uri"]
    return gltf_json, bin_path.read_bytes()


def read_accessor(gltf_json: dict, bin_data: bytes, idx: int) -> list:
    acc = gltf_json["accessors"][idx]
    bv = gltf_json["bufferViews"][acc["bufferView"]]
    n = _TYPE_N[acc["type"]]
    fmt = "<%d%s" % (acc["count"] * n, _COMP_FMT[acc["componentType"]])
    flat_vals = struct.unpack_from(fmt, bin_data, bv["byteOffset"])
    if n == 1:
        return list(flat_vals)
    return [tuple(flat_vals[i * n:(i + 1) * n]) for i in range(acc["count"])]


def _patch_floats(sector_data: list[bytearray], elem: gf.Element, values: tuple[float, ...]) -> None:
    struct.pack_into("<%df" % len(values), sector_data[elem.data_sector_id], elem.offset, *values)


def _patch_u8(sector_data: list[bytearray], elem: gf.Element, values: tuple[int, ...]) -> None:
    struct.pack_into("<%dB" % len(values), sector_data[elem.data_sector_id], elem.offset, *values)


def patch_model(sector_data: list[bytearray], root_elements: list[gf.Element], gltf_json: dict, bin_data: bytes,
                 touched_sectors: set[int]) -> None:
    models_field = field(root_elements, "Models")
    for model_fields in models_field.value:
        skeleton = field(model_fields, "Skeleton").value
        bones_field = field(skeleton, "Bones")
        bone_records = _group_repeated_records(
            bones_field.value,
            ["Name", "ParentIndex", "Transform", "InverseWorldTransform", "LightInfo", "CameraInfo", "ExtendedData"],
        )

        gltf_node_by_name = {n.get("name"): i for i, n in enumerate(gltf_json["nodes"]) if "name" in n}
        for bone in bone_records:
            name = field(bone, "Name").value
            node_idx = gltf_node_by_name.get(name)
            if node_idx is None:
                continue
            matrix = gltf_json["nodes"][node_idx].get("matrix")
            if matrix is None:
                continue
            transform_elem = field(bone, "Transform")
            # Transform is a single 'transform' leaf (flags+translation+rotation+
            # scale_shear, 68 bytes) -- only translation is cheaply/safely patchable
            # from a plain 4x4 matrix without a full matrix->TRS decomposition.
            # Patch just the translation (bytes 4..16 within the Transform block);
            # rotation/scale edits need a follow-up decomposition step, not done here.
            tx, ty, tz = matrix[12], matrix[13], matrix[14]
            struct.pack_into("<3f", sector_data[transform_elem.data_sector_id], transform_elem.offset + 4, tx, ty, tz)
            touched_sectors.add(transform_elem.data_sector_id)

        mesh_bindings_field = field(model_fields, "MeshBindings")
        for mb_record in _group_repeated_records(mesh_bindings_field.value, ["Mesh"]):
            mesh_elem = field(mb_record, "Mesh").value
            _patch_mesh(sector_data, mesh_elem, gltf_json, bin_data, touched_sectors)


def _patch_mesh(sector_data: list[bytearray], mesh_elem: list[gf.Element], gltf_json: dict, bin_data: bytes,
                 touched_sectors: set[int]) -> None:
    mesh_name = field(mesh_elem, "Name").value
    gltf_mesh = next((m for m in gltf_json["meshes"] if m["name"] == mesh_name), None)
    if gltf_mesh is None:
        print(f"warning: no glTF mesh named {mesh_name!r} found, skipping", file=sys.stderr)
        return
    # v1 scope is same-topology edits split across possibly multiple primitives in
    # the export, but PrimaryVertexData.Vertices was exported as ONE contiguous
    # attribute set shared by all primitives (see gr2_to_gltf.py) -- read it back
    # from the first primitive's attributes, which cover the whole vertex buffer.
    attrs = gltf_mesh["primitives"][0]["attributes"]
    positions = read_accessor(gltf_json, bin_data, attrs["POSITION"])
    normals = read_accessor(gltf_json, bin_data, attrs["NORMAL"])
    texcoords = read_accessor(gltf_json, bin_data, attrs["TEXCOORD_0"])
    weights = read_accessor(gltf_json, bin_data, attrs["WEIGHTS_0"])

    vertex_data = field(mesh_elem, "PrimaryVertexData").value
    vertices = field(vertex_data, "Vertices").value  # list of per-vertex Element lists

    if len(vertices) != len(positions):
        raise ValueError(
            f"vertex count changed ({len(vertices)} -> {len(positions)}) for mesh {mesh_name!r} -- "
            "retopology edits aren't supported by this patch-based importer, see docs/gr2-format.md"
        )

    for v_fields, pos, norm, uv, wts in zip(vertices, positions, normals, texcoords, weights):
        pos_elem = field(v_fields, "Position")
        _patch_floats(sector_data, pos_elem, pos)
        touched_sectors.add(pos_elem.data_sector_id)

        norm_elem = field(v_fields, "Normal")
        _patch_floats(sector_data, norm_elem, norm)
        touched_sectors.add(norm_elem.data_sector_id)

        uv_elem = field(v_fields, "TextureCoordinates0")
        _patch_floats(sector_data, uv_elem, uv)
        touched_sectors.add(uv_elem.data_sector_id)

        wt_elem = field(v_fields, "BoneWeights")
        # BoneWeights is 4 raw u8 bytes on disk (0-255); glTF WEIGHTS_0 gave us
        # normalized 0.0-1.0 floats (see the matching normalization in gr2_to_gltf.py).
        wt_u8 = tuple(max(0, min(255, round(w * 255.0))) for w in wts)
        _patch_u8(sector_data, wt_elem, wt_u8)
        touched_sectors.add(wt_elem.data_sector_id)
        # BoneIndices intentionally not patched: re-skinning (changing which bones
        # influence a vertex) isn't in v1's scope, only reweighting existing ones.


# ---------------------------------------------------------------------------
# Repacking: rebuild header/file_info/sector table around the (possibly resized)
# patched sectors, copying untouched sectors byte-for-byte from the original file.
# ---------------------------------------------------------------------------

def _serialize_fixup_table(pointer_table: dict[int, gf.Pointer], endian: str) -> bytes:
    out = bytearray()
    for src_offset in sorted(pointer_table):
        p = pointer_table[src_offset]
        out += struct.pack(endian + "III", p.src_offset, p.dst_sector, p.dst_offset)
    return bytes(out)


def repack(raw: bytes, header: gf.Header, file_info: gf.FileInfo, sector_infos: list[gf.SectorInfo],
           sectors: list[gf.Sector], sector_data: list[bytearray], touched_sectors: set[int],
           out_path: str) -> None:
    endian = ">" if header.big_endian else "<"

    fixup_blobs = [_serialize_fixup_table(s.pointer_table, endian) for s in sectors]

    new_infos: list[gf.SectorInfo] = []
    for i, info in enumerate(sector_infos):
        if i in touched_sectors:
            new_infos.append(gf.SectorInfo(
                compression_type=0,
                data_offset=0,  # filled in below
                compressed_length=len(sector_data[i]),
                decompressed_length=len(sector_data[i]),
                alignment=info.alignment,
                oodle_stop_0=0,
                oodle_stop_1=0,
                fixup_offset=0,  # filled in below
                fixup_size=info.fixup_size,
                marshall_offset=0,
                marshall_size=info.marshall_size,
            ))
        else:
            new_infos.append(gf.SectorInfo(**vars(info)))

    header_and_table_size = 32 + file_info.file_info_size + len(sector_infos) * 44
    assert header_and_table_size == header.size, "header.size mismatch with recomputed layout"

    offset = header_and_table_size
    for i, info in enumerate(new_infos):
        info.fixup_offset = offset
        offset += len(fixup_blobs[i])
        while offset % 4:
            offset += 1

    data_blobs: list[bytes] = []
    for i, info in enumerate(new_infos):
        if i in touched_sectors:
            data_blobs.append(bytes(sector_data[i]))
        else:
            orig = sector_infos[i]
            data_blobs.append(raw[orig.data_offset:orig.data_offset + orig.compressed_length])
        info.marshall_offset = 0 if info.marshall_size == 0 else offset  # marshalling data not relocated (unused in all files seen so far)

    for i, info in enumerate(new_infos):
        align = max(info.alignment, 1)
        while offset % align:
            offset += 1
        info.data_offset = offset
        offset += len(data_blobs[i])

    total_size = offset

    out = bytearray()
    magic, big_endian, extra_16, bits_64 = next(
        m for m in gf._MAGICS if m[1] == header.big_endian and m[2] == header.extra_16 and m[3] == header.bits_64
    )
    out += magic
    out += struct.pack(endian + "II", header.size, header.format)
    out += b"\x00" * 8

    out += struct.pack(
        endian + "iIIII", file_info.format_version, total_size, 0, file_info.file_info_size, file_info.sector_count,
    )
    out += struct.pack(endian + "II", file_info.type_ref.sector, file_info.type_ref.position)
    out += struct.pack(endian + "II", file_info.root_ref.sector, file_info.root_ref.position)
    out += struct.pack(endian + "I", file_info.tag)
    out += b"\x00" * (file_info.file_info_size - 40)

    for info in new_infos:
        out += struct.pack(
            endian + "11I", info.compression_type, info.data_offset, info.compressed_length,
            info.decompressed_length, info.alignment, info.oodle_stop_0, info.oodle_stop_1,
            info.fixup_offset, info.fixup_size, info.marshall_offset, info.marshall_size,
        )

    for i, info in enumerate(new_infos):
        assert len(out) <= info.fixup_offset
        out += b"\x00" * (info.fixup_offset - len(out))
        out += fixup_blobs[i]

    for i, info in enumerate(new_infos):
        assert len(out) <= info.data_offset
        out += b"\x00" * (info.data_offset - len(out))
        out += data_blobs[i]

    assert len(out) == total_size, (len(out), total_size)
    Path(out_path).write_bytes(bytes(out))
    print(f"wrote {out_path} ({total_size} bytes, sectors touched: {sorted(touched_sectors)})")
    print("NOTE: crc32 written as 0 (placeholder) -- not yet confirmed whether/how "
          "granny2.dll validates this on load. See docs/gr2-format.md.")


def patch_gr2(original_gr2_path: str, edited_gltf_path: str, output_gr2_path: str) -> None:
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

    gltf_json, bin_data = read_gltf_or_glb(Path(edited_gltf_path))

    touched_sectors: set[int] = set()
    patch_model(sector_data, root_elements, gltf_json, bin_data, touched_sectors)

    repack(raw, header, file_info, sector_infos, sectors, sector_data, touched_sectors, output_gr2_path)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: python gltf_to_gr2.py <original.gr2> <edited.gltf|.glb> <output.gr2>")
        sys.exit(1)
    patch_gr2(sys.argv[1], sys.argv[2], sys.argv[3])
