"""通用对话框与自定义控件：颜色按钮、新建画布、文字输入、截图确认窗口。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)


class ColorButton(QPushButton):
    """显示当前颜色的小按钮，点击弹出取色框。"""

    colorChanged = Signal(str)

    def __init__(self, color: str = "#E53935", parent: QWidget | None = None):
        super().__init__(parent)
        self._color = color
        self.setFixedHeight(26)
        self.setToolTip("点击选择颜色")
        self.clicked.connect(lambda checked=False: self._choose())
        self._sync()

    def color(self) -> str:
        return self._color

    def setColor(self, value: str) -> None:
        if value and value != self._color:
            self._color = value
            self._sync()

    def _sync(self) -> None:
        self.setText(f"  {self._color.upper()}  ")
        self.setStyleSheet(
            f"QPushButton {{ background:{self._color}; color:{'#FFFFFF' if _dark(self._color) else '#222222'};"
            " border:1px solid #B9BEC7; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { border-color:#2F6FE4; }"
        )

    def _choose(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, "选择颜色")
        if c.isValid():
            self._color = c.name()
            self._sync()
            self.colorChanged.emit(self._color)


def _dark(hex_color: str) -> bool:
    """根据亮度决定按钮文字黑白，保证色块上的文字可读。"""
    c = QColor(hex_color)
    return (c.red() * 0.299 + c.green() * 0.587 + c.blue() * 0.114) < 150


class NewCanvasDialog(QDialog):
    """新建空白画布：选择颜色与宽高。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("新建空白画布")
        self.setModal(True)
        self._color = "#FFFFFF"

        self.color_button = ColorButton(self._color, self)
        self.color_button.colorChanged.connect(self._on_color)
        self.width_spin = QSpinBox()
        self.height_spin = QSpinBox()
        for spin, default in ((self.width_spin, 1280), (self.height_spin, 720)):
            spin.setRange(32, 8192)
            spin.setValue(default)
            spin.setSuffix(" px")
            spin.setAccelerated(True)

        form = QFormLayout()
        form.addRow("画布颜色：", self.color_button)
        form.addRow("宽度：", self.width_spin)
        form.addRow("高度：", self.height_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setFixedWidth(300)

    def _on_color(self, value: str) -> None:
        self._color = value

    def values(self) -> tuple[str, int, int]:
        return self._color, self.width_spin.value(), self.height_spin.value()


class TextInputDialog(QDialog):
    """文字标注输入；空文字不会产生图层。"""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("文字标注")
        self.setModal(True)
        self.edit = QTextEdit()
        self.edit.setPlainText(text)
        self.edit.setFixedHeight(110)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入标注文字（可换行）："))
        layout.addWidget(self.edit)
        layout.addWidget(buttons)
        self.resize(360, 200)
        self.edit.setFocus()

    def text(self) -> str:
        return self.edit.toPlainText()


class CaptureConfirmDialog(QDialog):
    """截图确认窗口：预览 + 确认 / 重新截图；ESC 取消整个截图流程。"""

    confirmed = Signal()
    reshoot = Signal()
    cancelled = Signal()

    def __init__(self, image: QImage, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("截图预览")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setModal(False)

        preview = QLabel()
        preview.setAlignment(Qt.AlignCenter)
        preview.setMinimumSize(220, 140)
        preview.setStyleSheet("background:#2B2F36; border:1px solid #C9CDD4; border-radius:4px;")
        pm = QPixmap.fromImage(image)
        preview.setPixmap(pm.scaled(QSize(440, 280), Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.btn_ok = QPushButton("确认")
        self.btn_again = QPushButton("重新截图")
        self.btn_ok.setDefault(True)
        for b in (self.btn_ok, self.btn_again):
            b.setMinimumWidth(110)
        self.btn_ok.clicked.connect(lambda checked=False, s=self.confirmed: s.emit())
        self.btn_again.clicked.connect(lambda checked=False, s=self.reshoot: s.emit())

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn_again)
        row.addWidget(self.btn_ok)
        row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"已截取区域：{image.width()} × {image.height()} 像素"))
        layout.addWidget(preview, 1)
        layout.addLayout(row)
        self.setMinimumSize(360, 260)
        self.adjustSize()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        """用户点右上角关闭 = 取消整个截图流程。"""
        self.cancelled.emit()
        super().closeEvent(event)

    def place_near(self, center_x: int, center_y: int) -> None:
        """尽量放到选区所在显示器中央。"""
        screen = QGuiApplication.screenAt(QPoint(center_x, center_y))
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.center().y() - self.height() // 2)
