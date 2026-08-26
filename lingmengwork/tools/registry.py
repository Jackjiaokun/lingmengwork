"""工具注册表: 定义 / 分发 / 路径与安全上下文。"""
import os
import re
import json
import time
import datetime
import collections

from .common import ToolError
from . import fs, shell, patch, agent_tools, memory, advanced, review, semantic, decision, dev, office, backup_tools, template_tools, secret_tools, snippet_tools, note_tools, todo_tools
from .undo import get_default_stack, SnapshotStack

# 主题 A — 工具结果缓存 (批次4): 只读搜索类工具同查询的内存缓存 (进程级共享)
_CACHEABLE_TOOLS = {
    "web_search", "code_search", "db_query", "db_list_tables",
    "symbol_search", "grep", "glob", "repo_map", "read_file",
    "fs_read", "list_dir", "diff_view", "semantic_search",
    "generate_project_docs", "impact_analysis",
    "read_office", "data_table",
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
    # ===== 编程生产力 (新增) =====
    {
        "name": "lint_code",
        "description": "静态检查 (对标 ESLint/Flake8 自检): 对文件/目录做零依赖语法校验 + 内建风格扫描(超长行/尾空白/裸except/TODO/调试残留); 若装了 flake8/pylint/eslint/gofmt 则叠加深度检查。写完代码自我体检用。path=文件或目录。",
        "parameters": {"path": "文件或目录", "lang?": "python|js|go|shell(默认按扩展名推断)"},
    },
    {
        "name": "format_code",
        "description": "自动格式化 (对标 Prettier/Black 一键美化): 对文件/目录做自动格式化, 优先 black/autopep8/isort(prettier/gofmt)。check=true 仅预览差异不写入。提交前统一风格用。path=文件或目录, check?=true 预览。",
        "parameters": {"path": "文件或目录", "check?": "true 仅预览差异"},
    },
    {
        "name": "run_server",
        "description": "启动/停止本地开发服务 (对标 Vite/nodemon 内循环): 后台运行 command, 可选 port 做健康检查, 返回 URL/pid/日志路径。action=start(默认)|stop|list; name=服务名(便于停)。Web 开发联调用。",
        "parameters": {"action?": "start|stop|list", "command": "启动命令(如 python -m http.server 8000)", "name?": "服务名", "port?": "健康检查端口", "cwd?": "工作目录"},
    },
    {
        "name": "db_run",
        "description": "数据库查询 (对标 sqlite/数据分析): 执行 SQL。db=sqlite 文件路径(只读打开); 不传 db 则内存库, 可先用 csv 把 CSV 载入为表(表名取文件名)再查。只读探查数据用。query=SQL, db?=sqlite路径, csv?=[csv路径]。",
        "parameters": {"query": "SQL 语句", "db?": "sqlite 文件路径(只读)", "csv?": "[csv 文件路径列表, 作为内存表]"},
    },
    # ===== 办公生产力 (新增) =====
    {
        "name": "read_pdf",
        "description": "抽取 PDF 文本 (对标文档解析): 优先 pypdf/PyPDF2/pdfplumber, 其次 pdftotext(CLI), 最后零依赖 FlateDecode 流扫描尽力抽取。读论文/合同/报告用。path=pdf 路径, max_chars?=截断上限。",
        "parameters": {"path": "PDF 文件路径", "max_chars?": "截断上限(默认20000)"},
    },
    {
        "name": "read_office",
        "description": "抽取 Office 文档文本 (零依赖 zip+XML): 支持 .docx(段落)/.xlsx(表格)/.pptx(幻灯片)。读 Word/Excel/PPT 内容喂给分析或总结用。path=office 文件路径。",
        "parameters": {"path": ".docx/.xlsx/.pptx 文件路径"},
    },
    {
        "name": "make_doc",
        "description": "生成文档 (对标文档自动化): 根据标题+正文生成文件。format=md(Markdown, 默认)/docx(零依赖 zip 构建 Word)。写报告/说明/周报用。path=输出路径, title=标题, body=内容(markdown 风格: # / ## / - )。",
        "parameters": {"path": "输出文件路径", "title": "标题", "body": "正文内容", "format?": "md|docx"},
    },
    {
        "name": "data_table",
        "description": "数据分析 (对标 pandas/Excel 透视): 对 csv/json 数据做统计分析。source=csv/json 路径 或 data=内联JSON。op=summary(默认:形状+描述统计)|head|describe|columns|groupby|chart(零依赖 SVG 柱状图, out?=保存路径)。",
        "parameters": {"source?": "csv/json 路径", "data?": "内联JSON", "op?": "summary|head|describe|columns|groupby|chart", "key?": "分组/分类列", "value?": "数值列", "agg?": "count|sum|mean|min|max", "out?": "图表输出路径"},
    },
    # ===== 备份 / 回滚 (工作区时间点快照) =====
    {
        "name": "backup_create",
        "description": "创建工作区快照备份 (类 Time Machine): 把允许根目录整目录打包为 .zip 存入 <根>/.lmw_backups, 自动排除 .git/__pycache__/node_modules 等。大改动前先拍快照, 出事整体回滚。label?=便于辨识的标签。",
        "parameters": {"label?": "备份标签(便于辨识)"},
    },
    {
        "name": "backup_list",
        "description": "列出已有备份 (ID/标签/时间/文件数/体积/根目录), 回滚前先用它取目标 ID。",
        "parameters": {},
    },
    {
        "name": "backup_rollback",
        "description": "回滚到指定备份: 把该快照解压回各工作区根。id=目标备份 ID(必填); clean=true 额外删除「备份中不存在」的文件(彻底还原到该时间点, 危险, 默认 false 仅覆盖/补回)。",
        "parameters": {"id": "备份 ID (backup_list 取得)", "clean?": "true 彻底还原(删备份外文件)"},
    },
    {
        "name": "backup_delete",
        "description": "删除指定备份以释放空间。id=目标备份 ID(必填)。",
        "parameters": {"id": "备份 ID (backup_list 取得)"},
    },
    # —— 提示词模板 (新增) ——
    {
        "name": "template_list",
        "description": "列出工作区已保存的提示词模板(按分类/名称排序)。无参数。",
        "parameters": {},
    },
    {
        "name": "template_get",
        "description": "读取单个模板的完整内容。id=模板 ID(必填, template_list 取得)。",
        "parameters": {"id": "模板 ID"},
    },
    {
        "name": "template_save",
        "description": "新建或更新提示词模板。name=名称(必填, 同名则更新); content=提示词正文; category=分类(默认其他); id=更新时指定。",
        "parameters": {"name": "模板名称(必填)", "content?": "提示词正文", "category?": "分类", "id?": "更新时指定"},
    },
    {
        "name": "template_delete",
        "description": "删除一个提示词模板。id=模板 ID(必填)。",
        "parameters": {"id": "模板 ID"},
    },
    # —— 密钥保险箱 (新增) ——
    {
        "name": "secret_list",
        "description": "列出密钥保险箱中的条目名称(不返回明文值)。无参数。",
        "parameters": {},
    },
    {
        "name": "secret_get",
        "description": "读取某条密钥的明文值(仅在确有必要时调用)。key=密钥名称(必填)。",
        "parameters": {"key": "密钥名称"},
    },
    {
        "name": "secret_set",
        "description": "设置/更新一条密钥。key=名称(必填); value=明文值; note?=备注。值以轻量本地加密落盘。",
        "parameters": {"key": "密钥名称(必填)", "value": "明文值", "note?": "备注"},
    },
    {
        "name": "secret_delete",
        "description": "删除一条密钥。key=密钥名称(必填)。",
        "parameters": {"key": "密钥名称"},
    },
    # —— 代码片段库 (新增) ——
    {
        "name": "snippet_list",
        "description": "列出工作区已保存的代码片段(按语言/标题排序)。language?=按语言过滤; tag?=按标签过滤。",
        "parameters": {"language?": "语言过滤", "tag?": "标签过滤"},
    },
    {
        "name": "snippet_get",
        "description": "读取单个代码片段的完整内容(含语言/标签)。id=片段 ID(必填, snippet_list 取得)。",
        "parameters": {"id": "片段 ID"},
    },
    {
        "name": "snippet_save",
        "description": "新建或更新代码片段。title=标题(必填, 同名则更新); content=代码正文; language=语言(默认其他); tags=标签(数组或逗号分隔); id=更新时指定。",
        "parameters": {"title": "片段标题(必填)", "content?": "代码正文", "language?": "语言", "tags?": "标签", "id?": "更新时指定"},
    },
    {
        "name": "snippet_delete",
        "description": "删除一个代码片段。id=片段 ID(必填)。",
        "parameters": {"id": "片段 ID"},
    },
    # —— 笔记 (新增) ——
    {
        "name": "note_list",
        "description": "列出工作区已保存的笔记(按更新时间倒序)。无参数。",
        "parameters": {},
    },
    {
        "name": "note_get",
        "description": "读取单条笔记的完整 Markdown 内容。id=笔记 ID(必填, note_list 取得)。",
        "parameters": {"id": "笔记 ID"},
    },
    {
        "name": "note_save",
        "description": "新建或更新笔记(Markdown)。title=标题(必填, 同名则更新); content=正文; id=更新时指定。",
        "parameters": {"title": "笔记标题(必填)", "content?": "正文", "id?": "更新时指定"},
    },
    {
        "name": "note_delete",
        "description": "删除一条笔记。id=笔记 ID(必填)。",
        "parameters": {"id": "笔记 ID"},
    },
    # —— 待办清单 (新增) ——
    {
        "name": "todo_list",
        "description": "列出工作区待办清单, 附带 待办/进行中/已完成 计数。status?=按状态过滤(todo|doing|done)。",
        "parameters": {"status?": "状态过滤"},
    },
    {
        "name": "todo_add",
        "description": "新增一条待办(默认状态 todo)。title=标题(必填); priority?=low|mid|high; due?=截止日期; note?=备注。",
        "parameters": {"title": "待办标题(必填)", "priority?": "low|mid|high", "due?": "截止日期", "note?": "备注"},
    },
    {
        "name": "todo_done",
        "description": "更新某条待办状态。id=待办 ID(必填); status?=doing|done(默认 done, 即勾掉)。",
        "parameters": {"id": "待办 ID", "status?": "doing|done"},
    },
    {
        "name": "todo_delete",
        "description": "删除一条待办。id=待办 ID(必填)。",
        "parameters": {"id": "待办 ID"},
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
    # —— 编程生产力 (新增) ——
    "lint_code": dev.lint_code,
    "format_code": dev.format_code,
    "run_server": dev.run_server,
    "db_run": dev.db_run,
    # —— 办公生产力 (新增) ——
    "read_pdf": office.read_pdf,
    "read_office": office.read_office,
    "make_doc": office.make_doc,
    "data_table": office.data_table,
    # —— 备份 / 回滚 (工作区时间点快照) ——
    "backup_create": backup_tools.backup_create,
    "backup_list": backup_tools.backup_list,
    "backup_rollback": backup_tools.backup_rollback,
    "backup_delete": backup_tools.backup_delete,
    # —— 提示词模板 ——
    "template_list": template_tools.template_list,
    "template_get": template_tools.template_get,
    "template_save": template_tools.template_save,
    "template_delete": template_tools.template_delete,
    # —— 密钥保险箱 ——
    "secret_list": secret_tools.secret_list,
    "secret_get": secret_tools.secret_get,
    "secret_set": secret_tools.secret_set,
    "secret_delete": secret_tools.secret_delete,
    # —— 代码片段库 ——
    "snippet_list": snippet_tools.snippet_list,
    "snippet_get": snippet_tools.snippet_get,
    "snippet_save": snippet_tools.snippet_save,
    "snippet_delete": snippet_tools.snippet_delete,
    # —— 笔记 ——
    "note_list": note_tools.note_list,
    "note_get": note_tools.note_get,
    "note_save": note_tools.note_save,
    "note_delete": note_tools.note_delete,
    # —— 待办清单 ——
    "todo_list": todo_tools.todo_list,
    "todo_add": todo_tools.todo_add,
    "todo_done": todo_tools.todo_done,
    "todo_delete": todo_tools.todo_delete,
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
_READONLY_TOOLS = {"read_file", "list_dir", "glob", "grep", "diff_view", "repo_map", "symbol_search", "review_code", "semantic_search", "impact_analysis", "compare_options", "generate_project_docs", "lint_code", "db_run", "read_pdf", "read_office", "data_table", "backup_list", "template_list", "template_get", "secret_list", "secret_get", "snippet_list", "snippet_get", "note_list", "note_get", "todo_list"}
_WRITE_TOOLS = {"write_file", "edit_file", "apply_patch", "insert_at", "replace_in_files", "undo", "format_code", "make_doc", "backup_create", "backup_rollback", "backup_delete", "template_save", "template_delete", "secret_set", "secret_delete", "snippet_save", "snippet_delete", "note_save", "note_delete", "todo_add", "todo_done", "todo_delete"}
_EXEC_TOOLS = {"run_command", "auto_test", "git_commit", "run_server"}


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
        # 主题 E — 可观测性基础 (批次9): 统一埋点计时 + 记录, 无论成功/失败/拦截都计入统计
        t0 = time.time()
        try:
            res, ok, tag = self._execute_core(name, args)
        except Exception as e:
            # 理论上 _execute_core 内部已兜底; 双保险
            res = f"[tool error] {type(e).__name__}: {e}"
            ok, tag = False, _classify_err(e)
        dur = int((time.time() - t0) * 1000)
        # 主题 A — 工具结果结构化 (批次13): 成功结果尝试抽取 JSON 结构, 随事件入统计
        structured = _extract_struct(res) if ok else None
        _record(name, ok, dur, tag, structured)
        return res

    def _execute_core(self, name, args):
        """核心分发; 返回 (result_text, ok:bool, tag:str|None)。"""
        allowed, reason = self._check_permission(name)
        if not allowed:
            return f"[权限拒绝] {reason}", False, "permission"
        # 主题 A — 工具结果缓存: 只读搜索类同查询命中内存缓存, 省 token/时延
        _ttl = 0
        try:
            _ttl = int((self.cfg or {}).get("agent", {}).get("tool_cache_ttl") or 0)
        except Exception:
            _ttl = 0
        if _ttl > 0 and name in _CACHEABLE_TOOLS:
            _hit = _cache_get(name, args, _ttl)
            if _hit is not None:
                return _hit + "\n[缓存命中]", True, None
        if name == "think":
            return _tool_think(args or {}, self.ctx), True, None
        if name == "undo":
            return _tool_undo(args or {}, self.ctx), True, None
        if name == "todo":
            return agent_tools._tool_todo(args or {}, self.ctx), True, None
        if name == "subagent":
            return agent_tools._tool_subagent(args or {}, self.ctx), True, None
        if name == "memory":
            a = args or {}
            act = a.get("action", "read")
            if act == "read":
                return memory.memory_read(a, self.ctx), True, None
            if act == "write":
                return memory.memory_write(a, self.ctx), True, None
            return memory.memory_append(a, self.ctx), True, None
        # —— 全球领先破坏性操作护栏 (批次7) —— 致命项任何模式硬拦; plan/accept 拦高危项; bypass 高危告警放行
        guard = _guard_destructive(
            name, args, self.permission_mode,
            (self.cfg or {}).get("agent", {}).get("security", {}).get("destructive_guard", "block"),
        )
        if guard is not None:
            gtext, glevel = guard
            self._audit(name, args, False, blocked=True, note=glevel)
            if glevel in ("critical", "high"):
                return gtext, False, "blocked"  # 致命/受限模式高危 -> 硬拦
            # high_warn (bypass 放行但告警): 继续往下执行, 仅记录

        func = _IMPLS.get(name)
        if not func:
            return f"[tool error] 未知工具: {name}", False, "notfound"
        try:
            result = func(args or {}, self.ctx)
            self._audit(name, args, True)
            if _ttl > 0 and name in _CACHEABLE_TOOLS and not str(result).startswith(("[tool error]", "[权限拒绝]")):
                _cache_put(name, args, result, _ttl)
            return result, True, None
        except ToolError as e:
            self._audit(name, args, False)
            return f"[tool error] {e}", False, _classify_err(e)
        except Exception as e:  # 兜底: 让模型看到错误并自我修复
            self._audit(name, args, False)
            return f"[tool error] {type(e).__name__}: {e}", False, _classify_err(e)


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


# —— 主题 E — 可观测性基础 (批次9): 工具调用统计埋点 ——
# 进程级聚合, 面板重启清零 (符合运行期统计语义)。配套 GET /api/stats 展示。
import threading
_STATS_LOCK = threading.Lock()
_STATS = {"tools": {}, "total": {"calls": 0, "ok": 0, "fail": 0}, "recent": []}
_STATS_MAX_RECENT = 50

# 主题 E 可视化深化 (批次12): 每工具 + 全局耗时分布样本(有界), 用于计算 p50/p95/p99 分位
_DUR_MAX = 240                 # 每工具保留最近样本数
_DURATIONS = {}                # name -> collections.deque(maxlen=_DUR_MAX) of dur_ms
_DUR_ALL = collections.deque(maxlen=800)   # 全局样本池

# 错误分类 (与 agent/loop._classify_failure 对齐; 独立实现避免与 loop 循环依赖)
_NET_ERR = ("connectionerror", "timeout", "timed out", "urlerror", "connectionreset",
            "remotedisconnected", "nameresolutionerror", "getaddrinfo", "socket")
_PERM_ERR = ("permissionerror", "accessdenied", "403", "eacces", "denied", "forbidden")
_RES_ERR = ("memoryerror", "outofmemory", "resourceexhausted", "diskfull", "no space", "quotaexceeded")
_NOTFOUND_ERR = ("filenotfounderror", "notfound", "no such file", "404", "does not exist")


def _classify_err(e):
    """把异常分类为 network/permission/resource/notfound/logic (与批次6 _classify_failure 同源)。"""
    s = f"{type(e).__name__}: {e}".lower()
    if any(k in s for k in _NET_ERR):
        return "network"
    if any(k in s for k in _PERM_ERR):
        return "permission"
    if any(k in s for k in _RES_ERR):
        return "resource"
    if any(k in s for k in _NOTFOUND_ERR):
        return "notfound"
    return "logic"


_STRUCT_PREVIEW_ROWS = 8  # 数组类型「表格化对比」预览的最大行数 (控制透传体积)


def _extract_struct(result_text):
    """主题 A — 工具结果结构化 (批次13/本次增强): 尝试从工具结果抽取 JSON 结构。

    返回 dict:
      {is_json:True, kind:'object', n, keys:[...], sample:{k:v,...}}          (对象)
      {is_json:True, kind:'array',  n, keys:[...], preview:[row...], preview_n} (数组, 行级对比)
      {is_json:True, kind:'scalar', n:1, keys:[], value:str}                  (标量)
      {is_json:False}                                                          (非 JSON)
    纯函数, 便于单测; 复杂度 O(n), 不回溯全串。preview/value 仅携带有限预览, 控制 SSE 体积。
    """
    if not result_text or not isinstance(result_text, str):
        return {"is_json": False}
    text = result_text.strip()
    if not text:
        return {"is_json": False}
    data = None
    try:
        data = json.loads(text)
    except Exception:
        # 从文本中定位首个 { / [ 并抽取「括号平衡」子串 (O(n) 扫描)
        s = text.find("{"); a = text.find("[")
        starts = sorted([x for x in (s, a) if x >= 0])
        if not starts:
            return {"is_json": False}
        for st in starts:
            frag = _extract_balanced(text, st)
            if frag:
                try:
                    data = json.loads(frag)
                    break
                except Exception:
                    data = None
    if data is None:
        return {"is_json": False}
    if isinstance(data, dict):
        keys = list(data.keys())
        sample = {k: _truncate_val(data[k]) for k in keys[:12]}
        return {"is_json": True, "kind": "object", "n": len(keys), "keys": keys[:24], "sample": sample}
    if isinstance(data, list):
        n = len(data)
        keys_union = []
        preview = []
        # 抽取前若干元素做「表格化对比」预览 (限制行数/字段数/值长, 控制透传体积)
        for item in data[:_STRUCT_PREVIEW_ROWS]:
            if isinstance(item, dict):
                row = {}
                for k in list(item.keys())[:12]:
                    if k not in keys_union:
                        keys_union.append(k)
                    row[k] = _truncate_val(item[k])
                preview.append(row)
            else:
                if "#" not in keys_union:
                    keys_union.append("#")
                preview.append({"#": _truncate_val(item)})
        # 补全字段并集 (覆盖前 50 个元素, 维持原行为, 供列头完整)
        for item in data[:50]:
            if isinstance(item, dict):
                for k in item.keys():
                    if k not in keys_union:
                        keys_union.append(k)
        return {"is_json": True, "kind": "array", "n": n,
                "keys": keys_union[:24], "preview": preview, "preview_n": len(preview)}
    return {"is_json": True, "kind": "scalar", "n": 1, "keys": [], "value": _truncate_val(data)}


def _extract_balanced(text, start):
    """从 start 起, 按括号深度抽取平衡的子串 (支持字符串内引号/转义)。失败返回 None。"""
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    instring = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if instring:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instring = False
            continue
        if ch == '"':
            instring = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _truncate_val(v, maxlen=80):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    if len(s) > maxlen:
        return s[:maxlen] + "…"
    return s


def _record(name, ok, dur_ms, tag=None, structured=None):
    """记录一次工具调用的统计 (线程安全)。structured 为 _extract_struct 结果(可选)。"""
    with _STATS_LOCK:
        t = _STATS["tools"].setdefault(name, {"calls": 0, "ok": 0, "fail": 0, "total_ms": 0, "fail_by_tag": {}})
        t["calls"] += 1
        t["total_ms"] += dur_ms
        if ok:
            t["ok"] += 1
        else:
            t["fail"] += 1
            if tag:
                t["fail_by_tag"][tag] = t["fail_by_tag"].get(tag, 0) + 1
        _STATS["total"]["calls"] += 1
        if ok:
            _STATS["total"]["ok"] += 1
        else:
            _STATS["total"]["fail"] += 1
        ev = {"name": name, "ok": ok, "ms": dur_ms, "tag": tag, "ts": int(time.time())}
        if structured is not None and structured.get("is_json"):
            ev["structured"] = {"is_json": True, "kind": structured.get("kind"), "n": structured.get("n"),
                                "keys": structured.get("keys", [])[:12]}
        _STATS["recent"].append(ev)
        if len(_STATS["recent"]) > _STATS_MAX_RECENT:
            _STATS["recent"].pop(0)
        _buf = _DURATIONS.setdefault(name, collections.deque(maxlen=_DUR_MAX))
        _buf.append(dur_ms)
        _DUR_ALL.append(dur_ms)


def _pct(vals, p):
    """线性插值分位 (与 numpy 默认一致)。vals 为可迭代数值。"""
    if not vals:
        return 0
    sv = sorted(vals)
    if len(sv) == 1:
        return sv[0]
    k = (len(sv) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sv) - 1)
    if f == c:
        return sv[f]
    return round(sv[f] + (sv[c] - sv[f]) * (k - f))


def get_stats():
    """返回聚合统计: 总调用/成功率/各工具统计(含耗时分位)/最近事件。供 Web /api/stats。"""
    with _STATS_LOCK:
        tools = []
        for n, st in _STATS["tools"].items():
            avg = round(st["total_ms"] / st["calls"], 1) if st["calls"] else 0
            durs = list(_DURATIONS.get(n, []))
            if durs:
                p50 = int(_pct(durs, 50)); p95 = int(_pct(durs, 95)); p99 = int(_pct(durs, 99))
                max_ms = max(durs); min_ms = min(durs)
            else:
                p50 = p95 = p99 = max_ms = min_ms = 0
            tools.append({
                "name": n, "calls": st["calls"], "ok": st["ok"], "fail": st["fail"],
                "avg_ms": avg, "p50_ms": p50, "p95_ms": p95, "p99_ms": p99,
                "max_ms": max_ms, "min_ms": min_ms, "fail_by_tag": dict(st["fail_by_tag"]),
            })
        tools.sort(key=lambda x: (-x["calls"], x["name"]))
        tot = _STATS["total"]
        rate = round(tot["ok"] / tot["calls"], 4) if tot["calls"] else 1.0
        total_ms = sum((st["total_ms"] for st in _STATS["tools"].values()), 0)
        avg_ms = round(total_ms / tot["calls"], 1) if tot["calls"] else 0
        all_durs = list(_DUR_ALL)
        g_p50 = int(_pct(all_durs, 50)); g_p95 = int(_pct(all_durs, 95)); g_p99 = int(_pct(all_durs, 99))
        return {
            "total_calls": tot["calls"], "total_ok": tot["ok"], "total_fail": tot["fail"],
            "success_rate": rate,
            "total_ms": total_ms, "avg_ms": avg_ms,
            "p50_ms": g_p50, "p95_ms": g_p95, "p99_ms": g_p99,
            "tools": tools, "recent": list(_STATS["recent"]),
        }


def reset_stats():
    """清空运行期统计 (供调试/新会话重置)。"""
    with _STATS_LOCK:
        _STATS["tools"].clear()
        _STATS["total"] = {"calls": 0, "ok": 0, "fail": 0}
        _STATS["recent"] = []
        _DURATIONS.clear()
        _DUR_ALL.clear()
