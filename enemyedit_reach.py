"""Where a creature appears: every spawner group that names it, and at what party strength.

Tough and Super versions are separate creatures, and maps choose between them with
spawner groups (`CGeneratorAIGroup`), each with a `Max Party Mojo`, the party strength it
is used up to, and its own quantity. A wasp spawner at Crossroads makes 1 Wasp up to
strength 2, 2-3 Wasp Tough up to 10, and 3-4 Wasp Super up to 25 (docs/enemy-editor-e0.md).
So "where does this creature appear" is a list of those groups.

One pass over every map (the archive is stored uncompressed, so about two seconds) reads
the groups line by line: a group resets the strength and quantity, and each `Entity=` under
it is recorded with them. Other files that create the creature by name (scripts, cans) are
counted too. Developer test maps are kept apart, as in the item editor.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from icontool import BINARY_EXTS, Library
from itemedit_reach import is_test_map, pretty

GROUP = re.compile(rb"^\s*Group=CGeneratorAIGroup\s*$")
VALUE = re.compile(rb"^\s*([^=\r\n]+?)=([^\r\n]*)$")


@dataclass
class Spawn:
    map: str           # map file stem
    test: bool
    mojo: str          # Max Party Mojo of the group, "" outside a group
    qmin: str
    qmax: str


@dataclass
class Appearances:
    spawns: list[Spawn] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)     # other files that create it by name

    @property
    def game_spawns(self) -> list[Spawn]:
        return [s for s in self.spawns if not s.test]

    @property
    def maps(self) -> list[str]:
        return sorted({s.map for s in self.game_spawns}, key=str.lower)

    @property
    def test_maps(self) -> list[str]:
        return sorted({s.map for s in self.spawns if s.test}, key=str.lower)


class SpawnIndex:
    def __init__(self, lib: Library):
        self.lib = lib
        known = {n[len("Resources/"):-len(".can")].lower()
                 for n in lib.names()
                 if n.startswith("Resources/Monster Cans/") and n.lower().endswith(".can")}
        self.by_can: dict[str, Appearances] = defaultdict(Appearances)
        for rel in lib.names():
            low = rel.lower()
            if low.endswith(BINARY_EXTS) or not low.startswith(("levels/", "resources/")):
                continue
            data = lib.read(rel)
            if b"Monster Cans/" not in data:
                continue
            if low.endswith(".zax"):
                self._scan_map(rel, data, known)
            elif not low.startswith("resources/monster cans/"):
                seen = set()
                for m in re.finditer(rb"=\s*(Monster Cans/[^\r\n]+?)\s*\r?\n", data):
                    ref = m.group(1).decode("latin-1").lower()
                    if ref in known and ref not in seen:
                        seen.add(ref)
                        self.by_can[ref].scripts.append(pretty(rel))

    def _scan_map(self, rel: str, data: bytes, known: set):
        name, test = pretty(rel), is_test_map(rel)
        mojo = qmin = qmax = ""
        for line in data.split(b"\n"):
            line = line.rstrip(b"\r")      # maps are CRLF; the value pattern ends at $
            if GROUP.match(line):
                mojo = qmin = qmax = ""
                continue
            m = VALUE.match(line)
            if not m:
                continue
            key, val = m.group(1).strip(), m.group(2).strip()
            if key == b"Max Party Mojo":
                mojo = val.decode("latin-1")
            elif key == b"Quantity to generate min":
                qmin = val.decode("latin-1")
            elif key == b"Quantity to generate max":
                qmax = val.decode("latin-1")
            elif val.startswith(b"Monster Cans/"):
                ref = val.decode("latin-1").lower()
                if ref in known:
                    self.by_can[ref].spawns.append(Spawn(name, test, mojo, qmin, qmax))

    def where(self, can_ref: str) -> Appearances:
        return self.by_can.get(can_ref.lower(), Appearances())


def summary(a: Appearances) -> list[str]:
    out = []
    if a.maps:
        shown = ", ".join(a.maps[:6]) + (f" and {len(a.maps) - 6} more" if len(a.maps) > 6 else "")
        out.append(f"Spawns in {len(a.maps)} map{'s' * (len(a.maps) != 1)}: {shown}.")
        by_mojo: dict[str, int] = defaultdict(int)
        for s in a.game_spawns:
            if s.mojo:
                by_mojo[s.mojo] += 1
        if by_mojo:
            ordered = sorted(by_mojo.items(), key=lambda kv: float(kv[0]))
            if len(ordered) <= 4:
                parts = [f"up to {m} ({n} group{'s' * (n != 1)})" for m, n in ordered]
                out.append("Chosen for parties of strength " + ", ".join(parts) + ".")
            else:
                common = max(ordered, key=lambda kv: kv[1])[0]
                out.append(f"Chosen for parties of strength up to {ordered[0][0]} through up "
                           f"to {ordered[-1][0]}, most often up to {common} "
                           f"({sum(by_mojo.values())} spawner groups).")
    if a.scripts:
        out.append("Also created by: " + ", ".join(sorted(set(a.scripts))[:5])
                   + (" ..." if len(set(a.scripts)) > 5 else "") + ".")
    if a.test_maps:
        out.append(f"Also in {len(a.test_maps)} developer test map"
                   f"{'s' * (len(a.test_maps) != 1)}, which players never visit.")
    if not a.maps and not a.scripts:
        out.append("Nothing in the game spawns this creature. Use Spawn it in the game to "
                   "try it, and the map editor to place it.")
    return out
