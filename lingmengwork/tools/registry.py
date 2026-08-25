"""工具注册表: 定义 / 分发 / 路径与安全上下文。"""
import os
import re
import json
import time
import datetime

from .common import ToolError
from . import fs, shell, patch, agent_tools, memory, advanced, review, semantic, decision
from .undo import get_default_stack, SnapshotStack

# 主题 A — 工具结果缓存 (批次4): 只读搜索类工具同查询的内存缓存 (进程级共享)
_CACHEABLE_TOOLS = {
    "web_search", "code_search", "db_query", "db_list_tables",
    "symbol_search", "grep", "glob", "repo_map", "read_file",
    "fs_read", "list_dir", "diff_view", "semantic_search",
    "generate_project_docs", "impact_analysis",
}
_RESULT_CACHE = {}  # {(name, args_json): (value, expire_ts)}


def _cache_key(name, args):
    try:
        a = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    except Exception:
        a = str(args)
    return (name, a)


def _cache_get(name, args, ttl):
    if ttl <= 0:
        return None
    key = _cache_key(name, args)
    entry = _RESULT_CACHE.get(key)
    if entry is None:
        return None
    val, exp = entry
    if exp and time.time() > exp:
        _RESULT_CACHE.pop(key, None)
        return None
    return val


def _cache_put(name, args, val, ttl):
    if ttl <= 0:
        return
    _RESULT_CACHE[_cache_key(name, args)] = (val, time.time() + ttl)


# 工具 schema: 用于系统提示词与 Web 控制台展示
TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": "读取文件内容, 可选 offset/limit 切片行。",
        "parameters": {"path": "路径", "offset?": "起始行(0基)", "limit?": "读取行数"},
    },
    {
        "name": "write_file",
        "description": "创建或覆盖写入文件 (自动建目录)。",
        "parameters": {"path": "路径", "content": "完整内容"},
    },
    {
        "name": "edit_file",
        "description": "在文件中替换一段文本 (默认仅替换首次出现)。",
        "parameters": {"path": "路径", "old_string": "待替换", "new_string": "新文本", "replace_all?": "true 全替换"},
    },
    {
        "name": "diff_view",
        "description": "预览对某文件的拟改动 (unified diff), 不真正写入; 确认无误后再调用 edit_file/write_file。改前先用它核对。",
        "parameters": {"path": "路径", "old_string?": "待替换片段", "new_string?": "新片段", "replace_all?": "true 全替换"},
    },
    {
        "name": "apply_patch",
        "description": "整文件多 hunk 智能补丁 (仿 Aider): 一次提交多个 {path, old, new} 块, 先全部校验再原子应用。适合一份文件的多处分散改动, 比 edit_file 更稳。",
        "parameters": {"blocks": "[{path, old, new}, ...] 列表"},
    },
    {
        "name": "insert_at",
        "description": "在文件指定行号前精确插入内容 (仿 Cursor 精确插入)。line 从 0 计; 越界则末尾追加。",
        "parameters": {"path": "路径", "line": "行号(0基)", "content": "插入内容"},
    },
    {
        "name": "replace_in_files",
        "description": "跨文件批量正则替换 (仿 IDE 全局替换)。pattern/replacement + 可选 path/glob/ignore_case/max_files。",
        "parameters": {"pattern": "正则", "replacement": "替换串", "path?": "起始目录", "glob?": "如 *.py", "ignore_case?": "true", "max_files?": "上限"},
    },
    {
        "name": "list_dir",
        "description": "列出目录内容。",
        "parameters": {"path?": "目录, 默认当前根"},
    },
    {
        "name": "glob",
        "description": "按通配符查找文件 (支持 ** 递归)。",
        "parameters": {"pattern": "如 **/*.py"},
    },
    {
        "name": "grep",
        "description": "在文件中按正则搜索文本。",
        "parameters": {"pattern": "正则", "path?": "起始目录", "ignore_case?": "true", "max_matches?": "上限"},
    },
    {
        "name": "run_command",
        "description": "执行 shell 命令 (受危险命令拦截与沙箱约束)。",
        "parameters": {"command": "命令字符串"},
    },
    {
        "name": "think",
        "description": "在工具调用间隙做结构化推理/规划 (extended thinking)。内容仅回灌给你自己, 不写入文件、不展示给用户; 用于复杂任务的中间推演。",
        "parameters": {"thought": "你的推理/计划内容"},
    },
    {
        "name": "undo",
        "description": "回滚最近的文件改动 (仿 Aider /undo)。参数 path 回滚指定文件; 不传则回滚最近一个被改文件。返回恢复摘要。",
        "parameters": {"path?": "要回滚的文件, 留空回滚最近一次改动"},
    },
    {
        "name": "todo",
        "description": "任务清单 (仿 Cline TodoWrite): 复杂任务前先建可勾选清单。action=set(整体替换 items 列表)/update(更新 index 项 status)/get(查看)。",
        "parameters": {"action": "set|update|get", "items?": "[{content,status}]", "index?": "项序号", "status?": "pending|in_progress|completed"},
    },
    {
        "name": "subagent",
        "description": "派发子任务给独立 AgentLoop (仿 Codex 子代理): 子代理自主调用工具调研/编码, 返回结果给主 Agent。用于先调研再动手。provider? 指定通道。prompts 传列表则多线程并发多个子任务。",
        "parameters": {"prompt?": "单个子任务描述", "prompts?": "[子任务列表] 并发", "provider?": "通道名"},
    },
    {
        "name": "memory",
        "description": "跨会话项目记忆 (仿 Claude Code 记忆): 读写项目根 MEMORY.md, 记住约定/偏好/踩坑。action=read|write|append。",
        "parameters": {"action": "read|write|append", "content?": "写入/追加内容"},
    },
    {
        "name": "auto_test",
        "description": "运行测试/构建并结构化解析结果 (Aider auto-test / Devin 自愈范式): 返回通过/失败/错误计数 + 失败用例 + traceback 摘要。Agent 据此自动修复代码并再次调用, 形成「红→绿」自愈闭环。command? 自定义命令(默认自动探测 pytest/npm test)。path? 限定目录/文件。",
        "parameters": {"command?": "测试命令", "path?": "限定目录/文件"},
    },
    {
        "name": "repo_map",
        "description": "生成仓库符号地图 (Aider repo-map 范式): 扫描代码文件提取 class/def/函数签名及行号, 给 LLM 仓库结构认知, 大仓库编码前先调用。零依赖。max_files?/max_symbols?/max_depth? 限幅, 自动尊重 .gitignore。",
        "parameters": {"path?": "目录", "max_files?": "文件上限", "max_symbols?": "每文件符号上限", "max_depth?": "扫描深度上限"},
    },
    {
        "name": "symbol_search",
        "description": "跨仓库按名称检索符号定义位置 (仿 LSP 跳转定义): 返回 path:Lline: 签名。改代码前先看定义/签名一致性。name=符号名(子串), regex?=true 时按正则, glob?=文件过滤(*.py), limit?=上限。",
        "parameters": {"name": "符号名(子串/正则)", "regex?": "true 按正则", "glob?": "如 *.py", "limit?": "上限"},
    },
    {
        "name": "git_commit",
        "description": "智能提交 (Claude Code /commit 范式): 自动 git add + 抓取 diff 摘要; 不传 message 则返回摘要供你生成, 传 message 则直接提交(保留 hook, 不 --no-verify)。push?=true 额外推送。",
        "parameters": {"message?": "提交信息", "add_all?": "true 全暂存", "push?": "true 推送"},
    },
    {
        "name": "review_code",
        "description": "代码评审自评估 (Critic Loop, 领先一代质量门禁): 对文件/代码片段/diff 做零依赖静态评审(py_compile 语法 + 规则扫描), 可选叠加 LLM 评审子代理。返回 VERDICT(approve|revise)/SCORE/ISSUES/SUGGESTIONS。写完关键代码后调用自检, verdict=revise 则改后再 review, 形成写-审-改闭环。target=文件路径或代码片段, focus?=评审焦点, critic?=是否叠加 LLM(默认 true)。",
        "parameters": {"target": "文件路径或代码片段/diff", "focus?": "评审焦点(安全/性能/...)", "critic?": "默认 true 叠加 LLM 评审"},
    },
    {
        "name": "semantic_search",
        "description": "语义近似检索 (零依赖本地向量召回, 对标 Cody/Cursor 找代码): 当你只知道「要找做 X 的代码/文档」而不知精确符号名时, 用 TF-IDF 向量 + 余弦相似度召回 top-k 最相关片段。支持中文(逐字+bigram)。索引持久化 <root>/.lmw_index 并按 mtime 增量复用。query=意图(中/英), scope?=code|docs|all(默认 all), top_k?=返回条数(默认8), glob?=文件过滤, rebuild?=true 强制重建索引。命中后用 read_file/grep 接力精确定位。",
        "parameters": {"query": "要找的代码/文档意图(中/英)", "scope?": "code|docs|all", "top_k?": "返回条数", "glob?": "文件过滤", "rebuild?": "true 强制重建索引"},
    },
    {
        "name": "impact_analysis",
        "description": "变更影响分析 (对标重构前回归范围评估): 输入符号名 symbol, 扫描仓库定位其定义位置 + 所有调用方/使用点, 按文件聚合调用数量并列出调用点明细。大重构/重命名前先调它看清回归范围。symbol=符号名(精确匹配, 支持中文), glob?=文件过滤(*.py), root?=扫描根(默认当前根)。",
        "parameters": {"symbol": "符号名(精确匹配)", "glob?": "如 *.py", "root?": "扫描根目录"},
    },
    {
        "name": "compare_options",
        "description": "多方案对比 (对标复杂决策先比后落): 输入任务 task + 2~N 个候选方案 options(每项含 title/description/pros/cons/effort/risk), 输出结构化对比表 + 建议方案(评分=优点数-缺点数-0.5×(工作量+风险))。复杂任务先比对权衡再落地, 避免盲目选边。",
        "parameters": {"task?": "任务描述", "options": "[{title,description?,pros?,cons?,effort?,risk?}, ...]"},
    },
    {
        "name": "generate_project_docs",
        "description": "项目文档自动生成 (对标 CLAUDE.md/AGENTS.md 引导): 扫描仓库生成草稿, 含技术栈(按文件数统计语言)/关键目录/入口点/测试命令/已有约定(README/LICENSE 等)。返回 Markdown 草稿, 供人工复核后保存为 CLAUDE.md/AGENTS.md, 让后续会话自动获得项目认知。format?=claude_md|agents_md, root?=扫描根。",
        "parameters": {"format?": "claude_md|agents_md", "root?": "扫描根目录"},
    },
]

# 名称 -> 实现函数 (签名: func(args, ctx) -> str)
_IMPLS = {
    "read_file": fs.read_file,
    "write_file": fs.write_file,
    "edit_file": fs.edit_file,
    "diff_view": fs.diff_view,
    "apply_patch": patch.apply_patch,
    "insert_at": fs.insert_at,
    "replace_in_files": fs.replace_in_files,
    "list_dir": fs.list_dir,
    "glob": fs.glob_files,
    "grep": fs.grep_files,
    "run_command": shell.run_command,
    "think": None,   # 特殊处理: 由 Registry.execute 内联
    "undo": None,    # 特殊处理: 操作快照栈
    "todo": None,    # 特殊处理: 任务清单
    "subagent": None,  # 特殊处理: 子代理派发
    "memory": None,  # 特殊处理: 跨会话记忆
    "auto_test": advanced.auto_test,
    "repo_map": advanced.repo_map,
    "symbol_search": advanced.symbol_search,
    "git_commit": advanced.git_commit,
    "review_code": review._tool_review_code,
    "semantic_search": semantic.semantic_search,
    "impact_analysis": decision.impact_analysis,
    "compare_options": decision.compare_options,
    "generate_project_docs": decision.generate_project_docs,
}


def _tool_think(args, ctx):
    thought = (args.get("thought") or "").strip()
    if not thought:
        return "[think] 空思考, 已忽略。"
    n = len(thought)
    return f"[think] 已记录推理 ({n} 字符), 仅回灌上下文, 不改动任何文件。"


def _tool_undo(args, ctx):
    stack = ctx.get("undo_stack") or get_default_stack()
    path = args.get("path")
    if path:
        resolved = str(fs.resolve_path(ctx["roots"], path).resolve())
        old = stack.undo_file(resolved)
        if old == "_EMPTY_":
            return f"[undo] 文件 {path} 无快照, 无法回滚。"
        # 恢复
        rp = fs.resolve_path(ctx["roots"], path)
        if old is None:
            # 原本不存在 -> 删除新建的文件
            try:
                rp.unlink()
            except Exception:
                pass
            return f"[undo] 已回滚 {path} (恢复为: 文件不存在, 已删除新建)。"
        rp.write_text(old, encoding="utf-8")
        return f"[undo] 已回滚 {path} (恢复为改动前内容, {len(old)} 字符)。"
    last = stack.undo_last()
    if not last:
        return "[undo] 没有可回滚的改动。"
    rpath, old = last
    try:
        rp = fs.resolve_path(ctx["roots"], rpath)
    except Exception:
        rp = None
    if rp is None:
        return f"[undo] 已回滚 {rpath} (但路径已不在允许根内, 跳过写回)。"
    if old is None:
        try:
            rp.unlink()
        except Exception:
            pass
        return f"[undo] 已回滚 {rpath} (恢复为: 文件不存在)。"
    rp.write_text(old, encoding="utf-8")
    return f"[undo] 已回滚 {rpath} (恢复为改动前内容, {len(old)} 字符)。"


# 权限模式: 工具访问分层 (仿 Claude Code 权限)
# plan            : 仅只读探查 (list/read/grep/glob/diff_view), 禁写/编辑/执行
# acceptEdits     : 允许文件读写/编辑(diff_view 预览仍建议), 但 run_command 默认拦截
# bypassPermissions: 全放开 (等同 dangerously, 由 deny_patterns 仍拦危险命令)
_READONLY_TOOLS = {"read_file", "list_dir", "glob", "grep", "diff_view", "repo_map", "symbol_search", "review_code", "semantic_search", "impact_analysis", "compare_options", "generate_project_docs"}
_WRITE_TOOLS = {"write_file", "edit_file", "apply_patch", "insert_at", "replace_in_files", "undo"}
_EXEC_TOOLS = {"run_command", "auto_test", "git_commit"}


# —— 全球领先破坏性操作护栏 (批次7) ——
# 致命模式: 任何权限模式都硬拦截(不可逆/可能破坏系统或他人远程数据)
_DESTRUCT_CRITICAL = (
    "rm -rf /", "rm -rf ~", "rm -rf /*", "rm -rf ~/",
    "mkfs", "dd if=", "dd of=/dev", "> /dev/sda", "> /dev/sdb", "> /dev/sd",
    "chmod -r 777 /", "chmod -r 777 ~", "chmod 777 /",
    "shutdown", "reboot", "halt", "poweroff",
    "git push --force", "git push -f ", "git push -f$",
    "format c:", "format d:", ":(){", "fork bomb",
)
# 高危模式: plan/acceptEdits 模式拦截; bypass 模式告警放行(注入确认提示)
_DESTRUCT_HIGH = (
    "rm -rf", "git reset --hard", "git clean -f", "git clean -fd",
    "drop table", "truncate table", "delete from", "truncate ",
    "mv / ", "mv /etc",
)


def _args_text(args):
    """把工具参数摊平成小写文本, 便于危险模式扫描。"""
    if not args:
        return ""
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args, ensure_ascii=False)
    except Exception:
        return str(args)


def _guard_destructive(name, args, mode, enabled="block"):
    """破坏性操作护栏。返回 None 表示放行; 否则 (提示文本, 级别)。

    级别: critical=致命(任何模式硬拦), high=高危(受限模式硬拦), high_warn=bypass 放行但告警。
    """
    if enabled != "block":
        return None
    if name in _READONLY_TOOLS:
        return None  # 只读工具(含 semantic_search/review_code)永不拦
    text = _args_text(args).lower()
    if not text.strip():
        return None
    # 远程代码执行管道: curl/wget ... | sh 等 (致命)
    if re.search(r"\|\s*(sh|bash|pwsh|powershell|cmd)\b", text) and ("curl" in text or "wget" in text):
        return ("[安全护栏] 已拦截「下载即执行」管道(curl/wget ... | sh), 这是远程代码执行高危模式。请先把脚本下载到本地审查, 再显式执行。", "critical")
    for pat in _DESTRUCT_CRITICAL:
        if pat in text:
            return (f"[安全护栏] 已拦截致命操作(匹配: {pat.strip()}); 该操作不可逆且可能破坏系统/他人数据, 任何模式均不允许。", "critical")
    for pat in _DESTRUCT_HIGH:
        if pat in text:
            if mode in ("plan", "acceptEdits"):
                return (f"[安全护栏][{mode}] 已拦截高危写操作(匹配: {pat.strip()}); 当前模式禁止破坏性写。如需执行, 切到 bypassPermissions 并显式确认。", "high")
            return (f"[安全护栏][警告] 检测到高危写操作(匹配: {pat.strip()}); 执行后不可逆, 请确认你确实要这么做。", "high_warn")
    return None


# 审计日志脱敏 (轻量, 避免循环依赖): 遮蔽键值对中的敏感值
_SECRET_AUDIT_RE = re.compile(
    r'(?i)("?(?:password|passwd|pwd|token|secret|api[_-]?key|authorization|access[_-]?key)"?\s*[:=]\s*["\']?)[^\s"\',}]{4,}'
)
def _redact_audit(text):
    if not text:
        return text
    return _SECRET_AUDIT_RE.sub(lambda m: f"{m.group(1)}***REDACTED***", text)


class Registry:
    def __init__(self, roots, deny_patterns=None, dangerously=False, cwd=None, undo_stack=None, permission_mode="bypassPermissions", cfg=None, clients=None):
        self.roots = roots
        self.undo_stack = undo_stack or get_default_stack()
        self.permission_mode = permission_mode
        self.cfg = cfg
        self.clients = clients or {}
        # 注意: dangerously_run_commands 始终来自 cfg (默认 False), 不受 bypassPermissions 强制开启。
        # bypassPermissions 仅放宽「工具调用权限」, 危险命令护栏 (deny_patterns) 始终生效。
        self.ctx = {
            "roots": roots,
            "deny_patterns": deny_patterns or [],
            "dangerously_run_commands": dangerously,
            "cwd": cwd,
            "undo_stack": self.undo_stack,
            "permission_mode": permission_mode,
            "registry": self,
            "cfg": self.cfg,
            "clients": self.clients,
        }

    def set_permission_mode(self, mode):
        self.permission_mode = mode
        self.ctx["permission_mode"] = mode
        # 不强制覆盖 dangerously_run_commands (护栏始终优先)

    def _check_permission(self, name):
        """按当前模式判断是否允许该工具。返回 (allowed, reason)。"""
        mode = self.permission_mode
        if mode == "bypassPermissions":
            return True, ""
        if name in ("think", "undo", "todo", "subagent", "memory"):
            return True, ""
        if mode == "plan":
            if name in _READONLY_TOOLS:
                return True, ""
            return False, f"当前为「计划模式」, 禁止 {name} (只读探查工具可用: read/list/grep/glob/diff_view)。"
        if mode == "acceptEdits":
            if name in _READONLY_TOOLS or name in _WRITE_TOOLS:
                return True, ""
            if name in _EXEC_TOOLS:
                return False, "当前为「自动接受编辑」模式, run_command 需在 bypassPermissions 下执行。"
        return False, f"模式 {mode} 不允许工具 {name}。"

    def list_tools(self):
        # 按模式过滤展示可用工具
        if self.permission_mode == "bypassPermissions":
            return TOOL_SCHEMAS
        out = []
        for t in TOOL_SCHEMAS:
            allowed, _ = self._check_permission(t["name"])
            if allowed:
                out.append(t)
        return out

    def execute(self, name, args):
        allowed, reason = self._check_permission(name)
        if not allowed:
            return f"[权限拒绝] {reason}"
        # 主题 A — 工具结果缓存: 只读搜索类同查询命中内存缓存, 省 token/时延
        _ttl = 0
        try:
            _ttl = int((self.cfg or {}).get("agent", {}).get("tool_cache_ttl") or 0)
        except Exception:
            _ttl = 0
        if _ttl > 0 and name in _CACHEABLE_TOOLS:
            _hit = _cache_get(name, args, _ttl)
            if _hit is not None:
                return _hit + "\n[缓存命中]"
        if name == "think":
            return _tool_think(args or {}, self.ctx)
        if name == "undo":
            return _tool_undo(args or {}, self.ctx)
        if name == "todo":
            return agent_tools._tool_todo(args or {}, self.ctx)
        if name == "subagent":
            return agent_tools._tool_subagent(args or {}, self.ctx)
        if name == "memory":
            return memory.memory_read(args or {}, self.ctx) if (args or {}).get("action", "read") == "read" else (
                memory.memory_write(args or {}, self.ctx) if (args or {}).get("action") == "write" else
                memory.memory_append(args or {}, self.ctx)
            )
        # —— 全球领先破坏性操作护栏 (批次7) —— 致命项任何模式硬拦; plan/accept 拦高危项; bypass 高危告警放行
        guard = _guard_destructive(
            name, args, self.permission_mode,
            (self.cfg or {}).get("agent", {}).get("security", {}).get("destructive_guard", "block"),
        )
        if guard is not None:
            gtext, glevel = guard
            self._audit(name, args, False, blocked=True, note=glevel)
            if glevel in ("critical", "high"):
                return gtext  # 致命/受限模式高危 -> 硬拦
            # high_warn (bypass 放行但告警): 继续往下执行, 仅记录

        func = _IMPLS.get(name)
        if not func:
            return f"[tool error] 未知工具: {name}"
        try:
            result = func(args or {}, self.ctx)
            self._audit(name, args, True)
            if _ttl > 0 and name in _CACHEABLE_TOOLS and not str(result).startswith(("[tool error]", "[权限拒绝]")):
                _cache_put(name, args, result, _ttl)
            return result
        except ToolError as e:
            self._audit(name, args, False)
            return f"[tool error] {e}"
        except Exception as e:  # 兜底: 让模型看到错误并自我修复
            self._audit(name, args, False)
            return f"[tool error] {type(e).__name__}: {e}"


    def _audit(self, name, args, ok, blocked=False, note=""):
        """写操作审计日志 (批次7): 落盘 <root>/.lmw_audit.log, 便于合规追溯。脱敏后写入。"""
        try:
            sec = (self.cfg or {}).get("agent", {}).get("security", {})
            if not sec.get("audit_log", True):
                return
            root = self.roots[0] if self.roots else "."
            logp = os.path.join(root, ".lmw_audit.log")
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            raw = json.dumps(args, ensure_ascii=False) if args else ""
            raw = _redact_audit(raw)[:600]
            with open(logp, "a", encoding="utf-8") as f:
                f.write(f"{ts}\t{self.permission_mode}\t{name}\tok={ok}\tblocked={blocked}\tnote={note}\t{raw}\n")
        except Exception:
            pass


def build_registry(cfg, base_dir=None, permission_mode="bypassPermissions", clients=None):
    from ..config import resolve_roots
    from ..config import build_clients

    roots = resolve_roots(cfg, base_dir=base_dir)
    sec = cfg["agent"]["security"]
    if clients is None:
        try:
            clients = build_clients(cfg)
        except Exception:
            clients = {}
    # 接入外部 MCP 工具 (零依赖 stdio JSON-RPC; 默认无配置则不 spawn)
    _populate_mcp(cfg)
    return Registry(
        roots=roots,
        deny_patterns=sec.get("deny_patterns", []),
        dangerously=sec.get("dangerously_run_commands", False),
        cwd=base_dir,
        permission_mode=permission_mode,
        cfg=cfg,
        clients=clients,
    )
def _populate_mcp(cfg):
    """把配置的外部 MCP 工具注入注册表 (失败静默, 不阻断主流程)。"""
    try:
        from . import mcp as _mcp
        _mcp.populate_registry(cfg)
    except Exception:
        pass
