"""Headless core for the item editor.

UI-independent and testable without Qt. An item is one of two file types:

* a **base item** (`.InventoryItem`): the sword, the ring, the gem, and the named uniques,
  which are self-contained base items with their magic merged in;
* a **magic addition** (`.InventoryAddition`): what a random magic roll adds to a base
  item -- the "of Poison Resistance" in "Helmet of Poison Resistance".

This module reads them through `icontool.Library` (the mod's `files/` shadows the vanilla
archive), recognises the parts a person wants to change and exposes each one as a small
object a card in the window can show:

* `Basics`: name, description, price, weight, rarity;
* `DamageLine`: one entry of a damage behaviour (type and range);
* `StatLine`: one "while worn" modifier (which stat, how much, percent or not);
* `Roll`: an addition's weakest / middle / strongest roll and the price at each.

Anything the core does not recognise is left exactly as it is. The load/save contract is
the other editors': an item loaded and saved unedited is byte-identical (true of all 536
shipped item files), and one field edit is a one-line diff.

**What an edit reaches.** A save stores every item the player carries, and every item
lying in a visited map, as a full copy: damage, price, name, description, art and the
rolled bonus are all written into the save. So a change to an item file reaches new
copies only -- loot not yet rolled, maps not yet visited, a new game. `REACH_NOTE` says
this in the window.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import mdl16_format
from canedit_core import register_in_mod_json
from icontool import Library, parse_item
from resource_format import ResourceNode, parse_resource_text

ITEM_EXT = ".InventoryItem"
ADDITION_EXT = ".InventoryAddition"
CR = "\r\n"

REACH_NOTE = ("A saved game keeps its own copy of every item the player already has or has "
              "seen lying in a map. Your change shows up on items found from now on and in "
              "a new game, not on ones already picked up.")

DAMAGE_TYPES = ("Slashing", "Piercing", "Crushing", "Fire", "Cold", "Electrical", "Acid",
                "Poison", "Disease")

# The three modifier classes items use, keyed by how a person would say it. Each names
# its target in a different field.
STAT_KINDS = {
    "CCharacterModifierDerivedAttribute": ("Derived character attribute to modify",
                                           "Derived Character Attributes"),
    "CCharacterModifierSkill": ("Skill to modify", "Skills"),
    "CCharacterModifierAttribute": ("Character attribute to modify", "Character Attributes"),
}
# Folders of derived attributes that are engine bookkeeping, not stats a player has.
HIDDEN_STAT_FOLDERS = ("Game Scripting Variables", "Perk and Trait Support")

PLACEHOLDER = re.compile(r"%([is])\[([^\]]*)\]")


class ItemError(Exception):
    """A request the core refuses, with a message written for the person using it."""


# ---------------------------------------------------------------------------
# Small tree helpers
# ---------------------------------------------------------------------------

def child(node: ResourceNode | None, key: str) -> ResourceNode | None:
    v = node.get(key) if node is not None else None
    return v if isinstance(v, ResourceNode) else None


def walk(node: ResourceNode):
    yield node
    for _, v in node.fields:
        if isinstance(v, ResourceNode):
            yield from walk(v)


def constant(value: str) -> ResourceNode:
    return ResourceNode("CConstant", [("Constant Value", value)])


def fmt_number(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:g}"


def parse_number(text: str, what: str) -> str:
    """The text as the file should store it, or ItemError naming the field."""
    t = text.strip()
    try:
        float(t)
    except ValueError:
        raise ItemError(f"{what} must be a number, not \"{text}\".") from None
    return t


# Engine names that do not say what they do. Only names whose meaning is certain.
FRIENDLY = {
    "derived character attributes/speed/attack animation speed multiplier": "Attack speed",
    "derived character attributes/speed/ranged attack animation speed multiplier":
        "Ranged attack speed",
    "derived character attributes/speed/walk animation speed multiplier": "Walking speed",
    "derived character attributes/monster summoning number of monsters":
        "Number of creatures summoned",
    "derived character attributes/carryweight": "Carrying capacity",
    "derived character attributes/healingrate": "Healing rate",
    "derived character attributes/damage related/melee damage percentage gives health to "
    "attacker zero to one": "Melee damage returned as health (0 to 1)",
}


def readable(ref: str) -> str:
    """`Derived Character Attributes/(AC) Armor Class` -> `Armor Class`;
    `.../CarryWeight` -> `Carry Weight`."""
    if ref.lower() in FRIENDLY:
        return FRIENDLY[ref.lower()]
    name = ref.rsplit("/", 1)[-1]
    name = re.sub(r"^\([A-Z]{2}\)\s*", "", name)
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return name


# ---------------------------------------------------------------------------
# Expressions: enough of an evaluator to fill in a tooltip
# ---------------------------------------------------------------------------

Interval = tuple[float, float]


def evaluate(node, amount: float, exprs: dict[str, ResourceNode],
             depth: int = 0) -> Interval | None:
    """The range an expression can take when the addition rolled `amount`, or None when
    it depends on something only the game knows (a skill, a perk, a script variable).

    A condition is treated as "either branch", so a skill ladder evaluates to its whole
    span; that is what a tooltip wants."""
    if not isinstance(node, ResourceNode) or depth > 30:
        return None
    t = node.type_name

    def sub(key):
        return evaluate(node.get(key), amount, exprs, depth + 1)

    try:
        if t == "CConstant":
            v = float(node.get("Constant Value", "0") or 0)
            return (v, v)
        if t == "CInventoryAdditionAmmountExpression":
            return (amount, amount)
        if t == "CInventoryAdditionsNamedExpressionExpression":
            target = exprs.get((node.get("Expression Name") or "").lower())
            return evaluate(target, amount, exprs, depth + 1) if target else None
        if t == "CRandom":
            a, b = sub("Minimum"), sub("Maximum")
            return (a[0], b[1]) if a and b else None
        if t in ("CAdd", "CSubtract", "CMultiply", "CDivide"):
            a, b = sub("Operand1"), sub("Operand2")
            if not a or not b:
                return None
            if t == "CAdd":
                return (a[0] + b[0], a[1] + b[1])
            if t == "CSubtract":
                return (a[0] - b[1], a[1] - b[0])
            prods = ([x * y for x in a for y in b] if t == "CMultiply"
                     else [x / y for x in a for y in b if y])
            return (min(prods), max(prods)) if prods else None
        if t == "CExpressionScale":
            v, i0, i1, o0, o1 = (sub(k) for k in ("Input Value", "Input Minimum",
                                                   "Input Maximum", "Output Minimum",
                                                   "Output Maximum"))
            if not all((v, i0, i1, o0, o1)) or i1[0] == i0[0]:
                return None

            def scale(x):
                return o0[0] + (x - i0[0]) * (o1[0] - o0[0]) / (i1[0] - i0[0])
            lo, hi = scale(v[0]), scale(v[1])
            return (min(lo, hi), max(lo, hi))
        if t == "CCacheFirstResult":
            return sub("Operand1")
        if t == "CIfExpression":
            a, b = sub("Value if True "), sub("Value if False")
            return (min(a[0], b[0]), max(a[1], b[1])) if a and b else None
    except (TypeError, ValueError):
        return None
    return None


def fmt_interval(iv: Interval | None) -> str:
    if iv is None:
        return "?"
    lo, hi = int(round(iv[0])), int(round(iv[1]))
    return str(lo) if lo == hi else f"{lo}-{hi}"


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

class ItemDocument:
    """One item file, parsed. Edits mutate `root` in place.

    `origin` is "vanilla", "mod" (the open mod's own copy) or "new" (made in this
    session, not yet saved). A vanilla item is never written back; saving puts a copy in
    the mod, after which the document is the mod's file."""

    def __init__(self, rel: str, raw: bytes, origin: str):
        self.rel = rel
        self._raw = raw
        self.root = parse_resource_text(raw.decode("latin-1"))
        if self.root.type_name not in ("CInventoryItem", "CInventoryAddition"):
            raise ItemError(f"{rel} is not an item: its root is {self.root.type_name}")
        self.origin = origin

    @classmethod
    def from_library(cls, lib: Library, rel: str) -> "ItemDocument":
        rel = lib.canonical(rel)
        return cls(rel, lib.read(rel), lib.where(rel))

    # -- identity -----------------------------------------------------------

    @property
    def is_addition(self) -> bool:
        return self.root.type_name == "CInventoryAddition"

    @property
    def kind(self) -> str:
        if self.is_addition:
            return "magic addition"
        if "/unique magic items/" in self.rel.lower():
            return "unique item"
        return "item"

    @property
    def ref(self) -> str:
        """How the game names this file: no Resources/ prefix, no extension."""
        return item_ref(self.rel)

    @property
    def stem(self) -> str:
        return self.rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    @property
    def name(self) -> str:
        return (self.root.get("Display Name") or "").strip() or self.stem

    # -- persistence -----------------------------------------------------------

    def to_bytes(self) -> bytes:
        return self.root.to_text().encode("latin-1")

    @property
    def dirty(self) -> bool:
        return self.origin == "new" or self.to_bytes() != self._raw

    def save_into_mod(self, mod_dir: Path) -> Path:
        """Write `<mod>/files/<rel>` and list it in the mod's mod.json. Afterwards the
        document is the mod's copy."""
        mod_dir = Path(mod_dir)
        if not (mod_dir / "mod.json").is_file():
            raise ItemError(f"{mod_dir} has no mod.json, so it is not a mod folder.")
        data = self.to_bytes()
        parse_resource_text(data.decode("latin-1"))   # never write what will not load
        target = mod_dir / "files" / Path(self.rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        register_in_mod_json(mod_dir, self.rel)
        self._raw = data
        self.origin = "mod"
        return target

    # -- the parts ---------------------------------------------------------------

    def behaviours(self) -> list[ResourceNode]:
        arr = child(self.root, "PlugIn Behaviors")
        return arr.items() if arr else []

    def expressions(self) -> dict[str, ResourceNode]:
        """The addition's named expressions, by lower-cased name."""
        arr = child(child(self.root, "Expressions"), "Expressions")
        out = {}
        for it in (arr.items() if arr else []):
            name = (it.get("Name") or "").strip().lower()
            expr = it.get("Expression")
            if name and isinstance(expr, ResourceNode):
                out[name] = expr
        return out


def item_ref(rel: str) -> str:
    r = rel.replace("\\", "/")
    if r.lower().startswith("resources/"):
        r = r[len("resources/"):]
    return r.rsplit(".", 1)[0]


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------

@dataclass
class Field:
    key: str          # the file's field name
    label: str        # what the window calls it
    numeric: bool = False
    help: str = ""


BASE_FIELDS = (
    Field("Display Name", "Name"),
    Field("Description", "Description",
          help="%s[damage] is replaced by the item's damage, e.g. \"Slashing Damage 1-5\"."),
    Field("Encumbrance", "Weight", numeric=True),
)
ADDITION_FIELDS = (
    Field("Display Name", "Name",
          help="Shown after the base item's name: \"Helmet of\" + this name."),
    Field("Description", "Description",
          help="%i[name] is replaced by the named amount; a % right after it is a "
               "plain percent sign."),
)


def basic_fields(doc: ItemDocument) -> tuple[Field, ...]:
    return ADDITION_FIELDS if doc.is_addition else BASE_FIELDS


def get_field(doc: ItemDocument, key: str) -> str:
    v = doc.root.get(key)
    return v if isinstance(v, str) else ""


def set_field(doc: ItemDocument, key: str, text: str, numeric: bool = False,
              label: str = "") -> tuple[str, str]:
    """Set a top-level text field. Returns (old, new). Refuses line breaks, which the
    format cannot hold in a one-line field."""
    if "\r" in text or "\n" in text:
        raise ItemError(f"{label or key} must be one line.")
    new = parse_number(text, label or key) if numeric else text
    old = get_field(doc, key)
    if doc.root.get(key) is None:
        raise ItemError(f"this item has no {label or key} field")
    doc.root.set(key, new)
    return old, new


def price_node(doc: ItemDocument) -> ResourceNode | None:
    """The CConstant a base item's price lives in, or None if it is a formula."""
    v = child(doc.root, "Value")
    return v if v is not None and v.type_name == "CConstant" else None


def get_price(doc: ItemDocument) -> str:
    v = price_node(doc)
    return (v.get("Constant Value") or "0") if v is not None else ""


def set_price(doc: ItemDocument, text: str) -> tuple[str, str]:
    v = price_node(doc)
    if v is None:
        raise ItemError("this item's price is a formula, not a number")
    new = parse_number(text, "Price")
    old = v.get("Constant Value") or "0"
    v.set("Constant Value", new)
    return old, new


# ---------------------------------------------------------------------------
# An addition's roll: weakest / middle / strongest
# ---------------------------------------------------------------------------

ROLL_STEPS = (("Minimum", "Weakest"), ("Inbetween", "Middle"), ("Maximum", "Strongest"))


@dataclass
class RollStep:
    prefix: str       # Minimum / Inbetween / Maximum
    label: str
    amount: str       # the rolled number the named amounts are built from
    price: str        # what the magic adds to the price at this roll


def roll(doc: ItemDocument) -> list[RollStep]:
    if not doc.is_addition:
        return []
    return [RollStep(p, label, get_field(doc, f"{p} Addition Ammount"),
                     get_field(doc, f"{p} Value")) for p, label in ROLL_STEPS]


def set_roll(doc: ItemDocument, prefix: str, which: str, text: str) -> tuple[str, str]:
    key = f"{prefix} Addition Ammount" if which == "amount" else f"{prefix} Value"
    label = dict(ROLL_STEPS)[prefix] + (" roll" if which == "amount" else " price")
    return set_field(doc, key, text, numeric=True, label=label)


def set_flat_price(doc: ItemDocument, text: str) -> None:
    """For magic whose numbers do not depend on the roll: the same price at every roll."""
    new = parse_number(text, "Price")
    for prefix, _label in ROLL_STEPS:
        set_field(doc, f"{prefix} Value", new, numeric=True, label="Price")


def roll_amounts(doc: ItemDocument) -> dict[str, float]:
    out = {}
    for step in roll(doc):
        try:
            out[step.label] = float(step.amount)
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------------------
# Damage
# ---------------------------------------------------------------------------

@dataclass
class DamageLine:
    """One entry in a damage behaviour.

    kind:
      "range"   a plain min-max the person can edit;
      "ladder"  a min-max plus the shared skill ladder and specialization bonus (every
                melee base weapon); the min-max is editable and the ladder is kept;
      "rolled"  an addition's damage built from its roll (edit the roll instead);
      "fixed"   a single number;
      "over time" damage over time; shown, not yet editable;
      "formula" anything else; shown, left alone.
    """
    behaviour: ResourceNode
    node: ResourceNode          # the CXRPGDamage / CXRPGDamageOverTime
    kind: str
    lo: ResourceNode | None = None    # the CConstant holding the minimum
    hi: ResourceNode | None = None    # the CConstant holding the maximum
    bonus: ResourceNode | None = None  # the ladder, for "ladder"

    @property
    def damage_type(self) -> str:
        return readable(self.node.get("Damage Type") or "")

    @property
    def editable(self) -> bool:
        return self.kind in ("range", "ladder", "fixed")


def _is_const_random(n) -> bool:
    return (isinstance(n, ResourceNode) and n.type_name == "CRandom"
            and isinstance(n.get("Minimum"), ResourceNode)
            and n.get("Minimum").type_name == "CConstant"
            and isinstance(n.get("Maximum"), ResourceNode)
            and n.get("Maximum").type_name == "CConstant")


def classify_damage(behaviour: ResourceNode, d: ResourceNode) -> DamageLine:
    amt = d.get("Damage Amount")
    if d.type_name == "CXRPGDamageOverTime":
        return DamageLine(behaviour, d, "over time")
    if _is_const_random(amt):
        return DamageLine(behaviour, d, "range", amt.get("Minimum"), amt.get("Maximum"))
    if isinstance(amt, ResourceNode) and amt.type_name == "CConstant":
        return DamageLine(behaviour, d, "fixed", amt, amt)
    if (isinstance(amt, ResourceNode) and amt.type_name == "CAdd"
            and _is_const_random(amt.get("Operand1"))):
        r = amt.get("Operand1")
        return DamageLine(behaviour, d, "ladder", r.get("Minimum"), r.get("Maximum"),
                          amt.get("Operand2"))
    if isinstance(amt, ResourceNode) and any(
            n.type_name == "CInventoryAdditionsNamedExpressionExpression" for n in walk(amt)):
        return DamageLine(behaviour, d, "rolled")
    return DamageLine(behaviour, d, "formula")


def damage_lines(doc: ItemDocument) -> list[DamageLine]:
    out = []
    for b in doc.behaviours():
        if b.type_name != "CPlugInBehaviorDamage":
            continue
        arr = child(b, "Damages")
        for d in (arr.items() if arr else []):
            if d.type_name in ("CXRPGDamage", "CXRPGDamageOverTime"):
                out.append(classify_damage(b, d))
    return out


def damage_range(line: DamageLine) -> tuple[str, str]:
    if not line.editable:
        return "", ""
    return (line.lo.get("Constant Value") or "0", line.hi.get("Constant Value") or "0")


def set_damage_range(line: DamageLine, lo: str, hi: str) -> None:
    if not line.editable:
        raise ItemError("this damage is not a plain range")
    lo = parse_number(lo, "Minimum damage")
    hi = parse_number(hi, "Maximum damage")
    if float(lo) > float(hi):
        raise ItemError("Minimum damage is more than maximum damage.")
    if line.kind == "fixed" and lo != hi:
        # A single number becomes a range: the one structural edit here.
        line.node.set("Damage Amount", ResourceNode("CRandom", [
            ("Operator", ""), ("Minimum", constant(lo)), ("Maximum", constant(hi))]))
        amt = line.node.get("Damage Amount")
        line.kind, line.lo, line.hi = "range", amt.get("Minimum"), amt.get("Maximum")
        return
    line.lo.set("Constant Value", lo)
    if line.hi is not line.lo:
        line.hi.set("Constant Value", hi)


def set_damage_type(line: DamageLine, name: str) -> None:
    if name not in DAMAGE_TYPES:
        raise ItemError(f"unknown damage type {name}")
    line.node.set("Damage Type", f"Damage Types/{name}")


def ladder_span(line: DamageLine) -> Interval | None:
    return evaluate(line.bonus, 0, {}) if line.kind == "ladder" else None


def damage_entry(damage_type: str, lo: str, hi: str) -> ResourceNode:
    if damage_type not in DAMAGE_TYPES:
        raise ItemError(f"unknown damage type {damage_type}")
    lo, hi = parse_number(lo, "Minimum damage"), parse_number(hi, "Maximum damage")
    if float(lo) > float(hi):
        raise ItemError("Minimum damage is more than maximum damage.")
    return ResourceNode("CXRPGDamage", [
        ("Damage Type", f"Damage Types/{damage_type}"),
        ("Damage Amount", ResourceNode("CRandom", [
            ("Operator", ""), ("Minimum", constant(lo)), ("Maximum", constant(hi))])),
    ])


HIT_OR_MISS = "Damage Types/Damage Hit Or Miss/"
# Who extra damage lands on, by the hit condition its behaviour names. Each condition
# misses everything that is not that kind of creature. Dragons and Titans are defined by
# the game but no original item uses them.
EXTRA_AGAINST = {
    "Everything": "AlwaysHit",
    "Undead": "Special conditions/HitUndeadOnly",
    "Goblins": "Special conditions/HitGoblinsOnly",
    "Dragons": "Special conditions/HitDragonsOnly",
    "Titans": "Special conditions/HitTitansOnly",
}
UNTRIED_AGAINST = ("Dragons", "Titans")


def extra_against(line: DamageLine) -> str | None:
    """"Everything", "Undead"... for an extra damage behaviour, or None when its hit
    condition is the weapon's own (one- or two-handed, ranged)."""
    h = (line.behaviour.get("Hit Or Miss") or "")
    for name, tail in EXTRA_AGAINST.items():
        if h.lower() == (HIT_OR_MISS + tail).lower():
            return name
    return None


def set_extra_against(line: DamageLine, name: str) -> None:
    if name not in EXTRA_AGAINST or extra_against(line) is None:
        raise ItemError("only extra damage can be aimed at one kind of creature")
    line.behaviour.set("Hit Or Miss", HIT_OR_MISS + EXTRA_AGAINST[name])


def add_extra_damage(doc: ItemDocument, damage_type: str, lo: str, hi: str,
                     against: str = "Everything") -> DamageLine:
    """Extra damage on every successful strike, built the way vanilla's elemental
    suffixes are: a damage behaviour of its own whose hit condition always hits, so it
    lands whenever the weapon's own blow does. `against` narrows it to one kind of
    creature, the way the Ivory Mace's bonus against the undead works."""
    if against not in EXTRA_AGAINST:
        raise ItemError(f"unknown target {against}")
    entry = damage_entry(damage_type, lo, hi)
    behaviour = ResourceNode("CPlugInBehaviorDamage", [
        ("Hit Or Miss", HIT_OR_MISS + EXTRA_AGAINST[against]),
        ("Cache Hit Or Miss Result", "1"),
        ("Hit Margin", "0"),
        ("Damages", ResourceNode("Array", [("Item Count", "1"), ("Damage", entry)])),
    ])
    array_append(child(doc.root, "PlugIn Behaviors"), "PlugIn Behavior", behaviour)
    return classify_damage(behaviour, entry)


def extra_damage_sentence(line: DamageLine, amount: float = 0, exprs=None) -> str:
    against = extra_against(line)
    if against is None or line.kind == "over time":
        return ""
    rng = fmt_interval(evaluate(line.node.get("Damage Amount"), amount, exprs or {}))
    if "?" in rng:
        return ""
    who = "" if against == "Everything" else f" against {against.lower()}"
    return f"Successful strikes deal an extra {rng} {line.damage_type} damage{who}."


def remove_damage(doc: ItemDocument, line: DamageLine) -> None:
    """Remove one damage entry. A behaviour left with nothing is removed too. The
    weapon's own damage cannot be removed: a weapon that deals nothing is broken."""
    lines = damage_lines(doc)
    if not doc.is_addition and lines and line.behaviour is lines[0].behaviour:
        rest = [ln for ln in lines if ln.behaviour is line.behaviour and ln.node is not line.node]
        if not rest:
            raise ItemError("This is the weapon's own damage. Change it instead of removing "
                            "it.")
    arr = child(line.behaviour, "Damages")
    array_remove(arr, line.node)
    if not arr.items():
        array_remove(child(doc.root, "PlugIn Behaviors"), line.behaviour)


# ---------------------------------------------------------------------------
# On a hit: a chance to deal damage, at once or over time
# ---------------------------------------------------------------------------

# A damage over time is dealt in one tick every 5 seconds, and its Damage Amount is the
# TOTAL (Fixt engine notes, settled in the binary). Hitting again refreshes it; it never
# stacks. The game's own over-time effects deal 0.20 to 1.20 damage a second.
TICK_SECONDS = 5
DOT_BAND = (0.20, 1.20)


@dataclass
class OnHit:
    """One "when a strike lands" effect.

    kind: "instant" or "over time" when the editor recognises it; "other" for the many
    one-off effects (fear, freezing, healing the wielder...), which are described and
    left alone."""
    behaviour: ResourceNode
    kind: str
    chance: ResourceNode | None = None   # the Operand2 the 0-100 roll is compared with
    damage: ResourceNode | None = None   # CXRPGDamage or CXRPGDamageOverTime
    summary: str = ""

    @property
    def chance_fixed(self) -> bool:
        return self.chance is None or self.chance.type_name == "CConstant"

    @property
    def per_hit(self) -> ResourceNode | None:
        """The CRandom holding the per-hit (instant) or per-tick (over time) range, when
        it is plain numbers."""
        amt = self.damage.get("Damage Amount") if self.damage is not None else None
        if self.kind == "over time" and isinstance(amt, ResourceNode) \
                and amt.type_name == "CMultiply":
            amt = amt.get("Operand1")
        return amt if _is_const_random(amt) else None

    @property
    def ticks_node(self) -> ResourceNode | None:
        amt = self.damage.get("Damage Amount") if self.damage is not None else None
        if self.kind == "over time" and isinstance(amt, ResourceNode) \
                and amt.type_name == "CMultiply":
            t = amt.get("Operand2")
            return t if isinstance(t, ResourceNode) and t.type_name == "CConstant" else None
        return None

    @property
    def duration_node(self) -> ResourceNode | None:
        d = self.damage.get("Duration") if self.damage is not None else None
        return d if isinstance(d, ResourceNode) and d.type_name == "CConstant" else None

    @property
    def editable(self) -> bool:
        if self.kind == "instant":
            return self.per_hit is not None and self.chance_fixed
        if self.kind == "over time":
            return (self.per_hit is not None and self.chance_fixed
                    and self.ticks_node is not None and self.duration_node is not None)
        return False

    @property
    def damage_type(self) -> str:
        return readable(self.damage.get("Damage Type") or "") if self.damage is not None else ""


def _is_hit_check(n) -> bool:
    return (isinstance(n, ResourceNode) and n.type_name == "CIsGreaterThan"
            and isinstance(n.get("Operand1"), ResourceNode)
            and n.get("Operand1").type_name == "CExpressionHitMargin")


def _chance_test(n) -> ResourceNode | None:
    """Operand2 of `CRandom(0..100) < X`, or None."""
    if not (isinstance(n, ResourceNode) and n.type_name in ("CIsLessThan", "CIsLessThanOrEqual")):
        return None
    r = n.get("Operand1")
    if not _is_const_random(r):
        return None
    if (r.get("Minimum").get("Constant Value") or "0") != "0" or \
            (r.get("Maximum").get("Constant Value") or "") != "100":
        return None
    x = n.get("Operand2")
    return x if isinstance(x, ResourceNode) else None


def _describe_action(n) -> str:
    names = {
        "CActionDoDamage": "deals damage", "CAssignTemporaryTaskAction": "makes the target "
        "freeze or flee", "CGiveHealthToCharacterAction": "heals", "CAddCharacterModifierTo"
        "CharacterAction": "changes the target's stats for a while", "CDelayWithExpressionAction":
        "acts after a delay", "CDelayAction": "acts after a delay", "CSpawnEffectAction":
        "shows an effect"}
    seen = []
    for m in walk(n):
        s = names.get(m.type_name)
        if s and s not in seen:
            seen.append(s)
    return ", ".join(seen) if seen else "runs a script"


def classify_on_hit(b: ResourceNode) -> OnHit:
    root = b.get("Strike Action")
    other = OnHit(b, "other", summary="A special effect that " + (
        _describe_action(root) if isinstance(root, ResourceNode) else "does nothing") +
        ". This editor leaves it as it is.")
    if not (isinstance(root, ResourceNode) and root.type_name == "CIfExpressionAction"
            and _is_hit_check(root.get("If Expression"))):
        return other
    t = root.get("Then")
    chance = None
    if isinstance(t, ResourceNode) and t.type_name == "CIfExpressionAction":
        chance = _chance_test(t.get("If Expression"))
        if chance is None:
            return other
        t = t.get("Then")
    if isinstance(t, ResourceNode) and t.type_name == "CMultipleActionsAction":
        # one damage, plus at most a visual effect or a sound
        acts = child(t, "Action")
        items = acts.items() if acts else []
        hits = [a for a in items if a.type_name == "CActionDoDamage"]
        cosmetic = all(a.type_name in ("CActionDoDamage", "CSpawnEffectAction",
                                       "CPlaySoundAction") for a in items)
        t = hits[0] if len(hits) == 1 and cosmetic else None
    if not (isinstance(t, ResourceNode) and t.type_name == "CActionDoDamage"):
        return other
    dmg = child(t, "Damages")
    entries = dmg.items() if dmg else []
    if len(entries) != 1:
        return other
    d = entries[0]
    kind = {"CXRPGDamage": "instant", "CXRPGDamageOverTime": "over time"}.get(d.type_name)
    if kind is None:
        return other
    return OnHit(b, kind, chance, d)


def on_hits(doc: ItemDocument) -> list[OnHit]:
    return [classify_on_hit(b) for b in doc.behaviours()
            if b.type_name == "CPlugInBehaviorStrikeAction"]


def damage_range_text(h: OnHit) -> tuple[str, str]:
    """The per-hit (or per-tick) range as the file stores it, for an editable effect."""
    r = h.per_hit
    if r is None:
        return "", ""
    return (r.get("Minimum").get("Constant Value") or "0",
            r.get("Maximum").get("Constant Value") or "0")


def _value_text(node, amount: float, exprs) -> str:
    return fmt_interval(evaluate(node, amount, exprs))


def on_hit_values(h: OnHit, amount: float = 0, exprs=None) -> dict:
    """What the card shows: chance, per-hit or per-tick range, duration, total and damage a
    second, as text. Rolled values are evaluated at `amount`."""
    exprs = exprs or {}
    out = {"chance": "100" if h.chance is None else _value_text(h.chance, amount, exprs)}
    if h.kind == "instant":
        out["range"] = _value_text(h.damage.get("Damage Amount"), amount, exprs)
    elif h.kind == "over time":
        amt = h.damage.get("Damage Amount")
        dur = evaluate(h.damage.get("Duration"), amount, exprs)
        total = evaluate(amt, amount, exprs)
        per = h.per_hit
        if per is None and isinstance(amt, ResourceNode) and amt.type_name == "CMultiply":
            per = amt.get("Operand1")
        out["range"] = _value_text(per, amount, exprs) if per is not None else "?"
        out["duration"] = fmt_interval(dur)
        out["total"] = fmt_interval(total)
        if total and dur and dur[1]:
            out["per_second"] = f"{(total[0] + total[1]) / 2 / dur[1]:.2f}"
    return out


def set_on_hit(h: OnHit, chance: str, lo: str, hi: str, duration: str | None = None) -> None:
    """Change a recognised on-hit effect. For damage over time, `lo`-`hi` is per tick and
    the total the file stores is worked out from the duration."""
    if not h.editable:
        raise ItemError("this effect is not plain numbers")
    chance = parse_number(chance, "Chance")
    if not 0 < float(chance) <= 100:
        raise ItemError("Chance must be between 1 and 100.")
    lo, hi = parse_number(lo, "Minimum damage"), parse_number(hi, "Maximum damage")
    if float(lo) > float(hi):
        raise ItemError("Minimum damage is more than maximum damage.")
    if h.kind == "over time":
        duration = parse_number(duration or "", "Duration")
        secs = float(duration)
        if secs < TICK_SECONDS or secs % TICK_SECONDS:
            raise ItemError(f"Duration must be a multiple of {TICK_SECONDS} seconds: damage "
                            f"over time lands once every {TICK_SECONDS} seconds.")
        h.duration_node.set("Constant Value", duration)
        h.ticks_node.set("Constant Value", fmt_number(secs / TICK_SECONDS))
    if h.chance is not None:
        h.chance.set("Constant Value", chance)
    elif chance != "100":
        raise ItemError("This effect always happens; it has no chance to change.")
    h.per_hit.get("Minimum").set("Constant Value", lo)
    h.per_hit.get("Maximum").set("Constant Value", hi)


def set_on_hit_type(h: OnHit, damage_type: str) -> None:
    if damage_type not in DAMAGE_TYPES:
        raise ItemError(f"unknown damage type {damage_type}")
    h.damage.set("Damage Type", f"Damage Types/{damage_type}")


def new_on_hit(chance: str, damage_type: str, lo: str, hi: str,
               duration: str | None = None) -> ResourceNode:
    """A strike action in the shape of a working one: the Bloodletter mod's bleed, with
    plain numbers instead of named amounts. `duration` makes it damage over time."""
    entry = damage_entry(damage_type, lo, hi)
    if duration is not None:
        duration = parse_number(duration, "Duration")
        secs = float(duration)
        if secs < TICK_SECONDS or secs % TICK_SECONDS:
            raise ItemError(f"Duration must be a multiple of {TICK_SECONDS} seconds.")
        per_tick = entry.get("Damage Amount")
        entry = ResourceNode("CXRPGDamageOverTime", [
            ("Damage Type", entry.get("Damage Type")),
            ("Damage Amount", ResourceNode("CMultiply", [
                ("Operand1", per_tick), ("Operator", ""),
                ("Operand2", constant(fmt_number(secs / TICK_SECONDS)))])),
            ("Duration", constant(duration)),
        ])
    chance = parse_number(chance, "Chance")
    if not 0 < float(chance) <= 100:
        raise ItemError("Chance must be between 1 and 100.")
    do_damage = ResourceNode("CActionDoDamage", [
        ("Character Doing Damage", "$Instigator"),
        ("Character To Damage", "$trigger"),
        ("Defend Against", "0"),
        ("No Friendly Fire Check", "0"),
        ("Damages", ResourceNode("Array", [("Item Count", "1"), ("Damage", entry)])),
    ])
    roll = ResourceNode("CIfExpressionAction", [
        ("If Expression", ResourceNode("CIsLessThan", [
            ("Operand1", ResourceNode("CRandom", [
                ("Operator", ""), ("Minimum", constant("0")), ("Maximum", constant("100"))])),
            ("Operator", ""),
            ("Operand2", constant(chance))])),
        ("Character to get attributes from", "$Instigator"),
        ("Then", ResourceNode("CMultipleActionsAction", [
            ("Action", ResourceNode("Array", [("Item Count", "1"), ("Action", do_damage)]))])),
        ("Else", ""),
    ])
    return ResourceNode("CPlugInBehaviorStrikeAction", [
        ("Strike Action", ResourceNode("CIfExpressionAction", [
            ("If Expression", ResourceNode("CIsGreaterThan", [
                ("Operand1", ResourceNode("CExpressionHitMargin", [])),
                ("Operator", ""),
                ("Operand2", constant("0"))])),
            ("Character to get attributes from", "$Instigator"),
            ("Then", roll),
            ("Else", ""),
        ])),
    ])


def add_on_hit(doc: ItemDocument, chance: str, damage_type: str, lo: str, hi: str,
               duration: str | None = None) -> OnHit:
    b = new_on_hit(chance, damage_type, lo, hi, duration)
    array_append(child(doc.root, "PlugIn Behaviors"), "PlugIn Behavior", b)
    return classify_on_hit(b)


def is_weapon(doc: ItemDocument) -> bool:
    """Whether strikes can carry this item's effects: a weapon or ammunition, or magic
    made for one."""
    if doc.is_addition:
        group = (doc.root.get("Inventory Addition Group") or "").lower()
        return "/weapons/" in group or any(
            b.type_name in ("CPlugInBehaviorDamage", "CPlugInBehaviorStrikeAction")
            for b in doc.behaviours())
    slot = (doc.root.get("Slot Used In") or "").lower()
    return slot.endswith(("/weapon", "/arrow", "/bolt")) or bool(damage_lines(doc))


def remove_behaviour(doc: ItemDocument, behaviour: ResourceNode) -> None:
    array_remove(child(doc.root, "PlugIn Behaviors"), behaviour)


def on_hit_sentence(h: OnHit, amount: float = 0, exprs=None) -> str:
    """A description line for the effect, for the person to add if they want it. The
    game does not write one: the description is whatever the item's text says."""
    v = on_hit_values(h, amount, exprs)
    if "?" in v.get("range", "?") or "?" in v["chance"]:
        return ""
    chance = "" if v["chance"] == "100" else f"a {v['chance']}% chance to "
    if h.kind == "instant":
        what = f"deal an extra {v['range']} {h.damage_type} damage"
    elif h.kind == "over time":
        what = (f"deal {v['range']} {h.damage_type} damage every {TICK_SECONDS} seconds for "
                f"{v['duration']} seconds")
    else:
        return ""
    return f"Successful strikes have {chance}{what}." if chance else \
        f"Successful strikes {what}."


# ---------------------------------------------------------------------------
# When used: potions, scrolls, books
# ---------------------------------------------------------------------------

@dataclass
class UseEffect:
    """What using the item does.

    kind "timed": stat changes for a number of seconds (most potions and scrolls);
    "permanent": stat changes that stay (the books, the Potion of Luck);
    "other": healing, mana and the rest, which are built as one-off scripts and are
    described, not edited."""
    behaviour: ResourceNode
    kind: str
    action: ResourceNode | None = None      # CAddCharacterModifierToCharacterAction
    icon: ResourceNode | None = None        # CDisplayAffectingPlayerIconAction
    summary: str = ""

    @property
    def duration_node(self) -> ResourceNode | None:
        d = self.action.get("OPTIONAL Duration") if self.action is not None else None
        return d if isinstance(d, ResourceNode) and d.type_name == "CConstant" else None

    @property
    def icon_duration_node(self) -> ResourceNode | None:
        d = self.icon.get("Duration") if self.icon is not None else None
        return d if isinstance(d, ResourceNode) and d.type_name == "CConstant" else None

    def stats(self) -> list[StatLine]:
        return modifier_lines(self.action) if self.action is not None else []


def use_effects(doc: ItemDocument) -> list[UseEffect]:
    out = []
    for b in doc.behaviours():
        if b.type_name != "CPlugInBehaviorUseAction":
            continue
        root = b.get("Use Action")
        acts = [root] if isinstance(root, ResourceNode) else []
        if acts and acts[0].type_name == "CMultipleActionsAction":
            arr = child(acts[0], "Action")
            acts = arr.items() if arr else []
        mods = [a for a in acts if a.type_name == "CAddCharacterModifierToCharacterAction"
                and (a.get("Character to modify") or "").lower() == "$instigator"]
        icons = [a for a in acts if a.type_name == "CDisplayAffectingPlayerIconAction"]
        if len(mods) == 1:
            permanent = (mods[0].get("Modification is permanent") or "0").strip() == "1"
            out.append(UseEffect(b, "permanent" if permanent else "timed", mods[0],
                                 icons[0] if len(icons) == 1 else None))
        else:
            out.append(UseEffect(b, "other", summary="Using it " + (
                _describe_use(root) if isinstance(root, ResourceNode) else "does nothing")
                + ". This editor leaves it as it is."))
    return out


def _describe_use(n: ResourceNode) -> str:
    names = {"CGiveHealthToCharacterAction": "heals", "CGiveManaToCharacterAction":
             "restores mana", "CActionCancelPendingDamageOverTime": "cures poison and "
             "disease", "CActionRemoveCriticalHits": "heals wounds",
             "CAddCharacterModifierToCharacterAction": "changes stats",
             "CCreateCustomEntityAction": "creates something", "CAddAIAction":
             "affects the people around", "CSpawnEffectAction": "shows an effect"}
    seen = []
    for m in walk(n):
        s = names.get(m.type_name)
        if s and s not in seen:
            seen.append(s)
    return ", ".join(seen) if seen else "runs a script"


def get_use_duration(e: UseEffect) -> str:
    d = e.duration_node
    return (d.get("Constant Value") or "0") if d is not None else ""


def set_use_duration(e: UseEffect, text: str) -> tuple[str, str]:
    """Seconds the effect lasts. The HUD icon carries its own copy of the duration, and
    an icon that outlasts the effect (or vanishes first) lies to the player, so both are
    set together."""
    if e.kind != "timed" or e.duration_node is None:
        raise ItemError("this effect has no duration to change")
    new = parse_number(text, "Duration")
    if float(new) <= 0:
        raise ItemError("Duration must be more than 0 seconds.")
    old = get_use_duration(e)
    e.duration_node.set("Constant Value", new)
    if e.icon_duration_node is not None:
        e.icon_duration_node.set("Constant Value", new)
    return old, new


def get_use_label(e: UseEffect) -> str:
    return (e.icon.get("Affect Description") or "") if e.icon is not None else ""


def set_use_label(e: UseEffect, text: str) -> None:
    if e.icon is None:
        raise ItemError("this effect shows nothing on screen")
    if "\r" in text or "\n" in text:
        raise ItemError("The on-screen name must be one line.")
    e.icon.set("Affect Description", text)


# ---------------------------------------------------------------------------
# Wands: described, not edited
# ---------------------------------------------------------------------------

WAND_NOTE = ("Wands are shown here but can't be changed. In the original game, wands never "
             "drop, they only work when the game's loot generator makes them, and their "
             "charges never run down. Lionheart Fixt's engine notes cover this in detail.")


def wand_summary(doc: ItemDocument) -> str:
    """One line per wand behaviour: the spell, the charges and the level it casts at."""
    exprs = doc.expressions()
    lines = []
    for b in doc.behaviours():
        if b.type_name != "CPlugInBehaviorWand":
            continue
        spell = b.get("Spell") or ""
        charges = fmt_interval(evaluate(b.get("Maximum Charges"), 0, exprs))
        level = ""
        for st in stat_lines(doc):
            if st.target.lower() == spell.lower():
                e = exprs.get(st.expression_name.lower())
                inner = child(e, "Operand1") if e is not None and \
                    e.type_name == "CCacheFirstResult" else None
                if inner is not None and inner.type_name == "CSubtract" and \
                        child(inner, "Operand1") is not None and \
                        child(inner, "Operand1").type_name == "CConstant":
                    level = f", at spell level {child(inner, 'Operand1').get('Constant Value')}"
        lines.append(f"Casts {readable(spell)}, {charges} charges{level}.")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Stats: "while worn" modifiers
# ---------------------------------------------------------------------------

@dataclass
class StatLine:
    behaviour: ResourceNode     # the CPlugInBehaviorModifyCharacterWhenSelected
    node: ResourceNode          # the modifier
    amount_kind: str            # "fixed", "rolled" or "formula"

    @property
    def target_key(self) -> str:
        return STAT_KINDS[self.node.type_name][0]

    @property
    def target(self) -> str:
        return self.node.get(self.target_key) or ""

    @property
    def label(self) -> str:
        return readable(self.target) if self.target else "(the default stat)"

    @property
    def percent(self) -> bool:
        return (self.node.get("Modification amount is a percent") or "0").strip() == "1"

    @property
    def amount_node(self) -> ResourceNode | None:
        v = self.node.get("Modification Amount")
        return v if isinstance(v, ResourceNode) else None

    @property
    def expression_name(self) -> str:
        a = self.amount_node
        return (a.get("Expression Name") or "") if a is not None and \
            a.type_name == "CInventoryAdditionsNamedExpressionExpression" else ""


def stat_lines(doc: ItemDocument) -> list[StatLine]:
    out = []
    for b in doc.behaviours():
        if b.type_name != "CPlugInBehaviorModifyCharacterWhenSelected":
            continue
        arr = child(b, "Modifiers")
        for m in (arr.items() if arr else []):
            if m.type_name not in STAT_KINDS:
                continue
            a = m.get("Modification Amount")
            kind = "formula"
            if isinstance(a, ResourceNode):
                if a.type_name == "CConstant":
                    kind = "fixed"
                elif a.type_name == "CInventoryAdditionsNamedExpressionExpression":
                    kind = "rolled"
            out.append(StatLine(b, m, kind))
    return out


def get_stat_amount(line: StatLine) -> str:
    a = line.amount_node
    return (a.get("Constant Value") or "0") if line.amount_kind == "fixed" else ""


def set_stat_amount(line: StatLine, text: str) -> tuple[str, str]:
    if line.amount_kind != "fixed":
        raise ItemError("this bonus is not a plain number")
    a = line.amount_node
    new = parse_number(text, "Amount")
    old = a.get("Constant Value") or "0"
    a.set("Constant Value", new)
    return old, new


def set_stat_target(line: StatLine, target: str) -> None:
    kind = stat_kind_for(target)
    if kind != line.node.type_name:
        raise ItemError("pick a stat of the same kind, or remove this bonus and add a new one")
    line.node.set(line.target_key, target)


def set_stat_percent(line: StatLine, on: bool) -> None:
    line.node.set("Modification amount is a percent", "1" if on else "0")


def stat_kind_for(target: str) -> str:
    top = target.split("/", 1)[0].lower()
    for cls, (_, folder) in STAT_KINDS.items():
        if folder.lower() == top:
            return cls
    raise ItemError(f"{target} is not a stat")


def new_modifier(target: str, amount: str, percent: bool = False) -> ResourceNode:
    cls = stat_kind_for(target)
    key = STAT_KINDS[cls][0]
    return ResourceNode(cls, [
        ("Modification Amount", constant(parse_number(amount, "Amount"))),
        ("Modification amount is a percent", "1" if percent else "0"),
        ("Allow Accumulation", "1"),
        (key, target),
    ])


def new_worn_behaviour() -> ResourceNode:
    return ResourceNode("CPlugInBehaviorModifyCharacterWhenSelected", [
        ("Modifier Description", ""),
        ("Modification is permanent", "0"),
        ("Modifiers", ResourceNode("Array", [("Item Count", "0")])),
    ])


def add_stat(doc: ItemDocument, target: str, amount: str, percent: bool = False) -> StatLine:
    """Add a fixed "while worn" bonus, to the item's existing worn behaviour if it has
    one that is not permanent, else to a new one."""
    mod = new_modifier(target, amount, percent)
    host = next((b for b in doc.behaviours()
                 if b.type_name == "CPlugInBehaviorModifyCharacterWhenSelected"
                 and (b.get("Modification is permanent") or "0").strip() == "0"), None)
    if host is None:
        host = new_worn_behaviour()
        array_append(child(doc.root, "PlugIn Behaviors"), "PlugIn Behavior", host)
    array_append(child(host, "Modifiers"), "Modifier", mod)
    return StatLine(host, mod, "fixed")


def remove_stat(doc: ItemDocument, line: StatLine) -> None:
    array_remove(child(line.behaviour, "Modifiers"), line.node)


def add_stat_to(host: ResourceNode, target: str, amount: str,
                percent: bool = False) -> StatLine:
    """Add a fixed modifier to any node holding a `Modifiers` array: a worn behaviour, or a
    "when used" action."""
    mod = new_modifier(target, amount, percent)
    array_append(child(host, "Modifiers"), "Modifier", mod)
    return StatLine(host, mod, "fixed")


def modifier_lines(host: ResourceNode) -> list[StatLine]:
    out = []
    arr = child(host, "Modifiers")
    for m in (arr.items() if arr else []):
        if m.type_name not in STAT_KINDS:
            continue
        a = m.get("Modification Amount")
        kind = "formula"
        if isinstance(a, ResourceNode):
            if a.type_name == "CConstant":
                kind = "fixed"
            elif a.type_name == "CInventoryAdditionsNamedExpressionExpression":
                kind = "rolled"
        out.append(StatLine(host, m, kind))
    return out


def array_append(arr: ResourceNode, key: str, node: ResourceNode) -> None:
    """Append to an Array and keep its declared count in step. Item arrays count with
    `Item Count`; named-expression arrays with `Array Count`."""
    count_key = "Array Count" if arr.get("Array Count") is not None else "Item Count"
    arr.fields.append((key, node))
    arr.set(count_key, str(len(arr.items())))


def array_remove(arr: ResourceNode, node: ResourceNode) -> None:
    count_key = "Array Count" if arr.get("Array Count") is not None else "Item Count"
    arr.fields = [(k, v) for k, v in arr.fields if v is not node]
    arr.set(count_key, str(len(arr.items())))


@dataclass
class StatChoice:
    target: str        # e.g. Skills/Fighting/OneHandedMelee
    label: str
    group: str         # e.g. "Skills", "Resistances"


def stat_choices(lib: Library) -> list[StatChoice]:
    """Every stat an item can change, from the game's own definition files, minus the
    engine bookkeeping folders. Labels come from the file names; skills carry a
    Display Name and use it."""
    out = []
    for rel in lib.names():
        low = rel.lower()
        if not low.startswith("resources/"):
            continue
        parts = rel.split("/")[1:]
        top = parts[0]
        if top == "Derived Character Attributes" and low.endswith(".derivedcharacterattribute"):
            if len(parts) > 2 and parts[1] in HIDDEN_STAT_FOLDERS:
                continue
            group = "Resistances and damage" if len(parts) > 2 and parts[1] == "Damage Related" \
                else "Other stats"
        elif top == "Skills" and low.endswith(".skill"):
            group = "Skills"
        elif top == "Character Attributes" and low.endswith(".characterattribute"):
            group = "Attributes"
        else:
            continue
        target = "/".join(parts).rsplit(".", 1)[0]
        label = readable(target)
        if group == "Skills":
            m = re.search(rb"^\tDisplay Name=([^\r\n]+)", lib.read(rel), re.M)
            shown = m.group(1).decode("latin-1").strip() if m else ""
            if shown and shown.strip("?"):
                label = shown
        out.append(StatChoice(target, label, group))
    order = {"Attributes": 0, "Skills": 1, "Resistances and damage": 2, "Other stats": 3}
    out.sort(key=lambda c: (order[c.group], c.label.lower()))
    return out


# ---------------------------------------------------------------------------
# Cloning: a new item from a template
# ---------------------------------------------------------------------------

SAFE_STEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '\-_,.()]*$")


def self_refs(root: ResourceNode, ref: str) -> list[tuple[ResourceNode, str]]:
    """Every field in `root` that names the file at `ref` itself: (node, key). A base
    item names itself in `Origonal Item`; an addition names itself in each named-amount
    lookup. A copy that kept the old path would quietly read the original's numbers."""
    me = ref.lower()
    out = []
    for n in walk(root):
        for k, v in n.fields:
            if k in ("Origonal Item", "InventoryAddition") and isinstance(v, str)                     and v.strip().lower() == me:
                out.append((n, k))
    return out


def clone(doc: ItemDocument, lib: Library, new_stem: str, new_name: str) -> ItemDocument:
    """A copy of `doc` as a new file beside it, named `new_stem`, with every self-path
    pointed at the new file and its Display Name set. Nothing is written until saved."""
    new_stem = new_stem.strip()
    if not SAFE_STEM.match(new_stem):
        raise ItemError("Use letters, numbers, spaces and simple punctuation for the file "
                        "name.")
    folder, ext = doc.rel.rsplit("/", 1)[0], "." + doc.rel.rsplit(".", 1)[-1]
    new_rel = f"{folder}/{new_stem}{ext}"
    if lib.exists(new_rel):
        raise ItemError(f"There is already an item file called {new_stem} there; pick "
                        "another name.")
    copy = ItemDocument(new_rel, doc.to_bytes(), "new")
    new_ref = item_ref(new_rel)
    for node, key in self_refs(copy.root, doc.ref):
        node.set(key, new_ref)
    if copy.root.get("Display Name") is not None and new_name.strip():
        set_field(copy, "Display Name", new_name.strip(), label="Name")
    return copy


# ---------------------------------------------------------------------------
# Problems
# ---------------------------------------------------------------------------

def problems(doc: ItemDocument) -> list[str]:
    """Things that would read wrong or silently do nothing in the game."""
    out = []
    if not (doc.root.get("Display Name") or "").strip() and not doc.is_addition:
        out.append("The item has no name.")
    exprs = doc.expressions()
    desc = get_field(doc, "Description")
    for kind, name in PLACEHOLDER.findall(desc):
        if kind == "i" and doc.is_addition and name.lower() not in exprs:
            out.append(f"The description shows %i[{name}], but this magic has no amount "
                       f"called \"{name}\". The game would show it blank.")
        if kind == "s" and name.lower() != "damage":
            out.append(f"The description uses %s[{name}]; only %s[damage] is known.")
    for line in damage_lines(doc):
        if line.editable:
            lo, hi = damage_range(line)
            try:
                if float(lo) > float(hi):
                    out.append(f"{line.damage_type} damage: minimum {lo} is more than "
                               f"maximum {hi}.")
            except ValueError:
                pass
    for st in stat_lines(doc):
        if st.amount_kind == "rolled" and st.expression_name.lower() not in exprs:
            out.append(f"The {st.label} bonus reads the amount \"{st.expression_name}\", "
                       "which this magic does not define.")
    steps = roll(doc)
    try:
        amounts = [float(s.amount) for s in steps]
        if amounts and amounts != sorted(amounts):
            out.append("The rolls should go weakest <= middle <= strongest.")
    except ValueError:
        out.append("Every roll amount must be a number.")
    for e in use_effects(doc):
        if e.duration_node is not None and e.icon_duration_node is not None:
            a, b = get_use_duration(e), e.icon_duration_node.get("Constant Value") or "0"
            if a != b:
                out.append(f"The effect lasts {a} seconds but its on-screen icon shows "
                           f"{b}. Change the duration to set both.")
    me = doc.ref.lower()
    for n in walk(doc.root):
        for k, v in n.fields:
            if k == "Origonal Item" and n is doc.root and isinstance(v, str) \
                    and v.strip().lower() != me:
                out.append(f"Origonal Item names {v.strip()}, not this file.")
            if k == "InventoryAddition" and isinstance(v, str) and doc.is_addition \
                    and v.strip().lower() != me:
                out.append(f"An amount is read from {v.strip()}, not from this file.")
    return out


# ---------------------------------------------------------------------------
# Tooltip preview
# ---------------------------------------------------------------------------

def damage_text(doc: ItemDocument, amount: float = 0) -> str:
    """What %s[damage] becomes, e.g. "Slashing Damage 1-5". Only the first damage
    behaviour counts: the Ivory Mace's second one is its bonus against the undead."""
    parts = []
    exprs = doc.expressions()
    lines = damage_lines(doc)
    for line in lines:
        if line.kind == "over time" or line.behaviour is not lines[0].behaviour:
            continue
        amt = line.node.get("Damage Amount")
        if line.kind == "ladder":
            base = evaluate(line.node.get("Damage Amount").get("Operand1"), amount, exprs)
        else:
            base = evaluate(amt, amount, exprs)
        parts.append(f"{line.damage_type} Damage {fmt_interval(base)}")
    return ", ".join(parts)


def fill_description(doc: ItemDocument, amount: float) -> str:
    exprs = doc.expressions()

    def sub(m):
        kind, name = m.group(1), m.group(2)
        if kind == "s":
            return damage_text(doc, amount) if name.lower() == "damage" else m.group(0)
        e = exprs.get(name.lower())
        return fmt_interval(evaluate(e, amount, exprs)) if e is not None else ""
    return PLACEHOLDER.sub(sub, get_field(doc, "Description"))


@dataclass
class Tooltip:
    title: str
    lines: list[str] = field(default_factory=list)
    price: str = ""


def tooltip(doc: ItemDocument, base: ItemDocument | None = None,
            step: str = "Middle") -> Tooltip:
    """The item as the game's tooltip would show it. For an addition, `base` is the item
    it is shown on (the window picks one from its group); the roll is `step`."""
    if not doc.is_addition:
        t = Tooltip(doc.name, [fill_description(doc, 0)], get_price(doc))
        return t
    amount = roll_amounts(doc).get(step, 0.0)
    suffix = (doc.root.get("Display Name") or "").strip()
    if base is not None:
        title = f"{base.name} of {suffix}" if suffix else base.name
        lines = [fill_description(base, 0), fill_description(doc, amount)]
        try:
            price = fmt_number(float(get_price(base) or 0) + float(
                next(s.price for s in roll(doc) if s.label == step) or 0))
        except (ValueError, StopIteration):
            price = ""
    else:
        title = f"... of {suffix}" if suffix else "..."
        lines = [fill_description(doc, amount)]
        price = next((s.price for s in roll(doc) if s.label == step), "")
    return Tooltip(title, [ln for ln in lines if ln.strip()], price)


# ---------------------------------------------------------------------------
# Art
# ---------------------------------------------------------------------------

@dataclass
class Picture:
    slot: str          # "inventory", "Basic", "Better", "Special" or "ground"
    ref: str           # e.g. Items/Inventory Images/Armor/Gauntlets


def pictures(doc: ItemDocument) -> list[Picture]:
    """The item's pictures, one per distinct file: a base item whose three grades share
    one image lists it once, as "inventory"."""
    art = parse_item(doc.rel, doc.to_bytes()).art
    grades = [a for a in art if a.slot in ("Basic", "Better", "Special")]
    out = []
    if grades and len({a.ref.lower() for a in grades}) == 1:
        out.append(Picture("inventory", grades[0].ref))
    else:
        out += [Picture(a.slot, a.ref) for a in grades]
    out += [Picture(a.slot, a.ref) for a in art if a.slot == "inventory"]
    out += [Picture(a.slot, a.ref) for a in art if a.slot == "ground"]
    return out


def picture_rgba(lib: Library, ref: str) -> tuple[int, int, bytes] | None:
    """A picture decoded as the game composites it, or None if it is missing or does
    not decode."""
    rel = "Cache/Models/" + ref + ".mdl16"
    if not lib.exists(rel):
        return None
    try:
        comp = mdl16_format.decode_icon_composite(lib.read(rel))
    except (ValueError, IndexError):
        return None
    data = bytearray()
    for row in comp["rows"]:
        for px in row:
            data += bytes(px)
    return comp["width"], comp["height"], bytes(data)


# ---------------------------------------------------------------------------
# Mods
# ---------------------------------------------------------------------------

def create_mod(parent: Path, name: str, author: str = "") -> Path:
    """A new, empty mod folder in the shape modmanager.py installs."""
    name = name.strip()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ItemError("Give the mod a name with at least one letter or number.")
    mod = Path(parent) / slug
    if mod.exists():
        raise ItemError(f"{mod} already exists.")
    (mod / "files").mkdir(parents=True)
    manifest = {"mod_format_version": 1, "id": slug, "name": name, "version": "0.1.0",
                "author": author.strip(), "description": "", "files": []}
    (mod / "mod.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return mod


# ---------------------------------------------------------------------------
# The library: every item, grouped as a person would look for it
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    rel: str
    name: str
    group: str
    origin: str        # "vanilla" or "mod"
    is_addition: bool


def group_for(rel: str, name: str, root_head: str) -> str:
    low = rel.lower()
    if low.endswith(ADDITION_EXT.lower()):
        parts = rel.split("/")
        # Resources/Inventory/Inventory Additions/<Cat>/<Sub>/x
        sub = [p.title() if p.isupper() else p for p in parts[3:-1]]
        if len(sub) > 1 and sub[0] == "Miscellaneous":
            sub = sub[1:]
        return "Magic: " + (" / ".join(sub) if sub else "Other")
    if "/unique magic items/" in low:
        return "Unique items"
    if "/quest items/" in low:
        return "Quest items"
    if "/keys/" in low:
        return "Keys"
    if "spell projectiles/" in low or "projectile" in low:
        return "Spell projectiles"
    if "/enemy items/" in low:
        return "Creature weapons"
    if "/spirit energy/" in low:
        return "Spirit energy"
    return "Items"


HIDDEN_GROUPS = ("Spell projectiles", "Creature weapons", "Spirit energy")


def bases_for(lib: Library, entries: list[Entry], doc: ItemDocument) -> list[Entry]:
    """The plain items a magic addition can be shown on, likeliest first.

    Most magic names the same addition group as its base items. Potions, scrolls and
    wands name none (`!None`: they are given whole, never rolled onto an item), so for
    those the folder decides: magic in `Potions` goes on `Potion`."""
    items = [e for e in entries if not e.is_addition and e.group == "Items"]
    group = (doc.root.get("Inventory Addition Group") or "").strip().lower()
    out = []
    if group and not group.endswith("/!none"):
        line = f"inventory addition group={group}".encode("latin-1")
        out = [e for e in items if line in lib.read(e.rel).lower().replace(b"\t", b"")]
    else:
        folder = doc.rel.rsplit("/", 2)[-2].lower()
        hint = folder[:-1] if folder.endswith("s") else folder
        out = [e for e in items if e.rel.rsplit("/", 1)[-1].lower().startswith(hint)
               or e.name.lower().startswith(hint)]
    out = out or items
    # the plain base items first: "Potion" before "Ale"
    out.sort(key=lambda e: (e.rel.count("/") > 2, e.name.lower()))
    return out


def library_entries(lib: Library) -> list[Entry]:
    out = []
    for rel in lib.names():
        low = rel.lower()
        if not low.endswith((ITEM_EXT.lower(), ADDITION_EXT.lower())):
            continue
        if low.rsplit("/", 1)[-1].startswith("!"):
            continue
        raw = lib.read(rel)
        m = re.search(rb"^\tDisplay Name=([^\r\n]*)", raw, re.M)
        name = m.group(1).decode("latin-1").strip() if m else ""
        stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        is_add = low.endswith(ADDITION_EXT.lower())
        if is_add:
            name = f"... of {name}" if name else f"({stem})"
        out.append(Entry(rel, name or stem, group_for(rel, name, ""), lib.where(rel), is_add))
    out.sort(key=lambda e: (e.group, e.name.lower()))
    return out
