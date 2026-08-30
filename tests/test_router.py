"""web/router.py 纯单元测试 — 不依赖 socket, 直接验证匹配/参数/方法/异常。"""

import pytest

from lingmengwork.web.router import Router, HttpError, Match


def test_exact_get_match():
    r = Router()
    r.get("/api/health", lambda h: "HEALTH")
    m = r.match("GET", "/api/health")
    assert isinstance(m, Match)
    assert m.params == {}
    assert m.handler(None) == "HEALTH"


def test_method_mismatch_returns_none():
    r = Router()
    r.get("/api/health", lambda h: "HEALTH")
    assert r.match("POST", "/api/health") is None
    assert r.match("PUT", "/api/health") is None


def test_query_string_ignored():
    r = Router()
    r.get("/api/health", lambda h: "H")
    assert r.match("GET", "/api/health?x=1&y=2") is not None


def test_trailing_slash_tolerant():
    r = Router()
    r.get("/api/health", lambda h: "H")
    assert r.match("GET", "/api/health/") is not None
    assert r.match("GET", "/api/health") is not None


def test_param_extraction():
    r = Router()
    captured = {}

    def h(ctx, id):
        captured["id"] = id
        return "OK"

    r.get("/api/orchestrations/<id>", h)
    m = r.match("GET", "/api/orchestrations/abc123")
    assert m is not None
    assert m.params == {"id": "abc123"}
    m.handler(None, **m.params)
    assert captured["id"] == "abc123"


def test_param_only_matches_one_segment():
    r = Router()
    r.get("/api/orchestrations/<id>", lambda ctx, id: id)
    # 多段不应误匹配(参数段不含 /)
    assert r.match("GET", "/api/orchestrations/a/b") is None


def test_static_param_route():
    r = Router()
    got = {}

    def h(ctx, file):
        got["file"] = file

    r.get("/static/<file>", h)
    m = r.match("GET", "/static/preview.js")
    assert m is not None
    assert m.params == {"file": "preview.js"}
    m.handler(None, **m.params)
    assert got["file"] == "preview.js"


def test_post_registration():
    r = Router()
    r.post("/api/chat", lambda h: "CHAT")
    assert r.match("POST", "/api/chat") is not None
    assert r.match("GET", "/api/chat") is None


def test_unregistered_returns_none():
    r = Router()
    r.get("/api/health", lambda h: "H")
    assert r.match("GET", "/nope") is None
    assert r.match("GET", "/api/health/full") is None


def test_routes_listing_for_observability():
    r = Router()
    r.get("/api/health", lambda h: "H")
    r.get("/static/<file>", lambda h, file: None)
    r.post("/api/chat", lambda h: "C")
    routes = r.routes()
    assert ("GET", "/api/health") in routes
    assert ("GET", "/static/<file>") in routes
    assert ("POST", "/api/chat") in routes


def test_http_error_is_exception():
    e = HttpError(404, "not found")
    assert e.status == 404
    assert e.message == "not found"
    with pytest.raises(HttpError):
        raise e
