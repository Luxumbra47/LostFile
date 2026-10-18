"""关键词 FTS（trigram）测试：等价性红线、短词回退、同步路径。"""
import os
import time

import pytest

from conftest import make_file, wait_until
from lostfile import fts as ftsmod
from lostfile.db import Database
from lostfile.indexer import scan_directory
from lostfile.query import SearchQuery, _keyword_conds, search
from lostfile.workers import FtsBackfill


@pytest.fixture()
def env(tmp_path):
    """构造带「难应付文件名」的索引环境。"""
    db = Database(str(tmp_path / "idx.db"))
    root = tmp_path / "root"
    make_file(root / "project-notes-main.zip", 100)       # 变体：连字符/主干
    make_file(root / "Quarterly_Report_Final.pdf", 100)    # 混合大小写
    make_file(root / "a%b_c.txt", 100)                    # LIKE 通配符字符
    make_file(root / "模版.txt", 100)                      # 2 字中文（trigram 不支持）
    make_file(root / "项目计划书_2025.docx", 100)          # >=3 字中文
    make_file(root / "报告模板.docx", 100)
    (root / "deepdir").mkdir(exist_ok=True)
    make_file(root / "deepdir" / "project-notes" / "skill.json", 100)  # 路径命中
    db.add_root(str(root))
    res = scan_directory(str(root), db)
    assert res.completed and res.errors == 0
    yield db, root
    db.close()


def _ids(conn, sql, params):
    return {r[0] for r in conn.execute(sql, params).fetchall()}


def test_fts5_and_trigram_available():
    assert ftsmod.fts5_available()
    assert ftsmod.quote_phrase('a"b') == '"a""b"'


def test_keyword_conds_branch_selection():
    # >=3 码位 → FTS MATCH 分支
    cond, params = _keyword_conds("project-notes", False, fts_ok=True)
    assert "MATCH" in cond and any("project-notes" in p for p in params)
    # 2 字中文 → 只能走 LIKE
    cond2, _ = _keyword_conds("模版", False, fts_ok=True)
    assert "MATCH" not in cond2 and "LIKE" in cond2
    # fts_ok=False 时一律 LIKE（含 >=3 码位）
    cond3, _ = _keyword_conds("project-notes", False, fts_ok=False)
    assert "MATCH" not in cond3


def test_gate_before_after_backfill(env):
    db, _ = env
    conn = db.connect()
    assert ftsmod.ready(conn) is False          # 表未建
    assert db.ensure_files_fts(conn) is True
    assert ftsmod.ready(conn) is False          # 表已建但历史行未入 FTS
    q = SearchQuery(keywords=["project-notes"])
    before = sorted(r["path"] for r in search(conn, q))   # 此时仍是 LIKE
    bf = FtsBackfill(db)
    bf.start()
    assert wait_until(lambda: not db.has_fts_pending(), timeout=30)
    bf.stop()
    assert ftsmod.ready(conn) is True
    after = sorted(r["path"] for r in search(conn, q))    # 此时走 FTS
    assert after and after == before
    conn.close()


def test_fts_like_equivalence_matrix(env):
    """多种关键词在 FTS 与 LIKE 两种路径下的结果集必须完全一致。"""
    db, _ = env
    conn = db.connect()
    assert db.ensure_files_fts(conn)
    bf = FtsBackfill(db)
    bf.start()
    assert wait_until(lambda: not db.has_fts_pending(), timeout=30)
    bf.stop()
    kws = ["project-notes", "projectnotes", "report", "a%b", "ab",
           "项目计划书", "报告模板", "模版", "quarterly"]
    for kw in kws:
        cond_f, p_f = _keyword_conds(kw, False, fts_ok=True)
        cond_l, p_l = _keyword_conds(kw, False, fts_ok=False)
        ids_f = _ids(conn, f"SELECT id FROM files WHERE {cond_f}", p_f)
        ids_l = _ids(conn, f"SELECT id FROM files WHERE {cond_l}", p_l)
        assert ids_f == ids_l, f"关键词 {kw!r} 两路径结果不一致"
    conn.close()


def test_upsert_rename_delete_sync(env):
    """扫描/改名/删除/remove_file/clear_index 都要保持 files 与 FTS 同步。"""
    db, root = env
    conn = db.connect()
    assert db.ensure_files_fts(conn)
    bf = FtsBackfill(db)
    bf.start()
    assert wait_until(lambda: not db.has_fts_pending(), timeout=30)
    bf.stop()

    def fts_count():
        return conn.execute(
            f"SELECT COUNT(*) FROM {ftsmod.FILES_FTS}").fetchone()[0]

    assert fts_count() == 7
    # 改名后重扫：旧名消失、新名可查
    old = root / "报告模板.docx"
    new = root / "季度总结PPT.docx"
    os.rename(old, new)
    db.add_root(str(root))
    res = scan_directory(str(root), db)
    assert res.completed
    assert wait_until(lambda: not db.has_fts_pending(), timeout=30)
    q = SearchQuery(keywords=["季度总结"])
    assert [r["name"] for r in search(conn, q)] == ["季度总结PPT.docx"]
    assert fts_count() == 7
    # 文件删除后重扫：cleanup_stale 同步移除
    os.remove(str(new))
    res = scan_directory(str(root), db)
    assert res.completed
    assert fts_count() == 6
    # remove_file 同步移除单行
    victim = str(root / "a%b_c.txt")
    db.remove_file(victim)
    assert fts_count() == 5
    # clear_index 清空 FTS
    db.clear_index()
    assert fts_count() == 0
    conn.close()


def test_drop_files_fts_falls_back_to_like(env):
    """维护页关闭加速：删除 trigram 索引后查询自动回退 LIKE，结果不变。"""
    db, _ = env
    conn = db.connect()
    assert db.ensure_files_fts(conn)
    bf = FtsBackfill(db)
    bf.start()
    assert wait_until(lambda: not db.has_fts_pending(), timeout=30)
    bf.stop()
    assert ftsmod.ready(conn) is True
    q = SearchQuery(keywords=["project-notes"])
    before = sorted(r["path"] for r in search(conn, q))
    assert before

    db.drop_files_fts()
    assert ftsmod.ready(conn) is False          # 表已删除 → 自动回退 LIKE
    after = sorted(r["path"] for r in search(conn, q))
    assert after == before                       # 结果完全一致
    conn.close()