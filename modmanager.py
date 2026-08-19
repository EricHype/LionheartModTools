"""Lionheart mod packaging/install tool.

Mods are lightweight overlays: a mod.json manifest plus a files/ tree containing only
the resource files a mod adds or changes (never a full data.dat copy -- see SKILL.md
for why data.dat itself can't be redistributed and why store-only compression matters).

Registry lives inside the game directory:
    <game-dir>\\data.dat                  live, built file the game reads
    <game-dir>\\data.dat.vanilla.bak      pristine original, created once by `init`
    <game-dir>\\mods\\installed\\<id>\\    installed mod packages
    <game-dir>\\mods\\enabled.json        ordered list of enabled mod ids (last wins on conflict)
    <game-dir>\\data\\                    loose mirror of data.dat's contents -- if present,
                                          this SHADOWS data.dat for any path it contains, so
                                          `build` also syncs every touched file here. See
                                          SKILL.md's "Loose file mirror" section for why this
                                          exists and how it was discovered.
"""
from __future__ import annotations

import argparse
import stat
import json
import os
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
        "loose_dir": game_dir / "data",
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


# A mod source may be a whole git repository -- Lionheart Fixt is one, with the
# mod package at its root. None of this belongs in the installed copy, and .git
# in particular makes the install un-removable: git marks its objects read-only,
# so the rmtree on the next install dies with WinError 5.
_NOT_MOD_CONTENT = (".git", ".gitattributes", ".gitignore", ".github",
                    "__pycache__", ".venv", "docs")


def _force_remove(func, path, _exc):
    """rmtree onerror hook: clear the read-only bit and retry."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


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
        shutil.rmtree(dest, onerror=_force_remove)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(*_NOT_MOD_CONTENT))

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


def _collect_touched(paths: dict, enabled: list, scratch=None, announce: bool = True) -> dict:
    """Map every mod-provided relative path to the mod that wins it (last in load order).

    When `scratch` is given, also copies each file into the scratch tree. Split out so a
    resumed build can recompute the same file list without repacking.
    """
    touched_by: dict[str, str] = {}
    for mod_id in enabled:
        mod_dir = paths["installed_dir"] / mod_id
        manifest = json.loads((mod_dir / "mod.json").read_text(encoding="utf-8"))
        files_dir = mod_dir / "files"
        for rel in manifest["files"]:
            if rel in touched_by and announce:
                print(f"CONFLICT: {rel!r} touched by both {touched_by[rel]!r} and {mod_id!r} "
                      f"-- {mod_id!r} wins (later in load order)")
            if scratch is not None:
                dst = scratch / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(files_dir / rel, dst)
            touched_by[rel] = mod_id
    return touched_by


def _validate_archive(tmp_dat: str) -> None:
    with zipfile.ZipFile(tmp_dat) as zf:
        bad = zf.testzip()
        methods = {zf.getinfo(n).compress_type for n in zf.namelist()}
    if bad is not None or methods != {0}:
        raise SystemExit(
            f"Built archive failed validation (testzip={bad!r}, compress_types={methods}) -- "
            f"leaving existing data.dat untouched."
        )


def _pending_build_is_usable(paths: dict, tmp_dat: str) -> bool:
    """True if a leftover .build.tmp is a complete archive that is still current.

    A build that repacked successfully but failed to swap (the game was reopened during
    the several minutes of repacking, locking data.dat) leaves a perfectly good archive
    behind. Reusing it turns "close the game and rerun" into a rename instead of another
    full repack. Conservative: any mod content newer than the archive means it is stale,
    so fall through to a normal rebuild.
    """
    tmp = Path(tmp_dat)
    if not tmp.exists():
        return False
    try:
        _validate_archive(tmp_dat)
    except (SystemExit, zipfile.BadZipFile, OSError):
        # A build killed partway through leaves a truncated file that is not a readable
        # archive at all (BadZipFile), not merely one that fails validation.
        print(f"Discarding incomplete {tmp.name} from an interrupted run.")
        tmp.unlink(missing_ok=True)
        return False
    built = tmp.stat().st_mtime
    newer = [paths["vanilla_bak"]]
    if paths["enabled_json"].exists():
        newer.append(paths["enabled_json"])
    for p in paths["installed_dir"].rglob("*"):
        if p.is_file():
            newer.append(p)
    stale = [p for p in newer if p.stat().st_mtime > built]
    if stale:
        print(f"Ignoring leftover {tmp.name}: {len(stale)} file(s) changed since it was built.")
        return False
    return True


def _finalize_build(paths: dict, tmp_dat: str, touched_by: dict, scratch=None) -> None:
    """Swap the built archive into place and sync the loose mirror.

    The game must not be running: data.dat is locked while it is open, and os.replace
    fails with WinError 5. That check happens here, immediately before the swap, rather
    than only at the start of the build -- a repack takes minutes, which is ample time to
    launch the game and lose the whole run.
    """
    if _is_game_running():
        raise SystemExit(
            f"Lionheart.exe is running -- cannot replace data.dat while the game has it open.\n"
            f"The finished archive is kept at {tmp_dat}\n"
            f"Close the game and rerun `build`; it will finish from that file without repacking."
        )
    try:
        os.replace(tmp_dat, paths["data_dat"])
    except PermissionError as exc:
        raise SystemExit(
            f"Could not replace data.dat ({exc.strerror}).\n"
            f"Something still has it open -- usually Lionheart.exe, sometimes an antivirus "
            f"scan or an open archive viewer.\n"
            f"The finished archive is kept at {tmp_dat}\n"
            f"Close whatever holds the file and rerun `build`; it will finish from that file "
            f"without repacking."
        ) from None

    # This install ships (or has accumulated) a COMPLETE loose mirror of data.dat's
    # contents at <game-dir>\data\ -- confirmed by direct comparison, not assumption:
    # every file the game reads loose (present since the original 2001 install, going
    # by preserved timestamps) is read from there INSTEAD of data.dat, permanently
    # shadowing it. Any path with no pre-existing loose copy gets one written the first
    # time the game reads it via data.dat, which then shadows all FUTURE data.dat
    # changes too. Net effect: rebuilding data.dat alone is silently ineffective for any
    # path that already has (or ever gets) a loose copy -- see SKILL.md's "Loose file
    # mirror" section. Sync every touched path here so `build` alone is sufficient.
    if paths["loose_dir"].is_dir():
        synced = 0
        with zipfile.ZipFile(paths["data_dat"]) as zf:
            for rel in touched_by:
                dst = paths["loose_dir"] / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if scratch is not None and (scratch / rel).exists():
                    shutil.copy2(scratch / rel, dst)
                else:
                    # Resumed build: the scratch tree is long gone, so take the bytes
                    # from the archive we just installed.
                    dst.write_bytes(zf.read(rel))
                synced += 1
        if synced:
            print(f"Synced {synced} touched file(s) into the loose mirror at {paths['loose_dir']}")


def cmd_build(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    if not paths["vanilla_bak"].exists():
        raise SystemExit("No vanilla backup found -- run `init` first")
    if _is_game_running():
        raise SystemExit("Lionheart.exe is running -- close the game before building")

    enabled = _read_enabled(paths)
    if not enabled:
        print("No mods enabled -- build will just restore vanilla data.dat")

    tmp_dat = str(paths["data_dat"]) + ".build.tmp"
    if _pending_build_is_usable(paths, tmp_dat):
        print("Found a complete archive from an interrupted build -- finishing it "
              "(no repack needed).")
        touched_by = _collect_touched(paths, enabled, scratch=None, announce=False)
        _finalize_build(paths, tmp_dat, touched_by, scratch=None)
        print(f"Built data.dat with {len(enabled)} mod(s) enabled: {', '.join(enabled) or '(none)'}")
        return

    scratch = paths["scratch_dir"]
    if scratch.exists():
        shutil.rmtree(scratch)
    archive.unpack(str(paths["vanilla_bak"]), str(scratch))

    touched_by = _collect_touched(paths, enabled, scratch=scratch)

    archive.repack(str(scratch), tmp_dat, compression="store")
    try:
        _validate_archive(tmp_dat)
    except SystemExit as exc:
        raise SystemExit(f"{exc} Scratch dir left at {scratch} for inspection.") from None

    _finalize_build(paths, tmp_dat, touched_by, scratch=scratch)

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

    # Restoring data.dat alone is NOT enough. The loose mirror at <game-dir>\data\
    # shadows data.dat for every path it contains, so leaving modded files there means
    # the game still loads mod content from a supposedly-restored install. Undo every
    # path any installed mod touches: revert to the vanilla copy if there is one, delete
    # it if the mod added the file (no vanilla original to fall back to).
    if not paths["loose_dir"].is_dir():
        return
    touched: set[str] = set()
    if paths["installed_dir"].is_dir():
        for mod_dir in paths["installed_dir"].iterdir():
            files_dir = mod_dir / "files"
            if not files_dir.is_dir():
                continue
            for p in files_dir.rglob("*"):
                if p.is_file():
                    touched.add(p.relative_to(files_dir).as_posix())

    reverted = deleted = 0
    with zipfile.ZipFile(paths["vanilla_bak"]) as zf:
        vanilla = set(zf.namelist())
        for rel in sorted(touched):
            dst = paths["loose_dir"] / rel
            if not dst.exists():
                continue
            if rel in vanilla:
                data = zf.read(rel)
                if dst.read_bytes() != data:
                    dst.write_bytes(data)
                    reverted += 1
            else:
                dst.unlink()
                deleted += 1
    if reverted or deleted:
        print(f"Cleaned the loose mirror: {reverted} file(s) reverted to vanilla, "
              f"{deleted} mod-added file(s) removed")
    else:
        print("Loose mirror was already clean")


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
