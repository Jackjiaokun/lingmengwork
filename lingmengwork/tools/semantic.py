"""语义检索: 零依赖本地向量近似召回 (TF-IDF 向量 + 余弦相似度)。

对标 Sourcegraph Cody / Cursor 的「AI 找代码」能力, 但零外部依赖、本地优先:
- 对代码 + 文档建 TF-IDF 索引, 持久化到 <root>/.lmw_index/index.json (按 mtime 增量复用)
- 查询时把 query 与每个索引块算余弦相似度, 返回 top-k 最相关片段
- 中文: 逐字 unigram + 相邻 bigram (c: 前缀隔离), 支持中文语义近似召回
- 英文/数字: 词级 token

用途: 当你只知道「要找做 X 的代码/文档」而不知道精确符号名时, 用本工具
比 grep/symbol_search 更快命中意图。可与 read_file/grep 接力精确定位。
"""
import fnmatch
import json
import math
import os
import re
import time
from collections import Counter

_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "dist", "build", ".venv", "venv",
    ".workbuddy", "target", ".idea", ".vs", ".tox", ".lmw_index",
}
_CODE_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cpp",
    ".cc", ".c", ".h", ".hpp", ".rb", ".php", ".cs", ".kt", ".swift", ".scala", ".sh",
}
_DOC_EXT = {".md", ".markdown", ".txt", ".rst"}

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[一-鿿]")

_MIN_DF = 2  # 文档频率阈值: 仅出现在 1 个 chunk 的词视为噪声, 不入索引


def _tokenize(text):
    """英文/数字词 (>=2 长度) + 中文逐字 unigram 与相邻 bigram (c: 前缀隔离)。"""
    toks = []
    for m in _WORD_RE.findall(text.lower()):
        if len(m) >= 2:
            toks.append(m)
    cjk = _CJK_RE.findall(text)
    for i, ch in enumerate(cjk):
        toks.append("c:" + ch)
        if i + 1 < len(cjk):
            toks.append("c:" + ch + cjk[i + 1])
    return toks


def _chunk_file(path, size=40, overlap=8):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return []
    out = []
    n = len(lines)
    i = 0
    while i < n:
        seg = lines[i:i + size]
        text = "".join(seg)
        if text.strip():
            out.append((i + 1, text))  # 1-based 起始行
        step = max(1, size - overlap)
        i += step
    return out


def _collect(root, scope, glob_filter):
    if scope == "code":
        exts = _CODE_EXT
    elif scope == "docs":
        exts = _DOC_EXT
    else:
        exts = _CODE_EXT | _DOC_EXT
    files = []
    for dp, dirs, fns in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in exts:
                continue
            fp = os.path.join(dp, fn)
            if glob_filter and not fnmatch.fnmatch(fn, glob_filter) and not fnmatch.fnmatch(fp, glob_filter):
                continue
            files.append(fp)
    return files


def _build_index(root, scope, glob_filter, min_df=_MIN_DF):
    files = _collect(root, scope, glob_filter)
    mtimes = {}
    raw = []  # (path, start_line, text)
    for fp in files:
        try:
            mtimes[fp] = os.path.getmtime(fp)
        except Exception:
            mtimes[fp] = 0.0
        for sl, txt in _chunk_file(fp):
            raw.append((fp, sl, txt))
    df = {}
    for (_p, _s, txt) in raw:
        for w in Counter(_tokenize(txt)):
            df[w] = df.get(w, 0) + 1
    n = len(raw) or 1
    # 平滑 IDF: 仅保留 df>=min_df 的词, 噪声词不入索引
    vocab = {w: math.log((1 + n) / (1 + df[w])) + 1.0 for w in df if df[w] >= min_df}
    return {
        "root": os.path.abspath(root),
        "built": time.time(),
        "scope": scope,
        "n_chunks": len(raw),
        "vocab": vocab,
        "chunks": [{"p": p, "s": s, "t": t} for (p, s, t) in raw],
        "mtimes": {p: mtimes[p] for p in mtimes},
    }


def _index_file(root):
    return os.path.join(root, ".lmw_index", "index.json")


def _load_or_build(root, scope, glob_filter, rebuild):
    """读已存索引并增量校验 (mtime), 命中则复用; 否则全量重建并落盘。"""
    ip = _index_file(root)
    if not rebuild and os.path.isfile(ip):
        try:
            with open(ip, "r", encoding="utf-8") as f:
                idx = json.load(f)
            if idx.get("root") == os.path.abspath(root) and idx.get("scope") == scope:
                stale = False
                for p, mt in idx.get("mtimes", {}).items():
                    try:
                        if os.path.getmtime(p) != mt:
                            stale = True
                            break
                    except Exception:
                        stale = True
                        break
                if not stale:
                    return idx, False  # (index, used_cache)
        except Exception:
            pass
    idx = _build_index(root, scope, glob_filter)
    try:
        os.makedirs(os.path.dirname(ip), exist_ok=True)
        with open(ip, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
    except Exception:
        pass
    return idx, True


def _search(index, query, top_k):
    vocab = index["vocab"]
    qt = Counter(_tokenize(query))
    if not qt:
        return []
    qv = {w: (c / len(qt)) * vocab.get(w, 1.0) for w, c in qt.items()}
    qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0
    scored = []
    for ch in index["chunks"]:
        tf = Counter(_tokenize(ch["t"]))
        dn = math.sqrt(sum((c / len(tf) * vocab.get(w, 1.0)) ** 2 for w, c in tf.items())) or 1.0
        dot = 0.0
        for w, c in tf.items():
            qw = qv.get(w)
            if qw:
                dot += (c / len(tf)) * vocab.get(w, 1.0) * qw
        if dot > 0:
            scored.append((dot / (qn * dn), ch))
    scored.sort(key=lambda x: -x[0])
    return scored[:top_k]


def semantic_search(args, ctx):
    root = ctx.get("cwd") or (str(ctx["roots"][0]) if ctx.get("roots") else ".")
    query = (args.get("query") or "").strip()
    if not query:
        return "[semantic_search] 需提供 query 参数 (要找的代码/文档意图, 如 '数据库连接池配置' 或 'parse config file')"
    top_k = int(args.get("top_k") or 8)
    scope = (args.get("scope") or "all").lower()
    if scope not in ("code", "docs", "all"):
        scope = "all"
    glob_filter = (args.get("glob") or "").strip() or None
    rebuild = args.get("rebuild") in (True, "true", "1")
    try:
        idx, built_fresh = _load_or_build(root, scope, glob_filter, rebuild)
    except Exception as e:
        return f"[semantic_search] 建索引失败: {e}"
    hits = _search(idx, query, top_k)
    if not hits:
        return (f"[semantic_search] 未召回相关片段 (索引 {idx['n_chunks']} 块, "
                f"vocab {len(idx['vocab'])} 词)。尝试换关键词, 或 scope=docs/code 限定范围。")
    lines = [f"[semantic_search] 命中 {len(hits)} 个相关片段 (语义近似召回, 索引 {idx['n_chunks']} 块"
             f"{'·已增量复用' if not built_fresh else '·已重建'}):"]
    for score, ch in hits:
        rel = os.path.relpath(ch["p"], root).replace(os.sep, "/")
        snippet = ch["t"].strip()[:240].replace("\n", " ⏎ ")
        lines.append(f"\n{rel}:{ch['s']}  score={score:.3f}")
        lines.append(f"  {snippet}")
    lines.append("\n→ 用 read_file 查看完整上下文, 或 grep 精确定位。")
    return "\n".join(lines)
