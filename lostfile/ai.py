"""可选联网 AI 解析：把自然语言描述转为结构化搜索条件（手动触发）。

隐私红线：只发送用户输入的描述文字；绝不发送文件名/路径/索引/文件内容。
任何失败（网络/超时/非法输出）→ 返回 (None, error)，由 UI 回退本地规则解析。

输出的条件 schema 与 nl.py 完全一致（kind ∈ location/time/size/type/keywords），
这样 AI 结果与本地解析共用 chips 渲染与 conditions_to_query 管线。
"""
import json
import re

from . import filetypes as ft
from . import timeutil as tu

TIME_PRESETS = {
    "today": ("今天", tu.today_range),
    "yesterday": ("昨天", tu.yesterday_range),
    "day_before": ("前天", lambda: (tu.yesterday_range()[0] - 86400,
                                    tu.yesterday_range()[0])),
    "this_week": ("本周", tu.this_week_range),
    "last_week": ("上周", tu.last_week_range),
    "this_month": ("本月", tu.this_month_range),
    "last_month": ("上个月", tu.last_month_range),
    "this_year": ("今年", lambda: tu.year_range(tu.now().year)),
    "last_year": ("去年", lambda: tu.year_range(tu.now().year - 1)),
}

TYPE_CATS = set(ft.CATEGORIES) - {"other"}
LOC_SPECIALS = {"downloads", "desktop", "documents", "drive", "path"}
UNIT_BYTES = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}

SYSTEM_PROMPT = (
    "你是一个本地文件搜索助手。用户会用一句话描述他想找的东西（可能是文件、"
    "软件、游戏、资料、某个下载来的项目等）。请依次完成三件事：\n"
    "1) 判断他描述的到底是什么（可以运用你自己的知识，例如某个下载来的项目名、"
    "游戏名、软件名分别指什么）；\n"
    "2) 猜出这个对象在本机【文件名或文件夹名】里可能出现的写法；\n"
    "3) 把描述里的其它线索（时间、大小、文件类型、位置）解析成条件。\n\n"
    "只输出一个严格的 JSON 对象，不要任何解释文字，格式固定为：\n"
    '{"understanding": "…", "names": ["…"], "conditions": [ … ]}\n\n'
    "- understanding：一句简短中文，说明你判断这是什么东西；"
    "不确定就如实写“无法确定，按字面名称检索”。\n"
    "- names：最多 5 个、用于在本地文件名与路径里匹配的名称。要包含规范写法、"
    "常见英文名或缩写、去掉空格与连字符的紧凑写法，以及你判断高度相关的同义词"
    "（例：用户说“游戏地图”，可给 游戏地图 / 地图 / map / minimap）。"
    "只给真的可能出现在文件名里的词，不要给“文件”“资料”这类过宽的词。\n"
    "- conditions：条件数组，每个对象必须是下面五种之一（kind 必填）：\n"
    '1. 位置 {"kind":"location","special":"downloads|desktop|documents|drive|path",'
    '"path":"显式路径或盘符如 D:\\\\"}'
    "——special=path 时必须给 path；special=drive 时 path 写盘符（如 D:\\\\）。\n"
    '2. 时间 {"kind":"time","field":"mtime|ctime","preset":"today|yesterday|'
    'day_before|this_week|last_week|this_month|last_month|this_year|last_year"}'
    '，或 {"kind":"time","field":"mtime|ctime","recent":{"n":3,"unit":"day|week|month"}}\n'
    '3. 大小 {"kind":"size","op":"approx|ge|le|between","value":3,"unit":"mb",'
    '"value2":5,"unit2":"mb"}'
    "——approx 表示大约；between 需给 value2/unit2；ge/le 只需 value/unit。\n"
    '4. 类型 {"kind":"type","categories":["pdf","word","excel","ppt","image",'
    '"video","audio","archive","installer","text","code"]}\n'
    '5. 关键词 {"kind":"keywords","values":["报告","report"]}'
    "——文件名里可能出现的词，最多 5 个。\n\n"
    "规则：无法归入上述类别的信息放进 keywords；不要虚构路径与数字；"
    "时间是相对当前时刻的，用 preset/recent 表达，不要自己算时间戳。"
)


def _json_parse(text: str) -> dict | None:
    """尽量宽松地取出 JSON：整段解析 → 代码块提取 → 花括号配对。"""
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def _norm_location(c: dict) -> dict | None:
    special = str(c.get("special") or "").lower()
    if special not in LOC_SPECIALS:
        return None
    if special in ("downloads", "desktop", "documents"):
        return {"kind": "location", "special": special, "path": None,
                "label": "位置：" + {"downloads": "下载目录", "desktop": "桌面",
                                    "documents": "文档目录"}[special]}
    raw_path = str(c.get("path") or "").strip().rstrip("\\/")
    if special == "drive":
        clean = raw_path.upper()
        if re.fullmatch(r"[A-Za-z]", clean):
            letter = clean
        elif re.fullmatch(r"[A-Za-z]:", clean):
            letter = clean[0]
        else:
            return None
        return {"kind": "location", "special": "drive",
                "path": f"{letter}:\\", "label": f"位置：{letter} 盘"}
    if not raw_path:
        return None
    return {"kind": "location", "special": "path", "path": raw_path,
            "label": f"位置：{raw_path}"}


def _norm_time(c: dict) -> dict | None:
    field = str(c.get("field") or "mtime").lower()
    if field not in ("mtime", "ctime"):
        field = "mtime"
    field_label = "创建时间" if field == "ctime" else "修改时间"
    preset = str(c.get("preset") or "").lower()
    if preset in TIME_PRESETS:
        text, fn = TIME_PRESETS[preset]
        s, e = fn()
        return {"kind": "time", "field": field, "start": int(s), "end": int(e),
                "label": f"{field_label}：{text}（{tu.fmt_date(int(s))} 起）"}
    recent = c.get("recent") or {}
    n = int(recent.get("n") or 1) if isinstance(recent, dict) else 1
    unit = str(recent.get("unit") or "day").lower() if isinstance(recent, dict) else "day"
    if unit not in ("day", "week", "month"):
        unit = "day"
    n = max(1, min(365, n))
    days = n * {"day": 1, "week": 7, "month": 30}[unit]
    s, e = tu.last_days_range(days)
    unit_cn = {"day": "天", "week": "周", "month": "个月"}[unit]
    return {"kind": "time", "field": field, "start": int(s), "end": int(e),
            "label": f"{field_label}：最近 {n} {unit_cn}"
                     f"（{tu.fmt_date(int(s))} 起）"}


def _bytes(c: dict, prefix: str) -> float | None:
    v = c.get(prefix)
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    unit = str(c.get(prefix.replace("value", "unit")) or "b").lower()
    return v * UNIT_BYTES.get(unit, 1)


def _norm_size(c: dict) -> dict | None:
    op = str(c.get("op") or "approx").lower()
    if op == "between":
        lo, hi = _bytes(c, "value"), _bytes(c, "value2")
        if lo is None or hi is None:
            return None
        if lo > hi:
            lo, hi = hi, lo
        label = f"大小：{ft.human_size(lo)} 到 {ft.human_size(hi)}"
        return {"kind": "size", "min": int(lo), "max": int(hi), "label": label}
    v = _bytes(c, "value")
    if v is None:
        return None
    if op == "ge":
        return {"kind": "size", "min": int(v), "max": None,
                "label": f"大小：大于 {ft.human_size(v)}"}
    if op == "le":
        return {"kind": "size", "min": None, "max": int(v),
                "label": f"大小：小于 {ft.human_size(v)}"}
    lo, hi = int(v * 2 / 3), int(v * 5 / 3)
    return {"kind": "size", "min": lo, "max": hi,
            "label": f"大小：{ft.human_size(lo)} 到 {ft.human_size(hi)}（约数自动放宽）"}


def _norm_type(c: dict) -> dict | None:
    cats = [str(x).lower() for x in (c.get("categories") or [])
            if str(x).lower() in TYPE_CATS]
    if not cats:
        return None
    labels = [ft.LABELS[x] for x in cats]
    return {"kind": "type", "categories": cats, "exts": [],
            "label": "文件类型：" + "、".join(labels)}


def _norm_keywords(c: dict) -> dict | None:
    vals = c.get("values")
    if isinstance(vals, str):
        vals = [vals]
    kws: list[str] = []
    for v in vals or []:
        v = str(v).strip()
        if v and v not in kws and len(v) >= 2:
            kws.append(v)
    kws = kws[:5]
    if not kws:
        return None
    return {"kind": "keywords", "values": kws,
            "label": "文件名关键词：" + "、".join(kws)}


_NORMALIZERS = {
    "location": _norm_location,
    "time": _norm_time,
    "size": _norm_size,
    "type": _norm_type,
    "keywords": _norm_keywords,
}


def normalize_conditions(raw_conds: list) -> list[dict]:
    """把 LLM 输出的宽松 JSON 条件规范化为 nl 条件 schema。"""
    out: list[dict] = []
    if not isinstance(raw_conds, list):
        return out
    for c in raw_conds:
        if not isinstance(c, dict):
            continue
        kind = str(c.get("kind") or "").lower()
        norm = _NORMALIZERS.get(kind)
        if norm is None:
            continue
        try:
            cond = norm(c)
        except Exception:
            cond = None
        if cond and not any(o == cond for o in out):
            out.append(cond)
    return out


def _norm_names(raw) -> list[str]:
    """把 AI 给出的“名称线索”清理为可直接用于本地匹配的关键词。"""
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for v in raw or []:
        v = str(v).strip()
        key = v.lower()
        if len(v) < 2 or key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out[:5]


def build_result(obj: dict) -> dict:
    """把 AI 的宽松 JSON 规范化为解析结果。

    返回 {"understanding": str, "names": [str], "conditions": [nl 条件...]}；
    AI 给出的名称线索会并入关键词条件（本地文件名/路径匹配的主线索）。
    """
    conds = normalize_conditions(obj.get("conditions"))
    names = _norm_names(obj.get("names"))
    understanding = str(obj.get("understanding") or "").strip()[:120]
    if names:
        kw = next((c for c in conds if c.get("kind") == "keywords"), None)
        if kw is None:
            new_kw = _norm_keywords({"values": names})
            if new_kw:
                conds.append(new_kw)
        else:
            vals = list(kw.get("values") or [])
            low = {v.lower() for v in vals}
            for n in names:
                if n.lower() not in low:
                    vals.append(n)
                    low.add(n.lower())
            vals = vals[:5]
            kw["values"] = vals
            kw["label"] = "文件名关键词：" + "、".join(vals)
    return {"understanding": understanding, "names": names,
            "conditions": conds}


def parse_with_llm(text: str, cfg, timeout_s: float = 20.0):
    """联网调用 AI 分析描述并给出搜索条件。

    返回 (result | None, error | None)；result 见 build_result()。
    任何失败都返回 error，由界面回退本地规则解析。
    """
    from . import secrets  # 延迟导入：本模块被 ai 测试直接引用
    try:
        import httpx
    except ImportError:
        return None, "缺少 httpx 依赖"
    base = str(cfg.get("ai_base_url") or "").strip().rstrip("/")
    model = str(cfg.get("ai_model") or "").strip()
    if not base or not model:
        return None, "未配置 AI 服务（请在 设置 → AI 里填写端点与模型）"
    key = secrets.decrypt_str(cfg.get("ai_api_key_enc") or "") or ""
    url = base if base.endswith("/chat/completions") \
        else base + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (text or "").strip()},
        ],
        "temperature": 0,
        "max_tokens": 800,
    }
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=timeout_s)
    except Exception as e:
        return None, f"网络请求失败：{type(e).__name__}"
    if resp.status_code != 200:
        return None, f"AI 服务返回错误（HTTP {resp.status_code}）"
    try:
        payload = resp.json()
    except Exception:
        return None, "AI 服务返回的不是有效响应"
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None, "AI 返回内容结构异常"
    obj = _json_parse(content)
    if obj is None:
        return None, "AI 未返回可解析的 JSON，已回退本地规则解析"
    result = build_result(obj)
    if not result["conditions"]:
        return None, "AI 没有解析出可用条件，已回退本地规则解析"
    return result, None