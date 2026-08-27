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
                 confidence, auto_fixable, source_ref, rule_id):
        self.severity = severity          # high / medium / low
        self.area = area
        self.symptom = symptom
        self.hypothesis = hypothesis
        self.actions = actions            # list[str]
        self.confidence = float(confidence)
        self.auto_fixable = bool(auto_fixable)
        self.source_ref = source_ref
        self.rule_id = rule_id

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
    actions = ["查看日志定位根因", "在源码层修复并补回归测试", "重新跑 selfcheck 复核"]
    hypothesis = "自检项『%s』未通过，系统该能力处于降级/不可用状态。" % name
    if name == "关键静态资产":
        missing = _parse_missing_static(detail)
        if missing:
            hypothesis = "关键前端资产缺失，面板相关页面无法加载。"
            actions = ["从版本库恢复缺失文件: " + "、".join(missing),
                       "校验 web/static 目录构建产物完整",
                       "重新跑 selfcheck 复核"]
    elif name == "核心模块导入":
        hypothesis = "存在模块导入失败，通常是依赖缺失或语法错误。"
        actions = ["检查 import 报错模块与依赖",
                   "运行 target 解释器的 py_compile 校验",
                   "补 requirements / 重建虚拟环境"]
    return Proposal(
        severity="high", area=area,
        symptom="自检项『%s』失败: %s" % (name, detail),
        hypothesis=hypothesis, actions=actions,
        confidence=0.9, auto_fixable=False,
        source_ref="selfcheck:%s" % name, rule_id="selfcheck_fail",
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


def export_proposals(report, out_dir):
    """把提议落盘到 out_dir/.lmw_heal/proposals.jsonl (human-in-the-loop 待审)。

    返回 {ok, path, count}。不直接改动任何源码。
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
        readme = os.path.join(d, "README.md")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as f:
                f.write("# 自愈提议待审区 (.lmw_heal)\n\n"
                        "本目录由 Phase 18 自主进化闭环生成。\n"
                        "`proposals.jsonl` 中每条为一次健康快照的补丁提议清单\n"
                        "(severity/area/symptom/hypothesis/actions/confidence)。\n\n"
                        "**human-in-the-loop**：系统仅生成建议，不自动改动源码；\n"
                        "请人工复核后再落地补丁。\n")
        return {"ok": True, "path": path, "count": report.get("proposal_count", 0)}
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
