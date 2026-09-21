"""Switch the Playtest Kit on or off over whatever Lionheart Fixt currently ships.

The kit hangs its menu off Merchant Lope's and Jafar's conversations. Fixt edits both trees
too and wins the load order, so a kit built from a snapshot of those files silently vanishes
the moment Fixt touches them. This script rebuilds the kit's two trees from Fixt's current
copies (or vanilla, if Fixt does not ship one), splices the `[TEST KIT]` reply into the same
greeting nodes as before, appends the menu node, reinstalls the kit last in the load order
and rebuilds data.dat.

    python playtest_kit.py on      # menu present on Lope and Jafar
    python playtest_kit.py off     # kit disabled, data.dat rebuilt without it
    python playtest_kit.py status

`off` is what "remove it before testing a release for real" means.
"""
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
GAME = Path(r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader")
FIXT = Path.home() / "LionheartFixt" / "files"
KIT = HERE / "mods" / "playtest-kit"
CR = "\r\n"
DASH = "-" * 60
HOOK = "Requirement=!None" + CR + "Reply Text=[TEST KIT] Open the playtest menu." + CR + "Go to node ID=9000 playtest menu" + CR + CR
TREES = {
    "Resources/Levels/1 Barcelona/Dialog/Gate District/Merchant Lope.DialogTree": [
        "1 Conversation Start", "1 Conversation Start Tainted", "3 Welcome if female", "10 tainted return high",
        "10 tainted return normal", "10 tainted return low", "40 after shop", "5 return male", "5 return female",
        "100 after tainted purchases"],
    "Resources/Levels/1 Barcelona/Dialog/Gate District/Jafar.DialogTree": ["1 Conversation Start", "3 Return Dialogue"],
}


def base_tree(rel):
    p = FIXT / rel
    if p.exists():
        return p.read_bytes().decode("latin-1"), "fixt"
    with zipfile.ZipFile(GAME / "data.dat.vanilla.bak") as zf:
        return zf.read(rel).decode("latin-1"), "vanilla"


def hooked(t, nodes):
    assert "TEST KIT" not in t and "Node ID=9000" not in t
    for nid in nodes:
        i = t.index("Node ID=" + nid + CR)
        k = re.compile("Should Have Voiceover=[01]" + CR).search(t, i).end()
        assert k <= t.find(DASH, i) % (len(t) + 1), nid  # the last node has no dashes after it
        # a node with replies has a blank line here; a closer with none goes straight to the dashes
        if t.startswith(CR, k):
            k += len(CR)
            t = t[:k] + HOOK + t[k:]
        else:
            t = t[:k] + CR + HOOK + t[k:]
    menu = (KIT / "menu.fragment").read_bytes().decode("latin-1")
    k = t.rindex("}" + CR)
    assert t[k - len(CR):k] == CR
    return t[:k] + DASH + CR + menu + t[k:]


def mm(*args):
    subprocess.run([sys.executable, str(HERE / "modmanager.py"), *args, str(GAME)] if args[0] in ("enable", "disable", "build", "list") else
                   [sys.executable, str(HERE / "modmanager.py"), *args], check=True)


def enabled():
    return json.loads((GAME / "mods" / "enabled.json").read_text(encoding="utf-8"))


def on():
    for rel, nodes in TREES.items():
        t, src = base_tree(rel)
        out = KIT / "files" / rel; out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(hooked(t, nodes).encode("latin-1"))
        print(f"{rel.split('/')[-1]}: hooked {len(nodes)} node(s) over the {src} tree")
    manifest = json.loads((KIT / "mod.json").read_text(encoding="utf-8"))
    manifest["files"] = sorted(str(x.relative_to(KIT / "files")).replace("\\", "/") for x in (KIT / "files").rglob("*") if x.is_file())
    (KIT / "mod.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    subprocess.run([sys.executable, str(HERE / "modmanager.py"), "install", "--no-build", str(KIT), str(GAME)], check=True)
    if "playtest-kit" not in enabled():
        mm("enable", "playtest-kit")
    order = [m for m in enabled() if m != "playtest-kit"] + ["playtest-kit"]
    subprocess.run([sys.executable, str(HERE / "modmanager.py"), "reorder", ",".join(order), str(GAME)], check=True)
    mm("build")
    verify(True)


def off():
    if "playtest-kit" in enabled():
        mm("disable", "playtest-kit")
    mm("build")
    verify(False)


def verify(expect):
    with zipfile.ZipFile(GAME / "data.dat") as zf:
        for rel in TREES:
            present = b"TEST KIT" in zf.read(rel)
            assert present == expect, rel
    print("data.dat:", "menu present on Lope and Jafar" if expect else "no trace of the kit")


def status():
    with zipfile.ZipFile(GAME / "data.dat") as zf:
        present = b"TEST KIT" in zf.read(next(iter(TREES)))
    print("kit", "enabled" if "playtest-kit" in enabled() else "disabled", "|", "load order:", " -> ".join(enabled()), "|", "menu in data.dat:", present)


if __name__ == "__main__":
    {"on": on, "off": off, "status": status}[sys.argv[1]]()
