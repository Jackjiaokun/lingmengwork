"""Phase 36 · 真实连接器示例: HTTP 端点探测 (sample_http_probe).

提供对任意 URL 的 HEAD/GET 探测能力: 返回状态码、响应耗时(ms)、响应体长度、
Content-Type、是否超时。配 tags 支持联邦路由标签匹配, 供联邦派发时自动调用。

设计:
- 零三方依赖, 仅 urllib + ssl + re; 无 env 依赖, 始终 available。
- 从 goal 中提取首个 http(s) URL 作为目标; 未命中时回退内置自检 URL。
- 8s 超时保护, 异常降级返回 {ok:false, error}。
"""
import re
import ssl
import time
import urllib.request
import urllib.error


_DEFAULT_TARGETS = [
    "https://www.google.com/generate_204",
    "https://httpbin.org/status/204",
]


_URL_RE = re.compile(r"https?://[^\s,'\"\]\)>]+")


def _extract_url(goal):
    m = _URL_RE.search(goal or "")
    if m:
        return m.group(0).rstrip(".,;")
    return None


def _probe(url, method="HEAD", timeout=8):
    """对 url 发起 method 请求, 返回结构化结果。"""
    started = time.time()
    try:
        req = urllib.request.Request(
            url, method=method,
            headers={"User-Agent": "LingMengWork-HTTPProbe/1.0",
                     "Accept": "*/*"})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            elapsed_ms = int((time.time() - started) * 1000)
            body = resp.read(8192)
            return {
                "ok": True, "name": "http_probe",
                "url": url, "method": method,
                "status_code": resp.getcode(),
                "elapsed_ms": elapsed_ms,
                "content_length": len(body),
                "content_type": resp.headers.get("Content-Type", "") or "",
                "result": "OK %d (%dms, %d bytes)" % (resp.getcode(), elapsed_ms, len(body)),
            }
    except urllib.error.HTTPError as e:
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "ok": True, "name": "http_probe",
            "url": url, "method": method,
            "status_code": e.code, "elapsed_ms": elapsed_ms,
            "content_length": 0, "content_type": "",
            "result": "HTTP %d (%dms)" % (e.code, elapsed_ms),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "ok": False, "name": "http_probe", "url": url,
            "elapsed_ms": elapsed_ms,
            "error": "%s: %s" % (type(e).__name__, str(e)[:120]),
        }


def call_fn(goal="", **kw):
    """连接器 handler: goal 优先解析 URL, 失败回退内置探针 URL。
    kw 可含 method(timeout 秒)/url(覆盖)/targets(list)。"""
    target = kw.get("url") or _extract_url(goal)
    method = str(kw.get("method", "HEAD")).upper()
    timeout = int(kw.get("timeout", 8))
    if not target:
        targets = kw.get("targets") or _DEFAULT_TARGETS
        target = targets[0]
    return _probe(target, method=method, timeout=timeout)


def register_connectors(hub):
    """目录扫描入口: 发现并注册本模块的连接器。"""
    hub.register_connector(
        name="http_probe",
        category="network",
        description="HTTP 端点探测连接器: 对任意 URL 做 HEAD/GET 请求, "
                   "返回状态码、耗时(ms)、响应长度与 Content-Type; "
                   "支持联邦路由标签匹配(诊断/网络/探测/端点)。",
        call_fn=call_fn,
        tags=["probe", "http", "network", "diagnosis", "endpoint",
              "latency", "status", "head", "get", "ping",
              "诊断", "网络", "端点", "探测", "延迟", "连通", "超时",
              "http_probe"],
    )
