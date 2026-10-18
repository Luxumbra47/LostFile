"""内容全文检索测试：文本提取、正文命中、同步清理与 UI 分组。"""
import os

import pytest

from conftest import make_file, wait_until
from lostfile import textio
from lostfile.db import Database
from lostfile.indexer import scan_directory
from lostfile.query import content_search
from lostfile.workers import ContentBackfill


def _write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w", encoding=encoding) as f:
        f.write(text)
    return path          # 必须返回路径：调用方靠它拿文件位置（曾漏 return 把路径变成 None）


@pytest.fixture()
def env(tmp_path):
    db = Database(str(tmp_path / "idx.db"))
    root = tmp_path / "root"
    _write(root / "note.txt", "会议纪要：数据不出本机 是我们坚守的原则")
    _write(root / "legacy.txt", "旧系统导出的记录，隐私保护方案讨论", encoding="gbk")
    _write(root / "code.py", "# 实现语义向量搜索 embedding_model = bge-small\n")
    make_file(root / "binary.log", 64)
    with open(str(root / "binary.log"), "wb") as f:
        f.write(b"secretmark\x00\x00binary-tail")
    big = root / "big.txt"
    with open(str(big), "w", encoding="utf-8") as f:
        f.write("startmark " + "x" * (80 * 1024) + " needle_tail")
    make_file(root / "pic.png", 10)
    db.add_root(str(root))
    res = scan_directory(str(root), db)
    assert res.completed and res.errors == 0
    yield db, root
    db.close()


def _build(db):
    bf = ContentBackfill(db)
    bf.start()
    ok = wait_until(lambda: not db.has_content_pending(), timeout=60)
    bf.stop()
    assert ok, "内容索引构建未完成"


def test_extract_text_variants(tmp_path):
    assert textio.extract_text(str(tmp_path / "nope.txt")) is None
    utf16 = _write(tmp_path / "u.txt", "你好世界")
    with open(str(utf16), "w", encoding="utf-16") as f:
        f.write("你好世界")
    assert "你好世界" in textio.extract_text(str(utf16))
    gbk = _write(tmp_path / "g.txt", "中文内容")
    with open(str(gbk), "w", encoding="gbk") as f:
        f.write("中文内容")
    assert "中文内容" in textio.extract_text(str(gbk))
    binary = tmp_path / "b.log"
    make_file(binary, 64)
    with open(str(binary), "wb") as f:
        f.write(b"abc\x00def")
    assert textio.extract_text(str(binary)) is None


def test_content_backfill_and_search(env):
    db, _ = env
    _build(db)
    conn = db.connect()
    hits = content_search(conn, ["数据不出本机"])
    names = [f["name"] for f in hits["files"]]
    assert names == ["note.txt"]
    assert "数据不出本机" in hits["files"][0]["snippet"]

    assert [f["name"] for f in content_search(conn, ["隐私保护"])["files"]] \
        == ["legacy.txt"]
    assert [f["name"] for f in content_search(conn, ["embedding"])["files"]] \
        == ["code.py"]
    assert [f["name"] for f in content_search(conn, ["startmark"])["files"]] \
        == ["big.txt"]
    # 上限截断（MAX_TEXT_BYTES）：超出部分的关键词不可命中
    assert content_search(conn, ["needle_tail"])["total"] == 0
    # 二进制文件（NUL）不提取：内容里的词不可命中
    assert content_search(conn, ["secretmark"])["total"] == 0
    # trigram 已知限制：2 字中文不能做正文检索
    assert content_search(conn, ["隐私"])["total"] == 0
    conn.close()


def test_content_stats_rebuild_and_row_cleanup(env):
    db, root = env
    _build(db)
    total_all = 6   # note/legacy/code/big/binary.log/pic.png（非文本只标记不提取）
    done, total = db.content_stats()
    assert (done, total) == (total_all, total_all)

    # 单行删除 → 正文行同步移除
    db.remove_file(str(root / "note.txt"))
    conn = db.connect()
    assert content_search(conn, ["数据不出本机"])["total"] == 0
    conn.close()

    # 重建：清空并重置增量标记
    db.reset_content_index()
    assert db.has_content_pending() is True
    conn = db.connect()
    assert content_search(conn, ["隐私保护"])["total"] == 0
    conn.close()
    _build(db)
    conn = db.connect()
    assert content_search(conn, ["隐私保护"])["total"] == 1
    conn.close()

    db.clear_index()
    conn = db.connect()
    assert content_search(conn, ["隐私保护"])["total"] == 0
    conn.close()


def test_ui_content_group_in_tree(app, tmp_path):
    """开启内容检索后，正文命中的文件出现在分组树「内容命中」组。"""
    from lostfile.config import AppConfig
    from lostfile.query import SearchQuery
    from lostfile.ui.mainwindow import MainWindow

    cfg = AppConfig(data_dir=str(tmp_path / "data"))
    db = Database.open_safe(cfg.db_path())
    root = tmp_path / "r"
    _write(root / "recipe.txt", "红烧肉的配方：五花肉 冰糖 生抽")
    db.add_root(str(root))
    cfg.set("wizard_completed", True)
    cfg.set("content_search_enabled", True)
    cfg.save()
    scan_directory(str(root), db)

    win = MainWindow(cfg, db)
    assert wait_until(lambda: (app.processEvents() or
                               (not db.has_content_pending()
                                and not win._searching)),
                      timeout=60)
    win._execute_query(SearchQuery(keywords=["五花肉"], limit=100))
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    top = win.results_view.group_tree.topLevelItem(0)
    assert top is not None
    assert "内容命中" in top.text(0)
    assert top.child(0).text(0) == "recipe.txt"
    assert "五花肉" in top.child(0).toolTip(0)
    win.shutdown()
    db.close()


def test_extract_text_respects_max_bytes(tmp_path):
    """正文只读取 MAX_TEXT_BYTES（缩小索引体积的关键）。"""
    p = tmp_path / "big.txt"
    with open(str(p), "w", encoding="utf-8") as f:
        f.write("head_marker " + "a" * (20 * 1024) + " tail_marker")
    text = textio.extract_text(str(p))
    assert "head_marker" in text
    assert "tail_marker" not in text            # 上限之外的内容不读取
    assert len(text.encode("utf-8")) <= textio.MAX_TEXT_BYTES


def test_skippable_path_matches_dirs_only(tmp_path):
    """依赖/构建目录要跳过；只比对目录分段，文件名里含这些词不算。"""
    assert textio.is_skippable_path(r"D:\proj\node_modules\a\index.js")
    assert textio.is_skippable_path(r"D:\proj\.venv\Lib\site-packages\x\y.py")
    assert textio.is_skippable_path(r"D:\proj\build\out.txt")
    assert textio.is_skippable_path(r"D:\proj\dist\app.js")
    # 文件名里含这些词不算，普通目录不误伤
    assert not textio.is_skippable_path(r"D:\proj\build_notes.txt")
    assert not textio.is_skippable_path(r"D:\proj\src\dist.py")
    assert not textio.is_skippable_path(r"D:\proj\main.py")


def test_content_backfill_skips_dependency_dirs(tmp_path):
    """依赖/构建目录里的文本不建正文索引，普通目录照常命中。"""
    db = Database(str(tmp_path / "idx.db"))
    root = tmp_path / "root"
    _write(root / "keep.txt", "重要内容 markerkeep")
    _write(root / "node_modules" / "pkg" / "huge.js",
           "dependency content markerdep " + "x" * 4096)
    _write(root / "site-packages" / "lib.py", "lib content markersite")
    db.add_root(str(root))
    res = scan_directory(str(root), db)
    assert res.completed and res.errors == 0
    _build(db)
    conn = db.connect()
    assert [f["name"] for f in content_search(conn, ["markerkeep"])["files"]] \
        == ["keep.txt"]
    assert content_search(conn, ["markerdep"])["total"] == 0
    assert content_search(conn, ["markersite"])["total"] == 0
    conn.close()
    # 依赖目录里的文件被视为“已处理”，不会一直挂在待办上
    assert db.has_content_pending() is False
    db.close()