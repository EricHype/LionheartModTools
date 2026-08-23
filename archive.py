"""Unpack/repack Lionheart's data.dat -- a standard ZIP archive with a renamed extension.

Confirmed via Ghidra/ReVa analysis of Lionheart.exe:
  - It links zlib 1.1.3 for inflate/deflate (unmodified, well-known open-source code).
  - It checks for the standard ZIP end-of-central-directory signature (0x06054b50).
  - It rejects any compression method other than "none" (stored) or its supported
    deflate type, with the error: "...must be created with compression set to none
    or a type of compression supported by the game engine."
  - No CRC/checksum-mismatch string exists anywhere in the binary, so there is no
    extra integrity gate beyond normal ZIP structure -- only use ZIP_STORED or
    ZIP_DEFLATED when repacking.
"""
from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path

_COMPRESSION = {
    "deflate": zipfile.ZIP_DEFLATED,
    "store": zipfile.ZIP_STORED,
}


def unpack(dat_path: str, out_dir: str) -> None:
    with zipfile.ZipFile(dat_path, "r") as zf:
        zf.extractall(out_dir)


def rebuild(src_dat: str, dst_dat: str, replace: dict[str, bytes] | None = None,
            drop: set[str] | None = None) -> tuple[int, int]:
    """Write a new archive from an existing one, substituting and adding entries.

    Returns (entries written, entries added).

    This exists because unpack-then-repack is the wrong shape for the job. Building
    data.dat from vanilla plus a handful of mod files does not require materialising
    19,030 files on disk and reading them all back; it requires copying a stream and
    swapping a few entries out of it. The scratch tree cost several minutes and ~3.2 GB
    of I/O per build, on a workload that is inherently a single pass.

    Always stored, never deflated -- see the module docstring for why the game insists.
    """
    replace = replace or {}
    drop = drop or set()
    written = added = 0
    tmp_path = f"{dst_dat}.tmp"

    with zipfile.ZipFile(src_dat, "r") as src, \
            zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as out:
        seen = set()
        for info in src.infolist():
            if info.filename in drop:
                continue
            seen.add(info.filename)
            data = replace.get(info.filename)
            if data is None:
                data = src.read(info.filename)
            # Carry the original timestamp so untouched entries stay identical to the
            # source in every field a comparison might look at.
            new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            new_info.compress_type = zipfile.ZIP_STORED
            new_info.external_attr = info.external_attr
            out.writestr(new_info, data)
            written += 1
        for name in replace:
            if name in seen:
                continue
            out.writestr(name, replace[name])
            added += 1

    os.replace(tmp_path, dst_dat)
    return written, added


def repack(in_dir: str, dat_path: str, compression: str = "deflate") -> None:
    compress_type = _COMPRESSION[compression]
    in_dir = Path(in_dir)
    tmp_path = f"{dat_path}.tmp"
    with zipfile.ZipFile(tmp_path, "w", compression=compress_type) as zf:
        for file_path in sorted(in_dir.rglob("*")):
            if file_path.is_file():
                arcname = file_path.relative_to(in_dir).as_posix()
                zf.write(file_path, arcname, compress_type=compress_type)
    os.replace(tmp_path, dat_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Unpack/repack Lionheart's data.dat archive")
    sub = parser.add_subparsers(dest="command", required=True)

    p_unpack = sub.add_parser("unpack", help="Extract data.dat into a directory")
    p_unpack.add_argument("dat_path")
    p_unpack.add_argument("out_dir")

    p_repack = sub.add_parser("repack", help="Rebuild data.dat from a directory")
    p_repack.add_argument("in_dir")
    p_repack.add_argument("dat_path")
    p_repack.add_argument("--compression", choices=list(_COMPRESSION), default="deflate")

    args = parser.parse_args()
    if args.command == "unpack":
        unpack(args.dat_path, args.out_dir)
    else:
        repack(args.in_dir, args.dat_path, args.compression)


if __name__ == "__main__":
    main()
