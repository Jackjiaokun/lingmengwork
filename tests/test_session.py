import os
from pathlib import Path

from lingmengwork.agent.session import save_session, load_session, list_sessions, delete_session, new_session_id
from lingmengwork.config import DEFAULTS
from lingmengwork.tools.registry import build_registry
from lingmengwork.agent.loop import AgentLoop
from lingmengwork.llm.client import MockClient


def test_session_save_load_roundtrip(tmp_path):
    # 用临时 HOME 隔离会话目录
    os.environ["USERPROFILE"] = str(tmp_path)  # Windows
    os.environ["HOME"] = str(tmp_path)         # Linux/mac
    sid = new_session_id()
    save_session(sid, [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    data = load_session(sid)
    assert data is not None
    assert len(data["messages"]) == 2
    assert data["messages"][0]["content"] == "hi"


def test_loop_save_and_resume(tmp_path):
    os.environ["USERPROFILE"] = str(tmp_path)
    os.environ["HOME"] = str(tmp_path)
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path))
    client = MockClient(model="t")

    # 第一轮: 跑一次并保存
    loop1 = AgentLoop(client, reg, DEFAULTS)
    client.set_script(["好的", "完成"])
    loop1.run("写个函数")
    sid = loop1.save_session()
    assert sid

    # 第二轮: 恢复会话, 历史应保留
    loop2 = AgentLoop(client, reg, DEFAULTS)
    ok = loop2.load_session_messages(sid)
    assert ok
    # system 在最前, 之后是历史 user/assistant
    roles = [m["role"] for m in loop2.messages]
    assert roles[0] == "system"
    assert "user" in roles and "assistant" in roles


def test_list_sessions_and_delete(tmp_path):
    os.environ["USERPROFILE"] = str(tmp_path)
    os.environ["HOME"] = str(tmp_path)
    sid = new_session_id()
    save_session(sid, [{"role": "user", "content": "test summary here"}])
    items = list_sessions()
    assert any(it["id"] == sid for it in items)
    assert any("test summary" in it["summary"] for it in items)
    assert delete_session(sid)
    assert load_session(sid) is None
