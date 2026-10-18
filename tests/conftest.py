"""pytest 公共夹具。测试全部使用临时目录，不触碰真实数据目录。"""
import os
import sys

# 无界面平台必须在导入 PySide6 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402


def make_file(path, size: int = 100, mtime: float | None = None):
    """在指定位置创建指定大小的文件；mtime 为 unix 时间戳。返回原路径对象。"""
    spath = os.fspath(path)
    os.makedirs(os.path.dirname(spath), exist_ok=True)
    with open(spath, "wb") as f:
        f.write(b"x" * size)
    if mtime is not None:
        os.utime(spath, (mtime, mtime))
    return path


@pytest.fixture(scope="session")
def app():
    """offscreen QApplication（会话级复用）。"""
    from PySide6.QtWidgets import QApplication
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
        inst.setStyle("Fusion")
    return inst


@pytest.fixture(scope="session", autouse=True)
def _cleanup_widgets(app):
    """会话结束时清理所有顶层窗口，避免 offscreen 平台退出阶段的崩溃。"""
    yield
    from PySide6.QtWidgets import QApplication
    for w in list(QApplication.topLevelWidgets()):
        try:
            w.close()
            w.deleteLater()
        except Exception:
            pass
    app.processEvents()
    app.setStyleSheet("")


def wait_until(fn, timeout: float = 10.0, interval: float = 0.02) -> bool:
    """轮询等待 fn() 为真。"""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return fn()


def purge_from_recycle_bin(original_paths: list[str]) -> None:
    """把测试删进回收站的文件**永久清掉**（只匹配“原始路径”，绝不误删其它条目）。

    回收站每个条目由一对 $Ixxxxx.ext（元数据，记录删除前的原始完整路径）
    与 $Rxxxxx.ext（内容）组成。这里只读取 $I 元数据，原始路径与给定列表
    完全一致时，才连同 $R 一起 os.remove（绕过回收站、直接抹除）。

    用途：回收站回归测试在验证功能后自清理，不往用户回收站留垃圾。
    """
    if os.name != "nt" or not original_paths:
        return
    import struct

    targets = {os.path.normcase(os.path.abspath(p)) for p in original_paths}

    def _orig_path(meta_file: str) -> str | None:
        try:
            with open(meta_file, "rb") as f:
                data = f.read(8192)
        except OSError:
            return None
        if len(data) < 28:
            return None
        version = struct.unpack_from("<Q", data, 0)[0]
        if version != 2:          # Vista+ 的 $I 为 v2 格式；v1 不存在于现代系统
            return None
        name_len = struct.unpack_from("<I", data, 24)[0]
        if 28 + name_len * 2 > len(data):
            return None
        try:
            name = data[28:28 + name_len * 2].decode("utf-16-le", "replace")
        except Exception:
            return None
        return name.rstrip("\x00").rstrip()

    import string
    for letter in string.ascii_uppercase:
        rb = f"{letter}:\\$Recycle.Bin"
        if not os.path.isdir(rb):
            continue
        try:
            sid_dirs = os.listdir(rb)
        except PermissionError:
            continue          # 其他用户/系统的 SID 目录无权访问
        for sid_dir in sid_dirs:
            base = os.path.join(rb, sid_dir)
            if not os.path.isdir(base):
                continue
            try:
                names = os.listdir(base)
            except PermissionError:
                continue
            for fname in names:
                if not fname.upper().startswith("$I"):
                    continue
                meta = os.path.join(base, fname)
                orig = _orig_path(meta)
                if not orig or os.path.normcase(os.path.abspath(orig)) not in targets:
                    continue
                for victim in (meta, os.path.join(base, "$R" + fname[2:])):
                    try:
                        os.remove(victim)
                    except OSError:
                        pass
                print(f"[tests] 已从回收站永久清除测试文件：{orig}")