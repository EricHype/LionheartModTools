"""A visual editor for `.DialogTree` files.

Dialogue is a graph -- NPC lines as nodes, player replies as edges -- and a graph is the
one shape a text editor shows badly. This draws it, lets you edit the lines, and rewires
replies without hand-matching node ID strings.

Everything rests on `dialogtree_format`, whose round-trip is byte-exact across all 341
shipped files. Text this editor does not touch is reproduced from the file's own lines,
not regenerated, so an edit changes only what was edited.

Run standalone:

    python dialogedit.py "<path to a .DialogTree>"
"""
from __future__ import annotations

import argparse
import math
import sys
import textwrap
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen,
    QPolygonF, QUndoCommand, QUndoStack,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDockWidget, QFileDialog, QFormLayout, QGraphicsItem,
    QGraphicsPathItem, QGraphicsScene, QGraphicsView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import dialogtree_format as dtf

# Files under here are the installed game: reference only, never written to.
DEFAULT_GAME_DATA = (
    r"C:\Program Files (x86)\GOG Galaxy\Games"
    r"\Lionheart - Legacy of the Crusader\data"
)

NODE_W = 300
NODE_PAD = 14
COL_GAP = 190
ROW_GAP = 34


# ---------------------------------------------------------------------------
# Undo commands
# ---------------------------------------------------------------------------

class _Edit(QUndoCommand):
    """Set one attribute on a node or reply. Both are views over the parsed file, so
    assigning through them writes into the entry list the serialiser reads."""

    def __init__(self, window, target, attr, old, new, label):
        super().__init__(label)
        self.window, self.target, self.attr = window, target, attr
        self.old, self.new = old, new

    def _apply(self, value):
        setattr(self.target, self.attr, value)
        self.window.after_edit(relayout=self.attr == "goto")

    def redo(self):
        self._apply(self.new)

    def undo(self):
        self._apply(self.old)


# ---------------------------------------------------------------------------
# Scene items
# ---------------------------------------------------------------------------

class NodeItem(QGraphicsItem):
    """One dialogue node: its ID and the NPC's line, sized to the text."""

    def __init__(self, node: dtf.Node, window):
        super().__init__()
        self.node = node
        self.window = window
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setAcceptHoverEvents(True)
        self._hover = False
        self.rebuild()

    def rebuild(self):
        self.prepareGeometryChange()
        self.title = self.node.node_id or "(no id)"
        body = self.node.text or "(no text)"
        self.body_lines = textwrap.wrap(body, 42)[:6] or [""]
        if len(textwrap.wrap(body, 42)) > 6:
            self.body_lines[-1] += " ..."
        self.height = NODE_PAD * 2 + 20 + 16 * len(self.body_lines)
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, NODE_W, self.height)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        selected = self.isSelected()
        entry = self.window.is_entry_node(self.node)
        orphan = self.window.is_orphan(self.node)

        if entry:
            fill = QColor(48, 74, 58)        # the way in
        elif orphan:
            fill = QColor(74, 60, 40)        # nothing links here
        else:
            fill = QColor(48, 52, 62)
        border = QColor(235, 200, 110) if selected else (
            QColor(150, 150, 160) if self._hover else QColor(90, 94, 104))

        path = QPainterPath()
        path.addRoundedRect(self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        painter.fillPath(path, QBrush(fill))
        painter.setPen(QPen(border, 2.0 if selected else 1.0))
        painter.drawPath(path)

        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(9.5)
        painter.setFont(font)
        painter.setPen(QColor(235, 220, 160) if not orphan else QColor(235, 190, 120))
        painter.drawText(QRectF(NODE_PAD, NODE_PAD - 4, NODE_W - NODE_PAD * 2, 18),
                         Qt.AlignLeft | Qt.AlignVCenter, self.title)

        font.setBold(False)
        font.setPointSizeF(9.0)
        painter.setFont(font)
        painter.setPen(QColor(212, 214, 220))
        y = NODE_PAD + 18
        for line in self.body_lines:
            painter.drawText(QRectF(NODE_PAD, y, NODE_W - NODE_PAD * 2, 16),
                             Qt.AlignLeft | Qt.AlignVCenter, line)
            y += 16

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()

    def mousePressEvent(self, event):
        if self.window.retarget_reply is not None:
            self.window.finish_retarget(self.node)
            event.accept()
            return
        super().mousePressEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.window.update_edges()
        return super().itemChange(change, value)


class EdgeItem(QGraphicsPathItem):
    """A reply, drawn from its node to its target."""

    def __init__(self, source: NodeItem, target: NodeItem, label: str, broken=False):
        super().__init__()
        self.source, self.target, self.label = source, target, label
        self.broken = broken
        colour = QColor(190, 90, 80) if broken else QColor(120, 130, 145)
        self.setPen(QPen(colour, 1.4, Qt.DashLine if broken else Qt.SolidLine))
        self.setZValue(-1)
        self.refresh()

    def refresh(self):
        if self.source is None or self.target is None:
            return
        a = self.source.scenePos() + QPointF(NODE_W, self.source.height / 2)
        b = self.target.scenePos() + QPointF(0, self.target.height / 2)
        # Route backward edges around rather than through the boxes -- dialogue loops
        # back constantly ("anything else?") and straight lines through text are unreadable.
        dx = max(60.0, abs(b.x() - a.x()) * 0.45)
        path = QPainterPath(a)
        path.cubicTo(a + QPointF(dx, 0), b - QPointF(dx, 0), b)
        self.setPath(path)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------

class DialogView(QGraphicsView):
    MIN_SCALE, MAX_SCALE, STEP = 0.12, 3.0, 1.2

    def __init__(self, scene, window):
        super().__init__(scene)
        self.window = window
        self.setRenderHint(QPainter.Antialiasing)
        # NoDrag, not RubberBandDrag: dragging the background pans instead. Rubber-band
        # selection bought nothing here -- the node panel only ever reads one selected
        # node, so selecting several did nothing you could act on -- and on a laptop
        # trackpad, left-drag is the only drag there is.
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(32, 34, 40)))
        self.setMouseTracking(True)
        self._pan_from = None
        self._pan_moved = False
        self._space_held = False
        # Space only reaches keyPressEvent if the view can hold focus. It still types
        # normally in the dock's text fields, which take focus away from the view.
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta:
            return super().wheelEvent(event)
        factor = self.STEP if delta > 0 else 1 / self.STEP
        scale = self.transform().m11() * factor
        if self.MIN_SCALE <= scale <= self.MAX_SCALE:
            self.scale(factor, factor)
        event.accept()

    # -- panning ----------------------------------------------------------
    #
    # A dialogue graph is far wider than the window -- Acolyte alone is 20 nodes across
    # six columns, and the big ones are several times that -- so getting around matters
    # more than it does in a map view.
    #
    # Left-drag the background is the primary way, because on a laptop trackpad it is the
    # only drag there is. Dragging a node still moves the node; only empty canvas pans.
    # Middle-drag and Space+drag also work and pan from anywhere, including from on top
    # of a node.
    #
    # Implemented by moving the scrollbars rather than by switching to ScrollHandDrag,
    # which only ever listens to the left button and would take node dragging with it.

    def _pannable_background(self, pos) -> bool:
        """True when there is no item under the cursor, so a drag here should pan."""
        return self.itemAt(pos) is None

    def _start_pan(self, pos):
        self._pan_from = pos
        self._pan_moved = False
        self.viewport().setCursor(Qt.ClosedHandCursor)

    def _end_pan(self):
        self._pan_from = None
        self.viewport().setCursor(Qt.OpenHandCursor if self._space_held
                                  else Qt.ArrowCursor)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        if event.button() == Qt.MiddleButton or (
                event.button() == Qt.LeftButton
                and (self._space_held or self._pannable_background(pos))):
            self._start_pan(pos)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_from is not None:
            pos = event.position().toPoint()
            delta = pos - self._pan_from
            self._pan_from = pos
            if delta.x() or delta.y():
                self._pan_moved = True
            h, v = self.horizontalScrollBar(), self.verticalScrollBar()
            h.setValue(h.value() - delta.x())
            v.setValue(v.value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)
        # Show the grab cursor over empty canvas, so it reads as draggable before the
        # drag rather than after it.
        if not self._space_held:
            self.viewport().setCursor(
                Qt.OpenHandCursor if self._pannable_background(event.position().toPoint())
                else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self._pan_from is not None and event.button() in (
                Qt.MiddleButton, Qt.LeftButton):
            clicked_without_moving = not self._pan_moved
            self._end_pan()
            # A click on empty canvas still deselects, the way it did when this was a
            # rubber band. Only a click -- a drag that happens to end on empty space
            # must not throw the selection away.
            if clicked_without_moving and event.button() == Qt.LeftButton:
                self.scene().clearSelection()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.window.retarget_reply is not None:
            self.window.cancel_retarget()
            event.accept()
            return
        # isAutoRepeat: holding Space fires press events continuously, and without this
        # the cursor is reset on every repeat while the drag is in progress.
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            if self._pan_from is None:
                self.viewport().setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            if self._pan_from is None:
                self.viewport().unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        # Alt-tabbing away while Space is down would otherwise leave the view stuck in
        # pan mode with no key release ever arriving.
        self._space_held = False
        if self._pan_from is not None:
            self._end_pan()
        self.viewport().unsetCursor()
        super().focusOutEvent(event)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class DialogWindow(QMainWindow):
    def __init__(self, path: Path, mods_root: Path | None = None,
                 game_root: Path | None = None):
        super().__init__()
        self.mods_root = Path(mods_root) if mods_root else Path(__file__).parent / "mods"
        self.game_root = Path(game_root) if game_root else Path(DEFAULT_GAME_DATA)
        self.undo_stack = QUndoStack(self)
        self.retarget_reply = None
        self.node_items: dict[int, NodeItem] = {}
        self.edges: list[EdgeItem] = []
        self._updating = False
        self.read_only = False

        self.scene = QGraphicsScene(self)
        self.view = DialogView(self.scene, self)
        self.setCentralWidget(self.view)

        self._build_docks()
        self._build_menus()
        self._populate_file_list()
        self.load_file(path)

    def _title(self):
        mark = "*" if self.tree.dirty else ""
        tag = "  [read-only]" if self.read_only else ""
        return f"{self.path.name}{mark}{tag} - Dialogue Editor"

    # -- files ------------------------------------------------------------

    def _is_game_file(self, path: Path) -> bool:
        """True only for files inside the installed game.

        Game data is reference material, never an edit target: the toolchain layers mods
        over a pristine backup, so writing into the install would corrupt the thing every
        rebuild restores from. Those files open read-only rather than not opening at all.

        Scoped to the game directory specifically, not "outside mods/" -- that broader
        rule also locked scratch copies and files from another checkout, which there is
        no reason to protect.
        """
        try:
            Path(path).resolve().relative_to(self.game_root.resolve())
            return True
        except (ValueError, OSError):
            return False

    def _populate_file_list(self):
        """Every .DialogTree shipped by a mod, grouped by mod."""
        self.file_tree.clear()
        by_mod: dict[str, list[Path]] = {}
        if self.mods_root.is_dir():
            for p in sorted(self.mods_root.glob("*/**/*.DialogTree")):
                mod = p.relative_to(self.mods_root).parts[0]
                by_mod.setdefault(mod, []).append(p)
        total = 0
        for mod, paths in sorted(by_mod.items()):
            group = QTreeWidgetItem([mod])
            group.setFlags(group.flags() & ~Qt.ItemIsSelectable)
            self.file_tree.addTopLevelItem(group)
            for p in paths:
                leaf = QTreeWidgetItem([p.stem])
                leaf.setData(0, Qt.UserRole, str(p))
                # The folder under Dialog/ is how you tell two same-named files apart --
                # Herbalist Dialogue exists in two mods and in two areas.
                leaf.setToolTip(0, str(p))
                parent_dir = p.parent.name
                if parent_dir:
                    leaf.setText(0, f"{p.stem}   ({parent_dir})")
                group.addChild(leaf)
                total += 1
            group.setExpanded(True)
        if total == 0:
            self.file_tree.addTopLevelItem(
                QTreeWidgetItem([f"No .DialogTree files under {self.mods_root}"]))

    def _on_file_filter(self, text: str):
        text = text.lower()
        for i in range(self.file_tree.topLevelItemCount()):
            group = self.file_tree.topLevelItem(i)
            any_visible = False
            for j in range(group.childCount()):
                child = group.child(j)
                hit = text in child.text(0).lower() or text in group.text(0).lower()
                child.setHidden(not hit)
                any_visible = any_visible or hit
            group.setHidden(not any_visible)
            if text:
                group.setExpanded(True)

    def _on_file_activated(self, item, _column=0):
        path = item.data(0, Qt.UserRole)
        if path and Path(path) != self.path:
            self.load_file(Path(path))

    def _confirm_discard(self) -> bool:
        """Ask before dropping unsaved edits. True means it is safe to proceed."""
        if not getattr(self, "tree", None) or not self.tree.dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes",
            f"{self.path.name} has unsaved changes.\n\nSave before switching?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            return self.save()
        return True

    def load_file(self, path):
        path = Path(path)
        if not self._confirm_discard():
            self._sync_file_selection()
            return False
        try:
            tree = dtf.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not open", f"{path.name}\n\n{exc}")
            self._sync_file_selection()
            return False

        self.tree = tree
        self.path = path
        self.read_only = self._is_game_file(path)
        self.undo_stack.clear()
        self.retarget_reply = None
        self.rebuild_scene()
        self.run_validate()
        self._refresh_panel()
        self._sync_file_selection()
        self.setWindowTitle(self._title())
        self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40),
                            Qt.KeepAspectRatio)
        self.statusBar().showMessage(
            f"{path.name}: {len(self.tree.nodes)} nodes"
            + ("  --  read-only, this file is in the game directory"
               if self.read_only else ""), 6000)
        return True

    def _sync_file_selection(self):
        """Highlight the open file in the list, without triggering a reload."""
        self.file_tree.blockSignals(True)
        self.file_tree.clearSelection()
        for i in range(self.file_tree.topLevelItemCount()):
            group = self.file_tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                data = child.data(0, Qt.UserRole)
                if data and Path(data) == self.path:
                    child.setSelected(True)
                    self.file_tree.setCurrentItem(child)
        self.file_tree.blockSignals(False)

    def open_dialog(self):
        start = str(self.path.parent if self.path else self.mods_root)
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Open a DialogTree", start, "DialogTree (*.DialogTree);;All files (*)")
        if chosen:
            self.load_file(Path(chosen))

    # -- docks ------------------------------------------------------------

    def _build_docks(self):
        # File list: every DialogTree a mod ships. Without it the window only ever
        # showed whatever single path was on the command line, with no way to reach the
        # rest -- which is exactly how the Test Pocket dialogues stayed invisible.
        files = QWidget()
        files_layout = QVBoxLayout(files)
        files_layout.setContentsMargins(4, 4, 4, 4)
        self.file_filter = QLineEdit()
        self.file_filter.setPlaceholderText("Filter files...")
        self.file_filter.textChanged.connect(self._on_file_filter)
        files_layout.addWidget(self.file_filter)
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["Dialogue files"])
        self.file_tree.itemClicked.connect(self._on_file_activated)
        files_layout.addWidget(self.file_tree)
        file_dock = QDockWidget("Files", self)
        file_dock.setWidget(files)
        self.addDockWidget(Qt.LeftDockWidgetArea, file_dock)

        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.header_label = QLabel()
        self.header_label.setWordWrap(True)
        layout.addWidget(self.header_label)

        layout.addWidget(QLabel("<b>NPC line</b>"))
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Select a node in the graph.")
        self.text_edit.focusOutEvent = self._wrap_focus_out(self.text_edit)
        layout.addWidget(self.text_edit, 2)

        layout.addWidget(QLabel("<b>Replies</b>"))
        self.reply_host = QWidget()
        self.reply_form = QVBoxLayout(self.reply_host)
        self.reply_form.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.reply_host)
        layout.addWidget(scroll, 3)

        dock = QDockWidget("Node", self)
        dock.setWidget(panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        self.issue_list = QListWidget()
        self.issue_list.itemClicked.connect(self._on_issue_clicked)
        issues = QDockWidget("Problems", self)
        issues.setWidget(self.issue_list)
        self.addDockWidget(Qt.BottomDockWidgetArea, issues)

        self.scene.selectionChanged.connect(self._on_selection)
        self.statusBar()

    def _wrap_focus_out(self, widget):
        original = QPlainTextEdit.focusOutEvent

        def handler(event):
            self._commit_text()
            original(widget, event)
        return handler

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        open_action = file_menu.addAction("&Open...")
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.open_dialog)
        file_menu.addSeparator()
        save = file_menu.addAction("&Save")
        save.setShortcut(QKeySequence.Save)
        save.triggered.connect(self.save)

        edit_menu = self.menuBar().addMenu("&Edit")
        undo = self.undo_stack.createUndoAction(self, "&Undo")
        undo.setShortcut(QKeySequence.Undo)
        redo = self.undo_stack.createRedoAction(self, "&Redo")
        redo.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        edit_menu.addAction(undo)
        edit_menu.addAction(redo)

        view_menu = self.menuBar().addMenu("&View")
        fit = view_menu.addAction("&Fit")
        fit.setShortcut(QKeySequence("Ctrl+0"))
        fit.triggered.connect(lambda: self.view.fitInView(
            self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40),
            Qt.KeepAspectRatio))
        relayout = view_menu.addAction("Re-&layout")
        relayout.setShortcut(QKeySequence("Ctrl+L"))
        relayout.triggered.connect(self.rebuild_scene)

    # -- graph ------------------------------------------------------------

    def is_entry_node(self, node) -> bool:
        return bool(self.tree.nodes) and node is self.tree.nodes[0]

    def is_orphan(self, node) -> bool:
        return node.node_id in self._orphans

    def rebuild_scene(self):
        """Lay the graph out in columns by distance from the entry node.

        A layered layout matches how dialogue is actually written -- opening line, then
        what it leads to -- and keeps the common backward "anything else?" edges as
        visible returns rather than tangling the columns.
        """
        self._orphans = set(self.tree.unreachable_nodes())
        self.scene.clear()
        self.node_items.clear()
        self.edges.clear()

        by_id = {dtf.normalise_id(n.node_id): n for n in self.tree.nodes}
        depth: dict[int, int] = {}
        if self.tree.nodes:
            frontier = [self.tree.nodes[0]]
            depth[id(self.tree.nodes[0])] = 0
            while frontier:
                nxt = []
                for node in frontier:
                    for reply in node.replies:
                        target = by_id.get(dtf.normalise_id(reply.goto)) if reply.goto else None
                        if target is not None and id(target) not in depth:
                            depth[id(target)] = depth[id(node)] + 1
                            nxt.append(target)
                frontier = nxt
        # Anything unreachable goes in a trailing column rather than being hidden.
        max_depth = max(depth.values(), default=0)
        for node in self.tree.nodes:
            depth.setdefault(id(node), max_depth + 1)

        columns: dict[int, list] = {}
        for node in self.tree.nodes:
            columns.setdefault(depth[id(node)], []).append(node)

        x = 0.0
        for col in sorted(columns):
            y = 0.0
            for node in columns[col]:
                item = NodeItem(node, self)
                item.setPos(x, y)
                self.scene.addItem(item)
                self.node_items[id(node)] = item
                y += item.height + ROW_GAP
            x += NODE_W + COL_GAP

        for node in self.tree.nodes:
            source = self.node_items[id(node)]
            for reply in node.replies:
                if not reply.goto:
                    continue
                target_node = by_id.get(dtf.normalise_id(reply.goto))
                if target_node is None:
                    continue        # dangling; reported in Problems, no edge to draw
                edge = EdgeItem(source, self.node_items[id(target_node)], reply.text)
                self.scene.addItem(edge)
                self.edges.append(edge)

    def update_edges(self):
        for edge in self.edges:
            edge.refresh()

    # -- selection and editing -------------------------------------------

    def selected_node(self):
        items = [i for i in self.scene.selectedItems() if isinstance(i, NodeItem)]
        return items[0].node if items else None

    def _on_selection(self):
        if self._updating:
            return
        self._refresh_panel()

    def _refresh_panel(self):
        self._updating = True
        while self.reply_form.count():
            item = self.reply_form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        node = self.selected_node()
        if node is None:
            self.header_label.setText(
                f"<b>{self.tree.name or self.path.stem}</b><br>"
                f"{len(self.tree.nodes)} nodes. Select one to edit it.")
            self.text_edit.setPlainText("")
            self.text_edit.setEnabled(False)
            self._updating = False
            return

        self.header_label.setText(f"<b>{node.node_id}</b>")
        self.text_edit.setEnabled(True)
        self.text_edit.setPlainText(node.text)

        ids = [""] + [n.node_id for n in self.tree.nodes]
        for reply in node.replies:
            box = QWidget()
            form = QFormLayout(box)
            form.setContentsMargins(0, 4, 0, 10)

            text = QLineEdit(reply.text)
            text.editingFinished.connect(
                lambda w=text, r=reply: self._commit(r, "text", w.text(), "Edit reply"))
            form.addRow("Reply", text)

            target = QComboBox()
            target.setEditable(True)
            target.addItems(ids)
            target.setCurrentText(reply.goto)
            if reply.goto and self.tree.node_by_id(reply.goto) is None:
                target.setStyleSheet("color: #e05a4e;")
            target.currentTextChanged.connect(
                lambda value, r=reply: self._commit(r, "goto", value, "Retarget reply"))
            form.addRow("Goes to", target)

            pick = QPushButton("Pick target in graph")
            pick.clicked.connect(lambda _=False, r=reply: self.begin_retarget(r))
            form.addRow("", pick)

            bits = []
            if reply.requirement and reply.requirement != "!None":
                bits.append(f"requires {reply.requirement}")
            if reply.icon:
                bits.append(reply.icon)
            if reply.is_default:
                bits.append("default")
            if reply.block_for("Custom Action"):
                bits.append("has a custom action")
            if bits:
                note = QLabel("<i>" + ", ".join(bits) + "</i>")
                note.setWordWrap(True)
                form.addRow("", note)

            self.reply_form.addWidget(box)
        self.reply_form.addStretch(1)
        self._updating = False

    def _commit_text(self):
        node = self.selected_node()
        if node is None or self._updating:
            return
        new = self.text_edit.toPlainText()
        if new != node.text:
            self.undo_stack.push(_Edit(self, node, "text", node.text, new, "Edit line"))

    def _commit(self, target, attr, value, label):
        if self._updating:
            return
        old = getattr(target, attr)
        if value != old:
            self.undo_stack.push(_Edit(self, target, attr, old, value, label))

    def after_edit(self, relayout=False):
        self.tree.dirty = True
        self.setWindowTitle(self._title())
        node = self.selected_node()
        if relayout:
            self.rebuild_scene()
            if node is not None and id(node) in self.node_items:
                self._updating = True
                self.node_items[id(node)].setSelected(True)
                self._updating = False
        else:
            for item in self.node_items.values():
                item.rebuild()
        self._refresh_panel()
        self.run_validate()

    # -- retargeting by clicking --------------------------------------

    def begin_retarget(self, reply):
        self.retarget_reply = reply
        self.statusBar().showMessage(
            "Click the node this reply should lead to. Escape cancels.")

    def cancel_retarget(self):
        self.retarget_reply = None
        self.statusBar().clearMessage()

    def finish_retarget(self, node):
        reply = self.retarget_reply
        self.retarget_reply = None
        self.statusBar().clearMessage()
        if reply is not None:
            self._commit(reply, "goto", node.node_id, "Retarget reply")

    # -- validation -------------------------------------------------------

    def run_validate(self):
        self.issue_list.clear()
        for source, text, target in self.tree.dangling_targets():
            item = QListWidgetItem(
                f"dead end: {source}  --  reply {text[:40]!r} points at "
                f"{target!r}, which no node matches")
            item.setForeground(QBrush(QColor(224, 90, 78)))
            item.setData(Qt.UserRole, source)
            self.issue_list.addItem(item)
        for node_id in self.tree.unreachable_nodes():
            item = QListWidgetItem(f"unreachable: nothing links to {node_id}")
            item.setForeground(QBrush(QColor(210, 170, 100)))
            item.setData(Qt.UserRole, node_id)
            self.issue_list.addItem(item)
        if self.issue_list.count() == 0:
            self.issue_list.addItem("No problems found.")

    def _on_issue_clicked(self, item):
        node_id = item.data(Qt.UserRole)
        if not node_id:
            return
        node = self.tree.node_by_id(node_id)
        if node is None or id(node) not in self.node_items:
            return
        target = self.node_items[id(node)]
        self.scene.clearSelection()
        target.setSelected(True)
        self.view.centerOn(target)

    # -- save -------------------------------------------------------------

    def save(self) -> bool:
        if self.read_only:
            # Refusing rather than silently writing: the toolchain layers mods over a
            # pristine backup, so editing the installed game in place would corrupt the
            # thing every rebuild is restored from.
            QMessageBox.warning(
                self, "Read-only",
                f"{self.path.name} is in the game directory, not a mod.\n\n"
                "Copy it into a mod's files/ tree and open it from there.")
            return False
        try:
            self.tree.save()
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self.setWindowTitle(self._title())
        self.statusBar().showMessage(
            "Saved. Run modmanager install + build for this to reach the game.", 8000)
        return True

    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()


def _first_dialog(mods_root: Path):
    """Something to show when launched with no argument."""
    found = sorted(Path(mods_root).glob("*/**/*.DialogTree"))
    return found[0] if found else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Lionheart .DialogTree editor")
    parser.add_argument("path", nargs="?", help="path to a .DialogTree file")
    parser.add_argument("--mods-root", default=None,
                        help="directory to list dialogue files from (default: ./mods)")
    parser.add_argument("--game-root", default=None,
                        help="installed game data dir; files here open read-only")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    mods_root = Path(args.mods_root) if args.mods_root else Path(__file__).parent / "mods"
    path = Path(args.path) if args.path else _first_dialog(mods_root)
    if path is None:
        print(f"No .DialogTree files under {mods_root}; pass one explicitly.")
        return 1
    window = DialogWindow(path, mods_root,
                          Path(args.game_root) if args.game_root else None)
    window.resize(1500, 950)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
