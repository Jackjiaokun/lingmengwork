import os
import tempfile

from lingmengwork import todos as T


def test_add_and_counts():
    with tempfile.TemporaryDirectory() as t:
        rec = T.add(t, "实现登录", "high", "2026-09-01", "备注")
        assert rec["status"] == "todo"
        assert rec["priority"] == "high" and rec["due"] == "2026-09-01"
        d = T.list_todos(t)
        assert d["counts"]["todo"] == 1
        assert d["counts"]["doing"] == 0 and d["counts"]["done"] == 0


def test_set_status_cycle():
    with tempfile.TemporaryDirectory() as t:
        rec = T.add(t, "A")
        rec = T.set_status(rec["id"], "doing", t)
        assert rec["status"] == "doing"
        rec = T.set_status(rec["id"], "done", t)
        assert rec["status"] == "done"
        assert T.list_todos(t)["counts"]["done"] == 1


def test_set_status_invalid_raises():
    with tempfile.TemporaryDirectory() as t:
        rec = T.add(t, "A")
        try:
            T.set_status(rec["id"], "bogus", t)
            assert False, "应拒绝非法 status"
        except ValueError:
            pass


def test_set_status_missing_returns_none():
    with tempfile.TemporaryDirectory() as t:
        assert T.set_status("nope", "done", t) is None


def test_priority_sorting():
    with tempfile.TemporaryDirectory() as t:
        T.add(t, "low", "low")
        T.add(t, "high", "high")
        T.add(t, "mid", "mid")
        titles = [x["title"] for x in T.list_todos(t)["todos"]]
        assert titles == ["high", "mid", "low"]


def test_delete():
    with tempfile.TemporaryDirectory() as t:
        rec = T.add(t, "A")
        assert T.delete(rec["id"], t) == 1
        assert T.list_todos(t)["todos"] == []


def test_filter_by_status():
    with tempfile.TemporaryDirectory() as t:
        a = T.add(t, "A")
        T.add(t, "B")
        T.set_status(a["id"], "done", t)
        assert len(T.list_todos(t, status="done")["todos"]) == 1
        assert len(T.list_todos(t, status="todo")["todos"]) == 1


def test_missing_file_returns_empty():
    with tempfile.TemporaryDirectory() as t:
        assert T.load(t) == {"todos": []}
