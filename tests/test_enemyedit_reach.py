"""Tests for where creatures appear, and summoning them from the test menu.

    python -m unittest tests.test_enemyedit_reach -v

Needs the game's data.dat.vanilla.bak; skips without it. Writes only to temporary mods.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dialogtree_format as dt  # noqa: E402
import enemyedit_reach as r  # noqa: E402
import itemedit_core as ic  # noqa: E402
import itemedit_test as tm  # noqa: E402
from canedit_core import default_vanilla_dat  # noqa: E402
from icontool import Library  # noqa: E402

DAT = default_vanilla_dat()


@unittest.skipUnless(DAT.is_file(), "needs the vanilla game archive")
class SpawnIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = Library(DAT)
        cls.idx = r.SpawnIndex(cls.lib)

    def test_counts(self):
        total = sum(len(a.spawns) for a in self.idx.by_can.values())
        self.assertGreater(total, 14000)          # 14,580 when measured
        self.assertGreater(len(self.idx.by_can), 400)

    def test_tiers_follow_party_strength(self):
        mojo = lambda ref: {float(s.mojo) for s in self.idx.where(ref).game_spawns if s.mojo}
        base = mojo("Monster Cans/Animals/Wasp")
        tough = mojo("Monster Cans/Animals/Wasp Tough")
        sup = mojo("Monster Cans/Animals/Wasp Super")
        self.assertLess(max(base), min(tough))
        self.assertLess(max(tough), min(sup))
        self.assertIn("Crossroads", self.idx.where("Monster Cans/Animals/Wasp").maps)

    def test_unused_and_summary(self):
        horse = self.idx.where("Monster Cans/Animals/Horse")
        self.assertEqual(horse.maps, [])
        self.assertIn("Nothing in the game spawns", r.summary(horse)[-1])
        wolf = r.summary(self.idx.where("Monster Cans/Animals/Wolf Gray"))
        self.assertTrue(wolf[0].startswith("Spawns in 14 maps"))
        self.assertIn("most often", wolf[1])     # a long list is compressed

    def test_test_maps_kept_apart(self):
        a = self.idx.where("Monster Cans/Animals/Wasp")
        self.assertTrue(a.test_maps)
        self.assertFalse(set(a.test_maps) & set(a.maps))


@unittest.skipUnless(DAT.is_file(), "needs the vanilla game archive")
class SummonTests(unittest.TestCase):
    def test_summon_reply_and_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = ic.create_mod(Path(tmp), "Summon")
            manifest = (mod / "mod.json").read_bytes()
            lib = Library(DAT, mod)
            tm.add(lib, mod, ic.ItemDocument.from_library(lib, "Resources/Inventory Items/Gem.InventoryItem"))
            tm.add_creature(lib, mod, "Resources/Monster Cans/Animals/Wolf Gray.can", "Grey Wolf")
            tm.add_creature(lib, mod, "Resources/Monster Cans/Animals/Wolf Gray.can", "Grey Wolf")
            tree = dt.parse((mod / "files" / tm.HOST_TREE).read_bytes().decode("latin-1"))
            menu = tree.node_by_id(tm.MENU_ID)
            self.assertEqual([x.text for x in menu.replies], ["Gem", "Summon: Grey Wolf", "Goodbye."])
            text = (mod / "files" / tm.HOST_TREE).read_bytes().decode("latin-1")
            self.assertIn("Custom Action=CCreateEntityFromCanAction", text)
            self.assertIn("Canned Entity to Create=Monster Cans/Animals/Wolf Gray\r\n", text)
            self.assertEqual([i.creature for i in tm.load(mod)], ["", "Monster Cans/Animals/Wolf Gray"])
            with self.assertRaises(ic.ItemError):
                tm.add_creature(lib, mod, "Resources/Monster Cans/Animals/Not There.can", "x")
            tm.remove(mod)
            self.assertEqual((mod / "mod.json").read_bytes(), manifest)


if __name__ == "__main__":
    unittest.main()
