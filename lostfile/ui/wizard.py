"""首次启动引导：欢迎 → 选择索引目录 → 开始使用。"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import APP_DISPLAY_NAME, __version__
from .. import knownfolders as kf


class FirstRunWizard(QDialog):
    """三步引导。用户选择要索引的目录；绝不默认扫描整个磁盘。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"欢迎使用 {APP_DISPLAY_NAME}")
        self.resize(620, 680)
        self._downloads = kf.known_folder("downloads")
        self._desktop = kf.known_folder("desktop")
        self._documents = kf.known_folder("documents")
        self._custom_dirs: list[str] = []
        self._extra_checks: dict[str, QCheckBox] = {}
        self._build()

    # ---------- UI ----------

    def _build(self):
        root = QVBoxLayout(self)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.stack.addWidget(self._page_welcome())
        self.stack.addWidget(self._page_choose())
        self.stack.addWidget(self._page_confirm())

        nav = QHBoxLayout()
        self.back_btn = QPushButton("上一步")
        self.back_btn.setObjectName("ghost")
        self.next_btn = QPushButton("下一步")
        self.next_btn.setObjectName("primary")
        nav.addWidget(self.back_btn)
        nav.addStretch(1)
        nav.addWidget(self.next_btn)
        root.addLayout(nav)

        self.back_btn.clicked.connect(self._on_back)
        self.next_btn.clicked.connect(self._on_next)
        self._sync_nav()

    def _page_welcome(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(32, 40, 32, 8)
        hero = QLabel("找回那个“好像是……”的文件")
        hero.setObjectName("hero")
        intro = QLabel(
            "LostFile 是一款本地文件查找工具。\n\n"
            "忘记文件名也没关系——只要还记得大概的类型、时间、大小或位置，\n"
            "就能把它找回来。比如：\n\n"
            "· “昨天下载的 PDF，大概 3MB”\n"
            "· “上周修改的图片”\n"
            "· “下载目录里超过 100MB 的视频”\n\n"
            "所有数据只保存在你的电脑上，不读取文件内容，不上传任何信息。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("sub")
        lay.addWidget(hero)
        lay.addSpacing(8)
        lay.addWidget(intro)
        lay.addStretch(1)
        return page

    def _page_choose(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(32, 32, 32, 8)
        title = QLabel("选择要索引的目录")
        title.setObjectName("title")
        sub = QLabel("LostFile 只搜索你选择的目录，不会自动扫描整个磁盘。之后可随时在设置中调整。")
        sub.setObjectName("sub")
        sub.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(12)

        def cb(special, label):
            h = QHBoxLayout()
            path = getattr(self, "_" + special)
            if path:
                chk = QCheckBox(label)
                chk.setChecked(True)
                h.addWidget(chk, 0)
                p = QLabel(path)
                p.setObjectName("sub")
                p.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                h.addWidget(p, 1)
            else:
                chk = QCheckBox(label + "（未找到）")
                chk.setEnabled(False)
                h.addWidget(chk, 0)
                h.addStretch(1)
            chk.toggled.connect(lambda _v: self._sync_nav())
            setattr(self, f"_chk_{special}", chk)
            return h

        self._chk_downloads = None
        for special, label in (("downloads", "下载目录"), ("desktop", "桌面"),
                               ("documents", "文档目录")):
            lay.addLayout(cb(special, label))

        # ---- 其他盘上探测到的常用文件夹 ----
        known_now = {os.path.normcase(os.path.abspath(p)) for p in
                     (self._downloads, self._desktop, self._documents) if p}
        extras = [d for d in kf.suggested_dirs()
                  if os.path.normcase(d["path"]) not in known_now]
        if extras:
            lay.addSpacing(8)
            extra_title = QLabel("在其他盘上发现的常用文件夹（已自动探测，勾选即可索引）")
            extra_title.setObjectName("sub")
            extra_title.setWordWrap(True)
            lay.addWidget(extra_title)
            row = QHBoxLayout()
            col = QVBoxLayout()
            for item in extras:
                chk = QCheckBox(item["label"])
                chk.setChecked(True)
                chk.setToolTip(item["path"])
                chk.toggled.connect(lambda _v: self._sync_nav())
                self._extra_checks[item["path"]] = chk
                col.addWidget(chk)
            row.addLayout(col, 1)
            lay.addLayout(row)

        lay.addSpacing(12)
        custom_title = QLabel("自定义目录")
        custom_title.setObjectName("sub")
        lay.addWidget(custom_title)
        self.custom_list = QListWidget()
        self.custom_list.setMaximumHeight(120)
        lay.addWidget(self.custom_list)
        btns = QHBoxLayout()
        add_btn = QPushButton("添加文件夹…")
        rm_btn = QPushButton("移除所选")
        rm_btn.setObjectName("ghost")
        btns.addWidget(add_btn)
        btns.addWidget(rm_btn)
        btns.addStretch(1)
        lay.addLayout(btns)
        add_btn.clicked.connect(self._add_custom)
        rm_btn.clicked.connect(self._remove_custom)

        # ---- 整个磁盘（可选，扫描量大） ----
        from .. import drives
        self._drive_checks: dict[str, QCheckBox] = {}
        drives_list = drives.list_fixed_drives()
        if drives_list:
            lay.addSpacing(8)
            disk_title = QLabel("整个磁盘（可选：扫描量大、耗时长）")
            disk_title.setObjectName("sub")
            lay.addWidget(disk_title)
            disk_row = QHBoxLayout()
            for drive in drives_list:
                chk = QCheckBox(f"{drive[0:2]} 盘（{drive}）")
                chk.setChecked(False)  # 绝不默认勾选整个磁盘
                chk.toggled.connect(lambda _v: self._sync_nav())
                self._drive_checks[drive] = chk
                disk_row.addWidget(chk)
            disk_row.addStretch(1)
            lay.addLayout(disk_row)
        lay.addStretch(1)
        return page

    def _page_confirm(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(32, 40, 32, 8)
        title = QLabel("准备就绪")
        title.setObjectName("title")
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(title)
        lay.addSpacing(8)
        lay.addWidget(self.summary)
        note = QLabel(
            "点击“开始使用”后将立即开始对这些目录建立索引。\n"
            "扫描在后台进行，可以随时暂停或取消。\n"
            "LostFile 只保存文件名、大小、时间等元数据，不读取文件内容。"
        )
        note.setObjectName("sub")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return page

    # ---------- 行为 ----------

    def _add_custom(self):
        d = QFileDialog.getExistingDirectory(self, "选择要索引的文件夹")
        if d and d not in self._custom_dirs:
            self._custom_dirs.append(d)
            self._refresh_custom()

    def _remove_custom(self):
        row = self.custom_list.currentRow()
        if 0 <= row < len(self._custom_dirs):
            self._custom_dirs.pop(row)
            self._refresh_custom()

    def _refresh_custom(self):
        self.custom_list.clear()
        self.custom_list.addItems(self._custom_dirs)

    def selected_paths(self) -> list[str]:
        """返回所有选中的目录路径（不含未勾选的磁盘）。"""
        out: list[str] = []
        for special in ("downloads", "desktop", "documents"):
            chk = getattr(self, f"_chk_{special}", None)
            path = getattr(self, "_" + special)
            if chk is not None and chk.isChecked() and path:
                out.append(path)
        out += [p for p, chk in self._extra_checks.items() if chk.isChecked()]
        out += list(self._custom_dirs)
        out += [d for d, chk in self._drive_checks.items() if chk.isChecked()]
        return out

    def _has_selection(self) -> bool:
        return bool(self.selected_paths())

    def _on_next(self):
        idx = self.stack.currentIndex()
        if idx < self.stack.count() - 1:
            if idx == 1 and not self._has_selection():
                return  # 未选择任何目录，停留在选择页
            if idx == 1:
                paths = self.selected_paths()
                self.summary.setText("将索引以下目录：\n\n" +
                                     "\n".join("  · " + p for p in paths))
            self.stack.setCurrentIndex(idx + 1)
        else:
            self.accept()
        self._sync_nav()

    def _on_back(self):
        idx = self.stack.currentIndex()
        if idx > 0:
            self.stack.setCurrentIndex(idx - 1)
        self._sync_nav()

    def _sync_nav(self):
        idx = self.stack.currentIndex()
        self.back_btn.setVisible(idx > 0)
        if idx == self.stack.count() - 1:
            self.next_btn.setText("开始使用")
            self.next_btn.setEnabled(True)
        else:
            self.next_btn.setText("下一步")
            self.next_btn.setEnabled(True)
        if idx == 1:
            self.next_btn.setEnabled(self._has_selection())