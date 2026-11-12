"""Tests for the icon export/import tool.

    python -m unittest tests.test_icontool -v

Needs the game's `data.dat.vanilla.bak`; the tests skip without it. Nothing here writes
outside a temporary folder.
"""
from __future__ import annotations

import contextlib
import difflib
import io
import json
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import icontool as it  # noqa: E402
import mdl16_format as m  # noqa: E402
import png_io  # noqa: E402
from canedit_core import default_vanilla_dat  # noqa: E402

DAT = default_vanilla_dat()
HAVE_DAT = DAT.is_file()

LA_MANCHA = "Gauntlets of La Mancha"
LA_MANCHA_REL = ("Resources/Inventory/Specific Item Cans/Unique Magic Items/"
                 "Gauntlets of La Mancha.InventoryItem")
GAUNTLETS = "Cache/Models/Items/Inventory Images/Armor/Gauntlets.mdl16"
GAUNTLET_PU = "Cache/Models/Items/PickUps/Misc Items/Gauntlet_PU.mdl16"


def _diff(a: bytes, b: bytes) -> list[str]:
    return [l for l in difflib.unified_diff(
        a.decode("latin-1").split("\r\n"), b.decode("latin-1").split("\r\n"),
        lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("---", "+++")]


def _run(*argv) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = it.main(["--vanilla", str(DAT), *argv])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            out.write(str(e.code))
    return code, out.getvalue()


class PngTests(unittest.TestCase):

    def test_rgba_round_trip(self):
        w, h = 5, 3
        px = bytes(v for y in range(h) for x in range(w)
                   for v in (x * 40, y * 80, 7, (x * 60) % 256))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.png"
            png_io.write_rgba(p, w, h, px)
            self.assertEqual(png_io.read_rgba(p), (w, h, bytearray(px)))

    def test_rgb_png_reads_as_opaque(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.png"
            png_io.write_rgb(p, 2, 1, bytes((1, 2, 3, 4, 5, 6)))
            self.assertEqual(png_io.read_rgba(p)[2], bytearray((1, 2, 3, 255, 4, 5, 6, 255)))

    def test_palette_png_with_transparency(self):
        def chunk(tag, data):
            c = tag + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        data = (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 3, 0, 0, 0))
                + chunk(b"PLTE", bytes((10, 20, 30, 40, 50, 60)))
                + chunk(b"tRNS", bytes((0,)))
                + chunk(b"IDAT", zlib.compress(b"\x00\x00\x01"))
                + chunk(b"IEND", b""))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.png"
            p.write_bytes(data)
            self.assertEqual(png_io.read_rgba(p)[2], bytearray((10, 20, 30, 0, 40, 50, 60, 255)))

    def test_sixteen_bit_png_is_refused_with_advice(self):
        def chunk(tag, data):
            c = tag + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        data = (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 16, 6, 0, 0, 0))
                + chunk(b"IEND", b""))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.png"
            p.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "8-bit RGBA"):
                png_io.read_rgba(p)


@unittest.skipUnless(HAVE_DAT, "needs data.dat.vanilla.bak")
class FormatTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.zf = zipfile.ZipFile(DAT)
        cls.sprites = sorted(n for n in cls.zf.namelist()
                             if n.startswith("Cache/Models/Items/") and n.endswith(".mdl16"))

    @classmethod
    def tearDownClass(cls):
        cls.zf.close()

    def test_old_recolor_callbacks_still_work(self):
        raw = self.zf.read(GAUNTLETS)
        self.assertEqual(m.recolor_icon_in_place(raw, lambda v: v), raw)
        self.assertEqual(m.recolor_icon_in_place(raw, lambda v, i: v), raw)

    def test_composite_keeps_buffer_one_and_adds_plane_five(self):
        raw = self.zf.read("Cache/Models/Items/Inventory Images/Quest Items/Titan Crystal.mdl16")
        one = m.decode_icon(raw)["rows"]
        comp = m.decode_icon_composite(raw)
        for y, row in enumerate(one):
            for x, px in enumerate(row):
                if px[3]:
                    self.assertEqual(comp["rows"][y][x], px)
        n5 = sum(p == 5 for row in comp["plane"] for p in row)
        self.assertGreater(n5, 10000)   # the glow lives in buffer 5

    def test_every_item_sprite_round_trips_byte_identical(self):
        """Export then import with no painting must give back the file unchanged, for
        every inventory image and ground sprite in the game."""
        bad = []
        for name in self.sprites:
            raw = self.zf.read(name)
            comp = m.decode_icon_composite(raw)
            w, h = comp["width"], comp["height"]
            W, H, big = it.scale_up(w, h, it.to_rgba_bytes(comp["rows"]), 4)
            rows = it.scale_down(W, H, big, 4)
            if rows != comp["rows"]:
                bad.append((name, "scale"))
                continue
            out, stats = it.repaint_in_place(raw, rows)
            if out != raw or stats["averaged"]:
                bad.append((name, "repaint"))
        print(f"\n  round-trip: {len(self.sprites) - len(bad)} of {len(self.sprites)} byte-identical")
        self.assertEqual(bad, [])

    def test_new_art_verifies_and_matches_the_painting(self):
        lib = it.Library(DAT)
        comp = m.decode_icon_composite(self.zf.read(GAUNTLETS))
        rows = [[(255, 0, 0, 255) if px[3] else (0, 0, 0, 0) for px in row] for row in comp["rows"]]
        rows[0][0] = (0, 255, 0, 255)       # outside the outline, so a new shape
        res = it.make_art(lib, self.zf.read(GAUNTLETS), rows, ground=False)
        self.assertEqual(res.method, "built as new art")
        m.verify_icon(res.data)
        back = m.decode_icon_composite(res.data)["rows"]
        self.assertEqual([[p[3] > 0 for p in r] for r in back], [[p[3] > 0 for p in r] for r in rows])
        self.assertEqual(back[0][0][:3], (0, 255, 0))

    def test_ground_art_refuses_a_new_size(self):
        lib = it.Library(DAT)
        raw = self.zf.read(GAUNTLET_PU)
        comp = m.decode_icon_composite(raw)
        rows = [row + [(0, 0, 0, 0)] for row in comp["rows"]]
        with self.assertRaises(SystemExit):
            it.make_art(lib, raw, rows, ground=True)

    def test_ground_paint_outside_the_outline_is_ignored(self):
        lib = it.Library(DAT)
        raw = self.zf.read(GAUNTLET_PU)
        comp = m.decode_icon_composite(raw)
        rows = [list(r) for r in comp["rows"]]
        y, x = next((y, x) for y, r in enumerate(rows) for x, p in enumerate(r) if not p[3])
        rows[y][x] = (255, 255, 255, 255)
        res = it.make_art(lib, raw, rows, ground=True)
        self.assertEqual(res.data, raw)
        self.assertTrue(any("outside the original outline" in n for n in res.notes))


@unittest.skipUnless(HAVE_DAT, "needs data.dat.vanilla.bak")
class CatalogueTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.cat = it.Catalogue(it.Library(DAT))

    def test_la_mancha_shares_the_gauntlet_picture_with_six_items(self):
        item = it.pick_one(self.cat, LA_MANCHA)
        self.assertEqual(item.rel, LA_MANCHA_REL)
        self.assertEqual(len(self.cat.others_using("Items/Inventory Images/Armor/Gauntlets", item)), 6)

    def test_maps_and_cans_count_as_users(self):
        # The false Spear of Destiny's ground sprite is also scenery in Alamut's final
        # encounter: repainting it in place would repaint the map too.
        spear = self.cat.users["items/pickups/special items/speardestinyfalse_pu"]
        self.assertIn("Levels/8 Alamut/08 Final Encounter.zax", spear)
        mana = self.cat.users["items/pickups/spirit energy/pu spirit mana charge 3"]
        self.assertTrue(any(u.lower().endswith(".can") for u in mana))

    def test_finding_by_path_and_by_words(self):
        self.assertEqual(it.pick_one(self.cat, LA_MANCHA_REL).rel, LA_MANCHA_REL)
        self.assertEqual(it.pick_one(self.cat, "mancha gauntlets").rel, LA_MANCHA_REL)
        with self.assertRaises(SystemExit):
            it.pick_one(self.cat, "gauntlet")      # several


@unittest.skipUnless(HAVE_DAT, "needs data.dat.vanilla.bak")
class CommandTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.mod = self.dir / "mod"
        (self.mod / "files").mkdir(parents=True)
        (self.mod / "mod.json").write_text(json.dumps({"id": "t", "files": []}, indent=2) + "\n")
        self.zf = zipfile.ZipFile(DAT)

    def tearDown(self):
        self.zf.close()
        self.tmp.cleanup()

    def test_export_then_import_unchanged_copies_and_repoints(self):
        code, out = _run("export", LA_MANCHA, "--out", str(self.dir))
        self.assertEqual(code, 0, out)
        png = self.dir / f"{LA_MANCHA} - inventory.png"
        self.assertEqual(png_io.read_rgba(png)[:2], (400, 368))
        code, out = _run("import", LA_MANCHA, str(png), "--mod", str(self.mod))
        self.assertEqual(code, 0, out)
        files = self.mod / "files"
        art = files / "Cache/Models/Items/Inventory Images/Armor/Gauntlets of La Mancha.mdl16"
        self.assertEqual(art.read_bytes(), self.zf.read(GAUNTLETS))
        diff = _diff(self.zf.read(LA_MANCHA_REL), (files / LA_MANCHA_REL).read_bytes())
        self.assertEqual(len(diff), 6, diff)       # three grade lines, each - and +
        self.assertTrue(all("Gauntlets of La Mancha" in l for l in diff if l[0] == "+"))
        listed = json.loads((self.mod / "mod.json").read_text())["files"]
        self.assertEqual(len(listed), 2)
        # importing again updates the copy in place, and lists nothing twice
        code, out = _run("import", LA_MANCHA, str(png), "--mod", str(self.mod))
        self.assertEqual(code, 0, out)
        self.assertNotIn("other items use", out)
        self.assertEqual(len(json.loads((self.mod / "mod.json").read_text())["files"]), 2)

    def test_dry_run_writes_nothing(self):
        _run("export", LA_MANCHA, "--out", str(self.dir))
        png = self.dir / f"{LA_MANCHA} - inventory.png"
        code, out = _run("import", LA_MANCHA, str(png), "--mod", str(self.mod), "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual([p for p in (self.mod / "files").rglob("*") if p.is_file()], [])

    def test_canvas_must_divide_by_the_scale(self):
        png = self.dir / "odd.png"
        png_io.write_rgba(png, 401, 368, bytes(401 * 368 * 4))
        code, out = _run("import", LA_MANCHA, str(png), "--mod", str(self.mod))
        self.assertNotEqual(code, 0)
        self.assertIn("does not divide by 4", out)

    def test_builder_that_writes_the_art_is_named(self):
        tools = self.mod / "tools"
        tools.mkdir()
        (tools / "build_it.py").write_text(
            'DEST = "Items/Inventory Images/Armor/Gauntlets of La Mancha"\n')
        (tools / "mentions.py").write_text("# Gauntlets of La Mancha wears the generic icon\n")
        _run("export", LA_MANCHA, "--out", str(self.dir))
        png = self.dir / f"{LA_MANCHA} - inventory.png"
        code, out = _run("import", LA_MANCHA, str(png), "--mod", str(self.mod), "--dry-run")
        self.assertIn("tools/build_it.py", out)
        self.assertNotIn("mentions.py", out)


if __name__ == "__main__":
    unittest.main()
