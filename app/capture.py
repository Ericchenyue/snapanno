"""截图流程：多显示器桌面合成、高 DPI 坐标换算、拉框覆盖层、确认/重截/取消。

坐标约定：
  - 屏幕几何（geometry）为 Qt 逻辑坐标；
  - grabWindow(0) 返回设备像素；
  - 合成图按「逻辑尺寸 × 主屏 dpr」构造，覆盖层铺满虚拟桌面（逻辑坐标）；
  - 选区（逻辑）→ 合成图设备像素只需乘同一个 dpr，因此不会随缩放错位。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QGuiApplication, QImage, QKeyEvent, QPainter, QPen, QBrush,
)
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from .config import MIN_SELECTION
from .dialogs import CaptureConfirmDialog


class CaptureError(Exception):
    """截图失败，消息为中文。"""


def grab_desktop() -> tuple[QImage, float, QRect]:
    """把所有显示器合成为一张设备像素图，返回 (合成图, dpr, 虚拟桌面逻辑矩形)。"""
    screens = QGuiApplication.screens()
    if not screens:
        raise CaptureError("未检测到可用的显示器。")

    vgeo = QRect()
    for screen in screens:
        vgeo = vgeo.united(screen.geometry())
    if vgeo.width() <= 0 or vgeo.height() <= 0:
        raise CaptureError("无法获取桌面尺寸。")

    dpr = QGuiApplication.primaryScreen().devicePixelRatio() or 1.0
    width = max(1, round(vgeo.width() * dpr))
    height = max(1, round(vgeo.height() * dpr))
    composite = QImage(width, height, QImage.Format_RGB32)
    composite.fill(Qt.black)

    painter = QPainter(composite)
    ok_any = False
    for screen in screens:
        pm = screen.grabWindow(0)
        if pm.isNull():
            continue
        geo = screen.geometry()
        target = QRectF(
            (geo.x() - vgeo.x()) * dpr,
            (geo.y() - vgeo.y()) * dpr,
            geo.width() * dpr,
            geo.height() * dpr,
        )
        painter.drawPixmap(target, pm, QRectF(pm.rect()))
        ok_any = True
    painter.end()
    if not ok_any:
        raise CaptureError("未能抓取任何显示器画面。")
    return composite, dpr, vgeo


def looks_black(image: QImage, samples: int = 5) -> bool:
    """整屏网格抽样，判断抓屏是否异常全黑。

    Windows 服务/远程/未登录桌面会话下 grabWindow(0) 常返回黑屏，
    此时给出提示比默默交付一张黑图更友好。
    """
    if image.isNull():
        return False
    w, h = image.width(), image.height()
    black = total = 0
    for i in range(1, samples + 1):
        for j in range(1, samples + 1):
            c = image.pixelColor(int(w * i / (samples + 1)), int(h * j / (samples + 1)))
            total += 1
            if max(c.red(), c.green(), c.blue()) < 12:
                black += 1
    return total > 0 and black / total >= 0.96


def _clamp_rect(rect: QRect, limit: QRect) -> QRect:
    """把矩形裁剪到图像范围内。"""
    return rect.intersected(limit)


class SelectionOverlay(QWidget):
    """全虚拟桌面置顶覆盖层：暗化桌面 + 实时选区边框与尺寸。"""

    regionSelected = Signal(object, int, int)   # (QImage, 选区中心全局 x, y)
    cancelled = Signal()

    def __init__(self, composite: QImage, dpr: float, vgeo: QRect):
        super().__init__()
        self._bg = composite
        self._dpr = dpr
        self._vgeo = vgeo
        self._start: QPoint | None = None
        self._current: QPoint | None = None
        self._selecting = False
        self._interactive = True

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(vgeo)

    def set_interactive(self, value: bool) -> None:
        self._interactive = value

    def reset(self) -> None:
        """清空上一次选区（连续截图不残留）。"""
        self._start = self._current = None
        self._selecting = False
        self._interactive = True
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if not self._interactive or event.button() != Qt.LeftButton:
            return
        self._start = event.position().toPoint()
        self._current = QPoint(self._start)
        self._selecting = True
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._selecting:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if not self._selecting or event.button() != Qt.LeftButton:
            return
        self._selecting = False
        self._current = event.position().toPoint()
        rect = QRect(self._start, self._current).normalized()
        # 选区过小：不进入确认窗口，直接重新等待选择
        if rect.width() < MIN_SELECTION or rect.height() < MIN_SELECTION:
            self.reset()
            return
        self._interactive = False
        self.update()
        try:
            image, cx, cy = self._crop(rect)
        except CaptureError as exc:
            QMessageBox.warning(self, "截图失败", str(exc))
            self.reset()
            return
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            self.reset()
            return
        self.regionSelected.emit(image, cx, cy)

    def _crop(self, rect: QRect) -> tuple[QImage, int, int]:
        """把逻辑选区换算为合成图设备像素并裁切。"""
        d = self._dpr
        dev = QRect(
            int(round(rect.x() * d)),
            int(round(rect.y() * d)),
            max(1, int(round(rect.width() * d))),
            max(1, int(round(rect.height() * d))),
        )
        dev = _clamp_rect(dev, self._bg.rect())
        if dev.width() < 2 or dev.height() < 2:
            raise CaptureError("选区无效，请重新框选。")
        cropped = self._bg.copy(dev).convertToFormat(QImage.Format_ARGB32)
        center = self.mapToGlobal(rect.center())
        return cropped, center.x(), center.y()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        # 合成图按窗口逻辑尺寸整体缩放绘制，与屏幕内容对齐
        painter.drawImage(QRectF(self.rect()), self._bg, QRectF(self._bg.rect()))
        selection = QRect()
        if self._start and self._current:
            selection = QRect(self._start, self._current).normalized()

        dim = QColor(0, 0, 0, 105)
        if selection.isNull() or selection.width() < 1 or selection.height() < 1:
            painter.fillRect(self.rect(), dim)
        else:
            # 选区外四个矩形暗化，选区内保持原亮度
            x, y, w, h = selection.x(), selection.y(), selection.width(), selection.height()
            for r in (QRect(0, 0, self.width(), y),
                      QRect(0, y + h, self.width(), self.height() - y - h),
                      QRect(0, y, x, h),
                      QRect(x + w, y, self.width() - x - w, h)):
                painter.fillRect(r, dim)
            self._draw_border(painter, selection, x, y, w, h)
        painter.end()

    @staticmethod
    def _draw_border(painter: QPainter, selection: QRect, x: int, y: int, w: int, h: int) -> None:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 200), 3))
        painter.drawRect(selection)
        painter.setPen(QPen(Qt.white, 1, Qt.DashLine))
        painter.drawRect(selection)

        label = f"{w} × {h}"
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw, th = fm.horizontalAdvance(label) + 12, fm.height() + 6
        ly = y - th - 6 if y - th - 6 > 0 else y + 4
        box = QRect(x, ly, tw, th)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(20, 24, 30, 220)))
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, label)


class _EscapeFilter(QObject):
    """截图期间应用级 ESC 监听，避免焦点漂移导致 ESC 失效。"""

    escapePressed = Signal()

    def eventFilter(self, _obj, event) -> bool:
        if isinstance(event, QKeyEvent) and event.type() == QEvent.KeyPress \
                and event.key() == Qt.Key_Escape:
            self.escapePressed.emit()
            return True
        return False


class CaptureController(QObject):
    """截图状态机：拉框 → 确认 / 重新截图 / ESC 取消 → 交付底图。"""

    def __init__(self, main_window: QWidget, on_confirmed, parent: QObject | None = None):
        super().__init__(parent)
        self._main = main_window
        self._on_confirmed = on_confirmed
        self._overlay: SelectionOverlay | None = None
        self._confirm: CaptureConfirmDialog | None = None
        self._captured: QImage | None = None
        self._active = False
        self._warned_black = False
        self._esc_filter = _EscapeFilter(self)
        self._esc_filter.escapePressed.connect(self.cancel)

    # -- 内部工具 ----------------------------------------------------------
    @staticmethod
    def _settle(millis: int = 120) -> None:
        """等待窗口真正从屏幕消失后再抓屏，避免把覆盖层拍进去。"""
        QApplication.processEvents()
        time.sleep(millis / 1000.0)
        QApplication.processEvents()

    def _close_overlay(self) -> None:
        ov, self._overlay = self._overlay, None
        if ov is not None:
            ov.blockSignals(True)
            ov.hide()
            ov.deleteLater()

    def _close_confirm(self) -> None:
        dlg, self._confirm = self._confirm, None
        if dlg is not None:
            dlg.blockSignals(True)
            dlg.hide()
            dlg.deleteLater()

    def _cleanup(self) -> None:
        qapp = QApplication.instance()
        if qapp is not None:
            qapp.removeEventFilter(self._esc_filter)
        self._close_confirm()
        self._close_overlay()
        self._captured = None
        self._active = False

    def _show_main(self) -> None:
        self._main.showNormal()
        self._main.raise_()
        self._main.activateWindow()

    # -- 状态机 ------------------------------------------------------------
    def start(self) -> None:
        """主界面点「截图」：隐藏主窗口，进入可拉框状态。"""
        self._cleanup()
        self._active = True
        self._warned_black = False
        qapp = QApplication.instance()
        if qapp is not None:
            qapp.installEventFilter(self._esc_filter)
        self._main.hide()
        self._settle()
        try:
            self._open_overlay()
        except CaptureError as exc:
            self._cleanup()
            self._show_main()
            QMessageBox.warning(self._main, "截图失败", str(exc))
        except Exception as exc:                     # noqa: BLE001 - 兜底，保证不崩溃
            self._cleanup()
            self._show_main()
            QMessageBox.warning(self._main, "截图失败", f"无法启动截图：{exc}")

    def _open_overlay(self) -> None:
        composite, dpr, vgeo = grab_desktop()
        if looks_black(composite) and not self._warned_black:
            self._warned_black = True
            QMessageBox.warning(
                self._main, "截图提示",
                "抓到的桌面画面疑似全黑。\n\n"
                "常见原因：当前运行在远程桌面、Windows 服务或未登录的桌面会话中，"
                "或目标窗口启用了硬件加速保护。\n"
                "请在正常登录的本地桌面会话中运行 SnapAnno 后重试。\n\n"
                "（截图流程仍会继续，可正常框选。）",
            )
        ov = SelectionOverlay(composite, dpr, vgeo)
        ov.regionSelected.connect(self._on_region)
        ov.cancelled.connect(self.cancel)
        self._overlay = ov
        ov.show()
        ov.raise_()
        ov.activateWindow()
        ov.setFocus()

    def _on_region(self, image: QImage, cx: int, cy: int) -> None:
        """松手得到有效选区：隐藏覆盖层，弹确认窗口。"""
        if not self._active:
            return
        self._captured = image
        if self._overlay is not None:
            self._overlay.hide()
        dlg = CaptureConfirmDialog(image)
        dlg.confirmed.connect(self._on_confirm)
        dlg.reshoot.connect(self._reshoot)
        dlg.cancelled.connect(self.cancel)
        self._confirm = dlg
        dlg.place_near(cx, cy)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _reshoot(self) -> None:
        """重新截图：销毁确认窗与旧覆盖层，全新实例，不留上次选区。"""
        if not self._active:
            return
        self._close_confirm()
        self._close_overlay()
        self._captured = None
        self._settle()
        try:
            self._open_overlay()
        except Exception as exc:                     # noqa: BLE001
            self._cleanup()
            self._show_main()
            QMessageBox.warning(self._main, "截图失败", f"重新截图失败：{exc}")

    def _on_confirm(self) -> None:
        """确认：以截图为底图直接进入编辑工作台。"""
        image = self._captured
        self._cleanup()
        if image is None or image.isNull():
            self._show_main()
            QMessageBox.warning(self._main, "截图失败", "截图内容为空，请重试。")
            return
        self._on_confirmed(image)

    def cancel(self) -> None:
        """ESC / 关闭预览：取消整个截图流程并恢复主界面。"""
        self._cleanup()
        self._show_main()
