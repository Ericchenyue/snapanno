"""编辑画布：底图 + 图层渲染、草稿实时预览、命中测试、移动/缩放/绘制交互。

性能策略：已提交图层拼合结果缓存为 QImage，只在数据变化时重算；
拖拽过程中的草稿层直接叠加绘制，不重算整张图。
"""

from __future__ import annotations

import copy
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from .config import HANDLE_SIZE, MIN_SHAPE, SHAPE_TOOLS, STROKE_TOOLS
from .dialogs import TextInputDialog
from .models import (
    handle_positions, layer_bounds, normalized_rect, refresh_text_bounds,
    resize_bounds, scale_layer, translate_layer,
)
from .render import draw_layer, render_document


def _circle_rect(start: QPointF, end: QPointF) -> list[float]:
    """由拖拽起止点计算居中内切正方形，保证「圆形」始终是正圆。"""
    dx, dy = end.x() - start.x(), end.y() - start.y()
    side = min(abs(dx), abs(dy))
    x = start.x() if dx >= 0 else start.x() - side
    y = start.y() if dy >= 0 else start.y() - side
    return [x, y, side, side]


class Canvas(QWidget):
    """图像像素坐标系 * zoom = 控件坐标。"""

    committed = Signal()
    selectionChanged = Signal(int)
    statusMessage = Signal(str)
    zoomChanged = Signal(float)

    def __init__(self, editor, parent: QWidget | None = None):
        super().__init__(parent)
        self.editor = editor
        self._zoom = 1.0
        self._cache = QImage()
        self._cache_dirty = True
        self._draft: dict | None = None
        self._selected_id = 0
        self._mode: str | None = None
        self._handle: str | None = None
        self._press = QPointF()
        self._last = QPointF()
        self._orig_layer: dict | None = None
        self._orig_bounds = QRectF()

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self.setStyleSheet("background:#FFFFFF;")

    # -- 基础 --------------------------------------------------------------
    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, value: float) -> None:
        self._zoom = max(0.05, min(8.0, value))
        base = self.editor.base
        if not base.isNull():
            self.setFixedSize(max(1, round(base.width() * self._zoom)),
                              max(1, round(base.height() * self._zoom)))
        self.update()
        self.zoomChanged.emit(self._zoom)

    def zoom_by(self, steps: int, anchor: QPointF | None = None) -> float:
        """按 1.15 倍逐级缩放；anchor 为鼠标在控件上的位置，缩放后保持该点不动。"""
        old = self._zoom
        new = max(0.05, min(8.0, old * (1.15 ** steps)))
        if abs(new - old) < 1e-6:
            return old
        self.set_zoom(new)
        if anchor is not None:
            scroll = getattr(self.editor, "scroll", None)
            if scroll is not None:
                hb = scroll.horizontalScrollBar()
                vb = scroll.verticalScrollBar()
                hb.setValue(round(hb.value() + anchor.x() * (new - old)))
                vb.setValue(round(vb.value() + anchor.y() * (new - old)))
        self.statusMessage.emit(f"缩放 {round(new * 100)}%")
        return new

    def wheelEvent(self, event) -> None:
        """Alt + 滚轮缩放；普通滚轮交给外层 QScrollArea 滚动。"""
        if not (event.modifiers() & Qt.AltModifier):
            event.ignore()
            return
        dy = event.angleDelta().y()
        if dy:
            if hasattr(self.editor, "_auto_fit"):
                self.editor._auto_fit = False
            self.zoom_by(1 if dy > 0 else -1, event.position())
        event.accept()

    def refresh(self) -> None:
        """图层或底图变化后调用。"""
        self._cache_dirty = True
        self.update()

    def set_document(self) -> None:
        self._draft = None
        self._mode = None
        self._selected_id = 0
        self._cache_dirty = True
        self.set_zoom(self._zoom)
        self.update()

    def set_tool(self, _tool: str) -> None:
        self._draft = None
        self._mode = None
        self.setCursor(Qt.ArrowCursor if _tool == "select" else Qt.CrossCursor)
        self.update()

    def set_selected(self, layer_id: int) -> None:
        self._selected_id = int(layer_id)
        self.update()

    def selected_id(self) -> int:
        return self._selected_id

    def _to_image(self, pos) -> QPointF:
        return QPointF(pos.x() / self._zoom, pos.y() / self._zoom)

    def _to_widget(self, pos: QPointF) -> QPointF:
        return QPointF(pos.x() * self._zoom, pos.y() * self._zoom)

    # -- 绘制 --------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        if self._cache_dirty:
            self._cache = render_document(self.editor.base, self.editor.layers)
            self._cache_dirty = False
        painter.save()
        painter.scale(self._zoom, self._zoom)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        if not self._cache.isNull():
            painter.drawImage(0, 0, self._cache)
        if self._draft is not None:
            try:
                draw_layer(painter, self._cache, self._draft)
            except Exception:                      # noqa: BLE001 - 草稿异常不影响主画面
                pass
        painter.restore()
        self._draw_selection(painter)
        painter.end()

    def _draw_selection(self, painter: QPainter) -> None:
        layer = self.editor.find_layer(self._selected_id)
        if layer is None:
            return
        b = layer_bounds(layer)
        if b.width() <= 0 or b.height() <= 0:
            return
        r = QRectF(b.x() * self._zoom, b.y() * self._zoom,
                   b.width() * self._zoom, b.height() * self._zoom)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#2F6FE4"), 1.5, Qt.DashLine))
        painter.drawRect(r)
        half = HANDLE_SIZE / 2
        for _name, pt in handle_positions(b):
            w = self._to_widget(pt)
            box = QRectF(w.x() - half, w.y() - half, HANDLE_SIZE, HANDLE_SIZE)
            painter.setPen(QPen(QColor("#2F6FE4"), 1))
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRect(box)

    # -- 命中测试 ----------------------------------------------------------
    def _hit_handle(self, pos: QPointF, bounds: QRectF) -> str | None:
        tol = HANDLE_SIZE / self._zoom / 2 + 3 / self._zoom
        for name, pt in handle_positions(bounds):
            if abs(pos.x() - pt.x()) <= tol and abs(pos.y() - pt.y()) <= tol:
                return name
        return None

    def _hit_layer(self, pos: QPointF) -> dict | None:
        tol = 4 / self._zoom
        for layer in reversed(self.editor.layers):
            b = layer_bounds(layer).adjusted(-tol, -tol, tol, tol)
            if b.contains(pos):
                return layer
        return None

    # -- 鼠标：按下 --------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self.setFocus()
        tool = self.editor.tool
        pos = self._to_image(event.position())
        self._press = pos
        self._last = pos

        if tool == "select":
            self._press_select(pos)
        elif tool == "text":
            self._create_text(pos)
        elif tool in SHAPE_TOOLS:
            self._mode = "draw_shape"
            self._draft = self.editor.make_layer(tool, [pos.x(), pos.y(), 0, 0])
            if tool == "arrow":
                self._draft["points"] = [[pos.x(), pos.y()], [pos.x(), pos.y()]]
        elif tool in STROKE_TOOLS:
            self._mode = "draw_stroke"
            layer = self.editor.make_layer(tool, [0, 0, 0, 0], [[pos.x(), pos.y()]])
            self._draft = layer
        self.update()

    def _press_select(self, pos: QPointF) -> None:
        selected = self.editor.find_layer(self._selected_id)
        if selected is not None:
            bounds = layer_bounds(selected)
            handle = self._hit_handle(pos, bounds)
            if handle:
                self._mode = "resize"
                self._handle = handle
                self._orig_layer = copy.deepcopy(selected)
                self._orig_bounds = QRectF(bounds)
                self.statusMessage.emit("拖动控制点缩放标注")
                return
        hit = self._hit_layer(pos)
        if hit is None:
            if self._selected_id != 0:
                self._select(0)
            return
        self._select(hit["id"])
        self._mode = "move"
        self._orig_layer = copy.deepcopy(hit)
        self._orig_bounds = QRectF(layer_bounds(hit))
        self.statusMessage.emit("拖动移动标注，Delete 删除")

    def _create_text(self, pos: QPointF) -> None:
        preset = self.editor.tool_defaults.get("text", {}).get("text", "")
        dlg = TextInputDialog(preset, self)
        if dlg.exec() != QDialog.Accepted:
            return
        text = dlg.text().strip()
        if not text:
            QMessageBox.information(self, "文字标注", "文字内容为空，未创建标注。")
            return
        layer = self.editor.make_layer("text", [pos.x(), pos.y(), 0, 0], text=text)
        refresh_text_bounds(layer)
        self.editor.add_layer(layer)
        self.editor.finish_drawing(layer["id"])
        self.statusMessage.emit("已添加文字标注并切回选择箭头，可直接修改内容/样式")

    # -- 鼠标：移动 --------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        pos = self._to_image(event.position())
        if self._mode == "draw_shape" and self._draft is not None:
            self._update_shape_draft(pos)
        elif self._mode == "draw_stroke" and self._draft is not None:
            self._append_stroke_point(pos)
        elif self._mode == "move":
            layer = self.editor.find_layer(self._selected_id)
            if layer is not None:
                translate_layer(layer, pos.x() - self._last.x(), pos.y() - self._last.y())
                self._last = pos
                self.refresh()
        elif self._mode == "resize":
            layer = self.editor.find_layer(self._selected_id)
            if layer is not None and self._orig_layer is not None:
                # 从拖拽前的原始快照重算，避免误差累积
                idx = self.editor.layers.index(layer)
                fresh = copy.deepcopy(self._orig_layer)
                self.editor.layers[idx] = fresh
                delta = QPointF(pos.x() - self._press.x(), pos.y() - self._press.y())
                new_bounds = resize_bounds(self._orig_bounds, self._handle or "se",
                                           delta, fresh["type"].startswith("circle"))
                scale_layer(fresh, self._orig_bounds, new_bounds)
                self.refresh()
        elif self.editor.tool == "select":
            self._update_hover_cursor(pos)
        self.update()

    def _update_shape_draft(self, pos: QPointF) -> None:
        draft = self._draft
        kind = draft["type"]
        if kind.startswith("circle"):
            draft["rect"] = _circle_rect(self._press, pos)
        else:
            draft["rect"] = [self._press.x(), self._press.y(),
                             pos.x() - self._press.x(), pos.y() - self._press.y()]
        if kind == "arrow":
            draft["points"] = [[self._press.x(), self._press.y()], [pos.x(), pos.y()]]

    @staticmethod
    def _finalize_draft(draft: dict, start: QPointF, end: QPointF) -> bool:
        """整理草稿几何并返回是否达到有效尺寸。"""
        kind = draft["type"]
        if kind.startswith("circle"):
            draft["rect"] = _circle_rect(start, end)
        elif kind == "arrow":
            draft["rect"] = [start.x(), start.y(), end.x() - start.x(), end.y() - start.y()]
            draft["points"] = [[start.x(), start.y()], [end.x(), end.y()]]
            valid = math.hypot(end.x() - start.x(), end.y() - start.y()) >= MIN_SHAPE
            r = normalized_rect(draft)
            draft["rect"] = [r.x(), r.y(), r.width(), r.height()]
            return valid
        else:
            draft["rect"] = [start.x(), start.y(), end.x() - start.x(), end.y() - start.y()]
        r = normalized_rect(draft)
        valid = r.width() >= MIN_SHAPE and r.height() >= MIN_SHAPE
        draft["rect"] = [r.x(), r.y(), r.width(), r.height()]
        return valid

    def _append_stroke_point(self, pos: QPointF, draft: dict | None = None) -> None:
        layer = draft if draft is not None else self._draft
        pts = layer["points"]
        step = 2.0 if layer["type"] == "pen" else max(2.0, layer["brush"] / 5.0)
        if not pts or math.hypot(pos.x() - pts[-1][0], pos.y() - pts[-1][1]) >= step:
            pts.append([pos.x(), pos.y()])
            # 草稿点数上限，避免异常输入导致卡顿
            if len(pts) > 20000:
                pts.pop(1)

    def _update_hover_cursor(self, pos: QPointF) -> None:
        selected = self.editor.find_layer(self._selected_id)
        if selected is not None and self._hit_handle(pos, layer_bounds(selected)):
            self.setCursor(Qt.SizeFDiagCursor)
        elif self._hit_layer(pos) is not None:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    # -- 鼠标：松开 --------------------------------------------------------
    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._mode is None:
            return
        mode, self._mode = self._mode, None
        pos = self._to_image(event.position())

        if mode == "draw_shape" and self._draft is not None:
            draft, self._draft = self._draft, None
            if self._finalize_draft(draft, self._press, pos):
                self.editor.add_layer(draft)
                self.editor.finish_drawing(draft["id"])
            self.refresh()

        elif mode == "draw_stroke" and self._draft is not None:
            draft, self._draft = self._draft, None
            self._append_stroke_point(pos, draft)
            if draft["points"]:
                self.editor.add_layer(draft)
                self.editor.finish_drawing(draft["id"])
            self.refresh()

        elif mode in ("move", "resize"):
            layer = self.editor.find_layer(self._selected_id)
            if layer is not None and self._orig_layer is not None and layer != self._orig_layer:
                self.committed.emit()
                self.statusMessage.emit("已更新标注位置/大小")
            self._orig_layer = None
            self._handle = None
            self.refresh()

        self.update()

    # -- 键盘 --------------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self._selected_id:
                self.editor.delete_selected()
                return
        super().keyPressEvent(event)

    def _select(self, layer_id: int) -> None:
        self._selected_id = int(layer_id)
        self.selectionChanged.emit(self._selected_id)
        self.update()

    def clear_interaction(self) -> None:
        self._draft = None
        self._mode = None
        self._orig_layer = None
        self._handle = None
        self.update()
