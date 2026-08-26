"""上下文操作 (Context Ops): 对会话上下文做 压缩 / 整理 / 拆解。

作用于一段 messages(来自 ~/.lingmengwork/sessions/<id>.json 的 messages 字段,
或前端直接提交的 messages 列表)。

双模实现:
- **规则版** (_rule_*): 纯确定性, 不依赖 LLM, 永远可用。
- **LLM 版**: 当传入 llm_call(turn_text)->str 回调时, 用它做整体合成, 输出更贴合语义的
  Markdown; 若 LLM 不可用 / 调用失败 / 返回过短, 自动回退到规则版(保证不降级)。

messages 元素约定: {"role": "user"|"assistant"|"tool"|"system", "content": str | list}
产出均为 Markdown 文本, 可直接存入「记忆」或「计划书」。
"""
from datetime import datetime

ROLE_LABEL = {
    "user": "用户",
    "assistant": "助手",
    "tool": "工具",
    "system": "系统",
}

# ---- LLM 系统提示 (用于整体合成) ----
_COMPRESS_SYS = (
    "你是顶级的对话上下文压缩器。基于给定的对话转录, 输出一份 Markdown 压缩报告, "
    "必须包含以下小节(用 ## 标题):\n"
    "## 🎯 会话目标\n一行概括用户的根本意图。\n"
    "## ✅ 关键结论\n用 - 列出已达成的关键结论/交付物(无则写 无)。\n"
    "## 🔄 对话脉络\n用 1. **[角色]** 摘要 的形式逐轮精炼(每轮不超过一句)。\n"
    "只输出 Markdown, 不要额外解释。"
)
_ORGANIZE_SYS = (
    "你是顶级的对话整理助手。基于给定的对话转录, 输出一份结构化 Markdown 笔记, "
    "必须包含以下小节(用 ## 标题):\n"
    "## 🎯 目标\n## 🛠 已执行的步骤\n## 🔍 关键发现\n## 🧭 决策与方案\n"
    "## ⚠️ 风险与待确认\n## 📋 用户诉求清单\n"
    "每节用 - 列出要点(无则写 (无))。只输出 Markdown, 不要额外解释。"
)
_DECOMPOSE_SYS = (
    "你是顶级的任务拆解专家。基于给定的对话转录(尤其是用户的目标与助手的规划), "
    "输出一份 Markdown 任务拆解, 必须包含:\n"
    "## 🎯 总目标\n一行。\n## 🧩 拆解出的子任务\n用 1. [ ] 子任务 的复选框形式列出可执行的子任务"
    "(最多 20 条, 去重, 每条是一个具体动作)。\n"
    "只输出 Markdown, 不要额外解释。")


def _text(content):
    """把 message.content(可能是 str / list[dict] / list[str]) 还原为纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content") or ""
                if isinstance(t, list):
                    t = " ".join(str(x) for x in t)
                parts.append(str(t))
        return "\n".join(p for p in parts if p)
    return str(content)


def _turns(messages):
    """过滤出 user/assistant 的有效轮次(丢弃纯 tool 噪音), 返回 [(role,text)]。"""
    out = []
    for m in messages or []:
        role = (m.get("role") or "").lower()
        text = _text(m.get("content"))
        if not text.strip():
            continue
        out.append((role, text))
    return out


def _clip(text, n=240):
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


def _extract_goal(turns):
    for role, text in turns:
        if role == "user" and text.strip():
            return text.strip()
    return "(未识别到明确的用户目标)"


def _dump(turns, limit_chars=6000):
    """把轮次拼成给 LLM 看的紧凑转录文本(截断保护)。"""
    buf = []
    total = 0
    for role, text in turns:
        label = ROLE_LABEL.get(role, role)
        seg = "[%s] %s" % (label, text.strip())
        if total + len(seg) > limit_chars and buf:
            buf.append("…(已截断)")
            break
        buf.append(seg)
        total += len(seg)
    return "\n".join(buf)


def _ask(llm_call, system, user):
    """安全调用 LLM; 任何异常返回 None。"""
    if not llm_call:
        return None
    try:
        resp = llm_call(user, system=system)
        if isinstance(resp, str) and len(resp.strip()) > 20:
            return resp.strip()
    except Exception:
        pass
    return None


# =====================================================================
# 规则版 (确定性, 永远可用)
# =====================================================================
def _rule_compress(turns):
    goal = _extract_goal(turns)
    lines = ["# 上下文压缩报告", "",
             "> 生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
             "## 🎯 会话目标", "", goal, ""]
    conclusions = []
    for role, text in turns:
        if role == "assistant":
            low = text.lower()
            if any(k in low for k in ("最终结论", "总结", "结论:", "已完成", "已交付", "deliver", "总结如下", "交付物")):
                conclusions.append(_clip(text, 200))
    if conclusions:
        lines += ["## ✅ 关键结论", ""]
        for c in conclusions[:8]:
            lines.append("- %s" % c)
        lines.append("")
    lines += ["## 🔄 对话脉络(逐轮摘要)", ""]
    idx = 0
    for role, text in turns:
        label = ROLE_LABEL.get(role, role)
        summary = _clip(text, 180)
        idx += 1
        lines.append("%d. **[%s]** %s" % (idx, label, summary))
    lines.append("")
    lines.append("> 共 %d 条有效消息被压缩为 %d 行脉络。" % (len(turns), len(turns)))
    return "\n".join(lines)


def _rule_organize(turns):
    goal = _extract_goal(turns)
    steps, findings, decisions, risks, todos = [], [], [], [], []
    for role, text in turns:
        low = text.lower()
        if role == "assistant":
            if any(k in low for k in ("发现", "检测", "注意到", "排查", "根因", "报错", "error", "异常")):
                findings.append(_clip(text, 160))
            if any(k in low for k in ("决定", "采用", "选择", "方案", "策略", "建议")):
                decisions.append(_clip(text, 160))
            if any(k in low for k in ("待办", "下一步", "计划", "TODO", "需要", "风险", "注意")):
                risks.append(_clip(text, 160))
        if role == "user" and any(k in low for k in ("请", "需要", "做", "实现", "修复", "添加", "创建")):
            todos.append(_clip(text, 160))
    lines = ["# 上下文整理笔记", "",
             "> 生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
             "## 🎯 目标", "", goal, "",
             "## 🛠 已执行的步骤", ""]
    if steps:
        lines += ["- %s" % s for s in steps]
    else:
        lines.append("(未识别到明确步骤)")
    lines += ["", "## 🔍 关键发现", ""]
    lines += ["- %s" % f for f in findings] or ["(无)"]
    lines += ["", "## 🧭 决策与方案", ""]
    lines += ["- %s" % d for d in decisions] or ["(无)"]
    lines += ["", "## ⚠️ 风险与待确认", ""]
    lines += ["- %s" % r for r in risks] or ["(无)"]
    lines += ["", "## 📋 用户诉求清单", ""]
    lines += ["- %s" % t for t in todos] or ["(无)"]
    return "\n".join(lines)


def _rule_decompose(turns):
    goal = _extract_goal(turns)
    tasks = []
    for role, text in turns:
        if role == "user":
            for line in text.splitlines():
                s = line.strip().lstrip("-•*0123456789. ").strip()
                if len(s) >= 4 and any(k in s for k in ("请", "实现", "添加", "创建", "修复", "写", "做", "生成", "配置", "设计", "重构")):
                    tasks.append(s)
        elif role == "assistant":
            low = text.lower()
            if "任务分解" in text or "拆解" in text or "子任务" in text or "todo" in low:
                for line in text.splitlines():
                    s = line.strip().lstrip("-•*0123456789. ").strip()
                    if s and any(k in s for k in ("任务", "步骤", "stage", "phase", "实现", "开发")):
                        tasks.append(s)
    seen, uniq = set(), []
    for t in tasks:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    lines = ["# 上下文任务拆解", "",
             "> 生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
             "## 🎯 总目标", "", goal, "",
             "## 🧩 拆解出的子任务", ""]
    if uniq:
        for i, t in enumerate(uniq[:30], 1):
            lines.append("%d. [ ] %s" % (i, _clip(t, 160)))
    else:
        lines.append("(未能自动识别子任务; 建议结合 LLM 进一步拆解)")
    lines.append("")
    lines.append("> 提示: 可将本清单一键存入「计划书」的任务清单。")
    return "\n".join(lines)


# =====================================================================
# 公开 API (LLM 驱动 + 规则回退)
# =====================================================================
def compress(messages, llm_call=None):
    """压缩: 保留目标 + 关键结论 + 逐轮精摘要, 形成「可回看」的浓缩上下文。"""
    turns = _turns(messages)
    if llm_call:
        md = _ask(llm_call, _COMPRESS_SYS, "对话转录:\n" + _dump(turns))
        if md:
            return md
    return _rule_compress(turns)


def organize(messages, llm_call=None):
    """整理: 把上下文重新组织为结构化笔记(目标/步骤/发现/决策/风险/诉求)。"""
    turns = _turns(messages)
    if llm_call:
        md = _ask(llm_call, _ORGANIZE_SYS, "对话转录:\n" + _dump(turns))
        if md:
            return md
    return _rule_organize(turns)


def decompose(messages, llm_call=None):
    """拆解: 把会话目标拆为可执行的子任务清单(Markdown 复选框)。"""
    turns = _turns(messages)
    if llm_call:
        md = _ask(llm_call, _DECOMPOSE_SYS, "对话转录:\n" + _dump(turns))
        if md:
            return md
    return _rule_decompose(turns)
