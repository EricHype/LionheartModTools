"""Tests for the item editor's in-game test menu.

    python -m unittest tests.test_itemedit_testmenu -v

Needs the game's `data.dat.vanilla.bak`; skips without it. Writes only to temporary mods.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dialogtree_format as dt  # noqa: E402
import itemedit_core as c  # noqa: E402
import itemedit_test as t  # noqa: E402
from canedit_core import default_vanilla_dat  # noqa: E402
from icontool import Library  # noqa: E402

DAT = default_vanilla_dat()
SWORD = "Resources/Inventory Items/ShortSword.InventoryItem"
HELMET = "Resources/Inventory Items/Helmet.InventoryItem"
POISON = ("Resources/Inventory/Inventory Additions/Miscellaneous/Helmets/"
          "Poison Resistance.InventoryAddition")
WAND = "Resources/Inventory/Inventory Additions/Miscellaneous/Wands/Slow.InventoryAddition"
WAND_BASE = "Resources/Inventory Items/Wand.InventoryItem"


@unittest.skipUnless(DAT.is_file(), "needs the vanilla game archive")
class TestMenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = c.create_mod(Path(self.tmp.name), "Menu Mod")
        self.lib = Library(DAT, self.mod)
        self.manifest = (self.mod / "mod.json").read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def doc(self, rel):
        return c.ItemDocument.from_library(self.lib, rel)

    def tree(self):
        return dt.parse((self.mod / "files" / t.HOST_TREE).read_bytes().decode("latin-1"))

    def test_entry_nodes_come_from_the_map(self):
        nodes = t.entry_nodes(self.lib)
        # every greeting the map can open: by gender, by race (tainted) and on return
        self.assertIn("1 Conversation Start", nodes)
        self.assertIn("3 Welcome if female", nodes)
        self.assertIn("1 Conversation Start Tainted", nodes)
        self.assertEqual(len(nodes), 8)

    def test_add_hooks_every_entry_node_once(self):
        t.add(self.lib, self.mod, self.doc(SWORD))
        t.add(self.lib, self.mod, self.doc(POISON), self.doc(HELMET))
        t.add(self.lib, self.mod, self.doc(SWORD))          # again: replaced, not doubled
        tree = self.tree()
        for nid in t.entry_nodes(self.lib):
            texts = [r.text for r in tree.node_by_id(nid).replies]
            self.assertEqual(texts.count(t.HOOK_TEXT), 1, nid)
            self.assertEqual(texts[0], t.HOOK_TEXT, nid)
        menu = tree.node_by_id(t.MENU_ID)
        self.assertEqual([r.text for r in menu.replies],
                         ["Helmet of Poison Resistance", "Short Sword", "Goodbye."])
        # nothing new dangles (vanilla's own tree already has one dead reply)
        vanilla = dt.parse(self.lib.read(t.HOST_TREE).decode("latin-1"))
        self.assertEqual(tree.dangling_targets(), vanilla.dangling_targets())
        text = (self.mod / "files" / t.HOST_TREE).read_bytes().decode("latin-1")
        self.assertIn("Addition to add=Inventory/Inventory Additions/Miscellaneous/Helmets/"
                      "Poison Resistance\r\n", text)
        self.assertEqual(text.count("\r\n"), text.count("\n"))          # CRLF throughout

    def test_remove_restores_the_mod(self):
        t.add(self.lib, self.mod, self.doc(SWORD))
        t.remove(self.mod)
        self.assertEqual((self.mod / "mod.json").read_bytes(), self.manifest)
        self.assertEqual(list((self.mod / "files").rglob("*")), [])
        self.assertFalse(t.is_installed(self.mod))

    def test_remove_item(self):
        t.add(self.lib, self.mod, self.doc(SWORD))
        t.add(self.lib, self.mod, self.doc(POISON), self.doc(HELMET))
        t.remove_item(self.lib, self.mod, 0)
        self.assertEqual([i.label for i in t.load(self.mod)], ["Helmet of Poison Resistance"])
        t.remove_item(self.lib, self.mod, 0)
        self.assertFalse(t.is_installed(self.mod))

    def test_refusals(self):
        with self.assertRaises(c.ItemError):          # wands get no charges this way
            t.add(self.lib, self.mod, self.doc(WAND), self.doc(WAND_BASE))
        with self.assertRaises(c.ItemError):          # magic needs a base item
            t.add(self.lib, self.mod, self.doc(POISON))
        d = self.doc(SWORD)
        c.set_price(d, "99")
        with self.assertRaises(c.ItemError):          # unsaved changes
            t.add(self.lib, self.mod, d)
        own = self.mod / "files" / t.HOST_TREE
        own.parent.mkdir(parents=True)
        own.write_bytes(self.lib.read(t.HOST_TREE))
        with self.assertRaises(c.ItemError):          # the mod edits Raymundo itself
            t.add(self.lib, self.mod, self.doc(SWORD))

    def test_unregister_keeps_a_hand_formatted_manifest(self):
        m = self.mod / "mod.json"
        m.write_bytes(b'{\n    "id": "x",\n    "files": [\n        "a.txt",\n        "b.txt"\n    ]\n}\n')
        self.assertTrue(t.unregister_in_mod_json(self.mod, "b.txt"))
        self.assertEqual(m.read_bytes(),
                         b'{\n    "id": "x",\n    "files": [\n        "a.txt"\n    ]\n}\n')


if __name__ == "__main__":
    unittest.main()
