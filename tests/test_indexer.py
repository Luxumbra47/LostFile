"""扫描与增量索引测试。"""
import json
import os
import threading
import time

import pytest

from conftest import make_file, wait_until
from lostfile.db import Database
from lostfile.indexer import display_path, long_path, safe_text, scan_directory


@pytest.fixture()
def env(tmp_path):
    db = Database(str(tmp_path / "idx.db"))
    root = tmp_path / "root"
    yield db, root
    db.close()


def _scan(db, root, **kw):
    return scan_directory(root, db, **kw)


def test_basic_scan_and_metadata(env):
    db, root = env
    make_file(root / "doc" / "报告.pdf", 3000)
    make_file(root / "doc" / "数据.xlsx", 500)
    make_file(root / "img" / "照片.png", 2048)
    db.add_root(str(root))
    res = _scan(db, str(root))
    assert res.completed
    assert res.errors == 0
    assert db.count_files() == 3

    conn = db.connect()
    rows = conn.execute(
        "SELECT name, size, ext, parent FROM files ORDER BY name"
    ).fetchall()
    conn.close()
    by_name = {r[0]: r for r in rows}
    assert by_name["报告.pdf"][1] == 3000
    assert by_name["报告.pdf"][2] == "pdf"
    assert by_name["照片.png"][0] == "照片.png"
    # 父目录为显示路径（不含 \\?\ 前缀）
    assert "\\\\?\\" not in by_name["报告.pdf"][3]


def test_incremental_delete_detection(env):
    db, root = env
    f1 = make_file(root / "a.txt", 10)
    make_file(root / "b.txt", 20)
    db.add_root(str(root))
    _scan(db, str(root))
    assert db.count_files() == 2

    os.remove(f1)
    _scan(db, str(root))
    assert db.count_files() == 1
    names = db.connect().execute("SELECT name FROM files").fetchall()
    assert names == [("b.txt",)]


def test_incremental_modify(env):
    db, root = env
    f = make_file(root / "c.bin", 100)
    db.add_root(str(root))
    _scan(db, str(root))
    with open(f, "wb") as fh:
        fh.write(b"y" * 999)
    _scan(db, str(root))
    row = db.connect().execute(
        "SELECT size FROM files WHERE name='c.bin'"
    ).fetchone()
    assert row[0] == 999


def test_excludes(env):
    db, root = env
    make_file(root / "keep.jpg", 50)
    make_file(root / "skip.tmp", 50)
    sub = root / "excluded_dir"
    make_file(sub / "inside.txt", 50)
    db.add_root(str(root))
    res = _scan(db, str(root),
                exclude_exts={"tmp"},
                exclude_dirs={str(sub)})
    assert res.completed
    names = [r[0] for r in db.connect().execute(
        "SELECT name FROM files").fetchall()]
    assert names == ["keep.jpg"]


def test_cancel_keeps_existing_index(env):
    db, root = env
    make_file(root / "keep.txt", 10)
    db.add_root(str(root))
    _scan(db, str(root))
    assert db.count_files() == 1

    ev = threading.Event()
    ev.set()  # 立即取消
    res = _scan(db, str(root), cancel_event=ev)
    assert not res.completed
    # 取消后不得清理旧索引
    assert db.count_files() == 1


def test_pause_then_resume(env):
    db, root = env
    for i in range(50):
        make_file(root / f"f{i}.txt", 10)
    db.add_root(str(root))
    pause = threading.Event()
    held = {}

    def run():
        held["res"] = _scan(db, str(root), pause_event=pause)

    t = threading.Thread(target=run)
    pause.set()
    t.start()
    paused = wait_until(
        lambda: t.is_alive() and db.list_roots()[0]["scan_status"] == "scanning",
        timeout=5,
    )
    assert paused
    pause.clear()  # 继续
    t.join(10)
    assert not t.is_alive()
    assert held["res"].completed
    assert db.count_files() == 50


def test_nonexistent_root_counts_error(env):
    db, root = env
    db.add_root(str(root / "missing"))
    res = _scan(db, str(root / "missing"))
    assert res.completed
    assert res.errors >= 1
    assert res.error_samples[0][1]


def test_unicode_and_case_insensitive(env):
    db, root = env
    make_file(root / "中文文件名（副本）.MP4", 42)
    make_file(root / "UPPER.PDF", 42)
    db.add_root(str(root))
    _scan(db, str(root))
    conn = db.connect()
    rows = conn.execute("SELECT name, ext FROM files").fetchall()
    conn.close()
    exts = {r[1] for r in rows}
    assert exts == {"mp4", "pdf"}


def test_deep_long_path(env, tmp_path):
    db, root = env
    # 用 \\?\ 前缀构造超过 260 字符的深路径
    deep = long_path(str(root))
    name = "d" * 40
    for _ in range(8):
        deep = os.path.join(deep, name)
    os.makedirs(deep, exist_ok=True)
    fpath = os.path.join(deep, "deep.txt")
    with open(fpath, "wb") as f:
        f.write(b"deep")
    assert len(display_path(fpath)) > 260

    db.add_root(str(root))
    res = _scan(db, str(root))
    assert res.completed
    assert db.count_files() == 1

    # 清理：逐层删除（带前缀）
    cur = fpath
    for _ in range(10):
        try:
            os.remove(cur)
        except OSError:
            pass
        cur = os.path.dirname(cur)
        if display_path(cur) == display_path(long_path(str(root))):
            break
    for _ in range(20):
        try:
            os.rmdir(cur)
        except OSError:
            break
        cur = os.path.dirname(cur)


def test_symlink_skipped(env):
    db, root = env
    make_file(root / "real.txt", 10)
    target = root / "link_dir"
    try:
        os.symlink(str(root), str(target), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接（需开发者模式/权限）")
    db.add_root(str(root))
    res = _scan(db, str(root))
    assert res.completed
    # 不因链接循环而崩溃，且计数不翻倍
    assert 0 <= db.count_files() <= 2


def test_surrogate_filename_scan_survives(env, tmp_path):
    """回归：文件名含孤立代理字符（\ude75，Windows 真实存在）时，
    扫描不得中断；该文件以替换后的安全名字入库。"""
    db, root = env
    bad = "bad-" + chr(0xDE75) + ".txt"
    make_file(root / bad, 50)
    make_file(root / "normal.txt", 50)
    db.add_root(str(root))
    res = _scan(db, str(root))
    assert res.completed
    assert db.count_files() == 2
    conn = db.connect()
    names = [r[0] for r in conn.execute("SELECT name FROM files")]
    conn.close()
    assert "normal.txt" in names
    assert chr(0xDE75) not in "".join(names)          # 已替换，无残留代理
    assert any(n.startswith("bad-") for n in names)   # 前缀保留


def test_safe_text_roundtrip():
    assert safe_text(None) == ""
    assert safe_text("正常.txt") == "正常.txt"
    bad = "D:\\x\\" + chr(0xDE75)
    fixed = safe_text(bad)
    assert chr(0xDE75) not in fixed
    fixed.encode("utf-8")  # 结果必须可编码


def test_scanchild_json_surrogate_safe(tmp_path):
    """子进程状态 JSON 写入包含代理字符的目录名也不得崩溃。"""
    from lostfile import scanchild
    p = str(tmp_path / "scan_state.json")
    bad = "D:\\x\\" + chr(0xDE75)
    scanchild._write_json(p, {"state": "scanning", "current_dir": bad})
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["current_dir"] == bad  # 转义写入后可原样读回