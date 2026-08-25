"""批次5: 语义检索 (semantic_search) 零依赖向量近似召回回归测试。

纯算法, 不依赖 LLM。覆盖: tokenize / 英文召回 / 中文召回 / 增量复用 / 强制重建 / 范围过滤 / 空查询。
"""
import os
import shutil
import tempfile

from lingmengwork.tools import semantic


def _mkrepo():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "db_pool.py"), "w", encoding="utf-8") as f:
        f.write(
            "# 数据库连接池配置 / database connection pool config\n"
            "class ConnectionPool:\n"
            "    def __init__(self, max_conn=10):\n"
            "        self.max_conn = max_conn\n"
            "    def acquire(self):\n"
            "        return 'conn'\n"
        )
    with open(os.path.join(d, "readme.md"), "w", encoding="utf-8") as f:
        f.write(
            "# 项目说明\n"
            "本模块负责用户认证与权限校验。数据库连接池配置在此初始化。"
            "database connection pool is used here.\n"
        )
    return d


def _ctx(d):
    return {"cwd": d}


def test_tokenize_mixed():
    toks = semantic._tokenize("Database connect 数据库连接")
    assert "database" in toks
    assert "connect" in toks
    assert "c:数" in toks          # 中文 unigram
    assert "c:数据" in toks        # 中文 bigram


def test_english_query_hits():
    d = _mkrepo()
    try:
        out = semantic.semantic_search({"query": "database connection pool"}, _ctx(d))
        assert "semantic_search" in out
        assert "db_pool.py" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_chinese_query_hits():
    d = _mkrepo()
    try:
        out = semantic.semantic_search({"query": "数据库连接池配置"}, _ctx(d))
        assert "db_pool.py" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_incremental_cache_reuse():
    d = _mkrepo()
    try:
        idx1, fresh1 = semantic._load_or_build(d, "all", None, False)
        assert fresh1 is True              # 首次必须建
        idx2, fresh2 = semantic._load_or_build(d, "all", None, False)
        assert fresh2 is False             # mtime 未变 -> 复用
        assert idx1["n_chunks"] == idx2["n_chunks"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rebuild_forces():
    d = _mkrepo()
    try:
        _, fresh1 = semantic._load_or_build(d, "all", None, False)
        assert fresh1 is True
        _, fresh2 = semantic._load_or_build(d, "all", None, True)
        assert fresh2 is True              # rebuild 强制重建
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_scope_docs_only():
    d = _mkrepo()
    try:
        idx = semantic._build_index(d, "docs", None)
        paths = {c["p"] for c in idx["chunks"]}
        assert all(p.endswith((".md", ".txt", ".rst", ".markdown")) for p in paths)
        assert not any("db_pool.py" in p for p in paths)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_empty_query_rejected():
    d = _mkrepo()
    try:
        out = semantic.semantic_search({"query": ""}, _ctx(d))
        assert "需提供 query" in out
    finally:
        shutil.rmtree(d, ignore_errors=True)
