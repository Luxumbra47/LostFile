"""数据库层测试：CRUD、级联清理、损坏恢复、收藏与历史。"""
import os

import pytest

from lostfile.db import Database


@pytest.fixture()
def db(tmp_path):
    return Database(str(tmp_path / "idx.db"))


def test_add_list_remove_root(db, tmp_path):
    rid = db.add_root(str(tmp_path / "aa"))
    assert rid > 0
    roots = db.list_roots()
    assert len(roots) == 1
    assert roots[0]["path"] == str(tmp_path / "aa")

    db.remove_root(rid)
    assert db.list_roots() == []


def test_upsert_and_root_cascade(db, tmp_path):
    rid = db.add_root(str(tmp_path / "r"))
    conn = db.connect()
    db.upsert_many(conn, [
        (rid, str(tmp_path / "r" / "a.txt"), str(tmp_path / "r"), "a.txt",
         "txt", 10, 100, 200, 1, "", ""),
    ])
    conn.commit()
    assert db.count_files() == 1
    db.remove_root(rid)
    assert db.count_files() == 0
    conn.close()


def test_cleanup_stale(db, tmp_path):
    rid = db.add_root(str(tmp_path / "r"))
    conn = db.connect()
    base = str(tmp_path / "r")
    db.upsert_many(conn, [
        (rid, os.path.join(base, "keep.txt"), base, "keep.txt", "txt", 1, 1, 1, 5, "", ""),
        (rid, os.path.join(base, "gone.txt"), base, "gone.txt", "txt", 1, 1, 1, 4, "", ""),
    ])
    conn.commit()
    Database.cleanup_stale(conn, rid, 5)
    conn.commit()
    left = conn.execute("SELECT name FROM files").fetchall()
    conn.close()
    assert [r[0] for r in left] == ["keep.txt"]


def test_corruption_recovery(tmp_path):
    p = str(tmp_path / "corrupt.db")
    with open(p, "wb") as f:
        f.write(b"this is not a sqlite database at all ###")
    db = Database.open_safe(p)
    assert db.check_integrity()
    assert db.recovery_backup is not None
    assert os.path.exists(db.recovery_backup)
    db.close()


def test_favorites_and_history(db):
    db.add_favorite(r"D:\a\b.txt")
    assert db.is_favorite(r"D:\a\b.txt")
    db.remove_favorite(r"D:\a\b.txt")
    assert not db.is_favorite(r"D:\a\b.txt")

    db.add_history("昨天的 PDF")
    db.add_history("昨天的 PDF")
    db.add_history("视频")
    assert "昨天的 PDF" in db.recent_history()
    db.clear_search_history()
    assert db.recent_history() == []


def test_clear_index(db, tmp_path):
    rid = db.add_root(str(tmp_path / "r"))
    conn = db.connect()
    db.upsert_many(conn, [
        (rid, str(tmp_path / "r" / "x.bin"), str(tmp_path / "r"), "x.bin",
         "bin", 3, 3, 3, 1, "", ""),
    ])
    conn.commit()
    conn.close()
    db.clear_index()
    assert db.count_files() == 0
    assert db.list_roots() == []


def test_migration_v2_pinyin_columns(db):
    """新库直接建到最新版本：files 表包含拼音列。"""
    conn = db.connect()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version >= 2
    cols = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
    assert {"pinyin_full", "pinyin_init", "pinyin_done"} <= cols
    conn.close()
    assert db.has_pinyin_pending() is False  # 空表无待补算行


def test_no_unused_parent_index(db):
    """回归 v3：未使用的 idx_files_parent 不应存在（干净新建即不含）。"""
    conn = db.connect()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_files_parent" not in names


def test_migration_drops_parent_index_on_old_db(tmp_path):
    """回归：旧库（已有 idx_files_parent、版本 2）升级后被删掉该索引。"""
    import sqlite3

    from lostfile.db import Database
    from lostfile.db import MIGRATIONS  # noqa: F401  确保迁移表存在
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,"
        " parent TEXT NOT NULL, name TEXT NOT NULL, ext TEXT NOT NULL,"
        " size INTEGER NOT NULL, mtime INTEGER NOT NULL, ctime INTEGER NOT NULL,"
        " scan_tick INTEGER NOT NULL DEFAULT 0, seen INTEGER NOT NULL DEFAULT 1,"
        " root_id INTEGER NOT NULL DEFAULT 0,"
        " pinyin_full TEXT NOT NULL DEFAULT '',"
        " pinyin_init TEXT NOT NULL DEFAULT '',"
        " pinyin_done INTEGER NOT NULL DEFAULT 0);"
        "CREATE INDEX idx_files_parent ON files(parent);"
        "PRAGMA user_version = 2;"
    )
    conn.commit()
    conn.close()

    db = Database(path)
    names = {r[0] for r in db.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    db.close()
    assert "idx_files_parent" not in names


def test_disk_size_reports_bytes(db):
    assert db.disk_size() > 0