"""Headless core for the creature can editor.

Everything here is UI-independent and testable without Qt: loading a `.can` from vanilla
`data.dat` or a mod folder, saving it back (or into a mod, registered in its `mod.json`),
the "who spawns this" index over every map, locating the combat AI, the edits the Combat
AI panel makes, and the class-safety scan that refuses to write a can the engine would
crash on. `canedit.py` supplies the PySide6 window on top.

The load/save contract is the same as the map editor's: a can loaded and saved unedited
is **byte-identical** (true of all 1698 shipped cans), and one field edit changes exactly
one line. Files are CRLF and latin-1, and `resource_format` keeps both.
"""
from __future__ import annotations

import copy
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import can_schema as cs
import script_schema as ss
from resource_format import ResourceNode, parse_resource_text

DEFAULT_GAME_DIR = Path(
    r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader")
VANILLA_NAME = "data.dat.vanilla.bak"

# Shown in the title and status bar for a can read out of the vanilla archive.
VANILLA_TAG = "vanilla"


def default_vanilla_dat(game_dir: Path | None = None) -> Path:
    return Path(game_dir or DEFAULT_GAME_DIR) / VANILLA_NAME


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def find_mod_root(path: Path) -> Path | None:
    """The mod folder a file belongs to: the parent of the nearest `files/` that sits
    beside a `mod.json`. None for a file outside any mod."""
    path = Path(path).resolve()
    for parent in path.parents:
        if parent.name.lower() == "files" and (parent.parent / "mod.json").is_file():
            return parent.parent
    return None


def resource_path_for(path: Path) -> str | None:
    """`Resources/Monster Cans/X.can` for a can on disk, or None if it cannot be told.

    Inside a mod it is the path under `files/`. Elsewhere (a scratch copy, an unpacked
    tree) it is the path from the last `Resources` folder down.
    """
    path = Path(path).resolve()
    mod = find_mod_root(path)
    if mod is not None:
        return path.relative_to(mod / "files").as_posix()
    parts = path.parts
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].lower() == "resources":
            return "/".join(("Resources",) + parts[i + 1:])
    return None


def can_ref(resource_path: str) -> str:
    """How a map names a can: `Resources/Monster Cans/X.can` -> `Monster Cans/X`."""
    ref = resource_path.replace("\\", "/")
    if ref.lower().startswith("resources/"):
        ref = ref[len("resources/"):]
    if ref.lower().endswith(".can"):
        ref = ref[:-4]
    return ref


def is_inside(path: Path, folder: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(folder).resolve())
        return True
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

class CanDocument:
    """An editable `.can`. Holds the parsed tree; edits mutate it in place.

    `path` is the file on disk, or None for a can read out of the vanilla archive, which
    is always read-only. A file inside the installed game is read-only too: the toolchain
    layers mods over a pristine backup, so writing into the install would corrupt what
    every rebuild restores from.
    """

    def __init__(self, raw: bytes, *, path: Path | None = None,
                 resource_path: str | None = None, read_only: bool = False,
                 origin: str = ""):
        self._raw = raw
        self.root = parse_resource_text(raw.decode("latin-1"))
        if self.root.type_name != "CCannedObject":
            raise ValueError(f"not a can: root is {self.root.type_name}")
        self.path = Path(path) if path else None
        self.resource_path = resource_path
        self.read_only = read_only
        self.origin = origin            # what to call where it came from
        self.dirty = False
        # id(skeleton) -> the CNormalAttackAI last switched away from, so switching back
        # restores its Shoot Completed bank and distances instead of a stock block.
        self.attack_stash: dict[int, ResourceNode] = {}

    @classmethod
    def from_file(cls, path, game_dir: Path | None = None) -> "CanDocument":
        path = Path(path)
        game = Path(game_dir or DEFAULT_GAME_DIR)
        return cls(path.read_bytes(), path=path, resource_path=resource_path_for(path),
                   read_only=is_inside(path, game), origin=str(path))

    @classmethod
    def from_vanilla(cls, member: str, dat: Path | None = None) -> "CanDocument":
        dat = Path(dat or default_vanilla_dat())
        with zipfile.ZipFile(dat) as zf:
            raw = zf.read(member)
        return cls(raw, path=None, resource_path=member, read_only=True,
                   origin=f"{VANILLA_TAG}: {member}")

    @property
    def name(self) -> str:
        if self.path is not None:
            return self.path.name
        return (self.resource_path or "untitled.can").rsplit("/", 1)[-1]

    @property
    def entity(self) -> ResourceNode | None:
        obj = self.root.get("Object")
        return obj if isinstance(obj, ResourceNode) else None

    # -- persistence ------------------------------------------------------

    def to_bytes(self) -> bytes:
        return self.root.to_text().encode("latin-1")

    def is_unchanged_from_disk(self) -> bool:
        return self.to_bytes() == self._raw

    def check_writable(self) -> None:
        """Raise if this can must not be written. Called by every save."""
        bad = find_abstract(self.root)
        if bad:
            where, name = bad[0]
            more = f" (and {len(bad) - 1} more)" if len(bad) > 1 else ""
            why = cs.ABSTRACT_CLASSES.get(name, "")
            raise UnsafeCanError(
                f"{name} appears at {where}{more}. It is {why}. The game shows a "
                "DataCrash box and exits when it loads a can naming it.")

    def save(self, path=None, game_dir: Path | None = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise PermissionError("this can came from the vanilla archive; use Save As")
        if path is None and self.read_only:
            raise PermissionError(f"{self.name} is read-only; use Save As")
        if is_inside(target, game_dir or DEFAULT_GAME_DIR):
            raise PermissionError("refusing to write inside the installed game")
        self.check_writable()
        data = self.to_bytes()
        # Re-parse what we are about to write; a file that will not load is worse than a
        # failed save.
        parse_resource_text(data.decode("latin-1"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if path is None or Path(path) == self.path:
            self._raw = data
            self.dirty = False
        return target

    def save_into_mod(self, mod_dir: Path, game_dir: Path | None = None) -> Path:
        """Write to `<mod>/files/<resource path>` and register it in the mod's mod.json.

        Afterwards the document *is* that file: writable, and the target of Save.
        """
        mod_dir = Path(mod_dir)
        if not (mod_dir / "mod.json").is_file():
            raise ValueError(f"{mod_dir} has no mod.json")
        if not self.resource_path:
            raise ValueError("cannot tell this can's resource path (no Resources/ folder "
                             "above it), so there is nowhere in a mod to put it")
        target = mod_dir / "files" / Path(self.resource_path)
        self.save(target, game_dir)
        register_in_mod_json(mod_dir, self.resource_path)
        self._raw = self.to_bytes()
        self.path = target
        self.read_only = False
        self.dirty = False
        self.origin = str(target)
        return target


class UnsafeCanError(Exception):
    """The can names a class the engine cannot build."""


def find_abstract(root: ResourceNode, path: str = "") -> list[tuple[str, str]]:
    """Every place an abstract class is named, as (field path, class name).

    Checks block types and scalar values both: `Attack AI=CChargeAttackAI` with no block
    is a class name too. The engine retries a lookup with a C in front, so the bare name
    counts as well. Case is folded -- over-refusing a save is cheap, a crash is not.
    """
    lookup = {k.lower(): k for k in cs.ABSTRACT_CLASSES}
    lookup.update({k[1:].lower(): k for k in cs.ABSTRACT_CLASSES})
    found = []
    here = path or root.type_name
    if root.type_name.strip().lower() in lookup:
        found.append((here, lookup[root.type_name.strip().lower()]))
    for key, value in root.fields:
        sub = f"{here} / {key}"
        if isinstance(value, ResourceNode):
            found.extend(find_abstract(value, sub))
        elif value.strip().lower() in lookup:
            found.append((sub, lookup[value.strip().lower()]))
    return found


# ---------------------------------------------------------------------------
# mod.json
# ---------------------------------------------------------------------------

def _render_manifest(data: dict, newline: str, trailing: bool) -> str:
    text = json.dumps(data, indent=2, ensure_ascii=False).replace("\n", newline)
    return text + (newline if trailing else "")


def register_in_mod_json(mod_dir: Path, resource_path: str) -> bool:
    """Add `resource_path` to the mod's `files` list if it is not there. True if added.

    The manifest must name every file the mod ships, or `dist` refuses to build. The
    file's own line endings and trailing newline are kept: every manifest in this repo
    and in Fixt re-renders byte-identically from json.dumps(indent=2) with its own
    newline, which is checked before writing so a hand-formatted one is never reflowed.
    """
    manifest = Path(mod_dir) / "mod.json"
    raw = manifest.read_bytes()
    text = raw.decode("utf-8")
    data = json.loads(text)
    files = data.setdefault("files", [])
    if any(f.replace("\\", "/").lower() == resource_path.lower() for f in files):
        return False
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(newline)
    if _render_manifest(data, newline, trailing) != text:
        # Not in the shape json.dumps writes; insert one line by hand rather than
        # reformatting someone's file.
        match = re.search(r'("files"\s*:\s*\[)(.*?)(\s*)\]', text, re.S)
        if not match:
            raise ValueError(f"{manifest}: cannot find the files list to extend")
        body = match.group(2)
        indent = "    "
        last = re.findall(r"([ \t]*)\"[^\"\n]*\"", body)
        if last:
            indent = last[-1]
        entry = json.dumps(resource_path, ensure_ascii=False)
        sep = "," if body.strip() else ""
        new = (match.group(1) + body + sep + newline + indent + entry + match.group(3) + "]")
        text = text[:match.start()] + new + text[match.end():]
        json.loads(text)
        manifest.write_bytes(text.encode("utf-8"))
        return True
    files.append(resource_path)
    manifest.write_bytes(_render_manifest(data, newline, trailing).encode("utf-8"))
    return True


# ---------------------------------------------------------------------------
# Who spawns this can
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Use:
    map_path: str       # `Levels/1 Barcelona/X.zax`
    part: str           # the level part's Name, or "" when it has none
    source: str         # "vanilla" or the mod's folder name


def scan_entity_refs(text: str) -> list[tuple[str, str]]:
    """(can ref, level part name) for every `Entity=` line in a map.

    A line scanner rather than a parse: 200 maps are 83 MB, and the only structure that
    matters is which Level Part a line sits in. The part's name is its own `Name=` field,
    one level inside it.
    """
    out = []
    depth = 0
    part_depth = None
    part_name = ""
    pending: list[str] = []
    for line in text.split("\n"):
        s = line.strip("\t\r")
        if s == "{":
            depth += 1
            continue
        if s == "}":
            depth -= 1
            if part_depth is not None and depth == part_depth:
                out.extend((ref, part_name) for ref in pending)
                part_depth, part_name, pending = None, "", []
            continue
        if part_depth is None:
            if s.startswith("Level Part="):
                part_depth, part_name, pending = depth, "", []
            elif s.startswith("Entity="):
                out.append((s[len("Entity="):], ""))
            continue
        if depth == part_depth + 1 and s.startswith("Name="):
            part_name = s[len("Name="):]
        elif s.startswith("Entity="):
            pending.append(s[len("Entity="):])
    return out


class UseIndex:
    """`Entity=` references across every map: vanilla's, with a mod's maps laid over.

    Vanilla is scanned once per process and cached; a mod is rescanned whenever asked,
    because it is the thing being edited. A mod's copy of a map replaces vanilla's, the
    way the build does. References are matched with case folded and slashes normalised.
    """

    _vanilla_cache: dict[str, dict[str, list[tuple[str, str]]]] = {}

    def __init__(self, dat: Path | None = None):
        self.dat = Path(dat or default_vanilla_dat())

    def _vanilla(self) -> dict[str, list[tuple[str, str]]]:
        key = str(self.dat)
        if key not in self._vanilla_cache:
            maps = {}
            if self.dat.is_file():
                with zipfile.ZipFile(self.dat) as zf:
                    for name in zf.namelist():
                        if name.lower().endswith(".zax"):
                            maps[name] = scan_entity_refs(zf.read(name).decode("latin-1"))
            self._vanilla_cache[key] = maps
        return self._vanilla_cache[key]

    @staticmethod
    def _mod_maps(mod_dir: Path) -> dict[str, list[tuple[str, str]]]:
        files = Path(mod_dir) / "files"
        maps = {}
        if files.is_dir():
            for p in files.rglob("*.zax"):
                rel = p.relative_to(files).as_posix()
                maps[rel] = scan_entity_refs(p.read_bytes().decode("latin-1"))
        return maps

    def uses(self, resource_path: str, mod_dir: Path | None = None) -> list[Use]:
        want = _norm_ref(can_ref(resource_path))
        vanilla = self._vanilla()
        mod = self._mod_maps(mod_dir) if mod_dir else {}
        overridden = {m.lower() for m in mod}
        out = []
        for map_path, refs in sorted(vanilla.items()):
            if map_path.lower() in overridden:
                continue
            out.extend(Use(map_path, part, VANILLA_TAG)
                       for ref, part in refs if _norm_ref(ref) == want)
        tag = Path(mod_dir).name if mod_dir else ""
        for map_path, refs in sorted(mod.items()):
            out.extend(Use(map_path, part, tag) for ref, part in refs if _norm_ref(ref) == want)
        return out


def _norm_ref(ref: str) -> str:
    return ref.strip().replace("\\", "/").lower()


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def profile(doc: CanDocument) -> ResourceNode | None:
    """The CAICharacterProfile: where the name the combat log prints, and the race, live."""
    for node in activity_items(doc):
        if node.type_name == "CAICharacterProfile":
            return node
    return None


def activity_items(doc: CanDocument) -> list[ResourceNode]:
    ent = doc.entity
    act = ent.get("Activity") if ent is not None else None
    if not isinstance(act, ResourceNode):
        return []
    if act.type_name == "Array":
        return act.items()
    return [act]


def header(doc: CanDocument) -> dict[str, str]:
    ent = doc.entity
    prof = profile(doc)

    def val(node, key):
        v = node.get(key) if node is not None else None
        return v if isinstance(v, str) else ""

    return {
        "User Assigned Name": val(prof, "User Assigned Name"),
        "Race": val(prof, "Race"),
        "Category": val(ent, "Category"),
        "Team Number": val(ent, "Team Number"),
        "Model": val(ent, "Model"),
        "Entity class": ent.type_name if ent is not None else "",
    }


# Which node each header field lives on.
HEADER_HOST = {"User Assigned Name": "profile", "Race": "profile", "Category": "entity",
               "Team Number": "entity", "Model": "entity"}


def header_node(doc: CanDocument, key: str) -> ResourceNode | None:
    return profile(doc) if HEADER_HOST.get(key) == "profile" else doc.entity


# ---------------------------------------------------------------------------
# Combat AI
# ---------------------------------------------------------------------------

def find_skeleton(doc: CanDocument) -> ResourceNode | None:
    """The can's CSkeletonAI, from its Activity array.

    Of the 478 vanilla cans under Monster Cans/, 470 have exactly one and none has two.
    The other eight have none: seven dragons run CAIDragon instead, and Mongol Bounty
    Hunter Attacker ships with no AI at all.
    """
    for node in activity_items(doc):
        if node.type_name == "CSkeletonAI":
            return node
    return None


def why_no_skeleton(doc: CanDocument) -> str:
    items = activity_items(doc)
    if not items:
        return "This can has no Activity array, so no AI of any kind."
    names = ", ".join(n.type_name for n in items)
    if any(n.type_name == "CAIDragon" for n in items):
        return f"No CSkeletonAI: this creature runs CAIDragon instead. Activity holds: {names}."
    return f"No CSkeletonAI in this can. Activity holds: {names}."


def host_for(skeleton: ResourceNode, where: str) -> ResourceNode | None:
    if where == "skeleton":
        return skeleton
    key = {"target": "Target Type", "attack": "Attack AI"}[where]
    node = skeleton.get(key)
    return node if isinstance(node, ResourceNode) else None


def attack_ai_class(skeleton: ResourceNode) -> str:
    """The attack AI's class name, or "" for an empty `Attack AI=`."""
    v = skeleton.get("Attack AI")
    if isinstance(v, ResourceNode):
        return v.type_name
    return (v or "").strip()


# A stock CNormalAttackAI, copied from vanilla Monster Cans/English Enemies/Soldier1.can.
# Used only when switching to CNormalAttackAI from a class that has no Move Behavior to
# carry over; the creature's own Shoot and Turn Behavior blocks replace these two.
_NORMAL_ATTACK_TEMPLATE = """CNormalAttackAI
{
\tMarked For Deletion=0
\tPublisher=
\tUse Primary Shoot Behavior=1
\tShoot Behavior=CShootEntityBehavior
\t{
\t\tPlay Sequence=
\t\tPlay Reload Sequence=
\t}
\tShoot Completed=
\tTurn Behavior=CPointWeaponAtTargetEntityBehavior
\t{
\t}
\tMoveBehavior=CEntityBehaviorMoveToPos
\t{
\t\tOld Stationary Flag=0
\t\tAngle To Turn To=0
\t\tFacing Backwards=0
\t\tIdle Behavior=CEntityBehaviorIdle
\t\t{
\t\t\tNum Seconds=1
\t\t\tSeconds Have Transitioned=0
\t\t\tOverride Fidget Options=
\t\t\tCurrent Fidget Data=
\t\t\tTime Until Next Fidget=0
\t\t\tFidgeting=0
\t\t}
\t\tPlay Ratio=1
\t\tSmooth Turning=0
\t\tDo Path Finding=1
\t\tFinished=1
\t\tFailed Last Path=0
\t\tIdle Begin Called=0
\t\tMove To Rate=0
\t\tCurTargetNormalX=0
\t\tCurTargetNormalY=0
\t\tPath Finding Params=CWayPointPath
\t\t{
\t\t\tStart Point=0,0
\t\t\tEnd Point=0,0
\t\t\tLength=0
\t\t\tMax Dist To Travel=0
\t\t\tNum Failed Paths=0
\t\t\tMax Failed Paths=0
\t\t\tPos To Move Index=0
\t\t\tDist So Far=0
\t\t\tPath=
\t\t}
\t\tPos Moving From=0.000000,0.000000
\t\tFinal Dest=0.000000,0.000000
\t\tDest Radius=0
\t\tCur Dest Move To=0.000000,0.000000
\t\tLast Neg Dist=0
\t\tNumTimesLostGound=0
\t\tSequence=
\t\tPlaned Path For=
\t}
\tMinimum Attack Distance=100
\tMove Buffer Zone=50
\tWaiting For Threshold=0
\tMoving State=1
\tFiring Complete=1
\tFiring In Progress=0
\tWaiting To Shoot=0
\tTime To Next State Change=0
\tTime To Next MoveRevision=0
\tBegan Shooting From=0.000000,0.000000
}
"""


def stock_normal_attack() -> ResourceNode:
    return parse_resource_text(_NORMAL_ATTACK_TEMPLATE)


def build_attack_ai(doc: CanDocument, skeleton: ResourceNode, new_class: str):
    """The value `Attack AI` should hold after switching to `new_class`.

    Returns a new node, or "" for an empty slot. Never mutates the current block: the
    caller swaps the value in through the undo stack.

    * CStandStillAttackAI keeps only what it reads -- Shoot Behavior and Turn Behavior,
      copied from the current block -- behind the two base fields every AI writes.
    * CNormalAttackAI restores the block last switched away from in this session if
      there is one (its attack bank and distances survive a round trip), and otherwise
      starts from a stock vanilla block carrying the current Shoot and Turn Behavior.
    * "" empties the slot: the shipped way to stop a creature fighting (47 placements).
    """
    if new_class in cs.ABSTRACT_CLASSES:
        raise UnsafeCanError(f"{new_class} is abstract: {cs.ABSTRACT_CLASSES[new_class]}")
    old = skeleton.get("Attack AI")
    old = old if isinstance(old, ResourceNode) else None
    if new_class == "":
        return ""
    if new_class == "CStandStillAttackAI":
        fields = []
        for key in ("Marked For Deletion", "Publisher"):
            v = old.get(key) if old is not None else None
            fields.append((key, v if isinstance(v, str) else ("0" if key.startswith("M") else "")))
        for key in ("Shoot Behavior", "Turn Behavior"):
            v = old.get(key) if old is not None else None
            if not isinstance(v, ResourceNode):
                v = stock_normal_attack().get(key)
            fields.append((key, copy.deepcopy(v)))
        return ResourceNode("CStandStillAttackAI", fields)
    if new_class == "CNormalAttackAI":
        stashed = doc.attack_stash.get(id(skeleton))
        if stashed is not None:
            return copy.deepcopy(stashed)
        block = stock_normal_attack()
        if old is not None:
            for key in ("Shoot Behavior", "Turn Behavior"):
                v = old.get(key)
                if isinstance(v, ResourceNode):
                    block.set(key, copy.deepcopy(v))
        return block
    raise ValueError(f"the editor does not build {new_class}; edit it in the Can tree")


def remember_attack_ai(doc: CanDocument, skeleton: ResourceNode) -> None:
    """Stash the current CNormalAttackAI before it is switched away from."""
    old = skeleton.get("Attack AI")
    if isinstance(old, ResourceNode) and old.type_name in ("CNormalAttackAI",
                                                            "CStandGroundAttackAI"):
        doc.attack_stash[id(skeleton)] = copy.deepcopy(old)


def shoot_completed_items(skeleton: ResourceNode) -> list[str]:
    """The attack bank, one line per branch, for display.

    `Shoot Completed` fires after each swing; a CRandomAction there picks one branch per
    attack, so the branch count is the rate denominator (engine-notes, "one attack
    brain").
    """
    attack = skeleton.get("Attack AI")
    if not isinstance(attack, ResourceNode):
        return []
    bank = attack.get("Shoot Completed")
    if not isinstance(bank, ResourceNode):
        return []
    inner = bank.get("Action")
    if isinstance(inner, ResourceNode) and inner.type_name == "Array":
        n = len(ss.array_items(inner))
        lines = [f"{bank.type_name}: {n} branch(es)"]
        for _, _, v in ss.array_items(inner):
            lines.append("  " + (_describe(v) if isinstance(v, ResourceNode) else v))
        return lines
    return [_describe(bank)]


def _describe(node: ResourceNode) -> str:
    text = ss.summarise(node)
    if node.type_name == "CActionSelectSkill":
        skill = node.get("Skill")
        if isinstance(skill, str) and skill:
            text += f"  --  {skill}"
    return text


# -- transitions ------------------------------------------------------------

def transitions(skeleton: ResourceNode) -> ResourceNode | None:
    t = skeleton.get("Transition")
    return t if isinstance(t, ResourceNode) else None


def ensure_transitions(skeleton: ResourceNode) -> ResourceNode:
    """The Transition array, created empty the way vanilla writes one if missing.

    Every shipped CSkeletonAI has one (434 of 716 empty), and every one counts with
    `Array Count`, not `Item Count`.
    """
    t = transitions(skeleton)
    if t is None:
        t = ss.new_array("Array Count")
        skeleton.set("Transition", t)
    return t


def new_transition(trigger: str = "Once Per New Target", from_state: str = "Any",
                   to_state: str = "Attack", action=None) -> ResourceNode:
    """A CSkeletonTransition in vanilla's field order: one order across all 325."""
    return ResourceNode("CSkeletonTransition", [
        ("Trigger", trigger),
        ("From State", from_state),
        ("To State", to_state),
        ("Action", action if action is not None else ""),
        ("IsDone", "0"),
    ])


def call_for_help_transition(max_distance: str = "500") -> ResourceNode:
    """'Call for help when it first attacks': on the first switch to Attack against each
    new target, call idle allies within 500 px into the fight. The action and its
    defaults are dormant-engine.md's CCallForReinforcementsAction; the transition is the
    shape vanilla uses for its war cries."""
    action = ResourceNode("CCallForReinforcementsAction", [
        ("Requester Name", "$Trigger"),
        ("Max Distance", max_distance),
    ])
    return new_transition("Once Per New Target", "Any", "Attack", action)


TRANSITION_ITEM_KEY = "Array Item"


def summarise_transition(node: ResourceNode) -> str:
    action = node.get("Action")
    what = _describe(action) if isinstance(action, ResourceNode) else "(no action)"
    return (f"{node.get('Trigger') or '?'}: {node.get('From State') or '?'} -> "
            f"{node.get('To State') or '?'}  --  {what}")


# ---------------------------------------------------------------------------
# Listing cans
# ---------------------------------------------------------------------------

def vanilla_cans(dat: Path | None = None) -> list[str]:
    dat = Path(dat or default_vanilla_dat())
    with zipfile.ZipFile(dat) as zf:
        return sorted(n for n in zf.namelist() if n.lower().endswith(".can"))


def mod_cans(mods: list[Path]) -> list[tuple[str, Path]]:
    """(mod folder name, path) for every .can a mod ships."""
    out = []
    for mod in mods:
        files = Path(mod) / "files"
        if files.is_dir():
            out.extend((Path(mod).name, p) for p in sorted(files.rglob("*.can")))
    return out
