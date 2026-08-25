"""文件系统工具: 读 / 写 / 编辑 / 列目录 / glob / grep。"""
import glob as _glob_mod
import os
import re
from pathlib import Path

from .common import ToolError, resolve_path
from .undo import get_default_stack

# 仅对文本类扩展名做 grep, 避免读二进制
_TEXT_SUFFIX = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".c", ".cpp", ".h", ".hpp",
    ".rs", ".rb", ".php", ".html", ".htm", ".css", ".scss", ".json", ".toml", ".yaml",
    ".yml", ".md", ".txt", ".sh", ".bat", ".ps1", ".sql", ".xml", ".ini", ".cfg",
    ".csv", ".log", ".lua", ".r", ".pl", ".kt", ".swift", ".vue", ".ipynb",
}
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".workbuddy", "reference"}


def read_file(args, ctx):
    p = resolve_path(ctx["roots"], args["path"])
    if not p.exists():
        raise ToolError("文件不存在: " + args["path"])
    if p.is_dir():
        raise ToolError("路径是目录而非文件: " + args["path"])
    text = p.read_text(encoding="utf-8", errors="replace")
    offset = int(args.get("offset", 0) or 0)
    limit = args.get("limit")
    numbered = args.get("numbered") in (True, "true", "1")
    lines = text.splitlines()
    if offset or (limit is not None and limit != ""):
        lim = int(limit) if limit not in (None, "", "0") else None
        lines = lines[offset: (offset + lim) if lim else None]
    body = "\n".join(lines)
    if numbered and body:
        shown = body.splitlines()
        width = len(str(offset + len(shown)))
        body = "\n".join(f"{str(offset + i).rjust(width)} | {ln}" for i, ln in enumerate(shown, 1))
    return body


def write_file(args, ctx):
    p = resolve_path(ctx["roots"], args["path"])
    content = args.get("content", "")
    if p.exists() and p.is_dir():
        raise ToolError("目标已是目录: " + args["path"])
    # 改动前快照(文件不存在则记 None), 供 undo 回滚
    old = p.read_text(encoding="utf-8") if p.exists() else None
    get_default_stack().push(str(p.resolve()), old)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {args['path']} ({len(content)} 字符)"


def edit_file(args, ctx):
    p = resolve_path(ctx["roots"], args["path"])
    if not p.exists() or p.is_dir():
        raise ToolError("文件不存在或不是文件: " + args["path"])
    text = p.read_text(encoding="utf-8")
    old = args["old_string"]
    new = args.get("new_string", "")
    if old not in text:
        raise ToolError("未找到待替换文本 old_string。\n" + _nearest_lines(text, old))
    count = text.count(old)
    if count > 1 and args.get("replace_all") not in (True, "true", "1"):
        raise ToolError(f"old_string 出现 {count} 次, 存在歧义; 请提供更多上下文或设 replace_all=true。")
    # 改动前快照
    get_default_stack().push(str(p.resolve()), text)
    if args.get("replace_all") in (True, "true", "1"):
        text = text.replace(old, new)
    else:
        text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    return f"已编辑 {args['path']} (替换 {count} 处)"


def _nearest_lines(text, old, k=3):
    """old_string 未命中时, 给出可操作的修正提示 (近似行/候选行号)。"""
    from difflib import get_close_matches
    old_lines = [l.strip() for l in old.splitlines() if l.strip()]
    if not old_lines:
        return "(old_string 为空或仅含空白)"
    key = old_lines[0][:50]
    lines = text.splitlines()
    cand = [(i, ln) for i, ln in enumerate(lines, 1) if key in ln]
    if cand:
        return ("未找到精确匹配, 但以下 %d 行含片段「%s」(行号: %s), 请据此对齐 old_string:\n- "
                % (len(cand), key, ", ".join(str(i) for i, _ in cand[:k]))) + "\n- ".join(
            f"L{i}: {ln.strip()[:80]}" for i, ln in cand[:k])
    pool = [ln.strip() for ln in lines if ln.strip()]
    close = get_close_matches(key, pool, n=k, cutoff=0.5)
    if close:
        return "未找到包含「%s」的行, 近似行(请据此修正 old_string):\n- " % key + "\n- ".join(close)
    return "未找到包含「%s」的行, 且无近似匹配。" % key


def insert_at(args, ctx):
    """在文件指定行号之前插入内容 (精确行编辑, 仿 Cursor 精确插入)。

    参数: path 文件, line 行号(0基, 行首插入; 传文件总行数则在末尾追加), content 插入内容。
    返回插入摘要。
    """
    p = resolve_path(ctx["roots"], args["path"])
    if not p.exists() or p.is_dir():
        raise ToolError("文件不存在或不是文件: " + args["path"])
    text = p.read_text(encoding="utf-8")
    old = text
    get_default_stack().push(str(p.resolve()), old)
    line = int(args.get("line", 0) or 0)
    content = args.get("content", "")
    lines = text.splitlines(keepends=True)
    if line < 0:
        raise ToolError("line 不能为负")
    if line >= len(lines):
        # 末尾追加
        if lines and not lines[-1].endswith("\n"):
            new_text = text + "\n" + content
        else:
            new_text = text + content
    else:
        new_text = "".join(lines[:line]) + content + ("\n" if not content.endswith("\n") else "") + "".join(lines[line:])
    p.write_text(new_text, encoding="utf-8")
    return f"已向 {args['path']} 第 {line} 行前插入 {len(content)} 字符"


def replace_in_files(args, ctx):
    """跨文件批量正则替换 (仿 IDE 全局替换)。

    参数: pattern 正则, replacement 替换串, path? 起始目录(默认根), glob? 文件通配(如 *.py),
          ignore_case? max_files? (上限, 默认 50)。
    仅修改文本类文件, 命中文件先推快照供 undo。返回受影响文件列表。
    """
    import glob as _glob
    pattern = args["pattern"]
    replacement = args.get("replacement", "")
    try:
        rx = re.compile(pattern, re.IGNORECASE if args.get("ignore_case") in (True, "true", "1") else 0)
    except re.error as e:
        raise ToolError("正则表达式错误: " + str(e))
    root = resolve_path(ctx["roots"], args.get("path", "."))
    fglob = args.get("glob", "*")
    max_files = int(args.get("max_files", 50) or 50)
    touched = []
    scanned = 0
    for r in ctx["roots"]:
        for dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if len(touched) >= max_files:
                    break
                fp = Path(dirpath) / fn
                rel = str(fp.relative_to(r)) if fp.is_relative_to(r) else fn
                if not _glob.fnmatch.fnmatch(rel, fglob):
                    continue
                if fp.suffix.lower() not in _TEXT_SUFFIX:
                    continue
                scanned += 1
                try:
                    content = fp.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if not rx.search(content):
                    continue
                new_content = rx.sub(replacement, content)
                if new_content != content:
                    get_default_stack().push(str(fp.resolve()), content)
                    fp.write_text(new_content, encoding="utf-8")
                    touched.append(f"{rel} ({content.count('')-1}行)")
        if len(touched) >= max_files:
            break
    if not touched:
        return f"(无替换: 扫描 {scanned} 个文件, 0 处命中)"
    return f"replace_in_files 命中 {len(touched)} 个文件:\n- " + "\n- ".join(touched)


def diff_view(args, ctx):
    """预览对某文件的拟改动, 返回 unified diff (不改写文件)。

    参数: path 目标文件, old_string/new_string (局部替换预览) 或 old/new (整文件预览)。
    返回 diff 文本, 模型据此决定是否正式 edit/write。
    """
    import difflib
    p = resolve_path(ctx["roots"], args["path"])
    if not p.exists() or p.is_dir():
        raise ToolError("文件不存在或不是文件: " + args["path"])
    current = p.read_text(encoding="utf-8")
    new_text = None
    if "old_string" in args or "new_string" in args:
        os_ = args.get("old_string", "")
        ns_ = args.get("new_string", "")
        if os_ not in current:
            raise ToolError("diff_view: 未找到 old_string, 无法生成预览。")
        cnt = current.count(os_)
        if cnt > 1 and args.get("replace_all") not in (True, "true", "1"):
            raise ToolError(f"diff_view: old_string 出现 {cnt} 次存在歧义, 请提供更多上下文。")
        if args.get("replace_all") in (True, "true", "1"):
            new_text = current.replace(os_, ns_)
        else:
            new_text = current.replace(os_, ns_, 1)
    elif "old" in args or "new" in args:
        new_text = args.get("new", current)
    else:
        raise ToolError("diff_view: 需提供 old_string/new_string 或 old/new。")
    a = current.splitlines()
    b = new_text.splitlines()
    diff = difflib.unified_diff(a, b, fromfile="current", tofile="proposed", lineterm="")
    out = "\n".join(diff)
    if not out:
        return "(无差异)"
    return out


def list_dir(args, ctx):
    p = resolve_path(ctx["roots"], args.get("path", "."))
    if not p.exists():
        raise ToolError("目录不存在: " + str(args.get("path", ".")))
    if p.is_file():
        return f"{p.name} (文件)"
    entries = []
    for child in sorted(p.iterdir()):
        if child.name in _SKIP_DIRS:
            continue
        kind = "目录" if child.is_dir() else "文件"
        size = ""
        if child.is_file():
            try:
                size = f" {child.stat().st_size}B"
            except Exception:
                size = ""
        entries.append(f"- [{kind}] {child.name}{size}")
    if not entries:
        return "(空目录)"
    return "\n".join(entries)


def glob_files(args, ctx):
    import glob as _glob
    pattern = args["pattern"]
    base = ctx["roots"][0] if ctx["roots"] else Path.cwd()
    matches = []
    for r in ctx["roots"]:
        found = _glob.glob(str(r / pattern), recursive=True)
        for f in found:
            fp = Path(f)
            if fp.name in _SKIP_DIRS or any(part in _SKIP_DIRS for part in fp.parts):
                continue
            matches.append(str(fp))
    matches = sorted(set(matches))
    if not matches:
        return "(无匹配)"
    return "\n".join(matches)


def grep_files(args, ctx):
    """按正则搜索文本。增强: context 上下行 / glob 文件过滤 / head_limit 单文件上限。"""
    pattern = args["pattern"]
    try:
        rx = re.compile(pattern, re.IGNORECASE if args.get("ignore_case") in (True, "true", "1") else 0)
    except re.error as e:
        raise ToolError("正则表达式错误: " + str(e))
    root = resolve_path(ctx["roots"], args.get("path", "."))
    max_matches = int(args.get("max_matches", 200) or 200)
    head_limit = int(args.get("head_limit", 50) or 50)
    context = int(args.get("context", 0) or 0)
    fglob = (args.get("glob") or "").strip()
    out = []
    total = 0
    for r in ctx["roots"]:
        for dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if total >= max_matches:
                    break
                fp = Path(dirpath) / fn
                if fp.suffix.lower() not in _TEXT_SUFFIX:
                    continue
                if fglob and not _glob_mod.fnmatch.fnmatch(str(fp), fglob) and not _glob_mod.fnmatch.fnmatch(fp.name, fglob):
                    continue
                try:
                    content = fp.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                lines = content.splitlines()
                hits = [i for i, ln in enumerate(lines, 1) if rx.search(ln)][:head_limit]
                if not hits:
                    continue
                rel = str(fp.relative_to(r)) if fp.is_relative_to(r) else fn
                for i in hits:
                    if context > 0:
                        lo, hi = max(1, i - context), min(len(lines), i + context)
                        for j in range(lo, hi + 1):
                            mark = ">" if j == i else " "
                            out.append(f"{rel}:{j}:{mark} {lines[j - 1]}")
                    else:
                        out.append(f"{rel}:{i}: {lines[i - 1]}")
                    total += 1
                    if total >= max_matches:
                        break
            if total >= max_matches:
                break
        if total >= max_matches:
            break
    if not out:
        return "(无匹配)"
    if total >= max_matches:
        out.append(f"... (已达上限 {max_matches} 条)")
    return "\n".join(out)
