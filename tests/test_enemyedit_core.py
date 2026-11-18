"""Tests for the enemy editor's headless core.

    python -m unittest tests.test_enemyedit_core -v

Needs the game's `data.dat.vanilla.bak`; skips without it. Writes only to temporary mods.
"""
from __future__ import annotations

import difflib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import enemyedit_core as e  # noqa: E402
import itemedit_core as ic  # noqa: E402
from canedit_core import default_vanilla_dat  # noqa: E402
from icontool import Library  # noqa: E402

DAT = default_vanilla_dat()
WOLF = "Resources/Monster Cans/Animals/Wolf Gray.can"
HORSE_MODEL = "Characters/Monsters/Animals/Horse"
BEAR_MODEL = "Characters/Monsters/Animals/Bear"
HP = "Derived Character Attributes/(HP) Hit Points"
POISON = "Derived Character Attributes/Damage Related/Poison Damage Resistance"


def changed(a: bytes, b: bytes) -> list[str]:
    return [l.replace("\t", "") for l in difflib.unified_diff(
        a.decode("latin-1").split("\r\n"), b.decode("latin-1").split("\r\n"),
        lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("---", "+++")]


@unittest.skipUnless(DAT.is_file(), "needs the vanilla game archive")
class EnemyCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = Library(DAT)

    def wolf(self):
        return e.Creature.from_library(self.lib, WOLF)

    def test_every_creature_round_trips(self):
        problems = {}
        for n in e.creature_cans(self.lib):
            c = e.Creature.from_library(self.lib, n)
            self.assertEqual(c.can.to_bytes(), self.lib.read(n), n)
            if c.race is not None:
                self.assertEqual(c.race.to_bytes(), self.lib.read(c.race.rel), c.race.rel)
            self.assertFalse(c.dirty, n)
            for w in c.weapons():
                ic.damage_lines(w), ic.on_hits(w)
            if c.problems():
                problems[n.rsplit("/", 1)[-1]] = c.problems()
        # the known cases only: the Horse's missing Attack, and monsters on a player race
        self.assertEqual(problems.pop("Horse.can"), ["Its model has no animation for: Attack."])
        for name, ps in problems.items():
            self.assertTrue(all("sets no" in p for p in ps), (name, ps))
        self.assertLessEqual(len(problems), 6)

    def test_wolf_reads(self):
        w = self.wolf()
        self.assertEqual((w.name, w.level, w.experience), ("Grey Wolf", "1", "39"))
        self.assertEqual(w.categories, ["Animal", "Enemy"])
        self.assertEqual(w.model, "Characters/Monsters/Wolf Gray")
        self.assertEqual(w.preset(HP).value, "18")
        self.assertEqual(len(w.attributes()), 7)
        self.assertIn(("When hurt", "Enemies/Animals/Wolf Pain.ogg"),
                      [(s.when, s.file) for s in w.sounds()])
        (weapon,) = w.weapons()
        self.assertEqual(ic.damage_range(ic.damage_lines(weapon)[0]), ("2", "5"))
        self.assertEqual(weapon.reach.get("Max Attack Range"), "35")

    def test_model_info_and_swap_check(self):
        info = e.model_info(self.lib, "Characters/Monsters/Wolf Gray")
        self.assertEqual(info.mesh, "Models3D/Enemies/Wolves/Gray Wolf/GrayWolf")
        self.assertIn("Attack01", info.clip_names)
        self.assertEqual(info.scaling, "0.28")
        self.assertTrue(info.finds("Attack"))           # prefix: Attack -> Attack01
        w = self.wolf()
        self.assertEqual(w.missing_sequences(HORSE_MODEL), ["Attack"])
        with self.assertRaises(ic.ItemError):
            w.set_model(HORSE_MODEL)
        before = w.can.to_bytes()
        w.set_model(BEAR_MODEL)
        self.assertEqual(changed(before, w.can.to_bytes()),
                         ["-Model=Characters/Monsters/Wolf Gray", f"+Model={BEAR_MODEL}"])
        with self.assertRaises(ic.ItemError):
            w.set_model("Characters/Monsters/No Such Thing")

    def test_stat_edits_are_one_line(self):
        w = self.wolf()
        before = w.race.to_bytes()
        w.set_preset(HP, "30")
        self.assertEqual(changed(before, w.race.to_bytes()),
                         ["-Value To Preset=18", "+Value To Preset=30"])
        self.assertFalse(w.can.dirty)
        w.set_preset(POISON, "25")                    # a new preset is added, count kept
        arr = ic.child(w.race.root, "DerivedCharacterAttribute")
        self.assertEqual(arr.get("Item Count"), str(len(arr.items())))
        self.assertEqual(w.preset(POISON).value, "25")
        w.remove_preset(POISON)
        self.assertIsNone(w.preset(POISON))
        self.assertEqual(arr.get("Item Count"), "5")
        with self.assertRaises(ic.ItemError):
            w.set_preset(HP, "lots")

    def test_attribute_range_widens(self):
        w = self.wolf()
        w.set_attribute("Character Attributes/(ST) Strength", "12")
        arr = ic.child(w.race.root, "Initial CharacterAttribute Values")
        self.assertEqual(arr.get("Character Attributes/(ST) Strength"), "12,1,12")
        self.assertEqual(dict(w.attributes())["Character Attributes/(ST) Strength"], "12")

    def test_shared_race_is_forked(self):
        w = self.wolf()
        self.assertEqual(sorted(x.rsplit("/", 1)[-1] for x in w.race_users()),
                         ["Horse.can", "Wolf Gray.can"])
        old = w.race.rel
        w.fork_race()
        self.assertEqual(w.race.rel, old.replace("Wolf Gray.Race", "Wolf Gray 2.Race"))
        self.assertEqual(w.profile.get("Race"), "Races/Enemies/Animals/Wolf Gray 2")
        self.assertEqual(w.race.to_bytes(), self.lib.read(old))   # same stats, new file

    def test_clone_and_save(self):
        w = self.wolf()
        new = w.clone("Wolf Shadow", "Shadow Wolf")
        self.assertEqual(new.can.rel, "Resources/Monster Cans/Animals/Wolf Shadow.can")
        self.assertEqual(new.race.rel, "Resources/Races/Enemies/Animals/Wolf Shadow.Race")
        self.assertEqual(new.profile.get("Race"), "Races/Enemies/Animals/Wolf Shadow")
        self.assertEqual(new.name, "Shadow Wolf")
        new.set_preset(HP, "40")
        self.assertEqual(w.preset(HP).value, "18")       # the original is untouched
        self.assertFalse(w.dirty)
        self.assertEqual(new.problems(), [])
        with self.assertRaises(ic.ItemError):
            w.clone("wolf black", "x")                  # taken, case-insensitively
        with tempfile.TemporaryDirectory() as tmp:
            mod = ic.create_mod(Path(tmp), "Wolves")
            written = new.save_into_mod(mod)
            self.assertEqual(len(written), 2)
            files = json.loads((mod / "mod.json").read_text())["files"]
            self.assertIn(new.can.rel, files)
            self.assertIn(new.race.rel, files)
            again = e.Creature.from_library(Library(DAT, mod), new.can.rel)
            self.assertEqual((again.name, again.preset(HP).value), ("Shadow Wolf", "40"))
            self.assertEqual(again.race.origin, "mod")

    def test_weapon_damage_through_item_cards(self):
        w = self.wolf()
        before = w.can.to_bytes()
        line = ic.damage_lines(w.weapons()[0])[0]
        ic.set_damage_range(line, "3", "5")
        self.assertEqual(changed(before, w.can.to_bytes()),
                         ["-Constant Value=2", "+Constant Value=3"])

    def test_sounds(self):
        w = self.wolf()
        s = next(s for s in w.sounds() if s.when == "When hurt")
        w.set_sound(s, "Enemies/Animals/Bear Pain.ogg")
        self.assertEqual(s.file, "Enemies/Animals/Bear Pain.ogg")
        with self.assertRaises(ic.ItemError):
            w.set_sound(s, " ")


if __name__ == "__main__":
    unittest.main()
