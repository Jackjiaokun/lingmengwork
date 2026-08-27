"""Phase 36 · 真实连接器示例 (plugins/sample_http_probe.py) 端到端测试。

覆盖:
- call_fn 直接调用: 无 URL 时回退默认目标, 有 URL 时精确探测
- discover() 从 plugins/ 目录扫描发现并注册
- match_connectors 按目标关键词命中 tags
- federation.dispatch 通过 connector_names 调用, 结果进 matched_connectors
"""
import os
import sys
import tempfile

from lingmengwork import plugin_hub as _ph
from lingmengwork import federation as _fed


_PLUGINS_DIR = os.path.join(os.path.dirname(__file__),
                            "..", "lingmengwork", "plugins")


def _ensure_plugin_path():
    p = os.path.dirname(_PLUGINS_DIR)  # lingmengwork/
    if p not in sys.path:
        sys.path.insert(0, p)


def test_http_probe_call_fn_default_target():
    """call_fn 未解析到 URL → 回退内置默认目标, 不崩。"""
    from lingmengwork.plugins import sample_http_probe as m
    r = m.call_fn("随便给个目标")
    assert r["name"] == "http_probe"
    assert r.get("ok") is not None
    assert "url" in r and "elapsed_ms" in r


def test_http_probe_call_fn_with_url():
    """call_fn 从 goal 解析到 URL → 探测指定目标。"""
    from lingmengwork.plugins import sample_http_probe as m
    r = m.call_fn("probe https://httpbin.org/status/418")
    assert r["name"] == "http_probe"
    assert r["url"].startswith("https://httpbin.org")
    assert r.get("status_code") == 418 or r["ok"] is False


def test_discover_scans_plugins_dir():
    """discover(plugins/) 能发现 sample_http_probe 连接器。"""
    _ph.reset_hub()
    _ensure_plugin_path()
    hub = _ph.get_hub()
    # 清空内置 health/recall/recent_runs, 保留 bootstrap 但覆盖
    hub.connectors.clear()
    found = hub.discover(_PLUGINS_DIR)
    assert found["connectors"] >= 1, "应发现至少一个连接器"
    assert "http_probe" in hub.connectors
    conn = hub.get_connector("http_probe")
    assert conn
    assert "network" in conn.category
    assert "probe" in conn.tags
    assert "endpoint" in conn.tags


def test_match_connectors_hits_by_tags():
    """match_connectors 按 tags 命中 http_probe(诊断/网络/探测/端点)。"""
    _ph.reset_hub()
    _ensure_plugin_path()
    hub = _ph.get_hub()
    hub.connectors.clear()
    hub.discover(_PLUGINS_DIR)
    m1 = hub.match_connectors("诊断网络端点状态")
    names = {x["name"] for x in m1}
    assert "http_probe" in names, "应匹配 http_probe: %s" % names
    m2 = hub.match_connectors("网络诊断 latency")
    assert any(x["name"] == "http_probe" for x in m2)
    m3 = hub.match_connectors("endpoint diagnosis probe")
    assert any(x["name"] == "http_probe" for x in m3), "英文目标也应命中"


def test_federation_dispatch_calls_http_probe(tmp_path, monkeypatch):
    """federation.dispatch 传入 connector_names → 调用 http_probe → 结果进 matched_connectors。"""
    _ph.reset_hub()
    _ensure_plugin_path()
    hub = _ph.get_hub()
    hub.connectors.clear()
    hub.discover(_PLUGINS_DIR)

    # 把 plugins 目录也纳入 _resolve_out_dir 需要的合法 cwd
    monkeypatch.chdir(tempfile.mkdtemp())

    f = _fed.get_federation()
    goal = "probe https://httpbin.org/status/204"
    rep = f.dispatch(goal, connector_names=["http_probe"])
    assert "matched_connectors" in rep
    assert rep["matched_connectors"], "应有 matched_connectors"
    mcs = rep["matched_connectors"]
    assert any(mc["name"] == "http_probe" for mc in mcs), rep
    probe = next(mc for mc in mcs if mc["name"] == "http_probe")
    assert probe["ok"] is True or probe["error"], "http_probe 应有明确结果"
    assert "url" in probe or "error" in probe


def test_http_probe_url_extract():
    """call_fn 从复杂字符串中正确提取首个 URL。"""
    from lingmengwork.plugins import sample_http_probe as m
    r = m.call_fn("请帮我探测下 https://httpbin.org/get 这个接口是否正常")
    assert r["url"].startswith("https://httpbin.org/get")


def test_http_probe_with_kw_url_override():
    """call_fn kw.url 覆盖 goal 解析。"""
    from lingmengwork.plugins import sample_http_probe as m
    r = m.call_fn("随便说点啥", url="https://httpbin.org/headers")
    assert r["url"] == "https://httpbin.org/headers"
