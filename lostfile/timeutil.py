"""本地时间工具：相对时间范围的换算与格式化。全部基于本地时区。"""
import time
from datetime import datetime, timedelta


def _day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _ts(dt: datetime) -> int:
    return int(time.mktime(dt.timetuple()))


def now() -> datetime:
    return datetime.now()


def today_range() -> tuple[int, int]:
    t = now()
    return _ts(_day_start(t)), _ts(t)


def yesterday_range() -> tuple[int, int]:
    t = now()
    end = _day_start(t)
    return _ts(end - timedelta(days=1)), _ts(end)


def last_days_range(n: int) -> tuple[int, int]:
    """包含今天在内的最近 n 天。"""
    t = now()
    return _ts(_day_start(t) - timedelta(days=n - 1)), _ts(t)


def this_week_range() -> tuple[int, int]:
    t = now()
    start = _day_start(t) - timedelta(days=t.weekday())
    return _ts(start), _ts(t)


def last_week_range() -> tuple[int, int]:
    t = now()
    this_monday = _day_start(t) - timedelta(days=t.weekday())
    start = this_monday - timedelta(days=7)
    return _ts(start), _ts(this_monday)


def this_month_range() -> tuple[int, int]:
    t = now()
    return _ts(_day_start(t.replace(day=1))), _ts(t)


def last_month_range() -> tuple[int, int]:
    t = now()
    first_this = _day_start(t.replace(day=1))
    first_last = first_this - timedelta(days=1)
    first_last = first_last.replace(day=1)
    return _ts(first_last), _ts(first_this)


def month_day_range(month: int, day: int) -> tuple[int, int]:
    """N 月 N 日（缺省今年；若晚于现在则视为去年）。"""
    t = now()
    start = None
    for year in (t.year, t.year - 1):
        try:
            candidate = _day_start(datetime(year, month, day))
        except ValueError:
            continue
        if candidate <= t:
            start = candidate
            break
    if start is None:
        start = _day_start(datetime(t.year, month, day))
    return _ts(start), _ts(start + timedelta(days=1))


def year_range(year: int) -> tuple[int, int]:
    return _ts(datetime(year, 1, 1)), _ts(datetime(year + 1, 1, 1))


def fmt_ts(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "未知"


def fmt_date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "未知"


def fmt_range(start: int, end: int) -> str:
    return f"{fmt_ts(start)} – {fmt_ts(end)}"