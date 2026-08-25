from lingmengwork.llm.client import MockClient, OllamaClient, OpenAIClient, build_client
from lingmengwork.config import DEFAULTS


def test_mock_client_deterministic():
    c = MockClient()
    c.set_script(["你好", "你好"])
    assert c.chat([{"role": "user", "content": "hi"}]) == "你好"
    # 流式
    assert list(c.chat([{"role": "user", "content": "hi"}], stream=True)) == ["你好"]


def test_build_client_mock():
    c = build_client("mock", cfg=DEFAULTS)
    assert isinstance(c, MockClient)


def test_build_client_ollama_openai():
    c1 = build_client("ollama", cfg=DEFAULTS)
    c2 = build_client("openai", cfg=DEFAULTS)
    assert isinstance(c1, OllamaClient)
    assert isinstance(c2, OpenAIClient)


def test_ollama_client_construct():
    c = OllamaClient(base_url="http://127.0.0.1:11434", model="qwen2.5:7b")
    assert c.model == "qwen2.5:7b"
    assert c.base_url == "http://127.0.0.1:11434"


def test_openai_base_url_normalization():
    c = OpenAIClient(base_url="https://api.deepseek.com/v1", model="deepseek-chat", api_key="x")
    assert c.model == "deepseek-chat"


def test_mock_agentic_emits_tool_call():
    c = MockClient()
    out = c.chat([{"role": "user", "content": "列出当前目录的文件"}])
    assert "```tool" in out and '"name":"list_dir"' in out
    # 第二轮回灌工具结果 -> 给出总结, 不再调工具
    summary = c.chat([{"role": "user", "content": "[tool result: list_dir]\n..."}])
    assert "[tool result" not in summary
    assert "任务完成" in summary


def test_mock_agentic_run_command():
    c = MockClient()
    out = c.chat([{"role": "user", "content": "运行一条命令"}])
    assert '"name":"run_command"' in out


def test_mock_scripted_beats_agentic():
    c = MockClient()
    c.set_script(["直接文本回复"])
    assert c.chat([{"role": "user", "content": "列出目录"}]) == "直接文本回复"
