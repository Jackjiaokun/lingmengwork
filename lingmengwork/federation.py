"""灵梦work · 工作伙伴多智能体联邦 (Phase 25).

把「单循环编码 AGENT」升级为「多智能体联邦 + 统一超级 AGENT 内核」的第一步:
- Partner: 专职某域的子 AGENT(编码/创作/研究/运维), 各有独立职责与工具集。
- Federation: 伙伴注册中心, 负责 目标路由 → 并行派发 → 结果汇聚。
- 协作契约: 跨伙伴任务并行派发 + 结构化汇聚(去重/冲突检测) + 审计链路。

工程信条: 零三方依赖(纯标准库); 无 LLM 亦可用(规则兜底); 复用 creation_domains 四域派发;
单伙伴失败不影响整体(错误隔离); 每次派发/汇聚进事件总线 + 审计链。
"""

import re
import concurrent.futures as cf
from dataclasses import dataclass, field, asdict
from datetime import datetime

# 伙伴域名 → 关键词映射(用于目标路由, 命中即派发对应伙伴)
_DOMAIN_KEYWORDS = {
    "code": ["编码", "代码", "函数", "脚本", "bug", "重构", "调试", "接口", "api", "程序", "软件",
             "网站", "应用", "后端", "前端", "类", "模块", "服务", "sdk", "cli", "开发"],
    "creation": ["音频", "语音", "配音", "音乐", "音效", "图片", "图像", "海报", "配图", "插画",
                 "视频", "短片", "分镜", "动画", "封面", "logo", "ppt", "h5"],
    "research": ["研究", "调研", "资料", "搜索", "检索", "查", "分析", "对比", "总结", "综述",
                "文献", "竞品", "报告", "趋势"],
    "ops": ["部署", "运维", "上线", "服务器", "ci", "cd", "构建", "打包", "发布", "重打包",
            "配置", "环境", "docker", "k8s", "监控", "日志"],
}

# 四个默认伙伴元信息(与《终极蓝图》能力矩阵一致)
_PARTNER_META = {
    "code": {
        "id": "code", "name": "编码伙伴", "domain": "code", "emoji": "🟣", "theme": "#8b5cf6",
        "tools": ["read", "write", "run", "refactor", "debug", "multitest", "review"],
        "desc": "读/写/跑/重构/调试/多文件、工具链、单测、评审",
    },
    "creation": {
        "id": "creation", "name": "创作伙伴", "domain": "creation", "emoji": "🌈", "theme": "#f472b6",
        "tools": ["audio", "image", "video", "multimodal-lib"],
        "desc": "音频/图片/视频统一创作工作台(经 creation_domains 派发到子域)",
    },
    "research": {
        "id": "research", "name": "研究伙伴", "domain": "research", "emoji": "🔍", "theme": "#22d3ee",
        "tools": ["search", "fetch", "retrieve", "summarize"],
        "desc": "资料检索/竞品分析/文献综述/结构化研究简报",
    },
    "ops": {
        "id": "ops", "name": "运维伙伴", "domain": "ops", "emoji": "🛠️", "theme": "#34d399",
        "tools": ["shell", "git", "fs", "deploy", "monitor"],
        "desc": "构建/打包/部署/CI/环境/监控 一体化运维",
    },
}


@dataclass
class Partner:
    """专职某域的子 AGENT。"""
    id: str
    name: str
    domain: str
    tools: list
    max_iter: int = 8
    emoji: str = ""
    theme: str = "#8b5cf6"
    desc: str = ""


@dataclass
class PartnerResult:
    """单伙伴执行结果(结构化, 可直接进 struct-panel)。"""
    partner_id: str
    name: str
    domain: str
    status: str            # ok | error
    summary: str
    plan: str = ""
    artifacts: list = field(default_factory=list)
    error: str = ""


def _creation_subdomain(goal):
    """创作伙伴内部子域路由: audio / image(默认) / video。"""
    g = (goal or "").lower()
    if any(k in g for k in ["音频", "语音", "配音", "音乐", "音效", "tts", "配乐"]):
        return "audio"
    if any(k in g for k in ["视频", "短片", "分镜", "动画", "封面", "片头"]):
        return "video"
    return "image"


class Federation:
    """伙伴注册中心: 路由 / 并行派发 / 汇聚。"""

    def __init__(self):
        self._partners = {}
        for pid, meta in _PARTNER_META.items():
            self.register(Partner(**meta))

    # ---- 注册 / 查询 ----
    def register(self, p: Partner):
        self._partners[p.id] = p

    def list_partners(self):
        return [asdict(p) for p in self._partners.values()]

    def get(self, pid):
        return self._partners.get(pid)

    # ---- 路由: 目标 → 伙伴 id 列表 ----
    def route(self, goal, hint_domains=None, llm_call=None):
        g = (goal or "").lower()
        routed = []
        if hint_domains:
            for hd in hint_domains:
                if hd in self._partners:
                    routed.append(hd)
        if not routed:
            for pid, kws in _DOMAIN_KEYWORDS.items():
                if any(k in g for k in kws):
                    routed.append(pid)
        if not routed:
            routed = ["code"]  # 默认编码伙伴兜底
        # 去重保序
        seen = set()
        routed = [x for x in routed if not (x in seen or seen.add(x))]
        return routed

    # ---- 单伙伴执行(确定性兜底, 异常隔离) ----
    def _run_partner(self, partner, goal, session_id, llm_call):
        try:
            if partner.domain == "code":
                return self._run_code(partner, goal, llm_call)
            if partner.domain == "creation":
                return self._run_creation(partner, goal, llm_call)
            if partner.domain == "research":
                return self._run_research(partner, goal, llm_call)
            if partner.domain == "ops":
                return self._run_ops(partner, goal, llm_call)
            return PartnerResult(partner.id, partner.name, partner.domain, "ok",
                                 "已接收目标并登记(通用兜底)", "")
        except Exception as e:
            return PartnerResult(partner.id, partner.name, partner.domain, "error",
                                 "伙伴执行异常", "", error="%s: %s" % (type(e).__name__, e))

    def _run_code(self, p, goal, llm_call):
        from . import creation_domains as cd
        res = cd.dispatch("code", goal, llm_call=llm_call)
        return PartnerResult(p.id, p.name, p.domain, "ok",
                             "产出编码方案(架构/关键产出/执行步骤)", res.get("plan", ""),
                             artifacts=[{"type": "blueprint", "domain": "code"}])

    def _run_creation(self, p, goal, llm_call):
        from . import creation_domains as cd
        sub = _creation_subdomain(goal)
        res = cd.dispatch(sub, goal, llm_call=llm_call)
        return PartnerResult(p.id, p.name, p.domain, "ok",
                             "经创作工作台派发到 %s 域, 产出制作蓝图" % res.get("domain_name", sub),
                             res.get("plan", ""),
                             artifacts=[{"type": "blueprint", "domain": sub}])

    def _run_research(self, p, goal, llm_call):
        keywords = [w for w in re.split(r"[\s,，。.、]+", goal) if len(w) >= 2][:6]
        outline = (
            "## 研究目标\n%s\n\n"
            "## 关键检索词\n%s\n\n"
            "## 建议步骤\n"
            "1) 明确研究边界与量化指标\n"
            "2) 多源检索(搜索/抓取/记忆召回)\n"
            "3) 交叉验证与去噪\n"
            "4) 对比分析并形成结论\n"
            "5) 沉淀为结构化报告"
        ) % (goal, " · ".join(keywords) if keywords else "(见目标)")
        if llm_call:
            try:
                out = llm_call("请为以下研究需求撰写结构化研究简报: " + goal,
                               system="你是资深研究分析师, 输出 Markdown 三段: 研究目标 / 关键发现框架 / 结论建议")
                if isinstance(out, str) and out.strip():
                    outline = out.strip()
            except Exception:
                pass
        return PartnerResult(p.id, p.name, p.domain, "ok",
                             "产出结构化研究简报(检索词 + 步骤)", outline,
                             artifacts=[{"type": "research_brief", "domain": "research"}])

    def _run_ops(self, p, goal, llm_call):
        outline = (
            "## 运维目标\n%s\n\n"
            "## 推荐动作\n"
            "1) 评估改动范围与风险\n"
            "2) 本地 / 沙箱验证(构建/单测)\n"
            "3) 灰度发布或回滚预案\n"
            "4) 健康监控与日志告警\n"
            "5) 完成后记入审计链"
        ) % goal
        if llm_call:
            try:
                out = llm_call("请为以下运维/部署需求撰写执行计划: " + goal,
                               system="你是资深 SRE, 输出 Markdown 三段: 目标 / 风险 / 执行步骤")
                if isinstance(out, str) and out.strip():
                    outline = out.strip()
            except Exception:
                pass
        return PartnerResult(p.id, p.name, p.domain, "ok",
                             "产出运维/部署执行计划", outline,
                             artifacts=[{"type": "ops_plan", "domain": "ops"}])

    # ---- 并行派发 ----
    def dispatch(self, goal, session_id="", hint_domains=None, llm_call=None, max_workers=4):
        routed = self.route(goal, hint_domains=hint_domains, llm_call=llm_call)
        results = []
        n = max(1, min(max_workers, len(routed)))
        with cf.ThreadPoolExecutor(max_workers=n) as ex:
            futs = {ex.submit(self._run_partner, self._partners[pid], goal, session_id, llm_call): pid
                    for pid in routed}
            for fut in cf.as_completed(futs):
                results.append(fut.result())
        # 按路由顺序重排, 保证结果稳定可读
        order = {pid: i for i, pid in enumerate(routed)}
        results.sort(key=lambda r: order.get(r.partner_id, 99))
        # 审计: 派发(关键操作) + 各伙伴完成
        try:
            from . import event_bus as _eb
            _eb.emit("federation", "dispatch",
                     "联邦派发 %d 伙伴: %s" % (len(results), ", ".join(routed)),
                     {"goal": goal, "routed": routed, "session_id": session_id}, audit=True)
            for r in results:
                _eb.emit("federation", "partner_done",
                         "伙伴 %s 完成(%s)" % (r.name, r.status),
                         {"partner": r.partner_id, "status": r.status}, audit=False)
        except Exception:
            pass
        merged = self.merge(results)
        return {
            "ok": True,
            "goal": goal,
            "routed": routed,
            "partners": [asdict(r) for r in results],
            "merged": merged,
            "dispatched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": session_id,
        }

    # ---- 汇聚: 去重 / 冲突检测 / 统一 struct-panel ----
    def merge(self, results):
        parts = []
        conflicts = []
        seen_types = {}
        for r in results:
            parts.append({
                "partner_id": r.partner_id,
                "name": r.name,
                "domain": r.domain,
                "status": r.status,
                "summary": r.summary,
            })
            for a in (r.artifacts or []):
                t = a.get("type")
                if t in seen_types:
                    conflicts.append({
                        "type": t,
                        "partners": [seen_types[t], r.partner_id],
                        "note": "多伙伴产出同类产物(%s), 需人工取舍" % t,
                    })
                else:
                    seen_types[t] = r.partner_id
        lines = ["## 联邦协同结果", ""]
        for p in parts:
            mark = "✅" if p["status"] == "ok" else "❌"
            lines.append("%s **%s**（%s）: %s" % (mark, p["name"], p["domain"], p["summary"]))
        if conflicts:
            lines.append("")
            lines.append("### ⚠ 产出冲突")
            for c in conflicts:
                lines.append("- %s" % c["note"])
        return {
            "ok": True,
            "parts": parts,
            "conflicts": conflicts,
            "summary": "\n".join(lines),
        }


def get_federation():
    """联邦全局单例(无状态路由中心, 每次返回新实例即可, 此处直接构造)。"""
    return Federation()
