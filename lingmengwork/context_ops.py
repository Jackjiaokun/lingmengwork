"""上下文操作 (Context Ops): 对会话上下文做 压缩 / 整理 / 拆解。

作用于一段 messages(来自 ~/.lingmengwork/sessions/<id>.json 的 messages 字段,
或前端直接提交的 messages 列表)。均为**规则版**实现, 不依赖 LLM 即可运行;
若传入 llm_call(turn_text)->str 回调, 则在各阶段用它做增强摘要(可选)。

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
                # Anthropic 风格: {"type":"text","text":...} 或 {"type":"tool_result","content":...}
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


def compress(messages, llm_call=None):
    """压缩: 保留目标 + 每轮一行的精简摘要, 形成「可回看」的浓缩上下文。"""
    turns = _turns(messages)
    goal = _extract_goal(turns)
    lines = ["# 上下文压缩报告", "",
             "> 生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
             "## 🎯 会话目标", "", goal, ""]
    # 关键结论: assistant 中出现的「结论/总结/最终/完成/交付」类表述
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
        if llm_call and role == "assistant":
            try:
                s2 = llm_call(text)
                if s2 and s2.strip():
                    summary = _clip(s2, 180)
            except Exception:
                pass
        idx += 1
        lines.append("%d. **[%s]** %s" % (idx, label, summary))
    lines.append("")
    lines.append("> 共 %d 条有效消息被压缩为 %d 行脉络。" % (len(turns), len(turns)))
    return "\n".join(lines)


def organize(messages, llm_call=None):
    """整理: 把上下文重新组织为结构化笔记(目标/步骤/发现/决策/风险)。"""
    turns = _turns(messages)
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


def decompose(messages, llm_call=None):
    """拆解: 把会话目标拆为可执行的子任务清单(Markdown 复选框)。"""
    turns = _turns(messages)
    goal = _extract_goal(turns)
    tasks = []
    for role, text in turns:
        # 用户诉求 / 助手规划中的动作点
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
    # 去重保序
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
