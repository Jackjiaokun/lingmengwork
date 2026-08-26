import os
import tempfile

from lingmengwork import snippets as S


def test_upsert_new_and_dedup_by_title():
    with tempfile.TemporaryDirectory() as t:
        rec, is_new = S.upsert(t, "快排", "def q(): pass", "python", ["算法"])
        assert is_new is True
        assert rec["id"] and rec["title"] == "快排"
        assert rec["language"] == "python" and rec["tags"] == ["算法"]
        rec2, is_new2 = S.upsert(t, "快排", "def q2(): pass", "python")
        assert is_new2 is False
        assert rec2["id"] == rec["id"]
        assert S.list_snippets(t)["snippets"][0]["content"] == "def q2(): pass"


def test_upsert_by_id_updates():
    with tempfile.TemporaryDirectory() as t:
        rec, _ = S.upsert(t, "A", "x", "go")
        rec2, is_new = S.upsert(t, "B", "y", "rust", tid=rec["id"])
        assert is_new is False
        assert rec2["title"] == "B"
        assert len(S.list_snippets(t)["snippets"]) == 1


def test_empty_title_rejected():
    with tempfile.TemporaryDirectory() as t:
        try:
            S.upsert(t, "   ", "x")
            assert False, "应拒绝空标题"
        except ValueError:
            pass


def test_tags_normalization():
    with tempfile.TemporaryDirectory() as t:
        S.upsert(t, "A", "x", "python", "算法, 排序")
        d = S.list_snippets(t, tag="排序")
        assert len(d["snippets"]) == 1
        # 逗号+空格分割
        S.upsert(t, "B", "y", "python", ["c", "d"])
        assert S.list_snippets(t, tag="c")["snippets"][0]["title"] == "B"


def test_get_and_delete():
    with tempfile.TemporaryDirectory() as t:
        rec, _ = S.upsert(t, "A", "x", "python")
        assert S.get_snippet(rec["id"], t) is not None
        removed = S.delete(rec["id"], t)
        assert removed == 1
        assert S.get_snippet(rec["id"], t) is None


def test_list_filter_by_language():
    with tempfile.TemporaryDirectory() as t:
        S.upsert(t, "a", "1", "python")
        S.upsert(t, "b", "2", "go")
        assert len(S.list_snippets(t, language="go")["snippets"]) == 1
        assert "python" in S.list_snippets(t)["languages"]


def test_missing_file_returns_empty():
    with tempfile.TemporaryDirectory() as t:
        d = S.load(t)
        assert d == {"snippets": []}
