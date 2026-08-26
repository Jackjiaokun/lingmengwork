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
        "adapter": "mcp",
        "status": "pending_adapter",
    },
    "image": {
        "id": "image", "name": "图片", "emoji": "\U0001F338", "theme": "#f472b6",
        "desc": "文生图、图生图、智能编辑/抠图/标注、版式设计",
        "delivery": "图片集 + 说明",
        "capabilities": ["文生图", "图生图", "智能抠图", "标注", "版式设计"],
        "adapter": "mcp",
        "status": "pending_adapter",
    },
    "video": {
        "id": "video", "name": "视频", "emoji": "\U0001F535", "theme": "#6366f1",
        "desc": "文生视频、脚本→分镜→成片、字幕/配音合成、剪辑封装",
        "delivery": "视频文件 + 工程",
        "capabilities": ["文生视频", "分镜脚本", "字幕合成", "配音合成", "剪辑封装"],
        "adapter": "mcp",
        "status": "pending_adapter",
    },
}

DOMAIN_ORDER = ["code", "audio", "image", "video"]

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
