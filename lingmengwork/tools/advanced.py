"""次世代编码工具集: 测试自愈闭环 / 仓库符号地图 / 智能交付。

这三项是对标 Aider / Devin / Claude Code 领先一代的核心能力, 且零外部依赖:

- auto_test : 运行测试/构建, 结构化解析失败, Agent 据此自动修复代码并再跑,
             形成「红 -> 绿」自愈闭环 (Aider auto-test / Devin self-heal 范式)。
- repo_map  : 生成仓库符号地图 (Aider repo-map 范式), 给 LLM 仓库级结构认知,
             大仓库编码前先调用, 远胜普通 grep。
- git_commit: 自动 stage + 抓取 diff 摘要回灌 -> 生成提交信息 -> 提交 (保留 hook),
             仿 Claude Code /commit 智能交付。
"""
import fnmatch
import os
import re
import subprocess

from .common import ToolError


def _run(cmd, cwd, timeout=120):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise ToolError(f"命令超时 ({timeout}s): {cmd}")
    except Exception as e:
        raise ToolError(f"命令执行失败: {e}")
    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else (r.stdout or "")
    err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else (r.stderr or "")
    return r.returncode, out, err


def _q(s):
    """给 shell 用的安全双引号包裹 (Git Bash 兼容)。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _first_int(m):
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------
# auto_test : 测试自愈闭环
# --------------------------------------------------------------------------
def auto_test(args, ctx):
    cmd = (args.get("command") or "").strip()
    path = args.get("path")
    cwd = ctx.get("cwd") or (str(ctx["roots"][0]) if ctx.get("roots") else ".")
    if not cmd:
        # 自动探测测试命令
        if os.path.exists(os.path.join(cwd, "package.json")):
            cmd = "npm test"
        elif _probe_pytest(cwd):
            cmd = "pytest -q"
        else:
            cmd = "python -m pytest -q"
    if path and ("pytest" in cmd or "npm test" in cmd):
        cmd = f"{cmd} {path}"
    try:
        rc, out, err = _run(cmd, cwd, timeout=180)
    except ToolError as e:
        return f"[auto_test] {e}"
    text = out + "\n" + err
    passed = _first_int(re.search(r"(\d+)\s+passed", text))
    failed = _first_int(re.search(r"(\d+)\s+failed", text))
    errn = _first_int(re.search(r"(\d+)\s+error", text))
    if passed is None and failed is None and errn is None:
        if rc == 0:
            passed = "?"
        else:
            failed = "?"
    head = [
        f"[auto_test] 命令: {cmd}",
        f"退出码: {rc}",
        f"通过: {passed}  失败: {failed}  错误: {errn}",
    ]
    if rc == 0:
        head.append("✅ 全部通过, 无需自愈。")
        return "\n".join(head)
    failed_cases = re.findall(r"FAILED\s+([\w./:_-]+)", text)
    if failed_cases:
        head.append("失败用例:")
        head += [f"  - {c}" for c in failed_cases[:40]]
    tb = _tail_traceback(text, 60)
    if tb:
        head.append("错误摘要 (traceback 末段):")
        head.append(tb)
    head.append("→ 请根据上述失败自行修复代码, 然后再次调用 auto_test 验证, 直至全绿。")
    return "\n".join(head)


def _probe_pytest(cwd):
    for nm in ("pytest.ini", "pyproject.toml", "setup.py", "conftest.py", "tox.ini"):
        if os.path.exists(os.path.join(cwd, nm)):
            return True
    if os.path.isdir(os.path.join(cwd, "tests")):
        return True
    return False


def _tail_traceback(text, n):
    idx = text.rfind("Traceback (most recent call last)")
    if idx >= 0:
        seg = text[idx:]
        return "\n".join(seg.splitlines()[-n:])
    lines = text.splitlines()
    return "\n".join(lines[-n:])


# --------------------------------------------------------------------------
# repo_map : 仓库符号地图
# --------------------------------------------------------------------------
_CODE_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cpp",
    ".cc", ".c", ".h", ".hpp", ".rb", ".php", ".cs", ".kt", ".swift", ".scala", ".sh",
}
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "dist", "build", ".venv", "venv",
    ".workbuddy", "target", ".idea", ".vs", ".tox",
}

_SYM_RES = {
    "py": [
        re.compile(r"^\s*(?:(?:async\s+)?def|class)\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "js": [
        re.compile(r"^(?:export\s+)?(?:default\s+)?(?:function|class|const|let|var)\s+(?P<n>[A-Za-z_$]\w*)"),
        re.compile(r"(?P<n>[A-Za-z_$]\w*)\s*[:=]\s*\([^)]*\)\s*=>"),
        re.compile(r"(?P<n>[A-Za-z_$]\w*)\s*\([^)]*\)\s*\{"),
    ],
    "ts": [
        re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:function|class|interface|type|enum|const|let|var)\s+(?P<n>[A-Za-z_$]\w*)"),
        re.compile(r"(?P<n>[A-Za-z_$]\w*)\s*[:=]\s*\([^)]*\)\s*=>"),
    ],
    "jsx": [
        re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:function|class|const|let|var)\s+(?P<n>[A-Za-z_$]\w*)"),
    ],
    "tsx": [
        re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:function|class|const|let|var)\s+(?P<n>[A-Za-z_$]\w*)"),
    ],
    "java": [
        re.compile(r"^\s*(?:public|private|protected|static|final|abstract|\s)*\s*(?:class|interface|enum)\s+(?P<n>[A-Za-z_]\w*)"),
        re.compile(r"^\s*(?:public|private|protected|static|final|\s)*\s*(?P<n>[A-Za-z_]\w*)\s*\([^)]*\)\s*\{"),
    ],
    "go": [
        re.compile(r"^\s*(?:func|type|struct|interface|package|const|var|func)\s+(?P<n>[A-Za-z_]\w*)"),
        re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?(?P<n>[A-Za-z_]\w*)\s*\("),
    ],
    "rs": [
        re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait|mod|impl|type)\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "c": [
        re.compile(r"^\s*(?:static\s+|inline\s+)?[A-Za-z_][\w\s\*]*\s+(?P<n>[A-Za-z_]\w*)\s*\("),
        re.compile(r"^\s*(?:typedef\s+)?struct\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "cpp": [
        re.compile(r"^\s*(?:inline\s+|static\s+|virtual\s+)?[A-Za-z_][\w:<\>,\s\*&]*\s+(?P<n>[A-Za-z_]\w*)\s*\("),
        re.compile(r"^\s*(?:class|struct|enum|namespace)\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "cc": [
        re.compile(r"^\s*(?:inline\s+|static\s+|virtual\s+)?[A-Za-z_][\w:<\>,\s\*&]*\s+(?P<n>[A-Za-z_]\w*)\s*\("),
        re.compile(r"^\s*(?:class|struct|enum|namespace)\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "h": [
        re.compile(r"^\s*(?:inline\s+|static\s+)?[A-Za-z_][\w\s\*]*\s+(?P<n>[A-Za-z_]\w*)\s*\("),
        re.compile(r"^\s*(?:class|struct|enum|namespace)\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "hpp": [
        re.compile(r"^\s*(?:inline\s+|static\s+)?[A-Za-z_][\w:<\>,\s\*&]*\s+(?P<n>[A-Za-z_]\w*)\s*\("),
        re.compile(r"^\s*(?:class|struct|enum|namespace)\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "rb": [
        re.compile(r"^\s*(?:def|class|module)\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "php": [
        re.compile(r"^\s*(?:public|private|protected|static|\s)*\s*function\s+(?P<n>[A-Za-z_]\w*)"),
        re.compile(r"^\s*(?:abstract\s+|final\s+)?class\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "cs": [
        re.compile(r"^\s*(?:public|private|protected|internal|static|async|\s)*\s*(?:class|interface|struct|enum|namespace)\s+(?P<n>[A-Za-z_]\w*)"),
        re.compile(r"^\s*(?:public|private|protected|internal|static|async|\s)*\s*(?P<n>[A-Za-z_]\w*)\s*\([^)]*\)\s*\{"),
    ],
    "kt": [
        re.compile(r"^\s*(?:fun|class|interface|object|enum\s+class|data\s+class|val|var)\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "swift": [
        re.compile(r"^\s*(?:func|class|struct|enum|protocol|extension)\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "scala": [
        re.compile(r"^\s*(?:def|class|object|trait|val|var)\s+(?P<n>[A-Za-z_]\w*)"),
    ],
    "sh": [
        re.compile(r"^\s*(?:function\s+)?(?P<n>[A-Za-z_]\w*)\s*\(\s*\)\s*\{?"),
    ],
}


def _load_gitignore(base):
    """轻量解析 .gitignore 为模式列表 (支持 ! 取反 / 目录后缀 / 通配)。"""
    pats = []
    gi = os.path.join(base, ".gitignore")
    if os.path.isfile(gi):
        try:
            with open(gi, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    pats.append(line)
        except Exception:
            pass
    return pats


def _is_ignored(rel_parts, pats):
    """判断相对路径 parts 是否被 gitignore 模式忽略 (支持 ! 取反)。"""
    if not pats:
        return False
    name = rel_parts[-1]
    result = False
    for p in pats:
        neg = p.startswith("!")
        pat = p[1:] if neg else p
        dir_only = pat.endswith("/")
        matched = False
        if "/" in pat.rstrip("/"):
            full = "/".join(rel_parts)
            norm = pat.lstrip("/")
            if fnmatch.fnmatch(full, norm) or fnmatch.fnmatch(full, "*" + norm):
                matched = True
        else:
            if fnmatch.fnmatch(name, pat.rstrip("/")) or fnmatch.fnmatch(name, pat.rstrip("/") + "/*"):
                matched = True
        if matched:
            result = not neg
    return result


def repo_map(args, ctx):
    base = ctx.get("cwd") or (str(ctx["roots"][0]) if ctx.get("roots") else ".")
    max_files = int(args.get("max_files") or 80)
    max_sym = int(args.get("max_symbols") or 40)
    md = args.get("max_depth")
    max_depth = int(md) if md not in (None, "") else None
    gi_pats = _load_gitignore(base)
    files = []
    for root, dirs, fns in os.walk(base):
        depth = root[len(base):].count(os.sep)
        if max_depth is not None and depth >= max_depth:
            dirs[:] = []
        kept = []
        for d in dirs:
            if d in _SKIP_DIRS:
                continue
            rel = os.path.relpath(os.path.join(root, d), base).split(os.sep)
            if _is_ignored(rel, gi_pats):
                continue
            kept.append(d)
        dirs[:] = kept
        for fn in fns:
            if max_depth is not None and depth > max_depth:
                continue
            if os.path.splitext(fn)[1].lower() in _CODE_EXT:
                rel = os.path.relpath(os.path.join(root, fn), base).split(os.sep)
                if _is_ignored(rel, gi_pats):
                    continue
                files.append(os.path.join(root, fn))
    files.sort()
    files = files[:max_files]
    out = [f"[repo_map] 扫描 {len(files)} 个文件 (上限 {max_files}), 提取符号映射:"]
    total = 0
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue
        ext = os.path.splitext(fp)[1].lower().lstrip(".")
        res_list = _SYM_RES.get(ext) or _SYM_RES["js"]
        syms = []
        for i, line in enumerate(lines, 1):
            for rgx in res_list:
                m = rgx.search(line)
                if m:
                    syms.append((i, line.strip()[:64]))
                    break
            if len(syms) >= max_sym:
                break
        if syms:
            rel = os.path.relpath(fp, base)
            out.append(f"\n{rel}:")
            for ln, sig in syms:
                out.append(f"  L{ln}  {sig}")
            total += len(syms)
    out.append(f"\n共 {total} 个符号。用 read_file/grep 查看细节。")
    return "\n".join(out)


# --------------------------------------------------------------------------
# git_commit : 智能交付
# --------------------------------------------------------------------------
def git_commit(args, ctx):
    cwd = ctx.get("cwd") or (str(ctx["roots"][0]) if ctx.get("roots") else ".")
    message = (args.get("message") or "").strip()
    add_all = args.get("add_all", True)
    rc, _, err = _run("git rev-parse --is-inside-work-tree", cwd, timeout=20)
    if rc != 0:
        return f"[git_commit] 当前目录不是 git 仓库, 无法提交: {err.strip()[:200]}"
    _run("git add -A" if add_all else "git add -u", cwd, timeout=60)
    _, diffstat, _ = _run("git diff --cached --stat", cwd, timeout=30)
    if not diffstat.strip() or "0 files changed" in diffstat:
        return "[git_commit] 没有已暂存的改动, 无需提交。"
    if not message:
        _, diffbody, _ = _run("git diff --cached | head -c 4000", cwd, timeout=30)
        return ("[git_commit] 已暂存改动, 请生成提交信息后再次调用本工具 (带 message 参数)。\n"
                f"改动统计:\n{diffstat}\n\n前 4KB diff:\n{diffbody}")
    rc4, cout, cerr = _run(f"git commit -m {_q(message)}", cwd, timeout=60)
    if rc4 != 0:
        return f"[git_commit] 提交失败: {cerr.strip()[:300]}\n(如为 hook 拒绝请修复后重试, 不要 --no-verify 绕过)"
    if args.get("push"):
        _run("git push", cwd, timeout=90)
    return f"[git_commit] 提交成功:\n{cout.strip()[:300]}\n{diffstat.strip()}"


# --------------------------------------------------------------------------
# symbol_search : 跨仓库符号检索 (仿 LSP 跳转定义)
# --------------------------------------------------------------------------
def symbol_search(args, ctx):
    """跨仓库按名称检索符号定义位置。

    参数:
      name    : 符号名 (子串匹配, 或 regex=true 时视作正则)
      regex?  : true 时 name 按正则匹配
      glob?   : 文件过滤, 如 *.py / src/**
      limit?  : 命中上限 (默认 50)
    返回 path:Lline: 签名 列表, 供「改之前先看定义/签名一致」类任务。
    """
    base = ctx.get("cwd") or (str(ctx["roots"][0]) if ctx.get("roots") else ".")
    name = (args.get("name") or "").strip()
    if not name:
        return "[symbol_search] 需提供 name 参数"
    use_regex = args.get("regex") in (True, "true", "1")
    if use_regex:
        try:
            rx = re.compile(name, re.IGNORECASE)
        except re.error as e:
            raise ToolError("正则表达式错误: " + str(e))
        match = lambda s: bool(rx.search(s))
    else:
        needle = name.lower()
        match = lambda s: needle in s.lower()
    fglob = (args.get("glob") or "").strip()
    limit = int(args.get("limit", 50) or 50)

    files = []
    for root, dirs, fns in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in _CODE_EXT:
                files.append(os.path.join(root, fn))
    files.sort()

    out = []
    total = 0
    for fp in files:
        if fglob and not fnmatch.fnmatch(fp, fglob) and not fnmatch.fnmatch(os.path.basename(fp), fglob):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue
        ext = os.path.splitext(fp)[1].lower().lstrip(".")
        res_list = _SYM_RES.get(ext) or _SYM_RES["js"]
        for i, line in enumerate(lines, 1):
            for rgx in res_list:
                m = rgx.search(line)
                if m and match(m.group("n")):
                    rel = os.path.relpath(fp, base)
                    out.append(f"{rel}:{i}: {line.strip()[:80]}")
                    total += 1
                    break
        if total >= limit:
            break
    if not out:
        return f"[symbol_search] 未找到匹配「{name}」的符号 (扫描 {len(files)} 文件)。"
    if total >= limit:
        out.append(f"... (已达上限 {limit})")
    return "[symbol_search] 命中 %d 个符号:\n" % total + "\n".join(out)
