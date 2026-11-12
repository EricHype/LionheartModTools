"""Export an item's art to a PNG, paint it in any program, and import it back.

    python icontool.py find gauntlets
    python icontool.py export "Gauntlets of La Mancha"
    python icontool.py import "Gauntlets of La Mancha" "Gauntlets of La Mancha - inventory.png" --mod <mod folder>

Made for people who paint, not people who script. Export writes the art four times its
real size with real transparency, so it is comfortable to paint over; import shrinks it
back, encodes it, checks it the way the engine reads it, and wires it into the item --
copying the art first if other items share the same file, so repainting one pair of
gauntlets does not repaint all seven items that use that picture.

What it will and will not do, and why (each rule is from something that went wrong):

* **Inventory art** can change shape. When the outline is unchanged and the painting can
  be stored without loss, the original file is recoloured in place, which keeps its soft
  edges. Otherwise new art is built from the painting the way Lionheart Fixt's
  hand-painted La Mancha gauntlets were; soft edges then become hard ones, and the import
  says how many pixels that touched.
* **Ground art** (the sprite lying on the floor) keeps its outline. A from-scratch ground
  sprite draws correctly and cannot be picked up -- found in Fixt 0.86.1 -- so ground art
  is always recoloured in place. Paint outside the outline is ignored and counted.
* **Vanilla is never written.** Everything goes into the mod given with `--mod`, which is
  also read first, so a mod's own version of an item is the one edited.
* **A builder can undo an import.** Lionheart Fixt generates some art from scripts in its
  `tools/` folder; if one of them writes the file being replaced, the import names it,
  because rerunning that script would put the old art back.

The format work underneath is `mdl16_format` (decoding both colour planes, the in-place
recolour, `build_icon_file`, `verify_icon`); see docs/icon-tool.md for the walkthrough and
docs/mdl16-icon-format.md for the format.
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import mdl16_format as m
import png_io
from canedit_core import default_vanilla_dat, register_in_mod_json

ITEM_EXTS = (".inventoryitem", ".inventoryaddition")
CR = "\r\n"
DONOR = "Cache/Models/Items/Inventory Images/Quest Items/Deed Silver Mine.mdl16"
NO_ART = {"", "!unknown model", "!none"}
# An art field. `slot` is how a person names it on the command line.
GRADES = ("Basic", "Better", "Special")


# ---------------------------------------------------------------------------
# Where files come from: the mod first, then the vanilla archive
# ---------------------------------------------------------------------------

class Library:
    """Read-only view of the game's files as a mod sees them: the mod's `files/` tree
    shadows the vanilla archive. Names are compared case-insensitively, as the game
    does."""

    def __init__(self, vanilla: Path, mod: Path | None = None):
        self.vanilla_path = Path(vanilla)
        if not self.vanilla_path.is_file():
            raise SystemExit(
                f"Cannot find the vanilla game archive at {self.vanilla_path}.\n"
                "Pass --vanilla with the path to data.dat.vanilla.bak (modmanager.py "
                "makes it on the first install).")
        self.zf = zipfile.ZipFile(self.vanilla_path)
        self.mod = Path(mod).resolve() if mod else None
        self._vanilla = {n.lower(): n for n in self.zf.namelist() if not n.endswith("/")}
        self._mod: dict[str, Path] = {}
        if self.mod is not None:
            files = self.mod / "files"
            if not files.is_dir():
                raise SystemExit(f"{self.mod} has no files/ folder, so it is not a mod folder.")
            for p in files.rglob("*"):
                if p.is_file():
                    self._mod[p.relative_to(files).as_posix().lower()] = p

    def names(self) -> list[str]:
        out = {k: v for k, v in self._vanilla.items()}
        for k, p in self._mod.items():
            out[k] = p.relative_to(self.mod / "files").as_posix()
        return sorted(out.values(), key=str.lower)

    def exists(self, rel: str) -> bool:
        return rel.lower() in self._mod or rel.lower() in self._vanilla

    def where(self, rel: str) -> str:
        if rel.lower() in self._mod:
            return "mod"
        return "vanilla" if rel.lower() in self._vanilla else ""

    def read(self, rel: str) -> bytes:
        key = rel.lower()
        if key in self._mod:
            return self._mod[key].read_bytes()
        if key in self._vanilla:
            return self.zf.read(self._vanilla[key])
        raise FileNotFoundError(rel)

    def canonical(self, rel: str) -> str:
        """The spelling the file actually has, so a written copy matches it exactly."""
        key = rel.lower()
        if key in self._mod:
            return self._mod[key].relative_to(self.mod / "files").as_posix()
        return self._vanilla.get(key, rel)


# ---------------------------------------------------------------------------
# Items and their art fields
# ---------------------------------------------------------------------------

@dataclass
class ArtField:
    slot: str          # "inventory", "Basic", "Better", "Special" or "ground"
    line: int          # index into the item's lines
    key: str           # the field name on that line
    ref: str           # e.g. Items/Inventory Images/Armor/Gauntlets

    @property
    def rel(self) -> str:
        return "Cache/Models/" + self.ref + ".mdl16"

    @property
    def ground(self) -> bool:
        return self.slot == "ground"


@dataclass
class Item:
    rel: str
    text: str
    name: str = ""
    kind: str = ""
    art: list[ArtField] = field(default_factory=list)

    @property
    def stem(self) -> str:
        return self.rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    @property
    def ref(self) -> str:
        """How other files name this item: no Resources/ prefix, no extension."""
        r = self.rel
        if r.lower().startswith("resources/"):
            r = r[len("resources/"):]
        return r.rsplit(".", 1)[0]


def parse_item(rel: str, raw: bytes) -> Item:
    text = raw.decode("latin-1")
    item = Item(rel, text, kind="magic addition" if rel.lower().endswith(".inventoryaddition")
                else "item")
    depth, in_images = 0, False
    for i, line in enumerate(text.split(CR)):
        s = line.strip()
        if depth == 1 and "=" in s:
            key, val = s.split("=", 1)
            if key == "Display Name":
                item.name = val.strip()
            elif key == "Inventory Window" and val.strip().lower() not in NO_ART:
                item.art.append(ArtField("inventory", i, key, val.strip()))
            elif key == "On the ground" and val.strip().lower() not in NO_ART:
                item.art.append(ArtField("ground", i, key, val.strip()))
            in_images = key == "Inventory Window Images"
        elif depth == 2 and in_images and "=" in s:
            key, val = s.split("=", 1)
            if key in GRADES and val.strip().lower() not in NO_ART:
                item.art.append(ArtField(key, i, key, val.strip()))
        depth += s.count("{") - s.count("}")
        if depth <= 1 and s == "}":
            in_images = False
    if not item.name:
        item.name = item.stem
    return item


ART_REF = re.compile(rb"=[ \t]*(Items/(?:Inventory Images|PickUps)/[^\r\n]*)")
BINARY_EXTS = (".mdl16", ".frm16", ".gr2", ".ogg", ".wav", ".mp3", ".bik", ".dds", ".png",
               ".bmp", ".tga", ".jpg", ".xls", ".fnt", ".ttf", ".dat", ".bin")


class Catalogue:
    """Every item and magic addition, and every file that shows each picture.

    Items are not the only users. Creature cans embed whole items (the Spirit Energy
    drops name `Potion Unknown` and the pickup sprites), and maps place item sprites as
    scenery by `Model=` (Alamut stands a pickup sprite up as a prop). All of it is
    scanned -- about 0.1 s, since the archive is stored uncompressed -- because a picture
    counted as unshared when a map also shows it would be overwritten for both."""

    def __init__(self, lib: Library):
        self.lib = lib
        self.items: list[Item] = []
        self.users: dict[str, set[str]] = {}
        for rel in lib.names():
            low = rel.lower()
            if low.endswith(ITEM_EXTS):
                item = parse_item(rel, lib.read(rel))
                self.items.append(item)
                for a in item.art:
                    self.users.setdefault(a.ref.lower(), set()).add(item.rel)
            elif (low.startswith(("resources/", "levels/"))
                  and not low.endswith(BINARY_EXTS)):
                data = lib.read(rel)
                if b"=Items/" not in data and b"= Items/" not in data:
                    continue
                for ref in ART_REF.findall(data):
                    key = ref.decode("latin-1").strip().lower()
                    self.users.setdefault(key, set()).add(rel)

    def find(self, query: str) -> list[Item]:
        q = query.strip().replace("\\", "/")
        # a path, with or without Resources/ and the extension
        qp = q.lower()
        if qp.startswith("resources/"):
            qp = qp[len("resources/"):]
        qp = re.sub(r"\.(inventoryitem|inventoryaddition)$", "", qp)
        by_path = [it for it in self.items if it.ref.lower() == qp]
        if by_path:
            return by_path
        exact = [it for it in self.items if it.name.lower() == q.lower()]
        if exact:
            return exact
        words = q.lower().split()
        return [it for it in self.items
                if all(w in (it.name + " " + it.stem).lower() for w in words)]

    def others_using(self, ref: str, item: Item) -> list[str]:
        return sorted(r for r in self.users.get(ref.lower(), ()) if r != item.rel)


def pick_one(cat: Catalogue, query: str) -> Item:
    hits = cat.find(query)
    if not hits:
        raise SystemExit(f'No item matches "{query}". Try: python icontool.py find <a word from its name>')
    if len(hits) > 1:
        lines = [f'"{query}" matches {len(hits)} items. Use the file path to pick one:']
        lines += [f"  {it.name:40}  {it.ref}" for it in hits[:30]]
        if len(hits) > 30:
            lines.append(f"  ... and {len(hits) - 30} more")
        raise SystemExit("\n".join(lines))
    return hits[0]


# ---------------------------------------------------------------------------
# Pixels
# ---------------------------------------------------------------------------

def to_rgba_bytes(rows) -> bytearray:
    out = bytearray()
    for row in rows:
        for px in row:
            out += bytes(px)
    return out


def scale_up(w: int, h: int, rgba: bytes, k: int) -> tuple[int, int, bytearray]:
    out = bytearray()
    for y in range(h):
        row = bytearray()
        for x in range(w):
            row += rgba[(y * w + x) * 4:(y * w + x) * 4 + 4] * k
        out += row * k
    return w * k, h * k, out


def scale_down(w: int, h: int, rgba: bytes, k: int) -> list[list[tuple[int, int, int, int]]]:
    """Average each k*k block to one pixel. Colour is weighted by alpha, so a brush edge
    fading to transparent does not darken the pixel it lands in."""
    rows = []
    for y in range(h // k):
        row = []
        for x in range(w // k):
            r = g = b = a = 0
            for dy in range(k):
                base = ((y * k + dy) * w + x * k) * 4
                for dx in range(k):
                    o = base + dx * 4
                    pa = rgba[o + 3]
                    r += rgba[o] * pa
                    g += rgba[o + 1] * pa
                    b += rgba[o + 2] * pa
                    a += pa
            if a:
                row.append((round(r / a), round(g / a), round(b / a), round(a / (k * k))))
            else:
                row.append((0, 0, 0, 0))
        rows.append(row)
    return rows


def checker_preview(rows, k: int = 4) -> tuple[int, int, bytearray]:
    """RGB image of `rows` over a grey checkerboard, so transparency is visible."""
    h, w = len(rows), len(rows[0])
    rgb = bytearray()
    for y in range(h):
        for x in range(w):
            r, g, b, a = rows[y][x]
            bg = 205 if ((x * k) // 8 + (y * k) // 8) % 2 else 150
            rgb += bytes(((r * a + bg * (255 - a)) // 255, (g * a + bg * (255 - a)) // 255,
                          (b * a + bg * (255 - a)) // 255))
    return png_io.scale_nearest(w, h, rgb, k)


def rgb565(r: int, g: int, b: int) -> int:
    return m._rgb888_to_rgb565(r, g, b)


# ---------------------------------------------------------------------------
# Building the new art
# ---------------------------------------------------------------------------

@dataclass
class Result:
    data: bytes
    method: str                       # "repainted in place" or "new art"
    notes: list[str] = field(default_factory=list)


def repaint_in_place(orig: bytes, painted, keep_outside_count: bool = True) -> tuple[bytes, dict]:
    """Recolour `orig` from `painted` (rows of RGBA at the same size) without touching
    any run length, skip, alpha byte or the file length. Every stored colour becomes the
    average of the painted pixels it covers; a pixel the painting leaves transparent
    keeps its old colour. Returns (bytes, stats)."""
    comp = m.decode_icon_composite(orig)
    w = comp["width"]
    planes = comp["plane"]
    stats = {"averaged": 0, "kept": 0, "outside": 0, "changed": 0}
    for y, row in enumerate(painted):
        for x, px in enumerate(row):
            if planes[y][x] == 0 and px[3]:
                stats["outside"] += 1
            elif planes[y][x] and not px[3]:
                stats["kept"] += 1

    def transform(v, idx, plane, count):
        if v == 0:
            return 0
        y, x0 = divmod(idx, w)
        cols = []
        for x in range(x0, x0 + count):
            if planes[y][x] != plane:
                continue          # hidden under the other plane, or transparent
            px = painted[y][x]
            if px[3]:
                cols.append(px[:3])
        if not cols:
            return v
        n = len(cols)
        avg = tuple(round(sum(c[i] for c in cols) / n) for i in range(3))
        new = rgb565(*avg) or 1   # 0 is the format's "nothing here"
        for c in cols:
            if max(abs(c[i] - avg[i]) for i in range(3)) > 24:
                stats["averaged"] += 1
        if new != v:
            stats["changed"] += 1
        return new

    return m.recolor_icon_in_place(orig, transform), stats


def build_new_art(donor: bytes, painted) -> tuple[bytes, dict]:
    h, w = len(painted), len(painted[0])
    stats = {"softened": 0, "dropped": 0}
    rows = []
    for row in painted:
        out = []
        for r, g, b, a in row:
            if 0 < a < 255:
                if a >= 128:
                    stats["softened"] += 1
                else:
                    stats["dropped"] += 1
            out.append((r, g, b, 255 if a >= 128 else 0))
        rows.append(out)
    return m.build_icon_file(donor, w, h, rows, hotspot_x=w // 2, hotspot_y=h // 2), stats


def visible_mask(rows) -> list[list[bool]]:
    return [[px[3] > 0 for px in row] for row in rows]


def make_art(lib: Library, orig: bytes, painted, ground: bool) -> Result:
    comp = m.decode_icon_composite(orig)
    same_size = (len(painted), len(painted[0])) == (comp["height"], comp["width"])
    if ground:
        if not same_size:
            raise SystemExit(
                f"Ground art has to keep its size ({comp['width']}x{comp['height']} pixels, "
                f"{comp['width'] * 4}x{comp['height'] * 4} in the exported PNG); this painting is "
                f"{len(painted[0])}x{len(painted)} after shrinking.\n"
                "A ground sprite built from scratch draws correctly but cannot be picked up, "
                "so ground art is only ever repainted inside its original outline.")
        data, st = repaint_in_place(orig, painted)
        res = Result(data, "repainted in place")
        if st["outside"]:
            res.notes.append(f"{st['outside']} painted pixels were outside the original outline "
                             "and were left out (ground art cannot change shape).")
        if st["averaged"]:
            res.notes.append(f"{st['averaged']} pixels were blended with their neighbours: this "
                             "file stores runs of pixels as one colour, and the outline has to "
                             "stay as it was.")
        if st["kept"]:
            res.notes.append(f"{st['kept']} pixels the painting left transparent kept their old colour.")
        return res

    if same_size and visible_mask(painted) == [[p != 0 for p in row] for row in comp["plane"]]:
        data, st = repaint_in_place(orig, painted)
        if not st["averaged"]:
            return Result(data, "repainted in place",
                          ["Same outline, so the original file was recoloured; soft edges kept."])
    data, st = build_new_art(lib.read(DONOR), painted)
    res = Result(data, "built as new art")
    if st["softened"] or st["dropped"]:
        res.notes.append(
            f"This file type has no partial transparency for new art: {st['softened']} "
            f"half-transparent pixels became solid and {st['dropped']} faint ones became clear.")
    return res


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", s).strip() or "item"


def art_groups(item: Item) -> list[tuple[str, list[ArtField]]]:
    """Art fields grouped by picture: the three grade images of a unique usually name
    one file, and should be exported and replaced together."""
    groups: dict[str, list[ArtField]] = {}
    for a in item.art:
        groups.setdefault(a.ref.lower(), []).append(a)
    out = []
    for fields in groups.values():
        slots = [f.slot for f in fields]
        if "ground" in slots:
            label = "ground"
        elif set(slots) <= set(GRADES) | {"inventory"} and len(fields) > 1:
            label = "inventory"
        else:
            label = "inventory" if slots == ["inventory"] else "inventory " + " ".join(slots)
        out.append((label, fields))
    return out


def cmd_find(args) -> int:
    lib = Library(args.vanilla, args.mod)
    cat = Catalogue(lib)
    hits = cat.find(" ".join(args.words))
    if not hits:
        print("Nothing matches. Try a single word from the name.")
        return 1
    for it in hits[:200]:
        tag = "" if lib.where(it.rel) == "vanilla" else "  (your mod's version)"
        print(f"{it.name:42} {it.kind:15} {it.ref}{tag}")
    if len(hits) > 200:
        print(f"... and {len(hits) - 200} more; add another word to narrow it down.")
    return 0


def cmd_export(args) -> int:
    lib = Library(args.vanilla, args.mod)
    cat = Catalogue(lib)
    item = pick_one(cat, args.item)
    out_dir = Path(args.out or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = art_groups(item)
    if not groups:
        print(f"{item.name} has no art of its own to export.")
        return 1
    print(f"{item.name}  ({item.ref})")
    for label, fields in groups:
        a = fields[0]
        if not lib.exists(a.rel):
            print(f"  {label}: {a.ref} -- no such file in the game, skipped")
            continue
        comp = m.decode_icon_composite(lib.read(a.rel))
        w, h = comp["width"], comp["height"]
        W, H, big = scale_up(w, h, to_rgba_bytes(comp["rows"]), args.scale)
        dest = out_dir / f"{safe_name(item.name)} - {label}.png"
        png_io.write_rgba(dest, W, H, big)
        others = cat.others_using(a.ref, item)
        share = f"; {len(others)} other items use the same picture" if others else ""
        print(f"  {label}: wrote {dest}  ({W}x{H}, {args.scale}x the real {w}x{h}{share})")
    print("\nPaint over the PNG in any program and keep its size, then import it:")
    print(f'  python icontool.py import "{item.name}" "<the png>" --mod <your mod folder>')
    return 0


def edit_item_text(item: Item, fields: list[ArtField], new_ref: str) -> str:
    lines = item.text.split(CR)
    for f in fields:
        old = lines[f.line]
        prefix = old[:len(old) - len(old.lstrip())]
        assert old.strip() == f"{f.key}={f.ref}" or old.strip().startswith(f"{f.key}="), old
        lines[f.line] = f"{prefix}{f.key}={new_ref}"
    out = CR.join(lines)
    assert out.count(CR) == item.text.count(CR), "line count changed"
    changed = [i for i, (a, b) in enumerate(zip(item.text.split(CR), lines)) if a != b]
    assert set(changed) <= {f.line for f in fields}, changed
    return out


def builders_writing(mod: Path, names: list[str]) -> list[str]:
    tools = mod / "tools"
    if not tools.is_dir():
        return []
    hits = []
    for p in sorted(tools.rglob("*.py")):
        try:
            t = p.read_text("utf-8", errors="replace")
        except OSError:
            continue
        if any(n and n in t for n in names):
            hits.append(p.relative_to(mod).as_posix())
    return hits


def cmd_import(args) -> int:
    lib = Library(args.vanilla, args.mod)
    cat = Catalogue(lib)
    item = pick_one(cat, args.item)
    groups = art_groups(item)
    want = args.art.lower() if args.art else None
    chosen = None
    for label, fields in groups:
        slots = {f.slot.lower() for f in fields}
        if want is None and label == "inventory":
            chosen = (label, fields)
        elif want is not None and (want == label.lower() or want in slots):
            chosen = (label, [f for f in fields if want in (label.lower(), f.slot.lower())])
    if chosen is None:
        avail = ", ".join(lbl for lbl, _ in groups) or "none"
        raise SystemExit(f"{item.name} has no {want or 'single inventory'} art to replace. "
                         f"Its art: {avail}. Pick one with --art.")
    label, fields = chosen
    old_ref = fields[0].ref
    if not lib.exists(fields[0].rel):
        raise SystemExit(f"{item.name}'s {label} art names {old_ref}, which is not in the game.")
    orig = lib.read(fields[0].rel)

    pw, ph, rgba = png_io.read_rgba(args.png)
    k = args.scale
    if pw % k or ph % k:
        raise SystemExit(
            f"{args.png} is {pw}x{ph}, which does not divide by {k}. Exported art is {k}x its "
            f"real size; keep the canvas a multiple of {k} (or pass --scale).")
    painted = scale_down(pw, ph, rgba, k)
    if not any(px[3] for row in painted for px in row):
        raise SystemExit(f"{args.png} is completely transparent.")

    res = make_art(lib, orig, painted, ground=fields[0].ground)
    m.verify_icon(res.data)
    back = m.decode_icon_composite(res.data)

    # Where it goes. Copy the art if anything else shows the same picture.
    others = sorted({r for f in fields for r in cat.others_using(f.ref, item)})
    new_ref = old_ref
    if others:
        folder = old_ref.rsplit("/", 1)[0]
        base = f"{folder}/{safe_name(item.stem)}"
        new_ref, n = base, 2
        while lib.exists("Cache/Models/" + new_ref + ".mdl16"):
            new_ref, n = f"{base} {n}", n + 1
    art_rel = lib.canonical("Cache/Models/" + new_ref + ".mdl16")
    item_text = edit_item_text(item, fields, new_ref) if new_ref != old_ref else None

    print(f"{item.name}: {label} art {res.method} ({back['width']}x{back['height']} pixels), and "
          "checked the way the game reads it.")
    for n in res.notes:
        print("  " + n)
    if others:
        print(f"  {len(others)} other items use the old picture, so this item gets its own "
              f"copy: {new_ref}")
        if fields[0].ground:
            print("  Note: a ground sprite under a new file name has not been tested in game "
                  "yet. Check that the item can still be picked up off the floor.")

    # Folder plus file name, e.g. "Armor/Gauntlets La Mancha": what a script that writes
    # the file has to spell out. The item's name alone also matches prose in comments.
    builders = builders_writing(args.mod, ["/".join(new_ref.split("/")[-2:])])
    if builders:
        print("\nWarning: these scripts in the mod mention this art or item, and may write it:")
        for b in builders:
            print(f"  {b}")
        print("If one of them generates this file, rerunning it will put the old art back. "
              "Update or retire it.")

    preview = Path(args.png).with_name(Path(args.png).stem + " (as the game will show it).png")
    W, H, rgb = checker_preview(back["rows"])
    if not args.dry_run:
        png_io.write_rgb(preview, W, H, rgb)

    if args.dry_run:
        print("\nDry run: nothing written.")
        return 0

    files = args.mod / "files"
    dest = files / art_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(res.data)
    written = [art_rel]
    if item_text is not None:
        item_rel = lib.canonical(item.rel)
        p = files / item_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(item_text.encode("latin-1"))
        written.append(item_rel)
    if (args.mod / "mod.json").is_file():
        for rel in written:
            register_in_mod_json(args.mod, rel)
    print("\nWrote:")
    for rel in written:
        print(f"  {files / rel}")
    print(f"Preview: {preview}")
    print("\nTo see it in game, install the mod as usual "
          "(python modmanager.py install <mod folder> <game folder>).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="icontool.py",
        description="Export an item's art to PNG, and import a painted PNG back into a mod.")
    ap.add_argument("--vanilla", type=Path, default=default_vanilla_dat(),
                    help="the vanilla archive (default: data.dat.vanilla.bak in the GOG install)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="find items by name")
    f.add_argument("words", nargs="+")
    f.add_argument("--mod", type=Path, help="also show this mod's items")
    f.set_defaults(func=cmd_find)

    e = sub.add_parser("export", help="save an item's art as PNG files to paint over")
    e.add_argument("item", help="the item's name as the game shows it, or its file path")
    e.add_argument("--mod", type=Path, help="export this mod's version of the item")
    e.add_argument("--out", help="folder to write the PNGs into (default: here)")
    e.add_argument("--scale", type=int, default=4, help="how many times bigger (default 4)")
    e.set_defaults(func=cmd_export)

    i = sub.add_parser("import", help="put a painted PNG into a mod as an item's art")
    i.add_argument("item", help="the item's name as the game shows it, or its file path")
    i.add_argument("png", help="the painted PNG")
    i.add_argument("--mod", type=Path, required=True, help="the mod folder to write into")
    i.add_argument("--art", help="which art: inventory (default), ground, or Basic/Better/Special")
    i.add_argument("--scale", type=int, default=4, help="how many times bigger the PNG is (default 4)")
    i.add_argument("--dry-run", action="store_true", help="check everything, write nothing")
    i.set_defaults(func=cmd_import)

    args = ap.parse_args(argv)
    if getattr(args, "scale", 1) < 1:
        ap.error("--scale must be 1 or more")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
