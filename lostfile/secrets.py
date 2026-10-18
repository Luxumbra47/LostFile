"""Windows DPAPI 加密存储（API Key 等敏感配置）。

DPAPI 只可被当前 Windows 帐户解密：换用户或重装系统后解不开——
调用方（UI）需如实提示“请重新填写”。
非 Windows 平台直接原文存放（本项目目标平台为 Windows）。
"""
import base64
import sys
from ctypes import Structure, byref, c_char, cast, create_string_buffer, c_void_p
from ctypes import wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DATA_BLOB(Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", c_void_p),
    ]


def _blob(data: bytes) -> _DATA_BLOB:
    buf = create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), cast(buf, c_void_p))


def _blob_bytes(blob: _DATA_BLOB) -> bytes:
    if not blob.pbData or not blob.cbData:
        return b""
    return bytes((c_char * blob.cbData).from_address(blob.pbData))


def _local_free(addr: int | None) -> None:
    if addr:
        try:
            from ctypes import windll
            windll.kernel32.LocalFree(addr)
        except Exception:
            pass


def _crypt32():
    from ctypes import windll
    return windll.crypt32


def encrypt_str(plain: str) -> str | None:
    """加密为 base64 文本；空串原样返回 ""；失败返回 None。"""
    if sys.platform != "win32":
        return plain
    if not plain:
        return ""
    data_in = _blob(plain.encode("utf-16-le"))
    data_out = _DATA_BLOB()
    try:
        ok = _crypt32().CryptProtectData(
            byref(data_in), "LostFile", None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, byref(data_out),
        )
    except Exception:
        return None
    if not ok:
        return None
    try:
        return base64.b64encode(_blob_bytes(data_out)).decode("ascii")
    finally:
        _local_free(data_out.pbData)


def decrypt_str(enc: str | None) -> str | None:
    """解密 base64 密文；空串返回 ""；失败（换用户等）返回 None。"""
    if sys.platform != "win32":
        return enc
    if not enc:
        return ""
    try:
        raw = base64.b64decode(enc)
    except Exception:
        return None
    data_in = _blob(raw)
    data_out = _DATA_BLOB()
    try:
        ok = _crypt32().CryptUnprotectData(
            byref(data_in), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, byref(data_out),
        )
    except Exception:
        return None
    if not ok:
        return None
    try:
        return _blob_bytes(data_out).decode("utf-16-le")
    finally:
        _local_free(data_out.pbData)