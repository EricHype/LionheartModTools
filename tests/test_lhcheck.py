"""Tests for lhcheck.py and auto_open_risk.py: one seeded defect per check, plus a control.

    python -m unittest tests.test_lhcheck -v
    python -m pytest tests/test_lhcheck.py

Every check is tested against a tiny mod built in a temp folder, with a tiny stand-in for the
vanilla archive, so these run anywhere. Each test seeds the real defect the check was written for
and asserts the check reports it, then removes the defect (the control) and asserts it is silent --
a check that passes both ways would otherwise look like a pass.

The last class runs against the real data.dat.vanilla.bak and skips without it.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auto_open_risk  # noqa: E402
import lhcheck  # noqa: E402
from resource_format import ResourceNode  # noqa: E402

CR = "\r\n"
DASHES = "-" * 60


def R(type_name, *fields):
    return ResourceNode(type_name=type_name, fields=list(fields))


def res(node):
    """Canonical engine formatting, as bytes."""
    return node.to_text().encode("latin-1")


def level_map(*parts):
    return res(R("CLayerSaveData", ("Layer", R("CLayer", *[("Level Part", p) for p in parts]))))


def part(name, *fields, cls="CEntityBase"):
    return R(cls, ("Name", name), *fields)


def reply(goto="", text="Goodbye.", req="!None", action=None):
    """A reply block, as the lines of a DialogTree. `action` is (class, [field lines])."""
    lines = ["Requirement=" + req, "Reply Text=" + text, "Go to node ID=" + goto]
    if action:
        cls, body = action
        lines += ["Custom Action=" + cls, "{"] + list(body) + ["}"]
    lines.append("Icon=Exit Icon")
    return lines


def tree(name, nodes):
    """nodes: [(node id, text, [reply lines, ...])]."""
    lines = ["CDialogTree", "{", "Name=" + name, "Portrait=!None", "Should Have Voiceovers=0",
             "Default Canceled Node Action="]
    for nid, text, replies in nodes:
        lines += [DASHES, "Node ID=" + nid, "Text=" + text, "Should Have Voiceover=0"]
        for r in replies:
            lines += [""] + r
    lines.append("}")
    return (CR.join(lines) + CR).encode("latin-1")


class Mod:
    """A mod root and a stand-in vanilla archive, in a temp folder."""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.root = self.tmp / "mod"
        self.root.mkdir()
        self.van = {}
        self.exe = None

    def add(self, rel, data):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return self

    def remove(self, rel):
        (self.root / rel).unlink()

    def vanilla(self, rel, data):
        self.van[rel] = data
        return self

    def archive(self):
        z = self.tmp / "data.dat.vanilla.bak"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("Resources/placeholder.txt", "x")
            for k, v in self.van.items():
                zf.writestr(k, v)
        return z

    def ctx(self, **config):
        exe = self.tmp / "Lionheart.exe"
        if self.exe is not None:
            exe.write_bytes(b"\0".join(c.encode("latin-1") for c in self.exe))
        else:
            exe = self.tmp / "no-such.exe"
        return lhcheck.Context(self.root, self.archive(), exe=exe, config=config)

    def find(self, check, **config):
        ctx = self.ctx(**config)
        findings, _ = lhcheck.run(ctx, only=[check])
        return [f.message for f in findings if f.check == check]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mod = Mod(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def assertFinds(self, check, needle, **config):
        got = self.mod.find(check, **config)
        self.assertTrue(any(needle in m for m in got), "%s did not report %r; got %r" % (check, needle, got))
        return got

    def assertSilent(self, check, **config):
        self.assertEqual(self.mod.find(check, **config), [])


TREE = "Resources/Levels/T/Dialog/Bob.DialogTree"
CAN = "Resources/Common Objects and Scripts/Thing.can"
MAP = "Levels/T/Map.zax"


class FileChecks(Base):
    def test_encoding_empty_binary(self):
        self.mod.add("Cache/Models/Props/Empty.mdl16", b"")
        self.assertFinds("encoding", "binary payload is empty")
        self.mod.add("Cache/Models/Props/Empty.mdl16", b"\x01")
        self.assertSilent("encoding")

    def test_nested_assignment(self):
        # 0.71.10: `Else=<tab>Dialog Tree File=...`
        bad = R("CCannedObject", ("Action", R("CIfAction", ("Else", "\tDialog Tree File=Levels/T/Dialog/Bob"))))
        self.mod.add(CAN, res(bad))
        self.assertFinds("nested-assignment", "a value that is itself an assignment")
        self.mod.add(CAN, res(R("CCannedObject", ("Text", "Resistances -= 100"))))
        self.assertSilent("nested-assignment")

    def test_nested_assignment_brace_value(self):
        self.mod.add(CAN, res(R("CCannedObject", ("Action", "{"))))
        self.assertFinds("nested-assignment", "a brace as a whole value")

    def test_dialogtree_dangling_target(self):
        self.mod.add(TREE, tree("Bob", [("10 Hello", "Hi.", [reply("99 missing")])]))
        self.assertFinds("dialogtree", "dangling target '99 missing'")
        # case-insensitive, as the engine is: 244 vanilla links rely on it
        self.mod.add(TREE, tree("Bob", [("10 Hello", "Hi.", [reply("10 HELLO")])]))
        self.assertSilent("dialogtree")

    def test_dialogtree_inherited_dangling_is_excused(self):
        t = tree("Bob", [("10 Hello", "Hi.", [reply("99 missing")])])
        self.mod.add(TREE, t).vanilla(TREE, t)
        self.assertSilent("dialogtree")

    def test_dialogtree_unresolved_requirement(self):
        self.mod.add(TREE, tree("Bob", [("10 Hello", "Hi.", [reply(req="Has The Gold")])]))
        self.assertFinds("dialogtree", "unresolved Requirement 'Has The Gold'")
        self.mod.vanilla("Resources/Requirements/Has The Gold.can", res(R("CCannedExpression")))
        self.assertSilent("dialogtree")

    def test_dialogtree_reply_without_blank_line(self):
        t = tree("Bob", [("10 Hello", "Hi.", [reply(), reply()])])
        self.mod.add(TREE, t.replace(b"Icon=Exit Icon\r\n\r\nRequirement=", b"Icon=Exit Icon\r\nRequirement=", 1))
        self.assertFinds("dialogtree", "reply not separated by a blank line")

    def test_dialogtree_mixed_line_endings(self):
        t = tree("Bob", [("10 Hello", "Hi.", [reply()])])
        self.mod.add(TREE, t.replace(b"\r\n", b"\n", 2))
        self.assertFinds("dialogtree", "mixed line endings")

    def test_nis_exit(self):
        # cathartoughs: every exit vanilla could reach ends the sequence; the mod adds one that does not
        end = ("CEndNonInteractiveSequenceAction", [])
        vanilla = [("1 start", "Well?", [reply("2 a"), reply("3 b")]),
                   ("2 a", "A.", [reply(action=end)]),
                   ("3 b", "B.", [reply(action=end)])]
        self.mod.vanilla(TREE, tree("Bob", vanilla))
        modded = [("1 start", "Well?", [reply("2 a"), reply("3 b"), reply("4 c")])] + vanilla[1:] + \
                 [("4 c", "C.", [reply()])]
        self.mod.add(TREE, tree("Bob", modded))
        self.assertFinds("nis-exit", "node '4 c' is an exit this mod made reachable")
        modded[-1] = ("4 c", "C.", [reply(action=end)])
        self.mod.add(TREE, tree("Bob", modded))
        self.assertSilent("nis-exit")

    def _map_showing(self, node_id):
        show = R("CDisplayDialogTreeAction", ("Dialog Tree File", "Levels/T/Dialog/Bob"), ("Node ID", node_id))
        return level_map(part("Bob", ("Action", show)))

    def test_node_refs_byte_exact(self):
        # the Goblin Warrens crash: vanilla node IDs carry trailing spaces
        self.mod.add(TREE, tree("Bob", [("100 Hi ", "Hi.", [reply()])]))
        self.mod.add(MAP, self._map_showing("100 Hi"))
        self.assertFinds("node-refs", "CRASH -- exact node ref '100 Hi' into Bob (tree has '100 Hi ')")
        self.mod.add(MAP, self._map_showing("100 Hi "))
        self.assertSilent("node-refs")

    def test_node_refs_in_a_can(self):
        self.mod.add(TREE, tree("Bob", [("100 Hi", "Hi.", [reply()])]))
        show = R("CDisplayDialogTreeAction", ("Dialog Tree File", "Levels/T/Dialog/Bob"), ("Node ID", "100 Bye"))
        self.mod.add(CAN, res(R("CCannedObject", ("Action", show))))
        self.assertFinds("node-refs", "no such node")

    def test_parse_and_round_trip(self):
        good = res(R("CCannedObject", ("Action", R("CDeleteAction", ("Target Name", "$Instigator")))))
        self.mod.add(CAN, good.replace(b"\r\n", b"\n"))
        self.assertFinds("parse", "not in canonical engine formatting")
        self.mod.add(CAN, b"CCannedObject\r\n{\r\n\tAction=CDeleteAction\r\n\t{\r\n")
        self.assertFinds("parse", "parse failed")
        self.mod.add(CAN, good)
        self.assertSilent("parse")

    def test_class_value_led_by_whitespace(self):
        # 0.9.1: Then=<tabs>CMultipleActionsAction
        self.mod.add(CAN, res(R("CCannedObject", ("Then", "\tCMultipleActionsAction"))))
        self.assertFinds("class-whitespace", "class value led by whitespace")
        self.mod.add(CAN, res(R("CCannedObject", ("Then", "CMultipleActionsAction"))))
        self.assertSilent("class-whitespace")

    def test_orphan_block(self):
        # 0.60.5's Fixt Horse Calm.can: the class name blanked, its block left behind
        self.mod.add(CAN, res(R("CCannedObject", ("Attack AI", R("", ("Valid Targets", ""))))))
        self.assertFinds("orphan-block", "empty value followed by a block: 'Attack AI'")
        self.mod.add(CAN, res(R("CCannedObject", ("Attack AI", ""))))
        self.assertSilent("orphan-block")

    def test_array_count(self):
        arr = R("Array", ("Item Count", "2"), ("Action", R("CDeleteAction", ("Target Name", "$Instigator"))))
        self.mod.add(CAN, res(R("CCannedObject", ("Action", R("CMultipleActionsAction", ("Action", arr))))))
        self.assertFinds("array-count", "says 2 items, holds 1")
        arr.fields[0] = ("Item Count", "1")
        self.mod.add(CAN, res(R("CCannedObject", ("Action", R("CMultipleActionsAction", ("Action", arr))))))
        self.assertSilent("array-count")

    def test_array_count_declared_late(self):
        # 0.79.7's Brother Pau penalty: the right number, in the wrong place
        arr = R("Array", ("Action", R("CDeleteAction", ("Target Name", "$Instigator"))), ("Item Count", "1"))
        self.mod.add(CAN, res(R("CCannedObject", ("Action", arr))))
        self.assertFinds("array-count", "declares its count after 'Action'")

    def test_models(self):
        self.mod.vanilla("Cache/Models/Props/Chest.mdl16", b"\x01")
        self.mod.vanilla(MAP, level_map(part("Chest", ("Model", "Props/Chest"), ("Cur Sequence", "Closed"))))
        self.mod.add("Levels/T/Other.zax", level_map(part("Box", ("Model", "Environments/Misc/Chest/Chest A"))))
        self.assertFinds("models", "Model='Environments/Misc/Chest/Chest A' does not exist")
        # 0.5.0: a chest set to Idle, which the chest models do not have
        self.mod.add("Levels/T/Other.zax", level_map(part("Box", ("Model", "Props/Chest"), ("Cur Sequence", "Idle"))))
        self.assertFinds("models", "Cur Sequence='Idle' on Model='Props/Chest'; vanilla uses ['Closed']")
        self.mod.add("Levels/T/Other.zax", level_map(part("Box", ("Model", "Props/Chest"), ("Cur Sequence", "Closed"))))
        self.assertSilent("models")


QUEST = "Resources/Quests/Errand.Quest.txt"


class TreeChecks(Base):
    def _quest(self):
        states = R("Array", ("Item Count", "1"), ("State", R("CQuestState", ("ID", "ABCD1234"))))
        self.mod.add(QUEST, res(R("CQuest", ("States", states))))

    def test_quest_state_never_activated(self):
        # Quinn's reagent errands, 0.4.0: offered, never started
        self._quest()
        self.assertFinds("quest-states", "state 'ABCD1234' is never activated")
        act = R("CActivateQuestStateAction", ("Quest", "Quests/Errand"), ("State", "ABCD1234"))
        self.mod.add(MAP, level_map(part("Giver", ("Action", act))))
        self.assertSilent("quest-states")

    def test_quest_activated_by_vanilla_counts(self):
        self._quest()
        act = R("CActivateQuestStateAction", ("Quest", "Quests/Errand"), ("State", "ABCD1234"))
        self.mod.vanilla(MAP, level_map(part("Giver", ("Action", act))))
        self.assertSilent("quest-states")
        # ...unless the mod ships its own copy of that map without it
        self.mod.add(MAP, level_map(part("Giver")))
        self.assertFinds("quest-states", "never activated")

    def test_references(self):
        # Cedric's gate: a can's NAME written in the path form
        self.mod.add(CAN, res(R("CCannedObject", ("Canned Expression", "Requirements/Cedric Gate"))))
        self.assertFinds("references", "reference does not resolve to any file: 'Requirements/Cedric Gate'")
        self.mod.vanilla("Resources/Requirements/Cedric Gate.can", res(R("CCannedExpression")))
        self.assertSilent("references")

    def test_self_reference(self):
        show = ("CDisplayDialogTreeAction", ["Dialog Tree File=Levels/T/Dialog/Bob", "Node ID=10 Hello"])
        self.mod.add(TREE, tree("Bob", [("10 Hello", "Hi.", [reply(action=show)])]))
        self.assertFinds("self-reference", "names its own file, which hangs the loader")
        show = ("CDisplayDialogTreeAction", ["Dialog Tree File=Levels/T/Dialog/Other", "Node ID=1"])
        self.mod.add(TREE, tree("Bob", [("10 Hello", "Hi.", [reply(action=show)])]))
        self.assertSilent("self-reference")

    def test_reference_loop(self):
        def to(other):
            return ("CDisplayDialogTreeAction", ["Dialog Tree File=Levels/T/Dialog/" + other, "Node ID=1"])
        self.mod.add(TREE, tree("Bob", [("1", "Hi.", [reply(action=to("Ann"))])]))
        self.mod.add("Resources/Levels/T/Dialog/Ann.DialogTree", tree("Ann", [("1", "Hi.", [reply(action=to("Bob"))])]))
        self.assertFinds("self-reference", "dialogue trees form a reference loop")

    def _boss(self, nda):
        drop = R("CGenerateItemAction", ("Item", "Inventory/Specific Item Cans/Quest Items/Lava Troll Hide"))
        self.mod.add("Resources/Monster Cans/Boss.can", res(R("CCannedObject", ("Destroyed Script Action", drop))))
        gen = R("CGeneratorAI", ("Entity", "Monster Cans/Boss"),
                ("After Action", R("CSetDestroyedScriptActionAction", ("New Destroyed Action", nda))))
        self.mod.add(MAP, level_map(part("Boss Spawner", ("AI", gen))))

    def test_overridden_drop(self):
        # 0.10.0's Lava Troll Hide: the generator replaces the can's death slot
        self._boss(R("CDeleteAction", ("Target Name", "$Instigator")))
        self.assertFinds("overridden-drops", "its quest-item drop 'Lava Troll Hide' is overridden")
        self._boss(R("CGenerateItemAction", ("Item", "Inventory/Specific Item Cans/Quest Items/Lava Troll Hide")))
        self.assertSilent("overridden-drops")

    def test_class_names(self):
        # 0.13.0's CIsQuestStatusCompletedAction
        self.mod.exe = ["CCannedObject", "CIsQuestCompletedAction"]
        self.mod.add(CAN, res(R("CCannedObject", ("Expression", R("CIsQuestStatusCompletedAction")))))
        self.assertFinds("class-names", "names a class the engine does not register: 'CIsQuestStatusCompletedAction'")
        self.mod.add(CAN, res(R("CCannedObject", ("Expression", R("CIsQuestCompletedAction")),
                                ("State", "CHF3M8QW"))))
        self.assertSilent("class-names")

    def test_class_names_without_exe_is_a_note(self):
        self.mod.add(CAN, res(R("CCannedObject", ("Expression", R("CNothingAction")))))
        ctx = self.mod.ctx()
        findings, _ = lhcheck.run(ctx, only=["class-names"])
        self.assertEqual(findings, [])
        self.assertTrue(any("class names are unchecked" in n for n in ctx.notes))

    def test_dangling_names(self):
        # the Montserrat garrison lists, written from filenames
        act = R("CSetAttitudeAction", ("Target Name", "Snakebreed Venom"))
        self.mod.add(MAP, level_map(part("Snakebreed Viper"), part("Sahar", ("Action", act))))
        self.assertFinds("dangling-names", "Target Name='Snakebreed Venom' names something nothing")
        act.fields[0] = ("Target Name", "Snakebreed Viper")
        self.mod.add(MAP, level_map(part("Snakebreed Viper"), part("Sahar", ("Action", act))))
        self.assertSilent("dangling-names")

    def test_empty_blocks(self):
        # the Montserrat rout: a walk with no move behaviour
        go = R("CGoToAI", ("Move Behavior", R("CEntityBehaviorMoveToPos")))
        self.mod.add(MAP, level_map(part("Guard", ("AI", go))))
        self.assertFinds("empty-blocks", "CEntityBehaviorMoveToPos is empty")
        go.fields[0] = ("Move Behavior", R("CEntityBehaviorMoveToPos", ("Speed", "1")))
        self.mod.add(MAP, level_map(part("Guard", ("AI", go))))
        self.assertSilent("empty-blocks")

    def _relay(self, name, action, once="0"):
        return part(name, ("AI", R("CRelayAI", ("Trigger Only Once", once), ("Action", action))))

    def test_series_relay_fires_itself_inline(self):
        self.mod.add(MAP, level_map(self._relay("Loop", R("CTriggerRelayAction", ("Relay Name", "Loop")))))
        self.assertFinds("series-flow", "relay `Loop` fires itself inline")
        later = R("CDisplayDialogBalloonAction", ("After Action", R("CTriggerRelayAction", ("Relay Name", "Loop"))))
        self.mod.add(MAP, level_map(self._relay("Loop", later)))
        self.assertSilent("series-flow")

    def _chain(self, name):
        steps = R("Array", ("Item Count", "3"),
                  ("Action", R("CDeleteAction", ("Target Name", "Credits"))),
                  ("Action", R("CDisplayDialogTreeAction", ("Dialog Tree File", "Levels/T/Dialog/Bob"))),
                  ("Action", R("CTriggerRelayAction", ("Relay Name", name))))
        self.mod.add(MAP, level_map(self._relay(name, R("CSeriesAction", ("Action", steps)), once="1")))

    def test_series_dead_steps(self):
        # the epilogue: credits ahead of fifteen steps of slides
        self._chain("Fixt Slides")
        self.assertFinds("series-flow", "relay `Fixt Slides` step 1 does not continue, so steps 2-3 are dead")
        self.assertFinds("series-flow", "step 1 does not continue", chained_relay_prefix="Fixt")
        self.assertSilent("series-flow", chained_relay_prefix="Other")

    def test_series_dead_steps_default_scope_skips_vanilla_relays(self):
        self._chain("Slides")
        self.assertFinds("series-flow", "step 1 does not continue")
        self.mod.vanilla(MAP, (self.mod.root / MAP).read_bytes())
        self.assertSilent("series-flow")

    def _snakes(self, new_name):
        can = R("CCannedObject", ("Object", R("CCharacter", ("User Assigned Name", "Snakebreed Viper"), ("Name", ""))))
        self.mod.add("Resources/Monster Cans/Snake.can", res(can))
        gen = R("CGeneratorAI", ("Entity", "Monster Cans/Snake"), ("New Name", new_name))
        act = R("CSetAttitudeAction", ("Target Name", "Snakebreed Viper"))
        return level_map(part("Snake Gen", ("AI", gen)), part("Sahar", ("Action", act)))

    def test_nameless_spawns(self):
        self.mod.add(MAP, self._snakes(""))
        self.assertFinds("nameless-spawns", "scripts aim at `snakebreed viper`, which only nameless spawns bear")
        self.mod.add(MAP, self._snakes("Snakebreed Viper"))
        self.assertSilent("nameless-spawns")

    def test_nameless_spawns_inherited(self):
        self.mod.add(MAP, self._snakes("")).vanilla(MAP, self._snakes(""))
        self.assertSilent("nameless-spawns")
        self.assertFinds("nameless-spawns", "snakebreed viper", nameless_spawn_excuse_vanilla=False)
        self.assertSilent("nameless-spawns", nameless_spawn_excuse_vanilla=False, nameless_spawn_skip={MAP})

    def test_corpse_specifier(self):
        # 0.79.9: Sahar handed her conversation when she died
        add = R("CAddAIAction", ("Target Name", "$Instigator"),
                ("AI", R("CAIInteractionSpecifier", ("Interaction Type", "Interaction Specifiers/Talk"))))
        script = R("CMultipleActionsAction", ("Action", R("Array", ("Item Count", "1"), ("Action", add))))
        death = R("CSetDestroyedScriptActionAction", ("New Destroyed Action", script))
        self.mod.add(MAP, level_map(part("Sahar", ("Action", death))))
        self.assertFinds("corpse-specifier", "is added to $Instigator inside a destroyed script")
        add.fields[0] = ("Target Name", "Chest")
        self.mod.add(MAP, level_map(part("Sahar", ("Action", death))))
        self.assertSilent("corpse-specifier")

    def test_repeatable_grants(self):
        # 0.83.3: Brendan's Serpent's Bile, six gated doors and one open one
        give = ("CActionGiveInventoryItem", ["Inventory Item To Give=Inventory/Serpents Bile"])
        def build(open_door_req):
            return tree("Bob", [("1 hi", "Hi.", [reply("2 give", req="Took Drink"), reply("3 irish")]),
                                ("3 irish", "Irish.", [reply("2 give", req=open_door_req)]),
                                ("2 give", "Drink.", [reply(action=give)])])
        self.mod.add(TREE, build("!None"))
        self.assertFinds("repeatable-grants", "'2 give' hands out 'Serpents Bile'")
        self.mod.add(TREE, build("Took Drink"))
        self.assertSilent("repeatable-grants")


class Running(Base):
    def setUp(self):
        super().setUp()
        self.mod.add(CAN, res(R("CCannedObject", ("Then", "\tCDeleteAction"))))

    def _main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = lhcheck.main([str(self.mod.root), "--vanilla", str(self.mod.archive()),
                                 "--exe", str(self.mod.tmp / "no-such.exe")] + list(argv))
        return code, out.getvalue()

    def test_exit_code_and_format(self):
        code, out = self._main("--only", "class-whitespace")
        self.assertEqual(code, 1)
        self.assertIn("checked 1 files (0 binary payloads skipped)", out)
        self.assertIn("1 PROBLEM(S):", out)

    def test_skip(self):
        code, out = self._main("--skip", "class-whitespace,class-names")
        self.assertEqual(code, 0, out)
        self.assertIn("all checks passed", out)

    def test_unknown_id_is_an_error(self):
        code, out = self._main("--skip", "no-such-check")
        self.assertEqual(code, 2)
        self.assertIn("unknown check id", out)

    def test_package_folder_checks_its_files(self):
        pkg = self.mod.tmp / "pkg"
        (pkg / "files").mkdir(parents=True)
        (pkg / "mod.json").write_text("{}", encoding="utf-8")
        self.assertEqual(lhcheck.resolve_root(pkg), pkg / "files")

    def test_modmanager_check(self):
        import subprocess
        pkg = self.mod.tmp / "pkg"
        (pkg / "files").mkdir(parents=True)
        (pkg / "mod.json").write_text("{}", encoding="utf-8")
        (pkg / "files" / "Resources").mkdir()
        (pkg / "files" / "Resources" / "Thing.can").write_bytes((self.mod.root / CAN).read_bytes())
        cmd = [sys.executable, str(ROOT / "modmanager.py"), "check", str(pkg), "--vanilla",
               str(self.mod.archive()), "--exe", str(self.mod.tmp / "no-such.exe")]
        bad = subprocess.run(cmd + ["--only", "class-whitespace"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("class value led by whitespace", bad.stdout)
        ok = subprocess.run(cmd + ["--skip", "class-whitespace,class-names"], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

    def test_extra_check_runs_in_place(self):
        seen = []
        ctx = self.mod.ctx()
        findings, _ = lhcheck.run(ctx, only=["self-reference", "probes"],
                                  extra=[("probes", lambda c: seen.append(1) or ["x: probe"], "self-reference")])
        self.assertEqual([f.check for f in findings], ["probes"])
        self.assertEqual(seen, [1])


class AutoOpenRisk(Base):
    def _setup(self, costly=True):
        spec = R("CAIInteractionSpecifier", ("Interaction Type", "Interaction Specifiers/GetCloseThenTrigger"),
                 ("Action", R("CDisplayDialogTreeAction", ("Dialog Tree File", "Levels/T/Dialog/Tank"),
                              ("Node ID", "1 examine"))))
        self.mod.add(MAP, level_map(part("Tank", ("AI", spec))))
        act = ("CActionRemoveInventoryItem", ["Inventory Item To remove=Inventory/Rotary Gear"]) if costly else None
        self.mod.add("Resources/Levels/T/Dialog/Tank.DialogTree",
                     tree("Tank", [("1 examine", "A tank.", [reply(text="(fit the gear)", action=act)])]))

    def _run(self, allowed=None):
        out = io.StringIO()
        code = auto_open_risk.run(self.mod.root, self.mod.archive(), allowed, out=out)
        return code, out.getvalue()

    def test_costly_reply_on_proximity_node(self):
        # 0.57.4: the rotary gear lost on walking into the chamber
        self._setup()
        code, out = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn("tank / '1 examine' opened from Map.zax carries CActionRemoveInventoryItem", out)
        self.assertIn("[THIS MOD SHIPS THAT TREE]", out)

    def test_allow_list(self):
        self._setup()
        code, out = self._run({("tank", "1 examine"): "its text asks"})
        self.assertEqual(code, 0, out)
        self.assertIn("allowed: tank", out)

    def test_allow_file(self):
        p = self.mod.tmp / "allow.json"
        p.write_text('[{"tree": "Tank", "node": "1 Examine", "why": "asks"}]', encoding="utf-8")
        self.assertEqual(auto_open_risk.load_allow(p), {("tank", "1 examine"): "asks"})

    def test_harmless_reply(self):
        self._setup(costly=False)
        self.assertEqual(self._run()[0], 0)


DAT = Path(os.environ.get("LIONHEART_VANILLA", lhcheck.DEFAULT_VANILLA))


@unittest.skipUnless(DAT.is_file(), "needs data.dat.vanilla.bak")
class AgainstVanilla(unittest.TestCase):
    """A vanilla map copied into a mod unchanged must pass every check; one seeded defect must not."""

    @classmethod
    def setUpClass(cls):
        zf = zipfile.ZipFile(DAT)
        cands = [i for i in zf.infolist() if i.filename.lower().endswith(".zax")]
        cands.sort(key=lambda i: i.file_size)
        for i in cands:
            raw = zf.read(i.filename)
            if b"CDisplayDialogTreeAction" in raw:
                cls.rel, cls.raw = i.filename, raw
                break

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "mod"
        (self.root / self.rel).parent.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, data, only=None):
        (self.root / self.rel).write_bytes(data)
        ctx = lhcheck.Context(self.root, DAT)
        return lhcheck.run(ctx, only=only)[0]

    def test_unchanged_vanilla_map_is_clean(self):
        self.assertEqual(self._run(self.raw), [])

    def test_seeded_node_ref(self):
        i = self.raw.index(b"CDisplayDialogTreeAction")
        j = self.raw.index(b"Node ID=", i)
        k = self.raw.index(b"\r\n", j)
        seeded = self.raw[:k] + b"x" + self.raw[k:]
        got = self._run(seeded, only=["node-refs"])
        self.assertTrue(any("CRASH -- exact node ref" in f.message for f in got), got)


if __name__ == "__main__":
    unittest.main()
