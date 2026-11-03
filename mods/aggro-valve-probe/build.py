"""Build the aggro-valve probe.

Two questions, one fight.

1. WHAT DO THE TOKENS MEAN inside a damage hook?  Vanilla implies `$Instigator` is the attacker
   (`CCheckCategoryAction{Target Name=$Instigator, Category to check for=Player}` in Calle Perdida
   only makes sense as "was I hit by the player") and `$Trigger` is the thing that was hit
   (`Name of Position=$Trigger` puts a balloon over it).  Nothing prints a name, so the probe
   decodes both by asking each one's category and printing a different literal per answer.

2. DOES THE INSTALL PATH CARRY THE BODY UNEVALUATED?  Vanilla has
   `CSetDamagedScriptActionAction{Target Name=$Instigator, New Damaged Action=<no tokens>}` and
   `{Target Name=<literal>, New Damaged Action=<uses $Instigator>}` but never the two combined,
   which is exactly the generator `After Action` pattern.  So the same decoder goes in twice,
   labelled CAN and GEN, and the GEN copy overwrites the CAN copy on any wolf it reaches -- so the
   label that appears says which path ran.

Also installs the valve itself: hit by a Player Friend, turn on them; hit by the player, behave
exactly as today.  Guarded on Player Friend AND NOT Player, because the save shows the companion
carrying `Companion,Player Friend` and nothing confirms the player does not also carry it.
"""
import re
import sys
import pathlib

sys.path.insert(0, r"C:\Users\vkays\LionheartFixt\tools")
import lhbuild as L

CR = L.CR
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "files"

WOLVES = ["Wolf Gray", "Wolf Gray Tough", "Wolf Gray Super",
          "Wolf Brown", "Wolf Brown Tough", "Wolf Brown Super",
          "Wolf Black", "Wolf Black Tough", "Wolf Black Super",
          "Rabid Wolf Tough"]
CROSSROADS = "Levels/Wilderness Maps/Crossroads.zax"


def text(s):
    # Player#1-9# and not $Instigator: if $Instigator turns out to be the attacker, a companion's
    # hit would otherwise print the line to the companion and the player would never see it.
    return ("CPrintCombatTextAction", [("Text to print", s),
                                       ("Character to show text to", "Player#1-9#"),
                                       ("Emphasize", "0"), ("Suppress Duplicates", "1")])


def is_cat(who, cat):
    return ("CCheckCategoryAction", [("Target Name", who), ("Category to check for", cat)])


def hook(tag):
    """The decoder plus the valve, as one Damaged Script Action body."""
    probes = [
        ("$Instigator", "Player", tag + ": Instigator IS Player"),
        ("$Instigator", "Player Friend", tag + ": Instigator IS Player Friend"),
        ("$Instigator", "Enemy", tag + ": Instigator IS Enemy -- bound at install, not at fire"),
        ("$Trigger", "Enemy", tag + ": Trigger IS Enemy -- so Trigger is the wolf"),
        ("$Trigger", "Player", tag + ": Trigger IS Player -- tokens are the other way round"),
        ("$Trigger", "Player Friend", tag + ": Trigger IS Player Friend"),
    ]
    acts = [L.if_action(is_cat(who, cat), text(msg)) for who, cat, msg in probes]

    # the valve: a Player Friend that is not the Player
    turn = L.multi([text(tag + " VALVE: a companion hit me, turning on them"),
                    ("CSetTargetAction", [("Target to attack", "$Instigator"),
                                          ("Attacker", "$Trigger")])])
    acts.append(L.if_action(is_cat("$Instigator", "Player Friend"),
                            L.if_action(is_cat("$Instigator", "Player"), "", turn)))
    return L.multi(acts)


made = []

# -- 1) the can route: reaches any wolf spawned after install, anywhere ---------------------
body = L.B(*hook("CAN"))
for w in WOLVES:
    rel = "Resources/Monster Cans/Animals/" + w + ".can"
    t = L.read(rel)
    key = "Damaged Script Action="
    n = t.count(CR + "\t\t" + key + CR)
    assert n == 1, (w, "expected one empty Damaged Script Action, found %d" % n)
    t2 = t.replace(CR + "\t\t" + key + CR, CR + "\t\t" + key + body, 1)
    assert t2 != t
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(L.resource_format.parse_resource_text(t2).to_text().encode("latin-1"))
    # read back: the slot is filled, the effect action is untouched, braces balance
    back = p.read_bytes().decode("latin-1")
    assert back.count("Damaged Script Action=CMultipleActionsAction") == 1, w
    assert back.count("Damaged Effect Action=CRandomAction") == 1, w
    assert back.count("=CSetTargetAction") == 1, w
    assert back.count('Text to print=CAN') == 7, (w, back.count('Text to print=CAN'))
    made.append(rel)

# -- 2) the generator route: wrap each wolf generator's After Action ------------------------
z = L.read(CROSSROADS)
# Fixt's Crossroads already carries damage hooks of its own (Alvaro's horse, among others), so
# every check below is a DELTA against this baseline, never an absolute count.
base = {k: z.count(k) for k in ["Categories to add=Scripted Custom 1", "=CSetDamagedScriptActionAction",
                                "=CSetTargetAction", "=CAddCategoryAction", "Text to print="]}
install = ("CSetDamagedScriptActionAction", [("New Damaged Action", hook("GEN")),
                                             ("Target Name", "$Instigator")])
spliced = 0
off = 0
while True:
    i = z.find("Name=Wolf generator", off)
    if i < 0:
        break
    s = z.rfind("Level Part=", 0, i)
    e = L.balanced(z, s)
    part = z[s:e]
    m = re.search(r"^([ \t]*)After Action=(CAddCategoryAction)\r?\n", part, re.M)
    assert m, "wolf generator %d has no CAddCategoryAction After Action" % spliced
    ind = m.group(1)
    bs = part.index("{", m.end() - 1)
    be = L.balanced(part, bs)
    original = ("CAddCategoryAction", None)  # placeholder, replaced below by raw text
    orig_raw = part[m.end() - len("CAddCategoryAction" + CR):be]
    new = L.B(*L.multi([("PLACEHOLDER_ORIGINAL", []), install]))
    # splice the original block text back in place of the placeholder
    new = new.replace("Action=PLACEHOLDER_ORIGINAL" + CR + "{" + CR + "}" + CR,
                      "Action=" + orig_raw + CR)
    assert "PLACEHOLDER" not in new, "placeholder not substituted"
    part2 = part[:m.start()] + ind + "After Action=" + new + part[be + len(CR):]
    z = z[:s] + part2 + z[e:]
    off = s + len(part2)
    spliced += 1
assert spliced == 14, "spliced %d wolf generators, expected 14" % spliced

p = OUT / CROSSROADS
p.parent.mkdir(parents=True, exist_ok=True)
p.write_bytes(L.resource_format.parse_resource_text(z).to_text().encode("latin-1"))
back = p.read_bytes().decode("latin-1")
delta = {k: back.count(k) - v for k, v in base.items()}
assert delta["Categories to add=Scripted Custom 1"] == 0, "lost an Esteban wolf tag"
assert delta["=CAddCategoryAction"] == 0, "lost or duplicated a CAddCategoryAction"
assert delta["=CSetDamagedScriptActionAction"] == 14, delta
assert delta["=CSetTargetAction"] == 14, delta
assert back.count("Text to print=GEN") == 14 * 7, back.count("Text to print=GEN")
# and the original tag action still sits inside each new wrapper, not beside it
assert back.count("Action=CSetDamagedScriptActionAction" + CR) >= 14
made.append(CROSSROADS)

print("wrote %d files" % len(made))
for m2 in made:
    print("   ", m2)
print("Crossroads: %d generators spliced; deltas %s" % (spliced, delta))
