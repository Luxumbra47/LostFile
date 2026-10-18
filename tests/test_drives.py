"""固定磁盘枚举测试。"""
import os
import re

from lostfile import drives


def test_list_fixed_drives_format():
    out = drives.list_fixed_drives()
    assert isinstance(out, list)
    for d in out:
        assert re.fullmatch(r"[A-Z]:\\", d), d
    # 本机存在 D 盘时，应出现在列表中
    if os.path.exists("D:\\"):
        assert "D:\\" in out
    if os.path.exists("C:\\"):
        assert "C:\\" in out


def test_no_duplicates():
    out = drives.list_fixed_drives()
    assert len(out) == len(set(out))