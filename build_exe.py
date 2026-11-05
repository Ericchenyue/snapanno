"""把 SnapAnno 打包成单文件 Windows GUI exe。

用法：python build_exe.py
产物：dist/SnapAnno v<APP_VERSION>.exe（英文名单文件 exe，版本号取自 app/config.py）

说明：exe 名称含中文与空格，全部通过 Python 字符串传给 PyInstaller API，
避免 Windows 命令行参数编码导致乱码。
"""

from __future__ import annotations

import struct
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from app.config import APP_VERSION                       # noqa: E402

# "1.2" / "1.2.0" 统一取 major.minor 作为文件名版本
EXE_NAME = f"SnapAnno v{'.'.join(APP_VERSION.split('.')[:2])}"
ICON_PATH = ROOT / "app_icon.ico"

# 只排除与本项目确定无关的模块；Qt 核心/Widgets/Gui 由 hook 自动收集。
EXCLUDES = [
    "tkinter", "unittest", "pytest", "numpy", "PIL", "matplotlib", "scipy",
    "pandas", "IPython", "jupyter",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQml", "PySide6.QtQuick3D",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtUiTools",
    "PySide6.QtHelp", "PySide6.QtTest", "PySide6.QtSerialPort", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtWebSockets", "PySide6.QtSql",
    "PySide6.QtNetworkAuth", "PySide6.QtAxContainer", "PySide6.QtOpenGLWidgets",
]


def make_icon() -> Path:
    """用 Qt 画一个取景框图标，包成 PNG 载荷的 ICO（Vista+ 支持）。"""
    if ICON_PATH.exists():
        return ICON_PATH

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QLineF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen

    img = QImage(256, 256, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2F6FE4"))
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 52, 52)

    pen = QPen(QColor("#FFFFFF"))
    pen.setWidth(14)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for line in (
        QLineF(56, 100, 56, 56), QLineF(56, 56, 100, 56),
        QLineF(156, 56, 200, 56), QLineF(200, 56, 200, 100),
        QLineF(56, 156, 56, 200), QLineF(56, 200, 100, 200),
        QLineF(156, 200, 200, 200), QLineF(200, 200, 200, 156),
    ):
        p.drawLine(line)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    p.drawEllipse(QRectF(112, 112, 32, 32))
    p.end()

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        raise RuntimeError("生成图标 PNG 失败")
    buf.close()
    png = bytes(ba)

    # ICO = 6 字节文件头 + 16 字节目录项 + PNG 数据；宽高字节 0 表示 256。
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    ICON_PATH.write_bytes(header + entry + png)
    return ICON_PATH


def _backup_existing(exe: Path) -> None:
    """重打包前把同名旧 exe 移入备份目录（移动而非删除）。

    PyInstaller 写 onefile 产物时会 os.remove 同名旧文件，部分受管环境下
    删除被安全策略拦截导致打包失败；先移走既避免删除，又保留可恢复版本。
    """
    if not exe.exists():
        return
    backup = ROOT / "dist" / "backup"
    backup.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup / f"{exe.stem} ({stamp}).exe"
    i = 1
    while target.exists():
        target = backup / f"{exe.stem} ({stamp}_{i}).exe"
        i += 1
    exe.rename(target)
    print(f"已备份旧版本：{target}")


def main() -> int:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("缺少 PyInstaller，请先执行：python -m pip install -r requirements.txt")
        return 1

    icon = make_icon()
    exe = ROOT / "dist" / f"{EXE_NAME}.exe"
    _backup_existing(exe)
    args = [
        "--noconfirm", "--clean", "--onefile", "--windowed",
        f"--name={EXE_NAME}",
        f"--icon={icon}",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyi"),
        "--specpath", str(ROOT / "build"),
    ]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    args.append(str(ROOT / "main.py"))

    print("打包参数：", EXE_NAME)
    pyi.run(args)
    if not exe.exists():
        print("打包结束但未找到 exe：", exe)
        return 1
    print(f"完成：{exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
