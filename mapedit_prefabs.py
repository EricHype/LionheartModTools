"""Prefabs for the map editor: groups of entities placed together as one.

Most of what reads as one object on a Lionheart map is several sprites. A door frame
is `DoorLftLeg` + `DoorRtLeg` + `DoorTop`; a lit dwarf wall is `Wall 01 A` +
`Wall light A`. They are split so the game can sort the player between the parts,
and they are placed at one shared point. Assembling them by hand means knowing the
set exists and which letters pair -- and they do not always match: Crypt corner `F`
takes torch `E`.

Two libraries:

  prefabs/vanilla.json   mined from the shipped maps (see `mine`), read-only, in the repo
  ~/.lionheart_mapedit/prefabs.json   the user's own, saved from a selection

Regenerate the starter library against an unpacked *vanilla* archive, never the
install (which carries enabled mods):

    python mapedit_prefabs.py mine .vanilla_unpacked/Levels -o prefabs/vanilla.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mapedit_core import MapDocument, node_anchor
from resource_format import ResourceNode

VANILLA_LIBRARY = Path(__file__).resolve().parent / "prefabs" / "vanilla.json"
USER_LIBRARY = Path.home() / ".lionheart_mapedit" / "prefabs.json"


@dataclass
class Prefab:
    name: str
    folder: str = ""          # palette grouping, e.g. "Crypt Of Vika/Walls/Straight Wall"
    # (dx, dy, node): offset from the placement point and a full entity node to clone.
    pieces: list[tuple[int, int, ResourceNode]] = field(default_factory=list)
    seen: int = 0             # vanilla only: how many times the shipped maps use it
    builtin: bool = False

    @property
    def key(self) -> str:
        return f"{'vanilla' if self.builtin else 'user'}/{self.folder}/{self.name}"

    def to_dict(self) -> dict:
        d = {"name": self.name, "folder": self.folder,
             "pieces": [[dx, dy, n.to_dict()] for dx, dy, n in self.pieces]}
        if self.seen:
            d["seen"] = self.seen
        return d

    @staticmethod
    def from_dict(d: dict, *, builtin: bool) -> "Prefab":
        return Prefab(d["name"], d.get("folder", ""),
                      [(dx, dy, ResourceNode.from_dict(n)) for dx, dy, n in d["pieces"]],
                      d.get("seen", 0), builtin)


def prefab_from_nodes(name: str, nodes: list[ResourceNode], folder: str = "") -> Prefab:
    """A prefab from placed entities, anchored on their rounded centroid -- so placing
    it puts the group's middle under the cursor, as paste does."""
    anchors = [node_anchor(n) for n in nodes]     # areas have no Position X/Y
    xs = [x for x, _ in anchors]
    ys = [y for _, y in anchors]
    cx, cy = round(sum(xs) / len(xs)), round(sum(ys) / len(ys))
    return Prefab(name, folder, [(round(x - cx), round(y - cy), copy.deepcopy(n))
                                 for n, x, y in zip(nodes, xs, ys)])


def load_library(path: Path, *, builtin: bool) -> list[Prefab]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    return [Prefab.from_dict(d, builtin=builtin) for d in data.get("prefabs", [])]


def save_library(path: Path, prefabs: list[Prefab]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"prefabs": [p.to_dict() for p in prefabs]}, indent=1),
                   encoding="utf-8")
    tmp.replace(path)    # a crash mid-write must not eat the user's library


# ---------------------------------------------------------------------------
# Mining the shipped maps
# ---------------------------------------------------------------------------

def mine(levels_root: Path, *, min_seen: int = 2) -> tuple[list[Prefab], int]:
    """Groups of scenery from one asset folder stacked on one exact point, used at
    least `min_seen` times across the shipped maps. Returns (prefabs, maps read).

    One folder only: a candle that happens to sit on a table's hotspot is a
    coincidence, a wall and its own light are an object. Repetition filters the rest.
    """
    found: dict[tuple, list] = {}   # model tuple -> [count, first occurrence's nodes]
    maps = 0
    for path in sorted(Path(levels_root).rglob("*.zax")):
        try:
            doc = MapDocument(path)
        except ValueError:
            continue        # a handful of shipped files do not parse; skip, as survey does
        maps += 1
        at: dict[tuple, list] = {}
        for ent in doc.entities():
            if ent.is_scenery():
                at.setdefault((round(ent.x), round(ent.y), ent.model.rsplit("/", 1)[0]),
                              []).append(ent)
        for ents in at.values():
            if len(ents) < 2:
                continue
            # Keyed on the set, but the nodes keep map order: stacked sprites at one
            # point can draw in file order, and the first author's order is the safe one.
            key = tuple(sorted(e.model for e in ents))
            if key in found:
                found[key][0] += 1
            else:
                found[key] = [1, [e.node for e in ents]]

    prefabs = []
    for key, (count, nodes) in found.items():
        if count < min_seen:
            continue
        folder = key[0].rsplit("/", 1)[0].removeprefix("Environments/")
        name = " + ".join(m.rsplit("/", 1)[1] for m in key)
        p = prefab_from_nodes(name, nodes, folder)
        p.seen, p.builtin = count, True
        for _dx, _dy, node in p.pieces:
            # A few pieces are named ("secret door4") because a script in their own
            # map moves them. Carried into another map the name means nothing, or
            # worse, collides with something that does use it.
            if node.get("Name"):
                node.set("Name", "")
        prefabs.append(p)
    prefabs.sort(key=lambda p: (p.folder.lower(), p.name.lower()))
    return prefabs, maps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mine", help="build the starter library from vanilla Levels/")
    m.add_argument("levels", type=Path)
    m.add_argument("-o", "--out", type=Path, default=VANILLA_LIBRARY)
    m.add_argument("--min-seen", type=int, default=2)
    args = ap.parse_args()

    prefabs, maps = mine(args.levels, min_seen=args.min_seen)
    save_library(args.out, prefabs)
    print(f"{len(prefabs)} prefabs from {maps} maps -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
