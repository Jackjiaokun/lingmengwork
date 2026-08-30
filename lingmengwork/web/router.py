"""web/router.py — 零依赖、可测试、与传输无关的 HTTP 路由内核。

灵梦work P2（分解 5732 行单体 server.py）的核心抽象。目标是把
``do_GET`` / ``do_POST`` 里 ``if p == "..."`` 的长链字符串匹配，收口为声明式路由表。

设计要点（对标世界级框架）:
- **方法路由**: GET/POST/PUT/DELETE/PATCH/OPTIONS 各自独立注册。
- **参数路径**: ``/static/<file>``、``/api/orchestrations/<id>`` 用 ``<name>`` 占位，
  匹配时提取为命名参数 dict，交给 handler。
- **传输解耦**: Router 只负责 ``(method, path) -> (handler, params)``；是否通过 socket
  调用、如何读 body/写响应，由集成层决定。本项目中 handler 签名为
  ``handler(ctx, **params)``，``ctx`` 即 ``Handler`` 实例（复用其 ``_send_json`` 等）。
- **纯函数匹配**: 不依赖 socket，可在单测中直接 ``match()`` 验证，无需起服务。
- **易扩展**: 后续把剩余 ~1700 行 /api 分支按域拆成 blueprint 时，只需
  ``router.include(prefix, blueprint)`` 式注册即可（蓝图接口预留）。
"""

import re
from typing import Callable, Dict, List, Optional, Tuple


class HttpError(Exception):
    """路由 handler 可抛出来中断并转成 JSON 错误响应。"""

    def __init__(self, status: int, message: str = ""):
        self.status = status
        self.message = message
        super().__init__(message or "HTTP %d" % status)


class Match:
    """一次成功匹配的结果。"""

    __slots__ = ("handler", "params", "raw")

    def __init__(self, handler: Callable, params: dict, raw: str):
        self.handler = handler
        self.params = params
        self.raw = raw


class Router:
    """声明式 HTTP 路由表。"""

    _PARAM_RE = r"(?P<%s>[^/]+)"

    def __init__(self):
        # (method, compiled_pattern, handler, raw_path) — 含 <param> 的路由
        self._param_routes: List[Tuple[str, "re.Pattern", Callable, str]] = []
        # (method, path) -> handler — 字面量路由, O(1) 精确查表
        self._exact: Dict[Tuple[str, str], Callable] = {}

    # ---- 注册 API ----
    def add(self, method: str, path: str, handler: Callable) -> "Router":
        method = method.upper()
        if "<" in path:
            self._param_routes.append((method, self._compile(path), handler, path))
        else:
            self._exact[(method, path)] = handler
        return self

    def get(self, path, handler):
        return self.add("GET", path, handler)

    def post(self, path, handler):
        return self.add("POST", path, handler)

    def put(self, path, handler):
        return self.add("PUT", path, handler)

    def delete(self, path, handler):
        return self.add("DELETE", path, handler)

    def patch(self, path, handler):
        return self.add("PATCH", path, handler)

    def options(self, path, handler):
        return self.add("OPTIONS", path, handler)

    # ---- 匹配 ----
    def match(self, method: str, path: str) -> Optional[Match]:
        """返回 Match 或 None（供集成层回退到遗留分支）。

        查询串不影响路径匹配; 字面量路由容忍尾部斜杠歧义。
        """
        method = method.upper()
        path = path.split("?", 1)[0]
        hit = self._exact.get((method, path))
        if hit is None:
            hit = self._exact.get((method, path.rstrip("/") or "/"))
        if hit is not None:
            return Match(hit, {}, path)
        for m, rx, handler, raw in self._param_routes:
            if m != method:
                continue
            mo = rx.match(path)
            if mo:
                return Match(handler, mo.groupdict(), raw)
        return None

    def _compile(self, path: str) -> "re.Pattern":
        segs = []
        for seg in path.split("/"):
            if seg.startswith("<") and seg.endswith(">"):
                segs.append(self._PARAM_RE % seg[1:-1])
            else:
                segs.append(re.escape(seg))
        return re.compile("^" + "/".join(segs) + "/?$")

    def routes(self) -> List[Tuple[str, str]]:
        """调试/可观测用: 列出所有已注册 (method, path)。"""
        out = [(m, p) for (m, p) in self._exact]
        for m, _rx, _h, raw in self._param_routes:
            out.append((m, raw))
        return out
