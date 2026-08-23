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
import hashlib
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import archive
import resourcedelta

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
                    "__pycache__", ".venv", "docs", "dist")


def _force_remove(func, path, _exc):
    """rmtree onerror hook: clear the read-only bit and retry."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _require_manifest_matches_disk(mod_dir: Path, manifest: dict, where: str) -> None:
    """Refuse to proceed when mod.json and files/ disagree about what the mod contains.

    `install` copies the whole files/ tree, but `build` packs only what mod.json lists.
    A file added to files/ without regenerating the manifest therefore installs fine,
    looks present in the installed copy, and is then silently omitted from data.dat --
    which is how a Lionheart Fixt edit shipped a map referencing an attribute file that
    never made it into the game. Nothing anywhere reported a problem; the mod was simply
    wrong in play.

    Cheap to check and impossible to notice by eye, so it is enforced at both ends.
    """
    files_dir = mod_dir / "files"
    on_disk = set()
    if files_dir.is_dir():
        on_disk = {p.relative_to(files_dir).as_posix()
                   for p in files_dir.rglob("*") if p.is_file()}
    listed = set(manifest.get("files", []))
    unlisted, absent = sorted(on_disk - listed), sorted(listed - on_disk)
    if not unlisted and not absent:
        return

    lines = [f"{mod_dir.name}: mod.json does not match files/ ({where})."]
    if unlisted:
        lines.append(f"  {len(unlisted)} file(s) present but NOT listed -- these would be "
                     f"installed and then silently left out of data.dat:")
        lines += [f"      {r}" for r in unlisted]
    if absent:
        lines.append(f"  {len(absent)} file(s) listed but MISSING from files/:")
        lines += [f"      {r}" for r in absent]
    lines.append("  Regenerate the manifest's \"files\" list from files/ and reinstall.")
    raise SystemExit("\n".join(lines))


_INSTALLER_FILES = ("Install.bat", "Uninstall.bat", "Mod Manager.bat",
                    "mod-installer.ps1", "ModManager.ps1", "lh-core.ps1")


def cmd_dist(args: argparse.Namespace) -> None:
    r"""Build the release zip a player can download, unzip and double-click.

    Distinct from `package`, which is the authoring path: `package` diffs an edited data
    tree against vanilla to synthesise a mod folder. `dist` takes a finished mod folder
    and wraps it for release.

    A release ships NO content from the shipped game. A mod that changes an existing file
    normally has to carry the whole file, because the engine reads no patch format -- a
    40-line edit to Crossroads.zax would mean redistributing 1.2 MB of the publisher's
    map. Instead, every file that exists in vanilla ships as a delta against the copy the
    player already owns, and the installer reconstructs it locally. Only genuinely new
    files travel verbatim. For Lionheart Fixt that is 2.2 MB of shipped content reduced
    to 76 KB of delta.

    Every delta is applied and hash-checked here, against the real vanilla bytes, before
    it is written into the archive.
    """
    source = Path(args.mod_source)
    manifest_path = source / "mod.json"
    if not manifest_path.exists():
        raise SystemExit(f"No mod.json found in {source}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _require_manifest_matches_disk(source, manifest, "in the mod source")

    vanilla_path = Path(args.vanilla)
    if not vanilla_path.exists():
        raise SystemExit(f"No vanilla archive at {vanilla_path}")

    installer_dir = Path(__file__).parent / "installer"
    missing = [f for f in _INSTALLER_FILES if not (installer_dir / f).exists()]
    if missing:
        raise SystemExit(f"Installer files missing from {installer_dir}: {missing}")

    stem = f"{manifest['id']}-{manifest['version']}"
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{stem}.zip"
    root = f"{stem}/"

    verbatim, patched = [], []
    deltas: dict[str, str] = {}
    src_bytes = 0

    with zipfile.ZipFile(vanilla_path) as vz:
        vanilla_names = set(vz.namelist())
        for rel in manifest["files"]:
            modded = (source / "files" / rel).read_bytes()
            if rel not in vanilla_names:
                verbatim.append(rel)
                continue
            original = vz.read(rel)
            delta = resourcedelta.make_delta(original, modded)
            # Prove it before shipping it: a delta that does not reproduce the file is
            # worse than shipping the file, because it fails on the player's machine.
            if resourcedelta.apply_delta(original, delta) != modded:
                raise SystemExit(f"Delta for {rel} does not reproduce the modded file")
            deltas[rel] = json.dumps(delta, separators=(",", ":"))
            patched.append(rel)
            src_bytes += len(modded)

    payload = {
        "payload_format_version": 1,
        "verbatim": verbatim,
        "patched": patched,
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in verbatim:
            zf.write(source / "files" / rel, f"{root}files/{rel}")
        for rel in patched:
            zf.writestr(f"{root}patches/{rel}.lhpatch", deltas[rel])
        zf.writestr(f"{root}payload.json", json.dumps(payload, indent=2))
        zf.write(manifest_path, f"{root}mod.json")
        for name in _INSTALLER_FILES:
            zf.write(installer_dir / name, f"{root}{name}")
        readme = source / "dist" / "README.txt"
        if readme.exists():
            zf.write(readme, f"{root}README.txt")

    # Read the archive back and rebuild every file from it exactly as the player's
    # machine will, against the real vanilla bytes. A release that cannot reconstruct
    # itself is the one defect nobody catches until someone reports it.
    with zipfile.ZipFile(zip_path) as zf, zipfile.ZipFile(vanilla_path) as vz:
        names = set(zf.namelist())
        for rel in verbatim:
            entry = f"{root}files/{rel}"
            if entry not in names:
                raise SystemExit(f"Release is missing {rel}")
            if zf.read(entry) != (source / "files" / rel).read_bytes():
                raise SystemExit(f"Release copy of {rel} does not match the source")
        for rel in patched:
            entry = f"{root}patches/{rel}.lhpatch"
            if entry not in names:
                raise SystemExit(f"Release is missing the patch for {rel}")
            rebuilt = resourcedelta.apply_delta(vz.read(rel),
                                                json.loads(zf.read(entry)))
            if rebuilt != (source / "files" / rel).read_bytes():
                raise SystemExit(f"Rebuilding {rel} from the release did not match")
        for name in _INSTALLER_FILES:
            if f"{root}{name}" not in names:
                raise SystemExit(f"Release is missing the installer file {name}")
        leaked = [n for n in names if any(part in n.split("/") for part in _NOT_MOD_CONTENT)]
        if leaked:
            raise SystemExit(f"Release contains non-mod content: {leaked[:5]}")

    delta_bytes = sum(len(d) for d in deltas.values())
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (out_dir / f"{stem}.zip.sha256").write_text(f"{digest}  {zip_path.name}\n",
                                                encoding="ascii", newline="\n")

    print(f"Built {zip_path}  ({zip_path.stat().st_size / 1e6:.2f} MB)")
    print(f"  {len(verbatim)} file(s) shipped verbatim (newly authored)")
    if patched:
        print(f"  {len(patched)} file(s) shipped as deltas: "
              f"{src_bytes / 1024:.1f} KB of shipped game content -> "
              f"{delta_bytes / 1024:.1f} KB "
              f"({100 * (1 - delta_bytes / src_bytes):.1f}% smaller, and none of it theirs)")
    print(f"  verified: every file reconstructs byte-for-byte from the release")
    print(f"  sha256: {digest}")


def _ensure_initialized(paths: dict, expect_sources: dict | None = None,
                        registry_only: bool = False) -> None:
    """Create the registry and vanilla backup if they are missing, but only when safe.

    `init` used to be a separate step the caller had to know about, and it adopted
    whatever data.dat it found as "vanilla" without checking. That is silently
    catastrophic for anyone who had already modded: the baseline is poisoned and
    `restore` faithfully restores them to the mod.

    So: refuse to invent a baseline when mods are already installed, and where the mod
    being installed ships deltas, use their recorded source hashes to actually verify the
    archive is unmodded in the regions that matter, instead of assuming it.
    """
    paths["installed_dir"].mkdir(parents=True, exist_ok=True)
    if not paths["enabled_json"].exists():
        _write_enabled(paths, [])
    if registry_only or paths["vanilla_bak"].exists():
        return

    existing = [d.name for d in paths["installed_dir"].iterdir() if (d / "mod.json").exists()]
    if existing:
        raise SystemExit(
            f"No vanilla backup at {paths['vanilla_bak']}, but these mods are already "
            f"installed: {', '.join(existing)}.\nRefusing to treat the current data.dat as "
            f"vanilla -- it is probably not. Restore the game's original data.dat and rerun."
        )

    if expect_sources:
        with zipfile.ZipFile(paths["data_dat"]) as dz:
            names = set(dz.namelist())
            mismatched = [rel for rel, want in expect_sources.items()
                          if rel not in names
                          or hashlib.sha256(dz.read(rel)).hexdigest() != want]
        if mismatched:
            raise SystemExit(
                f"This data.dat is not the version the mod expects to patch "
                f"({len(mismatched)} of {len(expect_sources)} file(s) differ, e.g. "
                f"{mismatched[0]}).\nIt has probably been modified already. Nothing was "
                f"changed, and no backup was made from it."
            )
        print(f"Verified {len(expect_sources)} file(s) against the mod's expected "
              f"originals -- data.dat looks unmodded.")

    shutil.copy2(paths["data_dat"], paths["vanilla_bak"])
    print(f"Created vanilla backup: {paths['vanilla_bak']}")


def _materialize_payload(source: Path, manifest: dict, paths: dict) -> dict:
    """Return {rel: bytes} for every file the mod provides.

    A release ships no content from the game: files that already exist in the player's
    archive travel as deltas and are reconstructed here from the vanilla backup. A
    development checkout has no payload.json and carries everything verbatim.
    """
    payload_path = source / "payload.json"
    if not payload_path.exists():
        _require_manifest_matches_disk(source, manifest, "in the mod source")
        return {rel: (source / "files" / rel).read_bytes() for rel in manifest["files"]}

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    verbatim, patched = payload.get("verbatim", []), payload.get("patched", [])
    if set(verbatim) | set(patched) != set(manifest["files"]):
        raise SystemExit("payload.json does not describe the same files as mod.json")

    contents = {rel: (source / "files" / rel).read_bytes() for rel in verbatim}
    if not patched:
        return contents

    deltas = {rel: json.loads((source / "patches" / f"{rel}.lhpatch").read_text(encoding="ascii"))
              for rel in patched}
    _ensure_initialized(paths, {rel: d["srcSha256"] for rel, d in deltas.items()})

    with zipfile.ZipFile(paths["vanilla_bak"]) as vz:
        names = set(vz.namelist())
        for rel, delta in deltas.items():
            if rel not in names:
                raise SystemExit(f"Cannot rebuild {rel}: not present in the vanilla archive")
            contents[rel] = resourcedelta.apply_delta(vz.read(rel), delta)
    print(f"Rebuilt {len(patched)} file(s) from the vanilla archive "
          f"({len(verbatim)} shipped verbatim)")
    return contents


def cmd_install(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    if not paths["data_dat"].exists():
        raise SystemExit(f"No data.dat found at {paths['data_dat']}")
    source = Path(args.mod_source)
    cleanup_extract = None

    if source.suffix == ".zip":
        tmp_extract = paths["mods_dir"] / ".install_tmp"
        if tmp_extract.exists():
            shutil.rmtree(tmp_extract, onerror=_force_remove)
        with zipfile.ZipFile(source) as zf:
            zf.extractall(tmp_extract)
        # a zip may contain the mod folder itself, or its contents directly
        candidates = [p for p in tmp_extract.iterdir() if (p / "mod.json").exists()]
        source = candidates[0] if candidates else tmp_extract
        cleanup_extract = tmp_extract

    manifest_path = source / "mod.json"
    if not manifest_path.exists():
        raise SystemExit(f"No mod.json found in {source}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("mod_format_version") != MOD_FORMAT_VERSION:
        raise SystemExit(
            f"Unsupported mod_format_version {manifest.get('mod_format_version')!r} "
            f"(expected {MOD_FORMAT_VERSION})"
        )

    _ensure_initialized(paths, registry_only=True)
    contents = _materialize_payload(source, manifest, paths)
    # A full-form source carries no delta hashes, so there is nothing to verify against;
    # the baseline is whatever data.dat is, which is why the check above matters.
    _ensure_initialized(paths)

    # The installed copy is always full-form, whatever shape the source arrived in, so
    # nothing downstream has to know that deltas exist.
    mod_id = manifest["id"]
    dest = paths["installed_dir"] / mod_id
    if dest.exists():
        shutil.rmtree(dest, onerror=_force_remove)
    (dest / "files").mkdir(parents=True)
    for rel, data in contents.items():
        dst = dest / "files" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    (dest / "mod.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    enabled = _read_enabled(paths)
    if mod_id not in enabled:
        enabled.append(mod_id)
        _write_enabled(paths, enabled)

    if cleanup_extract and cleanup_extract.exists():
        shutil.rmtree(cleanup_extract, onerror=_force_remove)

    print(f"Installed {manifest['name']!r} ({mod_id}) v{manifest['version']} "
          f"-- {len(contents)} file(s)")
    if args.no_build:
        print("Skipped the rebuild (--no-build); run `build` to apply it.")
    else:
        cmd_build(args)


def cmd_uninstall(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    dest = paths["installed_dir"] / args.id
    if not dest.exists():
        raise SystemExit(f"{args.id!r} is not installed")

    manifest = json.loads((dest / "mod.json").read_text(encoding="utf-8"))
    shutil.rmtree(dest, onerror=_force_remove)
    enabled = [m for m in _read_enabled(paths) if m != args.id]
    _write_enabled(paths, enabled)
    print(f"Removed {manifest.get('name', args.id)!r} ({args.id})")

    if args.no_build:
        print("Skipped the rebuild (--no-build); run `build` to apply it.")
    else:
        cmd_build(args)


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
        _require_manifest_matches_disk(mod_dir, manifest, "in the installed copy")
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


def _validate_archive(dat_path: str) -> None:
    with zipfile.ZipFile(dat_path) as zf:
        bad = zf.testzip()
        methods = {zf.getinfo(n).compress_type for n in zf.namelist()}
    if bad is not None or methods != {0}:
        raise SystemExit(
            f"Built archive failed validation (testzip={bad!r}, compress_types={methods})."
        )


def _sync_loose_mirror(paths: dict, touched_by: dict, contents: dict) -> None:
    r"""Keep <game-dir>\data\ in step with the archive, if it exists at all.

    A stock install has no such directory -- the game ships data.dat alone. Where one does
    exist (someone extracted the archive), it SHADOWS data.dat for every path it holds, so
    a rebuild that ignored it would be silently ineffective.
    """
    loose = paths["loose_dir"]
    if not loose.is_dir():
        return

    synced = 0
    for rel, data in contents.items():
        dst = loose / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        synced += 1
    if synced:
        print(f"Synced {synced} file(s) into the loose mirror at {loose}")

    # Anything an installed-but-disabled mod provides has to go back to vanilla, or
    # disabling it would leave its loose copy shadowing the archive and doing nothing.
    provided: set[str] = set()
    if paths["installed_dir"].is_dir():
        for mod_dir in paths["installed_dir"].iterdir():
            mj = mod_dir / "mod.json"
            if mj.exists():
                provided.update(json.loads(mj.read_text(encoding="utf-8")).get("files", []))
    stale = sorted(provided - set(touched_by))
    if not stale:
        return
    reverted = removed = 0
    with zipfile.ZipFile(paths["vanilla_bak"]) as vz:
        vanilla = set(vz.namelist())
        for rel in stale:
            dst = loose / rel
            if not dst.exists():
                continue
            if rel in vanilla:
                data = vz.read(rel)
                if dst.read_bytes() != data:
                    dst.write_bytes(data)
                    reverted += 1
            else:
                dst.unlink()
                removed += 1
    if reverted or removed:
        print(f"Cleaned {reverted + removed} stale loose file(s) from disabled mods "
              f"({reverted} reverted to vanilla, {removed} removed)")


def cmd_build(args: argparse.Namespace) -> None:
    paths = _game_paths(args.game_dir)
    if not paths["vanilla_bak"].exists():
        raise SystemExit("No vanilla backup found -- run `init` first")
    if _is_game_running():
        raise SystemExit("Lionheart.exe is running -- close the game before building")

    enabled = _read_enabled(paths)
    if not enabled:
        print("No mods enabled -- build will just restore vanilla data.dat")

    touched_by = _collect_touched(paths, enabled)
    contents = {rel: (paths["installed_dir"] / mod_id / "files" / rel).read_bytes()
                for rel, mod_id in touched_by.items()}

    written, added = archive.rebuild(str(paths["vanilla_bak"]), str(paths["data_dat"]),
                                     replace=contents)
    _validate_archive(str(paths["data_dat"]))
    print(f"Rebuilt data.dat: {written} entries from vanilla, {added} added by mods")

    _sync_loose_mirror(paths, touched_by, contents)
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

    p = sub.add_parser("dist", help="Build a player-installable release zip from a mod package")
    p.add_argument("mod_source", help="Mod package directory (contains mod.json and files/)")
    p.add_argument("output_dir", help="Where to write <id>-<version>.zip")
    p.add_argument("--vanilla", required=True,
                   help="Pristine data.dat (usually data.dat.vanilla.bak) to diff against")
    p.set_defaults(func=cmd_dist)

    p = sub.add_parser("install",
                       help="Install a mod (folder or .zip), then rebuild data.dat")
    p.add_argument("mod_source")
    p.add_argument("game_dir")
    p.add_argument("--no-build", action="store_true",
                   help="Install without rebuilding data.dat")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall",
                       help="Remove an installed mod, then rebuild data.dat")
    p.add_argument("id")
    p.add_argument("game_dir")
    p.add_argument("--no-build", action="store_true",
                   help="Remove without rebuilding data.dat")
    p.set_defaults(func=cmd_uninstall)

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
    p.add_argument("--no-build", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("restore", help="Revert data.dat to the pristine vanilla backup")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_restore)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
