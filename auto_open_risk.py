"""Destructive replies on conversations the player never asked for.

A `GetCloseThenTrigger` interaction specifier fires on PROXIMITY. The conversation opens by itself
as the player walks past, so any reply on that node can be taken by one dismissive click. Lionheart
Fixt 0.57.4 put `(fit the rotary gear into the turret ring)` -- with `CActionRemoveInventoryItem` on
it -- as the FIRST reply of the siege tank's examine node, which a map opens at radius 200. The next
player to walk into the chamber lost the quest item on entry, and because the item was gone the
hand-over scene could never play either. One click, two features destroyed.

THE RULE THIS ENFORCES: a node opened by an auto-firing specifier must not carry a reply that
removes an item, takes money, hurts the player or completes a quest -- unless the node's own text
ASKS THE QUESTION, so the click is a decision rather than a dismissal.

Vanilla has exactly one of those, and it is the model: `Relican Summoning Ring / 01 Conversation
Start` opens by itself and its text reads *"Do you wish to activate the ring and bring destruction
onto La Calle Perdida?"*, with the removal behind "Yes." It is allow-listed by name below, with the
reason, rather than by a pattern match on question marks. Because it is vanilla's own node, every
mod inherits it, so it is a built-in default rather than one mod's choice.

A mod adds its own deliberate decision points with `--allow FILE`, a JSON list of
`{"tree": "<tree basename>", "node": "<node id>", "why": "<the reason>"}`, or by passing `allowed=`
to `run()`.

    python auto_open_risk.py <mod root> [--vanilla <data.dat.vanilla.bak>] [--allow FILE]

Exits non-zero on a finding, so it can gate a release. It is a separate tool rather than an
lhcheck check because it reads every map in the archive -- run it when adding a reply to anything
a map opens on its own.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path

DEFAULT_VANILLA = os.path.join(
    "C:" + os.sep, "Program Files (x86)", "GOG Galaxy", "Games",
    "Lionheart - Legacy of the Crusader", "data.dat.vanilla.bak")

# Fires without the player choosing to talk. GetCloseThenTalk is NOT in this list: a greeting is
# the player walking up to somebody, and vanilla hangs ordinary conversations on it.
AUTO = re.compile(r"Interaction Type=Interaction Specifiers/(GetCloseThenTrigger\w*)")
COSTLY = re.compile(r"CActionRemoveInventoryItem|CTakeMoneyAction|CPainAction|"
                    r"CSetQuestSatusToCompletedAction")

# Deliberate decision points: the node's own text asks, so the reply is an answer.
ALLOWED = {
    ("relican summoning ring", "01 conversation start"):
        "its text asks 'Do you wish to activate the ring and bring destruction onto La Calle"
        " Perdida?' and the removal sits behind Yes.",
}

SCANNED = (".zax", ".can")


def load_allow(path):
    """A mod's allow-list file -> {(tree basename, node id), lowered: why}."""
    out = {}
    for e in json.loads(Path(path).read_text(encoding="utf-8")):
        out[(e["tree"].strip().lower(), e["node"].strip().lower())] = e.get("why", "allowed by the mod")
    return out


def corpus(root, zf):
    """Every map and can, with the mod's files shadowing the archive."""
    seen = set()
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in SCANNED:
            rel = p.relative_to(root).as_posix()
            seen.add(rel.lower())
            yield rel, p.read_bytes().decode("latin-1")
    for n in zf.namelist():
        low = n.lower()
        if low.startswith("cache/") or not low.endswith(SCANNED) or low in seen:
            continue
        yield n, zf.read(n).decode("latin-1")


def tree_text(root, zf, basename):
    """A tree by basename, the mod's first."""
    for p in root.rglob("*.DialogTree"):
        if p.name[:-11].lower() == basename:
            return p.read_bytes().decode("latin-1"), True
    for n in zf.namelist():
        if n.lower().endswith(".dialogtree") and n.rsplit("/", 1)[-1][:-11].lower() == basename:
            return zf.read(n).decode("latin-1"), False
    return None, False


def find(root, zf, allowed):
    """-> (number of auto-opened pairs, [(basename, node, why)] allowed, [problem tuples])."""
    pairs = {}
    for rel, text in corpus(root, zf):
        if "Interaction Specifiers/GetCloseThenTrigger" not in text:
            continue
        for m in AUTO.finditer(text):
            seg = text[m.start():m.start() + 2500]
            tm = re.search(r"Dialog Tree File=([^\r\n]*)", seg)
            nm = re.search(r"Node ID=([^\r\n]*)", seg)
            if not (tm and nm):
                continue
            key = (tm.group(1).strip().rsplit("/", 1)[-1].lower(), nm.group(1).strip())
            pairs.setdefault(key, set()).add(rel.rsplit("/", 1)[-1])

    ok, problems = [], []
    for (basename, node), where in sorted(pairs.items()):
        text, is_ours = tree_text(root, zf, basename)
        if text is None:
            continue
        m = re.search(r"^Node ID=" + re.escape(node) + r"\r?$", text, re.M)
        if not m:
            continue
        end = text.find("\r\n" + "-" * 60 + "\r\n", m.start())
        seg = text[m.start():end if end > 0 else len(text)]
        hit = COSTLY.search(seg)
        if not hit:
            continue
        why = allowed.get((basename, node.lower()))
        if why:
            ok.append((basename, node, why))
            continue
        problems.append((basename, node, sorted(where), hit.group(0), is_ours))
    return len(pairs), ok, problems


def run(root, vanilla=None, allowed=None, out=None):
    """Check a mod root, print the report, return the exit code. `allowed` adds to ALLOWED."""
    out = out or sys.stdout
    vanilla = Path(vanilla or os.environ.get("LIONHEART_VANILLA", DEFAULT_VANILLA))
    if not vanilla.exists():
        print("vanilla archive not found: %r -- set LIONHEART_VANILLA" % str(vanilla), file=out)
        return 2
    allow = dict(ALLOWED)
    allow.update(allowed or {})
    n, ok, problems = find(Path(root), zipfile.ZipFile(vanilla), allow)
    print("%d (tree, node) pair(s) are opened by an auto-firing specifier" % n, file=out)
    for basename, node, why in ok:
        print("   allowed: %-26s %-30s %s" % (basename, node[:30], why[:60]), file=out)
    if problems:
        print("\n%d PROBLEM(S): a conversation the player did not ask for can cost them something"
              % len(problems), file=out)
        for basename, node, where, what, is_ours in problems:
            print("   - %s / %r opened from %s carries %s%s"
                  % (basename, node, ", ".join(where), what,
                     "  [THIS MOD SHIPS THAT TREE]" if is_ours else ""), file=out)
        print("\n   Put the costly reply behind a second, deliberate click, or make the node's own"
              "\n   text ask the question the way Relican's ring does.", file=out)
        return 1
    print("no auto-opened conversation can cost the player anything", file=out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Destructive replies on proximity-opened conversations.")
    ap.add_argument("root", help="the mod root (overlays data.dat), or a folder with mod.json and files/")
    ap.add_argument("--vanilla", default=None)
    ap.add_argument("--allow", action="append", default=[], metavar="FILE",
                    help="a JSON allow-list of deliberate decision points (repeatable)")
    a = ap.parse_args(argv)
    root = Path(a.root)
    if (root / "mod.json").is_file() and (root / "files").is_dir():
        root = root / "files"
    allowed = {}
    for f in a.allow:
        allowed.update(load_allow(f))
    return run(root, a.vanilla, allowed)


if __name__ == "__main__":
    sys.exit(main())
