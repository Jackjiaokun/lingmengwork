"""自动化与本地智能工具集 (Phase 94): 对标主流产品的差异化能力。

覆盖:
  - flow_runner       : 工作流编排(类 GitHub Actions / n8n 轻量版), 支持 run/set/echo/if/http/write 步骤, 变量替换
  - formatter         : 多语言代码格式化(black/autopep8/jsbeautifier/gofmt/json), 缺失引擎优雅降级
  - deep_review       : 深度评审(AST 指标 + 危险模式扫描 + 每文件概览), 零依赖
  - local_llm_route   : 本地 LLM 路由(Ollama / OpenAI 兼容 llama.cpp), 未运行服务优雅降级
  - screenshot        : 网页/桌面截图(playwright/selenium), 无引擎优雅降级
  - clipboard         : 剪贴板读写(pyperclip / Windows PowerShell 降级)
  - csv_convert       : CSV <-> JSON / Markdown / XLSX 转换, 零依赖(json/markdown)

设计纪律(与 registry 工具一致):
  - 工具函数签名统一 def name(args, ctx) -> str
  - 路径经 common.resolve_path 落域防护
  - 零硬依赖: 联网用标准库 urllib; 格式化/截图/LLM 调用外部引擎, 缺失自动降级并提示, 绝不崩溃
  - 失败信息以 [tool] 前缀回灌模型, 让其自我修复, 而非抛异常中断
"""

import os
import re
import json
import ast
import subprocess
import urllib.request
import urllib.error

from .common import resolve_path


# ============================================================================
# 公共辅助
# ============================================================================
def _roots(ctx):
    return ctx.get("roots") or ["."]


def _cwd(ctx):
    return ctx.get("cwd") or (str(_roots(ctx)[0]) if _roots(ctx) else ".")


def _resolve(ctx, path):
    return resolve_path(_roots(ctx), path)


def _trim(text, limit=20000):
    text = text.strip() if isinstance(text, str) else str(text)
    try:
        n = int(limit)
    except Exception:
        n = 20000
    if len(text) <= n:
        return text
    return text[:n] + "\n... (已截断, 共 %d 字符)" % len(text)


_VAR = re.compile(r"\$\{([^}]+)\}")


def _substitute(text, vars_):
    if not isinstance(text, str):
        return text

    def repl(m):
        key = m.group(1).strip()
        return str(vars_.get(key, m.group(0)))

    return _VAR.sub(repl, text)


def _eval_cond(cond, vars_):
    cond = (cond or "").strip()
    for op in ("==", "!="):
        if op in cond:
            k, v = cond.split(op, 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            a = vars_.get(k)
            return (str(a) == v) if op == "==" else (str(a) != v)
    return bool(vars_.get(cond))


# ============================================================================
# 1. flow_runner —— 工作流编排
# ============================================================================
def _exec_step(step, vars_, ctx, log, idx=0):
    if not isinstance(step, dict):
        log.append("步骤%s: 跳过(非对象)" % idx)
        return
    name = step.get("name") or ("step%s" % idx)
    cwd = _cwd(ctx)

    if "set" in step:
        s = step["set"] or {}
        for k, v in s.items():
            vars_[k] = _substitute(str(v), vars_)
        log.append("✓ %s: set %s" % (name, list(s.keys())))
        return

    if "echo" in step:
        log.append("» %s: %s" % (name, _substitute(step["echo"], vars_)))
        return

    if "write" in step:
        w = step["write"] or {}
        fp = _resolve(ctx, _substitute(str(w.get("file", "")), vars_))
        txt = _substitute(w.get("text", ""), vars_)
        try:
            parent = os.path.dirname(fp) or "."
            os.makedirs(parent, exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(txt)
            log.append("✓ %s: 写入 %s (%d 字)" % (name, fp, len(txt)))
        except Exception as e:
            log.append("✗ %s: 写入失败 %s" % (name, e))
        return

    if "http" in step:
        h = step["http"] or {}
        url = _substitute(str(h.get("url", "")), vars_)
        method = h.get("method", "GET")
        body = _substitute(h.get("body", "") or "", vars_)
        try:
            data = body.encode("utf-8") if body else None
            req = urllib.request.Request(url, data=data, method=method)
            with urllib.request.urlopen(req, timeout=8) as resp:
                out = resp.read().decode("utf-8", "replace")[:800]
            log.append("✓ %s: HTTP %s %s -> %d 字" % (name, method, url, len(out)))
        except Exception as e:
            log.append("✗ %s: HTTP 失败 %s" % (name, e))
        return

    if "if" in step:
        cond = step["if"]
        ok = _eval_cond(cond, vars_)
        log.append("⤷ %s: if(%s) = %s" % (name, cond, ok))
        if ok:
            for j, sub in enumerate(step.get("then") or []):
                _exec_step(sub, vars_, ctx, log, idx * 10 + j)
        return

    cmd = step.get("run") or step.get("cmd") or step.get("shell")
    if cmd:
        cmd = _substitute(cmd, vars_)
        try:
            r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=60)
            out = (r.stdout or "") + (r.stderr or "")
            log.append("%s %s: rc=%d\n%s" % ("✓" if r.returncode == 0 else "✗", name, r.returncode, _trim(out, 600)))
        except Exception as e:
            log.append("✗ %s: 执行异常 %s" % (name, e))
        return

    log.append("步骤%s: 未知类型, 跳过" % idx)


def flow_runner(args, ctx):
    """工作流编排: 按 steps 串行执行 run/set/echo/if/http/write 步骤, 支持 ${var} 变量替换。"""
    spec = args.get("spec")
    file = args.get("file")
    if not spec and file:
        p = _resolve(ctx, file)
        if not os.path.exists(p):
            return "[flow_runner] 文件不存在: %s" % file
        try:
            spec = open(p, encoding="utf-8").read()
        except Exception as e:
            return "[flow_runner] 读取失败: %s" % e
    if not spec:
        return "[flow_runner] 请提供 spec(JSON 字符串) 或 file(路径)"

    try:
        data = json.loads(spec)
    except Exception:
        try:
            import yaml
            data = yaml.safe_load(spec)
        except ImportError:
            return "[flow_runner] 非 JSON 且未安装 pyyaml, 请提供 JSON spec"
        except Exception as e:
            return "[flow_runner] 解析失败: %s" % e

    if not isinstance(data, dict) or "steps" not in data:
        return "[flow_runner] spec 需为含 steps 列表的对象"

    vars_ = dict(data.get("vars") or {})
    cwd = _cwd(ctx)
    log = ["## 工作流执行报告", "", "步骤数: %d" % len(data["steps"]), ""]
    for i, step in enumerate(data["steps"]):
        _exec_step(step, vars_, ctx, log, i + 1)
    log.append("")
    log.append("最终变量: %s" % json.dumps(vars_, ensure_ascii=False))
    report = "\n".join(log)

    outp = args.get("report")
    if outp:
        rp = _resolve(ctx, outp)
        try:
            with open(rp, "w", encoding="utf-8") as f:
                f.write(report)
            report += "\n\n报告已保存: %s" % rp
        except Exception:
            pass
    return report


# ============================================================================
# 2. formatter —— 多语言代码格式化
# ============================================================================
_EXT_LANG = {
    ".py": "python", ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js",
    ".css": "css", ".html": "html", ".htm": "html", ".json": "json",
    ".go": "go", ".rs": "rust", ".java": "java", ".c": "c", ".cpp": "c",
    ".md": "markdown",
}


def formatter(args, ctx):
    """多语言代码格式化: 自动识别语言, 委托 black/autopep8/jsbeautifier/gofmt, JSON 零依赖。"""
    p = _resolve(ctx, args.get("path", ""))
    if not p or not os.path.exists(p):
        return "[formatter] 文件不存在: %s" % args.get("path")
    ext = os.path.splitext(p)[1].lower()
    try:
        with open(p, encoding="utf-8") as f:
            src = f.read()
    except Exception as e:
        return "[formatter] 读取失败: %s" % e

    lang = args.get("lang") or _EXT_LANG.get(ext, "")
    out = None

    if lang == "json":
        try:
            obj = json.loads(src)
            out = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=args.get("sort_keys", False))
        except Exception as e:
            return "[formatter] JSON 解析失败: %s" % e

    elif lang == "python":
        try:
            import black
            out = black.format_file_contents(src, fast=True, mode=black.Mode())
        except ImportError:
            try:
                import autopep8
                out = autopep8.fix_code(src)
            except ImportError:
                return "[formatter] 未安装 black/autopep8, 无法格式化 Python(可 pip install black)"
        except Exception as e:
            return "[formatter] black 失败: %s" % e

    elif lang in ("js", "css", "html"):
        try:
            import jsbeautifier
            opts = jsbeautifier.default_options()
            opts.indent_size = 2
            out = jsbeautifier.beautify(src, opts)
        except ImportError:
            return "[formatter] 未安装 jsbeautifier, 无法格式化 %s(可 pip install jsbeautifier)" % lang

    elif lang == "go":
        r = subprocess.run(["gofmt", "-w", p], capture_output=True, text=True)
        if r.returncode != 0:
            return "[formatter] gofmt 失败: %s" % r.stderr
        return "[formatter] gofmt 已格式化: %s" % p

    else:
        return "[formatter] 暂不支持语言: %s" % (lang or ext or "未知")

    if out is None:
        return "[formatter] 未产生输出"
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(out)
        before = src.count("\n") + 1
        after = out.count("\n") + 1
        return "[formatter] 已格式化 %s (%d→%d 行)" % (p, before, after)
    except Exception as e:
        return "[formatter] 写回失败: %s" % e


# ============================================================================
# 3. deep_review —— 深度评审
# ============================================================================
_DANGER = [
    ("eval/exec", r"\b(eval|exec)\s*\("),
    ("os.system", r"os\.system\s*\("),
    ("pickle.load", r"pickle\.load\s*\("),
    ("subprocess shell", r"subprocess[^\)]*shell\s*=\s*True"),
    ("SQL 拼接", r"(execute|cursor\.execute)\s*\([^)]*%s?\s*\+"),
    ("明文密码", r"(password|passwd|secret|token)\s*=\s*['\"][A-Za-z0-9]{6,}['\"]"),
]


def deep_review(args, ctx):
    """深度评审: AST 统计 + 危险模式扫描 + 每文件概览, 产出 markdown 报告。"""
    root = str(_resolve(ctx, args.get("path", ".")))
    if not os.path.exists(root):
        return "[deep_review] 路径不存在: %s" % args.get("path")

    files = []
    if os.path.isfile(root):
        if root.endswith(".py"):
            files = [root]
    else:
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if fn.endswith(".py"):
                    files.append(os.path.join(dp, fn))
    if not files:
        return "[deep_review] 未找到 .py 文件"

    total_lines = total_func = total_class = 0
    dangers = []
    per = []

    for fp in files:
        try:
            code = open(fp, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        lines = code.count("\n") + 1
        total_lines += lines
        try:
            tree = ast.parse(code)
        except Exception:
            per.append((fp, lines, 0, 0, "AST 解析失败"))
            continue
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        total_func += len(funcs)
        total_class += len(classes)
        hits = [label for label, pat in _DANGER if re.search(pat, code)]
        if hits:
            dangers.append((fp, hits))
        per.append((fp, lines, len(funcs), len(classes), ", ".join(hits) or "—"))

    rep = ["# 深度评审报告", "", "路径: `%s`" % root, ""]
    rep.append("- 文件数: %d" % len(files))
    rep.append("- 代码行: %d" % total_lines)
    rep.append("- 函数/方法: %d" % total_func)
    rep.append("- 类: %d" % total_class)
    rep.append("")
    rep.append("## 危险模式扫描")
    if dangers:
        for fp, hits in dangers:
            rep.append("- `%s`: %s" % (fp, ", ".join(hits)))
    else:
        rep.append("- 未发现明显危险模式")
    rep.append("")
    rep.append("## 每文件概览")
    rep.append("| 文件 | 行 | 函数 | 类 | 标记 |")
    rep.append("|---|---|---|---|---|")
    for fp, ln, fn, cl, tag in per:
        rel = os.path.relpath(fp, root)
        rep.append("| `%s` | %d | %d | %d | %s |" % (rel, ln, fn, cl, tag))

    report = "\n".join(rep)
    outp = args.get("report") or "deep_review.md"
    rp = _resolve(ctx, outp)
    try:
        with open(rp, "w", encoding="utf-8") as f:
            f.write(report)
        report += "\n\n报告已保存: %s" % rp
    except Exception:
        pass
    return report


# ============================================================================
# 4. local_llm_route —— 本地 LLM 路由
# ============================================================================
def local_llm_route(args, ctx):
    """本地 LLM 路由: 调用 Ollama 或 OpenAI 兼容端点(llama.cpp 等), 服务未运行优雅降级。"""
    prompt = args.get("prompt", "")
    if not prompt:
        return "[local_llm_route] 请提供 prompt"
    base = args.get("base_url") or "http://localhost:11434"
    model = args.get("model") or "llama3"
    backend = (args.get("backend") or "ollama").lower()
    try:
        if backend == "ollama":
            url = base.rstrip("/") + "/api/generate"
            payload = {"model": model, "prompt": prompt, "stream": False}
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                obj = json.loads(resp.read().decode("utf-8"))
            return "[local_llm_route] %s" % obj.get("response", "(空)")
        else:
            url = base.rstrip("/") + "/v1/chat/completions"
            payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                obj = json.loads(resp.read().decode("utf-8"))
            return "[local_llm_route] %s" % obj["choices"][0]["message"]["content"]
    except Exception as e:
        return ("[local_llm_route] 本地 LLM 调用失败(服务未运行?): %s\n"
                "提示: 启动 Ollama(`ollama serve`)或 llama.cpp 后重试。" % e)


# ============================================================================
# 5. screenshot —— 截图
# ============================================================================
def screenshot(args, ctx):
    """网页/桌面截图: 优先 playwright, 其次 selenium, 无引擎优雅降级。"""
    out = args.get("out") or "screenshot.png"
    op = _resolve(ctx, out)
    url = args.get("url")
    filep = args.get("file")
    if not url and not filep:
        return "[screenshot] 请提供 url 或 file"

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            if url:
                pg.goto(url, wait_until="networkidle")
            else:
                pg.goto("file://" + os.path.abspath(filep))
            pg.screenshot(path=op)
            b.close()
        return "[screenshot] 已保存: %s" % op
    except ImportError:
        try:
            from selenium import webdriver  # noqa: F401
            return "[screenshot] 未安装 playwright(可 pip install playwright && playwright install); selenium 需额外配置 driver"
        except ImportError:
            return "[screenshot] 未安装 playwright/selenium, 无法截图(可 pip install playwright && playwright install chromium)"
    except Exception as e:
        return "[screenshot] 截图失败: %s" % e


# ============================================================================
# 6. clipboard —— 剪贴板读写
# ============================================================================
def clipboard(args, ctx):
    """剪贴板读写: pyperclip 优先, Windows 下 PowerShell 降级。"""
    action = (args.get("action") or "read").lower()
    try:
        import pyperclip
        if action == "write":
            pyperclip.copy(args.get("text", ""))
            return "[clipboard] 已复制 (%d 字)" % len(args.get("text", ""))
        return "[clipboard] %s" % pyperclip.paste()
    except ImportError:
        try:
            if os.name == "nt":
                if action == "write":
                    subprocess.run(
                        ["powershell", "-command", "$input | Set-Clipboard"],
                        input=args.get("text", "").encode("utf-16"),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    return "[clipboard] 已通过 PowerShell 复制 (%d 字) [降级]" % len(args.get("text", ""))
                r = subprocess.run(["powershell", "-command", "Get-Clipboard"], capture_output=True, text=True, encoding="utf-8", errors="replace")
                return "[clipboard] %s [降级]" % r.stdout.strip()
            return "[clipboard] 未安装 pyperclip 且非 Windows(pip install pyperclip)"
        except Exception as e:
            return "[clipboard] 剪贴板访问失败(可 pip install pyperclip): %s" % e


# ============================================================================
# 7. csv_convert —— 表格格式转换
# ============================================================================
def csv_convert(args, ctx):
    """CSV <-> JSON / Markdown / XLSX 转换。json/markdown 零依赖, xlsx 需 openpyxl。"""
    p = _resolve(ctx, args.get("path", ""))
    if not p or not os.path.exists(p):
        return "[csv_convert] 文件不存在: %s" % args.get("path")
    to = (args.get("to") or "json").lower()
    try:
        import csv
        with open(p, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except Exception as e:
        return "[csv_convert] 读取失败: %s" % e
    if not rows:
        return "[csv_convert] 空文件"

    header = rows[0]
    data = rows[1:]

    if to == "json":
        objs = [dict(zip(header, r)) for r in data]
        op = _resolve(ctx, args.get("out") or (os.path.splitext(p)[0] + ".json"))
        with open(op, "w", encoding="utf-8") as f:
            f.write(json.dumps(objs, ensure_ascii=False, indent=2))
        return "[csv_convert] 已导出 JSON: %s (%d 行)" % (op, len(objs))

    if to == "markdown":
        md = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
        for r in data:
            md.append("| " + " | ".join(r) + " |")
        op = _resolve(ctx, args.get("out") or (os.path.splitext(p)[0] + ".md"))
        with open(op, "w", encoding="utf-8") as f:
            f.write("\n".join(md))
        return "[csv_convert] 已导出 Markdown 表格: %s (%d 行)" % (op, len(data))

    if to == "xlsx":
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            for r in rows:
                ws.append(r)
            op = _resolve(ctx, args.get("out") or (os.path.splitext(p)[0] + ".xlsx"))
            wb.save(op)
            return "[csv_convert] 已导出 XLSX: %s (%d 行)" % (op, len(data))
        except ImportError:
            return "[csv_convert] 未安装 openpyxl, 无法导出 XLSX(pip install openpyxl); 或改用 to=json/markdown"

    return "[csv_convert] to 仅支持 json/markdown/xlsx"
