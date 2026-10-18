"""后台索引任务线程：FTS / 内容 / 哈希 / 向量的增量构建。

统一模式（抽象自 pinyin.PinyinBackfill）：daemon 线程 + db.connect()
另开连接 + `*_done=0` 增量标记 + chunk/delay 节流 + stop()/join(2)。
所有任务绝不阻塞 UI；磁盘/CPU 密集任务是否在扫描进行中启动由 UI 层控制。
后台任务失败不影响主功能，下次启动可重试。
"""
import threading

from . import fts
from . import hash as _hash
from . import textio


class BaseBackfill:
    """按 done 列增量处理的通用骨架。

    子类实现 select_pending / process / mark_done；重写 start 可加前置条件
    （如先建表）。基类保证：不重复启动、可停机、异常只终止自己的线程。
    """

    name = "base-backfill"
    chunk = 5000
    delay = 0.05

    def __init__(self, db):
        self._db = db
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return                      # 已在运行
        self._thread = None             # 上次已结束：允许再次启动
        self._begin()

    def _begin(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=self.name
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    # ---- 子类接口 ----

    @staticmethod
    def prepare(conn) -> bool:
        """进入循环前的一次性准备（如表创建）；False 则直接退出。"""
        return True

    def select_pending(self, conn) -> list:
        raise NotImplementedError

    def process(self, conn, rows: list) -> None:
        raise NotImplementedError

    def mark_done(self, conn, ids: list[int]) -> None:
        raise NotImplementedError

    # ---- 内部 ----

    def _ids(self, rows: list) -> list[int]:
        return [int(r[0]) for r in rows]

    def _run(self) -> None:
        conn = None
        try:
            conn = self._db.connect()
            if not self.prepare(conn):
                return
            while not self._stop.is_set():
                rows = self.select_pending(conn)
                if not rows:
                    break
                self.process(conn, rows)
                self.mark_done(conn, self._ids(rows))
                conn.commit()
                self._stop.wait(self.delay)
        except Exception:
            pass  # 后台任务失败不影响主功能；下次启动可重试
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


class FtsBackfill(BaseBackfill):
    """为 files_fts（关键词 trigram）追赶历史行；建表由本任务负责。"""

    name = "fts-backfill"

    def start(self) -> None:
        if not fts.fts5_available():
            return
        super().start()

    @staticmethod
    def prepare(conn) -> bool:
        # 此处 conn 为 worker 自己的连接；DB 级建表入口见 db.ensure_files_fts
        return True

    def select_pending(self, conn) -> list:
        return conn.execute(
            "SELECT id, name, path FROM files WHERE fts_done=0"
            " ORDER BY id LIMIT ?",
            (self.chunk,),
        ).fetchall()

    def process(self, conn, rows: list) -> None:
        conn.executemany(
            f"INSERT OR REPLACE INTO {fts.FILES_FTS}(rowid, name, path)"
            " VALUES(?,?,?)",
            rows,
        )

    def mark_done(self, conn, ids: list[int]) -> None:
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE files SET fts_done=1 WHERE id IN ({marks})", ids
        )

    def _run(self) -> None:
        # 建表用 worker 自己的连接：主连接由 UI 线程使用，避免锁竞争
        conn = self._db.connect()
        try:
            if not self._db.ensure_files_fts(conn):
                return  # fts5 不可用：永远不建表，查询保持 LIKE 路径
        except Exception:
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass
        super()._run()


class ContentBackfill(BaseBackfill):
    """为 files_content_fts 提取纯文本正文（仅白名单扩展名）。"""

    name = "content-backfill"

    def start(self) -> None:
        if not fts.fts5_available():
            return
        super().start()

    def _run(self) -> None:
        # 建表用 worker 自己的连接：主连接由 UI 线程使用，避免锁竞争
        conn = self._db.connect()
        try:
            if not self._db.ensure_content_fts(conn):
                return  # fts5 不可用：内容检索保持不可用
        except Exception:
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass
        super()._run()

    def select_pending(self, conn) -> list:
        return conn.execute(
            "SELECT id, path, ext FROM files WHERE content_done=0"
            " ORDER BY id LIMIT ?",
            (self.chunk,),
        ).fetchall()

    def process(self, conn, rows: list) -> None:
        inserts = []
        for fid, path, ext in rows:
            # 非白名单扩展名、或位于依赖/构建/缓存目录：标记完成但不提取，
            # 避免正文索引被 node_modules / site-packages 这类内容撑爆。
            if not textio.is_text_ext(ext) \
                    or textio.is_skippable_path(path):
                continue
            try:
                text = textio.extract_text(path)
            except Exception:
                text = None
            if text:
                inserts.append((fid, text))
        if inserts:
            conn.executemany(
                f"INSERT OR REPLACE INTO {fts.CONTENT_FTS}(rowid, content)"
                " VALUES(?,?)",
                inserts,
            )

    def mark_done(self, conn, ids: list[int]) -> None:
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE files SET content_done=1 WHERE id IN ({marks})", ids
        )


class HashBackfill(BaseBackfill):
    """为重复文件检测计算内容哈希（跳过 >2GB 文件，读失败标记完成不重试）。"""

    name = "hash-backfill"

    def select_pending(self, conn) -> list:
        return conn.execute(
            "SELECT id, path FROM files WHERE hash_done=0"
            " ORDER BY id LIMIT ?",
            (self.chunk,),
        ).fetchall()

    def process(self, conn, rows: list) -> None:
        updates = []
        for fid, path in rows:
            try:
                digest = _hash.blake2b_file(path)
            except Exception:
                digest = None
            updates.append((digest or "", fid))
        if updates:
            conn.executemany(
                "UPDATE files SET content_hash=?, hash_done=1 WHERE id=?",
                updates,
            )

    def mark_done(self, conn, ids: list[int]) -> None:
        # process 已逐行更新；此处只兜底未覆盖的行，保证进度前进
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE files SET hash_done=1 WHERE id IN ({marks})"
            " AND hash_done=0",
            ids,
        )