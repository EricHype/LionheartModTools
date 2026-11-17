"""Tests for the item editor's first-run setup and install button.

    python -m unittest tests.test_itemedit_setup -v

Everything runs against a tiny fake game folder in a temporary directory: a few-KB
data.dat stands in for the real 1.6 GB one, so the real install is never touched.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import itemedit_core as c  # noqa: E402
import itemedit_setup as s  # noqa: E402


def fake_game(folder: Path) -> Path:
    game = folder / "Lionheart"
    game.mkdir()
    with zipfile.ZipFile(game / "data.dat", "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("Resources/Inventory Items/Gem.InventoryItem",
                    "CInventoryItem\r\n{\r\n\tDisplay Name=Gem\r\n}\r\n")
        zf.writestr("Resources/readme.txt", "x")
    return game


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = fake_game(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_not_a_game(self):
        st = s.check_game(Path(self.tmp.name))
        self.assertFalse(st.ok or st.can_backup)
        self.assertIn("no data.dat", st.message)

    def test_first_run_backup(self):
        st = s.check_game(self.game)
        self.assertEqual((st.ok, st.can_backup), (False, True))
        dst = s.make_backup(self.game)
        self.assertEqual(dst.read_bytes(), (self.game / "data.dat").read_bytes())
        self.assertFalse(dst.with_name(dst.name + ".partial").exists())
        self.assertTrue(s.check_game(self.game).ok)
        with self.assertRaises(RuntimeError):           # never twice
            s.make_backup(self.game)

    def test_refuses_a_modded_game(self):
        for marker in (self.game / "mods" / "installed" / "fixt" / "mod.json",
                       self.game / "mods" / "bloodletter" / "install-record.json"):
            marker.parent.mkdir(parents=True)
            marker.write_text("{}")
            st = s.check_game(self.game)
            self.assertEqual((st.ok, st.can_backup), (False, False), marker)
            self.assertIn("Verify / Repair", st.message)
            with self.assertRaises(RuntimeError):
                s.make_backup(self.game)
            marker.unlink()
            marker.parent.rmdir()

    def test_install_into_a_fake_game(self):
        s.make_backup(self.game)
        mod = c.create_mod(Path(self.tmp.name), "My Mod")
        rel = "Resources/Inventory Items/Gem.InventoryItem"
        (mod / "files" / rel).parent.mkdir(parents=True)
        (mod / "files" / rel).write_bytes(
            b"CInventoryItem\r\n{\r\n\tDisplay Name=Moonstone\r\n}\r\n")
        c.register_in_mod_json(mod, rel)
        ok, text = s.install_mod(mod, self.game)
        self.assertTrue(ok, text)
        with zipfile.ZipFile(self.game / "data.dat") as zf:
            self.assertIn(b"Moonstone", zf.read(rel))
        self.assertEqual(json.loads((self.game / "mods" / "enabled.json").read_text()),
                         ["my-mod"])
        # installing again replaces the copy rather than failing
        (mod / "files" / rel).write_bytes(
            b"CInventoryItem\r\n{\r\n\tDisplay Name=Sunstone\r\n}\r\n")
        ok, text = s.install_mod(mod, self.game)
        self.assertTrue(ok, text)
        with zipfile.ZipFile(self.game / "data.dat") as zf:
            self.assertIn(b"Sunstone", zf.read(rel))

    def test_install_failure_is_reported(self):
        ok, text = s.install_mod(Path(self.tmp.name) / "no such mod", self.game)
        self.assertFalse(ok)
        self.assertTrue(text)


if __name__ == "__main__":
    unittest.main()
