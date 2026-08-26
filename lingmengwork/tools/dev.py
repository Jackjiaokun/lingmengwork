"""编程生产力工具: 静态检查 / 自动格式化 / 本地开发服务 / 数据库查询。

设计原则:
- 全部优先复用系统已装工具 (flake8/pylint/black/autopep8/prettier/gofmt/eslint),
  缺失时以「零依赖内建能力」优雅降级 (如语法检查用 py_compile, 数据查询用 sqlite3)。
- 工具函数签名统一为 def name(args, ctx) -> str, 与 registry 分发契约一致。
- 路径一律经 fs.resolve_path 落域防护, 不越界。
"""
import os
import re
import io
import csv
import json
import time
import shlex
import sqlite3
import subprocess
import urllib.request
from pathlib import Path

from .common import ToolError, resolve_path


# ============================================================
# 1) lint_code — 静态检查 (语法零依赖 + 可选 flake8/pylint/eslint)
# ============================================================
_LINT_TARGET_EXT = {
    ".py": "python",
    ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".mjs": "js", ".cjs": "js",
    ".go": "go",
    ".sh": "shell", ".bash": "shell",
    ".rb": "ruby",
}
_BUILTIN_STYLE_RE = [
    (re.compile(r".{121,}"), "行超过 120 字符"),
    (re.compile(r"[ \t]+$"), "行尾多余空白"),
    (re.compile(r"except\s*:\s*$"), "裸 except (应捕获具体异常)"),
    (re.compile(r"import\s+\*"), "通配 import (*) 降低可读性"),
    (re.compile(r"#\s*(TODO|FIXME|XXX|HACK)"), "待办标记"),
    (re.compile(r"print\s*\("), "调试 print (发布前应移除)"),
    (re.compile(r"pdb\.set_trace|breakpoint\s*\("), "调试断点残留"),
]


def lint_code(args, ctx):
    """对文件/目录做静态检查。零依赖语法校验常驻; 若装了 flake8/pylint/eslint 则叠加深度检查。"""
    raw = (args.get("path") or "").strip()
    if not raw:
        return "[lint_code] 未提供 path"
    rp = fs_resolve(ctx, raw)
    if rp.is_dir():
        targets = []
        for ext in _LINT_TARGET_EXT:
            targets.extend(rp.rglob("*" + ext))
            if len(targets) >= 200:
                break
        targets = targets[:200]
    else:
        targets = [rp]
    if not targets:
        return f"[lint_code] 在 {rp} 未找到可检查的文件"

    out = []
    total_issues = 0
    for f in targets:
        lang = _LINT_TARGET_EXT.get(f.suffix.lower())
        if not lang:
            continue
        issues = _lint_one(f, lang)
        if issues:
            total_issues += len(issues)
            out.append(f"### {f.relative_to(rp) if rp.is_dir() else f.name} ({len(issues)} 项)")
            out.extend("  - " + i for i in issues[:60])
            if len(issues) > 60:
                out.append(f"  ... 其余 {len(issues) - 60} 项省略")
    if not out:
        return f"[lint_code] ✅ 通过: 检查了 {len(targets)} 个文件, 未发现语法/风格问题。"
    return "[lint_code] 检查报告 (语法零依赖 + 内建风格):\n" + "\n".join(out) + \
        f"\n\n合计 {total_issues} 项发现, 涉及 {sum(1 for _ in out if _.startswith('###'))} 个文件。"


def _lint_one(f, lang):
    issues = []
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [f"无法读取: {e}"]
    lines = text.split("\n")

    # —— 零依赖语法检查 ——
    if lang == "python":
        try:
            compile(text, str(f), "exec")
        except SyntaxError as e:
            issues.append(f"第 {e.lineno} 行语法错误: {e.msg}")
        except Exception as e:
            issues.append(f"编译失败: {e}")
    # 其它语言先走外部 linter, 没有则仅做内建风格检查

    # —— 外部深度 linter (尽力而为) ——
    ext_lint = _external_lint(f, lang)
    if ext_lint:
        issues.extend(ext_lint)

    # —— 内建风格检查 (所有语言通用) ——
    for i, ln in enumerate(lines, 1):
        for rx, msg in _BUILTIN_STYLE_RE:
            if rx.search(ln):
                issues.append(f"第 {i} 行: {msg}")
                break
    return issues


def _external_lint(f, lang):
    if lang == "python":
        for tool in ("flake8", "pylint"):
            exe = _which(tool)
            if exe:
                try:
                    r = subprocess.run([exe, str(f)], capture_output=True, text=True, timeout=60)
                    res = (r.stdout or r.stderr).strip()
                    if res:
                        return [l for l in res.split("\n") if l.strip()][:60]
                except Exception:
                    pass
    elif lang == "js":
        exe = _which("eslint")
        if exe:
            try:
                r = subprocess.run([exe, "--no-eslintrc", "--format=compact", str(f)],
                                   capture_output=True, text=True, timeout=60)
                res = (r.stdout or r.stderr).strip()
                if res:
                    return [l for l in res.split("\n") if l.strip()][:60]
            except Exception:
                pass
    elif lang == "go":
        exe = _which("go")
        if exe:
            try:
                r = subprocess.run([exe, "vet", str(f)], capture_output=True, text=True, timeout=60)
                res = (r.stderr or r.stdout).strip()
                if res:
                    return [l for l in res.split("\n") if l.strip()][:60]
            except Exception:
                pass
    return []


# ============================================================
# 2) format_code — 自动格式化 (black/autopep8/prettier/gofmt ...)
# ============================================================
def format_code(args, ctx):
    """对文件/目录做自动格式化。check=true 仅预览差异不写入。优先用 black/autopep8/prettier/gofmt/isort。"""
    raw = (args.get("path") or "").strip()
    check = str(args.get("check", "")).lower() in ("1", "true", "yes", "dry")
    if not raw:
        return "[format_code] 未提供 path"
    rp = fs_resolve(ctx, raw)

    if rp.is_dir():
        files = [p for p in rp.rglob("*") if p.suffix.lower() in _LINT_TARGET_EXT]
        files = files[:200]
    else:
        files = [rp]

    applied, skipped, diffs = [], [], []
    for f in files:
        lang = _LINT_TARGET_EXT.get(f.suffix.lower())
        res = _format_one(f, lang, check)
        if res is None:
            skipped.append(f.name)
        elif res == "":
            applied.append(f.name)
        else:
            applied.append(f.name)
            diffs.append(f"### {f.name}\n{res}")
    head = f"[format_code] {'预览(dry-run) ' if check else ''}处理 {len(files)} 个文件: " \
          f"已格式化 {len(applied)}, 跳过 {len(skipped)}"
    if not diffs:
        return head + (" ✅" if applied else "")
    return head + "\n\n" + "\n\n".join(diffs[:20]) + \
        (f"\n\n... 仅显示前 20 个文件差异" if len(diffs) > 20 else "")


def _format_one(f, lang, check):
    if lang == "python":
        order = [("black", ["--fast", "--quiet"]), ("autopep8", ["-a", "-a"]), ("isort", [])]
        for tool, extra in order:
            exe = _which(tool)
            if not exe:
                continue
            try:
                if check:
                    r = subprocess.run([exe, *(extra if tool != "black" else ["--check", "--diff"]),
                                        str(f)], capture_output=True, text=True, timeout=60)
                    if tool == "black" and r.returncode == 0:
                        return ""  # 已符合规范
                    diff = (r.stdout or "").strip()
                    return diff if diff else ""
                else:
                    subprocess.run([exe, *extra, str(f)], capture_output=True, text=True, timeout=60)
                    return ""
            except Exception:
                continue
        return None  # 无可用格式化器
    if lang == "js":
        exe = _which("prettier")
        if exe:
            try:
                if check:
                    r = subprocess.run([exe, "--check", str(f)], capture_output=True, text=True, timeout=60)
                    return "" if r.returncode == 0 else (r.stdout or r.stderr).strip()[:2000]
                subprocess.run([exe, "--write", str(f)], capture_output=True, text=True, timeout=60)
                return ""
            except Exception:
                return None
        return None
    if lang == "go":
        exe = _which("gofmt")
        if exe:
            try:
                if check:
                    r = subprocess.run([exe, "-l", str(f)], capture_output=True, text=True, timeout=60)
                    return "" if not r.stdout.strip() else f"需格式化: {f.name}"
                subprocess.run([exe, "-w", str(f)], capture_output=True, text=True, timeout=60)
                return ""
            except Exception:
                return None
        return None
    return None


# ============================================================
# 3) run_server — 启动/停止本地开发服务 (后台 + 健康检查)
# ============================================================
_SERVERS = {}  # name -> {"proc": Popen, "log": Path, "url": str}


def run_server(args, ctx):
    """启动/停止后台开发服务。action=start(默认)/stop/list; command 为启动命令; port 用于健康检查。"""
    action = (args.get("action") or "start").strip().lower()
    name = (args.get("name") or "dev").strip()
    if action == "list":
        if not _SERVERS:
            return "[run_server] 当前无运行中的服务。"
        lines = []
        for n, info in _SERVERS.items():
            alive = info["proc"].poll() is None
            lines.append(f"- {n}: {'运行中' if alive else '已退出'} pid={info['proc'].pid} url={info['url']}")
        return "[run_server] 运行中的服务:\n" + "\n".join(lines)
    if action == "stop":
        info = _SERVERS.pop(name, None)
        if not info:
            return f"[run_server] 未找到名为 {name} 的服务。"
        try:
            info["proc"].terminate()
            info["proc"].wait(timeout=5)
        except Exception:
            try:
                info["proc"].kill()
            except Exception:
                pass
        return f"[run_server] 已停止服务 {name} (pid={info['proc'].pid})。"

    # start
    command = (args.get("command") or "").strip()
    if not command:
        return "[run_server] 未提供 command (启动命令), 例如 python -m http.server 8000"
    base = ctx.get("cwd") or (ctx["roots"][0] if ctx.get("roots") else ".")
    cwd = fs_resolve(ctx, (args.get("cwd") or str(base))) if args.get("cwd") else Path(base)
    log_dir = Path(base) / ".lmw_servers"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=str(cwd),
            stdout=open(log_path, "w", encoding="utf-8", errors="replace"),
            stderr=subprocess.STDOUT,
        )
    except Exception as e:
        return f"[run_server] 启动失败: {e}"
    time.sleep(1.2)
    if proc.poll() is not None:
        tail = _tail(log_path, 12)
        return f"[run_server] ❌ 服务 {name} 启动后已退出 (code={proc.returncode}):\n{tail}"
    url = ""
    port = args.get("port")
    if port:
        url = f"http://127.0.0.1:{port}"
        try:
            urllib.request.urlopen(url, timeout=3)
            health = "✅ 健康检查通过"
        except Exception as e:
            health = f"⚠️ 暂未响应 (可能仍在启动): {e}"
    else:
        health = "已启动 (未指定 port, 跳过健康检查)"
    _SERVERS[name] = {"proc": proc, "log": log_path, "url": url or "(未配置 port)"}
    return f"[run_server] ✅ 服务 {name} 已启动 (pid={proc.pid}, cwd={cwd})\n" \
           f"URL: {url or '(未配置 port)'}\n{health}\n日志: {log_path}"


# ============================================================
# 4) db_run — SQLite 查询 / CSV 载入即查 (零依赖)
# ============================================================
def db_run(args, ctx):
    """执行 SQL。db=sqlite 文件路径(只读); 不传 db 则内存库, 可先用 csv 把文件载入为表再查。"""
    query = (args.get("query") or "").strip()
    if not query:
        return "[db_run] 未提供 query"
    db_path = (args.get("db") or "").strip()
    csv_files = args.get("csv") or []
    if isinstance(csv_files, str):
        csv_files = [csv_files]

    try:
        if db_path:
            rp = fs_resolve(ctx, db_path)
            uri = f"file:{rp}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
        else:
            conn = sqlite3.connect(":memory:")
        cur = conn.cursor()

        # 载入 CSV 为内存表 (表名取文件名 stem)
        loaded = []
        for cf in csv_files:
            crp = fs_resolve(ctx, cf)
            tbl = _load_csv_to_table(cur, crp)
            loaded.append(tbl)

        statements = [s for s in query.split(";") if s.strip()]
        results = []
        for stmt in statements:
            cur.execute(stmt)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                results.append(_render_table(cols, rows))
            else:
                results.append(f"(影响行数: {cur.rowcount})")
        conn.commit()
        conn.close()
        head = ""
        if loaded:
            head = "已载入表: " + ", ".join(loaded) + "\n"
        body = "\n\n".join(results)
        return f"[db_run] {head}{body}"
    except Exception as e:
        return f"[db_run] 执行失败: {e}"


def _load_csv_to_table(cur, csv_path):
    stem = re.sub(r"\W+", "_", csv_path.stem)[:40] or "tbl"
    with open(csv_path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        return stem
    header = rows[0]
    cols = [re.sub(r"\W+", "_", h) or f"c{i}" for i, h in enumerate(header)]
    cur.execute(f'DROP TABLE IF EXISTS "{stem}"')
    col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
    cur.execute(f'CREATE TABLE "{stem}" ({col_defs})')
    q = f'INSERT INTO "{stem}" VALUES ({", ".join("?" * len(cols))})'
    for r in rows[1:]:
        r = (r + [""] * len(cols))[:len(cols)]
        cur.execute(q, r)
    return stem


def _render_table(cols, rows, max_rows=200):
    if not rows:
        return "列: " + ", ".join(cols) + "\n(0 行)"
    head = " | ".join(str(c) for c in cols)
    sep = "-+-".join("-" * max(3, len(str(c))) for c in cols)
    body = []
    for r in rows[:max_rows]:
        body.append(" | ".join("" if v is None else str(v) for v in r))
    more = f"\n... 共 {len(rows)} 行, 仅显示前 {max_rows}" if len(rows) > max_rows else ""
    return head + "\n" + sep + "\n" + "\n".join(body) + more


# ============================================================
# 公共辅助
# ============================================================
def fs_resolve(ctx, path):
    return resolve_path(ctx["roots"], path)


def _which(name):
    from shutil import which
    return which(name)


def _tail(path, n=12):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        return "\n".join(lines[-n:])
    except Exception:
        return "(无日志)"
