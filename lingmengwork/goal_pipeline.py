"""全链路目标驱动流水线 (Goal Pipeline) —— 终极蓝图 Phase 7.

把已建成的三大引擎串成「理解 -> 拆解 -> 编排 -> 执行 -> 自检 -> 交付」一站式闭环,
对齐《终极蓝图》的终极目标态: 用户输入一个模糊目标, 平台自动完成
「理解 -> 拆解 -> 编排 -> 执行 -> 自检 -> 交付」, 并在编程/音频/图片/视频四域间自由调度。

- 拆解: decompose_engine.decompose (LLM 驱动 + 规则兜底)
- 编排: 按域分组 + 拓扑并行层 (execution_order / layer)
- 执行: 对每个步骤按其 domain 调用 creation_domains.dispatch 产出可执行创作蓝图
- 自检: LLM Critic 评审完整性 + 规则兜底 (缺失/过短检测)
- 交付: 汇总 Markdown 交付稿 + 可直接落计划书的 plan_payload

所有 LLM 调用经 llm_call(prompt, system=None)->str|None 注入, 无 key 时全程规则兜底仍可用。
"""

import json
import re
import time

from . import decompose_engine as de
from . import creation_domains as cd

# 域元信息兜底(含 any)
_DOM_META = {
    "code": {"name": "编程", "emoji": "\U0001F7E3", "theme": "#8b5cf6", "adapter": "native"},
    "audio": {"name": "音频", "emoji": "\U0001F7E2", "theme": "#10b981", "adapter": "mcp"},
    "image": {"name": "图片", "emoji": "\U0001F338", "theme": "#f472b6", "adapter": "mcp"},
    "video": {"name": "视频", "emoji": "\U0001F535", "theme": "#6366f1", "adapter": "mcp"},
    "any": {"name": "通用", "emoji": "\U0001F3F7", "theme": "#22d3ee", "adapter": "native"},
}

_STAGE_NAMES = ["理解", "拆解", "编排", "执行", "自检", "交付"]


def _dom_meta(domain):
    return _DOM_META.get(domain) or _DOM_META["any"]


def _parse_json(raw):
    """容忍代码块包裹的 JSON 解析。"""
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
        s = s.strip("`")
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _parallel_groups(steps):
    """按 layer 分组, 生成可并行执行批次。"""
    groups = {}
    for s in steps:
        layer = s.get("layer", 0)
        groups.setdefault(layer, []).append(s["id"])
    return [groups[k] for k in sorted(groups)]


def _self_check(goal, steps, executions, llm_call=None):
    """LLM Critic 评审流水线的完整性与缺口, 失败/无 key 回退规则版。"""
    gaps, notes, score = [], [], 100
    if not steps:
        gaps.append("未能拆解出任何执行步骤")
        score -= 60
    for ex in executions:
        plan = ex.get("plan", "")
        if not plan or len(plan) < 40:
            gaps.append("步骤「%s」执行方案缺失或过短" % ex.get("title", ""))
            score -= 15
    if llm_call:
        try:
            sys = ("你是严格的质量评审 Critic。检查以下「目标驱动流水线」的完整性, "
                   "只输出一个 JSON: {\"ok\": true/false, \"score\": 0-100, "
                   "\"gaps\": [字符串列表, 缺失/薄弱项], \"notes\": [字符串列表, 改进建议]}。不要解释。")
            summary = "目标: %s\n步骤数: %d\n各域执行方案摘要:\n" % (goal, len(steps))
            for ex in executions:
                summary += "- [%s/%s] %s: %s\n" % (ex["domain"], ex["domain_name"],
                                                    ex["title"], (ex.get("plan", "") or "")[:220])
            raw = llm_call(summary, system=sys)
            p = _parse_json(raw)
            if isinstance(p, dict):
                score = int(p.get("score", score))
                gaps = p.get("gaps") or gaps
                notes = p.get("notes") or notes
                return {"ok": bool(p.get("ok", score >= 70)), "score": max(0, min(100, score)),
                        "gaps": gaps, "notes": notes, "mode": "llm"}
        except Exception:
            pass
    return {"ok": score >= 70, "score": max(0, min(100, score)),
            "gaps": gaps, "notes": notes or ["规则自检: 各步骤执行方案均已生成"], "mode": "rule"}


def _assemble_delivery(goal, steps, executions, selfcheck):
    """汇总交付稿 (Markdown)。"""
    lines = ["# 交付稿 · %s" % goal, "",
             "> 目标驱动全链路流水线自动产出 · 自检评分 %d/100" % selfcheck.get("score", 0), "",
             "## 一、目标理解", "", goal, "",
             "## 二、任务拆解（%d 步）" % len(steps), ""]
    for s in steps:
        meta = _dom_meta(s.get("domain", "any"))
        dep = (" ← 依赖 " + ",".join(s["depends"])) if s.get("depends") else ""
        lines.append("- **%s** 〔%s %s〕%s" % (s["title"], meta["emoji"], meta["name"], dep))
    lines += ["", "## 三、域编排", ""]
    cov = [s.get("domain", "any") for s in steps if s.get("domain", "any") != "any"]
    cov = list(dict.fromkeys(cov))
    lines.append("覆盖域: " + ("、".join(_dom_meta(c)["name"] for c in cov) if cov else "通用/编程"))
    lines += ["", "## 四、各步骤执行蓝图", ""]
    for ex in executions:
        lines.append("### %s 〔%s %s〕" % (ex["title"], ex["emoji"], ex["domain_name"]))
        lines.append(ex.get("plan", "") or "（无）")
        lines.append("")
    lines += ["## 五、自检结论", ""]
    sc = selfcheck
    lines.append("- 评分: 完整度 **%d/100** · 模式 %s" % (sc.get("score", 0), sc.get("mode", "?")))
    if sc.get("gaps"):
        lines.append("- 待补强: " + "；".join(sc["gaps"]))
    if sc.get("notes"):
        lines.append("- 建议: " + "；".join(sc["notes"]))
    lines.append("")
    lines.append("> 本稿可由「存入计划」一键落为计划书 + 任务清单, 进入编排/自主执行。")
    return "\n".join(lines)


def run_pipeline(goal, context="", llm_call=None, max_dispatch=4, do_selfcheck=True):
    """执行全链路流水线, 返回结构化结果 dict。

    goal: 用户模糊目标
    context: 可选补充上下文
    llm_call: llm_call(prompt, system=None)->str|None
    max_dispatch: 最多为前 N 个步骤生成执行蓝图 (控制 LLM fan-out 时延)
    """
    started = time.time()
    ctx = (context or "").strip()

    # Stage 1 · 理解
    understand = {
        "goal": goal,
        "context": ctx,
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Stage 2 · 拆解
    steps = de.decompose(goal, ctx, llm_call=llm_call)
    order = de.execution_order(steps)
    exec_steps = steps[:max_dispatch] if max_dispatch else steps

    # Stage 3 · 编排 (域覆盖 + 并行层)
    coverage = []
    for s in steps:
        d = s.get("domain", "any")
        if d != "any" and d not in coverage:
            coverage.append(d)
    orchestrate = {
        "domain_coverage": coverage,
        "parallel_groups": _parallel_groups(steps),
        "execution_order": order,
    }

    # Stage 4 · 执行 (每步按其域产出可执行蓝图)
    executions = []
    for s in exec_steps:
        dom = s.get("domain") or "any"
        if dom == "any":
            dom = "code"
        meta = _dom_meta(dom)
        brief = "%s\n%s" % (s.get("title", ""), s.get("detail", ""))
        plan = ""
        try:
            res = cd.dispatch(dom, brief, ctx, llm_call=llm_call)
            plan = res.get("plan", "")
        except Exception as e:
            plan = "（执行方案生成失败: %s）" % e
        executions.append({
            "step_id": s["id"],
            "title": s.get("title", ""),
            "domain": dom,
            "domain_name": meta["name"],
            "emoji": meta["emoji"],
            "theme": meta["theme"],
            "adapter": meta["adapter"],
            "plan": plan,
        })

    # Stage 5 · 自检
    selfcheck = (_self_check(goal, steps, executions, llm_call=llm_call)
                 if do_selfcheck else {"ok": True, "score": 100, "gaps": [], "notes": ["自检已跳过"], "mode": "skip"})

    # Stage 6 · 交付
    delivery = _assemble_delivery(goal, steps, executions, selfcheck)
    plan_payload = de.to_plan_payload(goal, steps)

    return {
        "ok": True,
        "goal": goal,
        "stages": _STAGE_NAMES,
        "understand": understand,
        "decompose": {"steps": steps, "count": len(steps), "execution_order": order},
        "orchestrate": orchestrate,
        "execute": executions,
        "selfcheck": selfcheck,
        "delivery": delivery,
        "plan_payload": plan_payload,
        "elapsed_sec": round(time.time() - started, 1),
    }
