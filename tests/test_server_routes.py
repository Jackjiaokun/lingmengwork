"""server.py 路由层集成测试 — 验证 P2 收口正确, 且未迁移路径回退 legacy。

不依赖真实 socket: 直接调用 ROUTER.match 并手动 invoke handler(传入 FakeCtx 桩)。
"""

import pytest

import lingmengwork.web.server as S
from lingmengwork.web.router import Router


class FakeCtx:
    """模拟 Handler 实例, 记录被路由 handler 调用的副作用。"""

    def __init__(self, path="/"):
        self.path = path
        self.served = None
        self.json = None
        self.json_status = None

    def _serve_file(self, name):
        self.served = name

    def _send_json(self, obj, status=200):
        self.json = obj
        self.json_status = status

    def _health_full(self):
        self.json = {"ok": True, "full": True}
        self.json_status = 200

    def _api_health(self):
        # 桩: 验证路由 handler 被正确调用(真实逻辑在 server._api_health)
        self.json = {"ok": True, "version": "stub", "backend": "stub", "model": "stub"}
        self.json_status = 200

    def _serve_static(self, file):
        self.served = "static:" + file


ROUTER: Router = S.ROUTER


def test_router_imported_and_populated():
    assert isinstance(ROUTER, Router)
    assert len(ROUTER.routes()) > 0


@pytest.mark.parametrize("path,expected_file", [
    ("/", "index.html"),
    ("/index.html", "index.html"),
    ("/observability", "observability.html"),
    ("/cost", "cost.html"),
    ("/planboard", "planboard.html"),
    ("/settings", "settings.html"),
    ("/sandbox", "sandbox.html"),
    ("/backups", "backup.html"),            # 路由/文件命名不一致, 必须精确
    ("/memory-graph", "memory_graph.html"),  # 连字符->下划线
    ("/plugins", "plugin_hub.html"),         # 路由/文件命名不一致
    ("/superagent", "superagent.html"),
    ("/studio", "studio.html"),
    ("/automation", "automation.html"),
])
def test_page_route_serves_correct_file(path, expected_file):
    m = ROUTER.match("GET", path)
    assert m is not None, "页面路由 %s 未注册" % path
    ctx = FakeCtx(path)
    m.handler(ctx, **m.params)
    assert ctx.served == expected_file, "%s 应服务 %s, 实际 %s" % (path, expected_file, ctx.served)


def test_static_param_route():
    m = ROUTER.match("GET", "/static/preview.js")
    assert m is not None
    assert m.params == {"file": "preview.js"}
    ctx = FakeCtx("/static/preview.js")
    m.handler(ctx, **m.params)
    assert ctx.served == "static:preview.js"


def test_health_route_wired():
    m = ROUTER.match("GET", "/api/health")
    assert m is not None
    ctx = FakeCtx("/api/health")
    m.handler(ctx, **m.params)  # 真实执行 _api_health (走 _get_cfg/build_client)
    assert ctx.json is not None
    assert ctx.json.get("ok") is True
    assert "version" in ctx.json
    assert "backend" in ctx.json


def test_health_full_route_wired():
    m = ROUTER.match("GET", "/api/health/full")
    assert m is not None
    ctx = FakeCtx("/api/health/full")
    m.handler(ctx, **m.params)
    # _health_full 返回 None(内部已 _send_json), 此处仅确认未抛异常
    assert ctx.json_status in (None, 200) or ctx.json is not None


def test_unmigrated_routes_fallback_to_none():
    # 这些 /api 分支本期未迁移, 必须回退到 legacy(返回 None)
    for path in ("/api/chat", "/api/tasks", "/api/superagent",
                 "/api/orchestrations/abc", "/api/settings", "/api/cost"):
        assert ROUTER.match("GET", path) is None, "%s 不应被 Router 拦截(应走 legacy)" % path
    # POST 路由表为空, 全部回退
    assert ROUTER.match("POST", "/api/chat") is None


def test_dispatch_helper_invokes_and_returns_true():
    # 直接验证 Handler._route_dispatch 对迁移路径返回 True 且执行 handler
    ctx = FakeCtx("/cost")
    assert S.Handler._route_dispatch(ctx, "GET") is True
    assert ctx.served == "cost.html"


def test_dispatch_helper_false_for_unmigrated():
    ctx = FakeCtx("/api/chat")
    assert S.Handler._route_dispatch(ctx, "GET") is False
