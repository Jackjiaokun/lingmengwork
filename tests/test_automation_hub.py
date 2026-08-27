"""Phase 15 自动化调度中枢测试。

纯标准库、无 LLM key: 覆盖 调度表达式解析 / 时间计算 / 任务 CRUD /
规则兜底执行四引擎 / 调度推进(tick) / 持久化 / server API。
"""
import io
import os
import sys
import json

import pytest

pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)

from lingmengwork import automation_hub as ah  # noqa: E402
from lingmengwork.web import server  # noqa: E402


# ---------------- 调度表达式解析 ----------------
def test_parse_interval_ok():
    s = ah.parse_schedule("interval:60")
    assert s["typ"] == "interval" and s["seconds"] == 60


def test_parse_interval_too_small():
    with pytest.raises(ValueError):
        ah.parse_schedule("interval:10")


def test_parse_daily_ok():
    s = ah.parse_schedule("daily:09:30")
    assert s["typ"] == "daily" and s["hour"] == 9 and s["minute"] == 30


def test_parse_daily_bad():
    with pytest.raises(ValueError):
        ah.parse_schedule("daily:25:00")


def test_parse_cron_ok():
    s = ah.parse_schedule("cron:*/5 * * * *")
    assert s["typ"] == "cron" and len(s["fields"]) == 5


def test_parse_cron_bad_fields():
    with pytest.raises(ValueError):
        ah.parse_schedule("cron:* * *")


def test_parse_unknown():
    with pytest.raises(ValueError):
        ah.parse_schedule("weekly:1")


# ---------------- 时间计算 ----------------
def test_next_interval():
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 1, 12, 0, 0)
    sp = ah.parse_schedule("interval:120")
    t = ah.compute_next_run({"schedule": "interval:120", "schedule_spec": sp}, base=base)
    assert t == base + timedelta(seconds=120)


def test_next_daily_tomorrow():
    from datetime import datetime
    base = datetime(2026, 1, 1, 23, 0, 0)
    sp = ah.parse_schedule("daily:09:00")
    t = ah.compute_next_run({"schedule": "daily:09:00", "schedule_spec": sp}, base=base)
    assert t.day == 2 and t.hour == 9


def test_next_daily_today():
    from datetime import datetime
    base = datetime(2026, 1, 1, 8, 0, 0)
    sp = ah.parse_schedule("daily:09:00")
    t = ah.compute_next_run({"schedule": "daily:09:00", "schedule_spec": sp}, base=base)
    assert t.day == 1 and t.hour == 9


def test_next_cron_every5min():
    from datetime import datetime
    base = datetime(2026, 1, 1, 0, 1, 0)
    sp = ah.parse_schedule("cron:*/5 * * * *")
    t = ah.compute_next_run({"schedule": "cron:*/5 * * * *", "schedule_spec": sp}, base=base)
    assert t.minute % 5 == 0 and t >= base


# ---------------- 任务 CRUD ----------------
def test_hub_add_list_get(tmp_path):
    hub = ah.AutomationHub(tmp_path)
    t = hub.add(name="t1", kind="decompose", goal="g", schedule="interval:300")
    assert t["id"].startswith("auto_")
    assert hub.get(t["id"])["name"] == "t1"
    assert len(hub.list_tasks()) == 1


def test_hub_add_bad_kind(tmp_path):
    hub = ah.AutomationHub(tmp_path)
    with pytest.raises(ValueError):
        hub.add(name="x", kind="nope", goal="g", schedule="interval:300")


def test_hub_remove(tmp_path):
    hub = ah.AutomationHub(tmp_path)
    t = hub.add(name="t", kind="decompose", goal="g", schedule="interval:300")
    assert hub.remove(t["id"]) is True
    assert hub.get(t["id"]) is None


def test_hub_set_enabled(tmp_path):
    hub = ah.AutomationHub(tmp_path)
    t = hub.add(name="t", kind="decompose", goal="g", schedule="interval:300")
    assert hub.set_enabled(t["id"], False)["enabled"] is False
    assert hub.set_enabled(t["id"], True)["enabled"] is True


# ---------------- 规则兜底执行四引擎 ----------------
def test_run_now_kinds(tmp_path):
    hub = ah.AutomationHub(tmp_path)
    for kind, extra in [("decompose", {}), ("autonomous", {}),
                        ("pipeline", {}), ("creation", {"domain": "code"})]:
        t = hub.add(name="t_" + kind, kind=kind, goal="写一个 hello 函数",
                    schedule="interval:99999", **extra)
        out = hub.run_now(t["id"])
        assert out["ok"] is True, out
        assert out["result"]["ok"] is True, out
        assert out["task"]["run_count"] == 1
        assert out["task"]["last_run"]
        assert out["task"]["last_status"] == "ok"


# ---------------- 调度推进 ----------------
def test_tick_triggers(tmp_path):
    from datetime import datetime, timedelta
    hub = ah.AutomationHub(tmp_path)
    hub.add(name="t", kind="decompose", goal="g", schedule="interval:300")
    past = (datetime.now() - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
    hub.tasks[0]["next_run"] = past
    triggered = hub.tick()
    assert hub.tasks[0]["id"] in triggered
    assert hub.get(hub.tasks[0]["id"])["run_count"] == 1


# ---------------- 持久化 ----------------
def test_persistence(tmp_path):
    hub = ah.AutomationHub(tmp_path)
    hub.add(name="p", kind="decompose", goal="g", schedule="interval:300")
    hub2 = ah.AutomationHub(tmp_path)
    assert len(hub2.list_tasks()) == 1
    assert hub2.list_tasks()[0]["name"] == "p"


# ---------------- server API ----------------
class _StubHandler(server.Handler):
    def __init__(self, body):
        raw = json.dumps(body).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self._captured = {}

    def _send_json(self, obj, status=200):
        self._captured = {"obj": obj, "status": status}


@pytest.fixture
def stub():
    return _StubHandler


def test_api_create_and_run(stub, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = stub({"name": "日报", "kind": "decompose", "goal": "做每日总结", "schedule": "interval:99999"})
    h._automations_create()
    out = h._captured["obj"]
    assert out["ok"] is True and out["task"]["id"]
    tid = out["task"]["id"]

    h2 = stub({})
    h2._automations_run(tid)
    assert h2._captured["obj"]["ok"] is True
    assert h2._captured["obj"]["task"]["run_count"] == 1


def test_api_get_structure(stub, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = stub({})
    h._automations_get()
    out = h._captured["obj"]
    assert out["ok"] is True
    assert "tasks" in out and "scheduler" in out


def test_api_delete(stub, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = stub({"name": "x", "kind": "decompose", "goal": "g", "schedule": "interval:99999"})
    h._automations_create()
    tid = h._captured["obj"]["task"]["id"]
    h2 = stub({})
    h2._automations_delete(tid)
    assert h2._captured["obj"]["ok"] is True
    h3 = stub({})
    h3._automations_delete(tid)
    assert h3._captured["status"] == 404


def test_api_toggle(stub, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = stub({"name": "x", "kind": "decompose", "goal": "g", "schedule": "interval:99999"})
    h._automations_create()
    tid = h._captured["obj"]["task"]["id"]
    h2 = stub({})
    h2._automations_toggle(tid)  # 无 body -> 翻转(默认启用->停用)
    assert h2._captured["obj"]["task"]["enabled"] is False


def test_api_create_missing_field(stub, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = stub({"name": "x", "kind": "decompose"})  # 缺 goal/schedule
    h._automations_create()
    assert h._captured["status"] == 400


def test_api_create_bad_schedule(stub, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = stub({"name": "x", "kind": "decompose", "goal": "g", "schedule": "weekly:1"})
    h._automations_create()
    assert h._captured["status"] == 400
