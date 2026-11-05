"""扩大画布预览覆盖层。

Canvas 的固定尺寸严格等于「底图 × zoom」，图片之外的区域不属于 Canvas，
收不到鼠标事件。因此扩展框必须由一个铺满 QScrollArea.viewport() 的透明
QWidget 承担：既能画到图片外，也能接收图片外的拖动。

本类只负责预览，绝不修改底图或图层；真正的应用逻辑在 EditorWindow.apply_expand。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .config import HANDLE_SIZE
from .models import handle_positions


class ExpandOverlay(QWidget):
    """四边 / 四角共 8 个控制点，只能向外拖动扩展画布区域。"""

    def __init__(self, editor, parent: QWidget | None = None):
        super().__init__(parent)
        self._editor = editor
        self._expand_color = "#FFFFFF"
        self._preview_bounds = QRectF(0.0, 0.0,
                                      float(editor.base.width()),
                                      float(editor.base.height()))
        self._start_bounds = QRectF(self._preview_bounds)
        self._drag_handle: str | None = None
        self._press_img = QPointF()

        # 透明覆盖：让底图内容透出，同时不遮挡自己的绘制
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("background:transparent;")
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    # -- 坐标换算（实时计算，绝不缓存）--------------------------------------
    @property
    def _zoom(self) -> float:
        return self._editor.canvas.zoom

    def _origin(self) -> QPointF:
        """Canvas 左上角在 viewport 坐标系中的位置。

        QScrollArea 的 AlignCenter 居中偏移、滚动条偏移都已体现在 canvas.pos() 里，
        用 mapTo 一次拿到，不要再手工加减。
        """
        return QPointF(self._editor.canvas.mapTo(
            self._editor.scroll.viewport(), QPoint(0, 0)))

    def _to_image(self, vp: QPointF) -> QPointF:
        """viewport 坐标 → 图像像素坐标（可为负，表示左/上扩展区）。"""
        o = self._origin()
        z = self._zoom
        return QPointF((vp.x() - o.x()) / z, (vp.y() - o.y()) / z)

    def _to_viewport(self, ip: QPointF) -> QPointF:
        """图像像素坐标 → viewport 坐标。"""
        o = self._origin()
        z = self._zoom
        return QPointF(o.x() + ip.x() * z, o.y() + ip.y() * z)

    # -- 对外接口 ----------------------------------------------------------
    def set_expand_color(self, color: str) -> None:
        self._expand_color = color
        self.update()

    def preview_bounds(self) -> QRectF:
        """返回当前预览包围盒（图像像素坐标，负值表示左/上扩展）。"""
        return QRectF(self._preview_bounds)

    def reset(self) -> None:
        self._preview_bounds = QRectF(0.0, 0.0,
                                      float(self._editor.base.width()),
                                      float(self._editor.base.height()))
        self._start_bounds = QRectF(self._preview_bounds)
        self._drag_handle = None
        self.update()

    def drag_bounds(self, handle: str, delta: QPointF) -> None:
        """按控制点应用一次外扩增量（供自检与交互共用，保证规则一致）。"""
        h = handle
        l, t = self._start_bounds.left(), self._start_bounds.top()
        r, b = self._start_bounds.right(), self._start_bounds.bottom()
        W, H = float(self._editor.base.width()), float(self._editor.base.height())
        # 只能向外：左侧/上侧最多到 0 以内，右侧/下侧最少保持原尺寸
        if "w" in h:
            l = min(0.0, l + delta.x())
        if "e" in h:
            r = max(W, r + delta.x())
        if "n" in h:
            t = min(0.0, t + delta.y())
        if "s" in h:
            b = max(H, b + delta.y())
        self._preview_bounds = QRectF(l, t, r - l, b - t)
        self.update()

    # -- 绘制 --------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        # 不调用 super()，避免系统背景把底图盖住
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        z = self._zoom
        o = self._origin()
        b = self._preview_bounds
        W, H = float(self._editor.base.width()), float(self._editor.base.height())

        new_r = QRectF(o.x() + b.left() * z, o.y() + b.top() * z,
                       b.width() * z, b.height() * z)
        orig_r = QRectF(o.x(), o.y(), W * z, H * z)

        # OddEven 填充：只画「新边界内、原图外」的环带，四角同时扩展也正确
        path = QPainterPath()
        path.addRect(new_r)
        path.addRect(orig_r)
        fill = QColor(self._expand_color)
        fill.setAlpha(130)
        painter.setPen(Qt.NoPen)
        painter.fillPath(path, QBrush(fill))

        blue = QColor("#2F6FE4")
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(blue, 1.5, Qt.DashLine))
        painter.drawRect(orig_r)                       # 原图边界：虚线
        painter.setPen(QPen(blue, 1.8, Qt.SolidLine))
        painter.drawRect(new_r)                        # 新边界：实线

        half = HANDLE_SIZE / 2
        painter.setPen(QPen(blue, 1))
        painter.setBrush(QColor("#FFFFFF"))
        for _name, pt in handle_positions(b):
            w = self._to_viewport(pt)
            painter.drawRect(QRectF(w.x() - half, w.y() - half,
                                    HANDLE_SIZE, HANDLE_SIZE))
        painter.end()

    # -- 交互 --------------------------------------------------------------
    def _hit_handle(self, p: QPointF) -> str | None:
        tol = (HANDLE_SIZE / 2 + 3) / self._zoom
        for name, pt in handle_positions(self._preview_bounds):
            if abs(p.x() - pt.x()) <= tol and abs(p.y() - pt.y()) <= tol:
                return name
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        p = self._to_image(event.position())
        h = self._hit_handle(p)
        if h is None:
            return
        self._drag_handle = h
        self._press_img = p
        self._start_bounds = QRectF(self._preview_bounds)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        p = self._to_image(event.position())
        if self._drag_handle is None:
            self.setCursor(Qt.SizeAllCursor if self._hit_handle(p) else Qt.CrossCursor)
            return
        self.drag_bounds(self._drag_handle,
                         QPointF(p.x() - self._press_img.x(),
                                 p.y() - self._press_img.y()))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._drag_handle = None
        self.update()
