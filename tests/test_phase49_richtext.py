"""Phase 49 · Webhook 富文本消息 + 报告链接直出测试.

覆盖:
- set_public_base_url / _report_url_for 链接生成
- notify payload 携带 report_url(需 result.ts — _record 回写)
- 飞书卡片: interactive + header 模板色 + markdown 内容含报告链接 + action 按钮
- 钉钉: markdown 消息 + actionCard singleURL
- raw payload 内嵌 report_url
"""

import json

import pytest

from lingmengwork import superagent as sa_mod


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(sa_mod, "_HOOKS", {})
    monkeypatch.setattr(sa_mod, "_HOOKS_LOADED", set())
    sa_mod.set_public_base_url("")
    yield
    sa_mod.set_public_base_url("")


def _result(ok=True):
    return {"ok": ok, "goal": "巡检服务", "ts": "2026-08-28 12:00:00",
            "elapsed_sec": 3.2, "routed": ["research", "ops"],
            "converge": {"selfcheck_score": 96, "partners_ok": 2},
            "executions": {"artifacts": []}, "error": ""}


def test_set_public_base_url_and_report_url():
    assert sa_mod.set_public_base_url("http://192.168.1.5:8318/") == "http://192.168.1.5:8318"
    url = sa_mod._report_url_for(_result())
    assert url == "http://192.168.1.5:8318/api/superagent/report?ts=2026-08-28+12%3A00%3A00" \
        or "api/superagent/report?ts=" in url
    assert url.startswith("http://192.168.1.5:8318/api/superagent/report?ts=")
    # 未配置 → 空
    sa_mod.set_public_base_url("")
    assert sa_mod._report_url_for(_result()) == ""
    # 无 ts → 空
    sa_mod.set_public_base_url("http://x:1")
    assert sa_mod._report_url_for({"goal": "x"}) == ""


def test_record_stamps_ts_for_notify(tmp_path, monkeypatch):
    """_record 后 result 带 ts → notify payload.report_url 生成。"""
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [], "note": ""})
                         for d in ("code", "creation", "research", "ops")})
    sa_mod.set_public_base_url("http://192.168.1.5:8318")
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("研究分析竞品趋势并部署监控", session_id="p49", quality_gate=False)
    assert rep.get("ts"), "_record 应回写 ts"
    url = sa_mod._report_url_for(rep)
    assert url.startswith("http://192.168.1.5:8318/api/superagent/report?ts=")
    # 通过真实持久化记录同样可还原链接
    detail_ts = sa_mod.get_recent_runs(1, base_dir=str(tmp_path))[0]["ts"]
    assert detail_ts == rep["ts"]


def test_feishu_card_rich_structure():
    sa_mod.set_public_base_url("http://192.168.1.5:8318")
    payload = dict(sa_mod.__dict__.get("_dummy", {}) or {})
    payload = {"event": "done", "goal": "巡检服务", "ok": True,
               "routed": ["research", "ops"], "elapsed_sec": 3,
               "selfcheck_score": 96, "error": "",
               "report_url": sa_mod._report_url_for(_result())}
    wrapped = sa_mod._webhook_wrap({"fmt": "feishu"}, payload)
    assert wrapped["msg_type"] == "interactive"
    assert wrapped["card"]["header"]["template"] == "green"
    md = wrapped["card"]["elements"][0]["content"]
    assert "**目标**: 巡检服务" in md and "**自检分**: 96" in md
    assert "[📄 查看完整报告](http://192.168.1.5:8318/api/superagent/report" in md
    btn = wrapped["card"]["elements"][1]["actions"][0]
    assert btn["url"].startswith("http://192.168.1.5:8318/api/superagent/report")
    # 失败 → 红色头
    payload2 = dict(payload, ok=False, event="fail", error="boom")
    w2 = sa_mod._webhook_wrap({"fmt": "feishu"}, payload2)
    assert w2["card"]["header"]["template"] == "red"
    assert "编排失败" in w2["card"]["header"]["title"]["content"]


def test_dingtalk_markdown_and_actioncard():
    sa_mod.set_public_base_url("http://192.168.1.5:8318")
    payload = {"event": "done", "goal": "巡检服务", "ok": True,
               "routed": ["ops"], "elapsed_sec": 2,
               "selfcheck_score": 90, "error": "",
               "report_url": sa_mod._report_url_for(_result())}
    wrapped = sa_mod._webhook_wrap({"fmt": "dingtalk"}, payload)
    assert wrapped["msgtype"] == "markdown"
    assert "### " in wrapped["markdown"]["text"]
    assert "[📄 查看完整报告]" in wrapped["markdown"]["text"]
    assert wrapped["actionCard"]["singleURL"].startswith("http://192.168.1.5:8318/api/superagent/report")


def test_raw_payload_embeds_report_url():
    sa_mod.set_public_base_url("http://192.168.1.5:8318")
    r = _result()
    payload = {"event": "done", "goal": r["goal"], "ok": True, "routed": r["routed"],
               "elapsed_sec": 1, "error": "",
               "report_url": sa_mod._report_url_for(r)}
    assert payload["report_url"].startswith("http://192.168.1.5:8318/api/superagent/report?ts=")
    # raw 包装原样透传(带链接)
    assert sa_mod._webhook_wrap({"fmt": "raw"}, payload) is payload
