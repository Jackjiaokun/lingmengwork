"""会话续跑 (server 端真续跑) 测试: 验证工具执行态回灌与活体/磁盘两种恢复路径。"""
import uuid

from lingmengwork.config import DEFAULTS
from lingmengwork.llm.client import MockClient
from lingmengwork.tools.registry import build_registry
from lingmengwork.agent.loop import AgentLoop
from lingmengwork.agent.session import delete_session as sess_del, save_session


def _fresh_reg(tmp_path):
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path))
    reg.clients = {"mock": MockClient(model="mock")}
    return reg


def test_session_resume_reinjects_tool_state(tmp_path):
    """首轮已跑过工具 -> 落盘 -> 新 loop 水合 -> 工具结果(执行态)被重新注入且续轮可见。"""
    reg = _fresh_reg(tmp_path)
    loop = AgentLoop(MockClient(model="mock"), reg, DEFAULTS)
    # 模拟首轮已发生工具调用 (工具结果以 role=user 的 [tool result: ...] 文本存储)
    loop.messages.append({"role": "user", "content": "写一个加法函数"})
    loop.messages.append({"role": "assistant", "content": "```tool\n{\"name\":\"run_command\",\"arguments\":{\"cmd\":\"echo def add\"}}\n```"})
    loop.messages.append({"role": "user", "content": "[tool result: run_command]\ndef add(a,b): return a+b"})
    loop.messages.append({"role": "assistant", "content": "已创建 add 函数。"})
    sid = loop.save_session(base_dir=str(tmp_path))
    try:
        loop2 = AgentLoop(MockClient(model="mock"), reg, DEFAULTS)
        ok = loop2.load_session_messages(sid)
        assert ok is True
        assert loop2.messages[0]["role"] == "system"  # system 保留在最前
        # 工具执行态被重新注入
        assert any("[tool result: run_command]" in m["content"] for m in loop2.messages)
        # 续跑: 再发一条, 历史(含工具结果)仍对模型可见
        loop2.run("再确认下 add 是否正确", on_event=lambda *a, **k: None)
        contents = [m["content"] for m in loop2.messages]
        assert any("再确认下 add 是否正确" in c for c in contents)
        assert any("[tool result: run_command]" in c for c in contents)
    finally:
        sess_del(sid)


def test_acquire_session_reuses_live_loop(tmp_path):
    """同一 session_id 两次请求 -> 复用同一活体 AgentLoop, 执行态(消息/令牌)跨轮保留。"""
    from lingmengwork.web import server as web
    client = MockClient(model="mock")
    reg = _fresh_reg(tmp_path)
    sid = "test-reuse-" + uuid.uuid4().hex[:8]
    try:
        loop1, _, hydrated1 = web.acquire_session(sid, client, reg, DEFAULTS, "mock")
        assert hydrated1 is False
        loop1.messages.append({"role": "user", "content": "首轮问题"})
        loop1.messages.append({"role": "assistant", "content": "首轮回答"})
        loop2, _, hydrated2 = web.acquire_session(sid, client, reg, DEFAULTS, "mock")
        assert loop2 is loop1          # 复用同一对象
        assert hydrated2 is False
        assert any(m["content"] == "首轮问题" for m in loop2.messages)
        # 令牌计数跨轮连续(活体 loop 不重置)
        before = loop1.token_stats()["est_total_tokens"]
        loop1.est_input_chars += 100
        assert loop2.token_stats()["est_total_tokens"] == before + int(100 / loop2._CHAR_PER_TOKEN)
    finally:
        web._SESSION_LOOPS.pop(sid, None)
        web._SESSION_LOCKS.pop(sid, None)
        sess_del(sid)


def test_acquire_session_hydrates_from_disk(tmp_path):
    """内存映射无该会话(如进程重启) -> 从磁盘水合, 历史消息含续跑所需上下文。"""
    from lingmengwork.web import server as web
    client = MockClient(model="mock")
    reg = _fresh_reg(tmp_path)
    sid = "test-hydrate-" + uuid.uuid4().hex[:8]
    try:
        loop1, _, _ = web.acquire_session(sid, client, reg, DEFAULTS, "mock")
        loop1.messages.append({"role": "user", "content": "持久化问题"})
        save_session(loop1.session_id, loop1.messages, model="mock", provider="mock", base_dir=str(tmp_path))
        # 模拟进程重启: 清空内存映射
        web._SESSION_LOOPS.pop(sid, None)
        web._SESSION_LOCKS.pop(sid, None)
        loop2, _, hydrated = web.acquire_session(sid, client, reg, DEFAULTS, "mock")
        assert hydrated is True
        assert any(m["content"] == "持久化问题" for m in loop2.messages)
    finally:
        web._SESSION_LOOPS.pop(sid, None)
        web._SESSION_LOCKS.pop(sid, None)
        sess_del(sid)
