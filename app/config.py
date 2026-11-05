"""全局常量、工具清单与默认样式配置。"""

APP_NAME = "SnapAnno"
APP_VERSION = "1.2"
PROJECT_EXT = ".snproj"
PROJECT_VERSION = 1                 # 工程文件格式版本

MAX_UNDO = 60                       # 撤销栈上限（状态快照数）
MIN_SELECTION = 8                   # 选区最小边长（逻辑像素），小于此值视为无效
MIN_SHAPE = 4                       # 标注最小边长（图像像素）
HANDLE_SIZE = 8                     # 选中控制点边长（屏幕像素）

# 左侧工具栏：(工具标识, 中文名称)
# V1.2：选择箭头置顶；layer_up / layer_down 为动作型按钮，非绘制工具。
TOOL_LIST = [
    ("select", "选择箭头"),
    ("rect", "矩形框"),
    ("circle", "圆形框"),
    ("ellipse", "椭圆框"),
    ("rect_fill", "矩形填充/蒙版"),
    ("circle_fill", "圆形填充/蒙版"),
    ("ellipse_fill", "椭圆填充/蒙版"),
    ("arrow", "箭头"),
    ("text", "文字标注"),
    ("pen", "自由画笔"),
    ("mosaic", "马赛克"),
    ("blur", "模糊"),
    ("layer_up", "图层上移"),
    ("layer_down", "图层下移"),
]

DRAW_TOOLS = {t for t, _ in TOOL_LIST} - {"layer_up", "layer_down", "select"}
SHAPE_TOOLS = {"rect", "circle", "ellipse", "rect_fill", "circle_fill", "ellipse_fill", "arrow"}
STROKE_TOOLS = {"pen", "mosaic", "blur"}
FILL_TOOLS = {"rect_fill", "circle_fill", "ellipse_fill"}

# 允许打开的图片格式
IMAGE_FILTER = "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)"
OPEN_FILTER = f"SnapAnno 可编辑图层文件 (*{PROJECT_EXT});;{IMAGE_FILTER};;所有文件 (*.*)"

# 基础样式（所有图层字段均有默认值，保证 JSON 往返稳定）
DEFAULT_STYLE = {
    "color": "#E53935",           # 主色（线条 / 文字 / 画笔）
    "width": 3,                   # 线宽（矩形、圆、椭圆、箭头、画笔）
    "outline": False,             # 是否加外轮廓
    "outline_width": 2,           # 外轮廓大小
    "outline_color": "#FFFFFF",   # 外轮廓颜色
    "fill_color": "#FFC107",      # 填充颜色
    "fill_alpha": 45,             # 填充不透明度 0~100
    "text": "",                   # 文字内容
    "font_family": "Microsoft YaHei",
    "font_size": 24,
    "bold": False,
    "underline": False,
    "brush": 30,                  # 马赛克 / 模糊笔刷粗细
}

# 各工具对基础样式的覆盖
TOOL_STYLE_OVERRIDE = {
    "arrow": {"width": 4},
    "text": {"font_size": 26, "bold": True},
    "pen": {"width": 4},
    "mosaic": {"brush": 36},
    "blur": {"brush": 36},
}

STYLESHEET = """
QWidget { font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif; font-size: 12px; }
QMainWindow, QDialog { background: #F5F6F8; }
QPushButton {
    background: #FFFFFF; border: 1px solid #D5D9E0; border-radius: 5px;
    padding: 6px 12px; color: #222831;
}
QPushButton:hover { background: #ECF3FF; border-color: #7FA9E8; }
QPushButton:pressed { background: #DCE9FF; }
QPushButton:checked { background: #FFF1F0; border-color: #E53935; color: #D32F2F; font-weight: bold; }
QPushButton:disabled { color: #A0A6B0; background: #F0F1F3; }
QGroupBox { border: 1px solid #E0E3E8; border-radius: 5px; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #4A5568; }
QLineEdit, QSpinBox, QComboBox, QFontComboBox, QTextEdit {
    border: 1px solid #D5D9E0; border-radius: 4px; padding: 3px 6px; background: #FFFFFF;
}
QScrollArea { border: none; background: #E9ECF1; }
QToolBar { background: #FFFFFF; border-bottom: 1px solid #E0E3E8; spacing: 4px; padding: 4px; }
QStatusBar { color: #6B7280; }
"""
