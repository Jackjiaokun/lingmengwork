"""四大创作域注册 / 路由 / 统一创作工作台 后端引擎 (终极蓝图 Phase 4).

四域(编程/音频/图片/视频)共享同一上下文与记忆, 经统一工作台串联为跨域流水线。
- 编程域 adapter=native: 由内置 AgentLoop / 工具直接执行(此处产出可执行方案骨架)。
- 音频/图片/视频域 adapter=mcp: 真实媒体生成预留 MCP / API 接入点(配置 key 后由适配层产出文件),
  当前阶段用 LLM 产出该域「可执行的创作蓝图 / 脚本 / 提示词」(不伪造媒体文件)。

所有域均用 LLM 驱动产出结构化方案, 失败/无 key 时回退规则版, 保证工作台始终可用。
"""

# 四域注册表 (主题色与《终极蓝图》能力矩阵一致)
DOMAINS = {
    "code": {
        "id": "code", "name": "编程", "emoji": "\U0001F7E3", "theme": "#8b5cf6",
        "desc": "读/写/跑/重构/调试/多文件、工具链、单测、评审",
        "delivery": "代码 + 评审报告 + PR 草稿",
        "capabilities": ["读码", "写码", "运行", "重构", "调试", "多文件编辑", "单元测试", "代码评审"],
        "adapter": "native",
        "status": "ready",
    },
    "audio": {
        "id": "audio", "name": "音频", "emoji": "\U0001F7E2", "theme": "#10b981",
        "desc": "语音合成(TTS)、语音转写(STT)、音效/配乐生成、音频清洗",
        "delivery": "音频文件 + 字幕/脚本",
        "capabilities": ["TTS 语音合成", "STT 语音转写", "音效/配乐", "音频清洗"],
        "adapter": "native",
        "status": "ready",
    },
    "image": {
        "id": "image", "name": "图片", "emoji": "\U0001F338", "theme": "#f472b6",
        "desc": "文生图、图生图、智能编辑/抠图/标注、版式设计",
        "delivery": "图片集 + 说明",
        "capabilities": ["文生图", "图生图", "智能抠图", "标注", "版式设计"],
        "adapter": "native",
        "status": "ready",
    },
    "video": {
        "id": "video", "name": "视频", "emoji": "\U0001F535", "theme": "#6366f1",
        "desc": "文生视频、脚本→分镜→成片、字幕/配音合成、剪辑封装",
        "delivery": "视频文件 + 工程",
        "capabilities": ["文生视频", "分镜脚本", "字幕合成", "配音合成", "剪辑封装"],
        "adapter": "native",
        "status": "ready",
    },
}

DOMAIN_ORDER = ["code", "audio", "image", "video"]

# 各域装配的工具组合 (Phase 90)。
#
# 设计原则:
# 1) 共用底座: read_file / list_dir / glob / think / todo / memory 任何创作都要用。
# 2) 域差异体现在「执行手段」上, 而不是简单的增删读写:
#    - code : 能跑能测能评审 —— 执行类 + 静态检查 + 版本 + 代码智能
#    - image: 素材盘点 + 提示词/说明产出 —— 读素材(含 pdf/office) + 文档表格
#    - audio: 脚本/字幕/参数 —— 文本处理 + 片段库(存 TTS 文本与音色参数)
#    - video: 分镜 + 工程备份 + 外部命令(ffmpeg 之类) —— 比音频多执行与备份
# 3) 工具名必须与 tools/registry.py 的 _IMPLS / 特殊工具一致,
#    由 tests/test_phase90_domain_modes.py 校验, 防止写了不存在的工具名。
_SHARED = ["read_file", "list_dir", "glob", "think", "todo", "memory"]

DOMAIN_TOOLS = {
    "code": _SHARED + [
        "write_file", "edit_file", "apply_patch", "insert_at", "replace_in_files",
        "diff_view", "grep",
        "run_command", "auto_test", "lint_code", "format_code", "run_server",
        "repo_map", "symbol_search", "semantic_search", "review_code",
        "git_commit", "git_status", "git_diff", "git_log", "git_branch",
        "git_checkout", "git_stash", "git_pr_draft",
        "impact_analysis", "compare_options",
        "web_fetch", "http_request", "test_gen", "explain_code", "security_scan",
        "mindmap", "translate", "summarize", "pdf_extract", "markdown_to_docx", "data_analysis", "db_query",
        "diagram", "chart", "api_test", "email_compose", "calendar_event", "knowledge_search", "pdf_make",
        "flow_runner", "formatter", "deep_review", "local_llm_route", "screenshot", "clipboard", "csv_convert",
        "code_metrics", "agent_team", "db_migrate", "pdf_merge", "pdf_split", "form_to_pdf", "text_compare",
        "agent_team_run", "pdf_redact", "db_schema_doc", "form_validate", "release_notes", "code_search_semantic", "template_render", "webhook_sign", "db_diff", "changelog_update", "code_search_ast", "csv_merge", "json_query", "env_check", "webhook_verify", "sql_format", "csv_diff", "json_schema_validate", "release_tag", "log_tail", "password_generate", "webhook_emit", "sql_explain", "csv_to_json", "hash_file", "cron_parse", "text_diff", "yaml_query", "webhook_dispatch", "sql_lint", "json_schema_gen", "cron_next_n", "diff_patch", "yaml_merge", "hash_verify", "secret_audit", "dep_check", "license_check", "perm_diff", "json_to_csv", "xml_query", "toml_query", "xml_to_json", "json_to_sql", "toml_to_json", "json_patch", "secret_mask", "sbom_gen", "dep_graph",
        "yaml_to_json", "json_to_yaml", "xml_to_csv", "toml_to_yaml", "license_compat", "dep_outdated", "file_classify", "json_pointer", "csv_to_xml", "yaml_to_toml", "ini_query", "ini_to_json", "license_list", "json_schema_lint", "json_to_xml", "csv_to_yaml", "yaml_to_ini", "toml_to_xml", "json_schema_compile", "xml_to_yaml", "json_schema_docs", "json_to_ini", "csv_to_ini", "xml_to_toml", "yaml_to_xml", "json_schema_to_ts", "ini_to_yaml", "json_to_toml", "xml_to_ini", "toml_to_ini", "csv_to_toml", "json_schema_to_python", "yaml_to_csv", "ini_to_xml", "toml_to_csv", "json_schema_to_go", "json_schema_to_java", "markdown_table_to_csv", "sql_to_json", "env_to_json", "dockerfile_lint", "gitignore_gen", "jwt_decode", "url_parse", "markdown_toc", "text_stats", "csv_to_markdown", "env_lint", "requirements_diff", "openapi_gen", "json_minify", "regex_test", "semver_compare", "sql_validate", "cron_validate", "base64_codec",

        "undo", "subagent",
    ],
    "image": _SHARED + [
        "write_file", "make_doc", "make_ppt", "make_pdf", "data_table",
        "read_pdf", "read_office",
        "image_generate", "image_understand", "ocr",
        "mindmap", "markdown_to_docx", "pdf_extract", "summarize",
        "diagram", "chart", "pdf_make", "knowledge_search",
        "note_save", "note_list", "snippet_save", "snippet_list",
        "flow_runner", "formatter", "deep_review", "local_llm_route", "screenshot", "clipboard", "csv_convert",
        "db_migrate", "pdf_merge", "pdf_split", "form_to_pdf", "text_compare",
        "db_schema_doc", "form_validate", "release_notes", "code_search_semantic", "template_render", "db_diff", "csv_merge", "json_query", "env_check", "code_search_ast", "changelog_update", "webhook_verify", "sql_format", "csv_diff", "json_schema_validate", "release_tag", "log_tail", "webhook_dispatch", "sql_lint", "json_schema_gen", "cron_next_n", "diff_patch", "yaml_merge", "hash_verify", "xml_to_json", "toml_to_json", "json_patch", "sbom_gen", "dep_graph",
        "yaml_to_json", "json_to_yaml", "xml_to_csv", "toml_to_yaml", "license_compat", "dep_outdated", "file_classify", "json_pointer", "csv_to_xml", "yaml_to_toml", "ini_query", "ini_to_json", "license_list", "json_schema_lint", "json_to_xml", "csv_to_yaml", "yaml_to_ini", "toml_to_xml", "json_schema_compile", "xml_to_yaml", "json_schema_docs", "json_to_ini", "csv_to_ini", "xml_to_toml", "yaml_to_xml", "json_schema_to_ts", "ini_to_yaml", "json_to_toml", "xml_to_ini", "toml_to_ini", "csv_to_toml", "json_schema_to_python", "yaml_to_csv", "ini_to_xml", "toml_to_csv", "json_schema_to_go", "json_schema_to_java", "markdown_table_to_csv", "sql_to_json", "env_to_json", "dockerfile_lint", "gitignore_gen", "jwt_decode", "url_parse", "markdown_toc", "text_stats", "csv_to_markdown", "env_lint", "requirements_diff", "openapi_gen", "json_minify", "regex_test", "semver_compare", "sql_validate", "cron_validate", "base64_codec",
    ],
    "audio": _SHARED + [
        "write_file", "make_doc", "data_table",
        "tts", "transcribe",
        "translate", "summarize",
        "email_compose", "calendar_event", "pdf_make", "knowledge_search",
        "note_save", "note_list", "note_get",
        "snippet_save", "snippet_list", "snippet_get",
        "formatter", "deep_review", "local_llm_route", "clipboard", "csv_convert",
        "code_metrics", "db_migrate", "pdf_merge", "pdf_split", "form_to_pdf", "text_compare",
        "form_validate", "release_notes", "template_render", "code_search_semantic", "json_query", "env_check", "db_diff", "sql_format", "csv_diff", "json_schema_validate", "release_tag", "log_tail", "webhook_dispatch", "sql_lint", "json_schema_gen", "cron_next_n", "diff_patch", "yaml_merge", "hash_verify", "xml_to_json", "toml_to_json", "json_patch", "sbom_gen", "dep_graph",
        "yaml_to_json", "json_to_yaml", "xml_to_csv", "toml_to_yaml", "license_compat", "dep_outdated", "file_classify", "json_pointer", "csv_to_xml", "yaml_to_toml", "ini_query", "ini_to_json", "license_list", "json_schema_lint", "json_to_xml", "csv_to_yaml", "yaml_to_ini", "toml_to_xml", "json_schema_compile", "xml_to_yaml", "json_schema_docs", "json_to_ini", "csv_to_ini", "xml_to_toml", "yaml_to_xml", "json_schema_to_ts", "ini_to_yaml", "json_to_toml", "xml_to_ini", "toml_to_ini", "csv_to_toml", "json_schema_to_python", "yaml_to_csv", "ini_to_xml", "toml_to_csv", "json_schema_to_go", "json_schema_to_java", "markdown_table_to_csv", "sql_to_json", "env_to_json", "dockerfile_lint", "gitignore_gen", "jwt_decode", "url_parse", "markdown_toc", "text_stats", "csv_to_markdown", "env_lint", "requirements_diff", "openapi_gen", "json_minify", "regex_test", "semver_compare", "sql_validate", "cron_validate", "base64_codec",
    ],
    "video": _SHARED + [
        "write_file", "edit_file", "make_doc", "make_ppt", "data_table",
        "run_command",
        "video_generate",
        "mindmap", "summarize",
        "diagram", "chart", "pdf_make", "knowledge_search",
        "email_compose", "calendar_event",
        "backup_create", "backup_list",
        "note_save", "note_list",
        "flow_runner", "formatter", "deep_review", "local_llm_route", "screenshot", "clipboard", "csv_convert",
        "code_metrics", "agent_team", "db_migrate", "pdf_merge", "pdf_split", "form_to_pdf", "text_compare",
        "agent_team_run", "db_schema_doc", "form_validate", "release_notes", "code_search_semantic", "template_render", "db_diff", "csv_merge", "json_query", "env_check", "code_search_ast", "changelog_update", "webhook_verify", "sql_format", "csv_diff", "json_schema_validate", "release_tag", "log_tail", "webhook_dispatch", "sql_lint", "json_schema_gen", "cron_next_n", "diff_patch", "yaml_merge", "hash_verify", "xml_to_json", "toml_to_json", "json_patch", "sbom_gen", "dep_graph",
        "yaml_to_json", "json_to_yaml", "xml_to_csv", "toml_to_yaml", "license_compat", "dep_outdated", "file_classify", "json_pointer", "csv_to_xml", "yaml_to_toml", "ini_query", "ini_to_json", "license_list", "json_schema_lint", "json_to_xml", "csv_to_yaml", "yaml_to_ini", "toml_to_xml", "json_schema_compile", "xml_to_yaml", "json_schema_docs", "json_to_ini", "csv_to_ini", "xml_to_toml", "yaml_to_xml", "json_schema_to_ts", "ini_to_yaml", "json_to_toml", "xml_to_ini", "toml_to_ini", "csv_to_toml", "json_schema_to_python", "yaml_to_csv", "ini_to_xml", "toml_to_csv", "json_schema_to_go", "json_schema_to_java", "markdown_table_to_csv", "sql_to_json", "env_to_json", "dockerfile_lint", "gitignore_gen", "jwt_decode", "url_parse", "markdown_toc", "text_stats", "csv_to_markdown", "env_lint", "requirements_diff", "openapi_gen", "json_minify", "regex_test", "semver_compare", "sql_validate", "cron_validate", "base64_codec",
    ],
}


def tools_for_domain(domain):
    """返回该域装配的工具名列表(顺序稳定); 未知域返回 None(表示不过滤)。"""
    return list(DOMAIN_TOOLS.get(domain) or []) or None


def list_modes():
    """四种工作模式完整信息(域元信息 + 装配的工具组合), 供前端模式选择器使用。"""
    out = []
    for d in DOMAIN_ORDER:
        m = dict(DOMAINS[d])
        m["tools"] = tools_for_domain(d) or []
        out.append(m)
    return out

# 各域的 LLM 系统提示词: 把用户 brief 转成该域「可执行的创作蓝图 / 脚本 / 提示词」
_PROMPTS = {
    "code": (
        "你是灵梦work的资深全栈工程师 Agent。用户给出一个编程创作需求, "
        "请产出可直接落地的工程方案。用 Markdown 输出, 必须包含三段标题:\n"
        "## 方案\n(架构选择 / 技术栈 / 模块划分, 3-6 条)\n"
        "## 关键产出\n(将交付的核心文件 / 接口 / 函数, 列出文件名与职责)\n"
        "## 执行步骤\n(有序步骤, 含构建 / 测试 / 评审 / PR 草稿)\n"
        "若需求涉及具体代码, 在「关键产出」中以代码块给出骨架。"
    ),
    "audio": (
        "你是灵梦work的音频制作导演 Agent。用户给出音频创作需求(配音/转写/配乐/清洗), "
        "请产出可交给音频管线执行的制作蓝图。用 Markdown 输出, 必须包含三段标题:\n"
        "## 方案\n(语音合成 vs 转写 vs 配乐 vs 清洗的路线与参数, 如 TTS 音色/语速/语言)\n"
        "## 关键产出\n(将交付的音频文件 / 字幕 / 脚本清单)\n"
        "## 执行步骤\n(有序步骤, 含素材准备 / 合成 / 校验)\n"
    ),
    "image": (
        "你是灵梦work的视觉设计 Agent。用户给出图片创作需求(文生图/图生图/抠图/版式), "
        "请产出可交给图像管线执行的制作蓝图。用 Markdown 输出, 必须包含三段标题:\n"
        "## 方案\n(构图 / 风格 / 版式走向)\n"
        "## 关键产出\n(中英文文生图提示词 prompt + 产出图片清单与尺寸)\n"
        "## 执行步骤\n(有序步骤, 含参考图 / 生成 / 智能编辑 / 标注)\n"
    ),
    "video": (
        "你是灵梦work的视频导演 Agent。用户给出视频创作需求(文生视频/脚本→分镜→成片), "
        "请产出可交给视频管线执行的制作蓝图。用 Markdown 输出, 必须包含三段标题:\n"
        "## 方案\n(叙事结构 / 时长 / 画幅 / 风格)\n"
        "## 关键产出\n(分镜脚本: 镜头/画面/旁白/时长; 配音与字幕方案; 产出视频与工程)\n"
        "## 执行步骤\n(有序步骤, 含脚本→分镜→生成→配音→字幕→剪辑封装)\n"
    ),
}


def list_domains():
    """返回四域元信息列表 (按固定顺序)。"""
    return [dict(DOMAINS[d]) for d in DOMAIN_ORDER]


def dispatch(domain, brief, context="", llm_call=None):
    """将创作需求路由到指定域, 产出该域 LLM 驱动的结构化创作蓝图。

    domain: code/audio/image/video
    brief: 用户创作需求
    context: 可选补充上下文
    llm_call: llm_call(prompt, system=None)->str|None (由 server._make_llm_call 注入)
    返回 dict: 含 domain/plan/adapter_hint 等。
    """
    if domain not in DOMAINS:
        raise ValueError("未知创作域: %s (可选: %s)" % (domain, ", ".join(DOMAIN_ORDER)))
    dom = DOMAINS[domain]
    system = _PROMPTS[domain]
    user = "创作需求: %s\n" % (brief or "").strip()
    if context and context.strip():
        user += "补充上下文: %s\n" % context.strip()
    user += "请严格按系统提示的三段标题(## 方案 / ## 关键产出 / ## 执行步骤)输出中文。"

    plan = ""
    if llm_call:
        try:
            out = llm_call(user, system=system)
            if isinstance(out, str):
                plan = out.strip()
        except Exception:
            plan = ""
    if not plan:
        plan = _rule_fallback(domain, brief, context)

    return {
        "ok": True,
        "domain": domain,
        "domain_name": dom["name"],
        "emoji": dom["emoji"],
        "theme": dom["theme"],
        "adapter": dom["adapter"],
        "status": dom["status"],
        "brief": brief,
        "plan": plan,
        "adapter_hint": _adapter_hint(domain) if dom["adapter"] == "mcp" else "",
    }


def _adapter_hint(domain):
    return {
        "audio": "真实音频生成需接入 TTS/STT 服务(MCP 或 API), 配置 key 后由适配层产出音频文件。",
        "image": "真实图片生成需接入文生图模型(MCP 或 API), 配置 key 后由适配层产出图片集。",
        "video": "真实视频生成需接入文生视频/剪辑管线(MCP 或 API), 配置 key 后由适配层产出视频。",
    }.get(domain, "")


def _rule_fallback(domain, brief, context):
    dom = DOMAINS[domain]
    ctx = (" 补充上下文: %s" % context.strip()) if context and context.strip() else ""
    return (
        "## 方案\n"
        "针对【%s】域需求「%s」的初步规划: 明确交付物 -> 拆分子任务 -> 调用%s能力。%s\n\n"
        "## 关键产出\n%s\n\n"
        "## 执行步骤\n"
        "1) 明确交付物与验收标准\n"
        "2) 拆解为可并行/串行的子任务\n"
        "3) 调用对应工具或预留 MCP/API 适配层\n"
        "4) 校验产出并回写记忆\n"
    ) % (dom["name"], brief, dom["name"], ctx, dom["delivery"])
