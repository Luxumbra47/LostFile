"""重复文件检测测试：哈希计算、分组统计、后台构建与对话框。"""
import os

import pytest

from conftest import make_file, wait_until
from lostfile import hash as hashmod
from lostfile.db import Database
from lostfile.indexer import scan_directory
from lostfile.workers import HashBackfill


def _write(path, data: bytes):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "wb") as f:
        f.write(data)


@pytest.fixture()
def env(tmp_path):
    db = Database(str(tmp_path / "idx.db"))
    root = tmp_path / "root"
    _write(root / "a.txt", b"same-content-AAAA")
    _write(root / "b.txt", b"same-content-AAAA")       # 与 a 内容相同
    _write(root / "c.txt", b"different-1111")
    _write(root / "d.md", b"different-2222")           # 同样长度、不同内容
    _write(root / "solo.txt", b"only-one")
    big = root / "huge.bin"
    with open(str(big), "wb") as f:                    # 稀疏 >2GB：跳过检测
        f.seek(2 * 1024 ** 3 + 1)
        f.write(b"\0")
    db.add_root(str(root))
    res = scan_directory(str(root), db)
    assert res.completed and res.errors == 0
    yield db, root
    db.close()


def test_blake2b_basics(tmp_path):
    p1 = tmp_path / "x.bin"
    p2 = tmp_path / "y.bin"
    _write(p1, b"hello world")
    _write(p2, b"hello world")
    assert hashmod.blake2b_file(str(p1)) == hashmod.blake2b_file(str(p2))
    _write(p2, b"hello worlD")
    assert hashmod.blake2b_file(str(p1)) != hashmod.blake2b_file(str(p2))
    assert hashmod.blake2b_file(str(tmp_path / "nope.bin")) is None
    big = tmp_path / "big.bin"
    with open(str(big), "wb") as f:
        f.seek(2 * 1024 ** 3 + 1)
        f.write(b"\0")
    assert hashmod.blake2b_file(str(big)) == hashmod.SKIP_HASH


def test_backfill_and_groups(env):
    db, _ = env
    bf = HashBackfill(db)
    bf.start()
    assert wait_until(lambda: not db.has_hash_pending(), timeout=60)
    bf.stop()
    conn = db.connect()
    groups = hashmod.duplicate_groups(conn)
    conn.close()
    assert len(groups) == 1
    g = groups[0]
    assert g["count"] == 2
    names = sorted(f["name"] for f in g["files"])
    assert names == ["a.txt", "b.txt"]
    assert hashmod.total_wasted(groups) == len(b"same-content-AAAA")
    done, total = db.hash_stats()
    assert (done, total) == (6, 6)      # 含跳过的大文件


def test_reset_hash_index(env):
    db, _ = env
    bf = HashBackfill(db)
    bf.start()
    assert wait_until(lambda: not db.has_hash_pending(), timeout=60)
    bf.stop()
    db.reset_hash_index()
    assert db.has_hash_pending() is True
    conn = db.connect()
    assert hashmod.duplicate_groups(conn) == []
    conn.close()


def test_duplicates_dialog_lists_and_deletes(app, tmp_path, env, monkeypatch):
    """对话框：显示重复组；删除一个后组消失（免弹窗 + 永久删测试文件）。"""
    db, root = env
    bf = HashBackfill(db)
    bf.start()
    assert wait_until(lambda: not db.has_hash_pending(), timeout=60)
    bf.stop()

    from lostfile.config import AppConfig
    from lostfile.ui.duplicatesdialog import DuplicatesDialog
    cfg = AppConfig(data_dir=str(tmp_path / "data"))

    monkeypatch.setattr(
        "lostfile.ui.duplicatesdialog.QMessageBox.question",
        lambda *a, **k: QMsgYes())
    import lostfile.fileops as fileops
    monkeypatch.setattr(fileops, "delete_to_recycle_bin",
                        lambda p: (os.remove(p) or True))

    dlg = DuplicatesDialog(cfg, db, worker=bf)
    dlg.show()
    app.processEvents()
    dlg._building = False
    dlg._refresh()
    assert dlg.tree.topLevelItemCount() == 1
    top = dlg.tree.topLevelItem(0)
    assert top.childCount() == 2

    victim = str(root / "b.txt")
    dlg._delete(victim)
    app.processEvents()
    assert not os.path.exists(victim)
    assert dlg.tree.topLevelItemCount() == 0      # 组内只剩一个，不再是重复
    dlg.close()
    db.close()


class QMsgYes:
    """伪 QMessageBox 返回值：question 走 Yes。"""
    StandardButton = type("B", (), {"Yes": 1})

    def __eq__(self, other):
        return True