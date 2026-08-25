"""批次8: 结构化决策与项目记忆补全 (decision.py) 零依赖回归测试。

覆盖: generate_project_docs (语言/入口/测试/空仓) / impact_analysis (定义+调用点/空符号/无使用) / compare_options (表格/建议/空列表)。
"""
import os
import shutil
import tempfile

from lingmengwork.tools import decision


def _ctx(d):
    return {"cwd": d, "roots": [d]}


def _mkrepo():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "main.py"), "w", encoding="utf-8") as f:
        f.write(
            "def connect():\n"
            "    return 'conn'\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    connect()\n"
        )
    with open(os.path.join(d, "svc.py"), "w", encoding="utf-8") as f:
        f.write(
            "from main import connect\n"
            "def run():\n"
            "    x = connect()\n"
            "    connect()\n"
            "    return x\n"
        )
    with open(os.path.join(d, "tool.ts"), "w", encoding="utf-8") as f:
        f.write(
            "export function connect() {\n"
            "  return 'ts-conn';\n"
            "}\n"
        )
    os.makedirs(os.path.join(d, "tests"), exist_ok=True)
    with open(os.path.join(d, "tests", "test_main.py"), "w", encoding="utf-8") as f:
        f.write("def test_connect():\n    assert connect()\n")
    with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
        f.write("# Demo\n")
    return d


# ---------------- generate_project_docs ----------------

def test_generate_docs_detects_language():
    d = _mkrepo()
    try:
        out = decision.generate_project_docs({"root": d}, _ctx(d))
        assert "CLAUDE.md" in out
        assert "Python" in out
        assert "TypeScript" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_generate_docs_entry_and_test():
    d = _mkrepo()
    try:
        out = decision.generate_project_docs({"root": d}, _ctx(d))
        assert "__main__" in out or "main.py" in out   # 入口识别
        assert "pytest" in out                          # 测试命令识别
        assert "README.md" in out                       # 约定识别
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_generate_docs_agents_format():
    d = _mkrepo()
    try:
        out = decision.generate_project_docs({"root": d, "format": "agents_md"}, _ctx(d))
        assert "AGENTS.md" in out
        assert "impact_analysis" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_generate_docs_empty_root():
    d = tempfile.mkdtemp()
    try:
        # 空目录: 不应抛错, 返回友好草稿
        out = decision.generate_project_docs({"root": d}, _ctx(d))
        assert "CLAUDE.md" in out
        assert "未检测到代码文件" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------- impact_analysis ----------------

def test_impact_definition_and_usages():
    d = _mkrepo()
    try:
        out = decision.impact_analysis({"symbol": "connect", "root": d}, _ctx(d))
        assert "定义点" in out
        assert "使用/调用点" in out
        # connect 在 main.py/tool.ts 有定义, 在 main/svc 有调用
        assert "svc.py" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_impact_no_symbol():
    d = _mkrepo()
    try:
        out = decision.impact_analysis({"symbol": ""}, _ctx(d))
        assert "需提供 symbol" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_impact_no_usages():
    d = tempfile.mkdtemp()
    try:
        # 定义私有符号但全仓不使用
        with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as f:
            f.write("def _secret_internal():\n    return 1\n")
        out = decision.impact_analysis({"symbol": "_secret_internal", "root": d}, _ctx(d))
        assert "定义位置" in out
        assert "使用/调用点" in out
        assert "未找到" in out or "仓库内未找到" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_impact_glob_filter():
    d = _mkrepo()
    try:
        out = decision.impact_analysis({"symbol": "connect", "root": d, "glob": "*.ts"}, _ctx(d))
        # 仅 .ts 文件: tool.ts 定义 connect, 但 .py 中的调用不计入
        assert "tool.ts" in out
        assert "svc.py" not in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------- compare_options ----------------

def test_compare_options_table():
    d = tempfile.mkdtemp()
    try:
        opts = [
            {"title": "方案A", "description": "用 Redis 缓存", "pros": ["快"], "cons": ["多依赖"], "effort": "medium", "risk": "low"},
            {"title": "方案B", "description": "用内存缓存", "pros": ["无依赖", "简单"], "cons": ["重启丢"], "effort": "low", "risk": "low"},
        ]
        out = decision.compare_options({"task": "加缓存", "options": opts}, _ctx(d))
        assert "方案A" in out and "方案B" in out
        assert "建议" in out
        assert "| 方案 |" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_compare_options_recommends_lower_risk():
    d = tempfile.mkdtemp()
    try:
        opts = [
            {"title": "高风险方案", "pros": ["快"], "cons": [], "effort": "low", "risk": "high"},
            {"title": "稳健方案", "pros": ["安全", "简单"], "cons": ["略慢"], "effort": "low", "risk": "low"},
        ]
        out = decision.compare_options({"options": opts}, _ctx(d))
        # 稳健方案评分更高 -> 被推荐
        assert "**推荐方案: 稳健方案**" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_compare_options_empty():
    d = tempfile.mkdtemp()
    try:
        out = decision.compare_options({"options": []}, _ctx(d))
        assert "需提供 options" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)
