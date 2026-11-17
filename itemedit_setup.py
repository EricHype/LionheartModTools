"""First-run setup and installing, so the item editor needs no terminal.

The editor reads the game's original files from `data.dat.vanilla.bak`, the pristine copy
`modmanager.py` keeps beside `data.dat`. A player who never ran `modmanager.py init` has
no such file, so the editor offers to make it -- under the same rule `modmanager` itself
applies before adopting a `data.dat` as the original: **no mod may be installed.** A
backup taken from a modded `data.dat` would quietly become "the original game", and
every later rebuild would restore the mod.

Both installers leave a trace when they install something:

* `modmanager.py` registers mods under `mods/installed/<id>/mod.json`;
* the PowerShell Mod Manager records each install in `mods/<id>/install-record.json`.

If either is present and there is no backup, the editor refuses and says how to get the
original files back (GOG Galaxy's "Verify / Repair").

Installing the user's mod runs `modmanager.py install`, the same command the docs give,
in a child process so a failure is reported, never fatal to the editor.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from canedit_core import DEFAULT_GAME_DIR, VANILLA_NAME

HERE = Path(__file__).resolve().parent


@dataclass
class GameState:
    game: Path
    ok: bool              # the original files are available to read
    can_backup: bool      # no backup yet, and it is safe to make one now
    message: str          # what to tell the person


def is_game_dir(p: Path) -> bool:
    return (Path(p) / "data.dat").is_file()


def installed_mods(game: Path) -> list[str]:
    """Mods either installer has put into this game."""
    game = Path(game)
    found = []
    inst = game / "mods" / "installed"
    if inst.is_dir():
        found += [d.name for d in inst.iterdir() if (d / "mod.json").is_file()]
    mods = game / "mods"
    if mods.is_dir():
        found += [d.name for d in mods.iterdir()
                  if d.is_dir() and (d / "install-record.json").is_file()]
    return sorted(set(found))


def check_game(game: Path) -> GameState:
    game = Path(game)
    if not is_game_dir(game):
        return GameState(game, False, False,
                         f"{game} is not the Lionheart folder: it has no data.dat.")
    if (game / VANILLA_NAME).is_file():
        return GameState(game, True, False, "")
    mods = installed_mods(game)
    if mods:
        return GameState(game, False, False,
                         "The editor needs a copy of the game's original files, and this "
                         "game already has mods installed (" + ", ".join(mods) + "), so its "
                         "data.dat is not the original any more.\n\nUninstall them with the "
                         "Mod Manager, or use GOG Galaxy's Verify / Repair on Lionheart to "
                         "get the original files back, then start the editor again.")
    size = (game / "data.dat").stat().st_size
    return GameState(game, False, True,
                     "The editor reads the game's original files from a backup copy of "
                     f"data.dat ({size / 2**30:.1f} GB), kept beside it in the game folder. "
                     "There is no backup yet, and no mods are installed, so the current "
                     "data.dat is the original.\n\nMake the backup now? It takes a minute "
                     "and is only done once. Every mod tool in this toolkit uses it.")


def make_backup(game: Path) -> Path:
    """Copy data.dat to data.dat.vanilla.bak, refusing if that is not safe."""
    state = check_game(game)
    if not state.can_backup:
        raise RuntimeError(state.message or "A backup already exists.")
    src, dst = Path(game) / "data.dat", Path(game) / VANILLA_NAME
    tmp = dst.with_name(dst.name + ".partial")
    shutil.copy2(src, tmp)
    with zipfile.ZipFile(tmp) as zf:      # a copy that is not a readable archive is useless
        zf.namelist()
    tmp.replace(dst)
    return dst


def game_running() -> bool:
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True, check=False,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except OSError:
        return False
    return "lionheart.exe" in out.lower()


def install_mod(mod: Path, game: Path, python: str | None = None,
                timeout: float = 900) -> tuple[bool, str]:
    """Run `modmanager.py install <mod> <game>`. Returns (ok, what it printed)."""
    if game_running():
        return False, "Lionheart is running. Close the game first: it locks data.dat."
    cmd = [python or _console_python(), str(HERE / "modmanager.py"), "install", str(mod),
           str(game)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"Could not run the installer: {e}"
    out = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    return p.returncode == 0, out


def _console_python() -> str:
    """The editor may run under pythonw.exe; the installer prints, so use python.exe."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").is_file():
        return str(exe.parent / "python.exe")
    return str(exe)


def default_game() -> Path:
    return Path(DEFAULT_GAME_DIR)
