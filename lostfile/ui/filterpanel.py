"""多条件筛选面板：类型 / 扩展名 / 文件名关键词 / 时间 / 大小 / 位置。"""
import re
import time
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDoubleSpinBox, QFileDialog, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
)

from .. import filetypes as ft
from .. import knownfolders as kf
from .. import timeutil as tu
from ..query import SearchQuery

MB = 1024 * 1024


class FilterPanel(QWidget):
    """条件变化后由用户点击“搜索”，发出 SearchQuery。"""

    search_requested = Signal(object)

    _CATEGORY_OPTIONS = [
        "image", "video", "audio", "pdf", "word", "excel", "ppt", "archive",
        "installer", "text", "code", "other",
    ]

    def __init__(self, parent=None, max_results: int = 500):
        super().__init__(parent)
        self._limit = max_results
        self._build()

    # ---------- UI ----------

    def _build(self):
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        row = 0
        grid.addWidget(QLabel("文件类型"), row, 0)
        self.type_combo = QComboBox()
        self.type_combo.addItem("全部类型", None)
        for cat in self._CATEGORY_OPTIONS:
            self.type_combo.addItem(ft.LABELS[cat], cat)
        grid.addWidget(self.type_combo, row, 1)

        grid.addWidget(QLabel("扩展名"), row, 2)
        self.ext_edit = QLineEdit()
        self.ext_edit.setPlaceholderText("如 mp4 zip（空格或逗号分隔）")
        grid.addWidget(self.ext_edit, row, 3)

        row += 1
        grid.addWidget(QLabel("文件名关键词"), row, 0)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("可选，支持模糊匹配")
        kw_row = QHBoxLayout()
        kw_row.addWidget(self.name_edit, 1)
        self.name_only_check = QCheckBox("仅匹配文件名（不搜索路径）")
        self.name_only_check.setToolTip(
            "默认关键词也会在文件所在路径中查找；勾选后仅与文件名比对。"
        )
        kw_row.addWidget(self.name_only_check)
        grid.addLayout(kw_row, row, 1)

        grid.addWidget(QLabel("时间范围"), row, 2)
        time_row = QHBoxLayout()
        self.time_combo = QComboBox()
        for label in ("全部时间", "今天", "昨天", "最近 7 天", "最近 30 天", "自定义范围"):
            self.time_combo.addItem(label)
        self.time_field_combo = QComboBox()
        self.time_field_combo.addItem("修改时间", "mtime")
        self.time_field_combo.addItem("创建时间", "ctime")
        self.time_field_combo.addItem("创建或修改", "any")
        time_row.addWidget(self.time_combo, 1)
        time_row.addWidget(self.time_field_combo, 1)
        grid.addLayout(time_row, row, 3)

        row += 1
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        grid.addWidget(QLabel("自定义日期"), row, 0)
        date_row = QHBoxLayout()
        date_row.addWidget(self.date_from)
        date_row.addWidget(QLabel("至"))
        date_row.addWidget(self.date_to)
        date_row.addStretch(1)
        grid.addLayout(date_row, row, 1, 1, 3)

        row += 1
        grid.addWidget(QLabel("大小范围"), row, 0)
        self.size_combo = QComboBox()
        for label in ("全部大小", "小于 1 MB", "1 – 10 MB", "10 – 100 MB",
                      "大于 100 MB", "自定义范围"):
            self.size_combo.addItem(label)
        grid.addWidget(self.size_combo, row, 1)

        grid.addWidget(QLabel("文件位置"), row, 2)
        self.loc_combo = QComboBox()
        grid.addWidget(self.loc_combo, row, 3)

        row += 1
        self.size_min_spin = QDoubleSpinBox()
        self.size_min_spin.setRange(0.1, 10240.0)
        self.size_min_spin.setDecimals(1)
        self.size_min_spin.setSuffix(" MB")
        self.size_max_spin = QDoubleSpinBox()
        self.size_max_spin.setRange(0.1, 10240.0)
        self.size_max_spin.setDecimals(1)
        self.size_max_spin.setSuffix(" MB")
        grid.addWidget(QLabel("自定义大小"), row, 0)
        size_row = QHBoxLayout()
        size_row.addWidget(self.size_min_spin)
        size_row.addWidget(QLabel("至"))
        size_row.addWidget(self.size_max_spin)
        size_row.addStretch(1)
        grid.addLayout(size_row, row, 1)

        self.loc_custom_edit = QLineEdit()
        self.loc_custom_edit.setPlaceholderText("选择或输入自定义目录")
        self.browse_btn = QPushButton("浏览…")
        self.browse_btn.setObjectName("ghost")
        loc_row = QHBoxLayout()
        loc_row.addWidget(self.loc_custom_edit, 1)
        loc_row.addWidget(self.browse_btn)
        grid.addLayout(loc_row, row, 3)
        self.browse_btn.clicked.connect(self._browse_location)

        row += 1
        btn_row = QHBoxLayout()
        self.search_btn = QPushButton("搜索")
        self.search_btn.setObjectName("primary")
        self.reset_btn = QPushButton("重置")
        btn_row.addWidget(self.search_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch(1)
        grid.addLayout(btn_row, row, 0, 1, 4)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.time_combo.currentIndexChanged.connect(self._sync_visibility)
        self.size_combo.currentIndexChanged.connect(self._sync_visibility)
        self.loc_combo.currentIndexChanged.connect(self._sync_visibility)
        self.search_btn.clicked.connect(self._emit_search)
        self.reset_btn.clicked.connect(self.reset)
        self._sync_visibility()

    # ---------- 位置选项 ----------

    def refresh_locations(self, roots: list[dict]):
        current = self.loc_combo.currentData()
        self.loc_combo.blockSignals(True)
        self.loc_combo.clear()
        self.loc_combo.addItem("全部位置", None)
        for special, label in (("downloads", "下载目录"), ("desktop", "桌面"),
                               ("documents", "文档目录")):
            path = kf.known_folder(special)
            self.loc_combo.addItem(label if path else f"{label}（未找到）", path)
        for r in roots:
            self.loc_combo.addItem(
                f"索引目录·{r['display_name']}（{r['path']}）", r["path"]
            )
        custom_idx = self.loc_combo.count()
        self.loc_combo.addItem("自定义目录…", None)
        self.loc_combo.blockSignals(False)
        self._custom_idx = custom_idx
        idx = self.loc_combo.findData(current)
        self.loc_combo.setCurrentIndex(idx if current and idx >= 0 else 0)
        self._sync_visibility()

    def _browse_location(self):
        d = QFileDialog.getExistingDirectory(self, "选择目录")
        if d:
            self.loc_custom_edit.setText(d)
            self.loc_combo.setCurrentIndex(getattr(self, "_custom_idx",
                                                   self.loc_combo.count() - 1))
            self._sync_visibility()

    def _sync_visibility(self):
        custom_time = self.time_combo.currentIndex() == 5
        self.date_from.setVisible(custom_time)
        self.date_to.setVisible(custom_time)
        custom_size = self.size_combo.currentIndex() == 5
        self.size_min_spin.setVisible(custom_size)
        self.size_max_spin.setVisible(custom_size)
        custom_loc = (getattr(self, "_custom_idx", -1) >= 0
                      and self.loc_combo.currentIndex() == self._custom_idx)
        self.loc_custom_edit.setVisible(custom_loc)
        self.browse_btn.setVisible(custom_loc)

    # ---------- 条件构建 ----------

    def _emit_search(self):
        self.search_requested.emit(self.build_query())

    def build_query(self) -> SearchQuery:
        q = SearchQuery(limit=self._limit)

        cat = self.type_combo.currentData()
        if cat:
            q.categories = [cat]
        ext_text = self.ext_edit.text().strip()
        if ext_text:
            q.extensions = [
                e.lower().lstrip(".") for e in re.split(r"[\s,;，；]+", ext_text) if e
            ]
        name_text = self.name_edit.text().strip()
        if name_text:
            q.keywords = [k for k in re.split(r"\s+", name_text) if k]
        q.name_only = self.name_only_check.isChecked()

        q.time_field = self.time_field_combo.currentData() or "mtime"
        tidx = self.time_combo.currentIndex()
        if tidx == 1:
            q.time_start, q.time_end = tu.today_range()
        elif tidx == 2:
            q.time_start, q.time_end = tu.yesterday_range()
        elif tidx == 3:
            q.time_start, q.time_end = tu.last_days_range(7)
        elif tidx == 4:
            q.time_start, q.time_end = tu.last_days_range(30)
        elif tidx == 5:
            q.time_start = self._qdate_start(self.date_from)
            q.time_end = self._qdate_end(self.date_to)

        sidx = self.size_combo.currentIndex()
        if sidx == 1:
            q.size_max = MB
        elif sidx == 2:
            q.size_min, q.size_max = MB, 10 * MB
        elif sidx == 3:
            q.size_min, q.size_max = 10 * MB, 100 * MB
        elif sidx == 4:
            q.size_min = 100 * MB
        elif sidx == 5:
            vmin, vmax = self.size_min_spin.value(), self.size_max_spin.value()
            if vmin > 0:
                q.size_min = int(vmin * MB)
            if vmax > 0:
                q.size_max = int(vmax * MB)

        loc = self.loc_combo.currentData()
        if loc:
            q.locations = [loc.rstrip("\\/") or loc]
        elif (getattr(self, "_custom_idx", -1) >= 0
              and self.loc_combo.currentIndex() == self._custom_idx
              and self.loc_custom_edit.text().strip()):
            p = self.loc_custom_edit.text().strip()
            q.locations = [p.rstrip("\\/") or p]
        return q

    @staticmethod
    def _qdate_start(w) -> int | None:
        try:
            d = w.date()
            dt = datetime(d.year(), d.month(), d.day(), 0, 0, 0)
            return int(time.mktime(dt.timetuple()))
        except Exception:
            return None

    @staticmethod
    def _qdate_end(w) -> int | None:
        try:
            d = w.date()
            dt = datetime(d.year(), d.month(), d.day(), 23, 59, 59)
            return int(time.mktime(dt.timetuple()))
        except Exception:
            return None

    # ---------- 从自然语言条件套用 ----------

    def set_from_conditions(self, conditions: list[dict]):
        self.reset()
        for c in conditions:
            if c["kind"] == "type" and c["categories"]:
                idx = self.type_combo.findData(c["categories"][0])
                if idx >= 0:
                    self.type_combo.setCurrentIndex(idx)
            elif c["kind"] == "time":
                field = c.get("field", "mtime")
                fi = self.time_field_combo.findData(field)
                if fi >= 0:
                    self.time_field_combo.setCurrentIndex(fi)
                start, end = c.get("start"), c.get("end")
                preset = self._match_preset_time(start, end)
                if preset is not None:
                    self.time_combo.setCurrentIndex(preset)
                else:
                    self.time_combo.setCurrentIndex(5)
                    if start:
                        self.date_from.setDate(datetime.fromtimestamp(start).date())
                    if end:
                        self.date_to.setDate(datetime.fromtimestamp(end).date())
            elif c["kind"] == "size":
                vmin, vmax = c.get("min"), c.get("max")
                preset = self._match_preset_size(vmin, vmax)
                if preset is not None:
                    self.size_combo.setCurrentIndex(preset)
                else:
                    self.size_combo.setCurrentIndex(5)
                    if vmin:
                        self.size_min_spin.setValue(round(vmin / MB, 1))
                    if vmax:
                        self.size_max_spin.setValue(round(vmax / MB, 1))
            elif c["kind"] == "location":
                path = c.get("path")
                if path:
                    self.loc_custom_edit.setText(path)
                    self.loc_combo.setCurrentIndex(
                        getattr(self, "_custom_idx", self.loc_combo.count() - 1)
                    )
            elif c["kind"] == "keywords":
                self.name_edit.setText(" ".join(c["values"]))
        self._sync_visibility()

    def _match_preset_time(self, start, end):
        presets = [None, tu.today_range(), tu.yesterday_range(),
                   tu.last_days_range(7), tu.last_days_range(30)]
        for i, rng in enumerate(presets[1:], start=1):
            if rng is not None and start == rng[0] and end == rng[1]:
                return i
        return None

    @staticmethod
    def _match_preset_size(vmin, vmax):
        presets = [None, (None, MB), (MB, 10 * MB), (10 * MB, 100 * MB),
                   (100 * MB, None)]
        for i, (lo, hi) in enumerate(presets[1:], start=1):
            if vmin == lo and vmax == hi:
                return i
        return None

    def reset(self):
        self.type_combo.setCurrentIndex(0)
        self.ext_edit.clear()
        self.name_edit.clear()
        self.name_only_check.setChecked(False)
        self.time_combo.setCurrentIndex(0)
        self.time_field_combo.setCurrentIndex(0)
        self.size_combo.setCurrentIndex(0)
        self.loc_combo.setCurrentIndex(0)
        self.loc_custom_edit.clear()
        self.size_min_spin.setValue(0.1)
        self.size_max_spin.setValue(0.1)
        today = datetime.now().date()
        self.date_from.setDate(today)
        self.date_to.setDate(today)
        self._sync_visibility()