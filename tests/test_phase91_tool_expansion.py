"""Phase 91: 扩展工具集一致性与冒烟测试。

覆盖:
- 25 个新工具全部进入 TOOL_SCHEMAS / _IMPLS
- 工具名全局唯一(无重复覆盖)
- 四创作域 DOMAIN_TOOLS 全部指向真实存在的工具
- 各新工具冒烟: 不崩溃, 文件产出类工具真实落盘且产物可解析
"""
import os
import json
import zipfile
import xml.etree.ElementTree as ET

from lingmengwork.tools import registry as reg
from lingmengwork import creation_domains as cd


NEW_TOOLS = [
    # 联网
    "web_fetch", "web_search", "http_request",
    # git
    "git_status", "git_diff", "git_log", "git_branch", "git_checkout",
    "git_stash", "git_pr_draft",
    # 多模态
    "image_generate", "image_understand", "tts", "transcribe", "video_generate",
    # 文档
    "make_ppt", "make_xlsx", "make_pdf", "ocr",
    # 自动化
    "schedule_task", "webhook_send", "notify",
    # 代码智能
    "test_gen", "explain_code", "security_scan",
]


def test_new_tools_in_schemas():
    names = {t["name"] for t in reg.TOOL_SCHEMAS}
    for n in NEW_TOOLS:
        assert n in names, f"工具 {n} 未进入 TOOL_SCHEMAS"
    # 全局唯一
    all_names = [t["name"] for t in reg.TOOL_SCHEMAS]
    assert len(all_names) == len(set(all_names)), "TOOL_SCHEMAS 存在重名工具"


def test_new_tools_in_impls():
    for n in NEW_TOOLS:
        assert n in reg._IMPLS, f"工具 {n} 未在 _IMPLS 注册"
        assert callable(reg._IMPLS[n]), f"工具 {n} 的实现不可调用"


def test_domain_tools_all_real():
    schema_names = {t["name"] for t in reg.TOOL_SCHEMAS}
    for dom, tools in cd.DOMAIN_TOOLS.items():
        for t in tools:
            assert t in schema_names, f"域 {dom} 引用了不存在的工具: {t}"


def _reg(tmp):
    return reg.Registry(roots=[str(tmp)], permission_mode="bypassPermissions", cfg={})


def test_web_search_no_crash(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("web_search", {"query": "lingmengwork"})
    assert isinstance(out, str) and out.startswith("[web_search]"), out[:200]


def test_git_status_clean_outside_repo(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("git_status", {})
    assert isinstance(out, str) and out.startswith("[git_status]"), out[:200]


def test_make_ppt_produces_valid_file(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("make_ppt", {"path": "demo.pptx", "title": "T",
                                 "slides": [{"title": "一", "bullets": ["a", "b"]},
                                            {"title": "二", "bullets": ["c"]}]})
    assert "[make_ppt]" in out, out[:200]
    fp = tmp_path / "demo.pptx"
    assert fp.exists()
    assert zipfile.is_zipfile(str(fp))
    # 关键部件 XML 可解析
    with zipfile.ZipFile(str(fp)) as z:
        ET.fromstring(z.read("ppt/presentation.xml"))
        ET.fromstring(z.read("ppt/slides/slide1.xml"))


def test_make_xlsx_produces_valid_file(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("make_xlsx", {"path": "d.xlsx", "sheet": "S1",
                                  "data": [["名称", "值"], ["x", 1], ["y", 2.5]]})
    assert "[make_xlsx]" in out, out[:200]
    fp = tmp_path / "d.xlsx"
    assert fp.exists() and zipfile.is_zipfile(str(fp))
    with zipfile.ZipFile(str(fp)) as z:
        ET.fromstring(z.read("xl/workbook.xml"))
        ET.fromstring(z.read("xl/worksheets/sheet1.xml"))


def test_make_pdf_produces_file(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("make_pdf", {"path": "d.pdf", "title": "H", "body": "# 标题\n正文一行\n- 要点"})
    assert "[make_pdf]" in out, out[:200]
    fp = tmp_path / "d.pdf"
    assert fp.exists()
    head = fp.read_text(encoding="latin-1")
    assert head.startswith("%PDF-1.4") and "%%EOF" in head


def test_explain_code_inline():
    r = reg.Registry(roots=["."], permission_mode="bypassPermissions", cfg={})
    code = "def add(a, b):\n    return a + b\n\nclass Calc:\n    pass\n"
    out = r.execute("explain_code", {"code": code})
    assert "[explain_code]" in out and "add" in out and "Calc" in out, out[:300]


def test_test_gen_inline():
    r = reg.Registry(roots=["."], permission_mode="bypassPermissions", cfg={})
    code = "def mul(a, b):\n    return a * b\n"
    out = r.execute("test_gen", {"code": code})
    assert "[test_gen]" in out and "test_mul" in out, out[:400]


def test_security_scan_finds_risk(tmp_path):
    risky = tmp_path / "bad.py"
    risky.write_text("import os\nkey = 'sk-1234567890abcdef'\nx = eval('1+1')\n", encoding="utf-8")
    r = _reg(tmp_path)
    out = r.execute("security_scan", {"path": "."})
    assert "[security_scan]" in out
    assert "eval" in out or "硬编码" in out, out[:400]


def test_image_generate_smoke(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("image_generate", {"prompt": "蓝色渐变信息图"})
    assert isinstance(out, str)
    if "[media]" in out:
        # 真实产出: 提取路径校验存在
        m = __import__("re").search(r"已生成: (.+)", out)
        assert m and os.path.exists(m.group(1)), out[:300]


def test_schedule_and_notify_persist(tmp_path):
    r = _reg(tmp_path)
    o1 = r.execute("schedule_task", {"name": "日报", "prompt": "写日报", "rrule": "FREQ=DAILY"})
    assert "[schedule_task]" in o1 and (tmp_path / ".lmw_schedules.json").exists()
    o2 = r.execute("notify", {"title": "提示", "message": "完成"})
    assert "[notify]" in o2 and (tmp_path / ".lmw_notifications.json").exists()
