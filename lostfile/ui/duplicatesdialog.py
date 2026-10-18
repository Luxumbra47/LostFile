"""重复文件管理对话框：按内容哈希分组，可打开/定位/回收站删除。"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QMenu, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from .. import fileops
from .. import filetypes as ft
from .. import hash as hashmod
from .. import timeutil as tu
from ..workers import HashBackfill

human_size = ft.human_size


class DuplicatesDialog(QDialog):
    """重复文件视图：检测在后台进行，界面定时刷新进度与分组。"""

    def __init__(self, cfg, db, worker: HashBackfill | None = None,
                 parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.db = db
        self._worker = worker or HashBackfill(db)
        self._building = False
        self.setWindowTitle("重复文件")
        self.resize(760, 560)
        self._build()

        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    # ---------- UI ----------

    def _build(self):
        lay = QVBoxLayout(self)
        title = QLabel("重复文件检测")
        title.setObjectName("hero")
        lay.addWidget(title)
        note = QLabel(
            "按文件内容哈希找出完全相同的文件（内容相同、名字不同也算）。\n"
            "· 只在本机计算哈希，不读取/保存文件内容，绝不上传；\n"
            "· 哈希值不可还原出内容，只用于内容比对；\n"
            "· 超过 2GB 的文件跳过检测；\n"
            "· 删除走回收站（可还原），不提供批量删除，请逐项确认。"
        )
        note.setWordWrap(True)
        note.setObjectName("sub")
        lay.addWidget(note)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("sub")
        lay.addWidget(self.status_label)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["文件", "大小", "修改时间", "位置"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 240)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 140)
        self.tree.header().setStretchLastSection(True)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_menu)
        lay.addWidget(self.tree, 1)

        btns = QHBoxLayout()
        self.start_btn = QPushButton("开始/继续检测")
        self.start_btn.setObjectName("primary")
        self.rebuild_btn = QPushButton("全部重新检测")
        close_btn = QPushButton("关闭")
        btns.addWidget(self.start_btn)
        btns.addWidget(self.rebuild_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        self.start_btn.clicked.connect(self._start)
        self.rebuild_btn.clicked.connect(self._rebuild)
        close_btn.clicked.connect(self.accept)

    # ---------- 数据 ----------

    def _groups(self) -> list[dict]:
        conn = self.db.connect()
        try:
            return hashmod.duplicate_groups(conn)
        finally:
            conn.close()

    def _refresh(self):
        if self._building or not self.isVisible():
            return
        self._building = True
        try:
            done, total = self.db.hash_stats()
            pending = total - done
            state = "正在后台检测…" if self._worker.running or pending else "已检测"
            self.status_label.setText(
                f"{state}：{done} / {total} 个文件已计算哈希"
            )
            self.start_btn.setEnabled(bool(pending))
            groups = self._groups()
            waste = hashmod.total_wasted(groups)
            if groups:
                self.status_label.setText(
                    self.status_label.text()
                    + f"　·　发现 {len(groups)} 组重复，"
                      f"清理后可节省约 {human_size(waste)}"
                )
            self._fill_tree(groups)
        finally:
            self._building = False

    def _fill_tree(self, groups: list[dict]):
        self.tree.clear()
        for g in groups:
            top = QTreeWidgetItem([
                f"{g['count']} 个文件内容相同（可保留一个）",
                human_size(g["waste"]), "", "",
            ])
            top.setToolTip(0, "整组文件内容完全一致（按哈希）")
            for f in g["files"]:
                child = QTreeWidgetItem([
                    f["name"], human_size(f["size"]),
                    tu.fmt_ts(f["mtime"]), f.get("parent", ""),
                ])
                child.setData(0, Qt.ItemDataRole.UserRole, f["path"])
                child.setToolTip(0, f["path"])
                top.addChild(child)
            top.setExpanded(True)
            self.tree.addTopLevelItem(top)

    # ---------- 行为 ----------

    def _start(self):
        self.cfg.set("dup_detect_enabled", True)
        self.cfg.save()
        self._worker.start()
        self._refresh()

    def _rebuild(self):
        if QMessageBox.question(
            self, "全部重新检测",
            "将清空已有哈希结果并对所有文件重新计算（不影响文件本身，"
            "大索引可能需要较长时间）。确定重新检测吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.db.reset_hash_index()
        self._start()

    def _on_double_click(self, item, _col):
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if path:
            fileops.open_file(path)

    def _on_tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if not path:
            return
        menu = QMenu(self)
        act_open = menu.addAction("打开")
        act_reveal = menu.addAction("打开所在文件夹")
        act_props = menu.addAction("属性")
        menu.addSeparator()
        act_copy = menu.addAction("复制完整路径")
        act_del = menu.addAction("删除到回收站…")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_open:
            fileops.open_file(path)
        elif chosen is act_reveal:
            fileops.open_parent_folder(path)
        elif chosen is act_props:
            fileops.show_properties(path)
        elif chosen is act_copy:
            QApplication.clipboard().setText(path)
        elif chosen is act_del:
            self._delete(path)

    def _delete(self, path: str):
        if QMessageBox.question(
            self, "删除到回收站",
            f"将该文件移入回收站（可随时还原）：\n{path}\n\n"
            "文件从磁盘与索引中移除。确定删除吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        if fileops.delete_to_recycle_bin(path):
            self.db.remove_file(path)
        self._refresh()

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)