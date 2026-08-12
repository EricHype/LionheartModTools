"""Headless core for the Lionheart map editor (phase 1).

Everything here is UI-independent and testable without Qt: loading a `.zax` into an
editable document, the placeable-sprite catalogue, and the placement validation rules.
`mapedit.py` supplies the PySide6 interface on top.

The load/save contract is the property the editor rests on: a document that is loaded and
saved without edits is **byte-identical** to the input, and one edit changes only the
lines that edit touched. That comes from `resource_format.ResourceNode` round-tripping
exactly, and from editing nodes in place rather than regenerating the file.

See docs/map-editor-design.md for the format details this builds on.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from resource_format import ResourceNode, parse_resource_text
from mdl16_format import decode_icon, find_header

GRID_CELL = 64          # world units between terrain grid vertices
DEFAULT_MARGIN = 30     # extra clearance required beyond two objects' half-widths


# ---------------------------------------------------------------------------
# Sprite catalogue
# ---------------------------------------------------------------------------

@dataclass
class SpriteInfo:
    """Dimensions and anchor of one placeable sprite. Pixels == world units."""
    model: str          # e.g. "Environments/Rethgorad/Town/Fence/Fence A"
    width: int
    height: int
    hotspot_x: int
    hotspot_y: int

    @property
    def half_width(self) -> float:
        return self.width / 2

    @property
    def radius(self) -> float:
        """Clearance radius. Half-width is the useful measure: footprints are wider
        than they are deep in this projection, and using height would over-reject."""
        return self.width / 2


class SpriteCatalogue:
    """Lazily decodes `Environments/**` sprites and caches them by model path."""

    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        self._info: dict[str, SpriteInfo | None] = {}
        self._pixels: dict[str, dict] = {}

    def path_for(self, model: str) -> Path:
        return self.data_root / "Cache" / "Models" / (model + ".mdl16")

    def info(self, model: str) -> SpriteInfo | None:
        """Header only -- cheap, enough for placement and validation."""
        if model in self._info:
            return self._info[model]
        result = None
        try:
            header = find_header(self.path_for(model).read_bytes())
            if header is not None:
                result = SpriteInfo(model, header.width, header.height,
                                    header.hotspot_x, header.hotspot_y)
        except OSError:
            pass
        self._info[model] = result
        return result

    def pixels(self, model: str) -> dict | None:
        """Full decode: {'width','height','rows'} with rows of (r,g,b,a). Cached."""
        if model in self._pixels:
            return self._pixels[model]
        try:
            decoded = decode_icon(self.path_for(model).read_bytes())
        except Exception:
            decoded = None
        if decoded is not None:
            self._pixels[model] = decoded
        return decoded

    def list_models(self) -> list[str]:
        """Every placeable environment sprite, as model paths, sorted."""
        root = self.data_root / "Cache" / "Models" / "Environments"
        out = []
        for p in root.rglob("*.mdl16"):
            rel = p.relative_to(self.data_root / "Cache" / "Models")
            out.append(rel.with_suffix("").as_posix())
        return sorted(out)


# Assets that do NOT tile into runs, and the step vectors for those that do. Derived by
# finding collinear chains across all 201 shipped maps; see the lionheart-modding skill.
# The perpendicular component is real -- flattening it to zero visibly misaligns a run.
TILING_VECTORS: dict[str, tuple[int, int]] = {
    "Environments/Mountain/Inside/Walls/Wall 01 A": (124, -7),
    "Environments/Mountain/Inside/Walls/Wall 01 E": (121, -7),
    "Environments/Mountain/Inside/Walls/Wall 01 C": (10, 88),
    "Environments/Mountain/Inside/Walls/Wall 01 G": (11, 86),
    "Environments/Druid Grove/Walls/StrateWall/StrateWall A": (145, 28),
    "Environments/Outpost/Transformed Region/Walls/Wall 02/Wall 02 B": (63, -53),
}

# Prefix match: nothing in these families forms a run anywhere in the shipped game.
NON_TILING_PREFIXES = ("Environments/Rethgorad/Town/Fence/",)


def tiling_vector(model: str) -> tuple[int, int] | None:
    return TILING_VECTORS.get(model)


def tiles(model: str) -> bool:
    return model in TILING_VECTORS


def known_non_tiling(model: str) -> bool:
    return any(model.startswith(p) for p in NON_TILING_PREFIXES)


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

# Field order copied from a real scenery entity, so generated entities are
# indistinguishable from shipped ones. Position/Model are filled per entity.
_ENTITY_TEMPLATE = [
    ("Name", ""), ("Child List", ""), ("Visible", "1"), ("Collideable", "1"),
    ("Half Height", "0"), ("Full Height", "1"), ("Tries To Collide", "0"),
    ("Has Hit Points", "0"), ("Stationary", "1"), ("Active", "1"),
    ("Is Temporarily Excluded", "0"), ("Is Marked For Deletion", "0"),
    ("__activity__", None),
    ("Category", ""), ("Team Number", "Nutral"), ("Used In", "QuestMode"),
    ("Current Target", ""), ("Publisher", ""),
    ("Model", ""), ("Position X", "0"), ("Position Y", "0"),
    ("Rendering Height", "0"), ("Rendering Height Float", "0"),
    ("Cur Sequence", "Idle"),
]


@dataclass
class Entity:
    """One placed object, wrapping its ResourceNode. Edits write straight through."""
    node: ResourceNode
    index: int              # position within Tree List, for stable identity

    @property
    def model(self) -> str:
        return self.node.get("Model") or ""

    @property
    def x(self) -> float:
        return float(self.node.get("Position X") or 0)

    @property
    def y(self) -> float:
        return float(self.node.get("Position Y") or 0)

    @property
    def name(self) -> str:
        return self.node.get("Name") or ""

    def move_to(self, x: float, y: float) -> None:
        # Integers where possible: shipped files use bare ints for whole coordinates and
        # gratuitous ".0" would show up as noise in a diff.
        self.node.set("Position X", _num(x))
        self.node.set("Position Y", _num(y))

    def set_field(self, key: str, value: str) -> None:
        self.node.set(key, value)

    def is_scenery(self) -> bool:
        """True for plain placeable art, as opposed to spawners, doors, triggers."""
        model = self.model
        if not model.startswith("Environments/"):
            return False
        activity = self.node.get("Activity")
        if isinstance(activity, ResourceNode):
            count = activity.get("Item Count")
            if count not in (None, "0"):
                return False
        return True


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


class MapDocument:
    """An editable `.zax`. Holds the parsed tree; edits mutate it in place."""

    def __init__(self, path):
        self.path = Path(path)
        self._raw = self.path.read_bytes()
        self.root = parse_resource_text(self._raw.decode("latin-1"))
        self.dirty = False

        tree = self.root.get("Tree List")
        if not isinstance(tree, ResourceNode):
            raise ValueError(f"{self.path.name}: no 'Tree List' -- not a level file?")
        self.tree = tree

    # -- geometry ---------------------------------------------------------

    @property
    def width(self) -> int:
        return int(self.root.get("Width") or 0)

    @property
    def height(self) -> int:
        return int(self.root.get("Height") or 0)

    # -- entities ---------------------------------------------------------

    def entities(self) -> list[Entity]:
        out = []
        for i, (key, value) in enumerate(self.tree.fields):
            if key == "Level Part" and isinstance(value, ResourceNode):
                out.append(Entity(value, i))
        return out

    def add_entity(self, model: str, x: float, y: float, *,
                   collideable: bool = True, half_height: bool = False) -> Entity:
        node = ResourceNode(type_name="CEntityBase", fields=[])
        for key, val in _ENTITY_TEMPLATE:
            if key == "__activity__":
                node.fields.append(("Activity", ResourceNode(
                    type_name="Array", fields=[("Item Count", "0")])))
            elif key == "Model":
                node.fields.append((key, model))
            elif key == "Position X":
                node.fields.append((key, _num(x)))
            elif key == "Position Y":
                node.fields.append((key, _num(y)))
            elif key == "Collideable":
                node.fields.append((key, "1" if collideable else "0"))
            elif key == "Half Height":
                node.fields.append((key, "1" if half_height else "0"))
            elif key == "Full Height":
                node.fields.append((key, "0" if half_height else "1"))
            else:
                node.fields.append((key, val))
        self.tree.fields.append(("Level Part", node))
        self.dirty = True
        return Entity(node, len(self.tree.fields) - 1)

    def remove_entity(self, entity: Entity) -> None:
        for i, (key, value) in enumerate(self.tree.fields):
            if value is entity.node:
                del self.tree.fields[i]
                self.dirty = True
                return
        raise ValueError("entity is not part of this document")

    # -- persistence ------------------------------------------------------

    def to_bytes(self) -> bytes:
        return self.root.to_text().encode("latin-1")

    def is_unchanged_from_disk(self) -> bool:
        """True if serialising right now reproduces the original bytes exactly."""
        return self.to_bytes() == self._raw

    def save(self, path=None) -> Path:
        target = Path(path) if path else self.path
        data = self.to_bytes()
        # Re-parse what we are about to write; a file that will not load is worse than
        # a failed save, and this is cheap next to an in-game test cycle.
        parse_resource_text(data.decode("latin-1"))
        target.write_bytes(data)
        if target == self.path:
            self._raw = data
            self.dirty = False
        return target


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    severity: str            # "error" | "warning"
    message: str
    entities: list[Entity] = field(default_factory=list)
    at: tuple[float, float] | None = None


def validate(doc: MapDocument, cat: SpriteCatalogue, *,
             margin: float = DEFAULT_MARGIN) -> list[Issue]:
    """Placement checks, as continuous feedback rather than one-off assertions.

    These are the checks that caught three bad layouts during the Test Pocket arena
    work -- one of which (a chest placed inside a rock) still reached an in-game test
    because nothing was enforcing them.
    """
    issues: list[Issue] = []
    placed = []
    for ent in doc.entities():
        model = ent.model
        if not model:
            continue
        info = cat.info(model) if model.startswith("Environments/") else None
        placed.append((ent, info))

        if model.startswith("Environments/") and info is None:
            issues.append(Issue("error", f"missing sprite: {model}", [ent],
                                (ent.x, ent.y)))
        if not (0 <= ent.x <= doc.width and 0 <= ent.y <= doc.height):
            issues.append(Issue("warning",
                                f"off-map at ({ent.x:g}, {ent.y:g})", [ent],
                                (ent.x, ent.y)))
        if known_non_tiling(model):
            issues.append(Issue("warning",
                                f"{model.rsplit('/', 1)[-1]} is scatter decoration; it "
                                f"does not tile into runs", [ent], (ent.x, ent.y)))

    # Overlap: sprite footprints are wide, and world units are sprite pixels, so a
    # naive eyeball placement can bury one object inside another.
    #
    # Tiled pieces are exempt from each other. A run is *supposed* to be tighter than
    # the footprints -- Wall 01 A is 130 wide on a 124 step -- so checking wall against
    # wall reports every piece of every run and buries the real findings. A prop against
    # a wall is still checked, which is the case that matters.
    for i in range(len(placed)):
        ent_a, info_a = placed[i]
        if info_a is None:
            continue
        for j in range(i + 1, len(placed)):
            ent_b, info_b = placed[j]
            if info_b is None:
                continue
            if tiles(info_a.model) and tiles(info_b.model):
                continue
            need = info_a.radius + info_b.radius + margin
            dist = math.hypot(ent_a.x - ent_b.x, ent_a.y - ent_b.y)
            if dist < need:
                issues.append(Issue(
                    "error",
                    f"{info_a.model.rsplit('/', 1)[-1]} and "
                    f"{info_b.model.rsplit('/', 1)[-1]} overlap "
                    f"({dist:.0f} apart, need {need:.0f})",
                    [ent_a, ent_b],
                    ((ent_a.x + ent_b.x) / 2, (ent_a.y + ent_b.y) / 2)))

    # Holes in wall runs. Warning rather than error: a run that stops is often a
    # deliberate gateway, and only the author knows which. The arena's missing
    # south-east corner reached an in-game test because nothing flagged it.
    for ent, neighbours in find_wall_gaps(doc.entities()):
        what = ("is on its own" if neighbours == 0
                else "ends here with nothing adjoining it")
        issues.append(Issue(
            "warning",
            f"wall run {what} at ({ent.x:g}, {ent.y:g}) — gap, or an intended opening?",
            [ent], (ent.x, ent.y)))
    return issues


def find_wall_gaps(entities: list[Entity], *, slack: float = 1.35
                   ) -> list[tuple[Entity, int]]:
    """Ends of tiled runs that nothing adjoins -- i.e. holes in a wall.

    Counts, for each tiled piece, how many other tiled pieces sit within one step (plus
    slack). Interior pieces have two neighbours; where two runs meet at a corner each
    corner piece still sees the other run's piece. **One** neighbour means the run just
    stops, and **zero** means the piece is stranded.

    Returns (entity, neighbour_count) for each such piece, so the caller can word an
    endpoint and an orphan differently.

    An earlier version of this looked for pieces whose nearest neighbour was further
    than one pitch, which only ever found fully isolated pieces -- it could not have
    detected the bug it was written for. The Test Pocket arena's missing south-east
    corner left two pieces each with a single neighbour, not zero.
    """
    tiled = []
    for ent in entities:
        vec = tiling_vector(ent.model)
        if vec is not None:
            tiled.append((ent, vec, math.hypot(*vec)))
    if len(tiled) < 2:
        return []

    def piece_at(x: float, y: float, tol: float):
        for o, _, _ in tiled:
            if math.hypot(x - o.x, y - o.y) <= tol:
                return o
        return None

    out = []
    for ent, vec, pitch in tiled:
        # Where this run would continue, forwards and backwards.
        slots = [(ent.x + vec[0], ent.y + vec[1]),
                 (ent.x - vec[0], ent.y - vec[1])]
        fillers = [piece_at(sx, sy, pitch * 0.5) for sx, sy in slots]
        continues = sum(1 for f in fillers if f is not None)
        if continues == 2:
            continue        # interior piece, nothing to say

        # One slot empty is normal -- every run has two ends, and at a corner the
        # adjoining run picks up from there. What distinguishes a corner from a hole is
        # the distance to the nearest piece that ISN'T already filling a slot: at the
        # arena's north-east corner that is 124 (one pitch, sprites still overlapping),
        # at its actual hole it was 157, and either side of a removed mid-run piece it
        # is 248. Excluding the run-mate is the crux -- measuring to *any* neighbour
        # makes a mid-run hole look adjoined by the piece on its other side.
        # Judge each candidate against the LARGER of the two pitches. Runs meeting at a
        # corner usually have different step lengths -- Wall 01 C steps 88 but the A run
        # it meets steps 124 -- so measuring against only this piece's pitch reports
        # every such corner as a hole.
        adjoined = any(
            math.hypot(ent.x - o.x, ent.y - o.y) <= max(pitch, op) * 1.15
            for o, _, op in tiled
            if o is not ent and o not in fillers)
        if not adjoined:
            out.append((ent, continues))
    return out
