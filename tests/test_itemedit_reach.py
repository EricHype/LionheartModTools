"""Tests for "where it appears".

    python -m unittest tests.test_itemedit_reach -v

Needs the game's `data.dat.vanilla.bak`; skips without it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import itemedit_core as c  # noqa: E402
import itemedit_reach as r  # noqa: E402
from canedit_core import default_vanilla_dat  # noqa: E402
from icontool import Library  # noqa: E402

DAT = default_vanilla_dat()


@unittest.skipUnless(DAT.is_file(), "needs the vanilla game archive")
class ReachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = Library(DAT)
        cls.idx = r.ReachIndex(cls.lib)

    def where(self, rel):
        d = c.ItemDocument.from_library(self.lib, rel)
        return d, self.idx.where(d)

    def test_short_sword(self):
        d, w = self.where("Resources/Inventory Items/ShortSword.InventoryItem")
        self.assertIn("Gate District", w.maps)
        self.assertIn("Drunk Guard Introduction", w.conversations)
        self.assertTrue(w.gets_magic)
        # developer test maps are kept apart from the game's maps
        self.assertIn("Erik Test map", w.test_maps)
        self.assertNotIn("Erik Test map", w.maps)
        self.assertIn("Weapons One Handed", [t for t, n in w.loot if n])
        # tables no map or creature names, followed through tables that name tables
        self.assertIn("Weapon Short Sword", [t for t, n in w.loot if not n])

    def test_magic_rolls_on_its_group(self):
        _d, w = self.where("Resources/Inventory/Inventory Additions/Miscellaneous/Helmets/"
                           "Poison Resistance.InventoryAddition")
        self.assertEqual(w.rolls_on, ["Helmet"])
        self.assertEqual(w.rarity, "2 Uncommon")
        _d, w = self.where("Resources/Inventory/Inventory Additions/Weapons/"
                           "Cold Bite.InventoryAddition")
        self.assertIn("Short Sword", w.rolls_on)
        self.assertNotIn("Helmet", w.rolls_on)

    def test_unique_and_summary(self):
        d, w = self.where("Resources/Inventory/Specific Item Cans/Unique Magic Items/"
                          "Gauntlets of La Mancha.InventoryItem")
        self.assertEqual(w.maps, ["Barcelona Coast"])
        self.assertFalse(w.gets_magic)
        lines = r.summary(w, d)
        self.assertTrue(lines[0].startswith("Found in 1 map"))

    def test_a_new_item_is_nowhere(self):
        lib = self.lib
        new = c.clone(c.ItemDocument.from_library(lib, "Resources/Inventory Items/Gem.InventoryItem"),
                      lib, "Moonstone", "Moonstone")
        w = self.idx.where(new)
        self.assertFalse(w.anywhere)
        self.assertIn("Nothing in the game gives this", r.summary(w, new)[-1])

    def test_test_map_rule(self):
        self.assertTrue(r.is_test_map("Levels/Test Maps/Ion/QA Magic Items.zax"))
        self.assertTrue(r.is_test_map("Levels/Erik Test map.zax"))
        self.assertFalse(r.is_test_map("Levels/1 Barcelona/Gate District.zax"))
        self.assertFalse(r.is_test_map("Levels/Wilderness Maps/Crossroads.zax"))


if __name__ == "__main__":
    unittest.main()
