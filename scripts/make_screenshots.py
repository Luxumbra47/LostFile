"""生成 README 使用的真实应用截图（原生渲染 + 示例数据）。

- 截图是程序真实界面在原生 Windows 平台下的渲染结果；
- 不显示窗口：用 Qt 的 WA_DontShowOnScreen，避免截图时窗口闪现；
- 示例文件为临时生成的演示数据（项目 build/_demo_data，已被 .gitignore 排除），
  截图完成后即清理，不会留下任何真实用户路径。
- 注意：不要用 QT_QPA_PLATFORM=offscreen——离屏平台没有中文字体，会渲染成方块。

用法：.venv\\Scripts\\python.exe scripts\\make_screenshots.py
"""
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MB = 1024 * 1024
DEMO_ROOT = os.path.join(ROOT, "build", "_demo_data")
DEMO_APP = os.path.join(ROOT, "build", "_demo_appdata")
SHOT_DIR = os.path.join(ROOT, "docs", "screenshots")

# 设置页标签顺序（与 settingsdlg._build 保持一致）
TAB_ROOTS, TAB_MAINT, TAB_AI, TAB_VEC, TAB_PRIVACY = 0, 1, 2, 3, 4


def make_demo_files() -> list[str]:
    """生成演示文件；大文件用稀疏写避免真占空间。返回索引根列表。"""
    shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    now = time.time()
    day = 86400
    downloads = os.path.join(DEMO_ROOT, "downloads")
    desktop = os.path.join(DEMO_ROOT, "desktop")
    arch = os.path.join(downloads, "归档")
    os.makedirs(arch, exist_ok=True)
    os.makedirs(desktop, exist_ok=True)

    files = {
        os.path.join(downloads, "季度销售报告.pdf"): (3 * MB, now - day + 8000),
        os.path.join(downloads, "季度销售报告-图表.xlsx"): (512 * 1024, now - day + 9000),
        os.path.join(downloads, "立项说明书.docx"): (200 * 1024, now - 2 * day),
        os.path.join(downloads, "产品演示视频.mp4"): (86 * MB, now - 3 * day),
        os.path.join(downloads, "全家福照片.png"): (5 * MB, now - day + 10000),
        os.path.join(downloads, "软件安装包.zip"): (150 * MB, now - 4 * day),
        os.path.join(downloads, "会议纪要.txt"): (12 * 1024, now - 3600),
        os.path.join(arch, "往年报告.pdf"): (1500 * 1024, now - 40 * day),
        os.path.join(arch, "旧版说明书.pdf"): (1024, now - 90 * day),
        os.path.join(desktop, "月度报告模板.xlsx"): (96 * 1024, now - 6 * day),
    }
    for path, (size, mtime) in files.items():
        with open(path, "wb") as f:
            f.seek(max(0, size - 1))
            f.write(b"\0")
        os.utime(path, (mtime, mtime))
    return [downloads, desktop]


def settle(app):
    """走完待处理事件（含控件延迟删除），避免截图时条件标签重叠。"""
    from PySide6.QtCore import QCoreApplication, QEvent
    for _ in range(3):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def wait_until(fn, timeout=20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.025)
    return fn()


def run_query(win, app, text: str, keep_location=False):
    """走一遍自然语言搜索流程（可去掉解析出的位置条件，便于用演示目录截图）。"""
    win.nl_edit.setText(text)
    win.on_nl_search()
    settle(app)
    if not keep_location:
        win._active_conditions = [
            c for c in win._active_conditions if c["kind"] != "location"]
        win._rebuild_chips()
    win._confirm_conditions()
    wait_until(lambda: (app.processEvents() or not win._searching))
    settle(app)


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QStyleFactory

    from lostfile.config import AppConfig
    from lostfile.db import Database
    from lostfile.indexer import scan_directory
    from lostfile.ui.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    for style_name in ("windows11", "windowsvista"):   # 与正式启动一致
        if QStyleFactory.create(style_name) is not None:
            app.setStyle(style_name)
            break
    apply_theme(app, "light")

    roots = make_demo_files()
    shutil.rmtree(DEMO_APP, ignore_errors=True)
    cfg = AppConfig(data_dir=DEMO_APP)
    cfg.set("wizard_completed", True)
    cfg.set("recent_import_enabled", False)
    cfg.save()
    db = Database.open_safe(cfg.db_path())
    for root in roots:
        db.add_root(root)
        res = scan_directory(root, db)
        assert res.completed, res
    os.makedirs(SHOT_DIR, exist_ok=True)

    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    win.resize(1024, 700)
    # 不显示窗口，但用原生平台正常渲染（中文字体正常）
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.show()
    settle(app)
    wait_until(lambda: (app.processEvents() or not win._searching))

    def shot(widget, name: str):
        settle(app)
        path = os.path.join(SHOT_DIR, name)
        widget.grab().save(path)
        print("[shots] saved", path)

    # 1) 主界面：自然语言解析 + 条件标签 + 结果 + 匹配原因
    run_query(win, app, "昨天下载的 PDF，大概 3MB")
    if win.results_view.table.rowCount() > 0:
        win.results_view.table.selectRow(0)
    settle(app)
    assert win.results_view.reason_list.count() > 0, "匹配原因面板为空"
    shot(win, "main_search.png")

    # 2) 两栏结果：上栏「相关文件」分组（展开）+ 下栏结果表
    run_query(win, app, "昨天改过的报告")
    assert win.results_view.current_rows(), "关键词查询无结果"
    assert win.results_view.group_tree.topLevelItemCount() > 0, "未生成相关文件分组"
    win.results_view.group_tree.topLevelItem(0).setExpanded(True)
    win.results_view._splitter.setSizes([156, 306, 168])
    if win.results_view.table.rowCount() > 0:
        win.results_view.table.selectRow(1)
    shot(win, "main_groups.png")

    # 3) 筛选面板
    run_query(win, app, "昨天下载的 PDF，大概 3MB")
    win.filter_toggle.setChecked(True)
    win.filter_panel.show()
    shot(win, "main_filter.png")

    # 4) 设置：隐私页 / 维护页 / AI 页 / 语义页
    from lostfile.ui.settingsdlg import SettingsDialog
    dlg = SettingsDialog(cfg, db, win)
    dlg.resize(660, 560)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dlg.show()
    for tab, name in ((TAB_PRIVACY, "settings_privacy.png"),
                      (TAB_MAINT, "settings_maintenance.png"),
                      (TAB_AI, "settings_ai.png"),
                      (TAB_VEC, "settings_vec.png")):
        dlg.select_tab(tab)
        shot(dlg, name)
    dlg.close()

    win.shutdown()
    db.close()
    shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    shutil.rmtree(DEMO_APP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
