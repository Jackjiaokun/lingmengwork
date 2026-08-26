"""错误日志与汇总 (Error Log): 工作区级别的运行错误归集。

所有运行期错误(Web API 异常、对话循环异常、工具执行失败等)统一归集到
``logs/errors/`` 目录::

    logs/errors/
        errors.md              人类可读的错误流水(追加写入)
        <ts>_<type>.json      单条结构化记录

提供 record() 记录一条错误, list_errors() 列举, summary() 做错误汇总
(按类型/来源聚合 + 高频错误 TopN)。均为规则实现, 不依赖 LLM。
"""
import json
import os
import time
from datetime import datetime
from collections import Counter

ERRORS_DIR = os.path.join("logs", "errors")
SEVERITIES = ("debug", "info", "warn", "error", "fatal")


def _root(base_dir=None):
    return os.path.join(base_dir or os.getcwd(), ERRORS_DIR)


def _ensure(base_dir=None):
    d = _root(base_dir)
    os.makedirs(d, exist_ok=True)
    return d


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def record(base_dir, error_type, message, source="", detail="", severity="error"):
    """记录一条错误, 返回记录 dict。任何异常都不向外抛出(静默失败)。"""
    try:
        d = _ensure(base_dir)
        severity = (severity or "error").strip().lower()
        if severity not in SEVERITIES:
            severity = "error"
        rec = {
            "ts": _now(),
            "type": (error_type or "unknown").strip() or "unknown",
            "severity": severity,
            "source": (source or "").strip(),
            "message": (message or "").strip(),
            "detail": (detail or "").strip(),
        }
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fn = "%s_%s.json" % (stamp, rec["type"].replace("/", "_")[:24])
        with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        # 追加到流水
        with open(os.path.join(d, "errors.md"), "a", encoding="utf-8") as f:
            f.write("\n- [%s] **%s** `%s` — %s\n" % (rec["severity"].upper(), rec["type"], rec["source"], rec["message"]))
            if rec["detail"]:
                f.write("  - 详情: %s\n" % rec["detail"][:500])
        return rec
    except Exception:
        return {"ts": _now(), "type": error_type, "severity": severity if 'severity' in dir() else "error",
                "source": source, "message": message, "detail": detail, "_write_failed": True}


def list_errors(base_dir=None, limit=200):
    d = _root(base_dir)
    items = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                rec = json.loads(open(os.path.join(d, fn), encoding="utf-8").read())
            except Exception:
                continue
            rec["file"] = fn
            items.append(rec)
            if len(items) >= limit:
                break
    return {"errors": items, "total": len(items)}


def summary(base_dir=None):
    """错误汇总: 总数/按严重度/按类型/按来源 聚合 + 高频错误 TopN。"""
    d = _root(base_dir)
    items = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            try:
                items.append(json.loads(open(os.path.join(d, fn), encoding="utf-8").read()))
            except Exception:
                continue
    total = len(items)
    by_sev = Counter(i.get("severity", "error") for i in items)
    by_type = Counter(i.get("type", "unknown") for i in items)
    by_source = Counter(i.get("source", "") or "(未标注)" for i in items)
    # 高频错误: 按 (type + message 前 60 字) 聚类
    msg_key = Counter((i.get("type", "unknown"), (i.get("message", "") or "")[:60]) for i in items)
    top = [{"type": k[0], "message_preview": k[1], "count": v} for k, v in msg_key.most_common(10)]
    recent = items[:8]
    md = ["# 错误汇总报告", "",
          "> 生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
          "## 📊 总览", "",
          "- 错误总数: **%d**" % total,
          "- 严重度分布: " + ", ".join("%s=%d" % (k, by_sev[k]) for k in sorted(by_sev, key=lambda x: -by_sev[x])) or "无",
          "", "## 🏷 按类型", ""]
    md += ["- %s: %d" % (k, v) for k, v in by_type.most_common()] or ["(无)"]
    md += ["", "## 📡 按来源", ""]
    md += ["- %s: %d" % (k, v) for k, v in by_source.most_common()] or ["(无)"]
    md += ["", "## 🔥 高频错误 Top10", ""]
    md += ["%d. [%s] %s — ×%d" % (i + 1, t["type"], t["message_preview"], t["count"]) for i, t in enumerate(top)] or ["(无)"]
    md += ["", "## 🕘 最近错误", ""]
    md += ["- [%s] %s `%s`: %s" % (r.get("severity", "error").upper(), r.get("type", ""), r.get("source", ""), (r.get("message", "") or "")[:120]) for r in recent] or ["(无)"]
    return {
        "ok": True,
        "total": total,
        "by_severity": dict(by_sev),
        "by_type": dict(by_type),
        "by_source": dict(by_source),
        "top": top,
        "recent": recent,
        "markdown": "\n".join(md),
    }
