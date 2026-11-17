"""The item editor: change an existing item, make a new one, change its art.

Made for people who do not want to read the game's files. Items are shown as cards
(Basics, Damage, Bonuses, Art) beside a preview of the game's tooltip, and every change
goes into your own mod; the game's files are never written.

    python itemedit.py                       # asks for your mod the first time
    python itemedit.py --mod <mod folder>

Everything rests on `itemedit_core`, whose load/save is byte-identical across all 536
shipped item files, so one edit is a one-line change in the saved file. Art goes through
`icontool`, which handles shared pictures and checks every picture the way the game
reads it.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QUrl
from PySide6.QtGui import (QAction, QDesktopServices, QIcon, QImage, QKeySequence, QPixmap,
                           QUndoCommand, QUndoStack)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QDockWidget, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

import icontool
import itemedit_core as core
from canedit_core import default_vanilla_dat
from icontool import Library
from qtwidgets import NoScrollComboBox
from resource_format import parse_resource_text

APP = "Lionheart Item Editor"
ORG = "LionheartModTools"
ROLE_REL = Qt.UserRole + 1

WELCOME = """<h2>Lionheart item editor</h2>
<p>Pick an item in the list on the left. You can do three things with it:</p>
<ol>
<li><b>Change it.</b> Edit its name, description, price, damage and bonuses on the cards.
Press <b>Save into my mod</b> when you're done.</li>
<li><b>Make a new item from it.</b> Press <b>Make a new item from this</b>. You get a copy
under a new name to change however you like. The original stays as it is.</li>
<li><b>Change its art.</b> On the Art card, export its pictures as PNG files, paint over
them in any paint program, and import them back.</li>
</ol>
<p>Everything you save goes into your own mod. The game's own files are never changed.</p>
<p><b>Magic</b> in the list is what a random magic item adds to a plain one: "of Poison
Resistance" turns a Helmet into a "Helmet of Poison Resistance". <b>Unique items</b> are
the named one-of-a-kind items.</p>
"""


def to_pixmap(rgba: tuple[int, int, bytes] | None, scale: int = 1) -> QPixmap | None:
    if rgba is None:
        return None
    w, h, data = rgba
    img = QImage(data, w, h, 4 * w, QImage.Format_RGBA8888).copy()
    pm = QPixmap.fromImage(img)
    if scale != 1:
        pm = pm.scaled(w * scale, h * scale, Qt.KeepAspectRatio, Qt.FastTransformation)
    return pm


def square_icon(pm: QPixmap | None, size: int = 32) -> QIcon:
    if pm is None:
        return QIcon()
    return QIcon(pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))


# ---------------------------------------------------------------------------
# Undo: each edit stores the whole file before and after
#
# Item files are a few KB, so a snapshot is cheap, and it makes every edit -- a field,
# an added bonus, a damage line turned into a range -- undo the same way.
# ---------------------------------------------------------------------------

class SnapshotCommand(QUndoCommand):
    def __init__(self, window, label: str, before: bytes, after: bytes):
        super().__init__(label)
        self.window, self.before, self.after = window, before, after
        self.first = True

    def redo(self):
        if self.first:          # the edit has already been made
            self.first = False
            return
        self.window.restore_bytes(self.after)

    def undo(self):
        self.window.restore_bytes(self.before)


# ---------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------

def commit_edit(text: str, on_commit, width: int | None = None,
                placeholder: str = "") -> QLineEdit:
    """A line edit that reports its text once, when the person finishes with it."""
    edit = QLineEdit(text)
    if width:
        edit.setMaximumWidth(width)
    if placeholder:
        edit.setPlaceholderText(placeholder)
    state = {"last": text}

    def done():
        if edit.text() != state["last"]:
            state["last"] = edit.text()
            on_commit(edit.text())
    edit.editingFinished.connect(done)
    return edit


class WrapEdit(QPlainTextEdit):
    """A one-line field shown wrapped over a few lines, for long descriptions. The file
    holds it on one line, so Enter finishes the edit instead of breaking the line."""

    def __init__(self, text: str, on_commit):
        super().__init__(text)
        self._last, self._on_commit = text, on_commit
        self.setTabChangesFocus(True)
        lh = self.fontMetrics().lineSpacing()
        self.setFixedHeight(lh * 4 + 12)

    def _commit(self):
        text = " ".join(self.toPlainText().splitlines())
        if text != self._last:
            self._last = text
            self._on_commit(text)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._commit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self._commit()
        super().focusOutEvent(event)

    def insertFromMimeData(self, source):
        # a pasted paragraph stays one line
        self.insertPlainText(" ".join(source.text().splitlines()))


def note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    f = lab.font()
    f.setPointSizeF(f.pointSizeF() * 0.92)
    lab.setFont(f)
    lab.setStyleSheet("color: palette(dark);")
    return lab


class StatPicker(QDialog):
    """Choose a stat to add as a bonus: search box, list grouped by kind, amount."""

    def __init__(self, parent, choices: list[core.StatChoice]):
        super().__init__(parent)
        self.setWindowTitle("Add a bonus")
        self.choices = choices
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search: strength, fire, sword...")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        form = QFormLayout()
        self.amount = QLineEdit("1")
        self.percent = QCheckBox("The amount is a percentage")
        form.addRow("Amount", self.amount)
        form.addRow("", self.percent)
        lay.addLayout(form)
        lay.addWidget(note("While the item is worn or held, the stat goes up by the "
                           "amount. Use a minus sign for a penalty."))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(420, 520)
        self._filter("")

    def _filter(self, text: str):
        self.list.clear()
        words = text.lower().split()
        group = None
        for ch in self.choices:
            hay = f"{ch.label} {ch.group} {ch.target}".lower()
            if not all(w in hay for w in words):
                continue
            if ch.group != group:
                group = ch.group
                head = QListWidgetItem(group)
                head.setFlags(Qt.NoItemFlags)
                f = head.font()
                f.setBold(True)
                head.setFont(f)
                self.list.addItem(head)
            it = QListWidgetItem("    " + ch.label)
            it.setData(ROLE_REL, ch.target)
            self.list.addItem(it)

    def chosen(self) -> str | None:
        it = self.list.currentItem()
        return it.data(ROLE_REL) if it is not None else None


class NewItemDialog(QDialog):
    def __init__(self, parent, doc: core.ItemDocument):
        super().__init__(parent)
        what = {"magic addition": "new magic", "unique item": "a new unique item"}.get(
            doc.kind, "a new item")
        self.setWindowTitle(f"Make {what} from {doc.name}")
        lay = QVBoxLayout(self)
        lay.addWidget(note(
            f"This makes a copy of <b>{doc.name}</b> under a new name. Change the copy "
            "however you like; the original stays as it is. The copy is not in the game "
            "until something gives it to the player: put it in a chest or shop with the "
            "map editor."))
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("The name the game shows")
        self.file = QLineEdit()
        self.file.setPlaceholderText("The file name (filled in for you)")
        self.name.textChanged.connect(self._follow)
        form.addRow("Name", self.name)
        form.addRow("File name", self.file)
        lay.addLayout(form)
        if doc.is_addition:
            lay.addWidget(note('For magic, the name is what follows "of": "Frost" makes '
                               '"Helmet of Frost".'))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._auto = True
        self.file.textEdited.connect(lambda _t: setattr(self, "_auto", False))

    def _follow(self, text: str):
        if self._auto:
            self.file.setText("".join(ch for ch in text if ch.isalnum() or ch in " '-_"))


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

class ItemWindow(QMainWindow):
    def __init__(self, vanilla: Path, mod: Path | None = None):
        super().__init__()
        self.vanilla = Path(vanilla)
        self.settings = QSettings(ORG, "itemedit")
        if mod is None:
            last = self.settings.value("mod", "")
            mod = Path(last) if last and (Path(last) / "mod.json").is_file() else None
        self.mod: Path | None = None
        self.doc: core.ItemDocument | None = None
        self.preview_base: core.ItemDocument | None = None
        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(lambda _c: self._title())
        self._thumbs: dict[str, QPixmap | None] = {}

        self._build_menus()
        self._build_library()
        self._build_central()
        self.resize(1250, 820)
        self.set_mod(mod, quiet=True)

    # -- setup ----------------------------------------------------------------

    def _build_menus(self):
        bar = self.menuBar()
        f = bar.addMenu("&File")
        for text, slot, key in (("&Choose my mod...", self.choose_mod, None),
                                ("&New mod...", self.new_mod, None),
                                ("&Save into my mod", self.save, QKeySequence.Save),
                                ("&Quit", self.close, QKeySequence.Quit)):
            act = QAction(text, self)
            act.triggered.connect(slot)
            if key is not None:
                act.setShortcut(key)
            f.addAction(act)
        e = bar.addMenu("&Edit")
        u = self.undo.createUndoAction(self, "&Undo")
        u.setShortcut(QKeySequence.Undo)
        r = self.undo.createRedoAction(self, "&Redo")
        r.setShortcut(QKeySequence.Redo)
        e.addAction(u)
        e.addAction(r)

        tb = self.addToolBar("Mod")
        tb.setMovable(False)
        self.mod_label = QLabel()
        self.mod_label.setContentsMargins(6, 0, 12, 0)
        tb.addWidget(self.mod_label)
        for text, slot in (("Choose my mod...", self.choose_mod), ("New mod...", self.new_mod)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            tb.addWidget(b)

    def _build_library(self):
        dock = QDockWidget("Items", self)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        w = QWidget()
        lay = QVBoxLayout(w)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search items...")
        self.search.textChanged.connect(self._fill_library)
        lay.addWidget(self.search)
        self.show_all = QCheckBox("Also show creature weapons and spell effects")
        self.show_all.toggled.connect(self._fill_library)
        lay.addWidget(self.show_all)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(32, 32))
        self.tree.currentItemChanged.connect(self._on_pick)
        lay.addWidget(self.tree, 1)
        dock.setWidget(w)
        dock.setMinimumWidth(280)
        dock.setMaximumWidth(380)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _build_central(self):
        self.stack = QStackedWidget()
        welcome = QLabel(WELCOME)
        welcome.setWordWrap(True)
        welcome.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        welcome.setContentsMargins(24, 24, 24, 24)
        self.stack.addWidget(welcome)

        page = QWidget()
        outer = QVBoxLayout(page)
        # header
        head = QHBoxLayout()
        self.head_icon = QLabel()
        self.head_icon.setFixedSize(72, 72)
        self.head_icon.setAlignment(Qt.AlignCenter)
        head.addWidget(self.head_icon)
        col = QVBoxLayout()
        self.head_name = QLabel()
        f = self.head_name.font()
        f.setPointSize(f.pointSize() + 5)
        f.setBold(True)
        self.head_name.setFont(f)
        self.head_where = note("")
        col.addWidget(self.head_name)
        col.addWidget(self.head_where)
        head.addLayout(col, 1)
        self.new_button = QPushButton("Make a new item from this...")
        self.new_button.clicked.connect(self.make_new)
        self.save_button = QPushButton("Save into my mod")
        self.save_button.clicked.connect(self.save)
        head.addWidget(self.new_button)
        head.addWidget(self.save_button)
        outer.addLayout(head)

        body = QHBoxLayout()
        # cards
        self.cards_area = QScrollArea()
        self.cards_area.setWidgetResizable(True)
        body.addWidget(self.cards_area, 3)
        # preview + problems
        side = QVBoxLayout()
        side.addWidget(QLabel("<b>How the game shows it</b>"))
        self.preview_opts = QHBoxLayout()
        self.step_box = NoScrollComboBox()
        self.step_box.addItems([label for _p, label in core.ROLL_STEPS])
        self.step_box.setCurrentText("Middle")
        self.step_box.currentTextChanged.connect(lambda _t: self.refresh_preview())
        self.base_box = NoScrollComboBox()
        self.base_box.currentIndexChanged.connect(self._on_base_pick)
        self.preview_opts.addWidget(QLabel("Roll"))
        self.preview_opts.addWidget(self.step_box)
        self.preview_opts.addWidget(QLabel("on"))
        self.preview_opts.addWidget(self.base_box, 1)
        self.preview_opts_w = QWidget()
        self.preview_opts_w.setLayout(self.preview_opts)
        side.addWidget(self.preview_opts_w)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.preview.setMinimumHeight(160)
        self.preview.setTextFormat(Qt.RichText)
        self.preview.setStyleSheet(
            "QLabel { background: #1d1a14; color: #d8d0bc; border: 2px solid #6b5a33;"
            " padding: 10px; }")
        side.addWidget(self.preview)
        side.addWidget(note("The preview is close to the game's tooltip, not exact."))
        side.addSpacing(10)
        side.addWidget(QLabel("<b>Problems</b>"))
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        side.addWidget(self.problems)
        side.addSpacing(10)
        side.addWidget(QLabel("<b>Before you test</b>"))
        side.addWidget(note(core.REACH_NOTE))
        side.addStretch(1)
        sidew = QWidget()
        sidew.setLayout(side)
        sidew.setMinimumWidth(300)
        sidew.setMaximumWidth(400)
        body.addWidget(sidew, 2)
        outer.addLayout(body, 1)
        self.stack.addWidget(page)
        self.setCentralWidget(self.stack)

    # -- the mod and the library -------------------------------------------------

    def set_mod(self, mod: Path | None, quiet: bool = False):
        if mod is not None and not (Path(mod) / "mod.json").is_file():
            if not quiet:
                QMessageBox.warning(self, APP, f"{mod} has no mod.json, so it is not a "
                                    "mod folder.")
            mod = None
        self.mod = Path(mod).resolve() if mod else None
        if self.mod:
            self.settings.setValue("mod", str(self.mod))
        try:
            self.lib = Library(self.vanilla, self.mod)
        except SystemExit as e:          # Library reports problems for the command line
            QMessageBox.critical(self, APP, str(e))
            raise
        self.stat_choices = core.stat_choices(self.lib)
        self.entries = core.library_entries(self.lib)
        self._thumbs.clear()
        self.mod_label.setText(f"My mod: <b>{self.mod.name}</b>" if self.mod
                               else "My mod: <i>none yet</i>")
        self._fill_library()
        if self.doc is not None:
            self.open_rel(self.doc.rel, force=True)

    def choose_mod(self):
        if not self._confirm_discard():
            return
        start = str(self.mod.parent) if self.mod else ""
        folder = QFileDialog.getExistingDirectory(self, "Choose your mod folder", start)
        if folder:
            self.set_mod(Path(folder))

    def new_mod(self):
        if not self._confirm_discard():
            return
        name, ok = QInputDialog.getText(self, "New mod", "What is your mod called?")
        if not ok or not name.strip():
            return
        start = str(self.mod.parent) if self.mod else ""
        parent = QFileDialog.getExistingDirectory(
            self, "Choose the folder to make the mod in", start)
        if not parent:
            return
        try:
            mod = core.create_mod(Path(parent), name)
        except core.ItemError as e:
            QMessageBox.warning(self, APP, str(e))
            return
        self.set_mod(mod)
        QMessageBox.information(self, APP, f"Made {mod}. Everything you save goes there.")

    def _thumb(self, rel: str) -> QPixmap | None:
        if rel not in self._thumbs:
            pm = None
            try:
                doc = core.ItemDocument.from_library(self.lib, rel)
                pics = [p for p in core.pictures(doc) if p.slot != "ground"]
                if pics:
                    pm = to_pixmap(core.picture_rgba(self.lib, pics[0].ref))
            except (core.ItemError, ValueError):
                pm = None
            self._thumbs[rel] = pm
        return self._thumbs[rel]

    def _fill_library(self, *_):
        words = self.search.text().lower().split()
        keep = self.doc.rel if self.doc else None
        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        select = None
        for e in self.entries:
            if e.group in core.HIDDEN_GROUPS and not self.show_all.isChecked():
                continue
            hay = f"{e.name} {e.rel} {e.group}".lower()
            if words and not all(w in hay for w in words):
                continue
            g = groups.get(e.group)
            if g is None:
                g = QTreeWidgetItem([e.group])
                g.setFlags(Qt.ItemIsEnabled)
                groups[e.group] = g
                self.tree.addTopLevelItem(g)
            label = e.name + ("   (in my mod)" if e.origin == "mod" else "")
            it = QTreeWidgetItem([label])
            it.setData(0, ROLE_REL, e.rel)
            it.setToolTip(0, e.rel)
            it.setIcon(0, square_icon(self._thumb(e.rel)))
            g.addChild(it)
            if e.rel == keep:
                select = it
        if words:
            self.tree.expandAll()
        self.tree.blockSignals(False)
        if select is not None:
            self.tree.setCurrentItem(select)
            if not words:
                select.parent().setExpanded(True)

    def _on_pick(self, cur, _prev):
        if cur is None:
            return
        rel = cur.data(0, ROLE_REL)
        if rel and (self.doc is None or rel != self.doc.rel):
            self.open_rel(rel)

    # -- opening -----------------------------------------------------------------

    def _confirm_discard(self) -> bool:
        if self.doc is None or not self.doc.dirty:
            return True
        r = QMessageBox.question(
            self, APP, f"{self.doc.name} has changes that are not saved. Save them into "
            "your mod first?", QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def open_rel(self, rel: str, force: bool = False):
        if not force and not self._confirm_discard():
            self._fill_library()
            return
        try:
            doc = core.ItemDocument.from_library(self.lib, rel)
        except (core.ItemError, ValueError) as e:
            QMessageBox.warning(self, APP, f"Cannot open {rel}: {e}")
            return
        self.set_document(doc)

    def set_document(self, doc: core.ItemDocument):
        self.doc = doc
        self.undo.clear()
        self.undo.setClean()
        self._fill_bases()
        self.stack.setCurrentIndex(1)
        self.rebuild()

    def _fill_bases(self):
        """For magic, the plain items it can appear on, to show the preview on."""
        self.base_box.blockSignals(True)
        self.base_box.clear()
        self.preview_base = None
        if self.doc is not None and self.doc.is_addition:
            group = (self.doc.root.get("Inventory Addition Group") or "").lower()
            bases = []
            for e in self.entries:
                if e.is_addition or e.group != "Items":
                    continue
                raw = self.lib.read(e.rel)
                if group and group.encode("latin-1") in raw.lower():
                    bases.append(e)
            if not bases:
                bases = [e for e in self.entries if e.group == "Items"]
            for e in bases:
                self.base_box.addItem(e.name, e.rel)
            self._on_base_pick(0)
        self.base_box.blockSignals(False)

    def _on_base_pick(self, index: int):
        rel = self.base_box.itemData(index) if index >= 0 else None
        self.preview_base = core.ItemDocument.from_library(self.lib, rel) if rel else None
        self.refresh_preview()

    # -- edits -----------------------------------------------------------------------

    def edit(self, label: str, fn, rebuild: bool = False) -> bool:
        """Make one edit as one undo step. `fn` mutates the document; ItemError from it
        is shown and the document is left as it was."""
        before = self.doc.to_bytes()
        try:
            fn()
        except core.ItemError as e:
            self.restore_bytes(before)
            QMessageBox.warning(self, APP, str(e))
            return False
        after = self.doc.to_bytes()
        if after != before:
            self.undo.push(SnapshotCommand(self, label, before, after))
        if rebuild:
            self.rebuild()
        else:
            self.refresh_preview()
        self._title()
        return True

    def restore_bytes(self, data: bytes):
        self.doc.root = parse_resource_text(data.decode("latin-1"))
        self.rebuild()

    # -- drawing -------------------------------------------------------------------

    def _title(self):
        if self.doc is None:
            self.setWindowTitle(APP)
            return
        star = " *" if self.doc.dirty else ""
        self.setWindowTitle(f"{self.doc.name}{star} - {APP}")

    def rebuild(self):
        doc = self.doc
        if doc is None:
            return
        pics = [p for p in core.pictures(doc) if p.slot != "ground"]
        pm = to_pixmap(core.picture_rgba(self.lib, pics[0].ref)) if pics else None
        self.head_icon.setPixmap(pm.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                                 if pm else QPixmap())
        self.head_name.setText(core.tooltip(doc).title if not doc.is_addition
                               else f"... of {doc.root.get('Display Name') or ''}")
        where = {"vanilla": "From the game. Saving puts your version in your mod.",
                 "mod": f"In your mod: {doc.rel}",
                 "new": "New, not saved yet. Save puts it in your mod."}[doc.origin]
        self.head_where.setText(f"{doc.kind.capitalize()}. {where}")
        self.preview_opts_w.setVisible(doc.is_addition)

        cards = QWidget()
        lay = QVBoxLayout(cards)
        lay.addWidget(self._basics_card())
        if doc.is_addition:
            lay.addWidget(self._roll_card())
        dmg = self._damage_card()
        if dmg is not None:
            lay.addWidget(dmg)
        lay.addWidget(self._bonus_card())
        lay.addWidget(self._art_card())
        lay.addStretch(1)
        old = self.cards_area.takeWidget()
        if old is not None:
            old.deleteLater()
        self.cards_area.setWidget(cards)
        self.refresh_preview()
        self._title()

    def refresh_preview(self):
        doc = self.doc
        if doc is None:
            return
        t = core.tooltip(doc, self.preview_base, self.step_box.currentText())
        esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        html = f"<p style='color:#e8c46a; font-size:14pt'><b>{esc(t.title)}</b></p>"
        html += "".join(f"<p>{esc(line)}</p>" for line in t.lines)
        if t.price:
            html += f"<p style='color:#bba76d'>Value: {esc(t.price)} gold</p>"
        self.preview.setText(html)
        probs = core.problems(doc)
        self.problems.setText("<br>".join("&bull; " + esc(p) for p in probs) if probs
                              else "<span style='color:palette(mid)'>None found.</span>")

    # -- cards -----------------------------------------------------------------------

    def _basics_card(self) -> QGroupBox:
        doc = self.doc
        box = QGroupBox("Basics")
        form = QFormLayout(box)
        for f in core.basic_fields(doc):
            if doc.root.get(f.key) is None:
                continue

            def commit(text, f=f):
                self.edit(f"Change {f.label.lower()}",
                          lambda: core.set_field(doc, f.key, text, f.numeric, f.label))
                if f.key == "Display Name":
                    self.rebuild()
            value = core.get_field(doc, f.key)
            form.addRow(f.label, WrapEdit(value, commit) if f.key == "Description"
                        else commit_edit(value, commit))
            if f.help:
                form.addRow("", note(f.help))
        if not doc.is_addition and core.price_node(doc) is not None:
            form.addRow("Price (gold)", commit_edit(
                core.get_price(doc), lambda t: self.edit("Change price",
                                                          lambda: core.set_price(doc, t)),
                width=120))
        if doc.is_addition and doc.root.get("Addition Rarity") is not None:
            box_r = NoScrollComboBox()
            rarities = [n for n in self.lib.names()
                        if n.lower().startswith("resources/inventory item rarity/")]
            current = doc.root.get("Addition Rarity") or ""
            for n in rarities:
                ref = n.split("/", 1)[1].split(".", 1)[0]
                box_r.addItem(ref.rsplit("/", 1)[-1], ref)
                if ref.lower() == current.lower():
                    box_r.setCurrentIndex(box_r.count() - 1)

            def pick(_i):
                ref = box_r.currentData()
                self.edit("Change rarity", lambda: doc.root.set("Addition Rarity", ref))
            box_r.currentIndexChanged.connect(pick)
            form.addRow("How rare", box_r)
            form.addRow("", note("How often this magic is picked when the game rolls a "
                                 "random magic item."))
        return box

    def _roll_card(self) -> QGroupBox:
        doc = self.doc
        box = QGroupBox("How strong it rolls")
        grid = QGridLayout(box)
        grid.addWidget(note("When the game makes this magic it rolls an amount between "
                            "weakest and strongest. The amounts in the description and "
                            "bonuses come from it, and the item's price goes up by the "
                            "magic's price at that roll."), 0, 0, 1, 3)
        grid.addWidget(QLabel("<b>Roll</b>"), 1, 1)
        grid.addWidget(QLabel("<b>Adds to price</b>"), 1, 2)
        for row, step in enumerate(core.roll(doc), start=2):
            grid.addWidget(QLabel(step.label), row, 0)
            for col, which, val in ((1, "amount", step.amount), (2, "price", step.price)):
                grid.addWidget(commit_edit(val, lambda t, s=step, w=which: self.edit(
                    f"Change {s.label.lower()} {w}",
                    lambda: core.set_roll(doc, s.prefix, w, t)), width=110), row, col)
        return box

    def _damage_card(self) -> QGroupBox | None:
        doc = self.doc
        lines = core.damage_lines(doc)
        if not lines:
            return None
        box = QGroupBox("Damage")
        grid = QGridLayout(box)
        grid.setColumnStretch(4, 1)
        first = lines[0].behaviour
        for row, line in enumerate(lines):
            if line.behaviour is not first and (row == 0 or lines[row - 1].behaviour is first):
                grid.addWidget(note("Extra damage, dealt only in some cases (for example "
                                    "against the undead):"), 2 * row, 0, 1, 5)
            r = 2 * row + 1
            if line.kind == "over time":
                grid.addWidget(QLabel(f"{line.damage_type} damage over time"), r, 0, 1, 5)
                continue
            types = NoScrollComboBox()
            types.addItems(core.DAMAGE_TYPES)
            types.setCurrentText(line.damage_type)
            types.currentTextChanged.connect(
                lambda t, ln=line: self.edit("Change damage type",
                                             lambda: core.set_damage_type(ln, t)))
            grid.addWidget(types, r, 0)
            if line.editable:
                lo, hi = core.damage_range(line)
                state = {"lo": lo, "hi": hi}

                def commit(which, text, ln=line, st=state):
                    st[which] = text
                    self.edit("Change damage", lambda: core.set_damage_range(
                        ln, st["lo"], st["hi"]), rebuild=ln.kind == "fixed")
                grid.addWidget(commit_edit(lo, lambda t, c=commit: c("lo", t), width=70), r, 1)
                grid.addWidget(QLabel("to"), r, 2)
                grid.addWidget(commit_edit(hi, lambda t, c=commit: c("hi", t), width=70), r, 3)
                if line.kind == "ladder":
                    span = core.ladder_span(line)
                    extra = (f"plus up to {core.fmt_number(span[1])} more with skill and "
                             "weapon specialization") if span else "plus a skill bonus"
                    grid.addWidget(note(extra), r, 4)
            elif line.kind == "rolled":
                grid.addWidget(note("set by the roll: change it on the roll card"), r, 1, 1, 4)
            else:
                grid.addWidget(note("a formula this editor leaves as it is"), r, 1, 1, 4)
        return box

    def _bonus_card(self) -> QGroupBox:
        doc = self.doc
        box = QGroupBox("Bonuses while worn or held")
        grid = QGridLayout(box)
        lines = core.stat_lines(doc)
        if not lines:
            grid.addWidget(note("None."), 0, 0, 1, 4)
        for row, st in enumerate(lines):
            combo = NoScrollComboBox()
            same = [ch for ch in self.stat_choices if core.stat_kind_for(ch.target)
                    == st.node.type_name]
            known = False
            for ch in same:
                combo.addItem(ch.label, ch.target)
                if ch.target.lower() == st.target.lower():
                    combo.setCurrentIndex(combo.count() - 1)
                    known = True
            if not known:
                combo.insertItem(0, st.label, st.target)
                combo.setCurrentIndex(0)
            combo.currentIndexChanged.connect(
                lambda _i, s=st, cb=combo: self.edit(
                    "Change bonus stat", lambda: core.set_stat_target(s, cb.currentData())))
            grid.addWidget(combo, row, 0)
            if st.amount_kind == "fixed":
                grid.addWidget(commit_edit(core.get_stat_amount(st), lambda t, s=st: self.edit(
                    "Change bonus", lambda: core.set_stat_amount(s, t)), width=80), row, 1)
            elif st.amount_kind == "rolled":
                grid.addWidget(note("set by the roll"), row, 1)
            else:
                grid.addWidget(note("a formula"), row, 1)
            pct = QCheckBox("%")
            pct.setToolTip("The amount is a percentage")
            pct.setChecked(st.percent)
            pct.toggled.connect(lambda on, s=st: self.edit(
                "Change percent", lambda: core.set_stat_percent(s, on)))
            grid.addWidget(pct, row, 2)
            rm = QPushButton("Remove")
            rm.clicked.connect(lambda _c=False, s=st: self.edit(
                f"Remove {s.label}", lambda: core.remove_stat(doc, s), rebuild=True))
            grid.addWidget(rm, row, 3)
        add = QPushButton("Add a bonus...")
        add.clicked.connect(self.add_bonus)
        grid.addWidget(add, len(lines) + 1, 0)
        if doc.is_addition:
            grid.addWidget(note("A bonus added here is a fixed amount. Bonuses set by the "
                                "roll come from the roll card."), len(lines) + 2, 0, 1, 4)
        return box

    def _art_card(self) -> QGroupBox:
        doc = self.doc
        box = QGroupBox("Art")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        pics = core.pictures(doc)
        names = {"inventory": "In the inventory", "Basic": "Plain", "Better": "Better",
                 "Special": "Best", "ground": "On the ground"}
        if not pics:
            lay.addWidget(note("This has no pictures of its own. Magic shows the picture "
                               "of the item it is on, unless it brings its own."))
        for p in pics:
            col = QVBoxLayout()
            img = QLabel()
            pm = to_pixmap(core.picture_rgba(self.lib, p.ref))
            if pm is not None:
                # big enough to judge, never taller than the card
                k = max(1, min(3, 110 // max(1, pm.height()), 110 // max(1, pm.width())))
                img.setPixmap(pm.scaled(pm.width() * k, pm.height() * k, Qt.KeepAspectRatio,
                                        Qt.FastTransformation))
            else:
                img.setText("(missing)")
            img.setAlignment(Qt.AlignCenter)
            img.setMinimumSize(64, 64)
            img.setStyleSheet("background: #2a2620; padding: 4px;")
            col.addWidget(img)
            cap = QLabel(names.get(p.slot, p.slot))
            cap.setAlignment(Qt.AlignCenter)
            col.addWidget(cap)
            row.addLayout(col)
        row.addStretch(1)
        lay.addLayout(row)
        if pics:
            buttons = QHBoxLayout()
            ex = QPushButton("Export pictures to paint...")
            ex.clicked.connect(self.export_art)
            im = QPushButton("Import a painted picture...")
            im.clicked.connect(self.import_art)
            buttons.addWidget(ex)
            buttons.addWidget(im)
            buttons.addStretch(1)
            lay.addLayout(buttons)
            lay.addWidget(note("Export saves each picture as a PNG at four times its size. "
                               "Paint over it in any program, keep the size, and import "
                               "it. A picture other items share is copied first, so only "
                               "this item changes."))
        return box

    # -- actions -----------------------------------------------------------------------

    def add_bonus(self):
        dlg = StatPicker(self, self.stat_choices)
        if dlg.exec() != QDialog.Accepted or not dlg.chosen():
            return
        target, amount, pct = dlg.chosen(), dlg.amount.text(), dlg.percent.isChecked()
        self.edit(f"Add {core.readable(target)}",
                  lambda: core.add_stat(self.doc, target, amount, pct), rebuild=True)

    def make_new(self):
        if self.doc is None or not self._confirm_discard():
            return
        dlg = NewItemDialog(self, self.doc)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            new = core.clone(self.doc, self.lib, dlg.file.text(), dlg.name.text())
        except core.ItemError as e:
            QMessageBox.warning(self, APP, str(e))
            return
        self.set_document(new)

    def _need_mod(self) -> bool:
        if self.mod is not None:
            return True
        r = QMessageBox.question(
            self, APP, "You have not picked a mod yet. Your changes are saved into a mod "
            "folder.\n\nMake a new mod now? (No lets you pick an existing one.)",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if r == QMessageBox.Yes:
            self.new_mod()
        elif r == QMessageBox.No:
            self.choose_mod()
        return self.mod is not None

    def save(self) -> bool:
        doc = self.doc
        if doc is None:
            return True
        if not doc.dirty:
            return True
        if not self._need_mod():
            return False
        probs = core.problems(doc)
        if probs:
            r = QMessageBox.warning(
                self, APP, "There are problems:\n\n" + "\n".join("- " + p for p in probs)
                + "\n\nSave anyway?", QMessageBox.Save | QMessageBox.Cancel)
            if r != QMessageBox.Save:
                return False
        if doc.origin == "vanilla":
            r = QMessageBox.question(
                self, APP, f"This puts your version of {doc.name} in your mod. With the "
                "mod installed, every one of them in the game changes.\n\n"
                + core.REACH_NOTE, QMessageBox.Save | QMessageBox.Cancel)
            if r != QMessageBox.Save:
                return False
        try:
            target = doc.save_into_mod(self.mod)
        except (core.ItemError, OSError, ValueError) as e:
            QMessageBox.critical(self, APP, f"Not saved: {e}")
            return False
        self.undo.setClean()
        self.statusBar().showMessage(f"Saved {target}", 8000)
        rel = doc.rel
        self.doc = None                     # the library is reread; reopen from the mod
        self.set_mod(self.mod)
        self.open_rel(rel, force=True)
        return True

    def _run_icontool(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = icontool.main(["--vanilla", str(self.vanilla), *argv])
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                if e.code and not isinstance(e.code, int):
                    print(e.code)
        return code or 0, out.getvalue()

    def export_art(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose where to put the PNGs")
        if not folder:
            return
        args = ["export", self.doc.ref, "--out", folder]
        if self.mod is not None:
            args += ["--mod", str(self.mod)]
        if self.doc.origin == "new":
            QMessageBox.information(self, APP, "Save the new item first, then export its "
                                    "pictures.")
            return
        code, text = self._run_icontool(*args)
        QMessageBox.information(self, APP, text.strip() or "Done.")
        if code == 0:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def import_art(self):
        doc = self.doc
        if doc.dirty:
            QMessageBox.information(self, APP, "Save your other changes first, then import "
                                    "the picture.")
            return
        if not self._need_mod():
            return
        pics = core.pictures(doc)
        names = {"inventory": "In the inventory", "Basic": "Plain", "Better": "Better",
                 "Special": "Best", "ground": "On the ground"}
        slot = pics[0].slot
        if len(pics) > 1:
            labels = [names.get(p.slot, p.slot) for p in pics]
            label, ok = QInputDialog.getItem(self, APP, "Which picture is it?", labels, 0,
                                             False)
            if not ok:
                return
            slot = pics[labels.index(label)].slot
        png, _ = QFileDialog.getOpenFileName(self, "Choose the painted PNG", "",
                                             "PNG pictures (*.png)")
        if not png:
            return
        base = ["import", doc.ref, png, "--mod", str(self.mod), "--art", slot]
        code, text = self._run_icontool(*base, "--dry-run")
        if code != 0:
            QMessageBox.warning(self, APP, text.strip())
            return
        r = QMessageBox.question(self, APP, text.strip() + "\n\nGo ahead?",
                                 QMessageBox.Yes | QMessageBox.Cancel)
        if r != QMessageBox.Yes:
            return
        code, text = self._run_icontool(*base)
        (QMessageBox.information if code == 0 else QMessageBox.warning)(
            self, APP, text.strip() or "Done.")
        rel = doc.rel
        self.doc = None
        self.set_mod(self.mod)
        self.open_rel(rel, force=True)

    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mod", type=Path, help="your mod folder")
    ap.add_argument("--vanilla", type=Path, default=default_vanilla_dat(),
                    help="the game's data.dat.vanilla.bak")
    ap.add_argument("--open", help="an item to open, by its file path")
    args = ap.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP)
    if not args.vanilla.is_file():
        QMessageBox.critical(None, APP, f"Cannot find the game's original files at "
                             f"{args.vanilla}.\nRun modmanager.py init once, or pass "
                             "--vanilla.")
        return 1
    win = ItemWindow(args.vanilla, args.mod)
    if args.open:
        win.open_rel(args.open)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
