"""工具注册表: 定义 / 分发 / 路径与安全上下文。"""
import os
import re
import json
import time
import datetime
import collections

from .common import ToolError
from . import fs, shell, patch, agent_tools, memory, advanced, review, semantic, decision, dev, office, backup_tools, template_tools, secret_tools, snippet_tools, note_tools, todo_tools, suite_extended, suite_knowledge, suite_productivity, suite_automation, suite_rnd, suite_phase96, suite_phase97, suite_phase98, suite_phase99, suite_phase100, suite_phase101, suite_phase102, suite_phase103, suite_phase104, suite_phase105, suite_phase106, suite_phase107, suite_phase108, suite_phase109
from .undo import get_default_stack, SnapshotStack

# 主题 A — 工具结果缓存 (批次4): 只读搜索类工具同查询的内存缓存 (进程级共享)
_CACHEABLE_TOOLS = {
    "web_search", "code_search", "db_query", "db_list_tables",
    "symbol_search", "grep", "glob", "repo_map", "read_file",
    "fs_read", "list_dir", "diff_view", "semantic_search",
    "generate_project_docs", "impact_analysis",
    "read_office", "data_table",
    "image_understand", "ocr", "explain_code", "security_scan",
    "summarize", "pdf_extract", "data_analysis", "deep_review",
    "code_metrics", "text_compare", "db_schema_doc", "form_validate", "code_search_semantic",
    "db_diff", "code_search_ast", "json_query", "env_check",
    "webhook_verify", "sql_format", "csv_diff", "json_schema_validate", "release_tag", "log_tail", "password_generate",
    "sql_explain", "csv_to_json", "hash_file", "cron_parse", "text_diff", "yaml_query", "sql_lint", "json_schema_gen", "cron_next_n", "diff_patch", "yaml_merge", "hash_verify",
    "secret_audit", "dep_check", "license_check", "perm_diff", "xml_query", "toml_query",
    "xml_to_json", "toml_to_json", "json_patch", "sbom_gen", "dep_graph",
    "yaml_to_json", "json_to_yaml", "xml_to_csv", "toml_to_yaml", "license_compat", "dep_outdated", "file_classify",
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
    # ===== 联网与 API (对标 豆包/千问/dsh 联网) =====
    {
        "name": "web_fetch",
        "description": "抓取网页 URL 并抽取可读正文(去脚本/样式/标签)。离线/沙箱限制时返回失败提示。url 必填。",
        "parameters": {"url": "目标网页 URL(http/https)", "max_chars?": "截断上限(默认20000)"},
    },
    {
        "name": "web_search",
        "description": "联网搜索(零依赖 DuckDuckGo lite 抓取), 返回标题/摘要/链接。无网络时优雅降级。query 必填。对标豆包/千问联网搜索。",
        "parameters": {"query": "搜索关键词", "limit?": "结果条数(默认8)"},
    },
    {
        "name": "http_request",
        "description": "调用任意 REST API(任意方法)。method 默认 GET; 支持 headers/body/json, 返回状态+响应。对标工作流集成。",
        "parameters": {"url": "接口地址", "method?": "GET|POST|PUT|DELETE", "headers?": "请求头 dict", "body?": "请求体(字符串/字节)", "json?": "JSON 请求体", "max_chars?": "响应截断(默认4000)"},
    },
    # ===== 完整 Git 工作流 (对标 Claude Code / Codex) =====
    {
        "name": "git_status",
        "description": "查看工作区状态(分支+改动清单), 仿 git status --short -b。非仓库返回提示。",
        "parameters": {},
    },
    {
        "name": "git_diff",
        "description": "查看差异。cached=true 看暂存区; path 限定文件; stat=true 仅统计。非仓库返回提示。",
        "parameters": {"cached?": "true 看暂存区", "path?": "限定文件", "stat?": "true 仅统计", "max_chars?": "截断(默认20000)"},
    },
    {
        "name": "git_log",
        "description": "查看提交历史(oneline 格式)。n 限制条数(默认20)。",
        "parameters": {"n?": "条数(默认20)"},
    },
    {
        "name": "git_branch",
        "description": "列出分支。a=true 含远程分支。",
        "parameters": {"a?": "true 含远程"},
    },
    {
        "name": "git_checkout",
        "description": "切换/新建分支。ref 必填; create=true 新建。改动前先 git_status 看清楚。",
        "parameters": {"ref": "分支名/commit", "create?": "true 新建分支"},
    },
    {
        "name": "git_stash",
        "description": "贮藏工作区。action=push(默认)|list|pop|show。临时切换分支前先 stash。",
        "parameters": {"action?": "push|list|pop|show", "message?": "push 备注"},
    },
    {
        "name": "git_pr_draft",
        "description": "生成 PR 草稿(标题/改动范围/提交列表/差异摘要), 不推送。可选 path 落盘 markdown。对标 Codex PR 草稿。",
        "parameters": {"base?": "对比基线(默认 main)", "path?": "落盘 markdown 路径"},
    },
    # ===== 多模态真实生成 (对标 豆包/千问 图文音视频) =====
    {
        "name": "image_generate",
        "description": "文生图/图生图/超分。委托适配层: 有图生成 key 走远程真生成, 否则本地 Pillow 真实信息图。prompt 必填; mode=gen|inpaint|upscale; image_path 参考图。",
        "parameters": {"prompt": "画面描述", "mode?": "gen|inpaint|upscale", "image_path?": "参考图(用于 inpaint/upscale)"},
    },
    {
        "name": "image_understand",
        "description": "图像理解(零依赖): 抽取尺寸/格式/主色等元信息 + 启发式描述。path 必填。接入视觉 LLM 后可得语义描述。",
        "parameters": {"path": "图像路径"},
    },
    {
        "name": "tts",
        "description": "语音合成(TTS)。委托适配层: 优先 edge_tts 真实 MP3, 否则降级文字稿+声波占位图。text 必填; voice/rate/pitch 语音参数。",
        "parameters": {"text": "要朗读的文本", "voice?": "音色", "rate?": "语速", "pitch?": "音高"},
    },
    {
        "name": "transcribe",
        "description": "语音转写(STT)。需本地引擎(whisper/SpeechRecognition), 缺失则优雅提示。path 必填。对标豆包/千问语音转写。",
        "parameters": {"path": "音频文件路径", "language?": "语言(默认 zh-CN)"},
    },
    {
        "name": "video_generate",
        "description": "文生视频/图生视频/剪辑合成。委托适配层: 有视频 key 走远程 MP4, 否则本地 Pillow 真实 GIF 动图。prompt 必填; mode=gen|img2video|clips。",
        "parameters": {"prompt": "视频描述", "mode?": "gen|img2video|clips", "image_path?": "参考图/图序列(逗号分隔)"},
    },
    # ===== 文档全家桶 (对标 千问办公/豆包 文档) =====
    {
        "name": "make_ppt",
        "description": "生成 PPTX 演示文稿(零依赖 zip+XML 构建)。slides=[{title,bullets}] 或 body 分页 markdown。path 必填。",
        "parameters": {"path": "输出 .pptx 路径", "title?": "标题", "slides?": "幻灯片列表", "body?": "分页 markdown"},
    },
    {
        "name": "make_xlsx",
        "description": "生成 XLSX 表格(零依赖 zip+XML)。data=二维数组或对象列表。path/sheet 可选。对标 Excel 生成。",
        "parameters": {"path": "输出 .xlsx 路径", "data": "二维数组或对象列表", "sheet?": "表名(默认 Sheet1)"},
    },
    {
        "name": "make_pdf",
        "description": "生成 PDF 文档(零依赖最小 PDF, 多页文本)。title/body(markdown 或纯文本)。path 必填。对标 PDF 导出。",
        "parameters": {"path": "输出 .pdf 路径", "title?": "标题", "body": "正文(markdown/纯文本)"},
    },
    {
        "name": "ocr",
        "description": "OCR 图片转文字。需 tesseract/pytesseract, 缺失优雅提示。path 必填; lang 默认 chi_sim+eng。",
        "parameters": {"path": "图片路径", "lang?": "语言(默认 chi_sim+eng)"},
    },
    # ===== 自动化与集成 (对标 dsh 工作流/定时) =====
    {
        "name": "schedule_task",
        "description": "创建定时/自动化任务(写入工作区 .lmw_schedules.json)。name/prompt 必填; rrule 调度表达式(默认 once)。对标 dsh 定时任务。",
        "parameters": {"name": "任务名", "prompt": "任务指令", "rrule?": "调度表达式(默认 once)", "enabled?": "是否启用(默认 true)"},
    },
    {
        "name": "webhook_send",
        "description": "向外部 webhook 推送 JSON(payload 任意对象)。url 必填。对标工作流集成/回调。",
        "parameters": {"url": "webhook 地址", "payload?": "推送的 JSON 对象"},
    },
    {
        "name": "notify",
        "description": "发送通知(落盘工作区 .lmw_notifications.json + 尽力触发系统 toast)。title/message 必填。对标系统通知。",
        "parameters": {"title": "标题", "message": "内容", "level?": "info|warn|error"},
    },
    # ===== 代码智能增强 (领先一代) =====
    {
        "name": "test_gen",
        "description": "为源文件生成 pytest 单元测试脚手架(零依赖 AST 解析顶层函数/类)。path 或 code 必填; path_out 可选落盘。",
        "parameters": {"path?": "源文件路径", "code?": "内联代码", "path_out?": "落盘测试文件路径"},
    },
    {
        "name": "explain_code",
        "description": "代码解释(零依赖 AST 摘要): 行数/顶层定义/导入/调用。path 或 code 必填。对标代码解读。",
        "parameters": {"path?": "源文件路径", "code?": "内联代码"},
    },
    {
        "name": "security_scan",
        "description": "仓库安全扫描(零依赖静态规则): 危险函数/硬编码密钥/SQL 拼接/命令注入等。path 限定目录。对标安全门禁。",
        "parameters": {"path?": "限定目录/文件(默认当前根)"},
    },
    # —— 知识办公 (Phase 92, 对标 豆包/千问 办公) ——
    {
        "name": "mindmap",
        "description": "生成 Mermaid 脑图/思维导图: topic+items 或 markdown 文本 -> .mmd 源(可渲染 SVG)。对标思维导图。",
        "parameters": {"topic?": "中心主题", "items?": "分支列表[分支,[子项]]", "text?": "markdown 文本(按标题层级成图)", "path?": "输出 .mmd 路径"},
    },
    {
        "name": "translate",
        "description": "多语翻译(零依赖 MyMemory 免费 API, 无网降级)。text 必填; to 目标语(默认 zh-CN), from 源语(默认 en)。对标多语翻译。",
        "parameters": {"text": "待翻译文本", "to?": "目标语言(zh-CN/en/ja/ko...)", "from?": "源语言(默认 en)"},
    },
    {
        "name": "summarize",
        "description": "长文摘要(零依赖抽取式, 词频打分选关键句+关键词, 无需 LLM)。text 必填; sentences 提取句数(默认 5)。",
        "parameters": {"text": "待摘要文本", "sentences?": "提取句数(默认 5)"},
    },
    {
        "name": "pdf_extract",
        "description": "从 PDF 抽取文本(PyPDF2 优先, pdftotext 回退, 均无则提示)。path 必填; path_out 落盘。对标 PDF 读取。",
        "parameters": {"path": "PDF 文件路径", "path_out?": "提取文本输出路径"},
    },
    {
        "name": "markdown_to_docx",
        "description": "Markdown 转 Word(.docx, 零依赖 OOXML)。path 输出 .docx; md 内容或 src markdown 文件。对标文档写作。",
        "parameters": {"path": "输出 .docx 路径", "md?": "markdown 内容", "src?": "markdown 源文件", "title?": "文档标题"},
    },
    {
        "name": "data_analysis",
        "description": "CSV 数据分析(零依赖): 列概览(数值/类别统计)、数值列相关性、首列直方图; 产出 md+html 图表。对标表格洞察。",
        "parameters": {"path": "CSV 文件路径"},
    },
    {
        "name": "db_query",
        "description": "SQLite 查询(标准库 sqlite3)。db 必填; sql 为空列出表, 否则执行(SELECT 返回表格, 其他返回影响行数)。对标数据查询。",
        "parameters": {"db": "sqlite 数据库路径", "sql?": "SQL 语句(空则列出表)"},
    },
    {
        "name": "diagram",
        "description": "生成 Mermaid 图(零依赖): kind=flowchart/sequence/class/state/gantt; 直接给 spec(mermaid 正文)或结构化 nodes+edges。产出 .mmd(有 mmdc 渲染 SVG)。对标 draw.io/语雀绘图。",
        "parameters": {"kind?": "图类型(flowchart/sequence/class/state/gantt)", "spec?": "直接 mermaid 正文", "nodes?": "节点字典{id:标签}", "edges?": "边列表(['A-->B: 说明'])", "title?": "图标题", "out?": "输出 .mmd 路径"},
    },
    {
        "name": "chart",
        "description": "数据→SVG 图表(零依赖): type=line/bar/pie; data=JSON({labels, series:[{name,values}]} 或饼图 {labels,values})。产出 .svg+.html 预览。对标数据可视化/QuickChart。",
        "parameters": {"type": "图表类型(line/bar/pie)", "data": "JSON 数据", "title?": "标题", "out?": "输出 .svg 路径"},
    },
    {
        "name": "api_test",
        "description": "多接口测试(零依赖 urllib): cases=[{name,method,url,headers?,body?,asserts?}]; asserts={status?,contains?,json_path?,equals?}。产出报告。对标 Postman/接口测试。",
        "parameters": {"cases": "用例 JSON 列表", "base_url?": "基础 URL 前缀", "out?": "报告路径(默认 api_test_report.md)"},
    },
    {
        "name": "email_compose",
        "description": "撰写邮件草稿(标准库, 输出 .eml); 提供 smtp{host,port,user,pass}+send=true 可发送。对标邮件客户端。",
        "parameters": {"to": "收件人", "subject": "主题", "body": "正文", "from?": "发件人", "cc?": "抄送", "smtp?": "SMTP 配置", "send?": "是否发送(bool)", "out?": "草稿 .eml 路径"},
    },
    {
        "name": "calendar_event",
        "description": "生成日历事件(标准 ICS 2.0): title+start(ISO); end?/duration?(分钟)/location?/description?/alarm?(提前分钟)。对标日历/日程。",
        "parameters": {"title": "事件标题", "start": "开始时间(ISO8601)", "end?": "结束时间", "duration?": "时长(分钟, 与 end 二选一)", "location?": "地点", "description?": "描述", "alarm?": "提前提醒分钟", "out?": "输出 .ics 路径"},
    },
    {
        "name": "knowledge_search",
        "description": "本地知识检索(零依赖 TF-IDF): action=index(path) 建索引(存 .lmw_kb_index.json); action=query(query,limit?) 检索返回相似文档。对标 RAG/语义检索。",
        "parameters": {"action?": "index|query(默认 query)", "path?": "建索引目录", "query?": "检索词", "limit?": "返回条数(默认5)", "index?": "索引文件路径"},
    },
    {
        "name": "pdf_make",
        "description": "文本/Markdown→PDF: 优先 reportlab 完整排版, 否则零依赖最小 PDF。input(文件) 或 text/markdown。对标文档导出。",
        "parameters": {"text?": "正文/Markdown", "input?": "输入文件", "title?": "PDF 标题", "out?": "输出 .pdf 路径"},
    },
    {
        "name": "flow_runner",
        "description": "工作流编排(轻量 n8n/Actions): spec(JSON) 或 file 定义 steps, 支持 run/set/echo/if/http/write 步骤与 ${var} 变量替换, 串行执行产出报告。对标自动化编排。",
        "parameters": {"spec?": "JSON 工作流定义字符串", "file?": "工作流文件路径", "report?": "报告输出路径"},
    },
    {
        "name": "formatter",
        "description": "多语言代码格式化: 自动识别语言, 委托 black/autopep8(python)、jsbeautifier(js/css/html)、gofmt(go), JSON 零依赖美化。引擎缺失优雅提示。对标代码美化。",
        "parameters": {"path": "目标文件", "lang?": "强制语言(默认按扩展名)"},
    },
    {
        "name": "deep_review",
        "description": "深度评审(零依赖 AST): 统计函数/类/行数, 扫描危险模式(eval/os.system/SQL 拼接/明文密码等), 每文件概览, 产出 markdown 报告。对标代码评审。",
        "parameters": {"path?": "文件或目录(默认当前根)", "report?": "报告输出路径(默认 deep_review.md)"},
    },
    {
        "name": "local_llm_route",
        "description": "本地 LLM 路由: 调用 Ollama(/api/generate) 或 OpenAI 兼容端点(llama.cpp 等 /v1/chat/completions)。本地服务未运行优雅降级。对标私有化大模型。",
        "parameters": {"prompt": "提示词", "base_url?": "默认 http://localhost:11434", "model?": "默认 llama3", "backend?": "ollama|openai"},
    },
    {
        "name": "screenshot",
        "description": "网页/桌面截图: 优先 playwright, 其次 selenium, 无引擎优雅提示安装。对标可视化/截图能力。",
        "parameters": {"url?": "网页 URL", "file?": "本地文件", "out?": "输出 .png 路径(默认 screenshot.png)"},
    },
    {
        "name": "clipboard",
        "description": "剪贴板读写: pyperclip 优先, Windows 下 PowerShell 降级。action=read|write, write 需 text。对标剪贴板集成。",
        "parameters": {"action?": "read|write(默认 read)", "text?": "写入内容(write 时)"},
    },
    {
        "name": "csv_convert",
        "description": "CSV 格式转换: 转 JSON / Markdown 表格 / XLSX(openpyxl)。json/markdown 零依赖。对标表格处理。",
        "parameters": {"path": "CSV 文件", "to?": "json|markdown|xlsx(默认 json)", "out?": "输出路径"},
    },
    {
        "name": "code_metrics",
        "description": "AST 代码指标(零依赖): 目录或单文件统计 LOC/SLOC/注释/空行、函数数、类数、方法数、圈复杂度(合计/均值/峰值), 输出 Top 文件清单与 code_metrics.json。对标代码健康度。",
        "parameters": {"path?": "目录或 .py 文件(默认当前根)"},
    },
    {
        "name": "agent_team",
        "description": "多 Agent 编排(零依赖): spec(JSON) 定义 agents(role+task) 与 strategy(parallel/sequential/debate)+aggregator, 产出团队调度清单 .lmw_team/team_*.json。对标多智能体协作。",
        "parameters": {"spec?": "JSON 编排定义字符串", "file?": "编排文件路径"},
    },
    {
        "name": "db_migrate",
        "description": "SQLite 迁移运行器(标准库): action=init/create/status/up/down, 基于 migrations 目录与 lmw_migrations 表跟踪。对标数据库版本管理。",
        "parameters": {"db": "sqlite 数据库路径", "action?": "init|create|status|up|down(默认 status)", "dir?": "迁移目录(默认 migrations)", "name?": "迁移名/create 用", "pages?": "pdf_split 用"},
    },
    {
        "name": "pdf_merge",
        "description": "多 PDF 合并: 优先 PyPDF2/pypdf 真实合并, 无库优雅降级提示。files 为待合并列表, out 为目标文件。对标文档合并。",
        "parameters": {"files": "待合并 PDF 路径列表", "out?": "输出 .pdf 路径(默认 merged.pdf)"},
    },
    {
        "name": "pdf_split",
        "description": "PDF 按页拆分: 优先 PyPDF2/pypdf, 无库优雅降级。file 源文件, pages 可选范围(如 1-3,5), out_dir 输出目录。对标文档拆分。",
        "parameters": {"file": "源 PDF 路径", "pages?": "页范围(如 1-3,5)", "out_dir?": "输出目录(默认 split)"},
    },
    {
        "name": "form_to_pdf",
        "description": "表单/字段清单→PDF(零依赖, 内嵌系统 CJK 字体支持中文): title + fields(标签/值/类型) 生成多页表单 PDF。对标表单/文档生成。",
        "parameters": {"title?": "表单标题", "fields?": "字段列表(对象含 label/value/type 或字符串)", "out?": "输出 .pdf 路径(默认 form.pdf)"},
    },
    {
        "name": "text_compare",
        "description": "双文本差异与相似度(零依赖 difflib): 行级 unified diff + 相似度百分比 + 增删行统计, 支持 file_a/file_b 读文件。对标对比/查重。",
        "parameters": {"a?": "文本 A", "b?": "文本 B", "file_a?": "文本 A 文件", "file_b?": "文本 B 文件", "out?": "报告输出路径"},
    },
    {
        "name": "agent_team_run",
        "description": "执行 agent_team 生成的团队清单: 落盘各 agent 派发 prompt + 调度计划(parallel/sequential/debate), 供主控 AgentLoop 派发子 Agent。对标多 Agent 编排落地。",
        "parameters": {"team?": "团队清单 json 路径(默认 .lmw_team 最新)", "spec?": "直接传团队 spec", "rounds?": "debate 回合数(默认 2)"},
    },
    {
        "name": "pdf_redact",
        "description": "PDF 脱敏: 遮盖指定关键词/正则(优先 pypdf redact, 无库优雅降级)。file 源文件, terms 关键词列表, regex 是否正则, out 输出。对标文档合规脱敏。",
        "parameters": {"file": "源 PDF 路径", "terms?": "待遮盖关键词列表", "regex?": "true 时 terms 作正则", "out?": "输出 .pdf(默认 redacted.pdf)"},
    },
    {
        "name": "db_schema_doc",
        "description": "SQLite schema 文档生成(标准库 sqlite3, 零依赖): 输出每张表的列/类型/约束/索引/外键, 支持 md/json。对标数据库文档。",
        "parameters": {"db": "sqlite 数据库路径", "format?": "md|json(默认 md)", "out?": "文档输出路径"},
    },
    {
        "name": "form_validate",
        "description": "表单/数据校验(零依赖规则引擎): required/type/pattern/enum/min/max, 输出通过/失败明细与未声明字段。对标数据校验。",
        "parameters": {"data": "待校验数据对象(JSON)", "schema": "校验规则(字段->{type,pattern,enum,min,max} 或 {required,fields})", "out?": "结果输出路径"},
    },
    {
        "name": "release_notes",
        "description": "发布说明生成(零依赖): 按 feat/fix/perf/.. 分类汇总 changes, 或读取 CHANGELOG.md/changes.txt。对标版本发布文档。",
        "parameters": {"version?": "版本号(默认 unreleased)", "changes?": "变更清单(对象/字符串列表)", "out?": "发布说明输出路径"},
    },
    {
        "name": "code_search_semantic",
        "description": "语义代码搜索(零依赖 TF-IDF 跨文件): 按查询词返回相关文件与相似度+代码片段, 支持 ext 过滤与 top_k。对标语义检索。",
        "parameters": {"query": "查询文本", "path?": "代码根(默认 cwd)", "ext?": "扩展名过滤", "top_k?": "返回条数(默认 5)"},
    },
    {
        "name": "template_render",
        "description": "模板渲染(零依赖): 支持 {{var}} 与 {% for x in items %}...{% endfor %} 循环, 输出渲染文本或写文件。对标模板引擎。",
        "parameters": {"template?": "模板文本", "template_file?": "模板文件路径", "vars?": "变量对象(JSON)", "out?": "渲染结果输出路径"},
    },
    {
        "name": "webhook_sign",
        "description": "计算 webhook 签名(HMAC-SHA256, 含时间戳防重放): 返回 timestamp 与 X-Signature 头值, 用于发送/校验 webhook。",
        "parameters": {"secret": "签名密钥", "payload": "待签名内容(文本/JSON)", "timestamp?": "时间戳(默认当前)"},
    },
    {
        "name": "db_diff",
        "description": "对比两个 SQLite 库: 表结构(列差异)与行差异, 返回可读差异报告。",
        "parameters": {"a": "库A路径", "b": "库B路径", "tables?": "仅对比指定表"},
    },
    {
        "name": "changelog_update",
        "description": "按 Keep a Changelog 格式在 CHANGELOG.md 顶部插入新版本块(支持 Added/Changed 等小节)。",
        "parameters": {"file?": "CHANGELOG 路径(默认 CHANGELOG.md)", "version": "版本号", "date?": "日期(默认今天)", "changes": "变更条目(数组/多行文本)", "section?": "小节(默认 Added)"},
    },
    {
        "name": "code_search_ast",
        "description": "基于 AST 搜索 Python 代码: 查找 def/class/call/import/name, 非 Python 正则兜底。",
        "parameters": {"path": "代码路径(文件或目录)", "kind?": "def|class|call|import|name(默认 def)", "name?": "标识符过滤", "pattern?": "子串过滤"},
    },
    {
        "name": "csv_merge",
        "description": "合并多个 CSV: 纵向 concat(按首表头) 或 横向 join(on 键)。",
        "parameters": {"files": "CSV 路径列表", "out?": "输出路径(默认 merged.csv)", "how?": "concat|join(默认 concat)", "keys?": "join 键列"},
    },
    {
        "name": "json_query",
        "description": "轻量 JSONPath 查询(零依赖): 支持 $.a.b / $.x[*].y / $.a[0], 文件或内联数据。",
        "parameters": {"path?": "JSON 文件路径", "data?": "内联 JSON", "jsonpath": "查询路径(如 $.a.b)"},
    },
    {
        "name": "env_check",
        "description": "校验必需环境变量是否设置, 或对比 .env 模板与当前环境, 返回缺失/已设置清单。",
        "parameters": {"required?": "必需变量名(数组/逗号分隔)", "template?": "env 模板文件路径", "env_file?": "env 文件路径(覆盖当前环境)"},
    },
    {
        "name": "webhook_verify",
        "description": "验证 webhook 签名 (HMAC-SHA256, 可选时间戳防重放), 返回签名有效性与重放风险。",
        "parameters": {"payload": "待验证的 payload(字符串/JSON)", "secret": "共享密钥", "signature": "收到的签名(可带 sha256= 前缀)", "timestamp?": "发送方时间戳(秒)", "tolerance?": "重放容差秒数(默认300)"},
    },
    {
        "name": "sql_format",
        "description": "轻量 SQL 格式化(零依赖): 关键字折行 + 括号层级缩进, 支持内联或文件。",
        "parameters": {"sql?": "内联 SQL", "file?": "SQL 文件路径"},
    },
    {
        "name": "csv_diff",
        "description": "对比两个 CSV: 按 key 列对齐输出新增/删除/修改报告, 无 key 则按行号。",
        "parameters": {"a": "CSV A 路径", "b": "CSV B 路径", "key?": "对齐键列名"},
    },
    {
        "name": "json_schema_validate",
        "description": "校验 JSON 是否符合简化 schema(type/required/properties/enum/items), 数据或 schema 可来自文件。",
        "parameters": {"data?": "内联 JSON", "file?": "JSON 数据文件", "schema?": "内联 schema", "schema_file?": "schema 文件"},
    },
    {
        "name": "release_tag",
        "description": "semver 解析/校验/比较/递增(major.minor.patch)。",
        "parameters": {"version": "版本号(x.y.z)", "bump?": "递增段 major/minor/patch", "compare?": "用于比较的另一版本"},
    },
    {
        "name": "log_tail",
        "description": "读日志尾部 N 行, 支持关键字过滤(grep), 零依赖。",
        "parameters": {"file": "日志文件路径", "n?": "取末尾行数(默认50)", "grep?": "关键字过滤", "ignore_case?": "忽略大小写(bool)"},
    },
    {
        "name": "password_generate",
        "description": "生成强密码/口令(secrets 安全随机), 可选可读模式排除易混淆字符。",
        "parameters": {"length?": "长度(默认16)", "count?": "生成数量(默认1)", "lower?": "含小写(bool)", "upper?": "含大写(bool)", "digit?": "含数字(bool)", "symbol?": "含符号(bool)", "readable?": "可读模式(排除易混淆字符)"},
    },
    {
        "name": "webhook_emit",
        "description": "发送 webhook (POST, 超时保护, 支持 HMAC 签名与 dry_run 预演), 零依赖.",
        "parameters": {"url": "目标 URL", "body?": "请求体(dict|list|str)", "method?": "方法(默认POST)", "headers?": "附加请求头(dict)", "content_type?": "Content-Type(默认application/json)", "secret?": "HMAC 签名密钥", "timeout?": "超时秒(默认8)", "dry_run?": "仅预演不真正发送(bool)"},
    },
    {
        "name": "sql_explain",
        "description": "提取 SQL 操作类型/表/列 (轻量正则解析, 非完整引擎).",
        "parameters": {"sql": "SQL 语句"},
    },
    {
        "name": "csv_to_json",
        "description": "CSV -> JSON 数组 (每行一个对象), 支持自定义分隔符.",
        "parameters": {"file": "CSV 路径", "delimiter?": "分隔符(默认,)", "encoding?": "编码(默认utf-8-sig)"},
    },
    {
        "name": "hash_file",
        "description": "计算文件多算法哈希 (md5/sha1/sha256/sha512, 分块读取).",
        "parameters": {"file": "文件路径", "algorithms?": "算法列表(默认sha256)"},
    },
    {
        "name": "cron_parse",
        "description": "解析 cron 表达式(5段), 产出中文描述与下次运行时间.",
        "parameters": {"expression": "cron 表达式(分 时 日 月 周)"},
    },
    {
        "name": "text_diff",
        "description": "两文本行级统一 diff (difflib), 统计 +/- 行数.",
        "parameters": {"a": "文本A(或行列表)", "b": "文本B", "name_a?": "A 名称", "name_b?": "B 名称"},
    },
    {
        "name": "yaml_query",
        "description": "极简 YAML 路径查询 (a.b.c / a.list[0]), 支持 file 或 text.",
        "parameters": {"file?": "YAML 文件", "text?": "YAML 文本", "query?": "路径(如 a.b.c)"},
    },
    {
        "name": "webhook_dispatch",
        "description": "按事件路由到多目标 webhook 并发发送 (urllib POST, 可选 HMAC 签名, dry_run 预演).",
        "parameters": {"event": "事件名", "routes": "{event:url} 路由表", "body?": "请求体", "secret?": "HMAC 密钥", "timeout?": "超时秒", "dry_run?": "仅预演"},
    },
    {
        "name": "sql_lint",
        "description": "轻量 SQL 静态检查 (SELECT */缺 WHERE/INSERT 未指定列/关键字大小写/缺 LIMIT).",
        "parameters": {"sql": "SQL 语句"},
    },
    {
        "name": "json_schema_gen",
        "description": "由样本 JSON 推断 JSON Schema (type/required/properties/items).",
        "parameters": {"text?": "JSON 文本", "file?": "JSON 文件"},
    },
    {
        "name": "cron_next_n",
        "description": "cron 表达式 -> 接下来 N 次运行时间 (默认 5).",
        "parameters": {"expression": "5 段 cron", "count?": "次数"},
    },
    {
        "name": "diff_patch",
        "description": "将统一 diff 应用到文本, 产出打补丁后文本 (可选写文件).",
        "parameters": {"original": "原始文本/行列表", "patch": "统一 diff", "out_file?": "写出文件"},
    },
    {
        "name": "yaml_merge",
        "description": "两份 YAML 深度合并 (a 基底, b 覆盖/扩展, 可选写 JSON).",
        "parameters": {"a?": "YAML a", "b?": "YAML b", "file_a?": "文件a", "file_b?": "文件b", "out_file?": "写出"},
    },
    {
        "name": "hash_verify",
        "description": "校验文件哈希是否与期望值一致 (md5/sha1/sha256/sha512).",
        "parameters": {"file": "文件路径", "expected": "期望哈希", "algo?": "算法(默认sha256)"},
    },    {
        "name": "secret_audit",
        "description": "扫描目录/文件中的硬编码密钥 (API key/token/密码).",
        "parameters": {"path": "文件或目录", "recursive?": "是否递归(默认true)", "max_find?": "最多命中数"},
    },
    {
        "name": "dep_check",
        "description": "检查依赖清单 (requirements.txt/package.json) 的版本钉固与风险.",
        "parameters": {"path": "目录或依赖文件"},
    },
    {
        "name": "license_check",
        "description": "识别项目许可证类型 (LICENSE 文件关键字匹配).",
        "parameters": {"path": "目录或许可证文件"},
    },
    {
        "name": "perm_diff",
        "description": "比较两个目录树的文件存在性/大小差异.",
        "parameters": {"a": "目录a", "b": "目录b", "recursive?": "是否递归", "max?": "最多列出差异"},
    },
    {
        "name": "json_to_csv",
        "description": "JSON 数组 -> CSV (写出文件).",
        "parameters": {"json?": "JSON字符串", "file?": "JSON文件", "out_file": "输出CSV"},
    },
    {
        "name": "xml_query",
        "description": "极简 XPath 式 XML 查询 (取文本或属性).",
        "parameters": {"file?": "XML文件", "xml?": "XML字符串", "query": "路径查询", "max?": "最多条数"},
    },
    {
        "name": "toml_query",
        "description": "TOML 路径查询 (a.b.c / a.list[0].c).",
        "parameters": {"file?": "TOML文件", "toml?": "TOML字符串", "path": "点分路径"},
    },
    {
        "name": "xml_to_json",
        "description": "XML -> JSON (可写出文件).",
        "parameters": {"file?": "XML文件", "xml?": "XML字符串", "out_file?": "输出JSON"},
    },
    {
        "name": "json_to_sql",
        "description": "JSON 数组 -> SQL INSERT (写出文件).",
        "parameters": {"json?": "JSON字符串", "file?": "JSON文件", "table?": "表名", "out_file?": "输出SQL"},
    },
    {
        "name": "toml_to_json",
        "description": "TOML -> JSON (可写出文件).",
        "parameters": {"file?": "TOML文件", "toml?": "TOML字符串", "out_file?": "输出JSON"},
    },
    {
        "name": "json_patch",
        "description": "应用 JSON Patch (add/replace/remove/test/move/copy).",
        "parameters": {"json?": "JSON字符串", "file?": "JSON文件", "patch": "操作数组JSON", "out_file?": "输出JSON"},
    },
    {
        "name": "secret_mask",
        "description": "敏感信息掩码 (可写出文件).",
        "parameters": {"text?": "文本", "file?": "文件", "out_file?": "输出文件"},
    },
    {
        "name": "sbom_gen",
        "description": "软件物料清单 (SBOM) 生成.",
        "parameters": {"path": "目录", "out_file?": "输出JSON"},
    },
    {
        "name": "dep_graph",
        "description": "Python 模块依赖图生成.",
        "parameters": {"path": "目录或py文件", "out_file?": "输出JSON"},
    },
    {
        "name": "yaml_to_json",
        "description": "YAML(极简缩进式子集)转 JSON.",
        "parameters": {"text?": "YAML文本", "file?": "YAML文件"},
    },
    {
        "name": "json_to_yaml",
        "description": "JSON 转 YAML(缩进式).",
        "parameters": {"text?": "JSON文本", "file?": "JSON文件"},
    },
    {
        "name": "xml_to_csv",
        "description": "XML 转 CSV(按重复同名子元素展平).",
        "parameters": {"text?": "XML文本", "file?": "XML文件", "row_tag?": "行元素标签"},
    },
    {
        "name": "toml_to_yaml",
        "description": "TOML 转 YAML.",
        "parameters": {"text?": "TOML文本", "file?": "TOML文件"},
    },
    {
        "name": "license_compat",
        "description": "许可证兼容矩阵检查.",
        "parameters": {"licenses?": "许可证列表(逗号/换行)", "primary?": "主许可证", "deps?": "依赖许可证列表"},
    },
    {
        "name": "dep_outdated",
        "description": "依赖过时/未固定离线启发式检查.",
        "parameters": {"path": "依赖清单(requirements/pyproject/package.json)"},
    },
    {
        "name": "file_classify",
        "description": "文件类型分类(魔数签名).",
        "parameters": {"file": "文件路径"},
    },
    {
        "name": "json_pointer",
        "description": "JSON Pointer (RFC6901) 取值.",
        "parameters": {"json": "JSON 文本", "pointer": "指针如 /a/b/0"},
    },
    {
        "name": "csv_to_xml",
        "description": "CSV 转 XML.",
        "parameters": {"csv": "CSV 文本", "root": "根元素名(默认 root)", "row": "行元素名(默认 row)"},
    },
    {
        "name": "yaml_to_toml",
        "description": "YAML 转 TOML.",
        "parameters": {"yaml": "YAML 文本"},
    },
    {
        "name": "ini_query",
        "description": "INI 配置读取/查询.",
        "parameters": {"ini": "INI 文本", "section": "段名(可选)", "key": "键名(可选)"},
    },
    {
        "name": "ini_to_json",
        "description": "INI 转 JSON.",
        "parameters": {"ini": "INI 文本"},
    },
    {
        "name": "license_list",
        "description": "常见开源许可证清单与兼容性速查.",
        "parameters": {"category": "筛选类别(permissive/weak-copyleft/copyleft, 可选)", "query": "关键字(可选)", "format": "json 则返回 JSON"},
    },
    {
        "name": "json_schema_lint",
        "description": "JSON Schema 语法基础校验.",
        "parameters": {"schema": "Schema JSON 文本"},
    },

    {
        "name": "json_to_xml",
        "description": "JSON 转 XML (嵌套/数组/文本, 可指定根与数组元素标签).",
        "parameters": {"json": "JSON 文本", "root": "根标签(可选, 默认 root)", "item": "数组元素标签(可选, 默认 item)"},
    },
    {
        "name": "csv_to_yaml",
        "description": "CSV 转 YAML (首行表头, 余下为记录).",
        "parameters": {"csv": "CSV 文本", "delimiter": "分隔符(可选, 默认 ,)"},
    },
    {
        "name": "yaml_to_ini",
        "description": "YAML 转 INI (顶层映射, 每键为段).",
        "parameters": {"yaml": "YAML 文本"},
    },
    {
        "name": "toml_to_xml",
        "description": "TOML 转 XML (嵌套表/数组表).",
        "parameters": {"toml": "TOML 文本", "root": "根标签(可选, 默认 root)"},
    },
    {
        "name": "json_schema_compile",
        "description": "JSON Schema 合并编译 ($ref / definitions 内联, allOf 展开).",
        "parameters": {"schema": "Schema JSON 文本"},
    },
    {
        "name": "xml_to_yaml",
        "description": "XML 转 YAML (元素/文本, 同名子元素聚合为数组).",
        "parameters": {"xml": "XML 文本"},
    },
    {
        "name": "json_schema_docs",
        "description": "JSON Schema 字段文档生成 (字段/类型/必填/描述, 嵌套深入一层).",
        "parameters": {"schema": "Schema JSON 文本"},
    },
    {
        "name": "json_to_ini",
        "description": "JSON 转 INI (顶层映射每键为段).",
        "parameters": {"json": "JSON 文本"},
    },
    {
        "name": "csv_to_ini",
        "description": "CSV 转 INI (可指定列作段名, 否则 rowN).",
        "parameters": {"csv": "CSV 文本", "key": "用作段名的列名(可选)", "delimiter": "分隔符(可选, 默认 ,)"},
    },
    {
        "name": "xml_to_toml",
        "description": "XML 转 TOML (嵌套元素转表, 同名子元素聚合为数组).",
        "parameters": {"xml": "XML 文本"},
    },
    {
        "name": "yaml_to_xml",
        "description": "YAML 转 XML (嵌套/数组, 可指定根标签).",
        "parameters": {"yaml": "YAML 文本", "root": "根标签(可选, 默认 root)"},
    },
    {
        "name": "json_schema_to_ts",
        "description": "JSON Schema 转 TypeScript interface (含嵌套对象内联).",
        "parameters": {"schema": "Schema JSON 文本", "name": "接口名(可选, 默认 Root)"},
    },
    {
        "name": "ini_to_yaml",
        "description": "INI 转 YAML (段转映射, 保留键大小写).",
        "parameters": {"ini": "INI 文本"},
    },
    {
        "name": "json_to_toml",
        "description": "JSON 转 TOML (嵌套对象/标量数组/数组表).",
        "parameters": {"json": "JSON 文本"},
    },
    {
        "name": "xml_to_ini",
        "description": "XML 转 INI (子元素为段, 孙元素文本为键).",
        "parameters": {"xml": "XML 文本"},
    },
    {
        "name": "toml_to_ini",
        "description": "TOML 转 INI (表为段).",
        "parameters": {"toml": "TOML 文本"},
    },
    {
        "name": "csv_to_toml",
        "description": "CSV 转 TOML (每行为一个数组表项).",
        "parameters": {"csv": "CSV 文本", "table": "数组表名(可选, 默认 rows)", "delimiter": "分隔符(可选, 默认 ,)"},
    },
    {
        "name": "json_schema_to_python",
        "description": "JSON Schema 转 Python dataclass / TypedDict (嵌套类前置定义).",
        "parameters": {"schema": "Schema JSON 文本", "name": "类名(可选, 默认 Model)", "style": "dataclass(默认) 或 typed_dict"},
    },
    {
        "name": "yaml_to_csv",
        "description": "YAML 转 CSV (映射列表, 键并集为列).",
        "parameters": {"yaml": "YAML 文本"},
    },
    {
        "name": "ini_to_xml",
        "description": "INI 转 XML (段为元素, 键为子元素).",
        "parameters": {"ini": "INI 文本", "root": "根标签(可选, 默认 root)"},
    },
    {
        "name": "toml_to_csv",
        "description": "TOML 转 CSV (数组表为行, 键并集为列).",
        "parameters": {"toml": "TOML 文本", "table": "数组表名(可选, 自动探测)"},
    },
    {
        "name": "json_schema_to_go",
        "description": "JSON Schema 转 Go struct (json tag / omitempty / 嵌套类型前置).",
        "parameters": {"schema": "Schema JSON 文本", "name": "结构体名(可选, 默认 Model)", "package": "包名(可选, 默认 main)"},
    },
    {
        "name": "json_schema_to_java",
        "description": "JSON Schema 转 Java POJO (字段 + getter/setter).",
        "parameters": {"schema": "Schema JSON 文本", "name": "类名(可选, 默认 Model)", "package": "包名(可选)"},
    },
    {
        "name": "markdown_table_to_csv",
        "description": "Markdown 表格 转 CSV (自动跳过对齐分隔行).",
        "parameters": {"markdown": "Markdown 文本"},
    },
    {
        "name": "sql_to_json",
        "description": "SQL CREATE TABLE 转 JSON 表结构描述 (列名/类型/约束).",
        "parameters": {"sql": "SQL 文本"},
    },
    {
        "name": "env_to_json",
        "description": ".env 转 JSON (跳过注释, 支持 export 前缀与引号剥离).",
        "parameters": {"env": ".env 文本"},
    },
    {
        "name": "dockerfile_lint",
        "description": "Dockerfile 最佳实践检查 (FROM tag/sudo/apt/ADD/USER/WORKDIR/HEALTHCHECK).",
        "parameters": {"dockerfile": "Dockerfile 文本"},
    },
    {
        "name": "gitignore_gen",
        "description": "按技术栈生成 .gitignore (python/node/go/java/rust/generic).",
        "parameters": {"stacks": "技术栈, 逗号或空格分隔, 如 python,node,go"},
    },
    {
        "name": "jwt_decode",
        "description": "JWT 解码 (header/payload/过期检查, 不校验签名).",
        "parameters": {"token": "JWT 令牌文本"},
    },
    {
        "name": "url_parse",
        "description": "URL 结构化解析 (协议/主机/端口/路径/查询串, 密码自动掩码).",
        "parameters": {"url": "URL 文本"},
    },
    {
        "name": "markdown_toc",
        "description": "Markdown 目录生成 (跳过代码块, GitHub 风格锚点).",
        "parameters": {"markdown": "Markdown 文本", "max_level": "最大标题层级(可选, 默认 3)"},
    },
    {
        "name": "text_stats",
        "description": "文本统计 (字符/行数/英文词/中日韩字/UTF-8 字节/高频词 top10).",
        "parameters": {"text": "待统计文本"},
    },
    {
        "name": "csv_to_markdown",
        "description": "CSV 转 Markdown 表格 (对齐方式可配).",
        "parameters": {"csv": "CSV 文本", "align": "对齐 left/center/right(可选, 默认 left)", "delimiter": "分隔符(可选, 默认 ,)"},
    },
    {
        "name": "env_lint",
        "description": ".env 语法检查 (重复键/非法键名/空值/未引号空格/行内注释).",
        "parameters": {"env": ".env 文本内容"},
    },
    {
        "name": "requirements_diff",
        "description": "依赖清单差分 (新增/移除/版本变更).",
        "parameters": {"a": "旧依赖清单文本", "b": "新依赖清单文本"},
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
    # —— 联网与 API ——
    "web_fetch": suite_extended.web_fetch,
    "web_search": suite_extended.web_search,
    "http_request": suite_extended.http_request,
    # —— 完整 Git 工作流 ——
    "git_status": suite_extended.git_status,
    "git_diff": suite_extended.git_diff,
    "git_log": suite_extended.git_log,
    "git_branch": suite_extended.git_branch,
    "git_checkout": suite_extended.git_checkout,
    "git_stash": suite_extended.git_stash,
    "git_pr_draft": suite_extended.git_pr_draft,
    # —— 多模态真实生成 ——
    "image_generate": suite_extended.image_generate,
    "image_understand": suite_extended.image_understand,
    "tts": suite_extended.tts,
    "transcribe": suite_extended.transcribe,
    "video_generate": suite_extended.video_generate,
    # —— 文档全家桶 ——
    "make_ppt": suite_extended.make_ppt,
    "make_xlsx": suite_extended.make_xlsx,
    "make_pdf": suite_extended.make_pdf,
    "ocr": suite_extended.ocr,
    # —— 自动化与集成 ——
    "schedule_task": suite_extended.schedule_task,
    "webhook_send": suite_extended.webhook_send,
    "notify": suite_extended.notify,
    # —— 代码智能增强 ——
    "test_gen": suite_extended.test_gen,
    "explain_code": suite_extended.explain_code,
    "security_scan": suite_extended.security_scan,
    # —— 知识办公 (Phase 92, 对标 豆包/千问 办公) ——
    "mindmap": suite_knowledge.mindmap,
    "translate": suite_knowledge.translate,
    "summarize": suite_knowledge.summarize,
    "pdf_extract": suite_knowledge.pdf_extract,
    "markdown_to_docx": suite_knowledge.markdown_to_docx,
    "data_analysis": suite_knowledge.data_analysis,
    "db_query": suite_knowledge.db_query,
    # —— 生产力 (Phase 93, 对标 绘图/可视化/接口测试/邮件/日历/RAG/文档导出) ——
    "diagram": suite_productivity.diagram,
    "chart": suite_productivity.chart,
    "api_test": suite_productivity.api_test,
    "email_compose": suite_productivity.email_compose,
    "calendar_event": suite_productivity.calendar_event,
    "knowledge_search": suite_productivity.knowledge_search,
    "pdf_make": suite_productivity.pdf_make,
    # —— 自动化与本地智能 (Phase 94) ——
    "flow_runner": suite_automation.flow_runner,
    "formatter": suite_automation.formatter,
    "deep_review": suite_automation.deep_review,
    "local_llm_route": suite_automation.local_llm_route,
    "screenshot": suite_automation.screenshot,
    "clipboard": suite_automation.clipboard,
    "csv_convert": suite_automation.csv_convert,
    # —— 研发效能/文档/协作 (Phase 95) ——
    "code_metrics": suite_rnd.code_metrics,
    "agent_team": suite_rnd.agent_team,
    "db_migrate": suite_rnd.db_migrate,
    "pdf_merge": suite_rnd.pdf_merge,
    "pdf_split": suite_rnd.pdf_split,
    "form_to_pdf": suite_rnd.form_to_pdf,
    "text_compare": suite_rnd.text_compare,
    # —— 协作/运维/文档增强 (Phase 96) ——
    "agent_team_run": suite_phase96.agent_team_run,
    "pdf_redact": suite_phase96.pdf_redact,
    "db_schema_doc": suite_phase96.db_schema_doc,
    "form_validate": suite_phase96.form_validate,
    "release_notes": suite_phase96.release_notes,
    "code_search_semantic": suite_phase96.code_search_semantic,
    "template_render": suite_phase96.template_render,
    "webhook_sign": suite_phase97.webhook_sign,
    "db_diff": suite_phase97.db_diff,
    "changelog_update": suite_phase97.changelog_update,
    "code_search_ast": suite_phase97.code_search_ast,
    "csv_merge": suite_phase97.csv_merge,
    "json_query": suite_phase97.json_query,
    "env_check": suite_phase97.env_check,
    "webhook_verify": suite_phase98.webhook_verify,
    "sql_format": suite_phase98.sql_format,
    "csv_diff": suite_phase98.csv_diff,
    "json_schema_validate": suite_phase98.json_schema_validate,
    "release_tag": suite_phase98.release_tag,
    "log_tail": suite_phase98.log_tail,
    "password_generate": suite_phase98.password_generate,
    "webhook_emit": suite_phase99.webhook_emit,
    "sql_explain": suite_phase99.sql_explain,
    "csv_to_json": suite_phase99.csv_to_json,
    "hash_file": suite_phase99.hash_file,
    "cron_parse": suite_phase99.cron_parse,
    "text_diff": suite_phase99.text_diff,
    "yaml_query": suite_phase99.yaml_query,
    "webhook_dispatch": suite_phase100.webhook_dispatch,
    "sql_lint": suite_phase100.sql_lint,
    "json_schema_gen": suite_phase100.json_schema_gen,
    "cron_next_n": suite_phase100.cron_next_n,
    "diff_patch": suite_phase100.diff_patch,
    "yaml_merge": suite_phase100.yaml_merge,
    "hash_verify": suite_phase100.hash_verify,
    "secret_audit": suite_phase101.secret_audit,
    "dep_check": suite_phase101.dep_check,
    "license_check": suite_phase101.license_check,
    "perm_diff": suite_phase101.perm_diff,
    "json_to_csv": suite_phase101.json_to_csv,
    "xml_query": suite_phase101.xml_query,
    "toml_query": suite_phase101.toml_query,
    "xml_to_json": suite_phase102.xml_to_json,
    "json_to_sql": suite_phase102.json_to_sql,
    "toml_to_json": suite_phase102.toml_to_json,
    "json_patch": suite_phase102.json_patch,
    "secret_mask": suite_phase102.secret_mask,
    "sbom_gen": suite_phase102.sbom_gen,
    "dep_graph": suite_phase102.dep_graph,
    "yaml_to_json": suite_phase103.yaml_to_json,
    "json_to_yaml": suite_phase103.json_to_yaml,
    "xml_to_csv": suite_phase103.xml_to_csv,
    "toml_to_yaml": suite_phase103.toml_to_yaml,
    "license_compat": suite_phase103.license_compat,
    "dep_outdated": suite_phase103.dep_outdated,
    "file_classify": suite_phase103.file_classify,

    "json_pointer": suite_phase104.json_pointer,
    "csv_to_xml": suite_phase104.csv_to_xml,
    "yaml_to_toml": suite_phase104.yaml_to_toml,
    "ini_query": suite_phase104.ini_query,
    "ini_to_json": suite_phase104.ini_to_json,
    "license_list": suite_phase104.license_list,
    "json_schema_lint": suite_phase104.json_schema_lint,
    "json_to_xml": suite_phase105.json_to_xml,
    "csv_to_yaml": suite_phase105.csv_to_yaml,
    "yaml_to_ini": suite_phase105.yaml_to_ini,
    "toml_to_xml": suite_phase105.toml_to_xml,
    "json_schema_compile": suite_phase105.json_schema_compile,
    "xml_to_yaml": suite_phase105.xml_to_yaml,
    "json_schema_docs": suite_phase105.json_schema_docs,
    "json_to_ini": suite_phase106.json_to_ini,
    "csv_to_ini": suite_phase106.csv_to_ini,
    "xml_to_toml": suite_phase106.xml_to_toml,
    "yaml_to_xml": suite_phase106.yaml_to_xml,
    "json_schema_to_ts": suite_phase106.json_schema_to_ts,
    "ini_to_yaml": suite_phase106.ini_to_yaml,
    "json_to_toml": suite_phase106.json_to_toml,
    "xml_to_ini": suite_phase107.xml_to_ini,
    "toml_to_ini": suite_phase107.toml_to_ini,
    "csv_to_toml": suite_phase107.csv_to_toml,
    "json_schema_to_python": suite_phase107.json_schema_to_python,
    "yaml_to_csv": suite_phase107.yaml_to_csv,
    "ini_to_xml": suite_phase107.ini_to_xml,
    "toml_to_csv": suite_phase107.toml_to_csv,
    "json_schema_to_go": suite_phase108.json_schema_to_go,
    "json_schema_to_java": suite_phase108.json_schema_to_java,
    "markdown_table_to_csv": suite_phase108.markdown_table_to_csv,
    "sql_to_json": suite_phase108.sql_to_json,
    "env_to_json": suite_phase108.env_to_json,
    "dockerfile_lint": suite_phase108.dockerfile_lint,
    "gitignore_gen": suite_phase108.gitignore_gen,
    "jwt_decode": suite_phase109.jwt_decode,
    "url_parse": suite_phase109.url_parse,
    "markdown_toc": suite_phase109.markdown_toc,
    "text_stats": suite_phase109.text_stats,
    "csv_to_markdown": suite_phase109.csv_to_markdown,
    "env_lint": suite_phase109.env_lint,
    "requirements_diff": suite_phase109.requirements_diff,
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
_READONLY_TOOLS = {"read_file", "list_dir", "glob", "grep", "diff_view", "repo_map", "symbol_search", "review_code", "semantic_search", "impact_analysis", "compare_options", "generate_project_docs", "lint_code", "db_run", "read_pdf", "read_office", "data_table", "backup_list", "template_list", "template_get", "secret_list", "secret_get", "snippet_list", "snippet_get", "note_list", "note_get", "todo_list", "translate", "summarize", "pdf_extract", "data_analysis", "db_query", "deep_review", "clipboard", "code_metrics", "text_compare", "db_schema_doc", "form_validate", "code_search_semantic", "webhook_sign", "db_diff", "code_search_ast", "json_query", "env_check", "webhook_verify", "sql_format", "csv_diff", "json_schema_validate", "release_tag", "log_tail", "password_generate", "sql_explain", "csv_to_json", "hash_file", "cron_parse", "text_diff", "yaml_query", "sql_lint", "json_schema_gen", "cron_next_n", "diff_patch", "yaml_merge", "hash_verify", "secret_audit", "dep_check", "license_check", "perm_diff", "xml_query", "toml_query", "xml_to_json", "toml_to_json", "json_patch", "sbom_gen", "dep_graph", "yaml_to_json", "json_to_yaml", "xml_to_csv", "toml_to_yaml", "license_compat", "dep_outdated", "file_classify", "json_pointer", "csv_to_xml", "yaml_to_toml", "ini_query", "ini_to_json", "license_list", "json_to_xml", "csv_to_yaml", "yaml_to_ini", "toml_to_xml", "json_schema_compile", "xml_to_yaml", "json_schema_docs", "json_schema_lint", "json_to_ini", "csv_to_ini", "xml_to_toml", "yaml_to_xml", "json_schema_to_ts", "ini_to_yaml", "json_to_toml", "xml_to_ini", "toml_to_ini", "csv_to_toml", "json_schema_to_python", "yaml_to_csv", "ini_to_xml", "toml_to_csv", "json_schema_to_go", "json_schema_to_java", "markdown_table_to_csv", "sql_to_json", "env_to_json", "dockerfile_lint", "gitignore_gen", "jwt_decode", "url_parse", "markdown_toc", "text_stats", "csv_to_markdown", "env_lint", "requirements_diff"}
_WRITE_TOOLS = {"write_file", "edit_file", "apply_patch", "insert_at", "replace_in_files", "undo", "format_code", "make_doc", "backup_create", "backup_rollback", "backup_delete", "template_save", "template_delete", "secret_set", "secret_delete", "snippet_save", "snippet_delete", "note_save", "note_delete", "todo_add", "todo_done", "todo_delete",
    "git_checkout", "git_stash", "git_pr_draft", "image_generate", "tts", "transcribe", "video_generate", "make_ppt", "make_xlsx", "make_pdf", "schedule_task", "notify", "mindmap", "markdown_to_docx",
    "diagram", "chart", "email_compose", "calendar_event", "knowledge_search", "pdf_make",
    "formatter", "screenshot", "csv_convert", "agent_team", "db_migrate",
    "pdf_merge", "pdf_split", "form_to_pdf", "agent_team_run", "pdf_redact", "release_notes", "template_render",     "changelog_update", "csv_merge", "json_to_csv", "json_to_sql", "secret_mask"}
_EXEC_TOOLS = {"run_command", "auto_test", "git_commit", "run_server", "http_request", "webhook_send", "webhook_emit", "api_test",
    "flow_runner", "local_llm_route", "webhook_dispatch"}


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
            return False, (f"当前为「计划模式」, 禁止 {name} "
                           f"(只读工具可用, 如 read_file / list_dir / grep / glob / diff_view)。")
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
