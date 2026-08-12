"""What the game's entity scripts are made of, and how to build new ones.

Entities in a `.zax` carry scripts: trees of `C*Action` nodes hung off keys like `Action`,
`After Action`, `Then`, `Next Action`. There are 125 distinct action classes across the
shipped maps and about 44,600 nodes, with real control flow -- conditionals, sequences,
delays, randomisation.

This module covers the ~20 classes that actual modding reaches for. It is deliberately
not all 125: the rest are readable and editable as a raw tree, and inventing an interface
for classes nobody uses would be guesswork dressed as support.

Every field list, ordering, default and enum here was **derived from the shipped maps**,
not from guesswork -- the most common value across all 201 `.zax` files is the default,
and a field with a small closed set of observed values becomes a choice list. Regenerate
with `tools/derive_script_schema.py` if the corpus assumption ever needs rechecking.

Field kinds:
    text     free string
    number   numeric string
    enum     one of `choices`
    action   a slot holding a single child action node
    actions  an Array node holding a list of child actions
    node     some other child node, shown but edited as raw text
"""
from __future__ import annotations

from dataclasses import dataclass

from resource_format import ResourceNode

# Keys anywhere in the game's data that hold a single action node. Sorted by how often
# they appear, so the script dock lists an entity's scripts in a stable, useful order.
ACTION_SLOT_KEYS = (
    "Action", "Next Action", "Activity", "After Action", "Then", "If", "Else",
    "Try Action", "Succeed Action", "Fail Action", "Enter Action", "Reveal Action",
    "Per Party Spawn Action", "New Destroyed Action", "Done action", "Done Action",
    "New Damaged Action", "Per Character Spawn Action", "After Opened", "After Closed",
    "Camera Arrived Action", "Level of item to generate",
)

# Where a script attaches to an entity. These are the keys worth surfacing at the top
# level of the script dock; anything deeper is reached by walking into the tree.
ENTITY_SCRIPT_SLOTS = (
    "Activity", "Action", "After Opened", "After Closed", "After Action",
    "New Destroyed Action", "New Damaged Action", "Enter Action",
)

# Array nodes declare their own length. Across all 201 shipped maps this is exact in
# 107,670 of 107,802 cases; the 132 exceptions declare no count at all and are all
# fixed-length stat tables (one entry per attribute / skill / damage type) that never
# change length. So: keep a count in step when one is present, never add one when it is
# absent.
COUNT_KEYS = ("Item Count", "Array Count")


@dataclass(frozen=True)
class Field:
    key: str
    kind: str
    default: str = ""
    choices: tuple[str, ...] = ()
    hint: str = ""


@dataclass(frozen=True)
class ActionSpec:
    label: str                      # plain-language name for the palette
    fields: tuple[Field, ...]
    summary: str = ""               # format string over field values, for the tree row


# Common targets. `$Trigger` is the entity the script hangs off, `$Instigator` the
# character who set it off -- usually the player. Getting these two the wrong way round
# is the classic bug: a CGoToCombatAction with Enemy Name=$Instigator on a dialogue node
# tells the *player* to go hostile, which does nothing visible.
WHO = ("$Instigator", "$Trigger", "Player1")

ACTION_SCHEMA: dict[str, ActionSpec] = {
    "CMultipleActionsAction": ActionSpec(
        "Do all of these",
        (Field("Action", "actions"),),
        "{n} action(s)",
    ),
    "CSeriesAction": ActionSpec(
        "Do these in order",
        (Field("Action", "actions"),),
        "{n} step(s)",
    ),
    "COnlyOnceAction": ActionSpec(
        "Only the first time",
        (Field("Action", "action"),),
    ),
    "CDelayAction": ActionSpec(
        "Wait, then...",
        (Field("Delay", "number", "0.5", hint="seconds"),
         Field("Plus or Minus", "number", "0", hint="random jitter, seconds"),
         Field("Forget Trigger", "enum", "0", ("0", "1")),
         Field("Next Action", "action")),
        "wait {Delay}s",
    ),
    "CIfAction": ActionSpec(
        "If / then / else",
        (Field("If", "action", hint="a test action, e.g. Is Alive"),
         Field("Then", "action"),
         Field("Else", "action"),
         Field("Return failure if the If failes", "enum", "0", ("0", "1"))),
    ),
    "CActionGiveStandardInventoryItem": ActionSpec(
        "Give an item",
        (Field("Who to give to", "enum", "$instigator", ("$instigator",) + WHO),
         Field("Delete Trigger", "enum", "0", ("0", "1")),
         Field("Notify Player", "enum", "1", ("0", "1")),
         Field("Inventory Item To Give", "text", "Inventory Items/Potion",
               hint="path under Inventory Items/"),
         Field("Additions to add", "node")),
        "give {Inventory Item To Give}",
    ),
    "CGenerateInventoryItemAction": ActionSpec(
        "Generate loot",
        (Field("Number of items to generate", "number", "1"),
         Field("Level of item to generate", "action"),
         Field("Location", "text", "$Trigger"),
         Field("Location Offset", "text", "0,0"),
         Field("Generate Within Radius", "number", "40"),
         Field("Aspect Ratio", "text", "0.712766")),
        "generate {Number of items to generate} item(s)",
    ),
    "CGoToCombatAction": ActionSpec(
        "Turn hostile",
        (Field("Enemy Name", "text", "$Instigator",
               hint="the character being MADE hostile, not the target"),),
        "{Enemy Name} turns hostile",
    ),
    "CDisplayDialogTreeAction": ActionSpec(
        "Open a dialogue",
        (Field("Dialog Tree File", "text", hint="path under Levels/.../Dialog/"),
         Field("Node ID", "text", "1 Conversation Start"),
         Field("Speaker", "text", "$trigger"),
         Field("Player Being Spoken To", "enum", "$Instigator", WHO)),
        "dialogue: {Node ID}",
    ),
    "CDisplayDialogBalloonAction": ActionSpec(
        "Speech balloon",
        (Field("Dialog Tree File", "text"),
         Field("Node ID", "text"),
         Field("Name of Position", "text", "$trigger"),
         Field("Position Offset", "text", "0.000000,-75.000000"),
         Field("After Action", "action"),
         Field("Include In Log", "enum", "1", ("0", "1"))),
        "says: {Node ID}",
    ),
    "CActivateQuestStateAction": ActionSpec(
        "Set quest state",
        (Field("Quest", "text", hint="path under Levels/.../Quests/"),
         Field("State", "text", hint="the 8-character state code from the quest file")),
        "quest state {State}",
    ),
    "CPlayAnimationAction": ActionSpec(
        "Play an animation",
        (Field("Animation", "text", "Opening"),
         Field("Random From Sequencial Set", "enum", "0", ("0", "1")),
         Field("Target Name", "text", "$Trigger"),
         Field("When Done", "enum", "Stop on last frame",
               ("Stop on last frame", "Set Sequence", "Return to First Frame",
                "Previous Sequence")),
         Field("End Sequence Name", "text", "Idle"),
         Field("Delay Between Targets", "number", "0"),
         Field("Play In Reverse", "enum", "0", ("0", "1"))),
        "animate {Animation}",
    ),
    "CTriggerRelayAction": ActionSpec(
        "Fire a relay",
        (Field("Relay Name", "text"),),
        "fire {Relay Name}",
    ),
    "CActivateAction": ActionSpec(
        "Activate something",
        (Field("Target Name", "text"),
         Field("Play Activate Sound", "enum", "0", ("0", "1")),
         Field("Delay Between Activations", "number", "0"),
         Field("Warp Behavior", "text", "")),
        "activate {Target Name}",
    ),
    "CDeactivateAction": ActionSpec(
        "Deactivate something",
        (Field("Target Name", "text"),),
        "deactivate {Target Name}",
    ),
    "CDeleteAction": ActionSpec(
        "Delete something",
        (Field("Target Name", "text", "$Trigger"),),
        "delete {Target Name}",
    ),
    "CPlaySoundAction": ActionSpec(
        "Play a sound",
        (Field("Sound", "text", hint="path under Sounds/, including .ogg"),
         Field("Position", "text", "$Trigger"),
         Field("Multiplayer", "enum", "Heard by all players",
               ("Heard by all players",))),
        "sound {Sound}",
    ),
    "CRelocateAction": ActionSpec(
        "Move to another map",
        (Field("New Map Name", "text"),
         Field("New Location", "text", "Start Here"),
         Field("Who To Switch", "enum", "$Instigator", WHO),
         Field("Return to map", "enum", "unlikely soon",
               ("unlikely soon", "probably soon", "definitely soon", "never")),
         Field("Relative Position", "enum", "0", ("0", "1"))),
        "go to {New Map Name}",
    ),
    "CCheckExistenceAction": ActionSpec(
        "Test: does it exist?",
        (Field("Target Name", "text"),),
        "exists? {Target Name}",
    ),
    "CIsAliveAction": ActionSpec(
        "Test: is it alive?",
        (Field("Target Name", "text"),),
        "alive? {Target Name}",
    ),
}


def spec_for(class_name: str) -> ActionSpec | None:
    return ACTION_SCHEMA.get(class_name)


def is_known(class_name: str) -> bool:
    return class_name in ACTION_SCHEMA


def count_key(node: ResourceNode) -> str | None:
    """Which key declares this Array's length, if any."""
    for key in COUNT_KEYS:
        if node.get(key) is not None:
            return key
    return None


def array_items(node: ResourceNode) -> list[tuple[int, str, object]]:
    """(index-into-fields, key, value) for each item, skipping the count field."""
    return [(i, k, v) for i, (k, v) in enumerate(node.fields) if k not in COUNT_KEYS]


def sync_count(node: ResourceNode) -> None:
    """Rewrite the Array's declared length to match reality.

    Only when one is already present. Adding a count to an array that ships without one
    would change a fixed-length stat table into something the engine reads differently.
    """
    key = count_key(node)
    if key is not None:
        node.set(key, str(len(array_items(node))))


def new_array(count_key_name: str = "Item Count") -> ResourceNode:
    return ResourceNode("Array", [(count_key_name, "0")])


def new_action(class_name: str) -> ResourceNode:
    """A fresh action node with the field order and defaults the shipped maps use.

    Field order matters for a clean diff: writing the fields in the order the game's own
    exporter used keeps a hand-authored script textually comparable to a shipped one.
    """
    spec = spec_for(class_name)
    if spec is None:
        return ResourceNode(class_name, [])
    fields: list[tuple[str, object]] = []
    for f in spec.fields:
        if f.kind == "actions":
            fields.append((f.key, new_array()))
        elif f.kind in ("action", "node"):
            fields.append((f.key, f.default))       # empty slot is an empty value
        else:
            fields.append((f.key, f.default))
    return ResourceNode(class_name, fields)


def is_empty_slot(value) -> bool:
    """An action slot with nothing in it. Stored as an empty string, not a missing key."""
    return not isinstance(value, ResourceNode) and not (value or "").strip()


def entity_scripts(entity_node: ResourceNode) -> list[tuple[str, object]]:
    """The script slots hanging off an entity, in a stable order."""
    out = []
    for key in ENTITY_SCRIPT_SLOTS:
        for k, v in entity_node.fields:
            if k == key and (isinstance(v, ResourceNode) or not is_empty_slot(v)):
                out.append((k, v))
    return out


def child_slots(node: ResourceNode) -> list[tuple[int, str, object]]:
    """Everything under `node` that belongs in the script tree.

    For an Array that is its items; for an action it is the slots that hold other
    actions. Scalar fields are not children -- they are edited in the property form.
    Empty action slots are included so there is somewhere to drop a new action.
    """
    if node.type_name == "Array":
        return array_items(node)
    spec = spec_for(node.type_name)
    if spec is not None:
        keys = {f.key for f in spec.fields if f.kind in ("action", "actions", "node")}
    else:
        # Unknown class: show every child node, so nothing is ever hidden.
        keys = {k for k, v in node.fields if isinstance(v, ResourceNode)}
        keys |= {k for k, v in node.fields if k in ACTION_SLOT_KEYS}
    return [(i, k, v) for i, (k, v) in enumerate(node.fields) if k in keys]


def scalar_fields(node: ResourceNode) -> list[Field]:
    """The editable non-child fields of a node, with schema metadata where known."""
    spec = spec_for(node.type_name)
    if spec is not None:
        return [f for f in spec.fields if f.kind not in ("action", "actions", "node")]
    # Unknown class: every scalar field, as free text, so it stays editable.
    return [Field(k, "text") for k, v in node.fields
            if not isinstance(v, ResourceNode) and k not in COUNT_KEYS]


def accepts_actions(parent: ResourceNode, key: str, value) -> bool:
    """Can this Array hold action nodes?

    Some arrays hold scalar item paths instead -- `Additions to add` on an item grant is
    a list of inventory-addition paths, not a list of actions. Putting an action node in
    one would write a nested object where the engine reads a string. Decided by the
    parent's schema when it is known, and by what the array already holds otherwise.
    """
    if not isinstance(value, ResourceNode) or value.type_name != "Array":
        return False
    spec = spec_for(parent.type_name)
    if spec is not None:
        for f in spec.fields:
            if f.key == key:
                return f.kind == "actions"
        return False
    items = array_items(value)
    if not items:
        return key in ACTION_SLOT_KEYS
    return all(isinstance(v, ResourceNode) for _, _, v in items)


def summarise(node: ResourceNode) -> str:
    """One line describing an action, for a tree row."""
    spec = spec_for(node.type_name)
    if spec is None:
        return node.type_name
    text = spec.label
    if spec.summary:
        values = {}
        for f in spec.fields:
            v = node.get(f.key)
            values[f.key] = "" if isinstance(v, ResourceNode) else (v or "")
        if "{n}" in spec.summary:
            slot = node.get(spec.fields[0].key)
            values["n"] = len(array_items(slot)) if isinstance(slot, ResourceNode) else 0
        try:
            detail = spec.summary.format(**values)
        except (KeyError, IndexError):
            detail = ""
        if detail:
            text = f"{text}  --  {detail}"
    return text
