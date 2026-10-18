"""本地语义向量测试：分词单元、假模型全链路、主窗口分组（真模型验收另测）。"""
import os

import numpy as np
import pytest

from conftest import make_file, wait_until
from lostfile import vecindex
from lostfile import vecmodel
from lostfile.config import AppConfig
from lostfile.db import Database
from lostfile.indexer import scan_directory


def _vocab():
    v = {"[PAD]": 0, "[UNK]": 100, "[CLS]": 101, "[SEP]": 102,
         "你": 200, "好": 201, "hello": 300, "world": 301, "##world": 302}
    return v


def test_wordpiece_tokenize():
    v = _vocab()
    ids = vecmodel.wordpiece_tokenize("你好 hello", v, max_len=64)
    assert ids == [200, 201, 300]
    ids2 = vecmodel.wordpiece_tokenize("helloworld", v, max_len=64)
    assert ids2 == [300, 302]        # hello + ##world
    # 未登录词 → [UNK]
    ids3 = vecmodel.wordpiece_tokenize("xyz", v, max_len=64)
    assert ids3 == [100]


class _FakeEmbedder:
    """确定性的假模型：按词表映射为 8 维单位向量，未知词为 0 向量。"""

    def __init__(self):
        self._np = np
        self.dim = 8
        words = {"报告": 0, "总结": 1, "游戏": 2, "音乐": 3,
                 "年度": 4, "计划": 5, "简历": 6, "论文": 7}

        def unit(i):
            v = np.zeros(8, dtype=np.float32)
            v[i] = 1.0
            return v

        self._map = {w: unit(i) for w, i in words.items()}

    def encode(self, texts):
        out = []
        for t in (texts or []):
            stem = os.path.splitext(t)[0]
            vec = None
            for w, v in self._map.items():
                if w in stem:
                    vec = v
                    break
            out.append(vec if vec is not None
                       else np.zeros(8, dtype=np.float32))
        return np.stack(out) if out else np.zeros((0, 8), dtype=np.float32)


@pytest.fixture()
def env(tmp_path):
    cfg = AppConfig(data_dir=str(tmp_path / "data"))
    db = Database.open_safe(cfg.db_path())
    root = tmp_path / "root"
    make_file(root / "报告.pdf", 100)
    make_file(root / "年度总结.docx", 100)
    make_file(root / "游戏存档.zip", 100)
    make_file(root / "音乐.mp3", 100)
    db.add_root(str(root))
    res = scan_directory(str(root), db)
    assert res.completed
    yield cfg, db, root
    db.close()


def test_backfill_and_semantic_search(env):
    cfg, db, _ = env
    fake = _FakeEmbedder()
    bf = vecindex.VectorBackfill(db, cfg, embedder=fake)
    bf.start()
    assert wait_until(lambda: not db.has_vec_pending(), timeout=30)
    bf.stop()
    done, total = db.vec_stats()
    assert (done, total) == (4, 4)

    hits = bf.semantic_search("报告", k=3)
    assert hits and hits[0][1] > 0.9
    conn = db.connect()
    row = conn.execute(
        "SELECT name FROM files WHERE id=?", (hits[0][0],)).fetchone()
    conn.close()
    assert row[0] == "报告.pdf"

    # 未训练词 → 全 0 向量行被过滤，不产生虚假高相似度
    hits2 = bf.semantic_search("完全不存在的词", k=3)
    assert all(s < 0.55 for _fid, s in hits2) or not hits2


def test_vec_settings_defaults(app, tmp_path):
    """语义搜索默认关闭；模型缺失时状态栏如实提示未下载。"""
    cfg = AppConfig(data_dir=str(tmp_path / "data"))
    db = Database.open_safe(cfg.db_path())
    from lostfile.ui.settingsdlg import SettingsDialog
    dlg = SettingsDialog(cfg, db)
    dlg.show()
    app.processEvents()
    assert not dlg.vec_enable_check.isChecked()
    assert "未下载" in dlg.vec_status.text()
    assert cfg.get("vec_enabled") is False
    dlg.close()
    db.close()


def test_mainwindow_semantic_group(app, env, monkeypatch):
    """开启语义搜索后：分组树出现「语义相关」组，tooltip 带相似度。"""
    cfg, db, _ = env
    fake = _FakeEmbedder()
    monkeypatch.setattr(vecmodel, "model_present", lambda c: True)
    monkeypatch.setattr(vecmodel, "get_embedder", lambda c: fake)
    cfg.set("wizard_completed", True)   # 防止 processEvents 触发向导弹窗阻塞
    cfg.set("vec_enabled", True)
    cfg.save()

    from lostfile.query import SearchQuery
    from lostfile.ui.mainwindow import MainWindow
    win = MainWindow(cfg, db)
    win.show()
    assert wait_until(
        lambda: (app.processEvents() or
                 (not db.has_vec_pending() and not win._searching)),
        timeout=30)
    win._execute_query(SearchQuery(keywords=["报告"], limit=100))
    assert wait_until(lambda: (app.processEvents() or not win._searching),
                      timeout=15)
    top = win.results_view.group_tree.topLevelItem(0)
    assert top is not None
    assert "语义相关" in top.text(0)
    child = top.child(0)
    assert child.text(0) == "报告.pdf"
    assert "语义相似度" in child.toolTip(0)
    win.shutdown()
    db.close()