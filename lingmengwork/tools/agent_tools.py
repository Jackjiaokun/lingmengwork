"""任务编排工具: todo 清单 + subagent 子任务派发 (仿 Cline TodoWrite / Codex 子代理)。

- todo: 让主 Agent 在复杂任务前建立可勾选的清单, 提升多步任务的可追溯性。
- subagent: 派发一个或多个独立子任务给新的 AgentLoop (共享同一 registry/cfg), 子代理自主调用工具,
  完成后把最终结果回传给主 Agent。支持单 prompt (串行) 或 prompts 列表 (线程池并发),
  用于「先调研再动手」「并行探索」等编排场景。

注意: subagent 复用传入的 registry (含 undo 快照栈与权限模式), 不新开进程。
"""
import threading
from concurrent.futures import ThreadPoolExecutor

_todo_store = {"items": []}  # 进程内清单: [{content, status, active_form}]
_todo_lock = threading.Lock()


def _resolve_subagent_cap(n_tasks, cfg):
    """计算子代理并发线程数上限。

    - cfg["agent"]["concurrency"] > 0: 作为硬上限。
    - 否则回退默认 4 路。
    最终不超过任务数本身。便于单元测试与配置化调参。
    """
    conc = 0
    try:
        conc = int((cfg or {}).get("agent", {}).get("concurrency", 0) or 0)
    except Exception:
        conc = 0
    if conc <= 0:
        conc = 4
    return max(1, min(n_tasks, conc))


def _tool_todo(args, ctx):
    """建立/更新任务清单。
    参数:
      action: "set" 整体替换清单(列表) | "update" 更新某项状态 | "get" 查看
      items: [{"content":..., "status":"pending|in_progress|completed"}, ...] (set 时)
      index: 项序号(0基, update 时)
      status: 新状态(update 时)
    """
    global _todo_store
    action = (args.get("action") or "get")
    with _todo_lock:
        if action == "set":
            items = args.get("items") or []
            _todo_store["items"] = [
                {"content": it.get("content", ""), "status": it.get("status", "pending")}
                for it in items
            ]
            n = len(_todo_store["items"])
            return f"[todo] 已建立 {n} 项清单:\n" + "\n".join(
                f"  [{i}] [{it['status']}] {it['content']}" for i, it in enumerate(_todo_store["items"])
            )
        if action == "update":
            idx = int(args.get("index", -1) or -1)
            status = args.get("status", "completed")
            items = _todo_store["items"]
            if idx < 0 or idx >= len(items):
                return f"[todo] 无效 index {idx} (当前 {len(items)} 项)"
            items[idx]["status"] = status
            return f"[todo] 已更新 #{idx} -> {status}: {items[idx]['content']}"
        # get
        items = _todo_store["items"]
        if not items:
            return "[todo] (清单为空)"
        return "[todo] 当前清单:\n" + "\n".join(
            f"  [{i}] [{it['status']}] {it['content']}" for i, it in enumerate(items)
        )


def _run_one_subagent(prompt, provider, registry, cfg, clients):
    """单个子任务的执行体 (供线程池调用)。"""
    # 选客户端: 优先 registry.clients (build_registry 已注入), 其次传入 clients
    client = None
    if clients:
        if provider and provider in clients:
            client = clients[provider]
        else:
            client = next(iter(clients.values()))
    else:
        client = getattr(registry, "client", None)
    if client is None:
        return f"[子任务失败] 无可用 LLM 通道: {prompt[:50]}..."

    from ..agent.loop import AgentLoop
    sub = AgentLoop(client, registry, cfg)
    try:
        result = sub.run(prompt, on_event=lambda t, kw: None)
    except Exception as e:
        return f"[子任务异常] {type(e).__name__}: {e}"
    if len(result) > 4000:
        result = result[:4000] + "\n...(子任务结果已截断)"
    return result


def _tool_subagent(args, ctx):
    """派发子任务给独立 AgentLoop。
    参数:
      prompt: 单个子任务描述 (串行)
      prompts: 多个子任务描述列表 (并发线程池, 各自独立 AgentLoop)
      provider?: 指定通道 (留空用默认/轮询)
    子代理拥有独立消息上下文, 但共享 registry (工具/权限/快照), 完成后返回最终文本。
    """
    prompts = args.get("prompts")
    prompt = (args.get("prompt") or "").strip()
    provider = args.get("provider")
    registry = ctx.get("registry") or ctx.get("subagent_registry")
    cfg = ctx.get("cfg")
    clients = (getattr(registry, "clients", None) or ctx.get("clients") or {})
    if registry is None or cfg is None:
        return "[subagent] 上下文缺少 registry/cfg, 无法派发"

    # 规整成任务列表
    if isinstance(prompts, list) and prompts:
        tasks = [str(p).strip() for p in prompts if str(p).strip()]
    elif prompt:
        tasks = [prompt]
    else:
        return "[subagent] prompt / prompts 至少一个非空"

    if len(tasks) == 1:
        res = _run_one_subagent(tasks[0], provider, registry, cfg, clients)
        if len(res) > 4000:
            res = res[:4000] + "\n...(子任务结果已截断)"
        return f"[subagent 结果]\n{res}"

    # 多子任务并发 (并发上限可经 config.toml agent.concurrency 配置化)
    results = [None] * len(tasks)
    cap = _resolve_subagent_cap(len(tasks), cfg)
    with ThreadPoolExecutor(max_workers=cap) as ex:
        futs = {
            ex.submit(_run_one_subagent, t, provider, registry, cfg, clients): i
            for i, t in enumerate(tasks)
        }
        for fut in futs:
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                results[i] = f"[子任务异常] {type(e).__name__}: {e}"

    parts = []
    for i, (t, r) in enumerate(zip(tasks, results)):
        parts.append(f"### 子任务 {i + 1}: {t[:80]}\n{r}")
    header = f"[subagent 并发 {len(tasks)} 路结果]\n"
    body = "\n\n".join(parts)
    if len(body) > 16000:
        body = body[:16000] + "\n...(多子任务汇总已截断)"
    return header + body
