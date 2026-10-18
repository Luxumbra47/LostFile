"""可选联网：GitHub 仓库搜索（默认关闭，仅发送用户输入的关键词）。

隐私约定：只把用户输入的关键词文本发送给 GitHub 公开搜索接口
（api.github.com）；本地文件名、路径与索引内容一律不上传。
无网络或接口出错时静默返回空列表，本地搜索不受影响。
"""
import json
import urllib.parse
import urllib.request


def search_repos(keyword: str, per_page: int = 5,
                 timeout: float = 8.0) -> list[dict]:
    """按关键词搜索 GitHub 仓库；失败时静默返回空列表。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    url = (
        "https://api.github.com/search/repositories?q="
        + urllib.parse.quote_plus(kw)
        + f"&per_page={int(per_page)}"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "LostFile/1.0",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return []
    out: list[dict] = []
    for item in (data.get("items") or []):
        out.append({
            "full_name": str(item.get("full_name") or ""),
            "html_url": str(item.get("html_url") or ""),
            "description": str(item.get("description") or "").strip(),
            "stars": int(item.get("stargazers_count") or 0),
        })
    return out