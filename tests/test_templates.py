import os
import tempfile

from lingmengwork import templates as T


def test_upsert_new_and_dedup_by_name():
    with tempfile.TemporaryDirectory() as t:
        rec, is_new = T.upsert(t, "代码审查", "请审查以下代码", "代码")
        assert is_new is True
        assert rec["id"] and rec["name"] == "代码审查"
        # 同名再次 upsert -> 更新而非新建
        rec2, is_new2 = T.upsert(t, "代码审查", "更严格的审查", "代码")
        assert is_new2 is False
        assert rec2["id"] == rec["id"]
        d = T.list_templates(t)
        assert len(d["templates"]) == 1
        assert d["templates"][0]["content"] == "更严格的审查"


def test_upsert_by_id_updates():
    with tempfile.TemporaryDirectory() as t:
        rec, _ = T.upsert(t, "A", "x", "通用")
        rec2, is_new = T.upsert(t, "B", "y", "通用", tid=rec["id"])
        assert is_new is False
        assert rec2["name"] == "B"
        assert len(T.list_templates(t)["templates"]) == 1


def test_empty_name_rejected():
    with tempfile.TemporaryDirectory() as t:
        try:
            T.upsert(t, "   ", "x")
            assert False, "应拒绝空名"
        except ValueError:
            pass


def test_get_and_delete():
    with tempfile.TemporaryDirectory() as t:
        rec, _ = T.upsert(t, "A", "x", "通用")
        assert T.get_template(rec["id"], t)["content"] == "x"
        removed = T.delete(rec["id"], t)
        assert removed == 1
        assert T.get_template(rec["id"], t) is None


def test_missing_file_returns_empty():
    with tempfile.TemporaryDirectory() as t:
        d = T.load(t)
        assert d == {"templates": []}


def test_categories_sorted():
    with tempfile.TemporaryDirectory() as t:
        T.upsert(t, "z", "1", "写作")
        T.upsert(t, "a", "2", "代码")
        cats = T.list_templates(t)["categories"]
        assert cats.index("代码") < cats.index("写作")
