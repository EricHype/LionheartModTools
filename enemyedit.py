"""The enemy editor: change a creature, change its model, watch its animations, make new ones.

The item editor's sibling, built the same way: a list of every creature on the left, a 3D
preview that plays the model's animations, and cards for what a person changes. Everything
saves into your own mod; the game's files are never written.

    python enemyedit.py [--mod <mod folder>] [--game <Lionheart folder>]

The core is `enemyedit_core` (byte-identical load and save of every creature and race), the
preview is `enemyedit_preview`, and the startup, error box and engine checks are shared
with `itemedit`.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QDockWidget, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSplitter,
    QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import enemyedit_core as core
import itemedit
import itemedit_core as icore
from enemyedit_preview import ModelCache, PreviewWidget, ViewerServer
from icontool import Library
from itemedit import StatPicker, commit_edit, engine_check, note
from qtwidgets import NoScrollComboBox
from resource_format import parse_resource_text

APP = "Lionheart Enemy Editor"
ORG = "LionheartModTools"
ROLE = Qt.UserRole + 1

WELCOME = """<h2>Lionheart enemy editor</h2>
<p>Pick a creature in the list on the left. You can:</p>
<ol>
<li><b>Change it.</b> Edit its name, level and stats on the cards. Press <b>Save into my
mod</b> when you're done.</li>
<li><b>Change its model.</b> On the Model card, pick another creature's look. The editor
refuses a model that is missing an animation this creature needs.</li>
<li><b>Watch its animations.</b> The preview plays every animation its model has. Drag to
turn it, scroll to zoom.</li>
<li><b>Make a new enemy from it.</b> Press <b>Make a new enemy from this</b>. The copy gets
its own stats, so changing it never changes the original.</li>
</ol>
<p><b>Tough</b> and <b>Super</b> are separate creatures. The game picks them for stronger
parties, so changing a Gray Wolf does not change a Gray Wolf Tough.</p>
"""


# ---------------------------------------------------------------------------
# Undo: snapshot both files
# ---------------------------------------------------------------------------

def snapshot(c: core.Creature):
    def one(d):
        return None if d is None else (d.rel, d._raw, d.origin, d.to_bytes())
    return one(c.can), one(c.race)


def restore(c: core.Creature, snap):
    def one(s):
        if s is None:
            return None
        rel, raw, origin, now = s
        d = core.ResDoc(rel, raw, origin)
        d.root = parse_resource_text(now.decode("latin-1"))
        return d
    c.can, c.race = one(snap[0]), one(snap[1])


class SnapshotCommand(QUndoCommand):
    def __init__(self, window, label, before, after):
        super().__init__(label)
        self.window, self.before, self.after, self.first = window, before, after, True

    def redo(self):
        if self.first:
            self.first = False
            return
        self.window.restore_snapshot(self.after)

    def undo(self):
        self.window.restore_snapshot(self.before)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

class ModelPicker(QDialog):
    """Choose another model. Each row says whether it has every animation this creature
    calls, and picking one previews it."""

    def __init__(self, parent, creature: core.Creature, models: list[str],
                 users: dict[str, int], preview: PreviewWidget):
        super().__init__(parent)
        self.setWindowTitle("Change the model")
        self.creature, self.preview = creature, preview
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search: wolf, skeleton, goblin...")
        lay.addWidget(self.search)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        lay.addWidget(self.verdict)
        lay.addWidget(note("Only models that can do everything this creature does can be "
                           "chosen. The preview behind this window shows the one you pick."))
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.models, self.users = models, users
        self.search.textChanged.connect(self._fill)
        self.list.currentItemChanged.connect(self._pick)
        self.resize(460, 560)
        self._fill("")

    def _fill(self, text):
        self.list.clear()
        words = text.lower().split()
        for m in self.models:
            if words and not all(w in m.lower() for w in words):
                continue
            n = self.users.get(m.lower(), 0)
            label = m.split("/", 1)[-1] + (f"   (used by {n})" if n else "")
            it = QListWidgetItem(label)
            it.setData(ROLE, m)
            if m.lower() == self.creature.model.lower():
                it.setText(label + "   [current]")
            self.list.addItem(it)

    def _pick(self, cur, _prev):
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        if cur is None:
            return
        ref = cur.data(ROLE)
        missing = self.creature.missing_sequences(ref)
        info = core.model_info(self.creature.lib, ref)
        if not info.mesh:
            self.verdict.setText("This model has no mesh the editor can find.")
            return
        if missing:
            self.verdict.setText(f"<b>Can't use:</b> it has no animation for "
                                 f"{', '.join(missing)}, which this creature needs.")
        else:
            self.verdict.setText(f"OK: it has every animation this creature uses "
                                 f"({len(info.clips)} in all).")
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        self.preview.show_model(ref)

    def chosen(self) -> str | None:
        it = self.list.currentItem()
        return it.data(ROLE) if it is not None else None


class NewEnemyDialog(QDialog):
    def __init__(self, parent, c: core.Creature):
        super().__init__(parent)
        self.setWindowTitle(f"Make a new enemy from {c.name}")
        lay = QVBoxLayout(self)
        lay.addWidget(note(f"This copies <b>{c.name}</b> and its stats under a new name. The "
                           "copy has its own stats, so changing it never changes the "
                           "original. It isn't in the game until something spawns it."))
        form = QFormLayout()
        self.name, self.file = QLineEdit(), QLineEdit()
        self.name.setPlaceholderText("The name the combat log shows")
        self.file.setPlaceholderText("The file name (filled in for you)")
        self._auto = True
        self.name.textChanged.connect(lambda t: self._auto and self.file.setText(
            "".join(ch for ch in t if ch.isalnum() or ch in " '-_")))
        self.file.textEdited.connect(lambda _t: setattr(self, "_auto", False))
        form.addRow("Name", self.name)
        form.addRow("File name", self.file)
        lay.addLayout(form)
        b = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        b.accepted.connect(self.accept)
        b.rejected.connect(self.reject)
        lay.addWidget(b)


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

class EnemyWindow(QMainWindow):
    def __init__(self, vanilla: Path, mod: Path | None = None, game: Path | None = None):
        super().__init__()
        self.vanilla = Path(vanilla)
        self.game = Path(game) if game else self.vanilla.parent
        self.settings = QSettings(ORG, "enemyedit")
        if mod is None:
            last = self.settings.value("mod", "") or QSettings(ORG, "itemedit").value("mod", "")
            mod = Path(last) if last and (Path(last) / "mod.json").is_file() else None
        self.mod: Path | None = None
        self.creature: core.Creature | None = None
        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(self._clean_changed)
        self.server = None
        self._build_menus()
        self._build_library()
        self._build_central()
        self.resize(1300, 860)
        self.set_mod(mod)

    # -- setup ------------------------------------------------------------------

    def _build_menus(self):
        f = self.menuBar().addMenu("&File")
        for text, slot, key in (("&Choose my mod...", self.choose_mod, None),
                                ("&New mod...", self.new_mod, None),
                                ("&Save into my mod", self.save, QKeySequence.Save),
                                ("&Quit", self.close, QKeySequence.Quit)):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if key is not None:
                a.setShortcut(key)
            f.addAction(a)
        e = self.menuBar().addMenu("&Edit")
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
        dock = QDockWidget("Creatures", self)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        w = QWidget()
        lay = QVBoxLayout(w)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search creatures...")
        self.search.textChanged.connect(self._fill_library)
        lay.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._on_pick)
        lay.addWidget(self.tree, 1)
        dock.setWidget(w)
        dock.setMinimumWidth(260)
        dock.setMaximumWidth(360)
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
        head = QHBoxLayout()
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
        self.new_button = QPushButton("Make a new enemy from this...")
        self.new_button.clicked.connect(self.make_new)
        self.save_button = QPushButton("Save into my mod")
        self.save_button.clicked.connect(self.save)
        head.addWidget(self.new_button)
        head.addWidget(self.save_button)
        outer.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.preview_holder = QVBoxLayout()
        lv.addLayout(self.preview_holder, 1)
        lv.addWidget(QLabel("<b>Problems</b>"))
        self.problems = QLabel()
        self.problems.setTextFormat(Qt.RichText)
        self.problems.setWordWrap(True)
        lv.addWidget(self.problems)
        lv.addWidget(note(core.REACH_NOTE))
        split.addWidget(left)
        self.cards_area = QScrollArea()
        self.cards_area.setWidgetResizable(True)
        split.addWidget(self.cards_area)
        split.setSizes([620, 560])
        outer.addWidget(split, 1)
        self.stack.addWidget(page)
        self.setCentralWidget(self.stack)

    # -- mod and library ------------------------------------------------------------

    def set_mod(self, mod: Path | None):
        if mod is not None and not (Path(mod) / "mod.json").is_file():
            mod = None
        self.mod = Path(mod).resolve() if mod else None
        if self.mod:
            self.settings.setValue("mod", str(self.mod))
        self.lib = Library(self.vanilla, self.mod)
        self.models = core.creature_models(self.lib)
        self.races = core.race_index(self.lib)
        self.entries = self._entries()
        self.model_users = {}
        for e in self.entries:
            self.model_users[e["model"].lower()] = self.model_users.get(e["model"].lower(), 0) + 1
        self.stat_choices = [c for c in icore.stat_choices(self.lib)
                             if c.group in ("Resistances and damage", "Other stats", "Skills")]
        self.mod_label.setText(f"My mod: <b>{self.mod.name}</b>" if self.mod
                               else "My mod: <i>none yet</i>")
        if not hasattr(self, "preview"):
            self.cache = ModelCache(self.lib)
            self.server = ViewerServer(self.cache.root)
            self.preview = PreviewWidget(self.cache, self.server)
            self.preview_holder.addWidget(self.preview)
        else:
            self.cache.lib = self.lib
        self._fill_library()
        if self.creature is not None:
            self.open_rel(self.creature.can.rel, force=True)

    def _entries(self) -> list[dict]:
        out = []
        for n in core.creature_cans(self.lib):
            raw = self.lib.read(n)
            m = re.search(rb"User Assigned Name=([^\r\n]*)", raw)
            mm = re.search(rb"\n\t\tModel=([^\r\n]+)", raw)
            stem = n.rsplit("/", 1)[-1][:-4]
            name = (m.group(1).decode("latin-1").strip() if m else "") or stem
            tier = core.tier_of(stem)
            parts = n[len(core.CAN_ROOT):].split("/")
            group = parts[0] if len(parts) > 1 else "Other"
            out.append({"rel": n, "name": name + (f" ({tier})" if tier else ""), "stem": stem,
                        "group": group, "origin": self.lib.where(n),
                        "model": mm.group(1).decode().strip() if mm else ""})
        out.sort(key=lambda e: (e["group"].lower(), e["name"].lower()))
        return out

    def _fill_library(self, *_):
        words = self.search.text().lower().split()
        keep = self.creature.can.rel if self.creature else None
        self.tree.blockSignals(True)
        self.tree.clear()
        groups, select = {}, None
        for e in self.entries:
            hay = f"{e['name']} {e['stem']} {e['group']}".lower()
            if words and not all(w in hay for w in words):
                continue
            g = groups.get(e["group"])
            if g is None:
                g = QTreeWidgetItem([e["group"]])
                g.setFlags(Qt.ItemIsEnabled)
                groups[e["group"]] = g
                self.tree.addTopLevelItem(g)
            it = QTreeWidgetItem([e["name"] + ("   (in my mod)" if e["origin"] == "mod" else "")])
            it.setData(0, ROLE, e["rel"])
            it.setToolTip(0, e["rel"])
            g.addChild(it)
            if e["rel"] == keep:
                select = it
        if words:
            self.tree.expandAll()
        self.tree.blockSignals(False)
        if select is not None:
            self.tree.setCurrentItem(select)
            select.parent().setExpanded(True)

    def _on_pick(self, cur, _prev):
        rel = cur.data(0, ROLE) if cur is not None else None
        if rel and (self.creature is None or rel != self.creature.can.rel):
            self.open_rel(rel)

    def choose_mod(self):
        if not self._confirm_discard():
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose your mod folder",
                                                  str(self.mod.parent) if self.mod else "")
        if folder:
            if not (Path(folder) / "mod.json").is_file():
                QMessageBox.warning(self, APP, f"{folder} has no mod.json, so it is not a mod "
                                    "folder.")
                return
            self.set_mod(Path(folder))

    def new_mod(self):
        if not self._confirm_discard():
            return
        name, ok = QInputDialog.getText(self, "New mod", "What is your mod called?")
        if not ok or not name.strip():
            return
        parent = QFileDialog.getExistingDirectory(self, "Choose the folder to make the mod in")
        if not parent:
            return
        try:
            self.set_mod(icore.create_mod(Path(parent), name))
        except icore.ItemError as e:
            QMessageBox.warning(self, APP, str(e))

    # -- opening and editing --------------------------------------------------------

    def _confirm_discard(self) -> bool:
        if self.creature is None or not self.creature.dirty:
            return True
        r = QMessageBox.question(self, APP, f"{self.creature.name} has changes that are not "
                                 "saved. Save them into your mod first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def open_rel(self, rel: str, force: bool = False):
        if not force and not self._confirm_discard():
            self._fill_library()
            return
        try:
            c = core.Creature.from_library(self.lib, rel)
        except (icore.ItemError, ValueError, OSError) as e:
            QMessageBox.warning(self, APP, f"Cannot open {rel}: {e}")
            return
        self.set_creature(c)

    def set_creature(self, c: core.Creature):
        self.creature = c
        self.undo.clear()
        self.stack.setCurrentIndex(1)
        self.rebuild()
        self.preview.show_model(c.model)

    def edit(self, label: str, fn, rebuild: bool = False) -> bool:
        before = snapshot(self.creature)
        try:
            fn()
        except icore.ItemError as e:
            restore(self.creature, before)
            QMessageBox.warning(self, APP, str(e))
            self.rebuild()
            return False
        after = snapshot(self.creature)
        if after != before:
            self.undo.push(SnapshotCommand(self, label, before, after))
        if rebuild:
            self.rebuild()
        else:
            self.refresh()
        return True

    def restore_snapshot(self, snap):
        model = self.creature.model
        restore(self.creature, snap)
        self.rebuild()
        if self.creature.model != model:
            self.preview.show_model(self.creature.model)

    def _shared_race_ok(self) -> bool:
        """Before a stat edit on a shared race, offer the creature its own copy."""
        c = self.creature
        if c.race is None or c.race.origin == "new":
            return True
        others = [u for u in c.race_users(self.races) if u.lower() != c.can.rel.lower()]
        if not others or getattr(self, "_shared_ok_for", None) == c.race.rel:
            return True
        names = ", ".join(u.rsplit("/", 1)[-1][:-4] for u in others[:6])
        r = QMessageBox.question(
            self, APP, f"{c.name}'s stats are shared with: {names}.\n\nGive {c.name} its own "
            "copy first, so only it changes? (No changes them all.)",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Yes:
            self.edit("Give it its own stats", c.fork_race, rebuild=True)
        else:
            self._shared_ok_for = c.race.rel
        return True

    def stat_edit(self, label: str, fn) -> bool:
        if not self._shared_race_ok():
            self.rebuild()
            return False
        return self.edit(label, fn, rebuild=True)

    # -- drawing ----------------------------------------------------------------------

    def _clean_changed(self, _clean):
        try:
            self._title()
        except RuntimeError:            # the stack can signal while the window is torn down
            pass

    def _title(self):
        c = self.creature
        self.setWindowTitle(f"{c.name}{' *' if c.dirty else ''} - {APP}" if c else APP)

    def rebuild(self):
        c = self.creature
        if c is None:
            return
        tier = core.tier_of(c.stem)
        self.head_name.setText(c.name + (f"  ({tier})" if tier else ""))
        where = {"vanilla": "From the game. Saving puts your version in your mod.",
                 "mod": f"In your mod: {c.can.rel}",
                 "new": "New, not saved yet. Save puts it in your mod."}[c.can.origin]
        self.head_where.setText(where)
        cards = QWidget()
        lay = QVBoxLayout(cards)
        lay.addWidget(self._basics_card())
        lay.addWidget(self._stats_card())
        lay.addWidget(self._model_card())
        lay.addStretch(1)
        old = self.cards_area.takeWidget()
        if old is not None:
            old.deleteLater()
        self.cards_area.setWidget(cards)
        self.refresh()

    def refresh(self):
        c = self.creature
        esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        probs = c.problems()
        self.problems.setText("<br>".join("&bull; " + esc(p) for p in probs) if probs
                              else "<span style='color:palette(dark)'>None found.</span>")
        self._title()

    # -- cards ------------------------------------------------------------------------

    def _basics_card(self) -> QGroupBox:
        c = self.creature
        box = QGroupBox("Basics")
        form = QFormLayout(box)

        def set_name(t):
            self.edit("Change name", lambda: c.set_name(t))
            self.head_name.setText(c.name)
        form.addRow("Name", commit_edit(c.name, set_name))
        form.addRow("", note("The name the combat log uses."))
        form.addRow("Level", commit_edit(c.level, lambda t: self.edit(
            "Change level", lambda: c.set_level(t)), width=80))
        form.addRow("Kind", QLabel(", ".join(c.categories) or "(none)"))
        awards = [n.get("Experience Points To Add") for n in c.death_xp_awards()]
        xp = c.experience or "?"
        form.addRow("Experience", QLabel(f"{xp}" + (f"  (its death action also names "
                                                     f"{', '.join(a for a in awards if a)})"
                                                     if awards else "")))
        form.addRow("", note("Which of these numbers a kill pays hasn't been checked in the "
                             "game yet, so experience can't be changed here for now."))
        return box

    def _stats_card(self) -> QGroupBox:
        c = self.creature
        box = QGroupBox("Stats")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        row = 0
        if c.race is None:
            grid.addWidget(note(f"Its race ({c.race_ref}) is missing, so its stats can't be "
                                "edited."), 0, 0, 1, 3)
            return box
        others = [u for u in c.race_users(self.races) if u.lower() != c.can.rel.lower()]
        if others and c.race.origin != "new":
            names = ", ".join(u.rsplit("/", 1)[-1][:-4] for u in others[:6])
            grid.addWidget(note(f"Shared with: {names}. Changing these changes them too."),
                           row, 0, 1, 2)
            own = QPushButton("Give it its own stats")
            own.clicked.connect(lambda: self.edit("Give it its own stats", c.fork_race,
                                                  rebuild=True))
            grid.addWidget(own, row, 2)
            row += 1
        first = ["Derived Character Attributes/(HP) Hit Points",
                 "Derived Character Attributes/(AC) Armor Class"]
        presets = sorted(c.presets(), key=lambda p: (p.target not in first,
                                                     first.index(p.target) if p.target in first else 0,
                                                     p.label.lower()))
        for p in presets + c.skills():
            grid.addWidget(QLabel(p.label), row, 0)
            grid.addWidget(commit_edit(p.value, lambda t, tg=p.target: self.stat_edit(
                f"Change {icore.readable(tg)}", lambda: c.set_preset(tg, t)), width=80), row, 1)
            if p.target not in first:
                rm = QPushButton("Remove")
                rm.clicked.connect(lambda _c=False, tg=p.target: self.stat_edit(
                    f"Remove {icore.readable(tg)}", lambda: c.remove_preset(tg)))
                grid.addWidget(rm, row, 2)
            row += 1
        add = QPushButton("Add a resistance, stat or skill...")
        add.clicked.connect(self.add_stat)
        grid.addWidget(add, row, 0, 1, 2)
        row += 1
        grid.addWidget(note("Resistances are percentages; a minus number is a weakness."),
                       row, 0, 1, 3)
        row += 1
        grid.addWidget(QLabel("<b>Attributes</b>"), row, 0)
        row += 1
        for target, value in c.attributes():
            grid.addWidget(QLabel(icore.readable(target)), row, 0)
            grid.addWidget(commit_edit(value, lambda t, tg=target: self.stat_edit(
                f"Change {icore.readable(tg)}", lambda: c.set_attribute(tg, t)), width=80), row, 1)
            row += 1
        return box

    def _model_card(self) -> QGroupBox:
        c = self.creature
        box = QGroupBox("Model")
        lay = QVBoxLayout(box)
        info = core.model_info(self.lib, c.model)
        n = self.model_users.get(c.model.lower(), 0)
        lay.addWidget(QLabel(f"<b>{c.model.split('/', 1)[-1]}</b>"
                             + (f"  (used by {n} creature{'s' * (n != 1)})" if n else "")))
        if info.clips:
            clips = QLabel("Animations: " + ", ".join(
                f"<a href='{x}'>{x}</a>" for x in info.clip_names))
            clips.setWordWrap(True)
            clips.linkActivated.connect(self.preview.play)
            lay.addWidget(clips)
        used = c.sequences_used()
        if used:
            lay.addWidget(note("This creature uses: " + ", ".join(used) + ". Another model must "
                               "have all of them."))
        if info.scaling:
            lay.addWidget(note(f"Size: {info.scaling}. The size belongs to the model, so it "
                               "can't be changed for one creature."))
        change = QPushButton("Change the model...")
        change.clicked.connect(self.change_model)
        lay.addWidget(change)
        return box

    # -- actions ------------------------------------------------------------------------

    def add_stat(self):
        dlg = StatPicker(self, self.stat_choices)
        dlg.setWindowTitle("Add a resistance, stat or skill")
        dlg.percent.hide()
        if dlg.exec() != QDialog.Accepted or not dlg.chosen():
            return
        target, amount = dlg.chosen(), dlg.amount.text()
        self.stat_edit(f"Add {icore.readable(target)}",
                       lambda: self.creature.set_preset(target, amount))

    def change_model(self):
        c = self.creature
        dlg = ModelPicker(self, c, self.models, self.model_users, self.preview)
        ok = dlg.exec() == QDialog.Accepted and dlg.chosen()
        if ok and dlg.chosen().lower() != c.model.lower():
            if self.edit("Change model", lambda: c.set_model(dlg.chosen()), rebuild=True):
                self.preview.show_model(c.model)
                return
        self.preview.show_model(c.model)

    def make_new(self):
        if self.creature is None or not self._confirm_discard():
            return
        dlg = NewEnemyDialog(self, self.creature)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            new = self.creature.clone(dlg.file.text(), dlg.name.text())
        except icore.ItemError as e:
            QMessageBox.warning(self, APP, str(e))
            return
        self.set_creature(new)

    def _need_mod(self) -> bool:
        if self.mod is not None:
            return True
        r = QMessageBox.question(self, APP, "You have not picked a mod yet. Make a new mod now? "
                                 "(No lets you pick an existing one.)",
                                 QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if r == QMessageBox.Yes:
            self.new_mod()
        elif r == QMessageBox.No:
            self.choose_mod()
        return self.mod is not None

    def save(self) -> bool:
        c = self.creature
        if c is None or not c.dirty:
            return True
        if not self._need_mod():
            return False
        probs = c.problems()
        if probs and QMessageBox.warning(
                self, APP, "There are problems:\n\n" + "\n".join("- " + p for p in probs)
                + "\n\nSave anyway?", QMessageBox.Save | QMessageBox.Cancel) != QMessageBox.Save:
            return False
        if c.can.origin == "vanilla" and c.can.dirty:
            if QMessageBox.question(
                    self, APP, f"This puts your version of {c.name} in your mod. With the mod "
                    "installed, every one of them in the game changes.\n\n" + core.REACH_NOTE,
                    QMessageBox.Save | QMessageBox.Cancel) != QMessageBox.Save:
                return False
        try:
            written = c.save_into_mod(self.mod)
        except (icore.ItemError, OSError, ValueError) as e:
            QMessageBox.critical(self, APP, f"Not saved: {e}")
            return False
        self.undo.setClean()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            found = engine_check(self.mod, self.vanilla)
        except Exception as e:
            found = [f"The checker itself failed: {e}"]
        finally:
            QApplication.restoreOverrideCursor()
        if found:
            QMessageBox.warning(self, APP, "Saved, but the engine checks found problems in your "
                                "mod:\n\n" + "\n".join("- " + m for m in found[:15]))
        self.statusBar().showMessage(
            f"Saved {len(written)} file(s). Engine checks "
            + (f"found {len(found)} problem(s)." if found else "passed."), 10000)
        rel = c.can.rel
        self.creature = None
        self.set_mod(self.mod)
        self.open_rel(rel, force=True)
        return True

    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        self.preview.wait_threads()
        if self.server:
            self.server.close()
        event.accept()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mod", type=Path, help="your mod folder")
    ap.add_argument("--game", type=Path, help="the Lionheart folder")
    ap.add_argument("--vanilla", type=Path, help="data.dat.vanilla.bak, if not in the game folder")
    ap.add_argument("--open", help="a creature to open, by its file path")
    args = ap.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP)
    itemedit.install_error_box(APP)
    settings = QSettings(ORG, "itemedit")       # the game folder is shared with the item editor
    if args.vanilla:
        if not args.vanilla.is_file():
            QMessageBox.critical(None, APP, f"There is no file at {args.vanilla}.")
            return 1
        vanilla, game = args.vanilla, args.game or args.vanilla.parent
    else:
        game = itemedit.find_game(settings, args.game, APP)
        if game is None:
            return 1
        vanilla = itemedit.original_files(game, APP)
        if vanilla is None:
            return 1
    settings.setValue("game", str(game))
    win = EnemyWindow(vanilla, args.mod, game)
    if args.open:
        win.open_rel(args.open)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
