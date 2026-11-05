"""动态属性面板：按「当前工具」或「选中图层」显示可用的样式设置。

无选中图层时编辑的是工具默认样式（下一次绘制生效）；
选中图层时直接编辑该图层，并纳入撤销/重做。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QFontComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QVBoxLayout, QWidget,
)

from .dialogs import ColorButton

# 每个工具/图层类型可用的属性行
_MAIN_COLOR = {"rect", "circle", "ellipse", "arrow", "text", "pen"}
_WIDTH = {"rect", "circle", "ellipse", "arrow", "pen"}
_OUTLINE = {"rect", "circle", "ellipse", "arrow", "text", "pen"}
_FILL = {"rect_fill", "circle_fill", "ellipse_fill"}
_TEXT = {"text"}
_EFFECT = {"mosaic", "blur"}


class PropertiesPanel(QWidget):
    """右侧属性区。"""

    liveChanged = Signal()        # 实时刷新画面，不记入撤销栈
    committedStyle = Signal()     # 一次样式修改完成，需要 commit
    copyRequested = Signal()
    deleteRequested = Signal()
    widthHint = Signal(int)       # V1.2：按当前属性内容建议面板宽度
    expandColorChanged = Signal(str)   # V1.2 r3：扩大画布的扩展区域颜色

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._target: dict | None = None
        self._kind: str | None = None
        self._loading = False
        self._expand_mode = False     # V1.2 r3：扩大画布预览模式
        self.rows: dict[str, QWidget] = {}

        self.title = QLabel("属性 · 可拖动左侧分隔条调整宽度")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-weight:bold; color:#2F6FE4; padding:2px 0 6px 0;")
        self.hint = QLabel("选择左侧工具后，在此调整样式。\n用「选择箭头」点选标注可单独修改。")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#6B7280; padding:6px 2px;")

        inner = QWidget()
        self.form = QVBoxLayout(inner)
        self.form.setContentsMargins(2, 2, 6, 2)
        self.form.setSpacing(3)
        self._build_rows()
        self.form.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # V1.2：关掉横向滚动条（字体下拉被挤时改由面板加宽解决）
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(inner)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addWidget(scroll, 1)

        self._text_timer = QTimer(self)
        self._text_timer.setSingleShot(True)
        self._text_timer.setInterval(450)
        self._text_timer.timeout.connect(self.committedStyle.emit)

    # -- 构建 --------------------------------------------------------------
    def _row(self, key: str, label: str, widget: QWidget) -> QWidget:
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 1, 0, 1)
        lab = QLabel(label)
        lab.setFixedWidth(56)
        lab.setStyleSheet("color:#4A5568;")
        hl.addWidget(lab)
        hl.addWidget(widget, 1)
        self.rows[key] = row
        self.form.addWidget(row)
        return row

    def _build_rows(self) -> None:
        self.color_btn = ColorButton()
        self.color_btn.colorChanged.connect(lambda v: self._set("color", v))
        self._row("color", "颜色", self.color_btn)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 60)
        self.width_spin.setSuffix(" px")
        self.width_spin.valueChanged.connect(lambda v: self._set("width", v))
        self._row("width", "线宽/粗细", self.width_spin)

        self.outline_check = QCheckBox("加外轮廓")
        self.outline_check.stateChanged.connect(lambda s: self._set("outline", bool(s)))
        self._row("outline", "轮廓", self.outline_check)

        self.outline_width_spin = QSpinBox()
        self.outline_width_spin.setRange(1, 30)
        self.outline_width_spin.valueChanged.connect(lambda v: self._set("outline_width", v))
        self._row("outline_width", "轮廓大小", self.outline_width_spin)

        self.outline_color_btn = ColorButton("#FFFFFF")
        self.outline_color_btn.colorChanged.connect(lambda v: self._set("outline_color", v))
        self._row("outline_color", "轮廓颜色", self.outline_color_btn)

        self.fill_color_btn = ColorButton("#FFC107")
        self.fill_color_btn.colorChanged.connect(lambda v: self._set("fill_color", v))
        self._row("fill_color", "填充颜色", self.fill_color_btn)

        alpha_row = QWidget()
        ah = QHBoxLayout(alpha_row)
        ah.setContentsMargins(0, 1, 0, 1)
        self.alpha_slider = QSlider(Qt.Horizontal)
        self.alpha_slider.setRange(0, 100)
        self.alpha_label = QLabel("45%")
        self.alpha_label.setFixedWidth(36)
        self.alpha_slider.valueChanged.connect(self._on_alpha)
        alpha_lab = QLabel("透明度")
        alpha_lab.setFixedWidth(56)
        ah.addWidget(alpha_lab)
        ah.addWidget(self.alpha_slider, 1)
        ah.addWidget(self.alpha_label)
        self.rows["fill_alpha"] = alpha_row
        self.form.addWidget(alpha_row)

        self.brush_spin = QSpinBox()
        self.brush_spin.setRange(4, 400)
        self.brush_spin.setSuffix(" px")
        self.brush_spin.valueChanged.connect(lambda v: self._set("brush", v))
        self._row("brush", "笔刷粗细", self.brush_spin)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setFixedHeight(62)
        self.text_edit.setPlaceholderText("输入标注文字")
        self.text_edit.textChanged.connect(self._on_text)
        self._row("text", "文字内容", self.text_edit)

        self.font_combo = QFontComboBox()
        self.font_combo.setMinimumWidth(160)
        self.font_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # 下拉列表独立加宽，字体名不再被面板裁切
        self.font_combo.setStyleSheet(
            "QComboBox QAbstractItemView { min-width: 260px; }")
        self.font_combo.currentFontChanged.connect(
            lambda f: self._set("font_family", f.family()))
        self._row("font_family", "字体", self.font_combo)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 300)
        self.size_spin.valueChanged.connect(lambda v: self._set("font_size", v))
        self._row("font_size", "字号", self.size_spin)

        self.bold_check = QCheckBox("加粗")
        self.bold_check.stateChanged.connect(lambda s: self._set("bold", bool(s)))
        self.underline_check = QCheckBox("下划线")
        self.underline_check.stateChanged.connect(lambda s: self._set("underline", bool(s)))
        style_row = QWidget()
        sh = QHBoxLayout(style_row)
        sh.setContentsMargins(0, 1, 0, 1)
        lab = QLabel("文字样式")
        lab.setFixedWidth(56)
        sh.addWidget(lab)
        sh.addWidget(self.bold_check)
        sh.addWidget(self.underline_check)
        sh.addStretch(1)
        self.rows["text_style"] = style_row
        self.form.addWidget(style_row)

        self.copy_btn = QPushButton("复制标注")
        self.delete_btn = QPushButton("删除标注")
        self.copy_btn.clicked.connect(lambda checked=False, s=self.copyRequested: s.emit())
        self.delete_btn.clicked.connect(lambda checked=False, s=self.deleteRequested: s.emit())
        action_row = QWidget()
        ah2 = QHBoxLayout(action_row)
        ah2.setContentsMargins(0, 6, 0, 2)
        ah2.addWidget(self.copy_btn)
        ah2.addWidget(self.delete_btn)
        self.rows["actions"] = action_row
        self.form.addWidget(action_row)

        # V1.2 r3：扩展区域颜色是文档级属性（不属于图层 dict），不走 _set()
        self.expand_color_btn = ColorButton("#FFFFFF")
        self.expand_color_btn.colorChanged.connect(
            lambda v: self.expandColorChanged.emit(v))
        self._row("expand_color", "扩展区域颜色", self.expand_color_btn)
        self.rows["expand_color"].setVisible(False)

    # -- 内部 --------------------------------------------------------------
    def _set(self, key: str, value, commit: bool = True) -> None:
        if self._loading or self._target is None:
            return
        self._target[key] = value
        self.liveChanged.emit()
        if commit:
            self.committedStyle.emit()
        # 轮廓联动：关闭时隐藏轮廓大小/颜色
        if key == "outline":
            self._apply_visibility()

    def _on_alpha(self, value: int) -> None:
        if self._loading:
            self.alpha_label.setText(f"{value}%")
            return
        self.alpha_label.setText(f"{value}%")
        self._set("fill_alpha", value, commit=False)
        self._text_timer.start()          # 与文字输入共用防抖提交，避免刷屏产生大量撤销步

    def _on_text(self) -> None:
        if self._loading:
            return
        value = self.text_edit.toPlainText()
        self._set("text", value, commit=False)
        self._text_timer.start()

    def _apply_visibility(self) -> None:
        show_outline_detail = bool(self._target and self._target.get("outline"))
        for key in ("outline_width", "outline_color"):
            if key in self.rows and (self._kind in _OUTLINE):
                self.rows[key].setVisible(show_outline_detail)

    def set_expand_mode(self, enabled: bool, color: str = "#FFFFFF") -> None:
        """扩大画布预览模式：属性区只显示扩展区域颜色，不被工具/选中切换覆盖。"""
        self._expand_mode = bool(enabled)
        if self._expand_mode:
            for row in self.rows.values():
                row.setVisible(False)
            self.rows["expand_color"].setVisible(True)
            self._loading = True
            self.expand_color_btn.setColor(color)   # setColor 不发信号，不会误写回
            self._loading = False
            self.title.setText("画布扩展")
            self.hint.setText("颜色仅作用于新增区域；点「应用扩展」后烘焙进底图，之后不可再改。")
            self.hint.setVisible(True)
            self.widthHint.emit(280)
        else:
            self.rows["expand_color"].setVisible(False)

    def set_context(self, target: dict | None, kind: str | None, has_selection: bool) -> None:
        """target 为选中图层或工具默认样式；kind 为图层类型/工具标识。"""
        if self._expand_mode:
            return
        self._loading = True
        self._text_timer.stop()
        self._target = target
        self._kind = kind
        try:
            if target is None or kind is None:
                for row in self.rows.values():
                    row.setVisible(False)
                self.title.setText("属性")
                self.hint.setText("用「选择箭头」点选画布上的标注，即可修改它的样式。")
                self.hint.setVisible(True)
                return

            show = {
                "color": kind in _MAIN_COLOR,
                "width": kind in _WIDTH,
                "outline": kind in _OUTLINE,
                "outline_width": kind in _OUTLINE and bool(target.get("outline")),
                "outline_color": kind in _OUTLINE and bool(target.get("outline")),
                "fill_color": kind in _FILL,
                "fill_alpha": kind in _FILL,
                "brush": kind in _EFFECT,
                "text": kind in _TEXT,
                "font_family": kind in _TEXT,
                "font_size": kind in _TEXT,
                "text_style": kind in _TEXT,
                "actions": has_selection,
                "expand_color": False,   # 只在扩大画布预览模式显示
            }
            for key, visible in show.items():
                self.rows[key].setVisible(visible)

            self.color_btn.setColor(target.get("color", "#E53935"))
            self.width_spin.setValue(int(target.get("width", 3)))
            self.outline_check.setChecked(bool(target.get("outline", False)))
            self.outline_width_spin.setValue(int(target.get("outline_width", 2)))
            self.outline_color_btn.setColor(target.get("outline_color", "#FFFFFF"))
            self.fill_color_btn.setColor(target.get("fill_color", "#FFC107"))
            alpha = int(target.get("fill_alpha", 45))
            self.alpha_slider.setValue(alpha)
            self.alpha_label.setText(f"{alpha}%")
            self.brush_spin.setValue(int(target.get("brush", 30)))
            self.text_edit.setPlainText(target.get("text", ""))
            font = QFont(target.get("font_family", "Microsoft YaHei"))
            self.font_combo.setCurrentFont(font)
            self.size_spin.setValue(int(target.get("font_size", 24)))
            self.bold_check.setChecked(bool(target.get("bold", False)))
            self.underline_check.setChecked(bool(target.get("underline", False)))

            self.title.setText("选中标注属性" if has_selection else "工具默认样式")
            self.hint.setText("修改后立即作用于选中标注。" if has_selection
                              else "此处设置将在下一次绘制时生效。")
            self.hint.setVisible(True)
        finally:
            self._loading = False
            # 文字属性控件更多，自动把面板加宽到能完整显示字体下拉；其它情况给常规宽度
            self.widthHint.emit(330 if kind in _TEXT else 280)
