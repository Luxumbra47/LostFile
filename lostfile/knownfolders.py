"""通过 SHGetKnownFolderPath 解析 Windows 已知系统目录（下载/桌面/文档）。
解析失败时回退到 expanduser 近似值；不写注册表、不改系统设置。
"""
import ctypes
import os
import uuid
from ctypes import wintypes

_FOLDERIDS = {
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-1168-4DFB-BF2C-ACDFD2460B5F}",
}

_FALLBACKS = {
    "downloads": "~/Downloads",
    "desktop": "~/Desktop",
    "documents": "~/Documents",
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


def _make_guid(text: str) -> "_GUID":
    b = uuid.UUID(text).bytes_le
    g = _GUID()
    g.Data1 = int.from_bytes(b[0:4], "little")
    g.Data2 = int.from_bytes(b[4:6], "little")
    g.Data3 = int.from_bytes(b[6:8], "little")
    ctypes.memmove(g.Data4, b[8:16], 8)
    return g


def known_folder(name: str) -> str | None:
    """返回已知目录的绝对路径（可能不存在于磁盘），失败返回 None。"""
    if os.name != "nt":
        return os.path.expanduser(_FALLBACKS.get(name, "~"))
    try:
        p = wintypes.PWSTR()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(_make_guid(_FOLDERIDS[name])), 0, None, ctypes.byref(p)
        )
        if hr != 0:
            return None
        try:
            return p.value
        finally:
            ctypes.windll.ole32.CoTaskMemFree(p)
    except Exception:
        return os.path.expanduser(_FALLBACKS.get(name, "~"))


def known_folders() -> dict[str, str | None]:
    return {name: known_folder(name) for name in _FOLDERIDS}


# 各盘上常见的“个人文件夹”名（中英）——用户常把文件放在其他盘的这些
# 目录里（例如 D:\桌面、D:\视频），系统“已知文件夹”只指向 C 盘那个。
_COMMON_DIR_NAMES = (
    "桌面", "下载", "文档", "视频", "图片", "音乐",
    "Desktop", "Downloads", "Documents", "Videos", "Pictures", "Music",
)


def suggested_dirs() -> list[dict]:
    """返回建议索引的目录清单：[{label, path}]（只列真实存在的目录）。

    包含：系统已知文件夹（下载/桌面/文档）+ 各固定磁盘上探测到的常见
    个人文件夹。用于向导与设置里的“一键添加常用目录”，避免用户去扫整盘。
    """
    out: list[dict] = []
    seen: set[str] = set()

    def add(label: str, path: str | None) -> None:
        if not path:
            return
        try:
            p = os.path.abspath(path)
        except (OSError, ValueError):
            return
        key = os.path.normcase(p)
        if key in seen or not os.path.isdir(p):
            return
        seen.add(key)
        out.append({"label": label, "path": p})

    for special, label in (("downloads", "下载目录"), ("desktop", "桌面"),
                           ("documents", "文档目录")):
        add(label, known_folder(special))

    try:
        from . import drives
        for drive in drives.list_fixed_drives():
            tag = drive[0:2]
            for name in _COMMON_DIR_NAMES:
                add(f"{name}（{tag}）", os.path.join(drive, name))
    except Exception:
        pass
    return out