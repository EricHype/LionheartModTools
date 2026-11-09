"""What a creature can's combat AI is made of, and which of it the engine actually reads.

A creature's behaviour lives in one `CSkeletonAI` block in the can's `Activity` array. It
holds targeting, the leash, the group-call listener, the attack AI and a list of state
transitions, all in one bundle (see `docs/can-editor.md`). This module is the curated part
of the can editor: the fields worth a labelled control, grouped the way a human thinks
about a fight, plus the class-safety tables.

Two sources, and every field says which one it came from:

    engine    read in Ghidra from Lionheart.exe -- the default, range or meaning is the
              registration's, documented in LionheartFixt's docs/dormant-engine.md and
              docs/engine-notes.md
    observed  derived by sweeping all 1698 `.can` files in vanilla `data.dat` -- the most
              common value is the default and the observed set becomes the choice list.
              The label says what the field is called, not what it is believed to do.

`python can_schema.py` re-runs that sweep and prints any field whose observed values have
drifted from the table below.

Field kinds follow `script_schema`: text, number, enum (a small observed set, still
editable), flag (0/1).
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Class safety
# ---------------------------------------------------------------------------

# Abstract classes. Creating one by name shows the "DataCrash" box and exits the process
# (FUN_00415fc0 refuses any class whose abstract flag at desc+0x48 is set). All three were
# read in Ghidra; see dormant-engine.md "Read this first: classes that crash".
ABSTRACT_CLASSES = {
    "CAttackAI": "the abstract base of every attack AI; the engine cannot build one",
    "CChargeAttackAI": "a registered placeholder (abstract, creator returns 0, no fields); "
                       "no charge behaviour exists in code",
    "CStrafeAttackAI": "a registered placeholder (abstract, creator returns 0, no fields); "
                       "no strafe behaviour exists in code",
}

# Attack AIs that build and run.
WORKING_ATTACK_AIS = {
    "CNormalAttackAI": "the attack AI every vanilla creature uses (701 shipped cans)",
    "CStandStillAttackAI": "turns and fires, never moves; still drops to Pursue when the "
                           "target passes Attack Max Dist, and a shot can be cut off",
}

# A second name registered for a class (FUN_004157d0). It loads as the real class and has
# no behaviour of its own, so the picker never offers it: choosing it would only rename
# the line.
ATTACK_AI_ALIASES = {
    "CStandGroundAttackAI": "CNormalAttackAI",
}

# The engine's own label for Minimum Attack Distance = 0, registered beside the field at
# 0x00456b30. Vanilla writes the label, not the number, in 202 of 701 CNormalAttackAI
# blocks and never writes 0, so the editor writes the label too.
DO_NOT_MOVE = "Do not move while attacking"

# Skeleton states. 3 Retreat exists but no caller ever enters it (dormant-engine.md), so
# it is not offered.
STATES = ("Any", "Patrol", "Pursue", "Attack", "Investigate")

# Transition triggers. The first three occur in vanilla cans (179 / 122 / 24 of 325); the
# other two are the rest of the set dormant-engine.md records from the shipped data.
TRIGGERS = ("Once Per New Target", "First Time", "Every Time", "Target Acquired",
            "Target Lost")


# ---------------------------------------------------------------------------
# Panel fields
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PanelField:
    where: str          # "skeleton", "target" (the Target Type block) or "attack"
    key: str            # the key exactly as the file writes it
    label: str
    kind: str           # text, number, enum, flag
    default: str = ""
    choices: tuple[str, ...] = ()
    hint: str = ""
    source: str = "observed"


VALID_TARGETS_SEEN = ("Player,Player Friend", "", "Enemy", "Scripted Custom 2", "Player",
                      "Player,Player Friend,Scripted Custom 1", "Enemy,Undead")

GROUPS: tuple[tuple[str, tuple[PanelField, ...]], ...] = (
    ("Targeting", (
        PanelField("target", "Valid Targets", "Valid Targets", "enum", "Player,Player Friend",
                   VALID_TARGETS_SEEN,
                   "Categories it will acquire, comma-separated. Empty means it sees no "
                   "one: the shipped way to make a creature ignore everybody.", "engine"),
        PanelField("target", "Max Distance", "Acquire within (px)", "number", "550",
                   ("550", "1000", "400", "625", "375", "500", "300"),
                   "How far away it notices a target. Engine default 550; 647 of 717 "
                   "shipped cans keep it.", "engine"),
        PanelField("target", "Vision Cone", "Vision Cone (degrees)", "number", "360",
                   ("360", "90"), "360 in every shipped can but one.", "engine"),
        PanelField("skeleton", "Target shooter if hit", "Target shooter if hit", "flag", "1",
                   hint="1 in 698 of 716 shipped CSkeletonAIs."),
        PanelField("skeleton", "Never Unacquire", "Never Unacquire", "flag", "0",
                   hint="0 in every shipped CSkeletonAI."),
        PanelField("skeleton", "Sneak Adjustment", "Sneak Adjustment", "number", "0",
                   ("0", "-50", "15", "-25", "10", "-20", "20", "40"),
                   "Observed range -300 to 100; 0 in 489 of 716."),
        PanelField("target", "Must Be Collideable", "Must Be Collideable", "flag", "1",
                   hint="1 in 716 of 717."),
        PanelField("target", "Must Have HPs", "Must Have HPs", "flag", "1",
                   hint="1 in 716 of 717."),
    )),
    ("Leash", (
        PanelField("skeleton", "Max dist from home", "Pursue: max dist from home", "number",
                   "1200", ("1200", "500", "5000", "1000", "4000", "3000"),
                   "Registered under the Pursue tab. 1200 in 651 of 716 shipped cans."),
        PanelField("skeleton", "Max distance from home", "Investigate: max distance from home",
                   "number", "1000", ("1000", "500", "5000", "1200", "3000", "4000"),
                   "Registered under the Investigate tab. 1000 in 648 of 716."),
        PanelField("skeleton", "Allowed To Roam Anywhere", "Allowed To Roam Anywhere", "flag",
                   "0", hint="0 in every shipped can that writes it."),
    )),
    ("Group", (
        PanelField("skeleton", "Respond to calls for reinforcements",
                   "Respond to calls for reinforcements", "flag", "0",
                   hint="The listener switch for CCallForReinforcementsAction. A creature "
                        "answers only while in Patrol, and only if the caller's target is "
                        "one of its own Valid Targets. 1 in 204 of 716 shipped cans.",
                   source="engine"),
    )),
    ("Attack", (
        PanelField("skeleton", "Max Dist", "Attack Max Dist (px)", "number", "200",
                   ("70", "80", "75", "350", "400", "60", "100"),
                   "Registered as Attack/Max Dist (+0x90). Past this the creature drops "
                   "to Pursue. Engine default 200; vanilla melee mostly 70-85, bows "
                   "350-400.", "engine"),
        PanelField("skeleton", "Ignore line of sight if in range",
                   "Ignore line of sight if in range", "flag", "1",
                   hint="The engine's own help text: within Max Dist of its target, all "
                        "line of sight checks are skipped. Meant for melee, not archers.",
                   source="engine"),
        PanelField("skeleton", "Check Range When Firing", "Check Range When Firing", "flag",
                   "0", hint="0 in every shipped CSkeletonAI."),
        PanelField("attack", "Minimum Attack Distance", "Minimum Attack Distance", "enum",
                   "100", ("100", "350", "400", DO_NOT_MOVE),
                   "The distance it closes to. 0 is the engine's 'Do not move while "
                   "attacking': it stands, turns and fires. Default 100, range 0 to "
                   "999999.", "engine"),
        PanelField("attack", "Move Buffer Zone", "Move Buffer Zone", "number", "50",
                   hint="Hysteresis on re-approach. Engine default 50.", source="engine"),
        PanelField("attack", "Use Primary Shoot Behavior", "Use Primary Shoot Behavior",
                   "flag", "1", hint="Engine default 1; 0 in 6 shipped cans.",
                   source="engine"),
    )),
)

# Fields that exist and parse but do nothing: the engine never enters the Retreat state.
RETREAT_FIELDS = (
    PanelField("skeleton", "Retreat when", "Retreat when (% health)", "number", "0",
               hint="Every shipped can sets 0, 40 or 50; none of them retreat.",
               source="engine"),
    PanelField("skeleton", "Retreat Start Action", "Retreat Start Action", "text",
               source="engine"),
    PanelField("skeleton", "Has Started Retreat", "Has Started Retreat", "flag", "0",
               source="engine"),
)

RETREAT_NOTE = ("Inert in this engine. The state setter would fire Retreat Start Action "
                "on entering state 3, but all 31 of its callers pass another state and "
                "the tick has no case for it. Shown so you can see what a can says, not "
                "to be edited.")

# The brief for this panel, and dormant-engine.md, both name a CNormalAttackAI field
# "Max Attack Range". The CNormalAttackAI registration at 0x00456b30 has no such field;
# the string is registered only by CPlugInBehaviorAttack (0x004a30a0), and no shipped can
# writes it. An archer's reach is the skeleton's Attack Max Dist.
MAX_ATTACK_RANGE_NOTE = ("No Max Attack Range: CNormalAttackAI registers no such field "
                         "(it belongs to CPlugInBehaviorAttack, and no shipped can writes "
                         "it). An archer's reach is Attack Max Dist above.")


def fields_for(where: str) -> list[PanelField]:
    return [f for _, group in GROUPS for f in group if f.where == where]


# ---------------------------------------------------------------------------
# Re-deriving the observed values
# ---------------------------------------------------------------------------

def derive(dat_path: str) -> dict:
    """Sweep every vanilla .can and count the values of each panel field.

    Returns {(where, key): Counter}. Run as a script to compare against the table.
    """
    import collections
    import zipfile
    from resource_format import ResourceNode, parse_resource_text

    counts: dict = collections.defaultdict(collections.Counter)
    with zipfile.ZipFile(dat_path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".can"):
                continue
            root = parse_resource_text(zf.read(name).decode("latin-1"))
            stack = [root]
            while stack:
                node = stack.pop()
                for k, v in node.fields:
                    if isinstance(v, ResourceNode):
                        stack.append(v)
                if node.type_name == "CSkeletonAI":
                    parts = {"skeleton": node, "target": node.get("Target Type"),
                             "attack": node.get("Attack AI")}
                    for _, group in GROUPS:
                        for f in group:
                            host = parts.get(f.where)
                            if isinstance(host, ResourceNode) and host.get(f.key) is not None:
                                value = host.get(f.key)
                                if not isinstance(value, ResourceNode):
                                    counts[(f.where, f.key)][value] += 1
    return counts


if __name__ == "__main__":
    import sys

    dat = sys.argv[1] if len(sys.argv) > 1 else (
        r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader"
        r"\data.dat.vanilla.bak")
    counts = derive(dat)
    for _, group in GROUPS:
        for f in group:
            c = counts.get((f.where, f.key))
            if not c:
                print(f"{f.key}: not written by any vanilla can")
                continue
            top = c.most_common(1)[0][0]
            flag = "" if top == f.default or f.source == "engine" else "   <-- default differs"
            print(f"{f.key}: n={sum(c.values())} top={c.most_common(6)}{flag}")
