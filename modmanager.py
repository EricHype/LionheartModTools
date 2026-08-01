"""Lionheart mod packaging/install tool.

Mods are lightweight overlays: a mod.json manifest plus a files/ tree containing only
the resource files a mod adds or changes (never a full data.dat copy -- see SKILL.md
for why data.dat itself can't be redistributed and why store-only compression matters).

Registry lives inside the game directory:
    <game-dir>\\data.dat                  live, built file the game reads
    <game-dir>\\data.dat.vanilla.bak      pristine original, created once by `init`
    <game-dir>\\mods\\installed\\<id>\\    installed mod packages
    <game-dir>\\mods\\enabled.json        ordered list of enabled mod ids (last wins on conflict)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import archive

MOD_FORMAT_VERSION = 1


def _game_paths(game_dir: str) -> dict:
    game_dir = Path(game_dir)
    return {
        "game_dir": game_dir,
        "data_dat": game_dir / "data.dat",
        "vanilla_bak": game_dir / "data.dat.vanilla.bak",
        "mods_dir": game_dir / "mods",
        "installed_dir": game_dir / "mods" / "installed",
        "enabled_json": game_dir / "mods" / "enabled.json",
        "scratch_dir": game_dir / "mods" / ".build_scratch",
    }


def _is_game_running() -> bool:
    out = subprocess.run(["tasklist"], capture_output=True, text=True, check=False).stdout
    return "lionheart.exe" in out.lower()


def _read_enabled(paths: dict) -> list[str]:
    if not paths["enabled_json"].exists():
        return []
    return json.loads(paths["enabled_json"].read_text(encoding="utf-8"))


def _write_enabled(paths: dict, ids: list[str]) -> None:
    paths["enabled_json"].write_text(json.dumps(ids, indent=2), encoding="utf-8")


def cmd_init(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    if not paths["data_dat"].exists():
        raise SystemExit(f"No data.dat found at {paths['data_dat']}")

    if paths["vanilla_bak"].exists():
        print(f"Vanilla backup already exists at {paths['vanilla_bak']} (not overwriting)")
    else:
        shutil.copy2(paths["data_dat"], paths["vanilla_bak"])
        print(f"Created vanilla backup: {paths['vanilla_bak']}")

    paths["installed_dir"].mkdir(parents=True, exist_ok=True)
    if not paths["enabled_json"].exists():
        _write_enabled(paths, [])
    print(f"Mods registry ready at {paths['mods_dir']}")


def _diff_files(edited_dir: Path, vanilla_dir: Path) -> list[str]:
    changed = []
    for path in sorted(edited_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(edited_dir).as_posix()
        vanilla_path = vanilla_dir / rel
        if not vanilla_path.exists() or vanilla_path.read_bytes() != path.read_bytes():
            changed.append(rel)
    return changed


def cmd_package(args: argparse.Namespace) -> None:
    edited_dir = Path(args.edited_dir)
    vanilla_dir = Path(args.vanilla_dir)
    changed = _diff_files(edited_dir, vanilla_dir)
    if not changed:
        raise SystemExit("No differences found between edited and vanilla directories")

    mod_dir = Path(args.output_dir) / args.id
    files_dir = mod_dir / "files"
    if mod_dir.exists():
        shutil.rmtree(mod_dir)
    files_dir.mkdir(parents=True)

    for rel in changed:
        dst = files_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(edited_dir / rel, dst)

    manifest = {
        "mod_format_version": MOD_FORMAT_VERSION,
        "id": args.id,
        "name": args.name,
        "version": args.version,
        "author": args.author,
        "description": args.description,
        "files": changed,
    }
    (mod_dir / "mod.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Packaged {len(changed)} file(s) into {mod_dir}")
    for rel in changed:
        print(f"  {rel}")


def cmd_install(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    source = Path(args.mod_source)

    if source.suffix == ".zip":
        tmp_extract = paths["mods_dir"] / ".install_tmp"
        if tmp_extract.exists():
            shutil.rmtree(tmp_extract)
        with zipfile.ZipFile(source) as zf:
            zf.extractall(tmp_extract)
        # a zip may contain the mod folder itself, or its contents directly
        candidates = [p for p in tmp_extract.iterdir() if (p / "mod.json").exists()]
        source = candidates[0] if candidates else tmp_extract

    manifest_path = source / "mod.json"
    if not manifest_path.exists():
        raise SystemExit(f"No mod.json found in {source}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("mod_format_version") != MOD_FORMAT_VERSION:
        raise SystemExit(
            f"Unsupported mod_format_version {manifest.get('mod_format_version')!r} "
            f"(expected {MOD_FORMAT_VERSION})"
        )

    mod_id = manifest["id"]
    dest = paths["installed_dir"] / mod_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)

    enabled = _read_enabled(paths)
    if mod_id not in enabled:
        enabled.append(mod_id)
        _write_enabled(paths, enabled)

    print(f"Installed {manifest['name']!r} ({mod_id}) v{manifest['version']}")


def cmd_list(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    enabled = _read_enabled(paths)
    if not paths["installed_dir"].exists() or not any(paths["installed_dir"].iterdir()):
        print("No mods installed.")
        return

    for mod_dir in sorted(paths["installed_dir"].iterdir()):
        manifest_path = mod_dir / "mod.json"
        if not manifest_path.exists():
            continue
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        status = "enabled" if m["id"] in enabled else "disabled"
        order = enabled.index(m["id"]) + 1 if m["id"] in enabled else "-"
        print(f"[{order}] {m['name']} ({m['id']}) v{m['version']} by {m['author']} -- {status}")


def cmd_enable(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    enabled = _read_enabled(paths)
    if args.id in enabled:
        print(f"{args.id} already enabled")
        return
    enabled.append(args.id)
    _write_enabled(paths, enabled)
    print(f"Enabled {args.id}")


def cmd_disable(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    enabled = _read_enabled(paths)
    if args.id not in enabled:
        print(f"{args.id} already disabled")
        return
    enabled.remove(args.id)
    _write_enabled(paths, enabled)
    print(f"Disabled {args.id}")


def cmd_reorder(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    new_order = args.order.split(",")
    current = set(_read_enabled(paths))
    if set(new_order) != current:
        raise SystemExit(f"--order must list exactly the currently enabled mods: {sorted(current)}")
    _write_enabled(paths, new_order)
    print("New load order:", " -> ".join(new_order))


def cmd_build(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    if not paths["vanilla_bak"].exists():
        raise SystemExit("No vanilla backup found -- run `init` first")
    if _is_game_running():
        raise SystemExit("Lionheart.exe is running -- close the game before building")

    enabled = _read_enabled(paths)
    if not enabled:
        print("No mods enabled -- build will just restore vanilla data.dat")

    scratch = paths["scratch_dir"]
    if scratch.exists():
        shutil.rmtree(scratch)
    archive.unpack(str(paths["vanilla_bak"]), str(scratch))

    touched_by: dict[str, str] = {}
    for mod_id in enabled:
        mod_dir = paths["installed_dir"] / mod_id
        manifest = json.loads((mod_dir / "mod.json").read_text(encoding="utf-8"))
        files_dir = mod_dir / "files"
        for rel in manifest["files"]:
            src = files_dir / rel
            dst = scratch / rel
            if rel in touched_by:
                print(f"CONFLICT: {rel!r} touched by both {touched_by[rel]!r} and {mod_id!r} "
                      f"-- {mod_id!r} wins (later in load order)")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            touched_by[rel] = mod_id

    tmp_dat = str(paths["data_dat"]) + ".build.tmp"
    archive.repack(str(scratch), tmp_dat, compression="store")

    with zipfile.ZipFile(tmp_dat) as zf:
        bad = zf.testzip()
        methods = {zf.getinfo(n).compress_type for n in zf.namelist()}
    if bad is not None or methods != {0}:
        raise SystemExit(
            f"Built archive failed validation (testzip={bad!r}, compress_types={methods}) -- "
            f"leaving existing data.dat untouched. Scratch dir left at {scratch} for inspection."
        )

    import os
    os.replace(tmp_dat, paths["data_dat"])
    shutil.rmtree(scratch)
    print(f"Built data.dat with {len(enabled)} mod(s) enabled: {', '.join(enabled) or '(none)'}")


def cmd_restore(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    if not paths["vanilla_bak"].exists():
        raise SystemExit("No vanilla backup found -- run `init` first")
    if _is_game_running():
        raise SystemExit("Lionheart.exe is running -- close the game before restoring")
    shutil.copy2(paths["vanilla_bak"], paths["data_dat"])
    print("Restored vanilla data.dat")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lionheart mod packaging/install tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Set up the mod registry + vanilla backup in a game directory")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("package", help="Diff an edited data dir against vanilla to produce a mod package")
    p.add_argument("edited_dir")
    p.add_argument("vanilla_dir")
    p.add_argument("output_dir")
    p.add_argument("--id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--version", default="1.0.0")
    p.add_argument("--author", default="")
    p.add_argument("--description", default="")
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("install", help="Install a mod package (folder or .zip)")
    p.add_argument("mod_source")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("list", help="List installed mods")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("enable", help="Enable an installed mod")
    p.add_argument("id")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser("disable", help="Disable an installed mod")
    p.add_argument("id")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("reorder", help="Set mod load order (comma-separated ids, last wins conflicts)")
    p.add_argument("order")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_reorder)

    p = sub.add_parser("build", help="Rebuild data.dat from vanilla + enabled mods")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("restore", help="Revert data.dat to the pristine vanilla backup")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_restore)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
