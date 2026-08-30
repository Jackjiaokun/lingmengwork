"""LLM 客户端韧性层测试: 重试/退避/Retry-After/断路器。

通过 monkeypatch urllib.request.urlopen 模拟瞬时与持久故障, 验证:
  - 瞬时 5xx 会退避重试并最终成功;
  - 429 优先采用服务端 Retry-After;
  - 非瞬时 4xx(非429) 不重试, 立即抛出;
  - 连续失败触达阈值后断路器开路, 快速失败;
  - 冷却后(半开)一次成功即复位;
  - FailoverClient 能接住"断路器开路"(URLError) 并切到健康提供者。

注意: 真实 urllib.request.urlopen 遇 HTTP 错误会 *抛出* HTTPError; 桩必须同样 raise,
否则会被误判为成功响应。
"""
import http.client
import urllib.error
import urllib.request

import pytest

from lingmengwork.llm import client as C


def _fake_resp(body=b"{}"):
    class R:
        headers = {}

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return R()


def _http_err(code, retry_after=None):
    hdrs = http.client.HTTPMessage()
    if retry_after is not None:
        hdrs.add_header("Retry-After", str(retry_after))
    return urllib.error.HTTPError("http://x", code, "e", hdrs, None)


def _patch(monkeypatch, seq):
    """seq 中 HTTPError 元素会被 *抛出*(模拟 urlopen 真实行为), 其余原样返回。"""
    it = iter(seq)

    def fake(url, timeout=None):
        v = next(it)
        if isinstance(v, urllib.error.HTTPError):
            raise v
        return v

    monkeypatch.setattr(urllib.request, "urlopen", fake)


def test_retry_transient_then_success(monkeypatch):
    _patch(monkeypatch, [
        _http_err(503),
        _http_err(500),
        _fake_resp(b'{"message":{"content":"hi"}}'),
    ])
    c = C.OllamaClient(max_retries=3, backoff_base=0)
    assert c.chat([{"role": "user", "content": "x"}]) == "hi"
    assert c._breaker.failures == 0  # 成功即复位


def test_retry_after_honored(monkeypatch):
    calls = {"n": 0}

    def fake(url, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_err(429, retry_after=0)  # 0 秒, 测试保持快速
        return _fake_resp(b'{"choices":[{"message":{"content":"ok"}}]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    c = C.OpenAIClient("http://x", "m", max_retries=2, backoff_base=0)
    assert c.chat([{"role": "user", "content": "x"}]) == "ok"
    assert calls["n"] == 2  # 仅重试一次


def test_non_transient_no_retry(monkeypatch):
    calls = {"n": 0}

    def fake(url, timeout=None):
        calls["n"] += 1
        raise _http_err(400)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    c = C.OpenAIClient("http://x", "m", max_retries=3, backoff_base=0)
    with pytest.raises(urllib.error.HTTPError):
        c.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 1  # 不重试


def test_always_fail_breaker_opens(monkeypatch):
    _patch(monkeypatch, [_http_err(503)] * 5)
    c = C.OpenAIClient("http://x", "m", max_retries=2, backoff_base=0)
    c._breaker = C._Breaker(threshold=2, cooldown=10.0)
    with pytest.raises(Exception):
        c.chat([{"role": "user", "content": "x"}])
    assert not c._breaker.allow()  # 已开路(冷却中, 快速失败)


def test_breaker_half_open_recovery(monkeypatch):
    c = C.OpenAIClient("http://x", "m", max_retries=1, backoff_base=0)
    c._breaker = C._Breaker(threshold=2, cooldown=0)
    c._breaker.record_fail()
    c._breaker.record_fail()  # 开路
    assert c._breaker.allow()  # cooldown=0 -> 半开探测

    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda url, timeout=None: _fake_resp(b'{"choices":[{"message":{"content":"recovered"}}]}'),
    )
    assert c.chat([{"role": "user", "content": "x"}]) == "recovered"
    assert c._breaker.failures == 0  # 成功复位


def test_failover_catches_circuit_open(monkeypatch):
    """断路器开路抛 URLError, FailoverClient 必须接住并切到 Mock 提供者。"""
    dead = C.OpenAIClient("http://dead", "m", max_retries=1, backoff_base=0)
    dead._breaker = C._Breaker(threshold=1, cooldown=10.0)
    dead._breaker.record_fail()  # 直接开路
    mock = C.MockClient(model="mock")
    fo = C.FailoverClient([dead, mock], per_timeout=5)
    assert fo.chat([{"role": "user", "content": "x"}])  # 落到 mock, 不抛
