"""目录扫描与增量索引。

纯 Python 实现（不依赖 Qt），便于测试；UI 层用 QThread 包装。
支持：增量 tick、取消/暂停、错误收集、排除目录/扩展名、长路径（\\\\?\\ 前缀）、
符号链接跳过（防环）。
"""
import os
import threading
from dataclasses import dataclass, field

from . import pinyin as _pinyin


def long_path(p: str) -> str:
    """给绝对路径加 \\\\?\\ 前缀以支持超长路径。"""
    if p.startswith("\\\\?\\"):
        return p
    return "\\\\?\\" + os.path.abspath(p)


def display_path(p: str) -> str:
    if p.startswith("\\\\?\\"):
        return p[4:]
    return p


def safe_text(s: str | None) -> str:
    """将可能含孤立代理字符（Windows 允许存在的“非法 Unicode”文件名）
    的字符串替换为安全文本，避免后续 SQLite 绑定 / JSON 序列化抛出
    UnicodeEncodeError 令整个扫描中断。"""
    if not s:
        return ""
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def normcase_path(p: str) -> str:
    """用于比较的路径标准化（Windows 大小写不敏感）。"""
    return os.path.normcase(os.path.normpath(p)).rstrip("\\/").lower()


@dataclass
class ScanResult:
    completed: bool                # False = 被取消/暂停
    files_indexed: int = 0
    dirs_visited: int = 0
    errors: int = 0
    error_samples: list[tuple[str, str]] = field(default_factory=list)  # (路径, 原因)


MAX_ERROR_SAMPLES = 50
BATCH_SIZE = 500


def scan_directory(
    root: str,
    db,
    exclude_dirs: set[str] | None = None,
    exclude_exts: set[str] | None = None,
    on_progress=None,
    on_dir=None,
    cancel_event: threading.Event | None = None,
    pause_event: threading.Event | None = None,
) -> ScanResult:
    """扫描一个根目录并写入索引。返回 ScanResult。

    用户必须在调用前完成 root 注册（db.add_root）并传入其路径；
    目录会被完整走查，文件 UPSERT 到当前 tick，完成后删除过期行。
    on_dir(d)：每次进入目录前回调（诊断用，回调异常会被忽略）。
    """
    exclude_dirs = {normcase_path(d) for d in (exclude_dirs or set())}
    exclude_exts = {e.lower().lstrip(".") for e in (exclude_exts or set())}
    res = ScanResult(completed=True)

    conn = db.connect()
    try:
        tick = db.next_tick(conn)
        rows = db.list_roots()
        root_id = next((r["id"] for r in rows if normcase_path(r["path"]) == normcase_path(root)), None)
        if root_id is None:
            root_id = db.add_root(root)

        db.mark_root_scan_started(root_id)
        prefix_root = long_path(root)
        stack = [prefix_root]
        batch: list[tuple] = []

        def flush():
            if batch:
                db.upsert_many(conn, batch)
                batch.clear()
                conn.commit()

        while stack:
            if _is_cancelled(cancel_event):
                res.completed = False
                break
            _wait_pause(pause_event, cancel_event)
            if _is_cancelled(cancel_event):
                res.completed = False
                break

            d = stack.pop()
            try:
                if on_dir:
                    try:
                        on_dir(d)
                    except Exception:
                        pass
                it = os.scandir(d)
            except OSError as e:
                res.errors += 1
                _sample(res, display_path(d), f"无法读取目录：{e.strerror or e}")
                continue
            with it:
                try:
                    for entry in it:
                        if _is_cancelled(cancel_event):
                            res.completed = False
                            break
                        _wait_pause(pause_event, cancel_event)
                        try:
                            if entry.is_symlink():
                                continue  # 跳过链接，防止环路
                            if entry.is_dir(follow_symlinks=False):
                                disp = display_path(entry.path)
                                if normcase_path(disp) in exclude_dirs:
                                    continue
                                stack.append(entry.path)
                                res.dirs_visited += 1
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            disp = safe_text(display_path(entry.path))
                            name = safe_text(entry.name)
                            ext = os.path.splitext(name)[1].lower().lstrip(".")
                            if ext in exclude_exts:
                                continue
                            try:
                                st = entry.stat(follow_symlinks=False)
                            except OSError as e:
                                res.errors += 1
                                _sample(res, disp, f"无法获取文件信息：{e.strerror or e}")
                                continue
                            parent = safe_text(display_path(os.path.dirname(entry.path)))
                            stem = os.path.splitext(name)[0]
                            py_full, py_init = _pinyin.name_pinyin(stem)
                            batch.append((
                                root_id, disp, parent, name, ext,
                                int(st.st_size), int(st.st_mtime), int(st.st_ctime), tick,
                                py_full, py_init,
                            ))
                            res.files_indexed += 1
                            if len(batch) >= BATCH_SIZE:
                                flush()
                        except OSError as e:  # 条目在扫描期间被删/权限变化
                            res.errors += 1
                            _sample(res, display_path(getattr(entry, "path", d)),
                                     f"访问失败：{e.strerror or e}")
                        if res.files_indexed % 200 == 0 and on_progress:
                            on_progress(res.dirs_visited, res.files_indexed, res.errors)
                except OSError as e:  # scandir 遍历中途失败
                    res.errors += 1
                    _sample(res, display_path(d), f"遍历中断：{e.strerror or e}")

        flush()
        if res.completed:
            db.cleanup_stale(conn, root_id, tick)
            conn.commit()
            db.set_root_status(root_id, "done", res.errors)
        else:
            db.set_root_status(root_id, "stopped", res.errors)
        return res
    finally:
        if on_progress:
            on_progress(res.dirs_visited, res.files_indexed, res.errors)
        conn.close()


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    return bool(cancel_event and cancel_event.is_set())


def _wait_pause(pause_event, cancel_event) -> None:
    if pause_event is None:
        return
    while pause_event.is_set():
        if _is_cancelled(cancel_event):
            return
        pause_event.wait(0.1)


def _sample(res: ScanResult, path: str, reason: str) -> None:
    if len(res.error_samples) < MAX_ERROR_SAMPLES:
        res.error_samples.append((path, reason))