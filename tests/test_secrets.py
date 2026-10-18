"""DPAPI 加密存储测试。"""
import os

import pytest

from lostfile import secrets


@pytest.mark.skipif(os.name != "nt", reason="DPAPI 仅 Windows 支持")
def test_dpapi_roundtrip():
    plain = "sk-test-1234-中文密钥"
    enc = secrets.encrypt_str(plain)
    assert enc and enc != plain
    assert secrets.decrypt_str(enc) == plain


def test_empty_and_invalid():
    assert secrets.encrypt_str("") == ""
    assert secrets.decrypt_str("") == ""
    assert secrets.decrypt_str("not-base64!!!") is None