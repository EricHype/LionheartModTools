"""Get it in game: a test menu in the user's own mod that hands out the items being made.

A new item is in no chest, shop or loot table, so nothing in the game gives it to the
player. This puts a `[TEST]` reply into the conversation of Raymundo the Bowyer, a vendor
who stands in the Barcelona Gate District from the start, opening a menu with one reply
per test item. Choosing a reply gives the item, the way the Playtest Kit does
(`CActionGiveStandardInventoryItem`).

Choices made from what went wrong before:

* **The host is not one the Playtest Kit uses.** The kit hangs its menu on Merchant Lope
  and Jafar, and two mods replacing the same conversation means only one menu survives.
* **The entry nodes are read from the map, not chosen.** The kit's first host, Farshid,
  failed because the map never opened the node its menu was spliced into. Here every node
  the Gate District map opens for Raymundo gets the reply, and the build refuses to run if
  the map names none.
* **A conversation edit reaches an existing save**, because the tree is read when the
  conversation opens. So the menu works on a save in progress, unlike a chest placed in
  a map the player has already visited.
* **Wands are refused.** Fixt's engine notes: a wand given by this action has no usable
  charges.
* **It is for testing only, and easy to take out.** The list of test items lives in
  `itemedit-test.json` beside `mod.json`, outside `files/`, and `remove()` deletes the
  tree and its `mod.json` entry. The window says the menu is there until it is removed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import dialogtree_format as dt
from canedit_core import _render_manifest, register_in_mod_json
from icontool import Library
from itemedit_core import ItemDocument, ItemError, item_ref, wand_summary

HOST_TREE = "Resources/Levels/1 Barcelona/Dialog/Gate District/Barcelona Vendor Sympathetic.DialogTree"
HOST_REF = "Levels/1 Barcelona/Dialog/Gate District/Barcelona Vendor Sympathetic"
HOST_MAP = "Levels/1 Barcelona/Gate District.zax"
HOST_NAME = "Raymundo the Bowyer"
WHERE = f"{HOST_NAME}, the bow seller in the Barcelona Gate District"
STATE = "itemedit-test.json"
MENU_ID = "9500 item editor test items"
HOOK_TEXT = "[TEST] Show me the items I am testing."
CR = "\r\n"


@dataclass
class TestItem:
    label: str
    item: str = ""         # the base item, as the game names it (no Resources/, no extension)
    addition: str = ""     # a magic addition, or empty
    creature: str = ""     # a creature can to summon instead (Monster Cans/...), or empty

    @property
    def key(self) -> tuple:
        return (self.item, self.addition, self.creature)


def state_path(mod: Path) -> Path:
    return Path(mod) / STATE


def load(mod: Path) -> list[TestItem]:
    p = state_path(mod)
    if not p.is_file():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [TestItem(**it) for it in data.get("items", [])]


def is_installed(mod: Path) -> bool:
    return state_path(mod).is_file()


def entry_nodes(lib: Library) -> list[str]:
    """Every node the Gate District map opens Raymundo's conversation at."""
    data = lib.read(HOST_MAP).decode("latin-1")
    pat = re.compile(r"Dialog Tree File=" + re.escape(HOST_REF) + r"\r\n\s*Node ID=([^\r\n]*)",
                     re.I)
    out = []
    for m in pat.finditer(data):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def give_block(it: TestItem) -> list[str]:
    adds = ["Item Count=1", f"Addition to add={it.addition}"] if it.addition else ["Item Count=0"]
    return ["{", "Who to give to=$instigator", "Delete Trigger=0", "Notify Player=1",
            f"Inventory Item To Give={it.item}", "Additions to add=Array", "{", *adds, "}", "}"]


# The developers' own "companion" cheat summons a creature beside the player this way
# (Resources/Cheat Code Scripts/Give Companion.Cheat.txt). In a reply, $Instigator is the
# player. Unproven inside a conversation until checked in the game (docs/enemy-editor-e0.md).
SUMMON_SPREAD = "300"


def summon_block(it: TestItem) -> list[str]:
    return ["{", "New Location=$Instigator", "Copy Height=0",
            f"Random Location Delta={SUMMON_SPREAD}", "Warp Behavior=",
            f"Canned Entity to Create={it.creature}", "New Name=", "}"]


def build_tree(lib: Library, items: list[TestItem]) -> bytes:
    """Raymundo's conversation with the hook in every entry node and the menu at the end."""
    base = lib.read(HOST_TREE).decode("latin-1")
    if "[TEST]" in base or MENU_ID in base:
        raise ItemError(f"{HOST_NAME}'s conversation in this mod already has a test menu "
                        "that this editor did not write.")
    tree = dt.parse(base)
    nodes = entry_nodes(lib)
    if not nodes:
        raise ItemError(f"The Gate District map never opens {HOST_NAME}'s conversation, so "
                        "a test menu there could not be reached.")
    for nid in nodes:
        node = tree.node_by_id(nid)
        if node is None:
            raise ItemError(f"The map opens node {nid!r}, which the conversation lacks.")
        hook = [dt.Entry("Requirement", "!None"), dt.Entry("Reply Text", HOOK_TEXT),
                dt.Entry("Go to node ID", MENU_ID)]
        replies = node.replies
        if replies:
            node.insert_reply_entries(replies[0].start, hook + [dt.Entry(None)])
        else:
            node.add_reply(HOOK_TEXT, MENU_ID)
    text = ("These are the things you are testing. Pick an item to take it, or a creature "
            "to summon it beside you." if any(i.creature for i in items) else
            "These are the items you are testing. Which one do you want?")
    menu = dt.new_node(MENU_ID, text)
    for it in items:
        if it.creature:
            menu.add_reply(f"Summon: {it.label}", "")      # end the talk; it will attack
            menu.entries.append(dt.Entry("Custom Action", "CCreateEntityFromCanAction",
                                         summon_block(it)))
        else:
            menu.add_reply(it.label, MENU_ID)
            menu.entries.append(dt.Entry("Custom Action", "CActionGiveStandardInventoryItem",
                                         give_block(it)))
    menu.add_reply("Goodbye.", "")
    tree.add_node(menu)
    text = tree.to_text()
    dt.parse(text)                       # never write what will not read back
    return text.encode("latin-1")


def _write(lib: Library, mod: Path, items: list[TestItem]) -> Path:
    target = Path(mod) / "files" / HOST_TREE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_tree(lib, items))
    register_in_mod_json(mod, HOST_TREE)
    state_path(mod).write_text(json.dumps(
        {"host": HOST_TREE, "items": [it.__dict__ for it in items]}, indent=2) + "\n",
        encoding="utf-8")
    return target


def vanilla_library(lib: Library) -> Library:
    """The host tree must be built from the game's copy, never from a mod copy this editor
    wrote earlier, or every rebuild would hook it again."""
    return Library(lib.vanilla_path)


def add(lib: Library, mod: Path, doc: ItemDocument, base: ItemDocument | None = None,
        label: str = "") -> TestItem:
    """Add `doc` to the test menu (on `base`, for magic) and rebuild it."""
    mod = Path(mod)
    if not (mod / "mod.json").is_file():
        raise ItemError(f"{mod} has no mod.json, so it is not a mod folder.")
    own = mod / "files" / HOST_TREE
    if own.is_file() and not is_installed(mod):
        raise ItemError(f"Your mod changes {HOST_NAME}'s conversation itself, so the test "
                        "menu cannot go there.")
    if doc.dirty:
        raise ItemError("Save the item first.")
    if wand_summary(doc):
        raise ItemError("Wands can't be tested this way: a wand given by a conversation "
                        "has no charges (Lionheart Fixt's engine notes).")
    if doc.is_addition:
        if base is None or base.is_addition:
            raise ItemError("Pick the item to put this magic on.")
        it = TestItem(label or f"{base.name} of {doc.root.get('Display Name') or doc.stem}",
                      item_ref(base.rel), item_ref(doc.rel))
    else:
        it = TestItem(label or doc.name, item_ref(doc.rel))
    items = [x for x in load(mod) if x.key != it.key] + [it]
    _write(vanilla_library(lib), mod, items)
    return it


def add_creature(lib: Library, mod: Path, can_rel: str, label: str) -> TestItem:
    """Add a "Summon:" reply for a creature can (saved in the mod or vanilla)."""
    mod = Path(mod)
    if not (mod / "mod.json").is_file():
        raise ItemError(f"{mod} has no mod.json, so it is not a mod folder.")
    own = mod / "files" / HOST_TREE
    if own.is_file() and not is_installed(mod):
        raise ItemError(f"Your mod changes {HOST_NAME}'s conversation itself, so the test "
                        "menu cannot go there.")
    if not lib.exists(can_rel):
        raise ItemError("Save the creature first.")
    it = TestItem(label, creature=item_ref(can_rel))
    items = [x for x in load(mod) if x.key != it.key] + [it]
    _write(vanilla_library(lib), mod, items)
    return it


def remove_item(lib: Library, mod: Path, index: int) -> None:
    items = load(mod)
    del items[index]
    if items:
        _write(vanilla_library(lib), mod, items)
    else:
        remove(mod)


def remove(mod: Path) -> None:
    """Take the test menu out of the mod entirely: the tree, its mod.json entry and the
    list of test items."""
    mod = Path(mod)
    tree = mod / "files" / HOST_TREE
    if tree.is_file():
        tree.unlink()
        # tidy the empty folders the tree lived in, up to files/
        p = tree.parent
        while p != mod / "files" and p.is_dir() and not any(p.iterdir()):
            p.rmdir()
            p = p.parent
    unregister_in_mod_json(mod, HOST_TREE)
    if state_path(mod).is_file():
        state_path(mod).unlink()


def unregister_in_mod_json(mod_dir: Path, resource_path: str) -> bool:
    """Remove `resource_path` from the mod's `files` list, keeping the file's own layout:
    the matching line is taken out, nothing is reflowed."""
    manifest = Path(mod_dir) / "mod.json"
    text = manifest.read_bytes().decode("utf-8")
    data = json.loads(text)
    files = data.get("files", [])
    hits = [f for f in files if f.replace("\\", "/").lower() == resource_path.lower()]
    if not hits:
        return False
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(newline)
    if _render_manifest(data, newline, trailing) == text:
        # in json.dumps layout: re-render it, which also writes an empty list as []
        files.remove(hits[0])
        manifest.write_bytes(_render_manifest(data, newline, trailing).encode("utf-8"))
        return True
    entry = json.dumps(hits[0], ensure_ascii=False)
    lines = text.splitlines(keepends=True)
    idx = next(i for i, ln in enumerate(lines) if ln.strip().rstrip(",") == entry)
    removed_comma = lines[idx].rstrip("\r\n").endswith(",")
    del lines[idx]
    if not removed_comma:
        # it was the last entry: the one before it loses its comma
        j = idx - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j >= 0 and lines[j].rstrip("\r\n").endswith(","):
            body = lines[j].rstrip("\r\n")
            lines[j] = body[:-1] + lines[j][len(body):]
    new = "".join(lines)
    json.loads(new)
    manifest.write_bytes(new.encode("utf-8"))
    return True
