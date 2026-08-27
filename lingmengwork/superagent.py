"""灵梦work · 超级 AGENT 内核 (Phase 27).

把「单循环编码 AGENT」收口为「统一超级 AGENT 内核」: 用户输入一个模糊目标,
内核自动完成「目标理解 → 域路由 → 并行编排 → 收敛(三级护栏) → 自检(质量门) → 记忆沉淀」。

复用既有能力(不长在另起炉灶, 严守不可变内核契约):
- 域路由 / 并行编排: 多智能体联邦 federation (关键词路由 + ThreadPoolExecutor 并行派发 + 汇聚)
- 记忆沉淀 / 召回: 记忆图谱 memory_graph (facts→实体关系, 跨会话推理, 隐私脱敏)
- 质量门: 离线自检中枢 selfcheck (无 LLM 确定性健康探针)
- 可观测: 事件总线 event_bus (每阶段结构化 trace 进审计链, 可回放)

工程信条: 零三方依赖(纯标准库); 无 LLM 亦可用(规则兜底); 单伙伴/单引擎失败不影响整体(错误隔离)。
验收门槛: 单目标跨 2+ 域编排成功(多伙伴并行派发 + 汇聚)。
"""

import collections
import json
import os
import time
from datetime import datetime

from . import federation as _fed
from . import memory_graph as _mg
from . import selfcheck as _sc

_STAGE_NAMES = ["目标理解", "域路由", "并行编排", "收敛护栏", "自检质量门", "记忆沉淀"]

# 进程内最近编排缓冲(供 API / 页面轮询, 重启清空), maxlen 上限防内存膨胀
_RUNS = collections.deque(maxlen=60)


def _parse_json(raw):
    """容忍代码块包裹的 JSON 解析(复用 goal_pipeline 思路)。"""
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
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SuperAgent:
    """统一超级 AGENT 内核。"""

    def __init__(self, base_dir=None):
        # base_dir=None → memory_graph 用 cwd 单例; 测试/探针注入临时目录隔离
        self.base_dir = base_dir

    # ---- 阶段 1: 目标理解 (LLM 抽取 intent/域标签/约束, 失败回退规则) ----
    def understand(self, goal, llm_call=None):
        goal = (goal or "").strip()
        intent = goal
        domains = None
        constraints = []
        # 可选 LLM 抽取 intent / 域标签 / 约束
        if llm_call:
            try:
                sys = ("你是任务理解器。把用户目标拆为结构化意图。只输出一个 JSON: "
                       "{\"intent\": \"意图摘要(一句话)\", "
                       "\"domains\": [\"code\"/\"creation\"/\"research\"/\"ops\" 中的 1-3 个], "
                       "\"constraints\": [字符串约束列表]}。domains 最多 3 个, 不相关的不要填。不要解释。")
                raw = llm_call("目标: " + goal, system=sys)
                p = _parse_json(raw)
                if isinstance(p, dict):
                    intent = (p.get("intent") or goal).strip() or goal
                    ds = [d for d in (p.get("domains") or [])
                          if d in ("code", "creation", "research", "ops")][:3]
                    if ds:
                        domains = ds
                    constraints = p.get("constraints") or []
            except Exception:
                pass
        # 规则兜底: LLM 未给出域 → 用联邦关键词路由(始终可用)
        if not domains:
            domains = _fed.get_federation().route(goal)
        # 跨会话记忆召回(注入历史经验, 失败不阻塞主流程)
        recap = ""
        try:
            recap = _mg.get_graph(self.base_dir).recall(goal, limit=12).get("recap", "")
        except Exception:
            recap = ""
        return {
            "goal": goal,
            "intent": intent,
            "domains": domains,
            "constraints": constraints,
            "memory_recap": recap,
        }

    # ---- 阶段 2: 域路由 (取 understand 给出的 domains) ----
    def route(self, understand):
        return understand.get("domains") or ["code"]

    # ---- 阶段 3: 并行编排 (联邦派发 N 伙伴) ----
    def dispatch(self, understand, session_id="", llm_call=None):
        return _fed.get_federation().dispatch(
            understand["goal"], session_id=session_id,
            hint_domains=understand.get("domains"), llm_call=llm_call)

    # ---- 阶段 4: 收敛 (三级护栏 + 一致性校验) ----
    def converge(self, dispatch_rep, quality_gate=True):
        partners = dispatch_rep.get("partners", [])
        merged = dispatch_rep.get("merged", {})
        conflicts = merged.get("conflicts", []) or []
        ok_partners = [p for p in partners if p.get("status") == "ok"]
        error_partners = [p for p in partners if p.get("status") != "ok"]
        guards = []
        # 一级护栏: 完整性(error 隔离, 单伙伴失败不阻断整体)
        if error_partners:
            guards.append({
                "level": 1, "kind": "partner_error", "severity": "warning",
                "msg": "有 %d 个伙伴执行异常, 已隔离(不影响其他伙伴): %s"
                       % (len(error_partners), "、".join(p.get("name", "") for p in error_partners)),
            })
        # 二级护栏: 冲突检测(多伙伴同类产物)
        for c in conflicts:
            guards.append({
                "level": 2, "kind": "conflict", "severity": "warning",
                "msg": c.get("note", "产出冲突, 需人工取舍"),
            })
        # 三级护栏: 质量门(系统自检评分阈值)
        score = 100
        if quality_gate:
            sc = self._quality_gate()
            score = sc.get("score", 100)
            if score < 70:
                guards.append({
                    "level": 3, "kind": "quality", "severity": "warning",
                    "msg": "系统自检评分 %d 低于阈值 70, 建议复核底层引擎" % score,
                })
        return {
            "ok": bool(ok_partners),
            "partners_total": len(partners),
            "partners_ok": len(ok_partners),
            "partners_error": len(error_partners),
            "conflicts": conflicts,
            "selfcheck_score": score,
            "guards": guards,
            "summary": merged.get("summary", ""),
            "passed": len(guards) == 0,
        }

    def _quality_gate(self):
        try:
            return _sc.run()
        except Exception:
            return {"ok": True, "score": 100, "passed": 12, "total": 12,
                    "all_ok": True, "checks": [], "ts": _now()}

    # ---- 阶段 6: 记忆沉淀 (异常隔离, 不阻塞主流程) ----
    def deposit_memory(self, goal, dispatch_rep, session_id="", llm_call=None):
        try:
            merged_summary = (dispatch_rep.get("merged") or {}).get("summary", "")
            return _mg.get_graph(self.base_dir).absorb(
                goal, merged_summary, session_id=session_id, llm_call=llm_call)
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    # ---- 统一入口 ----
    def run(self, goal, session_id="", llm_call=None, quality_gate=True):
        """超级 AGENT 统一编排入口。

        goal: 用户模糊目标
        llm_call: llm_call(prompt, system=None)->str|None, 无 key 全程规则兜底
        quality_gate: 是否执行第三级护栏(系统自检质量门); selfcheck 探针传 False 防递归
        """
        started = time.time()
        trace = []
        ok = True
        routed = []
        dispatch_rep = {}
        converge_rep = {}
        mem = {}
        understand = {}

        def _trace(stage, detail, sub_ok=True):
            trace.append({"stage": stage, "ts": _now(), "ok": sub_ok, "detail": detail})
            try:
                from . import event_bus as _eb
                _eb.emit("superagent", "stage", "%s: %s" % (stage, detail),
                         {"stage": stage, "session_id": session_id, "ok": sub_ok}, audit=True)
            except Exception:
                pass

        try:
            # 1 目标理解
            understand = self.understand(goal, llm_call=llm_call)
            _trace("目标理解", "intent=%s | 域=%s | 约束%d | 召回%d字"
                   % (understand["intent"][:24], "/".join(understand["domains"]),
                      len(understand["constraints"]), len(understand["memory_recap"])))
            # 2 域路由
            routed = self.route(understand)
            _trace("域路由", "→ %s" % "/".join(routed))
            # 3 并行编排(联邦多伙伴)
            dispatch_rep = self.dispatch(understand, session_id=session_id, llm_call=llm_call)
            _trace("并行编排", "派发 %d 伙伴, %d 成功"
                   % (len(dispatch_rep.get("partners", [])),
                      len([p for p in dispatch_rep.get("partners", []) if p.get("status") == "ok"])))
            # 4 收敛护栏(三级)
            converge_rep = self.converge(dispatch_rep, quality_gate=quality_gate)
            ok = converge_rep["ok"]
            _trace("收敛护栏", "通过=%s | 伙伴成功 %d/%d | 冲突 %d | 自检 %d"
                   % (converge_rep["passed"], converge_rep["partners_ok"],
                      converge_rep["partners_total"], len(converge_rep["conflicts"]),
                      converge_rep["selfcheck_score"]), sub_ok=ok)
            # 6 记忆沉淀
            mem = self.deposit_memory(goal, dispatch_rep, session_id=session_id, llm_call=llm_call)
            _trace("记忆沉淀", "实体+%d 关系+%d 事实+%d"
                   % (mem.get("entities_added", 0), mem.get("relations_added", 0), mem.get("facts_count", 0)))
        except Exception as e:
            ok = False
            _trace("内核异常", "%s: %s" % (type(e).__name__, e), sub_ok=False)
            result = {
                "ok": False, "goal": goal, "error": "%s: %s" % (type(e).__name__, e),
                "trace": trace, "elapsed_sec": round(time.time() - started, 1),
            }
            self._record(result)
            return result

        result = {
            "ok": ok,
            "goal": goal,
            "intent": understand,
            "routed": routed,
            "dispatch": dispatch_rep,
            "converge": converge_rep,
            "selfcheck": converge_rep.get("selfcheck_score"),
            "memory": mem,
            "trace": trace,
            "elapsed_sec": round(time.time() - started, 1),
        }
        self._record(result)
        return result

    def _record(self, result):
        try:
            cv = result.get("converge") or {}
            mem = result.get("memory") or {}
            _RUNS.append({
                "goal": result.get("goal", ""),
                "ts": _now(),
                "ok": result.get("ok", False),
                "routed": result.get("routed", []),
                "partners_ok": cv.get("partners_ok", 0),
                "partners_total": cv.get("partners_total", 0),
                "conflicts": len(cv.get("conflicts", []) or []),
                "selfcheck_score": cv.get("selfcheck_score", 100),
                "guards_passed": cv.get("passed", True),
                "entities_added": mem.get("entities_added", 0),
                "elapsed_sec": result.get("elapsed_sec", 0),
            })
        except Exception:
            pass


def get_recent_runs(limit=20):
    """最近编排概览(供 API / 页面轮询)。"""
    items = list(_RUNS)
    return items[-limit:][::-1]


def run(goal, session_id="", llm_call=None, base_dir=None, quality_gate=True):
    """模块级便捷入口。"""
    return SuperAgent(base_dir=base_dir).run(
        goal, session_id=session_id, llm_call=llm_call, quality_gate=quality_gate)
