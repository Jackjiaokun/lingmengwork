"""灵梦work统一入口 (供 PyInstaller 打包 & 直接运行)。

子命令:
    lingmengwork                 # 进入交互 REPL (默认, 纯文本)
    lingmengwork chat            # 交互 REPL (纯文本)
    lingmengwork tui             # 全屏 TUI (双栏+状态条+多路任务)
    lingmengwork web             # 启动 WebUI 控制台
    lingmengwork pool --prompt "A" --prompt "B"   # 多路并发编程(高并发)
    lingmengwork --prompt "..."  # 一次性执行(单路)
    lingmengwork --version
    lingmengwork --selftest

打包: pyinstaller lingmengwork.spec
"""
import argparse
import os
import sys

# frozen (PyInstaller) 时把包根加入 sys.path
if getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(sys.executable))

from lingmengwork import __version__
from lingmengwork.config import load_config
from lingmengwork.llm.client import build_client
from lingmengwork.tools.registry import build_registry
from lingmengwork.agent.loop import AgentLoop
from lingmengwork.web.server import run_web
from lingmengwork.tui import run_tui


def _print_event(type_, kw):
    if type_ == "text":
        sys.stdout.write(kw.get("chunk", ""))
        sys.stdout.flush()
    elif type_ == "tool":
        name = kw.get("name", "?")
        args = kw.get("args", {})
        a = ", ".join(f"{k}={v}" for k, v in args.items())
        print(f"\n  \033[36m[工具]\033[0m {name}({a})")
    elif type_ == "tool_result":
        out = kw.get("output", "")
        snippet = out if len(out) <= 600 else out[:600] + "\n... (已截断)"
        print(f"  \033[90m{snippet}\033[0m")
    elif type_ == "done":
        if kw.get("truncated"):
            print("\n\033[33m[已达最大迭代次数, 强行结束]\033[0m")


def run_repl(cfg, client, registry, session_id=None, permission_mode="bypassPermissions"):
    loop = AgentLoop(client, registry, cfg, session_id=session_id)
    if session_id:
        if loop.load_session_messages(session_id):
            print(f"灵梦work v{__version__} | 已恢复会话 {session_id}")
        else:
            print(f"灵梦work v{__version__} | 会话 {session_id} 不存在, 新建")
    else:
        print(f"灵梦work v{__version__} | 后端: {client.model} | 模式: {permission_mode}")
        print("  输入 /exit 退出, /clear 清空, /sessions 历史, /resume <id> 恢复, /mode <plan|acceptEdits|bypass> 切权限")
    while True:
        try:
            user = input("\n\033[1m你>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if not user:
            continue
        if user in ("/exit", "/quit"):
            sid = loop.save_session()
            print(f"会话已保存: {sid}")
            print("再见。")
            break
        if user == "/clear":
            loop.reset()
            print("历史已清空。")
            continue
        if user.startswith("/mode "):
            m = user[6:].strip()
            if m in ("plan", "acceptEdits", "bypassPermissions", "bypass"):
                mm = "bypassPermissions" if m == "bypass" else m
                registry.set_permission_mode(mm)
                print(f"权限模式已切换: {mm}")
            else:
                print("用法: /mode plan | /mode acceptEdits | /mode bypass")
            continue
        if user == "/sessions":
            from lingmengwork.agent.session import list_sessions
            items = list_sessions()
            if not items:
                print("  (无历史会话)")
            else:
                for it in items[:20]:
                    print(f"  {it['id']}  {it['summary'][:40] or '(空)'}  ({it['messages']}条)")
            continue
        if user.startswith("/resume "):
            sid = user[8:].strip()
            if loop.load_session_messages(sid):
                print(f"已恢复会话 {sid}")
            else:
                print(f"会话 {sid} 不存在")
            continue
        print("\033[1m灵梦>\033[0m ", end="")
        loop.run(user, on_event=_print_event)
        loop.save_session()
        print()


def run_prompt(cfg, client, registry, prompt):
    loop = AgentLoop(client, registry, cfg)
    print("\033[1m灵梦>\033[0m ", end="")
    loop.run(prompt, on_event=_print_event)
    print()


def run_selftest(cfg):
    print(f"灵梦work v{__version__} 自检 ...")
    from lingmengwork.llm.client import MockClient

    registry = build_registry(cfg, base_dir=".")
    client = MockClient(model="selftest")
    client.set_script([
        '查看目录:\n```tool\n{"name":"list_dir","arguments":{"path":"."}}\n```',
        "自检通过: 工具调用与回灌链路正常。",
    ])
    loop = AgentLoop(client, registry, cfg)
    events = []
    loop.run("列出当前目录", on_event=lambda t, kw: events.append(t))
    ok = "tool" in events and "tool_result" in events and "done" in events
    print("  模块导入        : OK")
    print("  工具执行        :", "OK" if "tool_result" in events else "FAIL")
    print("  多轮闭环        :", "OK" if ok else "FAIL")
    print("自检结果          :", "\033[32m通过\033[0m" if ok else "\033[31m失败\033[0m")
    return 0 if ok else 1


def run_pool(cfg, prompts, providers=None):
    """多路并发: 每个 --prompt 作为一个任务, 线程池高并发跑。"""
    from lingmengwork.agent.pool import TaskPool

    pool = TaskPool(cfg, base_dir=".")
    print(f"灵梦work 多路并发池 v{__version__} | 通道数: {len(pool.clients)} | 任务数: {len(prompts)}")
    snaps = []
    for i, p in enumerate(prompts):
        prov = (providers[i] if providers and i < len(providers) else None) or None
        snap = pool.submit(p, provider=prov)
        snaps.append(snap)
        print(f"  ➤ 已提交 #{snap['id']} [{snap['provider']}] {p[:40]}{'…' if len(p) > 40 else ''}")
    # 等待全部完成
    import time
    try:
        while any(s["status"] not in ("done", "error") for s in snaps):
            time.sleep(0.3)
            for i, s in enumerate(snaps):
                fresh = pool.get(s["id"])
                if fresh:
                    snaps[i] = fresh
    except KeyboardInterrupt:
        print("\n[中断] 部分任务可能仍在后台运行。")
    print("\n=== 结果汇总 ===")
    for s in snaps:
        fresh = pool.get(s["id"]) or s
        print(f"  #{fresh['id']} [{fresh['provider']}] -> {fresh['status']}")
    pool.shutdown(wait=False)
    return 0


def run_pool_interactive(cfg):
    """交互式多任务提交(空行提交一批, /exit 退出)。"""
    from lingmengwork.agent.pool import TaskPool

    pool = TaskPool(cfg, base_dir=".")
    print(f"灵梦work 多路并发池(交互) v{__version__} | 通道数: {len(pool.clients)}")
    print("  每行一个任务, 空行提交整批并发执行; /exit 退出。")
    buff = []
    while True:
        try:
            line = input("\n\033[1m任务>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line in ("/exit", "/quit"):
            break
        if not line:
            if not buff:
                continue
            for p in buff:
                snap = pool.submit(p)
                print(f"  ➤ #{snap['id']} [{snap['provider']}] {p[:40]}")
            buff = []
            continue
        buff.append(line)
    pool.shutdown(wait=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lingmengwork", description="灵梦work — 本地优先 AI 编码代理 (CLI / Web / Android)")
    ap.add_argument("cmd", nargs="?", default="web", choices=["chat", "web", "tui", "pool"], help="web=WebUI控制台(默认, 双击即开), chat=纯文本终端, tui=全屏TUI, pool=多路并发任务")
    ap.add_argument("--host", default="127.0.0.1", help="web 监听地址 (android 用 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8318, help="web 监听端口")
    ap.add_argument("--config", help="配置文件路径 (默认 config.toml)")
    ap.add_argument("--backend", help="强制 LLM 后端: ollama|openai|mock|auto")
    ap.add_argument("--model", help="覆盖模型名")
    ap.add_argument("--prompt", action="append", default=[], help="任务提示(可重复多次, 每个 --prompt 为一个并发任务)")
    ap.add_argument("--provider", action="append", default=[], help="指定通道名(与 --prompt 对应, 可重复; 留空自动轮询)")
    ap.add_argument("--version", action="store_true", help="打印版本号")
    ap.add_argument("--selftest", action="store_true", help="运行自检")
    ap.add_argument("--mcp-probe", action="store_true",
                    help="检测 MCP 外部工具接入: 连接已配置服务器并列出工具 (退出码 0=有工具)")
    ap.add_argument("--resume", help="恢复指定 session id 的历史对话 (chat/tui 模式)")
    ap.add_argument("--permission-mode", default="bypassPermissions",
                    choices=["plan", "acceptEdits", "bypassPermissions"],
                    help="权限模式: plan(仅只读) | acceptEdits(自动接受编辑,禁命令) | bypassPermissions(全放开)")
    args = ap.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    cfg = load_config(args.config)
    if args.backend:
        cfg["llm"]["backend"] = args.backend
        # 显式 --backend 时强制单后端, 覆盖 config.toml 的 providers 多通道
        # (否则 build_clients 优先用 providers, --backend 仅对单后端路径生效)
        cfg["llm"].pop("providers", None)

    if args.selftest:
        return run_selftest(cfg)
    if args.mcp_probe:
        import json as _json

        from lingmengwork.tools import mcp as _mcp

        mgr = _mcp.get_manager()
        mgr.connect_all(cfg)
        status = mgr.status()
        print(_json.dumps(status, ensure_ascii=False, indent=2))
        mgr.close_all()
        return 0 if status else 1
    if args.prompt:
        return run_pool(cfg, args.prompt, args.provider)
    if args.cmd == "pool":
        # 无 --prompt 时进入交互式多任务提交
        return run_pool_interactive(cfg)

    if args.cmd == "web":
        run_web(host=args.host, port=args.port, cfg=cfg)
        return 0

    if args.cmd == "tui":
        from lingmengwork.config import build_clients
        clients = build_clients(cfg)
        registry = build_registry(cfg)
        return run_tui(cfg, clients=clients, registry=registry)

    client = build_client(cfg["llm"]["backend"], cfg=cfg, model=args.model)
    registry = build_registry(cfg, permission_mode=args.permission_mode)
    run_repl(cfg, client, registry, session_id=args.resume, permission_mode=args.permission_mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
