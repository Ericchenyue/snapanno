"""图层渲染引擎。

把「底图 + 图层列表」合成为一张 QImage。马赛克/模糊从原始底图采样，
避免多次绘制互相污染；draw_layer 单独暴露，供画布绘制草稿层复用。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath,
    QPen, QPolygonF, QBrush,
)

from .models import normalized_rect


def render_document(base: QImage, layers: list[dict]) -> QImage:
    """拼合底图与全部图层，返回最终图像（ARGB32）。"""
    out = base.convertToFormat(QImage.Format_ARGB32).copy()
    source = out.copy()                 # 采样源固定为原始底图，效果层不叠加自身
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    for layer in layers:
        try:
            draw_layer(painter, source, layer)
        except Exception:
            # 单个图层异常不影响整张图输出（损坏工程文件也可尽量显示）
            continue
    painter.end()
    return out


def to_rgb(image: QImage) -> QImage:
    """导出 JPG 前去掉透明通道，透明区域补白。"""
    if not image.hasAlphaChannel():
        return image.convertToFormat(QImage.Format_RGB32)
    bg = QImage(image.size(), QImage.Format_RGB32)
    bg.fill(Qt.white)
    p = QPainter(bg)
    p.drawImage(0, 0, image)
    p.end()
    return bg


def expand_canvas(base: QImage, bounds: QRectF, color: str):
    """按预览包围盒向外扩展画布，返回 (新底图, 旧图偏移 dx, dy)。

    bounds 为图像像素坐标的 QRectF，负值表示向左/上扩展。
    这里做第二道 clamp，保证绝不裁掉原图；扩展区域填充 color 并烘焙进底图。
    """
    L = min(0, int(round(bounds.left())))
    T = min(0, int(round(bounds.top())))
    R = max(base.width(), int(round(bounds.right())))
    B = max(base.height(), int(round(bounds.bottom())))
    dx, dy = -L, -T
    out = QImage(R - L, B - T, QImage.Format_ARGB32)
    out.fill(QColor(color))
    p = QPainter(out)
    p.drawImage(QPointF(dx, dy), base)
    p.end()
    return out, dx, dy


def draw_layer(painter: QPainter, source: QImage, layer: dict) -> None:
    """绘制单个图层。painter 的坐标系为「图像像素」坐标。"""
    kind = layer.get("type")
    if kind in ("rect", "circle", "ellipse", "rect_fill", "circle_fill", "ellipse_fill"):
        _draw_shape(painter, layer)
    elif kind == "arrow":
        _draw_arrow(painter, layer)
    elif kind == "pen":
        _draw_pen(painter, layer)
    elif kind == "text":
        _draw_text(painter, layer)
    elif kind in ("mosaic", "blur"):
        _draw_effect(painter, source, layer)
    else:
        raise ValueError(f"未知图层类型：{kind}")


def _main_pen(layer: dict, width: float | None = None) -> QPen:
    pen = QPen(QColor(layer.get("color", "#E53935")))
    pen.setWidthF(max(0.5, float(width if width is not None else layer.get("width", 3))))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _outline_pen(layer: dict, width: float) -> QPen:
    pen = QPen(QColor(layer.get("outline_color", "#FFFFFF")))
    pen.setWidthF(max(0.5, width + 2 * float(layer.get("outline_width", 2))))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _draw_shape(painter: QPainter, layer: dict) -> None:
    """矩形/圆/椭圆的描边与填充。外轮廓 = 先画一圈更粗的底色，再画主体。"""
    r = normalized_rect(layer)
    if r.width() <= 0 or r.height() <= 0:
        return
    kind = layer["type"]
    is_fill = kind.endswith("_fill")

    if is_fill:
        color = QColor(layer.get("fill_color", "#FFC107"))
        color.setAlpha(int(max(0, min(100, layer.get("fill_alpha", 45))) / 100 * 255))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        _paint_shape(painter, kind, r)
    else:
        painter.setBrush(Qt.NoBrush)
        if layer.get("outline"):
            painter.setPen(_outline_pen(layer, layer.get("width", 3)))
            _paint_shape(painter, kind, r)
        painter.setPen(_main_pen(layer))
        _paint_shape(painter, kind, r)
    painter.setBrush(Qt.NoBrush)


def _paint_shape(painter: QPainter, kind: str, r: QRectF) -> None:
    """按当前画笔/画刷绘制形状主体。"""
    if kind.startswith("rect"):
        painter.drawRect(r)
    else:
        painter.drawEllipse(r)


def _draw_arrow(painter: QPainter, layer: dict) -> None:
    """直线箭头：可选外轮廓由一层更粗的线 + 箭头衬底模拟。"""
    pts = layer.get("points") or []
    if len(pts) >= 2:
        start = QPointF(pts[0][0], pts[0][1])
        end = QPointF(pts[-1][0], pts[-1][1])
    else:
        r = normalized_rect(layer)
        start, end = r.topLeft(), r.bottomRight()
    if (end - start).manhattanLength() < 2:
        return

    width = float(layer.get("width", 4))
    head_len = max(12.0, width * 3.2)
    angle = math.atan2(end.y() - start.y(), end.x() - start.x())
    spread = math.radians(24)
    p1 = QPointF(end.x() - head_len * math.cos(angle - spread),
                 end.y() - head_len * math.sin(angle - spread))
    p2 = QPointF(end.x() - head_len * math.cos(angle + spread),
                 end.y() - head_len * math.sin(angle + spread))
    head = QPolygonF([end, p1, p2])

    if layer.get("outline"):
        ow = float(layer.get("outline_width", 2))
        back = QPen(QColor(layer.get("outline_color", "#FFFFFF")))
        back.setWidthF(width + 2 * ow)
        back.setCapStyle(Qt.RoundCap)
        back.setJoinStyle(Qt.RoundJoin)
        painter.setPen(back)
        painter.setBrush(QBrush(QColor(layer.get("outline_color", "#FFFFFF"))))
        painter.drawLine(start, end)
        painter.drawPolygon(head)

    color = QColor(layer.get("color", "#E53935"))
    painter.setPen(_main_pen(layer, width))
    painter.setBrush(QBrush(color))
    painter.drawLine(start, end)
    painter.drawPolygon(head)
    painter.setBrush(Qt.NoBrush)


def _draw_pen(painter: QPainter, layer: dict) -> None:
    pts = layer.get("points") or []
    if not pts:
        return
    poly = QPolygonF([QPointF(p[0], p[1]) for p in pts])
    if layer.get("outline"):
        painter.setPen(_outline_pen(layer, layer.get("width", 4)))
        painter.drawPolyline(poly)
    painter.setPen(_main_pen(layer, layer.get("width", 4)))
    if len(pts) == 1:
        painter.drawPoint(poly[0])
    else:
        painter.drawPolyline(poly)


def _draw_text(painter: QPainter, layer: dict) -> None:
    text = layer.get("text") or ""
    if not text.strip():
        return
    font = QFont(layer.get("font_family", "Microsoft YaHei"))
    font.setPointSize(max(1, int(layer.get("font_size", 24))))
    font.setBold(bool(layer.get("bold", False)))
    font.setUnderline(bool(layer.get("underline", False)))
    fm = QFontMetricsF(font)
    r = normalized_rect(layer)
    x = r.left() + 4
    y = r.top() + 4

    color = QColor(layer.get("color", "#E53935"))
    for line in text.split("\n"):
        path = QPainterPath()
        path.addText(QPointF(x, y + fm.ascent()), font, line)
        if layer.get("outline"):
            stroke = QPen(QColor(layer.get("outline_color", "#FFFFFF")))
            stroke.setWidthF(max(1.0, float(layer.get("outline_width", 2)) * 2))
            stroke.setJoinStyle(Qt.RoundJoin)
            painter.setPen(stroke)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawPath(path)
        y += fm.height()
    painter.setBrush(Qt.NoBrush)


# ---------------------------------------------------------------------------
# 马赛克 / 模糊：涂抹式遮挡，每个采样点从原始底图取一块，处理后贴回当前位置
# ---------------------------------------------------------------------------

def _crop_safe(img: QImage, x: int, y: int, w: int, h: int) -> QImage:
    """安全裁剪，越界部分补透明，避免 QImage.copy 在越界时产生脏数据。"""
    target = QImage(w, h, QImage.Format_ARGB32)
    target.fill(Qt.transparent)
    ix, iy = max(0, x), max(0, y)
    iw, ih = min(img.width(), x + w) - ix, min(img.height(), y + h) - iy
    if iw > 0 and ih > 0:
        p = QPainter(target)
        p.drawImage(QPointF(ix - x, iy - y), img.copy(ix, iy, iw, ih))
        p.end()
    return target


def _pixelate(img: QImage, block: int = 10) -> QImage:
    w, h = img.width(), img.height()
    if w < 2 or h < 2:
        return img
    small = img.scaled(max(1, w // block), max(1, h // block),
                       Qt.IgnoreAspectRatio, Qt.FastTransformation)
    return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)


def _blur(img: QImage) -> QImage:
    w, h = img.width(), img.height()
    if w < 2 or h < 2:
        return img
    small = img.scaled(max(1, w // 5), max(1, h // 5),
                       Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def _draw_effect(painter: QPainter, source: QImage, layer: dict) -> None:
    """马赛克/模糊图层：笔刷粗细可调，强度固定为默认值，不提供轮廓。"""
    pts = layer.get("points") or []
    if not pts:
        return
    size = max(4, min(400, int(layer.get("brush", 30))))
    half = size // 2
    # 相邻采样点过密时跳过，兼顾性能与效果连续性
    min_step = max(2.0, size / 5.0)
    last = None
    for px, py in pts:
        if last is not None and math.hypot(px - last[0], py - last[1]) < min_step:
            continue
        last = (px, py)
        x, y = int(px) - half, int(py) - half
        patch = _crop_safe(source, x, y, size, size)
        patch = _pixelate(patch) if layer["type"] == "mosaic" else _blur(patch)
        painter.drawImage(QPointF(x, y), patch)
