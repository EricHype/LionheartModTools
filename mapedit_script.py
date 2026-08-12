"""The script dock: view and edit the action tree hanging off a map entity.

Chests, generators, doors and NPCs all carry scripts -- trees of `C*Action` nodes. This
shows the selected entity's tree, lets you edit an action's fields, and add, delete or
reorder actions, all through the editor's existing undo stack.

Two rules the whole thing rests on, both established from the shipped corpus and
documented in `script_schema`:

* Arrays declare their own length, and it is always exact. Every structural edit goes
  through `sync_count`, which updates a count that exists and never invents one.
* Field order is preserved, so a hand-authored script stays textually comparable to a
  shipped one and the file diff stays small.

Classes outside the curated schema are not hidden -- they render as a raw field list, so
every one of the game's 125 action classes remains viewable and editable.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QUndoCommand, QBrush, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QLineEdit, QScrollArea, QDialog, QListWidget,
    QDialogButtonBox, QAbstractItemView,
)

import script_schema as ss
from resource_format import ResourceNode
from qtwidgets import NoScrollComboBox


# ---------------------------------------------------------------------------
# Undo commands
# ---------------------------------------------------------------------------

class EditFieldCommand(QUndoCommand):
    """Change one scalar field on one action node."""

    def __init__(self, dock: "ScriptDock", node: ResourceNode, key: str, old, new):
        super().__init__(f"Edit {key}")
        self.dock, self.node, self.key, self.old, self.new = dock, node, key, old, new

    def _apply(self, value):
        self.node.set(self.key, value)
        self.dock.mark_dirty()
        self.dock.refresh(keep_selection=True)

    def redo(self):
        self._apply(self.new)

    def undo(self):
        self._apply(self.old)


class EditItemCommand(QUndoCommand):
    """Change one scalar item of an Array, addressed by position.

    Separate from EditFieldCommand because ResourceNode.set() finds a key, and an array
    routinely holds several items under the same key -- three "Addition to add" entries
    on one item grant, for instance.
    """

    def __init__(self, dock: "ScriptDock", parent: ResourceNode, index: int, key: str,
                 old, new):
        super().__init__(f"Edit {key}")
        self.dock, self.parent, self.index, self.key = dock, parent, index, key
        self.old, self.new = old, new

    def _apply(self, value):
        self.parent.fields[self.index] = (self.key, value)
        self.dock.mark_dirty()
        self.dock.refresh(keep_selection=True)

    def redo(self):
        self._apply(self.new)

    def undo(self):
        self._apply(self.old)


class AddActionCommand(QUndoCommand):
    """Put a new action into an array, or into an empty single-action slot."""

    def __init__(self, dock: "ScriptDock", parent: ResourceNode, key: str,
                 class_name: str, index: int | None = None):
        super().__init__(f"Add {ss.spec_for(class_name).label if ss.is_known(class_name) else class_name}")
        self.dock, self.parent, self.key, self.class_name = dock, parent, key, class_name
        self.index = index
        self.node = ss.new_action(class_name)
        self.prev_value = None      # what the slot held before, for a single slot

    def redo(self):
        if self.parent.type_name == "Array":
            at = len(self.parent.fields) if self.index is None else self.index
            self.parent.fields.insert(at, (self.key, self.node))
            ss.sync_count(self.parent)
        else:
            self.prev_value = self.parent.get(self.key)
            self.parent.set(self.key, self.node)
        self.dock.mark_dirty()
        self.dock.refresh(select_node=self.node)

    def undo(self):
        if self.parent.type_name == "Array":
            for i, (k, v) in enumerate(self.parent.fields):
                if v is self.node:
                    del self.parent.fields[i]
                    break
            ss.sync_count(self.parent)
        else:
            self.parent.set(self.key, self.prev_value if self.prev_value is not None else "")
        self.dock.mark_dirty()
        self.dock.refresh()


class DeleteActionCommand(QUndoCommand):
    """Remove an action. An array item goes away; a single slot goes back to empty."""

    def __init__(self, dock: "ScriptDock", parent: ResourceNode, node: ResourceNode):
        super().__init__(f"Delete {ss.summarise(node).split('  --')[0]}")
        self.dock, self.parent, self.node = dock, parent, node
        self.index = None
        self.key = None

    def redo(self):
        for i, (k, v) in enumerate(self.parent.fields):
            if v is self.node:
                self.index, self.key = i, k
                break
        if self.parent.type_name == "Array":
            del self.parent.fields[self.index]
            ss.sync_count(self.parent)
        else:
            self.parent.fields[self.index] = (self.key, "")
        self.dock.mark_dirty()
        self.dock.refresh()

    def undo(self):
        if self.parent.type_name == "Array":
            self.parent.fields.insert(self.index, (self.key, self.node))
            ss.sync_count(self.parent)
        else:
            self.parent.fields[self.index] = (self.key, self.node)
        self.dock.mark_dirty()
        self.dock.refresh(select_node=self.node)


class MoveActionCommand(QUndoCommand):
    """Reorder an action within its array. Order is execution order."""

    def __init__(self, dock: "ScriptDock", parent: ResourceNode, node: ResourceNode,
                 delta: int):
        super().__init__("Move action " + ("up" if delta < 0 else "down"))
        self.dock, self.parent, self.node, self.delta = dock, parent, node, delta

    def _swap(self, delta):
        items = ss.array_items(self.parent)
        pos = next(n for n, (i, k, v) in enumerate(items) if v is self.node)
        other = pos + delta
        i_a, i_b = items[pos][0], items[other][0]
        f = self.parent.fields
        f[i_a], f[i_b] = f[i_b], f[i_a]
        self.dock.mark_dirty()
        self.dock.refresh(select_node=self.node)

    def redo(self):
        self._swap(self.delta)

    def undo(self):
        self._swap(-self.delta)


# ---------------------------------------------------------------------------
# Choosing a new action
# ---------------------------------------------------------------------------

class ActionPicker(QDialog):
    """Pick an action class. The curated ones are listed by what they do, not by class
    name -- 'Turn hostile' rather than CGoToCombatAction."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Add action")
        self.resize(420, 460)
        layout = QVBoxLayout(self)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter...")
        self.filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self.list)
        for name, spec in sorted(ss.ACTION_SCHEMA.items(), key=lambda kv: kv[1].label):
            self.list.addItem(f"{spec.label}   [{name}]")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.list.setCurrentRow(0)

    def _apply_filter(self, text):
        text = text.lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(text not in item.text().lower())

    def chosen(self) -> str | None:
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return None
        return item.text().rsplit("[", 1)[1].rstrip("]")


# ---------------------------------------------------------------------------
# The dock
# ---------------------------------------------------------------------------

class ScriptDock(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.entity = None
        self._building = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.header = QLabel("No entity selected.")
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Script"])
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.currentItemChanged.connect(lambda *_: self._show_fields())
        layout.addWidget(self.tree, 3)

        row = QHBoxLayout()
        self.add_button = QPushButton("Add...")
        self.add_button.clicked.connect(self.add_action)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.delete_action)
        self.up_button = QPushButton("Up")
        self.up_button.clicked.connect(lambda: self.move_action(-1))
        self.down_button = QPushButton("Down")
        self.down_button.clicked.connect(lambda: self.move_action(1))
        for b in (self.add_button, self.delete_button, self.up_button, self.down_button):
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)

        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form_host)
        layout.addWidget(scroll, 2)

        self._update_buttons()

    # -- plumbing ---------------------------------------------------------

    def mark_dirty(self):
        self.window.doc.dirty = True
        self.window.setWindowTitle(self.window._title())

    def _push(self, command):
        self.window.undo_stack.push(command)

    def set_entity(self, entity):
        self.entity = entity
        self.refresh()

    # -- tree -------------------------------------------------------------

    def refresh(self, *, keep_selection: bool = False, select_node=None):
        """Rebuild the tree. Selection is restored by node identity, not row index --
        a structural edit renumbers the rows but the node objects survive."""
        want = select_node
        if want is None and keep_selection:
            want = self._current_node()

        self._building = True
        self.tree.clear()
        if self.entity is None:
            self.header.setText("No entity selected.")
            self._building = False
            self._show_fields()
            self._update_buttons()
            return

        name = self.entity.name or self.entity.model.rsplit("/", 1)[-1]
        slots = ss.entity_scripts(self.entity.node)
        self.header.setText(
            f"<b>{name}</b> - {len(slots)} script slot(s)" if slots
            else f"<b>{name}</b> - no scripts. Use Add to give it one.")

        found = []
        for key, value in slots:
            root = QTreeWidgetItem([key])
            index = next(i for i, (k, v) in enumerate(self.entity.node.fields)
                         if k == key and v is value)
            root.setData(0, Qt.UserRole, (self.entity.node, key, value, index))
            self.tree.addTopLevelItem(root)
            if isinstance(value, ResourceNode):
                self._add_children(root, value, found, want)
            root.setExpanded(True)
        self._building = False

        if found:
            self.tree.setCurrentItem(found[0])
        self._show_fields()
        self._update_buttons()

    def _add_children(self, parent_item, node: ResourceNode, found, want):
        """One row per child. Arrays are transparent: their items attach to the array's
        own row, because 'Action -> Array -> 6 items' reads worse than 'Action: 6 items'
        with the items directly beneath."""
        for index, key, value in ss.child_slots(node):
            if isinstance(value, ResourceNode) and value.type_name == "Array":
                label = f"{key}  ({len(ss.array_items(value))})"
            elif isinstance(value, ResourceNode):
                label = f"{key}: {ss.summarise(value)}"
            elif ss.is_empty_slot(value):
                label = f"{key}: (empty)"
            else:
                # A scalar array item -- an inventory addition, a category, a name. Not
                # an action, but very much a thing worth seeing and editing: this row is
                # how Bloodletter is attached to the scimitar the chest hands out.
                label = f"{key}: {value}"
            item = QTreeWidgetItem([label])
            # The index matters: an array holds several items under one key, so
            # "Additions to add" cannot be edited by key alone without hitting the first.
            item.setData(0, Qt.UserRole, (node, key, value, index))
            if not isinstance(value, ResourceNode):
                item.setForeground(0, QBrush(QColor(150, 150, 150)))
            elif not ss.is_known(value.type_name):
                # Outside the curated schema: still fully browsable, just unlabelled.
                item.setForeground(0, QBrush(QColor(200, 180, 120)))
            parent_item.addChild(item)
            if value is want:
                found.append(item)
            if isinstance(value, ResourceNode):
                self._add_children(item, value, found, want)
            item.setExpanded(True)

    def _current(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def _current_node(self):
        cur = self._current()
        return cur[2] if cur and isinstance(cur[2], ResourceNode) else None

    # -- field form -------------------------------------------------------

    def _show_fields(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        cur = self._current()
        node = self._current_node()
        self._update_buttons()

        # A scalar array item edits in place, against its parent array.
        if cur and node is None and not ss.is_empty_slot(cur[2]):
            parent, key, value, index = cur
            widget = QLineEdit(str(value))
            widget.editingFinished.connect(
                lambda w=widget, p=parent, i=index, k=key:
                self._commit_item(p, i, k, w.text()))
            self.form.addRow(key, widget)
            return

        if node is None or node.type_name == "Array":
            return
        if not ss.is_known(node.type_name):
            note = QLabel(f"{node.type_name} - not in the curated schema; "
                          "fields shown as raw text.")
            note.setWordWrap(True)
            self.form.addRow(note)
        for f in ss.scalar_fields(node):
            value = node.get(f.key)
            value = "" if isinstance(value, ResourceNode) else (value or "")
            if f.kind == "enum" and f.choices:
                widget = NoScrollComboBox()
                widget.setEditable(True)      # observed values are common, not exhaustive
                widget.addItems(list(f.choices))
                widget.setCurrentText(value)
                widget.currentTextChanged.connect(
                    lambda text, n=node, k=f.key: self._commit(n, k, text))
            else:
                widget = QLineEdit(value)
                widget.editingFinished.connect(
                    lambda w=widget, n=node, k=f.key: self._commit(n, k, w.text()))
            if f.hint:
                widget.setToolTip(f.hint)
            self.form.addRow(f.key, widget)

    def _commit_item(self, parent, index, key, text):
        if self._building:
            return
        old = parent.fields[index][1]
        if isinstance(old, ResourceNode) or text == old:
            return
        self._push(EditItemCommand(self, parent, index, key, old, text))

    def _commit(self, node, key, text):
        if self._building:
            return
        old = node.get(key)
        old = "" if isinstance(old, ResourceNode) else (old or "")
        if text == old:
            return
        self._push(EditFieldCommand(self, node, key, old, text))

    # -- structural edits -------------------------------------------------

    def _update_buttons(self):
        cur = self._current()
        node = self._current_node()
        parent = cur[0] if cur else None
        in_array = isinstance(parent, ResourceNode) and parent.type_name == "Array"

        can_add = False
        if cur:
            target, key, value, _ = cur
            # Into an action array, or into an action slot that is currently empty.
            # Not into "Additions to add" and friends: those hold item paths, and
            # dropping a C*Action node into one would write a nested object where the
            # engine reads a string.
            can_add = ss.accepts_actions(target, key, value) or (
                ss.is_empty_slot(value) and key in ss.ACTION_SLOT_KEYS)
        self.add_button.setEnabled(bool(can_add))
        self.delete_button.setEnabled(node is not None and node.type_name != "Array")

        movable = False
        if in_array and node is not None:
            items = [v for _, _, v in ss.array_items(parent)]
            movable = len(items) > 1
        pos = None
        if movable:
            items = [v for _, _, v in ss.array_items(parent)]
            pos = items.index(node)
        self.up_button.setEnabled(bool(movable) and pos > 0)
        self.down_button.setEnabled(bool(movable) and pos < len(
            ss.array_items(parent)) - 1 if movable else False)

    def add_action(self):
        cur = self._current()
        if not cur:
            return
        target, key, value, _ = cur
        picker = ActionPicker(self)
        if picker.exec() != QDialog.Accepted:
            return
        class_name = picker.chosen()
        if not class_name:
            return
        if isinstance(value, ResourceNode) and value.type_name == "Array":
            # Array items reuse the key their siblings use, defaulting to "Action".
            if not ss.accepts_actions(target, key, value):
                return
            items = ss.array_items(value)
            item_key = items[0][1] if items else "Action"
            self._push(AddActionCommand(self, value, item_key, class_name))
        else:
            self._push(AddActionCommand(self, target, key, class_name))

    def delete_action(self):
        cur = self._current()
        node = self._current_node()
        if not cur or node is None or node.type_name == "Array":
            return
        self._push(DeleteActionCommand(self, cur[0], node))

    def move_action(self, delta: int):
        cur = self._current()
        node = self._current_node()
        if not cur or node is None:
            return
        parent = cur[0]
        if not isinstance(parent, ResourceNode) or parent.type_name != "Array":
            return
        self._push(MoveActionCommand(self, parent, node, delta))
