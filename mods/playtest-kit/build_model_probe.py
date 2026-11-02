"""Build the weapon-model probe into the Playtest Kit.

The first version of this probe answered the question it was built for: an InventoryAddition
*can* override the Weapon slot, so magic weapons can be given their unused Better and
Special meshes. It also turned up two things that no static check can screen for.

  * `BowBetter` rendered 1.8x oversize, because its MODEL.TXT was shipped with
    `Short Name=undefined` and `Render Scaling=1`. Fixed in mods/weapon-mesh-scale-fix.
  * `ScimitarSpecial` rendered with the blade reversed. Its geometry is mirrored on X
    against every other scimitar under an identical placement transform, so it is broken
    art rather than broken wiring.

Orientation cannot be pre-screened from the files. Comparing a variant's post-transform
reach and lean against its family's base weapon flags `BowSpecial`, `Sacred Scim`,
`BattleAxeGreat`, `MaceGreat`, `IvoryMace` and `WarHammerPain` by 84 to 177 degrees -- and
every one of those ships today and renders correctly. So each mesh has to be looked at.

Hence this version: one grant per unused variant mesh, 21 of them, on the correct base item,
grouped so no node carries more than nine replies. Each group leads with the plain base
weapon as a control, and where the family has a *shipping* variant mesh that is known to
render correctly, that is granted too -- so a reversed mesh can be told apart from a
mistaken test.
"""
import sys
from pathlib import Path

MODTOOLS = Path.home() / "LionheartModTools"
sys.path.insert(0, str(MODTOOLS))
import resource_format

KIT = MODTOOLS / "mods" / "playtest-kit"
ADD = KIT / "files" / "Resources" / "Inventory" / "Inventory Additions" / "Weapons"
CR = "\r\n"
T = "\t"
DASH = "-" * 60
ROOT = "9010 weapon models"

SLOTS = ["!None", "Arm", "Arrow", "Body", "Bolt", "Finger", "Foot", "Hand", "Head",
         "HotKey", "Neck", "Shield", "Waist", "Weapon"]

# -- every variant mesh that nothing in the game references, with the base item it belongs on.
#    (mesh, base item, grade). The engine composes "<Base Item> of <Display Name>", so the
#    display name has to read as a noun phrase after "of".
PROBES = [
    ("LongSwordBetter", "LongSword", "Better"),
    ("LongSwordSpecial", "LongSword", "Special"),
    ("ShortSwordBetter", "ShortSword", "Better"),
    ("ShortSwordSpecial", "ShortSword", "Special"),
    ("BastardSwordBetter", "BastardSword", "Better"),
    ("BastardSwordSpecial", "BastardSword", "Special"),
    ("TwoHndedSwordBetter", "TwoHandedSword", "Better"),
    ("TwoHndedSwordSpecial", "TwoHandedSword", "Special"),
    ("ScimitarSpecial", "Scimitar", "Special"),
    ("BattleAxeBetter", "BattleAxe", "Better"),
    ("BattleAxeSpecial", "BattleAxe", "Special"),
    ("MaceBetter", "Mace", "Better"),
    ("MaceSpecial", "Mace", "Special"),
    ("MorningStarBetter", "MorningStar", "Better"),
    ("MorningStarSpecial", "MorningStar", "Special"),
    ("WarHammerBetter", "WarHammer", "Better"),
    ("WarHammerSpecial", "WarHammer", "Special"),
    ("ClubBetter", "Club", "Better"),
    ("BowBetter", "LongBow", "Better"),
    ("CrossBowBetter", "Crossbow", "Better"),
    ("CrossBowSpecial", "Crossbow", "Special"),
]

# -- node id, title, prose, and the rows. A row is either a probe mesh, or a plain item
#    granted with no addition: ("item", <item path>, <label>).
GROUPS = [
    ("9011 swords", "Long and short swords.", [
        ("item", "Inventory Items/LongSword", "Long Sword, plain. (control)"),
        ("mesh", "LongSwordBetter", None),
        ("mesh", "LongSwordSpecial", None),
        ("item", "Inventory Items/ShortSword", "Short Sword, plain. (control)"),
        ("mesh", "ShortSwordBetter", None),
        ("mesh", "ShortSwordSpecial", None),
    ]),
    ("9012 big swords", "Bastard swords, two-handers and scimitars.", [
        ("item", "Inventory Items/BastardSword", "Bastard Sword, plain. (control)"),
        ("mesh", "BastardSwordBetter", None),
        ("mesh", "BastardSwordSpecial", None),
        ("item", "Inventory Items/TwoHandedSword", "Two-Handed Sword, plain. (control)"),
        ("mesh", "TwoHndedSwordBetter", None),
        ("mesh", "TwoHndedSwordSpecial", None),
        ("item", "Inventory Items/Scimitar", "Scimitar, plain. (control)"),
        ("mesh", "ScimitarSpecial", None),
    ]),
    ("9013 axes and maces", "Axes and maces. The Great Axe and Holy Mace ship and render correctly, so they are the controls that prove the test.", [
        ("item", "Inventory Items/BattleAxe", "Battle Axe, plain. (control)"),
        ("item", "Inventory Items/BattleAxeGreat", "Great Battle Axe. (SHIPPING control)"),
        ("mesh", "BattleAxeBetter", None),
        ("mesh", "BattleAxeSpecial", None),
        ("item", "Inventory Items/Mace", "Mace, plain. (control)"),
        ("item", "Inventory/Specific Item Cans/Unique Magic Items/Holy Mace", "Holy Mace. (SHIPPING control)"),
        ("mesh", "MaceBetter", None),
        ("mesh", "MaceSpecial", None),
    ]),
    ("9014 hammers and clubs", "Morning stars, war hammers and clubs.", [
        ("item", "Inventory Items/MorningStar", "Morning Star, plain. (control)"),
        ("mesh", "MorningStarBetter", None),
        ("mesh", "MorningStarSpecial", None),
        ("item", "Inventory Items/WarHammer", "War Hammer, plain. (control)"),
        ("mesh", "WarHammerBetter", None),
        ("mesh", "WarHammerSpecial", None),
        ("item", "Inventory Items/Club", "Club, plain. (control)"),
        ("mesh", "ClubBetter", None),
    ]),
    ("9015 bows", "Bows and crossbows. The Composite Long Bow already uses the BowSpecial mesh in vanilla, so it is the control that proves the test.", [
        ("item", "Inventory Items/LongBow", "Long Bow, plain. (control)"),
        ("item", "Inventory Items/LongBow Composite", "Composite Long Bow. (SHIPPING control, BowSpecial)"),
        ("mesh", "BowBetter", None),
        ("item", "Inventory Items/Crossbow", "Crossbow, plain. (control)"),
        ("mesh", "CrossBowBetter", None),
        ("mesh", "CrossBowSpecial", None),
    ]),
]


LEVEL = "Common Objects and Scripts/Generate Magic Item Level Cans/"
ALL_WEAPONS = "Inventory/Creating Random Magic Items/All Weapons"
BOWS = "Inventory/Creating Random Magic Items/Magic Bows and Xbows"


def roll(text, count, level, canned_list, goto):
    """`CGenerateInventoryItemAction`, shaped as the four vanilla dialogue trees that
    generate from a reply write it: Location=$Trigger, which resolves to the NPC, so the
    items land at the merchant's feet.

    This is the only row in the kit that exercises an addition *pool*. Every other reply
    names its addition by path, which bypasses the pool and would pass regardless.
    """
    return ("Requirement=!None" + CR + "Reply Text=" + text + CR + "Go to node ID=" + goto + CR +
            "Custom Action=CGenerateInventoryItemAction" + CR + "{" + CR +
            "Number of items to generate=" + str(count) + CR +
            "Level of item to generate=CUseCannedExpressionExpression" + CR + "{" + CR +
            "Canned Expression=" + LEVEL + level + CR + "}" + CR +
            "Location=$Trigger" + CR +
            "Location Offset=0,0" + CR +
            "Generate Within Radius=128" + CR +
            "Aspect Ratio=0.712766" + CR +
            "Item List=CInventoryItemGeneratorMixedList" + CR + "{" + CR +
            "Items=Array" + CR + "{" + CR +
            "Array Count=1" + CR +
            "Array Item=CInventoryItemGeneratorMixedListItem" + CR + "{" + CR +
            "Weighting=1" + CR +
            "Item=CInventoryItemGeneratorCannedList" + CR + "{" + CR +
            "Canned Item List=" + canned_list + CR +
            "}" + CR + "}" + CR + "}" + CR + "}" + CR + "}" + CR + CR)


# node id, title, and the rolls. Each row is (label, count, level can, canned list).
ROLLS = ("9016 rolls",
         "Rolling, not granting. These run the real generator: a base weapon is picked, then "
         "additions are rolled from that weapon's own pool. Every other reply in this kit names "
         "its addition directly and would look fine even if the pools were empty. They drop at "
         "my feet.",
         [("Ten magic weapons. (ordinary mojo)", 10, "Chest Generator Good", ALL_WEAPONS),
          ("Ten magic weapons, very high mojo. (biased to Special)", 10,
           "Shopkeeper Generation Very High Mojo", ALL_WEAPONS),
          ("Ten magic bows and crossbows, very high mojo.", 10,
           "Shopkeeper Generation Very High Mojo", BOWS),
          ("One magic weapon.", 1, "Chest Generator Good", ALL_WEAPONS)])

def override_block(mesh):
    """As the 34 working vanilla cases write it. `Override Models` is inert: base items set
    all 14 entries to !None and let the name do the work."""
    out = T * 2 + "PlugIn Behavior=CPlugInBehaviorXRPGCharacterCompositeGrannyModelOverride" + CR + T * 2 + "{" + CR
    out += T * 3 + "Override Models=Array" + CR + T * 3 + "{" + CR
    for s in SLOTS:
        out += T * 4 + "Character Slot Types/" + s + "=Models3D/!None/!None" + CR
    out += T * 3 + "}" + CR
    out += T * 3 + "Override Names=Array" + CR + T * 3 + "{" + CR
    for s in SLOTS:
        out += T * 4 + "Character Slot Types/" + s + "=" + (mesh if s == "Weapon" else "") + CR
    out += T * 3 + "}" + CR
    out += T * 3 + "Override Active=0" + CR + T * 2 + "}" + CR
    return out


def modifier_block():
    """+10 Fire Resistance, so the tester can tell the addition attached even if the mesh
    does not change. A constant amount rather than the named-expression indirection: 80
    vanilla additions do it this way and a probe wants no moving parts."""
    return (
        T * 2 + "PlugIn Behavior=CPlugInBehaviorModifyCharacterWhenSelected" + CR + T * 2 + "{" + CR +
        T * 3 + "Modifier Description=" + CR +
        T * 3 + "Modification is permanent=0" + CR +
        T * 3 + "Modifiers=Array" + CR + T * 3 + "{" + CR +
        T * 4 + "Item Count=1" + CR +
        T * 4 + "Modifier=CCharacterModifierDerivedAttribute" + CR + T * 4 + "{" + CR +
        T * 5 + "Modification Amount=CConstant" + CR + T * 5 + "{" + CR +
        T * 6 + "Constant Value=10" + CR + T * 5 + "}" + CR +
        T * 5 + "Modification amount is a percent=0" + CR +
        T * 5 + "Allow Accumulation=1" + CR +
        T * 5 + "Derived character attribute to modify=Derived Character Attributes/Damage Related/Fire Damage Resistance" + CR +
        T * 4 + "}" + CR + T * 3 + "}" + CR + T * 2 + "}" + CR)


def addition(mesh, grade):
    return (
        "CInventoryAddition" + CR + "{" + CR +
        T + "PlugIn Behaviors=Array" + CR + T + "{" + CR +
        T * 2 + "Item Count=2" + CR +
        modifier_block() + override_block(mesh) +
        T + "}" + CR +
        T + "Value=CConstant" + CR + T + "{" + CR + T * 2 + "Constant Value=0" + CR + T + "}" + CR +
        T + "Is Magic=1" + CR +
        T + "Encumbrance=0" + CR +
        T + "On the ground=!Unknown Model" + CR +
        T + "Requirements=Array" + CR + T + "{" + CR + T * 2 + "Item Count=0" + CR + T + "}" + CR +
        T + "Display Name=the " + grade + " Mesh" + CR +
        T + "Description=Probe item. Grants 10 Fire Resistance and asks the character composite for the " + mesh + " mesh." + CR +
        T + "Item Grade=" + grade + CR +
        T + "Inventory Window=!Unknown Model" + CR +
        T + "Minimum Value=1000" + CR +
        T + "Minimum Addition Ammount=0" + CR +
        T + "Inbetween Value=1000" + CR +
        T + "Inbetween Addition Ammount=0" + CR +
        T + "Maximum Value=1000" + CR +
        T + "Maximum Addition Ammount=0" + CR +
        T + "Sample Value=0" + CR +
        T + "Addition Ammount=0" + CR +
        T + "Expressions=CNamedExpressionsArray" + CR + T + "{" + CR +
        T * 2 + "Expressions=Array" + CR + T * 2 + "{" + CR + T * 3 + "Array Count=0" + CR + T * 2 + "}" + CR +
        T + "}" + CR +
        # !None keeps the probes out of the random magic pool: 61 vanilla additions do this,
        # and the kit grants them by path, so the group is never consulted.
        T + "Inventory Addition Group=Inventory/!None" + CR +
        T + "Addition Rarity=Inventory Item Rarity/3 Rare" + CR +
        T + "Replaces matching plugins=0" + CR +
        T + "Additions Cannot Be Combined With=Array" + CR + T + "{" + CR + T * 2 + "Item Count=0" + CR + T + "}" + CR +
        "}" + CR)


def grant(text, item, goto, add=None):
    out = ("Requirement=!None" + CR + "Reply Text=" + text + CR + "Go to node ID=" + goto + CR +
           "Custom Action=CActionGiveStandardInventoryItem" + CR + "{" + CR +
           "Who to give to=$instigator" + CR + "Delete Trigger=0" + CR + "Notify Player=1" + CR +
           "Inventory Item To Give=" + item + CR +
           "Additions to add=Array" + CR + "{" + CR +
           "Item Count=" + ("1" if add else "0") + CR)
    if add:
        out += "Addition to add=Inventory/Inventory Additions/Weapons/" + add + CR
    return out + "}" + CR + "}" + CR + CR


BASE_OF = {m: b for m, b, _ in PROBES}
GRADE_OF = {m: g for m, _, g in PROBES}

# ---------------------------------------------------------------- the additions
ADD.mkdir(parents=True, exist_ok=True)
stale = {p.name for p in ADD.glob("Model Test *.InventoryAddition")}
written = set()
for mesh, _, grade in PROBES:
    name = "Model Test " + mesh + ".InventoryAddition"
    text = resource_format.parse_resource_text(addition(mesh, grade)).to_text()
    (ADD / name).write_bytes(text.encode("latin-1"))
    written.add(name)
for name in sorted(stale - written):
    (ADD / name).unlink()
    print("removed superseded %s" % name)
print("wrote %d probe additions" % len(written))

# ---------------------------------------------------------------- the kit menu
frag_path = KIT / "menu.fragment"
frag = frag_path.read_bytes().decode("latin-1")
assert frag.count("\n") == frag.count(CR), "menu.fragment is not all-CRLF"

# -- strip any previous build of the probe: its nodes, and its entry in the top menu
nodes = frag.split(DASH + CR)
kept = [n for n in nodes if not n.startswith("Node ID=901")]
assert kept, "stripped the whole fragment"
frag = (DASH + CR).join(kept)
entry = "Requirement=!None" + CR + "Reply Text=Weapon models under test." + CR + "Go to node ID=" + ROOT + CR + CR
frag = frag.replace(entry, "")
assert "901" not in frag, "a probe reference survived the strip"

anchor = ("Go to node ID=9006 wands" + CR + CR +
          "Requirement=!None" + CR + "Reply Text=Nothing more." + CR)
assert frag.count(anchor) == 1, "cannot pin the top menu's exit reply"
head, tail = anchor.split("Requirement", 1)
frag = frag.replace(anchor, head + entry + "Requirement" + tail)

index = ("Node ID=" + ROOT + CR +
         "Text=The seams of the armoury. Every mesh in here has sat unused since the game shipped, and at least one of them is reversed, so take the plain one alongside and hold them up together." + CR +
         "Should Have Voiceover=0" + CR + CR)
for gid, title, _, in [(g[0], g[1], None) for g in GROUPS]:
    index += "Requirement=!None" + CR + "Reply Text=" + title + CR + "Go to node ID=" + gid + CR + CR
index += ("Requirement=!None" + CR + "Reply Text=Roll magic weapons from the real pools." + CR +
          "Go to node ID=" + ROLLS[0] + CR + CR)
index += "Requirement=!None" + CR + "Reply Text=Back." + CR + "Go to node ID=9000 playtest menu" + CR + "Icon=Exit Icon" + CR

body = []
for gid, title, rows in GROUPS:
    node = ("Node ID=" + gid + CR + "Text=" + title + CR + "Should Have Voiceover=0" + CR + CR)
    for kind, a, label in rows:
        if kind == "item":
            node += grant(label, a, gid)
        else:
            node += grant("%s, %s. (expect %s)" % (BASE_OF[a], GRADE_OF[a], a),
                          "Inventory Items/" + BASE_OF[a], gid, "Model Test " + a)
    node += "Requirement=!None" + CR + "Reply Text=Back." + CR + "Go to node ID=" + ROOT + CR + "Icon=Exit Icon" + CR
    body.append(node)
    assert node.count("Reply Text=") <= 9, "%s carries %d replies" % (gid, node.count("Reply Text="))

gid, title, rows = ROLLS
node = "Node ID=" + gid + CR + "Text=" + title + CR + "Should Have Voiceover=0" + CR + CR
for label, count, level, canned in rows:
    node += roll(label, count, level, canned, gid)
node += "Requirement=!None" + CR + "Reply Text=Back." + CR + "Go to node ID=" + ROOT + CR + "Icon=Exit Icon" + CR
assert node.count("Reply Text=") <= 9, "the rolls node carries %d replies" % node.count("Reply Text=")
body.append(node)

assert frag.endswith("Icon=Exit Icon" + CR), repr(frag[-40:])
frag += DASH + CR + index + DASH + CR + (DASH + CR).join(body)
frag_path.write_bytes(frag.encode("latin-1"))
print("menu.fragment: %d bytes, %d nodes, %d grants" % (
    len(frag), frag.count("Node ID="), frag.count("CActionGiveStandardInventoryItem")))
