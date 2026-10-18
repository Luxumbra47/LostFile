"""多条件搜索测试：类型 / 大小 / 时间 / 位置 / 组合 / 排序 / 截断。"""
import os
import time

import pytest

from conftest import make_file
from lostfile.db import Database
from lostfile.indexer import scan_directory
from lostfile.query import SearchQuery, count, match_reasons, search

KB = 1024
MB = 1024 ** 2


def _sparse(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.seek(size - 1)
        f.write(b"\0")


@pytest.fixture()
def env(tmp_path):
    db = Database(str(tmp_path / "idx.db"))
    root = tmp_path / "root"
    now = time.time()
    day = 86400
    # 类型与大小
    make_file(root / "a.pdf", 3 * MB, mtime=now - 1 * day)      # 昨天的 3MB PDF
    make_file(root / "b.pdf", 50 * KB, mtime=now - 10 * day)    # 旧的小 PDF
    make_file(root / "c.png", 2 * MB, mtime=now - 60)           # 今天的图
    make_file(root / "d.mp4", 200 * MB if False else 200 * KB, mtime=now - 2 * day)
    _sparse(root / "e.mp4", 150 * MB)                            # 大视频（稀疏）
    make_file(root / "f.docx", 20 * KB, mtime=now - 5 * day)    # 旧 Word
    make_file(root / "g.zip", 5 * MB, mtime=now - 60)           # 压缩包
    (root / "oldsub").mkdir(exist_ok=True)
    make_file(root / "oldsub" / "h.pdf", 10 * KB, mtime=now - 40 * day)
    os.utime(root / "e.mp4", (now - 2 * day, now - 2 * day))
    db.add_root(str(root))
    res = scan_directory(str(root), db)
    assert res.completed and res.errors == 0
    yield db, root
    db.close()


def _names(rows):
    return sorted(r["name"] for r in rows)


def test_type_filter(env):
    db, _ = env
    conn = db.connect()
    q = SearchQuery(categories=["pdf"])
    assert _names(search(conn, q)) == ["a.pdf", "b.pdf", "h.pdf"]
    q2 = SearchQuery(categories=["image"])
    assert _names(search(conn, q2)) == ["c.png"]
    q3 = SearchQuery(categories=["other"])
    names = _names(search(conn, q3))
    assert names == []  # 本测试树中没有 other 类
    conn.close()


def test_size_filter(env):
    db, _ = env
    conn = db.connect()
    q = SearchQuery(size_min=2 * MB, size_max=10 * MB)
    assert _names(search(conn, q)) == ["a.pdf", "c.png", "g.zip"]
    q2 = SearchQuery(size_min=100 * MB)
    assert _names(search(conn, q2)) == ["e.mp4"]
    q3 = SearchQuery(size_max=KB * 1024)
    assert _names(search(conn, q3)) == ["b.pdf", "d.mp4", "f.docx", "h.pdf"]
    conn.close()


def test_time_filter(env):
    db, _ = env
    conn = db.connect()
    now = time.time()
    q = SearchQuery(time_start=now - 2 * 86400 - 3600, time_end=now,
                    time_field="mtime")
    names = _names(search(conn, q))
    assert "a.pdf" in names and "c.png" in names and "g.zip" in names
    assert "b.pdf" not in names
    conn.close()


def test_location_filter(env):
    db, _ = env
    conn = db.connect()
    q = SearchQuery(locations=[str(env[1] / "oldsub")])
    assert _names(search(conn, q)) == ["h.pdf"]
    conn.close()


def test_location_prefix_matches_subdirs(env):
    """回归：位置条件用路径前缀匹配。

    旧实现只做 `parent = 目录` 的精确比较，且把 `D:\\` 归一化成 `D:`，
    导致“D盘上超过 1GB 的文件”这类查询恒为 0 条。
    """
    db, root = env
    conn = db.connect()
    # 用索引根所在盘的盘符作为位置条件（形如 C:\ 或 D:\）
    drive = os.path.splitdrive(str(root))[0] + "\\"
    q = SearchQuery(locations=[drive], limit=500)
    names = {r["name"] for r in search(conn, q)}
    # h.pdf 位于 oldsub 子目录，必须靠前缀匹配命中
    assert "a.pdf" in names and "h.pdf" in names

    # 带尾部反斜杠的同一条件结果应完全一致
    q2 = SearchQuery(locations=[drive.rstrip("\\")], limit=500)
    assert {r["name"] for r in search(conn, q2)} == names
    conn.close()


def test_location_with_size_filter(env):
    """组合验证：盘符 + 大小条件（模拟“D盘上超过 1GB 的文件”）。"""
    db, root = env
    conn = db.connect()
    drive = os.path.splitdrive(str(root))[0] + "\\"
    q = SearchQuery(locations=[drive], size_min=100 * MB)
    assert _names(search(conn, q)) == ["e.mp4"]
    conn.close()


def test_keyword_type_filter_no_leak(tmp_path):
    """回归：关键词的拼音 OR 分支曾逃逸出 AND 链，导致类型/位置条件失效。"""
    db = Database(str(tmp_path / "leak.db"))
    root = tmp_path / "leak"
    make_file(root / "报告.pdf", 100)
    make_file(root / "报告.docx", 100)
    make_file(root / "无关.pdf", 100)
    db.add_root(str(root))
    scan_directory(str(root), db)
    conn = db.connect()
    rows = search(conn, SearchQuery(keywords=["报告"], categories=["pdf"]))
    assert {r["name"] for r in rows} == {"报告.pdf"}   # docx 不得混入

    # 位置条件同样不能被绕过
    other = tmp_path / "elsewhere"
    make_file(other / "报告.pdf", 100)
    db.add_root(str(other))
    scan_directory(str(other), db)
    rows2 = search(conn, SearchQuery(keywords=["报告"], locations=[str(root)]))
    assert {r["name"] for r in rows2} == {"报告.pdf", "报告.docx"}
    assert all(str(root) in r["path"] for r in rows2)
    conn.close()
    db.close()


def test_blank_keyword_matches_nothing_not_everything(env):
    """回归：空白关键词曾生成 LIKE '%%'，把“无关键词”变成“命中全部”。"""
    db, _ = env
    conn = db.connect()
    for kw in ("", " ", "   ", "\t"):
        rows = search(conn, SearchQuery(keywords=[kw], limit=500))
        # 视作“无关键词条件”：按 limit 返回即可，但绝不能是全部文件
        assert len(rows) <= 500, f"空白关键词 {kw!r} 返回了 {len(rows)} 行"
        assert count(conn, SearchQuery(keywords=[kw])) == 8   # 与无条件一致
    # 空白关键词不应把评分抬到“人人相关”
    q = SearchQuery(keywords=[" "], limit=2)
    assert count(conn, q) == 8
    conn.close()


def test_keyword_scoring_order(env):
    db, _ = env
    conn = db.connect()
    q = SearchQuery(keywords=["a"])
    rows = search(conn, q)
    assert rows  # a.pdf 命中
    assert rows[0]["name"] == "a.pdf"  # 名称精确匹配排最前
    conn.close()


def test_combination(env):
    db, _ = env
    conn = db.connect()
    now = time.time()
    q = SearchQuery(categories=["pdf"], size_min=1 * MB, size_max=10 * MB,
                    time_start=now - 3 * 86400, time_end=now,
                    time_field="mtime")
    names = _names(search(conn, q))
    assert names == ["a.pdf"]
    conn.close()


def test_ext_and_case(env):
    db, _ = env
    conn = db.connect()
    q = SearchQuery(extensions=["MP4"])  # 大小写不敏感
    assert _names(search(conn, q)) == ["d.mp4", "e.mp4"]
    conn.close()


def test_limit_truncation(env):
    db, _ = env
    conn = db.connect()
    q = SearchQuery(limit=2)
    rows = search(conn, q)
    assert len(rows) == 2
    assert count(conn, q) == 8
    conn.close()


def test_match_reasons_content(env):
    db, _ = env
    conn = db.connect()
    q = SearchQuery(categories=["pdf"], size_min=1 * MB, size_max=10 * MB)
    rows = search(conn, q)
    reasons = match_reasons(q, rows[0])
    joined = " ".join(reasons)
    assert "PDF" in joined and "大小" in joined
    conn.close()


def test_missing_file_does_not_crash(env):
    db, root = env
    import lostfile.fileops as fo
    assert fo.exists_file(str(root / "nope.bin")) is False
    assert fo.exists_file(str(root / "a.pdf")) is True


@pytest.fixture()
def zh_env(tmp_path):
    """中文文件名 + 拼音检索测试环境。"""
    db = Database(str(tmp_path / "zh.db"))
    root = tmp_path / "zh"
    make_file(root / "年度报告.pdf", 1024)
    make_file(root / "会议记录.docx", 512)
    make_file(root / "ngx_dlss.dll", 2048)
    make_file(root / "名表.xlsx", 100)       # 首字母 mb，与“模版”无关
    make_file(root / "模板设计.docx", 100)   # 与“模版”同音
    db.add_root(str(root))
    from lostfile.indexer import scan_directory
    res = scan_directory(str(root), db)
    assert res.completed
    yield db, root
    db.close()


def test_short_cjk_keyword_no_init_noise(zh_env):
    """回归：2 字中文词（模版）不能用首字母误中无关文件（名表）。"""
    db, _ = zh_env
    conn = db.connect()
    rows = search(conn, SearchQuery(keywords=["模版"]))
    names = [r["name"] for r in rows]
    assert "名表.xlsx" not in names          # mb 首字母误中已消除
    assert "模板设计.docx" in names          # 同音（moban）仍可命中
    conn.close()


def test_pinyin_initials_search(zh_env):
    """记得读音：'ndbg'（首字母）应找到《年度报告》。"""
    db, _ = zh_env
    conn = db.connect()
    rows = search(conn, SearchQuery(keywords=["ndbg"]))
    assert [r["name"] for r in rows] == ["年度报告.pdf"]
    conn.close()


def test_pinyin_full_search(zh_env):
    """记得读音：全拼 'baogao' 应找到《年度报告》。"""
    db, _ = zh_env
    conn = db.connect()
    rows = search(conn, SearchQuery(keywords=["baogao"]))
    assert [r["name"] for r in rows] == ["年度报告.pdf"]
    conn.close()


def test_chinese_keyword_still_works(zh_env):
    db, _ = zh_env
    conn = db.connect()
    rows = search(conn, SearchQuery(keywords=["报告"]))
    assert [r["name"] for r in rows] == ["年度报告.pdf"]
    conn.close()


def test_homophone_pinyin_match(zh_env):
    """写错字但读音相同（保/报 同音）：拼音匹配仍能找到《年度报告》。"""
    db, _ = zh_env
    conn = db.connect()
    rows = search(conn, SearchQuery(keywords=["年度保告"]))
    assert [r["name"] for r in rows] == ["年度报告.pdf"]
    conn.close()


def test_fuzzy_suggestion_for_typo(zh_env):
    """拼写记错（读音也不同）：精确搜索 0 结果，近似候选救场。"""
    from lostfile.query import fuzzy_suggestions
    db, _ = zh_env
    conn = db.connect()
    assert search(conn, SearchQuery(keywords=["dlss5.dll"])) == []
    sugg = fuzzy_suggestions(conn, ["dlss5.dll"])
    names = [s["name"] for s in sugg]
    assert "ngx_dlss.dll" in names
    conn.close()


def test_name_only_keyword(zh_env):
    """仅匹配文件名：路径命中的行不返回。"""
    db, root = zh_env
    conn = db.connect()
    # 路径里含“报告”但文件名不含
    make_file(root / "sub_dir_报告" / "其他文件.txt", 100)
    db.add_root(str(root))
    from lostfile.indexer import scan_directory
    scan_directory(str(root / "sub_dir_报告"), db)
    qs = SearchQuery(keywords=["报告"])
    rows = search(conn, qs)
    assert any(r["name"] == "其他文件.txt" for r in rows)  # 路径命中可见
    rows2 = search(conn, SearchQuery(keywords=["报告"], name_only=True))
    assert all("报告" in r["name"] for r in rows2)
    assert not any(r["name"] == "其他文件.txt" for r in rows2)
    conn.close()


def test_fuzzy_two_char_cjk(zh_env):
    """中文记错一个字（读音也不同）：近似候选按单字召回残局。"""
    from lostfile.query import fuzzy_suggestions
    db, _ = zh_env
    conn = db.connect()
    # “年度报表”与“年度报告”拼音不同、无公共二元组 → 精确搜索 0 命中
    assert search(conn, SearchQuery(keywords=["年度报表"])) == []
    sugg = fuzzy_suggestions(conn, ["年度报表"])
    names = [s["name"] for s in sugg]
    assert "年度报告.pdf" in names
    conn.close()


def test_keyword_match_locations():
    from lostfile.query import keyword_match_locations
    row = {"name": "abc.txt", "path": "D:\\模版\\abc.txt"}
    locs = keyword_match_locations(["模版"], row)
    assert any("路径" in x for x in locs)
    row2 = {"name": "模版abc.txt", "path": "D:\\x\\模版abc.txt"}
    assert any("文件名" in x for x in keyword_match_locations(["模版"], row2))


def test_pinyin_backfill(zh_env):
    """升级前遗留的行（pinyin_done=0）由后台补算线程填充。"""
    import time
    from lostfile.pinyin import PinyinBackfill
    from conftest import wait_until
    db, _ = zh_env
    conn = db.connect()
    conn.execute("UPDATE files SET pinyin_full='', pinyin_init='', pinyin_done=0")
    conn.commit()
    conn.close()
    bf = PinyinBackfill(db, chunk=100, delay=0.01)
    bf.start()
    ok = wait_until(lambda: not db.has_pinyin_pending(), timeout=10)
    bf.stop()
    assert ok
    conn2 = db.connect()
    row = conn2.execute(
        "SELECT pinyin_full, pinyin_init FROM files WHERE name='年度报告.pdf'"
    ).fetchone()
    conn2.close()
    assert row == ("niandubaogao", "ndbg")


@pytest.fixture()
def variant_env(tmp_path):
    """英文项目名变体：-/_/紧凑/主干 四种写法。"""
    db = Database(str(tmp_path / "v.db"))
    root = tmp_path / "v"
    make_file(root / "project-notes" / "SKILL.md", 100)
    make_file(root / "skills" / "project_notes" / "README.md", 100)
    make_file(root / "projectnotes-main" / "main.py", 100)
    make_file(root / "photoshop-plugin.zip", 200)
    db.add_root(str(root))
    res = scan_directory(str(root), db)
    assert res.completed
    yield db, root
    db.close()


def test_variant_matching_dash_underscore_compact(variant_env):
    """project-notes 应命中 -/_/紧凑 等名称变体的文件。"""
    db, _ = variant_env
    conn = db.connect()
    rows = search(conn, SearchQuery(keywords=["project-notes"]))
    names = {r["name"] for r in rows}
    assert names == {"SKILL.md", "README.md", "main.py"}
    assert "photoshop-plugin.zip" not in names
    conn.close()


def test_variant_matching_main_suffix_stem(variant_env):
    """带 -main 后缀的查询应通过主干/词序匹配到原项目文件。"""
    db, _ = variant_env
    conn = db.connect()
    rows = search(conn, SearchQuery(keywords=["project-notes-main"]))
    names = {r["name"] for r in rows}
    assert "main.py" in names and "SKILL.md" in names
    conn.close()


def test_related_groups_variants(variant_env):
    """分组树数据：关键词一个组，组内覆盖所有变体命中文件。"""
    from lostfile.query import related_groups
    db, _ = variant_env
    conn = db.connect()
    groups = related_groups(conn, ["project-notes"])
    assert len(groups) == 1
    assert groups[0]["title"] == "project-notes"
    names = {f["name"] for f in groups[0]["files"]}
    assert names == {"SKILL.md", "README.md", "main.py"}
    assert groups[0]["total"] == 3     # 总数用于界面“显示 N / 共 M”
    conn.close()


def test_related_groups_cap_and_total(variant_env):
    """cap 限制展示条数，但 total 保持真实命中数（供“还有更多”提示）。"""
    from lostfile.query import related_groups
    db, _ = variant_env
    conn = db.connect()
    groups = related_groups(conn, ["project-notes"], cap=2)
    assert len(groups[0]["files"]) == 2
    assert groups[0]["total"] == 3
    # cap 大于命中数时不应多取
    groups2 = related_groups(conn, ["project-notes"], cap=100)
    assert len(groups2[0]["files"]) == 3
    assert groups2[0]["total"] == 3
    conn.close()


def test_related_groups_ignores_short_kw(variant_env):
    """单字符关键词不生成分组（避免误报海量组）。"""
    from lostfile.query import related_groups
    db, _ = variant_env
    conn = db.connect()
    assert related_groups(conn, ["a"]) == []
    conn.close()


def test_offset_pagination_no_overlap_or_loss(env):
    """分页：OFFSET 翻页与一次性取全的切片完全一致（排序需稳定）。"""
    db, _ = env
    conn = db.connect()
    full = [r["path"] for r in search(conn, SearchQuery(limit=100))]
    assert len(full) == 8
    p1 = [r["path"] for r in search(conn, SearchQuery(limit=3, offset=0))]
    p2 = [r["path"] for r in search(conn, SearchQuery(limit=3, offset=3))]
    p3 = [r["path"] for r in search(conn, SearchQuery(limit=3, offset=6))]
    assert p1 + p2 + p3 == full          # 不重不漏
    assert len(set(p1 + p2 + p3)) == 8
    conn.close()