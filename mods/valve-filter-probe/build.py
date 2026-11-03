"""Build the valve-filter probe: three questions, one fight.

Q2  Does CSetTargetAction BYPASS the Valid Targets filter?
    Decisive for the ~20 real enemies whose filter is `Player` alone -- Act 5's eight among them.
    Rather than send the player to Nostrodomus, make the experiment locally: strip `Player Friend`
    off the wolves so a companion is NOT an eligible target, then fire the valve at one anyway.
    Turns on the companion => bypass, and those twenty need no filter work.  Stays on the player
    => the filter wins, and widening has to come first.

Q3  Does `Target shooter if hit=1` override the valve?
    Untouched on these wolves on purpose.  Shoot one the valve has pinned to the companion and read
    who its next attacks land on.  The hook prints a line on the player's own hits so the sequence
    is readable in the log.

Q4  Is a summoned creature a Player Friend?
    Statically ambiguous: no picker in the game combines `Summoned Creature` with `Player Friend`,
    which could mean summons are already covered by it or that they are a wholly separate category.
    So the decoder asks for `Summoned Creature` too, and the valve fires for it as well.  Only
    answered if the player happens to have a summon; the other two do not depend on it.

Everything prints to Player#1-9#, never to a token, because a token may resolve to the companion.
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

WOLVES = ["Wolf Gray", "Wolf Gray Tough", "Wolf Gray Super",
          "Wolf Brown", "Wolf Brown Tough", "Wolf Brown Super",
          "Wolf Black", "Wolf Black Tough", "Wolf Black Super",
          "Rabid Wolf Tough"]
CATS = ["Player", "Player Friend", "Enemy", "Summoned Creature"]


def text(s):
    return ("CPrintCombatTextAction", [("Text to print", s),
                                       ("Character to show text to", "Player#1-9#"),
                                       ("Emphasize", "0"), ("Suppress Duplicates", "1")])


def is_cat(who, cat):
    return ("CCheckCategoryAction", [("Target Name", who), ("Category to check for", cat)])


def const(v):
    return ("CConstant", [("Constant Value", str(v))])


def cmp_(cls, a, b):
    return (cls, [("Operand1", a), ("Operator", ""), ("Operand2", b)])


def ifexpr(expr, ctx, then, els=""):
    return ("CIfExpressionAction", [("If Expression", expr),
                                    ("Character to get attributes from", ctx),
                                    ("Then", then), ("Else", els)])


def health_bands(who, tag):
    """Does CExpressionHealthPercent resolve against `Character to get attributes from` inside a
    DAMAGE hook, and against the right entity?

    The planned design gates the valve on the hitting companion's own health, which only works if
    the expression reads $Instigator there.  Printing the band for BOTH tokens is what makes the
    answer falsifiable: $Instigator's band should track the companion (steady while you watch the
    wolf's fall) and $Trigger's should track the wolf (falling as it is beaten).  If both report the
    same thing, the context field is being ignored.
    """
    pct = ("CExpressionHealthPercent", [])
    out = []
    for lo, hi, word in [(75, None, "75 or more"), (50, 75, "50 to 75"),
                         (25, 50, "25 to 50"), (None, 25, "under 25")]:
        inner = text(tag + " HEALTH: " + word)
        if hi is not None:
            inner = ifexpr(cmp_("CIsLessThan", pct, const(hi)), who, inner)
        if lo is not None:
            inner = ifexpr(cmp_("CIsGreaterThanOrEqual", pct, const(lo)), who, inner)
        out.append(inner)
    return out


def turn():
    return L.multi([text("VALVE FIRED: filter is Player-only, forcing it onto my attacker"),
                    ("CSetTargetAction", [("Target to attack", "$Instigator"),
                                          ("Attacker", "$Trigger")])])


def hook():
    acts = [L.if_action(is_cat("$Instigator", c), text("HIT BY: " + c)) for c in CATS]
    # the valve, for a companion OR a summon; a nested if so each arm is independently readable.
    # The companion arm reads its own health first, which is the gate the real design needs.
    acts.append(L.if_action(is_cat("$Instigator", "Player Friend"),
                            L.multi(health_bands("$Instigator", "ATTACKER") + [turn()])))
    acts.append(L.if_action(is_cat("$Instigator", "Summoned Creature"), turn()))
    # the wolf's own band, every hit, so the two tokens can be told apart
    acts += health_bands("$Trigger", "VICTIM")
    # and a line on the player's own hits, so the attack sequence can be read off the log
    acts.append(L.if_action(is_cat("$Instigator", "Player"),
                            text("PLAYER HIT: valve declines, watch who it attacks next")))
    return L.multi(acts)


body = L.B(*hook())
made = []
for w in WOLVES:
    rel = "Resources/Monster Cans/Animals/" + w + ".can"
    t = L.read(rel)

    # 1) strip Player Friend from the picker, so the companion is not an eligible target at all
    old = "Valid Targets=Player,Player Friend" + CR
    assert t.count(old) == 1, (w, "picker filter not as expected", t.count(old))
    t2 = t.replace(old, "Valid Targets=Player" + CR, 1)

    # 2) the damage hook goes in the empty Damaged Script Action slot
    slot = CR + T * 2 + "Damaged Script Action=" + CR
    assert t2.count(slot) == 1, (w, "Damaged Script Action is not empty or not unique")
    t2 = t2.replace(slot, CR + T * 2 + "Damaged Script Action=" + body, 1)

    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(L.resource_format.parse_resource_text(t2).to_text().encode("latin-1"))

    back = p.read_bytes().decode("latin-1")
    assert L.resource_format.parse_resource_text(back).to_text() == back, (w, "not a fixed point")
    assert back.count("\n") == back.count(CR), (w, "stray LF")
    assert back.count("Valid Targets=Player" + CR) == 1, w
    assert back.count("Valid Targets=Player,Player Friend") == 0, w
    # Target shooter if hit must be left exactly as vanilla had it -- Q3 depends on it
    assert back.count("Target shooter if hit=1") == t.count("Target shooter if hit=1"), w
    assert back.count("Damaged Script Action=CMultipleActionsAction") == 1, w
    assert back.count("Damaged Effect Action=CRandomAction") == 1, w
    assert back.count("=CSetTargetAction") == 2, (w, "one valve arm per category")
    assert back.count("Text to print=HIT BY: ") == 4, w
    assert back.count("Text to print=VALVE FIRED") == 2, w
    assert back.count("Text to print=PLAYER HIT") == 1, w
    assert back.count("Text to print=ATTACKER HEALTH: ") == 4, w
    assert back.count("Text to print=VICTIM HEALTH: ") == 4, w
    # Deltas, not absolutes: the can's own drop gate already carries three
    # `Character to get attributes from=$Instigator` of its own.
    for key, want in [("=CExpressionHealthPercent", 12),
                      ("Character to get attributes from=$Instigator", 6),
                      ("Character to get attributes from=$Trigger", 6),
                      ("Character to get attributes from=Player1", 0)]:
        got = back.count(key) - t.count(key)
        assert got == want, (w, key, "delta", got, "expected", want)
    # the drop gate is untouched
    assert back.count("CWasDamagedByPlayerAction") == t.count("CWasDamagedByPlayerAction"), w
    assert back.count("DEATH GATE") == 0, (w, "drop-gate probe leaked in")
    made.append(rel)

print("wrote %d can(s); filter now Player-only, valve installed, Target shooter if hit untouched" % len(made))
for x in made:
    print("   ", x.split("/")[-1])
