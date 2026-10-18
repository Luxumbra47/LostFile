"""FTS5（trigram）能力探测与查询辅助（纯 stdlib，无 Qt 依赖）。

files_fts 维护策略：external-content 表（content='files', content_rowid='id'）
——文本不复制、VACUUM 可用；本机 SQLite 版本不会为该模式自动建同步
触发器，因此 db.py 的全部写路径手动同步：
  - upsert 批次后 INSERT OR REPLACE（rowid=files.id）
  - cleanup_stale / remove_file / clear_index 同步删除
老库行由 workers.FtsBackfill 后台追赶（files.fts_done=0 标记，断点续跑）。

查询侧 gating：fts.ready(conn)=True（表已建且 fts_done 无残留）时，
长度>=3 的纯子串变体才走 FTS；否则全部回退 LIKE（结果集与旧实现一致）。
"""
import sqlite3

FILES_FTS = "files_fts"
CONTENT_FTS = "files_content_fts"

MIN_TRIGRAM = 3          # trigram tokenizer 的最短查询码位
_fts5_cache: bool | None = None


def fts5_available() -> bool:
    """运行时探测 sqlite 是否编译进 fts5 且支持 trigram tokenizer。"""
    global _fts5_cache
    if _fts5_cache is not None:
        return _fts5_cache
    ok = False
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')"
            )
            conn.execute("INSERT INTO t VALUES('abc')")
            ok = True
        finally:
            conn.close()
    except Exception:
        ok = False
    _fts5_cache = ok
    return ok


def quote_phrase(lit: str) -> str:
    """把字面量包成 MATCH 双引号短语；内部引号按 FTS5 规则写成 ""。"""
    return '"' + lit.replace('"', '""') + '"'


def column_match(columns: list[str], phrase: str) -> str:
    """name : "x" OR path : "x" 形式；单列时无 OR。"""
    return " OR ".join(f"{c} : {phrase}" for c in columns)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def ready(conn: sqlite3.Connection, table: str = FILES_FTS) -> bool:
    """FTS 表是否已建且与 files 完全同步（否则查询必须回退 LIKE）。

    「无残留 fts_done=0」判定走部分索引 idx_files_fts_pending，
    在百万行规模下仍是 O(1) 级探测。
    """
    if not fts5_available() or not table_exists(conn, table):
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM files WHERE fts_done=0 LIMIT 1"
        ).fetchone()
        return row is None
    except sqlite3.Error:
        return False