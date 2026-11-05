"""左侧工具栏图标：纯 Qt 自绘，无外部图片资源，随工具清单一起打包。

每个图标都是 20×20 透明底 QPixmap，按工具语义画极简线稿，
便于一眼区分框 / 填充 / 箭头 / 文字 / 涂抹 / 图层操作。
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)

INK = QColor("#334155")
ACCENT = QColor("#2F6FE4")
FILL = QColor(47, 111, 228, 120)


@lru_cache(maxsize=None)
def tool_icon(key: str, size: int = 20) -> QIcon:
    """返回工具图标（同参数结果缓存复用）。"""
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(INK)
    pen.setWidthF(1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _draw(p, key)
    p.end()
    return QIcon(pm)


def _draw(p: QPainter, key: str) -> None:
    r = QRectF(3, 4, 14, 12)              # 通用外框
    c = QRectF(3, 3, 14, 14)

    if key == "select":                    # 鼠标箭头
        p.setBrush(QColor("#FFFFFF"))
        p.drawPolygon(QPolygonF([
            QPointF(3, 2), QPointF(3, 16), QPointF(7, 12.2), QPointF(10, 18),
            QPointF(12.3, 17), QPointF(9.4, 11.3), QPointF(15, 11),
        ]))
    elif key == "rect":
        p.drawRect(r)
    elif key == "circle":
        p.drawEllipse(c)
    elif key == "ellipse":
        p.drawEllipse(QRectF(2, 5, 16, 10))
    elif key == "rect_fill":
        p.setBrush(FILL)
        p.drawRect(r)
    elif key == "circle_fill":
        p.setBrush(FILL)
        p.drawEllipse(c)
    elif key == "ellipse_fill":
        p.setBrush(FILL)
        p.drawEllipse(QRectF(2, 5, 16, 10))
    elif key == "arrow":
        p.drawLine(QPointF(3, 16), QPointF(16, 4))
        p.drawLine(QPointF(16, 4), QPointF(10, 5))
        p.drawLine(QPointF(16, 4), QPointF(15, 10))
    elif key == "text":
        f = QFont("Arial")
        f.setBold(True)
        f.setPixelSize(17)
        p.setFont(f)
        p.drawText(QRectF(1, 1, 18, 18), Qt.AlignmentFlag.AlignCenter, "T")
    elif key == "pen":
        path = QPainterPath(QPointF(2.5, 15))
        path.cubicTo(QPointF(7, 2), QPointF(12, 18), QPointF(17.5, 4))
        p.drawPath(path)
    elif key == "mosaic":                  # 棋盘格
        for row in range(3):
            for col in range(3):
                x, y = 3 + col * 5, 3 + row * 5
                p.setBrush(FILL if (row + col) % 2 == 0 else Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(x, y, 4.2, 4.2))
    elif key == "blur":                    # 三个逐渐扩散的虚化圆
        for radius, alpha in ((7.5, 45), (5.2, 90), (2.6, 200)):
            p.setBrush(QColor(47, 111, 228, alpha))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(10, 10), radius, radius)
    elif key in ("layer_up", "layer_down"):
        p.drawRect(QRectF(3, 3, 12, 9))
        p.drawRect(QRectF(6, 7, 12, 9))
        p.setBrush(ACCENT)
        p.setPen(Qt.PenStyle.NoPen)
        if key == "layer_up":
            p.drawPolygon(QPolygonF([QPointF(15, 11), QPointF(18, 16), QPointF(12, 16)]))
        else:
            p.drawPolygon(QPolygonF([QPointF(15, 18), QPointF(18, 13), QPointF(12, 13)]))
    p.setBrush(Qt.BrushStyle.NoBrush)
