"""Build the threat-counter probe.

ONE question, and the whole threat-table design rests on it: **can a derived-attribute counter be
written to, and read back from, a non-player creature, per instance?**

If yes, Lionheart can have what commercial RPGs actually use -- a per-enemy threat accumulator with
a stickiness margin, which is the standard answer to the oscillation problem a naive last-hitter
rule has. If no, the design collapses back to the one-way valve.

The hook, on every hit a wolf takes:

  * bump `Fixt Threat Player` or `Fixt Threat Ally` on `$Trigger`, by who hit it;
  * print a sparse ladder of threshold lines for both counters, read with
    `Character to get attributes from=$Trigger`, so the log shows them climbing per wolf;
  * compare them with a 1.3x margin and, when one side leads, retarget to the hitter.

The retarget is included rather than measured, because `CSetTargetAction` is already proven -- so
any failure here is the counter, not the retarget. And if the counters silently read 0, `0 > 0*1.3`
is false, nothing fires, and the fight looks exactly like vanilla. That is a clean negative too.

Unlike the previous probe this leaves `Valid Targets` alone: normal conditions, because the forced
assignment is known to decay against a `Player`-only filter.
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

WOLVES = ["Wolf Gray", "Wolf Gray Tough", "Wolf Gray Super",
          "Wolf Brown", "Wolf Brown Tough", "Wolf Brown Super",
          "Wolf Black", "Wolf Black Tough", "Wolf Black Super",
          "Rabid Wolf Tough"]
PLAYER_VAR = "Fixt Threat Player"
ALLY_VAR = "Fixt Threat Ally"
LADDER = [1, 2, 3, 5, 8, 12]
MARGIN = 3            # additive, not a ratio: threat is +1 per HIT, so a 1.3x ratio on small
                      # integers was nearly no margin at all and flipped the lead six times in one
                      # fight.  "lead by three" is scale-stable where a ratio is not.
ALLY_PER_HIT = 2      # a companion earns threat faster, which is what makes it a tank
ALLY_HEALTH_FLOOR = 40  # ...but stops earning once badly hurt, so the player can overtake and
                        # pull the enemy off a dying companion.  Without this, ally x2 against
                        # player x1 means the gap only ever grows and the player can never rescue.


def const(v):
    return ("CConstant", [("Constant Value", str(v))])


def gsv(name):
    return ("CVariableDerivedCharacterAttribute", [("Character Attribute", GSV + name)])


def cmp_(cls, a, b):
    return (cls, [("Operand1", a), ("Operator", ""), ("Operand2", b)])


def add(a, b):
    return ("CAdd", [("Operand1", a), ("Operator", ""), ("Operand2", b)])


def ifexpr(expr, ctx, then, els=""):
    return ("CIfExpressionAction", [("If Expression", expr),
                                    ("Character to get attributes from", ctx),
                                    ("Then", then), ("Else", els)])


def text(s):
    return ("CPrintCombatTextAction", [("Text to print", s),
                                       ("Character to show text to", "Player#1-9#"),
                                       ("Emphasize", "0"), ("Suppress Duplicates", "1")])


def is_cat(who, cat):
    return ("CCheckCategoryAction", [("Target Name", who), ("Category to check for", cat)])


def bump(varname, who, amount=1):
    """The guard-set idiom the companion installer uses, pointed at the WOLF instead of Player1.
    `Modification is permanent=0` with `Allow Accumulation=1` is the combination already proven to
    accumulate and persist (it is how Fixt Companion Health Watch is written)."""
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


def turn(tag):
    return L.multi([text("THREAT " + tag + " LEADS by the margin, retargeting"),
                    ("CSetTargetAction", [("Target to attack", "$Instigator"),
                                          ("Attacker", "$Trigger")])])


def hook():
    pct = ("CExpressionHealthPercent", [])
    acts = []
    # the player's hit: one point, always
    acts.append(L.if_action(is_cat("$Instigator", "Player"),
                            L.multi([text("HIT BY: Player"), bump(PLAYER_VAR, "$Trigger")])))
    # the companion's hit: two points, but only while it is fit to hold the line.  `$Instigator`
    # is the companion here, so its own health needs no naming -- proven in the last probe.
    hurt = text("ALLY TOO HURT: under %d, earning no threat" % ALLY_HEALTH_FLOOR)
    healthy = L.multi([text("HIT BY: Player Friend (ally +%d)" % ALLY_PER_HIT),
                       bump(ALLY_VAR, "$Trigger", ALLY_PER_HIT)])
    acts.append(L.if_action(is_cat("$Instigator", "Player Friend"),
                            ifexpr(cmp_("CIsGreaterThanOrEqual", pct, const(ALLY_HEALTH_FLOOR)),
                                   "$Instigator", healthy, hurt)))

    # read both counters back off the wolf
    for var, tag in [(PLAYER_VAR, "P"), (ALLY_VAR, "A")]:
        for n in LADDER:
            acts.append(ifexpr(cmp_("CIsGreaterThanOrEqual", gsv(var), const(n)), "$Trigger",
                               text("THREAT " + tag + ": " + str(n) + " or more")))

    # the stickiness margin: lead by MARGIN, additively
    acts.append(ifexpr(cmp_("CIsGreaterThan", gsv(ALLY_VAR), add(gsv(PLAYER_VAR), const(MARGIN))),
                       "$Trigger", L.if_action(is_cat("$Instigator", "Player Friend"), turn("ALLY"))))
    acts.append(ifexpr(cmp_("CIsGreaterThan", gsv(PLAYER_VAR), add(gsv(ALLY_VAR), const(MARGIN))),
                       "$Trigger", L.if_action(is_cat("$Instigator", "Player"), turn("PLAYER"))))
    return L.multi(acts)


# -- the two counter variables, cloned so every field matches a working one ----------------
donor = L.read(VAR_DIR + "Fixt Companion Fang.DerivedCharacterAttribute")
assert "Short Name=Fixt Companion Fang" in donor
made = []
for v in [PLAYER_VAR, ALLY_VAR]:
    t = donor.replace("Short Name=Fixt Companion Fang", "Short Name=" + v)
    t = t.replace("Long Name=Fixt Companion Fang", "Long Name=" + v)
    t = re.sub("Description=[^" + chr(13) + chr(10) + "]*",
               "Description=probe only. Threat accumulated on an enemy by one side of the fight.", t)
    assert t.count(v) == 2 and "Fixt Companion Fang" not in t, v
    rel = VAR_DIR + v + ".DerivedCharacterAttribute"
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(L.resource_format.parse_resource_text(t).to_text().encode("latin-1"))
    assert "Display In Attributes Window=0" in p.read_bytes().decode("latin-1"), v
    made.append(rel)

# -- the hook, on the wolves, with their filter left alone ---------------------------------
body = L.B(*hook())
for w in WOLVES:
    rel = "Resources/Monster Cans/Animals/" + w + ".can"
    t = L.read(rel)
    assert t.count("Valid Targets=Player,Player Friend") == 1, (w, "read a stripped can by mistake")
    slot = CR + T * 2 + "Damaged Script Action=" + CR
    assert t.count(slot) == 1, (w, "Damaged Script Action is not empty")
    out = t.replace(slot, CR + T * 2 + "Damaged Script Action=" + body, 1)

    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(L.resource_format.parse_resource_text(out).to_text().encode("latin-1"))

    back = p.read_bytes().decode("latin-1")
    assert L.resource_format.parse_resource_text(back).to_text() == back, (w, "not a fixed point")
    assert back.count("\n") == back.count(CR), (w, "stray LF")
    assert back.count("Valid Targets=Player,Player Friend") == 1, (w, "filter must stay vanilla")
    assert back.count("Target shooter if hit=1") == t.count("Target shooter if hit=1"), w
    assert back.count("CWasDamagedByPlayerAction") == t.count("CWasDamagedByPlayerAction"), w
    assert back.count("Damaged Effect Action=CRandomAction") == 1, w
    for key, want in [("=CAddCharacterModifierToCharacterAction", 2),
                      ("Character to modify=$Trigger", 2),
                      ("=CSetTargetAction", 2),
                      ("=CAdd" + CR, 2),   # + CR: bare CAdd, not CAddCharacterModifierToCharacterAction
                      ("Text to print=THREAT ", 2 * len(LADDER) + 2),
                      ("Text to print=ALLY TOO HURT", 1),
                      ("Character to get attributes from=$Trigger", 2 * len(LADDER) + 2),
                      ("Character to get attributes from=$Instigator", 1)]:
        got = back.count(key) - t.count(key)
        assert got == want, (w, key, "delta", got, "expected", want)
    assert back.count(GSV + PLAYER_VAR) == len(LADDER) + 3, (w, back.count(GSV + PLAYER_VAR))
    assert back.count(GSV + ALLY_VAR) == len(LADDER) + 3, (w, back.count(GSV + ALLY_VAR))
    made.append(rel)

print("wrote %d file(s): 2 counters + %d wolf cans" % (len(made), len(WOLVES)))
print("  ladder %s, additive margin +%d, ally +%d per hit, ally floor %d%%"
      % (LADDER, MARGIN, ALLY_PER_HIT, ALLY_HEALTH_FLOOR))
