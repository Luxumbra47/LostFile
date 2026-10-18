"""主题：Windows 原生风格控件 + 明确的明暗文字对比；支持浅色/深色/跟随系统。

对比度约定（用户明确要求）：
- 浅色主题：文字统一为黑色系（#1a1a1a），次要文字 #4a4a4a；
- 深色主题：文字统一为白色系（#ffffff），次要文字 #b8b8b8；
- 控件几何/边框仍交由 Windows 原生样式绘制，仅显式声明文字/表面颜色，
  保证两种外观下文字始终清晰可读。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

_PALETTES = {
    "light": {
        "ACCENT": "#0067c0",        # Windows 强调色
        "ACCENT_HOVER": "#1975c5",
        "ACCENT_PRESSED": "#005a9e",
        "ACCENT_TEXT": "#ffffff",
        "TEXT": "#1a1a1a",          # 正文：黑
        "SUB": "#4a4a4a",           # 次要：深灰（可读）
        "HEADER": "#3a3a3a",
        "CHIP_BG": "#eef3fa",
        "DANGER": "#c42b1c",
        "MUTED": "#c8c8c8",
        "BORDER": "#c8c8c8",
        "PLACEHOLDER": "#6e6e6e",
        "SEL_BG": "#cce4f7",
        "SEL_TEXT": "#1a1a1a",
    },
    "dark": {
        "ACCENT": "#4cc2ff",
        "ACCENT_HOVER": "#63ccff",
        "ACCENT_PRESSED": "#3aa0d1",
        "ACCENT_TEXT": "#00263b",
        "TEXT": "#ffffff",          # 正文：白
        "SUB": "#b8b8b8",           # 次要：浅灰（可读）
        "HEADER": "#d0d0d0",
        "CHIP_BG": "#2a3a4d",
        "DANGER": "#ff99a4",
        "MUTED": "#5a5a5a",
        "BORDER": "#404040",
        "PLACEHOLDER": "#8a8a8a",
        "SEL_BG": "#2f4d75",
        "SEL_TEXT": "#ffffff",
    },
}

# 只声明颜色类属性；尺寸/边框等仍由原生风格绘制，保持 Windows 观感
_TEMPLATE = """
QLabel { color: $TEXT; }
QLabel#sub { color: $SUB; }
QLabel#title { color: $TEXT; font-size: 15px; font-weight: 600; }
QLabel#hero { color: $TEXT; font-size: 19px; font-weight: 700; }
QCheckBox, QRadioButton { color: $TEXT; }
QGroupBox { color: $TEXT; }
QGroupBox::title { color: $TEXT; }
QPushButton, QToolButton { color: $TEXT; }
QPushButton:checked { color: $TEXT; }
QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox, QSpinBox { color: $TEXT; }
QComboBox QAbstractItemView { color: $TEXT; }
QTableWidget, QTableView { color: $TEXT; }
QListWidget, QListWidget::item { color: $TEXT; }
QTreeView, QTreeWidget { color: $TEXT; }
QTableWidget::item:selected, QTableWidget::item:selected:hover,
QListWidget::item:selected, QTreeWidget::item:selected {
    color: $SEL_TEXT; background: $SEL_BG;
}
QHeaderView::section { color: $HEADER; }
QMenu { color: $TEXT; }
QStatusBar { color: $SUB; }
QStatusBar QLabel { color: $SUB; }
QToolTip { color: $TEXT; }
QProgressBar { color: $TEXT; }

QPushButton#primary {
    background-color: $ACCENT; color: $ACCENT_TEXT;
    border: 1px solid $ACCENT; border-radius: 4px; padding: 6px 14px;
}
QPushButton#primary:hover { background-color: $ACCENT_HOVER; }
QPushButton#primary:pressed { background-color: $ACCENT_PRESSED; }
QPushButton#primary:disabled { background-color: $MUTED; color: $SUB; }
QPushButton#danger { color: $DANGER; }
QFrame#condBar { border: 1px solid $BORDER; border-radius: 4px; }
QFrame#hintBar { border: 1px dashed $BORDER; border-radius: 4px; }
"""


def _mini(qss_template: str, colors: dict) -> str:
    out = qss_template
    for k, v in colors.items():
        out = out.replace(f"${k}", v)
    return out


def build_palette(mode: str) -> QPalette:
    """构造与样式表一致的系统调色板（浅色黑字 / 深色白字）。"""
    colors = _PALETTES[mode]
    p = QPalette()
    if mode == "dark":
        window, text, base, sub = ("#202020", "#ffffff", "#1f1f1f", "#777777")
        hl, hlt, link = ("#2f6fd8", "#ffffff", "#4cc2ff")
    else:
        window, text, base, sub = ("#f3f3f3", "#1a1a1a", "#ffffff", "#666666")
        hl, hlt, link = ("#0067c0", "#ffffff", "#0067c0")
    p.setColor(QPalette.ColorRole.Window, QColor(window))
    p.setColor(QPalette.ColorRole.WindowText, QColor(text))
    p.setColor(QPalette.ColorRole.Base, QColor(base))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["CHIP_BG"]))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(base))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(text))
    p.setColor(QPalette.ColorRole.Text, QColor(text))
    p.setColor(QPalette.ColorRole.Button, QColor(window if mode == "light" else "#2b2b2b"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(text))
    p.setColor(QPalette.ColorRole.BrightText, QColor(colors["DANGER"]))
    p.setColor(QPalette.ColorRole.Highlight, QColor(hl))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(hlt))
    p.setColor(QPalette.ColorRole.Link, QColor(link))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["PLACEHOLDER"]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(sub))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight,
               QColor("#3a3a3a" if mode == "dark" else "#d0d0d0"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText,
               QColor("#a0a0a0" if mode == "dark" else "#666666"))
    return p


def resolve_mode(requested: str, app=None) -> str:
    """把 system/light/dark 解析为实际模式。"""
    if requested in ("light", "dark"):
        return requested
    if app is not None:
        try:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
            if scheme == Qt.ColorScheme.Light:
                return "light"
        except AttributeError:
            pass
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if val else "dark"
    except Exception:
        return "light"


def apply_theme(app, requested: str) -> str:
    """应用主题并返回实际生效的模式；浅色黑字、深色白字。"""
    mode = resolve_mode(requested, app)
    app.setPalette(build_palette(mode))
    app.setStyleSheet(_mini(_TEMPLATE, _PALETTES[mode]))
    return mode