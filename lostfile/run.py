"""应用入口：QApplication 引导、主题、主窗口装配。"""
import os
import sys
import time

from . import APP_DISPLAY_NAME, APP_NAME, ORG_NAME, __version__


def _app_icon():
    """运行时绘制应用图标（蓝色圆角底 + 白色放大镜）。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 圆角背景
    path = QPainterPath()
    path.addRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
    p.fillPath(path, QColor("#2E7CF6"))

    # 放大镜
    pen = QPen(QColor("white"), 6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(16, 14, 22, 22))
    p.drawLine(34, 34, 46, 46)

    # 镜片内的文件折角
    pen2 = QPen(QColor("white"), 2.4)
    pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen2)
    p.drawLine(24, 20, 30, 20)
    p.drawLine(24, 24, 30, 24)
    p.drawLine(24, 28, 28, 28)
    p.end()
    return QIcon(pm)


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) >= 2 and args[0] == "--scan-child":
        # 扫描子进程入口（GUI 主进程以该参数拉起，用于崩溃隔离）
        return _run_scan_child(args[1])

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory
        from PySide6.QtCore import Qt
    except ImportError as e:  # 依赖缺失时给出可读错误
        print(f"[LostFile] 无法加载 PySide6（{e}）。请先安装依赖：")
        print("  .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        return 1

    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationDisplayName(APP_DISPLAY_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    QApplication.setApplicationVersion(__version__)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv if argv is None else [sys.argv[0], *argv])
    # Windows 原生风格：控件外观与系统保持一致（Win11 风格 > Win10 vista 风格）
    for style_name in ("windows11", "windowsvista"):
        if QStyleFactory.create(style_name) is not None:
            app.setStyle(style_name)
            break
    app.setWindowIcon(_app_icon())

    from .config import AppConfig
    cfg = AppConfig()

    # 崩溃取证：所有异常/原生崩溃写入数据目录日志（仅本机）
    _install_diagnostics(cfg)
    # 全局异常兜底：任何未捕获异常都会写盘记录，便于排查“点击后闪退”类问题
    _install_crash_log(cfg)

    from .db import Database
    try:
        db = Database.open_safe(cfg.db_path())
    except Exception as e:  # 数据目录不可写等情况
        QMessageBox.critical(
            None, "LostFile",
            f"无法初始化本地索引数据库：\n{e}\n\n"
            f"数据目录：{cfg.data_dir}\n请检查磁盘空间与写入权限。",
        )
        return 1

    from .ui.theme import apply_theme
    apply_theme(app, cfg.get("theme"))

    from .ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    win.show()

    if db.recovery_backup:
        QMessageBox.information(
            win, "索引数据库已重建",
            "检测到索引数据库损坏，已自动备份原文件：\n"
            f"{db.recovery_backup}\n\n已创建新的空索引，请在设置中重新扫描目录。",
        )

    code = app.exec()
    win.shutdown()
    db.close()
    return code


def _install_diagnostics(cfg) -> None:
    """把崩溃/异常信息写入数据目录（仅本机），用于定位“闪退”类问题。

    - faulthandler：原生崩溃（段错误等）时输出崩溃线程的调用栈；
    - 打包版重定向 stderr：Python 异常与线程错误不再丢失。
    两个文件都在数据目录下：faulthandler.log、stderr.log。
    """
    import faulthandler
    try:
        fh = open(os.path.join(cfg.data_dir, "faulthandler.log"), "a",
                  encoding="utf-8", buffering=1)
        fh.write(f"\n===== launch {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        faulthandler.enable(fh)
        if getattr(sys, "frozen", False):
            errf = open(os.path.join(cfg.data_dir, "stderr.log"), "a",
                        encoding="utf-8", buffering=1)
            errf.write(f"\n===== launch {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            sys.stderr = errf
    except OSError:
        pass


def _run_scan_child(job_path: str) -> int:
    """扫描子进程主函数：无界面；原生崩溃时 faulthandler 会留下现场。"""
    import json

    from .scanchild import run_child

    workdir = None
    try:
        with open(job_path, "r", encoding="utf-8") as f:
            job = json.load(f)
        workdir = job.get("workdir")
    except (OSError, json.JSONDecodeError):
        sys.stderr.write(f"[scanchild] 任务文件无法读取：{job_path}\n")
    if workdir:
        import faulthandler
        try:
            f = open(os.path.join(workdir, "scan_child_faulthandler.log"),
                     "a", encoding="utf-8", buffering=1)
            f.write(f"\n===== child launch {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            faulthandler.enable(f)
        except OSError:
            pass
    return run_child(job_path)


def _install_crash_log(cfg) -> None:
    """把未捕获异常写入数据目录 crash.log，帮助定位闪退原因（仅本机）。"""
    import sys
    import traceback

    def hook(etype, value, tb):
        import threading
        entry = (
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"thread={threading.current_thread().name} "
            f"{etype.__name__}: {value}\n"
            + "".join(traceback.format_tb(tb))
        )
        try:
            with open(os.path.join(cfg.data_dir, "crash.log"), "a",
                      encoding="utf-8") as f:
                f.write(entry + "\n")
        except OSError:
            pass
        sys.__excepthook__(etype, value, tb)

    sys.excepthook = hook