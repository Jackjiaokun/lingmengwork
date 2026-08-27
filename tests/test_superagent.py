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
        # ops 无注册执行器 → 走默认执行器产出交付文件
        ops_ex = next((e for e in ex["executions"] if e.get("domain") == "ops"), None)
        assert ops_ex["status"] == "artifact", "ops 应走默认执行器落地"
    finally:
        sa_mod.EXECUTORS.pop("code", None)  # 清理, 避免污染其他测试


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
    assert recs["code"]["status"] == "artifact", "成功伙伴应落地执行"
    assert recs["creation"]["status"] == "skipped", "异常伙伴应跳过"
    assert any(os.path.isfile(a) for a in ex["artifacts"]), "成功伙伴应产出文件"
