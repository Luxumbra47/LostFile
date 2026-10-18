"""自然语言解析测试：覆盖规格书全部示例及边界情况。"""
import pytest

from lostfile import nl, timeutil as tu
from lostfile.query import SearchQuery

MB = 1024 ** 2


def conds(text):
    return nl.parse(text)


def test_example_1_yesterday_pdf_3mb():
    r = conds("昨天下载的 PDF，大概 3MB")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["type"]["categories"] == ["pdf"]
    assert kinds["location"]["special"] == "downloads"
    s = kinds["size"]
    assert s["min"] == pytest.approx(2 * MB, rel=0.01)
    assert s["max"] == pytest.approx(5 * MB, rel=0.01)
    t = kinds["time"]
    assert (t["start"], t["end"]) == tu.yesterday_range()


def test_example_2_last_week_modified_image():
    r = conds("上周修改的图片")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["type"]["categories"] == ["image"]
    t = kinds["time"]
    assert t["field"] == "mtime"
    assert t["start"] == tu.last_week_range()[0]
    # “修改”不应成为文件名关键词
    assert all(c["kind"] != "keywords" or "修改" not in c["values"]
               for c in r.conditions)


def test_example_3_downloads_over_100mb_video():
    r = conds("下载目录里超过 100MB 的视频")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["location"]["special"] == "downloads"
    assert kinds["size"]["min"] == 100 * MB
    assert kinds["size"]["max"] is None
    assert kinds["type"]["categories"] == ["video"]


def test_example_4_last_month_created_word():
    r = conds("最近一个月创建的 Word 文件")
    kinds = {c["kind"]: c for c in r.conditions}
    t = kinds["time"]
    assert t["field"] == "ctime"
    assert t["start"] == tu.last_days_range(30)[0]
    assert kinds["type"]["categories"] == ["word"]


def test_example_5_recently_created_word():
    r = conds("帮我找出最近创建的 Word 文档")
    kinds = {c["kind"]: c for c in r.conditions}
    t = kinds["time"]
    assert t["field"] == "ctime"
    assert t["start"] == tu.last_days_range(7)[0]
    assert kinds["type"]["categories"] == ["word"]
    assert r.unrecognized == []


def test_example_6_desktop_big_video():
    r = conds("桌面上那个很大的视频文件")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["location"]["special"] == "desktop"
    assert kinds["type"]["categories"] == ["video"]
    assert kinds["size"]["min"] == 100 * MB


def test_example_7_few_days_ago_archive():
    r = conds("前几天下载的压缩包")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["type"]["categories"] == ["archive"]
    assert kinds["location"]["special"] == "downloads"
    assert kinds["time"]["start"] == tu.last_days_range(7)[0]


def test_size_range_1_to_10mb():
    r = conds("1到10MB的图片")
    kinds = {c["kind"]: c for c in r.conditions}
    s = kinds["size"]
    assert s["min"] == 1 * MB
    assert s["max"] == 10 * MB
    assert kinds["type"]["categories"] == ["image"]


def test_size_range_with_tilde():
    r = conds("找 2~5 MB 的压缩包")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["size"]["min"] == 2 * MB
    assert kinds["size"]["max"] == 5 * MB


def test_less_than_and_more_than():
    r = conds("小于 1MB 的文档")
    assert r.conditions and any(
        c["kind"] == "size" and c["min"] is None and c["max"] == 1 * MB
        for c in r.conditions
    )
    r2 = conds("大于500KB的PDF")
    assert any(
        c["kind"] == "size" and c["min"] is not None
        and abs(c["min"] - 500 * 1024) < 1
        for c in r2.conditions
    )


def test_explicit_path_location():
    r = conds(r"D:\downloads\temp 下的图片")
    assert any(c["kind"] == "location" and c["path"] == r"D:\downloads\temp"
               for c in r.conditions)


def test_drive_letter_location():
    r = conds("D盘上的视频")
    assert any(c["kind"] == "location" and c["path"] == "D:\\"
               for c in r.conditions)


def test_drive_letter_location_tolerates_space():
    """带空格的写法（D 盘）也要识别成位置，而不是变成“盘上”关键词。"""
    r = conds("D 盘上的压缩包")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["location"]["path"] == "D:\\"
    assert "盘上" not in (kinds.get("keywords", {}).get("values") or [])
    assert not r.unrecognized


def test_received_file_has_no_spurious_keyword():
    """“昨天收到的压缩包”：收到的只是语气词，不该变成文件名关键词。"""
    r = conds("昨天收到的压缩包")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["type"]["categories"] == ["archive"]
    assert (kinds["time"]["start"], kinds["time"]["end"]) == tu.yesterday_range()
    assert "keywords" not in kinds


def test_n_years_ago_range():
    """“两年前的视频”：相对年份也要能解析成时间条件。"""
    r = conds("两年前的视频")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["type"]["categories"] == ["video"]
    assert kinds["time"]["start"] == tu.year_range(tu.now().year - 2)[0]
    assert not r.unrecognized


def test_all_search_hints_are_parseable(app, tmp_path):
    """搜索栏轮换的每条提示词都必须是可直接执行的查询，且能被解析出条件。

    约定：提示词只放“用户能照着输入的搜索例子”，能力说明写在悬浮提示里；
    示例一律用通用词汇（不出现任何个人化的搜索记录）。
    """
    from lostfile.config import AppConfig
    from lostfile.db import Database
    from lostfile.ui.mainwindow import MainWindow

    cfg = AppConfig(data_dir=str(tmp_path / "data"))
    db = Database.open_safe(cfg.db_path())
    cfg.set("wizard_completed", True)
    cfg.save()
    win = MainWindow(cfg, db)
    hints = list(win._hints)
    assert len(hints) >= 20, f"提示词数量偏少：{len(hints)}"
    assert len(set(hints)) == len(hints), "提示词不应重复"
    for h in hints:
        r = conds(h)
        assert r.conditions, f"提示词无法解析：{h!r}"
        assert not r.unrecognized, f"提示词有未理解的部分：{h!r} -> {r.unrecognized}"
    win.shutdown()
    db.close()


def test_keywords_from_leftover():
    r = conds("meeting_notes 的 PDF")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["keywords"]["values"] == ["meeting_notes"]
    assert kinds["type"]["categories"] == ["pdf"]


def test_plain_english_keyword():
    """纯英文输入（如 minecraft）应解析为文件名关键词，不得报错或产生其他条件。"""
    r = conds("minecraft")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["keywords"]["values"] == ["minecraft"]
    assert set(kinds.keys()) == {"keywords"}
    assert r.unrecognized == []


def test_english_filename_with_dot():
    r = conds("nvngx_dlss.dll")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["keywords"]["values"] == ["nvngx_dlss.dll"]


def test_unknown_sentence_friendly():
    r = conds("请帮我随便找一个文件")
    # 纯客套句无法解析出任何搜索条件：如实返回空
    assert r.conditions == []


def test_specific_date():
    r = conds("2026年10月1日 修改的表格")
    kinds = {c["kind"]: c for c in r.conditions}
    t = kinds["time"]
    assert t["start"] == tu.month_day_range(10, 1)[0]


def test_conditions_to_query_mapping():
    r = conds("昨天下载的 PDF，大概 3MB")
    q = nl.conditions_to_query(r.conditions)
    assert isinstance(q, SearchQuery)
    assert q.categories == ["pdf"]
    assert q.size_min is not None and q.size_max is not None
    assert q.time_start is not None
    assert q.locations  # 下载目录已解析为真实路径


def test_today_yesterday_presets():
    r = conds("今天下载的文档")
    t = {c["kind"]: c for c in r.conditions}["time"]
    assert t["start"] == tu.today_range()[0]


def test_verb_forms_are_not_keywords():
    """回归：'今天我改过的文件' 里的动词不应被当成文件名关键词。"""
    for text in ("今天我改过的文件", "今天改过的文件", "最近改过的文档"):
        r = conds(text)
        kinds = {c["kind"]: c for c in r.conditions}
        assert "time" in kinds
        kws = kinds.get("keywords", {}).get("values", [])
        assert "改过" not in kws, f"{text} 把动词当成了关键词"
        assert "改的" not in kws and "改" not in kws


def test_hyphenated_keyword_not_reported_unrecognized():
    """回归：project-notes 被拆成 project/notes 后不应报“未理解”。"""
    r = conds("我昨天下载的 project-notes skill")
    kinds = {c["kind"]: c for c in r.conditions}
    assert "project-notes" in kinds["keywords"]["values"]
    assert "project" not in r.unrecognized
    assert "notes" not in r.unrecognized


def test_type_word_fully_consumed():
    """回归：'文档目录里的 Excel 表格' 里重复出现的类型词应全部消费，
    不得残留成多余的文件名关键词。"""
    r = conds("文档目录里的 Excel 表格")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["type"]["categories"] == ["excel"]
    assert kinds["location"]["special"] == "documents"
    assert kinds.get("keywords", {}).get("values", []) == []


def test_recent_n_weeks_fully_consumed():
    """回归：'最近一周' 曾只消费“最近”，把“一周”漏成文件名关键词。"""
    r = conds("最近一周改过的 Excel")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["type"]["categories"] == ["excel"]
    assert "keywords" not in kinds  # 不允许残留“一周”
    span = kinds["time"]["end"] - kinds["time"]["start"]
    assert 6 * 86400 <= span <= 8 * 86400  # 约 7 天

    # 天数说法保持原样
    r2 = conds("最近 3 天改的文件")
    k2 = {c["kind"]: c for c in r2.conditions}
    assert 2 * 86400 <= k2["time"]["end"] - k2["time"]["start"] <= 4 * 86400


def test_drive_beats_known_folder():
    """回归：'D盘桌面上的压缩包' —— 盘符应优先于 Windows 注册的桌面目录，
    否则会去查 C:\\Users\\...\\Desktop（往往不是用户实际的桌面）。"""
    r = conds("D盘桌面上的压缩包")
    kinds = {c["kind"]: c for c in r.conditions}
    assert kinds["location"]["special"] == "drive"
    assert kinds["location"]["path"] == "D:\\"
    assert kinds["type"]["categories"] == ["archive"]

    # 不带盘符时仍按“桌面”解析
    r2 = conds("桌面上的压缩包")
    assert {c["kind"]: c for c in r2.conditions}["location"]["special"] == "desktop"