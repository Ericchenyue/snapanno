"""图层数据模型。

图层用普通 dict 表示，全部字段可 JSON 序列化；渲染、命中测试、样式编辑、
撤销重做、工程文件读写都直接围绕这份数据工作，避免多套状态不一致。
"""

from __future__ import annotations

import copy
import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSizeF
from PySide6.QtGui import QFont, QFontMetricsF

from .config import (
    DEFAULT_STYLE,
    DRAW_TOOLS,
    FILL_TOOLS,
    PROJECT_VERSION,
    SHAPE_TOOLS,
    STROKE_TOOLS,
    TOOL_STYLE_OVERRIDE,
)

# 允许出现在工程文件里的图层类型
LAYER_TYPES = DRAW_TOOLS | SHAPE_TOOLS | STROKE_TOOLS  # = DRAW_TOOLS（形状/笔触均在其中）

_GEOMETRY_KEYS = ("rect",)
_NUMERIC_INT_KEYS = ("width", "outline_width", "fill_alpha", "font_size", "brush")
_BOOL_KEYS = ("outline", "bold", "underline")


def default_style(kind: str) -> dict:
    """某个工具的默认样式（用于新建图层 / 工具默认属性）。"""
    style = dict(DEFAULT_STYLE)
    style.update(TOOL_STYLE_OVERRIDE.get(kind, {}))
    return style


def new_layer(kind: str, layer_id: int, rect=None, points=None, **overrides) -> dict:
    """创建一个新图层。rect 为 [x, y, w, h]，points 为 [[x, y], ...]。"""
    if kind not in DRAW_TOOLS:
        raise ValueError(f"未知图层类型：{kind}")
    layer: dict[str, Any] = {
        "id": int(layer_id),
        "type": kind,
        "rect": [float(v) for v in (rect or [0.0, 0.0, 0.0, 0.0])],
        "points": [[float(p[0]), float(p[1])] for p in (points or [])],
    }
    layer.update(default_style(kind))
    layer.update(overrides)
    if kind.startswith("circle"):
        _make_square(layer)
    return layer


def _make_square(layer: dict) -> None:
    """圆形工具：把拖拽矩形收敛为居中正方形，保证选中框与绘制结果一致。"""
    r = normalized_rect(layer)
    side = min(r.width(), r.height())
    cx, cy = r.center().x(), r.center().y()
    layer["rect"] = [cx - side / 2, cy - side / 2, side, side]


def normalized_rect(layer: dict) -> QRectF:
    """返回宽高为正的矩形（拖拽时允许负方向）。"""
    r = layer.get("rect") or [0, 0, 0, 0]
    x, y, w, h = (float(r[i]) for i in range(4))
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    return QRectF(x, y, w, h)


def set_rect(layer: dict, r: QRectF) -> None:
    layer["rect"] = [r.x(), r.y(), r.width(), r.height()]


def measure_text(layer: dict) -> QSizeF:
    """按图层文字样式估算包围盒，供选中框与文字定位使用。"""
    font = QFont(layer.get("font_family", "Microsoft YaHei"))
    font.setPointSize(max(1, int(layer.get("font_size", 24))))
    font.setBold(bool(layer.get("bold", False)))
    font.setUnderline(bool(layer.get("underline", False)))
    fm = QFontMetricsF(font)
    lines = (layer.get("text") or "").split("\n") or [""]
    w = max(fm.horizontalAdvance(line) for line in lines)
    h = fm.height() * len(lines)
    return QSizeF(w + 8, h + 8)


def refresh_text_bounds(layer: dict) -> None:
    """文字内容/样式变化后同步包围盒（只改尺寸，保留左上角）。"""
    if layer.get("type") != "text":
        return
    r = normalized_rect(layer)
    size = measure_text(layer)
    set_rect(layer, QRectF(r.x(), r.y(), size.width(), size.height()))


def layer_bounds(layer: dict) -> QRectF:
    """图层包围盒（图像像素坐标），用于命中测试、选中框与控制点。"""
    kind = layer.get("type")
    if kind in STROKE_TOOLS:
        pts = layer.get("points") or []
        if not pts:
            return QRectF()
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        pad = max(2.0, float(layer.get("brush", 30)) / 2) if kind in ("mosaic", "blur") \
            else max(2.0, float(layer.get("width", 3)) / 2)
        return QRectF(min(xs) - pad, min(ys) - pad,
                      max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)
    return normalized_rect(layer)


def translate_layer(layer: dict, dx: float, dy: float) -> None:
    """平移图层（形状与笔画统一处理）。"""
    r = layer.get("rect") or [0, 0, 0, 0]
    layer["rect"] = [r[0] + dx, r[1] + dy, r[2], r[3]]
    for p in layer.get("points", []):
        p[0] += dx
        p[1] += dy


def scale_layer(layer: dict, old: QRectF, new: QRectF) -> None:
    """按新旧包围盒比例缩放图层（控制点拖拽用）。"""
    if old.width() <= 0 or old.height() <= 0 or new.width() <= 0 or new.height() <= 0:
        return
    sx = new.width() / old.width()
    sy = new.height() / old.height()
    r = normalized_rect(layer)
    set_rect(layer, QRectF(
        new.x() + (r.x() - old.x()) * sx,
        new.y() + (r.y() - old.y()) * sy,
        r.width() * sx,
        r.height() * sy,
    ))
    for p in layer.get("points", []):
        p[0] = new.x() + (p[0] - old.x()) * sx
        p[1] = new.y() + (p[1] - old.y()) * sy


def clone_layer(layer: dict, new_id: int, offset: float = 18.0) -> dict:
    """深拷贝图层并轻微偏移，用作「复制」生成的新独立图层。"""
    dup = copy.deepcopy(layer)
    dup["id"] = new_id
    translate_layer(dup, offset, offset)
    return dup


def clone_layers(layers: list[dict]) -> list[dict]:
    return copy.deepcopy(layers)


# ---------------------------------------------------------------------------
# 工程文件校验：把外部数据清洗成合法图层，损坏数据抛中文异常而不是崩溃
# ---------------------------------------------------------------------------

class ProjectError(Exception):
    """工程文件损坏 / 版本不兼容。消息为中文，可直接展示给用户。"""


def _num(v: Any, default: float) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def sanitize_layer(raw: Any, fallback_id: int) -> dict:
    """校验并规范化单个图层，缺失字段用默认值补齐，类型错误就地修正。"""
    if not isinstance(raw, dict):
        raise ProjectError("可编辑图层文件中的图层数据格式不正确。")
    kind = raw.get("type")
    if kind not in DRAW_TOOLS:
        raise ProjectError(f"可编辑图层文件包含不支持的图层类型：{kind}")

    layer = new_layer(kind, int(_num(raw.get("id"), fallback_id)))
    r = raw.get("rect")
    if isinstance(r, (list, tuple)) and len(r) == 4:
        layer["rect"] = [_num(r[i], 0.0) for i in range(4)]
    pts = raw.get("points")
    if isinstance(pts, list):
        clean = []
        for p in pts[:20000]:                       # 防止异常文件撑爆内存
            if isinstance(p, (list, tuple)) and len(p) == 2:
                clean.append([_num(p[0], 0.0), _num(p[1], 0.0)])
        layer["points"] = clean

    for key in ("color", "outline_color", "fill_color"):
        v = raw.get(key)
        if isinstance(v, str) and _is_color(v):
            layer[key] = v
    for key in ("font_family", "text"):
        v = raw.get(key)
        if isinstance(v, str):
            layer[key] = v[:2000] if key == "text" else v[:120]
    for key in _NUMERIC_INT_KEYS:
        layer[key] = int(_num(raw.get(key), layer[key]))
    for key in _BOOL_KEYS:
        layer[key] = bool(raw.get(key, layer[key]))

    layer["width"] = max(1, min(60, layer["width"]))
    layer["outline_width"] = max(1, min(30, layer["outline_width"]))
    layer["brush"] = max(4, min(400, layer["brush"]))
    layer["font_size"] = max(6, min(300, layer["font_size"]))
    layer["fill_alpha"] = max(0, min(100, layer["fill_alpha"]))
    if kind == "text":
        refresh_text_bounds(layer)
    elif kind.startswith("circle"):
        _make_square(layer)
    return layer


def _is_color(v: str) -> bool:
    return len(v) == 7 and v.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in v[1:])


def parse_project(data: dict) -> tuple[list[dict], int]:
    """解析 project.json，返回 (图层列表, 下一个可用 id)。"""
    if not isinstance(data, dict):
        raise ProjectError("可编辑图层文件内容不是有效的 JSON 对象。")
    version = data.get("version")
    if not isinstance(version, int) or version < 1 or version > PROJECT_VERSION:
        raise ProjectError(f"可编辑图层文件版本不兼容（文件版本 {version}，当前支持 1~{PROJECT_VERSION}）。")
    raw_layers = data.get("layers")
    if not isinstance(raw_layers, list):
        raise ProjectError("可编辑图层文件缺少图层数据。")
    layers = [sanitize_layer(item, i + 1) for i, item in enumerate(raw_layers)]
    used = {int(l["id"]) for l in layers}
    next_id = max(used, default=0) + 1
    return layers, next_id


def build_project(layers: list[dict], next_id: int) -> dict:
    return {
        "version": PROJECT_VERSION,
        "app": "SnapAnno",
        "next_id": int(next_id),
        "layers": clone_layers(layers),
    }


def handle_positions(bounds: QRectF) -> list[tuple[str, QPointF]]:
    """8 个控制点：(标识, 图像像素坐标)。"""
    l, t, r, b = bounds.left(), bounds.top(), bounds.right(), bounds.bottom()
    cx, cy = bounds.center().x(), bounds.center().y()
    return [
        ("nw", QPointF(l, t)), ("n", QPointF(cx, t)), ("ne", QPointF(r, t)),
        ("w", QPointF(l, cy)),                          ("e", QPointF(r, cy)),
        ("sw", QPointF(l, b)), ("s", QPointF(cx, b)), ("se", QPointF(r, b)),
    ]


def resize_bounds(bounds: QRectF, handle: str, delta: QPointF, keep_square: bool) -> QRectF:
    """根据被拖动的控制点计算新包围盒。"""
    l, t, r, b = bounds.left(), bounds.top(), bounds.right(), bounds.bottom()
    dx, dy = delta.x(), delta.y()
    if "w" in handle:
        l += dx
    if "e" in handle:
        r += dx
    if "n" in handle:
        t += dy
    if "s" in handle:
        b += dy
    # 禁止翻转，并保留最小尺寸
    if r - l < 8:
        if "w" in handle:
            l = r - 8
        else:
            r = l + 8
    if b - t < 8:
        if "n" in handle:
            t = b - 8
        else:
            b = t + 8
    if keep_square and handle in ("nw", "ne", "sw", "se"):
        side = max(r - l, b - t)
        if "w" in handle:
            l = r - side
        else:
            r = l + side
        if "n" in handle:
            t = b - side
        else:
            b = t + side
    return QRectF(l, t, r - l, b - t)
