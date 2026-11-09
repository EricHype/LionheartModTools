"""Tests for the can editor: `canedit_core` headless, and the window offscreen.

    python -m unittest tests.test_canedit -v

Needs the game's `data.dat.vanilla.bak` (the corpus every claim here is measured
against); the tests skip without it. The window tests also need PySide6, and run with
QT_QPA_PLATFORM=offscreen so no display is required.
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import canedit_core as core  # noqa: E402
from resource_format import ResourceNode  # noqa: E402

DAT = core.default_vanilla_dat()
HAVE_DAT = DAT.is_file()

SOLDIER = "Resources/Monster Cans/English Enemies/Soldier1.can"
ARCHER = "Resources/Monster Cans/Mongol Goblin Archer.can"
UNDEAD = "Resources/Monster Cans/Undead/Greater Skeleton.can"


def _diff(a: bytes, b: bytes) -> list[str]:
    return [l for l in difflib.unified_diff(
        a.decode("latin-1").split("\r\n"), b.decode("latin-1").split("\r\n"),
        lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("---", "+++")]


@unittest.skipUnless(HAVE_DAT, "needs data.dat.vanilla.bak")
class CoreTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.zf = zipfile.ZipFile(DAT)
        cls.cans = sorted(n for n in cls.zf.namelist() if n.lower().endswith(".can"))

    @classmethod
    def tearDownClass(cls):
        cls.zf.close()

    def test_every_vanilla_can_round_trips(self):
        bad = []
        for name in self.cans:
            raw = self.zf.read(name)
            doc = core.CanDocument(raw, resource_path=name)
            if doc.to_bytes() != raw:
                bad.append(name)
        print(f"\n  round-trip: {len(self.cans) - len(bad)} of {len(self.cans)} byte-identical")
        self.assertEqual(bad, [])
        self.assertGreater(len(self.cans), 1600)

    def test_one_field_edit_is_one_line(self):
        doc = core.CanDocument.from_vanilla(SOLDIER, DAT)
        before = doc.to_bytes()
        skel = core.find_skeleton(doc)
        skel.get("Target Type").set("Max Distance", "700")
        diff = _diff(before, doc.to_bytes())
        self.assertEqual(diff, ["-\t\t\t\t\tMax Distance=550", "+\t\t\t\t\tMax Distance=700"])
        self.assertIn(b"\r\n", doc.to_bytes())

    def test_save_is_byte_identical_and_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Resources" / "Soldier1.can"
            path.parent.mkdir(parents=True)
            raw = self.zf.read(SOLDIER)
            path.write_bytes(raw)
            doc = core.CanDocument.from_file(path)
            self.assertFalse(doc.read_only)
            doc.save()
            self.assertEqual(path.read_bytes(), raw)

    def test_vanilla_is_read_only(self):
        doc = core.CanDocument.from_vanilla(SOLDIER, DAT)
        self.assertTrue(doc.read_only)
        with self.assertRaises(PermissionError):
            doc.save()

    def test_refuses_to_write_inside_the_game(self):
        doc = core.CanDocument.from_vanilla(SOLDIER, DAT)
        with self.assertRaises(PermissionError):
            doc.save(core.DEFAULT_GAME_DIR / "data" / "x.can")

    def test_abstract_attack_ai_is_refused(self):
        for bad in ("CChargeAttackAI", "CStrafeAttackAI", "CAttackAI", "ChargeAttackAI"):
            doc = core.CanDocument.from_vanilla(SOLDIER, DAT)
            skel = core.find_skeleton(doc)
            skel.set("Attack AI", ResourceNode(bad, []))
            with self.assertRaises(core.UnsafeCanError) as ctx:
                doc.check_writable()
            self.assertIn(bad if bad.startswith("C") else "C" + bad, str(ctx.exception))
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "out.can"
                with self.assertRaises(core.UnsafeCanError):
                    doc.save(target)
                self.assertFalse(target.exists())
        # As a bare scalar value too.
        doc = core.CanDocument.from_vanilla(SOLDIER, DAT)
        core.find_skeleton(doc).set("Attack AI", "CChargeAttackAI")
        with self.assertRaises(core.UnsafeCanError):
            doc.check_writable()

    def test_no_vanilla_can_names_an_abstract_class(self):
        hits = [n for n in self.cans
                if core.find_abstract(core.CanDocument(self.zf.read(n)).root)]
        self.assertEqual(hits, [])

    def test_skeleton_found_in_monster_cans(self):
        monsters = [n for n in self.cans if n.startswith("Resources/Monster Cans/")]
        missing = []
        for name in monsters:
            doc = core.CanDocument(self.zf.read(name), resource_path=name)
            if core.find_skeleton(doc) is None:
                missing.append((name, core.why_no_skeleton(doc)))
        print(f"\n  CSkeletonAI found in {len(monsters) - len(missing)} of "
              f"{len(monsters)} monster cans; without one:")
        for name, why in missing:
            print(f"    {name}: {why}")
        # Seven dragons run CAIDragon; the Bounty Hunter Attacker ships with no AI.
        self.assertEqual(len(missing), 8)
        self.assertEqual(sum("CAIDragon" in why for _, why in missing), 7)
        self.assertTrue(any("Bounty Hunter Attacker" in n for n, _ in missing))

    def test_call_for_help_matches_vanilla_shape(self):
        doc = core.CanDocument.from_vanilla(ARCHER, DAT)
        skel = core.find_skeleton(doc)
        array = core.ensure_transitions(skel)
        before = len(array.items())
        array.fields.append((core.TRANSITION_ITEM_KEY, core.call_for_help_transition()))
        import script_schema as ss
        ss.sync_count(array)
        self.assertEqual(array.get("Array Count"), str(before + 1))
        self.assertIsNone(array.get("Item Count"))
        text = doc.to_bytes().decode("latin-1")
        self.assertIn("Array Item=CSkeletonTransition\r\n", text)
        self.assertIn("Action=CCallForReinforcementsAction\r\n", text)
        self.assertIn("Requester Name=$Trigger\r\n", text)
        self.assertIn("Max Distance=500\r\n", text)
        # Field order is vanilla's: the one order all 325 shipped transitions use.
        node = array.items()[-1]
        self.assertEqual([k for k, _ in node.fields],
                         ["Trigger", "From State", "To State", "Action", "IsDone"])

    def test_stand_still_carries_behaviours_over(self):
        doc = core.CanDocument.from_vanilla(ARCHER, DAT)
        skel = core.find_skeleton(doc)
        old = skel.get("Attack AI")
        new = core.build_attack_ai(doc, skel, "CStandStillAttackAI")
        self.assertEqual(new.type_name, "CStandStillAttackAI")
        self.assertEqual([k for k, _ in new.fields],
                         ["Marked For Deletion", "Publisher", "Shoot Behavior",
                          "Turn Behavior"])
        self.assertEqual(new.get("Shoot Behavior").to_text(), old.get("Shoot Behavior").to_text())
        self.assertIsNot(new.get("Shoot Behavior"), old.get("Shoot Behavior"))
        # And back again restores the original block, attack bank included.
        core.remember_attack_ai(doc, skel)
        skel.set("Attack AI", new)
        back = core.build_attack_ai(doc, skel, "CNormalAttackAI")
        self.assertEqual(back.to_text(), old.to_text())
        with self.assertRaises(core.UnsafeCanError):
            core.build_attack_ai(doc, skel, "CChargeAttackAI")

    def test_used_by_finds_generators(self):
        uses = core.UseIndex(DAT).uses(SOLDIER)
        maps = {u.map_path for u in uses}
        self.assertIn("Levels/3 Montaillou/02 Hamlet Burned.zax", maps)
        self.assertTrue(all(u.source == core.VANILLA_TAG for u in uses))

    def test_scan_entity_refs_names_the_part(self):
        text = ("CLevel\r\n{\r\n\tTree List=Array\r\n\t{\r\n\t\tLevel Part=CGenerator\r\n"
                "\t\t{\r\n\t\t\tName=Gen A\r\n\t\t\tThings=Array\r\n\t\t\t{\r\n"
                "\t\t\t\tName=inner\r\n\t\t\t\tEntity=Monster Cans/X\r\n\t\t\t}\r\n"
                "\t\t}\r\n\t}\r\n}\r\n")
        self.assertEqual(core.scan_entity_refs(text), [("Monster Cans/X", "Gen A")])

    def test_save_into_mod_registers_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            for newline in ("\n", "\r\n"):
                mod = Path(tmp) / f"mod{len(newline)}"
                (mod / "files").mkdir(parents=True)
                manifest = {"mod_format_version": 1, "id": "t", "files": ["Resources/a.txt"]}
                text = json.dumps(manifest, indent=2).replace("\n", newline) + newline
                (mod / "mod.json").write_bytes(text.encode())
                doc = core.CanDocument.from_vanilla(SOLDIER, DAT)
                target = doc.save_into_mod(mod)
                self.assertEqual(target, mod / "files" / SOLDIER)
                self.assertEqual(target.read_bytes(), self.zf.read(SOLDIER))
                raw = (mod / "mod.json").read_bytes().decode()
                self.assertEqual(json.loads(raw)["files"], ["Resources/a.txt", SOLDIER])
                if newline == "\r\n":
                    self.assertEqual(raw.count("\r\n"), raw.count("\n"))
                else:
                    self.assertNotIn("\r", raw)
                self.assertFalse(doc.read_only)
                # Saving again does not list it twice.
                doc.save_into_mod(mod)
                self.assertEqual(json.loads((mod / "mod.json").read_text())["files"].count(SOLDIER), 1)


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_DAT and HAVE_QT, "needs data.dat.vanilla.bak and PySide6")
class WindowTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import canedit
        cls.canedit = canedit
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _window(self):
        w = self.canedit.CanWindow(mods_root=self.tmp / "nomods", extra_mods=[])
        return w

    def _field(self, window, key):
        return next(w for f, w in window.panel.controls if f.key == key)

    def test_panel_edit_is_one_line_and_undoes(self):
        w = self._window()
        self.assertTrue(w.open_vanilla("Monster Cans/English Enemies/Soldier1"))
        before = w.doc.to_bytes()
        box = self._field(w, "Max Distance")
        box.setCurrentText("650")
        box.lineEdit().editingFinished.emit()
        self.assertTrue(w.doc.dirty)
        self.assertEqual(len(_diff(before, w.doc.to_bytes())), 2)
        w.undo_stack.undo()
        self.assertEqual(w.doc.to_bytes(), before)

    def test_non_number_is_rejected(self):
        w = self._window()
        w.open_vanilla("Monster Cans/English Enemies/Soldier1")
        before = w.doc.to_bytes()
        box = self._field(w, "Max Distance")
        box.setCurrentText("far")
        box.lineEdit().editingFinished.emit()
        self.assertEqual(w.doc.to_bytes(), before)

    def test_preset_and_class_switch(self):
        w = self._window()
        w.open_vanilla("Monster Cans/Mongol Goblin Archer")
        skel = w.panel.skeleton
        n = len(core.transitions(skel).items())
        w.panel._add_call_for_help()
        self.assertEqual(len(core.transitions(skel).items()), n + 1)
        self.assertEqual(w.panel.trans_list.count(), n + 1)
        index = w.panel._class_values.index("CStandStillAttackAI")
        w.panel.class_box.setCurrentIndex(index)
        w.panel._on_class_chosen(index)
        self.assertEqual(core.attack_ai_class(skel), "CStandStillAttackAI")
        self.assertFalse(self._field(w, "Minimum Attack Distance").isEnabled())
        # Abstract classes are listed but cannot be picked.
        model = w.panel.class_box.model()
        for name in ("CAttackAI", "CChargeAttackAI", "CStrafeAttackAI"):
            row = w.panel._class_values.index(name)
            self.assertFalse(model.item(row).isEnabled())
        w.undo_stack.undo()
        w.undo_stack.undo()
        self.assertEqual(w.doc.to_bytes(), core.CanDocument.from_vanilla(ARCHER, DAT).to_bytes())

    def test_can_tree_is_hosted(self):
        w = self._window()
        w.open_vanilla(UNDEAD)
        self.assertEqual(w.script.tree.topLevelItemCount(), 1)
        self.assertGreater(w.script.tree.topLevelItem(0).childCount(), 0)


if __name__ == "__main__":
    unittest.main()
