"""中文自然语言式搜索条件解析（本地规则实现，无 AI/联网）。

解析优先级：位置 > 时间 > 大小 > 类型 > 剩余词 → 文件名关键词。
无法理解的部分如实返回，供 UI 提示用户改用筛选器。
"""
import re
from dataclasses import dataclass, field

from . import filetypes as ft
from . import timeutil as tu

UNIT_MAP = {
    "b": 1, "k": 1024, "kb": 1024, "m": 1024 ** 2, "mb": 1024 ** 2,
    "g": 1024 ** 3, "gb": 1024 ** 3, "兆": 1024 ** 2,
}

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_num(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    if "十" in s:
        a, _, b = s.partition("十")
        tens = _CN_NUM.get(a, 1) if a else 1
        ones = _CN_NUM.get(b, 0) if b else 0
        return tens * 10 + ones
    return None


STOPWORDS = {
    "文件", "文件夹", "目录", "里面", "里边", "里", "中", "内", "的", "那个",
    "这个", "一个", "一些", "一下", "帮我", "请", "帮忙", "查找", "搜索",
    "找出", "找", "找找", "想", "要", "看看", "有没有", "记得", "好像是",
    "好像", "是什么", "什么", "哪个", "在哪", "在哪个", "位置", "大小",
    "时间", "大概", "大约", "左右", "最近", "几天", "之前", "以前",
    "下载过", "下载", "包含", "关于", "有关", "显示", "所有", "全部",
    "相关", "类似", "以及", "和", "与", "或", "是", "有", "没", "很",
    "非常", "比较", "特别", "大", "小", "我", "你", "一个文件",
    "修改", "编辑", "保存", "更新", "改动", "创建", "新建", "建立", "生成",
    "改过", "改的", "改了", "动过", "打开过", "用过", "看过", "玩过", "写过",
    "做过", "下过", "装过", "存过", "更新过", "保存过", "编辑过", "创建过",
    "最近改过", "动都没动过",
    "收到", "接到", "东西", "刚下", "刚刚", "刚才",
    "随便", "任何", "某些", "某个", "若干", "请问", "麻烦",
}


@dataclass
class ParseResult:
    text: str
    conditions: list[dict] = field(default_factory=list)
    unrecognized: list[str] = field(default_factory=list)

    @property
    def understood(self) -> bool:
        return bool(self.conditions)


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.buf = list(text)
        self.conditions: list[dict] = []

    # ---------- 工具 ----------

    def _consume(self, m: re.Match) -> None:
        for i in range(m.start(), m.end()):
            if not self.buf[i].isspace():
                self.buf[i] = "\u0000"

    def _remaining(self) -> str:
        return "".join(c if c != "\u0000" else " " for c in self.buf)

    # ---------- 位置 ----------

    def _pass_locations(self) -> None:
        # 位置候选按优先级收集（数字越小越优先）：
        # 显式路径 > 盘符 > 已知文件夹（下载/桌面/文档）。
        # 例：“D盘桌面上的压缩包” —— 用户的实际桌面可能放在 D:\桌面，
        # 此时盘符比 Windows 注册的 C:\Users\...\Desktop 更贴近真实意图。
        candidates: list[tuple[int, dict]] = []

        def add(pri: int, special: str, path: str | None, label: str) -> None:
            candidates.append((pri, {
                "kind": "location", "special": special, "path": path,
                "label": label,
            }))

        # 显式路径（含 UNC）—— 最高优先级
        for m in re.finditer(r"[A-Za-z]:\\[^\s，。；、,]+|\\\\[^\s，。；、]+", self.text):
            add(0, "path", m.group(0), f"位置：{m.group(0)}")
            self._consume(m)
        # 盘符（允许写成“D盘”“D 盘”“D: 盘”）
        for m in re.finditer(r"(?<![A-Za-z])([A-Za-z])\s*:?\s*盘", self.text):
            letter = m.group(1).upper()
            add(1, "drive", f"{letter}:\\", f"位置：{letter} 盘")
            self._consume(m)
        # 下载目录（长形式优先于动词“下载”）
        for m in re.finditer(r"下载目录|下载文件夹", self.text):
            add(2, "downloads", None, "位置：下载目录")
            self._consume(m)
        for m in re.finditer(r"下载(?!目录|文件夹)", self.text):
            add(2, "downloads", None, "位置：下载目录")
            self._consume(m)
        for m in re.finditer(r"桌面", self.text):
            add(2, "desktop", None, "位置：桌面")
            self._consume(m)
        for m in re.finditer(r"文档目录|文档文件夹|我的文档", self.text):
            add(2, "documents", None, "位置：文档目录")
            self._consume(m)

        if candidates:
            candidates.sort(key=lambda x: x[0])
            self.conditions.append(candidates[0][1])

    # ---------- 时间 ----------

    def _pass_time(self) -> None:
        if any(c["kind"] == "time" for c in self.conditions):
            return
        text = self._remaining()
        field = "mtime"
        if (re.search(r"创建|新建|建立|生成", self.text)
                and not re.search(r"修改|编辑|保存|更新|改动", self.text)):
            field = "ctime"
        field_label = "创建时间" if field == "ctime" else "修改时间"

        patterns: list[tuple[re.Pattern, callable]] = [
            (re.compile(r"最近(?:的)?\s*(?P<n>\d+|[一两三四五六七八九十]+)?\s*个?月(?!天)"),
             lambda m: (tu.last_days_range(30 * (_cn_num(m.group("n")) or 1)),
                        f"最近 {_cn_num(m.group('n')) or 1} 个月")),
            (re.compile(r"(?:最近|近)(?:的)?\s*(?P<n>\d+|[一两三四五六七八九十]+)\s*"
                        r"(?P<u>天|日|周|星期)"),
             lambda m: (tu.last_days_range(
                 (_cn_num(m.group("n")) or 1)
                 * (7 if m.group("u") in ("周", "星期") else 1)),
                 f"最近 {_cn_num(m.group('n')) or 1} "
                 f"{'周' if m.group('u') in ('周', '星期') else '天'}")),
            (re.compile(r"前几天|近几天|最近几天"),
             lambda m: (tu.last_days_range(7), "最近 7 天")),
            (re.compile(r"最近(?!天|周|星期|个月)"),
             lambda m: (tu.last_days_range(7), "最近 7 天")),
            (re.compile(r"今天|今日"), lambda m: (tu.today_range(), "今天")),
            (re.compile(r"昨天|昨日"), lambda m: (tu.yesterday_range(), "昨天")),
            (re.compile(r"前天"),
             lambda m: ((tu.yesterday_range()[0] - 86400, tu.yesterday_range()[0]),
                        "前天")),
            (re.compile(r"本周|这周|这个星期|这一周"),
             lambda m: (tu.this_week_range(), "本周")),
            (re.compile(r"上上周|上上星期"),
             lambda m: ((tu.last_week_range()[0] - 86400 * 7,
                         tu.last_week_range()[0]), "上上周")),
            (re.compile(r"上周|上星期|上个星期"),
             lambda m: (tu.last_week_range(), "上周")),
            (re.compile(r"本月|这个月"),
             lambda m: (tu.this_month_range(), "本月")),
            (re.compile(r"上个月|上月"),
             lambda m: (tu.last_month_range(), "上个月")),
            (re.compile(r"今年"), lambda m: (tu.year_range(tu.now().year), "今年")),
            (re.compile(r"去年"),
             lambda m: (tu.year_range(tu.now().year - 1), "去年")),
            (re.compile(r"前年"),
             lambda m: (tu.year_range(tu.now().year - 2), "前年")),
            (re.compile(r"(?<!\d)(?P<n>\d{1,2}|[一两三四五六七八九十]+)\s*年前"),
             lambda m: (tu.year_range(
                 tu.now().year - (_cn_num(m.group("n")) or 1)),
                 f"{_cn_num(m.group('n')) or 1} 年前")),
            (re.compile(r"(?P<y>\d{4})\s*年(?P<m>\d{1,2})\s*月(?P<d>\d{1,2})\s*[日号]?"),
             lambda m: ((tu.month_day_range(int(m.group("m")), int(m.group("d")))[0],
                         tu.month_day_range(int(m.group("m")), int(m.group("d")))[1]),
                        f"{m.group('y')} 年 {int(m.group('m'))} 月 {int(m.group('d'))} 日")),
            (re.compile(r"(?P<m>\d{1,2})\s*月(?P<d>\d{1,2})\s*[日号]"),
             lambda m: (tu.month_day_range(int(m.group("m")), int(m.group("d"))),
                        f"{int(m.group('m'))} 月 {int(m.group('d'))} 日")),
            (re.compile(r"(?P<y>\d{4})\s*年"),
             lambda m: (tu.year_range(int(m.group("y"))), f"{m.group('y')} 年")),
        ]

        for regex, builder in patterns:
            m = regex.search(text)
            if not m:
                continue
            rng, label_text = builder(m)
            self.conditions.append({
                "kind": "time", "field": field,
                "start": int(rng[0]), "end": int(rng[1]),
                "label": (f"{field_label}：{label_text}"
                          f"（{tu.fmt_date(rng[0])} 起）"),
            })
            self._consume(m)
            break

    # ---------- 大小 ----------

    def _pass_size(self) -> None:
        min_b = None
        max_b = None
        approx_nums: list[float] = []

        range_re = re.compile(
            r"(?P<a>\d+(?:\.\d+)?)\s*(?P<u1>kb|mb|gb|k|m|g|b|兆)?"
            r"\s*(?:到|至|~|～|-|—)\s*"
            r"(?P<b>\d+(?:\.\d+)?)\s*(?P<u2>kb|mb|gb|k|m|g|b|兆)?",
            re.IGNORECASE,
        )
        size_re = re.compile(
            r"(?:(?P<approx>大约|大概|约|差不多|好像)\s*)?"
            r"(?P<op>大于|超过|不小于|不低于|至少|最多|不超过|小于|不足|不到|不大于)?\s*"
            r"(?P<a>\d+(?:\.\d+)?)\s*(?P<u>kb|mb|gb|k|m|g|b|兆)"
            r"(?P<suffix>左右|上下|以上|以下|以内)?",
            re.IGNORECASE,
        )
        big_re = re.compile(r"很大|超大|巨大|特别大|非常大|挺大")
        small_re = re.compile(r"很小|特别小|非常小|挺小|不大|较小的")

        text = self._remaining()
        for m in re.finditer(range_re, text):
            unit1 = m.group("u1")
            unit2 = m.group("u2")
            if not unit1 and not unit2:
                continue  # 例如“2026-10-17”这类日期，不是大小范围
            unit = (unit1 or unit2).lower()
            lo = float(m.group("a")) * UNIT_MAP[unit]
            hi = float(m.group("b")) * UNIT_MAP[unit2.lower() if unit2 else unit]
            if lo > hi:
                lo, hi = hi, lo
            min_b = lo if min_b is None else max(min_b, lo)
            max_b = hi if max_b is None else min(max_b, hi)
            self._consume(m)

        text = self._remaining()
        for m in re.finditer(size_re, text):
            n = float(m.group("a")) * UNIT_MAP[(m.group("u") or "b").lower()]
            op = m.group("op") or ""
            suffix = m.group("suffix") or ""
            approx = bool(m.group("approx")) or suffix in ("左右", "上下")
            bare = not op and not suffix and not m.group("approx")
            if op in ("大于", "超过", "不小于", "不低于", "至少") or suffix == "以上":
                min_b = n if min_b is None else max(min_b, n)
            elif op in ("小于", "不足", "不到", "不大于", "最多", "不超过") \
                    or suffix in ("以下", "以内"):
                max_b = n if max_b is None else min(max_b, n)
            elif approx or bare:
                approx_nums.append(n)
            self._consume(m)

        text = self._remaining()
        for m in re.finditer(big_re, text):
            min_b = 100 * 1024 ** 2 if min_b is None else max(min_b, 100 * 1024 ** 2)
            self._consume(m)
        for m in re.finditer(small_re, text):
            max_b = 5 * 1024 ** 2 if max_b is None else min(max_b, 5 * 1024 ** 2)
            self._consume(m)

        if approx_nums:
            for n in approx_nums:
                lo, hi = n * 2 / 3, n * 5 / 3
                min_b = lo if min_b is None else max(min_b, lo)
                max_b = hi if max_b is None else min(max_b, hi)

        if min_b is not None or max_b is not None:
            if min_b is not None and max_b is not None:
                label = f"大小：{ft.human_size(min_b)} 到 {ft.human_size(max_b)}"
                if approx_nums:
                    label += "（约数自动放宽）"
            elif min_b is not None:
                label = f"大小：大于 {ft.human_size(min_b)}"
            else:
                label = f"大小：小于 {ft.human_size(max_b)}"
            self.conditions.append({
                "kind": "size",
                "min": int(min_b) if min_b is not None else None,
                "max": int(max_b) if max_b is not None else None,
                "label": label,
            })

    # ---------- 类型 ----------

    _TYPE_PATTERNS: list[tuple[str, str]] = [
        (r"word\s*文档|word\s*文件|Word", "word"),
        (r"docx?|\.docx?|\.doc", "word"),
        (r"Excel|电子表格|表格|xlsx?|xls", "excel"),
        (r"PPT|幻灯片|演示文稿|pptx?", "ppt"),
        (r"PDF|pdf", "pdf"),
        (r"图片|照片|相片|图像|截图|壁纸", "image"),
        (r"视频|影片|录像|电影|短视频", "video"),
        (r"音频|音乐|歌曲|录音|铃声", "audio"),
        (r"压缩包|压缩文件|解压包|zip|rar|7z", "archive"),
        (r"安装包|安装程序|安装文件|msi|exe", "installer"),
        (r"文本文件|文本|txt|记事本", "text"),
        (r"代码|源码|源代码|脚本", "code"),
        (r"文档", "word"),
    ]

    def _pass_types(self) -> None:
        text = self._remaining()
        cats: list[str] = []
        for pattern, cat in self._TYPE_PATTERNS:
            # 用 finditer：同一类型词重复出现时（如“Excel 表格”）
            # 必须全部消费，否则残留词会变成多余的文件名关键词。
            for m in re.finditer(pattern, text, re.IGNORECASE):
                if cat not in cats:
                    cats.append(cat)
                self._consume(m)
        if cats:
            labels = [ft.LABELS.get(c, c) for c in cats]
            self.conditions.append({
                "kind": "type", "categories": cats, "exts": [],
                "label": "文件类型：" + "、".join(labels),
            })

    # ---------- 关键词 ----------

    def _pass_keywords(self) -> None:
        remaining = self._remaining()
        tokens = re.findall(
            r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff._\-+#]*", remaining
        )
        kws: list[str] = []
        unrecognized: list[str] = []

        def segment(tok: str) -> list[str]:
            """把中文长串按虚词词典切分，返回剩余有意义的片段。"""
            pieces = [tok]
            for word in sorted(STOPWORDS, key=len, reverse=True):
                if not any(pieces):
                    break
                new_pieces: list[str] = []
                for p in pieces:
                    parts = p.split(word)
                    new_pieces += [x for x in parts if x]
                pieces = new_pieces
            return pieces

        for t in tokens:
            t = t.strip("._-+#")
            if not t:
                continue
            if re.fullmatch(r"[A-Za-z0-9._\-+#]+", t):
                # 纯 ASCII 词（如 project-notes）整体作为关键词
                if t.lower() in {"mb", "kb", "gb", "m", "g"} or len(t) < 2:
                    continue
                if t not in kws:
                    kws.append(t)
                continue
            for piece in segment(t):
                if not piece or len(piece) < 2:
                    continue
                if piece in STOPWORDS:
                    continue
                if piece not in kws:
                    kws.append(piece)
        kws = kws[:5]
        if kws:
            self.conditions.append({
                "kind": "keywords", "values": kws,
                "label": "文件名关键词：" + "、".join(kws),
            })
        # 未识别的剩余内容（如实上报，供 UI 提示）
        # 已作为关键词一部分出现的片段（如带连字符的 project-notes 会被
        # 拆成 project / notes）不再重复报为“未理解”。
        kw_lower = [k.lower() for k in kws]
        leftover = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", remaining)
        for t in leftover:
            for piece in segment(t):
                if (piece and len(piece) >= 2 and piece not in STOPWORDS
                        and piece not in kws and piece not in unrecognized
                        and not any(piece.lower() in k for k in kw_lower)):
                    unrecognized.append(piece)
        self.unrecognized = unrecognized[:5]


def parse(text: str) -> ParseResult:
    text = (text or "").strip()
    text = (text.replace("，", ",").replace("。", " ").replace("；", " ")
               .replace("、", " "))
    p = _Parser(text)
    p._pass_locations()
    p._pass_time()
    p._pass_size()
    p._pass_types()
    p._pass_keywords()
    return ParseResult(text=text, conditions=p.conditions,
                       unrecognized=p.unrecognized)


def conditions_to_query(conditions: list[dict], limit: int = 500):
    """把解析出的条件转换为 SearchQuery（延迟导入避免循环依赖）。"""
    from . import knownfolders
    from . import query
    q = query.SearchQuery(limit=limit)
    for c in conditions:
        if c["kind"] == "type":
            q.categories = list(c["categories"])
            q.extensions = list(c["exts"])
        elif c["kind"] == "time":
            q.time_field = c.get("field", "mtime")
            q.time_start, q.time_end = c["start"], c["end"]
        elif c["kind"] == "size":
            q.size_min, q.size_max = c["min"], c["max"]
        elif c["kind"] == "location":
            special = c.get("special")
            if special in ("downloads", "desktop", "documents"):
                path = knownfolders.known_folder(special)
            else:
                path = c.get("path")
            if path:
                q.locations.append(path.rstrip("\\/") or path)
        elif c["kind"] == "keywords":
            q.keywords = list(c["values"])
    return q