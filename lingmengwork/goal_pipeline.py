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
import os
import re
import time

from . import decompose_engine as de
from . import creation_domains as cd
from . import multimodal_adapters as ma
from . import memory_mgr as mm
from . import autonomous as au

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


def _recall_memory(goal, base_dir, k=6):
    """跨会话语义召回: 取与当前目标最相关的历史记忆片段 (Phase 9)。"""
    try:
        res = mm.retrieve(base_dir or os.getcwd(), goal, k=k)
        return res.get("results", []) or []
    except Exception:
        return []


def _format_memory_context(recalled):
    """把召回记忆拼成紧凑上下文串, 注入拆解/派发提示, 使计划带历史偏好与决策一致性。"""
    if not recalled:
        return ""
    out = ["【历史记忆 / 用户偏好（仅供参考：与当前目标冲突时以当前目标为准）】"]
    for r in recalled:
        snip = (r.get("snippet") or "").strip().replace("\n", " ")
        if snip:
            out.append("- %s" % snip[:240])
    return "\n".join(out)


# ---- Phase 11: 流水线 × 自主深度融合 ----
# 让「执行」阶段对编程域真正驱动自主回路: 规划->观察->Critic->反思,
# 抽取生成代码落盘, 并以编译校验作为评审门, 形成「蓝图->真实交付」闭环。
_EXT_MAP = {
    "python": "py", "py": "py", "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts",
    "html": "html", "css": "css", "json": "json", "bash": "sh", "sh": "sh", "shell": "sh",
    "sql": "sql", "yaml": "yaml", "yml": "yaml", "xml": "xml", "c": "c", "cpp": "cpp",
    "c++": "cpp", "java": "java", "go": "go", "rust": "rs", "ruby": "rb", "r": "r",
}


def _extract_code_blocks(text):
    """从 Markdown 文本抽取所有围栏代码块, 返回 [(lang, code), ...]。"""
    if not text:
        return []
    out = []
    for lang, code in re.findall(r"```([A-Za-z0-9_+#.-]*)\n(.*?)```", text, re.DOTALL):
        code = code.strip()
        if code:
            out.append((lang.strip().lower(), code))
    return out


def _slugify(s, maxlen=40):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", (s or "").strip())[:maxlen]
    return s.strip("_") or "task"


def _autonomous_execute(goal, ctx, llm_call, max_iter, out_root):
    """对编程域跑自主回路, 抽取代码落盘并以编译校验作为评审门。

    返回 {reached, iterations, conclusion, files:[name...], review:{...}}。
    无 LLM(llm_call=None)或没抽到代码时 files 为空, 由调用方回退到 dispatch 蓝图。
    """
    res = au.run(goal, llm_call, context=ctx, max_iter=max_iter)
    iterations = res.get("iterations", []) or []
    corpus = []
    for it in iterations:
        corpus.append(it.get("plan", "") or "")
        corpus.append(it.get("observation", "") or "")
        corpus.append(it.get("reflection", "") or "")
    corpus.append(res.get("conclusion", "") or "")
    blocks = []
    for c in corpus:
        blocks.extend(_extract_code_blocks(c))

    files, review = [], {"checked": 0, "passed": 0, "failed": 0, "details": []}
    if blocks and llm_call:
        slug = _slugify(goal)
        out_dir = os.path.join(out_root, "code", slug)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            out_dir = None
        for i, (lang, code) in enumerate(blocks, 1):
            if not out_dir:
                break
            ext = _EXT_MAP.get(lang, "txt")
            fname = "%02d.%s" % (i, ext)
            fpath = os.path.join(out_dir, fname)
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(code + "\n")
                files.append(fname)
                if ext == "py":
                    review["checked"] += 1
                    try:
                        import py_compile
                        py_compile.compile(fpath, doraise=True)
                        review["passed"] += 1
                        review["details"].append({"file": fname, "ok": True})
                    except Exception as e:
                        review["failed"] += 1
                        review["details"].append({"file": fname, "ok": False, "error": str(e)[:200]})
            except Exception:
                pass
        if files:
            review["dir"] = "/outputs/code/" + slug
    return {
        "reached": res.get("reached", False),
        "iterations": len(iterations),
        "conclusion": res.get("conclusion", ""),
        "files": files,
        "review": review,
    }


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
        au = ex.get("autonomous")
        if au and au.get("files"):
            rv = au["review"]
            lines.append("")
            lines.append("- 🤖 **自主执行**: %d 轮 · 达成=%s · 生成文件 `%s` · 编译校验 %d/%d 通过"
                         % (au["iterations"], "是" if au["reached"] else "否",
                            "、".join(au["files"]), rv["passed"], rv["checked"]))
            if rv.get("dir"):
                lines.append("- 代码目录: `%s`" % rv["dir"])
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


def run_pipeline(goal, context="", llm_call=None, max_dispatch=4, do_selfcheck=True,
                 do_render=True, memory_dir=None, do_learn=True, do_autonomous=True,
                 max_autonomous_iter=4):
    """执行全链路流水线, 返回结构化结果 dict。

    goal: 用户模糊目标
    context: 可选补充上下文
    llm_call: llm_call(prompt, system=None)->str|None
    max_dispatch: 最多为前 N 个步骤生成执行蓝图 (控制 LLM fan-out 时延)
    do_selfcheck: 是否执行 LLM Critic 自检
    do_render: 是否对 image/audio/video 域调用多模态适配层真实产出媒体文件
    memory_dir: 语义记忆根目录 (默认 cwd 下 .lmw_memory); 跨会话偏好喂入与写回
    do_learn: 流水线结束后是否把本次目标/域偏好写回记忆 (越用越聪明)
    do_autonomous: 是否对编程域步骤驱动自主回路 (Phase 11: 真实代码生成+编译评审门)
    max_autonomous_iter: 单个编程域步骤的自主最大自驱轮次
    """
    started = time.time()
    ctx = (context or "").strip()

    # Stage 1 · 理解 (含跨会话记忆召回 · Phase 9)
    recalled = _recall_memory(goal, memory_dir, k=6)
    mem_ctx = _format_memory_context(recalled)
    understand = {
        "goal": goal,
        "context": ctx,
        "memory_recalled": len(recalled),
        "memory": [{"source": r.get("source"), "snippet": r.get("snippet"), "score": r.get("score")}
                   for r in recalled],
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 富上下文: 用户补充 + 召回记忆一起注入下游 (拆解/派发/多模态)
    ctx_rich = ctx
    if mem_ctx:
        ctx_rich = (ctx + "\n\n" + mem_ctx).strip()

    # Stage 2 · 拆解 (记忆经 system_extra 注入, 仅 LLM 路径可见, 不影响规则兜底扫描)
    steps = de.decompose(goal, ctx, llm_call=llm_call, system_extra=mem_ctx or None)
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

    # Stage 4 · 执行 (每步按其域产出可执行蓝图; 编程域驱动自主回路真实生成代码; 创作域再真实产出媒体文件)
    executions = []
    for s in exec_steps:
        dom = s.get("domain") or "any"
        if dom == "any":
            dom = "code"
        meta = _dom_meta(dom)
        brief = "%s\n%s" % (s.get("title", ""), s.get("detail", ""))
        plan = ""
        autonomous = None
        # Phase 11 · 编程域驱动自主回路 (真实代码生成 + 编译评审门); 无产出则回退 dispatch 蓝图
        if dom == "code" and do_autonomous:
            try:
                autonomous = _autonomous_execute(brief, ctx_rich, llm_call, max_autonomous_iter, os.getcwd())
            except Exception:
                autonomous = None
        if autonomous and autonomous.get("files"):
            plan = autonomous.get("conclusion") or "（自主模式已生成 %d 个代码文件，详见交付稿与 outputs/code/）" % len(autonomous["files"])
        else:
            try:
                res = cd.dispatch(dom, brief, ctx_rich, llm_call=llm_call)
                plan = res.get("plan", "")
            except Exception as e:
                plan = "（执行方案生成失败: %s）" % e
        # Phase 8 · 真实多模态适配层: 创作域自动落真实媒体文件
        artifact = None
        if do_render and dom in ("image", "audio", "video"):
            try:
                artifact = ma.render(dom, brief, plan, ctx_rich, llm_call=llm_call)
                if artifact and artifact.get("file"):
                    artifact["url"] = "/outputs/" + os.path.basename(artifact["file"])
            except Exception:
                artifact = None
        executions.append({
            "step_id": s["id"],
            "title": s.get("title", ""),
            "domain": dom,
            "domain_name": meta["name"],
            "emoji": meta["emoji"],
            "theme": meta["theme"],
            "adapter": meta["adapter"],
            "plan": plan,
            "autonomous": autonomous,
            "artifact": artifact,
        })

    # Stage 5 · 自检
    selfcheck = (_self_check(goal, steps, executions, llm_call=llm_call)
                 if do_selfcheck else {"ok": True, "score": 100, "gaps": [], "notes": ["自检已跳过"], "mode": "skip"})

    # Stage 6 · 交付 (+ Phase 9 跨会话学习: 把本次目标/域偏好写回记忆, 越用越聪明)
    delivery = _assemble_delivery(goal, steps, executions, selfcheck)
    plan_payload = de.to_plan_payload(goal, steps)
    learned = None
    if do_learn:
        try:
            cov_names = "、".join(_dom_meta(c)["name"] for c in coverage) or "通用/编程"
            learn_text = ("目标: %s\n覆盖域: %s\n步骤数: %d\n前序关键步骤: %s"
                          % (goal, cov_names, len(steps), "；".join(s["title"] for s in steps[:3])))
            learned = mm.capture(memory_dir or os.getcwd(), learn_text, llm_call=llm_call)
        except Exception:
            learned = None

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
        "learned": learned,
        "elapsed_sec": round(time.time() - started, 1),
    }
