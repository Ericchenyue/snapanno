"""编辑工作台：起始选择页、顶部文件/编辑栏、左侧工具栏、画布、右侧属性区。

撤销/重做采用「图层状态快照栈」，因此添加、删除、移动、缩放、改样式、
复制、图层上下移、马赛克/模糊的全部操作都自动纳入撤销范围，且不影响底图。
"""

from __future__ import annotations

import copy
import math
import os

from PySide6.QtCore import (
    QBuffer, QEvent, QIODevice, QMimeData, QPointF, QSize, QStandardPaths,
    Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QCursor, QGuiApplication, QImage, QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QSplitter, QStackedWidget, QToolTip, QVBoxLayout, QWidget,
)

from .canvas import Canvas
from .config import (
    APP_NAME, DRAW_TOOLS, MAX_UNDO, OPEN_FILTER, PROJECT_EXT, TOOL_LIST,
)
from .dialogs import NewCanvasDialog
from .expand_overlay import ExpandOverlay
from .icons import tool_icon
from .render import expand_canvas, to_rgb
from .models import (
    ProjectError, clone_layer, clone_layers, default_style, refresh_text_bounds,
    translate_layer,
)
from .project_io import (
    open_any, render_for_export, save_jpeg, save_project, timestamp_name,
)
from .properties import PropertiesPanel


def _default_dir() -> str:
    path = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
    return path if path and os.path.isdir(path) else os.path.expanduser("~")


class StartPage(QWidget):
    """点「编辑」后的选择状态：先决定打开什么 / 新建什么。"""

    requestOpen = Signal()
    requestNew = Signal()
    requestBack = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet("background:#F5F6F8;")
        title = QLabel("编辑工作台")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:24px; font-weight:bold; color:#2F6FE4;")
        desc = QLabel("请选择一种开始方式：")
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("color:#6B7280; font-size:13px;")

        self.btn_open = QPushButton("打开图片 / 打开可编辑图层文件")
        self.btn_new = QPushButton("新建空白画布")
        self.btn_back = QPushButton("返回主界面")
        for b in (self.btn_open, self.btn_new, self.btn_back):
            b.setFixedWidth(240)
            b.setFixedHeight(40)
        self.btn_open.clicked.connect(lambda checked=False, s=self.requestOpen: s.emit())
        self.btn_new.clicked.connect(lambda checked=False, s=self.requestNew: s.emit())
        self.btn_back.clicked.connect(lambda checked=False, s=self.requestBack: s.emit())

        box = QVBoxLayout()
        box.setAlignment(Qt.AlignCenter)
        box.setSpacing(14)
        box.addWidget(title)
        box.addWidget(desc)
        box.addSpacing(16)
        box.addWidget(self.btn_open, 0, Qt.AlignCenter)
        box.addWidget(self.btn_new, 0, Qt.AlignCenter)
        box.addSpacing(10)
        box.addWidget(self.btn_back, 0, Qt.AlignCenter)

        root = QVBoxLayout(self)
        root.addStretch(1)
        root.addLayout(box)
        root.addStretch(1)


class EditorWindow(QMainWindow):
    """标注编辑主窗口。"""

    def __init__(self, on_close=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} - 编辑工作台")
        self.resize(1280, 820)
        self._on_close = on_close

        # -- 文档状态 ------------------------------------------------------
        self.base = QImage()
        self.layers: list[dict] = []
        self.next_id = 1
        self.tool = "select"
        self.tool_defaults = {t: default_style(t) for t in DRAW_TOOLS}
        self._history: list[list[dict]] = []
        self._hindex = -1
        self._doc_loaded = False
        self._project_path = ""
        self._auto_fit = True            # 用户手动缩放后不再被窗口 resize 重置
        self._expand_overlay = None      # V1.2 r3：扩大画布预览覆盖层
        self._expand_color = "#FFFFFF"  # 扩展区域填充色（默认白）

        self._build_ui()
        self._build_shortcuts()
        self._sync_history_buttons()

        # V1.2 修复：滚轮/快捷键改用应用级事件过滤。画布只占滚动区中间一块，
        # 鼠标落在空白、滚动条、属性面板时事件不会进 Canvas，必须由窗口统一接管。
        app = QApplication.instance()
        self._filter_installed = app is not None
        if app is not None:
            app.installEventFilter(self)

    # -- UI ----------------------------------------------------------------
    def _build_ui(self) -> None:
        self.stack = QStackedWidget()
        self.start_page = StartPage()
        self.work_view = self._build_work_view()
        self.stack.addWidget(self.start_page)
        self.stack.addWidget(self.work_view)
        self.setCentralWidget(self.stack)

        self.start_page.requestOpen.connect(self.open_file)
        self.start_page.requestNew.connect(self.new_canvas)
        self.start_page.requestBack.connect(self.close)

        self.statusBar().showMessage("就绪")
        self._add_zoom_controls()

    def _build_work_view(self) -> QWidget:
        view = QWidget()
        root = QVBoxLayout(view)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_left_tools())

        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setWidgetResizable(False)
        self.canvas = Canvas(self)
        self.scroll.setWidget(self.canvas)

        self.panel = PropertiesPanel()
        self.panel.setMinimumWidth(250)
        self.panel.setMaximumWidth(560)

        # V1.2：属性区放进可拖拽分隔条；文字工具下还会按内容自动加宽
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(5)
        self.splitter.setStyleSheet("QSplitter::handle { background:#D5D9E0; }")
        self.splitter.addWidget(self.scroll)
        self.splitter.addWidget(self.panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([900, 290])
        body.addWidget(self.splitter, 1)
        root.addLayout(body, 1)

        self.canvas.committed.connect(self.commit)
        self.canvas.zoomChanged.connect(self._on_zoom_changed)
        self.canvas.selectionChanged.connect(self._on_selection_changed)
        self.canvas.statusMessage.connect(self.statusBar().showMessage)
        self.panel.liveChanged.connect(self._on_style_live)
        self.panel.committedStyle.connect(self._on_style_committed)
        self.panel.copyRequested.connect(self.copy_selected)
        self.panel.deleteRequested.connect(self.delete_selected)
        self.panel.widthHint.connect(self._set_panel_width)
        self.panel.expandColorChanged.connect(self._on_expand_color_changed)
        for bar in (self.scroll.horizontalScrollBar(), self.scroll.verticalScrollBar()):
            bar.valueChanged.connect(self._on_scroll_for_overlay)
        return view

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet("background:#FFFFFF; border-bottom:1px solid #E0E3E8;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        def button(text: str, slot, width: int | None = None, tip: str = "") -> QPushButton:
            b = QPushButton(text)
            b.clicked.connect(lambda checked=False, s=slot: s())
            if width:
                b.setFixedWidth(width)
            if tip:
                b.setToolTip(tip)
            layout.addWidget(b)
            return b

        button("保存为图片", self.save_image)
        self.btn_save_project = button("保存为可编辑图层文件", self.save_project)
        button("打开", self.open_file)
        self.btn_copy = button("复制", self.copy_image_to_clipboard,
                              tip="把底图和全部标注拼合成图片，直接放入剪贴板")
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(lambda: self.btn_copy.setText("复制"))
        button("新建空白画布", self.new_canvas)
        self.btn_expand = button("扩大画布", self.enter_expand_mode,
                                 tip="在图片四边显示控制点，向外拖动扩展画布（拖动只预览）")
        self.btn_expand_apply = button("应用扩展", self.apply_expand,
                                       tip="按预览区域生成新画布，并把扩展色烘焙进底图")
        self.btn_expand_cancel = button("取消", self.cancel_expand,
                                        tip="放弃预览，不改动原图")
        self.btn_expand_apply.setVisible(False)
        self.btn_expand_cancel.setVisible(False)
        layout.addSpacing(10)
        self.btn_undo = button("撤销", self.undo, 70)
        self.btn_redo = button("重做", self.redo, 70)
        layout.addStretch(1)
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet("color:#6B7280;")
        layout.addWidget(self.lbl_info)
        return bar

    def _build_left_tools(self) -> QWidget:
        box = QFrame()
        box.setFixedWidth(168)
        box.setStyleSheet("background:#FFFFFF; border-right:1px solid #E0E3E8;")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(3)
        title = QLabel("标注工具")
        title.setStyleSheet("font-weight:bold; color:#2F6FE4; padding-bottom:4px;")
        layout.addWidget(title)

        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_buttons: dict[str, QPushButton] = {}
        for key, name in TOOL_LIST:
            b = QPushButton(name)
            b.setMinimumHeight(28)
            b.setIcon(tool_icon(key))
            b.setIconSize(QSize(18, 18))
            b.setStyleSheet(
                "QPushButton { text-align:left; padding:4px 6px; }"
                "QPushButton:checked { background:#FFF1F0; border-color:#E53935; "
                "color:#D32F2F; font-weight:bold; }"
            )
            if key in ("layer_up", "layer_down"):
                action = self.layer_up if key == "layer_up" else self.layer_down
                b.clicked.connect(lambda checked=False, s=action: s())
            else:
                b.setCheckable(True)
                b.clicked.connect(lambda checked=False, k=key: self.set_tool(k))
                self.tool_group.addButton(b)
                self.tool_buttons[key] = b
            layout.addWidget(b)
        layout.addStretch(1)
        self.tool_buttons["select"].setChecked(True)
        return box

    def _build_shortcuts(self) -> None:
        act_undo = QAction("撤销", self)
        act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        act_undo.setShortcutContext(Qt.WindowShortcut)
        act_undo.triggered.connect(self.undo)
        self.addAction(act_undo)

        for seq in ("Ctrl+Y", "Ctrl+Shift+Z"):
            act = QAction("重做", self)
            act.setShortcut(QKeySequence(seq))
            act.setShortcutContext(Qt.WindowShortcut)
            act.triggered.connect(self.redo)
            self.addAction(act)

    # -- 应用级事件过滤（V1.2 修复）----------------------------------------
    def _shortcut_active(self) -> bool:
        """只在编辑工作台为活动窗口时接管滚轮与 Ctrl+C，不干扰其它窗口。"""
        if not self._doc_loaded or self.stack.currentWidget() is not self.work_view:
            return False
        try:
            active = QApplication.activeWindow()
        except AttributeError:                 # 自检里模块名被临时替换
            active = None
        return self.isActiveWindow() or active is None

    @staticmethod
    def _is_alt_down(event) -> bool:
        """事件修饰键 + 实时按键状态双保险（部分平台 Alt 不写进事件）。"""
        mods = event.modifiers()
        try:
            mods = mods | QApplication.keyboardModifiers()
        except (AttributeError, RuntimeError):
            pass
        return bool(mods & Qt.AltModifier)

    @staticmethod
    def _text_selection_exists() -> bool:
        """焦点控件里有选中文字时，Ctrl+C 仍按常规复制文字。"""
        w = QApplication.focusWidget()
        if w is None:
            return False
        try:
            if hasattr(w, "textCursor"):
                return w.textCursor().hasSelection()
            if hasattr(w, "selectedText"):
                return bool(w.selectedText())
        except (AttributeError, RuntimeError):
            return False
        return False

    def eventFilter(self, obj, event):
        et = event.type()
        # 扩大画布预览层必须跟随 viewport 尺寸变化（放在快捷键守门之前，不吞事件）
        if (et == QEvent.Resize and obj is self.scroll.viewport()
                and self._expand_overlay is not None):
            self._expand_overlay.setGeometry(0, 0, self.scroll.viewport().width(),
                                             self.scroll.viewport().height())
            self._expand_overlay.update()
            return super().eventFilter(obj, event)
        if et not in (QEvent.Wheel, QEvent.KeyPress):
            return super().eventFilter(obj, event)
        if not self._shortcut_active():
            return super().eventFilter(obj, event)

        if et == QEvent.Wheel and self._is_alt_down(event):
            self._handle_alt_wheel(event, obj)
            return True                            # 吞掉，不再转发给 QScrollArea
        if (et == QEvent.KeyPress and event.key() == Qt.Key_C
                and (event.modifiers() & Qt.ControlModifier)
                and not (event.modifiers() & (Qt.AltModifier | Qt.ShiftModifier))):
            if self._expand_overlay is not None:
                return super().eventFilter(obj, event)   # 预览期间不复制图层
            if self._text_selection_exists():
                return super().eventFilter(obj, event)
            self._on_copy_shortcut()
            return True
        return super().eventFilter(obj, event)

    def _wheel_anchor(self, event, obj) -> QPointF:
        """把滚轮位置换算为画布坐标；鼠标在画布外时以画布中心为锚点。"""
        if obj is self.canvas:
            return QPointF(event.position())
        try:
            gp = event.globalPosition().toPoint()
        except AttributeError:
            gp = QCursor.pos()
        p = self.canvas.mapFromGlobal(gp)
        if not self.canvas.rect().contains(p):
            return QPointF(self.canvas.width() / 2, self.canvas.height() / 2)
        return QPointF(p)

    def _handle_alt_wheel(self, event, obj) -> None:
        delta = event.angleDelta().y()
        if not delta:
            return
        self._auto_fit = False
        self.canvas.zoom_by(1 if delta > 0 else -1, self._wheel_anchor(event, obj))

    def _zoom_step(self, direction: int) -> None:
        """状态栏 +/- 按钮：不依赖修饰键的缩放兜底入口。"""
        self._auto_fit = False
        self.canvas.zoom_by(direction, QPointF(self.canvas.width() / 2,
                                               self.canvas.height() / 2))

    def _add_zoom_controls(self) -> None:
        box = QWidget()
        hl = QHBoxLayout(box)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)

        # V1.2 r3：拖动条放在 ＋/－ 左边，对数刻度 25%~400%，正中 100%
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(0, 200)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(160)
        self.zoom_slider.setFixedHeight(20)
        self.zoom_slider.setToolTip("拖动缩放：25%~400%，正中 100%（对数刻度）")
        self.zoom_slider.valueChanged.connect(self._on_slider_moved)
        hl.addWidget(self.zoom_slider)

        entries = (
            ("－", lambda: self._zoom_step(-1), 24, "缩小画布（每级 ÷1.15）"),
            ("＋", lambda: self._zoom_step(1), 24, "放大画布（每级 ×1.15）"),
            ("适应窗口", self.fit_and_enable, None, "恢复自动适应窗口"),
            ("100%", self.zoom_100, None, "按 100% 显示"),
        )
        for text, slot, width, tip in entries:
            b = QPushButton(text)
            b.setFixedHeight(20)
            if width:
                b.setFixedWidth(width)
            b.setToolTip(tip)
            b.setStyleSheet("padding:0 8px; font-size:11px;")
            b.clicked.connect(lambda checked=False, s=slot: s())
            hl.addWidget(b)
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setStyleSheet("color:#2F6FE4; font-weight:bold; padding:0 6px;")
        hl.addWidget(self.lbl_zoom)
        self.statusBar().addPermanentWidget(box)

    # -- 窗口入口 ----------------------------------------------------------
    def enter_from_main(self) -> None:
        """主界面点「编辑」：先显示选择状态。"""
        self.stack.setCurrentWidget(self.start_page)
        self.statusBar().showMessage("请选择打开图片/可编辑图层文件，或新建空白画布")

    def open_with_image(self, image: QImage) -> None:
        """截图确认后以截图为底图直接进入正式编辑界面。"""
        self.load_document(image, [], 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._doc_loaded and self._auto_fit:
            self.fit_to_view()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._doc_loaded and self._auto_fit and self.stack.currentWidget() is self.work_view:
            self.fit_to_view()

    def closeEvent(self, event) -> None:
        self.canvas.clear_interaction()
        super().closeEvent(event)
        if self._on_close:
            self._on_close()

    # -- 文档 --------------------------------------------------------------
    def load_document(self, base: QImage, layers: list[dict], next_id: int,
                      path: str = "") -> None:
        """装载底图与图层，切到正式编辑界面并重置撤销栈。"""
        if base.isNull():
            QMessageBox.warning(self, "打开失败", "底图无效，无法进入编辑。")
            return
        self.base = base.convertToFormat(QImage.Format_ARGB32)
        self.layers = layers
        self._project_path = path
        self._doc_loaded = True
        self.next_id = max(int(next_id), max((int(l["id"]) for l in layers), default=0) + 1)
        self.stack.setCurrentWidget(self.work_view)
        self.canvas.set_document()
        self.set_tool("select")
        self.reset_history()
        self.sync_properties()
        self.lbl_info.setText(f"{base.width()} × {base.height()} · {len(layers)} 个图层")
        self._auto_fit = True
        self.fit_to_view()
        self.statusBar().showMessage("已进入编辑工作台")

    def find_layer(self, layer_id: int) -> dict | None:
        for layer in self.layers:
            if layer["id"] == int(layer_id):
                return layer
        return None

    def make_layer(self, tool: str, rect=None, points=None, **overrides) -> dict:
        """按当前工具默认样式创建图层（尚未加入文档，可作草稿预览）。"""
        from .models import new_layer
        layer = new_layer(tool, self.next_id, rect, points)
        for key, value in self.tool_defaults.get(tool, {}).items():
            if key not in ("id", "type", "rect", "points"):
                layer[key] = copy.deepcopy(value)
        layer.update(overrides)
        if tool == "text":
            refresh_text_bounds(layer)
        return layer

    def add_layer(self, layer: dict) -> None:
        layer["id"] = self.next_id
        self.next_id += 1
        self.layers.append(layer)
        self.commit()
        self.canvas.refresh()
        self._update_info()
        self.statusBar().showMessage("已添加标注")

    # -- 撤销 / 重做 -------------------------------------------------------
    def reset_history(self) -> None:
        self._history = [clone_layers(self.layers)]
        self._hindex = 0
        self._sync_history_buttons()

    def commit(self) -> None:
        """一次操作完成后记录状态；执行新操作自动清空重做栈。"""
        if not self._doc_loaded:
            return
        state = clone_layers(self.layers)
        if self._hindex < len(self._history) - 1:
            self._history = self._history[:self._hindex + 1]
        if self._history and self._history[-1] == state:
            return                                   # 状态未变化，不产生无效撤销步
        self._history.append(state)
        if len(self._history) > MAX_UNDO:
            self._history = self._history[-MAX_UNDO:]
        self._hindex = len(self._history) - 1
        self._sync_history_buttons()

    def undo(self) -> None:
        if self._expand_overlay is not None:   # 预览期间锁定，避免 layers 与底图错位
            return
        if self._hindex <= 0:
            self.statusBar().showMessage("没有可撤销的操作")
            return
        self._hindex -= 1
        self.layers = clone_layers(self._history[self._hindex])
        self._after_history_restore("已撤销")

    def redo(self) -> None:
        if self._expand_overlay is not None:
            return
        if self._hindex >= len(self._history) - 1:
            self.statusBar().showMessage("没有可重做的操作")
            return
        self._hindex += 1
        self.layers = clone_layers(self._history[self._hindex])
        self._after_history_restore("已重做")

    def _after_history_restore(self, message: str) -> None:
        self.next_id = max(int(self.next_id),
                           max((int(l["id"]) for l in self.layers), default=0) + 1)
        if self.canvas.selected_id() and self.find_layer(self.canvas.selected_id()) is None:
            self.canvas.set_selected(0)
        self.canvas.refresh()
        self.sync_properties()
        self._sync_history_buttons()
        self._update_info()
        self.statusBar().showMessage(message)

    def _sync_history_buttons(self) -> None:
        if hasattr(self, "btn_undo"):
            self.btn_undo.setEnabled(self._doc_loaded and self._hindex > 0)
            self.btn_redo.setEnabled(self._doc_loaded and self._hindex < len(self._history) - 1)

    # -- 工具 / 选择 / 属性 -------------------------------------------------
    def set_tool(self, tool: str) -> None:
        if tool not in DRAW_TOOLS and tool != "select":
            return
        self.tool = tool
        if tool in DRAW_TOOLS:
            # 换绘制工具时取消旧选中，属性面板回到「工具默认样式」
            self.canvas.set_selected(0)
        self.canvas.set_tool(tool)
        if tool in self.tool_buttons:
            self.tool_buttons[tool].setChecked(True)
        self.sync_properties()

    def finish_drawing(self, layer_id: int) -> None:
        """V1.2：一次绘制成功后自动切回选择箭头，并选中刚画好的标注。"""
        self.set_tool("select")
        self.canvas.set_selected(layer_id)
        self.sync_properties()
        self.statusBar().showMessage("已添加标注，已自动切回选择箭头")

    def _on_copy_shortcut(self) -> None:
        """Ctrl+C 统一入口：复制当前选中的标注图层（不碰剪贴板）。

        顶部「复制」按钮仍走 copy_image_to_clipboard()，两者语义不同。
        """
        self.copy_selected(from_shortcut=True)

    def _on_selection_changed(self, _layer_id: int) -> None:
        self.sync_properties()

    def sync_properties(self) -> None:
        selected = self.find_layer(self.canvas.selected_id())
        if selected is not None:
            self.panel.set_context(selected, selected["type"], True)
        elif self.tool in DRAW_TOOLS:
            self.panel.set_context(self.tool_defaults[self.tool], self.tool, False)
        else:
            self.panel.set_context(None, None, False)

    def _on_style_live(self) -> None:
        selected = self.find_layer(self.canvas.selected_id())
        if selected is not None and selected["type"] == "text":
            refresh_text_bounds(selected)
        self.canvas.refresh()

    def _on_style_committed(self) -> None:
        self._on_style_live()
        self.commit()
        self.statusBar().showMessage("样式已更新（可 Ctrl+Z 撤销）")

    # -- 图层操作 ----------------------------------------------------------
    def delete_selected(self) -> None:
        layer = self.find_layer(self.canvas.selected_id())
        if layer is None:
            return
        self.layers.remove(layer)
        self.canvas.set_selected(0)
        self.commit()
        self.canvas.refresh()
        self.sync_properties()
        self._update_info()
        self.statusBar().showMessage("已删除标注")

    def copy_selected(self, from_shortcut: bool = False) -> None:
        layer = self.find_layer(self.canvas.selected_id())
        if layer is None:
            if from_shortcut:
                # 高频快捷键不弹模态框，只在状态栏提示
                self.statusBar().showMessage("请先选择一个标注图层，再用 Ctrl+C 复制")
            else:
                QMessageBox.information(self, "复制标注", "请先选择一个标注图层")
            return
        dup = clone_layer(layer, self.next_id)
        self.next_id += 1
        self.layers.append(dup)
        self.canvas.set_selected(dup["id"])
        self.commit()
        self.canvas.refresh()
        self.sync_properties()
        self._update_info()
        self.statusBar().showMessage("已复制为新的独立图层（偏移 18px）")

    def _move_layer(self, direction: int, title: str) -> None:
        layer = self.find_layer(self.canvas.selected_id())
        if layer is None:
            QMessageBox.information(self, title, "请先选择一个标注图层")
            return
        i = self.layers.index(layer)
        j = i + direction
        if j < 0 or j >= len(self.layers):
            self.statusBar().showMessage("图层已在最上/最下层")
            return
        self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
        self.commit()
        self.canvas.refresh()
        self.statusBar().showMessage(f"{title}完成（可 Ctrl+Z 撤销）")

    def layer_up(self) -> None:
        self._move_layer(1, "图层上移")

    def layer_down(self) -> None:
        self._move_layer(-1, "图层下移")

    # -- 文件操作 ----------------------------------------------------------
    def _require_document(self) -> bool:
        if self._doc_loaded:
            return True
        QMessageBox.information(self, "提示", "请先打开图片/可编辑图层文件或新建空白画布。")
        return False

    def copy_image_to_clipboard(self) -> None:
        """底图 + 全部图层拼合后放入系统剪贴板（不写磁盘、不弹保存框）。

        V1.2 修复：同时写入 Qt 私有格式与标准 image/png，保证外部程序 Ctrl+V
        也能拿到图；并给出按钮文字 / 气泡 / 状态栏三重可见反馈。
        """
        if not self._require_document():
            return
        image = to_rgb(render_for_export(self.base, self.layers))
        if image.isNull():
            QMessageBox.warning(self, "复制失败", "无法生成图片，复制未完成。")
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            QMessageBox.warning(self, "复制失败", "系统剪贴板当前不可用。")
            return
        try:
            buf = QBuffer()
            buf.open(QIODevice.WriteOnly)
            ok = image.save(buf, "PNG")
            buf.close()
            mime = QMimeData()
            mime.setImageData(image)
            if ok:
                mime.setData("image/png", buf.data())
            clipboard.setMimeData(mime)
        except (OSError, RuntimeError, TypeError) as exc:
            try:
                clipboard.setImage(image)           # 降级：只写 Qt 私有格式
            except Exception:                       # noqa: BLE001
                QMessageBox.warning(self, "复制失败", f"写入剪贴板失败：{exc}")
                return
        self.statusBar().showMessage(
            f"已复制为图片（{image.width()} × {image.height()}）并放入剪贴板，可直接 Ctrl+V 粘贴")
        try:
            QToolTip.showText(QCursor.pos(), "已复制图片到剪贴板，可直接 Ctrl+V 粘贴", self)
        except (RuntimeError, AttributeError):
            pass
        self.btn_copy.setText("已复制 ✓")
        self._copy_timer.start(1500)

    def save_image(self) -> None:
        if not self._require_document():
            return
        default = os.path.join(_default_dir(), timestamp_name("SnapAnno", ".jpg"))
        path, _ = QFileDialog.getSaveFileName(self, "保存为图片", default,
                                              "JPEG 图片 (*.jpg *.jpeg)")
        if not path:                                  # 用户取消，不报错
            return
        if not path.lower().endswith((".jpg", ".jpeg")):
            path += ".jpg"
        try:
            save_jpeg(path, render_for_export(self.base, self.layers))
        except ProjectError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"保存图片失败：{exc}")
            return
        self.statusBar().showMessage(f"图片已保存：{path}")
        QMessageBox.information(self, "保存成功", f"图片已保存：\n{path}")

    def save_project(self) -> None:
        if not self._require_document():
            return
        default = os.path.join(
            _default_dir(),
            timestamp_name("SnapAnno", PROJECT_EXT) if not self._project_path else self._project_path,
        )
        path, _ = QFileDialog.getSaveFileName(self, "保存为可编辑图层文件", default,
                                              f"SnapAnno 可编辑图层文件 (*{PROJECT_EXT})")
        if not path:
            return
        if not path.lower().endswith(PROJECT_EXT):
            path += PROJECT_EXT
        try:
            save_project(path, self.base, self.layers, self.next_id)
        except ProjectError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"保存可编辑图层文件失败：{exc}")
            return
        self._project_path = path
        self.statusBar().showMessage(f"可编辑图层文件已保存：{path}")
        QMessageBox.information(self, "保存成功", f"可编辑图层文件已保存：\n{path}")

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开图片或可编辑图层文件",
                                              _default_dir(), OPEN_FILTER)
        if not path:
            return
        try:
            base, layers, next_id = open_any(path)
        except ProjectError as exc:
            QMessageBox.warning(self, "打开失败", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(self, "打开失败", f"打开文件失败：{exc}")
            return
        self.load_document(base, layers, next_id,
                           path if path.lower().endswith(PROJECT_EXT) else "")
        self.statusBar().showMessage(f"已打开：{os.path.basename(path)}")

    def new_canvas(self) -> None:
        dlg = NewCanvasDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        color, width, height = dlg.values()
        image = QImage(width, height, QImage.Format_ARGB32)
        image.fill(QColor(color))
        if image.isNull():
            QMessageBox.warning(self, "新建失败", "无法创建指定大小的画布。")
            return
        self.load_document(image, [], 1)
        self.statusBar().showMessage(f"已新建空白画布 {width} × {height}")

    # -- 视图 --------------------------------------------------------------
    # -- 缩放拖动条（V1.2 r3）----------------------------------------------
    @staticmethod
    def _slider_to_zoom(value: int) -> float:
        """滑块 0..200 → 0.25..4.0（底数 4 的对数刻度），100 → 1.0。"""
        return math.pow(4.0, (value - 100) / 100.0)

    @staticmethod
    def _zoom_to_slider(zoom: float) -> int:
        """缩放值 → 滑块位置；超出 25%~400% 时钳到端点。"""
        z = min(4.0, max(0.25, float(zoom)))
        return round(100 + 100 * math.log(z, 4.0))

    def _on_slider_moved(self, value: int) -> None:
        """用户拖动滑块：只调 set_zoom，绝不回写滑块（避免回环）。"""
        self._auto_fit = False
        self.canvas.set_zoom(self._slider_to_zoom(value))

    def _on_zoom_changed(self, zoom: float) -> None:
        if hasattr(self, "lbl_zoom"):
            self.lbl_zoom.setText(f"{round(zoom * 100)}%")
        if hasattr(self, "zoom_slider"):
            v = self._zoom_to_slider(zoom)
            if self.zoom_slider.value() != v:
                # 回写时阻断信号，防止 set_zoom → setValue → valueChanged → set_zoom 死循环
                self.zoom_slider.blockSignals(True)
                self.zoom_slider.setValue(v)
                self.zoom_slider.blockSignals(False)
        overlay = getattr(self, "_expand_overlay", None)
        if overlay is not None:
            overlay.update()

    def _set_panel_width(self, hint: int) -> None:
        """内容需要更宽时自动加宽属性区；用户手动拖得更宽则保持不变。"""
        if not hasattr(self, "splitter"):
            return
        sizes = self.splitter.sizes()
        if len(sizes) == 2 and sizes[1] < hint:
            total = max(sum(sizes), hint + 300)
            self.splitter.setSizes([max(200, total - hint), hint])

    def fit_and_enable(self) -> None:
        self._auto_fit = True
        self.fit_to_view()

    def zoom_100(self) -> None:
        self._auto_fit = False
        self.canvas.set_zoom(1.0)

    def fit_to_view(self) -> None:
        if self.base.isNull():
            return
        vw = max(100, self.scroll.viewport().width() - 24)
        vh = max(100, self.scroll.viewport().height() - 24)
        zoom = min(vw / self.base.width(), vh / self.base.height(), 1.0)
        self.canvas.set_zoom(zoom)

    def _update_info(self) -> None:
        if self._doc_loaded:
            self.lbl_info.setText(
                f"{self.base.width()} × {self.base.height()} · {len(self.layers)} 个图层")

    # -- 扩大画布（V1.2 r3）------------------------------------------------
    def _on_scroll_for_overlay(self, _value: int) -> None:
        """滚动画面时让扩展框的控制点跟着走，避免错位。"""
        if self._expand_overlay is not None:
            self._expand_overlay.update()

    def enter_expand_mode(self) -> None:
        """进入预览模式：图片四边/四角显示可向外拖动的控制点。"""
        if not self._require_document():
            return
        if self._expand_overlay is not None:
            self.exit_expand_mode()
        self._expand_color = "#FFFFFF"
        vp = self.scroll.viewport()
        overlay = ExpandOverlay(self, vp)
        overlay.setGeometry(0, 0, vp.width(), vp.height())
        self._expand_overlay = overlay
        overlay.show()
        overlay.raise_()
        self.btn_expand.setEnabled(False)
        self.btn_expand_apply.setVisible(True)
        self.btn_expand_cancel.setVisible(True)
        self.panel.set_expand_mode(True, self._expand_color)
        self.statusBar().showMessage(
            "扩大画布：拖动四边/四角控制点向外扩展；点「应用扩展」生效，点「取消」放弃")

    def _on_expand_color_changed(self, color: str) -> None:
        """属性区改色只重绘预览，不动底图 / 图层 / 撤销栈。"""
        self._expand_color = color
        if self._expand_overlay is not None:
            self._expand_overlay.set_expand_color(color)

    def exit_expand_mode(self) -> None:
        overlay, self._expand_overlay = self._expand_overlay, None
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()          # 不调 setParent(None)，避免变成游离顶层窗口
        self.btn_expand.setEnabled(True)
        self.btn_expand_apply.setVisible(False)
        self.btn_expand_cancel.setVisible(False)
        self.panel.set_expand_mode(False)
        self.sync_properties()

    def cancel_expand(self) -> None:
        self.exit_expand_mode()
        self.statusBar().showMessage("已取消画布扩展，原图未改动")

    def apply_expand(self) -> None:
        """按预览包围盒生成新底图；扩展色烘焙进去，已有图层整体平移。

        该操作是文档级提交：不纳入撤销重做，应用后清空历史与工程路径
        （下次保存必须另选位置，不会覆盖原 .snproj）。
        """
        overlay = self._expand_overlay
        if overlay is None:
            return
        bounds = overlay.preview_bounds()
        L = min(0, int(round(bounds.left())))
        T = min(0, int(round(bounds.top())))
        R = max(self.base.width(), int(round(bounds.right())))
        B = max(self.base.height(), int(round(bounds.bottom())))
        if (R - L, B - T) == (self.base.width(), self.base.height()):
            self.statusBar().showMessage("尚未拖动扩展区域，请先向外拉动控制点")
            return

        new_base, dx, dy = expand_canvas(self.base, bounds, self._expand_color)
        if new_base.isNull():
            QMessageBox.warning(self, "扩大画布失败", "无法生成新画布。")
            return
        self.base = new_base                       # 不走 load_document，保留图层与文档状态
        for layer in self.layers:
            translate_layer(layer, dx, dy)         # 左/上扩展时保持标注与原图对齐

        self.canvas.refresh()                      # 失效渲染缓存（尺寸随 base 自动更新）
        self.canvas.set_zoom(self.canvas.zoom)     # 按新底图重算控件固定尺寸（不叫 set_document，保留选中态）
        self._update_info()
        self._auto_fit = True
        self.fit_to_view()
        self.reset_history()                       # 以平移后的图层为唯一新基线
        self._project_path = ""                    # 强制另存为，不覆盖原工程文件
        self.exit_expand_mode()
        self.statusBar().showMessage(
            f"画布已扩展为 {new_base.width()} × {new_base.height()}，"
            "扩展色已烘焙进底图（历史已清空，保存时需另选位置）")
