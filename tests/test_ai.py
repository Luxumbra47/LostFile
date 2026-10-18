"""可选联网 AI 分析测试：mock httpx，覆盖成功/失败/回退/规范化/自动搜索。"""
import json

import httpx
import pytest

from conftest import make_file, wait_until
from lostfile import ai as aimod
from lostfile.config import AppConfig


def _cfg(tmp_path, base="https://example.com/v1", model="demo"):
    cfg = AppConfig(data_dir=str(tmp_path / "data"))
    cfg.set("ai_base_url", base)
    cfg.set("ai_model", model)
    cfg.save()
    return cfg


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _mk_post(content: str, status: int = 200):
    def fake(url, json=None, headers=None, timeout=None):
        return _Resp(status, {"choices": [{"message": {"content": content}}]})
    return fake


def test_parse_success(tmp_path, monkeypatch):
    """成功：AI 给出“这是什么”的判断与名称线索，名称并入关键词条件。"""
    content = json.dumps({
        "understanding": "用户要找的是昨天生成的一份 PDF 报告",
        "names": ["报告", "report"],
        "conditions": [
            {"kind": "time", "field": "mtime", "preset": "yesterday"},
            {"kind": "size", "op": "approx", "value": 3, "unit": "mb"},
            {"kind": "type", "categories": ["pdf"]},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr(httpx, "post", _mk_post(content))
    result, err = aimod.parse_with_llm("昨天的 PDF，大概 3MB", _cfg(tmp_path))
    assert err is None and result
    assert result["names"] == ["报告", "report"]
    assert result["understanding"].startswith("用户要找的是昨天")
    kinds = [c["kind"] for c in result["conditions"]]
    # names 自动补成关键词条件（本地文件名/路径匹配的主线索）
    assert kinds == ["time", "size", "type", "keywords"]
    kw = result["conditions"][-1]
    assert kw["values"] == ["报告", "report"]
    t = result["conditions"][0]
    assert t["start"] < t["end"] and "昨天" in t["label"]
    s = result["conditions"][1]
    assert s["min"] == int(3 * 1024 ** 2 * 2 / 3)
    assert s["max"] == int(3 * 1024 ** 2 * 5 / 3)


def test_build_result_merges_names_into_keywords():
    obj = {
        "understanding": "这是某游戏的地图模组",
        "names": ["游戏地图", "map", "地图", "MAP"],
        "conditions": [{"kind": "keywords", "values": ["地图"]}],
    }
    r = aimod.build_result(obj)
    assert r["names"] == ["游戏地图", "map", "地图"]      # 大小写去重、最多 5 个
    assert r["conditions"][0]["values"] == ["地图", "游戏地图", "map"]
    assert r["conditions"][0]["label"] == "文件名关键词：地图、游戏地图、map"
    assert r["understanding"] == "这是某游戏的地图模组"


def test_build_result_names_only():
    """AI 只给了名称线索、没给条件时，也应按名称检索而不是报失败。"""
    r = aimod.build_result({"names": ["project-notes"], "understanding": ""})
    assert r["conditions"][0]["kind"] == "keywords"
    assert r["conditions"][0]["values"] == ["project-notes"]


def test_parse_http_error(tmp_path, monkeypatch):
    def fake(url, json=None, headers=None, timeout=None):
        return _Resp(401, {"error": "auth"})
    monkeypatch.setattr(httpx, "post", fake)
    result, err = aimod.parse_with_llm("昨天的 PDF", _cfg(tmp_path))
    assert result is None and "401" in err


def test_parse_non_json_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post",
                        _mk_post("抱歉，我无法解析这句话。"))
    result, err = aimod.parse_with_llm("你好", _cfg(tmp_path))
    assert result is None and "回退" in err


def test_parse_fenced_json(tmp_path, monkeypatch):
    payload = {"understanding": "某个项目",
               "names": ["project-notes"],
               "conditions": [{"kind": "keywords", "values": ["project"]}]}
    fenced = "好的，分析结果如下：\n```json\n" + json.dumps(
        payload, ensure_ascii=False) + "\n```"
    monkeypatch.setattr(httpx, "post", _mk_post(fenced))
    result, err = aimod.parse_with_llm("找 project", _cfg(tmp_path))
    assert err is None
    kw = result["conditions"][0]
    assert kw["values"] == ["project", "project-notes"]


def test_parse_missing_config(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post", _mk_post("{}"))
    result, err = aimod.parse_with_llm("x", _cfg(tmp_path, base=""))
    assert result is None and "未配置" in err


def test_normalize_conditions_units_and_drops():
    raw = [
        {"kind": "location", "special": "drive", "path": "D:"},
        {"kind": "location", "special": "path", "path": r"C:\Users\me"},
        {"kind": "size", "op": "between", "value": 1, "unit": "mb",
         "value2": 10, "unit2": "mb"},
        {"kind": "type", "categories": ["pdf", "不存在的类型"]},
        {"kind": "nonsense", "foo": 1},
    ]
    out = aimod.normalize_conditions(raw)
    assert len(out) == 4
    assert out[0]["path"] == "D:\\"
    assert out[0]["label"] == "位置：D 盘"
    assert "不存在的类型" not in out[3]["categories"]


def test_mainwindow_ai_button_analyzes_and_searches(app, tmp_path, monkeypatch):
    """AI 按钮：展示“这是什么”的判断，并直接搜索出本地相关文件。"""
    from lostfile.db import Database
    from lostfile.ui.mainwindow import MainWindow

    cfg = AppConfig(data_dir=str(tmp_path / "data"))
    db = Database.open_safe(cfg.db_path())
    root = tmp_path / "r"
    make_file(root / "x.txt", 5)
    db.add_root(str(root))
    from lostfile.indexer import scan_directory
    scan_directory(str(root), db)          # 建好索引，才能验证“自动搜索出结果”
    cfg.set("wizard_completed", True)
    cfg.set("ai_consent", True)
    cfg.set("ai_base_url", "https://example.com/v1")
    cfg.set("ai_model", "demo")
    cfg.save()

    win = MainWindow(cfg, db)
    win.show()
    app.processEvents()

    # 成功路径：AI 分析出对象与名称 → 自动执行搜索（无需再点“确认并搜索”）
    result = {
        "understanding": "这是一个名字含 x 的文本文件",
        "names": ["x"],
        "conditions": [{"kind": "keywords", "values": ["x"],
                        "label": "文件名关键词：x"}],
    }
    monkeypatch.setattr(
        "lostfile.ai.parse_with_llm", lambda text, cfg: (result, None))
    win.nl_edit.setText("那个 x 文件")
    win._on_ai_parse()
    assert wait_until(lambda: (app.processEvents() or
                               (win._last_query is not None
                                and not win._searching)), timeout=20)
    assert win.cond_frame.isVisible()
    assert "AI 分析" in win.cond_hint.text()
    assert "名字含 x" in win.cond_hint.text()
    rows = win.results_view.current_rows()
    assert rows and any(r["name"] == "x.txt" for r in rows), "应已自动搜索出结果"

    # 失败路径：回退本地规则解析，同样直接搜索并如实提示
    monkeypatch.setattr(
        "lostfile.ai.parse_with_llm",
        lambda text, cfg: (None, "AI 服务返回错误（HTTP 500）"))
    win._last_query = None
    win.nl_edit.setText("昨天的 PDF")
    win._on_ai_parse()
    assert wait_until(lambda: (app.processEvents() or
                               (win._last_query is not None
                                and not win._searching)), timeout=20)
    assert "HTTP 500" in win.cond_hint.text()
    assert "本地规则" in win.cond_hint.text()
    assert any(c["kind"] == "time" for c in win._active_conditions)
    win.shutdown()
    db.close()