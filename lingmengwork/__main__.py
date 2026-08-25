"""灵梦work CLI: 交互 REPL + 一次性 --prompt + --selftest。

用法:
    python -m lingmengwork                 # 进入交互 REPL
    python -m lingmengwork --prompt "..."  # 一次性执行
    python -m lingmengwork --backend mock  # 强制后端
    python -m lingmengwork --selftest      # 自检
    python -m lingmengwork --version
"""
import argparse
import sys

from . import __version__
from .config import load_config
from .llm.client import build_client
from .tools.registry import build_registry
from .agent.loop import AgentLoop


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


def run_repl(cfg, client, registry):
    loop = AgentLoop(client, registry, cfg)
    print(f"灵梦work v{__version__} | 后端: {client.model} | 输入 /exit 退出, /clear 清空历史")
    while True:
        try:
            user = input("\n\033[1m你>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if not user:
            continue
        if user in ("/exit", "/quit"):
            print("再见。")
            break
        if user == "/clear":
            loop.reset()
            print("历史已清空。")
            continue
        print("\033[1m灵梦>\033[0m ", end="")
        loop.run(user, on_event=_print_event)
        print()


def run_prompt(cfg, client, registry, prompt):
    loop = AgentLoop(client, registry, cfg)
    print("\033[1m灵梦>\033[0m ", end="")
    loop.run(prompt, on_event=_print_event)
    print()


def run_selftest(cfg):
    print(f"灵梦work v{__version__} 自检 ...")
    from .llm.client import MockClient

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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lingmengwork", description="灵梦work — 本地优先 AI 编码代理")
    ap.add_argument("--config", help="配置文件路径 (默认 config.toml)")
    ap.add_argument("--backend", help="强制 LLM 后端: ollama|openai|mock|auto")
    ap.add_argument("--model", help="覆盖模型名")
    ap.add_argument("--prompt", help="一次性执行的自然语言任务")
    ap.add_argument("--version", action="store_true", help="打印版本号")
    ap.add_argument("--selftest", action="store_true", help="运行自检")
    ap.add_argument("--mcp-probe", action="store_true",
                    help="检测 MCP 外部工具接入: 连接已配置服务器并列出工具 (退出码 0=全部成功)")
    args = ap.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    cfg = load_config(args.config)
    if args.backend:
        cfg["llm"]["backend"] = args.backend
    client = build_client(cfg["llm"]["backend"], cfg=cfg, model=args.model)
    registry = build_registry(cfg)

    if args.selftest:
        return run_selftest(cfg)
    if args.mcp_probe:
        import json as _json

        from .tools import mcp as _mcp

        mgr = _mcp.get_manager()
        mgr.connect_all(cfg)
        status = mgr.status()
        print(_json.dumps(status, ensure_ascii=False, indent=2))
        mgr.close_all()
        return 0 if status else 1
    if args.prompt:
        run_prompt(cfg, client, registry, args.prompt)
        return 0
    run_repl(cfg, client, registry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
