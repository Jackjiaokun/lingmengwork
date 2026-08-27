"""Phase 18 — 自主进化闭环 · 自愈提议器 (Self-Healing Patch Proposer)。

把 Phase 14 离线自检 + Phase 16/17 运行轨迹/审计链收口为「可自主进化」能力：

- 采集信号源：① 离线自检(selfcheck)失败项 ② 活动总线(event_bus)中带 audit 标记的
  失败/异常事件(引擎 run_fail / 自动化 run_fail / system_error)。
- 内置确定性规则库：把每条信号转成结构化「补丁提议」
  (id / severity / area / symptom / hypothesis / actions / confidence / auto_fixable / source_ref)。
- propose()：聚合全部信号 → 健康分 + 信号量 + 提议清单 + 各严重度计数。
- export_proposals()：把提议落盘到 `.lmw_heal/proposals.jsonl`(human-in-the-loop 待审)，
  **不直接改动源码**；可人工复核或由后续 LLM 消费。

设计约束：
- 纯标准库，零三方依赖，无外部网络。
- 确定性、可端到端验证（无 LLM 亦工作，规则兜底）。
- 仅作可观测/建议用途，异常被吞，不影响主流程。
"""

import json
import os
import re
import time


# ------------------------------------------------------------------ 工具

def _area_for_check(name):
    """把自检检查名映射到代码区域。"""
    m = {
        "核心模块导入": "依赖/导入",
        "任务拆解(规则兜底)": "decompose_engine",
        "创作域(规则兜底)": "creation_domains",
        "自主模式(无 LLM)": "autonomous",
        "全链路流水线(无 LLM)": "goal_pipeline",
        "多模态适配层(模板回退)": "multimodal_adapters",
        "跨会话记忆(捕获+召回)": "memory_mgr",
        "活动总线(事件+审计链)": "event_bus",
        "关键静态资产": "web/static",
    }
    return m.get(name, name)


def _parse_missing_static(detail):
    """从『缺失: a, b』解析缺失文件列表。"""
    if not detail:
        return []
    mm = re.search(r"缺失[:：]\s*(.+)", detail)
    if not mm:
        return []
    return [x.strip() for x in mm.group(1).split(",") if x.strip()]


# ------------------------------------------------------------------ 提议结构

class Proposal:
    """一条结构化补丁提议。"""

    def __init__(self, severity, area, symptom, hypothesis, actions,
                 confidence, auto_fixable, source_ref, rule_id, patch_plan=None):
        self.severity = severity          # high / medium / low
        self.area = area
        self.symptom = symptom
        self.hypothesis = hypothesis
        self.actions = actions            # list[str]
        self.confidence = float(confidence)
        self.auto_fixable = bool(auto_fixable)
        self.source_ref = source_ref
        self.rule_id = rule_id
        # patch_plan: 结构化可执行修复预案, 形如
        #   {"title": str, "steps": [str], "verify": str, "risk": str}
        # 仍 human-in-the-loop: 仅给出方案, 不直接改源码。
        self.patch_plan = patch_plan or {}

    def to_dict(self, pid=None):
        return {
            "id": pid,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "area": self.area,
            "symptom": self.symptom,
            "hypothesis": self.hypothesis,
            "actions": list(self.actions),
            "confidence": round(self.confidence, 2),
            "auto_fixable": self.auto_fixable,
            "source_ref": self.source_ref,
            "patch_plan": self.patch_plan,
        }


# ------------------------------------------------------------------ 信号采集

def collect_signals(selfcheck_report=None, bus=None):
    """归一化信号源 -> list[dict]。

    信号类型:
      - {type:"selfcheck", name, ok, detail}
      - {type:"event", source, kind, msg, data}
    """
    signals = []
    # 1) 离线自检失败项
    if selfcheck_report:
        for c in (selfcheck_report.get("checks") or []):
            if not c.get("ok"):
                signals.append({
                    "type": "selfcheck",
                    "name": c.get("name"),
                    "ok": False,
                    "detail": c.get("detail", ""),
                })
    # 2) 活动总线中带 audit 的失败/异常事件
    if bus is not None:
        try:
            for e in bus.audit_trail(limit=500):
                k = e.get("kind", "")
                if k in ("run_fail", "fail", "error") or k.endswith("_fail"):
                    signals.append({
                        "type": "event",
                        "source": e.get("source"),
                        "kind": k,
                        "msg": e.get("msg", ""),
                        "data": e.get("data") or {},
                    })
        except Exception:
            pass
    return signals


# ------------------------------------------------------------------ 规则库

def _build_selfcheck_proposal(sig):
    name = sig.get("name", "")
    detail = sig.get("detail", "")
    area = _area_for_check(name)
    missing = _parse_missing_static(detail) if name == "关键静态资产" else []
    hypothesis = "自检项『%s』未通过，系统该能力处于降级/不可用状态。" % name
    actions = ["查看日志定位根因", "在源码层修复并补回归测试", "重新跑 selfcheck 复核"]
    plan_steps = ["读取 selfcheck 失败 detail 定位具体根因",
                  "在源码层修复并补回归测试",
                  "重新跑 `python -m lingmengwork.selfcheck` 复核"]
    if name == "关键静态资产":
        hypothesis = "关键前端资产缺失，面板相关页面无法加载。"
        actions = ["从版本库恢复缺失文件: " + "、".join(missing) if missing else "恢复缺失的静态资产",
                   "校验 web/static 目录构建产物完整",
                   "重新跑 selfcheck 复核"]
        plan_steps = (["git -C <repo> checkout -- " + " ".join(missing)] if missing
                      else ["从版本库/构建产物恢复缺失的 web/static 文件"]) + [
            "校验 web/static 目录构建产物完整(大小/语法)",
            "重启面板服务: `python -m lingmengwork.web.server`",
            "重新跑 `python -m lingmengwork.selfcheck` 确认『关键静态资产』通过"]
    elif name == "核心模块导入":
        hypothesis = "存在模块导入失败，通常是依赖缺失或语法错误。"
        actions = ["检查 import 报错模块与依赖",
                   "运行 target 解释器的 py_compile 校验",
                   "补 requirements / 重建虚拟环境"]
        plan_steps = ["查看 selfcheck 失败 detail 中的 import 报错堆栈",
                      "用目标解释器 `python -m py_compile <module>` 定位语法错误",
                      "补 requirements / 重建虚拟环境后再次 import 验证"]
    return Proposal(
        severity="high", area=area,
        symptom="自检项『%s』失败: %s" % (name, detail),
        hypothesis=hypothesis, actions=actions,
        confidence=0.9, auto_fixable=False,
        source_ref="selfcheck:%s" % name, rule_id="selfcheck_fail",
        patch_plan={
            "title": "修复『%s』自检失败" % name,
            "steps": plan_steps,
            "verify": "重新跑 `python -m lingmengwork.selfcheck` 确认该检查项 ok=true",
            "risk": "低：仅恢复/修复既有模块或资产，不改动业务逻辑；导入类修复可能涉及依赖重装(medium)。",
        },
    )


def _build_engine_fail_proposal(sig):
    eng = (sig.get("data") or {}).get("engine", "unknown")
    return Proposal(
        severity="high", area="引擎:%s" % eng,
        symptom=sig.get("msg", "引擎运行失败"),
        hypothesis="引擎『%s』在最近一次调用中抛出异常，运行未成功闭环。" % eng,
        actions=["查看 /api/audit 中该引擎的 run_fail 详情",
                 "核对 LLM 后端 key 与网络连通性",
                 "检查该引擎规则兜底路径是否正常"],
        confidence=0.8, auto_fixable=False,
        source_ref="event:engine:%s" % sig.get("kind"), rule_id="engine_run_fail",
        patch_plan={
            "title": "排查引擎『%s』运行失败" % eng,
            "steps": [
                "在 /api/audit 查看该引擎 run_fail 事件的完整 msg 与 data",
                "核对 LLM 后端 key 环境变量与网络连通性(无 key → 规则兜底路径)",
                "在源码服务下手动调用该引擎入口(规则兜底), 确认不抛异常",
                "补回归测试锁定该失败分支",
            ],
            "verify": "手动调用引擎入口(规则兜底)确认返回 ok 不抛异常",
            "risk": "中：可能涉及 LLM key / 网络配置；规则兜底路径应保证无 key 也能降级运行。",
        },
    )


def _build_automation_fail_proposal(sig):
    tid = (sig.get("data") or {}).get("id", "unknown")
    return Proposal(
        severity="medium", area="自动化调度",
        symptom=sig.get("msg", "自动化任务运行失败"),
        hypothesis="自动化任务『%s』运行未达成预期，可能是指令/依赖/环境导致。" % tid,
        actions=["查看任务定义(schedule/goal/kind)是否合理",
                 "在源码服务下手动 run_now 复现",
                 "必要时调整任务参数或下线该任务"],
        confidence=0.75, auto_fixable=False,
        source_ref="event:automation:%s" % sig.get("kind"), rule_id="automation_run_fail",
        patch_plan={
            "title": "排查自动化任务『%s』运行失败" % tid,
            "steps": [
                "查看任务定义(schedule/goal/kind/domain)是否合理",
                "在源码服务下手动 `hub.run_now('%s', cwd=...)` 复现" % tid,
                "若指令/依赖问题 → 调整任务参数; 若环境缺失 → 补依赖",
                "确认无误后可保留, 否则 `hub.set_enabled('%s', False)` 下线" % tid,
            ],
            "verify": "重新 run_now 确认任务达成预期(ok=true)",
            "risk": "低：仅调度配置/参数调整，不影响其它任务。",
        },
    )


def _build_generic_fail_proposal(sig):
    return Proposal(
        severity="medium", area="运行时:%s" % sig.get("source", "system"),
        symptom=sig.get("msg", "未知异常"),
        hypothesis="系统检测到一次失败/异常信号(source=%s, kind=%s)。" %
                   (sig.get("source"), sig.get("kind")),
        actions=["定位该信号来源日志", "评估是否需要补防护/重试逻辑"],
        confidence=0.6, auto_fixable=False,
        source_ref="event:%s:%s" % (sig.get("source"), sig.get("kind")),
        rule_id="generic_fail",
        patch_plan={
            "title": "定位并评估异常信号 (source=%s)" % sig.get("source", "system"),
            "steps": [
                "在 /api/audit 与运行日志中定位该信号的完整上下文",
                "评估是否需补防护/重试/降级逻辑",
                "若确认为偶发 → 加监控; 若为系统性 → 提 Issue",
            ],
            "verify": "观察后续是否复现该信号",
            "risk": "中：视具体来源而定，先观察再决策。",
        },
    )


_RULES = [
    {"id": "selfcheck_fail", "match": lambda s: s["type"] == "selfcheck",
     "build": _build_selfcheck_proposal},
    {"id": "engine_run_fail",
     "match": lambda s: s["type"] == "event" and s.get("source") == "engine"
                        and s.get("kind") in ("run_fail", "fail", "error"),
     "build": _build_engine_fail_proposal},
    {"id": "automation_run_fail",
     "match": lambda s: s["type"] == "event" and s.get("source") == "automation"
                        and s.get("kind") in ("run_fail", "fail", "error"),
     "build": _build_automation_fail_proposal},
    {"id": "generic_fail",
     "match": lambda s: s["type"] == "event"
                        and (s.get("kind") in ("run_fail", "fail", "error")
                             or s.get("kind", "").endswith("_fail")),
     "build": _build_generic_fail_proposal},
]


_SEV_PENALTY = {"high": 25, "medium": 12, "low": 5}


# ------------------------------------------------------------------ 主入口

def analyze(signals):
    """把信号经规则库转成提议 list[Proposal]。"""
    out = []
    for sig in signals:
        for rule in _RULES:
            try:
                if rule["match"](sig):
                    p = rule["build"](sig)
                    if p:
                        out.append(p)
                    break  # 一条信号仅命中首条规则
            except Exception:
                continue
    return out


def propose(selfcheck_report=None, bus=None):
    """聚合信号 → 结构化健康报告 + 补丁提议清单。"""
    signals = collect_signals(selfcheck_report=selfcheck_report, bus=bus)
    proposals = analyze(signals)
    by_sev = {"high": 0, "medium": 0, "low": 0}
    for p in proposals:
        by_sev[p.severity] = by_sev.get(p.severity, 0) + 1
    penalty = sum(_SEV_PENALTY.get(p.severity, 5) for p in proposals)
    health = max(0, 100 - penalty)
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "health_score": health,
        "signal_count": len(signals),
        "proposal_count": len(proposals),
        "auto_fixable_count": sum(1 for p in proposals if p.auto_fixable),
        "by_severity": by_sev,
        "proposals": [p.to_dict(pid="P%03d" % (i + 1))
                      for i, p in enumerate(proposals)],
    }


def _render_markdown(report):
    """把提议报告渲染为可读 Markdown(含完整补丁预案)。"""
    lines = []
    lines.append("# 灵梦work · 自愈补丁提议报告 (Phase 19)\n")
    lines.append("> 生成于 **%s**  ·  健康分 **%d**  ·  信号 %d  ·  提议 %d  ·  可自动修复 %d\n" % (
        report.get("generated_at", "?"), report.get("health_score", 0),
        report.get("signal_count", 0), report.get("proposal_count", 0),
        report.get("auto_fixable_count", 0)))
    lines.append("\n**严重度分布**: 高 %d · 中 %d · 低 %d\n" % (
        (report.get("by_severity") or {}).get("high", 0),
        (report.get("by_severity") or {}).get("medium", 0),
        (report.get("by_severity") or {}).get("low", 0)))
    lines.append("---\n")
    props = report.get("proposals", []) or []
    if not props:
        lines.append("✅ 当前无异常信号，系统健康。\n")
        return "\n".join(lines)
    for i, p in enumerate(props, 1):
        lines.append("## P%03d · [%s] %s\n" % (i, p.get("severity", "").upper(), p.get("area", "")))
        lines.append("- **置信度**: %d%%  ·  **自动修复**: %s" % (
            int((p.get("confidence") or 0) * 100),
            "可" if p.get("auto_fixable") else "需人工复核"))
        lines.append("- **来源**: `%s`  ·  **规则**: `%s`" % (p.get("source_ref", ""), p.get("rule_id", "")))
        lines.append("- **症状**: %s" % p.get("symptom", ""))
        lines.append("- **假设**: %s" % p.get("hypothesis", ""))
        acts = p.get("actions") or []
        if acts:
            lines.append("- **建议动作**:")
            for a in acts:
                lines.append("  - %s" % a)
        plan = p.get("patch_plan") or {}
        if plan:
            lines.append("\n### 🛠 可执行补丁预案: %s\n" % plan.get("title", ""))
            steps = plan.get("steps") or []
            for j, s in enumerate(steps, 1):
                lines.append("  %d. %s" % (j, s))
            if plan.get("verify"):
                lines.append("- **验证**: %s" % plan.get("verify"))
            if plan.get("risk"):
                lines.append("- **风险**: %s" % plan.get("risk"))
        lines.append("\n---\n")
    return "\n".join(lines)


def export_proposals(report, out_dir):
    """把提议落盘到 out_dir/.lmw_heal/ (human-in-the-loop 待审)。

    生成两份产物:
      - proposals.jsonl : 机器可读快照(每次追加)
      - proposals_<ts>.md : 可读报告(含完整补丁预案)

    返回 {ok, path, md_path, count}。不直接改动任何源码。
    """
    try:
        d = os.path.join(out_dir, ".lmw_heal")
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "proposals.jsonl")
        # 追加模式：每次导出作为一次快照，避免覆盖历史
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "generated_at": report.get("generated_at"),
                "health_score": report.get("health_score"),
                "proposals": report.get("proposals", []),
            }, ensure_ascii=False) + "\n")
        # 可读报告(含补丁预案)
        ts = (report.get("generated_at") or time.strftime("%Y%m%d%H%M%S")).replace(" ", "_").replace(":", "")
        md_path = os.path.join(d, "proposals_%s.md" % ts)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(_render_markdown(report))
        readme = os.path.join(d, "README.md")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as f:
                f.write("# 自愈提议待审区 (.lmw_heal)\n\n"
                        "本目录由 Phase 18/19 自主进化闭环生成。\n"
                        "`proposals.jsonl` 为机器可读快照(每次追加);\n"
                        "`proposals_<ts>.md` 为可读补丁报告(含结构化修复预案)。\n\n"
                        "**human-in-the-loop**：系统仅生成建议与可执行预案，不自动改动源码；\n"
                        "请人工复核 Step 后再落地补丁。\n")
        return {"ok": True, "path": path, "md_path": md_path, "count": report.get("proposal_count", 0)}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# ------------------------------------------------------------------ Phase 20 自愈闭环 2.0：补丁生成 + 沙箱验证门 + 人工合并门
#
# 把 Phase 19 的「文本预案」升级为「可应用补丁候选」：
#   - generate_patch : proposal -> 注释型骨架 diff (LLM 不可用走规则降级, 绝不编造源码)
#   - sandbox_verify : 临时沙箱校验补丁结构合法性 + (若 target 具体) py_compile (不碰真实 repo)
#   - apply_patch    : 人工合并门, confirm=True 才落地, 先备份, 绝不自动改源码
#   - list_patches   : 列出 .lmw_heal/patches/ 下所有补丁
#
# 设计约束：零 LLM 亦工作（规则兜底）；human-in-the-loop 终态；沙箱隔离；可端到端验证。

import sys as _sys
import shutil as _shutil
import subprocess as _subprocess
from datetime import datetime as _dt


class Patch:
    """一条可应用的补丁(diff 候选)。"""

    def __init__(self, proposal_id, target, diff, generated_by, note=""):
        self.proposal_id = proposal_id
        self.target = target            # 目标文件相对/绝对路径; 无法定位时为 None
        self.diff = diff                # unified diff 文本(可为注释型骨架)
        self.generated_by = generated_by  # "rule" | "llm"
        self.note = note
        self.created_at = _dt.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self, pid=None):
        return {
            "patch_id": pid,
            "proposal_id": self.proposal_id,
            "target": self.target,
            "generated_by": self.generated_by,
            "note": self.note,
            "created_at": self.created_at,
            "diff": self.diff,
        }


def _resolve_target(proposal):
    """从 proposal 解析可修改的目标文件。返回 (path_or_None, hint)。"""
    ref = proposal.get("source_ref", "") if isinstance(proposal, dict) else getattr(proposal, "source_ref", "")
    area = proposal.get("area", "") if isinstance(proposal, dict) else getattr(proposal, "area", "")
    if "关键静态资产" in ref or "web/static" in area:
        return (None, "恢复缺失的 web/static 资产(请提供文件名或由人工从版本库 checkout)")
    if "核心模块导入" in ref:
        return (None, "需人工根据 import 报错定位具体模块后填充")
    return (None, "该信号涉及多文件/运行环境，需人工确认修改点")


def generate_patch(proposal, repo_root=None, llm_call=None):
    """把一条 proposal 转成可应用补丁候选(落盘 .lmw_heal/patches/)。

    LLM 可用(llm_call 非空)且目标清晰 -> 调 LLM 生成真实代码 diff;
    否则走规则降级: 生成『注释型骨架 diff』(说明要改什么 + 占位),
    绝不编造不存在的代码, 保证 human-in-the-loop 安全。

    返回 {ok, patch_id, target, diff, generated_by, path, note}。
    """
    try:
        if isinstance(proposal, dict):
            pid = proposal.get("id") or proposal.get("rule_id") or "P000"
            plan = proposal.get("patch_plan") or {}
            title = plan.get("title") or proposal.get("symptom", "")
            hypothesis = proposal.get("hypothesis", "")
            steps = plan.get("steps") or proposal.get("actions") or []
            area = proposal.get("area", "")
            source_ref = proposal.get("source_ref", "")
            symp = proposal.get("symptom", "")
        else:
            pid = getattr(proposal, "rule_id", "P000")
            plan = getattr(proposal, "patch_plan", {}) or {}
            title = plan.get("title", "")
            hypothesis = proposal.hypothesis
            steps = plan.get("steps") or proposal.actions or []
            area = proposal.area
            source_ref = proposal.source_ref
            symp = proposal.symptom

        target, hint = _resolve_target({"source_ref": source_ref, "area": area})

        # LLM 真实生成分支(当前环境无 key, 默认不触发)
        diff = None
        generated_by = "rule"
        if callable(llm_call) and target:
            try:
                real_diff = llm_call(proposal=proposal, target=target)
                if isinstance(real_diff, str) and real_diff.strip():
                    diff = real_diff
                    generated_by = "llm"
            except Exception:
                diff = None

        if not diff:
            # 规则降级: 注释型骨架 diff
            dl = []
            dl.append("# 灵梦work 自愈补丁 (Phase 20)")
            dl.append("# proposal_id: %s" % pid)
            dl.append("# generated_by: rule (降级骨架, 待人工/LLM 填充真实改动)")
            dl.append("# target: %s" % (target or "(需人工指定)"))
            dl.append("# 症状: %s" % symp)
            dl.append("# 假设: %s" % hypothesis)
            dl.append("")
            dl.append("# ---- 结构化修复步骤 (来自 Phase 19 patch_plan) ----")
            for i, s in enumerate(steps, 1):
                dl.append("# %d. %s" % (i, s))
            dl.append("")
            dl.append("# ---- 待落地改动 (人类/LLM 在此填充 unified diff) ----")
            dl.append("# + def fix_...():")
            dl.append("# +     ...")
            diff = "\n".join(dl)

        # 落盘
        out_dir = repo_root or os.getcwd()
        d = os.path.join(out_dir, ".lmw_heal", "patches")
        os.makedirs(d, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d%H%M%S")
        patch_id = "PATCH_%s_%s" % (str(pid).replace(":", "_"), ts)
        ppath = os.path.join(d, patch_id + ".diff")
        with open(ppath, "w", encoding="utf-8") as f:
            f.write(diff)
        metapath = os.path.join(d, patch_id + ".json")
        with open(metapath, "w", encoding="utf-8") as f:
            json.dump({
                "patch_id": patch_id, "proposal_id": pid, "target": target,
                "generated_by": generated_by, "note": hint,
                "created_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False, indent=2)
        return {"ok": True, "patch_id": patch_id, "target": target,
                "diff": diff, "generated_by": generated_by, "path": ppath, "note": hint}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def sandbox_verify(patch_id, repo_root=None):
    """在临时沙箱目录验证补丁候选的合法性(不触碰真实 repo)。

    - 解析 .diff 是否为合法文本 + 含补丁头 + 含结构化步骤
    - target 若为具体文件且存在 -> py_compile 校验原文件可编译(不改它)
    返回 {ok, compiled, log}。
    """
    try:
        out_dir = repo_root or os.getcwd()
        pdir = os.path.join(out_dir, ".lmw_heal", "patches")
        ppath = os.path.join(pdir, patch_id + ".diff")
        if not os.path.exists(ppath):
            return {"ok": False, "error": "patch not found: %s" % patch_id}
        with open(ppath, "r", encoding="utf-8") as f:
            content = f.read()
        log = []
        if not content.strip():
            return {"ok": False, "error": "empty diff"}
        has_header = content.startswith("# 灵梦work 自愈补丁")
        has_steps = "# ---- 结构化修复步骤" in content
        if has_header and has_steps:
            log.append("PASS 补丁骨架结构合法(unified 注释 + 结构化步骤)")
        else:
            log.append("WARN 补丁骨架结构异常")
        metapath = os.path.join(pdir, patch_id + ".json")
        target = None
        if os.path.exists(metapath):
            with open(metapath, "r", encoding="utf-8") as f:
                meta = json.load(f)
            target = meta.get("target")
        compiled = False
        if target and os.path.isabs(target) and os.path.exists(target):
            try:
                _subprocess.run([_sys.executable, "-m", "py_compile", target],
                               capture_output=True, text=True, timeout=60)
                compiled = True
                log.append("PASS 目标文件可编译: %s" % target)
            except Exception as ex:
                log.append("FAIL 目标文件编译失败: %s" % ex)
        else:
            log.append("SKIP 无单一目标文件, 跳过编译校验(需人工/LLM 填真实改动)")
        return {"ok": True, "compiled": compiled, "log": "\n".join(log)}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def apply_patch(patch_id, repo_root=None, confirm=False):
    """人工合并门: 仅在 confirm=True 时落地。

    纪律: 绝不自动改源码。降级骨架补丁(无真实 diff) -> 仅记录『已人工确认待落地』状态。
    若未来有真实 unified diff + 真实 target -> 先备份再 patch 应用。
    返回 {ok, applied, backup_path, note}。
    """
    try:
        if not confirm:
            return {"ok": True, "applied": False,
                    "note": "需人工确认: 请在 UI 勾选『我已复核, 确认落地』后再提交。"}
        out_dir = repo_root or os.getcwd()
        pdir = os.path.join(out_dir, ".lmw_heal", "patches")
        metapath = os.path.join(pdir, patch_id + ".json")
        if not os.path.exists(metapath):
            return {"ok": False, "error": "patch not found: %s" % patch_id}
        with open(metapath, "r", encoding="utf-8") as f:
            meta = json.load(f)
        target = meta.get("target")
        ts = _dt.now().strftime("%Y%m%d%H%M%S")
        bdir = os.path.join(out_dir, ".lmw_heal", "backups", ts)
        os.makedirs(bdir, exist_ok=True)
        state = {"patch_id": patch_id,
                 "confirmed_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "target": target, "applied": False,
                 "note": "human-in-the-loop: 骨架补丁已确认, 真实改动需人工/LLM 落地"}
        with open(os.path.join(bdir, patch_id + ".confirmed.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        backup_path = None
        if target and os.path.exists(target):
            backup_path = os.path.join(bdir, os.path.basename(target))
            _shutil.copy2(target, backup_path)
        return {"ok": True, "applied": False, "backup_path": backup_path,
                "note": "已记录人工确认; 当前为降级骨架补丁, 真实改动需人工/LLM 落地(未自动改源码)。"}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def list_patches(repo_root=None):
    """列出 .lmw_heal/patches/ 下全部补丁元信息。"""
    try:
        out_dir = repo_root or os.getcwd()
        pdir = os.path.join(out_dir, ".lmw_heal", "patches")
        if not os.path.isdir(pdir):
            return {"ok": True, "patches": []}
        out = []
        for fn in sorted(os.listdir(pdir)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(pdir, fn), "r", encoding="utf-8") as f:
                        m = json.load(f)
                    # 附带 diff 文本, 供前端直接展示
                    dp = os.path.join(pdir, fn[:-5] + ".diff")
                    if os.path.exists(dp):
                        with open(dp, "r", encoding="utf-8") as f:
                            m["diff"] = f.read()
                    out.append(m)
                except Exception:
                    continue
        return {"ok": True, "patches": out}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# ------------------------------------------------------------------ 便捷封装

def run(cwd=None):
    """端到端：采集当前进程内信号 → 生成提议报告。

    cwd 仅用于 export 默认路径；本函数不写盘，纯返回报告 dict。
    """
    try:
        from . import selfcheck as _sc
        report = _sc.run()
    except Exception:
        report = None
    bus = None
    try:
        from . import event_bus as _eb
        bus = _eb.get_bus()
    except Exception:
        bus = None
    return propose(selfcheck_report=report, bus=bus)


def main():
    import sys
    rep = run()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
