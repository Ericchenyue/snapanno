"""文件 IO：JPG 拼合导出、.snproj 工程（ZIP）读写、普通图片加载。

.snproj 本质为 ZIP：
  original.png   原始底图（无损，不含标注）
  project.json   全部图层与元数据
  thumbnail.png  缩略图（可选）
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

from .models import ProjectError, build_project, parse_project
from .render import render_document, to_rgb


def timestamp_name(prefix: str = "SnapAnno", ext: str = ".jpg") -> str:
    """默认文件名：SnapAnno_20261105_165500.jpg"""
    return f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}{ext}"


def save_jpeg(path: str, image: QImage, quality: int = 92) -> None:
    """把最终效果（底图 + 全部图层）保存为 JPG。"""
    if not path:
        raise ProjectError("未选择保存位置。")
    rgb = to_rgb(image)
    if rgb.isNull() or not rgb.save(path, "JPEG", quality):
        raise ProjectError("保存图片失败，请检查保存路径与磁盘权限。")


def _png_bytes(image: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    if not image.save(buf, "PNG"):
        buf.close()
        raise ProjectError("无法编码底图，保存可编辑图层文件失败。")
    buf.close()
    return bytes(ba.data())


def save_project(path: str, base: QImage, layers: list[dict], next_id: int) -> None:
    """保存 .snproj（ZIP + JSON + 无损底图）。"""
    if not path:
        raise ProjectError("未选择保存位置。")
    thumb = base.scaled(320, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    payload = json.dumps(build_project(layers, next_id), ensure_ascii=False, indent=2).encode("utf-8")
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("original.png", _png_bytes(base))
            zf.writestr("project.json", payload)
            zf.writestr("thumbnail.png", _png_bytes(thumb))
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise ProjectError(f"保存可编辑图层文件失败：{exc}") from exc


def load_project(path: str) -> tuple[QImage, list[dict], int]:
    """读取 .snproj，返回 (底图, 图层列表, 下一个图层 id)。"""
    if not os.path.isfile(path):
        raise ProjectError("可编辑图层文件不存在。")
    if not zipfile.is_zipfile(path):
        raise ProjectError("可编辑图层文件已损坏（不是有效的 ZIP 文件）。")
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            missing = {"original.png", "project.json"} - names
            if missing:
                raise ProjectError("可编辑图层文件缺少必要内容，可能已损坏。")
            raw_json = zf.read("project.json")
            raw_png = zf.read("original.png")
        data = json.loads(raw_json.decode("utf-8"))
        layers, next_id = parse_project(data)     # 先校验版本/结构，便于给出准确中文提示
        base = QImage()
        if not base.loadFromData(raw_png, "PNG") or base.isNull():
            raise ProjectError("可编辑图层文件中的底图无法读取。")
    except ProjectError:
        raise
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError,
            KeyError, RuntimeError) as exc:
        raise ProjectError(f"可编辑图层文件损坏或版本不兼容：{exc}") from exc

    return base.convertToFormat(QImage.Format_ARGB32), layers, next_id


def load_image(path: str) -> QImage:
    """加载普通图片作为底图；不支持的格式/损坏文件抛中文异常。"""
    if not os.path.isfile(path):
        raise ProjectError("图片文件不存在。")
    reader_image = QImage(path)
    if reader_image.isNull():
        raise ProjectError("无法打开该图片，文件格式可能不受支持或文件已损坏。")
    return reader_image.convertToFormat(QImage.Format_ARGB32)


def open_any(path: str) -> tuple[QImage, list[dict], int]:
    """按扩展名分派：.snproj 恢复工程，其余按图片打开。"""
    if path.lower().endswith(".snproj"):
        return load_project(path)
    return load_image(path), [], 1


def render_for_export(base: QImage, layers: list[dict]) -> QImage:
    """供导出使用的最终拼合图。"""
    return render_document(base, layers)
