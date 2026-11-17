"""Tests for the item editor's headless core.

    python -m unittest tests.test_itemedit_core -v

Needs the game's `data.dat.vanilla.bak`; the tests skip without it. Nothing here writes
outside a temporary folder.
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

import itemedit_core as c  # noqa: E402
from canedit_core import default_vanilla_dat  # noqa: E402
from icontool import Library  # noqa: E402
from resource_format import parse_resource_text  # noqa: E402

DAT = default_vanilla_dat()
HAVE_DAT = DAT.is_file()

SHORT_SWORD = "Resources/Inventory Items/ShortSword.InventoryItem"
HELMET = "Resources/Inventory Items/Helmet.InventoryItem"
GEM = "Resources/Inventory Items/Gem.InventoryItem"
POISON = ("Resources/Inventory/Inventory Additions/Miscellaneous/Helmets/"
          "Poison Resistance.InventoryAddition")
COLD_TOUCH = "Resources/Inventory/Inventory Additions/Armor/Cold Touch.InventoryAddition"
HOLY_MACE = "Resources/Inventory/Specific Item Cans/Unique Magic Items/Holy Mace.InventoryItem"
POTIONS = "Resources/Inventory/Inventory Additions/Miscellaneous/Potions/"


def changed_lines(a: bytes, b: bytes) -> list[str]:
    return [l for l in difflib.unified_diff(
        a.decode("latin-1").split("\r\n"), b.decode("latin-1").split("\r\n"),
        lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("---", "+++")]


def make_mod(folder: Path) -> Path:
    mod = folder / "mymod"
    (mod / "files").mkdir(parents=True)
    (mod / "mod.json").write_bytes(b'{\r\n  "name": "mymod",\r\n  "files": []\r\n}\r\n')
    return mod


@unittest.skipUnless(HAVE_DAT, "needs the vanilla game archive")
class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = Library(DAT)

    def doc(self, rel):
        return c.ItemDocument.from_library(self.lib, rel)

    # -- the contract -------------------------------------------------------

    def test_every_item_round_trips_and_is_recognised(self):
        entries = c.library_entries(self.lib)
        self.assertGreater(len(entries), 500)
        for e in entries:
            d = self.doc(e.rel)
            self.assertFalse(d.dirty, e.rel)
            self.assertEqual(d.to_bytes(), self.lib.read(e.rel), e.rel)
            # every reader runs without error on every shipped item
            c.damage_lines(d), c.stat_lines(d), c.problems(d), c.tooltip(d)

    def test_one_edit_is_one_line(self):
        d = self.doc(SHORT_SWORD)
        before = d.to_bytes()
        line = c.damage_lines(d)[0]
        c.set_damage_range(line, "2", "5")
        self.assertEqual([l.replace("\t", "") for l in changed_lines(before, d.to_bytes())],
                         ["-Constant Value=1", "+Constant Value=2"])
        d2 = self.doc(GEM)
        before = d2.to_bytes()
        c.set_price(d2, "40")
        self.assertEqual(len(changed_lines(before, d2.to_bytes())), 2)

    # -- basics --------------------------------------------------------------

    def test_basics(self):
        d = self.doc(GEM)
        self.assertEqual(c.get_field(d, "Display Name"), "Gem")
        self.assertEqual(c.get_price(d), "25")
        c.set_field(d, "Display Name", "Moonstone")
        c.set_field(d, "Encumbrance", "1", numeric=True, label="Weight")
        self.assertEqual(d.name, "Moonstone")
        with self.assertRaises(c.ItemError):
            c.set_field(d, "Encumbrance", "heavy", numeric=True, label="Weight")
        with self.assertRaises(c.ItemError):
            c.set_field(d, "Description", "two\nlines")

    # -- damage --------------------------------------------------------------

    def test_damage_kinds(self):
        sword = c.damage_lines(self.doc(SHORT_SWORD))
        self.assertEqual([(l.kind, l.damage_type) for l in sword], [("ladder", "Slashing")])
        self.assertEqual(c.damage_range(sword[0]), ("1", "5"))
        self.assertEqual(c.ladder_span(sword[0]), (0.0, 9.0))   # +0..6 skill, +3 perk
        mace = c.damage_lines(self.doc(HOLY_MACE))
        self.assertEqual([l.kind for l in mace], ["ladder", "range"])

    def test_damage_refuses_bad_range(self):
        line = c.damage_lines(self.doc(SHORT_SWORD))[0]
        with self.assertRaises(c.ItemError):
            c.set_damage_range(line, "6", "2")
        c.set_damage_type(line, "Fire")
        self.assertEqual(line.damage_type, "Fire")

    # -- stats ---------------------------------------------------------------

    def test_stats_read_and_edit(self):
        d = self.doc(HOLY_MACE)
        labels = [(s.label, s.amount_kind, c.get_stat_amount(s)) for s in c.stat_lines(d)]
        self.assertIn(("Smite", "fixed", "1"), labels)
        poison = c.stat_lines(self.doc(POISON))
        self.assertEqual([(s.label, s.amount_kind) for s in poison],
                         [("Poison Damage Resistance", "rolled")])

    def test_add_and_remove_stat_keeps_counts(self):
        d = self.doc(GEM)                       # no worn behaviour yet
        line = c.add_stat(d, "Character Attributes/(LK) Luck", "2")
        reread = c.ItemDocument("x", d.to_bytes(), "new")
        self.assertEqual([(s.label, c.get_stat_amount(s)) for s in c.stat_lines(reread)],
                         [("Luck", "2")])
        self.assertEqual(c.child(reread.root, "PlugIn Behaviors").get("Item Count"), "4")
        c.remove_stat(d, line)
        mods = c.child(c.stat_lines(self.doc(HOLY_MACE))[0].behaviour, "Modifiers")
        self.assertEqual(mods.get("Item Count"), str(len(mods.items())))
        self.assertEqual(c.child(line.behaviour, "Modifiers").get("Item Count"), "0")

    def test_stat_choices(self):
        choices = c.stat_choices(self.lib)
        by_target = {ch.target: ch for ch in choices}
        self.assertEqual(by_target["Character Attributes/(ST) Strength"].label, "Strength")
        self.assertEqual(by_target["Skills/Fighting/OneHandedMelee"].label, "One-Handed Melee")
        self.assertIn("Derived Character Attributes/Damage Related/Poison Damage Resistance",
                      by_target)
        self.assertFalse(any("Game Scripting Variables" in t for t in by_target))
        self.assertFalse(any(ch.label.strip("?") == "" for ch in choices))

    # -- rolls and tooltips --------------------------------------------------

    def test_roll_table(self):
        steps = c.roll(self.doc(POISON))
        self.assertEqual([(s.label, s.amount, s.price) for s in steps],
                         [("Weakest", "5", "125"), ("Middle", "10", "800"),
                          ("Strongest", "15", "3500")])

    def test_tooltip_matches_a_real_save(self):
        # A save held "Helmet of Poison Resistance" at the weakest roll (5) priced 131:
        # the helmet's 6 plus the magic's 125.
        t = c.tooltip(self.doc(POISON), self.doc(HELMET), step="Weakest")
        self.assertEqual(t.title, "Helmet of Poison Resistance")
        self.assertEqual(t.price, "131")
        self.assertIn("Poison Resistance  5%.", t.lines[-1])

    def test_tooltip_scaled_amounts(self):
        t = c.tooltip(self.doc(COLD_TOUCH), step="Strongest")
        self.assertIn("dealing 2 - 7 points", t.lines[-1])
        t = c.tooltip(self.doc(SHORT_SWORD))
        self.assertEqual(t.lines, ["(One-Handed) Slashing Damage 1-5."])

    # -- problems --------------------------------------------------------------

    def test_problems(self):
        d = self.doc(POISON)
        self.assertEqual(c.problems(d), [])
        c.set_field(d, "Description", "Resists poison by %i[poisonbonus]%.")
        self.assertTrue(any("poisonbonus" in p for p in c.problems(d)))
        c.set_roll(d, "Minimum", "amount", "20")
        self.assertTrue(any("weakest" in p for p in c.problems(d)))

    # -- cloning -------------------------------------------------------------

    def test_clone_addition_rewrites_self_paths(self):
        d = self.doc(COLD_TOUCH)
        new = c.clone(d, self.lib, "Cold Touch Lesser", "Early Frost")
        self.assertEqual(new.rel, "Resources/Inventory/Inventory Additions/Armor/"
                                  "Cold Touch Lesser.InventoryAddition")
        text = new.to_bytes().decode("latin-1")
        self.assertNotIn("InventoryAddition=Inventory/Inventory Additions/Armor/Cold Touch\r\n",
                         text)
        self.assertEqual(text.count("InventoryAddition=Inventory/Inventory Additions/Armor/"
                                    "Cold Touch Lesser\r\n"), 2)
        self.assertEqual(new.name, "Early Frost")
        self.assertEqual(c.problems(new), [])
        self.assertTrue(new.dirty)
        # the template is untouched
        self.assertFalse(d.dirty)

    def test_clone_base_item_rewrites_origonal_item(self):
        new = c.clone(self.doc(GEM), self.lib, "Moonstone", "Moonstone")
        self.assertEqual(new.root.get("Origonal Item"), "Inventory Items/Moonstone")
        self.assertEqual(c.problems(new), [])

    def test_clone_refuses_taken_and_bad_names(self):
        with self.assertRaises(c.ItemError):
            c.clone(self.doc(GEM), self.lib, "expensive gem", "x")   # case-insensitive
        with self.assertRaises(c.ItemError):
            c.clone(self.doc(GEM), self.lib, "../escape", "x")

    # -- extra damage and strike effects ------------------------------------

    def test_add_extra_damage_against_a_creature(self):
        d = self.doc(SHORT_SWORD)
        line = c.add_extra_damage(d, "Fire", "2", "4", against="Undead")
        reread = c.ItemDocument("x", d.to_bytes(), "new")
        lines = c.damage_lines(reread)
        self.assertEqual([(l.kind, l.damage_type) for l in lines],
                         [("ladder", "Slashing"), ("range", "Fire")])
        self.assertEqual(c.extra_against(lines[1]), "Undead")
        self.assertIsNone(c.extra_against(lines[0]))
        c.set_extra_against(line, "Everything")
        self.assertEqual(line.behaviour.get("Hit Or Miss"),
                         "Damage Types/Damage Hit Or Miss/AlwaysHit")
        # the shown text is the weapon's own damage only
        self.assertEqual(c.tooltip(d).lines, ["(One-Handed) Slashing Damage 1-5."])
        c.remove_damage(d, line)
        self.assertEqual(d.to_bytes(), self.lib.read(SHORT_SWORD))
        with self.assertRaises(c.ItemError):
            c.remove_damage(d, c.damage_lines(d)[0])

    def test_every_hit_condition_exists(self):
        for tail in c.EXTRA_AGAINST.values():
            self.assertTrue(self.lib.exists(f"Resources/{c.HIT_OR_MISS}{tail}.DamageHitOrMiss"),
                            tail)

    def test_add_strike_effects(self):
        d = self.doc(SHORT_SWORD)
        c.add_on_hit(d, "25", "Fire", "3", "6")
        c.add_on_hit(d, "30", "Slashing", "2", "4", duration="15")
        reread = c.ItemDocument("x", d.to_bytes(), "new")
        hits = c.on_hits(reread)
        self.assertEqual([(h.kind, h.editable, h.damage_type) for h in hits],
                         [("instant", True, "Fire"), ("over time", True, "Slashing")])
        v = c.on_hit_values(hits[1])
        self.assertEqual((v["chance"], v["range"], v["duration"], v["total"], v["per_second"]),
                         ("30", "2-4", "15", "6-12", "0.60"))
        self.assertEqual(c.on_hit_sentence(hits[1]),
                         "Successful strikes have a 30% chance to deal 2-4 Slashing damage "
                         "every 5 seconds for 15 seconds.")
        c.set_on_hit(hits[1], "50", "1", "3", "20")
        self.assertEqual(hits[1].ticks_node.get("Constant Value"), "4")
        self.assertEqual(c.on_hit_values(hits[1])["total"], "4-12")
        with self.assertRaises(c.ItemError):
            c.set_on_hit(hits[1], "50", "1", "3", "12")      # not a multiple of 5
        with self.assertRaises(c.ItemError):
            c.set_on_hit(hits[0], "0", "1", "3")
        self.assertTrue(c.is_weapon(d))
        self.assertFalse(c.is_weapon(self.doc(GEM)))

    def test_new_bleed_has_the_bloodletter_shape(self):
        """Bloodletter is a released, working bleed; ours must be the same tree with plain
        numbers where it reads named amounts."""
        path = (ROOT / "mods/bloodletter-scimitar/files/Resources/Inventory/Inventory "
                "Additions/Weapons/Bloodletter.InventoryAddition")
        if not path.is_file():
            self.skipTest("Bloodletter mod not present")
        theirs = next(b for b in c.ItemDocument("b", path.read_bytes(), "mod").behaviours()
                      if b.type_name == "CPlugInBehaviorStrikeAction")
        for n in c.walk(theirs):
            for i, (k, v) in enumerate(n.fields):
                if isinstance(v, c.ResourceNode) and \
                        v.type_name == "CInventoryAdditionsNamedExpressionExpression":
                    n.fields[i] = (k, c.constant("0"))

        def skeleton(node):
            return [ln.split("=")[0] + ("=" + ln.split("=", 1)[1] if ln.split("=", 1)[1:]
                    and ln.split("=", 1)[1][:1] == "C" else "")
                    for ln in node.to_text().split("\r\n")]
        ours = c.new_on_hit("30", "Slashing", "2", "4", "15")
        self.assertEqual(skeleton(ours), skeleton(theirs))

    # -- when used ---------------------------------------------------------------

    def test_timed_potion(self):
        d = self.doc(POTIONS + "Strength.InventoryAddition")
        (e,) = c.use_effects(d)
        self.assertEqual((e.kind, c.get_use_duration(e), c.get_use_label(e)),
                         ("timed", "120", "Strength"))
        self.assertEqual([(s.label, c.get_stat_amount(s)) for s in e.stats()],
                         [("Strength", "2")])
        before = d.to_bytes()
        c.set_use_duration(e, "300")
        # the effect and its on-screen icon change together, and nothing else
        self.assertEqual(sorted(l.replace("\t", "") for l in changed_lines(before, d.to_bytes())),
                         ["+Constant Value=300", "+Constant Value=300",
                          "-Constant Value=120", "-Constant Value=120"])
        c.add_stat_to(e.action, "Character Attributes/(EN) Endurance", "1")
        reread = c.use_effects(c.ItemDocument("x", d.to_bytes(), "new"))[0]
        self.assertEqual([s.label for s in reread.stats()], ["Strength", "Endurance"])
        self.assertEqual(c.child(reread.action, "Modifiers").get("Item Count"), "2")
        with self.assertRaises(c.ItemError):
            c.set_use_duration(e, "0")

    def test_mismatched_icon_duration_is_a_problem(self):
        # vanilla's Fire Protection potion lasts 60 s and its icon says 120 s
        d = self.doc(POTIONS + "Fire Protection.InventoryAddition")
        self.assertTrue(any("on-screen icon shows 120" in p for p in c.problems(d)))
        c.set_use_duration(c.use_effects(d)[0], "60")
        self.assertFalse(any("on-screen icon" in p for p in c.problems(d)))

    def test_permanent_book_and_scripted_healing(self):
        book = c.use_effects(self.doc(
            "Resources/Inventory/Specific Item Cans/Quest Items/Books/Book of Death.InventoryItem"))
        self.assertEqual([(e.kind, [s.label for s in e.stats()]) for e in book],
                         [("permanent", ["Summoning"])])
        heal = c.use_effects(self.doc(POTIONS + "Healing.InventoryAddition"))
        self.assertEqual(heal[0].kind, "other")
        self.assertIn("heals", heal[0].summary)

    def test_preview_base_for_magic(self):
        entries = c.library_entries(self.lib)
        first = lambda rel: c.bases_for(self.lib, entries, self.doc(rel))[0].name
        self.assertEqual(first(POTIONS + "Fire Protection.InventoryAddition"), "Potion")
        self.assertEqual(first(POISON), "Helmet")
        flat = self.doc(POTIONS + "Strength.InventoryAddition")
        c.set_flat_price(flat, "95")
        self.assertEqual({s.price for s in c.roll(flat)}, {"95"})

    def test_wand_summary(self):
        d = self.doc("Resources/Inventory/Inventory Additions/Miscellaneous/Wands/"
                     "Fireball Minor.InventoryAddition")
        self.assertEqual(c.wand_summary(d), "Casts Fireball, 5-15 charges, at spell level 5.")

    # -- saving --------------------------------------------------------------

    def test_save_into_mod(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = make_mod(Path(tmp))
            d = self.doc(GEM)
            c.set_price(d, "40")
            target = d.save_into_mod(mod)
            self.assertEqual(target, mod / "files" / Path(GEM))
            self.assertFalse(d.dirty)
            self.assertEqual(d.origin, "mod")
            manifest = json.loads((mod / "mod.json").read_text())
            self.assertIn(GEM, json.dumps(manifest))
            # the mod's copy now shadows vanilla
            lib = Library(DAT, mod)
            again = c.ItemDocument.from_library(lib, GEM)
            self.assertEqual((again.origin, c.get_price(again)), ("mod", "40"))
            parse_resource_text(target.read_bytes().decode("latin-1"))


if __name__ == "__main__":
    unittest.main()
