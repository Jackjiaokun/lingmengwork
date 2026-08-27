"""灵梦work · 离线自检中枢 (Phase 14)。

无 LLM 依赖、确定性、可重复的离线健康探针:
覆盖 四引擎(编排/创作/自主/流水线) + 多模态适配层 + 跨会话记忆 + 关键静态资产,
输出结构化健康报告(含健康分与逐检查项明细)。

用法:
    python -m lingmengwork.selfcheck            # CLI: 打印报告并 exit 0/1
    from lingmengwork import selfcheck
    rep = selfcheck.run()                       # -> {ok, score, passed, total, all_ok, checks, ts}
"""
import os
import sys
import json
import tempfile

# 关键静态资产(相对包根 lingmengwork/)
_STATIC_FILES = [
    "web/static/index.html",
    "web/static/app.js",
    "web/static/styles.css",
    "web/static/control_center.html",
    "web/static/pipeline.html",
    "web/static/multimodal.html",
    "web/static/automation.html",
    "web/static/activity.html",
    "web/static/audit.html",
    "web/static/heal.html",
    "web/static/federation.html",
    "web/static/memory_graph.html",
    "web/static/superagent.html",
]


def _chk(name, fn):
    """运行单个检查, 健壮性包裹: 返回 {name, ok, detail}。"""
    try:
        detail = fn()
        if detail is False:
            return {"name": name, "ok": False, "detail": "检查返回 False"}
        return {"name": name, "ok": True,
                "detail": detail if isinstance(detail, str) else "ok"}
    except Exception as e:
        return {"name": name, "ok": False,
                "detail": "%s: %s" % (type(e).__name__, e)}


def check_imports():
    from . import (decompose_engine, creation_domains, autonomous,
                   goal_pipeline, multimodal_adapters, memory_mgr,
                   automation_hub, event_bus, self_heal, federation,
                   memory_graph, superagent)
    from .web import server  # noqa: F401
    return "9 个核心模块导入成功"


def check_decompose():
    from . import decompose_engine as de
    steps = de.decompose("搭建一个带鉴权的登录页", llm_call=None)
    assert isinstance(steps, list) and len(steps) >= 1, "规则兜底应至少 1 步"
    return "任务拆解返回 %d 步(规则兜底)" % len(steps)


def check_creation():
    from . import creation_domains as cd
    res = cd.dispatch("code", "写一个 hello 函数", llm_call=None)
    assert res.get("ok") and res.get("plan"), "创作蓝图应非空"
    return "创作域 %s 蓝图 %d 字" % (res.get("domain_name"), len(res.get("plan", "")))


def check_autonomous():
    from . import autonomous as au
    res = au.run("写 hello 函数", llm_call=None, max_iter=2)
    its = res.get("iterations") or []
    assert its, "自主回路应记录轨迹"
    # 无 LLM 时结论可能为空格串(引擎仍正常闭环), 仅需结构有效
    assert isinstance(res.get("conclusion"), str), "应包含结论字段"
    return "自主 %d 轮轨迹 + 结论" % len(its)


def check_pipeline():
    from . import goal_pipeline as gp
    with tempfile.TemporaryDirectory() as md:
        res = gp.run_pipeline("做一个命令行待办工具", llm_call=None, do_render=False,
                              do_autonomous=False, max_dispatch=2, do_learn=False,
                              memory_dir=md)
    assert res.get("stages"), "应有阶段"
    assert res.get("execute") is not None, "应有执行项"
    assert res.get("delivery"), "应有交付稿"
    return "流水线 %d 阶段 + %d 执行项" % (len(res.get("stages", [])), len(res.get("execute", [])))


def check_multimodal():
    from . import multimodal_adapters as ma
    old = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        try:
            art = ma.render("image", "每日 AI 速览",
                            "# 要点\n1. 模型迭代加速\n2. 多模态落地", "", llm_call=None)
        finally:
            os.chdir(old)
    assert art and art.get("file"), "应生成媒体文件"
    return "image 域真实产出 %s (real=%s)" % (os.path.basename(art.get("file")), art.get("real"))


def check_memory():
    from . import memory_mgr as mm
    with tempfile.TemporaryDirectory() as td:
        # 含强信号关键词『约定』, 触发规则兜底抽取(无 LLM 亦可用)
        text = "约定：灵梦work 的默认 LLM 后端是 SenseNova"
        cap = mm.capture(td, text, llm_call=None)
        assert cap.get("captured"), "应抽取事实(规则兜底)"
        hits = mm.retrieve(td, "默认 LLM 后端是什么", k=3)
        assert hits, "应召回事实"
    return "记忆捕获 %d 条 + 召回 %d 条" % (len(cap.get("captured", [])), len(hits))


def check_event_bus():
    from . import event_bus as eb
    ev = eb.emit("selfcheck", "audit_probe", "自检审计探针", audit=True)
    assert ev and ev.get("id"), "应发射审计事件并返回 id"
    assert ev.get("audit") is True, "审计标记应为 True"
    trail = eb.audit_trail(limit=10)
    assert isinstance(trail, list) and any(e.get("audit") for e in trail), "审计链应可追溯"
    return "活动总线发射+审计链回溯 %d 条" % len(trail)


def check_self_heal():
    from . import self_heal as sh
    # 无信号: 健康分 100 + 0 提议
    rep = sh.propose(selfcheck_report={"checks": []}, bus=None)
    assert rep["health_score"] == 100, "无信号时健康分应为 100"
    assert rep["proposal_count"] == 0, "无信号时不应有提议"
    # 注入失败信号: 应生成提议
    bad = {"checks": [{"name": "关键静态资产", "ok": False,
                       "detail": "缺失: web/static/missing.html"}]}
    rep2 = sh.propose(selfcheck_report=bad, bus=None)
    assert rep2["proposal_count"] >= 1, "失败信号应生成提议"
    assert any(p["area"] == "web/static" for p in rep2["proposals"]), "应定位到 web/static 区域"
    return "自愈提议器 无信号%d分 + 失败信号%d提议" % (rep["health_score"], rep2["proposal_count"])


def check_static_files():
    here = os.path.dirname(os.path.abspath(__file__))  # selfcheck.py 即位于 lingmengwork 包目录
    pkg = here
    missing = [f for f in _STATIC_FILES if not os.path.isfile(os.path.join(pkg, f))]
    assert not missing, "缺失: " + ", ".join(missing)
    return "%d 个关键静态资产齐备" % len(_STATIC_FILES)


def check_federation():
    from . import federation as fed
    f = fed.get_federation()
    partners = f.list_partners()
    assert len(partners) == 4, "应注册 4 个伙伴, 实际 %d" % len(partners)
    # 路由: 纯编码目标 → 仅 code
    assert f.route("写一个登录函数") == ["code"], "编码目标应仅路由 code"
    # 跨域目标 → code + creation + ops 等多伙伴
    routed = f.route("开发一款命令行待办应用，制作一张产品配图，撰写发布文案并准备上线部署")
    assert "code" in routed and "creation" in routed and "ops" in routed, "应跨域路由"
    # 派发闭环(无 LLM 规则兜底), 伙伴应全部成功
    rep = f.dispatch("开发一款命令行待办应用，制作一张产品配图，撰写发布文案并准备上线部署", llm_call=None)
    assert rep["ok"] and rep["partners"], "派发应返回伙伴结果"
    assert all(p["status"] == "ok" for p in rep["partners"]), "伙伴应全部成功"
    assert rep["merged"]["parts"], "汇聚应有结构"
    return "联邦 %d 伙伴 + 跨域路由 + 派发闭环(%d 伙伴)" % (len(partners), len(rep["partners"]))


def check_memory_graph():
    import tempfile
    from . import memory_graph as mg
    with tempfile.TemporaryDirectory() as td:
        db = ":memory:"  # 内存库, 避免临时文件锁(沙箱 safe-delete 干扰清理)
        g = mg.MemoryGraph(db)
        # 沉淀: 含「决策」「约定」「bug」「api」强信号 → 多类实体
        r1 = g.absorb("决定采用 SenseNova 作为默认 LLM 后端；约定灵梦work 默认不重打包 exe；"
                      "某接口需要 api_key=sk-123456 才能调用；曾因并发死锁导致崩溃(bug)",
                      "已落地决策与约定")
        assert r1["ok"] and r1["entities_added"] >= 3, "应抽取多类实体"
        types = {e["type"] for e in r1["entities"]}
        assert "decision" in types and "convention" in types and "bug" in types, "应覆盖决策/约定/故障"
        # 隐私: 含 api_key=sk-123456 的事实不应保留明文值
        facts = g.list_entities(limit=500)
        blob = json.dumps(facts, ensure_ascii=False)
        assert "sk-123456" not in blob, "密钥明文不得入图"
        # 召回: 新目标应召回相关实体(决策类)
        rc = g.recall("灵梦work 的默认 LLM 后端是什么", limit=10)
        assert rc["ok"] and rc["count"] >= 1, "应召回相关记忆"
        assert rc["recap"], "应产出 recap"
        g.close()
    return "记忆图谱 抽取%d实体+关系%d + 隐私脱敏 + 召回%d项" % (
        r1["entities_added"], r1["relations_added"], rc["count"])


def check_superagent():
    """Phase 27: 超级 AGENT 内核闭环(目标理解→域路由→并行编排→收敛→自检→记忆沉淀)。

    验收门槛: 单目标跨 2+ 域编排成功(多伙伴并行派发 + 汇聚)。
    quality_gate=False: 避免 superagent 质量门递归调用 selfcheck.run(本探针)。
    base_dir=":memory:" : 纯内存库隔离, 规避临时目录 SQLite 文件锁(沙箱 safe-delete 干扰)。
    """
    from . import superagent as sa
    goal = "做段产品介绍视频并写发布文案、准备上线部署到服务器"
    rep = sa.SuperAgent(base_dir=":memory:").run(goal, quality_gate=False)
    assert rep["ok"], "编排应成功"
    routed = rep.get("routed") or []
    assert len(routed) >= 2, "单目标应跨 2+ 域路由, 实际: %s" % routed
    partners = (rep.get("dispatch") or {}).get("partners") or []
    ok_n = sum(1 for p in partners if p.get("status") == "ok")
    assert ok_n >= 2, "至少 2 个伙伴应派发成功, 实际 %d/%d" % (ok_n, len(partners))
    cv = rep.get("converge") or {}
    assert cv.get("partners_ok", 0) >= 2, "收敛应确认伙伴成功 >=2"
    # 记忆沉淀(异常隔离, 空图也会计数)
    assert isinstance(rep.get("memory"), dict), "应产出记忆沉淀结果"
    # 执行落地: 默认执行器把方案写成真实交付文件(无 LLM 亦产出)
    ex = rep.get("executions") or {}
    assert ex.get("count", 0) >= 2, "至少 2 个域应落地执行, 实际 %s" % ex
    arts = ex.get("artifacts") or []
    assert len(arts) >= 2, "应产出 >=2 个交付文件, 实际 %s" % arts
    for a in arts:
        assert os.path.isfile(a), "交付文件应真实存在: %s" % a
    # 结构化 trace 进审计链(6 阶段)
    assert len(rep.get("trace") or []) >= 5, "应产出分阶段 trace"
    return "超级AGENT 跨域编排(路由 %s · %d 伙伴成功 · 记忆沉淀)" % ("/".join(routed), ok_n)


def run():
    """执行全部健康检查, 返回结构化报告 dict。"""
    checks = [
        _chk("核心模块导入", check_imports),
        _chk("任务拆解(规则兜底)", check_decompose),
        _chk("创作域(规则兜底)", check_creation),
        _chk("自主模式(无 LLM)", check_autonomous),
        _chk("全链路流水线(无 LLM)", check_pipeline),
        _chk("多模态适配层(模板回退)", check_multimodal),
        _chk("跨会话记忆(捕获+召回)", check_memory),
        _chk("活动总线(事件+审计链)", check_event_bus),
        _chk("自主进化(自愈提议)", check_self_heal),
        _chk("工作伙伴联邦(路由+派发)", check_federation),
        _chk("长期记忆图谱(抽取+召回)", check_memory_graph),
        _chk("超级AGENT内核(跨域编排闭环)", check_superagent),
        _chk("关键静态资产", check_static_files),
    ]
    total = len(checks)
    passed = sum(1 for c in checks if c["ok"])
    score = int(round(passed / total * 100)) if total else 0
    return {
        "ok": True,
        "score": score,
        "passed": passed,
        "total": total,
        "all_ok": passed == total,
        "checks": checks,
        "ts": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def main():
    rep = run()
    line = "=" * 56
    print(line)
    print("灵梦work · 离线自检中枢 (Self-Check Hub)")
    print(line)
    for c in rep["checks"]:
        mark = "✅" if c["ok"] else "❌"
        print("%s %-26s %s" % (mark, c["name"], c["detail"]))
    print("-" * 56)
    print("健康分: %d/%d  (%d%%)" % (rep["passed"], rep["total"], rep["score"]))
    print(line)
    sys.exit(0 if rep["all_ok"] else 1)


if __name__ == "__main__":
    main()
