"""扩展名 → 文件类型分类。全部扩展名小写、不带点。"""

CATEGORIES: dict[str, set[str]] = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".ico", ".tif",
              ".tiff", ".svg", ".heic", ".heif", ".raw", ".psd", ".ai", ".dng"},
    "video": {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v",
              ".mpg", ".mpeg", ".ts", ".3gp", ".rmvb", ".rm", ".vob"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".ape",
              ".opus", ".mid", ".midi", ".amr"},
    "pdf": {".pdf"},
    "word": {".doc", ".docx", ".docm", ".dot", ".dotx", ".wps", ".rtf", ".odt"},
    "excel": {".xls", ".xlsx", ".xlsm", ".xlsb", ".csv", ".et", ".ods"},
    "ppt": {".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".dps", ".odp"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz",
                ".iso", ".cab", ".zst", ".lz4"},
    "installer": {".exe", ".msi", ".msix", ".appx", ".dmg", ".apk", ".deb",
                  ".rpm", ".bat", ".cmd"},
    "text": {".txt", ".md", ".log", ".ini", ".json", ".xml", ".yaml", ".yml",
             ".toml", ".cfg", ".conf", ".nfo"},
    "code": {".py", ".pyw", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp",
             ".h", ".hpp", ".cs", ".go", ".rs", ".php", ".rb", ".sql", ".html",
             ".htm", ".css", ".sh", ".ps1", ".kt", ".swift", ".vue", ".lua"},
}

LABELS: dict[str, str] = {
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "pdf": "PDF",
    "word": "Word 文档",
    "excel": "Excel 表格",
    "ppt": "PPT 演示文稿",
    "archive": "压缩包",
    "installer": "安装包",
    "text": "文本文件",
    "code": "代码文件",
    "other": "其他文件",
}

ALL_KNOWN_EXTS: set[str] = set().union(*CATEGORIES.values())


def category_of_ext(ext: str) -> str:
    e = ("." + ext.lstrip(".")).lower() if ext else ""
    for cat, exts in CATEGORIES.items():
        if e in exts:
            return cat
    return "other"


def exts_of_categories(categories: list[str]) -> set[str]:
    """返回一组类型对应的扩展名集合（含 other 时由调用方特殊处理）。"""
    out: set[str] = set()
    for c in categories:
        if c in CATEGORIES:
            out |= CATEGORIES[c]
    return out


def human_size(n) -> str:
    """把字节数转成人类可读文本，如 3.0 MB / 512 KB。"""
    if n is None:
        return "未知"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "未知"
    if n < 0:
        return "未知"
    units = [("B", 1), ("KB", 1024), ("MB", 1024 ** 2), ("GB", 1024 ** 3), ("TB", 1024 ** 4)]
    for name, base in units:
        if n < base * 1024 or name == "TB":
            if name == "B":
                return f"{int(n)} B"
            return f"{n / base:.1f} {name}"
    return f"{n / (1024 ** 4):.1f} TB"