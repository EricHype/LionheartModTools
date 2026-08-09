"""Batch-export every character/weapon .MODEL.GR2 (mesh + skeleton + textures +
auto-discovered animations) to glTF under exports/, mirroring their path under
Models3D/. One-off bulk conversion tool, not part of the regular pipeline -- see
docs/adding-a-new-character.md for the per-model workflow this wraps.

Usage: python batch_export_characters.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import gr2_to_gltf as g2g

GAME_RESOURCES_ROOT = Path(
    r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader"
    r"\data\Resources\Models3D"
)
CATEGORIES = ["Enemies", "NPC", "Player Characters", "Weapons"]
OUT_ROOT = Path(__file__).parent / "exports"


def main() -> None:
    models: list[Path] = []
    # A single case-sensitive-looking *.MODEL.GR2 glob is enough: Windows'
    # filesystem is case-insensitive, so a second *.MODEL.gr2 pass (as
    # gr2_to_gltf.py's own _discover_animation_paths also does, harmlessly there
    # since it dedupes explicitly) would match the exact same files again and
    # double every model's work -- confirmed the hard way, this doubled the whole
    # batch to 648 "found" for ~324 real models.
    for category in CATEGORIES:
        cat_dir = GAME_RESOURCES_ROOT / category
        if not cat_dir.is_dir():
            print(f"warning: category dir not found: {cat_dir}", file=sys.stderr)
            continue
        models += sorted(cat_dir.rglob("*.MODEL.GR2"))

    print(f"found {len(models)} models across {CATEGORIES}", flush=True)
    OUT_ROOT.mkdir(exist_ok=True)

    ok = 0
    failed: list[tuple[Path, str]] = []
    t_start = time.time()

    for i, model_path in enumerate(models):
        rel = model_path.relative_to(GAME_RESOURCES_ROOT)
        out_path = (OUT_ROOT / rel).with_suffix("").with_suffix(".gltf")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            g2g.export_model(str(model_path), str(out_path))
            ok += 1
        except Exception as ex:
            failed.append((rel, f"{type(ex).__name__}: {ex}"))

        if (i + 1) % 20 == 0 or i == len(models) - 1:
            elapsed = time.time() - t_start
            print(f"[{i+1}/{len(models)}] ok={ok} failed={len(failed)} "
                  f"elapsed={elapsed:.0f}s avg={elapsed/(i+1):.2f}s/model", flush=True)

    elapsed = time.time() - t_start
    print(f"\nDONE: {ok}/{len(models)} exported, {len(failed)} failed, {elapsed:.0f}s elapsed")
    if failed:
        print(f"\nFailures ({len(failed)}):")
        for rel, err in failed:
            print(f"  {rel}: {err}")
        (OUT_ROOT / "_failures.txt").write_text(
            "\n".join(f"{rel}: {err}" for rel, err in failed), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
