import os
import tempfile

from lingmengwork import notes as N


def test_upsert_new_and_dedup_by_title():
    with tempfile.TemporaryDirectory() as t:
        rec, is_new = N.upsert(t, "架构决策", "# 背景\n...")
        assert is_new is True
        assert rec["id"] and rec["title"] == "架构决策"
        rec2, is_new2 = N.upsert(t, "架构决策", "更新内容")
        assert is_new2 is False
        assert rec2["id"] == rec["id"]
        assert N.list_notes(t)["notes"][0]["content"] == "更新内容"


def test_upsert_by_id_updates():
    with tempfile.TemporaryDirectory() as t:
        rec, _ = N.upsert(t, "A", "x")
        rec2, is_new = N.upsert(t, "B", "y", tid=rec["id"])
        assert is_new is False
        assert rec2["title"] == "B"
        assert len(N.list_notes(t)["notes"]) == 1


def test_empty_title_rejected():
    with tempfile.TemporaryDirectory() as t:
        try:
            N.upsert(t, "   ", "x")
            assert False, "应拒绝空标题"
        except ValueError:
            pass


def test_get_and_delete():
    with tempfile.TemporaryDirectory() as t:
        rec, _ = N.upsert(t, "A", "x")
        assert N.get_note(rec["id"], t) is not None
        removed = N.delete(rec["id"], t)
        assert removed == 1
        assert N.get_note(rec["id"], t) is None


def test_sorted_by_updated_desc(monkeypatch):
    times = iter(["2026-01-01 00:00:00", "2026-01-02 00:00:00", "2026-01-03 00:00:00",
                  "2026-01-04 00:00:00", "2026-01-05 00:00:00"])
    monkeypatch.setattr(N, "_now", lambda: next(times))
    with tempfile.TemporaryDirectory() as t:
        a, _ = N.upsert(t, "old", "1")
        b, _ = N.upsert(t, "new", "2")
        # 触发更新让 new 的 updated_at 更晚 -> 排序应居首
        N.upsert(t, "new", "2b", tid=b["id"])
        titles = [n["title"] for n in N.list_notes(t)["notes"]]
        assert titles == ["new", "old"]


def test_missing_file_returns_empty():
    with tempfile.TemporaryDirectory() as t:
        assert N.load(t) == {"notes": []}
