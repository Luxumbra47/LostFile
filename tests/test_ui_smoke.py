"""UI 冒烟测试（offscreen）：主窗口、自然语言流程、扫描流程、设置对话框。"""
import time

from conftest import make_file, purge_from_recycle_bin, wait_until
from lostfile.config import AppConfig
from lostfile.db import Database


def _make_env(tmp_path):
    cfg = AppConfig(data_dir=str(tmp_path / "data"))
    db = Database.open_safe(cfg.db_path())
    return cfg, db


def test_mainwindow_nl_search_flow(app, tmp_path):
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "downloads"
    make_file(root / "报告.pdf", 3 * 1024 * 1024, mtime=time.time() - 86400)
    make_file(root / "notes.txt", 100)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()

    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    win.show()

    def scan_done():
        app.processEvents()
        return db.count_files() >= 2 and not win.scan_controller.is_scanning()

    if scan_done() is False:
        # 触发扫描
        win.rescan_all()
        assert wait_until(scan_done, timeout=20), "扫描未完成"
    # 等待自动浏览搜索结束，避免与后续检索排队混淆
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)

    # 自然语言搜索
    win.nl_edit.setText("昨天的 PDF，大概 3MB")
    win.on_nl_search()
    app.processEvents()
    assert win.cond_frame.isVisible(), "条件条应显示"
    assert len(win._active_conditions) >= 2
    assert win.chips_box.count() >= 2  # chips + stretch

    # 测试目录并非真实“下载目录”，移除位置条件后确认搜索
    win._active_conditions = [
        c for c in win._active_conditions if c["kind"] != "location"
    ]
    win._confirm_conditions()
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15), "搜索未完成"
    rows = win.results_view.current_rows()
    assert rows, "应有搜索结果"
    assert rows[0]["name"] == "报告.pdf"
    assert rows[0]["exists"] is True

    # 匹配原因已生成
    assert rows[0]["reasons"]

    win.shutdown()
    db.close()


def test_mainwindow_simple_search(app, tmp_path):
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "x.txt", 5)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()

    from lostfile.ui.mainwindow import MainWindow
    from lostfile.query import SearchQuery
    win = MainWindow(cfg, db)
    win.rescan_all()
    assert wait_until(lambda: (app.processEvents() or
                               (db.count_files() >= 1
                                and not win.scan_controller.is_scanning())),
                      timeout=20)
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)

    win._execute_query(SearchQuery(keywords=["x"]))
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    rows = win.results_view.current_rows()
    assert rows and rows[0]["name"] == "x.txt"
    win.shutdown()
    db.close()


def test_settings_dialog_operations(app, tmp_path):
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "y.txt", 5)
    rid = db.add_root(str(root))

    from lostfile.ui.settingsdlg import SettingsDialog
    dlg = SettingsDialog(cfg, db)
    dlg.show()
    app.processEvents()
    dlg.refresh_roots()
    assert dlg.roots_list.count() == 1
    dlg.db.clear_index()
    assert dlg.db.count_files() == 0
    dlg.close()
    db.close()


def test_first_run_wizard_selection(app, tmp_path):
    cfg, db = _make_env(tmp_path)
    from lostfile.ui.wizard import FirstRunWizard
    wiz = FirstRunWizard()
    wiz.show()
    app.processEvents()
    # 默认勾选了存在的已知目录；至少一个已知目录存在时应有选择
    selected = wiz.selected_paths()
    # 在真实机器上（本机下载/桌面/文档目录均存在）至少选中一项；
    # 若测试机没有这些目录也不应报错
    assert isinstance(selected, list)
    # 整个磁盘选项：默认一律不勾选（绝不默认扫描全盘）
    import os
    from lostfile import drives
    if os.name == "nt" and drives.list_fixed_drives():
        assert any(not c.isChecked() for c in wiz._drive_checks.values())
    wiz.close()


def test_hint_rotation(app, tmp_path):
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "x.txt", 5)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()

    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    assert len(win._hints) >= 8
    p0 = win.nl_edit.placeholderText()
    win._rotate_hint()
    assert win.nl_edit.placeholderText() in win._hints
    win.nl_edit.setText("已有内容")
    p1 = win.nl_edit.placeholderText()
    win._rotate_hint()  # 输入框非空时不轮换
    assert win.nl_edit.placeholderText() == p1
    win.shutdown()
    db.close()


def test_drive_pick_dialog(app, tmp_path):
    cfg, db = _make_env(tmp_path)
    from lostfile.ui import settingsdlg
    dlg = settingsdlg._DrivePickDialog()
    dlg.show()
    app.processEvents()
    # 初始不勾选任何磁盘 → 添加按钮禁用
    assert not dlg.ok_btn.isEnabled()
    dlg.close()
    db.close()


def test_results_banner_suggestions(app, tmp_path):
    """空结果 + 近似候选时显示“你是不是要找”横幅，点击链接触发打开。"""
    import html as _html
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.show()
    app.processEvents()
    opened: list[str] = []
    view.open_requested.connect(opened.append)
    path = r"D:\某个目录\年度保告.pdf"
    view.set_results(
        [], 0, False,
        label="没找到名称匹配的文件。",
        suggestions=[{"name": "年度报告.pdf", "path": path, "similarity": 0.7}],
    )
    app.processEvents()
    assert view.info_banner.isVisible()
    assert "年度报告.pdf" in view.info_banner.text()
    view.info_banner.linkActivated.emit(_html.escape(path, quote=True))
    assert opened == [path]
    view.close()
    view.deleteLater()
    app.processEvents()


def test_results_empty_no_banner(app, tmp_path):
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.set_results([], 0, False)
    app.processEvents()
    assert not view.info_banner.isVisible()
    view.close()
    view.deleteLater()
    app.processEvents()


def test_filterpanel_custom_time_query(app, tmp_path):
    """自定义日期范围应正确进入查询条件（用户反馈“自定义时间无效”回归）。"""
    import time as _time
    from datetime import datetime

    from PySide6.QtCore import QDate

    from lostfile.ui.filterpanel import FilterPanel
    panel = FilterPanel(max_results=100)
    panel.show()
    app.processEvents()

    panel.time_combo.setCurrentIndex(5)  # 自定义范围
    panel.date_from.setDate(QDate(2026, 9, 1))
    panel.date_to.setDate(QDate(2026, 10, 17))
    q = panel.build_query()
    start = int(_time.mktime(datetime(2026, 9, 1).timetuple()))
    end = int(_time.mktime(datetime(2026, 10, 17, 23, 59, 59).timetuple()))
    assert q.time_start == start
    assert q.time_end == end

    # 仅匹配文件名开关
    panel.name_edit.setText("模版")
    panel.name_only_check.setChecked(True)
    q2 = panel.build_query()
    assert q2.keywords == ["模版"]
    assert q2.name_only is True
    assert q2.time_start == start  # 自定义时间仍保留
    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_result_limit_option(app, tmp_path):
    """显示数量选项：500/2000/不限，切换后自动按当前条件重搜。"""
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    for i in range(8):
        make_file(root / f"f{i}.txt", 5)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()
    from lostfile.indexer import scan_directory
    scan_directory(str(root), db)

    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)

    options = [(win.results_view.limit_combo.itemData(i),
                win.results_view.limit_combo.itemText(i))
               for i in range(win.results_view.limit_combo.count())]
    assert [v for v, _ in options] == [500, 2000, 0]
    assert any("不限" in t for _, t in options)

    # 切换到“不限” → 自动用当前条件重搜 → 8 条全部显示
    win.results_view.limit_combo.setCurrentIndex(2)
    assert wait_until(
        lambda: (app.processEvents() or
                 (len(win.results_view.current_rows()) == 8
                  and not win._searching)),
        timeout=15)
    assert cfg.get("result_limit") == 0
    assert ("共 8 条" in win.results_view.summary.text())
    win.shutdown()
    db.close()


def test_theme_text_contrast(app, tmp_path):
    """对比度约定：浅色=黑字，深色=白字（用户明确要求）。"""
    from PySide6.QtGui import QPalette
    from lostfile.ui.theme import apply_theme, resolve_mode

    apply_theme(app, "light")
    qss = app.styleSheet()
    assert "QLabel { color: #1a1a1a" in qss
    assert "QTableWidget, QTableView { color: #1a1a1a" in qss
    assert "QPushButton, QToolButton { color: #1a1a1a" in qss
    assert "QHeaderView::section { color: #3a3a3a" in qss
    p = app.palette()
    assert p.color(QPalette.ColorRole.WindowText).name().lower() == "#1a1a1a"
    assert p.color(QPalette.ColorRole.Text).name().lower() == "#1a1a1a"
    assert p.color(QPalette.ColorRole.ButtonText).name().lower() == "#1a1a1a"

    apply_theme(app, "dark")
    qss2 = app.styleSheet()
    assert "QLabel { color: #ffffff" in qss2
    assert "QTableWidget, QTableView { color: #ffffff" in qss2
    assert "QPushButton, QToolButton { color: #ffffff" in qss2
    p2 = app.palette()
    assert p2.color(QPalette.ColorRole.WindowText).name().lower() == "#ffffff"
    assert p2.color(QPalette.ColorRole.Text).name().lower() == "#ffffff"

    apply_theme(app, "light")
    app.setStyleSheet("")


def test_results_large_list_python_sort(app, tmp_path):
    """回归测试：大量结果 + 表头排序（曾经的闪退路径）。
    验证排序改为 Python 侧实现后：行数正确、点击表头不崩溃、顺序正确。
    """
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.show()
    app.processEvents()

    rows = [
        {
            "path": f"D:\\data\\f{i:04d}.bin", "name": f"f{i:04d}.bin",
            "ext": "bin", "size": (600 - i) * 10, "mtime": 1000 + i,
            "ctime": 900 + i, "parent": "D:\\data",
            "exists": i % 5 != 0, "reasons": ["测试原因"], "favorite": False,
        }
        for i in range(600)
    ]
    view.set_results(rows, 600, False)
    assert view.table.rowCount() == 600

    # 大小列升序
    view._sort_by_column(3)
    assert view.table.horizontalHeader().sortIndicatorSection() == 3
    first_before = view._ordered_rows()[0]["size"]
    last_before = view._ordered_rows()[-1]["size"]
    assert first_before < last_before
    # 再点一次 → 降序
    view._sort_by_column(3)
    assert not view._sort_asc
    assert view._ordered_rows()[0]["size"] > view._ordered_rows()[-1]["size"]

    # 名称列排序与筛选
    view._sort_by_column(0)
    names = [r["name"] for r in view._ordered_rows()[:5]]
    assert names == sorted(names)
    view.filter_edit.setText("f0000")
    app.processEvents()
    visible = sum(
        1 for r_idx in range(view.table.rowCount())
        if not view.table.isRowHidden(r_idx)
    )
    assert visible == 1

    # 选中行 → 匹配原因面板正确映射
    view.table.clearSelection()
    view.table.selectRow(0)
    app.processEvents()
    assert view.path_label.text()
    assert view.reason_list.count() >= 1
    view.close()


def test_group_tree_collapsed_and_open(app, tmp_path):
    """分组树：顶层默认折叠；展开后子项双击触发打开；空分组隐藏。"""
    from PySide6.QtCore import Qt
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.show()
    app.processEvents()
    opened: list[str] = []
    view.open_requested.connect(opened.append)

    p1 = r"D:\x\project-notes\SKILL.md"
    p2 = r"D:\x\project-notes\README.md"
    view.set_groups([{
        "title": "project-notes",
        "files": [
            {"name": "SKILL.md", "path": p1, "ext": "md", "size": 100,
             "mtime": 1700000000, "ctime": 1700000000,
             "parent": r"D:\x\project-notes"},
            {"name": "README.md", "path": p2, "ext": "md", "size": 200,
             "mtime": 1700000001, "ctime": 1700000001,
             "parent": r"D:\x\project-notes"},
        ],
    }])
    app.processEvents()
    assert view.group_tree.isVisible()
    assert view.group_tree.topLevelItemCount() == 1
    top = view.group_tree.topLevelItem(0)
    assert "project-notes" in top.text(0)
    assert top.childCount() == 2
    assert not top.isExpanded()  # 默认折叠，可点三角展开

    top.setExpanded(True)
    child = top.child(0)
    assert child.data(0, Qt.ItemDataRole.UserRole) == p1
    view._on_tree_double_click(child, 0)
    assert opened == [p1]

    # 在线组：本地无匹配时显示说明 + GitHub 链接子项
    view.set_groups([{
        "title": "GitHub · a/b ★3", "subtitle": "desc",
        "url": "https://github.com/a/b", "online": True, "files": [],
    }])
    app.processEvents()
    top2 = view.group_tree.topLevelItem(0)
    assert top2.childCount() == 2
    urls: list[str] = []
    view.open_url_requested.connect(urls.append)
    link_item = top2.child(1)
    assert link_item.data(0, Qt.ItemDataRole.UserRole) == "https://github.com/a/b"
    view._on_tree_double_click(link_item, 0)
    assert urls == ["https://github.com/a/b"]

    view.set_groups([])
    app.processEvents()
    assert not view.group_tree.isVisible()
    view.close()
    view.deleteLater()
    app.processEvents()


def test_online_github_checkbox_default_off(app, tmp_path):
    """「在线搜索 GitHub」默认关闭；配置默认 False（隐私要求）。"""
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "x.txt", 5)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()

    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    assert not win.results_view.online_check.isChecked()
    assert cfg.get("github_search") is False
    assert cfg.get("github_consent") is False
    win.shutdown()
    db.close()


def test_group_local_zero_files_note(app, tmp_path):
    """本地 0 文件分组：标题注明 0 个文件，子项显示引导说明。"""
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.show()
    app.processEvents()
    view.set_groups([{
        "title": "project-notes",
        "note": "建议重新扫描后再搜一次。",
        "files": [],
    }])
    app.processEvents()
    top = view.group_tree.topLevelItem(0)
    assert "0 个文件" in top.text(0)
    assert top.childCount() == 1
    assert "重新扫描" in top.child(0).text(0)
    view.close()
    view.deleteLater()
    app.processEvents()


def test_search_relax_conditions_when_no_results(app, tmp_path):
    """无结果时逐步放宽：关键词不中且原始条件（位置）无结果 →
    忽略位置后按时间条件找到昨天的文件。"""
    import time as _time
    from lostfile.indexer import scan_directory
    from lostfile.query import SearchQuery
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    other = tmp_path / "other"
    other.mkdir(exist_ok=True)
    yesterday = _time.time() - 86400
    make_file(root / "x.txt", 5, mtime=yesterday)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()
    scan_directory(str(root), db)

    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    q = SearchQuery(
        keywords=["x"], locations=[str(other)],
        time_field="mtime",
        time_start=yesterday - 3600, time_end=yesterday + 3600,
    )
    win._execute_query(q)
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    rows = win.results_view.current_rows()
    assert rows and rows[0]["name"] == "x.txt"
    assert "放宽条件" in win.results_view.info_banner.text()
    win.shutdown()
    db.close()


def test_incomplete_scan_warning_bar(app, tmp_path):
    """扫描未完成的根目录 → 顶部警告条可见并提示重新扫描。"""
    from lostfile.indexer import scan_directory
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "x.txt", 5)
    rid = db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()
    scan_directory(str(root), db)
    # 模拟上次扫描被中断：状态停留在 scanning
    db.set_root_status(rid, "scanning")

    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    win.show()
    app.processEvents()
    assert win.inc_bar.isVisible()
    assert "未完成" in win.inc_label.text()
    assert win.rescan_action.text()  # 重新扫描入口存在
    win.shutdown()
    db.close()


def test_results_summary_no_label_duplication(app, tmp_path):
    """说明文字只在横幅显示一次，summary 只显示条数（用户反馈重复显示）。"""
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.show()
    app.processEvents()
    view.set_results([], 0, False, label="说明：这是解释文字")
    app.processEvents()
    assert "说明" in view.info_banner.text()
    assert "说明" not in view.summary.text()
    assert view.summary.text() == "共 0 条结果"
    view.close()
    view.deleteLater()
    app.processEvents()


def test_group_tree_in_splitter_draggable(app, tmp_path):
    """分组树位于可拖动分割器第一栏：出现时有初始高度，可隐藏。"""
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.show()
    app.processEvents()
    assert view._splitter.count() >= 3
    assert view._splitter.widget(0) is view.group_tree  # 第一栏是分组树
    view.set_groups([{"title": "g", "note": "无内容", "files": []}])
    app.processEvents()
    sizes = view._splitter.sizes()
    assert len(sizes) == 3 and sizes[0] > 50  # 分组栏有可拖动的初始高度
    assert view.group_tree.isVisible()
    view.set_groups([])
    app.processEvents()
    assert view.group_tree.isHidden()
    view.close()
    view.deleteLater()
    app.processEvents()


def test_table_menu_delete_to_recycle_bin(app, tmp_path):
    """结果表右键菜单含“删除到回收站…”，触发后发出 delete_requested；
    已确认不存在的文件该项禁用。"""
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.show()
    app.processEvents()
    deleted: list[str] = []
    view.delete_requested.connect(deleted.append)
    path = r"D:\x\报告.pdf"
    row = {"path": path, "name": "报告.pdf", "ext": "pdf", "size": 1,
           "mtime": 1, "ctime": 1, "parent": r"D:\x", "exists": True,
           "reasons": [], "favorite": False}

    menu = view._build_table_menu(row)
    texts = [a.text() for a in menu.actions()]
    assert any("删除到回收站" in t for t in texts)
    act = next(a for a in menu.actions() if "删除到回收站" in a.text())
    assert act.isEnabled()
    act.trigger()
    assert deleted == [path]

    menu2 = view._build_table_menu(dict(row, exists=False))
    act2 = next(a for a in menu2.actions() if "删除到回收站" in a.text())
    assert not act2.isEnabled()

    view.close()
    view.deleteLater()
    app.processEvents()


def test_group_tree_menu_delete_to_recycle_bin(app, tmp_path):
    """分组树文件项右键菜单含删除；GitHub 链接项只提供“打开仓库”。"""
    from PySide6.QtCore import Qt
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.show()
    app.processEvents()
    deleted: list[str] = []
    view.delete_requested.connect(deleted.append)
    path = r"D:\x\project-notes\SKILL.md"
    view.set_groups([{
        "title": "project-notes",
        "files": [{"name": "SKILL.md", "path": path, "ext": "md", "size": 1,
                   "mtime": 1, "ctime": 1,
                   "parent": r"D:\x\project-notes"}],
    }])
    app.processEvents()
    item = view.group_tree.topLevelItem(0).child(0)
    target = str(item.data(0, Qt.ItemDataRole.UserRole))
    assert target == path
    menu = view._tree_file_menu(target)
    texts = [a.text() for a in menu.actions()]
    assert any("删除到回收站" in t for t in texts)
    next(a for a in menu.actions() if "删除到回收站" in a.text()).trigger()
    assert deleted == [path]

    url_menu = view._tree_url_menu("https://github.com/a/b")
    assert [a.text() for a in url_menu.actions()] == ["在浏览器中打开仓库"]
    view.close()
    view.deleteLater()
    app.processEvents()


def test_mainwindow_delete_to_recycle_bin(app, tmp_path):
    """主窗口删除流程：确认后文件离开磁盘与索引，列表自动刷新。"""
    import os

    from lostfile.indexer import scan_directory
    from lostfile.query import SearchQuery
    if os.name != "nt":
        return  # 回收站仅 Windows 支持

    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "doomed.txt", 10)
    make_file(root / "keep.txt", 10)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()
    scan_directory(str(root), db)

    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    win._confirm_delete = lambda path: True      # 测试中免弹窗
    win._execute_query(SearchQuery(keywords=["doomed"], limit=100))
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    assert any(r["name"] == "doomed.txt"
               for r in win.results_view.current_rows())

    win._on_delete(str(root / "doomed.txt"))
    assert not os.path.exists(str(root / "doomed.txt"))
    purge_from_recycle_bin([str(root / "doomed.txt")])   # 测试后自清理回收站
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    assert not any(r["name"] == "doomed.txt"
                   for r in win.results_view.current_rows())
    conn = db.connect()
    left = conn.execute(
        "SELECT COUNT(*) FROM files WHERE name='doomed.txt'").fetchone()[0]
    conn.close()
    assert left == 0
    win.shutdown()
    db.close()


def test_group_title_shows_total_and_hint_when_capped(app, tmp_path):
    """回归：分组树条数应与“显示数量”一致。

    未截断时标题显示“N 个文件”；被 cap 截断时显示
    “显示 N / 共 M 个文件”并追加“还有更多”提示行。
    """
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.show()
    app.processEvents()
    files = [{"name": f"f{i}.txt", "path": rf"D:\x\f{i}.txt", "ext": "txt",
              "size": 1, "mtime": 1, "ctime": 1, "parent": r"D:\x"}
             for i in range(3)]
    # 未截断：total == 展示数
    view.set_groups([{"title": "报告", "total": 3, "files": files}])
    app.processEvents()
    top = view.group_tree.topLevelItem(0)
    assert "（3 个文件）" in top.text(0)
    assert top.childCount() == 3          # 无“还有更多”提示行

    # 被截断：total 大于展示数
    view.set_groups([{"title": "报告", "total": 3077, "files": files}])
    app.processEvents()
    top = view.group_tree.topLevelItem(0)
    assert "显示 3 / 共 3077 个文件" in top.text(0)
    assert top.childCount() == 4          # 3 个文件 + 提示行
    assert "还有更多" in top.child(3).text(0)
    view.close()
    view.deleteLater()
    app.processEvents()


def test_group_cap_follows_display_limit(app, tmp_path):
    """分组树的 cap 必须跟随“显示数量”：不限时不再固定 200 条。"""
    from lostfile.indexer import scan_directory
    from lostfile.query import SearchQuery
    from lostfile.ui.mainwindow import MainWindow
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    for i in range(300):
        make_file(root / f"报告_{i:03d}.txt", 5)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()
    scan_directory(str(root), db)

    win = MainWindow(cfg, db)
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)

    # 默认 500 条：300 个文件全部展示，标题为“共 300 个文件”
    win._execute_query(SearchQuery(keywords=["报告"], limit=500))
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    top = win.results_view.group_tree.topLevelItem(0)
    assert top is not None
    assert "300 个文件" in top.text(0)
    assert top.childCount() == 300        # 不再被固定 200 截断

    win.shutdown()
    db.close()


def test_app_display_name_is_lostfile(app, tmp_path):
    """回归：作品名固定为 LostFile，不再带中文后缀；窗口标题随之。"""
    from lostfile import APP_DISPLAY_NAME, APP_NAME
    assert APP_NAME == "LostFile"
    assert APP_DISPLAY_NAME == "LostFile"

    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "x.txt", 5)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()

    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    assert win.windowTitle() == "LostFile"
    win.shutdown()
    db.close()


def test_fallback_keeps_keyword_when_time_is_wrong(app, tmp_path):
    """回归：时间判断有偏差时，回退必须最后才丢关键词。

    文件实际是 3 天前修改的；查询写着“昨天”，若回退先丢关键词，
    就会返回一堆无关文件而找不到目标。正确行为：先丢位置/时间，保住关键词。
    """
    import time as _time

    from lostfile.indexer import scan_directory
    from lostfile.query import SearchQuery
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "project-notes-skill.md", 10,
              mtime=_time.time() - 3 * 86400)
    make_file(root / "unrelated.md", 10, mtime=_time.time())
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()
    scan_directory(str(root), db)

    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)

    # “昨天”的时间窗 + 关键词（位置设为不存在的目录，模拟真实场景）
    # 注意：关键词要选只出现在文件名里的词——测试临时目录位于 D:\project\…，
    # 若用 project 这类通用词，其它文件会靠“路径包含”命中，放宽会在第一级就停下。
    day = 86400
    now = _time.time()
    q = SearchQuery(keywords=["skill"], locations=[str(tmp_path / "nope")],
                    time_field="mtime",
                    time_start=now - day, time_end=now)
    win._execute_query(q)
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    rows = win.results_view.current_rows()
    assert rows, "应通过放宽条件给出结果"
    assert any(r["name"] == "project-notes-skill.md" for r in rows), \
        "回退时不应先把关键词丢掉"
    assert "放宽条件" in win.results_view.info_banner.text()
    win.shutdown()
    db.close()


# ---------- 交互体验：布局记忆 / 快捷键 / 复制文件名 / 新入口 ----------

def test_results_layout_state_roundtrip(app, tmp_path):
    """分栏高度与列宽可保存并恢复到另一个视图。"""
    from lostfile.ui.resultsview import ResultsView

    def mk():
        v = ResultsView()
        v.resize(900, 620)
        v.show()
        v.set_groups([{"title": "g", "total": 1, "files": [
            {"name": "a.txt", "path": r"D:\x\a.txt", "ext": "txt", "size": 1,
             "mtime": 1, "ctime": 1, "parent": r"D:\x"}]}])
        app.processEvents()
        return v

    src = mk()
    src._splitter.setSizes([120, 400, 140])
    src.table.setColumnWidth(0, 321)
    src._sort_col, src._sort_asc = 3, False
    app.processEvents()
    state = src.layout_state()
    assert state["columns"][0] == 321

    dst = mk()
    dst.apply_layout_state(state)
    app.processEvents()
    # 同尺寸窗口下分栏高度应基本一致（允许像素级误差）
    assert all(abs(a - b) <= 6
               for a, b in zip(dst._splitter.sizes(), state["splitter"]))
    assert dst.table.columnWidth(0) == 321
    assert (dst._sort_col, dst._sort_asc) == (3, False)
    # 容错：异常数据不应抛错
    dst.apply_layout_state({"splitter": "bad", "columns": [None, "x"]})
    dst.apply_layout_state(None)
    for v in (src, dst):
        v.close()
        v.deleteLater()
    app.processEvents()


def test_mainwindow_remembers_layout(app, tmp_path):
    """退出时保存窗口几何/分栏/列宽/上次搜索词，下次启动恢复。"""
    from lostfile.ui.mainwindow import MainWindow
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "x.txt", 5)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()

    win = MainWindow(cfg, db)
    win.resize(900, 620)
    win.show()
    app.processEvents()
    win.results_view.set_groups([{"title": "g", "total": 1, "files": [
        {"name": "a.txt", "path": r"D:\x\a.txt", "ext": "txt", "size": 1,
         "mtime": 1, "ctime": 1, "parent": r"D:\x"}]}])
    app.processEvents()
    win.results_view._splitter.setSizes([120, 400, 140])
    win.results_view.table.setColumnWidth(0, 260)
    win.nl_edit.setText("昨天改过的 PDF")
    app.processEvents()
    win.shutdown()          # 触发保存
    db.close()

    assert cfg.get("window_geometry")
    saved = cfg.get("results_layout") or {}
    assert saved.get("columns", [None])[0] == 260
    assert len(saved.get("splitter") or []) == 3
    assert cfg.get("last_query_text") == "昨天改过的 PDF"

    # 重新加载配置（模拟下次启动）后应能恢复
    cfg2, db2 = _make_env(tmp_path)
    win2 = MainWindow(cfg2, db2)
    win2.resize(900, 620)
    win2.show()
    app.processEvents()
    assert win2.nl_edit.text() == "昨天改过的 PDF"
    assert win2.results_view.table.columnWidth(0) == 260
    # 分组树是有搜索结果后才出现的；出现时应沿用记住的分栏高度
    win2.results_view.set_groups([{"title": "g", "total": 1, "files": [
        {"name": "a.txt", "path": r"D:\x\a.txt", "ext": "txt", "size": 1,
         "mtime": 1, "ctime": 1, "parent": r"D:\x"}]}])
    app.processEvents()
    assert all(abs(a - b) <= 6 for a, b in
               zip(win2.results_view.layout_state()["splitter"],
                   saved["splitter"]))
    win2.shutdown()
    db2.close()


def test_mainwindow_shortcuts_and_entries(app, tmp_path):
    """快捷键已安装，且新增“收藏夹/回收站”入口存在。"""
    from lostfile.ui.mainwindow import MainWindow
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "x.txt", 5)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()

    win = MainWindow(cfg, db)
    win.show()
    app.processEvents()
    assert win.favorites_action.text() == "收藏夹"
    assert win.recycle_action.text() == "回收站"
    from PySide6.QtGui import QKeySequence, QShortcut
    keys = {QKeySequence(s.key()).toString()
            for s in win.findChildren(QShortcut)}
    assert "Ctrl+F" in keys
    assert "F5" in keys
    table_keys = {QKeySequence(s.key()).toString()
                  for s in win.results_view.table.findChildren(QShortcut)}
    assert any(k in table_keys for k in ("Del", "Delete"))
    win.shutdown()
    db.close()


def test_copy_file_name_from_table_menu(app, tmp_path):
    """右键菜单新增“复制文件名”，发出 copy_name_requested。"""
    from lostfile.ui.resultsview import ResultsView
    view = ResultsView()
    view.show()
    row = {"path": r"D:\x\报告.pdf", "name": "报告.pdf", "ext": "pdf",
           "size": 1, "mtime": 1, "ctime": 1, "parent": r"D:\x",
           "exists": True, "reasons": [], "favorite": False}
    view.set_results([row], 1, False)
    app.processEvents()
    got: list[str] = []
    view.copy_name_requested.connect(got.append)
    menu = view._build_table_menu(row)
    act = next(a for a in menu.actions() if a.text() == "复制文件名")
    act.trigger()
    assert got == [r"D:\x\报告.pdf"]

    # 右键菜单里的动作作用于“被右键的那一行”，不依赖表格选中状态
    open_got: list[str] = []
    view.open_requested.connect(open_got.append)
    next(a for a in menu.actions() if a.text() == "打开").trigger()
    assert open_got == [r"D:\x\报告.pdf"]

    tree_menu = view._tree_file_menu(r"D:\x\报告.pdf")
    assert any(a.text() == "复制文件名" for a in tree_menu.actions())
    view.close()
    view.deleteLater()
    app.processEvents()


def test_settings_maintenance_and_common_dirs(app, tmp_path):
    """设置里：维护页显示库大小并可压缩；常用目录对话框可多选。"""
    from lostfile.ui import settingsdlg
    from lostfile.ui.settingsdlg import SettingsDialog
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    make_file(root / "y.txt", 5)
    db.add_root(str(root))

    dlg = SettingsDialog(cfg, db)
    dlg.show()
    app.processEvents()
    assert "索引库大小" in dlg.maint_label.text()
    db.vacuum()                      # 小库，瞬间完成
    dlg._refresh_maintenance()
    assert "索引库大小" in dlg.maint_label.text()
    assert db.check_integrity() is True

    cand = [{"label": "桌面（D:）", "path": r"D:\桌面"},
            {"label": "视频（D:）", "path": r"D:\视频"}]
    pick = settingsdlg._CommonDirsDialog(cand)
    pick.show()
    app.processEvents()
    assert pick.selected() == [r"D:\桌面", r"D:\视频"]   # 默认全选
    pick._set_all(False)
    assert pick.selected() == []
    pick._set_all(True)
    assert len(pick.selected()) == 2
    pick.close()
    dlg.close()
    db.close()


def test_wizard_offers_discovered_common_dirs(app, tmp_path):
    """向导会把其他盘探测到的常用文件夹列为默认勾选项。"""
    import os

    from lostfile import knownfolders as kf
    from lostfile.ui.wizard import FirstRunWizard
    wiz = FirstRunWizard()
    wiz.show()
    app.processEvents()
    extras = list(wiz._extra_checks.keys())
    discovered = [d["path"] for d in kf.suggested_dirs()]
    known = {os.path.normcase(os.path.abspath(p)) for p in
             (wiz._downloads, wiz._desktop, wiz._documents) if p}
    expected = [p for p in discovered
                if os.path.normcase(os.path.abspath(p)) not in known]
    assert set(extras) == set(expected)
    selected = wiz.selected_paths()
    for p in extras:
        assert p in selected          # 默认勾选
    wiz.close()
    wiz.deleteLater()
    app.processEvents()


def test_version_and_changelog_in_sync(app, tmp_path):
    """回归：版本号必须与 CHANGELOG 的当前版本条目、隐私说明的生效版本一致。"""
    import os
    import re

    from lostfile import __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8") as f:
        changelog = f.read()
    assert f"## [{__version__}]" in changelog, "CHANGELOG 缺少当前版本条目"
    with open(os.path.join(root, "docs", "PRIVACY.md"), encoding="utf-8") as f:
        privacy = f.read()
    assert __version__ in privacy.splitlines()[2], "隐私说明生效版本未同步"


def test_favorites_dialog_lists_and_removes(app, tmp_path):
    """收藏夹对话框：列出收藏、标记不存在的文件、可取消收藏。"""
    from lostfile.ui.favoritesdialog import FavoritesDialog
    cfg, db = _make_env(tmp_path)
    good = str(make_file(tmp_path / "keep.txt", 5))
    db.add_favorite(good)
    db.add_favorite(str(tmp_path / "gone.txt"))

    dlg = FavoritesDialog(db)
    dlg.show()
    app.processEvents()
    assert dlg.listw.count() == 2
    texts = "\n".join(dlg.listw.item(i).text() for i in range(dlg.listw.count()))
    assert "keep.txt" in texts
    assert "已不存在" in texts

    dlg.listw.setCurrentRow(0)
    dlg._remove()
    app.processEvents()
    assert dlg.listw.count() == 1
    assert len(db.list_favorites()) == 1
    dlg.close()
    db.close()


def test_unlimited_page_loads_more(app, tmp_path, monkeypatch):
    """选“不限”：先渲染一页，点「加载更多」逐页取完（不再有 50000 硬上限）。"""
    from lostfile.indexer import scan_directory
    from lostfile.query import SearchQuery
    from lostfile.ui import mainwindow as mw

    monkeypatch.setattr(mw, "PAGE_SIZE", 3)
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    for i in range(8):
        make_file(root / f"报告_{i}.txt", 5)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()
    scan_directory(str(root), db)

    win = mw.MainWindow(cfg, db)
    win.show()
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)

    # 切到“不限”（阻塞信号，避免额外触发一次自动重搜）
    win.results_view.limit_combo.blockSignals(True)
    win.results_view.limit_combo.setCurrentIndex(2)
    win.results_view.limit_combo.blockSignals(False)
    win._execute_query(SearchQuery(keywords=["报告"]))
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)

    view = win.results_view
    assert len(view.current_rows()) == 3          # 首屏只渲染一页
    assert view.load_more_btn.isVisible()
    assert "已显示 3 条" in view.summary.text()

    # 点两次“加载更多” → 8 条全部加载出来，按钮随之消失
    for _ in range(2):
        win._load_more()
        assert wait_until(lambda: (app.processEvents() or not win._searching),
                          timeout=15)
    assert len(view.current_rows()) == 8
    assert view.table.rowCount() == 8
    assert not view.load_more_btn.isVisible()
    assert "共 8 条结果" in view.summary.text()
    win.shutdown()
    db.close()


def test_unmatched_keyword_lists_only_recent_few(app, tmp_path, monkeypatch):
    """回归：关键词无匹配时不再把整个索引当结果（曾出现 166 万条假结果）。"""
    from lostfile.indexer import scan_directory
    from lostfile.query import SearchQuery
    from lostfile.ui import mainwindow as mw

    monkeypatch.setattr(mw, "RECENT_FALLBACK", 4)
    cfg, db = _make_env(tmp_path)
    root = tmp_path / "r"
    for i in range(20):
        make_file(root / f"f{i}.txt", 5)
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.save()
    scan_directory(str(root), db)

    win = mw.MainWindow(cfg, db)
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)

    win._execute_query(SearchQuery(keywords=["zzz_no_such_name_zzz"]))
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    view = win.results_view
    assert len(view.current_rows()) == 4          # 只列最近少量文件
    assert "未匹配到名称" in view.summary.text()
    assert "20" not in view.summary.text()        # 不再报整库条数
    assert not win._more_available
    assert not view.load_more_btn.isVisible()
    win.shutdown()
    db.close()