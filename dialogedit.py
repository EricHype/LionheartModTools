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
    QApplication, QDockWidget, QFileDialog, QFormLayout, QGraphicsItem,
    QGraphicsPathItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QTreeWidget, QTreeWidgetItem, QUndoView, QVBoxLayout, QWidget,
)

import dialogtree_format as dtf
from qtwidgets import NoScrollComboBox

# Files under here are the installed game: reference only, never written to.
DEFAULT_GAME_DATA = (
    r"C:\Program Files (x86)\GOG Galaxy\Games"
    r"\Lionheart - Legacy of the Crusader\data"
)

NODE_W = 300
NODE_PAD = 14
COL_GAP = 190
ROW_GAP = 34

# Changed-since-opened. Deliberately a cool blue: green is the entry node and amber means
# unreachable, so a third state needs to be nobody else's colour.
TOUCHED = QColor(96, 178, 236)


# ---------------------------------------------------------------------------
# Undo commands
#
# Every command's label is written to be read, not just to fill an Undo menu entry: the
# Edits dock lists them, and a list of "Retarget reply" x6 tells you nothing about which
# reply went where. That mattered once already -- a stray mouse wheel silently retargeted
# a reply, and nothing on screen said so.
# ---------------------------------------------------------------------------

def _snip(text: str, limit: int = 34) -> str:
    """A value short enough for a menu line, with the ellipsis inside the quotes."""
    text = " ".join((text or "").split())
    if not text:
        return "(empty)"
    return f'"{text}"' if len(text) <= limit else f'"{text[:limit - 1]}..."'


def _target_name(goto: str) -> str:
    return goto.strip() or "(ends the conversation)"


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


class _AddNode(QUndoCommand):
    def __init__(self, window, node):
        super().__init__(f"Add node {node.node_id}")
        self.window, self.node = window, node

    def redo(self):
        self.window.tree.add_node(self.node)
        self.window.after_structural_edit(select=self.node)

    def undo(self):
        self.window.tree.remove_node(self.node)
        self.window.after_structural_edit()


class _DeleteNode(QUndoCommand):
    def __init__(self, window, node):
        super().__init__(f"Delete node {node.node_id}")
        self.window, self.node, self.index = window, node, None

    def redo(self):
        self.index = self.window.tree.remove_node(self.node)
        self.window.after_structural_edit()

    def undo(self):
        self.window.tree.add_node(self.node, self.index)
        self.window.after_structural_edit(select=self.node)


class _RenameNode(QUndoCommand):
    """Change a node's ID, retargeting every reply that points at it.

    The two halves are one command on purpose: a rename that leaves the links behind
    manufactures the same link rot phase 1 exists to repair, and an undo that restored
    only the ID would leave the file worse than it started.
    """

    def __init__(self, window, node, new_id):
        refs = len(window.tree.referrers(node))
        carried = f", carrying {refs} " + ("link" if refs == 1 else "links") if refs else ""
        super().__init__(f"Rename {node.node_id} -> {new_id}{carried}")
        self.window, self.node = window, node
        self.old_id, self.new_id = node.node_id, new_id
        self.moved = []

    def redo(self):
        # Record what each link said before, not just that it pointed here: links match
        # case-insensitively, so a reply reading `10 Goodbye` may target `10 goodbye`.
        # Rewriting it to the canonical spelling on undo would change bytes the user
        # never touched.
        self.moved = [(r, r.goto) for _n, r in self.window.tree.referrers(self.node)]
        self.window.tree.rename_node(self.node, self.new_id)
        self.window.after_edit(relayout=True)

    def undo(self):
        self.node.node_id = self.old_id
        for reply, goto in self.moved:
            reply.goto = goto
        self.window.after_edit(relayout=True)


class _AddReply(QUndoCommand):
    """Append a reply, and on undo take back exactly what was appended.

    Undoing by removing the reply's own window is not enough: adding the first reply to a
    node also inserts the blank line that separates it from the node header, and that
    blank sits *before* the window. Truncating back to the recorded length is exact,
    because `add_reply` only ever appends.
    """

    def __init__(self, window, node):
        super().__init__(f"Add reply to {node.node_id}")
        self.window, self.node = window, node
        self.entries = None
        self.mark = None

    def redo(self):
        self.mark = len(self.node.entries)
        if self.entries is None:
            self.node.add_reply("New reply", "")
        else:
            self.node.entries.extend(self.entries)   # identical objects, exact redo
        self.window.after_structural_edit(select=self.node)

    def undo(self):
        self.entries = self.node.entries[self.mark:]
        del self.node.entries[self.mark:]
        self.window.after_structural_edit(select=self.node)


class _DeleteReply(QUndoCommand):
    def __init__(self, window, node, index):
        super().__init__(f"Delete reply {_snip(node.replies[index].text)} "
                         f"from {node.node_id}")
        self.window, self.node, self.index = window, node, index
        self.entries = None
        self.at = None

    def redo(self):
        reply = self.node.replies[self.index]
        # `remove_reply` decides where the cut starts -- deleting the last reply takes
        # the blank line before it too -- so take the index from it, not from the reply.
        self.at, self.entries = self.node.remove_reply(reply)
        self.window.after_structural_edit(select=self.node)

    def undo(self):
        self.node.insert_reply_entries(self.at, self.entries)
        self.window.after_structural_edit(select=self.node)


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
        self.setToolTip("Changed since this file was opened."
                        if self.window.is_touched(self.node) else "")
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, NODE_W, self.height)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        selected = self.isSelected()
        entry = self.window.is_entry_node(self.node)
        orphan = self.window.is_orphan(self.node)
        touched = self.window.is_touched(self.node)

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

        title_w = NODE_W - NODE_PAD * 2
        if touched:
            # A dot rather than a different fill: a node can be the entry point, or
            # unreachable, *and* edited, and those already own the fill and the border.
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(TOUCHED))
            painter.drawEllipse(QPointF(NODE_W - NODE_PAD - 3, NODE_PAD + 4), 4.0, 4.0)
            painter.setBrush(Qt.NoBrush)
            title_w -= 14

        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(9.5)
        painter.setFont(font)
        painter.setPen(QColor(235, 220, 160) if not orphan else QColor(235, 190, 120))
        painter.drawText(QRectF(NODE_PAD, NODE_PAD - 4, title_w, 18),
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

    def __init__(self, source: NodeItem, target: NodeItem, label: str, broken=False,
                 touched=False):
        super().__init__()
        self.source, self.target, self.label = source, target, label
        self.broken = broken
        if broken:
            colour, width = QColor(190, 90, 80), 1.4
        elif touched:
            # Links added or retargeted this session, drawn to be noticed: a wrong-but-
            # valid target is a working link to the wrong place, so no validator catches
            # it and only seeing it will do.
            colour, width = TOUCHED, 2.2
        else:
            colour, width = QColor(120, 130, 145), 1.4
        self.setPen(QPen(colour, width, Qt.DashLine if broken else Qt.SolidLine))
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
        self._baseline: dict = {}
        self._touched: set = set()

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
        self._snapshot()
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

        id_row = QHBoxLayout()
        self.id_label = QLabel("<b>Node ID</b>")
        id_row.addWidget(self.id_label)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("Node ID")
        self.id_edit.setToolTip(
            "Renaming retargets every reply in this file that points here.\n"
            "It cannot follow references from map scripts.")
        self.id_edit.editingFinished.connect(self._commit_id)
        id_row.addWidget(self.id_edit, 1)
        layout.addLayout(id_row)

        layout.addWidget(QLabel("<b>NPC line</b>"))
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Select a node in the graph.")
        self.text_edit.focusOutEvent = self._wrap_focus_out(self.text_edit)
        layout.addWidget(self.text_edit, 2)

        reply_header = QHBoxLayout()
        reply_header.addWidget(QLabel("<b>Replies</b>"))
        reply_header.addStretch(1)
        self.add_reply_button = QPushButton("Add reply")
        self.add_reply_button.clicked.connect(self.add_reply)
        reply_header.addWidget(self.add_reply_button)
        layout.addLayout(reply_header)
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

        # Every edit, in order, in one list. The Problems dock only catches edits that
        # break something; this catches the ones that don't -- a reply retargeted to a
        # node that exists is a valid file and a broken conversation, and the only way
        # to notice is to see that it was changed at all.
        self.edit_view = QUndoView(self.undo_stack)
        self.edit_view.setEmptyLabel("Nothing changed yet")
        edits = QDockWidget("Edits", self)
        edits.setWidget(self.edit_view)
        self.addDockWidget(Qt.BottomDockWidgetArea, edits)
        self.tabifyDockWidget(issues, edits)
        issues.raise_()

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
        edit_menu.addSeparator()
        new_node = edit_menu.addAction("&New Node")
        new_node.setShortcut(QKeySequence("Ctrl+N"))
        new_node.triggered.connect(self.add_node)
        del_node = edit_menu.addAction("Delete &Node")
        del_node.setShortcut(QKeySequence("Ctrl+Shift+Delete"))
        del_node.triggered.connect(self.delete_node)
        add_reply = edit_menu.addAction("Add &Reply")
        add_reply.setShortcut(QKeySequence("Ctrl+R"))
        add_reply.triggered.connect(self.add_reply)

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

    # -- what has changed since the file was opened ------------------------

    def _snapshot(self):
        """Remember every node exactly as it was on disk.

        "Touched" is then *derived* by comparison rather than tracked as edits happen.
        That is the whole point: an undo clears the mark for free, a redo restores it,
        and the marks cannot drift out of step with the file the way a hand-maintained
        set would. It survives a save deliberately -- the question being answered is
        "what have I changed in this session", which is what you want to review before
        deploying, and saving is not the end of a session.
        """
        # Keyed by id() because Node is a dataclass and so unhashable -- and the node
        # itself is kept in the value to pin it alive, which is what makes the id safe
        # to use as a key: a freed node's id can be handed to a later one.
        self._baseline = {id(n): (n, *self._fingerprint(n)) for n in self.tree.nodes}
        self._recompute_touched()

    @staticmethod
    def _fingerprint(node):
        """The node's bytes, and where each of its replies pointed, keyed by reply text.

        Keyed by *text*, not by the target and not by position. Keying by target was the
        obvious choice and was wrong in the one case this feature exists for: the reply
        that got retargeted by accident was sent to `10 Transformation`, and its node
        already linked there through a different reply, so the link looked pre-existing
        and nothing lit up. Keying by text also survives replies being added or deleted
        above it, which shifts positions.
        """
        links: dict[str, set[str]] = {}
        for r in node.replies:
            links.setdefault(" ".join(r.text.split()).lower(), set()).add(
                dtf.normalise_id(r.goto))
        return ("\n".join(node.to_lines()), links)

    def _recompute_touched(self):
        self._touched = {
            id(node) for node in self.tree.nodes
            if id(node) not in self._baseline
            or self._baseline[id(node)][1] != "\n".join(node.to_lines())
        }

    def is_touched(self, node) -> bool:
        """Has this node changed since the file was opened? Added counts as changed."""
        return id(node) in self._touched

    def is_new_link(self, node, reply) -> bool:
        """Did this reply point somewhere else when the file was opened?

        A retarget reads as the old link disappearing and a new one appearing, which is
        the right way round: the new edge is the one worth looking at. A reply whose text
        was rewritten counts as new too -- it is no longer the reply that was there.
        """
        base = self._baseline.get(id(node))
        if base is None:
            return True
        was = base[2].get(" ".join(reply.text.split()).lower())
        return was is None or dtf.normalise_id(reply.goto) not in was

    def rebuild_scene(self):
        """Lay the graph out in columns by distance from the entry node.

        A layered layout matches how dialogue is actually written -- opening line, then
        what it leads to -- and keeps the common backward "anything else?" edges as
        visible returns rather than tangling the columns.
        """
        self._orphans = set(self.tree.unreachable_nodes())
        self._recompute_touched()
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
                edge = EdgeItem(source, self.node_items[id(target_node)], reply.text,
                                touched=self.is_new_link(node, reply))
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
            self.id_edit.setText("")
            self.id_edit.setEnabled(False)
            self._updating = False
            return

        refs = len(self.tree.referrers(node))
        note = "the entry node" if self.is_entry_node(node) else (
            f"{refs} reply links here" if refs == 1 else f"{refs} replies link here")
        self.header_label.setText(f"<i>{note}</i>")
        self.id_edit.setEnabled(not self.read_only)
        self.id_edit.setText(node.node_id)
        self.text_edit.setEnabled(True)
        self.text_edit.setPlainText(node.text)

        ids = [""] + [n.node_id for n in self.tree.nodes]
        for index, reply in enumerate(node.replies):
            box = QWidget()
            form = QFormLayout(box)
            form.setContentsMargins(0, 4, 0, 10)

            text = QLineEdit(reply.text)
            text.editingFinished.connect(
                lambda w=text, r=reply: self._commit(r, "text", w.text()))
            form.addRow("Reply", text)

            target = NoScrollComboBox()
            target.setEditable(True)
            target.addItems(ids)
            # `setCurrentText` on an editable combo sets the text and leaves the index
            # alone, so the box would show one node while its index pointed at another.
            # Keyboard and wheel navigation move the *index*, which made the first
            # keystroke land somewhere unrelated to what was on screen.
            known = target.findText(reply.goto)
            if known >= 0:
                target.setCurrentIndex(known)
            else:
                target.setCurrentText(reply.goto)
            if reply.goto and self.tree.node_by_id(reply.goto) is None:
                target.setStyleSheet("color: #e05a4e;")
            target.currentTextChanged.connect(
                lambda value, r=reply: self._commit(r, "goto", value))
            form.addRow("Goes to", target)

            buttons = QHBoxLayout()
            pick = QPushButton("Pick target in graph")
            pick.clicked.connect(lambda _=False, r=reply: self.begin_retarget(r))
            buttons.addWidget(pick)
            drop = QPushButton("Delete")
            drop.clicked.connect(lambda _=False, i=index: self.delete_reply(i))
            buttons.addWidget(drop)
            holder = QWidget()
            holder.setLayout(buttons)
            form.addRow("", holder)

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

    @staticmethod
    def _one_line(text: str) -> str:
        """Flatten a value to a single line.

        `.DialogTree` is line-based: a value runs to the end of its line, so a newline
        inside one splits the record. A trailing newline produces a stray blank line; a
        newline mid-sentence turns the remainder into a bare line the engine would read
        as a malformed key. The NPC line is edited in a multi-line box because the lines
        are long and wrap badly in a single-line field, so the flattening happens here
        rather than by forbidding the keypress.
        """
        return " ".join(text.split())

    def _commit_id(self):
        """Rename the selected node, refusing anything that would corrupt the file.

        An empty ID or a duplicate is rejected outright rather than pushed and undone:
        both make the node unreachable, and duplicates make every link to either one
        ambiguous, since matching is by name.
        """
        node = self.selected_node()
        if node is None or self._updating:
            return
        new = self._one_line(self.id_edit.text())
        if new == node.node_id:
            return
        if self.read_only:
            self.id_edit.setText(node.node_id)
            self._reject_if_read_only()
            return
        # Validate before pushing, not inside the command: a redo that raises would
        # leave a half-applied entry on the undo stack.
        clash = self.tree.node_by_id(new)
        if not new:
            reason = "a node needs an ID"
        elif clash is not None and clash is not node:
            reason = f"another node is already called {new!r}"
        else:
            reason = None
        if reason:
            self.id_edit.setText(node.node_id)
            self._announce(f"Kept the old ID: {reason}.")
            return

        was_entry = self.is_entry_node(node)
        command = _RenameNode(self, node, new)
        self._push(command)
        if was_entry:
            self._announce(f"{command.text()}. This is the entry node -- check any map "
                           "script that opens this dialogue by name.")

    def _announce(self, message: str):
        """Say what just changed. Every mutation goes through here.

        The status bar is the weaker half of the pair -- a message you were not looking
        at when it flashed is a message you never saw -- but it is the half that costs
        nothing, and the Edits dock keeps the same text where you can go back to it.
        """
        self.statusBar().showMessage(message, 6000)

    def _push(self, command):
        self.undo_stack.push(command)
        self._announce(command.text())

    @staticmethod
    def _describe(target, attr, old, new) -> str:
        """A label naming what changed, which node it happened to, and to what."""
        if isinstance(target, dtf.Reply):
            where = f" in {target.node.node_id}" if target.node.node_id else ""
            if attr == "goto":
                return (f"Retarget {_snip(target.text)}{where}: "
                        f"{_target_name(old)} -> {_target_name(new)}")
            return f"Edit reply{where}: {_snip(old)} -> {_snip(new)}"
        return f"Edit the line of {target.node_id}"

    def _commit_text(self):
        node = self.selected_node()
        if node is None or self._updating:
            return
        new = self._one_line(self.text_edit.toPlainText())
        if new != node.text:
            self._push(_Edit(self, node, "text", node.text, new,
                             self._describe(node, "text", node.text, new)))

    def _commit(self, target, attr, value):
        if self._updating:
            return
        value = self._one_line(value)
        old = getattr(target, attr)
        if value != old:
            self._push(_Edit(self, target, attr, old, value,
                             self._describe(target, attr, old, value)))

    def after_edit(self, relayout=False):
        self.tree.dirty = True
        self.setWindowTitle(self._title())
        node = self.selected_node()
        # Before the items repaint: `rebuild_scene` does this itself, but the no-relayout
        # path only calls `item.rebuild()`, which reads the set rather than filling it.
        self._recompute_touched()
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

    # -- authoring --------------------------------------------------------

    def after_structural_edit(self, select=None):
        """A node or reply was added or removed: relayout, revalidate, reselect."""
        self.tree.dirty = True
        self.setWindowTitle(self._title())
        self.rebuild_scene()
        if select is not None and id(select) in self.node_items:
            self._updating = True
            self.node_items[id(select)].setSelected(True)
            self._updating = False
            self.view.centerOn(self.node_items[id(select)])
        self._refresh_panel()
        self.run_validate()

    def _reject_if_read_only(self) -> bool:
        if self.read_only:
            self.statusBar().showMessage(
                f"{self.path.name} is in the game directory and cannot be edited. "
                "Copy it into a mod first.", 6000)
            return True
        return False

    def add_node(self):
        if self._reject_if_read_only():
            return
        node = dtf.new_node(self.tree.unique_node_id(), "")
        self._push(_AddNode(self, node))
        self.text_edit.setFocus()

    def delete_node(self):
        if self._reject_if_read_only():
            return
        node = self.selected_node()
        if node is None:
            self.statusBar().showMessage("Select a node to delete.", 4000)
            return
        if self.tree.nodes and node is self.tree.nodes[0]:
            QMessageBox.warning(
                self, "Cannot delete",
                "This is the conversation's entry point -- the first node is where the "
                "conversation starts. Move another node to the top first.")
            return
        # Deleting a node orphans every reply pointing at it, and in-game that presents
        # as a conversation that stops responding. Say how many before doing it.
        refs = self.tree.referrers(node)
        if refs:
            answer = QMessageBox.question(
                self, "Delete node",
                f"{len(refs)} repl{'y' if len(refs) == 1 else 'ies'} link to "
                f"{node.node_id!r}.\n\nDeleting it will leave "
                f"{'that reply' if len(refs) == 1 else 'those replies'} pointing at "
                "nothing, which in-game means the conversation stops responding."
                "\n\nDelete anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self._push(_DeleteNode(self, node))

    def add_reply(self):
        if self._reject_if_read_only():
            return
        node = self.selected_node()
        if node is None:
            self.statusBar().showMessage("Select a node to add a reply to.", 4000)
            return
        self._push(_AddReply(self, node))

    def delete_reply(self, index: int):
        if self._reject_if_read_only():
            return
        node = self.selected_node()
        if node is None or index >= len(node.replies):
            return
        self._push(_DeleteReply(self, node, index))

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
            self._commit(reply, "goto", node.node_id)

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
        # A soft-lock rather than a broken link: nothing dangles, the file is valid, and
        # the player still cannot get out. There is no cancel key in Lionheart dialogue --
        # every conversation is left by choosing a reply that ends it -- so a node with no
        # route to one is a trap.
        for node_id in self.tree.no_way_out():
            item = QListWidgetItem(
                f"no way out: {node_id} -- no sequence of replies from here ever ends "
                "the conversation")
            item.setForeground(QBrush(QColor(224, 90, 78)))
            item.setData(Qt.UserRole, node_id)
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
