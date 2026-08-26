"""自主模式引擎: 目标驱动自驱循环 + Critic 回路 + 自修复 (终极蓝图 Phase 5).

循环: 规划(plan) -> 行动(act: LLM 产出本轮可执行方案) -> 观察(observe: 自检)
      -> 评判(critic) -> 未达成则反思(reflect)并修正 -> 重复, 直至达成或达上限。

不依赖外部工具执行, 以 LLM 驱动的规划 / 观察 / 评判 / 反思闭环展示自主能力,
安全可演示; 真实工具执行可在此框架上接入(_call 替换为工具总线调用)。
"""

import json
import re

PLAN_SYS = (
    "你是灵梦work的自主执行 Agent。给定目标与已有进度, 请产出【本轮最关键的下一步行动计划】。\n"
    "要求: 具体、可执行、聚焦单步; 用 Markdown 输出, 含 ## 本轮目标 / ## 具体步骤 / ## 预期产出 三段。"
)
OBS_SYS = (
    "你是严谨的执行观察员。给定『行动计划』与『总目标』, 请客观评估本轮计划的执行情况与质量。\n"
    "用 Markdown 输出: ## 观察(做了什么/产出如何) / ## 与目标差距(还差什么)。"
)
CRITIC_SYS = (
    "你是 Critic 评判官。给定『总目标』与『当前进度摘要』, 只回答两件事:\n"
    "1) 目标是否已达成(达成/未达成); 2) 若未达成, 主要卡点是什么。\n"
    "严格按 JSON 输出: {\"done\": true|false, \"score\": 0-100, \"note\": \"一句话\"}"
)
REFLECT_SYS = (
    "你是反思与修复 Agent。给定『未达成的原因』与『当前计划』, 请产出【修正后的下一步计划】。\n"
    "要求: 针对卡点调整策略, 避免重复已失败的动作; 用 Markdown 输出 ## 修正策略 / ## 新步骤。"
)


def _call(llm_call, system, user):
    if not llm_call:
        return ""
    try:
        out = llm_call(user, system=system)
        return out.strip() if isinstance(out, str) else ""
    except Exception:
        return ""


def _parse_critic(raw, llm_call, goal, plan, observe):
    """解析 critic 的 JSON 判定; 失败则回退二次 LLM 判定。"""
    if raw:
        s = raw.strip()
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                done = bool(obj.get("done", False))
                try:
                    score = int(obj.get("score", 0) or 0)
                except (TypeError, ValueError):
                    score = 0
                note = str(obj.get("note", ""))
                return done, score, note
            except Exception:
                pass
    if llm_call:
        fb = _call(llm_call,
                   "只回答『达成』或『未达成』, 不要解释。总目标: %s\n进度: %s" % (goal, (plan + observe)[:1500]),
                   "判定")
        done = ("达成" in fb) and ("未达成" not in fb)
        return done, (90 if done else 40), fb[:80]
    return False, 0, "无评判"


def run(goal, llm_call, context="", max_iter=6, threshold=75):
    """执行自主循环。返回 {ok, goal, reached, conclusion, iterations:[...]}。

    goal: 总目标
    llm_call: llm_call(prompt, system=None)->str|None
    context: 可选前置进度
    max_iter: 最大自驱轮次
    threshold: 达成评分阈值(保留字段, 用于未来早停)
    """
    if max_iter < 1:
        max_iter = 1
    iterations = []
    progress = context or "（暂无前置进度）"
    reached = False
    conclusion = ""
    for i in range(1, max_iter + 1):
        plan = _call(llm_call, PLAN_SYS,
                     "总目标: %s\n已有进度: %s\n轮次: %d/%d\n请产出本轮计划。" % (goal, progress, i, max_iter))
        observe = _call(llm_call, OBS_SYS, "总目标: %s\n本轮计划:\n%s" % (goal, plan))
        done, score, note = _parse_critic(
            _call(llm_call, CRITIC_SYS, "总目标: %s\n当前进度摘要: %s" % (goal, (plan + "\n" + observe)[:2000])),
            llm_call, goal, plan, observe)
        iterations.append({
            "step": i, "plan": plan, "observation": observe,
            "critic": {"done": done, "score": score, "note": note},
        })
        progress = "第%d轮: %s\n观察: %s" % (i, plan[:600], observe[:400])
        if done:
            reached = True
            conclusion = observe or plan
            break
        reflect = _call(llm_call, REFLECT_SYS,
                        "总目标: %s\n未达成原因: %s\n当前计划:\n%s" % (goal, note, plan))
        iterations[-1]["reflection"] = reflect
        conclusion = reflect
    return {
        "ok": True, "goal": goal, "reached": reached,
        "iterations": iterations, "conclusion": conclusion, "threshold": threshold,
    }
