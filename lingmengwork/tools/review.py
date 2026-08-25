"""代码评审自评估 (Critic Loop): 写-审-改质量门禁 (领先一代 agent 的核心能力)。

- review_code: 主入口工具。以零依赖「静态评审」(py_compile 语法 + 规则扫描) 为内核,
  输出确定、可测、跨 mock/真实后端一致; 可选叠加 LLM 评审子代理 (critic) 做语义层增强。
- 返回结构化文本: VERDICT(approve|revise) / SCORE(0-100) / ISSUES / SUGGESTIONS / SUMMARY。
- 主 Agent 在写关键代码后调 review_code 自检; verdict=revise 时按 ISSUES 修改后再次 review,
  形成「写-审-改」闭环 (见 prompt.py 规则 13), 与 auto_test 红绿自愈、subagent 并发构成完整质量体系。

设计要点:
- 静态评审零依赖, 是 mock/测试/无网环境的确定性回退, 也是真实 LLM 评审的兜底。
- critic 子代理复用 subagent 的 _run_one_subagent (只读语义, 仅取文本, 不写文件)。
- 解析 critic 文本里的 VERDICT/SCORE 标记; 解析失败或无 LLM 时信任静态结果。
"""

import os
import re
import tempfile

# ---- 静态评审规则权重 ----
_W_HIGH = 15   # 高: 语法错误 / 裸 except 吞异常
_W_MED = 8     # 中: TODO/FIXME 占位 / import * / 疑似空实现
_W_LOW = 3     # 低: print 调试残留 / 超长行 / 裸 ... 占位


def _check_syntax(text, filename):
    """用 py_compile 检查 .py 语法, 返回 (ok, issue_str_or_None)。"""
    if not (filename or "").endswith(".py"):
        return True, None
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(text)
            tmp = f.name
        import py_compile
        py_compile.compile(tmp, doraise=True)
        return True, None
    except Exception as e:  # py_compile.PyCompileError 或 IOError
        msg = str(e)
        m = re.search(r"line (\d+)", msg)
        ln = m.group(1) if m else "?"
        return False, f"语法错误 (line {ln}): {msg.splitlines()[0][:80]}"
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


def _scan_rules(text, filename):
    """通用规则扫描, 返回 [(severity, desc), ...]。severity ∈ 高/中/低。"""
    issues = []
    lines = text.splitlines()
    in_py = (filename or "").endswith(".py")
    for i, line in enumerate(lines, 1):
        s = line.strip()
        # 裸 except: (吞异常, 高) —— 用 re.search 兼容缩进 (re.match start-anchored 会漏掉缩进行)
        if re.search(r"except\s*:\s*(#|$)", line):
            issues.append(("高", f"裸 except 吞异常 (line {i})——至少捕获 Exception 并日志化"))
        # import * (命名空间污染, 中)
        if re.search(r"from\s+\S+\s+import\s+\*", line) or re.search(r"import\s+\*", line):
            issues.append(("中", f"import * 污染命名空间 (line {i})"))
        # TODO/FIXME/XXX 占位 (中)
        if re.search(r"\b(TODO|FIXME|XXX)\b", line):
            issues.append(("中", f"含未完成占位标记 (line {i})"))
        # 疑似空实现: def/class 后紧跟仅 pass 或 ... 或 docstring
        if re.search(r"(def|class)\s+\w+", line):
            nxt = lines[i] if i < len(lines) else ""
            nxt2 = lines[i + 1] if i + 1 < len(lines) else ""
            body = nxt.strip()
            stub = False
            if body in ("pass", "..."):
                stub = True
            elif body.startswith('"""') or body.startswith("'''") or body.startswith('"') or body.startswith("'"):
                # docstring 后还空? 仅当紧接着是 pass/... 才算空实现
                if nxt2.strip() in ("pass", "..."):
                    stub = True
            if stub:
                issues.append(("中", f"疑似空实现 (line {i}: {s[:40]})"))
        # print 调试残留 (低, 仅 py)
        if in_py and re.search(r"\bprint\s*\(", line) and not re.search(r"#\s*noqa", line):
            issues.append(("低", f"含 print 调试输出 (line {i})——确认是否需移除"))
        # 超长行 (低)
        if len(line) > 120:
            issues.append(("低", f"行超 120 字符 (line {i}, {len(line)} 字符)"))
    return issues


def _static_review(text, filename=""):
    """零依赖静态评审, 返回 dict: {verdict, score, issues, summary}。"""
    ok, syn_err = _check_syntax(text, filename)
    issues = []
    if not ok and syn_err:
        issues.append(("高", syn_err))
    issues.extend(_scan_rules(text, filename))

    score = 100
    for sev, _ in issues:
        if sev == "高":
            score -= _W_HIGH
        elif sev == "中":
            score -= _W_MED
        else:
            score -= _W_LOW
    score = max(0, min(100, score))

    has_high = any(sev == "高" for sev, _ in issues)
    verdict = "revise" if (has_high or score < 75) else "approve"
    summary = "静态评审完成: 通过语法检查" if ok else "静态评审发现语法错误"
    if issues:
        summary += f", 命中 {len(issues)} 项规则 (高 {sum(1 for s,_ in issues if s=='高')} / 中 {sum(1 for s,_ in issues if s=='中')} / 低 {sum(1 for s,_ in issues if s=='低')})"
    else:
        summary += ", 无规则告警"
    return {"verdict": verdict, "score": score, "issues": issues, "summary": summary}


def _parse_critic(text):
    """从 critic 子代理文本解析 VERDICT/SCORE, 解析失败返回 None。"""
    if not text:
        return None
    m_v = re.search(r"VERDICT\s*:\s*(approve|revise)", text, re.I)
    if not m_v:
        return None
    verdict = m_v.group(1).lower()
    score = None
    m_s = re.search(r"SCORE\s*:\s*(\d{1,3})", text, re.I)
    if m_s:
        score = max(0, min(100, int(m_s.group(1))))
    return {"verdict": verdict, "score": score}


def _run_critic(target_text, ctx, focus):
    """起一个只读评审子代理做语义层增强, 返回 critic 文本或 None。"""
    clients = (ctx or {}).get("clients") or {}
    if not clients:
        return None
    try:
        from . import agent_tools
        registry = (ctx or {}).get("registry")
        cfg = (ctx or {}).get("cfg")
        if registry is None or cfg is None:
            return None
        focus_hint = f"重点关注: {focus}" if focus else "关注正确性/健壮性/可维护性"
        prompt = (
            "你是一名资深代码评审专家。请对以下代码做只读评审(不要修改任何文件)。\n"
            f"{focus_hint}\n"
            "在回复末尾用固定格式给出结论:\n"
            "VERDICT: approve 或 revise\n"
            "SCORE: 0-100 (代码质量分)\n"
            "ISSUES: 逐条列出问题(带严重度 高/中/低)\n"
            "SUGGESTIONS: 逐条列出改进建议\n"
            "\n===== 待评审代码 =====\n" + target_text[:6000]
        )
        res = agent_tools._run_one_subagent(prompt, None, registry, cfg, clients)
        return res if isinstance(res, str) else None
    except Exception:
        return None


def _tool_review_code(args, ctx):
    """代码评审自评估工具。

    参数:
      target: 文件路径 (存在则读取) 或 代码片段/diff 文本
      focus?: 评审焦点 (如 "安全性" / "性能") 传给 critic
      critic?: bool (默认 True) 是否叠加 LLM 评审子代理; 无 LLM/解析失败自动回退静态
    返回: 结构化评审文本 ([code-review] 块, 含 VERDICT/SCORE/ISSUES/SUGGESTIONS/SUMMARY)
    """
    target = (args or {}).get("target") or ""
    if not target:
        return "[code-review] 错误: 缺少 target (文件路径或代码片段)"
    focus = (args or {}).get("focus")
    use_critic = bool((args or {}).get("critic", True))

    # 解析 target: 路径存在则读, 否则当文本
    filename = ""
    text = target
    try:
        if os.path.isfile(target):
            filename = os.path.basename(target)
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
    except Exception:
        pass

    static = _static_review(text, filename)

    critic_text = None
    if use_critic:
        critic_text = _run_critic(text, ctx, focus)
    parsed = _parse_critic(critic_text) if critic_text else None

    # 采纳策略: critic 解析成功且给出分数 -> 以 critic 为准; 否则静态
    if parsed and parsed.get("score") is not None:
        verdict = parsed["verdict"]
        score = parsed["score"]
        source = "LLM 评审 + 静态"
    else:
        verdict = static["verdict"]
        score = static["score"]
        source = "静态评审" + ("" if not critic_text else " (critic 未给出结构化结论, 已回退)")

    # 汇总 ISSUES/SUGGESTIONS
    issue_lines = [f"- [{sev}] {desc}" for sev, desc in static["issues"]]
    if parsed:
        # critic 的结构化 ISSUES 逐条提取为独立 [sev] 项, 避免被「ISSUES (critic):」前缀吞掉首条,
        # 让前端/后端可视化能正确按 高/中/低 着色 (否则行首非 `- [sev]` 的正则匹配会丢首条)。
        m_iss = re.search(r"ISSUES\s*:(.*?)(?=\n[A-Z]+:|\Z)", critic_text or "", re.S | re.I)
        if m_iss:
            for line in m_iss.group(1).splitlines():
                mm = re.match(r"\s*-\s*\[([高中低])\]\s*(.*)", line)
                if mm:
                    issue_lines.append(f"- [{mm.group(1)}] (critic) {mm.group(2).strip()}")
        else:
            # critic 无结构化 ISSUES, 仅附原文摘要, 不污染 ISSUES 段
            issue_lines.append(f"- (critic) {critic_text.strip()[:200]}")

    summary = static["summary"]
    if critic_text and parsed is None:
        summary = (critic_text or static["summary"])[:400]

    out = [
        "[code-review]",
        f"VERDICT: {verdict}",
        f"SCORE: {score}",
        "ISSUES:",
    ]
    out.extend(issue_lines if issue_lines else ["- (无)"])
    out.append("SUMMARY: " + summary)
    out.append(f"(评审来源: {source})")
    return "\n".join(out)
