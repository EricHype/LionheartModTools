import sys
sys.path.insert(0, r"C:\Users\vkays\LionheartModTools")
from resource_format import parse_resource_text

QUEST_REF = "Levels/1 Barcelona/Quests/Gate District/Wolf Pelts for Quinn"
ID_GIVEN = "008HTXBM"
ITEM_REF = "Inventory Items/Wolf Pelt"

def validate_snippet(snippet_lines, label):
    """snippet_lines: list of raw lines (no trailing \r\n) representing the
    BODY of an object (what would sit between the outer { and }).
    Wrap in a dummy root and confirm it parses without error."""
    text = "DummyRoot\r\n{\r\n" + "\r\n".join(snippet_lines) + "\r\n}\r\n"
    try:
        parse_resource_text(text)
    except Exception as e:
        raise AssertionError(f"{label} failed to parse: {e}")
    print(f"  [ok] {label} ({len(snippet_lines)} lines)")

# ---------- nested check/remove/check/remove/check/remove ----------
def check_action():
    return [
        "Action=CActionExpression",
        "{",
        "Action=CActionCheckForInventoryItem",
        "{",
        "Who to give check=$instigator",
        f"Inventory Item To Check For={ITEM_REF}",
        "}",
        "}",
    ]

def remove_action():
    return [
        "Action=CActionRemoveInventoryItem",
        "{",
        "Who to remove from=$instigator",
        f"Inventory Item To remove={ITEM_REF}",
        "}",
    ]

def innermost_then():
    lines = ["Then=CMultipleActionsAction", "{", "Action=Array", "{", "Item Count=3"]
    lines += remove_action()
    lines += [
        "Action=CSetQuestSatusToCompletedAction",
        "{",
        f"Quest={QUEST_REF}",
        "}",
        "Action=CGiveExperiencePointsToAllPlayersAction",
        "{",
        "Get XP Frome=$instigator",
        "Experience Points To Add=25",
        "}",
        "}",
        "}",
    ]
    return lines

def nested_if(depth):
    # depth 3 = innermost (final removal + reward), depth 2 and 1 wrap around it
    lines = ["If=CActionExpression", "{", "Action=CActionCheckForInventoryItem", "{",
             "Who to give check=$instigator", f"Inventory Item To Check For={ITEM_REF}", "}", "}"]
    if depth == 3:
        lines += innermost_then()
    else:
        then_lines = ["Then=CMultipleActionsAction", "{", "Action=Array", "{", "Item Count=2"]
        then_lines += remove_action()
        then_lines += ["Action=CIfAction", "{"] + nested_if(depth + 1) + ["}"]
        then_lines += ["}", "}"]
        lines += then_lines
    lines += ["Else=", "Return failure if the If failes=0"]
    return lines

custom_action_lines = ["Custom Action=CIfAction", "{"] + nested_if(1) + ["}"]
validate_snippet(custom_action_lines, "turn-in Custom Action (nested check/remove x3)")

# ---------- Custom Requirement for the OFFER reply (only if not yet given) ----------
offer_requirement_lines = [
    "Custom Requirement=CActionExpression",
    "{",
    "Action=CNotAction",
    "{",
    "Action=CIsQuestStateTheCurrentStateAction",
    "{",
    f"Quest={QUEST_REF}",
    f"State={ID_GIVEN}",
    "}",
    "}",
    "}",
]
validate_snippet(offer_requirement_lines, "offer reply Custom Requirement")

# ---------- Custom Requirement for the TURN-IN reply (given AND not completed) ----------
turnin_requirement_lines = [
    "Custom Requirement=CAND",
    "{",
    "Operand1=CActionExpression",
    "{",
    "Action=CIsQuestStateTheCurrentStateAction",
    "{",
    f"Quest={QUEST_REF}",
    f"State={ID_GIVEN}",
    "}",
    "}",
    "Operator=",
    "Operand2=CActionExpression",
    "{",
    "Action=CNotAction",
    "{",
    "Action=CIsQuestCompletedAction",
    "{",
    f"Quest={QUEST_REF}",
    "}",
    "}",
    "}",
    "}",
]
validate_snippet(turnin_requirement_lines, "turn-in reply Custom Requirement")

print("ALL SNIPPETS VALID")

# ---------- assemble full reply blocks (as they'll appear in the .DialogTree file) ----------
def indent(lines):
    return lines  # DialogTree files use NO tab indentation at all (flat, left-aligned)

offer_reply = (
    ["Requirement=!None"]
    + offer_requirement_lines
    + [
        "Reply Text=Is there anything around here I could help you with?",
        "Go to node ID=800 wolf pelts request",
        "Action work in progress=(Offers the Wolf Pelts for Quinn quest if not already given)",
        "Custom Action=CActivateQuestStateAction",
        "{",
        f"Quest={QUEST_REF}",
        f"State={ID_GIVEN}",
        "}",
        "Icon=Quest Icon",
    ]
)

turnin_reply = (
    ["Requirement=!None"]
    + turnin_requirement_lines
    + [
        "Reply Text=I brought you three wolf pelts, as you asked.",
        "Go to node ID=810 wolf pelts turned in",
        "Action work in progress=(Checks for and removes 3 Wolf Pelts, completes the quest)",
    ]
    + custom_action_lines
    + ["Icon=Quest Icon"]
)

node_800 = [
    "Node ID=800 wolf pelts request",
    "Text=Actually, yes -- I have come across some wolf pelts that may be tainted by dark magic. Bring me three wolf pelts and I will test them for corruption. It is important work, and I will make it worth your while.",
    "Should Have Voiceover=0",
    "",
    "Requirement=!None",
    "Reply Text=I will see what I can find.",
    "Go to node ID=",
    "Icon=Exit Icon",
    "Is Default Reply=1",
]

node_810 = [
    "Node ID=810 wolf pelts turned in",
    "Text=Let me have a look at those...",
    "Should Have Voiceover=0",
    "",
    "Requirement=!None",
    "Reply Text=Good luck with your tests.",
    "Go to node ID=",
    "Icon=Exit Icon",
    "Is Default Reply=1",
]

with open(r"C:\Users\vkays\LionheartModTools\_quinn_splice_parts.txt", "w", encoding="latin-1", newline="\r\n") as f:
    def block(title, lines):
        f.write(f"=== {title} ===\n")
        f.write("\r\n".join(lines))
        f.write("\r\n\n")
    block("OFFER_REPLY", offer_reply)
    block("TURNIN_REPLY", turnin_reply)
    block("NODE_800", node_800)
    block("NODE_810", node_810)

print("wrote _quinn_splice_parts.txt")
