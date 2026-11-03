"""Build the can-indirection probe.

ONE question, and it decides whether the threat table can ship at all: **do `$Instigator` and
`$Trigger` survive into a canned object invoked from a damage hook?**

Why it matters: inlining the threat hook costs 516 lines per install. Across 2,568 enemy-generator
slots that is ~1.3M lines of map data, and it serializes per SPAWN -- ten wolf cans in one map
already put 272 copies of the attribute paths into one save behind a 4.1 MB live layer. Via a can
each install is four lines and the margin lives in one place.

The risk is real: in this engine both tokens resolve to nothing in a relay fired from a dialogue
reply. Encouragingly, Fixt's own growth installer IS a can invoked from a reply and its
`CAddAIAction{Target Name=$Instigator}` demonstrably lands on the player -- but a damage hook is a
different context.

READ THE RESULT FROM STORED STATE, NOT THE LOG. The combat log is lossy: a previous probe showed
zero ally lines while the save proved the hook had fired four times. So the can writes four
variables, which decode independently:

    Fixt Probe Can Ran        on Player1, no token        -> the can was invoked at all
    Fixt Probe Trigger OK     on $Trigger, no category    -> $Trigger resolves
    Fixt Probe Instigator OK  on Player1, inside a
                              $Instigator category check  -> $Instigator resolves
    Fixt Threat Player / Ally on $Trigger, inside a
                              $Instigator category check  -> BOTH together, the real requirement

Each is read out of the save as `Game Scripting Variables/<name>=<n>`.
"""
import re
import sys
import pathlib

sys.path.insert(0, r"C:\Users\vkays\LionheartFixt\tools")
import lhbuild as L

CR = L.CR
T = "\t"
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "files"
GSV = "Derived Character Attributes/Game Scripting Variables/"
VAR_DIR = "Resources/" + GSV
CAN_REL = "Resources/Common Objects and Scripts/Fixt Threat Probe.can"
CAN_REF = "Common Objects and Scripts/Fixt Threat Probe"

WOLVES = ["Wolf Gray", "Wolf Gray Tough", "Wolf Gray Super",
          "Wolf Brown", "Wolf Brown Tough", "Wolf Brown Super",
          "Wolf Black", "Wolf Black Tough", "Wolf Black Super",
          "Rabid Wolf Tough"]
CAN_RAN = "Fixt Probe Can Ran"
TRIGGER_OK = "Fixt Probe Trigger OK"
INSTIGATOR_OK = "Fixt Probe Instigator OK"
PLAYER_VAR = "Fixt Threat Player"
ALLY_VAR = "Fixt Threat Ally"
ALL_VARS = [CAN_RAN, TRIGGER_OK, INSTIGATOR_OK, PLAYER_VAR, ALLY_VAR]


def const(v):
    return ("CConstant", [("Constant Value", str(v))])


def text(s):
    return ("CPrintCombatTextAction", [("Text to print", s),
                                       ("Character to show text to", "Player#1-9#"),
                                       ("Emphasize", "0"), ("Suppress Duplicates", "1")])


def is_cat(who, cat):
    return ("CCheckCategoryAction", [("Target Name", who), ("Category to check for", cat)])


def bump(varname, who, amount=1):
    return ("CAddCharacterModifierToCharacterAction", [
        ("Modifier Description", ""),
        ("Modification is permanent", "0"),
        ("Character to modify", who),
        ("Modifiers", [("Modifier", ("CCharacterModifierDerivedAttribute", [
            ("Modification Amount", const(amount)),
            ("Modification amount is a percent", "0"),
            ("Allow Accumulation", "1"),
            ("Derived character attribute to modify", GSV + varname)]))]),
        ("OPTIONAL Duration", ""),
        ("Expression Context", "$Instigator")])


def can_body():
    return L.multi([
        # 1) did the can run at all?  No token involved, so this cannot fail for token reasons.
        bump(CAN_RAN, "Player1"),
        text("CAN RAN"),
        # 2) does $Trigger resolve?  No category test, so only the token is under test.
        bump(TRIGGER_OK, "$Trigger"),
        # 3) does $Instigator resolve?  Written to a literal, so only the token is under test.
        L.if_action(is_cat("$Instigator", "Player"),
                    L.multi([bump(INSTIGATOR_OK, "Player1"), text("CAN SEES: Player")])),
        L.if_action(is_cat("$Instigator", "Player Friend"),
                    L.multi([bump(INSTIGATOR_OK, "Player1"), text("CAN SEES: Player Friend")])),
        # 4) both at once, which is what the real design needs
        L.if_action(is_cat("$Instigator", "Player"), bump(PLAYER_VAR, "$Trigger", 1)),
        L.if_action(is_cat("$Instigator", "Player Friend"), bump(ALLY_VAR, "$Trigger", 2)),
    ])


made = []

# -- the five counters, cloned from a working variable ------------------------------------
donor = L.read(VAR_DIR + "Fixt Companion Fang.DerivedCharacterAttribute")
assert "Short Name=Fixt Companion Fang" in donor
for v in ALL_VARS:
    t = donor.replace("Short Name=Fixt Companion Fang", "Short Name=" + v)
    t = t.replace("Long Name=Fixt Companion Fang", "Long Name=" + v)
    t = re.sub("Description=[^" + chr(13) + chr(10) + "]*",
               "Description=probe only. Read from the save, never from the combat log.", t)
    assert t.count(v) == 2 and "Fixt Companion Fang" not in t, v
    rel = VAR_DIR + v + ".DerivedCharacterAttribute"
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(L.resource_format.parse_resource_text(t).to_text().encode("latin-1"))
    assert "Display In Attributes Window=0" in p.read_bytes().decode("latin-1"), v
    made.append(rel)

# -- the can ------------------------------------------------------------------------------
can = L.B("CCannedObject", [("Object", can_body())])
p = OUT / CAN_REL
p.parent.mkdir(parents=True, exist_ok=True)
p.write_bytes(L.resource_format.parse_resource_text(can).to_text().encode("latin-1"))
cantext = p.read_bytes().decode("latin-1")
assert cantext.count("Character to modify=Player1") == 3, cantext.count("Character to modify=Player1")
assert cantext.count("Character to modify=$Trigger") == 3
assert cantext.count("=CCheckCategoryAction") == 4
for v in ALL_VARS:
    assert cantext.count(GSV + v) >= 1, v
made.append(CAN_REL)
print("can body: %d lines" % cantext.count(CR))

# -- the four-line install on each wolf ---------------------------------------------------
call = L.B("CUseCannedActionAction", [("Canned Object", CAN_REF)])
for w in WOLVES:
    rel = "Resources/Monster Cans/Animals/" + w + ".can"
    t = L.read(rel)
    assert t.count("Valid Targets=Player,Player Friend") == 1, (w, "read a modified can by mistake")
    slot = CR + T * 2 + "Damaged Script Action=" + CR
    assert t.count(slot) == 1, (w, "Damaged Script Action is not empty")
    out = t.replace(slot, CR + T * 2 + "Damaged Script Action=" + call, 1)

    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(L.resource_format.parse_resource_text(out).to_text().encode("latin-1"))

    back = p.read_bytes().decode("latin-1")
    assert L.resource_format.parse_resource_text(back).to_text() == back, (w, "not a fixed point")
    assert back.count("\n") == back.count(CR), (w, "stray LF")
    # the whole point: the install is TINY
    cost = back.count(CR) - t.count(CR)
    assert cost == 3, (w, "install cost %d lines, expected 3" % cost)  # the empty slot line is reused
    assert back.count("Damaged Script Action=CUseCannedActionAction") == 1, w
    assert back.count("Canned Object=" + CAN_REF) == 1, w
    assert back.count("Valid Targets=Player,Player Friend") == 1, w
    assert back.count("Target shooter if hit=1") == t.count("Target shooter if hit=1"), w
    assert back.count("CWasDamagedByPlayerAction") == t.count("CWasDamagedByPlayerAction"), w
    assert back.count("Damaged Effect Action=CRandomAction") == 1, w
    # nothing inlined
    assert "CAddCharacterModifierToCharacterAction" not in back[back.index("Damaged Script Action"):
                                                                back.index("Damaged Effect Action")], w
    made.append(rel)

print("wrote %d file(s): %d counters + 1 can + %d wolf cans" % (len(made), len(ALL_VARS), len(WOLVES)))
print("  install cost per enemy: 3 lines (was 516 inlined)")
