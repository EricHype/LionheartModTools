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
