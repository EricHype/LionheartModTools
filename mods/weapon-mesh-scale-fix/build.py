"""Fill in the Render Scaling that six weapon MODEL.TXT files were shipped without.

A weapon's on-screen size is its mesh extent times `Render Scaling`. Six variant meshes
carry `Short Name=undefined` and `Render Scaling=1`, which is the signature of a MODEL.TXT
nobody filled in, while the base weapon they belong beside carries a real value. The probe
caught `BowBetter` rendering 1.8x oversize this way.

The rule is read off the families that *were* authored: a grade variant copies its base
weapon's scaling exactly -- long sword 0.55 across all three, short sword 0.5, two-hander
0.4, club 0.75, scimitar 0.6. The meshes differ in modelled size, so the variants still come
out 1.0x to 1.2x larger than their base, and that is intended: LongSwordBetter is 1.12x and
read correctly in play.

`MaceGreat` is in this set and is not part of the unused-mesh work at all -- it is the Great
Mace's own mesh, shipped, and 11% oversize in vanilla today.

Not touched:
  * `MaceSpecial` -- `Short Name` is filled in, so someone authored it, and at scale 1 it
    renders 25% *smaller* than the base rather than larger. Authored and small is not the
    defect this fixes.
  * the battle axe, morning star and war hammer families -- every mesh in them is unauthored
    including the base weapon, so they are internally consistent and offer no reference.
  * `BowSpecial` (0.45) and the unique weapons -- authored, and hand-tuned per weapon.
"""
import json
import re
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
VANILLA = Path(r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader\data.dat.vanilla.bak")

# variant mesh -> (the base weapon whose scaling it copies, that scaling)
FIXES = {
    "Bows/Bow Better/BowBetter": ("Bow-LP", "0.55"),
    "Bows/CrossBow Better/CrossBowBetter": ("CrossBow", "0.5"),
    "Bows/CrossBow Special/CrossBowSpecial": ("CrossBow", "0.5"),
    "Swords/Bastard Sword Better/BastardSwordBetter": ("BastardSword", "0.5"),
    "Swords/Bastard Sword Special/BastardSwordSpecial": ("BastardSword", "0.5"),
    "Misc_NonSwords/Mace Better/MaceBetter": ("Mace", "0.9"),
    "Misc_NonSwords/Mace Great/MaceGreat": ("Mace", "0.9"),
}
BASES = {
    "Bow-LP": "Bows/Bow/Bow-LP",
    "CrossBow": "Bows/CrossBow/CrossBow",
    "BastardSword": "Swords/Bastard Sword/BastardSword",
    "Mace": "Misc_NonSwords/Mace/Mace",
}
PRE = "Resources/Models3D/Weapons/"
CR = "\r\n"

zf = zipfile.ZipFile(VANILLA)


def meta(rel):
    t = zf.read(PRE + rel + ".MODEL.TXT").decode("latin-1")
    return t, re.search(r"Render Scaling=(.*)", t).group(1).strip(), re.search(r"Short Name=(.*)", t).group(1).strip()


# -- the claimed base scalings have to be what vanilla actually says
for base, rel in BASES.items():
    _, scale, short = meta(rel)
    assert short == base, "base %s reports Short Name=%s" % (base, short)
    wanted = {v for b, v in FIXES.values() if b == base}
    assert wanted == {scale}, "%s is at %s, this script claims %s" % (base, scale, wanted)

written = []
for rel, (base, scale) in FIXES.items():
    text, before, short = meta(rel)
    assert before == "1", "%s is already at %s, not the default" % (rel, before)
    assert short.lower() == "undefined", "%s has an authored Short Name (%s) -- look before overwriting" % (rel, short)
    assert text.count("\n") == text.count(CR), "%s is not all-CRLF" % rel
    out = re.sub(r"Render Scaling=1" + CR, "Render Scaling=" + scale + CR, text)
    assert out != text and out.count("Render Scaling=") == 1
    # -- the only change is that one field
    assert out.replace("Render Scaling=" + scale, "Render Scaling=1") == text

    dest = HERE / "files" / (PRE + rel + ".MODEL.TXT")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(out.encode("latin-1"))
    written.append(PRE + rel + ".MODEL.TXT")
    print("%-52s 1 -> %-5s (%s)" % (rel.split("/")[-1], scale, base))

manifest = json.loads((HERE / "mod.json").read_text(encoding="utf-8"))
manifest["files"] = sorted(written)
(HERE / "mod.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print()
print("%d MODEL.TXT files written" % len(written))
