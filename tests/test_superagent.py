"""Phase 27 · 超级 AGENT 内核 (superagent.py) 测试。

覆盖: 目标理解(规则兜底 + LLM 抽取) / 域路由 / 并行编排 / 三级护栏收敛 / 跨域闭环 /
质量门递归防护 / 服务端 API + 页面 / 自检探针计数。
"""

import json
import os
import tempfile
import threading
import time
import http.client

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


# ------------------------------------------------------------------ 目标理解
def test_understand_rule_fallback(tmp_path):
    """无 LLM: 跨域目标经联邦关键词路由到 >=2 伙伴。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    u = sa.understand("做段产品介绍视频并写发布文案、准备上线部署到服务器")
    assert len(u["domains"]) >= 2, "应跨域路由, 实际 %s" % u["domains"]
    assert "creation" in u["domains"], "视频/文案应命中创作域"
    assert "ops" in u["domains"], "上线/部署应命中运维域"


def test_understand_llm_extract(tmp_path):
    """有 LLM: 抽取 intent/域/约束 覆盖规则兜底。"""

    def llm(prompt, system=None):
        return '{"intent":"做宣传短视频","domains":["creation","code"],"constraints":["中文配音"]}'

    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    u = sa.understand("随便说点什么", llm_call=llm)
    assert u["domains"] == ["creation", "code"]
    assert u["intent"] == "做宣传短视频"
    assert u["constraints"] == ["中文配音"]


# ------------------------------------------------------------------ 收敛(三级护栏)
def test_converge_guards(tmp_path):
    """一级(伙伴异常) + 二级(冲突) 护栏触发; passed=False。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    fake = {
        "partners": [
            {"partner_id": "code", "name": "编码伙伴", "domain": "code", "status": "ok",
             "summary": "s", "artifacts": []},
            {"partner_id": "creation", "name": "创作伙伴", "domain": "creation", "status": "error",
             "error": "boom", "artifacts": []},
        ],
        "merged": {"summary": "..", "conflicts": [
            {"type": "blueprint", "partners": ["code", "creation"], "note": "多伙伴同类产物冲突"}]},
    }
    cv = sa.converge(fake, quality_gate=False)
    assert cv["partners_ok"] == 1 and cv["partners_error"] == 1
    assert len(cv["guards"]) >= 2          # 一级 error + 二级 conflict
    assert cv["passed"] is False
    assert cv["ok"] is True                # 至少 1 伙伴成功 → 编排整体成功


def test_converge_quality_low(tmp_path, monkeypatch):
    """三级护栏(质量门): 自检低分触发 level=3 告警。"""
    import lingmengwork.selfcheck as sc_mod
    monkeypatch.setattr(sc_mod, "run", lambda: {"score": 50})
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    fake = {"partners": [{"partner_id": "code", "name": "编码", "domain": "code",
                          "status": "ok", "summary": "s", "artifacts": []}],
            "merged": {"summary": "", "conflicts": []}}
    cv = sa.converge(fake, quality_gate=True)
    lv3 = [g for g in cv["guards"] if g["level"] == 3]
    assert lv3, "低自检分应触发三级护栏"
    assert "50" in lv3[0]["msg"]


def test_converge_clean(tmp_path):
    """全绿(无异常/无冲突/质量门满分): passed=True。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    fake = {"partners": [{"partner_id": "code", "name": "编码", "domain": "code",
                          "status": "ok", "summary": "s", "artifacts": []},
                         {"partner_id": "creation", "name": "创作", "domain": "creation",
                          "status": "ok", "summary": "s", "artifacts": []}],
            "merged": {"summary": "ok", "conflicts": []}}
    cv = sa.converge(fake, quality_gate=False)
    assert cv["passed"] is True and cv["guards"] == []
    assert cv["partners_ok"] == 2


# ------------------------------------------------------------------ 统一入口闭环
def test_run_cross_domain(tmp_path):
    """验收门槛: 单目标跨 2+ 域编排成功。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("做段产品介绍视频并写发布文案、准备上线部署到服务器")
    assert rep["ok"], rep.get("error")
    assert len(rep["routed"]) >= 2, "应跨 2+ 域"
    partners = rep["dispatch"]["partners"]
    ok_n = sum(1 for p in partners if p["status"] == "ok")
    assert ok_n >= 2, "至少 2 伙伴成功, 实际 %d/%d" % (ok_n, len(partners))
    assert rep["converge"]["partners_ok"] >= 2
    assert isinstance(rep["memory"], dict), "应产出记忆沉淀"
    assert len(rep["trace"]) >= 5, "应产出分阶段 trace(>=5 阶段)"


def test_run_quality_gate_no_recursion(tmp_path):
    """质量门递归防护: run(quality_gate=True) 不应无限递归 selfcheck。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("写个登录函数并做单元测试", quality_gate=True)
    assert rep["ok"]
    # 仍应正常记录 trace(含自检质量门阶段)
    assert len(rep["trace"]) >= 5


# ------------------------------------------------------------------ 服务端
def test_server_api():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8987
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", PORT), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        def post(path, body):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=40)
            c.request("POST", path, body=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        # 最近编排概览
        st, js = get("/api/superagent")
        assert st == 200, (st, js)
        assert json.loads(js)["ok"]

        # 跨域编排闭环
        st2, js2 = post("/api/superagent/run",
                        {"goal": "做段产品视频并写发布文案、准备上线部署到服务器"})
        assert st2 == 200, (st2, js2)
        d2 = json.loads(js2)
        assert d2["ok"], d2.get("error")
        assert len(d2["routed"]) >= 2
        assert all(p["status"] == "ok" for p in d2["dispatch"]["partners"])
        assert d2["converge"]["partners_ok"] >= 2
        # Phase 29: 默认真实执行器产物经 HTTP 落盘校验
        arts29 = (d2.get("executions") or {}).get("artifacts") or []
        assert len(arts29) >= 1, "应产出真实执行产物(经 API)"
        for a in arts29:
            assert os.path.isfile(a), "真实产物应落盘: " + a

        # 缺 goal → 400
        st3, js3 = post("/api/superagent/run", {})
        assert st3 == 400, (st3, js3)

        # 页面含编排容器 + 「超级 AGENT」字样(可测 id)
        st4, html = get("/superagent")
        assert st4 == 200
        assert "超级 AGENT" in html
        assert 'id="traceBox"' in html
        assert 'id="partnerResult"' in html
    finally:
        srv.shutdown()
        os.chdir(old)


# ------------------------------------------------------------------ 真实执行器(Phase29)
def test_real_executors_registered():
    """Phase 29: code/creation/research/ops 默认热插拔真实执行器。"""
    for d in ("code", "creation", "research", "ops"):
        assert sa_mod.get_executor(d) is not None, "应注册真实执行器: %s" % d


def test_exec_code_compiles(tmp_path):
    """code 执行器: 伙伴 plan 含 python 代码块 → 落盘 .py 且 compile 通过。"""
    partner = {"partner_id": "code", "name": "编码伙伴", "domain": "code", "status": "ok",
               "summary": "s", "plan": "实现如下:\n```python\ndef add(a,b):\n    return a+b\n```",
               "artifacts": []}
    res = sa_mod._exec_code(partner, goal="写加法函数", base_dir=str(tmp_path))
    assert res["status"] == "ok"
    assert res["artifacts"], "应产出代码文件"
    py = [a for a in res["artifacts"] if a.endswith(".py")][0]
    assert os.path.isfile(py)
    compile(open(py, encoding="utf-8").read(), py, "exec")  # 二次校验可编译


def test_exec_code_skeleton_when_no_block(tmp_path):
    """code 执行器: 无代码块(无 LLM) → 产出可编译骨架。"""
    partner = {"partner_id": "code", "name": "编码伙伴", "domain": "code", "status": "ok",
               "summary": "s", "plan": "## 方案\n做点东西", "artifacts": []}
    res = sa_mod._exec_code(partner, goal="写个服务", base_dir=str(tmp_path))
    assert res["status"] == "ok"
    sk = [a for a in res["artifacts"] if a.endswith("_skeleton.py")][0]
    assert os.path.isfile(sk)
    compile(open(sk, encoding="utf-8").read(), sk, "exec")


def test_exec_ops_script(tmp_path):
    """ops 执行器: 落盘 deploy.sh 真实文件。"""
    partner = {"partner_id": "ops", "name": "运维伙伴", "domain": "ops", "status": "ok",
               "summary": "s", "plan": "1) 构建镜像\n2) 推送到仓库\n3) 灰度发布", "artifacts": []}
    res = sa_mod._exec_ops(partner, goal="上线部署", base_dir=str(tmp_path))
    assert res["status"] == "ok"
    assert any(a.endswith(".sh") for a in res["artifacts"]), res
    assert os.path.isfile([a for a in res["artifacts"] if a.endswith(".sh")][0])


def test_exec_creation_manifest(tmp_path):
    """creation 执行器: 落盘可回读 asset_manifest.json。"""
    partner = {"partner_id": "creation", "name": "创作伙伴", "domain": "creation", "status": "ok",
               "summary": "s", "plan": "p", "artifacts": [{"type": "blueprint", "domain": "image"}]}
    res = sa_mod._exec_creation(partner, goal="画一张海报", base_dir=str(tmp_path))
    assert res["status"] == "ok"
    assert any(a.endswith(".json") for a in res["artifacts"])
    m = json.load(open([a for a in res["artifacts"] if a.endswith(".json")][0], encoding="utf-8"))
    assert m["sub_domain"] == "image"


def test_exec_research_fallback(tmp_path, monkeypatch):
    """research 执行器: 未开启抓取 → 落地研究简报(.md), 不挂起。"""
    monkeypatch.delenv("LMW_SA_ALLOW_FETCH", raising=False)
    partner = {"partner_id": "research", "name": "研究伙伴", "domain": "research", "status": "ok",
               "summary": "s", "plan": "## 研究目标\n量子计算", "artifacts": []}
    res = sa_mod._exec_research(partner, goal="调研量子计算", base_dir=str(tmp_path))
    assert res["status"] == "ok"
    assert any(a.endswith(".md") for a in res["artifacts"]), res


# ------------------------------------------------------------------ 自主编码(Phase30)
def test_exec_code_smoke_run_log(tmp_path):
    """Phase 30: code 执行器对代码块冒烟运行并落 .run.log。"""
    partner = {"partner_id": "code", "name": "编码伙伴", "domain": "code", "status": "ok",
               "summary": "s",
               "plan": "实现:\n```python\nprint('hello from generated code')\n```",
               "artifacts": []}
    res = sa_mod._exec_code(partner, goal="输出一句话", base_dir=str(tmp_path))
    assert res["status"] == "ok"
    assert res.get("run_ok") is True, res
    logs = [a for a in res["artifacts"] if a.endswith(".run.log")]
    assert logs, "应产出冒烟运行日志"
    assert "returncode=0" in open(logs[0], encoding="utf-8").read()


def test_exec_code_skeleton_smoke_ok(tmp_path):
    """Phase 30: 无 LLM 骨架应为可运行骨架(冒烟通过, 不再 NotImplementedError)。"""
    partner = {"partner_id": "code", "name": "编码伙伴", "domain": "code", "status": "ok",
               "summary": "s", "plan": "## 方案\n做点东西", "artifacts": []}
    res = sa_mod._exec_code(partner, goal="写个服务", base_dir=str(tmp_path))
    assert res["status"] == "ok"
    assert res.get("run_ok") is True, res
    sk = [a for a in res["artifacts"] if a.endswith("_skeleton.py")][0]
    src = open(sk, encoding="utf-8").read()
    assert "NotImplementedError" not in src


def test_exec_code_self_heal(tmp_path):
    """Phase 30: 首产代码冒烟失败 + LLM 在场 → 有限自修复后通过。"""
    calls = {"n": 0}

    def llm(prompt, system=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return "```python\nraise ValueError('boom')\n```"  # 首次产出带运行期错误
        return "```python\nprint('fixed output')\n```"        # 自修复产出可运行代码

    partner = {"partner_id": "code", "name": "编码伙伴", "domain": "code", "status": "ok",
               "summary": "s", "plan": "p", "artifacts": []}
    res = sa_mod._exec_code(partner, goal="修复我", llm_call=llm, base_dir=str(tmp_path))
    assert res.get("run_ok") is True and res.get("healed") is True, res
    assert calls["n"] >= 2, "应调用 LLM 做自修复"


def test_exec_code_smoke_fail_no_llm(tmp_path):
    """Phase 30: 运行期错误且无 LLM → run_ok=False 但不崩, run.log 记录报错。"""
    partner = {"partner_id": "code", "name": "编码伙伴", "domain": "code", "status": "ok",
               "summary": "s",
               "plan": "实现:\n```python\nraise ValueError('boom')\n```",
               "artifacts": []}
    res = sa_mod._exec_code(partner, goal="坏代码", base_dir=str(tmp_path))
    assert res["status"] == "ok", "产物仍应落盘"
    assert res.get("run_ok") is False, "冒烟应如实报告失败"
    log = [a for a in res["artifacts"] if a.endswith(".run.log")][0]
    assert "ValueError" in open(log, encoding="utf-8").read()


# ------------------------------------------------------------------ 真实创作(Phase31)
def test_exec_creation_real_media(tmp_path):
    """Phase 31: creation 执行器经 multimodal_adapters 真实渲染媒体 + 落盘可回读 manifest。"""
    partner = {"partner_id": "creation", "name": "创作伙伴", "domain": "creation", "status": "ok",
               "summary": "s", "plan": "p", "artifacts": [{"type": "blueprint", "domain": "image"}]}
    res = sa_mod._exec_creation_real(partner, goal="画一张海报", base_dir=str(tmp_path))
    assert res["status"] == "ok", res
    m = json.load(open([a for a in res["artifacts"] if a.endswith(".json")][0], encoding="utf-8"))
    assert m["sub_domain"] == "image"
    assert "render" in m, "manifest 应含真实渲染结果"
    media = [a for a in res["artifacts"] if not a.endswith(".json")]
    assert media and os.path.isfile(media[0]), "应产出真实媒体文件"
    assert any(a.endswith((".png", ".gif", ".mp3")) for a in media), media


def test_exec_creation_fallback_manifest(tmp_path, monkeypatch):
    """Phase 31: 适配层不可用 → 回退纯清单(不崩, status ok)。"""
    import lingmengwork.multimodal_adapters as mma

    def boom(*a, **k):
        raise RuntimeError("adapters down")

    monkeypatch.setattr(mma, "render", boom)
    partner = {"partner_id": "creation", "name": "创作伙伴", "domain": "creation", "status": "ok",
               "summary": "s", "plan": "p", "artifacts": [{"type": "blueprint", "domain": "audio"}]}
    res = sa_mod._exec_creation_real(partner, goal="配段音频", base_dir=str(tmp_path))
    assert res["status"] == "ok"
    m = json.load(open([a for a in res["artifacts"] if a.endswith(".json")][0], encoding="utf-8"))
    assert m["sub_domain"] == "audio"


def test_exec_creation_subdomain_route(tmp_path):
    """Phase 31: 无蓝图标注时按目标关键词路由子域(视频→video)。"""
    p = {"partner_id": "c", "name": "创作", "domain": "creation", "status": "ok",
         "summary": "s", "plan": "p", "artifacts": []}
    res = sa_mod._exec_creation_real(p, goal="做段产品视频", base_dir=str(tmp_path))
    m = json.load(open([a for a in res["artifacts"] if a.endswith(".json")][0], encoding="utf-8"))
    assert m["sub_domain"] == "video"


# ------------------------------------------------------------------ 自检集成
def test_selfcheck_probe_count():
    """selfcheck 探针数应为 13 (Phase25 联邦 + Phase26 记忆图谱 + Phase27 超级AGENT)。"""
    from lingmengwork import selfcheck as sc
    rep = sc.run()
    assert rep["total"] == 13, "探针数应为 13, 实际 %d" % rep["total"]
    failed = {c["name"]: c["detail"] for c in rep["checks"] if not c["ok"]}
    assert not failed, failed


# ------------------------------------------------------------------ 执行落地(Phase28)
def test_execute_default_artifact(tmp_path):
    """无真实执行器: 内核把每个成功伙伴的方案写成真实交付文件(.md)。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("做段产品介绍视频并写发布文案、准备上线部署到服务器", quality_gate=False)
    assert rep["ok"], rep.get("error")
    ex = rep["executions"]
    assert ex["count"] >= 2, "应跨 2+ 域落地执行, 实际 %s" % ex
    arts = ex["artifacts"]
    assert len(arts) >= 2, "应产出 >=2 个交付文件, 实际 %s" % arts
    for a in arts:
        assert os.path.isfile(a), "交付文件应真实存在: %s" % a


def test_execute_real_executor_injection(tmp_path):
    """注册的 domain 执行器应被调用, 其产物纳入 executions。"""
    calls = []
    def fake_code_executor(partner, goal="", llm_call=None, base_dir=None):
        calls.append(partner.get("domain"))
        return {"domain": "code", "status": "ok",
                "artifacts": [os.path.join(str(tmp_path), "gen_code.txt")],
                "note": "已调用真实 code 执行器"}
    prev = sa_mod.EXECUTORS.get("code")  # 可能是默认真实执行器
    sa_mod.register_executor("code", fake_code_executor)
    try:
        sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
        fake_dispatch = {
            "partners": [
                {"partner_id": "code", "name": "编码伙伴", "domain": "code", "status": "ok",
                 "summary": "s", "plan": "p", "artifacts": []},
                {"partner_id": "ops", "name": "运维伙伴", "domain": "ops", "status": "ok",
                 "summary": "s2", "plan": "", "artifacts": []},
            ],
            "merged": {"summary": "..", "conflicts": []},
        }
        ex = sa.execute(fake_dispatch, goal="写一个函数并上线部署")
        code_ex = next((e for e in ex["executions"] if e.get("domain") == "code"), None)
        assert code_ex is not None, "应存在 code 域执行记录"
        assert code_ex["status"] == "ok" and any("gen_code.txt" in a for a in code_ex["artifacts"]), code_ex
        assert "code" in calls, "真实 code 执行器应被调用"
        # ops 走默认真实执行器(已注册) → 落盘真实脚本
        ops_ex = next((e for e in ex["executions"] if e.get("domain") == "ops"), None)
        assert ops_ex["status"] in ("ok", "artifact"), "ops 应走默认真实执行器落地"
    finally:
        if prev is not None:
            sa_mod.EXECUTORS["code"] = prev  # 还原默认真实执行器, 避免污染其他测试
        else:
            sa_mod.EXECUTORS.pop("code", None)


def test_execute_partner_error_isolated(tmp_path):
    """伙伴异常被跳过(不执行), 成功伙伴仍正常落地, 整体不崩。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    fake_dispatch = {
        "partners": [
            {"partner_id": "code", "name": "编码伙伴", "domain": "code", "status": "ok",
             "summary": "s1", "plan": "p1", "artifacts": []},
            {"partner_id": "creation", "name": "创作伙伴", "domain": "creation", "status": "error",
             "error": "boom", "artifacts": []},
        ],
        "merged": {"summary": "..", "conflicts": []},
    }
    ex = sa.execute(fake_dispatch)
    recs = {e["domain"]: e for e in ex["executions"]}
    assert recs["code"]["status"] in ("ok", "artifact"), "成功伙伴应落地执行"
    assert recs["creation"]["status"] == "skipped", "异常伙伴应跳过"
    assert any(os.path.isfile(a) for a in ex["artifacts"]), "成功伙伴应产出文件"
