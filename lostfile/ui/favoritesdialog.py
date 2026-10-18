"""收藏夹对话框：查看、打开、定位、移除已收藏的文件。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from .. import fileops
from .. import timeutil as tu


class FavoritesDialog(QDialog):
    """收藏列表。收藏只是记住路径；文件被移动/删除时会明确标出。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("收藏夹")
        self.resize(660, 460)
        self._build()
        self.refresh()

    def _build(self):
        lay = QVBoxLayout(self)
        note = QLabel(
            "收藏只保存文件路径，不复制文件内容。文件被移动或删除后，"
            "对应条目会标注“已不存在”。"
        )
        note.setObjectName("sub")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.listw = QListWidget()
        self.listw.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.listw, 1)

        btns = QHBoxLayout()
        self.open_btn = QPushButton("打开")
        self.open_btn.setObjectName("primary")
        reveal_btn = QPushButton("打开所在文件夹")
        copy_btn = QPushButton("复制完整路径")
        remove_btn = QPushButton("取消收藏")
        remove_btn.setObjectName("danger")
        close_btn = QPushButton("关闭")
        btns.addWidget(self.open_btn)
        btns.addWidget(reveal_btn)
        btns.addWidget(copy_btn)
        btns.addWidget(remove_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        self.open_btn.clicked.connect(self._open)
        reveal_btn.clicked.connect(self._reveal)
        copy_btn.clicked.connect(self._copy)
        remove_btn.clicked.connect(self._remove)
        close_btn.clicked.connect(self.accept)
        self.listw.itemDoubleClicked.connect(lambda _i: self._open())

    # ---------- 数据 ----------

    def refresh(self):
        self.listw.clear()
        items = self.db.list_favorites()
        if not items:
            self.listw.addItem("还没有收藏任何文件（在结果里右键或点击“加入收藏”）")
            self._set_enabled(False)
            return
        for it in items:
            path = it["path"]
            exists = fileops.exists_file(path)
            mark = "" if exists else "　[已不存在]"
            item = QListWidgetItem(
                f"{path}{mark}\n    收藏于 {tu.fmt_ts(it.get('added_at'))}"
            )
            item.setData(Qt.ItemDataRole.UserRole, path)
            if not exists:
                item.setForeground(Qt.GlobalColor.gray)
            self.listw.addItem(item)
        self.listw.setCurrentRow(0)
        self._set_enabled(True)

    def _current_path(self) -> str | None:
        item = self.listw.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _set_enabled(self, on: bool):
        for b in (self.open_btn,):
            b.setEnabled(on)

    # ---------- 操作 ----------

    def _open(self):
        p = self._current_path()
        if p and not fileops.open_file(p):
            QMessageBox.information(self, "无法打开", "文件不存在或已被移动。")

    def _reveal(self):
        p = self._current_path()
        if p and not fileops.reveal_file(p):
            QMessageBox.information(self, "无法定位", "文件不存在或已被移动。")

    def _copy(self):
        p = self._current_path()
        if p:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(p)

    def _remove(self):
        p = self._current_path()
        if not p:
            return
        self.db.remove_favorite(p)
        self.refresh()