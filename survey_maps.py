"""Per-map inventory of Lionheart's shipped content, read straight out of an archive.

Every figure in Lionheart Fixt's plan document comes from here, so the plan can be checked
rather than believed (that project lives at https://github.com/EricHype/LionheartFixt).
Point it at `data.dat.vanilla.bak`, never the installed `data.dat`:
the install carries whatever mods are enabled, and measuring it has produced wrong
conclusions in this project before.

    python survey_maps.py "<game dir>/data.dat.vanilla.bak"            # section summary
    python survey_maps.py "<...>" --section "4 Crypt" "7 English Shrine"
    python survey_maps.py "<...>" --csv out.csv                        # every map, all columns
    python survey_maps.py "<...>" --silent                             # maps where nobody speaks

What each column counts, and why that measure:

  ents        CEntityBase -- everything placed, props included.
  convs       CDisplayDialogTreeAction -- conversations the player can open.
  balloons    CDisplayDialogBalloonAction -- floating one-liners, no NPC required.
  trees       distinct .DialogTree files the map opens.
  nodes       dialogue nodes *reachable from this map*, summed over those trees. Trees are
              shared -- a companion carries their Barcelona lines into the Crypt -- so this
              is larger than the count of nodes filed under the act's own folder. Both are
              real; this one answers "how much can a player talk to here".
  gated       replies on those trees behind a skill, faction or gender check.
  quests      distinct Quest= paths the map references.
  gens        CGeneratorAIGroup -- placed enemy groups.
  spawns      sum of "Quantity to generate max" over those groups.
  loot        CInventoryItemGenerator{Basic,CannedList,MixedList,AdditionalMagic}. There is
              no container class in the format; loot is placed as a generator. The
              "...Item" classes are entries inside a list and are deliberately not counted.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

# Test maps and multiplayer are not the shipped campaign; they distort every average.
NOT_CAMPAIGN = {"Test Maps", "Multiplayer", "(root)"}

SECTION_ORDER = [
    "1 Barcelona", "Sewers", "Wilderness Maps", "2 Montserrat", "3 Montaillou",
    "4 Crypt", "5 Nostrodomus", "6 Barcelona Attack", "7 English Shrine", "8 Alamut",
    "(root)", "Global", "Multiplayer", "Test Maps",
]

COLUMNS = ["ents", "convs", "balloons", "trees", "nodes", "gated", "quests",
           "gens", "spawns", "loot"]


def _values(key: str, text: str) -> list[str]:
    return [v.strip() for v in re.findall(rf"^\t*{re.escape(key)}=(.*)$", text, re.M)
            if v.strip()]


def _count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, re.M))


def _dialog_stats(zf: zipfile.ZipFile, lower: dict[str, str], ref: str):
    """(nodes, replies, gated) for a referenced dialogue, or None if it does not exist.

    Depth tracking is not optional: nothing in a .DialogTree is indented, including
    inside the brace blocks embedded in Custom Action, so `Node ID=` occurs both as a
    node header at depth 1 and as a field of an embedded action further down.
    """
    key = f"resources/{ref.strip().replace(chr(92), '/').lower()}.dialogtree"
    real = lower.get(key)
    if real is None:
        return None
    nodes = replies = gated = 0
    depth = 0
    for line in zf.read(real).decode("latin-1").splitlines():
        stripped = line.strip()
        if stripped == "{":
            depth += 1
        elif stripped == "}":
            depth -= 1
        elif depth != 1:
            continue
        elif stripped.startswith("Node ID="):
            nodes += 1
        elif stripped.startswith("Reply Text="):
            replies += 1
        elif stripped.startswith("Requirement=") and stripped != "Requirement=!None":
            gated += 1
    return nodes, replies, gated


def survey(archive: Path) -> list[dict]:
    zf = zipfile.ZipFile(archive)
    names = zf.namelist()
    lower = {n.lower(): n for n in names}
    rows = []
    for name in sorted(n for n in names
                       if n.lower().startswith("levels/") and n.lower().endswith(".zax")):
        parts = name.split("/")
        text = zf.read(name).decode("latin-1")

        trees = sorted(set(_values("Dialog Tree File", text)))
        nodes = replies = gated = missing = 0
        for ref in trees:
            got = _dialog_stats(zf, lower, ref)
            if got is None:
                missing += 1
                continue
            nodes += got[0]
            replies += got[1]
            gated += got[2]

        quests = sorted(set(_values("Quest", text)))
        quantities = [int(v) for v in _values("Quantity to generate max", text)
                      if v.isdigit()]
        rows.append(dict(
            section=parts[1] if len(parts) > 2 else "(root)",
            map=parts[-1][:-4],
            path=name,
            ents=_count(r"=CEntityBase\r?$", text),
            convs=_count(r"=CDisplayDialogTreeAction\r?$", text),
            balloons=_count(r"=CDisplayDialogBalloonAction\r?$", text),
            trees=len(trees),
            missing_trees=missing,
            nodes=nodes,
            replies=replies,
            gated=gated,
            quests=len(quests),
            gens=_count(r"=CGeneratorAIGroup\r?$", text),
            spawns=sum(quantities),
            loot=_count(r"=CInventoryItemGenerator"
                        r"(?:Basic|CannedList|MixedList|AdditionalMagic)\r?$", text),
            quest_paths=";".join(quests),
            tree_paths=";".join(trees),
        ))
    return rows


def print_sections(rows: list[dict]) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["section"]].append(r)
    print(f"{'section':<20}{'maps':>5}{'nodes/map':>11}{'spawns/map':>12}"
          f"{'per node':>10}{'quests':>8}")
    for section in SECTION_ORDER + sorted(set(groups) - set(SECTION_ORDER)):
        g = groups.get(section)
        if not g:
            continue
        nodes = sum(r["nodes"] for r in g)
        spawns = sum(r["spawns"] for r in g)
        quests = len({p for r in g for p in r["quest_paths"].split(";") if p})
        ratio = f"{spawns / nodes:>10.1f}" if nodes else f"{'--':>10}"
        print(f"{section:<20}{len(g):>5}{nodes / len(g):>11.1f}"
              f"{spawns / len(g):>12.1f}{ratio}{quests:>8}")
    live = [r for r in rows if r["section"] not in NOT_CAMPAIGN]
    print(f"\ncampaign: {len(live)} maps, {sum(r['gens'] for r in live)} generators, "
          f"{sum(r['spawns'] for r in live)} declared spawns")


def print_detail(rows: list[dict], sections: list[str]) -> None:
    header = f"{'map':<38}" + "".join(f"{c:>9}" for c in COLUMNS)
    for section in sections:
        g = sorted((r for r in rows if r["section"] == section), key=lambda r: r["map"])
        if not g:
            print(f"\n===== {section}: no such section", file=sys.stderr)
            continue
        print(f"\n===== {section}  ({len(g)} maps)")
        print(header)
        for r in g:
            print(f"{r['map'][:37]:<38}" + "".join(f"{r[c]:>9}" for c in COLUMNS))


def print_silent(rows: list[dict]) -> None:
    """Maps with enemies where nobody says anything at all -- not a line, not a balloon."""
    live = [r for r in rows if r["section"] not in NOT_CAMPAIGN]
    silent = [r for r in live
              if r["convs"] == 0 and r["balloons"] == 0 and r["spawns"] > 0]
    print(f"{len(silent)} silent maps of {len(live)} in the campaign")
    for r in sorted(silent, key=lambda r: -r["spawns"]):
        print(f"{r['spawns']:>6} spawns {r['gens']:>5} gens   {r['section']}/{r['map']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("archive", help="path to data.dat.vanilla.bak (not the live data.dat)")
    ap.add_argument("--section", nargs="+", metavar="NAME",
                    help="print every map in these sections")
    ap.add_argument("--silent", action="store_true",
                    help="list maps with enemies and no dialogue of any kind")
    ap.add_argument("--csv", metavar="PATH", help="write every map and column to a CSV")
    args = ap.parse_args()

    archive = Path(args.archive)
    if not archive.is_file():
        ap.error(f"no archive at {archive}")
    if archive.name.lower() == "data.dat":
        print("warning: that is the live archive, which carries any installed mods. "
              "Measure data.dat.vanilla.bak instead.", file=sys.stderr)

    rows = survey(archive)
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} maps to {args.csv}")
    if args.section:
        print_detail(rows, args.section)
    if args.silent:
        print_silent(rows)
    if not (args.section or args.silent or args.csv):
        print_sections(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
