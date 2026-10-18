"""设置对话框：索引目录、排除项、隐私、AI、外观、关于。"""
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QRadioButton, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

import os

from .. import APP_DISPLAY_NAME, __version__
from .. import ai as aimod
from .. import drives
from .. import filetypes as ft
from .. import knownfolders as kf
from .. import secrets
from .. import timeutil as tu

human_size = ft.human_size

PRIVACY_TEXT = """# 隐私说明

LostFile 严守“数据不出本机”的原则：

· 本应用默认全本地运行，不上传文件名、路径、搜索记录或使用统计。会联网的只有两个**默认关闭**的可选功能：(1) “在线搜索 GitHub”——开启后只把你输入的关键词发给 GitHub 公开搜索接口查找同名仓库；(2) “AI 解析”——点按钮时只把你输入的描述文字发给你自己配置的 AI 服务。两者都不发送任何本地文件名、路径或索引内容，且开启前都会弹窗确认。

· 索引默认只保存文件的元数据：路径、文件名、扩展名、大小、创建时间、修改时间与索引状态，不读取文件正文。

· “正文内容检索”为**默认关闭**的可选功能：开启后仅在本机读取白名单纯文本文件（txt/md/日志/csv/配置文件/源代码等）**开头最多 16KB** 的内容存入本地索引，用于按正文关键词命中；node_modules、site-packages、.git、build、dist 等依赖/构建目录默认跳过（索引体积因此可控）；内容绝不上传，可随时关闭或在维护页清空重建。

· “重复文件检测”为**默认关闭**的可选功能：开启后在本机为文件计算内容哈希（读取内容做摘要）以找出重复文件；哈希值不可还原出内容、不会保存文件正文，绝不上传，可随时关闭或在维护页重新检测。

· AI 解析的 API Key 经 Windows DPAPI 加密后保存在本机；换用户或重装系统后无法解密，需在「AI」页重新填写。

· “本地语义搜索”为**默认关闭**的可选功能：启用时首次需联网下载一次向量模型（约 24MB）到数据目录，之后全程离线；只对**文件名**编码并保存在本机，不读取文件内容。

· 搜索历史仅保存在本地数据库中，可随时在本页一键清除。

· 配置与数据库存放在应用数据目录（便携模式下即应用所在目录的 data 文件夹，可整体拷贝带走；删除该文件夹即删除全部数据）。

· “最近打开”功能默认关闭。开启后仅读取 Windows“最近使用文件”列表（.lnk 快捷方式）中的目标文件路径与时间；来源应用无法可靠得知，一律显示“未知”。本应用不会为此读取浏览器密码、Cookie 或其他任何敏感数据。

· 本应用绝不会修改、移动或重命名任何被索引的原始文件。结果列表只提供**单个文件**的“删除到回收站”（需二次确认，文件移入 Windows 回收站可随时还原，绝不永久删除），不提供批量删除。

· 卸载/清库：在设置页点击“清除全部索引”或直接删除数据目录即可。
"""


class SettingsDialog(QDialog):
    scan_requested = Signal(list)   # list[dict] roots
    roots_changed = Signal()
    theme_changed = Signal(str)
    content_changed = Signal()      # 内容检索开关/重建后，通知主窗口联动后台任务
    hash_changed = Signal()         # 重复检测开关/重建后，通知主窗口联动后台任务
    vec_changed = Signal()          # 语义向量开关/下载/重建后，通知主窗口联动后台任务
    fts_changed = Signal()          # 关键词 FTS 开关变化后，通知主窗口联动后台任务

    def __init__(self, cfg, db, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.db = db
        self.setWindowTitle("设置")
        self.resize(660, 560)
        self._build()
        self.refresh_roots()
        self.refresh_stats()

    # ---------- UI ----------

    def _build(self):
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(self._tab_roots(), "索引目录")
        self.tabs.addTab(self._tab_maintenance(), "维护")
        self.tabs.addTab(self._tab_ai(), "AI")
        self.tabs.addTab(self._tab_vec(), "语义")
        self.tabs.addTab(self._tab_privacy(), "隐私")
        self.tabs.addTab(self._tab_appearance(), "外观")
        self.tabs.addTab(self._tab_about(), "关于")

        btns = QHBoxLayout()
        btns.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("primary")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        lay.addLayout(btns)

    def _tab_roots(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.roots_list = QListWidget()
        lay.addWidget(self.roots_list, 1)

        row = QHBoxLayout()
        add_btn = QPushButton("添加目录…")
        add_common_btn = QPushButton("添加常用目录…")
        add_disk_btn = QPushButton("添加整个磁盘…")
        rm_btn = QPushButton("移除所选")
        scan_sel_btn = QPushButton("重新扫描所选")
        scan_all_btn = QPushButton("重新扫描全部")
        row.addWidget(add_btn)
        row.addWidget(add_common_btn)
        row.addWidget(add_disk_btn)
        row.addWidget(rm_btn)
        row.addWidget(scan_sel_btn)
        row.addWidget(scan_all_btn)
        row.addStretch(1)
        lay.addLayout(row)
        add_btn.clicked.connect(self._add_root)
        add_common_btn.clicked.connect(self._add_common_dirs)
        add_disk_btn.clicked.connect(self._add_drive)
        rm_btn.clicked.connect(self._remove_root)
        scan_sel_btn.clicked.connect(lambda: self._scan(selected_only=True))
        scan_all_btn.clicked.connect(lambda: self._scan(selected_only=False))

        excl_note = QLabel("排除设置：被排除的目录与文件类型在扫描时会被跳过。")
        excl_note.setObjectName("sub")
        lay.addWidget(excl_note)
        excl_row = QHBoxLayout()
        self.excl_dirs = QListWidget()
        self.excl_dirs.setMaximumHeight(90)
        excl_row.addWidget(self.excl_dirs, 1)
        excl_btns = QVBoxLayout()
        excl_add = QPushButton("添加目录")
        excl_rm = QPushButton("移除")
        excl_btns.addWidget(excl_add)
        excl_btns.addWidget(excl_rm)
        excl_btns.addStretch(1)
        excl_row.addLayout(excl_btns)
        lay.addLayout(excl_row)
        excl_add.clicked.connect(self._add_excl_dir)
        excl_rm.clicked.connect(self._remove_excl_dir)

        ext_row = QHBoxLayout()
        ext_row.addWidget(QLabel("排除的文件类型（扩展名，空格分隔）"))
        self.ext_edit = QLineEdit()
        self.ext_edit.setPlaceholderText("例如 jpg png tmp")
        ext_row.addWidget(self.ext_edit, 1)
        lay.addLayout(ext_row)
        self.ext_edit.editingFinished.connect(self._save_excl_exts)
        self._load_excludes()
        return page

    def _tab_maintenance(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        title = QLabel("索引库维护")
        title.setObjectName("title")
        lay.addWidget(title)
        self.maint_label = QLabel("")
        self.maint_label.setWordWrap(True)
        self.maint_label.setObjectName("sub")
        lay.addWidget(self.maint_label)

        row = QHBoxLayout()
        self.vacuum_btn = QPushButton("压缩索引（回收空间）")
        self.vacuum_btn.setObjectName("primary")
        self.integrity_btn = QPushButton("检查索引完整性")
        row.addWidget(self.vacuum_btn)
        row.addWidget(self.integrity_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.vacuum_btn.clicked.connect(self._vacuum)
        self.integrity_btn.clicked.connect(self._check_integrity)

        # ---- 关键词 FTS 加速（默认开启，可关闭释放空间） ----
        fts_note = QLabel(
            "关键词 FTS 加速（默认开启）：用 trigram 索引加速文件名关键词匹配。\n"
            "· 实测 167 万行索引：高选择性词（如完整英文名、数字串）可提速几十倍；\n"
            "  常见短词与高频词速度相当（检索正确性不受影响，短词自动走原路径）；\n"
            "· 代价是磁盘占用：167 万行实测约 +1.2GB（索引体积）；\n"
            "· 第一次会后台构建（百万行约需两到三分钟），期间搜索照常（自动走原路径）；\n"
            "· 想省磁盘可关闭：关闭即删除该索引，搜索回退原方式，结果完全一致。"
        )
        fts_note.setWordWrap(True)
        fts_note.setObjectName("sub")
        lay.addWidget(fts_note)
        self.fts_check = QCheckBox("启用关键词 FTS 加速")
        self.fts_check.setChecked(bool(self.cfg.get("fts_enabled")))
        lay.addWidget(self.fts_check)
        self.fts_check.toggled.connect(self._toggle_fts)

        note = QLabel(
            "· 重新扫描只会更新索引内容，被删除文件的记录会移除，但数据库文件"
            "不会自动变小；「压缩索引」可把空间真正还给磁盘。\n"
            "· 压缩耗时随索引大小增长（百万级索引可能需要十几秒到一分钟），"
            "期间界面会短暂无响应，请勿关闭窗口。\n"
            "· 压缩不会影响原始文件，也不会丢失索引内容。"
        )
        note.setWordWrap(True)
        note.setObjectName("sub")
        lay.addWidget(note)

        # ---- 正文内容检索（可选、默认关闭） ----
        content_note = QLabel(
            "正文内容检索（默认关闭）：开启后会在本机读取白名单纯文本文件"
            "（txt/md/日志/csv/配置文件/源代码等）**开头最多 16KB** 的内容，"
            "存入本地索引用于“正文命中”搜索。\n"
            "· 只读文本，不解析 Office/PDF，不读取图片视频等二进制内容；\n"
            "· node_modules、site-packages、.git、build、dist 等依赖与构建"
            "目录默认跳过，避免索引体积膨胀；\n"
            "· 一切数据留在本机，绝不上传；\n"
            "· 受检索技术限制，2 个字的中文词无法用于正文匹配（仍可搜文件名）；\n"
            "· 后台增量构建，可随时关闭并在下方一键清空重建。"
        )
        content_note.setWordWrap(True)
        content_note.setObjectName("sub")
        lay.addWidget(content_note)
        self.content_check = QCheckBox("启用正文内容检索")
        self.content_check.setChecked(
            bool(self.cfg.get("content_search_enabled")))
        lay.addWidget(self.content_check)
        self.content_check.toggled.connect(self._toggle_content)

        self.content_label = QLabel("")
        self.content_label.setWordWrap(True)
        self.content_label.setObjectName("sub")
        lay.addWidget(self.content_label)
        content_row = QHBoxLayout()
        self.rebuild_content_btn = QPushButton("重建内容索引")
        content_row.addWidget(self.rebuild_content_btn)
        content_row.addStretch(1)
        lay.addLayout(content_row)
        self.rebuild_content_btn.clicked.connect(self._rebuild_content)

        # ---- 重复文件检测（可选、默认关闭） ----
        dup_note = QLabel(
            "重复文件检测（默认关闭）：后台计算每个文件的内容哈希，用于找出"
            "内容完全相同的重复文件（结果在工具栏「重复文件」里查看）。\n"
            "· 只在本机计算，不保存文件内容（哈希不可还原），绝不上传；\n"
            "· 超过 2GB 的文件自动跳过；\n"
            "· 后台增量进行，关掉窗口也会继续，可随时关闭开关。"
        )
        dup_note.setWordWrap(True)
        dup_note.setObjectName("sub")
        lay.addWidget(dup_note)
        self.dup_check = QCheckBox("启用重复文件检测")
        self.dup_check.setChecked(bool(self.cfg.get("dup_detect_enabled")))
        lay.addWidget(self.dup_check)
        self.dup_check.toggled.connect(self._toggle_dup)
        self.dup_label = QLabel("")
        self.dup_label.setWordWrap(True)
        self.dup_label.setObjectName("sub")
        lay.addWidget(self.dup_label)
        dup_row = QHBoxLayout()
        self.rebuild_dup_btn = QPushButton("全部重新检测")
        dup_row.addWidget(self.rebuild_dup_btn)
        dup_row.addStretch(1)
        lay.addLayout(dup_row)
        self.rebuild_dup_btn.clicked.connect(self._rebuild_dup)
        lay.addStretch(1)
        return page

    def _refresh_maintenance(self):
        try:
            size = self.db.disk_size()
        except Exception:
            size = 0
        try:
            files = self.db.count_files()
        except Exception:
            files = 0
        roots = len(self.cfg.get("exclude_dirs") or [])
        self.maint_label.setText(
            f"索引库大小：{human_size(size)}　·　已索引 {files} 个文件"
            f"　·　排除目录 {roots} 个"
        )

    def _vacuum(self):
        if QMessageBox.question(
            self, "压缩索引",
            "将压缩索引数据库并回收已删除记录占用的空间。\n\n"
            "库很大时可能需要十几秒到一分钟，期间界面会短暂无响应。"
            "是否继续？"
        ) != QMessageBox.StandardButton.Yes:
            return
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QApplication
        before = self.db.disk_size()
        QApplication.setOverrideCursor(_Qt.CursorShape.WaitCursor)
        try:
            self.db.vacuum()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "压缩失败", f"压缩索引时出错：{e}")
            return
        finally:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        after = self.db.disk_size()
        self._refresh_maintenance()
        self.refresh_stats()
        QMessageBox.information(
            self, "压缩完成",
            f"压缩前：{human_size(before)}\n压缩后：{human_size(after)}\n"
            f"释放：{human_size(max(0, before - after))}"
        )

    def _toggle_fts(self, checked: bool):
        if not checked:
            answer = QMessageBox.question(
                self, "关闭关键词 FTS 加速",
                "关闭后将删除 trigram 加速索引（167 万行约释放 1GB 磁盘空间），"
                "搜索自动回退原有方式：结果完全一致，高选择性词的加速效果消失。\n\n"
                "确定关闭并删除加速索引吗？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.fts_check.blockSignals(True)
                self.fts_check.setChecked(True)
                self.fts_check.blockSignals(False)
                return
            self.db.drop_files_fts()
        self.cfg.set("fts_enabled", checked)
        self.cfg.save()
        self.fts_changed.emit()

    def _check_integrity(self):
        ok = self.db.check_integrity()
        QMessageBox.information(
            self, "完整性检查",
            "索引数据库结构正常。" if ok else
            "检测到索引数据库异常。建议在“索引目录”里重新扫描，"
            "或删除数据目录中的索引文件后重建。"
        )

    def _toggle_content(self, checked: bool):
        if checked:
            answer = QMessageBox.question(
                self, "启用正文内容检索",
                "开启后，LostFile 会在本机读取【纯文本文件】（txt/md/日志/"
                "csv/配置文件/源代码等）开头最多 16KB 的内容，存入本地索引，"
                "用于按正文关键词命中文件。\n\n"
                "· 只读取文本，不解析 Office/PDF，不读图片视频；\n"
                "· node_modules、site-packages、.git、build、dist 等依赖与"
                "构建目录会被跳过；\n"
                "· 内容只存在于本机索引，绝不上传；\n"
                "· 后台增量构建，可随时关闭，也可点「重建内容索引」清空重来。\n\n"
                "确定开启吗？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.content_check.blockSignals(True)
                self.content_check.setChecked(False)
                self.content_check.blockSignals(False)
                return
        self.cfg.set("content_search_enabled", checked)
        self.cfg.save()
        self._refresh_content_progress()
        self.content_changed.emit()

    def _rebuild_content(self):
        if QMessageBox.question(
            self, "重建内容索引",
            "将清空已提取的正文内容并重新逐文件提取（不影响文件本身与"
            "其他索引）。确定重建吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.db.reset_content_index()
        self._refresh_content_progress()
        self.content_changed.emit()

    def _refresh_content_progress(self):
        done, total = self.db.content_stats()
        if done >= total:
            self.content_label.setText(
                f"内容索引已构建完成：{done} / {total} 个文件（仅文本类会提取正文）"
            )
        else:
            self.content_label.setText(
                f"内容索引进度：{done} / {total} 个文件（仅文本类会提取正文）"
                "（后台进行中，可关闭窗口稍后再看）"
            )

    def _toggle_dup(self, checked: bool):
        if checked:
            answer = QMessageBox.question(
                self, "启用重复文件检测",
                "开启后，LostFile 会在本机为每个文件计算内容哈希（读取文件"
                "内容做摘要），用于找出内容完全相同的重复文件。\n\n"
                "· 不会保存文件内容——哈希值不可还原出内容；\n"
                "· 一切计算只在本机，绝不上传；\n"
                "· 超过 2GB 的文件自动跳过；\n"
                "· 结果在工具栏「重复文件」里查看，删除走回收站。\n\n"
                "确定开启吗？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.dup_check.blockSignals(True)
                self.dup_check.setChecked(False)
                self.dup_check.blockSignals(False)
                return
        self.cfg.set("dup_detect_enabled", checked)
        self.cfg.save()
        self._refresh_dup_progress()
        self.hash_changed.emit()

    def _rebuild_dup(self):
        if QMessageBox.question(
            self, "全部重新检测",
            "将清空已有哈希结果并对所有文件重新计算（不影响文件本身，"
            "大索引可能需要较长时间）。确定重新检测吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.db.reset_hash_index()
        self._refresh_dup_progress()
        self.hash_changed.emit()

    def _refresh_dup_progress(self):
        done, total = self.db.hash_stats()
        if done >= total:
            self.dup_label.setText(f"重复检测已完成：{done} / {total} 个文件")
        else:
            self.dup_label.setText(
                f"重复检测进度：{done} / {total} 个文件"
                "（后台进行中，结果实时更新到工具栏「重复文件」）"
            )

    def _tab_ai(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        note = QLabel("AI 解析（可选、默认关闭）")
        note.setObjectName("title")
        lay.addWidget(note)
        intro = QLabel(
            "按下主界面「AI 解析」按钮时，把你输入的描述文字发送给 AI 服务，"
            "由它判断你描述的到底是什么（例如某个下载来的项目名、游戏名），"
            "再据此在【本机】直接搜索相关文件，并显示 AI 的判断结果。\n"
            "· 只发送描述文字；不上传文件名、路径、索引或任何文件内容；\n"
            "· 支持任意 OpenAI 兼容端点（DeepSeek / 通义 / 硅基流动等）；\n"
            "· 也支持本地 Ollama：端点填 http://localhost:11434/v1，"
            "Key 留空即可；\n"
            "· API Key 经 Windows DPAPI 加密后保存在本机（换用户或重装系统后"
            "需重新填写）。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("sub")
        lay.addWidget(intro)

        self.ai_enable_check = QCheckBox("启用「AI 解析」按钮")
        self.ai_enable_check.setChecked(bool(self.cfg.get("ai_enabled")))
        lay.addWidget(self.ai_enable_check)
        self.ai_enable_check.toggled.connect(self._toggle_ai_enabled)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("服务端点"))
        self.ai_base_edit = QLineEdit(
            str(self.cfg.get("ai_base_url") or ""))
        self.ai_base_edit.setPlaceholderText("https://api.deepseek.com/v1")
        row1.addWidget(self.ai_base_edit, 1)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("模型名称"))
        self.ai_model_edit = QLineEdit(str(self.cfg.get("ai_model") or ""))
        self.ai_model_edit.setPlaceholderText("deepseek-chat")
        row2.addWidget(self.ai_model_edit, 1)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("API Key"))
        self.ai_key_edit = QLineEdit(
            secrets.decrypt_str(self.cfg.get("ai_api_key_enc") or "") or "")
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key_edit.setPlaceholderText("本地 Ollama 可留空")
        row3.addWidget(self.ai_key_edit, 1)
        lay.addLayout(row3)

        btns = QHBoxLayout()
        self.ai_save_btn = QPushButton("保存")
        self.ai_save_btn.setObjectName("primary")
        self.ai_test_btn = QPushButton("测试连接（发送一句话验证）")
        self.ai_clear_key_btn = QPushButton("清除已保存的密钥")
        btns.addWidget(self.ai_save_btn)
        btns.addWidget(self.ai_test_btn)
        btns.addStretch(1)
        btns.addWidget(self.ai_clear_key_btn)
        lay.addLayout(btns)
        self.ai_save_btn.clicked.connect(self._save_ai)
        self.ai_test_btn.clicked.connect(self._test_ai)
        self.ai_clear_key_btn.clicked.connect(self._clear_ai_key)
        lay.addStretch(1)
        return page

    def _toggle_ai_enabled(self, checked: bool):
        self.cfg.set("ai_enabled", checked)
        self.cfg.save()

    def _save_ai(self):
        import re as _re
        base = self.ai_base_edit.text().strip().rstrip("/")
        if base and not _re.match(r"^https?://", base):
            QMessageBox.warning(self, "端点格式",
                                "服务端点需要以 http:// 或 https:// 开头。")
            return
        self.cfg.set("ai_base_url", base)
        self.cfg.set("ai_model", self.ai_model_edit.text().strip())
        key = self.ai_key_edit.text()
        if key:
            enc = secrets.encrypt_str(key)
            if enc is None:
                QMessageBox.warning(
                    self, "密钥加密失败",
                    "未能用 Windows DPAPI 加密密钥（可能被安全策略禁用），"
                    "本次不保存密钥。")
                return
            self.cfg.set("ai_api_key_enc", enc)
        self.cfg.save()
        QMessageBox.information(self, "已保存", "AI 服务配置已保存。")

    def _clear_ai_key(self):
        self.cfg.set("ai_api_key_enc", "")
        self.cfg.save()
        self.ai_key_edit.clear()
        QMessageBox.information(self, "已清除", "已清除本机保存的 API Key。")

    def _test_ai(self):
        self._save_ai()
        if not (self.cfg.get("ai_base_url") and self.cfg.get("ai_model")):
            return
        self.ai_test_btn.setEnabled(False)
        self.ai_test_btn.setText("测试中…")
        box: dict = {"r": None}

        def work():
            box["r"] = aimod.parse_with_llm("昨天的 PDF", self.cfg)

        threading.Thread(target=work, daemon=True, name="ai-test").start()

        def poll():
            if box["r"] is None:
                QTimer.singleShot(200, poll)
                return
            self.ai_test_btn.setEnabled(True)
            self.ai_test_btn.setText("测试连接（发送一句话验证）")
            conds, err = box["r"]
            if conds:
                QMessageBox.information(
                    self, "测试结果",
                    "连接成功：AI 服务已正确把示例描述拆解为搜索条件，"
                    "可以正常使用。")
            else:
                QMessageBox.warning(
                    self, "测试结果",
                    f"测试未通过：{err}\n\n请检查端点、模型名称与 API Key，"
                    "或网络是否可访问该服务。")

        poll()

    def _tab_vec(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        note = QLabel("本地语义搜索（可选、默认关闭）")
        note.setObjectName("title")
        lay.addWidget(note)
        intro = QLabel(
            "用本地向量模型（bge-small-zh-v1.5）给“文件名”编码，搜索时按语义"
            "相近度召回名称里没有关键词、但意思相关的文件，结果显示为分组树里的"
            "「语义相关」组。\n"
            "· 全程离线，模型与向量数据只存在本机；\n"
            "· 首次启用需联网下载一次模型（约 24MB，存到数据目录 models/）；\n"
            "· 百万文件的向量矩阵约为 512MB（int8、内存映射加载）；\n"
            "· 后台增量构建，可随时关闭，也可选择「删除向量数据」腾出空间。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("sub")
        lay.addWidget(intro)

        self.vec_enable_check = QCheckBox("启用「语义相关」分组")
        self.vec_enable_check.setChecked(bool(self.cfg.get("vec_enabled")))
        lay.addWidget(self.vec_enable_check)
        self.vec_enable_check.toggled.connect(self._toggle_vec)

        self.vec_status = QLabel("")
        self.vec_status.setWordWrap(True)
        self.vec_status.setObjectName("sub")
        lay.addWidget(self.vec_status)

        btns = QHBoxLayout()
        self.vec_download_btn = QPushButton("下载/更新向量模型")
        self.vec_rebuild_btn = QPushButton("重建向量索引")
        self.vec_delete_btn = QPushButton("删除向量数据")
        btns.addWidget(self.vec_download_btn)
        btns.addWidget(self.vec_rebuild_btn)
        btns.addStretch(1)
        btns.addWidget(self.vec_delete_btn)
        lay.addLayout(btns)
        self.vec_download_btn.clicked.connect(self._download_vec_model)
        self.vec_rebuild_btn.clicked.connect(self._rebuild_vec)
        self.vec_delete_btn.clicked.connect(self._delete_vec)
        lay.addStretch(1)
        return page

    def _toggle_vec(self, checked: bool):
        from .. import vecmodel
        if checked:
            if not vecmodel.model_present(self.cfg):
                answer = QMessageBox.question(
                    self, "启用语义搜索",
                    "启用后需要先联网下载一次向量模型：\n\n"
                    "· 模型：bge-small-zh-v1.5（量化版）\n"
                    "· 大小：约 24MB\n"
                    "· 存放：应用数据目录的 models/ 文件夹\n"
                    "· 来源：ModelScope（魔搭），失败自动换镜像\n\n"
                    "下载只发生这一次；之后全部在本机离线运行。\n"
                    "确定开启并在下方点击「下载/更新向量模型」吗？"
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self.vec_enable_check.blockSignals(True)
                    self.vec_enable_check.setChecked(False)
                    self.vec_enable_check.blockSignals(False)
                    return
        self.cfg.set("vec_enabled", checked)
        self.cfg.set("vec_consent", True)
        self.cfg.save()
        self._refresh_vec_status()
        self.vec_changed.emit()

    def _download_vec_model(self):
        from .. import vecmodel
        if QMessageBox.question(
            self, "下载向量模型",
            "将联网下载 bge-small-zh-v1.5 量化模型（约 24MB）到应用数据目录"
            " models/ 文件夹。下载期间可继续使用本应用。是否开始？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.vec_download_btn.setEnabled(False)
        self.vec_download_btn.setText("下载中…")
        box: dict = {"r": None}

        def work():
            box["r"] = vecmodel.download_model(self.cfg)

        threading.Thread(target=work, daemon=True, name="vec-download").start()

        def poll():
            if box["r"] is None:
                QTimer.singleShot(300, poll)
                return
            self.vec_download_btn.setEnabled(True)
            self.vec_download_btn.setText("下载/更新向量模型")
            err = box["r"]
            if err:
                QMessageBox.warning(self, "下载失败", f"{err}\n\n请检查网络后重试。")
            else:
                QMessageBox.information(
                    self, "下载完成",
                    "向量模型已就绪。已开启语义搜索时会自动开始在后台"
                    "建立文件名向量（进度见上方状态）。")
            self._refresh_vec_status()
            self.vec_changed.emit()

        poll()

    def _rebuild_vec(self):
        from .. import vecindex
        from .. import vecmodel
        if QMessageBox.question(
            self, "重建向量索引",
            "将清空已建好的文件名向量并从头重建（百万文件约需几十分钟，"
            "后台进行，不影响正常搜索）。确定重建吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        for p in vecindex.vec_paths(self.cfg).values():
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        vecmodel.reset_embedder()
        self.db.reset_vec_index()
        self._refresh_vec_status()
        self.vec_changed.emit()

    def _delete_vec(self):
        from .. import vecindex
        from .. import vecmodel
        if QMessageBox.question(
            self, "删除向量数据",
            "将删除全部文件名向量（约几百 MB 磁盘空间）并关闭语义搜索。"
            "原始文件与其它索引不受影响。确定删除吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        for p in vecindex.vec_paths(self.cfg).values():
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        vecmodel.reset_embedder()
        self.db.reset_vec_index()
        self.cfg.set("vec_enabled", False)
        self.cfg.save()
        self.vec_enable_check.blockSignals(True)
        self.vec_enable_check.setChecked(False)
        self.vec_enable_check.blockSignals(False)
        self._refresh_vec_status()
        self.vec_changed.emit()

    def _refresh_vec_status(self):
        from .. import vecmodel
        lines = []
        if vecmodel.model_present(self.cfg):
            lines.append("向量模型：已就绪")
        else:
            lines.append("向量模型：未下载（点击下方按钮下载）")
        done, total = self.db.vec_stats()
        if done >= total:
            lines.append(f"文件名向量：已完成 {done} / {total}")
        else:
            lines.append(f"文件名向量：{done} / {total}（后台进行中）")
        self.vec_status.setText("　·　".join(lines))

    def _tab_privacy(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("sub")
        lay.addWidget(self.stats_label)

        clear_index_btn = QPushButton("清除全部索引")
        clear_index_btn.setObjectName("danger")
        clear_history_btn = QPushButton("清除搜索历史")
        clear_history_btn.setObjectName("danger")
        self.keep_check = QCheckBox("退出时保留索引")
        self.keep_check.setChecked(bool(self.cfg.get("keep_index_on_exit")))
        row = QHBoxLayout()
        row.addWidget(clear_index_btn)
        row.addWidget(clear_history_btn)
        row.addWidget(self.keep_check)
        row.addStretch(1)
        lay.addLayout(row)
        clear_index_btn.clicked.connect(self._clear_index)
        clear_history_btn.clicked.connect(self._clear_history)
        self.keep_check.toggled.connect(
            lambda v: (self.cfg.set("keep_index_on_exit", v), self.cfg.save())
        )

        recent_note = QLabel(
            "“最近打开”导入（默认关闭）："
            "开启后将读取 Windows“最近使用文件”列表中的快捷方式目标路径与时间，"
            "帮助你找回最近打开过的文件。\n仅读取路径和时间；来源应用无法可靠得知"
            "（显示“未知”）；不会读取文件内容、浏览器密码、Cookie 等敏感数据。"
        )
        recent_note.setWordWrap(True)
        recent_note.setObjectName("sub")
        lay.addWidget(recent_note)
        self.recent_check = QCheckBox("启用“最近打开”导入")
        self.recent_check.setChecked(bool(self.cfg.get("recent_import_enabled")))
        lay.addWidget(self.recent_check)
        self.recent_check.toggled.connect(self._toggle_recent)

        browser = QTextBrowser()
        browser.setMarkdown(PRIVACY_TEXT)
        lay.addWidget(browser, 1)
        return page

    def _tab_appearance(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        note = QLabel("主题")
        note.setObjectName("title")
        lay.addWidget(note)
        self.radio_light = QRadioButton("浅色")
        self.radio_dark = QRadioButton("深色")
        self.radio_system = QRadioButton("跟随系统")
        current = self.cfg.get("theme")
        for radio, mode in ((self.radio_light, "light"), (self.radio_dark, "dark"),
                            (self.radio_system, "system")):
            radio.setChecked(current == mode)
            radio.toggled.connect(
                lambda checked, m=mode: self._on_theme(m) if checked else None
            )
        lay.addWidget(self.radio_light)
        lay.addWidget(self.radio_dark)
        lay.addWidget(self.radio_system)
        lay.addStretch(1)
        return page

    def _tab_about(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        title = QLabel(APP_DISPLAY_NAME)
        title.setObjectName("hero")
        lay.addWidget(title)
        info = QLabel(
            f"版本 {__version__}\n\n"
            "LostFile 是一款本地文件查找工具：\n"
            "按你记忆中的文件特征（类型、时间、大小、位置）多条件搜索，\n"
            "支持中文自然语言描述解析与匹配原因解释。\n\n"
            "· 默认不联网、不读文件内容；联网与内容读取均为默认关闭、\n"
            "  经确认后开启的可选功能\n"
            "· 开源协议：MIT License\n"
            "· 核心搜索无需云服务、API Key 或数据库服务器"
        )
        info.setWordWrap(True)
        info.setObjectName("sub")
        lay.addWidget(info)
        lay.addStretch(1)
        return page

    # ---------- 行为 ----------

    def _selected_root_ids(self) -> list[int]:
        out = []
        roots = self.db.list_roots()
        for i in self.roots_list.selectedItems():
            row = self.roots_list.row(i)
            if 0 <= row < len(roots):
                out.append(roots[row]["id"])
        return out

    def refresh_roots(self):
        roots = self.db.list_roots()
        stats = self.db.roots_stats()
        self.roots_list.clear()
        for r in roots:
            status = {"idle": "待扫描", "scanning": "扫描中", "done": "完成",
                      "stopped": "已中止"}.get(r["scan_status"], r["scan_status"])
            text = (f"{r['display_name']} — {r['path']}\n"
                    f"    {status} · {stats.get(r['id'], 0)} 个文件"
                    + (f" · 错误 {r['last_error_count']}" if r["last_error_count"] else ""))
            item = self._text_item(text)
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            self.roots_list.addItem(item)

    @staticmethod
    def _text_item(text):
        from PySide6.QtWidgets import QListWidgetItem
        return QListWidgetItem(text)

    def refresh_stats(self):
        self.stats_label.setText(
            f"当前索引：{len(self.db.list_roots())} 个目录，"
            f"{self.db.count_files()} 个文件（数据目录：{self.cfg.data_dir}）"
        )
        self._refresh_maintenance()
        self._refresh_content_progress()
        self._refresh_dup_progress()
        self._refresh_vec_status()

    def _add_root(self):
        d = QFileDialog.getExistingDirectory(self, "选择要索引的文件夹")
        if d:
            self.db.add_root(d)
            self.refresh_roots()
            self.refresh_stats()
            self.roots_changed.emit()

    def _add_common_dirs(self):
        """一键添加常用目录（已知文件夹 + 各盘探测到的桌面/下载/文档等）。"""
        existing = {os.path.normcase(os.path.abspath(r["path"]))
                    for r in self.db.list_roots()}
        candidates = [d for d in kf.suggested_dirs()
                      if os.path.normcase(d["path"]) not in existing]
        if not candidates:
            QMessageBox.information(
                self, "常用目录",
                "常用目录都已加入索引。可以用「添加目录…」自行选择其他文件夹。"
            )
            return
        dlg = _CommonDirsDialog(candidates, self)
        if not dlg.exec() or not dlg.selected():
            return
        for path in dlg.selected():
            self.db.add_root(path)
        self.refresh_roots()
        self.refresh_stats()
        self.roots_changed.emit()
        roots = [r for r in self.db.list_roots()
                 if os.path.normcase(os.path.abspath(r["path"]))
                 in {os.path.normcase(os.path.abspath(p))
                     for p in dlg.selected()}]
        if roots:
            self.scan_requested.emit(roots)

    def _add_drive(self):
        """添加整个磁盘（显式选择，扫描量大）。"""
        dlg = _DrivePickDialog(self)
        if dlg.exec() and dlg.selected():
            for drive in dlg.selected():
                self.db.add_root(drive)
            self.refresh_roots()
            self.refresh_stats()
            self.roots_changed.emit()

    def _remove_root(self):
        ids = self._selected_root_ids()
        if not ids:
            return
        if QMessageBox.question(
            self, "移除索引目录",
            "将移除所选目录的索引记录（不会删除磁盘上的任何文件）。继续吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        for rid in ids:
            self.db.remove_root(rid)
        self.refresh_roots()
        self.refresh_stats()
        self.roots_changed.emit()

    def _scan(self, selected_only: bool):
        roots = self.db.list_roots()
        if selected_only:
            ids = set(self._selected_root_ids())
            roots = [r for r in roots if r["id"] in ids]
        if not roots:
            QMessageBox.information(self, "提示", "没有可扫描的目录，请先添加。")
            return
        self.scan_requested.emit(roots)

    # ---- 排除项 ----

    def _load_excludes(self):
        self.excl_dirs.clear()
        self.excl_dirs.addItems(self.cfg.get("exclude_dirs") or [])
        self.ext_edit.setText(" ".join(self.cfg.get("exclude_exts") or []))

    def _add_excl_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择要排除的文件夹")
        if d:
            dirs = list(self.cfg.get("exclude_dirs") or [])
            if d not in dirs:
                dirs.append(d)
                self.cfg.set("exclude_dirs", dirs)
                self.cfg.save()
                self.excl_dirs.addItem(d)

    def _remove_excl_dir(self):
        row = self.excl_dirs.currentRow()
        if row >= 0:
            dirs = list(self.cfg.get("exclude_dirs") or [])
            if 0 <= row < len(dirs):
                dirs.pop(row)
                self.cfg.set("exclude_dirs", dirs)
                self.cfg.save()
                self.excl_dirs.takeItem(row)

    def _save_excl_exts(self):
        exts = [e.strip().lower().lstrip(".")
                for e in self.ext_edit.text().split() if e.strip()]
        self.cfg.set("exclude_exts", exts)
        self.cfg.save()

    # ---- 隐私操作 ----

    def _clear_index(self):
        if QMessageBox.question(
            self, "清除全部索引",
            "将删除全部索引记录与索引目录列表（磁盘上的原始文件不受任何影响，"
            "收藏与搜索历史保留）。之后需要重新添加目录并扫描。确定清除吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.db.clear_index()
        self.refresh_roots()
        self.refresh_stats()
        self.roots_changed.emit()

    def _clear_history(self):
        if QMessageBox.question(
            self, "清除搜索历史", "确定清除全部本地搜索历史吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.db.clear_search_history()

    def _toggle_recent(self, checked: bool):
        if checked and not self.cfg.get("recent_import_enabled"):
            answer = QMessageBox.question(
                self, "启用“最近打开”导入",
                "开启后，LostFile 会读取 Windows“最近使用文件”列表"
                "（%APPDATA%\\Microsoft\\Windows\\Recent 下的快捷方式），"
                "仅提取目标文件路径与时间；来源应用无法可靠得知，显示“未知”。\n\n"
                "不会读取文件内容，也不会读取浏览器密码、Cookie 等敏感数据。\n\n"
                "确定开启吗？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.recent_check.blockSignals(True)
                self.recent_check.setChecked(False)
                self.recent_check.blockSignals(False)
                return
        self.cfg.set("recent_import_enabled", checked)
        self.cfg.save()

    def _on_theme(self, mode: str):
        self.cfg.set("theme", mode)
        self.cfg.save()
        self.theme_changed.emit(mode)

    def select_tab(self, index: int):
        self.tabs.setCurrentIndex(index)


class _CommonDirsDialog(QDialog):
    """一键添加常用目录：列出探测到的目录，默认全部勾选。"""

    def __init__(self, candidates: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加常用目录")
        self.resize(520, 380)
        lay = QVBoxLayout(self)
        tip = QLabel(
            "下面是在本机探测到的常用文件夹（含其他盘上的桌面/下载/文档等）。"
            "这些目录通常体积不大，扫描很快，能覆盖你平时放文件的地方。"
        )
        tip.setWordWrap(True)
        tip.setObjectName("sub")
        lay.addWidget(tip)

        self._checks: list[tuple[str, QCheckBox]] = []
        for item in candidates:
            row = QHBoxLayout()
            chk = QCheckBox(item["label"])
            chk.setChecked(True)
            path_label = QLabel(item["path"])
            path_label.setObjectName("sub")
            path_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(chk, 0)
            row.addWidget(path_label, 1)
            lay.addLayout(row)
            self._checks.append((item["path"], chk))
        lay.addStretch(1)

        btns = QHBoxLayout()
        sel_all = QPushButton("全选")
        sel_none = QPushButton("全不选")
        ok_btn = QPushButton("添加并扫描")
        ok_btn.setObjectName("primary")
        cancel_btn = QPushButton("取消")
        btns.addWidget(sel_all)
        btns.addWidget(sel_none)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        lay.addLayout(btns)
        sel_all.clicked.connect(lambda: self._set_all(True))
        sel_none.clicked.connect(lambda: self._set_all(False))
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

    def _set_all(self, value: bool):
        for _path, chk in self._checks:
            chk.setChecked(value)

    def selected(self) -> list[str]:
        return [p for p, chk in self._checks if chk.isChecked()]


class _DrivePickDialog(QDialog):
    """选择要整体索引的固定磁盘。默认全部不勾选。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加整个磁盘")
        self.resize(420, 240)
        lay = QVBoxLayout(self)
        tip = QLabel(
            "索引整个磁盘扫描量大、耗时长，可随时暂停或取消。\n"
            "无法访问的系统目录会自动跳过并计数。"
        )
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self._checks: dict[str, QCheckBox] = {}
        for drive in drives.list_fixed_drives():
            chk = QCheckBox(f"{drive[0:2]} 盘（{drive}）")
            chk.toggled.connect(self._sync)
            self._checks[drive] = chk
            lay.addWidget(chk)
        if not self._checks:
            lay.addWidget(QLabel("未发现可索引的固定磁盘。"))
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.ok_btn = QPushButton("添加所选磁盘")
        self.ok_btn.setObjectName("primary")
        cancel_btn = QPushButton("取消")
        btns.addWidget(cancel_btn)
        btns.addWidget(self.ok_btn)
        lay.addLayout(btns)
        cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.accept)
        self._sync()

    def _sync(self):
        self.ok_btn.setEnabled(any(c.isChecked() for c in self._checks.values()))

    def selected(self) -> list[str]:
        return [d for d, c in self._checks.items() if c.isChecked()]