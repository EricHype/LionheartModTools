"""Drive the item editor window offscreen, the way a person would use it.

    python -m unittest tests.test_itemedit_window -v

Needs PySide6 and the game's `data.dat.vanilla.bak`; skips without them. Writes only to
a temporary mod folder.
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
    from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QMessageBox
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

SHORT_SWORD = "Resources/Inventory Items/ShortSword.InventoryItem"
POISON = ("Resources/Inventory/Inventory Additions/Miscellaneous/Helmets/"
          "Poison Resistance.InventoryAddition")


@unittest.skipUnless(HAVE_QT and DAT.is_file(), "needs PySide6 and the vanilla archive")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import itemedit
        cls.ie = itemedit
        cls.app = QApplication.instance() or QApplication([])
        QSettings.setDefaultFormat(QSettings.IniFormat)
        cls.settings_dir = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls.settings_dir.name)
        # answer every confirmation with yes/save
        cls._q = QMessageBox.question
        cls._w = QMessageBox.warning
        cls._i = QMessageBox.information
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Save
                                            if "Save" in str(a[2]) or "puts" in str(a[2])
                                            else QMessageBox.Yes)
        cls.warnings = []
        QMessageBox.warning = staticmethod(lambda *a, **k: cls.warnings.append(a[2])
                                           or QMessageBox.Save)
        QMessageBox.information = staticmethod(lambda *a, **k: None)

    @classmethod
    def tearDownClass(cls):
        QMessageBox.question, QMessageBox.warning, QMessageBox.information = \
            cls._q, cls._w, cls._i
        cls.settings_dir.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import itemedit_core as core
        self.core = core
        self.mod = core.create_mod(Path(self.tmp.name), "My Test Mod")
        self.win = self.ie.ItemWindow(DAT, self.mod)
        self.warnings.clear()

    def tearDown(self):
        self.win.doc = None
        self.win.close()
        self.tmp.cleanup()

    def edits(self):
        return self.win.cards_area.widget().findChildren(QLineEdit)

    def test_library_lists_items_with_icons(self):
        top = [self.win.tree.topLevelItem(i).text(0)
               for i in range(self.win.tree.topLevelItemCount())]
        self.assertIn("Items", top)
        self.assertIn("Unique items", top)
        self.assertNotIn("Creature weapons", top)
        self.win.show_all.setChecked(True)
        top = [self.win.tree.topLevelItem(i).text(0)
               for i in range(self.win.tree.topLevelItemCount())]
        self.assertIn("Creature weapons", top)

    def test_edit_sword_damage_save_and_undo(self):
        w = self.win
        w.open_rel(SHORT_SWORD)
        self.assertIn("Slashing Damage 1-5", w.preview.text())
        lo = next(e for e in self.edits() if e.text() == "1")
        lo.setText("3")
        lo.editingFinished.emit()
        self.assertIn("Slashing Damage 3-5", w.preview.text())
        self.assertTrue(w.doc.dirty)
        w.undo.undo()
        self.assertIn("Slashing Damage 1-5", w.preview.text())
        w.undo.redo()
        self.assertIn("Slashing Damage 3-5", w.preview.text())
        self.assertTrue(w.save())
        saved = self.mod / "files" / SHORT_SWORD
        self.assertTrue(saved.is_file())
        self.assertIn(SHORT_SWORD, json.loads((self.mod / "mod.json").read_text())["files"])
        self.assertEqual(w.doc.origin, "mod")
        self.assertFalse(w.doc.dirty)

    def test_bad_number_is_refused_and_left_alone(self):
        w = self.win
        w.open_rel(SHORT_SWORD)
        lo = next(e for e in self.edits() if e.text() == "1")
        lo.setText("lots")
        lo.editingFinished.emit()
        self.assertTrue(self.warnings)
        self.assertFalse(w.doc.dirty)

    def test_add_bonus_and_make_new_item(self):
        w = self.win
        w.open_rel(SHORT_SWORD)
        w.edit("Add Luck", lambda: self.core.add_stat(w.doc, "Character Attributes/(LK) Luck",
                                                      "2"), rebuild=True)
        self.assertEqual([s.label for s in self.core.stat_lines(w.doc)][-1], "Luck")
        self.assertTrue(w.save())
        new = self.core.clone(w.doc, w.lib, "Lucky Sword", "Lucky Sword")
        w.set_document(new)
        self.assertEqual(w.head_name.text(), "Lucky Sword")
        self.assertTrue(w.save())
        saved = self.mod / "files" / "Resources/Inventory Items/Lucky Sword.InventoryItem"
        self.assertIn("Origonal Item=Inventory Items/Lucky Sword", saved.read_text("latin-1"))

    def test_art_export_and_import_into_the_mod(self):
        w = self.win
        rel = ("Resources/Inventory/Specific Item Cans/Unique Magic Items/"
               "Gauntlets of La Mancha.InventoryItem")
        w.open_rel(rel)
        out = Path(self.tmp.name) / "png"
        code, text = w._run_icontool("export", w.doc.ref, "--out", str(out),
                                     "--mod", str(self.mod))
        self.assertEqual(code, 0, text)
        png = next(out.glob("* - inventory.png"))
        code, text = w._run_icontool("import", w.doc.ref, str(png), "--mod", str(self.mod),
                                     "--art", "inventory")
        self.assertEqual(code, 0, text)
        self.assertTrue((self.mod / "files" / rel).is_file())
        # the shared gauntlet picture was copied for this item alone
        self.assertTrue(any(p.suffix == ".mdl16" for p in (self.mod / "files").rglob("*")))

    def test_strike_effect_card(self):
        from PySide6.QtWidgets import QLabel, QPushButton
        w = self.win
        w.open_rel(SHORT_SWORD)
        texts = lambda cls: [x.text() for x in w.cards_area.widget().findChildren(cls)]
        self.assertIn("Add a strike effect...", texts(QPushButton))
        self.assertIn("Add extra damage...", texts(QPushButton))
        w.edit("Add bleed", lambda: self.core.add_on_hit(w.doc, "30", "Slashing", "2", "4",
                                                         duration="15"), rebuild=True)
        self.assertTrue(any("about 0.60 a second" in t for t in texts(QLabel)))
        add_line = next(b for b in w.cards_area.widget().findChildren(QPushButton)
                        if b.text() == "Add this to the description")
        add_line.click()
        self.assertIn("every 5 seconds for 15 seconds", self.core.get_field(w.doc, "Description"))
        self.assertIn("every 5 seconds for 15 seconds", w.preview.text())
        # the gem is not a weapon: no strike card
        w.open_rel("Resources/Inventory Items/Gem.InventoryItem", force=True)
        self.assertNotIn("Add a strike effect...", texts(QPushButton))

    def test_potion_use_card(self):
        from PySide6.QtWidgets import QGroupBox
        w = self.win
        w.open_rel("Resources/Inventory/Inventory Additions/Miscellaneous/Potions/"
                   "Strength.InventoryAddition")
        titles = [g.title() for g in w.cards_area.widget().findChildren(QGroupBox)]
        self.assertIn("When used", titles)
        self.assertNotIn("Bonuses while worn or held", titles)   # a potion is not worn
        lasts = next(e for e in self.edits() if e.text() == "120")
        lasts.setText("240")
        lasts.editingFinished.emit()
        e = self.core.use_effects(w.doc)[0]
        self.assertEqual((self.core.get_use_duration(e),
                          e.icon_duration_node.get("Constant Value")), ("240", "240"))
        self.assertTrue(w.save())

    def test_try_in_game_adds_to_the_test_menu(self):
        import itemedit_test as testmenu
        w = self.win
        w.open_rel(SHORT_SWORD)
        self.assertFalse(w.test_label_act.isVisible())
        w.try_in_game()
        self.assertTrue(testmenu.is_installed(self.mod))
        self.assertEqual([i.label for i in testmenu.load(self.mod)], ["Short Sword"])
        self.assertIn("1 item", w.test_label.text())
        testmenu.remove(self.mod)
        w.set_mod(self.mod)
        self.assertEqual(w.test_label.text(), "")

    def test_where_it_appears_and_engine_check(self):
        w = self.win
        w.open_rel(SHORT_SWORD)
        w.wait_for_reach()
        self.assertIn("Gate District", w.reach_label.text())
        lo = next(e for e in self.edits() if e.text() == "1")
        lo.setText("2")
        lo.editingFinished.emit()
        self.assertTrue(w.save())
        self.assertIn("Engine checks passed", w.statusBar().currentMessage())

    def test_install_button_uses_the_game_folder(self):
        import zipfile
        import itemedit_setup as setup
        game = Path(self.tmp.name) / "FakeGame"
        game.mkdir()
        with zipfile.ZipFile(game / "data.dat", "w") as zf:
            zf.writestr("Resources/x.txt", "x")
        setup.make_backup(game)
        w = self.ie.ItemWindow(DAT, self.mod, game)
        w.wait_for_reach()
        w.open_rel(SHORT_SWORD)
        lo = next(e for e in w.cards_area.widget().findChildren(QLineEdit) if e.text() == "1")
        lo.setText("2")
        lo.editingFinished.emit()
        self.assertTrue(w.save())
        w.install_into_game()
        w._install_thread.wait(120000)
        self.app.processEvents()
        self.assertTrue(w.isEnabled())
        self.assertEqual(w.statusBar().currentMessage(), "Installed. Start the game to try it.")
        with zipfile.ZipFile(game / "data.dat") as zf:
            self.assertIn(SHORT_SWORD, zf.namelist())
        w.doc = None
        w.close()

    def test_magic_preview_follows_the_roll(self):
        w = self.win
        w.open_rel(POISON)
        self.assertTrue(w.preview_opts_w.isVisibleTo(w))
        w.step_box.setCurrentText("Strongest")
        self.assertIn("Poison Resistance  15%", w.preview.text())
        w.step_box.setCurrentText("Weakest")
        self.assertIn("Poison Resistance  5%", w.preview.text())


if __name__ == "__main__":
    unittest.main()
