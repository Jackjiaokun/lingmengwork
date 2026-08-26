import os
import tempfile

from lingmengwork import secrets as S


def test_set_get_roundtrip():
    with tempfile.TemporaryDirectory() as t:
        S.set_secret("OPENAI_API_KEY", "sk-secret-123", "测试", t)
        assert S.get_secret("OPENAI_API_KEY", t) == "sk-secret-123"
        # 列表不返回明文
        items = S.list_secrets(t)["secrets"]
        assert items[0]["key"] == "OPENAI_API_KEY"
        assert items[0]["has_value"] is True
        assert "sk-secret" not in str(items)


def test_update_existing():
    with tempfile.TemporaryDirectory() as t:
        S.set_secret("K", "v1", "", t)
        is_new = S.set_secret("K", "v2", "备注", t)
        assert is_new is False
        assert S.get_secret("K", t) == "v2"
        assert len(S.list_secrets(t)["secrets"]) == 1


def test_missing_key_returns_none():
    with tempfile.TemporaryDirectory() as t:
        assert S.get_secret("NOPE", t) is None


def test_empty_key_rejected():
    with tempfile.TemporaryDirectory() as t:
        try:
            S.set_secret("  ", "x", "", t)
            assert False, "应拒绝空名"
        except ValueError:
            pass


def test_delete():
    with tempfile.TemporaryDirectory() as t:
        S.set_secret("K", "v", "", t)
        assert S.delete_secret("K", t) == 1
        assert S.get_secret("K", t) is None


def test_cipher_changes_with_random_salt():
    with tempfile.TemporaryDirectory() as t:
        S.set_secret("A", "same-value", "", t)
        S.set_secret("B", "same-value", "", t)
        # 两次加密结果应不同(随机盐), 但解密一致
        d = S.load(t)
        c1 = d["secrets"][0]["value_cipher"]
        c2 = d["secrets"][1]["value_cipher"]
        assert c1 != c2
        assert S.get_secret("A", t) == S.get_secret("B", t) == "same-value"


def test_missing_file_returns_empty():
    with tempfile.TemporaryDirectory() as t:
        assert S.load(t) == {"secrets": []}
