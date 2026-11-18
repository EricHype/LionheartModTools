"""Drive the enemy editor window offscreen.

    python -m unittest tests.test_enemyedit_window -v

The 3D preview needs the real display (WebGL; see docs/enemy-editor-e0.md), so these tests
cover the cards, undo, the shared-stats prompt, the model check and saving, not the picture.
Needs PySide6 and the game's data.dat.vanilla.bak. Writes only to temporary folders, and
keeps the model cache in one too.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from canedit_core import default_vanilla_dat  # noqa: E402

DAT = default_vanilla_dat()
try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QPushButton
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

WOLF = "Resources/Monster Cans/Animals/Wolf Gray.can"
HP = "Derived Character Attributes/(HP) Hit Points"


@unittest.skipUnless(HAVE_QT and DAT.is_file(), "needs PySide6 and the vanilla archive")
class EnemyWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.tmpdir = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls.tmpdir.name)
        os.environ["LOCALAPPDATA"] = cls.tmpdir.name       # model cache stays in the temp dir
        import enemyedit
        cls.ee = enemyedit
        cls.answer = QMessageBox.Yes
        cls._q, cls._w, cls._i = QMessageBox.question, QMessageBox.warning, QMessageBox.information
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        cls.warnings = []
        QMessageBox.question = staticmethod(
            lambda *a, **k: QMessageBox.Save if "puts your version" in str(a[2]) else cls.answer)
        QMessageBox.warning = staticmethod(lambda *a, **k: cls.warnings.append(a[2])
                                           or QMessageBox.Save)

    @classmethod
    def tearDownClass(cls):
        QMessageBox.question, QMessageBox.warning, QMessageBox.information = cls._q, cls._w, cls._i
        cls.tmpdir.cleanup()

    def setUp(self):
        import itemedit_core as icore
        self.icore = icore
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = icore.create_mod(Path(self.tmp.name), "Enemy Test")
        self.win = self.ee.EnemyWindow(DAT, self.mod)
        self.warnings.clear()
        type(self).answer = QMessageBox.Yes

    def tearDown(self):
        self.win.creature = None
        self.win.close()
        self.tmp.cleanup()

    def edits(self):
        return self.win.cards_area.widget().findChildren(QLineEdit)

    def test_library_groups(self):
        top = [self.win.tree.topLevelItem(i).text(0)
               for i in range(self.win.tree.topLevelItemCount())]
        self.assertIn("Animals", top)
        self.assertIn("Undead", top)
        names = [self.win.tree.topLevelItem(top.index("Animals")).child(i).text(0)
                 for i in range(self.win.tree.topLevelItem(top.index("Animals")).childCount())]
        self.assertIn("Grey Wolf", names)
        self.assertIn("Grey Wolf (Tough)", names)

    def test_hp_edit_offers_own_stats_and_saves(self):
        w = self.win
        w.open_rel(WOLF)
        hp = next(e for e in self.edits() if e.text() == "18")
        hp.setText("30")
        hp.editingFinished.emit()           # shared with the Horse: "Yes" gives it its own
        c = w.creature
        self.assertEqual(c.race.rel, "Resources/Races/Enemies/Animals/Wolf Gray 2.Race")
        self.assertEqual(c.preset(HP).value, "30")
        w.undo.undo()                       # the HP change
        self.assertEqual(w.creature.preset(HP).value, "18")
        w.undo.undo()                       # the fork
        self.assertEqual(w.creature.race.rel, "Resources/Races/Enemies/Animals/Wolf Gray.Race")
        w.undo.redo()
        w.undo.redo()
        self.assertTrue(w.save())
        files = json.loads((self.mod / "mod.json").read_text())["files"]
        self.assertIn("Resources/Races/Enemies/Animals/Wolf Gray 2.Race", files)
        self.assertIn(WOLF, files)
        self.assertEqual(w.creature.preset(HP).value, "30")
        self.assertEqual(w.creature.can.origin, "mod")

    def test_editing_a_shared_race_when_told_to(self):
        type(self).answer = QMessageBox.No        # "No changes them all"
        w = self.win
        w.open_rel(WOLF)
        hp = next(e for e in self.edits() if e.text() == "18")
        hp.setText("25")
        hp.editingFinished.emit()
        self.assertEqual(w.creature.race.rel, "Resources/Races/Enemies/Animals/Wolf Gray.Race")
        self.assertEqual(w.creature.preset(HP).value, "25")

    def test_behaviour_panel_edits_undo_with_the_rest(self):
        w = self.win
        w.open_rel(WOLF)
        panel = w.combat.panel
        f, widget = next((f, wd) for f, wd in panel.controls if f.key == "Pursue max dist from home"
                         or "Pursue" in f.label)
        old = widget.currentText() if hasattr(widget, "currentText") else widget.text()
        if hasattr(widget, "setCurrentText"):
            widget.setCurrentText("2000")
            widget.lineEdit().editingFinished.emit()
        else:
            widget.setText("2000")
            widget.editingFinished.emit()
        self.assertIn("2000", w.creature.can.to_bytes().decode("latin-1"))
        self.assertTrue(w.creature.dirty)
        w.undo.undo()
        self.assertFalse(w.creature.dirty, "undo puts the can back exactly")
        self.assertFalse(any(b.isVisible() and b.text().startswith("Show in Can tree")
                             for b in panel.findChildren(QPushButton)))

    def test_drop_and_strike_through_the_window(self):
        w = self.win
        w.open_rel(WOLF)
        gold = "Inventory/Enemy Drop Items Cans/Gold/Chance 1 in 2 SmallMedium Gold Pile Drop Action"
        w.edit("Add drop", lambda: w.creature.add_drop(gold), rebuild=True)
        w.edit("Add strike", lambda: self.icore.add_on_hit(
            w.creature.weapons()[0], "20", "Cold", "1", "3"), rebuild=True)
        self.assertTrue(w.save())
        text = (self.mod / "files" / WOLF).read_text("latin-1")
        self.assertIn("Canned Object=" + gold, text)
        self.assertIn("CPlugInBehaviorStrikeAction", text)
        self.assertIn("Engine checks passed", w.statusBar().currentMessage())

    def test_where_it_appears_and_spawn(self):
        w = self.win
        w.open_rel(WOLF)
        w.wait_for_spawns()
        self.assertIn("Spawns in 14 maps", w.reach_label.text())
        self.assertIn("Wolf Gray Tough", w.reach_label.text())
        self.assertFalse(w.test_label_act.isVisible())
        w.spawn_in_game()
        import itemedit_test as tm
        self.assertEqual([i.creature for i in tm.load(self.mod)], ["Monster Cans/Animals/Wolf Gray"])
        self.assertIn("1 thing", w.test_label.text())

    def test_model_check_and_new_enemy(self):
        w = self.win
        w.open_rel(WOLF)
        self.assertFalse(w.edit("bad", lambda: w.creature.set_model(
            "Characters/Monsters/Animals/Horse")))
        self.assertTrue(any("no animation for: Attack" in m for m in self.warnings))
        self.assertTrue(w.edit("ok", lambda: w.creature.set_model(
            "Characters/Monsters/Animals/Bear"), rebuild=True))
        new = w.creature.clone("Wolf Shadow", "Shadow Wolf")
        w.set_creature(new)
        self.assertEqual(w.head_name.text(), "Shadow Wolf")
        self.assertTrue(w.save())
        saved = self.mod / "files/Resources/Monster Cans/Animals/Wolf Shadow.can"
        text = saved.read_text("latin-1")
        self.assertIn("Race=Races/Enemies/Animals/Wolf Shadow", text)
        self.assertIn("Model=Characters/Monsters/Animals/Bear", text)
        self.assertIn("Engine checks passed", w.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
