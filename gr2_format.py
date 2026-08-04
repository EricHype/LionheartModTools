"""Reader for Granny3D `.gr2` files (Lionheart's character model/animation format).

Python port of `opengr2-rs` (github.com/NoFr1ends/opengr2-rs), a generic/game-agnostic
GR2 container parser. Ported by hand rather than reusing the Rust/C tools directly since
no Rust or C toolchain is available in this environment.

File layout (all confirmed against a real Lionheart file this format was ported for --
see docs, `Resources/Models3D/Enemies/Wererats/Models/Wererat/WereRat.MODEL.GR2`):

    Header (32 bytes)
        16-byte magic (identifies endianness / 32-vs-64-bit / format version)
        size_with_sectors: u32, format: u32, 8 bytes unused
    FileInfo (`file_info_size` bytes, right after the header)
        format_version: i32, total_size: u32, crc32: u32, file_info_size: u32,
        sector_count: u32, type_ref: Reference, root_ref: Reference, tag: u32,
        then padding to file_info_size (40 fixed bytes + padding)
    SectorInfo table (44 bytes * sector_count, right after FileInfo)
        compression_type, data_offset, compressed_length, decompressed_length,
        alignment, oodle_stop_0, oodle_stop_1, fixup_offset, fixup_size,
        marshall_offset, marshall_size (11 x u32 each)
    Each sector's raw bytes are decompressed per `compression_type`, and a separate
    fixup/pointer table is read from the RAW (undecompressed) file at `fixup_offset` --
    `fixup_size` entries of (src_offset, dst_sector, dst_offset), each 3 x u32. This
    table is how pointer/reference fields embedded in a sector's data are resolved:
    the on-disk value at a pointer field is a placeholder: the *fixup table* holds the
    real (sector, offset) destination.

    The root element tree is then walked starting at `root_ref` (data) / `type_ref`
    (type describing that data), recursively, fully self-describing: every field's name
    is stored as a string in a sector, referenced by a fixup-resolved pointer.
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field as _field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

# (magic bytes, big_endian, extra_16 [i.e. Format 7], bits_64)
_MAGICS: list[tuple[bytes, bool, bool, bool]] = [
    (bytes([0xB8, 0x67, 0xB0, 0xCA, 0xF8, 0x6D, 0xB1, 0x0F, 0x84, 0x72, 0x8C, 0x7E, 0x5E, 0x19, 0x00, 0x1E]), False, False, False),  # LE 32-bit Format 6
    (bytes([0xCA, 0xB0, 0x67, 0xB6, 0x0F, 0xB1, 0xDB, 0xF8, 0x7E, 0x8C, 0x72, 0x84, 0x1E, 0x00, 0x19, 0x5E]), True, False, False),  # BE 32-bit Format 6
    (bytes([0x29, 0xDE, 0x6C, 0xC0, 0xBA, 0xA4, 0x53, 0x2B, 0x25, 0xF5, 0xB7, 0xA5, 0xF6, 0x66, 0xE2, 0xEE]), False, True, False),  # LE 32-bit Format 7
    (bytes([0xE5, 0x9B, 0x49, 0x5E, 0x6F, 0x63, 0x1F, 0x14, 0x1E, 0x13, 0xEB, 0xA9, 0x90, 0xBE, 0xED, 0xC4]), False, True, True),  # LE 64-bit Format 7
    (bytes([0xB5, 0x95, 0x11, 0x0E, 0x4B, 0xB5, 0xA5, 0x6A, 0x50, 0x28, 0x28, 0xEB, 0x04, 0xB3, 0x78, 0x25]), True, True, False),  # BE 32-bit Format 7
    (bytes([0xE3, 0xD4, 0x95, 0x31, 0x62, 0x4F, 0xDC, 0x20, 0x3A, 0xD0, 0x36, 0xCC, 0x89, 0xFF, 0x82, 0xB1]), True, True, True),  # BE 64-bit Format 7
]


@dataclass
class Header:
    big_endian: bool
    extra_16: bool
    bits_64: bool
    size: int
    format: int


def parse_header(data: bytes) -> tuple[Header, int]:
    """Returns (Header, bytes_consumed=32)."""
    for magic, big_endian, extra_16, bits_64 in _MAGICS:
        if data[:16] == magic:
            endian = ">" if big_endian else "<"
            size, fmt = struct.unpack_from(endian + "II", data, 16)
            return Header(big_endian, extra_16, bits_64, size, fmt), 32
    raise ValueError(f"Unrecognized GR2 magic: {data[:16].hex()}")


# ---------------------------------------------------------------------------
# Reference / Pointer
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Reference:
    sector: int
    position: int


def parse_reference(data: bytes, offset: int, endian: str) -> tuple[Reference, int]:
    sector, position = struct.unpack_from(endian + "II", data, offset)
    return Reference(sector, position), offset + 8


@dataclass(frozen=True)
class Pointer:
    src_offset: int
    dst_sector: int
    dst_offset: int


def parse_pointer(data: bytes, offset: int, endian: str) -> tuple[Pointer, int]:
    src_offset, dst_sector, dst_offset = struct.unpack_from(endian + "III", data, offset)
    return Pointer(src_offset, dst_sector, dst_offset), offset + 12


# ---------------------------------------------------------------------------
# FileInfo
# ---------------------------------------------------------------------------

@dataclass
class FileInfo:
    format_version: int
    total_size: int
    crc32: int
    file_info_size: int
    sector_count: int
    type_ref: Reference
    root_ref: Reference
    tag: int


def parse_file_info(data: bytes, offset: int, endian: str) -> tuple[FileInfo, int]:
    start = offset
    format_version, total_size, crc32, file_info_size, sector_count = struct.unpack_from(
        endian + "iIIII", data, offset
    )
    offset += 20
    type_ref, offset = parse_reference(data, offset, endian)
    root_ref, offset = parse_reference(data, offset, endian)
    (tag,) = struct.unpack_from(endian + "I", data, offset)
    offset += 4
    assert offset - start == 40
    offset = start + file_info_size  # skip any trailing padding
    return FileInfo(format_version, total_size, crc32, file_info_size, sector_count, type_ref, root_ref, tag), offset


# ---------------------------------------------------------------------------
# SectorInfo
# ---------------------------------------------------------------------------

_SECTOR_INFO_FIELDS = [
    "compression_type", "data_offset", "compressed_length", "decompressed_length",
    "alignment", "oodle_stop_0", "oodle_stop_1", "fixup_offset", "fixup_size",
    "marshall_offset", "marshall_size",
]


@dataclass
class SectorInfo:
    compression_type: int
    data_offset: int
    compressed_length: int
    decompressed_length: int
    alignment: int
    oodle_stop_0: int
    oodle_stop_1: int
    fixup_offset: int
    fixup_size: int
    marshall_offset: int
    marshall_size: int


def parse_sector_info(data: bytes, offset: int, endian: str) -> tuple[SectorInfo, int]:
    values = struct.unpack_from(endian + "11I", data, offset)
    return SectorInfo(*values), offset + 44


# ---------------------------------------------------------------------------
# Sector loading (decompression + fixup table)
# ---------------------------------------------------------------------------

class CompressionType:
    NONE = 0
    OODLE0 = 1
    OODLE1 = 2
    BITKNIT1 = 3
    BITKNIT2 = 4


def decompress_sector(raw_file: bytes, info: SectorInfo) -> bytes:
    sector_bytes = raw_file[info.data_offset:info.data_offset + info.compressed_length]

    if info.compression_type == CompressionType.NONE:
        return sector_bytes

    if info.compression_type in (CompressionType.OODLE0, CompressionType.OODLE1):
        # The C reference (opengr2-c/gr2_read.c) routes both OODLE0 and OODLE1 through
        # the same Compression_UnOodle1 decoder -- "Oodle0" was never a distinct shipped
        # algorithm (its would-be handler is #if 0'd out in the reference source).
        from gr2_oodle1 import oodle1_decompress
        result = oodle1_decompress(raw_file, info.data_offset, info.decompressed_length,
                                    info.oodle_stop_0, info.oodle_stop_1)
        if len(result) != info.decompressed_length:
            raise ValueError(
                f"Oodle1 decompression produced {len(result)} bytes, "
                f"expected {info.decompressed_length}"
            )
        return result

    raise NotImplementedError(f"Unsupported compression type {info.compression_type}")


@dataclass
class Sector:
    info: SectorInfo
    data: bytes
    pointer_table: dict[int, Pointer] = _field(default_factory=dict)

    def resolve_pointer(self, offset: int) -> Optional[Pointer]:
        return self.pointer_table.get(offset)


def load_sector(raw_file: bytes, endian: str, info: SectorInfo) -> Sector:
    data = decompress_sector(raw_file, info)

    pointer_table: dict[int, Pointer] = {}
    offset = info.fixup_offset
    for _ in range(info.fixup_size):
        ptr, offset = parse_pointer(raw_file, offset, endian)
        pointer_table[ptr.src_offset] = ptr

    return Sector(info=info, data=data, pointer_table=pointer_table)


# ---------------------------------------------------------------------------
# Element tree (self-describing)
# ---------------------------------------------------------------------------

@dataclass
class TypeInfo:
    type_id: int
    name_offset: Optional[Pointer]
    children_offset: Optional[Pointer]
    array_size: int


@dataclass
class Element:
    name: str
    kind: str  # 'reference' | 'array_of_references' | 'variant_reference' | 'string'
               # | 'f32' | 'u8' | 'i32' | 'transform' | 'array'
    value: object


def _read_unsigned(data: bytes, offset: int, bits_64: bool, endian: str) -> tuple[int, int]:
    if bits_64:
        (val,) = struct.unpack_from(endian + "Q", data, offset)
        return val, offset + 8
    (val,) = struct.unpack_from(endian + "I", data, offset)
    return val, offset + 4


def _parse_cstring(data: bytes, offset: int) -> str:
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("utf-8")


def parse_type_info(type_sector: Sector, offset: int, bits_64: bool, endian: str) -> tuple[TypeInfo, int]:
    ptr_size = 8 if bits_64 else 4
    (type_id,) = struct.unpack_from(endian + "I", type_sector.data, offset)
    array_size_offset = offset + 4 + ptr_size + ptr_size
    (array_size,) = struct.unpack_from(endian + "i", type_sector.data, array_size_offset)

    name_offset = type_sector.resolve_pointer(offset + 4)
    children_offset = type_sector.resolve_pointer(offset + (12 if bits_64 else 8))

    consumed = 4 + ptr_size + ptr_size + 4
    padding = 20 if bits_64 else 16
    return TypeInfo(type_id, name_offset, children_offset, array_size), offset + consumed + padding


def _resolve_name(sectors: list[Sector], name_ptr: Optional[Pointer]) -> str:
    if name_ptr is None:
        return ""
    return _parse_cstring(sectors[name_ptr.dst_sector].data, name_ptr.dst_offset)


def parse_element(sectors: list[Sector], data_sector_id: int, type_sector_id: int,
                   data_offset: int, type_offset: int, bits_64: bool, endian: str) -> tuple[list[Element], int]:
    type_sector = sectors[type_sector_id]

    elements: list[Element] = []

    while True:
        type_info, type_offset = parse_type_info(type_sector, type_offset, bits_64, endian)
        if type_info.type_id == 0 or type_info.type_id > 22:
            break

        name = _resolve_name(sectors, type_info.name_offset)

        if type_info.array_size > 0:
            inners = []
            for _ in range(type_info.array_size):
                value, data_offset = _parse_element_data(
                    sectors, data_sector_id, data_offset, type_info, bits_64, endian
                )
                inners.append(value)
            elements.append(Element(name, "array", inners))
        else:
            value, data_offset = _parse_element_data(
                sectors, data_sector_id, data_offset, type_info, bits_64, endian
            )
            elements.append(Element(name, value[0], value[1]))

    return elements, data_offset


def _parse_element_data(sectors: list[Sector], data_sector_id: int, data_offset: int,
                         type_info: TypeInfo, bits_64: bool, endian: str) -> tuple[tuple[str, object], int]:
    data_sector = sectors[data_sector_id]
    data = data_sector.data
    ptr_size = 8 if bits_64 else 4
    type_id = type_info.type_id

    if type_id == 1:
        return ("variant_reference", None), data_offset

    if type_id == 2:
        pos = data_offset
        data_offset += ptr_size

        ptr = data_sector.resolve_pointer(pos)
        elements: list[Element] = []
        if ptr is not None:
            assert ptr.dst_sector == data_sector_id
            children = type_info.children_offset
            elements, _ = parse_element(
                sectors, ptr.dst_sector, children.dst_sector, ptr.dst_offset, children.dst_offset, bits_64, endian
            )
        return ("reference", elements), data_offset

    if type_id == 3:
        pos = data_offset + 4
        (size,) = struct.unpack_from(endian + "I", data, data_offset)
        data_offset += 4 + ptr_size

        elements = []
        data_ptr = data_sector.resolve_pointer(pos)
        if size > 0 and data_ptr is not None:
            type_ptr = type_info.children_offset
            inner_data_sector = sectors[data_ptr.dst_sector]
            inner_data_offset = data_ptr.dst_offset
            for _ in range(size):
                e, inner_data_offset = parse_element(
                    sectors, data_ptr.dst_sector, type_ptr.dst_sector,
                    inner_data_offset, type_ptr.dst_offset, bits_64, endian
                )
                elements.extend(e)
        return ("reference", elements), data_offset

    if type_id == 4:
        pos = data_offset + 4
        (size,) = struct.unpack_from(endian + "I", data, data_offset)
        data_offset += 4 + ptr_size

        ptr = data_sector.resolve_pointer(pos)
        references = []
        if ptr is not None:
            type_ptr = type_info.children_offset
            element_data_sector = sectors[ptr.dst_sector]
            for i in range(size):
                element_ptr = element_data_sector.resolve_pointer(ptr.dst_offset + ptr_size * i)
                e, _ = parse_element(
                    sectors, element_ptr.dst_sector, type_ptr.dst_sector,
                    element_ptr.dst_offset, type_ptr.dst_offset, bits_64, endian
                )
                references.append(e)
        return ("array_of_references", references), data_offset

    if type_id == 5:
        data_offset += ptr_size + ptr_size
        return ("variant_reference", None), data_offset

    if type_id == 7:
        pos = data_offset
        (size,) = struct.unpack_from(endian + "I", data, data_offset + ptr_size)
        data_offset += ptr_size + 4 + ptr_size

        type_ptr = data_sector.resolve_pointer(pos)
        data_ptr = data_sector.resolve_pointer(pos + ptr_size + 4)

        elements = []
        inner_data_offset = data_ptr.dst_offset
        for _ in range(size):
            e, inner_data_offset = parse_element(
                sectors, data_ptr.dst_sector, type_ptr.dst_sector,
                inner_data_offset, type_ptr.dst_offset, bits_64, endian
            )
            elements.append(e)
        return ("array_of_references", elements), data_offset

    if type_id == 8:
        pos = data_offset
        data_offset += ptr_size

        ptr = data_sector.resolve_pointer(pos)
        value = _parse_cstring(sectors[ptr.dst_sector].data, ptr.dst_offset)
        return ("string", value), data_offset

    if type_id == 9:
        flags, = struct.unpack_from(endian + "I", data, data_offset)
        translation = struct.unpack_from(endian + "3f", data, data_offset + 4)
        rotation = struct.unpack_from(endian + "4f", data, data_offset + 16)
        scale_shear = struct.unpack_from(endian + "9f", data, data_offset + 32)
        data_offset += 4 + 12 + 16 + 36
        transform = {
            "flags": flags,
            "translation": translation,
            "rotation": rotation,
            "scale_shear": [scale_shear[0:3], scale_shear[3:6], scale_shear[6:9]],
        }
        return ("transform", transform), data_offset

    if type_id == 10:
        (val,) = struct.unpack_from(endian + "f", data, data_offset)
        return ("f32", val), data_offset + 4

    if type_id in (12, 14):
        val = data[data_offset]
        return ("u8", val), data_offset + 1

    if type_id == 19:
        (val,) = struct.unpack_from(endian + "i", data, data_offset)
        return ("i32", val), data_offset + 4

    raise NotImplementedError(f"Unknown element type id {type_id}")


# ---------------------------------------------------------------------------
# Top-level file loader
# ---------------------------------------------------------------------------

@dataclass
class GrannyFile:
    header: Header
    file_info: FileInfo
    sectors: list[Sector]
    root_elements: list[Element]

    @staticmethod
    def load_from_bytes(raw: bytes) -> "GrannyFile":
        header, offset = parse_header(raw)
        endian = ">" if header.big_endian else "<"

        file_info, offset = parse_file_info(raw, offset, endian)

        sectors: list[Sector] = []
        for _ in range(file_info.sector_count):
            info, offset = parse_sector_info(raw, offset, endian)
            sectors.append(load_sector(raw, endian, info))

        root_elements, _ = parse_element(
            sectors,
            file_info.root_ref.sector,
            file_info.type_ref.sector,
            file_info.root_ref.position,
            file_info.type_ref.position,
            header.bits_64,
            endian,
        )

        return GrannyFile(header=header, file_info=file_info, sectors=sectors, root_elements=root_elements)

    @staticmethod
    def load_from_file(path: str) -> "GrannyFile":
        return GrannyFile.load_from_bytes(Path(path).read_bytes())


# ---------------------------------------------------------------------------
# Debug dump
# ---------------------------------------------------------------------------

def dump_elements(elements: list[Element], indent: int = 0, max_depth: int = 12) -> None:
    prefix = "  " * indent
    if indent > max_depth:
        print(prefix + "...")
        return
    for e in elements:
        if e.kind == "reference":
            print(f"{prefix}{e.name} (reference, {len(e.value)} fields)")
            dump_elements(e.value, indent + 1, max_depth)
        elif e.kind == "array_of_references":
            print(f"{prefix}{e.name} (array_of_references, {len(e.value)} items)")
            for i, inner in enumerate(e.value[:5]):
                print(f"{prefix}  [{i}]")
                dump_elements(inner, indent + 2, max_depth)
            if len(e.value) > 5:
                print(f"{prefix}  ... ({len(e.value) - 5} more)")
        elif e.kind == "array":
            print(f"{prefix}{e.name} (array, {len(e.value)} items)")
        elif e.kind == "string":
            print(f"{prefix}{e.name} = {e.value!r}")
        elif e.kind == "transform":
            t = e.value
            print(f"{prefix}{e.name} = Transform(translation={t['translation']})")
        else:
            print(f"{prefix}{e.name} = {e.value!r} ({e.kind})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python gr2_format.py <file.gr2>")
        sys.exit(1)

    gf = GrannyFile.load_from_file(sys.argv[1])
    print(f"header: {gf.header}")
    print(f"file_info: {gf.file_info}")
    print(f"sectors: {len(gf.sectors)}")
    for i, s in enumerate(gf.sectors):
        print(f"  sector {i}: compression_type={s.info.compression_type} "
              f"decompressed_length={s.info.decompressed_length} actual={len(s.data)} "
              f"fixups={s.info.fixup_size}")
    print("root elements:")
    dump_elements(gf.root_elements)
