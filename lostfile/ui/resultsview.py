"""搜索结果列表 + 匹配原因详情 + 文件操作（打开/定位/复制/属性/收藏/删除）。

排序说明：不使用 Qt 原生 QTableWidget 排序（大量行时其条目比较会触发
原生崩溃——曾导致 83 万行索引上搜索后闪退），改为表头点击 -> Python 侧
对结果列表排序 -> 重新渲染，同样提供升/降序与表头箭头指示。
"""
import html
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QMenu, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import filetypes as ft
from .. import timeutil as tu


class ResultsView(QWidget):
    open_requested = Signal(str)
    reveal_requested = Signal(str)
    copy_requested = Signal(str)
    properties_requested = Signal(str)
    favorite_toggled = Signal(str)
    limit_changed = Signal(int)   # 用户切换"显示数量"：0 表示不限
    online_search_toggled = Signal(bool)  # 用户切换"在线搜索 GitHub"
    open_url_requested = Signal(str)      # 打开网页（GitHub 仓库链接）
    delete_requested = Signal(str)        # 删除到回收站（界面层负责确认）
    copy_name_requested = Signal(str)     # 复制文件名（非完整路径）
    load_more_requested = Signal()        # 选“不限”时请求追加下一页结果

    COLUMNS = ["文件名", "状态", "类型", "大小", "修改时间", "创建时间", "所在目录"]

    # 显示数量选项：(配置值, 显示文字)；0 = 不限（按页加载，可点“加载更多”取完全部）
    LIMIT_OPTIONS: list[tuple[int, str]] = [
        (500, "500 条"), (2000, "2000 条"), (0, "不限"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._sort_col = -1        # -1 表示保持结果原生相关度顺序
        self._sort_asc = True
        self._remembered_sizes: list[int] | None = None  # 上次记住的分栏高度
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setObjectName("sub")
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("在结果中筛选（按文件名或路径）")
        self.filter_edit.setClearButtonEnabled(True)
        limit_label = QLabel("显示数量")
        limit_label.setObjectName("sub")
        self.limit_combo = QComboBox()
        for value, text in self.LIMIT_OPTIONS:
            self.limit_combo.addItem(text, value)
        self.limit_combo.setToolTip(
            "控制单次搜索渲染到列表的结果数量；选“不限”时先渲染一页，\n"
            "其余可由列表下方「加载更多」按需要逐页追加，直到全部显示。"
        )
        self.limit_combo.currentIndexChanged.connect(
            lambda _i: self.limit_changed.emit(
                int(self.limit_combo.currentData() or 0))
        )
        top.addWidget(self.summary, 1)
        top.addWidget(limit_label)
        top.addWidget(self.limit_combo)
        self.online_check = QCheckBox("在线搜索 GitHub")
        self.online_check.setToolTip(
            "勾选后会把搜索关键词发送到 GitHub 公开搜索接口查找同名仓库；\n"
            "本地文件名、路径与索引内容不会上传。默认关闭。"
        )
        self.online_check.toggled.connect(self.online_search_toggled.emit)
        top.addWidget(self.online_check)
        top.addWidget(self.filter_edit, 2)
        lay.addLayout(top)

        # 信息横幅：回退说明 / “你是不是要找”近似候选（可点击打开）
        self.info_banner = QLabel("")
        self.info_banner.setWordWrap(True)
        self.info_banner.setTextFormat(Qt.TextFormat.RichText)
        self.info_banner.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.info_banner.setOpenExternalLinks(False)
        self.info_banner.linkActivated.connect(self._on_banner_link)
        self.info_banner.hide()
        lay.addWidget(self.info_banner)

        # 相关文件分组树：关键词命中的文件按组折叠，点三角展开查看
        self.group_tree = QTreeWidget()
        self.group_tree.setObjectName("groupTree")
        self.group_tree.setColumnCount(4)
        self.group_tree.setHeaderLabels(
            ["相关文件", "大小", "修改时间", "所在目录"]
        )
        self.group_tree.setRootIsDecorated(True)
        self.group_tree.setAlternatingRowColors(True)
        self.group_tree.setColumnWidth(0, 300)
        self.group_tree.setColumnWidth(1, 90)
        self.group_tree.setColumnWidth(2, 130)
        self.group_tree.header().setStretchLastSection(True)
        self.group_tree.header().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.group_tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self.group_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.group_tree.customContextMenuRequested.connect(
            self._on_tree_context_menu
        )
        self.group_tree.hide()

        # 垂直分割器：分组树 / 结果表 / 详情 三栏，均可拖动分隔手柄调整高度
        splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter = splitter
        splitter.addWidget(self.group_tree)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setObjectName("results")
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        # 关闭 Qt 原生排序：`setSortingEnabled` 的条目比较会导致闪退，
        # 排序由本类在 Python 侧完成（见模块说明）。
        self.table.setSortingEnabled(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._sort_by_column)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 130)
        self.table.setColumnWidth(5, 130)

        # 结果表 + “加载更多”：选“不限”时按页追加，避免一次性渲染海量行
        table_wrap = QWidget()
        tw = QVBoxLayout(table_wrap)
        tw.setContentsMargins(0, 0, 0, 0)
        tw.setSpacing(4)
        tw.addWidget(self.table, 1)
        self.load_more_btn = QPushButton("加载更多")
        self.load_more_btn.setToolTip(
            "继续加载下一批结果；选“不限”时可一直加载到全部显示完。"
        )
        self.load_more_btn.clicked.connect(
            lambda: self.load_more_requested.emit())
        more_row = QHBoxLayout()
        more_row.addStretch(1)
        more_row.addWidget(self.load_more_btn)
        more_row.addStretch(1)
        tw.addLayout(more_row)
        self.load_more_btn.hide()
        splitter.addWidget(table_wrap)

        details = QWidget()
        d = QVBoxLayout(details)
        d.setContentsMargins(4, 8, 4, 0)
        self.path_label = QLabel("")
        self.path_label.setObjectName("sub")
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.path_label.setWordWrap(True)
        d.addWidget(self.path_label)

        self.reason_list = QListWidget()
        self.reason_list.setMaximumHeight(110)
        d.addWidget(self.reason_list)

        btn_row = QHBoxLayout()
        self.open_btn = QPushButton("打开")
        self.open_btn.setObjectName("primary")
        self.reveal_btn = QPushButton("打开所在文件夹")
        self.copy_btn = QPushButton("复制完整路径")
        self.props_btn = QPushButton("属性")
        self.fav_btn = QPushButton("加入收藏")
        btn_row.addWidget(self.open_btn)
        btn_row.addWidget(self.reveal_btn)
        btn_row.addWidget(self.copy_btn)
        btn_row.addWidget(self.props_btn)
        btn_row.addWidget(self.fav_btn)
        btn_row.addStretch(1)
        d.addLayout(btn_row)
        d.addStretch(1)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([170, 400, 180])
        lay.addWidget(splitter, 1)

        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.filter_edit.textChanged.connect(self._apply_filter)

        self.open_btn.clicked.connect(lambda: self._emit("open"))
        self.reveal_btn.clicked.connect(lambda: self._emit("reveal"))
        self.copy_btn.clicked.connect(lambda: self._emit("copy"))
        self.props_btn.clicked.connect(lambda: self._emit("properties"))
        self.fav_btn.clicked.connect(self._toggle_favorite)
        self._set_details_enabled(False)

    # ---------- 数据 ----------

    def set_results(self, rows: list[dict], total: int, truncated: bool,
                    label: str | None = None,
                    suggestions: list[dict] | None = None,
                    more_available: bool = False,
                    summary: str | None = None):
        """渲染第一批结果。

        truncated：total 大于实际渲染行数（用于“仅显示前 N 条”提示）。
        more_available：选“不限”时还有未加载的结果，显示「加载更多」按钮。
        summary：需要覆盖默认条数文案时传入（例如回退列表）。
        """
        self._rows = list(rows)
        self._repopulate()
        # 说明文字统一走 info_banner，summary 只显示条数（避免重复）
        if summary:
            self.summary.setText(summary)
        else:
            self._update_summary(len(rows), total, truncated, more_available)
        self.load_more_btn.setVisible(bool(more_available))
        self._render_banner(label, suggestions or [])
        self._apply_filter()

    def append_results(self, new_rows: list[dict], total: int,
                       more_available: bool):
        """「加载更多」：把新一页结果追加到现有列表（不重建已有行）。"""
        start = len(self._rows)
        self._rows.extend(new_rows)
        if self._sort_col < 0:
            self._append_rows_to_table(start)
        else:
            # 当前按某列排序：新行需与已有行一起重排
            self._repopulate()
        self._update_summary(len(self._rows), total,
                             total > len(self._rows), more_available)
        self.load_more_btn.setVisible(bool(more_available))
        self._apply_filter()

    def _update_summary(self, shown: int, total: int, truncated: bool,
                        more_available: bool):
        if more_available:
            self.summary.setText(
                f"共 {total} 条结果，已显示 {shown} 条，可继续「加载更多」")
        elif truncated or total > shown:
            self.summary.setText(
                f"共 {total} 条结果，仅显示前 {shown} 条，"
                "可在右上角调整显示数量或细化条件"
            )
        else:
            self.summary.setText(f"共 {total} 条结果")

    def _render_banner(self, label: str | None, suggestions: list[dict]):
        parts: list[str] = []
        if label:
            parts.append(html.escape(label))
        if suggestions:
            links = []
            for s in suggestions:
                href = html.escape(s["path"], quote=True)
                name = html.escape(s["name"])
                links.append(
                    f'<a href="{href}" title="打开文件">{name}</a>'
                    f'<span style="color:#888">（相似 {int(s.get("similarity", 0) * 100)}%）</span>'
                )
            parts.append(
                "你是不是要找：" + "　".join(links[:6])
                + (" …" if len(links) > 6 else "")
            )
        if parts:
            self.info_banner.setText("<br>".join(parts))
            self.info_banner.show()
        else:
            self.info_banner.hide()

    def _on_banner_link(self, url: str):
        try:
            path = html.unescape(url)
        except Exception:
            path = url
        if path:
            self.open_requested.emit(path)

    def clear_results(self):
        self.set_results([], 0, False)
        self.summary.setText("")
        self.group_tree.clear()
        self.group_tree.hide()

    # ---------- 相关文件分组树 ----------

    def set_groups(self, groups: list[dict]):
        """填充可折叠分组树：每关键词/仓库一个顶层节点（默认折叠）。"""
        self.group_tree.clear()
        if not groups:
            self.group_tree.hide()
            return
        was_hidden = not self.group_tree.isVisible()
        for g in groups:
            files = g.get("files") or []
            title = g.get("title") or ""
            total = int(g.get("total", len(files)) or 0)
            shown = len(files)
            if files:
                if total > shown:
                    # 受“显示数量”限制，如实说明展示范围
                    top_text = f"{title}（显示 {shown} / 共 {total} 个文件）"
                else:
                    top_text = f"{title}（{total} 个文件）"
            elif g.get("online"):
                top_text = f"{title}（本地暂无）"
            else:
                top_text = f"{title}（本地索引 0 个文件）"
            top = QTreeWidgetItem([top_text, "", "", ""])
            tip = g.get("subtitle") or ""
            if g.get("url"):
                tip = (tip + "\n" if tip else "") + g["url"]
            if tip:
                top.setToolTip(0, tip)
            self.group_tree.addTopLevelItem(top)
            keep_tip = shown <= 2000   # 大分组跳过悬浮提示以加快渲染
            for f in files:
                child = QTreeWidgetItem([
                    f["name"], ft.human_size(f["size"]),
                    tu.fmt_ts(f["mtime"]), f.get("parent", ""),
                ])
                child.setData(0, Qt.ItemDataRole.UserRole, f["path"])
                if keep_tip:
                    tip = f.get("snippet") or f["path"]
                    if f.get("similarity") is not None and not f.get("snippet"):
                        tip = f"语义相似度 {f['similarity']}\n{f['path']}"
                    child.setToolTip(0, tip)
                top.addChild(child)
            if files and total > shown:
                top.addChild(QTreeWidgetItem([
                    "（还有更多，可在右上角调整“显示数量”或用筛选条件缩小范围）",
                    "", "", "",
                ]))
            elif not files:
                note = g.get("note") or (
                    "本地索引中未找到该名字的文件（可能未扫描到或名称不同）"
                    if g.get("online") else ""
                )
                if note:
                    top.addChild(QTreeWidgetItem([note, "", "", ""]))
                if g.get("url"):
                    link = QTreeWidgetItem(["在 GitHub 打开仓库", "", "", ""])
                    link.setData(0, Qt.ItemDataRole.UserRole, g["url"])
                    top.addChild(link)
            top.setExpanded(False)
        self.group_tree.show()
        if was_hidden:
            # 分组树从隐藏变为可见时给它一个高度：优先用上次记住的分栏，
            # 否则用默认值（否则会覆盖掉用户记住的布局）。
            self._splitter.setSizes(self._remembered_sizes or [170, 400, 180])

    def _on_tree_double_click(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        target = str(data)
        if target.startswith(("http://", "https://")):
            self.open_url_requested.emit(target)
        else:
            self.open_requested.emit(target)

    def _on_tree_context_menu(self, pos):
        """分组树右键菜单：打开/定位/复制/删除到回收站。

        分组节点本身（无文件路径）不弹菜单；GitHub 仓库链接只提供“打开”。
        """
        item = self.group_tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        target = str(data)
        if target.startswith(("http://", "https://")):
            menu = self._tree_url_menu(target)
        else:
            menu = self._tree_file_menu(target)
        if menu.actions():
            menu.exec(self.group_tree.viewport().mapToGlobal(pos))

    def _tree_url_menu(self, url: str) -> QMenu:
        menu = QMenu(self)
        act = QAction("在浏览器中打开仓库", self)
        act.triggered.connect(lambda: self.open_url_requested.emit(url))
        menu.addAction(act)
        return menu

    def _tree_file_menu(self, path: str) -> QMenu:
        menu = QMenu(self)
        for text, sig in (
            ("打开", self.open_requested),
            ("打开所在文件夹", self.reveal_requested),
            ("复制完整路径", self.copy_requested),
            ("复制文件名", self.copy_name_requested),
        ):
            act = QAction(text, self)
            act.triggered.connect(lambda _c=False, s=sig: s.emit(path))
            menu.addAction(act)
        menu.addSeparator()
        delete_act = QAction("删除到回收站…", self)
        delete_act.triggered.connect(
            lambda: self.delete_requested.emit(path))
        menu.addAction(delete_act)
        return menu

    def _ordered_rows(self) -> list[dict]:
        if self._sort_col < 0:
            return list(self._rows)
        key_map = {
            0: lambda r: r["name"].lower(),
            1: lambda r: 1 if r.get("exists", True) else 0,
            2: lambda r: ft.LABELS.get(ft.category_of_ext(r["ext"]), ""),
            3: lambda r: int(r["size"]),
            4: lambda r: int(r["mtime"]),
            5: lambda r: int(r["ctime"]),
            6: lambda r: r.get("parent", "").lower(),
        }
        key = key_map[self._sort_col]
        return sorted(self._rows, key=key, reverse=not self._sort_asc)

    def _repopulate(self):
        self.table.setRowCount(0)
        ordered = self._ordered_rows()
        self.table.setRowCount(len(ordered))
        keep_tooltips = len(ordered) <= 2000  # 大结果集时跳过悬浮提示以加快渲染
        for r, row in enumerate(ordered):
            self._fill_row(r, row, keep_tooltips)

    def _append_rows_to_table(self, start: int):
        """只把第 start 行之后的新行填进表格（自然顺序下使用，避免重建旧行）。"""
        keep_tooltips = len(self._rows) <= 2000
        self.table.setRowCount(len(self._rows))
        for r in range(start, len(self._rows)):
            self._fill_row(r, self._rows[r], keep_tooltips)

    def _fill_row(self, r: int, row: dict, keep_tooltips: bool):
        name_item = QTableWidgetItem(row["name"])
        if keep_tooltips:
            name_item.setToolTip(row["path"])
        exists = row.get("exists")   # True/False/None(未校验)
        if exists is True:
            status_item = QTableWidgetItem("存在")
        elif exists is False:
            status_item = QTableWidgetItem("不存在")
        else:
            status_item = QTableWidgetItem("未校验")
        if exists is False:
            f = name_item.font()
            f.setStrikeOut(True)
            name_item.setFont(f)
            name_item.setForeground(QBrush(QColor("#9aa0a8")))
        elif exists is None:
            name_item.setForeground(QBrush(QColor("#9aa0a8")))
        cat = ft.category_of_ext(row["ext"])
        type_item = QTableWidgetItem(ft.LABELS.get(cat, cat))
        size_item = QTableWidgetItem(ft.human_size(row["size"]))
        mtime_item = QTableWidgetItem(tu.fmt_ts(row["mtime"]))
        ctime_item = QTableWidgetItem(tu.fmt_ts(row["ctime"]))
        parent_item = QTableWidgetItem(row.get("parent", ""))
        if keep_tooltips:
            parent_item.setToolTip(row.get("parent", ""))
        for col, item in enumerate(
            (name_item, status_item, type_item, size_item,
             mtime_item, ctime_item, parent_item)
        ):
            self.table.setItem(r, col, item)

    def _sort_by_column(self, col: int):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self.table.horizontalHeader().setSortIndicator(
            col, Qt.SortOrder.AscendingOrder
            if self._sort_asc else Qt.SortOrder.DescendingOrder
        )
        self._repopulate()
        self._apply_filter()

    def row_dict(self) -> dict | None:
        items = self.table.selectedItems()
        if not items:
            return None
        r = items[0].row()
        if 0 <= r < len(self._rows):
            # 表格行与 _rows 一一对应
            return self._ordered_rows()[r]
        return None

    def current_rows(self) -> list[dict]:
        return list(self._rows)

    # ---------- 界面状态存取（供主窗口记住布局） ----------

    def layout_state(self) -> dict:
        """返回可持久化的布局信息：分栏高度 + 各列宽度。"""
        return {
            "splitter": list(self._splitter.sizes()),
            "columns": [self.table.columnWidth(i)
                        for i in range(len(self.COLUMNS))],
            "sort_col": self._sort_col,
            "sort_asc": self._sort_asc,
        }

    def apply_layout_state(self, state: dict | None) -> None:
        """恢复上次保存的布局（容错：任何异常都退回默认布局）。"""
        if not state:
            return
        try:
            sizes = [int(x) for x in (state.get("splitter") or [])]
            # 允许某一栏为 0（例如用户把分组树收起了），只要能撑满即可
            if len(sizes) == self._splitter.count() and sum(sizes) > 0:
                self._remembered_sizes = sizes
                self._splitter.setSizes(sizes)
            widths = [int(x) for x in (state.get("columns") or [])]
            for i, w in enumerate(widths[:len(self.COLUMNS)]):
                if w > 20:
                    self.table.setColumnWidth(i, w)
            col = int(state.get("sort_col", -1))
            if -1 <= col < len(self.COLUMNS):
                self._sort_col = col
                self._sort_asc = bool(state.get("sort_asc", True))
        except (TypeError, ValueError):
            pass

    # ---------- 交互 ----------

    def _on_selection(self):
        row = self.row_dict()
        self._set_details_enabled(row is not None)
        if row is None:
            self.path_label.setText("")
            self.reason_list.clear()
            return
        self.path_label.setText(row["path"])
        self.reason_list.clear()
        for reason in row.get("reasons", []):
            self.reason_list.addItem("· " + reason)
        if row.get("exists") is False:
            self.reason_list.addItem("· 注意：文件当前不存在，可能已被移动、重命名或删除")
        elif row.get("exists") is None:
            self.reason_list.addItem("· 结果较多时未逐一校验此文件是否存在")
        self.fav_btn.setText("取消收藏" if row.get("favorite") else "加入收藏")

    def _on_double_click(self, _item):
        self._emit("open")

    def _on_context_menu(self, pos):
        # 右键先把光标下的行选中，避免菜单操作落在上一次选中的行上
        item = self.table.itemAt(pos)
        if item is not None and item.row() < self.table.rowCount():
            self.table.selectRow(item.row())
        row = self.row_dict()
        if row is None:
            return
        menu = self._build_table_menu(row)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _build_table_menu(self, row: dict) -> QMenu:
        """结果表右键菜单：动作直接作用于被右键的那一行。

        （不依赖“当前选中行”，避免右键与选中不一致时操作错行。）
        """
        menu = QMenu(self)
        path = row["path"]
        for text, sig in (
            ("打开", self.open_requested),
            ("打开所在文件夹", self.reveal_requested),
            ("复制完整路径", self.copy_requested),
            ("复制文件名", self.copy_name_requested),
            ("查看属性", self.properties_requested),
        ):
            act = QAction(text, self)
            act.triggered.connect(lambda _c=False, s=sig: s.emit(path))
            menu.addAction(act)
        menu.addSeparator()
        fav = QAction("加入收藏" if not row.get("favorite") else "取消收藏", self)
        fav.triggered.connect(lambda: self._toggle_favorite_of(row))
        menu.addAction(fav)
        menu.addSeparator()
        delete_act = QAction("删除到回收站…", self)
        # 已确认不存在的文件无需（也无法）删除
        delete_act.setEnabled(row.get("exists") is not False)
        delete_act.triggered.connect(
            lambda: self.delete_requested.emit(path))
        menu.addAction(delete_act)
        return menu

    def _emit(self, kind: str):
        row = self.row_dict()
        if not row:
            return
        if kind == "open":
            self.open_requested.emit(row["path"])
        elif kind == "reveal":
            self.reveal_requested.emit(row["path"])
        elif kind == "copy":
            self.copy_requested.emit(row["path"])
        elif kind == "properties":
            self.properties_requested.emit(row["path"])
        elif kind == "copy_name":
            self.copy_name_requested.emit(row["path"])

    def _toggle_favorite(self):
        row = self.row_dict()
        if row:
            self._toggle_favorite_of(row)

    def _toggle_favorite_of(self, row: dict):
        row["favorite"] = not row.get("favorite", False)
        self.favorite_toggled.emit(row["path"])
        self._on_selection()

    def _set_details_enabled(self, enabled: bool):
        for btn in (self.open_btn, self.reveal_btn, self.copy_btn,
                    self.props_btn, self.fav_btn):
            btn.setEnabled(enabled)

    def _apply_filter(self, _text: str = ""):
        text = self.filter_edit.text().strip().lower()
        ordered = self._ordered_rows()
        for r, row in enumerate(ordered):
            if not text:
                self.table.setRowHidden(r, False)
                continue
            hit = text in row["name"].lower() or text in row["path"].lower()
            self.table.setRowHidden(r, not hit)