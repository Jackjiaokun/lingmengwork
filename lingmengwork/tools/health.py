"""全链路健康度自检 (主题 E 健康度自检)。

把「LLM 连通 + 9 MCP 服务器 + 文件系统根」统一体检，输出红绿状态，
供面板 /api/health/full 与可观测面板使用。

设计原则:
- 核心决策逻辑 (health_check) 是纯函数, 探针 (probe_*) 可注入, 便于单测确定性。
- 运行时端点注入真实探针: LLM 走真实最小 chat(线程超时保护), MCP 校验模块文件存在,
  filesystem 校验 allowed_roots 可达; 端点再补全各 MCP 的实时连接状态。
- 不依赖浏览器即可 e2e 验证 (curl + pytest)。
"""

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor


def enumerate_mcp_servers(cfg):
    """从 config 枚举已配置的 MCP 服务器 (名称/命令/参数/cwd)。

    返回 list[dict], 每项含 name/command/args/cwd。
    """
    sec = (cfg or {}).get("mcp") or {}
    out = []
    for s in (sec.get("servers") or []):
        name = s.get("name") or s.get("command")
        if not name:
            continue
        out.append({
            "name": name,
            "command": s.get("command"),
            "args": s.get("args") or [],
            "cwd": s.get("cwd"),
        })
    return out


def _module_file(server):
    """由 `args` 中的 `-m lingmengwork.tools.mcp_xxx_server` 推导模块源码文件路径。

    命中返回绝对路径, 否则回退 importlib 定位; 都失败返回 None。
    """
    args = server.get("args") or []
    if "-m" in args:
        i = args.index("-m")
        mod = args[i + 1] if i + 1 < len(args) else ""
        if mod:
            rel = mod.replace(".", os.sep) + ".py"
            base = server.get("cwd") or os.getcwd()
            p = os.path.join(base, rel)
            if os.path.isfile(p):
                return os.path.abspath(p)
            try:
                import importlib.util
                spec = importlib.util.find_spec(mod)
                if spec and spec.origin and os.path.isfile(spec.origin):
                    return spec.origin
            except Exception:
                pass
    return None


def _stream_first(client):
    """与真实对话一致的 streaming 路径: 取首个 chunk 即判定连通(首 token 最快)。

    非流式完整响应在某些后端冷启动/网络下偶发 >20s, 导致探针误判 fail(false negative);
    streaming 首 token 通常秒级到达, 与 /api/chat 真实路径一致且更稳健。
    """
    gen = client.chat([{"role": "user", "content": "ping"}], stream=True)
    return next(gen, None)


def probe_llm(cfg, timeout=20.0):
    """真实最小 LLM 连通探针: 发起一次极短 chat, 线程超时保护, 绝不阻塞 HTTP 处理。

    返回 (ok: bool, detail: str, latency_ms: float|None)。
    mock 后端直接视为可用, 不消耗外部额度。

    走 **streaming 首 chunk** 路径(与 /api/chat 一致): 首 token 到达即判定连通,
    避免非流式完整响应冷启动偶发 >20s 的误判。超时默认 20s 覆盖冷启动 TLS 握手/模型预热,
    又能在真正不可达时及时失败。
    """
    try:
        from ..llm.client import build_client
        backend = (cfg or {}).get("llm", {}).get("backend")
        client = build_client(backend, cfg=cfg)
        # mock 后端无需联网, 直接判可用
        if getattr(client, "backend", None) == "mock" or type(client).__name__ == "MockClient":
            return True, "backend=mock (offline) ok", 0.0
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_stream_first, client)
            try:
                first = fut.result(timeout=timeout)
                dt = (time.time() - t0) * 1000
                ok = bool(first)
                return ok, f"backend={backend} ok", round(dt, 1)
            except Exception as e:
                return False, f"{type(e).__name__}: {e}", None
    except Exception as e:
        return False, f"build_client: {type(e).__name__}: {e}", None


def probe_mcp_server(server, cfg):
    """MCP 服务器健康探针 (默认): 校验服务模块源码文件存在 (可启动)。

    返回 (ok: bool, detail: str)。实时连通状态由端点层用 MCPManager 补全。
    """
    f = _module_file(server)
    if not f:
        return False, "server module not found (检查 args 中 -m 模块路径)"
    return True, "module present: " + os.path.basename(f)


def probe_filesystem(cfg):
    """文件系统根探针: 校验 allowed_roots 全部可达。

    返回 (ok: bool, detail: str)。
    """
    try:
        from ..config import resolve_roots
        roots = resolve_roots(cfg)
    except Exception as e:
        return False, f"resolve_roots error: {e}"
    if not roots:
        return False, "no allowed_roots configured"
    missing = [r for r in roots if not os.path.isdir(r)]
    if missing:
        return False, "unreachable roots: " + ", ".join(missing)
    return True, f"{len(roots)} root(s) reachable"


def health_check(cfg, *, llm_probe=None, mcp_probe=None, fs_probe=None):
    """聚合全链路健康度, 返回结构化报告。

    探针可注入以做确定性单测; 默认使用真实探针 (见各 probe_*)。
    报告字段:
      ok / overall: "ok" | "warn" | "fail"
      llm: {status, backend, detail, latency_ms}
      mcp_servers: [{name, status, detail}]
      mcp_count: int
      filesystem: {status, detail}
      generated_at: str
    """
    llm_probe = llm_probe or probe_llm
    mcp_probe = mcp_probe or probe_mcp_server
    fs_probe = fs_probe or probe_filesystem

    backend = (cfg or {}).get("llm", {}).get("backend")
    llm_ok, llm_detail, llm_ms = llm_probe(cfg)
    llm = {
        "status": "ok" if llm_ok else "fail",
        "backend": backend,
        "detail": llm_detail,
        "latency_ms": llm_ms,
    }

    servers = enumerate_mcp_servers(cfg)
    mcp_results = []
    for s in servers:
        ok, detail = mcp_probe(s, cfg)
        mcp_results.append({
            "name": s["name"],
            "status": "ok" if ok else "fail",
            "detail": detail,
        })

    fs_ok, fs_detail = fs_probe(cfg)
    filesystem = {"status": "ok" if fs_ok else "fail", "detail": fs_detail}

    flags = [llm["status"]] + [m["status"] for m in mcp_results] + [filesystem["status"]]
    if any(f == "fail" for f in flags):
        overall = "fail"
    elif any(f == "warn" for f in flags):
        overall = "warn"
    else:
        overall = "ok"

    return {
        "ok": overall == "ok",
        "overall": overall,
        "llm": llm,
        "mcp_servers": mcp_results,
        "mcp_count": len(mcp_results),
        "filesystem": filesystem,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
