"""可选“最近打开”导入：解析 Windows 最近使用项目的 .lnk 快捷方式。

- 该功能默认关闭，由用户在设置中主动开启；
- 仅读取快捷方式的目标路径与最近访问时间（标记为“约”）；
- 不读取文件内容，不读取浏览器密码 / Cookie 等任何敏感数据；
- 来源应用无法可靠获知，一律显示“未知”，不做推测。
"""
import json
import os
import subprocess
from datetime import datetime


def recent_folder() -> str | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    p = os.path.join(appdata, "Microsoft", "Windows", "Recent")
    return p if os.path.isdir(p) else None


_PS_SCRIPT = r"""
$dir = '{dir}'
$items = Get-ChildItem -LiteralPath $dir -Filter *.lnk -ErrorAction SilentlyContinue |
  ForEach-Object {{
    $target = $null
    try {{
      $s = (New-Object -ComObject WScript.Shell).CreateShortcut($_.FullName)
      $target = $s.TargetPath
    }} catch {{}}
    if ($target -and (Test-Path -LiteralPath $target -PathType Leaf)) {{
      [pscustomobject]@{{
        Target = $target
        LastWrite = $_.LastWriteTime.ToString('o')
      }}
    }}
  }}
if ($items) {{ ,$items | ConvertTo-Json -Compress }} else {{ '[]' }}
"""


def load_recent(limit: int = 500) -> tuple[list[dict], list[str]]:
    """读取最近使用项目。

    返回 (items, notes)；items 元素：{path, opened_at, source}，
    source 恒为 None（显示为“未知”）；notes 为说明/错误信息。
    """
    folder = recent_folder()
    if not folder:
        return [], ["未找到 Windows“最近使用文件”目录"]
    safe = folder.replace("'", "''")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             _PS_SCRIPT.format(dir=safe)],
            capture_output=True, text=True, timeout=60, creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], [f"读取失败：{e}"]
    if proc.returncode != 0:
        return [], ["解析“最近使用文件”快捷方式失败"]

    raw = (proc.stdout or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [], []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return [], []

    items: list[dict] = []
    seen: set[str] = set()
    for d in data:
        target = (d or {}).get("Target")
        if not target or not isinstance(target, str):
            continue
        key = os.path.normcase(os.path.normpath(target)).lower()
        if key in seen:
            continue
        seen.add(key)
        ts = None
        try:
            ts = int(datetime.fromisoformat(d["LastWrite"]).timestamp())
        except (KeyError, TypeError, ValueError):
            pass
        items.append({
            "path": target,
            "opened_at": ts,       # 约（基于快捷方式时间）
            "source": None,        # 未知，不做推测
        })
    items.sort(key=lambda x: x["opened_at"] or 0, reverse=True)
    return items[:limit], []