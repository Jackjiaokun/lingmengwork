"""MCP 代码评审服务器 (stdio, 零依赖): 让 agent 拥有「始终可用、无需 LLM」的静态评审能力。

与 mcp_fs_server / mcp_shell_server 同协议 (stdio JSON-RPC, 换行分隔):
  initialize -> notifications/initialized -> tools/list -> tools/call
仅向 stdout 输出 JSON-RPC 行; 日志/错误走 stderr, 避免污染协议流。

提供工具:
  - code_review : 对「文件路径 / 代码片段 / diff 文本」做零依赖静态评审
                 (py_compile 语法检查 + 规则扫描), 返回与内置 review_code 完全一致的
                 [code-review] 文本块 (VERDICT/SCORE/ISSUES/SUMMARY), 前端可直接解析进趋势图。

设计定位 (与内置 review_code 互补, 不冲突):
  - 内置 review_code: 主力, 可选叠加 LLM critic 语义增强 (需商汤 key)。
  - 本 MCP code_review: 纯静态、确定性、无网无 key 也能跑, 作为「自动交付闭环」里
    review 步的兜底/可演示实现 —— agent 在测试全绿后调它做 VERDICT 判定, 闭环即使在
    离线/无 key 环境也能完整跑通 (改-跑-评)。

运行: python -m lingmengwork.tools.mcp_review_server
"""
import os
import sys
import io
import json
import re
import tempfile

PROTOCOL_VERSION = "2024-11-05"

# 静态评审规则权重 (与 tools/review.py 保持一致, 便于结果对齐)
_W_HIGH = 15   # 高: 语法错误 / 裸 except 吞异常
_W_MED = 8     # 中: TODO/FIXME 占位 / import * / 疑似空实现
_W_LOW = 3     # 低: print 调试残留 / 超长行 / 裸 ... 占位


def _fix_enc(s):
    """修复 Windows 下经 argv 传入的中文路径偶发 mojibake (utf-8 字节被误当 latin-1)。"""
    if not s:
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except Exception:
        return s


def _check_syntax(text, filename):
    """用 py_compile 检查 .py 语法, 返回 (ok, issue_str_or_None)。"""
    if not (filename or "").endswith(".py"):
        return True, None
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(text)
            tmp = f.name
        import py_compile
        py_compile.compile(tmp, doraise=True)
        return True, None
    except Exception as e:
        msg = str(e)
        m = re.search(r"line (\d+)", msg)
        ln = m.group(1) if m else "?"
        return False, "语法错误 (line %s): %s" % (ln, msg.splitlines()[0][:80])
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


def _scan_rules(text, filename):
    """通用规则扫描, 返回 [(severity, desc), ...]。severity ∈ 高/中/低。"""
    issues = []
    lines = text.splitlines()
    in_py = (filename or "").endswith(".py")
    for i, line in enumerate(lines, 1):
        s = line.strip()
        # 用 re.search 而非 re.match: 代码通常带缩进, start-anchored 会漏掉缩进后的问题行
        if re.search(r"except\s*:\s*(#|$)", line):
            issues.append(("高", "裸 except 吞异常 (line %d)——至少捕获 Exception 并日志化" % i))
        if re.search(r"from\s+\S+\s+import\s+\*", line) or re.search(r"import\s+\*", line):
            issues.append(("中", "import * 污染命名空间 (line %d)" % i))
        if re.search(r"\b(TODO|FIXME|XXX)\b", line):
            issues.append(("中", "含未完成占位标记 (line %d)" % i))
        if re.search(r"(def|class)\s+\w+", line):
            nxt = lines[i] if i < len(lines) else ""
            nxt2 = lines[i + 1] if i + 1 < len(lines) else ""
            body = nxt.strip()
            stub = False
            if body in ("pass", "..."):
                stub = True
            elif body.startswith('"""') or body.startswith("'''") or body.startswith('"') or body.startswith("'"):
                if nxt2.strip() in ("pass", "..."):
                    stub = True
            if stub:
                issues.append(("中", "疑似空实现 (line %d: %s)" % (i, s[:40])))
        if in_py and re.search(r"\bprint\s*\(", line) and not re.search(r"#\s*noqa", line):
            issues.append(("低", "含 print 调试输出 (line %d)——确认是否需移除" % i))
        if len(line) > 120:
            issues.append(("低", "行超 120 字符 (line %d, %d 字符)" % (i, len(line))))
    return issues


def _static_review(text, filename=""):
    """零依赖静态评审, 返回 dict: {verdict, score, issues, summary}。"""
    ok, syn_err = _check_syntax(text, filename)
    issues = []
    if not ok and syn_err:
        issues.append(("高", syn_err))
    issues.extend(_scan_rules(text, filename))

    score = 100
    for sev, _ in issues:
        if sev == "高":
            score -= _W_HIGH
        elif sev == "中":
            score -= _W_MED
        else:
            score -= _W_LOW
    score = max(0, min(100, score))

    has_high = any(sev == "高" for sev, _ in issues)
    verdict = "revise" if (has_high or score < 75) else "approve"
    summary = "静态评审完成: 通过语法检查" if ok else "静态评审发现语法错误"
    if issues:
        summary += ", 命中 %d 项规则 (高 %d / 中 %d / 低 %d)" % (
            len(issues),
            sum(1 for s, _ in issues if s == "高"),
            sum(1 for s, _ in issues if s == "中"),
            sum(1 for s, _ in issues if s == "低"),
        )
    else:
        summary += ", 无规则告警"
    return {"verdict": verdict, "score": score, "issues": issues, "summary": summary}


def _code_review(args):
    target = (args or {}).get("target") or ""
    if not target:
        return "[code-review]\nVERDICT: revise\nSCORE: 0\nISSUES:\n- [高] 缺少 target (文件路径或代码片段)\nSUMMARY: 输入为空"

    # 解析 target: 路径存在则读, 否则当代码片段/diff 文本
    filename = ""
    text = target
    try:
        if os.path.isfile(target):
            filename = os.path.basename(target)
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
    except Exception:
        pass

    st = _static_review(text, filename)
    issue_lines = ["- [%s] %s" % (sev, desc) for sev, desc in st["issues"]]
    out = [
        "[code-review]",
        "VERDICT: %s" % st["verdict"],
        "SCORE: %d" % st["score"],
        "ISSUES:",
    ]
    out.extend(issue_lines if issue_lines else ["- (无)"])
    out.append("SUMMARY: " + st["summary"])
    out.append("(评审来源: 静态评审 MCP 版 code_review · 无 LLM 依赖)")
    return "\n".join(out)


TOOLS = [
    {
        "name": "code_review",
        "description": "零依赖静态代码评审 (无需 LLM): 对文件路径/代码片段/diff 做 py_compile 语法检查 + 规则扫描, 返回 [code-review] 块 (VERDICT approve/revise · SCORE 0-100 · ISSUES · SUMMARY)。作为「自动交付闭环」review 步的无 key 兜底实现; 与内置 review_code 格式完全一致, 前端趋势图可直接解析。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "文件路径 (存在则读取) 或 代码片段/diff 文本"},
            },
            "required": ["target"],
        },
    },
]


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        _send({
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lingmeng-review", "version": "1.0"},
            },
        })
    elif method == "notifications/initialized":
        return
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = msg.get("params", {}) or {}
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}
        try:
            if name == "code_review":
                text = _code_review(arguments)
                is_error = False
            else:
                text = "unknown tool: %s" % name
                is_error = True
            _send({
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
            })
        except Exception as e:
            _send({
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"content": [{"type": "text", "text": "server error: %s" % e}], "isError": True},
            })
    else:
        if mid is not None:
            _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}})


def main():
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            try:
                _handle(msg)
            except Exception as e:
                sys.stderr.write("review server handle error: %s\n" % e)
    except Exception as e:
        sys.stderr.write("review server stdin loop exited: %s\n" % e)


if __name__ == "__main__":
    main()
