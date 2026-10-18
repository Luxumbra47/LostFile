"""枚举本机固定磁盘（供“整个磁盘”扫描选项使用）。"""
import ctypes
import os
from ctypes import wintypes

DRIVE_FIXED = 3


def list_fixed_drives() -> list[str]:
    """返回固定磁盘根路径列表，如 ['C:\\', 'D:\\']。失败返回空列表。"""
    if os.name != "nt":
        return []
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetLogicalDrives.restype = wintypes.DWORD
        kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetDriveTypeW.restype = ctypes.c_uint
        mask = kernel32.GetLogicalDrives()
    except Exception:
        return []
    out: list[str] = []
    for i in range(26):
        if not (mask & (1 << i)):
            continue
        root = chr(65 + i) + ":\\"
        try:
            if kernel32.GetDriveTypeW(root) == DRIVE_FIXED:
                out.append(root)
        except Exception:
            continue
    return out