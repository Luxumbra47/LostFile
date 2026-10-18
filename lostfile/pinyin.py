"""拼音检索支持：为中文文件名生成全拼/首字母，用于“记得读音不记得字”的搜索。

pypinyin 为可选依赖：未安装时所有函数安全降级（返回空串），
索引与搜索功能不受影响，仅拼音匹配不可用。
拼音基于文件名主干（不含扩展名）计算，与索引器保持一致。
"""
import os
import threading

_LAZY = None


def _loader():
    global _LAZY
    if _LAZY is None:
        try:
            import pypinyin
            _LAZY = pypinyin
        except ImportError:
            _LAZY = False  # 不可用
    return _LAZY


def available() -> bool:
    return bool(_loader())


def name_pinyin(name: str) -> tuple[str, str]:
    """返回 (全拼连写小写, 首字母连写小写)。

    不含中文或 pypinyin 不可用时返回 ("", "")。
    """
    if not name:
        return "", ""
    py = _loader()
    if not py:
        return "", ""

    def is_cjk(ch: str) -> bool:
        return "\u4e00" <= ch <= "\u9fff"

    if not any(is_cjk(c) for c in name):
        return "", ""

    def to_simple(syll: str) -> str:
        # 去掉声调数字/符号（pypinyin 用数字标调）
        out = []
        for ch in syll:
            if ch.isalpha():
                out.append(ch.lower())
        return "".join(out)

    try:
        fulls = []
        inits = []
        for seg in name:
            if is_cjk(seg):
                py_list = py.pinyin(seg, style=py.Style.NORMAL,
                                    heteronym=False, errors="ignore")
                syll = to_simple(py_list[0][0]) if py_list and py_list[0] else ""
                fulls.append(syll)
                inits.append(syll[0] if syll else "")
            elif seg.isalpha() and seg.isascii():
                fulls.append(seg.lower())
                inits.append(seg.lower()[0])
            # 数字/符号不参与拼音
        return "".join(fulls), "".join(inits)
    except Exception:
        return "", ""


def query_pinyin(kw: str) -> tuple[str, str]:
    """为查询关键词生成 (全拼, 首字母)；无中文时返回 ("", "")。"""
    full, init = name_pinyin(kw)
    return full, init


class PinyinBackfill:
    """启动后后台为历史数据补算拼音列（增量、可随时停止）。

    新扫描的文件由索引器直接写入拼音；此线程只处理升级前遗留的旧行。
    """

    def __init__(self, db, chunk: int = 5000, delay: float = 0.05):
        self._db = db
        self._chunk = chunk
        self._delay = delay
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not available() or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="pinyin-backfill")
        self._thread.start()

    def _run(self) -> None:
        try:
            conn = self._db.connect()
            try:
                while not self._stop.is_set():
                    rows = conn.execute(
                        "SELECT id, name FROM files WHERE pinyin_done=0"
                        " ORDER BY id LIMIT ?",
                        (self._chunk,),
                    ).fetchall()
                    if not rows:
                        break
                    updates = []
                    for fid, name in rows:
                        stem = os.path.splitext(name or "")[0]
                        full, init = name_pinyin(stem)
                        updates.append((full, init, fid))
                    conn.executemany(
                        "UPDATE files SET pinyin_full=?, pinyin_init=?,"
                        " pinyin_done=1 WHERE id=?",
                        updates,
                    )
                    conn.commit()
                    self._stop.wait(self._delay)
            finally:
                conn.close()
        except Exception:
            pass  # 后台任务失败不影响主功能；下次启动可重试

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None