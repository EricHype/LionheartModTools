"""Engine-rule checks for a Lionheart: Legacy of the Crusader mod.

Every check here is a rule the ENGINE enforces, found because a mod broke it and shipped. Each
one carries the comment explaining the defect that taught it, because that history is the point:
a rule with no story behind it is a rule nobody trusts enough to keep.

  .DialogTree  - CDialogTree wrapper present and closed, brace balance, every
                 'Go to node ID' resolves (case-insensitively, which the engine
                 tolerates -- 244 vanilla links rely on it), every named
                 Requirement resolves to a real .can, and every embedded
                 Custom Action/Requirement object parses.
  .zax         - parses and round-trips, AND every map -> dialogue node
                 reference matches BYTE-EXACTLY. That last one is stricter on
                 purpose: the map-side lookup is exact and a miss is a hard
                 crash on map entry, not a silent failure. Vanilla node IDs
                 contain trailing spaces, so a reference that looks identical
                 can still be fatal -- which is exactly how the Goblin Warrens
                 crash shipped.
  everything else - parses with resource_format and round-trips byte-exact.

Also asserts the whole tree is CRLF and latin-1 clean.

A "mod root" is the folder that overlays data.dat: it holds `Levels/`, `Resources/` and
`Cache/`. A modmanager package (a folder with `mod.json` and `files/`) is accepted too, and its
`files/` is checked.

Usage:
    python lhcheck.py <mod root> [--vanilla <data.dat.vanilla.bak>] [--skip ID] [--only ID]
    python lhcheck.py --list

Exits non-zero on any finding, so it can gate a build. See docs/checks.md.

As a library:

    import lhcheck
    ctx = lhcheck.Context("mods/my-mod/files", vanilla="...data.dat.vanilla.bak")
    findings, stats = lhcheck.run(ctx)
"""
from __future__ import annotations

import argparse
import functools
import os
import re
import sys
import zipfile
from collections import namedtuple
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import resource_format as rf  # noqa: E402

DEFAULT_VANILLA = os.path.join(
    "C:" + os.sep, "Program Files (x86)", "GOG Galaxy", "Games",
    "Lionheart - Legacy of the Crusader", "data.dat.vanilla.bak")

Finding = namedtuple("Finding", "check message")

LF = chr(10)
CRLF = chr(13) + chr(10)
DASHES = "-" * 60

# Binary payloads -- icon art, sprites, audio, models. They are shipped verbatim and are
# not resource text, so the grammar and line-ending checks do not apply to them. Every
# other extension is checked, so a new *text* type cannot slip through by being unlisted.
BINARY = {".mdl16", ".frm16", ".ogg", ".wav", ".gr2", ".way", ".bmp", ".tga", ".png"}

# Editor/* markers are placeholders the editor draws and the engine ignores, and the
# empty value and !Unknown Model are both legal "no art" spellings.
MODEL_OK = {"", "!unknown model"}

# `Cur Sequence` has to name an animation the model actually has, and the failure is the
# same fatal crash with the same dialog -- 0.5.0 shipped a chest set to "Idle" when the
# chest models only have Closed/Open/Opening. Rather than parse sequence tables out of
# the .mdl16, take the pairs vanilla itself uses: 200 shipped maps are a better authority
# on which sequence goes with which model than anything inferred.
SEQ_RE = re.compile(r"Model=([^\r\n]+)\r\n(?:[^\r\n]*\r\n){0,12}?[ \t]*Cur Sequence=([^\r\n]*)")

# a class-valued field whose value starts with whitespace; built from chr() so no escape
# sequence can be mangled on the way into this file (that is how the shipped defect happened)
WS_CLASS = re.compile(("^[^" + chr(13) + chr(10) + "=]*=[ " + chr(9) + "]+C[A-Z][A-Za-z0-9]*" + chr(13) + chr(10)).encode("latin-1"), re.M)
# A0.17: `Some Field=` on one line and `{` on the next. The class name was deleted and its block
# left behind -- a load-time fatal in the engine, invisible to resource_format.
ORPHAN_BLOCK = re.compile(("^[ " + chr(9) + "]*([^" + chr(13) + chr(10) + "=]+)=" + chr(13)
                           + chr(10) + "[ " + chr(9) + "]*\\{").encode("latin-1"), re.M)

# A0.18: a value that is itself a `Key=Value` assignment. 0.71.10's map builder took the literal
# `Else=` as part of a block's indentation, so every line of the conversation it generated came out
# as `Else=<tab>Dialog Tree File=...`. resource_format round-trips that happily, A0.15 sees a
# registered class name on the first line, and the old gate PASSED it -- the warden's walk to the
# bars, the whole point of that release, shipped as seven dead lines in the zip and in data.dat.
# Measured against the shipped game: of 4,035,942 `key=value` lines, the only values holding an `=`
# that is not preceded by a space belong to `Action work in progress` (195 designer comments) and
# `Text` (3 lines of prose, e.g. "Resistances -= 100"). Anything else is a builder bug.
PROSE_KEYS = {"action work in progress", "text", "reply text"}
ASSIGN_LINE = re.compile("^[ " + chr(9) + "]*([^" + chr(13) + chr(10) + "=]+)=([^" + chr(13) + chr(10) + "]*)$")


# --------------------------------------------------------------------------- the check registry
# (id, the Lionheart Fixt QA label it came from, one line). Per-file checks run in one pass over
# the tree, in this order, then the whole-tree checks run in theirs. The order is what makes the
# report read the same as Fixt's validate.py did.
FILE_CHECKS = [
    ("encoding", "A0.9", "latin-1 clean text, and no empty binary payload"),
    ("nested-assignment", "A0.18", "no value is itself a Key=Value assignment, and no brace is a whole value"),
    ("dialogtree", "A0.1-A0.5", "DialogTree structure, Go to node ID and Requirement resolution, embedded objects parse"),
    ("nis-exit", "A0.16", "no newly reachable exit leaves the player inside a non-interactive sequence"),
    ("node-refs", "A0.7b", "every map/can/tree -> dialogue node reference matches byte-exactly"),
    ("parse", "A0.6/A0.7", "resource files parse and round-trip byte-exact through resource_format"),
    ("class-whitespace", "A0.6", "no class-valued field is led by whitespace"),
    ("orphan-block", "A0.17", "no empty value is followed by a brace block"),
    ("array-count", "A0.6", "every Array's Item Count is right and comes first"),
    ("models", "A0.8", "every Model= exists, and Cur Sequence is one vanilla pairs with it"),
]
TREE_CHECKS = [
    ("quest-states", "-", "every quest state is activated somewhere, and the States array is well formed"),
    ("references", "A0.8", "every path-shaped resource reference resolves to a file"),
    ("self-reference", "-", "no DialogTree names itself, and no trees form a reference loop"),
    ("overridden-drops", "A0.14", "no can-level quest-item drop is overridden by the generator that spawns it"),
    ("class-names", "A0.15", "every =CSomething is a class the engine registers"),
    ("dangling-names", "-", "every name-bearing field names something the game can be called"),
    ("empty-blocks", "-", "no block vanilla always fills is shipped empty"),
    ("series-flow", "-", "no relay fires itself inline; no chained series has dead steps"),
    ("nameless-spawns", "-", "no script aims at a name only a nameless spawn would bear"),
    ("corpse-specifier", "A0.18", "no interaction specifier is installed on the entity that is dying"),
    ("repeatable-grants", "A0.19", "no item-giving node is reachable ungated while other replies to it are gated"),
]
ALL_IDS = [c[0] for c in FILE_CHECKS + TREE_CHECKS]


class Context:
    """What every check reads: the mod tree, the vanilla archive, and the indexes over both.

    `root` is the folder that overlays data.dat. `vanilla` is the pristine archive; it defaults to
    $LIONHEART_VANILLA, then the GOG install. `exe` is Lionheart.exe, for the class-name check; it
    defaults to the one beside the archive.

    `config` carries the few choices a mod makes about its own history:

      chained_relay_prefix      series-flow's dead-step rule applies only to relays the mod named.
                                Given a prefix, "named by the mod" means "starts with it" (Fixt
                                uses "Fixt"). Left None, it means "not a relay of the vanilla copy
                                of the same map".
      nameless_spawn_skip       map paths (relative to the root) nameless-spawns leaves alone.
      nameless_spawn_excuse_vanilla
                                True (default) excuses a miss the vanilla copy of the same map
                                already has. A mod that repairs those itself sets it False.
    """

    def __init__(self, root, vanilla=None, exe=None, config=None):
        self.root = Path(root)
        if not self.root.is_dir():
            raise FileNotFoundError("mod root not found: %r" % str(root))
        self.vanilla = str(vanilla or os.environ.get("LIONHEART_VANILLA", DEFAULT_VANILLA))
        if not Path(self.vanilla).exists():
            raise FileNotFoundError("vanilla archive not found: %r -- pass --vanilla" % self.vanilla)
        self.zf = zipfile.ZipFile(self.vanilla)
        self.exe = Path(exe) if exe else Path(self.vanilla).parent / "Lionheart.exe"
        self.config = dict(config or {})
        self.notes = []
        self.files = sorted(p for p in self.root.rglob("*") if p.is_file())
        self._vanilla_dangling = {}
        self._vanilla_reachable = {}

    # ------------------------------------------------------------------ helpers
    def rel(self, p):
        return p.relative_to(self.root).as_posix()

    def read(self, rel):
        """A file as the game would load it: the mod's copy, else vanilla's. Text, latin-1."""
        p = self.root / rel
        return (p.read_bytes() if p.exists() else self.zf.read(rel)).decode("latin-1")

    @functools.cached_property
    def names(self):
        return self.zf.namelist()

    @functools.cached_property
    def names_lower(self):
        out = {}
        for n in self.names:
            out.setdefault(n.lower(), n)
        return out

    @functools.cached_property
    def cans(self):
        out = {Path(n).stem.lower() for n in self.names
               if n.lower().endswith(".can") and "/requirements/" in n.lower()}
        out |= {Path(p).stem.lower() for p in self.root.rglob("*.can")}
        return out

    @functools.cached_property
    def canpaths(self):
        out = {n[len("Resources/"):-len(".can")].lower()
               for n in self.names if n.lower().endswith(".can")}
        res = self.root / "Resources"
        out |= {str(p.relative_to(res)).replace("\\", "/")[:-4].lower()
                for p in self.root.rglob("*.can")} if res.exists() else set()
        return out

    @functools.cached_property
    def models(self):
        # Every Model= an entity names must exist as art, or the game dies on map entry with a
        # "Fatal Not Found Error" naming the model and the map. That is a hard crash, not a
        # silent failure -- and it shipped in Fixt 0.5.0, from a chest placed with the invented
        # path "Environments/Misc/Chest/Chest A". Nothing else would have caught it: the file
        # parsed, round-tripped byte-exact, deployed byte-identical, and passed every other gate.
        out = {n[len("Cache/Models/"):-len(".mdl16")].lower()
               for n in self.names
               if n.lower().startswith("cache/models/") and n.lower().endswith(".mdl16")}
        mdir = self.root / "Cache" / "Models"
        if mdir.exists():
            out |= {str(p.relative_to(mdir)).replace("\\", "/")[:-len(".mdl16")].lower()
                    for p in mdir.rglob("*.mdl16")}
        return out

    @functools.cached_property
    def vanilla_seq(self):
        out = {}
        for n in self.names:
            if not n.lower().endswith(".zax"):
                continue
            for m in SEQ_RE.finditer(self.zf.read(n).decode("latin-1")):
                out.setdefault(m.group(1).strip().lower(), set()).add(m.group(2).strip())
        return out

    @functools.cached_property
    def can_name(self):
        # A can's `User Assigned Name` is what a spawn is called when its generator leaves `New Name`
        # blank. 725 of them carry one, and that mapping is the whole reason a list written from
        # filenames matches nothing.
        out = {}
        for n in self.names:
            if n.endswith(".can"):
                m = re.search(r"\r\n\t*User Assigned Name=([^\r\n]*)", self.zf.read(n).decode("latin-1"))
                if m and m.group(1).strip():
                    out[n[len("Resources/"):-len(".can")].lower()] = m.group(1).strip()
        return out


# --------------------------------------------------------------------------- per-file checks
def check_encoding(ctx, p, raw):
    try:
        raw.decode("latin-1").encode("latin-1")
    except Exception:
        return [p.name + ": not latin-1 clean"]
    return []


def check_nested_assignment(ctx, p, raw):
    out = []
    for ln in raw.decode("latin-1").split(CRLF):
        m = ASSIGN_LINE.match(ln)
        if not m or m.group(1).strip().lower() in PROSE_KEYS:
            continue
        v = m.group(2)
        # The same mangling turns a block's own braces into `SomeField={` and `SomeField=}`, and a
        # block of nothing but nested class names would show no other symptom. Zero vanilla lines
        # have a brace as their whole value.
        if v.strip() in ("{", "}"):
            out.append(p.name + ": a brace as a whole value (A0.18): " + repr(ln.strip()[:74]))
            continue
        for i, ch in enumerate(v):
            if ch == "=" and (i == 0 or v[i - 1] != " "):
                out.append(p.name + ": a value that is itself an assignment (A0.18) -- a builder"
                           " wrote the field name into the indentation: " + repr(ln.strip()[:74]))
                break
    return out


def terminal_exits(t):
    """({lowered id: as written}, {lowered ids that end the NIS}) for one tree's text."""
    reach = {g.strip().lower() for g in re.findall(r"^Go to node ID=(.*)$", t, re.M) if g.strip()}
    ends, all_ = set(), {}
    for seg in t.split(LF + DASHES + LF):
        m = re.match(r"Node ID=(.*)$", seg, re.M)
        if not m:
            continue
        nid = m.group(1).strip()
        if nid.lower() not in reach:
            continue                              # orphan: cannot be entered
        gos = re.findall(r"^Go to node ID=(.*)$", seg, re.M)
        if not gos or any(g.strip() for g in gos):
            continue                              # not a terminal exit
        all_[nid.lower()] = nid
        if "EndNonInteractiveSequenceAction" in seg:
            ends.add(nid.lower())
    return all_, ends


def vanilla_nis_tree(ctx, p):
    """Does the SHIPPED copy of this tree run wholly inside a non-interactive sequence?

    True only when vanilla could reach two or more terminal exits and EVERY one of them ends
    the sequence -- which is how `cathartoughs` is built, and how a tree says "an NIS is
    running for this entire conversation". `DaVinciBarcelona` and `Bar Patrons` end one
    somewhere too, but most of their exits correctly do not, so they are ordinary dialogue
    and A0.16 must stay silent about them. Returns the vanilla-reachable exits as well, so a
    node vanilla could already enter is never blamed on this mod.
    """
    rel = ctx.rel(p)
    if rel not in ctx._vanilla_reachable:
        try:
            v = ctx.zf.read(rel).decode("latin-1").replace(CRLF, LF)
        except KeyError:
            ctx._vanilla_reachable[rel] = (False, set())
        else:
            all_, ends = terminal_exits(v)
            ctx._vanilla_reachable[rel] = (len(all_) >= 2 and set(all_) == ends, set(all_))
    return ctx._vanilla_reachable[rel]


def vanilla_dangling(ctx, p):
    """Dangling `Go to node ID` targets in the SHIPPED copy of this same tree.

    Empty for a tree this mod authored, which is the point: only inherited breakage is
    excused, never breakage in something we wrote.
    """
    rel = ctx.rel(p)
    if rel in ctx._vanilla_dangling:
        return ctx._vanilla_dangling[rel]
    out = set()
    try:
        v = ctx.zf.read(rel).decode("latin-1").replace(CRLF, LF)
    except KeyError:
        pass
    else:
        ids = {x.strip().lower() for x in re.findall(r"^Node ID=(.*)$", v, re.M)}
        for m in re.finditer(r"^Go to node ID=(.*)$", v, re.M):
            g = m.group(1).strip().lower()
            if g and g not in ids and g not in ("!none", "none"):
                out.add(g)
    ctx._vanilla_dangling[rel] = out
    return out


def check_dialogtree(ctx, p, raw):
    fails = []
    name = p.name
    if b"\n" in raw and raw.count(b"\r\n") != raw.count(b"\n"):
        fails.append(name + ": mixed line endings")
    t = raw.decode("latin-1").replace(CRLF, LF)

    if not t.startswith("CDialogTree" + LF + "{" + LF):
        fails.append(name + ": missing CDialogTree wrapper")
    if not t.rstrip().endswith("}"):
        fails.append(name + ": missing closing brace")
    if t.count(LF + "{") != t.count(LF + "}"):
        fails.append(name + ": brace imbalance")

    # only top-level records count: a balloon inside a Custom Action carries its own Node ID= line
    ids, depth = [], 0
    for line in t.split(LF):
        if line == "{": depth += 1
        elif line == "}": depth -= 1
        elif depth == 1 and line.startswith("Node ID="): ids.append(line[8:].strip())
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        fails.append(name + ": duplicate node IDs " + repr(sorted(dupes)))
    low = {i.lower() for i in ids}

    # A mod's dialogue tree is very often a *shipped* tree with nodes spliced into it, so it
    # inherits whatever was already broken -- `Fish Monger` alone ships four dead
    # `Go to node ID=` targets, and `Bar Patrons` ships three more in a branch nothing can
    # reach. Reporting those says nothing about the mod and drowns out the ones it would
    # actually introduce, so they are tolerated: compare against the shipped copy of the same
    # file and report only what is new. A tree authored from scratch has no vanilla
    # counterpart, so every dangling target in it is still reported.
    inherited = vanilla_dangling(ctx, p)
    for m in re.finditer(r"^Go to node ID=(.*)$", t, re.M):
        g = m.group(1).strip()
        if g and g.lower() not in low and g.lower() not in ("!none", "none"):
            if g.lower() in inherited:
                continue
            fails.append(name + ": dangling target " + repr(g))

    for m in re.finditer(r"^Requirement=(.*)$", t, re.M):
        r = m.group(1).strip()
        if not r or r == "!None":
            continue
        if r.lower() in ctx.cans or r.lower() in ctx.canpaths:
            continue
        fails.append(name + ": unresolved Requirement " + repr(r))

    replies = t.count(LF + "Reply Text=")
    gotos = t.count(LF + "Go to node ID=")
    if replies != gotos:
        fails.append(name + ": %d replies but %d goto lines" % (replies, gotos))

    # Every reply must be preceded by a blank line. Vanilla holds this without a single
    # exception -- 10915 replies, 0 violations -- and the parser needs it: without the
    # separator a reply is swallowed into the one before it, so its Custom Action never
    # runs. That shipped silently in Fixt from 0.2.0 to 0.5 because helpers that reorder
    # replies rebuilt nodes by joining on a single newline. Caught in play.
    for m in re.finditer(LF + "Requirement=", t):
        if not t[:m.start()].endswith(LF):
            ctx_ = t[:m.start()].rsplit(LF + "Node ID=", 1)
            where = ctx_[1].split(LF)[0].strip() if len(ctx_) > 1 else "?"
            fails.append(name + ": reply not separated by a blank line, in node "
                         + repr(where))
            break

    # No blank line may fall inside an action or requirement block. Vanilla holds this
    # without exception (0 occurrences in 10915 replies); a blank line there is the
    # signature of a block helper that was handed an already-rendered child block and
    # appended its own line terminator after it. Harmless-looking, but the reply separator
    # is a blank line, so a stray one inside a Custom Action is the parser's cue to end the
    # reply early. Caught while building Fixt 0.15.0's Iapetus bargain.
    for m in re.finditer(LF + LF + r"(?!Requirement=)([A-Za-z][^" + LF + r"=]*)=", t):
        if not t[:m.start()].endswith("}"):
            continue
        ctx_ = t[:m.start()].rsplit(LF + "Node ID=", 1)
        where = ctx_[1].split(LF)[0].strip() if len(ctx_) > 1 else "?"
        fails.append(name + ": blank line inside a block, before " + repr(m.group(1))
                     + " in node " + repr(where))
        break

    for m in re.finditer(r"^(?:Custom Action|Custom Requirement)=(\w+)\n\{\n", t, re.M):
        start = m.end() - 2
        depth, i = 0, start
        while i < len(t):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        try:
            rf.parse_resource_text(m.group(1) + LF + t[start:i + 1])
        except Exception as e:
            fails.append(name + ": embedded " + m.group(1) + " failed to parse: " + str(e))
    return fails


def check_nis_exit(ctx, p, raw):
    # A0.16 -- an exit that leaves the player inside a non-interactive sequence HANGS THE GAME.
    # Reported from play in Fixt issue #6: paying the Montaillou Cathars 150 gold spun the gears
    # forever. `cathartoughs` begins its ambush with a `CBeginNonInteractiveSequenceAction` in
    # the map and had five reachable exits, only four of which ended it. The fifth,
    # `100 Welcome`, carries no actions at all -- and it is VANILLA'S node, an orphan nothing
    # could reach until Fixt 0.14.0 wired `Wielder IS` to it and 0.24.0 added the Templar's
    # 150-gold reply. The defect shipped the moment the node became enterable.
    #
    # KNOWN BLIND SPOT, and it is the one that nearly made this fix worse than the bug. "Exit"
    # here means reply-reachable: a node a MAP opens directly is invisible to this check.
    # `100 Welcome` is exactly that -- vanilla hangs it on `Cathar tough guy 3` as a
    # GetCloseThenTalk greeting with `Trigger Only Once=0`, which is why it rightly carries no
    # actions and why giving it the resolution would have let a friendly hello end a sequence
    # that was not running, re-award the XP and delete all three toughs, repeatably. Before
    # satisfying this check by adding actions to a node, grep the maps for it.
    #
    # Scoped twice so it accuses only what this mod did. First the tree must be one that runs
    # wholly inside an NIS in vanilla -- every exit vanilla could reach ends it -- which is
    # what makes `cathartoughs` different from `DaVinciBarcelona` and `Bar Patrons`, where an
    # NIS is ended on one branch and the ordinary goodbyes correctly do not. Then only an exit
    # VANILLA COULD NOT ALREADY ENTER is reported, so inherited shapes stay quiet.
    fails = []
    in_nis, was_reachable = vanilla_nis_tree(ctx, p)
    if in_nis:
        t = raw.decode("latin-1").replace(CRLF, LF)
        all_, ends = terminal_exits(t)
        for low in sorted(set(all_) - ends - was_reachable):
            fails.append(p.name + ": node " + repr(all_[low]) + " is an exit this mod made reachable"
                         " and it never ends the non-interactive sequence (A0.16) -- the game"
                         " hangs with the gears spinning")
    return fails


def tree_node_ids(ctx, tree_path):
    """Exact node IDs of a tree, preferring the mod copy over vanilla."""
    raw = None
    for suffix in (".DialogTree", ".dialogtree"):
        local = ctx.root / ("Resources/" + tree_path + suffix)
        if local.exists():
            raw = local.read_bytes()
            break
    if raw is None:
        hit = ctx.names_lower.get(("Resources/" + tree_path + ".dialogtree").lower())
        if hit is None:
            return None
        raw = ctx.zf.read(hit)
    txt = raw.decode("latin-1").replace(CRLF, LF)
    return [m.group(1) for m in re.finditer(r"^Node ID=(.*)$", txt, re.M)]


def check_node_refs(ctx, p, raw):
    # An exact node reference is just as fatal written in a tree or a can as written in a map:
    # until Fixt 0.25.2 this ran on .zax alone, which is the shape that shipped broken -- and
    # canned objects carry them too, as 0.25.2's seven do.
    fails = []
    z = raw.decode("latin-1").replace(CRLF, LF)
    pattern = (r"(?:CDisplayDialogTreeAction|CDisplayDialogBalloonAction)\s*\n"
               r"\s*\{(.*?)\n\s*\}")
    for m in re.finditer(pattern, z, re.S):
        body = m.group(1)
        f = re.search(r"Dialog Tree File=([^\n]*)", body)
        n = re.search(r"Node ID=([^\n]*)", body)
        if not (f and n):
            continue
        tree = f.group(1).strip()
        want = n.group(1)          # deliberately NOT stripped
        ids = tree_node_ids(ctx, tree)
        if ids is None:
            fails.append(p.name + ": dialogue tree not found: " + tree)
        elif want not in ids:
            near = [x for x in ids if x.strip().lower() == want.strip().lower()]
            why = ("tree has " + repr(near[0])) if near else "no such node"
            fails.append(p.name + ": CRASH -- exact node ref " + repr(want) +
                         " into " + tree.split("/")[-1] + " (" + why + ")")
    return fails


def check_models(ctx, p, raw):
    """Every Model= names art that exists. A miss is a fatal crash on map entry."""
    fails = []
    seen = set()
    for m in re.finditer(r"^\s*Model=(.*)$", raw.decode("latin-1"), re.M):
        v = m.group(1).strip()
        low = v.lower()
        if low in MODEL_OK or low.startswith("editor/") or low in seen:
            continue
        seen.add(low)
        if low not in ctx.models:
            fails.append(p.name + ": CRASH -- Model=" + repr(v)
                         + " does not exist; the engine dies on map entry")
    # Only pairs this mod introduces are judged; a pair already in the vanilla copy of
    # the same map is the game's own business even if it looks odd.
    try:
        vanilla_same_map = ctx.zf.read(ctx.rel(p)).decode("latin-1")
    except KeyError:
        vanilla_same_map = ""
    for m in SEQ_RE.finditer(raw.decode("latin-1")):
        mod, seq = m.group(1).strip(), m.group(2).strip()
        if mod.lower().startswith("editor/") or mod.lower() in MODEL_OK:
            continue
        if SEQ_RE.search(vanilla_same_map) and re.search(
                re.escape("Model=" + mod) + r"\r\n(?:[^\r\n]*\r\n){0,12}?[ \t]*Cur Sequence="
                + re.escape(seq), vanilla_same_map):
            continue
        known = ctx.vanilla_seq.get(mod.lower())
        if not known:
            # Art no shipped map ever places (Barrel Explode: effect art with no Idle) --
            # the undercroft crashed on entry with "Idle is missing" from exactly this.
            fails.append(p.name + ": CRASH -- Model=" + repr(mod) + " is placed on no vanilla map,"
                         " so no Cur Sequence is known to be safe for it")
            continue
        if seq not in known:
            fails.append(p.name + ": CRASH -- Cur Sequence=" + repr(seq) + " on Model="
                         + repr(mod) + "; vanilla uses " + repr(sorted(known)))
    return fails


def check_resource(ctx, p, raw, enabled):
    """parse, class-whitespace, orphan-block and array-count: one parse serves all four."""
    out = []
    try:
        node = rf.parse_resource_text(raw.decode("latin-1"))
    except Exception as e:
        if "parse" in enabled:
            out.append(Finding("parse", p.name + ": parse failed: " + str(e)))
        return out
    # A class-valued field whose value begins with whitespace is a crash: the engine looks up
    # a class named "<tabs>CMultipleActionsAction" and refuses to load the map. Fixt 0.9.1
    # shipped one, from re-indenting a nested block without stripping the first line's leading
    # tabs. The parser keeps such whitespace as part of the value, so only a byte check catches it.
    if "class-whitespace" in enabled:
        for m in WS_CLASS.finditer(raw):
            out.append(Finding("class-whitespace", p.name + ": CRASH -- class value led by whitespace: "
                               + repr(m.group(0)[:70])))
    # A0.17 -- a field with an EMPTY value must not be followed by a brace block. Blanking a
    # class name and leaving the block it owned reads fine to this parser, which attaches the
    # orphan to the preceding key, and kills the engine the moment the file loads:
    #   Found unexpected '{' while parsing class "CSkeletonAI" ... :Object:Action:AI
    # Fixt 0.60.5 shipped exactly that in Fixt Horse Calm.can and crashed the Crossroads.
    if "orphan-block" in enabled:
        for m in ORPHAN_BLOCK.finditer(raw):
            out.append(Finding("orphan-block", p.name + ": CRASH -- empty value followed by a block: "
                               + repr(m.group(1).decode("latin-1").strip())))
    # Compared in memory. Fixt's validate.py once wrote the round-trip to one fixed temp file,
    # `%TEMP%/_rf_check.txt`, so two runs at once read each other's output and reported
    # "not in canonical engine formatting" on maps nobody had touched.
    if "parse" in enabled and node.to_text().encode("latin-1") != raw:
        out.append(Finding("parse", p.name + ": not in canonical engine formatting"))
    if "array-count" in enabled:
        out.extend(Finding("array-count", m) for m in check_array_counts(p, node))
    return out


def check_array_counts(p, node, path="", fails=None):
    # Every Array's `Item Count` (or `Array Count`) must equal its number of items; vanilla
    # never disagrees. A splice that lands a new item inside the previous item's braces --
    # the array's closing brace is indented one deeper than `Action=Array`, so an rfind for
    # the shallower brace matches the tail of a deeper one -- bumps the count without adding
    # an item, and the stray block becomes an unknown field of its neighbour. Two of Fixt's
    # armory/cathedral reset relays shipped that way before this check existed.
    if fails is None:
        fails = []
    if node.type_name == "Array":
        count = None; items = 0
        for k, v in node.fields:
            if k in ("Item Count", "Array Count") and count is None:
                count = int(v)
            elif k not in ("Item Count", "Array Count"):
                items += 1
        if count is not None and count != items:
            fails.append(p.name + ": Array at " + (path or "<root>") + " says " + str(count) + " items, holds " + str(items))
        # And it must say so FIRST. The engine takes the count and then reads that many items, so an
        # item written ahead of the declaration is at best dropped. Vanilla never does it: 0 of
        # 103,108 arrays across 200 maps. Fixt 0.79.7 did, once, by inserting at index 0 of a field
        # list whose index 0 was the count, and the equality check above passed it because the number
        # was still right. That shipped the Brother Pau Karma penalty as dead weight.
        keys = [k for k, _ in node.fields]
        if ("Item Count" in keys or "Array Count" in keys) and keys[0] not in ("Item Count", "Array Count"):
            fails.append(p.name + ": Array at " + (path or "<root>") + " declares its count after "
                         + repr(keys[0]) + "; it must be the first field")
    for k, v in node.fields:
        if isinstance(v, rf.ResourceNode):
            check_array_counts(p, v, path + "/" + k, fails)
    return fails


def _file_pass(ctx, enabled):
    out, binary = [], 0
    for p in ctx.files:
        raw = p.read_bytes()
        suffix = p.suffix.lower()
        if suffix in BINARY:
            binary += 1
            if "encoding" in enabled and len(raw) == 0:
                out.append(Finding("encoding", p.name + ": binary payload is empty"))
            continue
        if "encoding" in enabled:
            out.extend(Finding("encoding", m) for m in check_encoding(ctx, p, raw))
        if "nested-assignment" in enabled:
            out.extend(Finding("nested-assignment", m) for m in check_nested_assignment(ctx, p, raw))
        if suffix == ".dialogtree":
            if "dialogtree" in enabled:
                out.extend(Finding("dialogtree", m) for m in check_dialogtree(ctx, p, raw))
            if "nis-exit" in enabled:
                out.extend(Finding("nis-exit", m) for m in check_nis_exit(ctx, p, raw))
            if "node-refs" in enabled:
                out.extend(Finding("node-refs", m) for m in check_node_refs(ctx, p, raw))
        else:
            out.extend(check_resource(ctx, p, raw, enabled))
            if suffix in (".can", ".zax") and "node-refs" in enabled:
                out.extend(Finding("node-refs", m) for m in check_node_refs(ctx, p, raw))
            if suffix == ".zax" and "models" in enabled:
                out.extend(Finding("models", m) for m in check_models(ctx, p, raw))
    return out, binary


# --------------------------------------------------------------------------- whole-tree checks
# Every state of every quest this mod defines must be set by something, somewhere.
# A state that is only ever *read* is a quest that can be offered and accepted but never
# actually starts -- so its turn-in, gated on that state, never appears. Two of Quinn's
# three reagent errands shipped in Fixt 0.4.0 that way: the reply that offers them carried
# the Custom Requirement deciding whether to show it, and no Custom Action to activate it.
# Both were uncompletable, and one of them gated the peaceful route to a lava troll hide.
# Caught in play three releases later.
# .can and .zax are tab-indented; DialogTrees are flat. Allow both, or this silently
# fails to see any activation that lives outside a conversation.
#
# The activation does not have to live in *our* files. As soon as a mod ships an edited
# copy of a shipped quest -- Fixt 0.6.0's `help distressed sailor` was the first -- the
# states it inherits are activated by vanilla maps it does not ship, and scanning only the
# mod tree reports every one of them as dead. So the vanilla archive is scanned too, with
# the mod's own files shadowing their archive counterparts rather than being unioned with
# them: unioning would hide an activation the mod had deliberately *removed*.
SET_STATE = re.compile(r"CActivateQuestStateAction[ \t]*\n[ \t]*\{[ \t]*\n"
                       r"[ \t]*Quest=([^\n]*)\n[ \t]*State=([^\n]*)")
SCANNED_FOR_STATES = (".zax", ".dialogtree", ".can", ".txt")


def check_quest_states(ctx):
    fails = []
    activated = set()

    def collect(text):
        for m in SET_STATE.finditer(text):
            activated.add((m.group(1).strip().lower(), m.group(2).strip()))

    ours = set()
    for p in ctx.files:
        if p.suffix.lower() in BINARY:
            continue
        ours.add(ctx.rel(p).lower())
        collect(p.read_bytes().decode("latin-1").replace(CRLF, LF))
    for name in ctx.names:
        low = name.lower()
        if low.startswith("cache/") or not low.endswith(SCANNED_FOR_STATES):
            continue
        if low in ours:                      # our edited copy already contributed
            continue
        collect(ctx.zf.read(name).decode("latin-1").replace(CRLF, LF))

    for q in ctx.root.rglob("*.Quest.txt"):
        rel = ctx.rel(q)
        if not rel.startswith("Resources/"):
            continue
        qpath = rel[len("Resources/"):-len(".Quest.txt")].lower()
        qt = q.read_bytes().decode("latin-1").replace(CRLF, LF)
        ids = [s.strip() for s in re.findall(r"^\s*ID=(.*)$", qt, re.M)]

        # The array's declared length must match what follows it. A `.zax` tolerates a wrong
        # `Item Count` in some places because the reader walks braces, but a quest's state
        # array is read as a counted list: declare too few and the tail is silently dropped,
        # which presents as a quest whose log never advances past a certain step.
        m = re.search(r"States=Array\s*\n\s*\{\s*\n\s*Item Count=(\d+)", qt)
        if not m:
            fails.append(q.name + ": no States=Array with an Item Count")
        elif int(m.group(1)) != len(ids):
            fails.append(q.name + ": Item Count=%s but %d State entries"
                         % (m.group(1), len(ids)))

        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            fails.append(q.name + ": duplicate state IDs " + repr(dupes))
        for s in ids:
            if not re.fullmatch(r"[A-Z0-9]{8}", s):
                fails.append(q.name + ": state ID " + repr(s)
                             + " is not 8 uppercase alphanumerics")

        for s in ids:
            if s and (qpath, s) not in activated:
                fails.append(q.name + ": state " + repr(s)
                             + " is never activated -- the quest can be offered but never starts")
    return fails


# Every path-shaped resource reference must resolve to a file, in the mod or in vanilla.
# `Canned Expression=` and friends are explicit paths under Resources/, unlike a named
# `Requirement=`, which the engine resolves by name across the Requirements folders -- so
# writing a level-specific can's *name* in the path form silently yields an expression that
# cannot evaluate true, and every branch behind it takes the Else forever. Three such
# references (Calle Perdida's Cedric gate x10, Farshad's Order-of-Saladin greeting, Javier's
# initiate gate x2) shipped in three separate Fixt releases before this check existed.
REFERENCE_KINDS = [
    (re.compile(r"Canned Expression=([^\r\n]*)"), (".can",)),
    (re.compile(r"Canned Object=([^\r\n]*)"), (".can",)),
    (re.compile(r"Perk To Check For=([^\r\n]*)"), (".Perk",)),
    (re.compile(r"Inventory Item To (?:Check For|remove|Give)=([^\r\n]*)"), (".InventoryItem",)),
    (re.compile(r"Dialog Tree File=([^\r\n]*)"), (".DialogTree",)),
    (re.compile(r"Entity=([^\r\n]*)"), (".can",)),
    (re.compile(r"Quest=([^\r\n]*)"), (".Quest.txt", ".Quest")),
    # A .Race naming a skill that does not exist is FATAL AT STARTUP, before the main menu. Fixt's
    # Grace O'Malley shipped in v0.19.0 saying Skills/Fighting/Melee -- the real skill is
    # OneHandedMelee -- and survived seven releases because .race files were not scanned at all.
    (re.compile(r"(?:^|\n)\t*Skill(?: to select)?=([^\r\n]*)"), (".Skill",)),
    (re.compile(r"Derived Character Attribute=([^\r\n]*)"), (".DerivedCharacterAttribute",)),
    (re.compile(r"(?:^|\n)\t*Race=([^\r\n]*)"), (".Race", ".race")),
]


def resource_exists(path, exts, have, mine):
    for e in exts:
        for cand in (("Resources/" + path + e).lower(), (path + e).lower()):
            if cand in have or cand in mine:
                return True
    return False


def check_references(ctx):
    fails = []
    have = set(n.lower() for n in ctx.names)
    mine = set()
    for f in ctx.files:
        rel = ctx.rel(f)
        mine.add(("Resources/" + rel).lower())
        mine.add(rel.lower())
    for f in ctx.files:
        if f.suffix.lower() not in (".dialogtree", ".zax", ".can", ".race"):
            continue
        text = f.read_bytes().decode("latin-1")
        seen = set()
        for rx, exts in REFERENCE_KINDS:
            for m in rx.finditer(text):
                v = m.group(1).strip()
                if "/" not in v or v.startswith("!") or v.endswith("!None") or v in seen:
                    continue
                seen.add(v)
                if not resource_exists(v, exts, have, mine):
                    fails.append(f.name + ": reference does not resolve to any file: " + repr(v))
    return fails


def check_self_reference(ctx):
    """A DialogTree naming its own file is FATAL the moment the tree loads -- the engine reports
    "Got stuck in an infinite loop while trying to load ...", so the NPC cannot be spoken to at all.

    Vanilla never does it: all four of its in-tree `Dialog Tree File=` references name a DIFFERENT
    tree (cortes -> Shylocke, SewerEntranceBeggar -> Find Enrique, SewerEntranceThief -> Find
    Juanita, Rakeb -> Woodcutter). Referencing another tree is fine; referencing your own is not.

    The fix the engine asks for is indirection: move the action into a `CCannedObject` and fire it
    with `CUseCannedActionAction{Canned Object=...}`, as Fixt 0.25.2 did for eleven sites.

    The engine detects a loop, not merely a self-reference, so `A -> B -> A` would hang the same way.
    The graph is therefore built over vanilla's trees as well as the mod's, since a mod tree can point
    into a shipped one that points back.
    """
    fails = []
    seen, text_of = set(), {}
    for n in ctx.names:
        if n.lower().endswith(".dialogtree"):
            text_of[n[len("Resources/"):-len(".DialogTree")].lower()] = ctx.zf.read(n).decode("latin-1")
    names = {}
    res = ctx.root / "Resources"
    for f in (sorted(ctx.root.rglob("*.[Dd]ialog[Tt]ree")) if res.exists() else []):
        if not f.is_file() or str(f).lower() in seen:
            continue
        seen.add(str(f).lower())
        rel = str(f.relative_to(res)).replace("\\", "/")
        key = re.sub(r"\.dialogtree$", "", rel, flags=re.I).lower()
        text_of[key] = f.read_bytes().decode("latin-1")     # the mod wins the load order
        names[key] = f.name

    edges = {}
    for key, text in text_of.items():
        edges[key] = {m.group(1).strip().lower()
                      for m in re.finditer(r"Dialog Tree File=([^\r\n]*)", text)}

    state, reported = {}, set()

    def walk(n, stack):
        state[n] = 1
        for m in edges.get(n, ()):
            if m not in edges:
                continue
            if state.get(m) == 1:
                cyc = stack[stack.index(m):] + [m]
                sig = tuple(sorted(set(cyc)))
                if sig in reported:
                    continue
                reported.add(sig)
                who = names.get(cyc[0], cyc[0].split("/")[-1])
                if len(set(cyc)) == 1:
                    fails.append(who + ": names its own file, which hangs the loader -- route it "
                                       "through a CCannedObject and CUseCannedActionAction")
                else:
                    fails.append(who + ": dialogue trees form a reference loop, which hangs the "
                                       "loader -- " + " -> ".join(x.split("/")[-1] for x in cyc))
            elif state.get(m, 0) == 0:
                walk(m, stack + [m])
        state[n] = 2

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    for k in list(edges):
        if state.get(k, 0) == 0:
            walk(k, [k])
    return fails


def _balanced(t, s):
    """End offset of the brace block that starts at or after `s`."""
    d, j = 0, t.index("{", s)
    while True:
        if t[j] == "{":
            d += 1
        elif t[j] == "}":
            d -= 1
            if d == 0:
                return j + 1
        j += 1


def check_overridden_drops(ctx):
    """A quest-item drop on a creature can is dead code if every spawner overrides the slot.

    This shipped in Fixt 0.10.0 and went unnoticed until someone played it: the Lava Troll Hide was
    put on all three `Lava Troll Boss` cans in `Destroyed Script Action`, copied field for field from
    Iapetus and Lethos, and it could never fire. The chief is spawned through a `CGeneratorAI` whose
    `After Action` runs `CSetDestroyedScriptActionAction` -- described in the exe as *"Changes the
    'Destroyed Action' of any entity on the map"* -- so the generator's `New Destroyed Action` replaces
    the can's the instant the creature appears.

    Nothing else here would catch it. The can parsed, round-tripped byte-exact, named a real item,
    referenced a real field used 3960 times, and matched a working vanilla boss line for line. Only the
    interaction between the can and the map that spawns it is wrong.

    So: for every quest item a mod can drops from its own destroyed slot, find the generators that
    spawn that can and fail if one of them overrides the slot without re-offering the same item. The
    fix is to put the item in the generator's own `New Destroyed Action`, which is what vanilla does for
    the four Titan bosses.
    """
    fails = []
    mine = ctx.root / "Resources" / "Monster Cans"
    if not mine.exists():
        return fails
    drops = {}
    for p in sorted(mine.rglob("*.can")):
        text = p.read_bytes().decode("latin-1")
        m = re.search(r"Destroyed Script Action=C[A-Za-z]", text)
        if not m:
            continue
        blk = text[m.start():_balanced(text, m.start())]
        items = set(re.findall(r"Item=(Inventory/Specific Item Cans/Quest Items/[^\r\n]+)", blk))
        if items:
            rel = str(p.relative_to(ctx.root / "Resources")).replace("\\", "/")[:-len(".can")]
            drops[rel] = {i.strip() for i in items}
    if not drops:
        return fails

    # the map that counts is the mod's if it exists, else the shipped one
    maps = {}
    for n in ctx.names:
        if n.lower().endswith(".zax"):
            maps[n] = None
    for p in ctx.root.rglob("*.zax"):
        maps[str(p.relative_to(ctx.root)).replace("\\", "/")] = p

    for rel, items in sorted(drops.items()):
        spawners, overridden = 0, []
        for n, p in sorted(maps.items()):
            text = (p.read_bytes() if p is not None else ctx.zf.read(n)).decode("latin-1")
            if "Entity=" + rel not in text:
                continue
            i = 0
            while True:
                j = text.find("Level Part=CEntityBase", i)
                if j < 0:
                    break
                e = _balanced(text, j)
                i = e
                part = text[j:e]
                if "Entity=" + rel not in part or "CGeneratorAI" not in part:
                    continue
                spawners += 1
                if "CSetDestroyedScriptActionAction" not in part:
                    continue
                k = part.index("CSetDestroyedScriptActionAction")
                nda = part[k:_balanced(part, k)]
                if not any(it in nda for it in items):
                    who = (re.findall(r"Name=([^\r\n]*)", part) or ["?"])[0].strip()
                    overridden.append(n.split("/")[-1] + " / " + repr(who))
        if spawners and overridden:
            fails.append(rel.split("/")[-1] + ".can: its quest-item drop "
                         + repr(sorted(items)[0].split("/")[-1])
                         + " is overridden by the generator that spawns it, so it can never fire -- "
                         + "put the item in that generator's New Destroyed Action (as vanilla does for "
                         + "the Titan bosses): " + "; ".join(sorted(set(overridden))))
    return fails


def check_class_names(ctx):
    """Every `=CSomething` we write must be a class the engine registers.

    A player hit a hard CTD entering the Inquisition Chambers -- *"Tried to use an unknown class
    ClsQuestStatusCompletedAction"* -- because Fixt's `GrandInquisitor.DialogTree` named
    `CIsQuestStatusCompletedAction`, which does not exist. The real class is
    `CIsQuestCompletedAction`, the field set was already right, and only the name was wrong. It
    shipped in Fixt 0.13.0 and survived 27 tagged releases.

    Nothing else here would have caught it: the tree parsed, round-tripped byte-exact, had balanced
    braces, no dangling targets and correct blank lines. So class names get their own authority --
    the C-prefixed strings in the exe, which is how the engine registers them, plus every class
    vanilla's own data uses.

    Quest state IDs (`State=CHF3M8QW`) look like class names to a regex, so they are excluded twice
    over: by key, and by requiring a lowercase letter, which every real CamelCase class has and no
    state ID does.
    """
    fails = []
    exe = ctx.exe
    if not exe.exists():
        ctx.notes.append("note: %s not found, so class names are unchecked (A0.15)" % exe.name)
        return fails
    known = set(m.group(0).decode("latin-1")
                for m in re.finditer(rb"C[A-Z][A-Za-z0-9_]{1,60}", exe.read_bytes()))

    # Scanning vanilla's own data for class names costs a pass over the whole archive, so it is
    # deferred: the exe's registered names clear everything in practice, and the archive is
    # consulted only to clear a candidate the exe did not list -- which should be nothing.
    cache = {}

    def vanilla_knows(cls):
        if not cache:
            cache["names"] = set()
            for n in ctx.names:
                if n.endswith((".can", ".zax", ".DialogTree", ".InventoryItem", ".Race",
                               ".Perk", ".Trait")):
                    for mm in re.finditer(r"=[ \t]*(C[A-Z][A-Za-z0-9_]*)\r?\n",
                                          ctx.zf.read(n).decode("latin-1")):
                        cache["names"].add(mm.group(1))
        return cls in cache["names"]

    # The hot path is a plain scan with no key capture: a leading `[^\r\n=]*` backtracks over every
    # line in every .zax and cost more than the rest of the gate put together. The field name is only
    # needed to report a failure, so it is recovered from the line then -- which is almost never.
    CLASS = re.compile(r"=[ \t]*(C[A-Z][A-Za-z0-9_]*)\r?\n")
    for f in ctx.files:
        if f.suffix.lower() not in (
                ".can", ".zax", ".dialogtree", ".inventoryitem", ".race", ".perk", ".trait"):
            continue
        text = f.read_bytes().decode("latin-1")
        seen = set()
        for m in CLASS.finditer(text):
            cls = m.group(1)
            if cls in known or cls in seen:
                continue
            if not any(c.islower() for c in cls):
                continue              # all-caps is an id; real classes are CamelCase
            key = text[text.rfind("\n", 0, m.start()) + 1:m.start()].strip()
            if key.lower().endswith("state"):
                continue              # `State=CHF3M8QW` is a quest state id, not a type
            if vanilla_knows(cls):
                continue
            seen.add(cls)
            fails.append(f.name + ": CRASH -- names a class the engine does not register: "
                         + repr(cls) + " (on " + repr(key) + "). The game dies on load with "
                         '"Invalid class type", naming this file')
    return fails


NAME_FIELDS = ("Target Name", "Entity Name", "Enemy Name", "Name To Check For", "Target",
               "Generator Name", "Name of Position", "Speaker", "Name To Target", "Source Name",
               "Old Name", "Companion To Release", "Part To Fade", "Door Name")
NAME_TOKENS = {"", "!none", "none", "player1", "player#1-9#"}
NAME_FIELD_RE = re.compile(r"(?:^|\r\n)\t*(%s)=([^\r\n]*)" % "|".join(NAME_FIELDS))


def names_borne(ctx, text):
    """Every name something in this file can be called at run time."""
    out = {x.strip() for x in re.findall(r"\r\n\t*Name=([^\r\n]*)\r\n", text) if x.strip()}
    out |= {x.strip() for x in re.findall(r"New Name=([^\r\n]*)", text) if x.strip()}
    for e in re.findall(r"Entity=([^\r\n]*)", text):
        hit = ctx.can_name.get(e.strip().lower())
        if hit:
            out.add(hit)
    return out


def check_dangling_names(ctx):
    """A name-bearing field whose value nothing in the game can be called. The action is inert and
    nothing reports it.

    This is the single most expensive class Fixt has shipped. Eleven such names sat in the three
    Montserrat maps for fourteen releases: the lists that pacify, rout and re-aggro Sahar's
    garrison were written from character-template FILENAMES, and a generator with a blank `New Name`
    leaves its spawn carrying the can's `User Assigned Name` instead -- `Snakebreed Venom` is really
    `Snakebreed Viper`, `Snakebreed Boss` is `Snakebreed Crusher`. So the parley stood down one
    family of four while the rest kept swinging, and the rout walked off a quarter of the abbey. Two
    players reported the symptoms for weeks and no gate had anything to say.

    A name resolves if ANY map or can in the game, the mod's or vanilla's, can produce it: a `Name=`
    on a Level Part, a `New Name=` on a generator or a clone, or the `User Assigned Name=` of a can
    some generator spawns. That definition is deliberately generous -- it covers companions like
    `Cortes with arm`, which exist at run time with no Level Part of their own -- because the point
    is to catch a name borne by NOTHING, not to prove which map it is in.

    Measured before it was written: against this definition the whole archive yields two dangling
    names in vanilla's own files, so inherited debris is excused per file the way
    `vanilla_dangling` excuses an inherited node reference.
    """
    fails = []
    borne = set()
    for name in ctx.names:
        if name.endswith((".zax", ".can")):
            borne |= names_borne(ctx, ctx.zf.read(name).decode("latin-1"))
    for p in ctx.files:
        if p.suffix.lower() in (".zax", ".can"):
            borne |= names_borne(ctx, p.read_bytes().decode("latin-1"))

    def dangling(text):
        out = set()
        for m in NAME_FIELD_RE.finditer(text):
            for part in m.group(2).split(","):
                v = part.strip()
                if not v or v.lower() in NAME_TOKENS or v.startswith("$") or "/" in v:
                    continue
                if v not in borne:
                    out.add((m.group(1), v))
        return out

    for p in ctx.files:
        if p.suffix.lower() not in (".zax", ".can", ".dialogtree"):
            continue
        rel = ctx.rel(p)
        mine = dangling(p.read_bytes().decode("latin-1"))
        if not mine:
            continue
        try:
            theirs = dangling(ctx.zf.read(rel).decode("latin-1"))
        except KeyError:
            theirs = set()
        for field, val in sorted(mine - theirs):
            fails.append(p.name + ": " + field + "=" + repr(val)
                         + " names something nothing in the game can be called")
    return fails


# Nested blocks the engine always wants filled in. Each count is from the 200 vanilla maps.
NEVER_EMPTY = {"CEntityBehaviorMoveToPos": 4735,
               "CEntityBehaviorTurnToAngle": 1355,
               "CNonInteractiveSequence": 169}
EMPTY_BLOCK = re.compile(r"=(C[A-Za-z]+)\r\n\t*\{\r\n\t*\}")


def check_empty_blocks(ctx):
    """A sub-object shipped empty that vanilla never ships empty.

    `Move Behavior=CEntityBehaviorMoveToPos {}` is how Fixt's Montserrat rout was written in all three
    abbey maps, and vanilla fills that block 4,735 times out of 4,735. Default-constructed it has no
    path-finding and no walk sequence, so the garrison Sahar promised would leave heads straight at
    its exit marker through the walls, never arrives, and never runs the `Done action=CDeleteAction`
    that removes it. The pacify worked, so players reported "they are not attackable any more" and
    "killing her doesn't make the garrison disappear" -- for fourteen releases, because the beat that
    was broken was the one nobody could see failing.

    An empty block is not wrong in general: vanilla ships `CEndNonInteractiveSequenceAction` empty
    188 times and `CPointWeaponAtTargetEntityBehavior` empty 761 times. Only the three types above
    are never empty there, so only those are gated.
    """
    fails = []
    for p in ctx.files:
        # dialogue trees too, not just maps. Fixt 0.79.8 repaired three of these in the abbey maps
        # and added this check, and a FOURTH copy sat in `Sahar.DialogTree` -- in the reply that
        # actually runs when you accept Sahar's ring. With no move behaviour the `CGoToAI` was
        # finished the moment it started, so its `Done action=CDeleteAction` fired at once and the
        # garrison was deleted where it stood instead of walking out. Every gate passed, because
        # this one skipped the file. A tree's brace objects are flat rather than tab-indented, and
        # the pattern already allows zero tabs.
        if p.suffix.lower() not in (".zax", ".dialogtree"):
            continue
        text = p.read_bytes().decode("latin-1")
        for m in EMPTY_BLOCK.finditer(text):
            cls = m.group(1)
            if cls not in NEVER_EMPTY:
                continue
            line = text[:m.start()].count("\r\n") + 1
            part = re.findall(r"\r\n\t*Name=([^\r\n]*)\r\n", text[:m.start()])
            fails.append("%s: %s is empty at line %d (in %r); vanilla fills it %d/%d times"
                         % (p.name, cls, line, (part[-1] if part else "?"),
                            NEVER_EMPTY[cls], NEVER_EMPTY[cls]))
    return fails


SELF_TARGETS = ("$Instigator", "$instigator", "$Trigger", "$trigger")


def check_corpse_specifier(ctx):
    """An interaction specifier installed on the entity that is dying (A0.18).

    Fixt 0.79.9 gave Sahar a talk interaction so a mis-closed conversation could be reopened, and put
    it in her death script instead of her spawn action -- the script that wrote it selected the array
    with a loop that had no `break`, so it took the last match in walk order rather than the first.
    She was handed her conversation when she died. Every existing gate passed: the map round-tripped
    byte-exact, every array count was right, and no name dangled.

    The broad form of this rule is false, so the gate is narrow. Vanilla installs a specifier from
    inside a destroyed script 4 times out of 336 -- the chest in Inquisition Chambers2, its south
    door, the Temple District church doors, and the map in Montaillou's House1 -- and every one
    names a *different* object, which is the legitimate "when this breaks, that becomes usable"
    shape. None targets the dying entity, and a specifier on a corpse can never fire.
    """
    fails = []
    for p in ctx.files:
        if p.suffix.lower() not in (".zax", ".can"):
            continue
        raw = p.read_bytes().decode("latin-1")
        if "New Destroyed Action" not in raw:
            continue
        try:
            root = rf.parse_resource_text(raw)
        except Exception:
            continue                      # other checks own parse failures

        def walk(n):
            for k, v in n.fields:
                if isinstance(v, rf.ResourceNode):
                    yield v
                    yield from walk(v)

        for node in walk(root):
            if node.type_name != "CSetDestroyedScriptActionAction":
                continue
            script = node.get("New Destroyed Action")
            if not isinstance(script, rf.ResourceNode):
                continue
            for act in walk(script):
                if act.type_name != "CAddAIAction":
                    continue
                ai = act.get("AI")
                if not (isinstance(ai, rf.ResourceNode)
                        and ai.type_name == "CAIInteractionSpecifier"):
                    continue
                target = (act.get("Target Name") or "").strip()
                if target not in SELF_TARGETS:
                    continue               # naming another object is vanilla's own pattern
                fails.append("%s: a %s is added to %s inside a destroyed script; it can only run "
                             "once the entity is dead, so it never runs (vanilla: 0 of 336)"
                             % (p.name, ai.get("Interaction Type") or "CAIInteractionSpecifier",
                                target))
    return fails


def check_repeatable_grants(ctx):
    """A node that hands out an item, reachable without crossing any gate, while some other
    reply to it IS gated (A0.19).

    Found in play in Fixt 0.83.3: Brendan's Serpent's Bile could be milked forever. Seven replies
    reached `200 drink`; six checked that the `took irish drink` flag did not exist yet, and the
    seventh, on `200 irish`, was a bare `Requirement=!None`. `200 irish` hangs off the greeting
    by *"That accent is odd - where are you from?"*, so every conversation re-opened the tap. It
    also re-opened Sullivan's Clover, which is reached from `200 drink` and nowhere else.

    The naive version of this check -- "are all replies into the giving node gated" -- reports
    four more trees and every one of them is a false positive, because the ordinary and correct
    pattern is a gate at the *entrance to a branch* with ungated replies inside it. `Magic
    Machine`'s DaVinci Tank Gear looks identical to the bug and is fine: its three ungated
    sources are reachable only through one gated reply at `700`.

    So the question is reachability, not incoming edges: walk the tree from its entry points
    following only ungated replies, and fail if a giving node turns up in that walk while
    something else about it is gated. The gated sibling is what tells us the author meant to
    gate it; a node whose every path is ungated is usually deliberately repeatable.

    Entry points are the first node in the file plus every node with no incoming reply, because
    a node nothing points at must be entered from a map or a can.
    """
    fails = []
    res = ctx.root / "Resources"
    trees = sorted(res.rglob("*.[Dd]ialog[Tt]ree")) if res.exists() else []
    for p in trees:
        text = p.read_bytes().decode("latin-1")
        chunks = [c for c in text.split("-" * 60) if re.search(r"^Node ID=", c, re.M)]
        if not chunks:
            continue
        order, nodes = [], {}
        for c in chunks:
            nid = re.search(r"^Node ID=(.*)$", c, re.M).group(1).strip()
            order.append(nid)
            nodes[nid] = c

        # replies, as (source, target, gated)
        edges, incoming = [], set()
        for nid, body in nodes.items():
            for blk in re.split("\r\n\r\n", body):
                m = re.search(r"^Go to node ID=(.*)$", blk, re.M)
                if not m:
                    continue
                tgt = m.group(1).strip()
                if not tgt:
                    continue
                gated = ("Custom Requirement=" in blk
                         or re.search(r"^Requirement=(?!!None)\S", blk, re.M) is not None)
                edges.append((nid, tgt, gated))
                incoming.add(tgt)

        entries = {order[0]} | {n for n in order if n not in incoming}
        seen, stack = set(entries), list(entries)
        while stack:
            cur = stack.pop()
            for src, tgt, gated in edges:
                if src == cur and not gated and tgt in nodes and tgt not in seen:
                    seen.add(tgt)
                    stack.append(tgt)

        for nid, body in nodes.items():
            items = [i.strip() for i in re.findall(r"^Inventory Item To Give=(.*)$", body, re.M)]
            if not items or nid not in seen:
                continue
            if not any(t == nid and g for _, t, g in edges):
                continue           # nothing about it is gated, so it is meant to repeat
            doors = sorted({s for s, t, g in edges if t == nid and not g})
            fails.append("%s: %r hands out %r and is reachable with no gate crossed, while other "
                         "replies to it ARE gated (A0.19) -- so it can be taken again every "
                         "conversation. Ungated door(s): %s"
                         % (p.name, nid, items[0].split("/")[-1], ", ".join(repr(d) for d in doors)))
    return fails


# --------------------------------------------------------------------------- series flow
# Two faults every other gate passes, because both resolve every reference.
#
# **A relay firing itself inline.** A series advances only after its step returns and a relay fires
# in the same call, so a step that fires its own relay runs again until the stack gives out.
# Crossing a delay, or a balloon's or a walk's After Action, is safe, because that runs later.
# Trigger Only Once blocks it too.
#
# **A chained series with steps behind one that never continues.** A series that drives itself, by
# firing its own relay, stops at the first step that neither fires that relay nor shows a
# conversation, so any step after it is dead. This found Fixt's credits ahead of fifteen steps of
# slides. A series that something else steps through is not chained and is left alone.
#
# Vanilla never does the first, so that rule covers every relay. Vanilla steps some series from
# outside while they also fire themselves, so the second rule covers only relays the mod named.
LATER = {"After Action", "Next Action", "Done action", "Stop Action", "Starting Action", "End Turn Action", "Action to execute After Effect"}


class N:
    __slots__ = ("cls", "kv")

    def __init__(self, c):
        self.cls = c
        self.kv = []


def parse_script(text):
    root = N("root")
    st = [root]
    pend = None
    for raw in text.split(CRLF):
        l = raw.strip()
        if l == "{":
            n = N(pend[1] if pend else "")
            (pend[2] if pend else st[-1]).kv.append((pend[0] if pend else "", n))
            st.append(n)
            pend = None
            continue
        if pend:
            pend[2].kv.append((pend[0], pend[1]))
            pend = None
        if l == "}":
            if len(st) > 1:
                st.pop()
            continue
        if "=" in l:
            k, v = l.split("=", 1)
            if re.fullmatch(r"C[A-Za-z0-9]+|Array", v):
                pend = (k, v, st[-1])
            else:
                st[-1].kv.append((k, v))
    return root


def _inline(n, name):
    """True if this action reaches a trigger of `name` without crossing anything that runs later."""
    if n.cls == "CTriggerRelayAction" and dict(n.kv).get("Relay Name") == name:
        return True
    return any(isinstance(v, N) and k not in LATER and _inline(v, name) for k, v in n.kv)


def _continues(n, name):
    """True if this action can hand on: a conversation, or firing the relay at any distance."""
    if n.cls == "CDisplayDialogTreeAction":
        return True
    if n.cls == "CTriggerRelayAction" and dict(n.kv).get("Relay Name") == name:
        return True
    return any(isinstance(v, N) and _continues(v, name) for k, v in n.kv)


def _fires(n, name):
    if n.cls == "CTriggerRelayAction" and dict(n.kv).get("Relay Name") == name:
        return True
    return any(isinstance(v, N) and _fires(v, name) for k, v in n.kv)


def _relays(root):
    def go(n, part):
        if n.cls == "CRelayAI":
            yield part, n
        for k, v in n.kv:
            if isinstance(v, N):
                yield from go(v, dict((a, b) for a, b in v.kv if not isinstance(b, N)).get("Name", part))

    yield from go(root, "")


def _series_in(n):
    if n.cls == "CSeriesAction":
        yield n
    for k, v in n.kv:
        if isinstance(v, N):
            yield from _series_in(v)


def check_series_flow(ctx):
    bad = []
    prefix = ctx.config.get("chained_relay_prefix")
    for f in sorted(ctx.root.rglob("*.zax")):
        rel = ctx.rel(f)
        root = parse_script(ctx.read(rel))
        vanilla_relays = None
        for name, ai in _relays(root):
            f_ = dict(ai.kv)
            act = f_.get("Action")
            if not name or not isinstance(act, N):
                continue
            if f_.get("Trigger Only Once") == "0" and _inline(act, name):
                bad.append(f"{rel}: relay `{name}` fires itself inline, and will recurse until the stack overflows")
            if prefix is not None:
                ours = name.startswith(prefix)
            else:
                if vanilla_relays is None:
                    try:
                        vanilla_relays = {n for n, _ in _relays(parse_script(ctx.zf.read(rel).decode("latin-1")))}
                    except KeyError:
                        vanilla_relays = set()
                ours = name not in vanilla_relays
            for s in _series_in(act):
                arr = dict(s.kv).get("Action")
                steps = [v for k, v in arr.kv if k == "Action" and isinstance(v, N)] if isinstance(arr, N) else []
                if not ours or not any(_fires(x, name) for x in steps):
                    continue
                last = max((i for i, x in enumerate(steps) if _continues(x, name)), default=-1)
                for i, x in enumerate(steps[:last]):
                    if not _continues(x, name):
                        bad.append(f"{rel}: relay `{name}` step {i + 1} does not continue, so steps {i + 2}-{len(steps)} are dead")
    return bad


# --------------------------------------------------------------------------- nameless spawns
# An action's name list compares an entity's `Name` (`0x62bc00`, through `0x5b9b40`), and skips one
# with none. A generator with a blank `New Name`, over a can with a blank `Name`, spawns exactly
# that. `User Assigned Name` is a display name the lookup never reads, so a script aimed at the
# display name of a nameless spawn reaches nobody. Found in Fixt because the English did not stand
# down: 0.79.4's repair of the garrison lists, and 0.97.3's Hujark retarget, were aimed at nobody.
# The repair is to give the generator a `New Name`; Fixt's tools/build_spawn_names.py does that.
SPAWN_FIELDS = r"(?:Target Name|Entity Name|Enemy Name|Name To Check For|Target|Name of Position)"


def _can_names(ctx, e, cache):
    """(display name, Name) of a can, or blanks."""
    if e not in cache:
        d = n = ""
        try:
            t = ctx.read("Resources/" + e + ".can")
            r = re.findall(r"User Assigned Name=([^\r\n]*)", t)
            d = r[0] if r else ""
            r = re.findall(r"(?m)^\t\tName=([^\r\n]*)\r", t)
            n = r[0] if r else ""
        except Exception:
            pass
        cache[e] = (d, n)
    return cache[e]


def _parts(z):
    return re.findall(r"(?ms)^\t\tLevel Part=\w+\r\n\t\t\{\r\n.*?^\t\t\}", z)


def _nameless(ctx, x, cache):
    """The display names a generator's spawns would have, if it spawns nameless characters."""
    if "CGeneratorAI" not in x and "CSimpleGenerator" not in x:
        return []
    if re.search(r"(?m)^\t\t\tName=[^\r\n]*Dead Body", x):
        return []
    nn = re.search(r"New Name=([^\r\n]*)", x)
    if not nn or nn.group(1):
        return []
    out = []
    for e in re.findall(r"Entity=([^\r\n]+)", x):
        d, n = _can_names(ctx, e, cache)
        if d and not n and d not in out:
            out.append(d)
    return out


def _wanted(z):
    """Lower-cased names this map's scripts aim at."""
    out = set()
    for m in re.finditer(r"(?m)^\s*" + SPAWN_FIELDS + r"=([^\r\n$][^\r\n]*)\r", z):
        out.update(t.strip().lower() for t in m.group(1).split(",") if t.strip())
    return out


def missed_spawn_names(ctx, z, cache=None):
    """Names scripts aim at that only nameless spawns would bear."""
    cache = {} if cache is None else cache
    named = {n.lower() for n in re.findall(r"(?m)^\t\t\tName=([^\r\n]+)\r", z)} | {n.lower() for n in re.findall(r"New Name=([^\r\n]+)\r", z)}
    bear = {d.lower() for x in _parts(z) for d in _nameless(ctx, x, cache)}
    return sorted((_wanted(z) & bear) - named)


def check_nameless_spawns(ctx):
    bad = []
    skip = set(ctx.config.get("nameless_spawn_skip", ()))
    excuse = ctx.config.get("nameless_spawn_excuse_vanilla", True)
    cache = {}
    for f in sorted(ctx.root.rglob("*.zax")):
        rel = ctx.rel(f)
        if rel in skip:
            continue
        miss = missed_spawn_names(ctx, ctx.read(rel), cache)
        if miss and excuse:
            try:
                theirs = set(missed_spawn_names(ctx, ctx.zf.read(rel).decode("latin-1"), cache))
            except KeyError:
                theirs = set()
            miss = [n for n in miss if n not in theirs]
        bad += [f"{rel}: scripts aim at `{n}`, which only nameless spawns bear, so they reach nobody" for n in miss]
    return bad


# --------------------------------------------------------------------------- running
TREE_FNS = {
    "quest-states": check_quest_states,
    "references": check_references,
    "self-reference": check_self_reference,
    "overridden-drops": check_overridden_drops,
    "class-names": check_class_names,
    "dangling-names": check_dangling_names,
    "empty-blocks": check_empty_blocks,
    "series-flow": check_series_flow,
    "nameless-spawns": check_nameless_spawns,
    "corpse-specifier": check_corpse_specifier,
    "repeatable-grants": check_repeatable_grants,
}


def select(only=None, skip=None, known=None):
    """The set of check ids to run. Unknown ids are an error, so a typo cannot skip nothing."""
    known = list(known or ALL_IDS)
    for i in list(only or ()) + list(skip or ()):
        if i not in known:
            raise ValueError("unknown check id %r; known: %s" % (i, ", ".join(known)))
    chosen = set(only) if only else set(known)
    return chosen - set(skip or ())


def run(ctx, only=None, skip=None, extra=()):
    """Run the checks. -> (findings, stats).

    `extra` is a list of (id, fn, after_id): a mod's own whole-tree check `fn(ctx) -> [message]`,
    run after the check named `after_id` (or last, if None), and selectable by `id` like any other.
    """
    tree_order = [c[0] for c in TREE_CHECKS]
    fns = dict(TREE_FNS)
    for cid, fn, after in extra:
        fns[cid] = fn
        tree_order.insert(tree_order.index(after) + 1 if after else len(tree_order), cid)
    enabled = select(only, skip, [c[0] for c in FILE_CHECKS] + tree_order)
    findings, binary = _file_pass(ctx, enabled)
    for cid in tree_order:
        if cid in enabled:
            findings.extend(Finding(cid, m) for m in fns[cid](ctx))
    return findings, {"files": len(ctx.files), "binary": binary}


def report(findings, stats, notes=(), out=None):
    """Print in the format Lionheart Fixt's validate.py always has, and return the exit code."""
    out = out or sys.stdout
    for n in notes:
        print(n, file=out)
    print("checked %d files (%d binary payloads skipped)" % (stats["files"], stats["binary"]), file=out)
    if findings:
        print("\n%d PROBLEM(S):" % len(findings), file=out)
        for f in findings:
            print("   -", f.message, file=out)
        return 1
    print("all checks passed", file=out)
    return 0


def resolve_root(path):
    """A modmanager package (mod.json beside files/) is checked through its files/."""
    p = Path(path)
    if (p / "mod.json").is_file() and (p / "files").is_dir():
        return p / "files"
    return p


def split_ids(vals):
    out = []
    for v in vals or ():
        out += [x.strip() for x in v.split(",") if x.strip()]
    return out


def add_arguments(ap):
    ap.add_argument("--vanilla", default=None,
                    help="pristine data.dat (default: $LIONHEART_VANILLA, then the GOG install)")
    ap.add_argument("--exe", default=None,
                    help="Lionheart.exe, for class-names (default: beside the vanilla archive)")
    ap.add_argument("--skip", action="append", metavar="ID", help="skip a check (repeatable, or comma-separated)")
    ap.add_argument("--only", action="append", metavar="ID", help="run only these checks")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Engine-rule checks for a Lionheart mod.")
    ap.add_argument("root", nargs="?", help="the mod root (overlays data.dat), or a folder with mod.json and files/")
    ap.add_argument("--list", action="store_true", help="list the checks and exit")
    add_arguments(ap)
    a = ap.parse_args(argv)
    if a.list:
        for cid, label, what in FILE_CHECKS + TREE_CHECKS:
            print("  %-18s %-10s %s" % (cid, label, what))
        return 0
    if not a.root:
        ap.error("a mod root is required")
    try:
        ctx = Context(resolve_root(a.root), a.vanilla, a.exe)
        findings, stats = run(ctx, split_ids(a.only), split_ids(a.skip))
    except (FileNotFoundError, ValueError) as e:
        print("lhcheck: %s" % e, file=sys.stderr)
        return 2
    return report(findings, stats, ctx.notes)


if __name__ == "__main__":
    sys.exit(main())
