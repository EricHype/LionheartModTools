"""Batch-validate gr2_format.py + gr2_granny_decompress.py against every .gr2 file
found under a directory tree. Reports pass/fail counts and groups failures by error
type/message so a handful of examples can be inspected per failure mode.

Usage:
    python scripts/validate_gr2.py [root_dir] [--limit N] [--verbose]

Default root_dir is the Lionheart game's data/Resources folder.
"""
from __future__ import annotations

import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gr2_format as gf

DEFAULT_ROOT = (
    r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader"
    r"\data\Resources"
)


def main() -> None:
    args = sys.argv[1:]
    verbose = "--verbose" in args
    args = [a for a in args if a != "--verbose"]
    limit = None
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1])
        del args[i:i + 2]
    root = Path(args[0]) if args else Path(DEFAULT_ROOT)

    files = sorted(root.rglob("*.gr2")) + sorted(root.rglob("*.GR2"))
    files = sorted(set(files))
    if limit:
        files = files[:limit]

    print(f"Found {len(files)} .gr2 files under {root}")

    ok = 0
    failures: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    start = time.time()

    for n, path in enumerate(files, 1):
        try:
            raw = path.read_bytes()
            gfile = gf.GrannyFile.load_from_bytes(raw)
            if not gfile.root_elements:
                raise ValueError("root_elements is empty")
            ok += 1
        except Exception as e:  # noqa: BLE001 -- deliberately broad for a validator
            kind = type(e).__name__
            msg = str(e)
            key = f"{kind}: {msg}"
            failures[key].append((path, traceback.format_exc() if verbose else ""))

        if n % 100 == 0 or n == len(files):
            elapsed = time.time() - start
            print(f"  [{n}/{len(files)}] ok={ok} fail={n - ok} ({elapsed:.1f}s)")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s. {ok}/{len(files)} loaded successfully.")

    if failures:
        print(f"\n{len(failures)} distinct failure kinds:")
        for key, examples in sorted(failures.items(), key=lambda kv: -len(kv[1])):
            print(f"\n  [{len(examples)}x] {key}")
            for path, tb in examples[:3]:
                print(f"      {path.relative_to(root)}")
                if tb:
                    print("      " + tb.replace("\n", "\n      "))


if __name__ == "__main__":
    main()
