"""纯文本文件内容提取（内容全文检索用）。

只提取白名单扩展名的纯文本；二进制（含 NUL）直接跳过。零第三方依赖。
编码探测顺序：BOM → utf-8 严格 → gbk 严格 → utf-8 容错。
"""
import os

MAX_TEXT_BYTES = 16 * 1024    # 每文件最多读取/入库字节数

TEXT_EXTS = {
    "txt", "md", "log", "ini", "json", "xml", "yaml", "yml", "toml",
    "cfg", "conf", "nfo", "csv", "tsv",
    "py", "pyw", "js", "ts", "tsx", "jsx", "java", "c", "h", "cpp",
    "hpp", "cs", "go", "rs", "php", "rb", "sql", "html", "htm", "css",
    "sh", "ps1", "cmd", "bat", "kt", "swift", "vue", "lua", "m", "mm",
}

# 依赖/构建/缓存目录：这些目录里的文本（node_modules 里的 .js、site-packages
# 里的 .py 等）数量巨大且几乎不会有人“按内容找文件”，索引它们会让正文索引
# 膨胀数倍（实测占全部内容体积的八成以上）。默认跳过；按路径分段精确匹配，
# 不比对文件名，避免误伤名字里含这些词的普通文件。
SKIP_DIR_NAMES = {
    "node_modules", "bower_components", "jspm_packages",
    "site-packages", "dist-packages", "dist-info", "egg-info",
    ".git", ".hg", ".svn", "venv", ".venv", ".tox", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "vendor", "third_party", "target", ".next", ".nuxt",
    "build", "dist", "coverage", ".idea",
}


def is_text_ext(ext: str) -> bool:
    return ext.lower().lstrip(".") in TEXT_EXTS


def is_skippable_path(path: str) -> bool:
    """路径中是否出现依赖/构建/缓存目录名（只比对目录分段，不含文件名）。"""
    parts = path.replace("\\", "/").split("/")
    return any(p.lower() in SKIP_DIR_NAMES for p in parts[:-1])


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def extract_text(path: str, max_bytes: int = MAX_TEXT_BYTES) -> str | None:
    """读取文件开头的文本内容。失败/二进制返回 None。"""
    try:
        size = os.path.getsize(path)
        if size <= 0:
            return None
        with open(path, "rb") as f:
            data = f.read(min(size, max_bytes))
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None          # 二进制内容：跳过
    return _decode(data)