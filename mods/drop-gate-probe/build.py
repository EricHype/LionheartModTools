"""Build the drop-gate probe.

ONE question: does `CWasDamagedByPlayerAction` return true when only a COMPANION damaged the
creature?  It is the only damaged/killed test class in the game -- 648 uses, every one of them
`Entity To Check For Damage=$Trigger` -- and it gates experience and every drop:

    Destroyed Effect Action=CIfAction
    {
        If=CWasDamagedByPlayerAction { Entity To Check For Damage=$Trigger }
        Then=CMultipleActionsAction { ...XP, drop rolls... }
        Else=                                                   <- empty in vanilla
    }

If the answer is no, the aggro valve costs the player experience and loot every time it works, and
needs rethinking rather than scoping.  The earlier playtest log is suggestive but not proof: it
shows `Fang kills Grey Wolf` followed by `Gained 39 experience` with no player attack on that wolf
in view, but the log is a ring buffer so an earlier player hit could have rolled off.

So read the gate's own answer instead of inferring it from whether XP appeared.  The empty `Else`
gets a line, the `Then` keeps everything it had plus a line, and both branches decode what
`$Instigator` resolves to at death -- vanilla's `Character to give perk to=$Instigator` in this same
slot implies the killer, which is worth confirming while we are here.

The Effect slot, not the Script slot, on purpose: 101 enemy generators overwrite
`Destroyed Script Action` with `CSetDestroyedScriptActionAction` and none of them touch the Effect
slot, so an edit here cannot be silently replaced.
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


def text(s):
    # Player#1-9# rather than a token: if $Instigator turns out not to be the player, the line must
    # still reach the player's log.
    return ("CPrintCombatTextAction", [("Text to print", s),
                                       ("Character to show text to", "Player#1-9#"),
                                       ("Emphasize", "1"), ("Suppress Duplicates", "0")])


def is_cat(who, cat):
    return ("CCheckCategoryAction", [("Target Name", who), ("Category to check for", cat)])


def decode(tag, headline):
    acts = [text("DEATH " + tag + ": " + headline)]
    for cat in ["Player", "Player Friend", "Enemy"]:
        acts.append(L.if_action(is_cat("$Instigator", cat),
                                text("DEATH " + tag + ": Instigator IS " + cat)))
    return acts


made = []
for w in WOLVES:
    rel = "Resources/Monster Cans/Animals/" + w + ".can"
    t = L.read(rel)

    # the one CIfAction that is the drop gate
    m = re.search(r"^(" + T + r"{2})Destroyed Effect Action=CIfAction\r?$", t, re.M)
    assert m, (w, "no Destroyed Effect Action=CIfAction")
    gate_s = t.index("{", m.end())
    gate_e = L.balanced(t, gate_s)
    gate = t[gate_s:gate_e]
    assert gate.count("CWasDamagedByPlayerAction") == 1, (w, "gate is not the damage test")

    # 1) the empty Else gets the FALSE branch
    else_key = CR + T * 3 + "Else=" + CR
    assert gate.count(else_key) == 1, (w, "Else= is not empty or not unique", gate.count(else_key))
    false_block = L.B(*L.multi(decode("GATE FALSE", "no player damage counted, so NO xp and NO drop")))
    new_gate = gate.replace(else_key, CR + T * 3 + "Else=" + false_block, 1)

    # 2) the Then keeps every original action, with a line in front
    then_m = re.search(r"^(" + T + r"{3})Then=(C[A-Za-z0-9]+)\r?$", new_gate, re.M)
    assert then_m and then_m.group(2) == "CMultipleActionsAction", (w, then_m and then_m.group(2))
    ts = new_gate.index("{", then_m.end())
    te = L.balanced(new_gate, ts)
    original = then_m.group(2) + CR + new_gate[ts:te]
    assert "CGiveExperiencePointsToAllPlayersAction" in original, (w, "Then lost the XP award")
    wrapper = L.B(*L.multi([("PLACEHOLDER", [])] + decode("GATE TRUE", "player damage counted, xp and drop paid")))
    wrapper = wrapper.replace("Action=PLACEHOLDER" + CR + "{" + CR + "}" + CR, "Action=" + original + CR)
    assert "PLACEHOLDER" not in wrapper
    new_gate = new_gate[:then_m.start()] + T * 3 + "Then=" + wrapper + new_gate[te + len(CR):]

    out = t[:gate_s] + new_gate + t[gate_e:]
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(L.resource_format.parse_resource_text(out).to_text().encode("latin-1"))

    # -- read back against the untouched original -----------------------------------------
    back = p.read_bytes().decode("latin-1")
    assert L.resource_format.parse_resource_text(back).to_text() == back, (w, "not a fixed point")
    assert back.count(CR) == back.count("\n"), (w, "stray LF")
    assert back.count("CWasDamagedByPlayerAction") == 1, w
    assert back.count("Text to print=DEATH GATE TRUE") == 4, w
    assert back.count("Text to print=DEATH GATE FALSE") == 4, w
    # everything the original Then did is still there, exactly once each
    for frag in ["CGiveExperiencePointsToAllPlayersAction", "Get XP Frome=$Trigger"]:
        assert back.count(frag) == t.count(frag), (w, frag, back.count(frag), t.count(frag))
    # and undoing the probe gives the original file back, byte for byte
    stripped = re.sub(r"\r\n\t*(?:Action=CIfAction\r\n\t*\{\r\n\t*If=CCheckCategoryAction.*?"
                      r"Return failure if the If failes=0\r\n\t*\}|Action=CPrintCombatTextAction\r\n"
                      r"\t*\{\r\n\t*Text to print=DEATH.*?Suppress Duplicates=0\r\n\t*\})", "", back, flags=re.S)
    assert stripped.count("DEATH ") == 0, (w, "probe text not fully strippable")
    made.append(rel)

print("wrote %d can(s)" % len(made))
for x in made:
    print("   ", x.split("/")[-1])
