"""生成应用图标 assets/app.ico（纯 Python + Qt 绘制，PNG 内嵌 ICO）。"""
import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets")
PNG = os.path.join(OUT, "app_icon.png")
ICO = os.path.join(OUT, "app.ico")


def paint(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64.0

    # 渐变圆角背景
    path = QPainterPath()
    path.addRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0.0, QColor("#4a90ff"))
    grad.setColorAt(1.0, QColor("#1f66e0"))
    p.fillPath(path, grad)

    # 放大镜
    pen = QPen(QColor("white"), 6 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(16, 14, 22, 22))
    pen2 = QPen(QColor("white"), 6 * s)
    pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen2)
    p.drawLine(int(34 * s), int(34 * s), int(46 * s), int(46 * s))

    # 镜片内的“文件行”
    pen3 = QPen(QColor("white"), int(2.4 * s))
    pen3.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen3)
    p.drawLine(int(24 * s), int(20 * s), int(30 * s), int(20 * s))
    p.drawLine(int(24 * s), int(24 * s), int(30 * s), int(24 * s))
    p.drawLine(int(24 * s), int(28 * s), int(28 * s), int(28 * s))
    p.end()
    return pm


def png_to_ico(png_bytes: bytes) -> bytes:
    """把 PNG 字节包装进 ICO（Vista+ 支持 PNG 压缩图标）。"""
    header = struct.pack("<HHH", 0, 1, 1)                      # 保留/类型/数量
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png_bytes), 22)
    return header + entry + png_bytes


def main() -> int:
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    os.makedirs(OUT, exist_ok=True)
    pm = paint(256)
    if not pm.save(PNG, "PNG"):
        print("[make_icon] PNG 保存失败")
        return 1
    with open(PNG, "rb") as f:
        png_bytes = f.read()
    with open(ICO, "wb") as f:
        f.write(png_to_ico(png_bytes))
    print(f"[make_icon] 已生成 {ICO}（{len(png_bytes)} 字节 PNG 内嵌）")
    return 0


if __name__ == "__main__":
    sys.exit(main())