"""批次8: 结构化决策与项目记忆补全工具 (零依赖, 纯函数易测)。

对标全球领先 AI 编码智能体的「决策辅助 + 项目记忆」能力:
- generate_project_docs: 扫描仓库生成 CLAUDE.md / AGENTS.md 草稿
  (技术栈 / 关键目录 / 入口 / 测试命令 / 约定), 直接补全批次7 的「项目记忆文档自动读取」待补项。
- impact_analysis: 变更影响分析。输入一个符号名, 输出其定义位置 + 所有调用方/使用点,
  大重构前先看清回归范围 (主题 C 变更影响分析)。
- compare_options: 多方案对比。输入任务 + 2~N 个候选方案, 结构化对比 + 给出建议,
  复杂任务先比后落 (主题 B 多方案对比)。

全部零外部依赖, 不依赖 LLM; 工具签名 (args, ctx) -> str, 与 registry 协议一致。
"""

import os
import re
from collections import Counter

_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "dist", "build", ".venv", "venv",
    ".workbuddy", "target", ".idea", ".vs", ".tox", ".lmw_index", ".pytest_cache",
    "android_app",
}

_CODE_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cpp", ".cc",
    ".c", ".h", ".hpp", ".rb", ".php", ".cs", ".kt", ".swift", ".scala", ".sh",
    ".vue", ".svelte",
}

_LANG_NAMES = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript(React)", ".jsx": "JavaScript(React)", ".java": "Java",
    ".go": "Go", ".rs": "Rust", ".cpp": "C++", ".cc": "C++", ".c": "C",
    ".h": "C/C++ Header", ".hpp": "C++ Header", ".rb": "Ruby", ".php": "PHP",
    ".cs": "C#", ".kt": "Kotlin", ".swift": "Swift", ".scala": "Scala",
    ".sh": "Shell", ".vue": "Vue", ".svelte": "Svelte",
}

# 各语言「按名定位定义」的正则模板 (用 {S} 占位符号名)
_DEF_TEMPLATES = {
    "py": r"(?:def|async def|class)\s+{S}\b",
    "js": r"(?:function|class)\s+{S}\b|(?:const|let|var)\s+{S}\s*=|{S}\s*[:=]\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>)",
    "go": r"func\s+(?:\([^)]*\)\s+)?{S}\s*\(|type\s+{S}\b|func\s+\([^)]*\)\s+{S}\s*\(",
    "java": r"(?:class|interface|enum|struct)\s+{S}\b|(?:public|private|protected|internal|static|final|void|int|string|bool|def|val|func)\s+[\w<>\[\],\s]*\s+{S}\s*\(",
}
_GENERIC_DEF = r"(?:def|class|function|func|interface|type|struct|enum)\s+{S}\b"


def _resolve_root(args, ctx):
    root = (args or {}).get("root")
    if root:
        return str(root)
    ctx = ctx or {}
    if ctx.get("cwd"):
        return str(ctx["cwd"])
    roots = ctx.get("roots") or []
    if roots:
        return str(roots[0])
    return "."


def _list_code_files(root):
    out = []
    for dp, dirs, fns in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            if ext in _CODE_EXT:
                out.append(os.path.join(dp, fn))
    return out


def _walk_lines(fp):
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# generate_project_docs
# ---------------------------------------------------------------------------

def _detect_langs(files):
    cnt = Counter(os.path.splitext(fp)[1].lower() for fp in files)
    langs = []
    for ext, n in cnt.most_common():
        name = _LANG_NAMES.get(ext, ext)
        langs.append((name, n))
    # 合并同名语言 (如 .ts/.tsx 都归 TypeScript)
    merged = {}
    for name, n in langs:
        key = name.split("(")[0]
        merged[key] = merged.get(key, 0) + n
    return sorted(merged.items(), key=lambda x: -x[1])


def _top_dirs(root, files):
    cnt = Counter(os.path.dirname(fp) for fp in files)
    rel = []
    for d, n in cnt.most_common(12):
        relpath = os.path.relpath(d, root).replace(os.sep, "/")
        rel.append((relpath if relpath != "." else "(root)", n))
    return rel


def _entry_points(root, files):
    eps = []
    names = {os.path.basename(fp) for fp in files}
    hints = {
        "main.py": "Python 主入口",
        "__main__.py": "Python 模块入口",
        "server.py": "服务入口 (server.py)",
        "app.py": "应用入口 (app.py)",
        "index.js": "Node 入口 (index.js)",
        "index.ts": "Node/TS 入口 (index.ts)",
        "main.go": "Go 入口 (main.go)",
        "main.js": "前端入口 (main.js)",
    }
    for fn, desc in hints.items():
        if fn in names:
            eps.append(desc)
    # package.json main / scripts.start
    pj = os.path.join(root, "package.json")
    if os.path.isfile(pj):
        try:
            data = __import__("json").loads(open(pj, encoding="utf-8").read())
            if isinstance(data, dict):
                if data.get("main"):
                    eps.append(f"package.json main: {data['main']}")
                start = (data.get("scripts") or {}).get("start")
                if start:
                    eps.append(f"npm start: {start}")
        except Exception:
            pass
    # if __name__ == "__main__"
    for fp in files:
        if fp.endswith(".py"):
            for line in _walk_lines(fp):
                if 'if __name__' in line and '__main__' in line:
                    rel = os.path.relpath(fp, root).replace(os.sep, "/")
                    eps.append(f"{rel} (含 __main__ 守卫)")
                    break
    return eps


def _test_command(root, files):
    cmds = []
    has_pytest = any(
        os.path.isfile(os.path.join(root, "pytest.ini"))
        or os.path.isfile(os.path.join(root, "pyproject.toml"))
        or os.path.isfile(os.path.join(root, "tests", "__init__.py"))
        or fp.endswith(os.sep + "tests" + os.sep) or os.path.basename(os.path.dirname(fp)) == "tests"
        for fp in files
    )
    # 更稳妥地检测 tests 目录
    if os.path.isdir(os.path.join(root, "tests")) or any(
        os.sep + "tests" + os.sep in (fp.replace("/", os.sep)) for fp in files
    ):
        has_pytest = True
    if has_pytest:
        cmds.append("pytest (建议: pytest tests/)")
    pj = os.path.join(root, "package.json")
    if os.path.isfile(pj):
        try:
            data = __import__("json").loads(open(pj, encoding="utf-8").read())
            if isinstance(data, dict) and (data.get("scripts") or {}).get("test"):
                cmds.append(f"npm test ({data['scripts']['test']})")
        except Exception:
            pass
    for marker, cmd in (("go.mod", "go test ./..."), ("Cargo.toml", "cargo test"),
                        ("pom.xml", "mvn test"), ("build.gradle", "gradle test")):
        if os.path.isfile(os.path.join(root, marker)):
            cmds.append(cmd)
    if not cmds:
        cmds.append("(未检测到标准测试命令; 请补充)")
    return cmds


def _conventions(root):
    notes = []
    for fn in ("README.md", "README", "LICENSE", "LICENSE.md", "CODE_OF_CONDUCT.md",
               "CONTRIBUTING.md", ".editorconfig", "pyproject.toml", "tsconfig.json"):
        if os.path.isfile(os.path.join(root, fn)):
            notes.append(fn)
    return notes


def generate_project_docs(args, ctx):
    root = _resolve_root(args, ctx)
    if not os.path.isdir(root):
        return f"[generate_project_docs] 目录不存在: {root}"
    fmt = (args.get("format") or "claude_md").lower()
    files = _list_code_files(root)
    if not files:
        return (f"# CLAUDE.md — {(os.path.basename(root) or root)} (草稿)\n\n"
                f"> 在 `{root}` 未检测到代码文件 (支持的扩展名: "
                f"{', '.join(sorted(_CODE_EXT))})。\n"
                f"> 若这是文档/配置型仓库, 可手动补充下方结构。\n\n"
                f"## Overview\n(一句话描述本项目。)\n\n"
                f"## Tech Stack\n(列出主要语言/框架。)\n\n"
                f"## Key Directories\n(列出核心目录及其职责。)\n\n"
                f"## Entry Points\n(列出程序入口/启动命令。)\n\n"
                f"## How to Test\n(列出测试命令。)\n\n"
                f"## Conventions & Notes\n(列出编码约定/重要提醒。)\n")
    proj = os.path.basename(os.path.abspath(root))
    langs = _detect_langs(files)
    dirs = _top_dirs(root, files)
    eps = _entry_points(root, files)
    tests = _test_command(root, files)
    conv = _conventions(root)

    if fmt == "agents_md":
        lines = [f"# AGENTS.md — {proj}", ""]
        lines.append("本文件供 AI 编码代理快速建立项目认知。改动代码前先读它。")
        lines.append("")
        lines.append("## 项目定位")
        lines.append(f"- 名称: {proj}")
        lines.append(f"- 技术栈: {', '.join(f'{n}×{c}' for n, c in langs)}")
        lines.append("")
        lines.append("## 代理协作约定")
        lines.append("- 改动前先用 repo_map / symbol_search 建立结构认知。")
        lines.append("- 写完关键代码跑测试 (见下) + review_code 自评估。")
        lines.append("- 大重构前用 impact_analysis 看清回归范围。")
        lines.append("- 复杂决策用 compare_options 比对方案后再落地。")
        lines.append("")
    else:
        lines = [f"# CLAUDE.md — {proj}", ""]
        lines.append("本文件供 Claude Code / 灵梦work 等编码代理自动加载, 建立项目认知。")
        lines.append("")

    lines.append("## Overview")
    lines.append(f"- 项目: {proj}")
    lines.append(f"- 代码文件数: {len(files)}")
    lines.append("(一句话描述本项目的目标与核心能力。)")
    lines.append("")
    lines.append("## Tech Stack")
    for n, c in langs:
        lines.append(f"- {n}: {c} 个文件")
    lines.append("")
    lines.append("## Key Directories")
    for d, c in dirs:
        lines.append(f"- `{d}/`: {c} 个代码文件")
    lines.append("")
    lines.append("## Entry Points")
    if eps:
        for e in eps[:12]:
            lines.append(f"- {e}")
    else:
        lines.append("(未发现明显入口; 可手动补充启动命令。)")
    lines.append("")
    lines.append("## How to Test")
    for t in tests:
        lines.append(f"- {t}")
    lines.append("")
    lines.append("## Conventions & Notes")
    if conv:
        for c in conv:
            lines.append(f"- 已存在: `{c}`")
    else:
        lines.append("- (未检测到 README / LICENSE / 配置文件。)")
    lines.append("- 编码约定: 建议补充缩进/命名/提交风格等。")
    lines.append("")
    lines.append("> 由 灵梦work `generate_project_docs` 自动生成草稿, 请人工复核后保存为 CLAUDE.md / AGENTS.md。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# impact_analysis
# ---------------------------------------------------------------------------

def _def_regex(ext):
    base = os.path.splitext("x" + ext)[1].lower()
    tmpl = _DEF_TEMPLATES.get(base)
    if tmpl is None:
        # 按扩展名粗分: 脚本类走 js 模板, 编译类走 java 模板
        if base in (".py",):
            tmpl = _DEF_TEMPLATES["py"]
        elif base in (".js", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".sh"):
            tmpl = _DEF_TEMPLATES["js"]
        elif base in (".go",):
            tmpl = _DEF_TEMPLATES["go"]
        else:
            tmpl = _GENERIC_DEF
    return tmpl


def _scan_symbol(root, symbol, glob_filter=None):
    if not symbol:
        return None
    sym = symbol.strip()
    if not sym:
        return None
    esc = re.escape(sym)
    usage_re = re.compile(r"(?<![\w])" + esc + r"(?![\w])")
    files = _list_code_files(root)
    if glob_filter:
        import fnmatch
        files = [f for f in files if fnmatch.fnmatch(os.path.basename(f), glob_filter)
                 or fnmatch.fnmatch(f, glob_filter)]
    definitions = []  # (relpath, line_no)
    usages = []       # (relpath, line_no, snippet)
    for fp in files:
        ext = os.path.splitext(fp)[1].lower()
        def_re = re.compile(_def_regex(ext).format(S=esc))
        for i, line in enumerate(_walk_lines(fp), start=1):
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            is_def = bool(def_re.search(line))
            has_usage = bool(usage_re.search(line))
            if is_def:
                definitions.append((rel, i, line.strip()[:120]))
            elif has_usage:
                usages.append((rel, i, line.strip()[:120]))
    return {"definitions": definitions, "usages": usages}


def impact_analysis(args, ctx):
    root = _resolve_root(args, ctx)
    symbol = (args.get("symbol") or "").strip()
    if not symbol:
        return "[impact_analysis] 需提供 symbol 参数 (要分析的符号名, 如 'connect' / 'UserService')"
    if not os.path.isdir(root):
        return f"[impact_analysis] 目录不存在: {root}"
    glob_filter = (args.get("glob") or "").strip() or None
    res = _scan_symbol(root, symbol, glob_filter)
    defs = res["definitions"]
    uses = res["usages"]
    lines = [f"[impact_analysis] 符号 `{symbol}` 在 `{root}` 的影响分析:"]
    lines.append(f"- 定义点: {len(defs)} 处")
    lines.append(f"- 使用/调用点: {len(uses)} 处")
    if defs:
        lines.append("\n## 定义位置")
        for rel, ln, snip in defs[:20]:
            lines.append(f"- {rel}:{ln}  `{snip}`")
    else:
        lines.append("\n## 定义位置\n(未找到定义; 可能来自外部依赖/动态生成, 下方使用点仍需关注。) ")
    if uses:
        # 按文件聚合调用点
        by_file = Counter(rel for rel, _, _ in uses)
        lines.append("\n## 受影响文件 (按调用点数量)")
        for rel, c in by_file.most_common(20):
            lines.append(f"- {rel}: {c} 处调用")
        lines.append("\n## 调用点明细 (前 30)")
        for rel, ln, snip in uses[:30]:
            lines.append(f"- {rel}:{ln}  `{snip}`")
    else:
        lines.append("\n## 使用/调用点\n(仓库内未找到该符号的使用; 若符号为私有函数, 影响范围局限在当前文件。) ")
    lines.append("\n→ 改动前请确认上述定义与调用方, 改完用 auto_test + review_code 验证回归。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# compare_options
# ---------------------------------------------------------------------------

_RISK_RANK = {"low": 1, "medium": 2, "high": 3}
_RISK_RANK.update({k.capitalize(): v for k, v in _RISK_RANK.items()})


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        # 支持 "a; b" 或 "a\nb" 或 "a, b"
        parts = re.split(r"[;\n]", v)
        out = []
        for p in parts:
            out.extend(x.strip() for x in p.split(",") if x.strip())
        return out
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v)]


def _rank(v):
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().lower()
    return _RISK_RANK.get(s, 2)


def compare_options(args, ctx):
    options = args.get("options")
    if isinstance(options, str):
        try:
            options = __import__("json").loads(options)
        except Exception:
            options = None
    if not isinstance(options, list) or not options:
        return "[compare_options] 需提供 options 参数 (方案列表, 每项含 title/description/pros/cons/effort/risk)"
    task = (args.get("task") or "").strip()
    parsed = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        title = str(opt.get("title") or opt.get("name") or "(未命名方案)")
        desc = str(opt.get("description") or "")
        pros = _as_list(opt.get("pros"))
        cons = _as_list(opt.get("cons"))
        effort = _rank(opt.get("effort"))
        risk = _rank(opt.get("risk"))
        score = len(pros) - len(cons) - 0.5 * (effort + risk)
        parsed.append({
            "title": title, "desc": desc, "pros": pros, "cons": cons,
            "effort": effort, "risk": risk, "score": round(score, 2),
        })
    if not parsed:
        return "[compare_options] options 解析为空, 请检查每项至少含 title。"
    # 推荐: 分数最高; 并列时取 effort+risk 更小者
    best = sorted(parsed, key=lambda x: (-x["score"], x["effort"] + x["risk"]))[0]

    lines = []
    if task:
        lines.append(f"[compare_options] 针对任务「{task}」的方案对比:")
    else:
        lines.append("[compare_options] 方案对比:")
    lines.append("")
    # 表头
    lines.append("| 方案 | 简述 | 优点 | 缺点 | 工作量 | 风险 | 评分 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for o in parsed:
        desc = (o["desc"][:40] + "…") if len(o["desc"]) > 41 else o["desc"]
        pros = "; ".join(o["pros"]) or "—"
        cons = "; ".join(o["cons"]) or "—"
        pros = (pros[:60] + "…") if len(pros) > 61 else pros
        cons = (cons[:60] + "…") if len(cons) > 61 else cons
        lines.append(
            f"| {o['title']} | {desc} | {pros} | {cons} | "
            f"{o['effort']} | {o['risk']} | {o['score']} |"
        )
    lines.append("")
    lines.append(f"## 建议")
    lines.append(f"**推荐方案: {best['title']}** (评分 {best['score']}, 工作量 {best['effort']}, 风险 {best['risk']})")
    if best["pros"]:
        lines.append(f"- 主要优点: {'; '.join(best['pros'])}")
    if best["cons"]:
        lines.append(f"- 主要缺点: {'; '.join(best['cons'])}")
    lines.append("\n→ 确认后据此落地; 若与你的判断不同, 以你的权衡为准。")
    return "\n".join(lines)
