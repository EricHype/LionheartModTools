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
import math
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QMimeData, QProcess, QPointF, QSize, QRectF
from PySide6.QtGui import (
    QImage, QPixmap, QColor, QBrush, QPen, QUndoStack, QUndoCommand, QKeySequence,
    QCursor, QPainter, QIcon,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsScene, QGraphicsView, QGraphicsPixmapItem,
    QGraphicsEllipseItem, QGraphicsItem, QDockWidget, QWidget, QVBoxLayout, QFormLayout,
    QLineEdit, QDoubleSpinBox, QSpinBox, QCheckBox, QListWidget, QListWidgetItem,
    QTreeWidget, QTreeWidgetItem, QLabel, QMessageBox, QDialog, QPlainTextEdit,
    QGraphicsSimpleTextItem, QAbstractSpinBox, QProgressBar,
    QDialogButtonBox,
)

import zax_render as zr
from mapedit_core import (
    MapDocument, SpriteCatalogue, validate, tiling_vector, known_non_tiling,
    TerrainLayer, GRID_CELL, plan_wall_run, MAX_RUN_PIECES,
    learn_vector_from_map,
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


def make_brush_cursor(radius_px: float) -> QCursor:
    """Draw a circle-plus-crosshair cursor whose circle spans `radius_px` screen pixels,
    so the terrain brush's on-screen footprint is visible before the first click.
    """
    d = max(8, min(220, int(round(radius_px * 2))))
    size = d + 6
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = size / 2
    pen_outline = QPen(QColor(0, 0, 0, 180), 3.0)
    pen_body = QPen(QColor(255, 255, 255, 230), 1.5)
    for pen in (pen_outline, pen_body):
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(c, c), d / 2, d / 2)
        p.drawLine(QPointF(c - 5, c), QPointF(c + 5, c))
        p.drawLine(QPointF(c, c - 5), QPointF(c, c + 5))
    p.end()
    return QCursor(pm, int(c), int(c))


def make_terrain_canvas(doc: MapDocument, data_root: Path) -> "zr.Canvas":
    """Build a full-resolution zax_render Canvas for the map's terrain.

    Kept around (not discarded like a one-shot render) so a paint stroke can hand a
    region back to `zax_render.render_terrain` and redraw only that rectangle, rather
    than reprocessing the whole map on every mouse-move step.
    """
    w, h = max(1, doc.width), max(1, doc.height)
    canvas = zr.Canvas(w, h, zr.BACKGROUND)
    plasma = doc.root.get("Plasma Ground")
    if isinstance(plasma, ResourceNode):
        try:
            zr.render_terrain(canvas, data_root, plasma, elevation_textures=True)
        except Exception:
            pass
    return canvas


def canvas_to_qpixmap(canvas: "zr.Canvas") -> QPixmap:
    img = QImage(bytes(canvas.pixels), canvas.width, canvas.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(img.copy())


def terrain_region_image(canvas: "zr.Canvas", x0: int, y0: int, x1: int, y1: int) -> QImage:
    """Extract the sub-rectangle [x0,x1) x [y0,y1) of a zax_render Canvas as a QImage,
    for compositing just that rectangle back onto the terrain pixmap."""
    w, h = x1 - x0, y1 - y0
    stride = canvas.width * 4
    buf = bytearray(w * 4 * h)
    src = canvas.pixels
    for row in range(h):
        src_off = (y0 + row) * stride + x0 * 4
        dst_off = row * w * 4
        buf[dst_off:dst_off + w * 4] = src[src_off:src_off + w * 4]
    img = QImage(bytes(buf), w, h, QImage.Format_RGBA8888)
    return img.copy()


def union_bounds(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


# ---------------------------------------------------------------------------
# Scene item for one scenery entity
# ---------------------------------------------------------------------------

class EntityItem(QGraphicsPixmapItem):
    """A placed entity. Wraps an `Entity`; dragging writes back on release.

    Covers both plain scenery and the non-scenery entities that give a map its meaning --
    spawn points, doors, generators, chests. Those are drawn faded with a name label so
    they read as reference rather than decoration, because you place props *relative* to
    them: the chest-inside-a-rock bug came from not being able to see the chest.
    """

    def __init__(self, entity, pixmap: QPixmap, info, window: "MainWindow",
                 *, marker: bool = False):
        super().__init__(pixmap)
        self.entity = entity
        self.info = info
        self.window = window
        self.marker = marker
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        self.setPos(entity.x - info.hotspot_x, entity.y - info.hotspot_y)
        self.setZValue(entity.y)
        if marker:
            # Editor/* placeholders carry Visible=0 because the game must not draw them.
            # In the editor they are exactly what we DO want to see, so ignore that flag
            # for markers and distinguish them by opacity + label instead.
            self.setOpacity(0.75)
            self.setVisible(True)
            label = QGraphicsSimpleTextItem(entity.name or entity.model.rsplit("/", 1)[-1],
                                            self)
            label.setBrush(QBrush(QColor(255, 235, 140)))
            font = label.font()
            font.setPointSizeF(max(7.0, font.pointSizeF()))
            font.setBold(True)
            label.setFont(font)
            # Keep the caption legible however far the view is zoomed out.
            label.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
            label.setPos(pixmap.width() / 2, -4)
            self._label = label
        else:
            self.setVisible((entity.node.get("Visible") or "1") != "0")
            self._label = None
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


class PaintTerrainCommand(QUndoCommand):
    """One stroke's worth of terrain painting. `before`/`after` are `TerrainLayer.snapshot()`
    results captured at mouse-press and mouse-release; redo/undo swap between them and
    re-render only the stroke's bounding box, not the whole map."""

    def __init__(self, window: "MainWindow", before: list, after: list, bounds: tuple):
        super().__init__("Paint terrain")
        self.window = window
        self.before = before
        self.after = after
        self.bounds = bounds

    def _apply(self, snap):
        self.window.terrain_layer.restore(snap)
        self.window.redraw_terrain_bounds(self.bounds)
        self.window.setWindowTitle(self.window._title())

    def redo(self):
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)


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
        self._paint_mode = False
        self._paint_active = False
        self._paint_cursor = None
        self._run_mode = False
        self._run_start = None      # scene coords of the piece the run grows from

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
        # [ and ] resize the brush, the usual paint-app binding. Handled here rather
        # than as a window-wide QAction shortcut so the keys still type normally in the
        # palette filter and the property fields -- a shortcut would swallow them
        # everywhere, which is the trap Backspace-to-delete already had to work around.
        if self._paint_mode and event.key() in (Qt.Key_BracketLeft, Qt.Key_BracketRight):
            step = -1 if event.key() == Qt.Key_BracketLeft else 1
            self.window.nudge_brush_radius(step)
            event.accept()
            return
        # Escape abandons a run in progress. Same reasoning as above for handling it
        # here: a window-wide Escape would also close dialogs and clear the filter box.
        if self._run_mode and event.key() == Qt.Key_Escape and self._run_start is not None:
            self.cancel_wall_run()
            event.accept()
            return
        super().keyPressEvent(event)
        self.refresh_cursor()

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        self.refresh_cursor()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.refresh_cursor()

    def mouseMoveEvent(self, event):
        if self._paint_mode:
            if self._paint_active and (event.buttons() & Qt.LeftButton):
                self._paint_at(event.position().toPoint())
            event.accept()
            return
        if self._run_mode and self._run_start is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            self.window.preview_wall_run(self._run_start,
                                         (scene_pos.x(), scene_pos.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)
        self.refresh_cursor()

    # -- wall run mode ------------------------------------------------------

    def set_wall_run_mode(self, enabled: bool) -> None:
        """Switch the view between normal interaction and laying wall runs.

        Like paint mode this takes over left-drag, so RubberBandDrag goes off. The
        crosshair is the standard "you are drawing, not picking" cursor and reads as
        clearly different from both the arrow and the brush circle.
        """
        self._run_mode = enabled
        self.cancel_wall_run()
        if enabled:
            self.setDragMode(QGraphicsView.NoDrag)
            self.update_run_cursor()
        else:
            self.setDragMode(QGraphicsView.RubberBandDrag)
            self.viewport().unsetCursor()
            self._cursor_is_dropper = False
            self.refresh_cursor()

    def update_run_cursor(self) -> None:
        """Crosshair when a run can be laid, forbidden sign when it cannot.

        Only 8 of 4787 sprites have a hand-measured step, so "this piece cannot run" is
        the common case, not the exception. The first version said so in the status bar
        only, and the honest result was a tool that looked broken: you click, and
        nothing visible happens. The cursor makes the refusal impossible to miss before
        the click rather than after it.
        """
        if not self._run_mode:
            return
        can = self.window.run_vector_for(self.window.selected_palette_model) is not None
        self.viewport().setCursor(Qt.CrossCursor if can else Qt.ForbiddenCursor)

    def cancel_wall_run(self) -> None:
        self._run_start = None
        self.window.clear_wall_run_preview()

    # -- terrain paint mode -------------------------------------------------

    def set_terrain_paint_mode(self, enabled: bool) -> None:
        """Switch the view between normal interaction and terrain painting.

        RubberBandDrag is turned off while painting -- left-drag must paint, not select
        -- and restored on exit. The brush cursor is (re)built for the current zoom.
        """
        self._paint_mode = enabled
        self._paint_active = False
        if enabled:
            self.setDragMode(QGraphicsView.NoDrag)
            self.update_paint_cursor()
        else:
            self.setDragMode(QGraphicsView.RubberBandDrag)
            self.viewport().unsetCursor()
            self._cursor_is_dropper = False
            self.refresh_cursor()

    def update_paint_cursor(self) -> None:
        """Rebuild the brush cursor for the current radius setting and zoom level."""
        radius_cells = self.window.terrain_radius_spin.value()
        radius_world = radius_cells * GRID_CELL if radius_cells > 0 else GRID_CELL * 0.35
        radius_px = max(4.0, radius_world * self.current_scale())
        self._paint_cursor = make_brush_cursor(radius_px)
        if self._paint_mode:
            self.viewport().setCursor(self._paint_cursor)

    def _paint_at(self, view_pos) -> None:
        scene_pos = self.mapToScene(view_pos)
        self.window.paint_terrain_at(scene_pos.x(), scene_pos.y())

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
        if self._paint_mode:
            self.update_paint_cursor()

    def zoom_in(self):
        self.zoom_by(self.ZOOM_STEP, anchor_under_mouse=False)

    def zoom_out(self):
        self.zoom_by(1 / self.ZOOM_STEP, anchor_under_mouse=False)

    def zoom_reset(self):
        self.resetTransform()
        self.window.report_zoom(self.current_scale())
        if self._paint_mode:
            self.update_paint_cursor()

    def zoom_fit(self):
        """Fit the whole map in the view -- the default on open for a wide map."""
        rect = self.scene().sceneRect()
        if rect.isEmpty():
            return
        self.fitInView(rect, Qt.KeepAspectRatio)
        self.window.report_zoom(self.current_scale())
        if self._paint_mode:
            self.update_paint_cursor()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(self.ZOOM_STEP if delta > 0 else 1 / self.ZOOM_STEP)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        if self._paint_mode:
            if event.button() == Qt.LeftButton:
                self._paint_active = True
                self.window.begin_paint_stroke()
                self._paint_at(event.position().toPoint())
                event.accept()
                return
            super().mousePressEvent(event)
            return
        if self._run_mode:
            if event.button() == Qt.LeftButton:
                scene_pos = self.mapToScene(event.position().toPoint())
                self._run_start = self.window.begin_wall_run(scene_pos.x(), scene_pos.y())
                if self._run_start is not None:
                    self.window.preview_wall_run(self._run_start, self._run_start)
                event.accept()
                return
            super().mousePressEvent(event)
            return
        if event.button() == Qt.LeftButton:
            # position() not pos(): the latter is deprecated in Qt 6 and warns on every
            # click, which is a lot of noise in a tool you click constantly.
            scene_pos = self.mapToScene(event.position().toPoint())
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

    def mouseReleaseEvent(self, event):
        if self._paint_mode:
            if self._paint_active and event.button() == Qt.LeftButton:
                self._paint_active = False
                self.window.end_paint_stroke()
            event.accept()
            return
        if self._run_mode:
            if event.button() == Qt.LeftButton and self._run_start is not None:
                scene_pos = self.mapToScene(event.position().toPoint())
                self.window.commit_wall_run(self._run_start,
                                            (scene_pos.x(), scene_pos.y()))
                self.cancel_wall_run()
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        # Coerce: several helpers do `data_root / "Cache" / ...`, which fails on a str.
        # main() happens to pass a Path, so a str only breaks for other callers/tests.
        data_root = Path(data_root)
        self.cat = SpriteCatalogue(data_root)
        self.data_root = data_root
        # modmanager works on the game directory, which is the data root's parent.
        self.game_dir = Path(data_root).resolve().parent
        self._deploy_proc = None

        # Terrain paint state. TerrainLayer parses the whole elevation grid up front, so
        # build it once here; None means this map has no Plasma Ground and terrain
        # painting stays disabled (the dock/action reflect that once _build_ui runs).
        try:
            self.terrain_layer = TerrainLayer(self.doc)
        except ValueError:
            self.terrain_layer = None
        self.terrain_paint_index = 0
        self._paint_snapshot = None          # TerrainLayer.snapshot() at stroke start
        self._paint_stroke_bounds = None     # union of dirty rects painted this stroke
        self._pending_redraw = None          # dirty rect awaiting the coalesced repaint
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.timeout.connect(self.flush_pending_redraw)

        self.pixmap_cache: dict[str, QPixmap | None] = {}
        self.entity_items: dict[int, EntityItem] = {}
        self.overlay_items: list[QGraphicsEllipseItem] = []
        self.issues = []
        self.selected_palette_model: str | None = None
        self.last_placed_by_model: dict[str, tuple[float, float]] = {}
        # Wall run state
        self._run_preview_items: list[QGraphicsPixmapItem] = []
        self._run_preview_positions: list[tuple[int, int]] = []
        self._run_anchor_entity = None   # existing piece the run grows from, if any
        self._run_vec = None             # step resolved at drag start
        self._suppress_autoselect = False
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
            self._populate_terrain_dock()
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
        # Keep the Canvas (not just the QPixmap made from it) so a paint stroke can hand
        # zax_render.render_terrain a region and redraw just that rectangle afterwards.
        self.terrain_canvas = make_terrain_canvas(self.doc, self.data_root)
        self.terrain_pixmap = canvas_to_qpixmap(self.terrain_canvas)
        bg = QGraphicsPixmapItem(self.terrain_pixmap)
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

    def create_item(self, entity, *, marker: bool = False) -> EntityItem | None:
        info = self.cat.info(entity.model)
        pm = self.make_pixmap(entity.model)
        if info is None or pm is None:
            return None
        item = EntityItem(entity, pm, info, self, marker=marker)
        if self.terrain_paint_action.isChecked():
            # Entities placed/undone while a paint stroke tool is active must stay
            # non-interactive too, matching whatever's already on the map.
            item.setFlag(QGraphicsItem.ItemIsSelectable, False)
            item.setFlag(QGraphicsItem.ItemIsMovable, False)
        return item

    def _populate_scene(self):
        for ent in self.doc.entities():
            if not ent.model:
                continue            # no art to draw and no position that means anything
            item = self.create_item(ent, marker=not ent.is_scenery())
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
        self.filter_edit.setPlaceholderText("Filter models...")
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

        # Terrain dock: texture picker + brush radius for terrain paint mode.
        terrain_widget = QWidget()
        terrain_layout = QVBoxLayout(terrain_widget)

        note = QLabel(
            "Texture order is light-to-dark and matters: adjacent indices are what a "
            "blend passes through, so a painted edge softens toward its neighbor in "
            "the list, not toward some other texture.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        terrain_layout.addWidget(note)

        self.terrain_texture_list = QListWidget()
        self.terrain_texture_list.setIconSize(QSize(48, 48))
        self.terrain_texture_list.currentRowChanged.connect(self.on_terrain_texture_changed)
        terrain_layout.addWidget(self.terrain_texture_list, stretch=1)

        radius_form = QFormLayout()
        self.terrain_radius_spin = QSpinBox()
        self.terrain_radius_spin.setRange(0, 8)
        self.terrain_radius_spin.setValue(1)
        self.terrain_radius_spin.setToolTip(
            "Brush radius in grid cells (0 paints a single vertex).\n"
            "Painting always registers instantly; only the redraw costs time, and it\n"
            "grows with the area. On a large map a wide brush repaints in about half a\n"
            "second, so the picture lags the strokes slightly.")
        self.terrain_radius_spin.valueChanged.connect(self.on_terrain_radius_changed)
        radius_form.addRow("Brush radius", self.terrain_radius_spin)
        terrain_layout.addLayout(radius_form)

        terrain_dock = QDockWidget("Terrain", self)
        terrain_dock.setWidget(terrain_widget)
        self.addDockWidget(Qt.RightDockWidgetArea, terrain_dock)

        # Menus
        file_menu = self.menuBar().addMenu("&File")
        save_action = file_menu.addAction("&Save")
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self.save)
        file_menu.addSeparator()
        self.deploy_action = file_menu.addAction("&Deploy to game...")
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
        self.delete_action = delete_action = edit_menu.addAction("&Delete Entity")
        # Backspace as well as Delete: on a laptop keyboard Delete is often awkward or
        # absent, and Backspace is what the hand reaches for.
        delete_action.setShortcuts([QKeySequence(Qt.Key_Delete),
                                    QKeySequence(Qt.Key_Backspace)])
        delete_action.triggered.connect(self.delete_selected)

        tools_menu = self.menuBar().addMenu("&Tools")
        self.eyedropper_action = tools_menu.addAction("&Eyedropper")
        self.eyedropper_action.setCheckable(True)
        self.eyedropper_action.setShortcut(QKeySequence("I"))
        self.eyedropper_action.setStatusTip(
            "Click an object to select its model in the palette (or hold Alt)")
        self.eyedropper_action.toggled.connect(self.on_eyedropper_toggled)

        self.wall_run_action = tools_menu.addAction("&Wall Run")
        self.wall_run_action.setCheckable(True)
        self.wall_run_action.setShortcut(QKeySequence("R"))
        self.wall_run_action.setStatusTip(
            "Drag to lay a run of the selected wall piece along its measured tiling "
            "vector; start on an existing piece to extend a run")
        self.wall_run_action.toggled.connect(self.on_wall_run_toggled)

        self.terrain_paint_action = tools_menu.addAction("&Terrain Paint")
        self.terrain_paint_action.setCheckable(True)
        self.terrain_paint_action.setShortcut(QKeySequence("T"))
        self.terrain_paint_action.setStatusTip(
            "Left-drag to paint the selected ground texture index onto the terrain grid")
        self.terrain_paint_action.toggled.connect(self.on_terrain_paint_toggled)

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
        view_menu.addSeparator()
        self.markers_action = view_menu.addAction("Show &Markers")
        self.markers_action.setCheckable(True)
        self.markers_action.setChecked(True)
        self.markers_action.setShortcut(QKeySequence("M"))
        self.markers_action.setStatusTip(
            "Show spawn points, doors, generators and other non-scenery entities")
        self.markers_action.toggled.connect(self.on_markers_toggled)

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
            # Say which assets tile before they are picked, not after they are placed.
            # The Fence set looks like wall material and is not -- laying it in a run
            # leaves a visible jog at every joint, which cost a build cycle to discover.
            vec = tiling_vector(model)
            if vec is not None:
                leaf.setText(0, f"{parts[-1]}   [tiles {vec[0]},{vec[1]}]")
                leaf.setForeground(0, QBrush(QColor(150, 220, 150)))
                leaf.setToolTip(0, f"{model}\nTiles into runs; step {vec[0]},{vec[1]}. "
                                   f"Hold Shift when placing to snap to it.")
            elif known_non_tiling(model):
                leaf.setForeground(0, QBrush(QColor(220, 160, 120)))
                leaf.setToolTip(0, f"{model}\nScatter decoration - does NOT tile into "
                                   f"runs. Laying these end to end leaves visible gaps.")
            else:
                leaf.setToolTip(0, model)
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

    # -- wall runs ------------------------------------------------------------

    def run_vector_for(self, model: str | None) -> tuple[int, int] | None:
        """The step to lay `model` at: hand-measured if known, else learned from the map."""
        if not model:
            return None
        return tiling_vector(model) or learn_vector_from_map(self.doc.entities(), model)

    ANCHOR_RADIUS = 48      # world units; how close a click must be to snap to a piece

    def _anchor_near(self, model: str, x: float, y: float):
        """The placed piece of `model` nearest (x, y), within ANCHOR_RADIUS.

        Proximity rather than scene.itemAt: QGraphicsPixmapItem hit-tests against the
        pixmap's alpha mask, and an entity's own position is its hotspot -- for a wall
        that is the base of the sprite, which is usually a transparent pixel. Hit-testing
        there missed the piece being clicked on almost every time.
        """
        best, best_d = None, self.ANCHOR_RADIUS
        for ent in self.doc.entities():
            if ent.model != model:
                continue
            d = math.hypot(ent.x - x, ent.y - y)
            if d <= best_d:
                best, best_d = ent, d
        return best

    def begin_wall_run(self, x: float, y: float):
        """Start a run at (x, y), or at the piece already under the cursor.

        Anchoring to an existing piece of the same model is what makes the tool usable
        for extending a wall: click the last piece of a run, drag, and the new pieces
        continue the exact lattice instead of starting a parallel one a few units off.
        Returns the start point, or None if there is nothing to lay.
        """
        model = self.selected_palette_model
        if not model:
            self.statusBar().showMessage(
                "Wall run: select a wall piece in the palette first.", 6000)
            return None
        if self.run_vector_for(model) is None:
            probe = plan_wall_run(model, (x, y), (x, y))
            self.statusBar().showMessage(
                self._run_message(probe, probe.positions, 0), 8000)
            return None

        # Resolve the step once per drag. Learning it walks every entity and counts
        # pairwise deltas, and preview runs on every mouse-move -- doing it per move
        # would make a long drag crawl. It also cannot change mid-drag: ghosts are not
        # entities, so there is nothing new for it to learn from until release.
        self._run_vec = self.run_vector_for(model)
        anchor = self._anchor_near(model, x, y)
        self._run_anchor_entity = anchor
        return (anchor.x, anchor.y) if anchor is not None else (x, y)

    def _run_message(self, run, placements, skipped) -> str:
        """Always says something. A run that adds nothing is the case most in need of
        explaining -- silence there is what makes the tool look broken."""
        if run.reason:
            msg = run.reason
            if run.alternatives:
                names = ", ".join(m.rsplit("/", 1)[-1] for m in run.alternatives)
                msg += f"  Pieces that do: {names}."
            return msg

        name = run.model.rsplit("/", 1)[-1]
        if not placements:
            step = self._run_vec or (0, 0)
            if skipped:
                return (f"{name}: nothing to add, all {skipped} position"
                        f"{'' if skipped == 1 else 's'} already filled. "
                        f"Drag past the end of the run to extend it.")
            return (f"{name}: drag further -- the next piece sits "
                    f"{step[0]}, {step[1]} from here.")

        n = len(placements)
        msg = f"{name}: {n} piece{'' if n == 1 else 's'}"
        if skipped:
            msg += f" ({skipped} already there)"
        if run.off_axis >= 1:
            msg += f", {run.off_axis:.0f} px off the drag"
        if run.truncated:
            msg += f" (capped at {MAX_RUN_PIECES})"
        return msg

    OCCUPIED_EPS = 4        # world units; closer than this counts as the same spot

    def _plan_run(self, start, end):
        """Plan the run, then drop every position that already holds this piece.

        Skipping occupied positions rather than just the anchor is what stops the tool
        stacking invisible duplicates: dragging back along a wall you already built used
        to place a second copy of every piece, exactly on top of the first. As a bonus
        the same rule makes the tool fill *gaps* in an existing run and leave the rest
        alone, which is the case that let the arena ship with an open corner.

        Returns (run, placements, skipped).
        """
        model = self.selected_palette_model
        run = plan_wall_run(model, start, end, vec=self._run_vec)
        if not run.positions:
            return run, [], 0
        taken = [(e.x, e.y) for e in self.doc.entities() if e.model == model]
        placements = [
            p for p in run.positions
            if not any(math.hypot(p[0] - tx, p[1] - ty) <= self.OCCUPIED_EPS
                       for tx, ty in taken)
        ]
        return run, placements, len(run.positions) - len(placements)

    def preview_wall_run(self, start, end) -> None:
        run, placements, skipped = self._plan_run(start, end)
        self.statusBar().showMessage(self._run_message(run, placements, skipped))

        # Rebuild only when the plan actually changed. A drag fires hundreds of moves and
        # most land on the same step, so re-creating a 40-item ghost each time is wasted
        # work that shows up as lag on a long run.
        if placements == self._run_preview_positions:
            return
        self._run_preview_positions = list(placements)
        self.clear_wall_run_preview(keep_positions=True)
        pixmap = self.make_pixmap(run.model)
        info = self.cat.info(run.model) if pixmap is not None else None
        if pixmap is None or info is None:
            return
        for px, py in placements:
            ghost = QGraphicsPixmapItem(pixmap)
            ghost.setPos(px - info.hotspot_x, py - info.hotspot_y)
            ghost.setZValue(py)
            ghost.setOpacity(0.5)
            self.scene.addItem(ghost)
            self._run_preview_items.append(ghost)

    def clear_wall_run_preview(self, *, keep_positions: bool = False) -> None:
        for ghost in self._run_preview_items:
            self.scene.removeItem(ghost)
        self._run_preview_items = []
        if not keep_positions:
            self._run_preview_positions = []

    def commit_wall_run(self, start, end) -> None:
        run, placements, skipped = self._plan_run(start, end)
        if not placements:
            # Always report. A release that adds nothing used to say nothing at all,
            # which is indistinguishable from the tool being broken.
            self.statusBar().showMessage(self._run_message(run, placements, skipped), 8000)
            return
        label = f"Lay {len(placements)} x {run.model.rsplit('/', 1)[-1]}"
        self._suppress_autoselect = True
        try:
            self.undo_stack.beginMacro(label)
            for px, py in placements:
                self.undo_stack.push(AddEntityCommand(self, run.model, px, py))
            self.undo_stack.endMacro()
        finally:
            self._suppress_autoselect = False
        self.last_placed_by_model[run.model] = placements[-1]
        self.statusBar().showMessage(f"{label}. Ctrl+Z undoes the whole run.", 5000)

    def push_move(self, entity, item, old, new):
        self.undo_stack.push(MoveEntityCommand(self, entity, item, old, new))

    def select_item(self, item: EntityItem):
        # Laying a run pushes one Add per piece, and each would otherwise select and
        # centre on itself -- forty selection changes and forty view jumps for one drag,
        # ending with the camera parked on the last piece.
        if self._suppress_autoselect:
            return
        self.scene.clearSelection()
        item.setSelected(True)
        self.view.centerOn(item)

    def delete_selected(self):
        # Delete and Backspace are window-wide shortcuts, so they fire even when focus is
        # in the palette filter or a property field -- where both keys must edit text
        # instead. Without this guard, backspacing a typo in the filter box silently
        # deletes whatever happens to be selected on the map.
        # self.focusWidget() before the application-wide one: the latter returns None
        # whenever the window is not active, and a None there would fall straight
        # through to the delete.
        focus = self.focusWidget() or QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QAbstractSpinBox, QPlainTextEdit)):
            return
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
        # Placing the second copy of a piece is what makes its step learnable, so the
        # run cursor can go from forbidden to crosshair purely as a result of an edit.
        self.view.update_run_cursor()

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
        name = model.rsplit("/", 1)[-1]
        measured = tiling_vector(model)
        vec = measured or learn_vector_from_map(self.doc.entities(), model)
        if vec:
            source = "tiling step" if measured else "step learned from this map"
            self.statusBar().showMessage(
                f"{name}: {source} ({vec[0]}, {vec[1]}). Press R and drag to lay a run.")
        elif known_non_tiling(model):
            self.statusBar().showMessage(f"{name}: scatter decoration, does not tile.")
        else:
            self.statusBar().showMessage(
                f"{name}: no tiling step known. Place two where you want them and the "
                "run tool (R) will copy that spacing.")
        # The palette drives the run cursor: which piece is selected decides whether a
        # run is possible at all.
        self.view.update_run_cursor()

    # -- save / close ----------------------------------------------------

    def _title(self) -> str:
        star = "*" if self.doc.dirty else ""
        return f"{self.doc.path.name}{star} - Lionheart Map Editor"

    def report_zoom(self, scale: float) -> None:
        self.zoom_label.setText(f"  {scale * 100:.0f}%  ")

    def on_markers_toggled(self, shown: bool) -> None:
        n = 0
        for item in self.entity_items.values():
            if item.marker:
                item.setVisible(shown)
                n += 1
        self.statusBar().showMessage(
            f"{'Showing' if shown else 'Hiding'} {n} marker(s).", 4000)

    # -- eyedropper ------------------------------------------------------

    def eyedropper_active(self, modifiers=None) -> bool:
        """True when the next click should pick a model rather than place or select."""
        if self.eyedropper_action.isChecked():
            return True
        return bool(modifiers is not None and (modifiers & Qt.AltModifier))

    def on_eyedropper_toggled(self, checked: bool) -> None:
        if checked:
            for other in (self.terrain_paint_action, self.wall_run_action):
                if other.isChecked():
                    other.setChecked(False)
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

    # -- terrain paint -----------------------------------------------------

    def _populate_terrain_dock(self) -> None:
        self.terrain_texture_list.clear()
        if self.terrain_layer is None:
            self.terrain_texture_list.addItem("(no Plasma Ground on this map)")
            self.terrain_texture_list.setEnabled(False)
            self.terrain_radius_spin.setEnabled(False)
            self.terrain_paint_action.setEnabled(False)
            return
        for name in self.terrain_layer.textures:
            icon = self._terrain_texture_icon(name)
            item = QListWidgetItem(icon, name) if icon is not None else QListWidgetItem(name)
            self.terrain_texture_list.addItem(item)
        if self.terrain_texture_list.count():
            self.terrain_texture_list.setCurrentRow(0)

    def _terrain_texture_icon(self, name: str) -> QIcon | None:
        # Ground textures live under Cache/Textures/*.frm16, not Cache/Models -- outside
        # SpriteCatalogue's remit, which only knows Cache/Models/Environments/**. Reuse
        # zax_render's loader (itself just mdl16_format.decode_icon on that path) rather
        # than duplicating the decode here.
        data = zr.load_ground_texture(self.data_root, name)
        if data is None:
            return None
        pm = pixels_to_qpixmap(data)
        if pm.isNull():
            return None
        pm = pm.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return QIcon(pm)

    def on_terrain_texture_changed(self, row: int) -> None:
        if row < 0:
            return
        self.terrain_paint_index = row
        if self.terrain_paint_action.isChecked() and self.terrain_layer is not None:
            names = self.terrain_layer.textures
            label = names[row] if 0 <= row < len(names) else "?"
            self.statusBar().showMessage(
                f"Terrain paint: texture index {row} ({label})", 4000)

    def on_terrain_radius_changed(self, _value: int) -> None:
        if self.view._paint_mode:
            self.view.update_paint_cursor()

    def on_wall_run_toggled(self, checked: bool) -> None:
        if checked:
            for other in (self.eyedropper_action, self.terrain_paint_action):
                if other.isChecked():
                    other.setChecked(False)
        self.set_entities_interactive(not checked)
        self.view.set_wall_run_mode(checked)
        if not checked:
            self.statusBar().clearMessage()
            return
        model = self.selected_palette_model
        if model and self.run_vector_for(model) is not None:
            self.statusBar().showMessage(
                f"Wall run: drag to lay {model.rsplit('/', 1)[-1]}. Start on an "
                "existing piece to extend that run; Escape cancels.")
        elif model:
            self.statusBar().showMessage(
                f"Wall run: no step known for {model.rsplit('/', 1)[-1]} (cursor shows "
                "the forbidden sign). Place two of them and the spacing is learned.")
        else:
            self.statusBar().showMessage(
                "Wall run: select a piece in the palette first.")

    def on_terrain_paint_toggled(self, checked: bool) -> None:
        if checked and self.terrain_layer is None:
            self.terrain_paint_action.setChecked(False)
            return
        for other in (self.eyedropper_action, self.wall_run_action):
            if checked and other.isChecked():
                other.setChecked(False)
        self.set_entities_interactive(not checked)
        self.view.set_terrain_paint_mode(checked)
        if checked:
            self.statusBar().showMessage(
                "Terrain paint: left-drag to paint; release to commit an undo step.")
        else:
            self.statusBar().clearMessage()

    def set_entities_interactive(self, enabled: bool) -> None:
        """Toggle whether entity items can be selected/dragged. Turned off while terrain
        painting so a left-drag over an entity paints the ground instead of moving it."""
        for item in self.entity_items.values():
            item.setFlag(QGraphicsItem.ItemIsSelectable, enabled)
            item.setFlag(QGraphicsItem.ItemIsMovable, enabled)
        if not enabled:
            self.scene.clearSelection()

    def nudge_brush_radius(self, step: int) -> None:
        spin = self.terrain_radius_spin
        new = max(spin.minimum(), min(spin.maximum(), spin.value() + step))
        if new == spin.value():
            self.statusBar().showMessage(
                f"Brush radius {new} is the {'smallest' if step < 0 else 'largest'}.",
                2000)
            return
        spin.setValue(new)      # fires valueChanged, which resizes the cursor
        self.statusBar().showMessage(f"Brush radius {new}", 2000)

    def flush_pending_redraw(self) -> None:
        """Redraw whatever has accumulated since the last repaint."""
        self._redraw_timer.stop()
        pending, self._pending_redraw = self._pending_redraw, None
        if pending is not None:
            self.redraw_terrain_bounds(pending)

    def begin_paint_stroke(self) -> None:
        if self.terrain_layer is None:
            return
        self._paint_snapshot = self.terrain_layer.snapshot()
        self._paint_stroke_bounds = None
        self._pending_redraw = None

    def paint_terrain_at(self, x: float, y: float) -> None:
        if self.terrain_layer is None:
            return
        col, row = TerrainLayer.world_to_grid(x, y)
        radius = self.terrain_radius_spin.value()
        changed = self.terrain_layer.paint(col, row, self.terrain_paint_index, radius=radius)
        if not changed:
            return
        self.terrain_layer.flush()
        bounds = self._dirty_bounds(col, row, radius)
        if bounds is None:
            return
        self._paint_stroke_bounds = (
            bounds if self._paint_stroke_bounds is None
            else union_bounds(self._paint_stroke_bounds, bounds))

        # Coalesce redraws instead of redrawing per mouse-move. A single step costs
        # 0.05s at radius 0 but 0.97s at radius 8 on Gate District, and a drag emits
        # move events far faster than that -- redrawing each one makes the whole app
        # stall. The grid itself is updated immediately (it is cheap), so no paint is
        # lost; only the repaint is batched, and end_paint_stroke() forces a final one.
        self._pending_redraw = (
            bounds if self._pending_redraw is None
            else union_bounds(self._pending_redraw, bounds))
        if not self._redraw_timer.isActive():
            self._redraw_timer.start(60)

    def _dirty_bounds(self, col: int, row: int, radius: int) -> tuple | None:
        """World-pixel rect touched by a paint at grid (col,row) with `radius` cells.

        +1/+2 margin because a vertex affects the tiles straddling it on both sides,
        clamped to the map -- see the terrain-paint design note for the derivation.
        """
        x0 = max(0, (col - radius - 1) * GRID_CELL)
        y0 = max(0, (row - radius - 1) * GRID_CELL)
        x1 = min(self.doc.width, (col + radius + 2) * GRID_CELL)
        y1 = min(self.doc.height, (row + radius + 2) * GRID_CELL)
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1, y1)

    def redraw_terrain_bounds(self, bounds: tuple) -> None:
        """Re-render just `bounds` (x0,y0,x1,y1 in world pixels) into the terrain canvas
        and composite that rectangle onto the on-screen pixmap."""
        if bounds is None:
            return
        plasma = self.doc.root.get("Plasma Ground")
        if not isinstance(plasma, ResourceNode):
            return
        x0, y0, x1, y1 = bounds
        zr.render_terrain(self.terrain_canvas, self.data_root, plasma,
                          elevation_textures=True, region=(x0, y0, x1, y1))
        sub_img = terrain_region_image(self.terrain_canvas, x0, y0, x1, y1)
        painter = QPainter(self.terrain_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.drawImage(x0, y0, sub_img)
        painter.end()
        # QPixmap is copy-on-write; painting into self.terrain_pixmap detaches it from
        # whatever the item is currently showing, so the item needs the update pushed
        # back to it explicitly.
        self.terrain_item.setPixmap(self.terrain_pixmap)
        self.terrain_item.update(QRectF(x0, y0, x1 - x0, y1 - y0))

    def end_paint_stroke(self) -> None:
        # Always settle the coalesced redraw, even on a no-op stroke -- otherwise the
        # last few paint steps of a drag would stay invisible until the next one.
        self.flush_pending_redraw()
        if self.terrain_layer is None or self._paint_snapshot is None:
            return
        before = self._paint_snapshot
        bounds = self._paint_stroke_bounds
        self._paint_snapshot = None
        self._paint_stroke_bounds = None
        after = self.terrain_layer.snapshot()
        if before == after:
            return   # no vertex actually changed -- nothing to undo, nothing to save
        self.undo_stack.push(PaintTerrainCommand(self, before, after, bounds))
        self.setWindowTitle(self._title())

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
            "finishes - it locks data.dat and the final step will fail."))
        phase = QLabel("Starting...")
        layout.addWidget(phase)
        bar = QProgressBar()
        bar.setRange(0, 0)          # busy until the repack starts producing bytes
        layout.addWidget(bar)

        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(log)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.button(QDialogButtonBox.Close).setEnabled(False)
        layout.addWidget(buttons)

        steps = [
            ("Installing the mod", [sys.executable, "modmanager.py", "install",
                                    f"mods/{mod_id}", str(self.game_dir)]),
            ("Repacking data.dat", [sys.executable, "modmanager.py", "build",
                                    str(self.game_dir)]),
        ]

        # modmanager prints nothing during the repack, which is the part that takes
        # minutes -- but archive.repack streams into data.dat.build.tmp.tmp and that
        # grows to roughly the size of the vanilla archive. Polling it gives a real
        # percentage instead of a bar that just spins.
        growing = self.game_dir / "data.dat.build.tmp.tmp"
        vanilla = self.game_dir / "data.dat.vanilla.bak"
        try:
            expected = vanilla.stat().st_size
        except OSError:
            expected = 0

        # A build that was interrupted leaves this file behind at whatever size it had
        # reached -- 1.5GB was sitting there when this was written. Left in place the bar
        # would open at 94% and sit there, so clear it first. It is scratch either way:
        # archive.repack recreates it, and nothing reads it afterwards.
        try:
            if growing.exists():
                stale_mb = growing.stat().st_size // (1 << 20)
                growing.unlink()
                log.appendPlainText(
                    f"Removed {stale_mb} MB of scratch left by an interrupted build.\n")
        except OSError:
            pass        # not fatal; at worst the percentage starts high

        def poll_progress():
            try:
                done = growing.stat().st_size
            except OSError:
                return          # not started yet, or already renamed into place
            if expected <= 0:
                return
            pct = min(99, int(done * 100 / expected))
            if bar.maximum() == 0:
                bar.setRange(0, 100)
            bar.setValue(pct)
            phase.setText(f"Repacking data.dat - {done // (1 << 20)} of "
                          f"{expected // (1 << 20)} MB")

        progress_timer = QTimer(dlg)
        progress_timer.timeout.connect(poll_progress)
        progress_timer.start(400)

        def run_next():
            if not steps:
                progress_timer.stop()
                bar.setRange(0, 100)
                bar.setValue(100)
                phase.setText("Done - the change is live in the game.")
                log.appendPlainText("\nDone. The change is live in the game.")
                buttons.button(QDialogButtonBox.Close).setEnabled(True)
                self._deploy_proc = None
                self.deploy_action.setEnabled(True)
                self.statusBar().showMessage("Deploy finished.", 8000)
                return
            label, cmd = steps.pop(0)
            phase.setText(label + "...")
            log.appendPlainText(f"$ {' '.join(cmd[1:])}\n")
            proc = QProcess(dlg)
            proc.setWorkingDirectory(str(Path(__file__).resolve().parent))
            proc.setProcessChannelMode(QProcess.MergedChannels)
            proc.readyReadStandardOutput.connect(
                lambda p=proc: log.appendPlainText(
                    bytes(p.readAllStandardOutput()).decode("utf-8", "replace").rstrip()))

            def finished(code, _status, p=proc):
                if code != 0:
                    progress_timer.stop()
                    bar.setRange(0, 100)
                    bar.setValue(0)
                    phase.setText(f"Failed (exit {code}).")
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
