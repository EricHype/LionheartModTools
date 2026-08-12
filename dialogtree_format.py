"""Read and write `.DialogTree` files with an exact round-trip.

`.DialogTree` is the one format in this game that `resource_format.py` cannot handle as a
whole file. It is a hybrid: a flat list of node records separated by 60-dash lines, with
brace-objects embedded inside `Custom Action=` and `Custom Requirement=` values.

What makes a naive line-based reader wrong is that **nothing in these files is indented**,
including the contents of the brace blocks. A line reading `Node ID=3 Angry` is a new
dialogue node at brace depth 0 and a field of an embedded `CDisplayDialogTreeAction` at
depth 5, and they are textually identical. Across the 341 shipped files that is not
hypothetical: `Node ID` occurs 5323 times at depth 0 and 4 times deeper. So the reader
tracks brace depth, and only depth 0 defines structure.

Measured facts about the shipped corpus, all 341 files:

* CRLF throughout, always ending `}\\r\\n`.
* Always begins `CDialogTree` then `{`.
* Separator lines are exactly 60 dashes, every time.
* Exactly 15 distinct keys at depth 0. Nesting reaches 15 levels.

The model keeps the file's own lines as the source of truth and exposes a structured view
over them, so anything the editor does not touch is reproduced exactly rather than
regenerated. `parse(text).to_text() == text` holds for every shipped file, which is the
gate this module has to pass before anything is built on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SEPARATOR = "-" * 60
NEWLINE = "\r\n"

# The keys that carry structure at depth 0. Anything else at depth 0 would be new.
NODE_KEYS = ("Node ID", "Text", "Should Have Voiceover")
REPLY_KEYS = ("Requirement", "Custom Requirement", "Reply Text", "Go to node ID",
              "Custom Action", "Icon", "Is Default Reply", "Action work in progress")

# A reply record starts here. Every reply in the corpus opens with Requirement, and the
# three mandatory reply keys occur exactly 10938 times each.
REPLY_START = "Requirement"


def normalise_id(node_id: str) -> str:
    """How the engine matches a `Go to node ID` against a `Node ID`.

    Case-insensitively, with surrounding whitespace trimmed. Established from the corpus
    rather than assumed: comparing exactly, 369 replies in 78 shipped files point at
    nothing -- including the Goodbye reply of the very first NPC in the game, which
    plainly works. 242 of those differ only in case (`10 Goodbye` vs `10 goodbye`) and 31
    only in trailing space. Under this rule they resolve.

    The leading number is *not* the key on its own, tempting though it looks: 534 numbers
    are reused within their own file (`1 Conversation Start Male` / `Female` / `Angry`),
    so it cannot identify a node. The full normalised string can -- across all 341 files
    there is not one collision.
    """
    return node_id.strip().lower()


@dataclass
class Entry:
    """One line of a record, plus the brace block it opens if it opens one.

    `key is None` marks a blank line. Blank lines are load-bearing for readability and
    separate replies visually, so they are preserved rather than normalised away.
    """
    key: str | None
    value: str = ""
    block: list[str] = field(default_factory=list)   # raw lines, braces included

    def to_lines(self) -> list[str]:
        if self.key is None:
            return [""]
        return [f"{self.key}={self.value}"] + list(self.block)


@dataclass
class Node:
    """One dialogue node: what the NPC says, and the replies it offers."""
    entries: list[Entry] = field(default_factory=list)

    # -- simple fields ----------------------------------------------------

    def _get(self, key: str) -> str | None:
        for e in self.entries:
            if e.key == key:
                return e.value
        return None

    def _set(self, key: str, value: str) -> bool:
        for e in self.entries:
            if e.key == key:
                e.value = value
                return True
        return False

    @property
    def node_id(self) -> str:
        return self._get("Node ID") or ""

    @property
    def text(self) -> str:
        return self._get("Text") or ""

    @text.setter
    def text(self, value: str) -> None:
        self._set("Text", value)

    @property
    def replies(self) -> list["Reply"]:
        """Group the entries into replies. Each starts at a `Requirement` line and runs
        to the next one, so a reply owns the blank lines that follow it."""
        out: list[Reply] = []
        start = None
        for i, e in enumerate(self.entries):
            if e.key == REPLY_START:
                if start is not None:
                    out.append(Reply(self, start, i))
                start = i
        if start is not None:
            out.append(Reply(self, start, len(self.entries)))
        return out

    def to_lines(self) -> list[str]:
        lines: list[str] = []
        for e in self.entries:
            lines.extend(e.to_lines())
        return lines


@dataclass
class Reply:
    """A player reply, as a window over its node's entry list.

    A view rather than a copy: editing through it writes into the node, so the file's
    own line order and blank lines survive untouched.
    """
    node: Node
    start: int
    end: int

    def _get(self, key: str) -> str | None:
        for e in self.node.entries[self.start:self.end]:
            if e.key == key:
                return e.value
        return None

    def _set(self, key: str, value: str) -> bool:
        for e in self.node.entries[self.start:self.end]:
            if e.key == key:
                e.value = value
                return True
        return False

    @property
    def text(self) -> str:
        return self._get("Reply Text") or ""

    @text.setter
    def text(self, value: str) -> None:
        self._set("Reply Text", value)

    @property
    def goto(self) -> str:
        """Target node ID, or empty when the conversation ends here.

        Whitespace-only counts as empty. 21% of all shipped replies end the conversation
        with a genuinely empty target, and 12 more write a single space; reading those 12
        as a link to a node named " " reports them as broken when they plainly are not.
        """
        return (self._get("Go to node ID") or "").strip()

    @goto.setter
    def goto(self, value: str) -> None:
        self._set("Go to node ID", value)

    @property
    def requirement(self) -> str:
        return self._get("Requirement") or ""

    @property
    def icon(self) -> str:
        return self._get("Icon") or ""

    @property
    def is_default(self) -> bool:
        return (self._get("Is Default Reply") or "0") == "1"

    def block_for(self, key: str) -> list[str]:
        """Raw lines of an embedded object, e.g. `Custom Action`."""
        for e in self.node.entries[self.start:self.end]:
            if e.key == key:
                return e.block
        return []


class DialogTree:
    """A parsed `.DialogTree`. `to_text()` reproduces the input exactly when unedited."""

    def __init__(self, header: list[Entry], nodes: list[Node], path: Path | None = None):
        self.header = header
        self.nodes = nodes
        self.path = path
        self.dirty = False

    # -- header fields ----------------------------------------------------

    def _header_get(self, key: str) -> str | None:
        for e in self.header:
            if e.key == key:
                return e.value
        return None

    @property
    def name(self) -> str:
        return self._header_get("Name") or ""

    @property
    def portrait(self) -> str:
        return self._header_get("Portrait") or ""

    # -- lookup -----------------------------------------------------------

    def node_by_id(self, node_id: str) -> Node | None:
        want = normalise_id(node_id)
        for n in self.nodes:
            if normalise_id(n.node_id) == want:
                return n
        return None

    def dangling_targets(self) -> list[tuple[str, str, str]]:
        """(source node, reply text, missing target) for every reply pointing nowhere.

        An empty `Go to node ID` is legal and ends the conversation; a non-empty one that
        matches no node is the bug this catches. In-game it presents as the conversation
        refusing to advance, with nothing said about why.
        """
        known = {normalise_id(n.node_id) for n in self.nodes}
        out = []
        for n in self.nodes:
            for r in n.replies:
                if r.goto and normalise_id(r.goto) not in known:
                    out.append((n.node_id, r.text, r.goto))
        return out

    def unreachable_nodes(self) -> list[str]:
        """Nodes nothing links to. The first node is the entry point, so it is exempt --
        and so is anything a map's CDisplayDialogTreeAction might name directly, which is
        why this is advisory rather than an error."""
        targets = {normalise_id(r.goto) for n in self.nodes for r in n.replies if r.goto}
        return [n.node_id for n in self.nodes[1:]
                if normalise_id(n.node_id) not in targets]

    # -- serialisation ----------------------------------------------------

    def to_text(self) -> str:
        lines = ["CDialogTree", "{"]
        for e in self.header:
            lines.extend(e.to_lines())
        for node in self.nodes:
            lines.append(SEPARATOR)
            lines.extend(node.to_lines())
        lines.append("}")
        return NEWLINE.join(lines) + NEWLINE

    def save(self, path: Path | None = None) -> None:
        target = Path(path or self.path)
        target.write_bytes(self.to_text().encode("latin-1"))
        self.dirty = False


def _read_entries(lines: list[str], i: int, stop) -> tuple[list[Entry], int]:
    """Consume depth-0 lines into entries until `stop(line)` or the closing brace.

    Brace blocks are swallowed whole and kept as raw text. That is deliberate: the
    editor has no reason to reformat an embedded action, and not touching it means it
    cannot be damaged.
    """
    entries: list[Entry] = []
    while i < len(lines):
        line = lines[i]
        if stop(line):
            break
        if not line.strip():
            entries.append(Entry(None))
            i += 1
            continue
        key, _, value = line.partition("=")
        entry = Entry(key, value)
        i += 1
        # A value that opens an object is followed by a lone brace.
        if i < len(lines) and lines[i].strip() == "{":
            depth = 0
            while i < len(lines):
                stripped = lines[i].strip()
                entry.block.append(lines[i])
                i += 1
                if stripped == "{":
                    depth += 1
                elif stripped == "}":
                    depth -= 1
                    if depth == 0:
                        break
        entries.append(entry)
    return entries, i


def parse(text: str, path: Path | None = None) -> DialogTree:
    lines = text.split(NEWLINE)
    # The file ends with a newline, so the split leaves a trailing empty element.
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) < 2 or lines[0] != "CDialogTree" or lines[1] != "{":
        raise ValueError(
            "not a DialogTree: must start with 'CDialogTree' then '{'. A file built "
            "without this wrapper fails in-game as 'the executable or data file has "
            "become corrupted', with no parse error.")
    if lines[-1] != "}":
        raise ValueError("DialogTree must end with a closing '}'")

    body = lines[:-1]
    i = 2

    def is_sep(line: str) -> bool:
        return line.strip() and set(line.strip()) == {"-"}

    header, i = _read_entries(body, i, is_sep)
    nodes: list[Node] = []
    while i < len(body):
        if is_sep(body[i]):
            i += 1
            entries, i = _read_entries(body, i, is_sep)
            nodes.append(Node(entries))
        else:
            i += 1
    return DialogTree(header, nodes, path)


def load(path) -> DialogTree:
    path = Path(path)
    return parse(path.read_bytes().decode("latin-1"), path)
