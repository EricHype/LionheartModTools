"""Headless core for the enemy editor.

A creature is two files:

* its **can** (`Resources/Monster Cans/.../X.can`): the on-screen name, level, experience,
  category, model, natural weapon, AI, drops and sounds;
* its **race** (`Resources/Races/.../X.Race`), named by the can's `Race=`: the stats. HP, armour
  class and resistances are `CPresetDerivedCharacterAttribute` presets, attributes are
  `value,min,max` triples, and skills are `CPresetSkill` presets.

This module reads both through `icontool.Library` (the mod's `files/` shadows the vanilla
archive) and exposes what a person edits. Everything else is left exactly as it is: load and
save are byte-identical for every shipped can and race, and one edit is a one-line change.

What E0 established and this relies on (docs/enemy-editor-e0.md):

* `Model=Characters/Monsters/X` names `Cache/Models/.../X.mdl16`, whose length-prefixed
  `Models3D/...` paths are the mesh and the animation clips.
* A sequence name a creature calls picks a clip whose name equals it or starts with it. A
  model swap is safe only if every called sequence still finds a clip; the Horse, which calls
  `Attack` on a model with only Death/Idle/Walk, is the cautionary case.
* Tough and Super are separate creatures chosen by spawner groups, not by difficulty.

Races are shared (87 of 351, up to 8 creatures; the Horse uses the Gray Wolf's), so editing
stats copies a shared race first: `fork_race`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import canedit_core
from canedit_core import register_in_mod_json
from icontool import Library
from itemedit_core import ItemError, child, parse_number, readable, walk
from resource_format import ResourceNode, parse_resource_text

CAN_ROOT = "Resources/Monster Cans/"
REACH_NOTE = ("A saved game keeps its own copy of every creature already spawned, and of each "
              "map's creatures from the first visit. Your change shows on creatures spawned "
              "from now on and in a new game.")
# Sequence names the engine uses for itself, not clips (49 uses, all on models without it).
ENGINE_SEQUENCES = {"snapshot"}
SEQUENCE_KEYS = ("Sequence Name", "Cur Sequence", "Turn Sequence Name")


# ---------------------------------------------------------------------------
# A resource file
# ---------------------------------------------------------------------------

class ResDoc:
    """One text resource, parsed; edits mutate `root`. `origin`: vanilla, mod or new."""

    def __init__(self, rel: str, raw: bytes, origin: str):
        self.rel, self._raw, self.origin = rel, raw, origin
        self.root = parse_resource_text(raw.decode("latin-1"))

    @classmethod
    def from_library(cls, lib: Library, rel: str) -> "ResDoc":
        rel = lib.canonical(rel)
        return cls(rel, lib.read(rel), lib.where(rel))

    @property
    def ref(self) -> str:
        r = self.rel[len("Resources/"):] if self.rel.lower().startswith("resources/") else self.rel
        return r.rsplit(".", 1)[0]

    def to_bytes(self) -> bytes:
        return self.root.to_text().encode("latin-1")

    @property
    def dirty(self) -> bool:
        return self.origin == "new" or self.to_bytes() != self._raw

    def save_into_mod(self, mod: Path) -> Path:
        mod = Path(mod)
        if not (mod / "mod.json").is_file():
            raise ItemError(f"{mod} has no mod.json, so it is not a mod folder.")
        data = self.to_bytes()
        parse_resource_text(data.decode("latin-1"))
        target = mod / "files" / Path(self.rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        register_in_mod_json(mod, self.rel)
        self._raw, self.origin = data, "mod"
        return target


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def mdl16_paths(data: bytes) -> list[str]:
    """Every `Models3D/...` path in a .mdl16, read through its 4-byte length prefix (a regex
    over printable runs merges adjacent strings)."""
    out = []
    for m in re.finditer(rb"Models3D/", data):
        i = m.start()
        ln = int.from_bytes(data[i - 4:i], "little") if i >= 4 else 0
        if 9 <= ln < 300:
            p = data[i:i + ln].decode("latin-1")
            if p not in out:
                out.append(p)
    return out


@dataclass
class ModelInfo:
    ref: str                       # Characters/Monsters/Wolf Gray
    mesh: str = ""                 # Models3D/.../GrayWolf (no extension)
    clips: list[str] = field(default_factory=list)      # clip paths, no extension
    scaling: str = ""              # Render Scaling from the mesh's MODEL.TXT
    exists: bool = True

    @property
    def clip_names(self) -> list[str]:
        return [c.rsplit("/", 1)[-1] for c in self.clips]

    def finds(self, sequence: str) -> bool:
        s = sequence.strip().lower()
        return any(c.lower() == s or c.lower().startswith(s) for c in self.clip_names)


def model_info(lib: Library, ref: str) -> ModelInfo:
    rel = "Cache/Models/" + ref + ".mdl16"
    if not lib.exists(rel):
        return ModelInfo(ref, exists=False)
    info = ModelInfo(ref)
    for p in mdl16_paths(lib.read(rel)):
        base = "Resources/" + p
        if lib.exists(base + ".MODEL.GR2"):
            info.mesh = info.mesh or p
        elif lib.exists(base + ".ANIMATION.GR2"):
            info.clips.append(p)
    if info.mesh and lib.exists("Resources/" + info.mesh + ".MODEL.TXT"):
        m = re.search(rb"Render Scaling=([^\r\n]+)", lib.read("Resources/" + info.mesh + ".MODEL.TXT"))
        info.scaling = m.group(1).decode().strip() if m else ""
    return info


def creature_models(lib: Library) -> list[str]:
    """Every character model a creature could wear: the .mdl16 files under
    Cache/Models/Characters that name a mesh."""
    out = []
    for n in lib.names():
        if n.startswith("Cache/Models/Characters/") and n.lower().endswith(".mdl16"):
            out.append(n[len("Cache/Models/"):-len(".mdl16")])
    return sorted(out, key=str.lower)


# ---------------------------------------------------------------------------
# The natural weapon, seen as an item so the item editor's cards work on it
# ---------------------------------------------------------------------------

class WeaponView:
    """A CInventoryItem inside a can, shaped like an itemedit_core.ItemDocument for the
    readers that only need its behaviours (damage_lines, on_hits)."""
    is_addition = False

    def __init__(self, node: ResourceNode, where: str):
        self.root, self.where = node, where
        self.rel = where

    def behaviours(self) -> list[ResourceNode]:
        arr = child(self.root, "PlugIn Behaviors")
        return arr.items() if arr else []

    def expressions(self) -> dict:
        return {}

    @property
    def name(self) -> str:
        name = (self.root.get("Display Name") or "").strip()
        return name if name and name.lower() not in ("unnamed", "undefined") else "natural weapon"

    @property
    def reach(self) -> ResourceNode | None:
        """The CPlugInBehaviorAttack holding Max Attack Range."""
        return next((b for b in self.behaviours() if b.type_name == "CPlugInBehaviorAttack"), None)

    @property
    def ammunition(self) -> list[str]:
        """What a ranged weapon launches (an arrow item, a spell projectile). Its damage
        lives in that item, not here."""
        out = []
        for n in walk(self.root):
            v = n.get("Secondary Launchable Type")
            if isinstance(v, str) and v.strip() and v.strip() not in out:
                out.append(v.strip())
        return out

    @property
    def ranged(self) -> bool:
        return bool(self.ammunition)


# ---------------------------------------------------------------------------
# The creature
# ---------------------------------------------------------------------------

@dataclass
class Preset:
    target: str        # Derived Character Attributes/(HP) Hit Points, Skills/..., ...
    node: ResourceNode
    key: str           # the field holding the number

    @property
    def label(self) -> str:
        return readable(self.target)

    @property
    def value(self) -> str:
        return self.node.get(self.key) or "0"


DROP_ROOT = "Inventory/Enemy Drop Items Cans/"


@dataclass
class Drop:
    """A drop recipe the death action calls: a shared can such as
    `Gold/Chance 1 in 2 SmallMedium Gold Pile Drop Action`."""
    node: ResourceNode       # the CUseCannedActionAction

    @property
    def ref(self) -> str:
        return (self.node.get("Canned Object") or "").strip()

    @property
    def label(self) -> str:
        return drop_label(self.ref)


def drop_label(ref: str) -> str:
    name = ref.rsplit("/", 1)[-1]
    name = re.sub(r"\s*Drop Action$", "", name, flags=re.I)
    folder = ref[len(DROP_ROOT):].rsplit("/", 1)[0] if "/" in ref[len(DROP_ROOT):] else ""
    return f"{folder}: {name}" if folder else name


@dataclass
class DropChoice:
    ref: str
    label: str
    comment: str


def drop_choices(lib: Library) -> list[DropChoice]:
    """Every drop recipe in the game, with the comment its author wrote."""
    out = []
    root = "Resources/" + DROP_ROOT
    for n in lib.names():
        if n.startswith(root) and n.lower().endswith(".can"):
            raw = lib.read(n)
            top = re.search(rb"\tObject=(C\w+)", raw[:300])
            if not top or not top.group(1).endswith(b"Action"):
                continue          # item lists the recipes use, not recipes
            m = re.search(rb"Comment=([^\r\n]*)", raw)
            ref = n[len("Resources/"):-len(".can")]
            out.append(DropChoice(ref, drop_label(ref),
                                  m.group(1).decode("latin-1").strip() if m else ""))
    return sorted(out, key=lambda c: c.label.lower())


@dataclass
class Sound:
    when: str          # "When hurt", "When it dies", ...
    node: ResourceNode # the CPlaySoundAction

    @property
    def file(self) -> str:
        return self.node.get("Sound") or ""


SOUND_WHEN = {
    "Damaged Effect Action": "When hurt",
    "Damaged Script Action": "When hurt",
    "Destroyed Effect Action": "When it dies",
    "Destroyed Script Action": "When it dies",
    "Hit Action": "When it hits",
    "Activity": "While active",
}


class Creature:
    def __init__(self, lib: Library, can: ResDoc, race: ResDoc | None):
        self.lib, self.can, self.race = lib, can, race
        if can.root.type_name != "CCannedObject" or not isinstance(can.root.get("Object"), ResourceNode):
            raise ItemError(f"{can.rel} is not a creature")

    @classmethod
    def from_library(cls, lib: Library, can_rel: str) -> "Creature":
        can = ResDoc.from_library(lib, can_rel)
        tmp = cls(lib, can, None)
        race_rel = "Resources/" + tmp.race_ref + ".Race" if tmp.race_ref else ""
        race = ResDoc.from_library(lib, race_rel) if race_rel and lib.exists(race_rel) else None
        return cls(lib, can, race)

    # -- parts ------------------------------------------------------------------

    @property
    def entity(self) -> ResourceNode:
        return self.can.root.get("Object")

    def activity(self, cls: str) -> ResourceNode | None:
        acts = child(self.entity, "Activity")
        return next((a for a in (acts.items() if acts else []) if a.type_name == cls), None)

    @property
    def profile(self) -> ResourceNode | None:
        return self.activity("CAICharacterProfile")

    @property
    def race_ref(self) -> str:
        return ((self.profile.get("Race") if self.profile is not None else "") or "").strip()

    @property
    def stem(self) -> str:
        return self.can.rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    @property
    def dirty(self) -> bool:
        return self.can.dirty or (self.race is not None and self.race.dirty)

    # -- basics -----------------------------------------------------------------

    @property
    def name(self) -> str:
        p = self.profile
        return ((p.get("User Assigned Name") if p is not None else "") or "").strip() or self.stem

    def set_name(self, text: str) -> None:
        if "\r" in text or "\n" in text:
            raise ItemError("The name must be one line.")
        self.profile.set("User Assigned Name", text)

    @property
    def level(self) -> str:
        return (self.profile.get("Character Level") or "1") if self.profile is not None else ""

    def set_level(self, text: str) -> None:
        self.profile.set("Character Level", parse_number(text, "Level"))

    @property
    def experience(self) -> str:
        """The creature's own Experience Points (Dynamic Properties). Its death action also
        carries a literal `Experience Points To Add`; which of the two a kill pays is still
        to be checked in the game, so the core reads both."""
        dp = child(self.entity, "Dynamic Properties")
        return (dp.get("Experience Points") or "") if dp is not None else ""

    def set_experience(self, text: str) -> None:
        dp = child(self.entity, "Dynamic Properties")
        if dp is None or dp.get("Experience Points") is None:
            raise ItemError("this creature has no experience value")
        dp.set("Experience Points", parse_number(text, "Experience"))

    def death_xp_awards(self) -> list[ResourceNode]:
        dea = child(self.entity, "Destroyed Effect Action")
        return [n for n in walk(dea) if n.type_name == "CGiveExperiencePointsToAllPlayersAction"] \
            if dea is not None else []

    @property
    def categories(self) -> list[str]:
        return [c.strip() for c in (self.entity.get("Category") or "").split(",") if c.strip()]

    # -- model --------------------------------------------------------------------

    @property
    def model(self) -> str:
        return (self.entity.get("Model") or "").strip()

    def sequences_used(self) -> list[str]:
        """Every sequence name the creature calls (on itself and its weapons)."""
        out = []
        for n in walk(self.entity):
            for k, v in n.fields:
                if k in SEQUENCE_KEYS and isinstance(v, str) and v.strip() \
                        and v.strip().lower() not in ENGINE_SEQUENCES and v.strip() not in out:
                    out.append(v.strip())
        return out

    def missing_sequences(self, model_ref: str | None = None) -> list[str]:
        info = model_info(self.lib, model_ref or self.model)
        if not info.exists:
            return self.sequences_used()
        return [s for s in self.sequences_used() if not info.finds(s)]

    def set_model(self, model_ref: str, force: bool = False) -> None:
        info = model_info(self.lib, model_ref)
        if not info.exists or not info.mesh:
            raise ItemError(f"There is no model called {model_ref}.")
        missing = self.missing_sequences(model_ref)
        if missing and not force:
            raise ItemError(f"{model_ref.rsplit('/', 1)[-1]} has no animation for: "
                            + ", ".join(missing) + ". The creature would freeze or fall back "
                            "to another model when it needs one.")
        self.entity.set("Model", model_ref)

    # -- stats (the race) ---------------------------------------------------------

    def _need_race(self) -> ResourceNode:
        if self.race is None:
            raise ItemError(f"This creature's race ({self.race_ref}) is missing, so its "
                            "stats can't be edited.")
        return self.race.root

    def attributes(self) -> list[tuple[str, str]]:
        """(Character Attributes/(ST) Strength, value) for the seven attributes."""
        arr = child(self.race.root, "Initial CharacterAttribute Values") if self.race else None
        return [(k, v.split(",")[0].strip()) for k, v in (arr.fields if arr else [])
                if isinstance(v, str) and k.startswith("Character Attributes/")]

    def set_attribute(self, target: str, text: str) -> None:
        arr = child(self._need_race(), "Initial CharacterAttribute Values")
        val = parse_number(text, readable(target))
        cur = arr.get(target)
        if cur is None:
            raise ItemError(f"{readable(target)} is not in this race")
        parts = [p.strip() for p in cur.split(",")]
        lo, hi = float(parts[1]), float(parts[2])
        # the triple is value,min,max; widen the range rather than refuse a value outside it
        parts[0] = val
        if float(val) < lo:
            parts[1] = val
        if float(val) > hi:
            parts[2] = val
        arr.set(target, ",".join(parts))

    def presets(self) -> list[Preset]:
        """HP, AC, resistances and the rest of the race's derived presets."""
        arr = child(self.race.root, "DerivedCharacterAttribute") if self.race else None
        return [Preset(p.get("Derived Character Attribute") or "", p, "Value To Preset")
                for p in (arr.items() if arr else [])
                if p.type_name == "CPresetDerivedCharacterAttribute"]

    def skills(self) -> list[Preset]:
        arr = child(self.race.root, "Skill") if self.race else None
        return [Preset(p.get("Skill") or "", p, "Value To Preset")
                for p in (arr.items() if arr else []) if p.type_name == "CPresetSkill"]

    def preset(self, target: str) -> Preset | None:
        t = target.lower()
        return next((p for p in self.presets() + self.skills() if p.target.lower() == t), None)

    def set_preset(self, target: str, text: str) -> Preset:
        """Set a derived stat or skill on the race, adding the preset if it has none."""
        root = self._need_race()
        val = parse_number(text, readable(target))
        p = self.preset(target)
        if p is not None:
            p.node.set("Value To Preset", val)
            return p
        if target.startswith("Skills/"):
            arr_key, cls, key = "Skill", "CPresetSkill", "Skill"
        elif target.startswith("Derived Character Attributes/"):
            arr_key, cls, key = ("DerivedCharacterAttribute", "CPresetDerivedCharacterAttribute",
                                 "Derived Character Attribute")
        else:
            raise ItemError(f"{target} is not a stat a race presets")
        arr = child(root, arr_key)
        if arr is None:
            arr = ResourceNode("Array", [("Item Count", "0")])
            root.set(arr_key, arr)
        node = ResourceNode(cls, [(key, target), ("Value To Preset", val)])
        item_key = "Skill Preset" if cls == "CPresetSkill" else "DerivedCharacterAttribute Preset"
        arr.fields.append((item_key, node))
        arr.set("Item Count", str(len(arr.items())))
        return Preset(target, node, "Value To Preset")

    def remove_preset(self, target: str) -> None:
        p = self.preset(target)
        if p is None:
            return
        for arr_key in ("DerivedCharacterAttribute", "Skill"):
            arr = child(self._need_race(), arr_key)
            if arr is not None and any(v is p.node for _k, v in arr.fields):
                arr.fields = [(k, v) for k, v in arr.fields if v is not p.node]
                arr.set("Item Count", str(len(arr.items())))

    def race_users(self, index: dict[str, list[str]] | None = None) -> list[str]:
        """Every creature can that uses this creature's race, this one included."""
        index = index if index is not None else race_index(self.lib)
        return index.get(self.race_ref.lower(), [])

    def fork_race(self, new_stem: str | None = None) -> None:
        """Give this creature a race of its own: a copy beside the original, named after
        the can, so stat edits stop changing every creature that shared it."""
        root = self._need_race()
        folder = self.race.rel.rsplit("/", 1)[0]
        stem = new_stem or self.stem
        rel, n = f"{folder}/{stem}.Race", 2
        while self.lib.exists(rel) or rel.lower() == self.race.rel.lower():
            rel = f"{folder}/{stem} {n}.Race"
            n += 1
        self.race = ResDoc(rel, root.to_text().encode("latin-1"), "new")
        self.profile.set("Race", self.race.ref)

    # -- weapons and sounds -------------------------------------------------------

    def weapons(self) -> list[WeaponView]:
        inv = self.activity("CAIInventory")
        if inv is None:
            return []
        out = []
        d = inv.get("Default Inventory Item")
        if isinstance(d, ResourceNode) and d.type_name == "CInventoryItem":
            out.append(WeaponView(d, "default"))
        arr = child(inv, "Inventory Items")
        for it in (arr.items() if arr else []):
            if it.type_name == "CInventoryItem":
                out.append(WeaponView(it, "carried"))
        return out

    def sounds(self) -> list[Sound]:
        out = []
        for key, value in self.entity.fields:
            if not isinstance(value, ResourceNode):
                continue
            when = SOUND_WHEN.get(key)
            if when is None:
                continue
            for n in walk(value):
                if n.type_name == "CPlaySoundAction" and isinstance(n.get("Sound"), str):
                    out.append(Sound(when, n))
        return out

    def set_sound(self, sound: Sound, file: str) -> None:
        if not file.strip():
            raise ItemError("Pick a sound file.")
        sound.node.set("Sound", file.strip())

    # -- drops ----------------------------------------------------------------------

    def _death(self) -> ResourceNode | None:
        return child(self.entity, "Destroyed Effect Action")

    def drops(self) -> list[Drop]:
        d = self._death()
        return [Drop(n) for n in walk(d) if n.type_name == "CUseCannedActionAction"
                and (n.get("Canned Object") or "").startswith(DROP_ROOT)] if d is not None else []

    def item_generators(self) -> list[str]:
        """What the death action generates directly (85 creatures), as readable names."""
        d = self._death()
        out = []
        for g in (walk(d) if d is not None else []):
            if g.type_name != "CGenerateInventoryItemAction":
                continue
            for n in walk(g):
                for key in ("Item", "Canned Item List", "Addition"):
                    v = n.get(key)
                    if isinstance(v, str) and "/" in v:
                        out.append(readable(v))
        return out

    def _drop_array(self) -> ResourceNode | None:
        """The action list the drops belong in: the one already holding drops or the
        experience award, inside the was-it-killed-by-the-player check."""
        d = self._death()
        for n in walk(d) if d is not None else []:
            if n.type_name != "Array":
                continue
            kinds = {v.type_name for _k, v in n.fields if isinstance(v, ResourceNode)}
            if "CGiveExperiencePointsToAllPlayersAction" in kinds or any(
                    v.type_name == "CUseCannedActionAction" and
                    (v.get("Canned Object") or "").startswith(DROP_ROOT)
                    for _k, v in n.fields if isinstance(v, ResourceNode)):
                return n
        return None

    def set_drop(self, drop: Drop, ref: str) -> None:
        if not ref.startswith(DROP_ROOT):
            raise ItemError("Pick one of the game's drop recipes.")
        drop.node.set("Canned Object", ref)

    def add_drop(self, ref: str) -> Drop:
        if not ref.startswith(DROP_ROOT):
            raise ItemError("Pick one of the game's drop recipes.")
        arr = self._drop_array()
        if arr is None:
            raise ItemError("This creature's death action has no place for drops, so the "
                            "editor can't add one safely.")
        node = ResourceNode("CUseCannedActionAction", [("Canned Object", ref)])
        arr.fields.append(("Action", node))
        arr.set("Item Count", str(len(arr.items())))
        return Drop(node)

    def remove_drop(self, drop: Drop) -> None:
        d = self._death()
        for n in walk(d) if d is not None else []:
            if n.type_name == "Array" and any(v is drop.node for _k, v in n.fields):
                n.fields = [(k, v) for k, v in n.fields if v is not drop.node]
                n.set("Item Count", str(len(n.items())))
                return

    # -- checks -------------------------------------------------------------------

    def problems(self) -> list[str]:
        out = []
        for where, name in canedit_core.find_abstract(self.can.root):
            out.append(f"{name} at {where} crashes the game when the creature loads.")
        missing = self.missing_sequences()
        if missing:
            out.append(f"Its model has no animation for: {', '.join(missing)}.")
        if self.race is None:
            out.append(f"Its race {self.race_ref} does not exist.")
        else:
            labels = {p.target.lower() for p in self.presets()}
            for need in ("Derived Character Attributes/(HP) Hit Points",
                         "Derived Character Attributes/(AC) Armor Class"):
                if need.lower() not in labels:
                    out.append(f"Its race ({self.race_ref.rsplit('/', 1)[-1]}) sets no "
                               f"{readable(need)}, so it starts with none. A player race on a "
                               "monster does this.")
        if not self.name.strip():
            out.append("It has no name for the combat log.")
        return out

    # -- saving and copying -------------------------------------------------------

    def save_into_mod(self, mod: Path) -> list[Path]:
        written = []
        if self.race is not None and self.race.dirty:
            written.append(self.race.save_into_mod(mod))
        if self.can.dirty:
            written.append(self.can.save_into_mod(mod))
        return written

    def clone(self, new_stem: str, new_name: str) -> "Creature":
        """A new creature beside this one, with its own copy of the race, so editing the
        copy never changes the original. Nothing is written until saved."""
        new_stem = new_stem.strip()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9 '\-_,.()]*$", new_stem):
            raise ItemError("Use letters, numbers, spaces and simple punctuation for the file "
                            "name.")
        folder = self.can.rel.rsplit("/", 1)[0]
        can_rel = f"{folder}/{new_stem}.can"
        if self.lib.exists(can_rel):
            raise ItemError(f"There is already a creature called {new_stem} there; pick "
                            "another name.")
        can = ResDoc(can_rel, self.can.to_bytes(), "new")
        new = Creature(self.lib, can, None)
        if self.race is not None:
            new.race = ResDoc(self.race.rel, self.race.to_bytes(), self.race.origin)
            new.fork_race(new_stem)
        if new_name.strip():
            new.set_name(new_name.strip())
        return new


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

def creature_cans(lib: Library) -> list[str]:
    return [n for n in lib.names() if n.startswith(CAN_ROOT) and n.lower().endswith(".can")]


RACE_RE = re.compile(rb"\n\t+Race=([^\r\n]+)")


def race_index(lib: Library) -> dict[str, list[str]]:
    """race ref (lower) -> creature cans that use it."""
    out: dict[str, list[str]] = {}
    for n in creature_cans(lib):
        for m in RACE_RE.finditer(lib.read(n)):
            out.setdefault(m.group(1).decode("latin-1").strip().lower(), []).append(n)
            break
    return out


def sound_choices(lib: Library) -> list[str]:
    """Every sound a creature can play, as `Sound=` names them (without `Sounds/`)."""
    return sorted((n[len("Sounds/"):] for n in lib.names()
                   if n.startswith("Sounds/") and n.lower().endswith((".ogg", ".wav"))),
                  key=str.lower)


def tier_of(stem: str) -> str:
    s = stem.lower()
    return "Super" if s.endswith(" super") else "Tough" if s.endswith(" tough") else ""
