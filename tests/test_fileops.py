"""文件操作测试：只读操作不改变文件；“删除到回收站”只移入回收站。

回收站类测试在验证后会调用 purge_from_recycle_bin，把**测试自己生成**的
条目从回收站永久清掉（按原始路径精确匹配，不碰用户数据）。
"""
import os

import pytest

from conftest import make_file, purge_from_recycle_bin
from lostfile import fileops


def test_exists_file(tmp_path):
    p = str(make_file(tmp_path / "a.txt", 10))
    assert fileops.exists_file(p) is True
    assert fileops.exists_file(str(tmp_path / "nope.txt")) is False


def test_delete_missing_file_returns_false(tmp_path):
    assert fileops.delete_to_recycle_bin(str(tmp_path / "nope.txt")) is False


def test_delete_to_recycle_bin_moves_file(tmp_path):
    if os.name != "nt":
        pytest.skip("回收站仅 Windows 支持")
    p = str(make_file(tmp_path / "to_delete.txt", 10))
    assert fileops.delete_to_recycle_bin(p) is True
    assert not os.path.exists(p)
    assert fileops.exists_file(p) is False
    purge_from_recycle_bin([p])          # 验证后自清理，不留回收站垃圾
    assert fileops.exists_file(p) is False

    # 顺带验证：路径不存在时返回 False，不抛异常
    assert fileops.delete_to_recycle_bin(p) is False


def test_delete_accepts_long_path_prefix(tmp_path):
    """带 \\\\?\\ 前缀的路径也要能删（内部需还原成普通绝对路径）。"""
    if os.name != "nt":
        pytest.skip("回收站仅 Windows 支持")
    p = str(make_file(tmp_path / "prefixed.txt", 10))
    assert fileops.delete_to_recycle_bin(fileops._long(p)) is True
    assert not os.path.exists(p)
    purge_from_recycle_bin([p])

    # 长前缀 + 不存在的文件 → False
    assert fileops.delete_to_recycle_bin(fileops._long(p)) is False


def test_reveal_file_passes_single_quoted_command(tmp_path, monkeypatch):
    """回归：定位文件必须把整条命令作为单字符串传给 explorer。

    历史 bug：用列表形式传参时，list2cmdline 会把参数改写成
    ``/select,\\"路径\\"``，explorer 解析失败后打开自己的默认文件夹
    （用户反馈“右击打开所在文件夹固定跳转到文档目录”）。
    """
    if os.name != "nt":
        pytest.skip("explorer 仅 Windows 支持")
    p = str(make_file(tmp_path / "季度报告 final.pdf", 10))
    captured: list = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured.append(args)

    monkeypatch.setattr(fileops.subprocess, "Popen", _FakePopen)
    assert fileops.reveal_file(p) is True
    assert len(captured) == 1
    cmd = captured[0]
    assert isinstance(cmd, str), "必须是单字符串形式，否则引号会被转义"
    assert cmd == f'explorer /select,"{p}"'
    assert '\\"' not in cmd, "命令行里不应出现被转义的引号"
    assert "/select," in cmd and cmd.endswith('"')

    # 文件不存在 → False，且不发起任何外部调用
    captured.clear()
    assert fileops.reveal_file(str(tmp_path / "nope.pdf")) is False
    assert captured == []


def test_open_parent_folder_on_missing_file(tmp_path):
    """只打开所在文件夹：目录不存在时返回 False，不抛异常。"""
    assert fileops.open_parent_folder(str(tmp_path / "nope" / "a.txt")) is False
