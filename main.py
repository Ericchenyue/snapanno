"""SnapAnno 入口。

Windows 高 DPI：让 Qt 6 使用逻辑坐标并开启高 DPI 缩放；
截图模块在抓屏时按各屏幕 devicePixelRatio 统一换算，避免坐标错位。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtGui import QImage                                 # noqa: E402
from PySide6.QtWidgets import (                                  # noqa: E402
    QApplication, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from app.capture import CaptureController                        # noqa: E402
from app.config import APP_NAME, APP_VERSION, STYLESHEET          # noqa: E402
from app.editor import EditorWindow                              # noqa: E402


class MainWindow(QWidget):
    """主界面：截图 / 编辑。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setFixedSize(360, 280)
        self._quitting = False

        self.editor = EditorWindow(on_close=self.show_main)
        self.capture = CaptureController(self, self._on_capture_confirmed)

        title = QLabel(APP_NAME)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:30px; font-weight:bold; color:#2F6FE4;")
        subtitle = QLabel("桌面截图与图片标注工具")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color:#6B7280; font-size:13px;")

        self.btn_capture = QPushButton("截图")
        self.btn_edit = QPushButton("编辑")
        for b in (self.btn_capture, self.btn_edit):
            b.setFixedHeight(46)
            b.setStyleSheet("font-size:14px;")
        self.btn_capture.clicked.connect(lambda checked=False: self.capture.start())
        self.btn_edit.clicked.connect(lambda checked=False: self.start_edit())

        hint = QLabel("截图：按住左键拉框，ESC 取消\n"
                      "编辑：状态栏滑块 / ＋－ 缩放 · Ctrl+C 复制选中标注 · Ctrl+Z 撤销")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color:#8A919E; font-size:11px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 26, 36, 20)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(14)
        layout.addWidget(self.btn_capture)
        layout.addWidget(self.btn_edit)
        layout.addStretch(1)
        layout.addWidget(hint)
        layout.addWidget(QLabel(f"v{APP_VERSION} · 本地运行，不联网"))

    def start_edit(self) -> None:
        """主界面点「编辑」：先进入选择状态。"""
        self.hide()
        self.editor.enter_from_main()
        self.editor.showMaximized()

    def _on_capture_confirmed(self, image: QImage) -> None:
        """截图确认：以截图为底图直接进入正式编辑页面。"""
        self.hide()
        self.editor.open_with_image(image)
        self.editor.showMaximized()

    def show_main(self) -> None:
        if self._quitting:
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        self._quitting = True
        self.capture.cancel()
        self.editor.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
