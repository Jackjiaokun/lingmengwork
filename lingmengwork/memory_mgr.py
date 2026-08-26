"""记忆管理 (Memory): 工作区级别的长期记忆与每日工作日志 + 语义召回。

数据落在工作区主根的 ``.lmw_memory/`` 目录:

    .lmw_memory/
        MEMORY.md           长期项目记忆(用户可编辑/追加)
        facts.jsonl         结构化关键事实条目(供语义召回)
        daily/
            YYYY-MM-DD.md   每日工作日志(按段追加)

双模能力:
- **capture(text, llm_call)**: 从一段文本中抽取关键事实/决策/偏好(LLM 抽取,
  规则保底), 去重后写入 facts.jsonl 并回写 MEMORY.md; 供「智能体记住项目」使用。
- **retrieve(query, k)**: 跨 MEMORY.md + facts.jsonl + 每日日志做语义召回,
  返回与查询最相关的若干记忆片段(关键词重叠打分 + 可选 LLM 重排)。

与对话解耦、可长期留存; 智能体运行时亦可读取这些文件以「记住」项目事实。
"""
import os
import re
import json
from datetime import datetime

MEMORY_DIR = ".lmw_memory"
MEMORY_FILE = "MEMORY.md"
FACTS_FILE = "facts.jsonl"
DAILY_DIR = "daily"

_DEFAULT_MEMORY = """# 项目长期记忆 (MEMORY.md)

> 在此记录项目的长期事实、约定与决策。智能体会在工作区启动时读取本文件。
> 用「更新记忆」按钮可直接向本文件追加一条带时间戳的笔记。

## 关键约定
- (待补充)

## 架构要点
- (待补充)

## 开放问题
- (待补充)
"""


def _root(base_dir=None):
    return os.path.join(base_dir or os.getcwd(), MEMORY_DIR)


def _ensure(base_dir=None):
    root = _root(base_dir)
    os.makedirs(os.path.join(root, DAILY_DIR), exist_ok=True)
    mp = os.path.join(root, MEMORY_FILE)
    if not os.path.isfile(mp):
        with open(mp, "w", encoding="utf-8") as f:
            f.write(_DEFAULT_MEMORY)
    return root


def read_memory(base_dir=None):
    root = _ensure(base_dir)
    mp = os.path.join(root, MEMORY_FILE)
    try:
        return open(mp, encoding="utf-8").read()
    except Exception:
        return _DEFAULT_MEMORY


def update_memory(base_dir, content):
    """整体覆盖 MEMORY.md。"""
    root = _ensure(base_dir)
    mp = os.path.join(root, MEMORY_FILE)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(content or "")
    return {"ok": True, "path": os.path.relpath(mp, base_dir or os.getcwd()), "bytes": len((content or "").encode("utf-8"))}


def append_memory(base_dir, text, title=""):
    """向 MEMORY.md 追加一条带时间戳的笔记, 返回追加后的全文。"""
    root = _ensure(base_dir)
    mp = os.path.join(root, MEMORY_FILE)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = "\n\n---\n### 📌 %s (%s)\n\n%s\n" % (title or "更新", ts, (text or "").strip() or "(空)")
    with open(mp, "a", encoding="utf-8") as f:
        f.write(block)
    return {"ok": True, "content": read_memory(base_dir)}


def list_logs(base_dir=None):
    root = _ensure(base_dir)
    d = os.path.join(root, DAILY_DIR)
    items = []
    for fn in sorted(os.listdir(d), reverse=True):
        if not fn.endswith(".md"):
            continue
        fp = os.path.join(d, fn)
        try:
            sz = os.path.getsize(fp)
        except Exception:
            sz = 0
        items.append({"date": fn[:-3], "file": fn, "size": sz})
    return {"logs": items}


def read_log(base_dir, date):
    root = _ensure(base_dir)
    fp = os.path.join(root, DAILY_DIR, "%s.md" % date)
    if not os.path.isfile(fp):
        return {"date": date, "content": "", "exists": False}
    try:
        return {"date": date, "content": open(fp, encoding="utf-8").read(), "exists": True}
    except Exception:
        return {"date": date, "content": "", "exists": False}


def append_log(base_dir, text, title="", date=None):
    """向指定日期(默认今天)的每日日志追加一段。"""
    root = _ensure(base_dir)
    date = date or datetime.now().strftime("%Y-%m-%d")
    fp = os.path.join(root, DAILY_DIR, "%s.md" % date)
    ts = datetime.now().strftime("%H:%M")
    block = "\n\n### %s · %s\n\n%s\n" % (title or "记录", ts, (text or "").strip() or "(空)")
    with open(fp, "a", encoding="utf-8") as f:
        f.write(block)
    return {"ok": True, "date": date, "content": read_log(base_dir, date)["content"]}


# =====================================================================
# 语义记忆: 事实抽取 (capture) + 召回 (retrieve)
# =====================================================================
_CAP_SYS = (
    "你是记忆抽取器。从用户/助手的对话片段中, 抽取「值得长期记住」的关键信息, 每行一条, "
    "格式为 `类型: 内容`。类型限定为: 约定 / 决策 / 偏好 / 事实 / 风险 / 账号(脱敏)。\n"
    "只输出抽取结果, 每行一条, 不要编号, 不要解释。无有价值信息则输出空。"
)
_FACT_RE = re.compile(r"^\s*(?:[-*]\s*)?(约定|决策|偏好|事实|风险|账号)\s*[:：]\s*(.+?)\s*$")


def _rule_extract(text):
    """规则兜底: 命中强信号关键词则判定为事实。"""
    out = []
    low = text.lower()
    sig = [("决策", "决定采用"), ("决策", "选择方案"), ("决策", "采用"), ("事实", "报错"),
           ("事实", "根因"), ("事实", "实现"), ("约定", "约定"), ("偏好", "偏好"),
           ("偏好", "喜欢"), ("风险", "风险"), ("风险", "注意")]
    for kind, kw in sig:
        if kw in low:
            snippet = text.strip().replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "…"
            out.append("%s: %s" % (kind, snippet))
            break
    return out


def _parse_facts(raw):
    out = []
    for line in (raw or "").splitlines():
        m = _FACT_RE.match(line)
        if m:
            out.append({"type": m.group(1), "text": m.group(2).strip()})
    return out


def capture(base_dir, text, llm_call=None):
    """从文本抽取关键事实, 去重后写入 facts.jsonl 并回写 MEMORY.md。

    返回 {ok, captured:[...], total}。无 llm_call 时走规则兜底。
    """
    root = _ensure(base_dir)
    facts_path = os.path.join(root, FACTS_FILE)
    # 去重集合(基于原文小写)
    existing = set()
    if os.path.isfile(facts_path):
        try:
            with open(facts_path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rec = json.loads(ln)
                        existing.add((rec.get("type"), (rec.get("text") or "").lower()))
                    except Exception:
                        pass
        except Exception:
            pass

    if llm_call:
        raw = _ask_llm(llm_call, _CAP_SYS, "待抽取文本:\n" + text[:6000])
        facts = _parse_facts(raw) if raw else []
    else:
        facts = []
    if not facts:
        facts = [{"type": t, "text": x} for (t, x) in
                 [f.split(": ", 1) for f in _rule_extract(text)]]

    captured = []
    for fc in facts:
        key = (fc.get("type"), (fc.get("text") or "").lower())
        if not fc.get("text") or key in existing:
            continue
        existing.add(key)
        rec = {"type": fc.get("type", "事实"), "text": fc["text"],
               "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
        with open(facts_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        captured.append(rec)
    # 回写 MEMORY.md (追加结构化区块)
    if captured:
        block = "\n\n---\n### 🧠 智能抽取 · %s\n\n" % datetime.now().strftime("%Y-%m-%d %H:%M")
        for c in captured:
            block += "- **%s**: %s\n" % (c["type"], c["text"])
        with open(os.path.join(root, MEMORY_FILE), "a", encoding="utf-8") as f:
            f.write(block)
    return {"ok": True, "captured": captured, "total": len(existing)}


def _ask_llm(llm_call, system, user):
    if not llm_call:
        return None
    try:
        r = llm_call(user, system=system)
        return r if isinstance(r, str) and r.strip() else None
    except Exception:
        return None


def _tokenize(s):
    # 简易中文/英文分词(按非词字符切), 用于重叠打分
    return set(w for w in re.split(r"[\s,，。、；;:.：:！!？?()（）\[\]【】\"'\"'/\\]+", (s or "").lower()) if len(w) >= 1)


def retrieve(base_dir, query, k=5):
    """语义召回: 跨 MEMORY.md + facts.jsonl + 每日日志检索与 query 最相关的片段。

    返回 {query, results:[{source, snippet, score}]}。
    """
    root = _ensure(base_dir)
    q_tokens = _tokenize(query)
    corpus = []  # (source, snippet)

    # facts.jsonl
    fp = os.path.join(root, FACTS_FILE)
    if os.path.isfile(fp):
        try:
            with open(fp, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rec = json.loads(ln)
                        corpus.append(("facts", "%s: %s" % (rec.get("type"), rec.get("text"))))
                    except Exception:
                        pass
        except Exception:
            pass
    # MEMORY.md 按段落
    mem = read_memory(base_dir)
    for para in re.split(r"\n\s*\n", mem):
        para = para.strip()
        if len(para) >= 6:
            corpus.append(("MEMORY.md", para))
    # 每日日志(最近 10 个)
    logs = list_logs(base_dir).get("logs", [])[:10]
    for lg in logs:
        c = read_log(base_dir, lg["date"]).get("content") or ""
        for para in re.split(r"\n\s*\n", c):
            para = para.strip()
            if len(para) >= 6:
                corpus.append(("log:%s" % lg["date"], para))

    scored = []
    for src, snip in corpus:
        s_tokens = _tokenize(snip)
        overlap = len(q_tokens & s_tokens)
        if overlap == 0:
            continue
        score = overlap / (len(q_tokens) or 1)
        scored.append({"source": src, "snippet": snip[:400], "score": round(score, 3)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"query": query, "results": scored[:k]}
