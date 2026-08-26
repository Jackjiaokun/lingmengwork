"""Agent 多轮循环: 解析工具调用 -> 执行 -> 回灌 -> 重复, 直至完成或达上限。"""
import json
import re
import os
import time

from .prompt import build_system_prompt
from .context import build_project_context
from .context import build_memory_context
from ..llm import pricing as _pricing
from ..tools.registry import _extract_struct

# 工具调用围栏: ```tool\n{json}\n```
TOOL_RE = re.compile(r"```tool\s*\n(.*?)```", re.DOTALL)

# —— 收敛护栏提示 (修复「已达最大迭代」被硬截断) ——
# ① 临近上限: 模型仍在调工具时, 强制其基于已有信息给最终结论
_CONVERGE_HINT = (
    "⚠️ 迭代即将达到上限。请立即停止调用任何工具, "
    "基于已获取的全部工具结果, 用中文直接给出最终结论。"
)
# ② 循环检测: 模型连续多轮发起完全相同的工具调用(无新进展)时, 打断死循环
_LOOP_HINT = (
    "⚠️ 检测到你正在重复调用完全相同的工具, 没有取得新信息。 "
    "请停止调用工具, 直接基于已有结果给出最终结论, 不要再次发起相同调用。"
)
# ③ 反思循环 (主题 B): 周期性自检, 评估「目标 vs 进展」, 偏离则纠偏 (抗空转/促收敛)
_REFLECT_HINT = (
    "🤔 阶段性自检: 请花一点时间回顾——你当前的目标是什么? 已通过工具取得了哪些关键事实? "
    "下一步最该做什么才能逼近最终交付? 若已掌握足够信息, 直接给结论; "
    "若还需工具, 只调用能带来新信息且必要的工具, 避免无效重复。"
)
# ④ 配额耗尽 (主题 A): 单任务工具调用次数达上限, 不再执行工具, 强制基于已有结果收尾。
_QUOTA_HINT = (
    "⚠️ 本任务的工具调用次数已达配置上限。请立即停止调用任何工具, "
    "基于已获取的全部工具结果, 用中文直接给出最终结论或交付物。"
)


# —— 主题 B — 计划看板 (批次13): 把计划 markdown 解析为可勾选卡片 (纯函数, 便于单测) ——
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_NUMBER_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _parse_plan_cards(md):
    """把计划类 markdown 解析为结构化看板数据。

    返回 dict:
      title:       文档主标题 (# 一级标题或首行)
      sections:    [{heading, items:[{text, kind:'task'|'note'|'step', checked:bool}]}]
      tasks:       扁平的可勾选任务列表 (由 checkbox / 编号项聚合), 供进度统计
      raw:         原始 markdown
    """
    if not md:
        return None
    lines = str(md).splitlines()
    title = ""
    sections = []
    cur = None           # 当前 section
    tasks = []           # 扁平可勾选任务
    # 预扫描: 是否整体为编号/复选框清单 (无标题则单 section)
    for i, line in enumerate(lines):
        h = _HEADING_RE.match(line)
        if h and h.group(1) == "#" and not title:
            title = h.group(2).strip()
            continue
        if h and h.group(1) != "#":
            if cur:
                sections.append(cur)
            cur = {"heading": h.group(2).strip(), "items": []}
            continue
        cb = _CHECKBOX_RE.match(line)
        if cb:
            checked = cb.group(1).lower() == "x"
            item = {"text": cb.group(2).strip(), "kind": "task", "checked": checked}
            if cur is None:
                cur = {"heading": "", "items": []}
            cur["items"].append(item)
            tasks.append(item)
            continue
        num = _NUMBER_RE.match(line)
        if num:
            item = {"text": num.group(1).strip(), "kind": "step", "checked": False}
            if cur is None:
                cur = {"heading": "", "items": []}
            cur["items"].append(item)
            tasks.append(item)
            continue
        bn = _BULLET_RE.match(line)
        if bn:
            item = {"text": bn.group(1).strip(), "kind": "note", "checked": False}
            if cur is None:
                cur = {"heading": "", "items": []}
            cur["items"].append(item)
            continue
    if cur:
        sections.append(cur)
    if not sections:
        # 纯段落文本: 作为单个 note section
        paras = [p.strip() for p in md.split("\n\n") if p.strip()]
        sections = [{"heading": "", "items": [{"text": p, "kind": "note", "checked": False} for p in paras]}]
    if not title:
        # 退而取首段首句
        first = next((s for s in sections if s.get("items")), None)
        if first and first["items"]:
            title = first["items"][0]["text"][:40]
    return {
        "title": title,
        "sections": sections,
        "tasks": tasks,
        "raw": md,
    }


# —— 工具结果脱敏 (主题 D): 回灌前自动遮蔽密钥/密码/令牌, 防凭证泄露 ——
# 匹配「敏感键名 = / : 值」与已知的密钥前缀格式。纯函数, 便于单测。
_SECRET_KEY_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|auth(?:orization)?|client[_-]?secret|session[_-]?key)\b"
    r"\s*[:=]\s*(\S+)"
)
# 已知密钥前缀/格式 (不依赖键名, 直接匹配值本身)
_SECRET_VALUE_RE = re.compile(
    r"(?i)("
    r"sk-[A-Za-z0-9]{8,}|"          # OpenAI / SenseNova 等 sk- 前缀
    r"ghp_[A-Za-z0-9]{20,}|"        # GitHub PAT
    r"xox[bap]-[A-Za-z0-9-]{10,}|"  # Slack token
    r"AIza[0-9A-Za-z_-]{20,}|"      # Google API key
    r"AKIA[0-9A-Z]{16,}|"           # AWS access key id
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"  # JWT
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"  # PEM 私钥块
    r")"
)
_REDACTED = "***REDACTED***"


def _redact(text):
    """把工具结果中的密钥/密码/令牌替换为 ***REDACTED***。纯函数。"""
    if not text:
        return text
    s = str(text)
    s = _SECRET_VALUE_RE.sub(lambda m: _REDACTED, s)
    s = _SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}: {_REDACTED}", s)
    return s


# —— 工具失败自愈归因 (主题 D / 全球领先标准): 把报错分类, 给模型可执行的修正提示 ——
# 返回 "" 表示无需特别提示; 否则返回形如 " [网络异常?重试/换源]" 的归因标签, 注入工具结果标记。
_FAIL_PATTERNS = [
    (("readtimeout", "operation timed out", "deadline exceeded", "timed out"),
     "超时异常? 降低数据量或增大超时后重试"),
    (("connectionerror", "connection refused", "connection reset", "econnreset",
      "name or service not known", "getaddrinfo", "network is unreachable",
      "socket.gaierror", "failed to resolve", "dns"),
     "网络异常? 检查网络/代理, 可重试一次或换源"),
    (("403 forbidden", "401 unauthorized", "401 ", "permission denied", "eacces", "access is denied",
      "[权限拒绝]", "not allowed"),
     "权限异常? 当前模式/凭据不足, 换用合法路径或提升权限模式"),
    (("memoryerror", "out of memory", "disk full", "no space left", "resource temporarily unavailable"),
     "资源异常? 输入过大或环境资源不足, 缩小范围后重试"),
    (("no such file", "filenotfounderror", "file not found", "404", "does not exist", "no such directory"),
     "未找到? 路径/文件名有误, 先用 list_dir/glob 确认实际位置"),
    (("syntaxerror", "indentationerror", "typeerror", "attributeerror", "keyerror", "valueerror",
      "nameerror", "indexerror", "modulenotfounderror"),
     "逻辑/语法异常? 检查参数与调用方式, 修正后重试"),
]
_FAIL_HINT_RE = None  # 延迟编译


def _classify_failure(text):
    """把工具报错文本分类为 网络/权限/超时/资源/未找到/逻辑, 返回归因提示标签。纯函数。"""
    if not text:
        return ""
    s = str(text).lower()
    if s.startswith("[tool error]") or "[mcp error]" in s or "[权限拒绝]" in s:
        # 已带前缀, 仍做细分以辅助自愈
        pass
    for keys, hint in _FAIL_PATTERNS:
        for k in keys:
            if k in s:
                return f" [{hint}]"
    return ""


# 历史压缩摘要提示 (自动上下文压缩): 把长会话旧回合提炼为关键结论/决策/待办, 保真溯源。
_COMPACT_PROMPT = (
    "你是一个长会话历史压缩器。下面是一段 AI 编码智能体的早期对话历史(含工具调用与结果)。"
    "请提炼对继续完成任务真正关键的信息: "
    "(1) 已完成的工具与关键发现(文件/符号/结论, 保留原始路径与行号); "
    "(2) 已做出的决策与约束; (3) 当前待办与未决问题。用中文要点输出, 不超过 16 条, "
    "保留原始文件路径与行号原文, 不要编造未出现的内容。"
)


# 工具结果 LLM 摘要提示 (主题 B): 把超长原始输出压缩为关键要点, 省 token/时延
_SUMMARIZE_PROMPT = (
    "你是一个工具结果压缩器。下面是一段过长的工具输出。请提炼对完成当前编程任务"
    "真正关键的信息: 错误/异常、关键数值、文件路径与行号、函数/符号签名、结论。去除噪声与重复。"
    "用中文要点输出, 不超过 12 条, 保留原始文件路径与行号原文, 不要编造未出现的内容。"
)


# —— 容忍性工具调用解析 (修复「多工具调用静默失效」) ——
# 模型常把多行代码直接写进 content 且**不转义引号/换行** -> 严格 JSON 非法 -> 调用被丢弃 -> 链路断裂。
# 因此协议采用**对模型鲁棒的行式格式** (content 用哨兵承接原始多行, 无需 JSON 转义):
#   ```tool
#   name: write_file
#   path: lmw_agent_probe/calc.py
#   content:
#   def add(a, b):
#       return a + b
#   ```
# 同时保留 ```tool\n{json}``` 旧格式的回退解析 (strict=False 容忍字符串内控制字符)。
_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"', re.DOTALL)

# 这些键后面的值是「原始多行内容」(从本行冒号后一直取到围栏结束, 不做 JSON 转义)
_VERBATIM_KEYS = {
    "content", "old", "new", "diff", "body", "thought", "note",
    "message", "plan", "summary", "items", "blocks", "output", "text", "code",
}
_KV_LINE_RE = re.compile(r"^([A-Za-z_][\w]*)\s*:\s?(.*)$")


def _tolerant_json_loads(raw):
    """回退: 优先严格解析, 失败则 strict=False (容忍字符串内控制字符) 重试。"""
    try:
        return json.loads(raw)
    except Exception:
        pass
    return json.loads(raw, strict=False)


def _coerce_scalar(v):
    """行式协议里非 verbatim 的标量: 尝试按 JSON 解析(数组/数字/布尔), 否则保留字符串。"""
    s = v.strip()
    if not s:
        return ""
    try:
        return json.loads(s)
    except Exception:
        return s


def _truncate_tool_result(res, limit):
    """把超长工具返回截断, 防止 web_fetch/code_search/shell 等撑爆上下文。

    limit<=0 表示不截断。纯函数, 便于单测。
    """
    if not limit or limit <= 0:
        return res
    s = str(res)
    if len(s) <= limit:
        return res
    return s[:limit] + f"\n... [工具结果已截断: 原文 {len(s)} 字符, 保留前 {limit} 字符]"


def _post_process_result(client, res, *, summarize, summarize_max, hard_limit):
    """长结果处理 (主题 B): 开启 LLM 摘要时优先摘要, 否则硬截断; 无 LLM/异常自动回退截断。

    - summarize=True 且原文超 summarize_max -> 调 client.chat(stream=False) 摘要, 失败回退截断。
    - 纯函数化调用 (client 传入), 便于单测用假 client 验证摘要路径。
    """
    s = str(res)
    if summarize and len(s) > summarize_max:
        try:
            summary = client.chat(
                [
                    {"role": "system", "content": _SUMMARIZE_PROMPT},
                    # 仅取前 8k 提炼, 防止摘要本身撑爆上下文
                    {"role": "user", "content": s[:8000]},
                ],
                stream=False,
                temperature=0.0,
            )
            summary = (summary or "").strip()
            if summary:
                return summary + f"\n... [已用 LLM 摘要原始 {len(s)} 字符的工具结果]"
        except Exception:
            pass
    return _truncate_tool_result(res, hard_limit)


def _parse_kv_tool(raw):
    """解析行式工具块: name: X / key: value 行; 命中 _VERBATIM_KEYS 则取后续全部行为原始值。"""
    lines = raw.split("\n")
    name = None
    args = {}
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _KV_LINE_RE.match(line)
        if not m:
            i += 1
            continue
        key = m.group(1).lower()
        val = m.group(2)
        if key == "name":
            name = val.strip()
            i += 1
            continue
        if key in _VERBATIM_KEYS:
            # 从本行冒号后的值 + 之后所有行, 拼接为原始多行值 (去尾部空行)
            rest = [val]
            j = i + 1
            while j < n:
                rest.append(lines[j])
                j += 1
            while rest and not rest[-1].strip():
                rest.pop()
            blob = "\n".join(rest)
            # 若整体是合法 JSON (如 todo 的 items: [...]), 优先用结构化值
            try:
                args[key] = json.loads(blob, strict=False)
            except Exception:
                args[key] = blob
            break
        args[key] = _coerce_scalar(val)
        i += 1
    if not name:
        return None
    return {"name": name, "arguments": args}


def _parse_one_tool(raw):
    """解析单个 ```tool 块: 行式优先, 否则回退 JSON。"""
    s = raw.strip()
    if not s:
        return None
    # 行式协议以首行 `name:` 起始 (只测首行: _KV_LINE_RE 无 MULTILINE, 整块多行会让 $ 失配)
    first_line = s.split("\n", 1)[0]
    if _KV_LINE_RE.match(first_line) and first_line.lower().startswith("name:"):
        call = _parse_kv_tool(s)
        if call:
            return call
    # 回退 JSON (严格的或 tolerant)
    try:
        obj = _tolerant_json_loads(s)
    except Exception:
        return None
    name = obj.get("name") or obj.get("tool")
    if not name:
        return None
    return {"name": name, "arguments": obj.get("arguments") or obj.get("args") or {}}



# 工具分类 (用于前端链路可视化染色)
_WRITE_TOOLS = {"write_file", "edit_file", "apply_patch", "insert_at", "replace_in_files", "undo"}
_READ_TOOLS = {"read_file", "list_dir", "glob", "grep", "diff_view", "repo_map", "review_code", "memory"}
_EXEC_TOOLS = {"run_command", "auto_test"}
_PLANNING_TOOLS = {"think", "todo", "subagent"}
# MCP 外部工具 (按危险度分类, 与 registry 权限分层保持一致)
_MCP_READ = {"fs_read", "fs_list", "code_search", "db_query", "db_list_tables",
             "web_fetch", "demo_echo", "demo_time"}
_MCP_WRITE = {"fs_write", "git_add"}
_MCP_EXEC = {"shell_exec"}
_MCP_GIT = {"git_status", "git_diff", "git_log", "git_branch", "git_commit"}


def tool_kind(name):
    """把工具名归到可视化类别: write/edit/read/exec/test/review/plan/git/search/other。"""
    if name in _WRITE_TOOLS or name in _MCP_WRITE:
        return "write"
    if name in _READ_TOOLS or name in _MCP_READ:
        return "read"
    if name in _EXEC_TOOLS or name in _MCP_EXEC:
        return "exec"
    if name == "review_code" or name == "code_review":
        return "review"
    if name in _PLANNING_TOOLS:
        return "plan"
    if name == "git_commit" or name in _MCP_GIT:
        return "git"
    if name in ("web_search", "fetch", "db_query", "db_list_tables", "symbol_search", "code_search"):
        return "search"
    return "other"



class AgentLoop:
    def __init__(self, client, registry, cfg, system_prompt_override=None, auto_context=True, session_id=None, provider="", experts=None, skills=None, enhance_data=None):
        self.client = client
        self.registry = registry
        self.cfg = cfg
        self.max_iter = int(cfg["agent"]["max_iterations"])
        self._tool_result_limit = int((cfg["agent"].get("tool_result_max_chars") or 6000))
        # 主题 B: 反思循环 / 工具结果 LLM 摘要 (默认均关闭, 由 config 开启)
        self._reflect_every = int(cfg["agent"].get("reflect_every") or 0)
        self._summarize = bool(cfg["agent"].get("summarize_tool_results") or False)
        self._summarize_max = int(cfg["agent"].get("summarize_max_chars") or 3000)
        # 主题 A — 工具调用配额 (批次4): 单任务累计次数上限, 0=不限
        self._tool_quota = int(cfg["agent"].get("tool_call_quota") or 0)
        self._tool_calls = 0  # 本轮 run() 已执行的工具调用计数 (跨轮累计)
        # 主题 D — 工具结果脱敏 (批次4): 回灌前遮蔽密钥/密码
        self._redact = bool(cfg["agent"].get("redact_secrets", True))
        # 主题 B-Compaction — 自动上下文压缩 (全球领先标准): 超阈值压缩旧回合, 防长会话退化
        self._compact_threshold = int(cfg["agent"].get("context_compact_threshold") or 0)
        self._keep_recent = max(1, int(cfg["agent"].get("context_keep_recent") or 6))
        self._compact_count = 0  # 已发生的压缩次数(供成本/可观测)
        self.session_id = session_id or None
        self.provider = provider
        self.model = getattr(client, "model", "") or ""
        # 主题 F — 专家/技能 提示词增强: 本轮激活的条目与增强库快照
        self._enhance_data = enhance_data or {"experts": [], "skills": []}
        self._active_experts = list(experts or [])
        self._active_skills = list(skills or [])
        # 主题 B — 计划看板 (批次13): 捕获计划模式产物, 供 Web 计划看板可视化
        self.plan_artifact = None
        override = system_prompt_override if system_prompt_override is not None else cfg["agent"].get("system_prompt")
        if override:
            self.system_prompt = override
        else:
            self.system_prompt = build_system_prompt(self.registry.list_tools())
        # 自动装配项目上下文 (仿 Claude Code 自动上下文), 注入 system 末尾
        self.project_context = ""
        self.memory_context = ""
        if auto_context:
            try:
                roots = getattr(registry, "roots", None)
                if roots:
                    self.project_context = build_project_context(roots)
                    self.memory_context = build_memory_context(roots)
                    # 批次7 — 项目记忆文档自动读取 (CLAUDE.md/AGENTS.md/README.md) 注入 system, 仿 Claude Code
                    if (cfg["agent"].get("security", {}) or {}).get("read_project_docs", True):
                        pdocs = self._load_project_docs()
                        if pdocs:
                            self.project_context = (self.project_context + "\n\n" + pdocs).strip()
            except Exception:
                self.project_context = ""
                self.memory_context = ""
        self.messages = [{"role": "system", "content": self._full_system()}]
        self.iteration = 0  # 当前已完成的主循环轮数 (工具回合)
        self._recent_sigs = []  # 最近几轮工具调用签名, 用于循环检测(防反复调同一工具)
        # Token/成本估算 (仿 Claude Code --cost): 接口多不返回 usage, 用字符数×系数估算
        self.est_input_chars = 0
        self.est_output_chars = 0
        self._CHAR_PER_TOKEN = 1.6  # 中英文混合经验值: ~1.6 字符/token

    def _est_tokens(self, chars):
        return int(chars / self._CHAR_PER_TOKEN) if chars else 0

    def token_stats(self):
        """返回估算的 token 用量与成本(按 model 价格档, 见 llm/pricing.py)。"""
        inp = self._est_tokens(self.est_input_chars)
        out = self._est_tokens(self.est_output_chars)
        total = inp + out
        cost = _pricing.cost(inp, out, self.model)
        return {
            "model": self.model,
            "est_input_tokens": inp,
            "est_output_tokens": out,
            "est_total_tokens": total,
            "est_cost_cny": round(cost, 6),
        }

    def _capture_plan(self, text):
        """主题 B — 计划看板 (批次13): 计划模式下捕获最终产物。"""
        if not text:
            return
        mode = getattr(getattr(self, "registry", None), "permission_mode", "") or ""
        if mode != "plan":
            return
        if str(text).startswith(("[tool error]", "[权限拒绝]", "[mcp error]")):
            return
        self.plan_artifact = text

    def get_plan_cards(self):
        """返回计划看板结构化数据: 解析 plan_artifact 为可勾选卡片。无产物返回 None。"""
        if not self.plan_artifact:
            return None
        return _parse_plan_cards(self.plan_artifact)

    def _full_system(self):
        parts = [self.system_prompt]
        if self.project_context:
            parts.append(self.project_context)
        if self.memory_context:
            parts.append(self.memory_context)
        # 主题 F — 专家/技能 提示词增强注入
        try:
            from .enhance import build_enhancement_block
            blk = build_enhancement_block(self._active_experts, self._active_skills, self._enhance_data)
            if blk:
                parts.append(blk)
        except Exception:
            pass
        return "\n\n".join(parts)

    def set_enhancement(self, experts=None, skills=None, enhance_data=None):
        """主题 F — 更新激活的专家/技能并重建系统提示(仅替换 messages[0])。"""
        if enhance_data is not None:
            self._enhance_data = enhance_data
        if experts is not None:
            self._active_experts = list(experts)
        if skills is not None:
            self._active_skills = list(skills)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self._full_system()

    def reset(self):
        self.messages = [{"role": "system", "content": self._full_system()}]
        # 同时清零 token/成本估算, 让 /clear 后状态条真正归零
        self.est_input_chars = 0
        self.est_output_chars = 0

    def _load_project_docs(self):
        """启动时读取项目根的项目记忆文档 (CLAUDE.md/AGENTS.md/README.md), 注入 system (仿 Claude Code)。

        返回拼接文本; 无则空串。
        """
        try:
            roots = getattr(self.registry, "roots", None) or []
            if not roots:
                return ""
            root = roots[0]
            docs = []
            for fn in ("CLAUDE.md", "AGENTS.md", "README.md"):
                p = os.path.join(root, fn)
                if os.path.isfile(p):
                    try:
                        txt = open(p, encoding="utf-8", errors="replace").read().strip()
                    except Exception:
                        continue
                    if txt:
                        docs.append("# 项目记忆文档 %s\n%s" % (fn, txt[:4000]))
            return "\n\n".join(docs)
        except Exception:
            return ""

    # —— 自动上下文压缩 (主题 B-Compaction, 全球领先标准) ——
    def _maybe_compact(self):
        """达到上下文阈值且旧回合足够多时压缩历史; 返回是否发生压缩。"""
        if not self._compact_threshold or self._compact_threshold <= 0:
            return False
        total = sum(len(m.get("content", "")) for m in self.messages)
        # 防抖: 至少要有「主system + 阈值外旧回合 + 最近 keep 轮」才压缩, 避免频繁/无意义压缩
        if total < self._compact_threshold:
            return False
        if len(self.messages) <= self._keep_recent + 2:
            return False
        return self._compact_history()

    def _compact_history(self):
        """把 messages[1:-keep] 的旧回合压缩为单条 [历史压缩摘要] (system 角色, 紧随主 system)。

        优先调 LLM 摘要(若可用), 失败回退启发式提取(工具名+关键片段/结论)。返回是否成功压缩。
        """
        keep = self._keep_recent
        head = self.messages[0]            # 主 system
        recent = self.messages[-keep:]     # 最近 keep 轮保留原文
        old = self.messages[1:-keep]       # 待压缩的旧回合
        if not old:
            return False
        summary = self._summarize_old(old)
        self.messages = [
            head,
            {"role": "system", "content":
             "[历史压缩摘要] 以下内容已自动压缩, 仅保留关键结论/决策/待办, 请据此继续:\n" + summary},
        ] + recent
        self._compact_count += 1
        return True

    def _summarize_old(self, old_messages):
        """把旧回合摘要为文本。优先 LLM 摘要, 无/失败则启发式提取。"""
        # 启发式提取: assistant 首段结论 + 每个 tool_result 的工具名与关键片段
        heur = []
        for m in old_messages:
            role = m.get("role", "")
            content = m.get("content", "") or ""
            if role == "assistant":
                first = next((ln.strip() for ln in content.split("\n") if ln.strip()), "")
                if first:
                    heur.append("• 结论: " + first[:300])
            elif role == "user" and "[tool result:" in content:
                head = content.split("\n", 1)[0][:80]
                body = content[len(head):].strip().replace("\n", " ")[:240]
                heur.append(f"• {head}: {body}")
        heur_text = "\n".join(heur) if heur else "(早期历史无有效工具结果)"
        # 尝试 LLM 摘要 (失败/空则回退启发式)
        try:
            s = self.client.chat(
                [
                    {"role": "system", "content": _COMPACT_PROMPT},
                    {"role": "user", "content": "\n\n".join(
                        f"[{m.get('role', '')}] {m.get('content', '')}" for m in old_messages
                    )[:8000]},
                ],
                stream=False,
                temperature=0.0,
            )
            s = (s or "").strip()
            if s:
                return s
        except Exception:
            pass
        return heur_text

    def run(self, user_message, on_event=None):
        """执行一轮用户请求, 返回最终文本。on_event(type, kw) 用于流式展示。"""
        self.messages.append({"role": "user", "content": user_message})

        def emit(type_, **kw):
            if on_event:
                on_event(type_, kw)

        def _emit_done(text, **kw):
            # 主题 B — 计划看板 (批次13): 计划模式下捕获最终产物, 供 Web 计划看板可视化
            self._capture_plan(text)
            emit("done", text=text, **kw)

        last = ""
        seq = 0
        chain = []  # 工具调用链 (时序): [{seq, name, kind, ok}] 供前端可视化全链路
        for _ in range(self.max_iter):
            self.iteration += 1
            # 估算 input token (本轮发给模型的全部上下文)
            self.est_input_chars += sum(len(m.get("content", "")) for m in self.messages)
            # 主题 B-Compaction — 自动上下文压缩: 超阈值压缩旧回合, 防长会话退化(全球领先标准)
            if self._maybe_compact():
                emit("compact", kept_recent=self._keep_recent, count=self._compact_count,
                     summary_len=len(self.messages[1].get("content", "")) if len(self.messages) > 1 else 0)
            chunks = []
            for chunk in self.client.chat(self.messages, stream=True):
                chunks.append(chunk)
                emit("text", chunk=chunk)
            assistant = "".join(chunks)
            last = assistant
            self.est_output_chars += len(assistant)
            self.messages.append({"role": "assistant", "content": assistant})

            calls = self._parse_tools(assistant)
            if not calls:
                _emit_done(assistant, truncated=False, chain=chain, **self.token_stats())
                return assistant

            # 主题 A — 工具调用配额: 达上限即停止执行工具, 落盘续跑点, 强制收尾
            if self._tool_quota and self._tool_calls >= self._tool_quota:
                self.messages.append({"role": "user", "content": _QUOTA_HINT})
                try:
                    self.save_session()
                except Exception:
                    pass
                _emit_done(last, truncated=True, chain=chain,
                     resume_available=True, session_id=self.session_id,
                     quota_exceeded=True, **self.token_stats())
                return last

            results = []
            for call in calls:
                name = call["name"]
                args = call.get("arguments", {})
                seq += 1
                self._tool_calls += 1
                emit("tool", name=name, args=args, seq=seq, kind=tool_kind(name))
                t0 = time.time()
                try:
                    res = self.registry.execute(name, args)
                    ok = not str(res).startswith(("[tool error]", "[权限拒绝]", "[mcp error]"))
                except Exception as e:
                    res = "[tool error] %s" % e
                    ok = False
                dt_ms = int((time.time() - t0) * 1000)
                # 长结果处理: 优先 LLM 摘要(若开启), 否则硬截断, 防上下文爆炸
                res = _post_process_result(
                    self.client, res,
                    summarize=self._summarize,
                    summarize_max=self._summarize_max,
                    hard_limit=self._tool_result_limit,
                )
                # 主题 D — 工具结果脱敏: 回灌前遮蔽密钥/密码, 防凭证泄露进上下文/会话/日志
                if self._redact:
                    res = _redact(res)
                # 证据链 (provenance): 结果标记带稳定 #seq, 配合文件:行号可溯源每个结论到具体工具调用
                fail_tag = "" if ok else _classify_failure(res)
                marker = f"[tool result: {name} #{seq}]{fail_tag}"
                # 主题 A 闭环 (批次15): 成功结果抽取 JSON 结构, 随 tool_result 事件回写对话流,
                # 供前端直接在气泡内渲染「结构化字段/键名」(不再只给整段文本, 省 token 且更直观)。
                _struct = _extract_struct(res) if ok else None
                emit("tool_result", name=name, args=args, output=res, seq=seq, kind=tool_kind(name),
                     ok=ok, duration_ms=dt_ms,
                     structured=(_struct if (_struct and _struct.get("is_json")) else None))
                results.append(f"{marker}\n{res}")
                chain.append({"seq": seq, "name": name, "kind": tool_kind(name), "ok": ok, "duration_ms": dt_ms, "fail_tag": fail_tag})

            self.messages.append({"role": "user", "content": "\n\n".join(results)})

            # —— 收敛护栏 (修复「已达最大迭代」硬截断) ——
            # 记录本轮工具调用签名; 临近上限或检测到重复死循环时, 注入引导强制模型给结论。
            self._recent_sigs.append(self._call_sig(calls))
            if self.iteration >= self.max_iter - 3:
                self.messages.append({"role": "user", "content": _CONVERGE_HINT})
            elif len(self._recent_sigs) >= 3 and len(set(self._recent_sigs[-3:])) == 1:
                self.messages.append({"role": "user", "content": _LOOP_HINT})
            elif self._reflect_every and self.iteration % self._reflect_every == 0:
                # 反思循环 (主题 B): 周期性自检, 抗空转/促收敛
                self.messages.append({"role": "user", "content": _REFLECT_HINT})

        # —— 长任务断点续跑 (主题 B): 命中上限不再硬失败, 落盘断点供「继续」恢复 ——
        try:
            self.save_session()
        except Exception:
            pass
        _emit_done(last, truncated=True, chain=chain,
             resume_available=True, session_id=self.session_id, **self.token_stats())
        return last

    @staticmethod
    def _call_sig(calls):
        """把一轮的工具调用归一化为可比较签名, 用于死循环检测。"""
        items = []
        for c in calls:
            try:
                a = json.dumps(c.get("arguments", {}), sort_keys=True, ensure_ascii=False)
            except Exception:
                a = str(c.get("arguments", {}))
            items.append((c.get("name"), a))
        return tuple(sorted(items))

    def save_session(self, base_dir=""):
        """把当前 messages 落盘 (若未分配 session_id 则自动分配)。返回 session id。"""
        if not self.session_id:
            from .session import new_session_id
            self.session_id = new_session_id()
        from .session import save_session
        save_session(
            self.session_id,
            self.messages,
            model=getattr(self.client, "model", ""),
            provider=self.provider,
            base_dir=base_dir,
        )
        return self.session_id

    def load_session_messages(self, sid):
        """从磁盘恢复历史消息 (保留 system 在最前)。返回 True 表示成功。"""
        from .session import load_session
        data = load_session(sid)
        if not data:
            return False
        self.session_id = sid
        hist = data.get("messages", [])
        # 保留当前自动生成的 system, 拼接历史中非 system 消息
        body = [m for m in hist if m.get("role") != "system"]
        self.messages = [{"role": "system", "content": self._full_system()}] + body
        return True

    def continue_run(self, user_message=None, on_event=None):
        """在已有执行态上续跑 (复用 messages/迭代), 用于「继续」恢复长任务。

        与 run() 区别: 自动以「续跑提示」作为本轮用户意图, 引导模型基于已有工具结果
        推进到最后结论, 而非重复已做过的调用。活体 loop 对象仍在时直接调用即可。
        """
        nudge = user_message or (
            "请基于已有的全部工具结果继续推进当前任务: 不要重复已做过的调用, "
            "直接朝着最终结论或交付物推进, 直到真正完成。"
        )
        return self.run(nudge, on_event=on_event)

    @classmethod
    def resume_from_disk(cls, sid, client, registry, cfg, user_message=None, on_event=None):
        """从磁盘会话恢复 (新进程 / loop 已丢失时), 再续跑。

        成功返回最终文本; 会话不存在/水合失败返回 None。配合 run() 强制结束时的
        save_session(), 实现跨进程的「继续」断点续跑。
        """
        loop = cls(client, registry, cfg, session_id=sid)
        if not loop.load_session_messages(sid):
            return None
        return loop.continue_run(user_message=user_message, on_event=on_event)

    @staticmethod
    def _parse_tools(text):
        out = []
        for m in TOOL_RE.finditer(text):
            call = _parse_one_tool(m.group(1))
            if call:
                out.append(call)
        return out
