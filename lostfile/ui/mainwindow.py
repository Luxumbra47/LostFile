"""主窗口：自然语言搜索、筛选面板、扫描进度、结果与设置编排。"""
import dataclasses
import os
import threading

from PySide6.QtCore import QObject, QProcess, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCompleter, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QStackedWidget,
    QToolBar, QVBoxLayout, QWidget,
)

from .. import APP_DISPLAY_NAME
from .. import fileops
from .. import github
from .. import nl
from .. import pinyin
from .. import query as qmod
from .. import vecindex as vecmod
from .. import vecmodel as vecmm
from .. import workers
from ..config import AppConfig
from ..db import Database
from ..ui.favoritesdialog import FavoritesDialog
from ..ui.filterpanel import FilterPanel
from ..ui.recentdialog import RecentDialog
from ..ui.resultsview import ResultsView
from ..ui.settingsdlg import SettingsDialog
from ..ui.theme import apply_theme, resolve_mode
from ..ui.wizard import FirstRunWizard

# 结果分页：选“不限”时先渲染一页，其余由「加载更多」按需追加。
PAGE_SIZE = 2000
# 关键词无精确匹配时，回退列表只列最近修改的少量文件（绝不把整个索引当结果）。
RECENT_FALLBACK = 200


class _Emitter(QObject):
    done = Signal(object, int, int, object)   # rows, total, generation, extra
    more_done = Signal(object, int, int)      # rows, total, generation（加载更多）
    failed = Signal(str)
    ai_result = Signal(object, object)        # (conditions | None, error | None)


class ScanController(QObject):
    """扫描控制器：扫描运行在独立子进程（QProcess）中。

    文件系统枚举在部分目录（云盘挂载/杀软过滤驱动等）上可能触发
    进程级原生崩溃。子进程隔离后，主程序不受影响；子进程崩溃时
    发出 crashed 信号（附最后处理的目录），由界面提示用户。
    """

    progress = Signal(str, int, int, int)   # root 名, 目录数, 文件数, 错误数
    root_finished = Signal(int, bool, int)  # 保留接口兼容（子进程模式由 finished 汇总）
    finished = Signal()                     # 正常结束（含取消）
    crashed = Signal(str)                   # 子进程异常退出（消息含最后目录）

    def __init__(self, db, cfg: AppConfig, parent=None):
        super().__init__(parent)
        self.db = db
        self._cfg = cfg
        self._proc: QProcess | None = None
        self._current_roots: list[dict] = []
        self._cancel_requested = False
        self._poll = QTimer(self)
        self._poll.setInterval(400)
        self._poll.timeout.connect(self._on_poll)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._force_kill)

    # ---- 文件路径 ----

    def _job_path(self) -> str:
        import os
        return os.path.join(self._cfg.data_dir, "scan_job.json")

    def _state_path(self) -> str:
        import os
        return os.path.join(self._cfg.data_dir, "scan_state.json")

    def _ctrl_path(self) -> str:
        import os
        return os.path.join(self._cfg.data_dir, "scan_control.json")

    def _result_path(self) -> str:
        import os
        return os.path.join(self._cfg.data_dir, "scan_result.json")

    def _write_json(self, path: str, obj: dict) -> None:
        import json
        import os
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            # ensure_ascii=True：孤立代理字符转义为 \udXXX，避免编码崩溃
            json.dump(obj, f, ensure_ascii=True)
        os.replace(tmp, path)

    def _read_json(self, path: str, default: dict) -> dict:
        import json
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default

    # ---- 生命周期 ----

    def is_scanning(self) -> bool:
        return bool(self._proc and
                    self._proc.state() != QProcess.ProcessState.NotRunning)

    def start(self, roots: list[dict],
              exclude_dirs: set | None = None,
              exclude_exts: set | None = None) -> bool:
        import os
        import sys
        if self.is_scanning():
            return False
        self._current_roots = list(roots)
        self._cancel_requested = False
        self._write_json(self._ctrl_path(), {"cancel": False, "pause": False})
        for p in (self._result_path(), self._state_path()):
            with suppress_oserror():
                os.remove(p) if os.path.exists(p) else None
        job = {
            "db_path": self._cfg.db_path(),
            "workdir": self._cfg.data_dir,
            "roots": [{"id": r["id"], "path": r["path"],
                       "display_name": r.get("display_name") or r["path"]}
                      for r in roots],
            "exclude_dirs": sorted(exclude_dirs or ()),
            "exclude_exts": sorted(exclude_exts or ()),
        }
        self._write_json(self._job_path(), job)

        self._proc = QProcess(self)
        self._proc.finished.connect(self._on_process_finished)
        if getattr(sys, "frozen", False):
            self._proc.setProgram(sys.executable)
            self._proc.setArguments(["--scan-child", self._job_path()])
        else:
            self._proc.setProgram(sys.executable)
            self._proc.setArguments(["-m", "lostfile.scanchild", self._job_path()])
        with suppress_oserror():
            self._proc.setStandardErrorFile(
                os.path.join(self._cfg.data_dir, "scan_child_stderr.log"))
        self._proc.start()
        self._poll.start()
        return True

    def _on_poll(self):
        st = self._read_json(self._state_path(), {})
        if st:
            self.progress.emit(
                str(st.get("root", "")), int(st.get("dirs", 0)),
                int(st.get("files", 0)), int(st.get("errors", 0)),
            )

    def _on_process_finished(self, exit_code: int, exit_status):
        self._poll.stop()
        self._kill_timer.stop()
        result = self._read_json(self._result_path(), {})
        st = self._read_json(self._state_path(), {})
        # Python 层异常（如非法文件名字符导致编码失败）：显式告知用户，
        # 不再静默地当作“扫描结束”
        if (result.get("state") == "error"
                and not self._cancel_requested):
            current = str(st.get("current_dir", "")) or "未知位置"
            message = str(result.get("message") or f"退出码 {exit_code}")
            self._scan_error_box(
                "扫描出错，已提前终止",
                f"扫描过程中出错：{message}\n\n最后处理的目录：\n{current}",
                current,
                "可点击下方按钮把该目录加入排除后重试；"
                "也可以到 设置→索引目录 把你关心的文件夹单独添加为索引目录，"
                "只扫描那一小块，速度快且不受影响。",
            )
        crash = (exit_status == QProcess.ExitStatus.CrashExit
                 and not self._cancel_requested)
        if crash:
            current = str(st.get("current_dir", "")) or "未知位置"
            self.crashed.emit(
                f"扫描进程在系统层面意外退出（错误码 {exit_code}）。\n"
                f"最后处理的目录：\n{current}\n\n"
                "这通常是该目录相关的系统驱动/权限问题，不是文件本身损坏。"
            )
            # 崩溃时，被中断的根目录状态由“扫描中”恢复为“已中止”
            for r in self._current_roots:
                with suppress_oserror():
                    self.db.set_root_status(r["id"], "stopped")
        self._proc = None
        self.finished.emit()

    def cancel(self):
        if not self.is_scanning():
            return
        self._cancel_requested = True
        self._write_json(self._ctrl_path(), {"cancel": True, "pause": False})
        # 宽限期后仍未退出则强制结束子进程
        self._kill_timer.start(4000)

    def _force_kill(self):
        with suppress_oserror():
            if self._proc and self._proc.state() != QProcess.ProcessState.NotRunning:
                self._proc.kill()

    def pause(self):
        self._write_json(self._ctrl_path(),
                         {"cancel": self._cancel_requested, "pause": True})

    def resume(self):
        self._write_json(self._ctrl_path(),
                         {"cancel": self._cancel_requested, "pause": False})

    def shutdown(self):
        if self.is_scanning():
            self._cancel_requested = True
            self.cancel()
            with suppress_oserror():
                if self._proc:
                    if not self._proc.waitForFinished(1500):
                        self._proc.kill()
                        self._proc.waitForFinished(1500)
        self._poll.stop()


class suppress_oserror:
    """上下文管理器：吞掉 OSError（清理/状态类次要操作）。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return exc[0] is None or issubclass(exc[0], OSError)


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig, db: Database):
        super().__init__()
        self.cfg = cfg
        self.db = db
        self._generation = 0
        self._active_conditions: list[dict] = []
        self._searching = False
        self._paused = False
        self._auto_browsed = False
        self._pending_query = None
        self._last_query = None
        self._last_auto = False
        self._loaded_rows: list[dict] = []   # 已加载（含“加载更多”）的结果
        self._more_available = False         # 是否还有可继续加载的结果
        self._page_size = PAGE_SIZE
        self._pinyin_backfill = pinyin.PinyinBackfill(db)
        self._fts_backfill = workers.FtsBackfill(db)
        self._content_backfill = workers.ContentBackfill(db)
        self._hash_backfill = workers.HashBackfill(db)
        self._vec_backfill = vecmod.VectorBackfill(db, cfg)

        self.scan_controller = ScanController(db, cfg, self)
        self._build()
        self._wire()
        # 恢复上次的“显示数量”选择（阻塞信号，避免启动即触发搜索）
        self.results_view.limit_combo.blockSignals(True)
        idx = self.results_view.limit_combo.findData(
            int(self.cfg.get("result_limit") or 500))
        self.results_view.limit_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.results_view.limit_combo.blockSignals(False)
        # 恢复“在线搜索 GitHub”开关状态（阻塞信号，避免启动即触发）
        self.results_view.online_check.blockSignals(True)
        self.results_view.online_check.setChecked(
            bool(self.cfg.get("github_search")))
        self.results_view.online_check.blockSignals(False)
        self.refresh_ui_state()
        QTimer.singleShot(0, self._maybe_wizard)

    # ---------- UI 搭建 ----------

    def _build(self):
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1020, 700)
        self.statusBar().showMessage("就绪")

        # 工具栏
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)
        self.rescan_action = QAction("重新扫描", self)
        self.recent_action = QAction("最近打开", self)
        self.favorites_action = QAction("收藏夹", self)
        self.dup_action = QAction("重复文件", self)
        self.recycle_action = QAction("回收站", self)
        self.theme_action = QAction("主题", self)
        self.settings_action = QAction("设置", self)
        tb.addAction(self.rescan_action)
        tb.addAction(self.recent_action)
        tb.addAction(self.favorites_action)
        tb.addAction(self.dup_action)
        tb.addAction(self.recycle_action)
        tb.addAction(self.theme_action)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy().Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.settings_action)

        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(12, 12, 12, 8)
        lay.setSpacing(8)

        # 搜索行
        search_row = QHBoxLayout()
        self.nl_edit = QLineEdit()
        # 提示词约定：每条都必须是“可直接执行的搜索例子”（能力说明写进悬浮提示），
        # 且只用通用词汇，不出现任何个人化的搜索记录。test_nlparser 会逐条校验可解析。
        self._hints = [
            "昨天下载的 PDF，大概 3MB",
            "上周修改的图片",
            "下载目录里超过 100MB 的视频",
            "最近一个月创建的 Word 文档",
            "桌面上那个很大的视频文件",
            "今天保存的 Excel 表格",
            "小于 1MB 的文本文件",
            "前几天下载的安装包",
            "昨天改过的 PPT",
            "昨天的实验报告",
            "上周的课表",
            "去年的照片",
            "上个月的照片",
            "D 盘上的压缩包",
            "本月修改的代码文件",
            "这个月的发票 PDF",
            "昨天收到的压缩包",
            "两年前的视频",
            "桌面上的简历 Word",
            "最近一周的音乐",
            "超过 500MB 的安装包",
            "photoshop 的安装包",
            "minecraft 的存档",
        ]
        self._hint_index = 0
        self.nl_edit.setPlaceholderText(self._hints[0])
        self.nl_edit.setToolTip(
            "直接用一句话描述你记得的样子，回车即可搜索：\n"
            "· 时间：今天 / 昨天 / 前几天下载 / 上周 / 最近三天 / 上个月 / 去年 / 两年前\n"
            "· 类型：PDF / Word / Excel / PPT / 图片 / 视频 / 音频 / 压缩包 / 安装包 / 代码\n"
            "· 大小：大概 3MB / 超过 500MB / 小于 1MB / 1–10MB\n"
            "· 位置：下载目录 / 桌面 / 文档目录 / D 盘 / 具体路径\n"
            "· 名称：中文名、英文名（photoshop）、拼音（jianli 可召回“简历”）、"
            "带连字符或下划线（project-notes 也匹配 projectnotes）\n"
            "· 记不清名字？写句大白话点「AI 解析」，让 AI 分析这是什么并直接搜出相关文件\n"
            "· 想按文件内容找？在 设置→维护 开启「正文内容检索」"
        )
        self.nl_edit.setClearButtonEnabled(True)
        self.search_btn = QPushButton("搜索")
        self.search_btn.setObjectName("primary")
        self.ai_btn = QPushButton("AI 解析")
        self.ai_btn.setToolTip(
            "把你的描述发给 AI 去分析“这是什么”，据此直接在本机搜索相关文件\n"
            "（默认关闭；只发送描述文字，不上传任何文件名/路径/索引）"
        )
        self.filter_toggle = QPushButton("筛选")
        self.filter_toggle.setCheckable(True)
        search_row.addWidget(self.nl_edit, 1)
        search_row.addWidget(self.search_btn)
        search_row.addWidget(self.ai_btn)
        search_row.addWidget(self.filter_toggle)
        lay.addLayout(search_row)

        # 扫描未完成警告条：部分索引目录扫描被中断/未完成时显示
        self.inc_bar = QFrame()
        self.inc_bar.setObjectName("hintBar")
        inc_lay = QHBoxLayout(self.inc_bar)
        inc_lay.setContentsMargins(10, 6, 10, 6)
        self.inc_label = QLabel("")
        self.inc_label.setWordWrap(True)
        self.inc_rescan_btn = QPushButton("重新扫描")
        self.inc_rescan_btn.setObjectName("primary")
        self.inc_rescan_btn.clicked.connect(self.rescan_all)
        inc_lay.addWidget(self.inc_label, 1)
        inc_lay.addWidget(self.inc_rescan_btn)
        self.inc_bar.hide()
        lay.addWidget(self.inc_bar)

        # 解析条件条
        self.cond_frame = QFrame()
        self.cond_frame.setObjectName("condBar")
        cond_lay = QVBoxLayout(self.cond_frame)
        cond_lay.setContentsMargins(10, 8, 10, 8)
        self.cond_hint = QLabel("")
        self.cond_hint.setObjectName("sub")
        self.cond_hint.setWordWrap(True)
        self.cond_hint.hide()
        cond_lay.addWidget(self.cond_hint)
        chips_row = QHBoxLayout()
        chips_row.addWidget(QLabel("已识别条件："))
        self.chips_box = QHBoxLayout()
        chips_row.addLayout(self.chips_box, 1)
        self.edit_filters_btn = QPushButton("在筛选器中修改")
        self.edit_filters_btn.setObjectName("ghost")
        self.confirm_search_btn = QPushButton("确认并搜索")
        self.confirm_search_btn.setObjectName("primary")
        chips_row.addWidget(self.edit_filters_btn)
        chips_row.addWidget(self.confirm_search_btn)
        cond_lay.addLayout(chips_row)
        self.cond_frame.hide()
        lay.addWidget(self.cond_frame)

        # 筛选面板
        self.filter_panel = FilterPanel(max_results=self.cfg.get("max_results"))
        self.filter_panel.hide()
        lay.addWidget(self.filter_panel)

        # 扫描进度条
        self.scan_frame = QFrame()
        scan_lay = QHBoxLayout(self.scan_frame)
        scan_lay.setContentsMargins(0, 0, 0, 0)
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)  # 忙碌指示
        self.scan_label = QLabel("准备扫描…")
        self.scan_label.setObjectName("sub")
        self.pause_btn = QPushButton("暂停")
        self.cancel_scan_btn = QPushButton("取消扫描")
        self.cancel_scan_btn.setObjectName("danger")
        scan_lay.addWidget(self.scan_progress, 1)
        scan_lay.addWidget(self.scan_label, 2)
        scan_lay.addWidget(self.pause_btn)
        scan_lay.addWidget(self.cancel_scan_btn)
        self.scan_frame.hide()
        lay.addWidget(self.scan_frame)

        # 主体：空状态 / 结果
        self.stack = QStackedWidget()
        self.empty_page = self._build_empty_page()
        self.results_view = ResultsView()
        self.stack.addWidget(self.empty_page)
        self.stack.addWidget(self.results_view)
        lay.addWidget(self.stack, 1)

        # 状态栏
        self.index_stats = QLabel("")
        self.index_stats.setObjectName("sub")
        self.statusBar().addPermanentWidget(self.index_stats)

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addStretch(2)
        title = QLabel("还没有索引任何目录")
        title.setObjectName("hero")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel(
            "LostFile 只搜索你主动选择的目录，不会自动扫描整个磁盘。\n"
            "先选择要索引的目录，扫描完成后就可以按记忆中的特征找文件了。"
        )
        sub.setObjectName("sub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(sub)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.wizard_btn = QPushButton("重新打开引导")
        self.wizard_btn.setObjectName("primary")
        self.add_root_btn = QPushButton("直接添加目录…")
        btn_row.addWidget(self.wizard_btn)
        btn_row.addWidget(self.add_root_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        lay.addStretch(3)
        return page

    # ---------- 信号连接 ----------

    def _wire(self):
        self.search_btn.clicked.connect(self.on_nl_search)
        self.nl_edit.returnPressed.connect(self.on_nl_search)
        self.ai_btn.clicked.connect(self._on_ai_parse)
        self.filter_toggle.toggled.connect(self.filter_panel.setVisible)
        self.filter_panel.search_requested.connect(self._on_panel_search)
        self.confirm_search_btn.clicked.connect(self._confirm_conditions)
        self.edit_filters_btn.clicked.connect(self._edit_in_filters)
        self.wizard_btn.clicked.connect(self._run_wizard)
        self.add_root_btn.clicked.connect(self._open_settings_add_root)
        self.rescan_action.triggered.connect(self.rescan_all)
        self.recent_action.triggered.connect(self._open_recent)
        self.favorites_action.triggered.connect(self._open_favorites)
        self.dup_action.triggered.connect(self._open_duplicates)
        self.recycle_action.triggered.connect(self._open_recycle_bin)
        self.theme_action.triggered.connect(self._toggle_theme)
        self.settings_action.triggered.connect(self._open_settings)

        self.results_view.open_requested.connect(self._on_open)
        self.results_view.reveal_requested.connect(self._on_reveal)
        self.results_view.copy_requested.connect(self._on_copy)
        self.results_view.properties_requested.connect(self._on_properties)
        self.results_view.favorite_toggled.connect(self._on_favorite)
        self.results_view.limit_changed.connect(self._on_limit_changed)
        self.results_view.online_search_toggled.connect(self._on_online_toggled)
        self.results_view.open_url_requested.connect(self._open_url)
        self.results_view.delete_requested.connect(self._on_delete)
        self.results_view.copy_name_requested.connect(self._on_copy_name)
        self.results_view.load_more_requested.connect(self._load_more)

        self.scan_controller.progress.connect(self._on_scan_progress)
        self.scan_controller.root_finished.connect(self._on_root_finished)
        self.scan_controller.finished.connect(self._on_scan_all_done)
        self.scan_controller.crashed.connect(self._on_scan_crashed)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.cancel_scan_btn.clicked.connect(self.scan_controller.cancel)

        self._refresh_completer()
        self._install_shortcuts()
        self._restore_layout()

        # 占位提示词轮换（仅当输入框为空时切换，不打扰输入）
        self._hint_timer = QTimer(self)
        self._hint_timer.setInterval(4500)
        self._hint_timer.timeout.connect(self._rotate_hint)
        self._hint_timer.start()

    def _rotate_hint(self):
        if not self.nl_edit.text().strip():
            self._hint_index = (self._hint_index + 1) % len(self._hints)
            self.nl_edit.setPlaceholderText(self._hints[self._hint_index])

    # ---------- 快捷键与布局记忆 ----------

    def _install_shortcuts(self):
        """常用快捷键：Ctrl+F 搜索、F5 重扫、Ctrl+L 清空；
        结果表内：Enter 打开、Delete 删除到回收站、Ctrl+C 复制路径。"""
        for key, slot in (
            (QKeySequence("Ctrl+F"), lambda: self.nl_edit.setFocus()),
            (QKeySequence("Ctrl+L"), lambda: self.nl_edit.clear()),
            (QKeySequence("F5"), self.rescan_all),
            (QKeySequence("Ctrl+Shift+F"), self._toggle_filters),
        ):
            sc = QShortcut(key, self)
            sc.activated.connect(slot)

        table = self.results_view.table
        QShortcut(QKeySequence(Qt.Key.Key_Return), table,
                  activated=lambda: self.results_view._emit("open"))
        QShortcut(QKeySequence(Qt.Key.Key_Delete), table,
                  activated=self._delete_current_row)
        QShortcut(QKeySequence.StandardKey.Copy, table,
                  activated=lambda: self.results_view._emit("copy"))

    def _toggle_filters(self):
        self.filter_toggle.setChecked(not self.filter_toggle.isChecked())

    def _delete_current_row(self):
        row = self.results_view.row_dict()
        if row:
            self._on_delete(row["path"])

    def _restore_layout(self):
        """恢复上次的窗口大小、结果区分栏与列宽、上次搜索词。"""
        geo = self.cfg.get("window_geometry")
        if geo:
            try:
                import base64
                self.restoreGeometry(base64.b64decode(geo.encode("ascii")))
            except Exception:
                pass
        self.results_view.apply_layout_state(self.cfg.get("results_layout"))
        last_text = str(self.cfg.get("last_query_text") or "")
        if last_text:
            self.nl_edit.setText(last_text)

    def _save_layout(self):
        try:
            import base64
            self.cfg.set("window_geometry",
                         base64.b64encode(bytes(self.saveGeometry()))
                         .decode("ascii"))
        except Exception:
            pass
        self.cfg.set("results_layout", self.results_view.layout_state())
        self.cfg.set("last_query_text", self.nl_edit.text().strip())
        self.cfg.save()

    # ---------- 自然语言搜索流程 ----------

    def on_nl_search(self):
        text = self.nl_edit.text().strip()
        if not text:
            return
        res = nl.parse(text)
        self._active_conditions = list(res.conditions)
        if res.conditions:
            self.db.add_history(text)
            self._refresh_completer()
            self._rebuild_chips()
            self.cond_hint.setText(
                "未能理解的部分已忽略：" + "、".join(res.unrecognized)
                if res.unrecognized else ""
            )
            self.cond_hint.setVisible(bool(res.unrecognized))
            self.cond_frame.show()
        else:
            self.cond_frame.hide()
            QMessageBox.information(
                self, "没能理解这条描述",
                f"抱歉，没能从「{text}」中识别出可用的搜索条件。\n\n"
                "可以试试这些说法：\n"
                "  · 昨天的 PDF，大概 3MB\n"
                "  · 上周修改的图片\n"
                "  · 下载目录里超过 100MB 的视频\n"
                "  · 最近一个月创建的 Word 文档\n\n"
                "也可以点击右上角“筛选”，用多条件筛选面板直接搜索。"
            )

    def _rebuild_chips(self):
        # 清空旧 chips
        while self.chips_box.count():
            item = self.chips_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        def make_chip(idx: int, cond: dict) -> QPushButton:
            chip = QPushButton(cond.get("label", "?") + "  ✕")
            chip.setObjectName("chip")
            chip.setToolTip("点击移除此条件")
            chip.clicked.connect(lambda _c=False: self._remove_chip(idx))
            return chip

        for i, cond in enumerate(self._active_conditions):
            self.chips_box.addWidget(make_chip(i, cond))
        self.chips_box.addStretch(1)

    def _remove_chip(self, idx: int):
        if 0 <= idx < len(self._active_conditions):
            self._active_conditions.pop(idx)
            self._rebuild_chips()
            if not self._active_conditions:
                self.cond_frame.hide()

    def _confirm_conditions(self):
        q = nl.conditions_to_query(
            self._active_conditions, limit=self.cfg.get("max_results")
        )
        self._execute_query(q)

    def _on_ai_parse(self):
        from .. import ai as aimod
        text = self.nl_edit.text().strip()
        if not text:
            self.statusBar().showMessage("请先输入描述，再点「AI 解析」", 4000)
            return
        if not self.cfg.get("ai_consent"):
            answer = QMessageBox.question(
                self, "开启 AI 解析？",
                "点「AI 解析」会把【你输入的这段描述文字】发送给你自己配置的\n"
                "AI 服务（OpenAI 兼容端点），让它判断你描述的到底是什么，\n"
                "再据此在本机直接搜索相关文件。\n\n"
                "· 只发送描述文字，不上传文件名、路径、索引或任何文件内容；\n"
                "· 服务端点与 API Key 在 设置 → AI 里配置，Key 经 Windows\n"
                "  DPAPI 加密后保存在本机；\n"
                "· 解析失败、超时或返回异常时，自动回退本地规则解析并如实提示。\n\n"
                "是否开启？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.cfg.set("ai_consent", True)
            self.cfg.set("ai_enabled", True)
            self.cfg.save()
        if not (str(self.cfg.get("ai_base_url") or "").strip()
                and str(self.cfg.get("ai_model") or "").strip()):
            QMessageBox.information(
                self, "未配置 AI 服务",
                "请先到 设置 → AI 填写服务端点与模型名称。\n"
                "（默认已填 DeepSeek 官方端点，通常只需补上 API Key；\n"
                "用本地 Ollama 可填 http://localhost:11434/v1，Key 留空。）"
            )
            return
        self.ai_btn.setEnabled(False)
        self.statusBar().showMessage("正在联网分析你的描述…")
        emitter = _Emitter(self)
        emitter.ai_result.connect(
            self._on_ai_done, Qt.ConnectionType.QueuedConnection)
        self._ai_emitter = emitter      # 保引用，防止信号对象被回收

        def worker():
            try:
                out = aimod.parse_with_llm(text, self.cfg)
            except Exception as e:
                out = (None, f"AI 解析出错：{type(e).__name__}: {e}")
            emitter.ai_result.emit(*out)

        threading.Thread(target=worker, daemon=True, name="ai-parse").start()

    def _on_ai_done(self, result, err):
        self.ai_btn.setEnabled(True)
        text = self.nl_edit.text().strip()
        if err:
            # 如实提示并回退本地规则解析，然后直接搜索（与「搜索」按钮同一路径）
            fallback = nl.parse(text)
            if fallback.conditions:
                self._active_conditions = list(fallback.conditions)
                self.db.add_history(text)
                self._refresh_completer()
                self._rebuild_chips()
                self.cond_hint.setText(f"{err}（已按本地规则解析并直接搜索）")
                self.cond_hint.setVisible(True)
                self.cond_frame.show()
                self._confirm_conditions()
            else:
                QMessageBox.information(
                    self, "AI 解析未成功",
                    f"{err}\n\n本地规则解析也没有得到可用条件，"
                    "可以换种说法，或点右上角「筛选」用筛选面板搜索。"
                )
            self.statusBar().showMessage("AI 解析失败，已回退本地规则", 5000)
            return
        self._active_conditions = list(result.get("conditions") or [])
        understanding = str(result.get("understanding") or "").strip()
        names = list(result.get("names") or [])
        self.db.add_history(text)
        self._refresh_completer()
        self._rebuild_chips()
        # 如实展示 AI 对“这是什么”的判断，并说明已据此直接检索（不再需要手点确认）
        hint = "AI 分析：" + (understanding
                              or "（未能确定具体对象，按描述中的名称检索）")
        if names:
            hint += "；已按相关名称直接搜索：" + "、".join(names)
        hint += "（不满意可在上方条件里调整后重新搜索）"
        self.cond_hint.setText(hint)
        self.cond_hint.setVisible(True)
        self.cond_frame.show()
        self.statusBar().showMessage("AI 分析完成，已直接搜索", 6000)
        self._confirm_conditions()

    def _edit_in_filters(self):
        if not self._active_conditions:
            return
        self.filter_toggle.setChecked(True)
        self.filter_panel.show()
        self.filter_panel.set_from_conditions(self._active_conditions)
        # 面板接管条件编辑：隐藏“确认并搜索”标签条，避免两个条件来源并存混淆
        self.cond_frame.hide()
        self.statusBar().showMessage(
            "条件已填入筛选面板，调整后点击面板里的「搜索」执行", 5000
        )

    def _on_panel_search(self, q: qmod.SearchQuery):
        """筛选面板发起的搜索：面板条件为唯一来源。"""
        self.cond_frame.hide()
        self._execute_query(q, auto=False)

    def _on_limit_changed(self, limit: int):
        """用户切换“显示数量”：保存设置并用当前条件重新搜索。"""
        self.cfg.set("result_limit", limit)
        self.cfg.save()
        if self._last_query is not None:
            self._execute_query(self._last_query, auto=self._last_auto)
        else:
            self.statusBar().showMessage(
                "显示数量已更新，下次搜索生效", 4000)

    def _open_url(self, url: str):
        QDesktopServices.openUrl(QUrl(url))

    def _on_online_toggled(self, enabled: bool):
        """「在线搜索 GitHub」开关：首次开启需确认隐私说明。"""
        if enabled and not self.cfg.get("github_consent"):
            self.results_view.online_check.blockSignals(True)
            self.results_view.online_check.setChecked(False)
            self.results_view.online_check.blockSignals(False)
            answer = QMessageBox.question(
                self, "开启在线搜索 GitHub？",
                "开启后，搜索时会把【你输入的关键词】发送到 GitHub 公开\n"
                "搜索接口（api.github.com），用于查找同名仓库。\n\n"
                "· 不会上传本地文件名、路径或索引内容；\n"
                "· 仅在有搜索关键词时才发起联网请求；\n"
                "· 网络不可用时自动跳过，本地搜索不受影响。\n\n"
                "是否开启？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.cfg.set("github_consent", True)
        self.cfg.set("github_search", bool(enabled))
        self.cfg.save()
        if enabled and self._last_query is not None:
            self._execute_query(self._last_query, auto=self._last_auto)

    # ---------- 搜索执行（后台线程） ----------

    def _execute_query(self, q: qmod.SearchQuery, auto: bool = False):
        if q.is_empty() and not auto:
            answer = QMessageBox.question(
                self, "显示全部文件",
                "未设置任何条件，将按最近修改排序显示已索引文件"
                "（显示数量由结果区右上角「显示数量」控制）。继续吗？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if self._searching:
            # 不打断进行中的搜索：排队等它完成后再自动执行
            self._pending_query = (q, auto)
            self.statusBar().showMessage("搜索中…完成后自动继续", 3000)
            return
        self._pending_query = None
        self._last_query = q
        self._last_auto = auto
        self._generation += 1
        gen = self._generation
        self._searching = True
        self.search_btn.setEnabled(False)
        self.statusBar().showMessage("正在搜索…")
        # 本次查询开始时是否存在“扫描未完成”的根目录（用于结果引导）
        scan_gap = (bool(self._incomplete_roots())
                    and not self.scan_controller.is_scanning())

        # 显示数量由界面上的下拉框决定：>0 时只取该数量；“不限”(0) 时按页
        # 加载（先渲染一页，其余交给「加载更多」逐页追加，最终可看到全部）。
        user_limit = int(self.results_view.limit_combo.currentData() or 0)
        page_size = user_limit if user_limit > 0 else PAGE_SIZE
        self._page_size = page_size

        emitter = _Emitter(self)
        self._search_emitter = emitter
        emitter.done.connect(self._on_search_done, Qt.ConnectionType.QueuedConnection)
        emitter.failed.connect(self._on_search_failed, Qt.ConnectionType.QueuedConnection)

        def work():
            try:
                conn = self.db.connect()
                try:
                    q2 = dataclasses.replace(q, limit=page_size, offset=0)
                    rows = qmod.search(conn, q2)
                    total = qmod.count(conn, q2)
                    extra: dict = {"label": None, "suggestions": [],
                                   "note": None, "summary": None,
                                   "more_available": False, "groups": []}
                    # 相关文件分组（纯本地）：按关键词名称变体 + 拼音近似聚合。
                    # 分组树仍按“显示数量”控制条数（不限时用较大的上限）。
                    group_cap = user_limit if user_limit > 0 else 50000
                    groups: list[dict] = []
                    if q2.keywords:
                        groups = qmod.related_groups(conn, q2.keywords,
                                                     cap=group_cap)
                    # 正文内容检索（可选、默认关闭）：命中作为分组树最顶上一组，
                    # 悬浮显示正文片段；不混入正式结果表（正式表口径仍是
                    # 文件名/路径/条件）。2 字中文无法参与正文检索（trigram
                    # 限制），在设置页有说明，不在此重复提示。
                    if bool(self.cfg.get("content_search_enabled")) \
                            and q2.keywords:
                        hits = qmod.content_search(conn, q2.keywords,
                                                   cap=group_cap)
                        if hits and hits["files"]:
                            groups.insert(0, {
                                "title": "内容命中（正文包含关键词）",
                                "subtitle": (
                                    "这些文件的正文包含你的关键词"
                                    "（仅纯文本文件，最多索引前 16KB，"
                                    "跳过 node_modules 等依赖目录；"
                                    "悬停文件可看命中片段）"
                                ),
                                "total": hits["total"],
                                "files": hits["files"],
                            })
                    # 本地语义向量（可选、默认关闭）：文件名语义相近的文件，
                    # 即使名称里没有关键词也可能召回；与内容命中同样只进
                    # 分组树，不污染正式结果表。
                    if bool(self.cfg.get("vec_enabled")) and q2.keywords \
                            and vecmm.model_present(self.cfg):
                        try:
                            embedder = vecmm.get_embedder(self.cfg)
                            if embedder is not None:
                                searcher = vecmod.VectorBackfill(
                                    self.db, self.cfg, embedder=embedder)
                                sims = searcher.semantic_search(
                                    " ".join(q2.keywords),
                                    k=min(group_cap, vecmod.TOP_K))
                                if sims:
                                    id_marks = ",".join("?" for _ in sims)
                                    sim_map = dict(sims)
                                    sem_rows = conn.execute(
                                        "SELECT id, path, name, ext, size,"
                                        " mtime, ctime, parent FROM files"
                                        f" WHERE id IN ({id_marks})",
                                        [fid for fid, _s in sims],
                                    ).fetchall()
                                    sem_files = [
                                        {"path": r[1], "name": r[2],
                                         "ext": r[3], "size": r[4],
                                         "mtime": r[5], "ctime": r[6],
                                         "parent": r[7],
                                         "similarity": round(sim_map.get(r[0], 0.0), 2)}
                                        for r in sem_rows
                                    ]
                                    if sem_files:
                                        groups.insert(0, {
                                            "title": "语义相关（名称语义相近）",
                                            "subtitle": (
                                                "本地向量模型按语义相近度匹配"
                                                "文件名：即使文件名里没有你的"
                                                "关键词，也可能与描述相关"
                                            ),
                                            "total": len(sem_files),
                                            "files": sem_files,
                                        })
                        except Exception:
                            pass   # 语义搜索失败不影响主搜索
                    # 可选联网：GitHub 同名仓库（默认关闭，见 _on_online_toggled）
                    if bool(self.cfg.get("github_search")) and q2.keywords:
                        for repo in github.search_repos(q2.keywords[0]):
                            repo_full = repo.get("full_name") or ""
                            repo_name = repo_full.rsplit("/", 1)[-1]
                            files: list[dict] = []
                            if repo_name:
                                found = qmod.related_groups(
                                    conn, [repo_name], cap=group_cap)
                                files = found[0]["files"] if found else []
                            groups.append({
                                "title": (f"GitHub · {repo_full}"
                                          f" ★{repo.get('stars', 0)}"),
                                "subtitle": repo.get("description") or "",
                                "url": repo.get("html_url") or "",
                                "online": True,
                                "files": files,
                            })
                    extra["groups"] = groups
                    used_q: qmod.SearchQuery = q2
                    # 选“不限”且还有没加载的结果时，由「加载更多」继续取；
                    # 其余情况（固定数量、回退列表）不提供分页。
                    more_available = (user_limit == 0 and total > len(rows))
                    if not rows and q2.keywords:
                        q_kwless = qmod.without_keywords(q2)
                        if q_kwless.is_empty():
                            # 只有关键词却一无所获：给出“你是不是要找”近似候选，
                            # 列表只列最近修改的少量文件——绝不把整个索引当结果。
                            sugg = qmod.fuzzy_suggestions(conn, q2.keywords)
                            extra["suggestions"] = sugg
                            rows = qmod.search(conn, dataclasses.replace(
                                q_kwless, limit=RECENT_FALLBACK, offset=0))
                            total = len(rows)
                            used_q = q_kwless
                            more_available = False
                            extra["summary"] = (
                                f"未匹配到名称，以下为最近修改的文件"
                                f"（前 {total} 条）"
                            )
                            if sugg:
                                extra["label"] = (
                                    "没找到名称匹配的文件。下面给出拼写相近的候选"
                                    "（点击可打开），列表已改为显示最近修改的文件。"
                                )
                            elif groups:
                                extra["label"] = (
                                    "没找到名称完全匹配的文件。已按名称变体把"
                                    "相关文件分组列在上方（点三角展开），"
                                    "列表同时显示最近修改的文件。"
                                )
                            else:
                                extra["label"] = (
                                    "没找到名称匹配的文件。已显示最近修改的文件，"
                                    "可按类型/时间/大小/位置缩小范围。若文件是"
                                    "最近下载或安装的，请点工具栏「重新扫描」"
                                    "完成后再搜一次。"
                                )
                        else:
                            # 还有其他条件：按“位置 → 时间 → 大小 → 类型 → 关键词”
                            # 逐步放宽。关键词最能反映用户想找什么，放到最后才丢：
                            # 否则“昨天下载的那个 skill”会因“昨天”判断有偏差，
                            # 先丢掉关键词、返回一堆无关文件。
                            def _drop(qq, name):
                                if name == "位置":
                                    return dataclasses.replace(qq, locations=[])
                                if name == "时间":
                                    return dataclasses.replace(
                                        qq, time_start=None, time_end=None)
                                if name == "大小":
                                    return dataclasses.replace(
                                        qq, size_min=None, size_max=None)
                                if name == "类型":
                                    return dataclasses.replace(
                                        qq, categories=[], extensions=[])
                                return dataclasses.replace(qq, keywords=[])

                            def _has(qq, name):
                                if name == "位置":
                                    return bool(qq.locations)
                                if name == "时间":
                                    return (qq.time_start is not None
                                            or qq.time_end is not None)
                                if name == "大小":
                                    return (qq.size_min is not None
                                            or qq.size_max is not None)
                                if name == "类型":
                                    return bool(qq.categories or qq.extensions)
                                return bool(qq.keywords)

                            cur = q2
                            dropped: list[str] = []
                            # 按“位置 → 时间 → 大小 → 类型”逐步放宽。关键词最能
                            # 反映用户想找什么，这里不再丢掉它：否则“丢掉关键词后
                            # 没有任何条件”会把整个索引当成结果（曾出现 166 万条）。
                            for name in ("位置", "时间", "大小", "类型"):
                                if not _has(cur, name):
                                    continue
                                cur = _drop(cur, name)
                                dropped.append(name)
                                n = qmod.count(conn, cur)
                                if n:
                                    rows = qmod.search(conn, dataclasses.replace(
                                        cur, limit=page_size, offset=0))
                                    total = n
                                    break
                            # 若上面哪一级都没命中，rows 仍为空 → 走无结果分支
                            used_q = cur
                            more_available = False
                            if dropped:
                                extra["note"] = ("关键词未命中，已逐步放宽条件："
                                                 + "、".join(dropped))
                            if rows:
                                extra["label"] = (
                                    "没有名称完全匹配的文件；已放宽条件（忽略"
                                    + "、".join(dropped) + f"）显示 {total} 条。"
                                    "关键词命中的文件仍列在上方“相关文件”分组。"
                                )
                            else:
                                sugg = qmod.fuzzy_suggestions(conn, q2.keywords)
                                extra["suggestions"] = sugg
                                if sugg:
                                    extra["label"] = (
                                        "没有名称匹配的文件，放宽条件后仍无结果。"
                                        "下面给出拼写相近的候选（点击可打开）。"
                                    )
                                else:
                                    extra["label"] = (
                                        "没有名称匹配的文件，放宽全部条件后仍"
                                        "无结果。若文件是最近下载或安装的，"
                                        "请点工具栏「重新扫描」完成后再搜一次。"
                                    )
                    extra["more_available"] = more_available
                    if auto and not extra["label"]:
                        extra["label"] = (f"未设置条件，按最近修改排序显示"
                                         f"（共 {total} 条）")
                    # 扫描未完成指引：无结果时提示用户重新扫描
                    if scan_gap and not rows:
                        extra["label"] = (
                            (extra["label"] + "　" if extra["label"] else "")
                            + "另外：检测到部分索引目录扫描未完成（此前有中断），"
                            "最近新增的文件可能还没被收录，建议点工具栏"
                            "「重新扫描」完成后再搜。"
                        )
                    if q2.keywords and scan_gap and groups and all(
                        not g.get("files") for g in groups
                    ):
                        groups.append({
                            "title": "提示：本地索引未找到",
                            "note": (
                                "已索引文件中没有与关键词相关的文件。检测到部分"
                                "索引目录扫描未完成，最近下载/安装的内容可能"
                                "还没被收录：点工具栏「重新扫描」完成后再搜一次。"
                                "也可勾选右上角「在线搜索 GitHub」在线查找同名仓库。"
                            ),
                            "files": [],
                        })
                finally:
                    conn.close()
                favorites = {f["path"] for f in self.db.list_favorites()}
                for r in rows:
                    r["exists"] = fileops.exists_file(r["path"])
                    r["reasons"] = qmod.match_reasons(used_q, r)
                    if used_q.keywords:
                        # 每条结果说明关键词命中位置（文件名/路径/读音近似）
                        r["reasons"][:0] = qmod.keyword_match_locations(
                            used_q.keywords, r)
                    if extra["note"]:
                        r["reasons"].insert(0, "· " + extra["note"])
                    r["favorite"] = r["path"] in favorites
                emitter.done.emit(rows, total, gen, extra)
            except Exception as e:
                emitter.failed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_search_done(self, rows, total, gen, extra):
        if gen != self._generation:
            return
        self._searching = False
        self.search_btn.setEnabled(True)
        extra = extra or {}
        self._loaded_rows = list(rows)
        self._more_available = bool(extra.get("more_available"))
        self.results_view.set_results(
            rows, total, truncated=total > len(rows),
            label=extra.get("label"),
            suggestions=extra.get("suggestions") or [],
            more_available=self._more_available,
            summary=extra.get("summary"),
        )
        self.results_view.set_groups(extra.get("groups") or [])
        self.stack.setCurrentWidget(self.results_view)
        self.statusBar().showMessage(f"搜索完成，共 {total} 条结果", 5000)
        self._drain_pending_search()

    def _load_more(self):
        """「加载更多」：追加下一页结果（仅选“不限”且仍有剩余时可用）。"""
        if self._searching or not self._more_available \
                or self._last_query is None:
            return
        q = self._last_query
        offset = len(self._loaded_rows)
        page_size = self._page_size
        gen = self._generation
        self._searching = True
        self.search_btn.setEnabled(False)
        self.statusBar().showMessage("正在加载更多…")
        emitter = _Emitter(self)
        self._search_emitter = emitter
        emitter.more_done.connect(self._on_more_done,
                                  Qt.ConnectionType.QueuedConnection)
        emitter.failed.connect(self._on_search_failed,
                               Qt.ConnectionType.QueuedConnection)

        def work():
            try:
                conn = self.db.connect()
                try:
                    q2 = dataclasses.replace(q, limit=page_size, offset=offset)
                    rows = qmod.search(conn, q2)
                    total = qmod.count(conn, q2)
                finally:
                    conn.close()
                favorites = {f["path"] for f in self.db.list_favorites()}
                for r in rows:
                    r["exists"] = fileops.exists_file(r["path"])
                    r["reasons"] = qmod.match_reasons(q, r)
                    if q.keywords:
                        r["reasons"][:0] = qmod.keyword_match_locations(
                            q.keywords, r)
                    r["favorite"] = r["path"] in favorites
                emitter.more_done.emit(rows, total, gen)
            except Exception as e:
                emitter.failed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_more_done(self, rows, total, gen):
        if gen != self._generation:
            return
        self._searching = False
        self.search_btn.setEnabled(True)
        self._loaded_rows.extend(rows)
        self._more_available = bool(rows) and total > len(self._loaded_rows)
        self.results_view.append_results(rows, total, self._more_available)
        self.statusBar().showMessage(
            f"已加载 {len(self._loaded_rows)} / {total} 条", 5000)

    def _drain_pending_search(self):
        if not self._searching and self._pending_query is not None:
            q, auto = self._pending_query
            self._pending_query = None
            self._execute_query(q, auto=auto)

    def _on_search_failed(self, msg):
        self._searching = False
        self.search_btn.setEnabled(True)
        self.statusBar().showMessage(f"搜索失败：{msg}", 8000)
        QMessageBox.warning(self, "搜索失败", f"查询出错：{msg}")
        self._drain_pending_search()

    def _refresh_completer(self):
        try:
            words = self.db.recent_history(15)
        except Exception:
            words = []
        comp = QCompleter(words, self.nl_edit)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        self.nl_edit.setCompleter(comp)

    # ---------- 文件操作 ----------

    def _on_open(self, path: str):
        if not fileops.open_file(path):
            QMessageBox.information(self, "无法打开", "文件不存在或已被移动。")

    def _on_reveal(self, path: str):
        if not fileops.reveal_file(path):
            QMessageBox.information(self, "无法定位", "文件不存在或已被移动。")

    def _on_copy(self, path: str):
        QApplication.clipboard().setText(path)
        self.statusBar().showMessage("已复制路径到剪贴板", 3000)

    def _on_copy_name(self, path: str):
        QApplication.clipboard().setText(os.path.basename(path))
        self.statusBar().showMessage("已复制文件名到剪贴板", 3000)

    def _on_properties(self, path: str):
        if not fileops.show_properties(path, int(self.winId())):
            QMessageBox.information(self, "无法查看属性", "文件不存在或已被移动。")

    def _on_favorite(self, path: str):
        if self.db.is_favorite(path):
            self.db.remove_favorite(path)
            self.statusBar().showMessage("已取消收藏", 3000)
        else:
            self.db.add_favorite(path)
            self.statusBar().showMessage("已加入收藏", 3000)

    # ---------- 删除到回收站 ----------

    def _confirm_delete(self, path: str) -> bool:
        """确认是否把文件删除到回收站（单独方法，便于测试覆写）。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("删除到回收站")
        box.setText(f"确定把「{os.path.basename(path)}」删除到回收站吗？")
        box.setInformativeText(
            f"{path}\n\n"
            "文件会移入 Windows 回收站，可随时还原（不会永久删除）；\n"
            "同时会把它从搜索索引中移除。"
        )
        delete_btn = box.addButton("删除到回收站",
                                   QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)   # 默认“取消”，防误触回车
        box.exec()
        return box.clickedButton() is delete_btn

    def _on_delete(self, path: str):
        """两个结果栏的右键“删除到回收站”统一入口：先确认，再执行。"""
        if not self._confirm_delete(path):
            return
        if not fileops.delete_to_recycle_bin(path):
            QMessageBox.warning(
                self, "删除失败",
                "无法删除这个文件：可能已被移动、正在被其他程序占用，"
                "或没有权限。"
            )
            return
        self.db.remove_file(path)
        self.statusBar().showMessage(
            f"已把「{os.path.basename(path)}」删除到回收站"
            "（可在工具栏「回收站」中还原）", 8000
        )
        # 重新执行当前查询：结果表与上方分组都会同步更新
        if self._last_query is not None:
            self._execute_query(self._last_query, auto=self._last_auto)

    # ---------- 扫描 ----------

    def rescan_all(self):
        roots = self.db.list_roots()
        if not roots:
            QMessageBox.information(self, "提示", "还没有索引目录，请先添加（设置 → 索引目录）。")
            return
        self._start_scan(roots)

    def _start_scan(self, roots: list[dict]):
        if self.scan_controller.is_scanning():
            self.statusBar().showMessage("正在扫描中", 3000)
            return
        ok = self.scan_controller.start(
            roots,
            exclude_dirs=set(self.cfg.get("exclude_dirs") or ()),
            exclude_exts=set(e.lower().lstrip(".")
                             for e in (self.cfg.get("exclude_exts") or ())),
        )
        if ok:
            self.scan_frame.show()
            self.scan_label.setText(f"正在扫描：{roots[0].get('display_name', '')}…")
            self._paused = False
            self.pause_btn.setText("暂停")
            self.statusBar().showMessage("开始在后台扫描…", 3000)

    def _on_scan_progress(self, name, dirs, files, errors):
        self.scan_label.setText(
            f"正在扫描 {name}：已遍历 {dirs} 个目录，"
            f"{files} 个文件，错误 {errors}"
        )

    def _on_root_finished(self, root_id, completed, errors):
        verb = "完成" if completed else "已中止"
        self.statusBar().showMessage(
            f"目录扫描{verb}，错误 {errors} 项", 5000
        )
        self.refresh_ui_state()

    def _on_scan_all_done(self):
        self.scan_frame.hide()
        self.refresh_ui_state()
        self.statusBar().showMessage("扫描全部结束", 5000)
        # 扫描可能新增/更新了大量行：有残留且开关开启时启动关键词 FTS 追赶
        self._sync_fts_worker()
        self._sync_content_worker()
        self._sync_hash_worker()
        self._sync_vec_worker()

    def _sync_fts_worker(self):
        # 关键词 FTS 加速开关（默认开）与后台追赶任务联动
        if bool(self.cfg.get("fts_enabled")) and self.db.has_fts_pending():
            self._fts_backfill.start()

    def _sync_content_worker(self):
        # 内容检索开关与后台提取任务联动（开启且有残留 → 启动）
        if bool(self.cfg.get("content_search_enabled")) \
                and self.db.has_content_pending():
            self._content_backfill.start()

    def _sync_hash_worker(self):
        # 重复检测开关与后台哈希任务联动（开启且有残留 → 启动）
        if bool(self.cfg.get("dup_detect_enabled")) \
                and self.db.has_hash_pending():
            self._hash_backfill.start()

    def _sync_vec_worker(self):
        # 语义向量开关与后台向量化任务联动（开启 + 模型已下载 + 有残留）
        if bool(self.cfg.get("vec_enabled")) \
                and vecmm.model_present(self.cfg) \
                and self.db.has_vec_pending():
            self._vec_backfill.start()

    def _scan_error_box(self, title: str, message: str, current_dir: str,
                        informative: str = "") -> None:
        """扫描出错的统一弹窗：提供“该目录加入排除并重试 / 知道了”。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        if informative:
            box.setInformativeText(informative)
        retry_btn = box.addButton(
            "将该目录加入排除并重试", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("知道了", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is retry_btn and current_dir:
            dirs = list(self.cfg.get("exclude_dirs") or [])
            if current_dir not in dirs:
                dirs.append(current_dir)
                self.cfg.set("exclude_dirs", dirs)
                self.cfg.save()
            self.rescan_all()

    def _on_scan_crashed(self, message: str):
        """扫描子进程系统级崩溃：主程序不受影响，给出排除建议。"""
        self.scan_frame.hide()
        self.refresh_ui_state()
        st = self.scan_controller._read_json(
            self.scan_controller._state_path(), {})
        current = str(st.get("current_dir", ""))
        self._scan_error_box(
            "扫描意外终止", message, current,
            "主程序未受影响，你可以继续搜索。",
        )

    def _toggle_pause(self):
        if self._paused:
            self.scan_controller.resume()
            self._paused = False
            self.pause_btn.setText("暂停")
        else:
            self.scan_controller.pause()
            self._paused = True
            self.pause_btn.setText("继续")

    # ---------- 其它入口 ----------

    def _maybe_wizard(self):
        if not self.cfg.get("wizard_completed") or not self.db.list_roots():
            self._run_wizard()

    def _run_wizard(self):
        wiz = FirstRunWizard(self)
        if wiz.exec() and wiz.selected_paths():
            for p in wiz.selected_paths():
                self.db.add_root(p)
            self.cfg.set("wizard_completed", True)
            self.cfg.save()
            self.refresh_ui_state()
            roots = self.db.list_roots()
            self._start_scan(roots)

    def _open_settings_add_root(self):
        self._open_settings()

    def _open_settings(self):
        dlg = SettingsDialog(self.cfg, self.db, self)
        dlg.scan_requested.connect(self._start_scan)
        dlg.roots_changed.connect(self._on_roots_changed)
        dlg.theme_changed.connect(self._apply_theme)
        dlg.content_changed.connect(self._sync_content_worker)
        dlg.hash_changed.connect(self._sync_hash_worker)
        dlg.vec_changed.connect(self._sync_vec_worker)
        dlg.fts_changed.connect(self._sync_fts_worker)
        dlg.exec()

    def _on_roots_changed(self):
        self.refresh_ui_state()

    def _open_recent(self):
        dlg = RecentDialog(self.cfg, self)
        dlg.exec()

    def _open_favorites(self):
        dlg = FavoritesDialog(self.db, self)
        dlg.exec()

    def _open_duplicates(self):
        from ..ui.duplicatesdialog import DuplicatesDialog
        dlg = DuplicatesDialog(self.cfg, self.db,
                               worker=self._hash_backfill, parent=self)
        dlg.exec()

    def _open_recycle_bin(self):
        if not fileops.open_recycle_bin():
            QMessageBox.information(self, "无法打开回收站",
                                    "未能打开系统回收站窗口。")

    def _apply_theme(self, mode: str):
        app = QApplication.instance()
        actual = apply_theme(app, mode)
        self.theme_action.setText("深色模式" if actual == "dark" else "浅色模式")

    def _toggle_theme(self):
        app = QApplication.instance()
        current = resolve_mode(self.cfg.get("theme"), app)
        new = "dark" if current == "light" else "light"
        self.cfg.set("theme", new)
        self.cfg.save()
        self._apply_theme(new)

    def _incomplete_roots(self) -> list[str]:
        """扫描状态不是 done 的根目录（未完成扫描，可能有文件漏索引）。"""
        out: list[str] = []
        for r in self.db.list_roots():
            if r.get("scan_status") != "done":
                out.append(str(r.get("display_name") or r.get("path") or "?"))
        return out

    def refresh_ui_state(self):
        roots = self.db.list_roots()
        self.filter_panel.refresh_locations(roots)
        count = self.db.count_files()
        self.index_stats.setText(
            f"索引目录 {len(roots)} 个 · 已索引 {count} 个文件"
            + (f" · 上次扫描 {self._last_scan_text(roots)}" if roots else "")
        )
        # 扫描未完成警告：非扫描进行中但仍有根目录状态未完成
        inc = self._incomplete_roots() if not self.scan_controller.is_scanning() else []
        if inc:
            self.inc_label.setText(
                "注意：部分索引目录的扫描未完成，其中的文件可能搜不到："
                + "、".join(inc)
                + "。请点击右侧按钮继续扫描。"
            )
            self.inc_bar.show()
        else:
            self.inc_bar.hide()
        if roots:
            self.stack.setCurrentWidget(self.results_view)
        else:
            self.stack.setCurrentWidget(self.empty_page)
        # 首次有索引数据时：默认浏览（最近修改的文件）+ 拼音/关键词 FTS 后台补算
        if roots and not self._auto_browsed:
            self._auto_browsed = True
            if self.db.has_pinyin_pending():
                self._pinyin_backfill.start()
            self._sync_fts_worker()
            self._sync_content_worker()
            self._sync_hash_worker()
            self._sync_vec_worker()
            QTimer.singleShot(250, self._auto_browse)

    def _auto_browse(self):
        """未输入任何条件时展示“最近修改的文件”，而不是空白页面。"""
        if self._searching or not self.db.list_roots():
            return
        q = qmod.SearchQuery(limit=int(self.cfg.get("max_results") or 500))
        self._execute_query(q, auto=True)

    @staticmethod
    def _last_scan_text(roots) -> str:
        from .. import timeutil as tu
        ts = max((r["last_scan_finished"] or 0 for r in roots), default=0)
        return tu.fmt_ts(ts)[5:16] if ts else "从未"

    def shutdown(self):
        self._save_layout()          # 记住窗口大小、分栏、列宽与上次搜索词
        self.scan_controller.shutdown()
        self._pinyin_backfill.stop()
        self._fts_backfill.stop()
        self._content_backfill.stop()
        self._hash_backfill.stop()
        self._vec_backfill.stop()
        if not self.cfg.get("keep_index_on_exit"):
            # 用户选择退出时清除索引：仅删除索引数据，不影响原始文件
            self.db.clear_index()
        self.cfg.save()