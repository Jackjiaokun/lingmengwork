"""配置加载: 原生 tomllib (零外部依赖) + 环境变量覆盖。"""
import copy
import os
import sys
import tomllib
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent  # .../lingmengwork/
# PyInstaller frozen 时, exe 同目录的 config.toml 优先 (用户可改)
if getattr(sys, "frozen", False):
    _EXE_DIR = Path(sys.executable).resolve().parent
    DEFAULT_CONFIG_PATHS = [
        _EXE_DIR / "config.toml",
        Path("config.toml"),
        PACKAGE_DIR / "config.toml",
    ]
else:
    DEFAULT_CONFIG_PATHS = [
        Path("config.toml"),
        PACKAGE_DIR / "config.toml",
    ]

DEFAULTS = {
    "llm": {
        # 默认后端: 商汤 SenseNova (OpenAI 兼容)。仅替换 base_url + api_key 即可迁移。
        "backend": "sensenova",
        "ollama": {"base_url": "http://127.0.0.1:11434", "model": "qwen2.5:7b"},
        "openai": {
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key_env": "DEEPSEEK_API_KEY",
            "api_key": "",
        },
        "sensenova": {
            # 商汤 SenseNova OpenAI 兼容接入 (日日新开放平台)。
            # 备选兼容地址: https://api.sensenova.cn/compatible-mode/v2 (模型如 SenseChat-5 / SenseNova-V6-Pro)
            "base_url": "https://token.sensenova.cn/v1",
            "model": "sensenova-6.8-flash-lite",
            "api_key_env": "SENSENOVA_API_KEY",
            "api_key": "",
        },
        "mock": {"model": "mock-coder"},
        # 多路 LLM 同时接入: 命名通道列表, pool/面板可指定通道并发编程。
        # 每项为 {"name", "type": ollama|openai|mock, "model", "base_url"?, "api_key"?/api_key_env?}
        # 未配置时 build_clients() 回退为单 backend。
        "providers": [],
    },
    "agent": {
        "max_iterations": 32,
        "system_prompt": "",
        # 并发上限: 0 = 自动(任务池=通道数*2, 子代理=4)。>0 时统一作为硬上限。
        "concurrency": 0,
        # 工具返回截断: web_fetch/code_search/shell 等可能返回超长内容, 截断以防上下文爆炸。0 = 不截断。
        "tool_result_max_chars": 6000,
        # 主题 B — 智能体循环与推理增强:
        # 反思循环: 每 N 轮注入一次自检提示(0=关闭), 抗空转/促收敛。
        "reflect_every": 0,
        # 工具结果 LLM 摘要: 超长结果用 LLM 摘要代替硬截断(默认关, 开启会增加一次 LLM 往返)。
        "summarize_tool_results": False,
        # 触发摘要的原文长度阈值(字符)。
        "summarize_max_chars": 3000,
        "security": {
            "allowed_roots": ["."],
            "dangerously_run_commands": False,
            "deny_patterns": [
                "rm -rf /",
                "format ",
                "mkfs",
                "shutdown",
                "reboot",
                ":(){",
                "dd if=",
            ],
        },
    },
    "mcp": {
        # 外部工具接入 (Model Context Protocol): 让 agent 调用任意 stdio MCP 服务器提供的工具。
        # 默认不启动任何外部服务 (servers 为空); 配置后进程内懒连接, 工具自动注入工具注册表。
        # 每项: {name, command, args?, env?, cwd?, timeout?}
        # 示例 (需本机有 node/npx):
        #   [[mcp.servers]]
        #   name = "filesystem"
        #   command = "npx"
        #   args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
        "enabled": True,
        "servers": [],
    },
}


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_dotenv():
    """启动期自动载入项目根/.env 的 API Key 等环境变量 (若存在), 不覆盖已设的 env。

    候选位置 (取第一个存在的 .env): 当前工作目录 → 包目录 → 冻结 exe 同目录。
    解析 KEY=VALUE 行, 跳过 # 注释与空行, 去除首尾引号。这是「商汤 key 注入」的
    统一入口: 无论经 启动面板.bat 还是直接 python/PowerShell 启动面板, 只要存在
    .env (由 .env.example 复制填写), 面板即自动注入 SENSENOVA_API_KEY(_2), 无需改代码。
    """
    # 候选顺序: 项目根 cwd → 包目录 → 冻结 exe 同目录 → 冻结 exe 的父目录(项目根)。
    # 覆盖 PyInstaller 冻结后 cwd 不可靠 / exe 在子目录下的各种布局。
    candidates = []
    try:
        cwd = Path.cwd()
        candidates.append(cwd / ".env")
    except Exception:
        pass
    candidates.append(PACKAGE_DIR / ".env")
    if getattr(sys, "frozen", False):
        candidates.append(_EXE_DIR / ".env")
        candidates.append(_EXE_DIR.parent / ".env")
    dotenv = None
    for c in candidates:
        if c and c.is_file():
            dotenv = c
            break
    if not dotenv:
        return
    try:
        with open(dotenv, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                if k and not os.environ.get(k):
                    # 仅当 env 中缺失或为(空字符串)时才用 .env 填充。
                    # 沙箱/Harness 常把 API key 预置成空串, 若用 `k not in os.environ`
                    # 会被误判为"已设置"而跳过真实 key 注入。真值 env (非空) 仍优先于
                    # .env, 保留"显式环境变量覆盖 .env"的语义。
                    os.environ[k] = v
    except Exception:
        pass


def load_config(path=None):
    _load_dotenv()
    cfg = copy.deepcopy(DEFAULTS)
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.extend(DEFAULT_CONFIG_PATHS)
    for c in candidates:
        if c and c.exists():
            try:
                with open(c, "rb") as f:
                    toml = tomllib.load(f)
                cfg = _deep_merge(cfg, toml)
            except Exception:
                pass
            break
    # 从环境变量注入 API Key (所有配置了 api_key_env 的后端都注入)
    for name, sec in cfg["llm"].items():
        if isinstance(sec, dict) and sec.get("api_key_env"):
            sec["api_key"] = os.environ.get(sec["api_key_env"], "")
    return cfg


def build_clients(cfg=None, override_backend=None):
    """构建多路 LLM 客户端。

    返回 dict: {channel_name: LLMClient}。
    - 若 cfg["llm"]["providers"] 非空, 每个 provider 生成一个命名通道。
    - 否则回退为单通道: 键名用当前 backend (ollama/openai/mock/auto 解析后的真实后端)。
    便于上层(任务池/面板)统一用 dict 接口, 无需区分单/多路。
    """
    from .llm.client import OllamaClient, OpenAIClient, MockClient

    cfg = cfg or load_config()
    providers = cfg["llm"].get("providers") or []
    if providers:
        out = {}
        for p in providers:
            name = p.get("name") or p.get("type") or "chan"
            ctype = p.get("type", "ollama")
            model = p.get("model") or cfg["llm"].get(ctype, {}).get("model", "")
            if ctype == "ollama":
                out[name] = OllamaClient(
                    base_url=p.get("base_url") or cfg["llm"]["ollama"]["base_url"],
                    model=model,
                )
            elif ctype == "openai":
                out[name] = OpenAIClient(
                    base_url=p.get("base_url") or cfg["llm"]["openai"]["base_url"],
                    model=model,
                    api_key=p.get("api_key") or os.environ.get(p.get("api_key_env", ""), ""),
                )
            elif ctype == "sensenova":
                out[name] = OpenAIClient(
                    base_url=p.get("base_url") or cfg["llm"]["sensenova"]["base_url"],
                    model=model,
                    api_key=p.get("api_key") or os.environ.get(p.get("api_key_env", ""), "")
                    or cfg["llm"]["sensenova"].get("api_key", ""),
                )
            elif ctype == "mock":
                out[name] = MockClient(model=model or cfg["llm"]["mock"]["model"])
            else:
                continue
        if out:
            return out

    # 回退单通道
    backend = override_backend or cfg["llm"].get("backend", "ollama")
    if backend == "auto":
        # 探测可用后端, 用真实后端名作键
        o = OllamaClient(model=cfg["llm"]["ollama"]["model"], base_url=cfg["llm"]["ollama"]["base_url"])
        if o.is_available():
            return {"ollama": o}
        op = OpenAIClient(model=cfg["llm"]["openai"]["model"], base_url=cfg["llm"]["openai"]["base_url"], api_key=os.environ.get(cfg["llm"]["openai"].get("api_key_env", ""), ""))
        if op.is_available():
            return {"openai": op}
        return {"mock": MockClient(model=cfg["llm"]["mock"]["model"])}
    if backend == "ollama":
        return {"ollama": OllamaClient(model=cfg["llm"]["ollama"]["model"], base_url=cfg["llm"]["ollama"]["base_url"])}
    if backend == "openai":
        return {"openai": OpenAIClient(model=cfg["llm"]["openai"]["model"], base_url=cfg["llm"]["openai"]["base_url"], api_key=os.environ.get(cfg["llm"]["openai"].get("api_key_env", ""), ""))}
    if backend == "sensenova":
        return {"sensenova": OpenAIClient(model=cfg["llm"]["sensenova"]["model"], base_url=cfg["llm"]["sensenova"]["base_url"], api_key=cfg["llm"]["sensenova"].get("api_key", ""))}
    if backend == "mock":
        return {"mock": MockClient(model=cfg["llm"]["mock"]["model"])}
    raise ValueError("unknown backend: " + str(backend))


def resolve_roots(cfg, base_dir=None):
    """把 allowed_roots 解析为绝对路径列表。"""
    base = Path(base_dir or Path.cwd()).resolve()
    roots = []
    for r in cfg["agent"]["security"]["allowed_roots"]:
        p = Path(r)
        if not p.is_absolute():
            p = (base / p).resolve()
        roots.append(p)
    return roots
