"""对结果文件执行操作：打开、定位、复制准备、属性面板、删除到回收站。

除「删除到回收站」外，其余操作均为只读，不修改/移动原始文件；
删除到回收站需界面层先获得用户确认，且只移入回收站（可还原），
不做永久删除。
"""
import ctypes
import os
import subprocess
from ctypes import wintypes


FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_WANTNUKEWARNING = 0x4000


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _long(p: str) -> str:
    if p.startswith("\\\\?\\"):
        return p
    return "\\\\?\\" + os.path.abspath(p)


def exists_file(path: str) -> bool:
    try:
        return os.path.isfile(_long(path))
    except Exception:
        return False


def open_file(path: str) -> bool:
    if not exists_file(path):
        return False
    try:
        os.startfile(path)  # noqa: S606 仅打开用户选中的本地文件
        return True
    except OSError:
        return False


def reveal_file(path: str) -> bool:
    """在资源管理器中打开所在文件夹并选中文件。

    注意：explorer 的 /select, 只认“命令行中带引号”的写法，必须把整条命令
    作为**单一字符串**交给 CreateProcess（不经 shell）。若用列表形式传参，
    Python 的 list2cmdline 会把它改写成 `/select,\\"路径\\"`，explorer 解析
    失败后会退回打开自己的默认文件夹——这正是“固定跳转到文档目录”的原因。
    Windows 文件名不含双引号，因此这里的引号拼接是安全的。
    """
    if not exists_file(path):
        return False
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.Popen(f'explorer /select,"{path}"', creationflags=flags)
        return True
    except OSError:
        return False


def show_properties(path: str, parent_wnd: int | None = None) -> bool:
    """弹出文件属性对话框（SHObjectProperties，类型 2 = 文件）。"""
    if not exists_file(path):
        return False
    try:
        shell32 = ctypes.windll.shell32
        shell32.SHObjectProperties.argtypes = [
            wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ]
        shell32.SHObjectProperties.restype = wintypes.BOOL
        hwnd = parent_wnd or 0
        shell32.SHObjectProperties(
            wintypes.HWND(hwnd), wintypes.DWORD(2), ctypes.c_wchar_p(path), None
        )
        return True
    except Exception:
        return False


def open_parent_folder(path: str) -> bool:
    """只打开所在文件夹（不选中）。文件不存在时同样可用。"""
    folder = os.path.dirname(path)
    if folder and os.path.isdir(folder):
        try:
            os.startfile(folder)  # noqa: S606
            return True
        except OSError:
            return False
    return False


def open_recycle_bin() -> bool:
    """打开 Windows 回收站（供用户查看/还原刚删除的文件）。"""
    if os.name != "nt":
        return False
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.Popen(["explorer", "shell:RecycleBinFolder"],
                         creationflags=flags)
        return True
    except OSError:
        return False


def delete_to_recycle_bin(path: str) -> bool:
    """把文件移入 Windows 回收站（可还原）；失败或不适用时返回 False。

    - 使用 SHFileOperationW + FOF_ALLOWUNDO：进入回收站而非永久删除；
    - 加 FOF_NOCONFIRMATION/FOF_SILENT：不弹系统确认框（确认由界面负责）；
    - 加 FOF_WANTNUKEWARNING：万一系统无法回收、只能永久删除时仍会警告
      （该标志会覆盖 NOCONFIRMATION），避免误删。
    """
    if os.name != "nt":
        return False
    if not exists_file(path):
        return False
    # SHFileOperationW 不支持 \\?\ 前缀，需还原成普通绝对路径
    target = path[4:] if path.startswith("\\\\?\\") else path
    target = os.path.abspath(target)
    try:
        # pFrom 必须以“双 \0”结尾
        buf = ctypes.create_unicode_buffer(len(target) + 2)
        buf[:len(target)] = target
        op = _SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
        op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
                     | FOF_WANTNUKEWARNING)
        shell32 = ctypes.windll.shell32
        shell32.SHFileOperationW.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
        shell32.SHFileOperationW.restype = ctypes.c_int
        rc = shell32.SHFileOperationW(ctypes.byref(op))
        return rc == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False