"""“最近打开”对话框：可选读取 Windows 最近使用记录（默认关闭）。"""
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from .. import fileops
from .. import recent
from .. import timeutil as tu


class _Emitter(QObject):
    done = Signal(list, list)   # items, notes
    failed = Signal(str)


class RecentDialog(QDialog):
    open_requested = Signal(str)
    reveal_requested = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("最近打开（可选功能）")
        self.resize(640, 480)
        self._items: list[dict] = []
        self._build()
        self._start()

    def _build(self):
        lay = QVBoxLayout(self)
        note = QLabel(
            "此功能默认关闭，开启后仅读取 Windows“最近使用文件”列表中的"
            "路径与时间；来源应用显示“未知”。"
        )
        note.setObjectName("sub")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.listw = QListWidget()
        self.listw.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.listw, 1)

        btns = QHBoxLayout()
        self.open_btn = QPushButton("打开")
        self.open_btn.setObjectName("primary")
        reveal_btn = QPushButton("在文件夹中定位")
        copy_btn = QPushButton("复制路径")
        close_btn = QPushButton("关闭")
        btns.addWidget(self.open_btn)
        btns.addWidget(reveal_btn)
        btns.addWidget(copy_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        self.open_btn.clicked.connect(self._open)
        reveal_btn.clicked.connect(self._reveal)
        copy_btn.clicked.connect(self._copy)
        close_btn.clicked.connect(self.accept)
        self.listw.itemDoubleClicked.connect(lambda _i: self._open())
        self._set_enabled(False)

    def _start(self):
        if not self.cfg.get("recent_import_enabled"):
            QMessageBox.information(
                self, "功能未开启",
                "“最近打开”导入默认关闭。\n\n"
                "如需使用，请在“设置 → 隐私”中开启“启用‘最近打开’导入”，"
                "开启前会有明确说明。"
            )
            self.reject()
            return
        self._emitter = _Emitter(self)
        self._emitter.done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)
        self._emitter.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)

        def work():
            try:
                items, notes = recent.load_recent(500)
                self._emitter.done.emit(items, notes)
            except Exception as e:
                self._emitter.failed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, items, notes):
        self._items = items
        self.listw.clear()
        if not items:
            self.listw.addItem("没有可显示的最近打开文件" +
                               (f"（{notes[0]}）" if notes else ""))
            return
        for it in items:
            when = tu.fmt_ts(it.get("opened_at")) if it.get("opened_at") else "时间未知"
            item = QListWidgetItem(
                f"{it['path']}\n    约 {when} · 来源应用：未知"
            )
            item.setData(Qt.ItemDataRole.UserRole, it["path"])
            self.listw.addItem(item)
        if notes:
            note_label = self.findChild(QLabel)
            if note_label:
                note_label.setText(note_label.text() + " " + "；".join(notes))
        self._set_enabled(True)

    def _on_failed(self, msg):
        self.listw.clear()
        self.listw.addItem(f"读取失败：{msg}")

    def _current_path(self) -> str | None:
        item = self.listw.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _set_enabled(self, on: bool):
        for b in (self.open_btn,):
            b.setEnabled(on)

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