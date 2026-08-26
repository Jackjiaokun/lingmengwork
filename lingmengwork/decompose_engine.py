"""层级任务分解引擎 (Decomposition Engine)。

把高层目标拆成**带依赖的可执行步骤树**, 支持:
- LLM 驱动拆解(给出结构化 JSON: 步骤 / 依赖 / 预估 / 域路由)
- 规则兜底(无 key 时按段落/编号/动作词抽取)
- 拓扑排序校验(检测环依赖)与扁平化执行序列生成
- 与计划书(plans)打通: 分解结果可直接落为计划书 + 任务清单

输出步骤结构:
    {"id","title","detail","domain"(code/audio/image/video/any),"depends":[id...],
     "estimate"(人时/可选),"status":"todo"}
"""
import json
import re

DOMAINS = ("code", "audio", "image", "video", "any")

_DECOMPOSE_SYS = (
    "你是顶级的任务分解架构师。把一个高层目标拆成可独立执行、可并行/串行编排的步骤树。\n"
    "只输出一个 JSON 数组(不要解释, 不要 markdown 代码块包裹), 每个元素:\n"
    "{\"id\":\"s1\",\"title\":\"步骤标题\",\"detail\":\"具体做法(1-2 句)\","
    "\"domain\":\"code|audio|image|video|any\",\"depends\":[\"s1\"前序步骤id],\"estimate\":\"可选人时\"}\n"
    "要求: id 用 s1,s2,... 顺序; depends 只能引用出现更早的 id; 依赖须构成 DAG(无环); "
    "步骤粒度适中(3-12 步); 若涉及创作域请标 domain。"
)


def _rule_decompose(goal, text=""):
    """规则兜底: 从目标/文本中按动作词/编号抽取步骤, 线性依赖。"""
    raw = (text or "").strip()
    steps = []
    if raw:
        for line in raw.splitlines():
            s = line.strip().lstrip("-•*0123456789. ").strip()
            if len(s) >= 4 and any(k in s for k in ("请", "实现", "添加", "创建", "修复", "写", "做", "生成", "配置", "设计", "重构", "搭建", "接入", "合成")):
                steps.append(s)
    if not steps:
        steps = [goal]
    # 创作域粗判
    def guess_domain(t):
        low = t.lower()
        if any(k in low for k in ("配音", "语音", "tts", "音频", "声音", "音效")):
            return "audio"
        if any(k in low for k in ("图", "配图", "文生图", "图像", "封面", "海报")):
            return "image"
        if any(k in low for k in ("视频", "成片", "剪辑", "分镜", "短片")):
            return "video"
        return "code"
    out = []
    for i, s in enumerate(steps[:12], 1):
        out.append({
            "id": "s%d" % i,
            "title": s[:60],
            "detail": s,
            "domain": guess_domain(s),
            "depends": ["s%d" % (i - 1)] if i > 1 else [],
            "estimate": "",
            "status": "todo",
        })
    return out


def _parse_llm_steps(raw):
    """从 LLM 输出解析 JSON 步骤数组(容忍代码块包裹)。"""
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
        # 再次去围栏
        s = s.strip("`")
    try:
        data = json.loads(s)
    except Exception:
        # 尝试抽取第一个 [ ... ] 片段
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(data, list):
        return None
    # 归一化 + 校验 id/depends
    out, seen = [], set()
    for i, it in enumerate(data, 1):
        if not isinstance(it, dict):
            continue
        sid = (it.get("id") or ("s%d" % i)).strip()
        if not sid or sid in seen:
            sid = "s%d" % i
        seen.add(sid)
        deps = it.get("depends") or []
        deps = [d for d in deps if isinstance(d, str) and d in seen]
        dom = (it.get("domain") or "any")
        if dom not in DOMAINS:
            dom = "any"
        out.append({
            "id": sid,
            "title": (it.get("title") or it.get("detail") or "").strip()[:80],
            "detail": (it.get("detail") or "").strip(),
            "domain": dom,
            "depends": deps,
            "estimate": (it.get("estimate") or "").strip(),
            "status": "todo",
        })
    return out or None


def decompose(goal, text="", llm_call=None, system_extra=None):
    """层级分解: 返回步骤树(list of step dict)。LLM 优先, 规则兜底。

    system_extra: 额外系统提示(如跨会话记忆上下文), 仅注入 LLM 路径的 system,
    不参与规则兜底的文本扫描(避免记忆文本被误判为步骤)。
    """
    if llm_call:
        sys = _DECOMPOSE_SYS
        if system_extra:
            sys = sys + "\n\n" + system_extra
        raw = _ask_llm(llm_call, sys, "目标: %s\n%s" % (goal, text[:4000]))
        steps = _parse_llm_steps(raw)
        if steps:
            return _validate_dag(steps)
    return _validate_dag(_rule_decompose(goal, text))


def _ask_llm(llm_call, system, user):
    if not llm_call:
        return None
    try:
        r = llm_call(user, system=system)
        return r if isinstance(r, str) and r.strip() else None
    except Exception:
        return None


def _validate_dag(steps):
    """拓扑排序校验: 检测环, 若有环则断开回边(保留线性近似), 并标注 parallel 分组。"""
    ids = [s["id"] for s in steps]
    idset = set(ids)
    # 合法化 depends(只保留出现在自身之前的 id, 防环)
    pos = {sid: i for i, sid in enumerate(ids)}
    for s in steps:
        s["depends"] = [d for d in s.get("depends", []) if d in idset and pos[d] < pos[s["id"]]]
    # 计算层数(用于并行分组展示)
    depth = {}
    def calc(sid):
        if sid in depth:
            return depth[sid]
        s = next((x for x in steps if x["id"] == sid), None)
        if not s or not s["depends"]:
            depth[sid] = 0
            return 0
        d = 1 + max(calc(d) for d in s["depends"])
        depth[sid] = d
        return d
    for s in steps:
        calc(s["id"])
        s["layer"] = depth[s["id"]]
    return steps


def to_plan_payload(goal, steps, title=None):
    """把分解结果转为计划书可保存的 payload(title/content/tasks)。"""
    lines = ["# %s" % (title or goal), "", "> 层级任务分解 · 共 %d 步" % len(steps), ""]
    for s in steps:
        dep = (" ← 依赖 " + ",".join(s["depends"])) if s.get("depends") else ""
        dom = s.get("domain", "any")
        lines.append("## %s · [%s]%s" % (s["title"], dom, dep))
        if s.get("detail"):
            lines.append(s["detail"])
        if s.get("estimate"):
            lines.append("预估: %s" % s["estimate"])
        lines.append("")
    tasks = [{"title": s["title"], "status": "todo", "domain": s.get("domain", "any"),
              "depends": s.get("depends", [])} for s in steps]
    return {"title": title or goal, "content": "\n".join(lines), "tasks": tasks, "status": "todo"}


def execution_order(steps):
    """返回扁平执行序列(拓扑序; 同层可并行)。"""
    done, order = set(), []
    remaining = list(steps)
    while remaining:
        ready = [s for s in remaining if all(d in done for d in s.get("depends", []))]
        if not ready:
            # 防御: 断不开的环, 直接按原序收尾
            ready = remaining[:1]
        for s in ready:
            order.append(s["id"])
            done.add(s["id"])
        remaining = [s for s in remaining if s["id"] not in done]
    return order
