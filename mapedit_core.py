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
    # Found by sweeping all 200 vanilla maps for collinear runs. Only these two of the
    # fourteen candidates are worth trusting: an 8-piece and a 4-piece run. The rest
    # rested on a single 3-piece chain, which three scattered props hit by chance --
    # several of the candidates were not even scenery (Editor/Relay, a church pew).
    # Wall 03 A steps exactly like Wall 01 A: same geometry, different skin.
    "Environments/Mountain/Inside/Walls/Wall 03 A": (124, -7),
    "Environments/Outpost/Dwarf Region/Support Beams/Straight Supports/Supports F": (11, 83),
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
# Wall runs
# ---------------------------------------------------------------------------

# A wall family is the set of tiling pieces sharing a stem, distinguished only by a
# trailing letter: "Wall 01 A" / "C" / "E" / "G" are the four faces of one wall. The
# letters are compass facings, not directions of travel -- A (north) and E (south) both
# run east-west, C (east) and G (west) both run north-south. So the family tells you
# which pieces *could* serve a given drag; it can never tell you which face you meant.
_FAMILY_SUFFIX = re.compile(r"^(.*) ([A-Z])$")

# Below this |cos| between the drag and the piece's tiling vector, the drag is closer to
# perpendicular than parallel and laying the run would produce a line of pieces marching
# off in a direction nobody asked for. cos 60 degrees.
RUN_ALIGNMENT_FLOOR = 0.5

MAX_RUN_PIECES = 256


def wall_family(model: str) -> list[str]:
    """Tiling pieces sharing this model's stem, including the model itself."""
    m = _FAMILY_SUFFIX.match(model)
    if not m:
        return [model] if model in TILING_VECTORS else []
    stem = m.group(1)
    return sorted(k for k in TILING_VECTORS
                  if (mm := _FAMILY_SUFFIX.match(k)) and mm.group(1) == stem)


def _alignment(vec: tuple[int, int], drag: tuple[float, float]) -> float:
    """|cos| of the angle between a tiling vector and a drag. 1.0 == same axis."""
    vlen = math.hypot(*vec)
    dlen = math.hypot(*drag)
    if not vlen or not dlen:
        return 0.0
    return abs(vec[0] * drag[0] + vec[1] * drag[1]) / (vlen * dlen)


MIN_LEARNED_STEP = 8        # shorter than this and two pieces are effectively stacked
MAX_LEARNED_STEP = 400      # longer and they are two separate props, not a run


def learn_vector_from_map(entities: list["Entity"], model: str) -> tuple[int, int] | None:
    """Work out how `model` tiles from copies already placed in this map.

    Only 8 of the 4787 placeable sprites have a hand-measured vector, and sweeping the
    vanilla maps for more finds barely a dozen -- most environment art is scatter, so the
    corpus simply has no runs to measure. But the map being edited does: place two pieces
    by hand where you want them and the spacing is defined, for any sprite in the game.

    Two placements give exactly one candidate. Three or more must agree: the most common
    delta has to actually chain through the pieces, otherwise a handful of scattered
    props would hand back a meaningless step.
    """
    pts = sorted({(round(e.x), round(e.y)) for e in entities if e.model == model})
    if len(pts) < 2:
        return None

    def plausible(d):
        return MIN_LEARNED_STEP <= math.hypot(*d) <= MAX_LEARNED_STEP

    if len(pts) == 2:
        d = (pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
        return d if plausible(d) else None

    counts: dict[tuple[int, int], int] = {}
    for i, a in enumerate(pts):
        for b in pts[i + 1:]:
            d = (b[0] - a[0], b[1] - a[1])
            if plausible(d):
                counts[d] = counts.get(d, 0) + 1
    if not counts:
        return None
    best = max(counts, key=lambda d: (counts[d], -abs(d[0]) - abs(d[1])))

    # Require a real chain, not just a repeated coincidence.
    seen = set(pts)
    longest = 0
    for a in pts:
        if (a[0] - best[0], a[1] - best[1]) in seen:
            continue
        length, cur = 1, a
        while (cur := (cur[0] + best[0], cur[1] + best[1])) in seen:
            length += 1
        longest = max(longest, length)
    return best if longest >= 3 else None


@dataclass
class WallRun:
    """A planned run of tiling pieces. Falsy when it could not be planned."""
    model: str = ""
    positions: list[tuple[int, int]] = field(default_factory=list)
    off_axis: float = 0.0       # world units the drag's end missed the run's axis by
    truncated: bool = False     # hit MAX_RUN_PIECES
    reason: str = ""            # why there is nothing to place
    alternatives: list[str] = field(default_factory=list)  # better-aligned family members

    def __bool__(self) -> bool:
        return bool(self.positions)


def plan_wall_run(model: str, start: tuple[float, float], end: tuple[float, float],
                  *, max_pieces: int = MAX_RUN_PIECES,
                  vec: tuple[int, int] | None = None) -> WallRun:
    """Lay `model` from `start` towards `end`, stepping along its measured tiling vector.

    The drag only chooses how far and which way along that one axis -- the piece's vector
    is authoritative for where each successive copy lands, because those vectors carry a
    perpendicular component (Wall 01 A steps (124, -7), so a ten-piece run really does
    climb 70 units) and eyeballing that drift is exactly what produced crooked runs by
    hand. Off-axis drag distance is reported rather than honoured.

    A drag closer to perpendicular than parallel is refused instead of being projected
    down to one or two pieces: it means the wrong piece is selected for the direction
    being drawn, and the family members that do run that way are named in `alternatives`.

    `vec` overrides the hand-measured table, for a step learned from the open map.
    """
    vec = vec or tiling_vector(model)
    if vec is None:
        if known_non_tiling(model):
            run = WallRun(model=model, reason=(
                f"{model.rsplit('/', 1)[-1]} is from a family that does not tile -- "
                "no run of it exists anywhere in the shipped game."))
        else:
            run = WallRun(model=model, reason=(
                f"No tiling step known for {model.rsplit('/', 1)[-1]}. Place two of "
                "them where you want them and the run tool will copy that spacing."))
        run.alternatives = [m for m in wall_family(model) if m != model]
        return run

    drag = (end[0] - start[0], end[1] - start[1])
    vlen2 = vec[0] ** 2 + vec[1] ** 2

    # A click rather than a drag: one piece, no direction to judge.
    if math.hypot(*drag) > 1e-6 and _alignment(vec, drag) < RUN_ALIGNMENT_FLOOR:
        better = [m for m in wall_family(model)
                  if m != model and (v := tiling_vector(m))
                  and _alignment(v, drag) >= RUN_ALIGNMENT_FLOOR]
        return WallRun(model=model, alternatives=better, reason=(
            f"That drag runs across {model.rsplit('/', 1)[-1]}, not along it."))

    steps = round((drag[0] * vec[0] + drag[1] * vec[1]) / vlen2)
    step = vec if steps >= 0 else (-vec[0], -vec[1])
    count = min(abs(steps) + 1, max_pieces)

    positions = [(round(start[0] + i * step[0]), round(start[1] + i * step[1]))
                 for i in range(count)]

    # Perpendicular distance from the drag's end to the run's axis: how far the run will
    # land from where the pointer actually was.
    off_axis = abs(drag[0] * vec[1] - drag[1] * vec[0]) / math.sqrt(vlen2)

    return WallRun(model=model, positions=positions, off_axis=off_axis,
                   truncated=abs(steps) + 1 > max_pieces)


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

class TerrainLayer:
    """Read/write access to a map's ground, for painting.

    There is no separate paint layer: the `Elevations` byte at each grid vertex IS the
    ground-texture index (see docs/map-editor-design.md, "Terrain"). Painting therefore
    means writing elevation bytes, and each byte is written as the *centre* of its
    index's band so that the engine's bilinear blend between neighbouring vertices lands
    cleanly rather than on a boundary.

    Row lengths are fixed -- one hex byte pair per vertex -- so a write must never change
    a row's length. `MapDocument`'s byte-identical guarantee depends on it.
    """

    def __init__(self, doc: "MapDocument"):
        self.doc = doc
        node = doc.root.get("Plasma Ground")
        if not isinstance(node, ResourceNode):
            raise ValueError("map has no 'Plasma Ground' -- cannot paint terrain")
        self.node = node
        self.cols = doc.width // GRID_CELL + 1
        self.rows = doc.height // GRID_CELL + 1
        self._grid = self._read_grid()

    # -- textures ---------------------------------------------------------

    @property
    def textures(self) -> list[str]:
        try:
            n = int(self.node.get("Num Textures") or 0)
        except ValueError:
            n = 0
        out = []
        for i in range(n):
            name = self.node.get(f"Texture {i}")
            if name:
                out.append(name)
        return out

    def set_textures(self, names: list[str]) -> None:
        """Replace the declared texture set, rewriting Num Textures and Texture N.

        Order matters and is the caller's business: adjacent indices are what a blend
        passes *through*, so a light-to-dark ordering gives soft edges while ordering by
        name gives hard seams.
        """
        if not names:
            raise ValueError("a map needs at least one ground texture")
        keep = [(k, v) for k, v in self.node.fields
                if k != "Num Textures" and not re.fullmatch(r"Texture \d+", k)]
        # Rebuild in place, putting the texture block back where it was.
        insert_at = next((i for i, (k, _) in enumerate(self.node.fields)
                          if k == "Num Textures" or re.fullmatch(r"Texture \d+", k)),
                         len(keep))
        block = [("Num Textures", str(len(names)))]
        block += [(f"Texture {i}", n) for i, n in enumerate(names)]
        self.node.fields = keep[:insert_at] + block + keep[insert_at:]
        self.doc.dirty = True

    def band_value(self, index: int) -> int:
        """The elevation byte that selects texture `index`, at its band's centre."""
        n = max(1, len(self.textures))
        band = 256 // n
        return min(255, index * band + band // 2)

    def index_at(self, col: int, row: int) -> int:
        n = max(1, len(self.textures))
        return min(self._grid[row][col] * n // 256, n - 1)

    # -- elevation grid ---------------------------------------------------

    def _read_grid(self) -> list[bytearray]:
        grid = []
        for r in range(self.rows):
            raw = self.node.get(f"Elevations Row {r}")
            if not isinstance(raw, str):
                raise ValueError(f"missing 'Elevations Row {r}'")
            grid.append(bytearray(bytes.fromhex(raw.strip())))
        return grid

    def value(self, col: int, row: int) -> int:
        return self._grid[row][col]

    def paint(self, col: int, row: int, index: int, radius: int = 0) -> bool:
        """Set the vertices within `radius` (in grid cells) to texture `index`.

        Returns True if anything actually changed, so the caller can skip a re-render
        and avoid marking the document dirty for a no-op drag.
        """
        val = self.band_value(index)
        changed = False
        for r in range(row - radius, row + radius + 1):
            if not (0 <= r < self.rows):
                continue
            for c in range(col - radius, col + radius + 1):
                if not (0 <= c < self.cols):
                    continue
                # Plain Euclidean radius, no slack. An earlier `radius + 0.5` let the
                # diagonals in at radius 1 -- 1.414 < 1.5 -- so the default brush was a
                # 3x3 square, and larger ones were squares with the corners clipped.
                if radius and math.hypot(c - col, r - row) > radius:
                    continue
                if self._grid[r][c] != val:
                    self._grid[r][c] = val
                    changed = True
        return changed

    def snapshot(self) -> list[bytes]:
        return [bytes(r) for r in self._grid]

    def restore(self, snap: list[bytes]) -> None:
        self._grid = [bytearray(r) for r in snap]
        self.flush()

    def flush(self) -> None:
        """Write the grid back into the document's nodes."""
        for r in range(self.rows):
            hexed = self._grid[r].hex().upper()
            key = f"Elevations Row {r}"
            old = self.node.get(key)
            if isinstance(old, str) and len(hexed) != len(old.strip()):
                raise ValueError(
                    f"{key}: refusing to change row length "
                    f"({len(old.strip())} -> {len(hexed)})")
            self.node.set(key, hexed)
        self.doc.dirty = True

    @staticmethod
    def world_to_grid(x: float, y: float) -> tuple[int, int]:
        return int(round(x / GRID_CELL)), int(round(y / GRID_CELL))


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
            f"wall run {what} at ({ent.x:g}, {ent.y:g}) - gap, or an intended opening?",
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
