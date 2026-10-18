"""端到端集成测试：扫描 → NL 解析 → 搜索 → 匹配原因 → 文件变动。"""
import os
import time

from conftest import make_file
from lostfile import fileops, nl
from lostfile.db import Database
from lostfile.indexer import scan_directory
from lostfile.query import SearchQuery, match_reasons, search

MB = 1024 ** 2


def test_full_scenario_yesterday_3mb_pdf(tmp_path):
    """验收场景 6：'昨天的 PDF，大约 3MB' 应准确找到唯一目标文件。"""
    db = Database(str(tmp_path / "idx.db"))
    root = tmp_path / "downloads"
    now = time.time()
    target = make_file(root / "meeting_notes.pdf", 3 * MB, mtime=now - 86400)
    make_file(root / "notes.txt", 500, mtime=now - 60)
    make_file(root / "old.pdf", 3 * MB, mtime=now - 40 * 86400)
    make_file(root / "small.pdf", 10 * 1024, mtime=now - 86400)
    db.add_root(str(root))
    res = scan_directory(str(root), db)
    assert res.completed

    parsed = nl.parse("昨天的 PDF，大概 3MB")
    q = nl.conditions_to_query(parsed.conditions)
    # 位置条件指向真实下载目录，与本测试目录不同 → 用自定义目录重写
    q.locations = [str(root)]

    conn = db.connect()
    rows = search(conn, q)
    assert [r["name"] for r in rows] == ["meeting_notes.pdf"]
    conn.close()

    # 匹配原因来自查询条件
    reasons = match_reasons(q, rows[0])
    joined = " ".join(reasons)
    assert "PDF" in joined and "大小" in joined and "修改时间" in joined
    assert rows[0]["path"].endswith("meeting_notes.pdf")
    db.close()


def test_file_deleted_after_index(tmp_path):
    """验收场景 8：文件被删除后搜索不崩溃，且标记为不存在。"""
    db = Database(str(tmp_path / "idx.db"))
    root = tmp_path / "r"
    f = make_file(root / "will_gone.pdf", 100)
    db.add_root(str(root))
    scan_directory(str(root), db)
    os.remove(f)
    conn = db.connect()
    rows = search(conn, SearchQuery(categories=["pdf"]))
    conn.close()
    assert rows
    assert fileops.exists_file(rows[0]["path"]) is False
    db.close()


def test_original_files_never_modified(tmp_path):
    """验收场景 11：扫描/清除索引绝不修改原始文件。"""
    db = Database(str(tmp_path / "idx.db"))
    root = tmp_path / "r"
    f = make_file(root / "data.bin", 4096)
    before_bytes = f.read_bytes()
    before_listing = sorted(p.name for p in root.iterdir())
    db.add_root(str(root))
    scan_directory(str(root), db)
    db.clear_index()
    assert f.read_bytes() == before_bytes
    assert sorted(p.name for p in root.iterdir()) == before_listing
    db.close()


def test_scan_cancel_returns_normally(tmp_path):
    """验收场景 9：取消扫描后正常返回。"""
    import threading
    db = Database(str(tmp_path / "idx.db"))
    root = tmp_path / "r"
    for i in range(3000):
        make_file(root / f"f{i}.txt", 10)
    db.add_root(str(root))
    ev = threading.Event()
    result = {}

    def on_progress(_d, _f, _e):
        ev.set()  # 第一次进度回调即请求取消

    t = threading.Thread(target=lambda: result.setdefault(
        "res", scan_directory(str(root), db, cancel_event=ev,
                              on_progress=on_progress)))
    t.start()
    t.join(15)
    assert not t.is_alive()
    res = result.get("res")
    assert res is not None and not res.completed
    # 取消后根目录状态为 stopped，数据库可继续使用
    roots = db.list_roots()
    assert roots[0]["scan_status"] == "stopped"
    db.close()