"""Confirm the weapon-model probe actually reached data.dat.

`playtest_kit.py on` ends with its own verify, but that one only checks the string
"TEST KIT" is present -- which is already true whenever the kit is installed at all, so it
passes whether or not this session's new files made it in. Check the bytes that matter.

The expected set is derived from the kit's own `files/` tree rather than hardcoded, so this
keeps working as the probe grows or shrinks.
"""
import re
import sys
import zipfile
from pathlib import Path

GAME = Path(r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader")
KIT = Path(__file__).parent
ADD_DIR = "Resources/Inventory/Inventory Additions/Weapons"
TREES = [
    "Resources/Levels/1 Barcelona/Dialog/Gate District/Merchant Lope.DialogTree",
    "Resources/Levels/1 Barcelona/Dialog/Gate District/Jafar.DialogTree",
]

expected = sorted(p.name for p in (KIT / "files" / ADD_DIR).glob("Model Test *.InventoryAddition"))
assert expected, "no probe additions on disk -- run build_model_probe.py first"

bad = []
with zipfile.ZipFile(GAME / "data.dat") as z:
    present = set(z.namelist())

    print("-- probe additions (%d expected)" % len(expected))
    ok_n = 0
    for name in expected:
        rel = ADD_DIR + "/" + name
        src = (KIT / "files" / rel).read_bytes()
        if rel not in present:
            bad.append("missing from data.dat: " + name)
        elif z.read(rel) != src:
            bad.append("stale in data.dat: " + name)
        else:
            ok_n += 1
            mesh = re.search(rb"Character Slot Types/Weapon=(\w+)", z.read(rel).replace(
                b"Character Slot Types/Weapon=Models3D/!None/!None", b""))
            print("   ok   %-34s -> %s" % (name[11:-18], mesh.group(1).decode() if mesh else "?"))
    print("   %d/%d byte-identical" % (ok_n, len(expected)))

    # nothing from an earlier build of the probe should still be in the archive
    strays = [n for n in present if n.startswith(ADD_DIR + "/Model Test ")
              and n.split("/")[-1] not in expected]
    for s in strays:
        bad.append("superseded addition still in data.dat: " + s.split("/")[-1])

    print("-- probe menu")
    for rel in TREES:
        t = z.read(rel)
        nodes = sorted(set(re.findall(rb"Node ID=(901\d[^\r\n]*)", t)))
        grants = t.count(b"Addition to add=Inventory/Inventory Additions/Weapons/Model Test")
        ok = len(nodes) == 6 and grants == len(expected)
        bad += [] if ok else ["%s: %d probe nodes, %d probe grants (want 6 and %d)" % (
            rel.split("/")[-1], len(nodes), grants, len(expected))]
        print("   %-5s %-26s %d nodes, %d grants" % ("ok" if ok else "FAIL",
                                                     rel.split("/")[-1], len(nodes), grants))
        for n in nodes:
            print("          %s" % n.decode())

print()
if bad:
    for b in bad:
        print("FAIL", b)
    sys.exit(1)
print("probe is live in data.dat")
