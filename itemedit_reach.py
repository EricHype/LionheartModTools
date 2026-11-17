"""Where an item appears: every map, creature, loot table and conversation that gives it.

Changing an item file changes that item everywhere, so before a person edits a Short Sword
they should know it lies in eighteen maps and ten loot tables. This scans every text
resource in the game (and the mod) once -- about two seconds, since the archive is stored
uncompressed -- and indexes every `Key=<resource path>` value against the items and cans
that exist, so one lookup answers "who names this file".

Loot tables are followed one step further: a can that drops the item is itself named by
maps and creatures, and the count of those is what says whether the loot table is live.
Developer test maps (`Levels/Test Maps`, `Levels/Multiplayer`, and maps loose at the top of
`Levels`) are counted apart, because a player never visits them.

For magic, the answer is mostly "it rolls at random": an addition names its addition group,
and it can roll on any base item whose group includes that group, directly or through the
groups it lists as part of itself.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from icontool import BINARY_EXTS, Library
from itemedit_core import ADDITION_EXT, ITEM_EXT, ItemDocument, item_ref

VALUE = re.compile(rb"=[ \t]*([^\r\n=]+?)[ \t]*\r?\n")
GAME_MAP_ROOTS = ("Levels/Test Maps/", "Levels/Multiplayer/")


def resource_ref(rel: str) -> str:
    """How other files name a resource: no `Resources/` prefix, no extension."""
    return item_ref(rel)


def is_test_map(rel: str) -> bool:
    return rel.startswith(GAME_MAP_ROOTS) or rel.count("/") == 1


def kind_of(rel: str) -> str:
    low = rel.lower()
    if low.endswith(".zax"):
        return "test map" if is_test_map(rel) else "map"
    if low.startswith("resources/monster cans/"):
        return "creature"
    if low.endswith(".dialogtree"):
        return "conversation"
    if low.startswith("resources/inventory/") and low.endswith(".can"):
        return "loot table"
    if low.endswith((ITEM_EXT.lower(), ADDITION_EXT.lower())):
        return "item"
    return "other"


def pretty(rel: str) -> str:
    return rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]


@dataclass
class Reach:
    maps: list[str] = field(default_factory=list)
    test_maps: list[str] = field(default_factory=list)
    creatures: list[str] = field(default_factory=list)
    conversations: list[str] = field(default_factory=list)
    loot: list[tuple[str, int]] = field(default_factory=list)   # (table, live users)
    items: list[str] = field(default_factory=list)              # other items naming it
    other: list[str] = field(default_factory=list)
    rolls_on: list[str] = field(default_factory=list)           # magic: base items
    rarity: str = ""
    gets_magic: bool = False                                     # base item: can roll magic

    @property
    def anywhere(self) -> bool:
        return bool(self.maps or self.creatures or self.conversations or
                    any(n for _t, n in self.loot) or self.rolls_on)


class ReachIndex:
    def __init__(self, lib: Library):
        self.lib = lib
        known = {}
        for rel in lib.names():
            low = rel.lower()
            if low.startswith("resources/") and low.endswith(
                    (".can", ITEM_EXT.lower(), ADDITION_EXT.lower())):
                known[resource_ref(rel).lower()] = rel
        self.known = known
        self.users: dict[str, dict[str, int]] = defaultdict(dict)
        for rel in lib.names():
            low = rel.lower()
            if low.endswith(BINARY_EXTS) or not low.startswith(("resources/", "levels/")):
                continue
            data = lib.read(rel)
            if b"Inventory" not in data and not low.endswith(".zax"):
                continue
            seen: dict[str, int] = defaultdict(int)
            for m in VALUE.finditer(data):
                v = m.group(1).decode("latin-1").lower()
                if v in known:
                    seen[v] += 1
            for v, n in seen.items():
                self.users[v][rel] = n
        self._groups = self._read_groups()

    # -- addition groups ---------------------------------------------------------

    def _read_groups(self) -> dict[str, set[str]]:
        """group ref -> the group refs it includes (not yet closed)."""
        out = {}
        for rel in self.lib.names():
            if not rel.lower().endswith(".additiongroup.txt"):
                continue
            ref = rel[len("Resources/"):-len(".AdditionGroup.txt")].lower()
            subs = re.findall(rb"Inventory Addition Group=([^\r\n]+)", self.lib.read(rel))
            out[ref] = {s.decode("latin-1").strip().lower() for s in subs}
        return out

    def closure(self, group: str) -> set[str]:
        group = group.strip().lower()
        seen, todo = set(), [group]
        while todo:
            g = todo.pop()
            if g in seen:
                continue
            seen.add(g)
            todo.extend(self._groups.get(g, ()))
        return seen

    # -- lookups -------------------------------------------------------------------

    def users_of(self, rel: str) -> dict[str, int]:
        return {u: n for u, n in self.users.get(resource_ref(rel).lower(), {}).items()
                if u.lower() != rel.lower()}

    def live_users(self, rel: str, depth: int = 0) -> int:
        """How many real maps and creatures give this loot table, following loot tables
        that name loot tables."""
        n = 0
        for u in self.users_of(rel):
            k = kind_of(u)
            if k in ("map", "creature", "conversation"):
                n += 1
            elif k == "loot table" and depth < 4:
                n += 1 if self.live_users(u, depth + 1) else 0
        return n

    def where(self, doc: ItemDocument) -> Reach:
        r = Reach()
        for u in sorted(self.users_of(doc.rel), key=str.lower):
            k = kind_of(u)
            name = pretty(u)
            if k == "map":
                r.maps.append(name)
            elif k == "test map":
                r.test_maps.append(name)
            elif k == "creature":
                r.creatures.append(name)
            elif k == "conversation":
                r.conversations.append(name)
            elif k == "loot table":
                r.loot.append((name, self.live_users(u)))
            elif k == "item":
                r.items.append(name)
            else:
                r.other.append(name)
        group = (doc.root.get("Inventory Addition Group") or "").strip()
        if doc.is_addition:
            r.rarity = (doc.root.get("Addition Rarity") or "").rsplit("/", 1)[-1]
            if group and not group.lower().endswith("/!none"):
                for rel, ref in ((rel, ref) for ref, rel in self.known.items()
                                 if rel.lower().endswith(ITEM_EXT.lower())):
                    m = re.search(rb"^\tInventory Addition Group=([^\r\n]+)",
                                  self.lib.read(rel), re.M)
                    if m and group.lower() in self.closure(m.group(1).decode("latin-1")):
                        name = re.search(rb"^\tDisplay Name=([^\r\n]*)", self.lib.read(rel), re.M)
                        label = name.group(1).decode("latin-1").strip() if name else pretty(rel)
                        if "/enemy items/" not in rel.lower() and label not in r.rolls_on:
                            r.rolls_on.append(label or pretty(rel))
                r.rolls_on.sort(key=str.lower)
        else:
            r.gets_magic = bool(group) and not group.lower().endswith("/!none")
        return r


def summary(r: Reach, doc: ItemDocument) -> list[str]:
    """Plain sentences for the panel, most important first."""
    out = []
    if doc.is_addition:
        if r.rolls_on:
            shown = ", ".join(r.rolls_on[:6]) + (f" and {len(r.rolls_on) - 6} more"
                                                  if len(r.rolls_on) > 6 else "")
            out.append(f"Rolls at random ({r.rarity or 'any rarity'}) on: {shown}.")
    elif r.gets_magic:
        out.append("Can come with random magic.")
    if r.maps:
        out.append(f"Found in {len(r.maps)} map{'s' * (len(r.maps) != 1)} (lying around, in "
                   "a container or for sale): "
                   + ", ".join(r.maps[:5]) + (" ..." if len(r.maps) > 5 else "") + ".")
    if r.creatures:
        out.append(f"Carried or dropped by {len(r.creatures)} creature type"
                   f"{'s' * (len(r.creatures) != 1)}: " + ", ".join(r.creatures[:4])
                   + (" ..." if len(r.creatures) > 4 else "") + ".")
    if r.conversations:
        out.append("Given in conversation by: " + ", ".join(r.conversations[:4])
                   + (" ..." if len(r.conversations) > 4 else "") + ".")
    live = [(t, n) for t, n in r.loot if n]
    if live:
        out.append(f"In {len(live)} loot table{'s' * (len(live) != 1)}: "
                   + ", ".join(t for t, _n in live[:4]) + (" ..." if len(live) > 4 else "")
                   + ".")
    dead = [t for t, n in r.loot if not n]
    if dead:
        out.append(f"Also in {len(dead)} loot table{'s' * (len(dead) != 1)} that no map or "
                   "creature uses.")
    if r.test_maps:
        out.append(f"Also in {len(r.test_maps)} developer test map"
                   f"{'s' * (len(r.test_maps) != 1)}, which players never visit.")
    if not r.anywhere:
        out.append("Nothing in the game gives this to the player. Use Try it in the game to "
                   "test it, and the map editor to place it.")
    return out
