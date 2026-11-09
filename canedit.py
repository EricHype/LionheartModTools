"""An editor for creature cans (`.can`), with a Combat AI panel.

A creature's behaviour is one `CSkeletonAI` block deep inside its can: who it targets and
from how far, how far it chases, whether it answers a call for help, what attack AI it
runs and what fires when its state changes. Editing that by hand means finding the right
one of 1,600 lines and knowing which of its fields the engine actually reads. This puts
the ones that matter in labelled controls, and hosts the map editor's script dock over
the whole can for everything else.

Everything rests on `canedit_core`, whose load/save is byte-identical across all 1698
shipped cans. Edits change fields in place, so one edit is a one-line diff.

    python canedit.py                                    # pick a can from the list
    python canedit.py "Monster Cans/English Enemies/Soldier1"     # a vanilla can
    python canedit.py "<path to a .can>" [--mod <mod folder>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDockWidget, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem, QUndoView, QVBoxLayout,
    QWidget,
)

import can_schema as cs
import canedit_core as core
from mapedit_script import ScriptDock
from qtwidgets import NoScrollComboBox
from resource_format import ResourceNode
import script_schema as ss

# The Fixt checkout sits beside this one (its README says it needs this repo to build).
SIBLING_MODS = (Path(__file__).resolve().parent.parent / "LionheartFixt",)

EMPTY_ATTACK = "(empty: does not fight)"
WARNING = ("Editing a can changes this creature everywhere it appears. For a change in one "
           "map only, give that generator an AI in its AIs to Add instead (the map "
           "editor's Entity Script dock).")


# ---------------------------------------------------------------------------
# Undo commands
#
# Panel edits go through the same QUndoStack as the hosted script dock, so Ctrl+Z walks
# back through both in the order they were made. Labels are written to be read: the
# Edits dock lists them.
# ---------------------------------------------------------------------------

class SetFieldCommand(QUndoCommand):
    """Set one field on one node. The value may be a string or a whole child node --
    switching attack AI class swaps the `Attack AI` block as one undoable step."""

    def __init__(self, window, node: ResourceNode, key: str, old, new, label: str):
        super().__init__(label)
        self.window, self.node, self.key, self.old, self.new = window, node, key, old, new

    def _apply(self, value):
        self.node.set(self.key, value)
        self.window.mark_dirty()

    def redo(self):
        self._apply(self.new)

    def undo(self):
        self._apply(self.old)


class InsertItemCommand(QUndoCommand):
    """Append a node to an Array, keeping its declared count in step."""

    def __init__(self, window, array: ResourceNode, key: str, node: ResourceNode, label):
        super().__init__(label)
        self.window, self.array, self.key, self.node = window, array, key, node

    def redo(self):
        self.array.fields.append((self.key, self.node))
        ss.sync_count(self.array)
        self.window.mark_dirty()

    def undo(self):
        for i, (_, v) in enumerate(self.array.fields):
            if v is self.node:
                del self.array.fields[i]
                break
        ss.sync_count(self.array)
        self.window.mark_dirty()


class RemoveItemCommand(QUndoCommand):
    def __init__(self, window, array: ResourceNode, node: ResourceNode, label):
        super().__init__(label)
        self.window, self.array, self.node = window, array, node
        self.index = self.key = None

    def redo(self):
        for i, (k, v) in enumerate(self.array.fields):
            if v is self.node:
                self.index, self.key = i, k
                del self.array.fields[i]
                break
        ss.sync_count(self.array)
        self.window.mark_dirty()

    def undo(self):
        self.array.fields.insert(self.index, (self.key, self.node))
        ss.sync_count(self.array)
        self.window.mark_dirty()


# ---------------------------------------------------------------------------
# The can tree: the map editor's script dock, over the whole can
# ---------------------------------------------------------------------------

class _CanEntity:
    """What ScriptDock asks of an entity: a name, a model and a node."""

    def __init__(self, doc: core.CanDocument):
        self.node = doc.root
        head = core.header(doc)
        self.name = head["User Assigned Name"] or doc.name
        self.model = head["Model"]


class CanScriptDock(ScriptDock):
    def script_slots(self):
        # Every child block of the CCannedObject -- in practice its one `Object`.
        return [(k, v) for k, v in self.entity.node.fields if isinstance(v, ResourceNode)]

    def refresh(self, **kwargs):
        super().refresh(**kwargs)
        if self.entity is not None:
            self.header.setText(f"<b>{self.entity.name}</b> - the whole can. Click a row "
                                "to edit its fields.")

    def delete_action(self):
        cur = self._current()
        if cur and cur[0] is getattr(self.entity, "node", None):
            self.window.statusBar().showMessage(
                "The can's own Object block cannot be deleted.", 6000)
            return
        super().delete_action()

    def select(self, node):
        self.refresh(select_node=node)


# ---------------------------------------------------------------------------
# The Combat AI panel
# ---------------------------------------------------------------------------

class CombatPanel(QWidget):
    """Labelled controls over the can's CSkeletonAI.

    Widgets are built once per document and re-synced after every edit rather than
    rebuilt: an edit is committed from a widget's own signal, and destroying that widget
    inside its handler is how Qt editors crash.
    """

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._syncing = False
        self.controls: list[tuple[cs.PanelField, QWidget]] = []
        self.skeleton: ResourceNode | None = None
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(4, 4, 4, 4)

    # -- building -----------------------------------------------------------

    def _clear(self):
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.controls = []

    def load(self, doc: core.CanDocument):
        self._clear()
        self.skeleton = core.find_skeleton(doc)
        if self.skeleton is None:
            note = QLabel(core.why_no_skeleton(doc) + "\n\nThe Can tree still edits "
                          "everything in it.")
            note.setWordWrap(True)
            self.layout_.addWidget(note)
            self.layout_.addStretch(1)
            return

        for title, fields in cs.GROUPS:
            box = QGroupBox(title)
            form = QFormLayout(box)
            for f in fields:
                widget = self._make_control(f)
                self.controls.append((f, widget))
                form.addRow(f.label, widget)
            if title == "Attack":
                self._build_attack_extras(form)
            self.layout_.addWidget(box)

        self._build_transitions()
        self._build_retreat()
        self.layout_.addStretch(1)
        self.sync()

    def _make_control(self, f: cs.PanelField) -> QWidget:
        tip = f.hint + (f"  [{f.source}]" if f.hint else f"[{f.source}]")
        if f.kind == "flag":
            w = QCheckBox()
            w.toggled.connect(lambda on, f=f: self._commit(f, "1" if on else "0"))
        elif f.choices:
            w = NoScrollComboBox()
            w.setEditable(True)        # observed values are common, not exhaustive
            w.addItems(list(f.choices))
            w.activated.connect(lambda _i, f=f, w=w: self._commit(f, w.currentText()))
            w.lineEdit().editingFinished.connect(
                lambda f=f, w=w: self._commit(f, w.currentText()))
        else:
            w = QLineEdit()
            w.editingFinished.connect(lambda f=f, w=w: self._commit(f, w.text()))
        w.setToolTip(tip)
        return w

    def _build_attack_extras(self, form: QFormLayout):
        self.class_box = NoScrollComboBox()
        model = self.class_box.model()
        self._class_values: list[str] = []
        for name, why in cs.WORKING_ATTACK_AIS.items():
            self.class_box.addItem(name)
            self.class_box.setItemData(self.class_box.count() - 1, why, Qt.ToolTipRole)
            self._class_values.append(name)
        self.class_box.addItem(EMPTY_ATTACK)
        self.class_box.setItemData(self.class_box.count() - 1,
                                   "Attack AI= with nothing: it still moves and targets, "
                                   "but never attacks. 47 shipped placements.",
                                   Qt.ToolTipRole)
        self._class_values.append("")
        for name, why in cs.ABSTRACT_CLASSES.items():
            self.class_box.addItem(f"{name}  (abstract: crashes the game)")
            index = self.class_box.count() - 1
            self.class_box.setItemData(index, why, Qt.ToolTipRole)
            model.item(index).setEnabled(False)
            self._class_values.append(name)
        self.class_box.activated.connect(self._on_class_chosen)
        form.insertRow(0, "Attack AI class", self.class_box)

        alias = QLabel("CStandGroundAttackAI is not offered: it is an alias of "
                       "CNormalAttackAI and loads as one. To stand ground, set Minimum "
                       f"Attack Distance to '{cs.DO_NOT_MOVE}'.")
        alias.setWordWrap(True)
        form.insertRow(1, alias)
        self.class_note = QLabel()
        self.class_note.setWordWrap(True)
        form.insertRow(2, self.class_note)

        range_note = QLabel(cs.MAX_ATTACK_RANGE_NOTE)
        range_note.setWordWrap(True)
        range_note.setEnabled(False)
        form.addRow(range_note)

        self.bank = QListWidget()
        self.bank.setMaximumHeight(110)
        self.bank.setToolTip("Shoot Completed fires after each attack. A CRandomAction "
                             "here picks one branch per attack, so the branch count is "
                             "the rate. Edit it in the Can tree.")
        bank_row = QWidget()
        bank_layout = QVBoxLayout(bank_row)
        bank_layout.setContentsMargins(0, 0, 0, 0)
        bank_layout.addWidget(self.bank)
        show = QPushButton("Show in Can tree")
        show.clicked.connect(self._show_bank)
        bank_layout.addWidget(show, 0, Qt.AlignLeft)
        form.addRow("Shoot Completed", bank_row)

    def _build_transitions(self):
        box = QGroupBox("Transitions")
        layout = QVBoxLayout(box)
        note = QLabel("Fire an action when the creature changes state. Vanilla uses this "
                      "for war cries on the first switch to Attack.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.trans_list = QListWidget()
        self.trans_list.setMaximumHeight(120)
        self.trans_list.currentRowChanged.connect(lambda _r: self._sync_transition_fields())
        layout.addWidget(self.trans_list)

        form = QFormLayout()
        self.trans_fields = {}
        for key, choices in (("Trigger", cs.TRIGGERS), ("From State", cs.STATES),
                             ("To State", cs.STATES)):
            w = NoScrollComboBox()
            w.addItems(list(choices))
            w.activated.connect(lambda _i, k=key, w=w: self._commit_transition(k, w.currentText()))
            self.trans_fields[key] = w
            form.addRow(key, w)
        layout.addLayout(form)

        row = QHBoxLayout()
        add = QPushButton("Add")
        add.setToolTip("A new transition with an empty Action; fill it in the Can tree.")
        add.clicked.connect(self._add_transition)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_transition)
        show = QPushButton("Show in Can tree")
        show.clicked.connect(self._show_transition)
        for b in (add, remove, show):
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)

        preset = QPushButton("Preset: call for help when it first attacks")
        preset.setToolTip("Adds a transition that fires CCallForReinforcementsAction "
                          "(Requester Name=$Trigger, Max Distance=500) the first time it "
                          "switches to Attack on each new target. Idle allies within "
                          "500 px that have Respond to calls for reinforcements=1 and "
                          "share its targets join the fight.")
        preset.clicked.connect(self._add_call_for_help)
        layout.addWidget(preset, 0, Qt.AlignLeft)
        self.layout_.addWidget(box)

    def _build_retreat(self):
        box = QGroupBox("Retreat  (inert in this engine)")
        form = QFormLayout(box)
        note = QLabel(cs.RETREAT_NOTE)
        note.setWordWrap(True)
        form.addRow(note)
        self.retreat_widgets = []
        for f in cs.RETREAT_FIELDS:
            w = QLineEdit()
            w.setReadOnly(True)
            w.setEnabled(False)
            self.retreat_widgets.append((f, w))
            form.addRow(f.label, w)
        self.layout_.addWidget(box)

    # -- syncing ------------------------------------------------------------

    def sync(self):
        """Pull every widget's value from the tree. Called after any edit or undo."""
        if self.skeleton is None:
            return
        self._syncing = True
        try:
            for f, w in self.controls:
                host = core.host_for(self.skeleton, f.where)
                value = host.get(f.key) if host is not None else None
                present = isinstance(value, str)
                w.setEnabled(present)
                if not present:
                    w.setToolTip(f"{f.key} is not in this can's "
                                 f"{host.type_name if host is not None else 'attack AI'} block.")
                    value = ""
                if isinstance(w, QCheckBox):
                    w.setChecked(value.strip() == "1")
                elif isinstance(w, NoScrollComboBox):
                    w.setCurrentText(value)
                else:
                    w.setText(value)

            current = core.attack_ai_class(self.skeleton)
            self._sync_class_box(current)
            self.bank.clear()
            self.bank.addItems(core.shoot_completed_items(self.skeleton) or ["(empty)"])

            for f, w in self.retreat_widgets:
                v = self.skeleton.get(f.key)
                w.setText(v.type_name if isinstance(v, ResourceNode) else (v or ""))

            row = self.trans_list.currentRow()
            self.trans_list.clear()
            for node in self._transition_nodes():
                self.trans_list.addItem(core.summarise_transition(node))
            if self.trans_list.count():
                self.trans_list.setCurrentRow(min(max(row, 0), self.trans_list.count() - 1))
        finally:
            self._syncing = False
        self._sync_transition_fields()

    def _sync_class_box(self, current: str):
        # A class the picker does not build (an alias, CNormalAttackAIForCPointClickAI)
        # is shown as the first item so it is visible, and choosing another replaces it.
        if self.class_box.count() > len(self._class_values):
            self.class_box.removeItem(0)
        if current in self._class_values:
            self.class_box.setCurrentIndex(self._class_values.index(current))
            other = ""
        else:
            self.class_box.insertItem(0, f"{current}  (current)")
            self.class_box.setCurrentIndex(0)
            other = current
        if other in cs.ATTACK_AI_ALIASES:
            text = f"{other} is an alias: it loads as {cs.ATTACK_AI_ALIASES[other]}."
        elif other:
            text = f"{other} is not one the panel builds; its fields are in the Can tree."
        elif current == "CStandStillAttackAI":
            text = cs.WORKING_ATTACK_AIS[current] + "."
        elif current == "":
            text = "No attack AI: it targets and follows, but never attacks."
        else:
            text = ""
        self.class_note.setText(text)
        self.class_note.setVisible(bool(text))

    def _transition_nodes(self) -> list[ResourceNode]:
        t = core.transitions(self.skeleton)
        if t is None:
            return []
        return [v for _, _, v in ss.array_items(t) if isinstance(v, ResourceNode)]

    def _selected_transition(self) -> ResourceNode | None:
        nodes = self._transition_nodes()
        row = self.trans_list.currentRow()
        return nodes[row] if 0 <= row < len(nodes) else None

    def _sync_transition_fields(self):
        node = self._selected_transition()
        self._syncing = True
        try:
            for key, w in self.trans_fields.items():
                w.setEnabled(node is not None)
                value = node.get(key) if node is not None else ""
                value = value if isinstance(value, str) else ""
                if value and w.findText(value) < 0:
                    w.addItem(value)
                w.setCurrentText(value)
        finally:
            self._syncing = False

    # -- committing ---------------------------------------------------------

    def _commit(self, f: cs.PanelField, text: str):
        if self._syncing or self.skeleton is None:
            return
        host = core.host_for(self.skeleton, f.where)
        if host is None:
            return
        old = host.get(f.key)
        if not isinstance(old, str):
            return
        text = text.strip()
        if text == old:
            return
        if f.kind == "number" or (f.key == "Minimum Attack Distance" and text != cs.DO_NOT_MOVE):
            try:
                number = float(text)
            except ValueError:
                self.window.statusBar().showMessage(
                    f"{f.label}: '{text}' is not a number. Not changed.", 6000)
                self.sync()
                return
            if f.key == "Minimum Attack Distance" and number == 0:
                # The engine labels 0 this way and vanilla always writes the label.
                text = cs.DO_NOT_MOVE
                if text == old:
                    self.sync()
                    return
        self.window.push(SetFieldCommand(self.window, host, f.key, old, text,
                                         f"{f.label}: {old or '(empty)'} -> {text or '(empty)'}"))

    def _on_class_chosen(self, index: int):
        if self._syncing or self.skeleton is None:
            return
        offset = self.class_box.count() - len(self._class_values)
        if index < offset:
            return                      # the "(current)" entry: nothing to do
        new = self._class_values[index - offset]
        current = core.attack_ai_class(self.skeleton)
        if new == current:
            return
        try:
            value = core.build_attack_ai(self.window.doc, self.skeleton, new)
        except (core.UnsafeCanError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot switch", str(exc))
            self.sync()
            return
        core.remember_attack_ai(self.window.doc, self.skeleton)
        old = self.skeleton.get("Attack AI")
        self.window.push(SetFieldCommand(
            self.window, self.skeleton, "Attack AI", old, value,
            f"Attack AI: {current or '(empty)'} -> {new or '(empty)'}"))

    def _commit_transition(self, key: str, text: str):
        if self._syncing:
            return
        node = self._selected_transition()
        if node is None:
            return
        old = node.get(key)
        if not isinstance(old, str) or old == text:
            return
        self.window.push(SetFieldCommand(self.window, node, key, old, text,
                                         f"Transition {key}: {old} -> {text}"))

    def _add_transition(self, node: ResourceNode | None = None, label="Add transition"):
        if self.skeleton is None:
            return
        array = core.transitions(self.skeleton)
        if array is None:
            # Never happens in vanilla (all 716 have one), so not worth an undo step
            # of its own: the empty array is created inside the same edit.
            array = core.ensure_transitions(self.skeleton)
        node = node if isinstance(node, ResourceNode) else core.new_transition()
        self.window.push(InsertItemCommand(self.window, array, core.TRANSITION_ITEM_KEY,
                                           node, label))
        self.trans_list.setCurrentRow(self.trans_list.count() - 1)

    def _add_call_for_help(self):
        self._add_transition(core.call_for_help_transition(),
                             "Preset: call for help when it first attacks")
        if self.skeleton.get("Respond to calls for reinforcements") != "1":
            self.window.statusBar().showMessage(
                "Added. The call reaches allies that have Respond to calls for "
                "reinforcements=1; this creature's own setting does not matter for "
                "calling.", 10000)

    def _remove_transition(self):
        node = self._selected_transition()
        if node is None:
            return
        self.window.push(RemoveItemCommand(self.window, core.transitions(self.skeleton),
                                           node, "Remove transition: "
                                           + core.summarise_transition(node)))

    def _show_transition(self):
        node = self._selected_transition()
        if node is not None:
            self.window.show_in_tree(node)

    def _show_bank(self):
        attack = self.skeleton.get("Attack AI") if self.skeleton else None
        if isinstance(attack, ResourceNode):
            bank = attack.get("Shoot Completed")
            self.window.show_in_tree(bank if isinstance(bank, ResourceNode) else attack)


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

class CanWindow(QMainWindow):
    def __init__(self, mods_root: Path | None = None, game_dir: Path | None = None,
                 extra_mods: list[Path] | None = None, context_mod: Path | None = None):
        super().__init__()
        self.mods_root = Path(mods_root) if mods_root else Path(__file__).parent / "mods"
        self.game_dir = Path(game_dir) if game_dir else core.DEFAULT_GAME_DIR
        self.vanilla_dat = core.default_vanilla_dat(self.game_dir)
        self.extra_mods = [Path(p) for p in (extra_mods if extra_mods is not None
                                             else SIBLING_MODS) if (Path(p) / "mod.json").is_file()]
        self.context_mod = Path(context_mod) if context_mod else None
        self.undo_stack = QUndoStack(self)
        self.undo_stack.indexChanged.connect(self._on_index_changed)
        self.doc: core.CanDocument | None = None
        self._loading = False
        self.uses = core.UseIndex(self.vanilla_dat)

        self.panel = CombatPanel(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(4, 4, 4, 4)
        central_layout.addWidget(self._build_header())
        central_layout.addWidget(self.panel)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)

        self._build_docks()
        self._build_menus()
        self._populate_file_list()
        self.setWindowTitle("Can Editor")

    # -- the three things ScriptDock needs, plus the panel's ------------------

    def _title(self):
        if self.doc is None:
            return "Can Editor"
        mark = "*" if self.doc.dirty else ""
        tag = "  [read-only: Save As into a mod]" if self.doc.read_only else ""
        return f"{self.doc.name}{mark}{tag} - Can Editor"

    def mark_dirty(self):
        self.doc.dirty = True
        self.setWindowTitle(self._title())

    def push(self, command):
        self.undo_stack.push(command)

    def _on_index_changed(self, _index):
        try:
            self.after_edit()
        except RuntimeError:
            # The stack outlives the window by a moment at teardown and clears itself,
            # which emits this once more against widgets already gone.
            pass

    def after_edit(self):
        if self.doc is None or self._loading:
            return
        self.panel.sync()
        self._sync_header()
        self.script.refresh(keep_selection=True)
        self.setWindowTitle(self._title())

    def show_in_tree(self, node):
        self.script_dock.raise_()
        self.script.select(node)

    # -- header ---------------------------------------------------------------

    def _build_header(self) -> QWidget:
        box = QGroupBox("Creature")
        layout = QVBoxLayout(box)
        self.where_label = QLabel()
        self.where_label.setWordWrap(True)
        self.where_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.where_label)
        form = QFormLayout()
        self.header_edits = {}
        for key in ("User Assigned Name", "Race", "Category", "Team Number", "Model"):
            w = QLineEdit()
            w.editingFinished.connect(lambda k=key, w=w: self._commit_header(k, w.text()))
            self.header_edits[key] = w
            form.addRow(key, w)
        self.header_edits["User Assigned Name"].setToolTip(
            "The name the combat log prints, and what an unnamed spawn is called.")
        self.class_label = QLabel()
        form.addRow("Entity class", self.class_label)
        layout.addLayout(form)
        return box

    def _sync_header(self):
        head = core.header(self.doc)
        for key, w in self.header_edits.items():
            host = core.header_node(self.doc, key)
            present = host is not None and isinstance(host.get(key), str)
            w.setEnabled(present)
            if w.text() != head[key]:
                w.setText(head[key])
        self.class_label.setText(head["Entity class"])

    def _commit_header(self, key: str, text: str):
        if self.doc is None:
            return
        node = core.header_node(self.doc, key)
        old = node.get(key) if node is not None else None
        if not isinstance(old, str) or old == text:
            return
        self.push(SetFieldCommand(self, node, key, old, text, f"{key}: {old} -> {text}"))

    def _sync_uses(self):
        self.uses_list.clear()
        if not self.doc.resource_path:
            self.uses_label.setText("Cannot tell this can's resource path, so cannot tell "
                                    "which maps spawn it.")
            return
        mod = self._doc_mod()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            uses = self.uses.uses(self.doc.resource_path, mod)
        finally:
            QApplication.restoreOverrideCursor()
        maps = sorted({u.map_path for u in uses})
        scope = "vanilla" + (f" and {mod.name}" if mod else "")
        self.uses_label.setText(
            f"<b>{core.can_ref(self.doc.resource_path)}</b><br>"
            f"Spawned by {len(uses)} generator entr{'y' if len(uses) == 1 else 'ies'} in "
            f"{len(maps)} map(s), searching {scope}.<br><br>{WARNING}")
        for u in uses:
            part = u.part or "(unnamed part)"
            self.uses_list.addItem(f"{u.map_path}  --  {part}   [{u.source}]")
        if not uses:
            self.uses_list.addItem("No map names this can in an Entity= line. It may be "
                                   "created by a script or cloned instead.")

    def _doc_mod(self) -> Path | None:
        if self.doc is not None and self.doc.path is not None:
            mod = core.find_mod_root(self.doc.path)
            if mod is not None:
                return mod
        return self.context_mod

    # -- docks ------------------------------------------------------------------

    def _build_docks(self):
        files = QWidget()
        files_layout = QVBoxLayout(files)
        files_layout.setContentsMargins(4, 4, 4, 4)
        self.file_filter = QLineEdit()
        self.file_filter.setPlaceholderText("Filter cans...  (e.g. goblin archer)")
        self.file_filter.textChanged.connect(self._on_file_filter)
        files_layout.addWidget(self.file_filter)
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.itemActivated.connect(self._on_file_activated)
        files_layout.addWidget(self.file_tree)
        files_dock = QDockWidget("Cans", self)
        files_dock.setWidget(files)
        self.addDockWidget(Qt.LeftDockWidgetArea, files_dock)

        self.script = CanScriptDock(self)
        self.script_dock = QDockWidget("Can tree", self)
        self.script_dock.setWidget(self.script)
        self.addDockWidget(Qt.RightDockWidgetArea, self.script_dock)

        uses = QWidget()
        uses_layout = QVBoxLayout(uses)
        uses_layout.setContentsMargins(4, 4, 4, 4)
        self.uses_label = QLabel()
        self.uses_label.setWordWrap(True)
        uses_layout.addWidget(self.uses_label)
        self.uses_list = QListWidget()
        uses_layout.addWidget(self.uses_list)
        uses_dock = QDockWidget("Used by", self)
        uses_dock.setWidget(uses)
        self.addDockWidget(Qt.BottomDockWidgetArea, uses_dock)

        edits_dock = QDockWidget("Edits", self)
        edits_dock.setWidget(QUndoView(self.undo_stack))
        self.addDockWidget(Qt.BottomDockWidgetArea, edits_dock)
        self.tabifyDockWidget(uses_dock, edits_dock)
        uses_dock.raise_()

    def _all_mods(self) -> list[Path]:
        mods = []
        if self.mods_root.is_dir():
            mods = [p for p in sorted(self.mods_root.iterdir()) if (p / "mod.json").is_file()]
        for extra in self.extra_mods + ([self.context_mod] if self.context_mod else []):
            if extra.resolve() not in {m.resolve() for m in mods}:
                mods.append(extra)
        return mods

    def _populate_file_list(self):
        self.file_tree.clear()
        for mod, paths in self._group_mod_cans().items():
            group = QTreeWidgetItem([f"{mod}  ({len(paths)})"])
            group.setFlags(group.flags() & ~Qt.ItemIsSelectable)
            self.file_tree.addTopLevelItem(group)
            for p in paths:
                leaf = QTreeWidgetItem([p.stem])
                leaf.setData(0, Qt.UserRole, ("file", str(p)))
                leaf.setToolTip(0, str(p))
                group.addChild(leaf)
        if self.vanilla_dat.is_file():
            members = core.vanilla_cans(self.vanilla_dat)
            group = QTreeWidgetItem([f"vanilla, read-only  ({len(members)})"])
            group.setFlags(group.flags() & ~Qt.ItemIsSelectable)
            self.file_tree.addTopLevelItem(group)
            for m in members:
                leaf = QTreeWidgetItem([core.can_ref(m)])
                leaf.setData(0, Qt.UserRole, ("vanilla", m))
                group.addChild(leaf)

    def _group_mod_cans(self) -> dict[str, list[Path]]:
        out: dict[str, list[Path]] = {}
        for mod, path in core.mod_cans(self._all_mods()):
            out.setdefault(mod, []).append(path)
        return out

    def _on_file_filter(self, text: str):
        terms = text.lower().split()
        for i in range(self.file_tree.topLevelItemCount()):
            group = self.file_tree.topLevelItem(i)
            any_visible = False
            for j in range(group.childCount()):
                child = group.child(j)
                haystack = (child.text(0) + " " + (child.toolTip(0) or "")).lower()
                hit = all(t in haystack for t in terms)
                child.setHidden(not hit)
                any_visible = any_visible or hit
            group.setHidden(not any_visible)
            group.setExpanded(bool(terms) and any_visible)

    def _on_file_activated(self, item, _column=0):
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        kind, where = data
        if kind == "vanilla":
            self.open_vanilla(where)
        else:
            self.open_file(Path(where))

    # -- open / save ------------------------------------------------------------

    def _confirm_discard(self) -> bool:
        if self.doc is None or not self.doc.dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes", f"{self.doc.name} has unsaved changes.\n\nSave first?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            return self.save()
        return True

    def open_file(self, path: Path) -> bool:
        return self._open(lambda: core.CanDocument.from_file(path, self.game_dir))

    def open_vanilla(self, member: str) -> bool:
        member = member.replace("\\", "/")
        if not member.lower().startswith("resources/"):
            member = "Resources/" + member
        if not member.lower().endswith(".can"):
            member += ".can"
        return self._open(lambda: core.CanDocument.from_vanilla(member, self.vanilla_dat))

    def _open(self, loader) -> bool:
        if not self._confirm_discard():
            return False
        try:
            doc = loader()
        except Exception as exc:
            QMessageBox.critical(self, "Could not open", str(exc))
            return False
        self.set_document(doc)
        return True

    def set_document(self, doc: core.CanDocument):
        self._loading = True
        self.doc = doc
        self.undo_stack.clear()
        self._loading = False
        self.where_label.setText(
            f"{doc.origin}" + ("<br><i>Read-only. Edit freely, then Save As into a mod.</i>"
                               if doc.read_only else ""))
        self.panel.load(doc)
        self._sync_header()
        self.script.set_entity(_CanEntity(doc))
        self._sync_uses()
        self.setWindowTitle(self._title())
        self.statusBar().showMessage(f"Opened {doc.origin}", 6000)

    def open_dialog(self):
        start = str(self.doc.path.parent if self.doc and self.doc.path else self.mods_root)
        chosen, _ = QFileDialog.getOpenFileName(self, "Open a can", start,
                                                "Cans (*.can);;All files (*)")
        if chosen:
            self.open_file(Path(chosen))

    def _refuse_unsafe(self) -> bool:
        try:
            self.doc.check_writable()
        except core.UnsafeCanError as exc:
            QMessageBox.critical(self, "Not saved", f"Refusing to write this can.\n\n{exc}")
            return True
        return False

    def save(self) -> bool:
        if self.doc is None:
            return False
        if self.doc.read_only:
            QMessageBox.information(
                self, "Read-only",
                f"{self.doc.name} is vanilla or inside the installed game, which is never "
                "written to.\n\nUse Save As to put it into a mod.")
            return self.save_as()
        if self._refuse_unsafe():
            return False
        try:
            self.doc.save(game_dir=self.game_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self.setWindowTitle(self._title())
        self.statusBar().showMessage(
            "Saved. Run modmanager install + build for this to reach the game.", 8000)
        return True

    def save_as(self, mod_dir: Path | None = None) -> bool:
        """Into a mod's files/ at the can's resource path, registered in its mod.json."""
        if self.doc is None or self._refuse_unsafe():
            return False
        if mod_dir is None:
            mods = self._all_mods()
            names = [str(m) for m in mods] + ["Other mod folder..."]
            chosen, ok = QInputDialog.getItem(self, "Save As into a mod",
                                              "Mod (a folder with mod.json):", names, 0, False)
            if not ok:
                return False
            if chosen == names[-1]:
                picked = QFileDialog.getExistingDirectory(self, "Mod folder",
                                                          str(self.mods_root))
                if not picked:
                    return False
                mod_dir = Path(picked)
            else:
                mod_dir = Path(chosen)
        if not self.doc.resource_path:
            QMessageBox.warning(self, "Not saved", "Cannot tell this can's resource path.")
            return False
        target = mod_dir / "files" / Path(self.doc.resource_path)
        if target.exists() and (self.doc.path is None or target.resolve() != self.doc.path.resolve()):
            answer = QMessageBox.question(
                self, "Overwrite?", f"{target}\nalready exists in that mod. Replace it?")
            if answer != QMessageBox.Yes:
                return False
        try:
            self.doc.save_into_mod(mod_dir, self.game_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self.where_label.setText(self.doc.origin)
        self._sync_uses()
        self.setWindowTitle(self._title())
        self.statusBar().showMessage(f"Saved to {target} and listed in mod.json.", 8000)
        return True

    def save_copy(self, path: Path | None = None) -> bool:
        """Write the can anywhere outside the game, without changing what is open."""
        if self.doc is None or self._refuse_unsafe():
            return False
        if path is None:
            chosen, _ = QFileDialog.getSaveFileName(self, "Save a copy", self.doc.name,
                                                    "Cans (*.can)")
            if not chosen:
                return False
            path = Path(chosen)
        try:
            self.doc.save(path, self.game_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self.statusBar().showMessage(f"Copy written to {path}", 8000)
        return True

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        open_action = file_menu.addAction("&Open...")
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.open_dialog)
        file_menu.addSeparator()
        save = file_menu.addAction("&Save")
        save.setShortcut(QKeySequence.Save)
        save.triggered.connect(self.save)
        save_as = file_menu.addAction("Save &As into a mod...")
        save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as.triggered.connect(lambda: self.save_as())
        copy_action = file_menu.addAction("Save a &Copy...")
        copy_action.triggered.connect(lambda: self.save_copy())

        edit_menu = self.menuBar().addMenu("&Edit")
        undo = self.undo_stack.createUndoAction(self, "&Undo")
        undo.setShortcut(QKeySequence.Undo)
        redo = self.undo_stack.createRedoAction(self, "&Redo")
        redo.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        edit_menu.addAction(undo)
        edit_menu.addAction(redo)

    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()


def main() -> int:
    parser = argparse.ArgumentParser(description="Lionheart creature can editor")
    parser.add_argument("can", nargs="?",
                        help="a .can file, or a vanilla can by resource path "
                             "(e.g. 'Monster Cans/English Enemies/Soldier1')")
    parser.add_argument("--mod", default=None,
                        help="a mod folder whose maps count in Used by, and the default "
                             "for Save As")
    parser.add_argument("--mods-root", default=None,
                        help="directory of mods to list (default: ./mods)")
    parser.add_argument("--game-dir", default=None,
                        help="installed game folder; never written to")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    window = CanWindow(Path(args.mods_root) if args.mods_root else None,
                       Path(args.game_dir) if args.game_dir else None,
                       context_mod=Path(args.mod) if args.mod else None)
    if args.can:
        if Path(args.can).is_file():
            window.open_file(Path(args.can))
        else:
            window.open_vanilla(args.can)
    window.resize(1500, 950)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
