"""多条件查询：条件模型、SQL 构建、排序打分、匹配原因与模糊建议。"""
import dataclasses
import difflib
import math
import os
import re
from dataclasses import dataclass, field

from . import filetypes as ft
from . import fts as _fts
from . import pinyin as _pinyin
from . import timeutil as tu

FIELD_LABELS = {"mtime": "修改时间", "ctime": "创建时间", "any": "创建或修改时间"}


def _is_cjk_text(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


@dataclass
class SearchQuery:
    categories: list[str] = field(default_factory=list)   # filetypes 中的类别名
    extensions: list[str] = field(default_factory=list)   # 精确扩展名（小写无点）
    keywords: list[str] = field(default_factory=list)     # 文件名/路径关键词
    time_field: str = "mtime"                             # mtime | ctime | any
    time_start: int | None = None                         # unix 秒（含）
    time_end: int | None = None                           # unix 秒（含）
    size_min: int | None = None                           # 字节（含）
    size_max: int | None = None                           # 字节（含）
    locations: list[str] = field(default_factory=list)    # 位置前缀（显示路径）
    name_only: bool = False                               # 关键词只比对文件名（不比路径）
    limit: int = 500
    offset: int = 0                                       # 分页起点（“加载更多”）

    def is_empty(self) -> bool:
        return not (
            self.categories or self.extensions or self.keywords
            or self.time_start is not None or self.time_end is not None
            or self.size_min is not None or self.size_max is not None
            or self.locations
        )


def _escape_like(s: str) -> str:
    """转义 LIKE 模式中的特殊字符（\\ % _）。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_pattern(kw: str) -> str:
    return f"%{_escape_like(kw.lower())}%"


def variant_like_patterns(kw: str) -> list[str]:
    """把一个关键词展开为多个 LIKE 模式，覆盖常见名称变体。

    例如 project-notes 会同时匹配：
    - 原样：%project-notes%
    - 紧凑：%projectnotes%（去掉 -/_/空格 等分隔符）
    - 词序：%project%notes%（各词按顺序出现、中间允许任意字符）
    - 主干：%project-notes%（去掉尾部 -main/-master/-dev 等后缀）
    """
    text = kw.strip().lower()
    seeds = [text]
    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)
    if compact and compact != text:
        seeds.append(compact)
    stem = re.sub(r"[-_\s]+(?:main|master|dev)$", "", text)
    if stem and stem != text:
        seeds.append(stem)
    tokens = re.findall(r"[0-9a-z\u4e00-\u9fff]+", text)
    patterns = ["%" + _escape_like(s) + "%" for s in seeds]
    if len(tokens) >= 2:
        patterns.append("%" + "%".join(_escape_like(t) for t in tokens) + "%")
    return list(dict.fromkeys(patterns))


def _unescape_like_core(core: str) -> str:
    """反转义 _escape_like 处理过的字面量（\\ % _ 三种转义）。"""
    out: list[str] = []
    i = 0
    while i < len(core):
        ch = core[i]
        if ch == "\\" and i + 1 < len(core) and core[i + 1] in "\\%_":
            out.append(core[i + 1])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _pure_literal(pattern: str) -> str | None:
    """%lit% 且中间不含裸 %（词序模式）时，返回反转义后的 lit；否则 None。"""
    if not (pattern.startswith("%") and pattern.endswith("%")):
        return None
    core = pattern[1:-1]
    if "%" in core:
        return None
    return _unescape_like_core(core)


def _keyword_conds(kw: str, name_only: bool, fts_ok: bool = False) -> tuple[str, list]:
    """单个关键词的 SQL 条件与绑定参数（名称变体 + 拼音近似）。

    整个条件必须用最外层括号包住：否则其中的 OR 分支会逃逸出 _where
    的 AND 链，把类型/位置等其它条件全部架空（历史 bug）。

    fts_ok=True（files_fts 已建且与 files 完全同步）时，长度>=3 码位的
    纯子串变体交给 trigram FTS 的 id 子查询，结果集与 LIKE 完全一致但
    快一个量级；短词（<3 码位 trigram 不支持）、词序模式（内部含 %）
    与拼音两列仍走 LIKE 原路径。
    """
    parts: list[str] = []
    params: list = []
    pf, pi = _kw_pinyin_values(kw)
    patterns = variant_like_patterns(kw)
    if not patterns and not pf and not pi:
        return "0", []      # 无意义关键词：不匹配任何行
    for p in patterns:
        core = _pure_literal(p)
        if fts_ok and core is not None and len(core) >= _fts.MIN_TRIGRAM:
            cols = ["name"] if name_only else ["name", "path"]
            parts.append(
                "id IN (SELECT rowid FROM files_fts"
                " WHERE files_fts MATCH ?)"
            )
            params.append(_fts.column_match(cols, _fts.quote_phrase(core)))
            continue
        if name_only:
            parts.append("lower(name) LIKE ? ESCAPE '\\'")
            params.append(p)
        else:
            parts.append(
                "(lower(name) LIKE ? ESCAPE '\\'"
                " OR lower(path) LIKE ? ESCAPE '\\')"
            )
            params += [p, p]
    if pf:
        parts.append("pinyin_full LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(pf))
    if pi:
        parts.append("pinyin_init LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(pi))
    return "(" + " OR ".join(parts) + ")", params


def _where(q: SearchQuery, fts_ok: bool = False) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []

    # ---- 类型与扩展名 ----
    raw: set[str] = {e.lower().lstrip(".") for e in q.extensions}
    for c in q.categories:
        if c != "other":
            raw |= {e.lower().lstrip(".") for e in ft.CATEGORIES.get(c, set())}
    selected = {e for e in raw if e}
    known = sorted({e.lstrip(".") for e in ft.ALL_KNOWN_EXTS})
    has_other = "other" in q.categories
    if has_other and not selected:
        marks = ",".join("?" for _ in known)
        clauses.append(f"ext NOT IN ({marks})")
        params += known
    elif selected:
        if has_other:
            marks_sel = ",".join("?" for _ in selected)
            marks_known = ",".join("?" for _ in known)
            clauses.append(
                f"(ext IN ({marks_sel}) OR ext NOT IN ({marks_known}))"
            )
            params += sorted(selected) + known
        else:
            marks = ",".join("?" for _ in selected)
            clauses.append(f"ext IN ({marks})")
            params += sorted(selected)

    # ---- 大小 ----
    if q.size_min is not None:
        clauses.append("size >= ?")
        params.append(int(q.size_min))
    if q.size_max is not None:
        clauses.append("size <= ?")
        params.append(int(q.size_max))

    # ---- 时间 ----
    if q.time_start is not None or q.time_end is not None:
        fields = ["mtime", "ctime"] if q.time_field == "any" else [q.time_field]

        def field_cond(f: str) -> str:
            local: list[str] = []
            if q.time_start is not None and q.time_end is not None:
                local.append(f"{f} BETWEEN ? AND ?")
                params.extend([int(q.time_start), int(q.time_end)])
            elif q.time_start is not None:
                local.append(f"{f} >= ?")
                params.append(int(q.time_start))
            else:
                local.append(f"{f} <= ?")
                params.append(int(q.time_end))
            return " AND ".join(local)

        if len(fields) == 1:
            clauses.append(field_cond(fields[0]))
        else:
            clauses.append("(" + " OR ".join(field_cond(f) for f in fields) + ")")

    # ---- 位置 ----
    # 匹配“位于该目录下”的文件：用完整路径做前缀匹配（含子目录）。
    # 注意 ESCAPE '\' 下，要表达「字面反斜杠 + 通配符」必须写成 \\%，
    # 写 \% 会被转义成字面百分号，导致前缀匹配永远失败。
    for loc in q.locations:
        norm = _norm_loc(loc)
        prefix = _escape_like(norm) + "\\\\%"
        clauses.append("lower(path) LIKE ? ESCAPE '\\'")
        params.append(prefix)

    # ---- 关键词 ----
    for kw in q.keywords:
        if not kw or not kw.strip():
            # 空/纯空白关键词不能参与匹配：否则会生成 LIKE '%%' 命中全部
            continue
        cond, kw_params = _keyword_conds(kw, q.name_only, fts_ok)
        clauses.append(cond)
        params += kw_params

    return " AND ".join(clauses), params


def _kw_pinyin_values(kw: str) -> tuple[str, str]:
    """返回 (全拼原始值, 首字母原始值)（小写、可为空串）。

    - 含中文：按拼音生成；2 字及以下的查询只用全拼（首字母过于宽泛，
      如“模版”的首字母 mb 会误中大量无关文件），3 字及以上两者都用；
    - 纯英文字母且长度>=3：可能是“中文文件名的拼音写法”，参与两列匹配。
    """
    if _is_cjk_text(kw):
        full, init = _pinyin.query_pinyin(kw)
        full = full.lower() if full else ""
        init = init.lower() if init else ""
        cjk_count = sum(1 for ch in kw if "\u4e00" <= ch <= "\u9fff")
        if cjk_count < 3:
            return full, ""
        return full, init
    if kw.isascii() and kw.isalpha() and len(kw) >= 3:
        return kw.lower(), kw.lower()
    return "", ""


def _norm_loc(loc: str) -> str:
    return os.path.normcase(os.path.normpath(loc)).rstrip("\\/").lower()


def _score_sql(keywords: list[str]) -> tuple[str, list]:
    """每关键词生成一段打分 CASE，分数求和后作为 _score。

    打分：名称精确 100 > 名称前缀 80 > 名称包含 60 > 路径包含 40 >
          拼音首字母前缀 45 > 拼音全拼包含 35。
    """
    if not keywords:
        return "0", []
    kws = [kw for kw in keywords if kw and kw.strip()]   # 空白关键词不参与打分
    if not kws:
        return "0", []
    parts: list[str] = []
    params: list = []
    for kw in kws:
        pf, pi = _kw_pinyin_values(kw)
        params += [kw, kw, kw, kw]
        case = (
            "CASE"
            " WHEN lower(name) = ? THEN 100"
            " WHEN lower(name) LIKE ? || '%' ESCAPE '\\' THEN 80"
            " WHEN lower(name) LIKE '%' || ? || '%' ESCAPE '\\' THEN 60"
            " WHEN lower(path) LIKE '%' || ? || '%' ESCAPE '\\' THEN 40"
        )
        if pi:
            case += " WHEN pinyin_init LIKE ? || '%' ESCAPE '\\' THEN 45"
            params.append(pi)
        if pf:
            case += " WHEN pinyin_full LIKE '%' || ? || '%' ESCAPE '\\' THEN 35"
            params.append(pf)
        case += " ELSE 0 END"
        parts.append(case)
    return "(" + " + ".join(parts) + ")", params


def _fts_ok(conn) -> bool:
    """该连接上 files_fts 是否可用且与 files 完全同步。"""
    try:
        return _fts.ready(conn)
    except Exception:
        return False


def search(conn, q: SearchQuery) -> list[dict]:
    fts_ok = _fts_ok(conn)
    where, params = _where(q, fts_ok)
    score_sql, score_params = _score_sql(q.keywords)
    sql = (
        f"SELECT path, name, ext, size, mtime, ctime, parent,"
        f" {score_sql} AS _score FROM files"
    )
    if where:
        sql += " WHERE " + where
    # ORDER BY 末尾的 id 是分页稳定性的兜底：score 与 mtime 相同的文件若
    # 不定序，翻页时会出现重复或漏行。LIMIT/OFFSET 支撑“加载更多”。
    sql += " ORDER BY _score DESC, mtime DESC, id ASC LIMIT ? OFFSET ?"
    # 注意绑定顺序必须按占位符出现顺序：SELECT 中的评分参数在前。
    # limit 下限截到 0：SQLite 会把负数 LIMIT 当作“不限”，会拉回上百万行。
    rows = conn.execute(
        sql,
        score_params + params
        + [max(0, int(q.limit)), max(0, int(q.offset))],
    ).fetchall()
    return [
        {
            "path": r[0], "name": r[1], "ext": r[2], "size": r[3],
            "mtime": r[4], "ctime": r[5], "parent": r[6], "score": r[7],
        }
        for r in rows
    ]


def count(conn, q: SearchQuery) -> int:
    where, params = _where(q, _fts_ok(conn))
    sql = "SELECT COUNT(*) FROM files"
    if where:
        sql += " WHERE " + where
    return int(conn.execute(sql, params).fetchone()[0])


def without_keywords(q: "SearchQuery") -> "SearchQuery":
    """去掉关键词的查询副本（用于“按其他条件回退显示”）。"""
    return dataclasses.replace(q, keywords=[])


def fuzzy_suggestions(conn, keywords: list[str], limit: int = 8,
                      candidate_limit: int = 20000,
                      min_ratio: float = 0.55) -> list[dict]:
    """关键词精确搜索无结果时，给出“是不是要找”的近似候选。

    算法：关键词相邻字符二元组粗筛（SQL）→ 二元组重合数过滤 →
    SequenceMatcher 相似度排序。纯本地计算，不联网。
    """
    if not keywords:
        return []
    kw = keywords[0].strip().lower()
    if len(kw) < 2:
        return []
    cjk = _is_cjk_text(kw)
    bigrams = sorted({kw[i:i + 2] for i in range(len(kw) - 1)})
    # 中文允许更宽松的重合要求（错一两个字也能召回），英文保持较严
    need = 1 if cjk else max(1, math.ceil(len(bigrams) * 0.4))

    ors: list[str] = [
        "lower(name) LIKE ? ESCAPE '\\'" for _ in bigrams
    ]
    or_params: list = [f"%{b}%" for b in bigrams]
    if cjk:
        # 二字中文查询（如“模版/模板”错字）没有公共二元组时，用单字兜底召回
        for ch in dict.fromkeys(kw):
            ors.append("lower(name) LIKE ? ESCAPE '\\'")
            or_params.append(f"%{ch}%")
    conds = " OR ".join(ors)
    try:
        rows = conn.execute(
            f"SELECT name, path, ext, size, mtime, ctime, parent FROM files"
            f" WHERE ({conds}) ORDER BY mtime DESC LIMIT ?",
            or_params + [int(candidate_limit)],
        ).fetchall()
    except Exception:
        return []

    scored: list[tuple[float, tuple]] = []
    for r in rows:
        name = r[0] or ""
        name_l = name.lower()
        if kw in name_l or kw in name_l.replace(".", ""):
            continue  # 能直接命中就不用近似
        if cjk:
            # 中文按单字重合粗筛（二元组粗筛对二字查询太严）
            if not (set(kw) & set(name_l)):
                continue
        else:
            name_bg = {name_l[i:i + 2] for i in range(len(name_l) - 1)}
            if len(set(bigrams) & name_bg) < need:
                continue
        ratio = difflib.SequenceMatcher(None, kw, name_l[:64]).ratio()
        if ratio >= (0.5 if cjk else min_ratio):
            scored.append((ratio, r))
    scored.sort(key=lambda x: -x[0])
    return [
        {
            "name": r[0], "path": r[1], "ext": r[2], "size": r[3],
            "mtime": r[4], "ctime": r[5], "parent": r[6],
            "similarity": round(s, 2),
        }
        for s, r in scored[:limit]
    ]


def related_groups(conn, keywords: list[str], cap: int = 200) -> list[dict]:
    """按关键词生成“相关文件分组”，用于结果区的可折叠分组树（纯本地）。

    每组 = 一个关键词；组内文件 = 名称/路径命中该关键词变体或拼音近似的
    索引文件（按修改时间倒序，最多 cap 条）。

    cap 由界面右上角“显示数量”决定，保证分组树与结果表口径一致；
    同时返回 total（该关键词的全部命中数），供界面显示
    “显示 N / 共 M 个文件”与“还有更多”提示。
    """
    groups: list[dict] = []
    seen: set[str] = set()
    fts_ok = _fts_ok(conn)
    for kw in keywords:
        key = kw.strip().lower()
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        cond, params = _keyword_conds(kw, name_only=False, fts_ok=fts_ok)
        total = 0
        rows: list = []
        try:
            rows = conn.execute(
                "SELECT path, name, ext, size, mtime, ctime, parent FROM files"
                f" WHERE ({cond}) ORDER BY mtime DESC LIMIT ?",
                params + [int(cap)],
            ).fetchall()
            if len(rows) < int(cap):
                # 没有取满 → 已经拿到全部命中，无需再做昂贵的 COUNT
                total = len(rows)
            else:
                total = int(conn.execute(
                    f"SELECT COUNT(*) FROM files WHERE ({cond})", params
                ).fetchone()[0])
        except Exception:
            total, rows = 0, []
        groups.append({
            "title": kw.strip(),
            "total": total,
            "files": [
                {"path": r[0], "name": r[1], "ext": r[2], "size": r[3],
                 "mtime": r[4], "ctime": r[5], "parent": r[6]}
                for r in rows
            ],
        })
    return groups


def _snippet(text: str, terms: list[str], radius: int = 40) -> str:
    """取正文中首个命中词前后约 radius 字的窗口作为片段。"""
    low = text.lower()
    best_idx, best_term = -1, ""
    for t in terms:
        idx = low.find(t.lower())
        if idx >= 0 and (best_idx < 0 or idx < best_idx):
            best_idx, best_term = idx, t
    if best_idx < 0:
        return text[:radius * 2].strip()
    start = max(0, best_idx - radius)
    end = min(len(text), best_idx + len(best_term) + radius)
    out = text[start:end].replace("\r", " ").replace("\n", " ").strip()
    if start > 0:
        out = "…" + out
    if end < len(text):
        out += "…"
    return out


def content_search(conn, terms: list[str], cap: int = 200) -> dict:
    """在 files_content_fts 中搜索正文命中（trigram 要求词长 >=3 码位）。

    返回 {"total": n, "files": [文件 dict（含 snippet 字段）...]}；
    表未建/无合格词时返回空结果（不抛异常）。
    2 字中文词无法参与正文检索，是 trigram 的已知限制（短词仍走文件名/
    拼音检索），由界面与文档如实声明。
    """
    empty = {"total": 0, "files": []}
    eligible = []
    for t in terms:
        t = (t or "").strip()
        if t and t not in eligible and len(t) >= _fts.MIN_TRIGRAM:
            eligible.append(t)
    if not eligible or not _fts.table_exists(conn, _fts.CONTENT_FTS):
        return empty
    match_sql = " OR ".join(_fts.quote_phrase(t) for t in eligible)
    try:
        ids = [
            r[0] for r in conn.execute(
                f"SELECT rowid FROM {_fts.CONTENT_FTS}"
                f" WHERE {_fts.CONTENT_FTS} MATCH ? LIMIT ?",
                (match_sql, int(cap)),
            ).fetchall()
        ]
        total = len(ids)
        if len(ids) >= int(cap):
            total = int(conn.execute(
                f"SELECT COUNT(*) FROM {_fts.CONTENT_FTS}"
                f" WHERE {_fts.CONTENT_FTS} MATCH ?",
                (match_sql,),
            ).fetchone()[0])
    except Exception:
        return empty
    files: list[dict] = []
    for fid in ids:
        row = conn.execute(
            "SELECT path, name, ext, size, mtime, ctime, parent FROM files"
            " WHERE id=?",
            (int(fid),),
        ).fetchone()
        if row is None:
            continue
        text_row = conn.execute(
            f"SELECT content FROM {_fts.CONTENT_FTS} WHERE rowid=?",
            (int(fid),),
        ).fetchone()
        files.append({
            "path": row[0], "name": row[1], "ext": row[2], "size": row[3],
            "mtime": row[4], "ctime": row[5], "parent": row[6],
            "snippet": _snippet(text_row[0], eligible) if text_row else "",
        })
    files.sort(key=lambda f: -f["mtime"])
    return {"total": total, "files": files}


def keyword_match_locations(kws: list[str], row: dict) -> list[str]:
    """说明每个关键词在哪命中：文件名 / 所在路径 / 拼音近似。"""
    name_l = (row.get("name") or "").lower()
    path_l = (row.get("path") or "").lower()
    out: list[str] = []
    for kw in kws:
        k = kw.lower()
        spots: list[str] = []
        if k and k in name_l:
            spots.append("文件名")
        if k and k in path_l:
            spots.append("路径")
        if not spots:
            spots.append("读音/近似")
        out.append(f"关键词「{kw}」出现在{'、'.join(spots)}中")
    return out


def match_reasons(q: SearchQuery, row: dict | None = None) -> list[str]:
    """生成“为什么匹配”的说明；row 用于补充具体数值。"""
    out: list[str] = []
    for c in q.categories:
        if c == "other":
            out.append("文件类型符合：其他文件")
        else:
            out.append(f"文件类型符合：{ft.LABELS.get(c, c)}")
    if q.extensions:
        out.append("扩展名符合：" + "、".join("." + e for e in q.extensions))
    for kw in q.keywords:
        out.append(f"文件名或路径包含关键词「{kw}」")
    if q.time_start is not None or q.time_end is not None:
        field = FIELD_LABELS.get(q.time_field, q.time_field)
        if q.time_start is not None and q.time_end is not None:
            out.append(
                f"{field}在 {tu.fmt_ts(q.time_start)} 到 {tu.fmt_ts(q.time_end)} 之间"
            )
        elif q.time_start is not None:
            out.append(f"{field}不早于 {tu.fmt_ts(q.time_start)}")
        else:
            out.append(f"{field}不晚于 {tu.fmt_ts(q.time_end)}")
    if q.size_min is not None and q.size_max is not None:
        out.append(f"文件大小在 {ft.human_size(q.size_min)} 到 {ft.human_size(q.size_max)} 之间")
    elif q.size_min is not None:
        out.append(f"文件大小不小于 {ft.human_size(q.size_min)}")
    elif q.size_max is not None:
        out.append(f"文件大小不大于 {ft.human_size(q.size_max)}")
    for loc in q.locations:
        out.append(f"文件位于：{loc}")
    if row is not None:
        out.append(f"实际大小 {ft.human_size(row.get('size'))}"
                   f"，修改于 {tu.fmt_ts(row.get('mtime'))}")
    if not out:
        out.append("显示全部已索引文件")
    return out