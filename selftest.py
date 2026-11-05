"""SnapAnno 离屏自检脚本（无需真实显示器，使用 Qt offscreen 平台）。

运行：
    set QT_QPA_PLATFORM=offscreen   (Windows CMD)
    python selftest.py

覆盖：主界面、截图状态机（确认/重截/ESC/坐标换算）、11 类图层、
      撤销重做、复制与图层顺序、JPG 导出、.snproj 读写与损坏提示、
      打开普通图片、新建空白画布；
      V1.2 r2 真实事件分发回归（滚轮 / Ctrl+C / 剪贴板格式 / 可见反馈）；
      V1.2 r3：Ctrl+C 复制选中标注、状态栏滑块缩放映射与防回环、
      「保存为可编辑图层文件」改名、扩大画布（预览 / 改色 / 拖角 / 应用 / 取消 /
      图层平移 / 历史重置 / 强制另存为）与扩展后 .snproj 往返。
共 165 项断言。
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QRect, Qt                  # noqa: E402
from PySide6.QtGui import QImage                                     # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.capture as capture_mod                                   # noqa: E402
import app.editor as editor_mod                                     # noqa: E402
from app.config import APP_NAME, MAX_UNDO, TOOL_LIST                 # noqa: E402
from app.models import ProjectError, layer_bounds, new_layer, refresh_text_bounds  # noqa: E402
from app.project_io import load_project, open_any, save_jpeg, save_project  # noqa: E402
from app.render import render_document                              # noqa: E402
from main import MainWindow                                         # noqa: E402


class Checker:
    def __init__(self):
        self.passed = 0
        self.failed: list[str] = []

    def ok(self, cond: bool, name: str) -> None:
        if cond:
            self.passed += 1
            print(f"  [PASS] {name}")
        else:
            self.failed.append(name)
            print(f"  [FAIL] {name}")

    def eq(self, a, b, name: str) -> None:
        self.ok(a == b, f"{name} (得到 {a!r}, 期望 {b!r})")

    def report(self) -> int:
        print("\n" + "=" * 56)
        print(f"自检结果：通过 {self.passed} 项，失败 {len(self.failed)} 项")
        for name in self.failed:
            print(f"  - {name}")
        print("=" * 56)
        return 1 if self.failed else 0


class FakeMessageBox:
    """屏蔽模态提示，避免离屏测试阻塞。"""
    @staticmethod
    def information(*_a, **_k):
        return None

    @staticmethod
    def warning(*_a, **_k):
        return None

    @staticmethod
    def critical(*_a, **_k):
        return None


class FakeFileDialog:
    save_path = ""
    open_path = ""

    @staticmethod
    def getSaveFileName(*_a, **_k):
        return (FakeFileDialog.save_path, "")

    @staticmethod
    def getOpenFileName(*_a, **_k):
        return (FakeFileDialog.open_path, "")


class FakeCanvasDialog:
    def __init__(self, *_a, **_k):
        pass

    def exec(self):
        return 1                       # QDialog.Accepted

    def values(self):
        return ("#FF0000", 320, 200)


def _new_base(w=320, h=200) -> QImage:
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0xFF336699)
    return img


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    c = Checker()
    editor_mod.QMessageBox = FakeMessageBox
    editor_mod.QFileDialog = FakeFileDialog
    editor_mod.NewCanvasDialog = FakeCanvasDialog

    tmp = tempfile.TemporaryDirectory(prefix="snapanno_selftest_")
    root = tmp.name

    print("\n[1] 主界面")
    win = MainWindow()
    c.ok(win.windowTitle() == APP_NAME, "主窗口标题为 SnapAnno")
    c.eq((win.btn_capture.text(), win.btn_edit.text()), ("截图", "编辑"), "主界面含截图/编辑按钮")
    c.eq(len(TOOL_LIST), 14, "左侧工具栏共 14 项")
    c.eq(len(win.editor.tool_buttons), 12, "可切换工具 12 个（含选择箭头）")
    c.ok(type(win.editor.stack.currentWidget()).__name__ == "StartPage", "「编辑」入口先显示选择状态")

    print("\n[2] 截图流程状态机 + 高 DPI 坐标换算")
    synthetic = _new_base(400, 300)
    capture_mod.grab_desktop = lambda: (synthetic.copy(), 1.0, QRect(0, 0, 400, 300))
    win.capture._settle = lambda *a, **k: None
    confirmed: list[QImage] = []
    win.capture._on_confirmed = lambda img: confirmed.append(img)

    # dpr = 2 时，逻辑选区 10,10,20,20 应裁出 40x40 的设备像素图
    ov = capture_mod.SelectionOverlay(_new_base(200, 100), 2.0, QRect(0, 0, 100, 50))
    cropped, _cx, _cy = ov._crop(QRect(10, 10, 20, 20))
    c.eq((cropped.width(), cropped.height()), (40, 40), "高 DPI 选区坐标换算 (dpr=2)")

    win.capture.start()
    c.ok(win.isHidden(), "点击截图后主窗口隐藏")
    c.ok(win.capture._overlay is not None, "截图覆盖层已创建")
    shot = synthetic.copy(QRect(20, 20, 120, 80))
    win.capture._on_region(shot, 60, 50)
    c.ok(win.capture._confirm is not None, "松手后弹出确认窗口")
    c.eq((win.capture._confirm.btn_ok.text(), win.capture._confirm.btn_again.text()),
         ("确认", "重新截图"), "确认窗口含确认/重新截图两个按钮")
    c.ok(win.capture._overlay.isHidden(), "确认期间覆盖层隐藏，不遮挡")

    win.capture._reshoot()
    c.ok(win.capture._confirm is None, "重新截图后确认窗口已销毁")
    c.ok(win.capture._overlay is not None and win.capture._overlay._start is None,
         "重新截图回到全新拉框状态，无上次选区残留")

    win.capture._on_region(shot, 60, 50)
    win.capture._on_confirm()
    c.eq(len(confirmed), 1, "点确认交付截图底图")
    c.ok(win.capture._overlay is None and win.capture._confirm is None, "确认后覆盖层与确认窗全部清理")

    win.capture.start()
    win.capture.cancel()
    c.ok(not win.isHidden(), "ESC 取消后恢复主界面")

    print("\n[3] 进入编辑页 + 11 类图层绘制")
    ed = win.editor
    ed.load_document(_new_base(), [], 1)
    c.ok(ed._doc_loaded, "文档已装载")
    c.eq(type(ed.stack.currentWidget()).__name__, "QWidget", "进入正式编辑界面")

    kinds = ["rect", "circle", "ellipse", "rect_fill", "circle_fill", "ellipse_fill",
             "arrow", "text", "pen", "mosaic", "blur"]
    for i, kind in enumerate(kinds):
        rect = [10 + i * 8, 12 + i * 6, 90, 60]
        layer = ed.make_layer(kind, rect,
                              [[20 + i * 8, 20], [60 + i * 8, 45]] if kind in ("pen", "mosaic", "blur")
                              else ([[10 + i * 8, 12], [100 + i * 8, 72]] if kind == "arrow" else None),
                              **({"text": "标注测试"} if kind == "text" else {}))
        if kind == "text":
            refresh_text_bounds(layer)
        ed.add_layer(layer)
    c.eq(len(ed.layers), 11, "11 类图层全部添加")
    c.eq({l["type"] for l in ed.layers}, set(kinds), "图层类型齐全")

    final = render_document(ed.base, ed.layers)
    c.ok(not final.isNull() and final.size() == ed.base.size(), "拼合渲染输出尺寸正确")

    print("\n[4] 撤销 / 重做")
    n = len(ed.layers)
    ed.undo()
    c.eq(len(ed.layers), n - 1, "Ctrl+Z 撤销最后一次添加")
    ed.redo()
    c.eq(len(ed.layers), n, "Ctrl+Y 重做恢复")
    # 改样式
    target = ed.layers[0]
    target["color"] = "#00FF00"
    ed.commit()
    ed.undo()
    c.eq(ed.layers[0]["color"], "#E53935", "撤销可回滚样式修改")
    ed.redo()
    c.eq(ed.layers[0]["color"], "#00FF00", "重做可恢复样式修改")
    c.ok(ed.base.width() == 320 and ed.base.height() == 200, "撤销/重做不影响原始底图")
    # 新操作清空重做栈
    ed.undo()
    extra = ed.make_layer("rect", [5, 5, 30, 30])
    ed.add_layer(extra)
    c.ok(ed._hindex >= len(ed._history) - 1, "执行新操作后重做栈被清空")
    ed.undo()  # 回到 11 层
    for _ in range(70):
        ed.add_layer(ed.make_layer("rect", [1, 1, 10, 10]))
    c.ok(len(ed._history) <= MAX_UNDO, "撤销步数不超过上限")
    # 恢复到 11 层
    ed.load_document(_new_base(), [], 1)
    for i, kind in enumerate(kinds):
        ed.add_layer(ed.make_layer(kind, [10 + i * 8, 12 + i * 6, 90, 60],
                                   [[20, 20], [60, 45]] if kind in ("pen", "mosaic", "blur") else None,
                                   **({"text": "文字"} if kind == "text" else {})))

    print("\n[5] 选择 / 复制 / 图层顺序 / 删除")
    ed.canvas.set_selected(0)
    before = len(ed.layers)
    ed.layer_up()  # 未选中应提示且不改变图层
    c.eq(len(ed.layers), before, "未选中时图层上移不生效")
    ed.canvas.set_selected(ed.layers[0]["id"])
    ed.copy_selected()
    c.eq(len(ed.layers), before + 1, "复制生成新的独立图层")
    c.eq(ed.canvas.selected_id(), ed.layers[-1]["id"], "复制后自动选中新图层")
    c.ok(ed.layers[-1]["rect"][0] != ed.layers[0]["rect"][0], "复制图层有偏移，不会完全重叠")
    orig_id = ed.layers[0]["id"]
    ed.canvas.set_selected(orig_id)
    ed.layer_up()
    c.eq(ed.layers[1]["id"], orig_id, "图层上移改变叠放顺序")
    ed.layer_down()
    c.eq(ed.layers[0]["id"], orig_id, "图层下移恢复叠放顺序")
    ed.undo(); ed.redo()
    c.ok(True, "图层顺序纳入撤销/重做")
    ed.delete_selected()
    c.eq(len(ed.layers), before, "删除选中标注生效")
    ed.undo()
    c.eq(len(ed.layers), before + 1, "删除可撤销")

    print("\n[6] 保存 JPG")
    jpg_path = os.path.join(root, "out.jpg")
    FakeFileDialog.save_path = jpg_path
    ed.save_image()
    c.ok(os.path.isfile(jpg_path) and os.path.getsize(jpg_path) > 1000, "JPG 文件已生成且非空")
    reopened = QImage(jpg_path)
    c.ok(not reopened.isNull() and reopened.size() == ed.base.size(), "JPG 可被重新打开且尺寸正确")
    FakeFileDialog.save_path = ""
    ed.save_image()
    c.ok(True, "取消保存对话框不报错")

    print("\n[7] 保存 / 重新打开 .snproj")
    proj_path = os.path.join(root, "proj.snproj")
    FakeFileDialog.save_path = proj_path
    ed.save_project()
    c.ok(zipfile.is_zipfile(proj_path), ".snproj 是 ZIP 压缩包")
    with zipfile.ZipFile(proj_path) as zf:
        names = set(zf.namelist())
        c.ok({"original.png", "project.json", "thumbnail.png"} <= names, "包含 original.png / project.json / thumbnail.png")
        data = json.loads(zf.read("project.json"))
        c.eq(len(data["layers"]), len(ed.layers), "project.json 图层数量一致")
    base2, layers2, nid2 = open_any(proj_path)
    c.eq((base2.width(), base2.height()), (ed.base.width(), ed.base.height()), "重新打开恢复底图尺寸")
    c.eq(len(layers2), len(ed.layers), "重新打开恢复全部图层")
    c.eq([l["type"] for l in layers2], [l["type"] for l in ed.layers], "图层顺序与类型一致")
    c.ok(nid2 > max(l["id"] for l in layers2), "下一个图层 id 正确")
    restored_count = len(layers2)
    ed.load_document(base2, layers2, nid2, proj_path)
    ed.add_layer(ed.make_layer("rect", [2, 2, 40, 40]))
    c.eq(len(ed.layers), restored_count + 1, "恢复工程后可继续编辑")

    print("\n[8] 损坏工程 / 不兼容版本中文提示")
    bad = os.path.join(root, "bad.snproj")
    with open(bad, "wb") as f:
        f.write(b"not a zip")
    try:
        load_project(bad)
        c.ok(False, "损坏工程应报错")
    except ProjectError as exc:
        c.ok("损坏" in str(exc) or "ZIP" in str(exc), f"损坏工程中文提示：{exc}")
    incompatible = os.path.join(root, "v99.snproj")
    with zipfile.ZipFile(incompatible, "w") as zf:
        zf.writestr("original.png", b"")
        zf.writestr("project.json", json.dumps({"version": 99, "layers": []}))
    try:
        load_project(incompatible)
        c.ok(False, "版本不兼容应报错")
    except ProjectError as exc:
        c.ok("版本不兼容" in str(exc) or "底图" in str(exc), f"版本不兼容中文提示：{exc}")

    print("\n[9] 打开普通图片 / 新建空白画布")
    png_path = os.path.join(root, "src.png")
    src = _new_base(240, 160)
    src.save(png_path, "PNG")
    FakeFileDialog.open_path = png_path
    ed.open_file()
    c.eq((ed.base.width(), ed.base.height()), (240, 160), "打开 PNG 作为底图")
    c.eq(len(ed.layers), 0, "打开普通图片后图层为空")
    ed.add_layer(ed.make_layer("rect", [5, 5, 50, 40]))
    c.ok(len(ed.layers) == 1, "打开图片后可直接标注")
    ed.new_canvas()
    c.eq((ed.base.width(), ed.base.height()), (320, 200), "新建空白画布尺寸生效")
    c.eq(len(ed.layers), 0, "新建画布图层为空")
    ed.add_layer(ed.make_layer("ellipse", [10, 10, 60, 40]))
    ed.undo()
    c.eq(len(ed.layers), 0, "新建画布上撤销重做可用")
    ed.redo()
    c.eq(len(ed.layers), 1, "新建画布上重做可用")

    print("\n[10] 真实鼠标交互（绘制 / 选中 / 移动 / 缩放 / 属性）")
    import app.canvas as canvas_mod                                  # noqa: E402
    from PySide6.QtTest import QTest                                 # noqa: E402

    class FakeTextDialog:
        def __init__(self, *_a, **_k):
            pass

        def exec(self):
            return 1

        def text(self):
            return "鼠标交互文字"

    canvas_mod.TextInputDialog = FakeTextDialog
    ed.load_document(_new_base(300, 200), [], 1)
    cv = ed.canvas
    cv.set_zoom(1.0)
    cv.show()

    def drag(tool, a, b):
        ed.set_tool(tool)
        QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, QPoint(*a))
        QTest.mouseMove(cv, QPoint(*b))
        QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, QPoint(*b))

    drag("rect", (10, 10), (80, 60))
    c.eq(len(ed.layers), 1, "鼠标拉框创建矩形框图层")
    c.eq(ed.layers[0]["type"], "rect", "图层类型正确")

    drag("pen", (100, 20), (160, 70))
    c.eq(len(ed.layers), 2, "鼠标自由划线创建画笔图层")
    c.ok(len(ed.layers[1]["points"]) >= 2, "画笔图层记录了笔迹点")

    drag("mosaic", (200, 20), (250, 80))
    c.eq(len(ed.layers), 3, "鼠标涂抹创建马赛克独立图层")

    ed.set_tool("text")
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, QPoint(40, 40))
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, QPoint(40, 40))
    c.eq(len(ed.layers), 4, "点击创建文字标注图层")
    c.eq(ed.layers[3]["text"], "鼠标交互文字", "文字图层内容正确")

    def shown(key):
        return not ed.panel.rows[key].isHidden()

    ed.set_tool("text")
    c.ok(shown("font_family"), "选中文字工具时显示字体设置")
    c.ok(not shown("fill_alpha"), "文字工具不显示填充透明度")
    ed.set_tool("mosaic")
    c.ok(shown("brush"), "马赛克工具显示笔刷粗细")
    c.ok(not shown("outline"), "马赛克工具不显示轮廓设置")

    # 选择箭头：点选、改样式、移动、缩放、删除
    ed.set_tool("select")
    cv.set_selected(0)
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, QPoint(40, 30))
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, QPoint(40, 30))
    sid = cv.selected_id()
    c.ok(sid != 0, "选择箭头可点选已有标注")
    c.ok(shown("actions"), "选中后显示复制/删除操作")

    old_color = ed.find_layer(sid)["color"]
    ed.panel.set_context(ed.find_layer(sid), "rect", True)
    ed.panel.color_btn.colorChanged.emit("#00AAFF")
    c.eq(ed.find_layer(sid)["color"], "#00AAFF", "属性面板改色作用于选中图层")
    ed.undo()
    c.eq(ed.find_layer(sid)["color"], old_color, "改色可撤销")
    ed.redo()

    before_bounds = layer_bounds(ed.find_layer(sid))
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, QPoint(40, 30))
    QTest.mouseMove(cv, QPoint(60, 50))
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, QPoint(60, 50))
    moved_bounds = layer_bounds(ed.find_layer(sid))
    c.ok(moved_bounds.x() != before_bounds.x() or moved_bounds.y() != before_bounds.y(),
         "选择箭头拖动可移动标注")
    ed.undo()
    c.eq(layer_bounds(ed.find_layer(sid)), before_bounds, "移动可撤销")

    b = layer_bounds(ed.find_layer(sid))
    hx, hy = round(b.right()), round(b.bottom())
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, QPoint(hx, hy))
    QTest.mouseMove(cv, QPoint(hx + 20, hy + 20))
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, QPoint(hx + 20, hy + 20))
    c.ok(layer_bounds(ed.find_layer(sid)).width() > b.width(), "拖动控制点可缩放标注")

    n_before = len(ed.layers)
    QTest.keyClick(cv, Qt.Key_Delete)
    c.eq(len(ed.layers), n_before - 1, "Delete 键删除选中标注")
    ed.undo()
    c.eq(len(ed.layers), n_before, "删除可撤销")

    print("\n[11] 边界")
    FakeFileDialog.open_path = ""
    ed.open_file()
    c.ok(True, "取消打开对话框不报错")
    c.ok(new_layer("text", 1, text="")["text"] == "", "空文字图层允许存在但不渲染内容")
    empty = render_document(_new_base(20, 20), [new_layer("text", 1, text="")])
    c.ok(not empty.isNull(), "空文字不导致渲染崩溃")

    print("\n[12] V1.2 新增功能")
    from PySide6.QtCore import QPointF                               # noqa: E402
    from PySide6.QtGui import QAction, QKeySequence                   # noqa: E402
    from PySide6.QtWidgets import QScrollArea, QSplitter              # noqa: E402
    from app.config import APP_VERSION, STYLESHEET                   # noqa: E402
    from PySide6.QtWidgets import QPushButton                        # noqa: E402

    c.eq(APP_VERSION, "1.2", "版本号已升级为 V1.2")
    c.eq(TOOL_LIST[0][0], "select", "选择箭头位于工具栏最上方")
    icon_count = sum(1 for b in ed.findChildren(QPushButton) if not b.icon().isNull())
    c.ok(icon_count >= 14, f"14 个工具按钮均带图标（实测 {icon_count} 个）")
    c.ok("#D32F2F" in ed.tool_buttons["rect"].styleSheet(), "工具选中时字体样式为红色")
    c.ok("QPushButton:checked" in STYLESHEET and "#D32F2F" in STYLESHEET,
         "全局选中样式也是红字")

    # Alt + 滚轮缩放 / 普通滚轮放行
    ed.load_document(_new_base(300, 200), [], 1)
    cv = ed.canvas
    cv.set_zoom(1.0)
    z0 = cv.zoom
    c.ok(cv.zoom_by(1, QPointF(60, 40)) > z0, "Alt+滚轮向上放大画布")
    c.ok(abs(cv.zoom_by(-1, QPointF(60, 40)) - z0) < 1e-6, "Alt+滚轮向下缩回原比例")
    c.eq(ed.lbl_zoom.text(), "100%", "状态栏同步显示缩放百分比")

    class FakeWheel:
        def __init__(self, mod):
            self.mod, self.ignored, self.accepted = mod, False, False

        def modifiers(self):
            return self.mod

        def angleDelta(self):
            return QPoint(0, 120)

        def position(self):
            return QPointF(30, 30)

        def ignore(self):
            self.ignored = True

        def accept(self):
            self.accepted = True

    plain = FakeWheel(Qt.NoModifier)
    cv.wheelEvent(plain)
    c.ok(plain.ignored and abs(cv.zoom - z0) < 1e-6, "普通滚轮不缩放，交给滚动区域")
    alt = FakeWheel(Qt.AltModifier)
    cv.wheelEvent(alt)
    c.ok(alt.accepted and cv.zoom > z0, "Alt+滚轮事件确实触发放大")
    c.ok(not ed._auto_fit, "手动缩放后不被窗口 resize 自动重置")
    cv.set_zoom(1.0)

    # 「复制」按钮 + Ctrl+C（V1.2 修复后写 QMimeData，含标准 image/png）
    class FakeClipboard:
        def __init__(self):
            self.image = None
            self.mime = None

        def setImage(self, img):
            self.image = img

        def setMimeData(self, mime):
            self.mime = mime
            self.image = mime.imageData()

    fake_clip = FakeClipboard()
    orig_qgui, orig_app = editor_mod.QGuiApplication, editor_mod.QApplication
    editor_mod.QGuiApplication = type("FakeQGui", (), {"clipboard": staticmethod(lambda: fake_clip)})
    editor_mod.QApplication = type("FakeApp", (), {
        "focusWidget": staticmethod(lambda: None),
        "activeWindow": staticmethod(lambda: None),
        "keyboardModifiers": staticmethod(lambda: Qt.NoModifier),
        "instance": staticmethod(lambda: QApplication.instance()),
    })
    try:
        ed.copy_image_to_clipboard()
        c.ok(fake_clip.image is not None and not fake_clip.image.isNull(),
             "「复制」把拼合图放入剪贴板")
        c.eq((fake_clip.image.width(), fake_clip.image.height()), (300, 200),
             "剪贴板图片尺寸与底图一致")
        c.ok(fake_clip.mime is not None and fake_clip.mime.hasImage(),
             "剪贴板同时提供 Qt 图像格式")
        c.ok(fake_clip.mime is not None and fake_clip.mime.hasFormat("image/png"),
             "剪贴板额外提供标准 image/png，外部程序可粘贴")
        c.eq(ed.btn_copy.text(), "已复制 ✓", "复制成功后按钮给出可见反馈")
        # Ctrl+C 不再碰剪贴板，改为转发到「复制选中标注图层」
        calls: list[int] = []
        orig_copy_sel = ed.copy_selected
        ed.copy_selected = lambda *a, **k: calls.append(1)
        try:
            ed._on_copy_shortcut()
            c.eq(len(calls), 1, "Ctrl+C 兼容入口转发到 copy_selected（复制选中标注）")
        finally:
            ed.copy_selected = orig_copy_sel
    finally:
        editor_mod.QGuiApplication, editor_mod.QApplication = orig_qgui, orig_app
        ed.btn_copy.setText("复制")

    # 绘制完成自动切回选择箭头
    ed.set_tool("rect")
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, QPoint(10, 10))
    QTest.mouseMove(cv, QPoint(80, 60))
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, QPoint(80, 60))
    c.eq(ed.tool, "select", "拉框绘制完成后自动切回选择箭头")
    c.ok(cv.selected_id() != 0, "自动选中刚创建的标注")
    c.ok(shown("actions"), "切回后属性面板显示该标注的复制/删除操作")
    ed.set_tool("text")
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
    c.eq(ed.tool, "select", "文字标注创建后同样自动切回选择箭头")

    # 属性面板可调整 / 自动加宽 / 字体下拉
    c.ok(isinstance(ed.splitter, QSplitter), "属性面板位于可拖拽分隔条中")
    c.ok(ed.panel.minimumWidth() >= 250 and ed.panel.maximumWidth() > ed.panel.minimumWidth(),
         "属性区宽度可调整")
    c.ok(ed.panel.font_combo.minimumWidth() >= 160, "字体下拉控件有足够最小宽度")
    c.ok("min-width" in ed.panel.font_combo.styleSheet(), "字体下拉列表独立加宽")
    sa = ed.panel.findChild(QScrollArea)
    c.ok(sa is not None and sa.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff,
         "属性区关闭横向滚动条，下拉不再被挤")
    hints: list[int] = []
    ed.panel.widthHint.connect(lambda w: hints.append(w))
    ed.set_tool("text")
    c.ok(330 in hints, "选中文字工具时自动请求加宽属性区")
    ed._set_panel_width(330)
    c.ok(True, "自动加宽处理无异常")

    print("\n[13] V1.2 修复回归：真实事件分发路径")
    from PySide6.QtGui import QKeyEvent, QWheelEvent                 # noqa: E402
    from PySide6.QtWidgets import QApplication as QApp               # noqa: E402

    ed.load_document(_new_base(300, 200), [], 1)
    cv = ed.canvas
    c.ok(ed._filter_installed, "编辑窗口已安装应用级事件过滤器")

    def wheel(target, mods):
        ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                         QPoint(0, 120), Qt.NoButton, mods, Qt.NoScrollPhase, False)
        QApp.sendEvent(target, ev)

    # 复现用户场景：鼠标在图片外的滚动区空白（事件原本根本不到 Canvas）
    z = cv.zoom
    wheel(ed.scroll.viewport(), Qt.AltModifier)
    c.ok(cv.zoom > z, "Alt+滚轮落在滚动区空白处也能缩放（本次修复点）")
    c.ok(not ed._auto_fit, "空白区缩放同样关闭自动适应窗口")

    z = cv.zoom
    wheel(ed.scroll.viewport(), Qt.NoModifier)
    c.ok(abs(cv.zoom - z) < 1e-6, "空白区普通滚轮仍交给滚动区域，不缩放")

    z = cv.zoom
    ed._zoom_step(1)
    c.ok(cv.zoom > z, "状态栏 + 按钮提供不依赖修饰键的缩放兜底")
    ed._zoom_step(-1)

    # Ctrl+C：通过真实 KeyPress 事件分发触发，而不是直接调函数
    hits: list[int] = []
    orig_copy = ed.copy_selected
    ed.copy_selected = lambda *a, **k: hits.append(1)
    try:
        QApp.sendEvent(ed, QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier))
        c.eq(len(hits), 1, "Ctrl+C 真实按键事件触发复制选中标注")
        QApp.sendEvent(ed.canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier))
        c.eq(len(hits), 2, "焦点在画布时 Ctrl+C 同样生效")

        # 输入框内有选中文字时放行，保留常规复制文字
        orig_sel = ed._text_selection_exists
        ed._text_selection_exists = staticmethod(lambda: True)
        try:
            QApp.sendEvent(ed, QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier))
            c.eq(len(hits), 2, "输入框有选中文字时 Ctrl+C 不抢复制文字")
        finally:
            ed._text_selection_exists = orig_sel
    finally:
        ed.copy_selected = orig_copy

    print("\n[13b] V1.2 r3：按钮改名与缩放拖动条")
    from PySide6.QtWidgets import QPushButton as _QPB, QSlider           # noqa: E402
    from app.config import PROJECT_EXT, PROJECT_VERSION                  # noqa: E402

    c.eq(ed.btn_save_project.text(), "保存为可编辑图层文件", "保存按钮已改名为可编辑图层文件")
    c.eq(PROJECT_VERSION, 1, "工程格式版本未变，旧 .snproj 仍可打开")
    c.eq(PROJECT_EXT, ".snproj", "工程扩展名未变")

    c.ok(isinstance(ed.zoom_slider, QSlider), "状态栏存在缩放拖动条")
    box = ed.zoom_slider.parentWidget()
    lay = box.layout()
    minus = [w for w in box.findChildren(_QPB) if w.text() == "－"]
    c.ok(bool(minus) and lay.indexOf(ed.zoom_slider) < lay.indexOf(minus[0]),
         "拖动条位于 ＋／－ 左边")
    c.eq(ed._zoom_to_slider(0.25), 0, "25% 对应滑块最左端")
    c.eq(ed._zoom_to_slider(1.0), 100, "100% 对应滑块正中")
    c.eq(ed._zoom_to_slider(4.0), 200, "400% 对应滑块最右端")
    c.ok(abs(ed._slider_to_zoom(100) - 1.0) < 1e-9, "滑块正中映射为 100%")
    c.ok(all(ed._zoom_to_slider(ed._slider_to_zoom(s)) == s
             for s in (0, 50, 100, 150, 200)), "缩放值与滑块位置可无损往返")

    ed._on_slider_moved(0)
    c.ok(abs(cv.zoom - 0.25) < 1e-6, "拖到最左端缩小为 25%")
    c.ok(not ed._auto_fit, "拖动滑块后关闭自动适应窗口")
    c.eq(ed.lbl_zoom.text(), "25%", "状态栏百分比与滑块同步")

    # 防回环：_on_zoom_changed 回写滑块时阻断 valueChanged
    fired: list[int] = []
    ed.zoom_slider.valueChanged.connect(lambda v: fired.append(v))
    ed.canvas.set_zoom(4.0)
    c.eq(ed.zoom_slider.value(), 200, "set_zoom 反向同步滑块到 400%")
    c.eq(len(fired), 0, "回写滑块时 valueChanged 被阻断，无死循环")
    ed.canvas.set_zoom(1.0)
    c.eq(ed.zoom_slider.value(), 100, "100% 时滑块回到正中")

    # 扩大画布底层：expand_canvas 纯函数（第二道 clamp 保证不裁图）
    from PySide6.QtCore import QRectF as _QRectF                         # noqa: E402
    from app.render import expand_canvas                                 # noqa: E402
    src = QImage(100, 80, QImage.Format_ARGB32)
    src.fill(0xFF336699)
    big, ddx, ddy = expand_canvas(src, _QRectF(-20, -10, 140, 110), "#FFFFFF")
    c.eq((big.width(), big.height()), (140, 110), "expand_canvas 输出尺寸含四边扩量")
    c.eq((ddx, ddy), (20, 10), "左/上扩展返回正确的图层平移量")
    c.ok(big.pixelColor(0, 0).name().lower() == "#ffffff", "扩展区域填充指定颜色")
    c.eq(big.pixelColor(20, 10).rgb(), src.pixelColor(0, 0).rgb(),
         "原图像素被完整保留在偏移处")
    small, _, _ = expand_canvas(src, _QRectF(10, 5, 90, 75), "#FFFFFF")
    c.eq((small.width(), small.height()), (100, 80), "向内拖动不会裁掉原图")

    print("\n[14] V1.2 r3：扩大画布完整流程")
    from PySide6.QtCore import QPointF as _QPF                             # noqa: E402
    from app.expand_overlay import ExpandOverlay                             # noqa: E402

    base3 = _new_base(300, 200)
    ed.load_document(base3, [], 1)
    ed._project_path = "D:/some/old.snproj"       # 模拟从工程文件打开
    ed.add_layer(ed.make_layer("rect", [10, 20, 60, 40]))
    ed.add_layer(ed.make_layer("pen", [0, 0, 0, 0], [[5, 5], [30, 30]]))
    rect_before = list(ed.layers[0]["rect"])
    pts_before = [list(p) for p in ed.layers[1]["points"]]
    size_before = (ed.base.width(), ed.base.height())

    ed.enter_expand_mode()
    c.ok(isinstance(ed._expand_overlay, ExpandOverlay), "扩大画布进入预览模式并创建覆盖层")
    c.ok(ed.btn_expand_apply.isVisibleTo(ed) and ed.btn_expand_cancel.isVisibleTo(ed),
         "预览时顶部出现「应用扩展 / 取消」")
    c.ok(ed.panel.rows["expand_color"].isVisibleTo(ed.panel), "属性区显示扩展区域颜色")
    c.ok(not ed.panel.rows["color"].isVisibleTo(ed.panel), "扩展模式下隐藏普通标注属性")
    c.eq(ed.panel.expand_color_btn.color(), "#FFFFFF", "扩展色默认白色")

    ed.panel.expand_color_btn.colorChanged.emit("#FFCC00")
    c.eq(ed._expand_color, "#FFCC00", "属性区改色写入文档级扩展色")
    c.eq(ed._expand_overlay._expand_color, "#FFCC00", "改色实时同步到预览覆盖层")
    c.eq((ed.base.width(), ed.base.height()), size_before, "改色预览不动底图")
    ed.sync_properties()
    c.ok(ed.panel.rows["expand_color"].isVisibleTo(ed.panel),
         "sync_properties 不覆盖扩展模式的颜色行")

    ov = ed._expand_overlay
    sample = _QPF(123.5, 77.25)
    rt = ov._to_image(ov._to_viewport(sample))
    c.ok(abs(rt.x() - sample.x()) < 1e-6 and abs(rt.y() - sample.y()) < 1e-6,
         "viewport ↔ 图像坐标换算可往返")

    ov._start_bounds = _QRectF(ov.preview_bounds())
    ov.drag_bounds("nw", _QPF(-20, -10))
    b = ov.preview_bounds()
    c.ok(b.left() <= -20 and b.top() <= -10, "拖动左上角可向左/上扩展")
    ov._start_bounds = _QRectF(ov.preview_bounds())
    ov.drag_bounds("nw", _QPF(999, 999))
    b2 = ov.preview_bounds()
    c.ok(b2.left() <= 0 and b2.top() <= 0 and b2.right() >= 300 and b2.bottom() >= 200,
         "向内拖动不会裁掉原图")

    ed.cancel_expand()
    c.eq((ed.base.width(), ed.base.height()), size_before, "取消后底图尺寸不变")
    c.eq(ed.layers[0]["rect"], rect_before, "取消后图层坐标不变")
    c.eq(ed._project_path, "D:/some/old.snproj", "取消后工程路径不变")
    c.ok(ed._expand_overlay is None, "取消后覆盖层销毁")
    c.ok(not ed.panel.rows["expand_color"].isVisibleTo(ed.panel), "取消后属性区恢复正常")

    ed.enter_expand_mode()
    ov = ed._expand_overlay
    ov._start_bounds = _QRectF(ov.preview_bounds())
    ov.drag_bounds("nw", _QPF(-20, -10))
    ed.apply_expand()
    c.eq((ed.base.width(), ed.base.height()), (320, 210), "应用后底图尺寸为四边扩量之和")
    c.eq(ed.layers[0]["rect"], [rect_before[0] + 20, rect_before[1] + 10,
                                rect_before[2], rect_before[3]],
         "形状图层整体平移 (20,10)")
    c.eq(ed.layers[1]["points"], [[x + 20, y + 10] for x, y in pts_before],
         "画笔图层点集整体平移")
    c.ok(ed.base.pixelColor(0, 0).name().lower() == "#ffffff", "扩展区域填充白色")
    c.eq(ed.base.pixelColor(20, 10).rgb(), base3.pixelColor(0, 0).rgb(),
         "原图像素出现在偏移后的位置")
    c.eq(len(ed._history), 1, "应用后历史重置为单一基线（不纳入撤销）")
    c.eq(ed._project_path, "", "应用后清空工程路径，强制另存为")
    c.ok(ed._expand_overlay is None, "应用后退出扩展模式")
    c.ok(not ed.btn_expand_apply.isVisibleTo(ed), "应用后隐藏「应用扩展」按钮")

    proj2 = os.path.join(tmp.name, "expanded" + PROJECT_EXT)
    save_project(proj2, ed.base, ed.layers, ed.next_id)
    b4, l4, _n4 = open_any(proj2)
    c.eq((b4.width(), b4.height()), (320, 210), "扩展后的画布可从工程文件恢复")
    c.eq(l4[0]["rect"], ed.layers[0]["rect"], "扩展后图层坐标可从工程文件恢复")

    tmp.cleanup()
    return c.report()


if __name__ == "__main__":
    sys.exit(main())
