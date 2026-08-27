"""灵梦work · 插件中枢 (Phase 32).

把「外部连接器 (Connector)」与「专家画像 (Expert)」统一收口, 使超级 AGENT 内核可自动发现并
热接入第三方能力: Connector 提供外部工具调用 (MCP/HTTP/CLI/文件系统), Expert 提供 LLM 专家
画像 (system prompt + 域标签) 供路由 hint 与执行器升级使用。

设计原则:
- **注册制**: register_connector / register_expert 手动注册, 亦支持目录扫描 (discover)。
- **可用性降级**: Connector 声明 env_required / optional_dep, 缺失时自动标 unavailable(不崩),
  wire() 时只激活 available 的; Expert 始终 available(纯画像)。
- **自动接入超级 AGENT**: wire() 把可用 connector/expert 注入 superagent ——
  expert 作为并行编排 hint_domains 补充 + executor 覆盖提示;
  connector 可在 execute 阶段按需调用 (可选)。
- **审计**: 接入/降级/调用均 emit 进 event_bus 审计链。
- **零三方依赖**, 无 key 全程可用。
"""

import os
import re
import time
import json
import importlib
from datetime import datetime
from collections import OrderedDict


class Connector:
    """外部连接器: 声明能力 + 可用性 + 调用入口。"""

    def __init__(self, name, category, description, call_fn=None,
                 env_required=None, optional_dep=None, extra=None, tags=None):
        self.name = name
        self.category = category or "tool"
        self.description = description
        self.call_fn = call_fn  # callable(goal, **kwargs)->dict|str|None
        self.env_required = list(env_required or [])
        self.optional_dep = list(optional_dep or [])
        self.extra = extra or {}
        self.tags = list(tags or [])  # 能力关键词, 供联邦路由标签匹配

    def check(self):
        """检查可用性: 必需 env 变量缺失 → 降级 unavailable。"""
        missing = []
        for k in self.env_required:
            v = os.environ.get(k)
            if not v:
                missing.append(k)
        return {"available": not missing, "missing_env": missing}

    def call(self, goal="", **kwargs):
        if not self.call_fn:
            return {"ok": False, "error": "no handler", "name": self.name}
        if not self.check()["available"]:
            return {"ok": False, "error": "unavailable", "name": self.name,
                    "missing_env": self.check()["missing_env"]}
        try:
            r = self.call_fn(goal, **kwargs)
            if r is None:
                return {"ok": True, "name": self.name, "result": ""}
            if isinstance(r, dict):
                r.setdefault("name", self.name)
                return r
            return {"ok": True, "name": self.name, "result": r}
        except Exception as e:
            return {"ok": False, "name": self.name, "error": "%s: %s" % (type(e).__name__, e)}

    def to_dict(self):
        chk = self.check()
        return {
            "name": self.name, "category": self.category,
            "description": self.description, "available": chk["available"],
            "missing_env": chk["missing_env"],
            "optional_dep": self.optional_dep,
            "extra": self.extra, "tags": self.tags,
        }


class Expert:
    """专家画像: LLM system prompt + 域标签, 供超级 AGENT 路由 hint / 执行器提示。"""

    def __init__(self, name, domain, system_prompt, description="", tags=None):
        self.name = name
        self.domain = domain
        self.system_prompt = system_prompt
        self.description = description
        self.tags = list(tags or [])

    def to_dict(self):
        return {
            "name": self.name, "domain": self.domain,
            "description": self.description, "tags": self.tags,
        }


class PluginHub:
    """插件中枢: 统一注册/发现/接入。进程内单例 (hub = get_hub())。"""

    def __init__(self):
        self.connectors = OrderedDict()
        self.experts = OrderedDict()

    # ---------------------------------------------------------------- 注册
    def register_connector(self, name, **kwargs):
        c = Connector(name, **kwargs)
        self.connectors[name] = c
        _emit("plugin", "connect", "connector 注册: %s (%s)" % (name, c.category),
              c.to_dict())
        return c

    def register_expert(self, name, domain, system_prompt, **kwargs):
        e = Expert(name, domain, system_prompt, **kwargs)
        self.experts[name] = e
        _emit("plugin", "connect", "expert 注册: %s (domain=%s)" % (name, domain),
              e.to_dict())
        return e

    # ---------------------------------------------------------------- 查询
    def list_connectors(self, category=None, available_only=False):
        out = []
        for c in self.connectors.values():
            d = c.to_dict()
            if category and c.category != category:
                continue
            if available_only and not d["available"]:
                continue
            out.append(d)
        return out

    def list_experts(self, domain=None):
        out = []
        for e in self.experts.values():
            if domain and e.domain != domain:
                continue
            out.append(e.to_dict())
        return out

    def get_connector(self, name):
        return self.connectors.get(name)

    def get_expert(self, name):
        return self.experts.get(name)

    # ---------------------------------------------------------------- 发现(目录扫描)
    def discover(self, plugin_dir):
        """扫描 plugin_dir 下 *.py, 对暴露 register_connectors(hub) / register_experts(hub)
        的模块逐一执行注册。目录不存在/模块异常均不崩。"""
        found = {"connectors": 0, "experts": 0}
        if not os.path.isdir(plugin_dir):
            return found
        for fn in sorted(os.listdir(plugin_dir)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            mod_name = fn[:-3]
            try:
                spec = importlib.util.spec_from_file_location(
                    "lmw_plugin_" + mod_name, os.path.join(plugin_dir, fn))
                if not spec or not spec.loader:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "register_connectors"):
                    before = len(self.connectors)
                    mod.register_connectors(self)
                    found["connectors"] += len(self.connectors) - before
                if hasattr(mod, "register_experts"):
                    before = len(self.experts)
                    mod.register_experts(self)
                    found["experts"] += len(self.experts) - before
            except Exception as e:
                _emit("plugin", "discover_error", "加载插件 %s 失败: %s" % (mod_name, e),
                      {"file": fn, "error": "%s: %s" % (type(e).__name__, e)})
        return found

    # ---------------------------------------------------------------- 联邦路由匹配
    def match_connectors(self, goal):
        """按目标关键词匹配可用连接器(name/category/tags/description 命中),
        返回 [{name, category, description, tags}]。无 LLM 亦可用(规则关键词)。
        兼容中英混合目标: 中文无标点时用 bigram 兜底(如「诊断网络端点」→「诊断」「网络」等)。"""
        g = (goal or "").lower()
        # 第一刀: ASCII 空白 + 中文标点
        raw = re.split(r"[\s,，。.、]+", g)
        # 第二刀: 对每个字块, 在 CJK/非CJK 边界再切(解决「系统health」粘连)
        pieces = []
        for r in raw:
            if not r:
                continue
            sub = re.split(r'(?<=[\u4e00-\u9fff])(?=[^\u4e00-\u9fff])|(?<=[^\u4e00-\u9fff])(?=[\u4e00-\u9fff])', r)
            pieces.extend(sub)
        # 过滤空串, 取长度 ≥2
        toks = list(dict.fromkeys([p for p in pieces if len(p) >= 2]))
        if not toks:
            return []
        # 中文 bigram 兜底: 对 CJK token 展开 2 字组合, 兼容无标点中文目标
        cn_bigrams = []
        for w in toks:
            if re.search(r"[\u4e00-\u9fff]", w):
                for i in range(max(0, len(w) - 1)):
                    cn_bigrams.append(w[i:i+2])
        toks = list(dict.fromkeys(toks + cn_bigrams))
        matched = []
        for c in self.connectors.values():
            if not c.check()["available"]:
                continue
            haystack = " %s %s %s" % (c.name, c.category, " ".join(c.tags))
            hl = haystack.lower()
            if any(t in hl for t in toks):
                matched.append({"name": c.name, "category": c.category,
                                "description": c.description, "tags": c.tags})
        return matched

    # ---------------------------------------------------------------- 接入超级 AGENT
    def wire(self, superagent, goal=""):
        """把可用 connector/expert 注入 superagent, 返回接入摘要。

        - experts → 作为理解阶段 hint_domains 补充(不覆盖 LLM/路由结果)
        - connectors → 注入 superagent._plugin_connectors, execute 阶段可按名调用
        """
        wired = {"experts": [], "connectors": [], "downgraded": []}
        for name, e in self.experts.items():
            hint = e.domain
            if hint:
                wired["experts"].append({"name": name, "domain": hint})
        for name, c in self.connectors.items():
            chk = c.check()
            if chk["available"]:
                wired["connectors"].append(c.to_dict())
            else:
                wired["downgraded"].append({"name": name, "missing_env": chk["missing_env"]})
        if hasattr(superagent, "plugin_connectors"):
            superagent.plugin_connectors = {
                n: c for n, c in self.connectors.items() if c.check()["available"]}
        _emit("plugin", "wire", "插件接入超级 AGENT: experts %d / connectors %d / 降级 %d"
              % (len(wired["experts"]), len(wired["connectors"]), len(wired["downgraded"])),
              wired, audit=True)
        return wired


_hub = None


def get_hub():
    """返回进程内单例 PluginHub。首次创建时触发 bootstrap。"""
    global _hub
    if _hub is None:
        _hub = PluginHub()
        _bootstrap()
    return _hub


def reset_hub():
    """重置插件中枢(测试用): 下次 get_hub() 会重新 bootstrap。"""
    global _hub
    _hub = None
    return get_hub()


# ---------------------------------------------------------------- 内置默认 connector/expert
def _default_health_connector(goal="", **kw):
    try:
        from . import selfcheck as _sc
        r = _sc.run()
        return {"ok": True, "score": r.get("score"), "total": r.get("total"),
                "passed": r.get("passed"), "checks": [c["name"] for c in r.get("checks", [])]}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def _default_recall_connector(goal="", **kw):
    try:
        from . import memory_graph as _mg
        g = _mg.get_graph(":memory:")
        r = g.recall(goal, limit=6)
        return {"ok": True, "recap": r.get("recap", ""), "entities": len(r.get("entities", []))}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def _default_recent_runs_connector(goal="", **kw):
    try:
        from . import superagent as _sa
        runs = _sa.get_recent_runs(10)
        return {"ok": True, "count": len(runs), "runs": runs}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# 首次导入时注入默认 connector/expert
def _emit(source, kind, msg, data=None, audit=False):
    """进 event_bus 审计链, 失败静默(不阻塞主流程)。"""
    try:
        from . import event_bus as _eb
        _eb.emit(source, kind, msg, data or {}, audit=audit)
    except Exception:
        pass


def _bootstrap():
    hub = get_hub()
    if "health" in hub.connectors:
        return  # 已引导
    hub.register_connector(
        name="health", category="observability",
        description="内置健康自检连接器: 调用 selfcheck.run 返回全链路健康评分与探针状态",
        call_fn=_default_health_connector)
    hub.register_connector(
        name="recall", category="memory",
        description="内置记忆召回连接器: 通过记忆图谱按目标召回历史经验",
        call_fn=_default_recall_connector)
    hub.register_connector(
        name="recent_runs", category="superagent",
        description="内置最近编排概览连接器: 返回最近 N 次超级 AGENT 编排记录",
        call_fn=_default_recent_runs_connector)
    hub.register_expert(
        name="codereview", domain="code",
        system_prompt="你是资深代码评审专家。给定代码与目标, 严格评审可读性/正确性/性能/安全/可维护性, "
                      "按严重度输出问题列表与改进建议。",
        description="代码评审专家画像: 供 code 域并行编排时附加评审视角",
        tags=["code-review", "quality"])
    hub.register_expert(
        name="design_lead", domain="creation",
        system_prompt="你是产品设计与创意总监。给定创作目标, 输出视觉方向/信息层级/文案风格/配色/构图建议。",
        description="创意设计专家画像: 供 creation 域编排时注入视觉建议",
        tags=["design", "creative"])
    hub.register_expert(
        name="ops_sre", domain="ops",
        system_prompt="你是 SRE 运维专家。给定部署/运维目标, 输出灰度策略/回滚预案/监控告警/容量规划建议。",
        description="运维专家画像: 供 ops 域编排时注入运维视角",
        tags=["sre", "ops", "deploy"])
    _emit("plugin", "bootstrap", "插件中枢默认 connector/expert 已注入",
          {"connectors": list(hub.connectors.keys()),
           "experts": list(hub.experts.keys())})


_bootstrap()
