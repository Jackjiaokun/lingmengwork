"""Phase 108 工具套件: Schema→多语言代码生成 / 工程文件检查与生成 (零依赖).

全部工具仅用标准库, 输入为文本, 失败以 [tool] 前缀优雅降级回灌模型。
- json_schema_to_go   : Schema -> Go struct (含 json tag / omitempty / 嵌套类型前置)
- json_schema_to_java : Schema -> Java POJO (字段 + getter/setter)
- markdown_table_to_csv: Markdown 表格 -> CSV (自动跳过 |---| 分隔行)
- sql_to_json         : CREATE TABLE -> JSON 表结构描述
- env_to_json         : .env -> JSON (跳过注释, 支持 export 前缀与引号剥离)
- dockerfile_lint     : Dockerfile 最佳实践检查 (FROM tag/sudo/apt/ADD/USER/WORKDIR/HEALTHCHECK)
- gitignore_gen       : 按技术栈生成 .gitignore (python/node/go/java/rust/generic)

标准库: json / re / csv / io
"""

import json
import re
import csv
import io


# ---------------------------------------------------------------------------
# json_schema_to_go
# ---------------------------------------------------------------------------
def _go_scalar(t):
    return {
        "string": "string",
        "integer": "int",
        "number": "float64",
        "boolean": "bool",
        "null": "interface{}",
        "object": "map[string]interface{}",
    }.get(t, "interface{}")


def _go_type(spec, name_hint, out):
    if not isinstance(spec, dict):
        return "interface{}"
    t = spec.get("type")
    if isinstance(t, list):
        return "interface{}"
    if t == "array":
        items = spec.get("items")
        if isinstance(items, dict):
            return "[]" + _go_type(items, name_hint + "Item", out)
        return "[]interface{}"
    if t == "object" or "properties" in spec:
        props = spec.get("properties")
        if isinstance(props, dict) and props:
            cls = (name_hint[:1].upper() + name_hint[1:]) or "Nested"
            out.append(_gen_go_struct(cls, spec, out))
            return cls
        return "map[string]interface{}"
    return _go_scalar(t)


def _gen_go_struct(name, spec, out):
    props = spec.get("properties") or {}
    req = set(spec.get("required") or [])
    lines = ["type %s struct {" % name]
    for k, v in props.items():
        go_t = _go_type(v, k, out)
        field = k[:1].upper() + k[1:]
        opt = "" if k in req else ",omitempty"
        lines.append('    %s %s `json:"%s%s"`' % (field, go_t, k, opt))
    if not props:
        lines.append("    // 无字段")
    lines.append("}")
    return "\n".join(lines)


def json_schema_to_go(args, ctx):
    raw = args.get("schema") or ""
    name = (args.get("name") or "Model").strip() or "Model"
    pkg = (args.get("package") or "main").strip() or "main"
    try:
        schema = json.loads(raw)
    except Exception as e:
        return "[json_schema_to_go] Schema 解析失败: %s" % e
    if not isinstance(schema, dict):
        return "[json_schema_to_go] Schema 顶层必须是对象."
    try:
        out = []
        main = _gen_go_struct(name, schema, out)
        # 嵌套类型在前, 主类型在后 (Go 同包内顺序无关, 但可读性更好)
        body = "\n\n".join(out + [main])
        return "package %s\n\n%s" % (pkg, body)
    except Exception as e:
        return "[json_schema_to_go] 转换失败: %s" % e


# ---------------------------------------------------------------------------
# json_schema_to_java
# ---------------------------------------------------------------------------
def _java_type(spec, boxed=False):
    """boxed=True 时用包装类型 (Java 泛型不支持基本类型, 如 List<Integer>)."""
    if not isinstance(spec, dict):
        return "Object"
    t = spec.get("type")
    if t == "array":
        items = spec.get("items")
        inner = _java_type(items, boxed=True) if isinstance(items, dict) else "Object"
        return "List<%s>" % inner
    if t == "object" or "properties" in spec:
        return "Map<String, Object>"
    box = {"integer": "Integer", "number": "Double", "boolean": "Boolean"}
    if boxed and t in box:
        return box[t]
    return {
        "string": "String",
        "integer": "int",
        "number": "double",
        "boolean": "boolean",
        "null": "Object",
    }.get(t, "Object")


def json_schema_to_java(args, ctx):
    raw = args.get("schema") or ""
    name = (args.get("name") or "Model").strip() or "Model"
    pkg = (args.get("package") or "").strip()
    try:
        schema = json.loads(raw)
    except Exception as e:
        return "[json_schema_to_java] Schema 解析失败: %s" % e
    if not isinstance(schema, dict):
        return "[json_schema_to_java] Schema 顶层必须是对象."
    props = schema.get("properties") or {}
    lines = []
    if pkg:
        lines.append("package %s;" % pkg)
        lines.append("")
    lines.append("import java.util.List;")
    lines.append("import java.util.Map;")
    lines.append("")
    lines.append("public class %s {" % name)
    for k, v in props.items():
        lines.append("    private %s %s;" % (_java_type(v), k))
    for k, v in props.items():
        typ = _java_type(v)
        cap = k[:1].upper() + k[1:]
        lines.append("")
        lines.append("    public %s get%s() { return this.%s; }" % (typ, cap, k))
        lines.append("    public void set%s(%s %s) { this.%s = %s; }" % (cap, typ, k, k, k))
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# markdown_table_to_csv
# ---------------------------------------------------------------------------
def markdown_table_to_csv(args, ctx):
    raw = args.get("markdown") or ""
    rows = []
    for line in raw.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        # 跳过 |---|---| 对齐分隔行
        if cells and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    if len(rows) < 2:
        return "[markdown_table_to_csv] 未找到有效表格 (需表头 + 至少一行数据)."
    header = rows[0]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows[1:]:
        padded = list(r) + [""] * (len(header) - len(r))
        w.writerow(padded[:len(header)])
    return buf.getvalue().rstrip("\r\n")


# ---------------------------------------------------------------------------
# sql_to_json
# ---------------------------------------------------------------------------
def sql_to_json(args, ctx):
    raw = args.get("sql") or ""
    tables = []
    pattern = (r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)[`\"\]]?\s*"
               r"\((.*?)\)\s*(?:;|$)")
    for m in re.finditer(pattern, raw, re.S | re.I):
        tname = m.group(1)
        body = m.group(2)
        cols = []
        depth = 0
        cur = ""
        parts = []
        for ch in body:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        if cur.strip():
            parts.append(cur)
        for part in parts:
            line = " ".join(part.split())
            if not line:
                continue
            if re.match(r"^(PRIMARY|FOREIGN|UNIQUE|KEY|CONSTRAINT|INDEX|CHECK)\b",
                        line, re.I):
                continue
            pm = re.match(r"[`\"\[]?(\w+)[`\"\]]?\s+(.+)$", line)
            if not pm:
                continue
            col_name = pm.group(1)
            rest = pm.group(2)
            tm = re.match(r"([\w]+(?:\s*\([^)]*\))?)", rest)
            col_type = tm.group(1).strip() if tm else rest.strip()
            flags = []
            if re.search(r"\bNOT\s+NULL\b", rest, re.I):
                flags.append("not_null")
            if re.search(r"\bPRIMARY\s+KEY\b", rest, re.I):
                flags.append("primary_key")
            if re.search(r"\bUNIQUE\b", rest, re.I):
                flags.append("unique")
            if re.search(r"\bAUTO_INCREMENT\b", rest, re.I):
                flags.append("auto_increment")
            cols.append({"name": col_name, "type": col_type, "constraints": flags})
        tables.append({"table": tname, "columns": cols})
    if not tables:
        return "[sql_to_json] 未解析到 CREATE TABLE 语句."
    payload = tables if len(tables) > 1 else tables[0]
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# env_to_json
# ---------------------------------------------------------------------------
def env_to_json(args, ctx):
    raw = args.get("env") or ""
    data = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[len("export "):].strip()
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k:
            data[k] = v
    return json.dumps(data, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# dockerfile_lint
# ---------------------------------------------------------------------------
def dockerfile_lint(args, ctx):
    raw = args.get("dockerfile") or ""
    issues = []
    lines = raw.splitlines()
    has_from = False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_]+)\s*(.*)$", s)
        if not m:
            continue
        cmd = m.group(1).upper()
        rest = m.group(2)
        if cmd == "FROM":
            has_from = True
            first = rest.split()[0] if rest.split() else ""
            if ":" not in first and "@" not in first:
                issues.append({"line": i, "level": "warn", "rule": "from-no-tag",
                               "msg": "FROM 未指定明确 tag (默认 latest, 构建不可复现)"})
            elif first.endswith(":latest"):
                issues.append({"line": i, "level": "warn", "rule": "from-latest",
                               "msg": "避免使用 :latest 标签"})
        elif cmd == "RUN":
            if re.search(r"\bsudo\b", rest):
                issues.append({"line": i, "level": "warn", "rule": "run-sudo",
                               "msg": "容器内无需使用 sudo"})
            if re.search(r"apt-get\s+install", rest) and "-y" not in rest:
                issues.append({"line": i, "level": "warn", "rule": "apt-no-yes",
                               "msg": "apt-get install 应加 -y 以免交互阻塞"})
            if re.search(r"apt-get\s+update", rest) and not re.search(
                    r"rm\s+-rf\s+/var/lib/apt/lists", rest):
                issues.append({"line": i, "level": "info", "rule": "apt-no-clean",
                               "msg": "建议在同一层清理 apt 缓存以减小镜像"})
        elif cmd == "ADD":
            if not re.search(r"(^https?://|\.(tar|tgz|tar\.gz|zip|gz)$)", rest):
                issues.append({"line": i, "level": "warn", "rule": "add-vs-copy",
                               "msg": "本地文件建议用 COPY 而不是 ADD"})
    if not has_from:
        issues.append({"line": 0, "level": "error", "rule": "no-from",
                       "msg": "Dockerfile 缺少 FROM 指令"})
    checks = [
        ("WORKDIR", "no-workdir", "info", "建议设置 WORKDIR 而非用绝对路径 cd"),
        ("USER", "no-user", "warn", "建议用 USER 切换到非 root 用户"),
        ("HEALTHCHECK", "no-healthcheck", "info", "建议添加 HEALTHCHECK"),
    ]
    for kw, rule, level, msg in checks:
        if not any(re.match(r"^\s*%s\b" % kw, l, re.I) for l in lines):
            issues.append({"line": 0, "level": level, "rule": rule, "msg": msg})
    counts = {}
    for it in issues:
        counts[it["level"]] = counts.get(it["level"], 0) + 1
    return json.dumps({
        "ok": not any(i["level"] == "error" for i in issues),
        "total": len(issues),
        "counts": counts,
        "issues": issues,
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# gitignore_gen
# ---------------------------------------------------------------------------
_TEMPLATES = {
    "python": (
        "__pycache__/\n*.py[cod]\n*$py.class\n.venv/\nvenv/\nenv/\n"
        ".env\n.env.*\n*.egg-info/\ndist/\nbuild/\n.pytest_cache/\n"
        ".mypy_cache/\n.coverage\nhtmlcov/"
    ),
    "node": (
        "node_modules/\nnpm-debug.log*\nyarn-error.log*\npnpm-debug.log*\n"
        ".env.local\n.env.*.local\ndist/\nbuild/\n.next/\nout/\n.cache/"
    ),
    "go": (
        "*.exe\n*.exe~\n*.dll\n*.so\n*.dylib\n*.test\n*.out\n"
        "vendor/\nbin/\ngo.work.sum"
    ),
    "java": (
        "*.class\n*.log\n*.jar\n*.war\n*.ear\ntarget/\nbuild/\n"
        ".gradle/\n.gradle/\n.idea/\n*.iml\nout/"
    ),
    "rust": (
        "target/\nCargo.lock\n**/*.rs.bk\n*.pdb"
    ),
    "generic": (
        ".DS_Store\nThumbs.db\n*.log\n*.tmp\n.idea/\n.vscode/\n*.swp"
    ),
}


def gitignore_gen(args, ctx):
    raw = args.get("stacks") or args.get("stack") or ""
    if isinstance(raw, list):
        items = [str(s).strip().lower() for s in raw if str(s).strip()]
    else:
        items = [s.strip().lower() for s in re.split(r"[,，\s]+", str(raw)) if s.strip()]
    if not items:
        return "[gitignore_gen] 请指定 stacks, 如 python,node,go (可选: %s)." % ", ".join(_TEMPLATES)
    known = [s for s in items if s in _TEMPLATES]
    unknown = [s for s in items if s not in _TEMPLATES]
    if not known:
        return "[gitignore_gen] 无匹配模板: %s (可选: %s)." % (
            ", ".join(unknown), ", ".join(_TEMPLATES))
    blocks = ["# 由 gitignore_gen 生成"]
    for s in known:
        blocks.append("# ===== %s =====\n%s" % (s, _TEMPLATES[s]))
    out = "\n\n".join(blocks)
    if unknown:
        out += "\n\n# 未知模板(已忽略): %s" % ", ".join(unknown)
    return out
