"""PySide6 desktop map editor for Lionheart `.zax` files (phase 1: entity placement).

Usage:
    python mapedit.py "<path to .zax>" [--data-root <game data dir>]

All the risky logic -- parsing, editing, validation, terrain rendering, sprite decoding --
lives in `mapedit_core.py` and `zax_render.py`. This file is the Qt front end over them:
`QGraphicsScene`/`QGraphicsView` for the map, dock widgets for the sprite palette,
selected-entity properties and live validation issues, and a `QUndoStack` for move / add /
delete. See docs/map-editor-design.md, "The rendering model" and "Phase 1", for the spec.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QMimeData, QProcess, QPointF
from PySide6.QtGui import (
    QImage, QPixmap, QColor, QBrush, QPen, QUndoStack, QUndoCommand, QKeySequence,
    QCursor, QPainter,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsScene, QGraphicsView, QGraphicsPixmapItem,
    QGraphicsEllipseItem, QGraphicsItem, QDockWidget, QWidget, QVBoxLayout, QFormLayout,
    QLineEdit, QDoubleSpinBox, QCheckBox, QListWidget, QListWidgetItem, QTreeWidget,
    QTreeWidgetItem, QLabel, QMessageBox, QDialog, QPlainTextEdit,
    QDialogButtonBox,
)

import zax_render as zr
from mapedit_core import (
    MapDocument, SpriteCatalogue, validate, tiling_vector,
)
from resource_format import ResourceNode

DEFAULT_DATA_ROOT = (
    r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader\data"
)


# ---------------------------------------------------------------------------
# Pixel conversion (shared by entity items and palette thumbnails)
# ---------------------------------------------------------------------------

def pixels_to_qpixmap(data: dict) -> QPixmap:
    """Convert a `SpriteCatalogue.pixels()`/`decode_icon()` dict to a QPixmap."""
    w, h = data["width"], data["height"]
    flat = bytes(v for row in data["rows"] for px in row for v in px)
    img = QImage(flat, w, h, QImage.Format_RGBA8888)
    return QPixmap.fromImage(img.copy())


def make_eyedropper_cursor() -> QCursor:
    """Draw a pipette cursor, hotspot at the tip.

    Drawn rather than shipped as an asset: the project carries no image files, and a
    generated cursor cannot go missing or fail to load. White outline under a dark body
    so it stays visible over both the pale courtyard and dark ground.
    """
    size = 32
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # barrel: a diagonal stroke from the tip (bottom-left) up to the bulb (top-right)
    tip = QPointF(3.5, 28.5)
    neck = QPointF(11.0, 21.0)
    bulb_a = QPointF(19.0, 13.0)
    bulb_b = QPointF(27.0, 5.0)

    outline = QPen(QColor(255, 255, 255, 230), 5.0, Qt.SolidLine, Qt.RoundCap)
    body = QPen(QColor(30, 30, 34), 3.0, Qt.SolidLine, Qt.RoundCap)
    for pen in (outline, body):
        p.setPen(pen)
        p.drawLine(tip, neck)
    # wider barrel section
    for pen, w in ((outline, 9.0), (body, 6.5)):
        p.setPen(QPen(pen.color(), w, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(neck, bulb_a)
    # squeeze bulb
    for pen, w in ((outline, 12.0), (body, 9.0)):
        p.setPen(QPen(pen.color(), w, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(bulb_a, bulb_b)
    # a bright dot exactly on the hotspot, so the pick point is unambiguous
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(QPointF(2.5, 29.5), 1.6, 1.6)
    p.end()
    return QCursor(pm, 2, 30)


def render_terrain_pixmap(doc: MapDocument, data_root: Path) -> QPixmap:
    """Render the map's terrain to a full-resolution QPixmap, reusing zax_render's terrain
    code (not reimplementing it). Falls back to a flat dark pixmap on any failure, per spec.
    """
    w, h = max(1, doc.width), max(1, doc.height)
    try:
        canvas = zr.Canvas(w, h, zr.BACKGROUND)
        plasma = doc.root.get("Plasma Ground")
        if isinstance(plasma, ResourceNode):
            zr.render_terrain(canvas, data_root, plasma, elevation_textures=True)
        img = QImage(bytes(canvas.pixels), w, h, QImage.Format_RGBA8888)
        return QPixmap.fromImage(img.copy())
    except Exception:
        pm = QPixmap(w, h)
        pm.fill(QColor(*zr.BACKGROUND))
        return pm


# ---------------------------------------------------------------------------
# Scene item for one scenery entity
# ---------------------------------------------------------------------------

class EntityItem(QGraphicsPixmapItem):
    """A placed scenery sprite. Wraps an `Entity`; dragging writes back on release."""

    def __init__(self, entity, pixmap: QPixmap, info, window: "MainWindow"):
        super().__init__(pixmap)
        self.entity = entity
        self.info = info
        self.window = window
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        self.setPos(entity.x - info.hotspot_x, entity.y - info.hotspot_y)
        self.setZValue(entity.y)
        self.setVisible((entity.node.get("Visible") or "1") != "0")
        self._press_pos = None

    def mousePressEvent(self, event):
        self._press_pos = (self.entity.x, self.entity.y)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._press_pos is not None:
            old_x, old_y = self._press_pos
            new_x = self.x() + self.info.hotspot_x
            new_y = self.y() + self.info.hotspot_y
            if round(new_x, 3) != round(old_x, 3) or round(new_y, 3) != round(old_y, 3):
                self.window.push_move(self.entity, self, (old_x, old_y), (new_x, new_y))
            self._press_pos = None


# ---------------------------------------------------------------------------
# Undo commands
# ---------------------------------------------------------------------------

class MoveEntityCommand(QUndoCommand):
    def __init__(self, window: "MainWindow", entity, item: EntityItem, old, new):
        label = f"Move {entity.name or entity.model.rsplit('/', 1)[-1]}"
        super().__init__(label)
        self.window = window
        self.entity = entity
        self.item = item
        self.old = old
        self.new = new

    def _apply(self, x, y):
        self.entity.move_to(x, y)
        self.item.setPos(x - self.item.info.hotspot_x, y - self.item.info.hotspot_y)
        self.item.setZValue(y)
        self.window.doc.dirty = True
        self.window.schedule_validate()

    def redo(self):
        self._apply(*self.new)

    def undo(self):
        self._apply(*self.old)


class AddEntityCommand(QUndoCommand):
    def __init__(self, window: "MainWindow", model, x, y, *,
                 collideable=True, half_height=False):
        super().__init__(f"Add {model.rsplit('/', 1)[-1]}")
        self.window = window
        self.model = model
        self.x = x
        self.y = y
        self.collideable = collideable
        self.half_height = half_height
        self.entity = None  # created on first redo, then identity is preserved
        self.item = None

    def redo(self):
        doc = self.window.doc
        if self.entity is None:
            self.entity = doc.add_entity(
                self.model, self.x, self.y,
                collideable=self.collideable, half_height=self.half_height)
        else:
            doc.tree.fields.append(("Level Part", self.entity.node))
            doc.dirty = True
        self.item = self.window.create_item(self.entity)
        if self.item is not None:
            self.window.scene.addItem(self.item)
            self.window.entity_items[id(self.entity.node)] = self.item
            self.window.select_item(self.item)
        self.window.schedule_validate()

    def undo(self):
        self.window.doc.remove_entity(self.entity)
        if self.item is not None:
            self.window.scene.removeItem(self.item)
            self.window.entity_items.pop(id(self.entity.node), None)
        self.window.schedule_validate()


class DeleteEntityCommand(QUndoCommand):
    def __init__(self, window: "MainWindow", entity, item: EntityItem | None):
        label = f"Delete {entity.name or entity.model.rsplit('/', 1)[-1]}"
        super().__init__(label)
        self.window = window
        self.entity = entity
        self.item = item
        self.index = None  # captured on first redo, for exact reinsertion on undo

    def redo(self):
        doc = self.window.doc
        for i, (key, value) in enumerate(doc.tree.fields):
            if value is self.entity.node:
                self.index = i
                break
        doc.remove_entity(self.entity)
        if self.item is not None:
            self.window.scene.removeItem(self.item)
            self.window.entity_items.pop(id(self.entity.node), None)
        self.window.schedule_validate()

    def undo(self):
        doc = self.window.doc
        idx = self.index if self.index is not None else len(doc.tree.fields)
        doc.tree.fields.insert(idx, ("Level Part", self.entity.node))
        doc.dirty = True
        if self.item is not None:
            self.window.scene.addItem(self.item)
            self.window.entity_items[id(self.entity.node)] = self.item
        self.window.schedule_validate()


# ---------------------------------------------------------------------------
# Palette (directory-grouped tree, lazy thumbnails, drag source)
# ---------------------------------------------------------------------------

class PaletteTree(QTreeWidget):
    """Drag source: mime text is the full model path of the dragged leaf."""

    def mimeData(self, items):
        md = QMimeData()
        leaves = [it for it in items if it.data(0, Qt.UserRole)]
        if leaves:
            md.setText(leaves[0].data(0, Qt.UserRole))
        return md


# ---------------------------------------------------------------------------
# Map view (click-to-place, drag-drop-to-place, otherwise normal QGraphicsView)
# ---------------------------------------------------------------------------

class MapView(QGraphicsView):
    # A 4096x960 map does not fit on screen at 1:1, so zooming out far enough to see the
    # whole thing matters more here than zooming in. Lower bound is generous for that.
    MIN_SCALE = 0.05
    MAX_SCALE = 8.0
    ZOOM_STEP = 1.25

    def __init__(self, scene: QGraphicsScene, window: "MainWindow"):
        super().__init__(scene)
        self.window = window
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setAcceptDrops(True)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        # Needed so holding Alt swaps the cursor without waiting for a click, and so
        # keyPress/keyRelease reach us at all.
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._eyedropper_cursor = make_eyedropper_cursor()
        self._cursor_is_dropper = False

    def refresh_cursor(self) -> None:
        """Show the pipette whenever the next click would pick -- sticky mode OR Alt.

        Driven from mouse-move and key events rather than set once, because the Alt path
        has no toggle to hang it off: without this, holding Alt gives no feedback at all
        until you click and something unexpected happens.
        """
        want = self.window.eyedropper_active(QApplication.keyboardModifiers())
        if want == self._cursor_is_dropper:
            return
        self._cursor_is_dropper = want
        if want:
            self.viewport().setCursor(self._eyedropper_cursor)
        else:
            self.viewport().unsetCursor()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        self.refresh_cursor()

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        self.refresh_cursor()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.refresh_cursor()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.refresh_cursor()

    # -- zoom ------------------------------------------------------------

    def current_scale(self) -> float:
        return self.transform().m11()

    def zoom_by(self, factor: float, anchor_under_mouse: bool = True) -> None:
        """Multiply the zoom, clamped. Keeping the point under the cursor fixed is what
        makes wheel zoom feel right; keyboard zoom anchors on the view centre instead."""
        target = self.current_scale() * factor
        if target < self.MIN_SCALE:
            factor = self.MIN_SCALE / self.current_scale()
        elif target > self.MAX_SCALE:
            factor = self.MAX_SCALE / self.current_scale()
        if abs(factor - 1.0) < 1e-9:
            return
        old_anchor = self.transformationAnchor()
        if not anchor_under_mouse:
            self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.scale(factor, factor)
        self.setTransformationAnchor(old_anchor)
        self.window.report_zoom(self.current_scale())

    def zoom_in(self):
        self.zoom_by(self.ZOOM_STEP, anchor_under_mouse=False)

    def zoom_out(self):
        self.zoom_by(1 / self.ZOOM_STEP, anchor_under_mouse=False)

    def zoom_reset(self):
        self.resetTransform()
        self.window.report_zoom(self.current_scale())

    def zoom_fit(self):
        """Fit the whole map in the view -- the default on open for a wide map."""
        rect = self.scene().sceneRect()
        if rect.isEmpty():
            return
        self.fitInView(rect, Qt.KeepAspectRatio)
        self.window.report_zoom(self.current_scale())

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(self.ZOOM_STEP if delta > 0 else 1 / self.ZOOM_STEP)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            item = self.scene().itemAt(scene_pos, self.transform())

            # Eyedropper: pick the clicked entity's model in the palette, so "more of
            # that" does not mean hunting for it among 4787 entries. Alt is the usual
            # modifier for this in paint tools; the toolbar toggle does the same thing
            # without needing a modifier held.
            if self.window.eyedropper_active(event.modifiers()):
                if isinstance(item, EntityItem):
                    self.window.pick_model(item.entity.model)
                else:
                    self.window.statusBar().showMessage(
                        "Eyedropper: nothing under the cursor.", 3000)
                event.accept()
                return

            if self.window.selected_palette_model and item is None:
                shift = bool(event.modifiers() & Qt.ShiftModifier)
                self.window.place_entity_at(scene_pos.x(), scene_pos.y(), snap=shift)
                event.accept()
                return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        model = event.mimeData().text()
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        scene_pos = self.mapToScene(pos)
        if model:
            self.window.place_entity_at(scene_pos.x(), scene_pos.y(), model=model)
            event.acceptProposedAction()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, zax_path: Path, data_root: Path):
        super().__init__()
        self.doc = MapDocument(zax_path)
        self.cat = SpriteCatalogue(data_root)
        self.data_root = data_root
        # modmanager works on the game directory, which is the data root's parent.
        self.game_dir = Path(data_root).resolve().parent
        self._deploy_proc = None

        self.pixmap_cache: dict[str, QPixmap | None] = {}
        self.entity_items: dict[int, EntityItem] = {}
        self.overlay_items: list[QGraphicsEllipseItem] = []
        self.issues = []
        self.selected_palette_model: str | None = None
        self.last_placed_by_model: dict[str, tuple[float, float]] = {}
        self._updating_props = False
        self._palette_leaf_items: list[QTreeWidgetItem] = []

        self.undo_stack = QUndoStack(self)

        self.validate_timer = QTimer(self)
        self.validate_timer.setSingleShot(True)
        self.validate_timer.timeout.connect(self.run_validate)

        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.timeout.connect(self.apply_filter)

        self._build_scene()
        self._build_ui()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._load_terrain()
            self._populate_scene()
            self._populate_palette()
        finally:
            QApplication.restoreOverrideCursor()

        self.setWindowTitle(self._title())
        self.schedule_validate()

    # -- scene / terrain / entities ---------------------------------------

    def _build_scene(self):
        self.scene = QGraphicsScene(0, 0, self.doc.width, self.doc.height, self)
        self.scene.selectionChanged.connect(self.on_scene_selection_changed)
        self.view = MapView(self.scene, self)
        self.setCentralWidget(self.view)

    def _load_terrain(self):
        pm = render_terrain_pixmap(self.doc, self.data_root)
        bg = QGraphicsPixmapItem(pm)
        bg.setZValue(-1000)
        bg.setPos(0, 0)
        self.scene.addItem(bg)
        self.terrain_item = bg

    def make_pixmap(self, model: str) -> QPixmap | None:
        if model in self.pixmap_cache:
            return self.pixmap_cache[model]
        data = self.cat.pixels(model)
        pm = pixels_to_qpixmap(data) if data is not None else None
        self.pixmap_cache[model] = pm
        return pm

    def create_item(self, entity) -> EntityItem | None:
        info = self.cat.info(entity.model)
        pm = self.make_pixmap(entity.model)
        if info is None or pm is None:
            return None
        return EntityItem(entity, pm, info, self)

    def _populate_scene(self):
        for ent in self.doc.entities():
            if not ent.is_scenery():
                continue
            item = self.create_item(ent)
            if item is None:
                continue
            self.scene.addItem(item)
            self.entity_items[id(ent.node)] = item

    # -- palette ------------------------------------------------------------

    def _build_ui(self):
        # Palette dock
        palette_widget = QWidget()
        palette_layout = QVBoxLayout(palette_widget)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter models…")
        self.filter_edit.textChanged.connect(lambda _: self.filter_timer.start(150))
        palette_layout.addWidget(self.filter_edit)

        self.palette_tree = PaletteTree()
        self.palette_tree.setHeaderHidden(True)
        self.palette_tree.setDragEnabled(True)
        self.palette_tree.itemSelectionChanged.connect(self.on_palette_selection_changed)
        palette_layout.addWidget(self.palette_tree, stretch=1)

        self.preview_label = QLabel("(no preview)")
        self.preview_label.setFixedHeight(100)
        self.preview_label.setAlignment(Qt.AlignCenter)
        palette_layout.addWidget(self.preview_label)

        palette_dock = QDockWidget("Palette", self)
        palette_dock.setWidget(palette_widget)
        self.addDockWidget(Qt.LeftDockWidgetArea, palette_dock)

        # Property dock
        prop_widget = QWidget()
        form = QFormLayout(prop_widget)
        self.prop_name = QLineEdit()
        self.prop_model = QLineEdit()
        self.prop_model.setReadOnly(True)
        self.prop_x = QDoubleSpinBox()
        self.prop_y = QDoubleSpinBox()
        for spin in (self.prop_x, self.prop_y):
            spin.setRange(-1_000_000, 1_000_000)
            spin.setDecimals(2)
        self.prop_visible = QCheckBox("Visible")
        self.prop_collideable = QCheckBox("Collideable")
        self.prop_half = QCheckBox("Half Height")
        self.prop_full = QCheckBox("Full Height")

        self.prop_name.editingFinished.connect(self.on_prop_name_changed)
        self.prop_x.editingFinished.connect(self.on_prop_pos_changed)
        self.prop_y.editingFinished.connect(self.on_prop_pos_changed)
        self.prop_visible.toggled.connect(self.on_prop_visible_changed)
        self.prop_collideable.toggled.connect(self.on_prop_collideable_changed)
        self.prop_half.toggled.connect(self.on_prop_half_changed)
        self.prop_full.toggled.connect(self.on_prop_full_changed)

        form.addRow("Name", self.prop_name)
        form.addRow("Model", self.prop_model)
        form.addRow("Position X", self.prop_x)
        form.addRow("Position Y", self.prop_y)
        form.addRow(self.prop_visible)
        form.addRow(self.prop_collideable)
        form.addRow(self.prop_half)
        form.addRow(self.prop_full)

        prop_dock = QDockWidget("Properties", self)
        prop_dock.setWidget(prop_widget)
        self.addDockWidget(Qt.RightDockWidgetArea, prop_dock)
        self.show_entity_properties(None)

        # Issues dock
        self.issue_list = QListWidget()
        self.issue_list.itemClicked.connect(self.on_issue_clicked)
        issue_dock = QDockWidget("Validation Issues", self)
        issue_dock.setWidget(self.issue_list)
        self.addDockWidget(Qt.BottomDockWidgetArea, issue_dock)

        # Menus
        file_menu = self.menuBar().addMenu("&File")
        save_action = file_menu.addAction("&Save")
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self.save)
        file_menu.addSeparator()
        self.deploy_action = file_menu.addAction("&Deploy to game…")
        self.deploy_action.setShortcut(QKeySequence("Ctrl+B"))
        self.deploy_action.setStatusTip(
            "Save, then run modmanager install + build so the change reaches the game "
            "(several minutes)")
        self.deploy_action.triggered.connect(self.deploy)

        edit_menu = self.menuBar().addMenu("&Edit")
        undo_action = self.undo_stack.createUndoAction(self, "&Undo")
        undo_action.setShortcut(QKeySequence.Undo)
        redo_action = self.undo_stack.createRedoAction(self, "&Redo")
        redo_action.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        edit_menu.addSeparator()
        delete_action = edit_menu.addAction("&Delete Entity")
        delete_action.setShortcut(QKeySequence(Qt.Key_Delete))
        delete_action.triggered.connect(self.delete_selected)

        tools_menu = self.menuBar().addMenu("&Tools")
        self.eyedropper_action = tools_menu.addAction("&Eyedropper")
        self.eyedropper_action.setCheckable(True)
        self.eyedropper_action.setShortcut(QKeySequence("I"))
        self.eyedropper_action.setStatusTip(
            "Click an object to select its model in the palette (or hold Alt)")
        self.eyedropper_action.toggled.connect(self.on_eyedropper_toggled)

        view_menu = self.menuBar().addMenu("&View")
        zoom_in = view_menu.addAction("Zoom &In")
        zoom_in.setShortcuts([QKeySequence.ZoomIn, QKeySequence("Ctrl+=")])
        zoom_in.triggered.connect(self.view.zoom_in)
        zoom_out = view_menu.addAction("Zoom &Out")
        zoom_out.setShortcuts([QKeySequence.ZoomOut, QKeySequence("Ctrl+-")])
        zoom_out.triggered.connect(self.view.zoom_out)
        fit = view_menu.addAction("&Fit Map in Window")
        fit.setShortcut(QKeySequence("Ctrl+0"))
        fit.triggered.connect(self.view.zoom_fit)
        actual = view_menu.addAction("&Actual Size (100%)")
        actual.setShortcut(QKeySequence("Ctrl+1"))
        actual.triggered.connect(self.view.zoom_reset)

        self.statusBar()
        self.zoom_label = QLabel()
        self.statusBar().addPermanentWidget(self.zoom_label)

    def _populate_palette(self):
        self.models = self.cat.list_models()
        group_cache: dict[tuple, QTreeWidgetItem] = {}
        for model in self.models:
            parts = model.split("/")
            parent = None
            prefix: tuple = ()
            for part in parts[:-1]:
                prefix = prefix + (part,)
                node = group_cache.get(prefix)
                if node is None:
                    node = QTreeWidgetItem([part])
                    if parent is None:
                        self.palette_tree.addTopLevelItem(node)
                    else:
                        parent.addChild(node)
                    group_cache[prefix] = node
                parent = node
            leaf = QTreeWidgetItem([parts[-1]])
            leaf.setData(0, Qt.UserRole, model)
            if parent is None:
                self.palette_tree.addTopLevelItem(leaf)
            else:
                parent.addChild(leaf)
            self._palette_leaf_items.append(leaf)

    def apply_filter(self):
        text = self.filter_edit.text().strip().lower()
        for leaf in self._palette_leaf_items:
            model = leaf.data(0, Qt.UserRole) or ""
            leaf.setHidden(bool(text) and text not in model.lower())

        def update_group(item: QTreeWidgetItem) -> bool:
            any_visible = False
            for i in range(item.childCount()):
                child = item.child(i)
                if child.childCount() > 0:
                    visible = update_group(child)
                else:
                    visible = not child.isHidden()
                any_visible = any_visible or visible
            item.setHidden(not any_visible)
            return any_visible

        for i in range(self.palette_tree.topLevelItemCount()):
            update_group(self.palette_tree.topLevelItem(i))
        if text:
            self.palette_tree.expandAll()

    def on_palette_selection_changed(self):
        items = self.palette_tree.selectedItems()
        model = items[0].data(0, Qt.UserRole) if items else None
        self.selected_palette_model = model
        if model is None:
            self.preview_label.setText("(no preview)")
            self.preview_label.setPixmap(QPixmap())
            return
        pm = self.make_pixmap(model)
        if pm is not None and not pm.isNull():
            self.preview_label.setPixmap(
                pm.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.preview_label.setText("(no preview)")
        self._show_tiling_status(model)

    # -- placement / snapping ------------------------------------------------

    def place_entity_at(self, x: float, y: float, model: str | None = None,
                         snap: bool = False):
        model = model or self.selected_palette_model
        if not model:
            return
        if snap:
            vec = tiling_vector(model)
            last = self.last_placed_by_model.get(model)
            if vec is not None and last is not None:
                x, y = last[0] + vec[0], last[1] + vec[1]
        x, y = round(x), round(y)
        cmd = AddEntityCommand(self, model, x, y)
        self.undo_stack.push(cmd)
        self.last_placed_by_model[model] = (x, y)

    def push_move(self, entity, item, old, new):
        self.undo_stack.push(MoveEntityCommand(self, entity, item, old, new))

    def select_item(self, item: EntityItem):
        self.scene.clearSelection()
        item.setSelected(True)
        self.view.centerOn(item)

    def delete_selected(self):
        items = [it for it in self.scene.selectedItems() if isinstance(it, EntityItem)]
        if not items:
            return
        if len(items) > 1:
            self.undo_stack.beginMacro("Delete entities")
        for item in items:
            self.undo_stack.push(DeleteEntityCommand(self, item.entity, item))
        if len(items) > 1:
            self.undo_stack.endMacro()

    # -- properties dock ------------------------------------------------------

    def on_scene_selection_changed(self):
        items = [it for it in self.scene.selectedItems() if isinstance(it, EntityItem)]
        entity = items[0].entity if items else None
        self.show_entity_properties(entity)
        if entity is not None:
            self._show_tiling_status(entity.model)

    def show_entity_properties(self, entity):
        self._updating_props = True
        self._current_entity = entity
        enabled = entity is not None
        for w in (self.prop_name, self.prop_x, self.prop_y, self.prop_visible,
                  self.prop_collideable, self.prop_half, self.prop_full):
            w.setEnabled(enabled)
        if entity is None:
            self.prop_name.clear()
            self.prop_model.clear()
            self.prop_x.setValue(0)
            self.prop_y.setValue(0)
            self.prop_visible.setChecked(False)
            self.prop_collideable.setChecked(False)
            self.prop_half.setChecked(False)
            self.prop_full.setChecked(False)
        else:
            self.prop_name.setText(entity.name)
            self.prop_model.setText(entity.model)
            self.prop_x.setValue(entity.x)
            self.prop_y.setValue(entity.y)
            self.prop_visible.setChecked((entity.node.get("Visible") or "1") != "0")
            self.prop_collideable.setChecked((entity.node.get("Collideable") or "0") == "1")
            self.prop_half.setChecked((entity.node.get("Half Height") or "0") == "1")
            self.prop_full.setChecked((entity.node.get("Full Height") or "0") == "1")
        self._updating_props = False

    def on_prop_name_changed(self):
        if self._updating_props or self._current_entity is None:
            return
        self._current_entity.set_field("Name", self.prop_name.text())
        self.doc.dirty = True
        self.schedule_validate()

    def on_prop_pos_changed(self):
        if self._updating_props or self._current_entity is None:
            return
        entity = self._current_entity
        old = (entity.x, entity.y)
        new = (self.prop_x.value(), self.prop_y.value())
        if round(old[0], 3) == round(new[0], 3) and round(old[1], 3) == round(new[1], 3):
            return
        item = self.entity_items.get(id(entity.node))
        if item is not None:
            self.push_move(entity, item, old, new)
        else:
            entity.move_to(*new)
            self.doc.dirty = True
            self.schedule_validate()

    def on_prop_visible_changed(self, checked: bool):
        if self._updating_props or self._current_entity is None:
            return
        entity = self._current_entity
        entity.set_field("Visible", "1" if checked else "0")
        item = self.entity_items.get(id(entity.node))
        if item is not None:
            item.setVisible(checked)
        self.doc.dirty = True
        self.schedule_validate()

    def on_prop_collideable_changed(self, checked: bool):
        if self._updating_props or self._current_entity is None:
            return
        self._current_entity.set_field("Collideable", "1" if checked else "0")
        self.doc.dirty = True
        self.schedule_validate()

    def on_prop_half_changed(self, checked: bool):
        if self._updating_props or self._current_entity is None:
            return
        entity = self._current_entity
        if checked:
            entity.set_field("Half Height", "1")
            entity.set_field("Full Height", "0")
            self._updating_props = True
            self.prop_full.setChecked(False)
            self._updating_props = False
        elif not self.prop_full.isChecked():
            # Mutually exclusive pair must keep exactly one set; re-check this one.
            self._updating_props = True
            self.prop_half.setChecked(True)
            self._updating_props = False
            return
        self.doc.dirty = True
        self.schedule_validate()

    def on_prop_full_changed(self, checked: bool):
        if self._updating_props or self._current_entity is None:
            return
        entity = self._current_entity
        if checked:
            entity.set_field("Full Height", "1")
            entity.set_field("Half Height", "0")
            self._updating_props = True
            self.prop_half.setChecked(False)
            self._updating_props = False
        elif not self.prop_half.isChecked():
            self._updating_props = True
            self.prop_full.setChecked(True)
            self._updating_props = False
            return
        self.doc.dirty = True
        self.schedule_validate()

    # -- validation overlay ------------------------------------------------

    def schedule_validate(self):
        self.setWindowTitle(self._title())
        self.validate_timer.start(300)

    def run_validate(self):
        self.issues = validate(self.doc, self.cat)
        self._refresh_issue_list()
        self._refresh_overlay()
        self.setWindowTitle(self._title())

    def _refresh_issue_list(self):
        self.issue_list.clear()
        for issue in self.issues:
            li = QListWidgetItem(f"[{issue.severity}] {issue.message}")
            li.setData(Qt.UserRole, issue)
            if issue.severity == "error":
                li.setForeground(QColor("#c0392b"))
            self.issue_list.addItem(li)

    def _refresh_overlay(self):
        for it in self.overlay_items:
            self.scene.removeItem(it)
        self.overlay_items.clear()
        brush = QBrush(QColor(255, 0, 0, 70))
        pen = QPen(Qt.NoPen)
        for issue in self.issues:
            if issue.severity != "error":
                continue
            for ent in issue.entities:
                info = self.cat.info(ent.model)
                if info is None:
                    continue
                r = info.radius
                circle = QGraphicsEllipseItem(ent.x - r, ent.y - r, 2 * r, 2 * r)
                circle.setBrush(brush)
                circle.setPen(pen)
                circle.setZValue(-1)
                self.scene.addItem(circle)
                self.overlay_items.append(circle)

    def on_issue_clicked(self, list_item: QListWidgetItem):
        issue = list_item.data(Qt.UserRole)
        if not issue.entities:
            return
        item = self.entity_items.get(id(issue.entities[0].node))
        if item is not None:
            self.select_item(item)

    def _show_tiling_status(self, model: str):
        vec = tiling_vector(model)
        if vec:
            self.statusBar().showMessage(
                f"{model.rsplit('/', 1)[-1]}: tiling step ({vec[0]}, {vec[1]})")
        else:
            self.statusBar().clearMessage()

    # -- save / close ----------------------------------------------------

    def _title(self) -> str:
        star = "*" if self.doc.dirty else ""
        return f"{self.doc.path.name}{star} — Lionheart Map Editor"

    def report_zoom(self, scale: float) -> None:
        self.zoom_label.setText(f"  {scale * 100:.0f}%  ")

    # -- eyedropper ------------------------------------------------------

    def eyedropper_active(self, modifiers=None) -> bool:
        """True when the next click should pick a model rather than place or select."""
        if self.eyedropper_action.isChecked():
            return True
        return bool(modifiers is not None and (modifiers & Qt.AltModifier))

    def on_eyedropper_toggled(self, checked: bool) -> None:
        self.view.refresh_cursor()
        if checked:
            self.statusBar().showMessage(
                "Eyedropper: click an object to select its model in the palette.")
        else:
            self.statusBar().clearMessage()

    def pick_model(self, model: str) -> None:
        """Select `model` in the palette tree, revealing and scrolling to it."""
        if not model:
            return
        leaf = next((it for it in self._palette_leaf_items
                     if it.data(0, Qt.UserRole) == model), None)
        if leaf is None:
            self.statusBar().showMessage(
                f"{model} is not in the palette (not under Environments/).", 5000)
            return

        # A filter can be hiding the match; clear it rather than silently doing nothing.
        # apply_filter must be called directly -- filter_edit.textChanged only restarts a
        # 150ms debounce timer, so relying on the signal would select a still-hidden row.
        if leaf.isHidden() and self.filter_edit.text().strip():
            self.filter_edit.clear()
            self.filter_timer.stop()
            self.apply_filter()

        parent = leaf.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.palette_tree.setCurrentItem(leaf)      # fires selection-changed
        self.palette_tree.scrollToItem(leaf, QTreeWidget.PositionAtCenter)

        vec = tiling_vector(model)
        extra = f"  tiling step {vec}" if vec else ""
        self.statusBar().showMessage(
            f"Picked {model.rsplit('/', 1)[-1]}{extra}", 5000)

    def save(self):
        try:
            self.doc.save()
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.setWindowTitle(self._title())
        self.statusBar().showMessage(
            "Saved. Run modmanager.py install and build for this change to reach the game.",
            8000)

    # -- deploy ----------------------------------------------------------

    def _mod_context(self):
        """Work out which mod this file belongs to, and who else ships the same path.

        Returns (mod_id, relative_path, winner) or None if the file is not inside a
        mod's `files/` tree. `winner` is the mod that actually reaches the game for
        this path -- the last enabled mod that ships it.
        """
        parts = self.doc.path.resolve().parts
        try:
            i = len(parts) - 1 - parts[::-1].index("files")
        except ValueError:
            return None
        if i < 1:
            return None
        mod_id = parts[i - 1]
        repo = Path(*parts[:i - 1])            # .../mods
        rel = Path(*parts[i + 1:]).as_posix()

        winner = mod_id
        enabled_path = self.game_dir / "mods" / "enabled.json"
        if enabled_path.exists():
            try:
                order = json.loads(enabled_path.read_text())
                if isinstance(order, dict):
                    order = order.get("enabled", [])
                for other in order:                     # last enabled mod wins
                    if (repo / other / "files" / rel).exists():
                        winner = other
            except Exception:
                pass
        return mod_id, rel, winner

    def deploy(self):
        """Save, then run modmanager install + build so the edit reaches the game."""
        if self._deploy_proc is not None:
            QMessageBox.information(self, "Deploy", "A deploy is already running.")
            return

        ctx = self._mod_context()
        if ctx is None:
            QMessageBox.warning(
                self, "Not a mod file",
                "This .zax is not inside a mod's files/ directory, so there is nothing "
                "to install. Deploy only works on files under mods/<id>/files/.")
            return
        mod_id, rel, winner = ctx

        # The trap this exists to catch: several mods can ship the same path, and only
        # the last enabled one reaches the game. Editing a losing copy looks like the
        # deploy silently did nothing.
        if winner != mod_id:
            QMessageBox.warning(
                self, "This copy will not reach the game",
                f"You are editing <b>{mod_id}</b>'s copy of<br><code>{rel}</code><br><br>"
                f"but <b>{winner}</b> also ships that file and loads later, so its copy "
                f"wins the conflict.<br><br>Edit "
                f"<code>mods/{winner}/files/{rel}</code> instead, or reorder the mods.")
            return

        if self.doc.dirty:
            self.save()
            if self.doc.dirty:      # save failed and already reported
                return

        dlg = QDialog(self)
        dlg.setWindowTitle("Deploy to game")
        dlg.resize(720, 380)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(
            f"Installing and rebuilding <b>{mod_id}</b>.<br>"
            "A full repack takes several minutes. Do not launch the game until it "
            "finishes — it locks data.dat and the final step will fail."))
        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(log)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.button(QDialogButtonBox.Close).setEnabled(False)
        layout.addWidget(buttons)

        steps = [
            [sys.executable, "modmanager.py", "install", f"mods/{mod_id}",
             str(self.game_dir)],
            [sys.executable, "modmanager.py", "build", str(self.game_dir)],
        ]

        def run_next():
            if not steps:
                log.appendPlainText("\nDone. The change is live in the game.")
                buttons.button(QDialogButtonBox.Close).setEnabled(True)
                self._deploy_proc = None
                self.deploy_action.setEnabled(True)
                self.statusBar().showMessage("Deploy finished.", 8000)
                return
            cmd = steps.pop(0)
            log.appendPlainText(f"$ {' '.join(cmd[1:])}\n")
            proc = QProcess(dlg)
            proc.setWorkingDirectory(str(Path(__file__).resolve().parent))
            proc.setProcessChannelMode(QProcess.MergedChannels)
            proc.readyReadStandardOutput.connect(
                lambda p=proc: log.appendPlainText(
                    bytes(p.readAllStandardOutput()).decode("utf-8", "replace").rstrip()))

            def finished(code, _status, p=proc):
                if code != 0:
                    log.appendPlainText(f"\nFAILED (exit {code}). Nothing further run.")
                    buttons.button(QDialogButtonBox.Close).setEnabled(True)
                    self._deploy_proc = None
                    self.deploy_action.setEnabled(True)
                    return
                run_next()

            proc.finished.connect(finished)
            self._deploy_proc = proc
            proc.start(cmd[0], cmd[1:])

        self.deploy_action.setEnabled(False)
        run_next()
        dlg.exec()

    def closeEvent(self, event):
        if self.doc.dirty:
            reply = QMessageBox.question(
                self, "Unsaved changes",
                "This map has unsaved changes. Save before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if reply == QMessageBox.Save:
                self.save()
                if self.doc.dirty:
                    event.ignore()
                    return
            elif reply == QMessageBox.Cancel:
                event.ignore()
                return
        event.accept()


def main() -> int:
    parser = argparse.ArgumentParser(description="Lionheart .zax map editor")
    parser.add_argument("zax_path", help="path to the .zax map file (e.g. under mods/)")
    parser.add_argument("--data-root", default=DEFAULT_DATA_ROOT,
                        help="game data root (contains Cache/Models, Cache/Textures)")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    window = MainWindow(Path(args.zax_path), Path(args.data_root))
    window.resize(1400, 900)
    window.show()
    # Fit after show(), so the viewport has its real size -- fitting before it is laid
    # out computes against a placeholder and lands at the wrong zoom.
    window.view.zoom_fit()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
