"""Agent 多轮循环: 解析工具调用 -> 执行 -> 回灌 -> 重复, 直至完成或达上限。"""
import json
import re

from .prompt import build_system_prompt
from .context import build_project_context
from .context import build_memory_context

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


def tool_kind(name):
    """把工具名归到可视化类别: write/edit/read/exec/test/review/plan/git/search/other。"""
    if name in _WRITE_TOOLS:
        return "write"
    if name in _READ_TOOLS:
        return "read"
    if name in _EXEC_TOOLS:
        return "exec"
    if name == "review_code":
        return "review"
    if name in _PLANNING_TOOLS:
        return "plan"
    if name == "git_commit":
        return "git"
    if name in ("web_search", "fetch", "db_query", "db_list_tables"):
        return "search"
    return "other"



class AgentLoop:
    def __init__(self, client, registry, cfg, system_prompt_override=None, auto_context=True, session_id=None, provider=""):
        self.client = client
        self.registry = registry
        self.cfg = cfg
        self.max_iter = int(cfg["agent"]["max_iterations"])
        self.session_id = session_id or None
        self.provider = provider
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
        """返回估算的 token 用量与成本(按 sensenova flash-lite 约 0.0001 元/千token 估算)。"""
        inp = self._est_tokens(self.est_input_chars)
        out = self._est_tokens(self.est_output_chars)
        total = inp + out
        # 粗略成本: 输入 0.0001 元/千tok, 输出 0.0002 元/千tok (flash-lite 量级)
        cost = inp / 1000 * 0.0001 + out / 1000 * 0.0002
        return {
            "est_input_tokens": inp,
            "est_output_tokens": out,
            "est_total_tokens": total,
            "est_cost_cny": round(cost, 5),
        }

    def _full_system(self):
        parts = [self.system_prompt]
        if self.project_context:
            parts.append(self.project_context)
        if self.memory_context:
            parts.append(self.memory_context)
        return "\n\n".join(parts)

    def reset(self):
        self.messages = [{"role": "system", "content": self._full_system()}]
        # 同时清零 token/成本估算, 让 /clear 后状态条真正归零
        self.est_input_chars = 0
        self.est_output_chars = 0

    def run(self, user_message, on_event=None):
        """执行一轮用户请求, 返回最终文本。on_event(type, kw) 用于流式展示。"""
        self.messages.append({"role": "user", "content": user_message})

        def emit(type_, **kw):
            if on_event:
                on_event(type_, kw)

        last = ""
        seq = 0
        chain = []  # 工具调用链 (时序): [{seq, name, kind, ok}] 供前端可视化全链路
        for _ in range(self.max_iter):
            self.iteration += 1
            # 估算 input token (本轮发给模型的全部上下文)
            self.est_input_chars += sum(len(m.get("content", "")) for m in self.messages)
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
                emit("done", text=assistant, truncated=False, chain=chain, **self.token_stats())
                return assistant

            results = []
            for call in calls:
                name = call["name"]
                args = call.get("arguments", {})
                seq += 1
                emit("tool", name=name, args=args, seq=seq, kind=tool_kind(name))
                try:
                    res = self.registry.execute(name, args)
                    ok = not str(res).startswith(("[tool error]", "[权限拒绝]", "[mcp error]"))
                except Exception as e:
                    res = "[tool error] %s" % e
                    ok = False
                emit("tool_result", name=name, args=args, output=res, seq=seq, kind=tool_kind(name), ok=ok)
                results.append(f"[tool result: {name}]\n{res}")
                chain.append({"seq": seq, "name": name, "kind": tool_kind(name), "ok": ok})

            self.messages.append({"role": "user", "content": "\n\n".join(results)})

            # —— 收敛护栏 (修复「已达最大迭代」硬截断) ——
            # 记录本轮工具调用签名; 临近上限或检测到重复死循环时, 注入引导强制模型给结论。
            self._recent_sigs.append(self._call_sig(calls))
            if self.iteration >= self.max_iter - 3:
                self.messages.append({"role": "user", "content": _CONVERGE_HINT})
            elif len(self._recent_sigs) >= 3 and len(set(self._recent_sigs[-3:])) == 1:
                self.messages.append({"role": "user", "content": _LOOP_HINT})

        emit("done", text=last, truncated=True, chain=chain, **self.token_stats())
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

    @staticmethod
    def _parse_tools(text):
        out = []
        for m in TOOL_RE.finditer(text):
            call = _parse_one_tool(m.group(1))
            if call:
                out.append(call)
        return out
