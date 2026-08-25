"""sqlite MCP 服务器单测 (纯函数逻辑, 不启子进程)。"""
import os

import pytest

from lingmengwork.tools import mcp_sqlite_server as m


@pytest.fixture
def dbpath(tmp_path):
    # 允许根隔离通过: 把 ROOT 设为临时目录 (ASCII)
    m.ROOT = str(tmp_path)
    return str(tmp_path / "t.db")


def test_create_insert_select(dbpath):
    cr = m._db_query({"db_path": dbpath, "sql": "CREATE TABLE u(id INTEGER PRIMARY KEY, name TEXT)"})
    assert "已执行写操作" in cr
    m._db_query({"db_path": dbpath, "sql": "INSERT INTO u(name) VALUES('alice'),('bob')"})
    rows = m._db_query({"db_path": dbpath, "sql": "SELECT * FROM u"})
    assert "alice" in rows and "bob" in rows


def test_list_tables(dbpath):
    m._db_query({"db_path": dbpath, "sql": "CREATE TABLE t1(x)"})
    m._db_query({"db_path": dbpath, "sql": "CREATE TABLE t2(y)"})
    tbl = m._db_list_tables({"db_path": dbpath})
    assert "t1" in tbl and "t2" in tbl


def test_query_limit(dbpath):
    m._db_query({"db_path": dbpath, "sql": "CREATE TABLE u(id INTEGER PRIMARY KEY, name TEXT)"})
    vals = ", ".join("('n%d')" % i for i in range(10))
    m._db_query({"db_path": dbpath, "sql": "INSERT INTO u(name) VALUES %s" % vals})
    rows = m._db_query({"db_path": dbpath, "sql": "SELECT * FROM u", "limit": 3})
    # 返回行数不超过 limit
    body = rows.split("返回")[1]
    assert "3 行" in body


def test_denied_outside_root(tmp_path):
    m.ROOT = str(tmp_path)
    outside = os.path.join(os.path.dirname(tmp_path), "outside.db")
    res = m._db_query({"db_path": outside, "sql": "SELECT 1"})
    assert ("路径超出" in res) or ("失败" in res)


def test_missing_sql(dbpath):
    assert "缺少 sql" in m._db_query({"db_path": dbpath})
