"""SQLite 本地索引层：建表、迁移、损坏恢复、扫描写入、收藏与搜索历史。"""
import contextlib
import os
import sqlite3
import threading
import time

from . import fts
from . import textio

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS scan_roots(
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    display_name TEXT,
    added_at INTEGER NOT NULL,
    last_scan_started INTEGER DEFAULT 0,
    last_scan_finished INTEGER DEFAULT 0,
    scan_status TEXT NOT NULL DEFAULT 'idle',
    last_error_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES scan_roots(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    parent TEXT NOT NULL,
    name TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime INTEGER NOT NULL,
    ctime INTEGER NOT NULL,
    scan_tick INTEGER NOT NULL DEFAULT 0,
    seen INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_files_ext ON files(ext);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime);
CREATE INDEX IF NOT EXISTS idx_files_ctime ON files(ctime);
CREATE INDEX IF NOT EXISTS idx_files_root ON files(root_id);
CREATE TABLE IF NOT EXISTS favorites(
    path TEXT PRIMARY KEY,
    added_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS search_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    query_time INTEGER NOT NULL
);
"""

MIGRATIONS = {
    # 版本 2：拼音检索列（名称包含中文时由索引器填充，旧数据后台补算）
    2: """
    ALTER TABLE files ADD COLUMN pinyin_full TEXT NOT NULL DEFAULT '';
    ALTER TABLE files ADD COLUMN pinyin_init TEXT NOT NULL DEFAULT '';
    ALTER TABLE files ADD COLUMN pinyin_done INTEGER NOT NULL DEFAULT 0;
    """,
    # 版本 3：删除 idx_files_parent。位置过滤已改为按 path 前缀匹配，
    # 该索引不再被任何查询使用（EXPLAIN QUERY PLAN 实测为全表扫描），
    # 而在百万行、长路径下它约占 114MB，删掉可显著缩小索引库。
    3: """
    DROP INDEX IF EXISTS idx_files_parent;
    """,
    # 版本 4：0.3.0 特性列（内容检索/内容哈希/向量/关键词 FTS）。
    # 各 *_done 为后台构建任务的增量标记；idx_files_fts_pending 保证
    # fts.ready() 的「无残留」探测在百万行下仍为 O(1)。
    # 注意：files_fts 虚拟表不在此创建——百万行建索引需分钟级，
    # 由后台 workers.FtsBackfill 在启动后追赶。
    4: """
    ALTER TABLE files ADD COLUMN content_done INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE files ADD COLUMN content_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE files ADD COLUMN hash_done INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE files ADD COLUMN vec_done INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE files ADD COLUMN fts_done INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX IF NOT EXISTS idx_files_hash
        ON files(content_hash) WHERE content_hash != '';
    CREATE INDEX IF NOT EXISTS idx_files_fts_pending
        ON files(fts_done) WHERE fts_done=0;
    """,
}


def _vtab_delete(conn: sqlite3.Connection, table: str, ids: list[int]) -> None:
    """按 files.id 同步删除虚拟表行（表未建时静默跳过）。"""
    if not ids:
        return
    try:
        if fts.fts5_available() and fts.table_exists(conn, table):
            marks = ",".join("?" for _ in ids)
            conn.execute(
                f"DELETE FROM {table} WHERE rowid IN ({marks})", ids
            )
    except sqlite3.Error:
        pass


class Database:
    """线程模型：主连接供 UI 线程使用；扫描线程通过 connect() 另开连接。
    WAL 模式允许多连接并发读写；SQLite 内部保证写串行。
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # check_same_thread=False + RLock：主连接可被 UI 线程与扫描线程共用
        self._conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
        self.recovery_backup: str | None = None
        try:
            with self._lock:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=30000")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._migrate(self._conn)
                self._conn.commit()
        except Exception:
            # 损坏库等初始化失败情况交由 open_safe 统一处理
            pass

    # ---------- 打开与恢复 ----------

    @classmethod
    def open_safe(cls, path: str) -> "Database":
        """打开数据库；损坏时备份原文件并重建，不抛异常（重建失败除外）。"""
        db = None
        try:
            db = cls(path)
            row = db._conn.execute("PRAGMA integrity_check").fetchone()
            if row and row[0] == "ok":
                return db
            raise sqlite3.DatabaseError(f"integrity_check: {row}")
        except Exception:
            if db is not None:
                db.close()
            backup = path + f".corrupt-{int(time.time())}"
            saved_backup = None
            with contextlib.suppress(OSError):
                if os.path.exists(path):
                    os.replace(path, backup)
                    saved_backup = backup
            new_db = cls(path)
            new_db.recovery_backup = saved_backup
            return new_db

    def close(self) -> None:
        """关闭主连接；任何一步失败都不能阻止 close 本身执行。"""
        with contextlib.suppress(Exception):
            if not self._lock.acquire(timeout=5):
                return
            try:
                with contextlib.suppress(Exception):
                    self._conn.commit()
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                with contextlib.suppress(Exception):
                    self._conn.close()
                self._lock.release()

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30.0)
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.executescript(SCHEMA_V1)
            conn.execute("PRAGMA user_version = 1")
            version = 1
        for v in range(version + 1, max(MIGRATIONS.keys()) + 1):
            script = MIGRATIONS.get(v)
            if script:
                conn.executescript(script)
                conn.execute(f"PRAGMA user_version = {v}")

    def check_integrity(self) -> bool:
        try:
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row and row[0] == "ok")
        except sqlite3.Error:
            return False

    # ---------- 扫描根目录 ----------

    def add_root(self, path: str, display_name: str | None = None) -> int:
        path = os.path.normpath(path)
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO scan_roots(path, display_name, added_at) VALUES(?,?,?)",
                (path, display_name or os.path.basename(path.rstrip("\\/")) or path,
                 int(time.time())),
            )
            row = self._conn.execute(
                "SELECT id FROM scan_roots WHERE path=?", (path,)
            ).fetchone()
            self._conn.commit()
            return int(row[0])

    def remove_root(self, root_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM scan_roots WHERE id=?", (root_id,))
            self._conn.commit()

    def list_roots(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, path, display_name, added_at, last_scan_started,"
                " last_scan_finished, scan_status, last_error_count FROM scan_roots"
                " ORDER BY id"
            ).fetchall()
        return [
            {
                "id": r[0], "path": r[1], "display_name": r[2], "added_at": r[3],
                "last_scan_started": r[4], "last_scan_finished": r[5],
                "scan_status": r[6], "last_error_count": r[7],
            }
            for r in rows
        ]

    def set_root_status(self, root_id: int, status: str, error_count: int | None = None) -> None:
        with self._lock:
            if error_count is None:
                self._conn.execute(
                    "UPDATE scan_roots SET scan_status=?, last_scan_finished=? WHERE id=?",
                    (status, int(time.time()), root_id),
                )
            else:
                self._conn.execute(
                    "UPDATE scan_roots SET scan_status=?, last_error_count=?,"
                    " last_scan_finished=? WHERE id=?",
                    (status, error_count, int(time.time()), root_id),
                )
            self._conn.commit()

    def mark_root_scan_started(self, root_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE scan_roots SET scan_status='scanning', last_scan_started=? WHERE id=?",
                (int(time.time()), root_id),
            )
            self._conn.commit()

    # ---------- 文件索引 ----------

    def next_tick(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COALESCE(MAX(scan_tick),0)+1 FROM files").fetchone()
        return int(row[0])

    def upsert_many(self, conn: sqlite3.Connection, rows: list[tuple]) -> None:
        conn.executemany(
            "INSERT INTO files(root_id,path,parent,name,ext,size,mtime,ctime,"
            " scan_tick,seen,pinyin_full,pinyin_init,pinyin_done)"
            " VALUES(?,?,?,?,?,?,?,?,?,1,?,?,1)"
            " ON CONFLICT(path) DO UPDATE SET root_id=excluded.root_id,"
            " parent=excluded.parent, name=excluded.name, ext=excluded.ext,"
            " size=excluded.size, mtime=excluded.mtime, ctime=excluded.ctime,"
            " scan_tick=excluded.scan_tick, seen=1,"
            " pinyin_full=excluded.pinyin_full, pinyin_init=excluded.pinyin_init,"
            " pinyin_done=1",
            rows,
        )
        # 关键词 FTS 同步（表已建立时）：按 path 找回 id，整行重写 FTS，
        # 并标记 fts_done=1。表未建立时跳过——新行保持 fts_done=0，
        # 等待 FtsBackfill 建表后追赶，保证不去依赖执行顺序。
        if not fts.fts5_available() or not fts.table_exists(conn, fts.FILES_FTS):
            return
        paths = [r[1] for r in rows]
        marks = ",".join("?" for _ in paths)
        fts_rows = conn.execute(
            f"SELECT id, name, path FROM files WHERE path IN ({marks})",
            paths,
        ).fetchall()
        if fts_rows:
            conn.executemany(
                f"INSERT OR REPLACE INTO {fts.FILES_FTS}(rowid, name, path)"
                " VALUES(?,?,?)",
                fts_rows,
            )
            conn.executemany(
                "UPDATE files SET fts_done=1 WHERE id=?",
                [(r[0],) for r in fts_rows],
            )

    @staticmethod
    def cleanup_stale(conn: sqlite3.Connection, root_id: int, tick: int) -> None:
        """删除该根目录下本次扫描未再见到的文件（扫描成功完成后调用）。"""
        doomed = [
            r[0] for r in conn.execute(
                "SELECT id FROM files WHERE root_id=? AND scan_tick<>?",
                (root_id, tick),
            ).fetchall()
        ]
        conn.execute(
            "DELETE FROM files WHERE root_id=? AND scan_tick<>?", (root_id, tick)
        )
        _vtab_delete(conn, fts.FILES_FTS, doomed)
        _vtab_delete(conn, fts.CONTENT_FTS, doomed)

    def count_files(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])

    def remove_file(self, path: str) -> None:
        """从索引中移除单个文件行（“删除到回收站”成功后同步索引用）。

        只删索引记录，不触碰磁盘文件；收藏记录保留，以便用户从回收站
        还原后仍能找回。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM files WHERE path=?", (path,)
            ).fetchone()
            self._conn.execute("DELETE FROM files WHERE path=?", (path,))
            if row is not None:
                fid = int(row[0])
                _vtab_delete(self._conn, fts.FILES_FTS, [fid])
                _vtab_delete(self._conn, fts.CONTENT_FTS, [fid])
            self._conn.commit()

    def has_pinyin_pending(self) -> bool:
        """是否存在尚未计算拼音的历史行（升级后后台补算用）。"""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT 1 FROM files WHERE pinyin_done=0 LIMIT 1"
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def roots_stats(self) -> dict[int, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT root_id, COUNT(*) FROM files GROUP BY root_id"
            ).fetchall()
        return {int(r[0]): int(r[1]) for r in rows}

    def clear_index(self) -> None:
        """清除全部索引（文件表 + 根目录表）。收藏与搜索历史不受影响。"""
        with self._lock:
            self._conn.execute("DELETE FROM files")
            self._conn.execute("DELETE FROM scan_roots")
            for table in (fts.FILES_FTS, fts.CONTENT_FTS):
                if fts.fts5_available() and fts.table_exists(self._conn, table):
                    self._conn.execute(f"DELETE FROM {table}")
            self._conn.commit()

    # ---------- 关键词 FTS ----------

    def ensure_files_fts(self, conn: sqlite3.Connection | None = None) -> bool:
        """确保 files_fts 虚拟表存在（fts5 可用时）。返回是否存在。"""
        if conn is None:
            with self._lock:
                return self._ensure_files_fts_conn(self._conn)
        return self._ensure_files_fts_conn(conn)

    def _ensure_files_fts_conn(self, conn: sqlite3.Connection) -> bool:
        if not fts.fts5_available() or fts.table_exists(conn, fts.FILES_FTS):
            return fts.table_exists(conn, fts.FILES_FTS)
        # external-content 表：文本不复制（files 是唯一数据源），体积显著更小。
        # 注意：实测本机 SQLite 版本不会为 external-content 自动创建同步
        # 触发器，因此 rows 同步仍全部由 db.py 的写路径手动完成。
        conn.execute(
            f"CREATE VIRTUAL TABLE {fts.FILES_FTS} USING fts5("
            "name, path, content='files', content_rowid='id',"
            " tokenize='trigram')"
        )
        conn.commit()
        return True

    def has_fts_pending(self) -> bool:
        """是否存在尚未进入 FTS 的行（FtsBackfill 的启动条件）。"""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT 1 FROM files WHERE fts_done=0 LIMIT 1"
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def drop_files_fts(self) -> None:
        """删除关键词 FTS 表并重置增量标记（关闭加速/释放磁盘空间用）。

        删除后 fts.ready() 恒为 False，查询自动回退 LIKE 老路径（结果不变）。
        """
        with self._lock:
            if fts.fts5_available() and fts.table_exists(
                    self._conn, fts.FILES_FTS):
                self._conn.execute(f"DROP TABLE {fts.FILES_FTS}")
            self._conn.execute("UPDATE files SET fts_done=0")
            self._conn.commit()

    # ---------- 内容全文检索 ----------

    def ensure_content_fts(self, conn: sqlite3.Connection | None = None) -> bool:
        """确保 files_content_fts 虚拟表存在（fts5 可用时）。返回是否存在。"""
        if conn is None:
            with self._lock:
                return self._ensure_content_fts_conn(self._conn)
        return self._ensure_content_fts_conn(conn)

    def _ensure_content_fts_conn(self, conn: sqlite3.Connection) -> bool:
        if not fts.fts5_available() or fts.table_exists(conn, fts.CONTENT_FTS):
            return fts.table_exists(conn, fts.CONTENT_FTS)
        conn.execute(
            f"CREATE VIRTUAL TABLE {fts.CONTENT_FTS} USING fts5("
            "content, tokenize='trigram')"
        )
        conn.commit()
        return True

    def has_content_pending(self) -> bool:
        """是否存在尚未提取正文的行（ContentBackfill 的启动条件）。"""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT 1 FROM files WHERE content_done=0 LIMIT 1"
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def content_stats(self) -> tuple[int, int]:
        """返回 (已处理文件数, 文件总数)。仅文本类扩展名会被真正提取正文，
        其余行只做标记（进度口径按“已处理/全部”展示更诚实）。"""
        try:
            with self._lock:
                done = self._conn.execute(
                    "SELECT COUNT(*) FROM files WHERE content_done=1"
                ).fetchone()[0]
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM files"
                ).fetchone()[0]
            return int(done), int(total)
        except sqlite3.Error:
            return 0, 0

    def reset_content_index(self) -> None:
        """清空内容索引并重置增量标记（“重建内容索引”用）。

        注意：fts5 的 `DELETE FROM` 只是把行标记删除，索引段仍留在影子表里
        （实测 42 万行删完后影子表仍有 88 万行），磁盘不会立刻回收；因此这里
        额外跑一次 fts5 的 `optimize` 合并索引段，避免“重建”后空间反而被占住。
        """
        with self._lock:
            self._conn.execute("UPDATE files SET content_done=0")
            if fts.fts5_available() and fts.table_exists(
                    self._conn, fts.CONTENT_FTS):
                self._conn.execute(f"DELETE FROM {fts.CONTENT_FTS}")
                try:
                    self._conn.execute(
                        f"INSERT INTO {fts.CONTENT_FTS}({fts.CONTENT_FTS})"
                        " VALUES('optimize')"
                    )
                except sqlite3.Error:
                    pass          # 老版本 fts5 不支持 optimize 时忽略
            self._conn.commit()

    # ---------- 重复文件检测 ----------

    def has_hash_pending(self) -> bool:
        """是否存在尚未计算内容哈希的行（HashBackfill 的启动条件）。"""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT 1 FROM files WHERE hash_done=0 LIMIT 1"
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def hash_stats(self) -> tuple[int, int]:
        """返回 (已计算哈希数, 文件总数)。"""
        try:
            with self._lock:
                done = self._conn.execute(
                    "SELECT COUNT(*) FROM files WHERE hash_done=1"
                ).fetchone()[0]
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM files"
                ).fetchone()[0]
            return int(done), int(total)
        except sqlite3.Error:
            return 0, 0

    def reset_hash_index(self) -> None:
        """清空所有内容哈希并重置增量标记（“重新检测”用）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE files SET content_hash='', hash_done=0"
            )
            self._conn.commit()

    # ---------- 语义向量 ----------

    def has_vec_pending(self) -> bool:
        """是否存在尚未向量化的行（VectorBackfill 的启动条件）。"""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT 1 FROM files WHERE vec_done=0 LIMIT 1"
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def vec_stats(self) -> tuple[int, int]:
        """返回 (已向量化数, 文件总数)。"""
        try:
            with self._lock:
                done = self._conn.execute(
                    "SELECT COUNT(*) FROM files WHERE vec_done=1"
                ).fetchone()[0]
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM files"
                ).fetchone()[0]
            return int(done), int(total)
        except sqlite3.Error:
            return 0, 0

    def reset_vec_index(self) -> None:
        """重置向量增量标记（“重建向量索引”用；bin 文件由 UI 负责删除）。"""
        with self._lock:
            self._conn.execute("UPDATE files SET vec_done=0")
            self._conn.commit()

    # ---------- 收藏 ----------

    def add_favorite(self, path: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO favorites(path, added_at) VALUES(?,?)",
                (path, int(time.time())),
            )
            self._conn.commit()

    def remove_favorite(self, path: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM favorites WHERE path=?", (path,))
            self._conn.commit()

    def is_favorite(self, path: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM favorites WHERE path=?", (path,)
            ).fetchone()
        return row is not None

    def list_favorites(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT path, added_at FROM favorites ORDER BY added_at DESC"
            ).fetchall()
        return [{"path": r[0], "added_at": r[1]} for r in rows]

    # ---------- 搜索历史 ----------

    def add_history(self, query: str) -> None:
        q = (query or "").strip()
        if not q:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO search_history(query, query_time) VALUES(?,?)",
                (q, int(time.time())),
            )
            self._conn.commit()

    def recent_history(self, limit: int = 20) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT query FROM search_history GROUP BY query"
                " ORDER BY MAX(query_time) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [r[0] for r in rows]

    def clear_search_history(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM search_history")
            self._conn.commit()

    # ---------- 维护 ----------

    def vacuum(self) -> None:
        """压缩数据库、回收已删除行占用的空间（耗时随库大小增长）。"""
        with self._lock:
            self._conn.commit()
            # WAL 先合并回主库并截断，否则压缩后 WAL 仍占空间
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("VACUUM")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def disk_size(self) -> int:
        """索引库占用的磁盘字节数（含 WAL/SHM 附属文件）。"""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(self.path + suffix)
            except OSError:
                pass
        return total