"""双 LLM 客户端: 本地 Ollama (原生 /api/chat) + 云端 OpenAI 兼容 + Mock 离线。

统一接口:
    client.chat(messages, *, stream=False, temperature=0.2)
      - stream=False -> str (完整回复)
      - stream=True  -> 迭代器, 逐块 yield str (用于 CLI/Web 流式)

messages 为 [{role, content}], role ∈ {system, user, assistant}。
工具结果以 user 角色文本回灌 (provider 无关, 兼容 Ollama/OpenAI/Mock)。
"""
import json
import logging
import os
import random
import re
import socket
import time
import urllib.error
import urllib.request

from ..config import load_config

_log = logging.getLogger("lmw.llm")

# 触发故障转移的网络层异常集合: 连接/超时/HTTP 错误均视为"模型未响应"。
_FAIL_EXC = (
    urllib.error.URLError,
    urllib.error.HTTPError,
    socket.timeout,
    TimeoutError,
    ConnectionError,
    OSError,
)

# 触发"退避重试"的瞬时 HTTP 状态: 限流/网关/过载。这类应先重试, 多次失败才降级故障转移。
_TRANSIENT_HTTP = frozenset({429, 500, 502, 503, 504, 529})


def _brief_err(e):
    return str(e) or type(e).__name__


def _backoff_sleep(attempt, base=0.5, cap=8.0, retry_after=None):
    """指数退避 + 全抖动; 服务端返回 Retry-After(秒) 时优先采用。"""
    if retry_after is not None:
        try:
            s = float(retry_after)
            if s >= 0:
                time.sleep(min(cap, s))
                return
        except (TypeError, ValueError):
            pass
    t = min(cap, base * (2 ** attempt))
    time.sleep(t / 2 + random.random() * t / 2)


class _Breaker:
    """单提供者的轻量断路器: 连续失败达阈值即开路(快速失败, 不拖垮整体),
    冷却后进入半开探测, 一次成功即复位。"""

    def __init__(self, threshold=5, cooldown=30.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at = 0.0

    def allow(self):
        if self.failures < self.threshold:
            return True
        return time.time() - self.opened_at >= self.cooldown  # 半开探测

    def record_fail(self):
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.time()

    def record_ok(self):
        self.failures = 0


def _http_post(url, data, headers, *, timeout=120, max_retries=2, backoff_base=0.5, breaker=None):
    """带重试/退避/Retry-After/断路器的 POST。

    返回已建立的响应对象(调用方负责读取与关闭)。
    - 非瞬时异常(如 400/401)直接抛出;
    - 瞬时异常(429/5xx/网络层)按 max_retries 退避重试, 且每次失败都累记断路器;
    - 断路器开路时抛 URLError, 让外层 FailoverClient 快速切到健康提供者。
    """
    if breaker is not None and not breaker.allow():
        raise urllib.error.URLError("circuit open: provider cooling down")
    last = None
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            r = urllib.request.urlopen(req, timeout=timeout)
            if breaker is not None:
                breaker.record_ok()
            return r
        except urllib.error.HTTPError as e:
            last = e
            if breaker is not None:
                breaker.record_fail()
            if e.code in _TRANSIENT_HTTP and attempt < max_retries:
                _log.warning("LLM 瞬时 HTTP %s, 第%d次退避重试", e.code, attempt + 1)
                _backoff_sleep(attempt, backoff_base, retry_after=e.headers.get("Retry-After"))
                continue
            raise
        except _FAIL_EXC as e:
            last = e
            if breaker is not None:
                breaker.record_fail()
            if attempt < max_retries:
                _log.warning("LLM 网络异常 %s, 第%d次退避重试", _brief_err(e), attempt + 1)
                _backoff_sleep(attempt, backoff_base)
                continue
            raise
    raise last or RuntimeError("LLM request failed")


class LLMClient:
    model = "base"

    def chat(self, messages, *, stream=False, temperature=0.2, timeout=120):
        raise NotImplementedError

    def is_available(self):
        return True

    def _post(self, url, payload, headers=None, timeout=120):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=timeout)


class OllamaClient(LLMClient):
    def __init__(self, base_url="http://127.0.0.1:11434", model="qwen2.5:7b", max_retries=2, backoff_base=0.5):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._breaker = _Breaker()

    def is_available(self):
        try:
            urllib.request.urlopen(self.base_url + "/", timeout=3)
            return True
        except Exception:
            return False

    def chat(self, messages, *, stream=False, temperature=0.2, timeout=120):
        url = self.base_url + "/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": temperature},
        }
        if stream:
            return self._stream_ollama(url, payload, timeout=timeout)
        r = _http_post(url, json.dumps(payload).encode(),
                       {"Content-Type": "application/json"},
                       timeout=timeout, max_retries=self.max_retries,
                       backoff_base=self.backoff_base, breaker=self._breaker)
        with r:
            obj = json.loads(r.read().decode())
        return obj.get("message", {}).get("content", "")

    def _stream_ollama(self, url, payload, timeout=120):
        r = _http_post(url, json.dumps(payload).encode(),
                       {"Content-Type": "application/json"},
                       timeout=timeout, max_retries=self.max_retries,
                       backoff_base=self.backoff_base, breaker=self._breaker)
        with r:
            for raw in r:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                c = obj.get("message", {}).get("content", "")
                if c:
                    yield c
                if obj.get("done"):
                    break


class OpenAIClient(LLMClient):
    def __init__(self, base_url, model, api_key="", max_retries=2, backoff_base=0.5):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._breaker = _Breaker()

    def is_available(self):
        try:
            root = self.base_url
            for suf in ("/v1", "/v1/", "/chat/completions"):
                root = root.removesuffix(suf)
            req = urllib.request.Request(
                root + "/",
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            )
            urllib.request.urlopen(req, timeout=3)
            return True
        except Exception:
            return False

    def chat(self, messages, *, stream=False, temperature=0.2, timeout=120):
        url = self.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if stream:
            return self._stream_openai(url, payload, headers, timeout=timeout)
        r = _http_post(url, json.dumps(payload).encode(), headers, timeout=timeout,
                       max_retries=self.max_retries, backoff_base=self.backoff_base, breaker=self._breaker)
        with r:
            obj = json.loads(r.read().decode())
        choices = obj.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "") or ""

    def _stream_openai(self, url, payload, headers, timeout=120):
        r = _http_post(url, json.dumps(payload).encode(), headers, timeout=timeout,
                       max_retries=self.max_retries, backoff_base=self.backoff_base, breaker=self._breaker)
        with r:
            for raw in r:
                line = raw.decode().strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                # 防御: 商汤/OpenAI 在内容安全拦截或限流时可能返回 choices:[] (键存在但为空),
                # 此时 get 不会触发默认值 -> [0] 越界。空 choices 直接跳过本行, 等待 [DONE]。
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                c = delta.get("content")
                if c:
                    yield c


class MockClient(LLMClient):
    """离线确定性后端, 主要用于测试与无网络演示。

    - 若通过 set_script 预置脚本, 则严格按脚本逐轮返回 (测试用)。
    - 否则进入 "agentic 演示模式": 根据最新用户意图自动产出工具调用围栏,
      让无 Ollama / 无网环境也能演示 工具调用 -> 执行 -> 回灌 的完整时间线。
    """

    def __init__(self, model="mock-coder"):
        self.model = model
        self.script = []

    def set_script(self, responses):
        self.script = list(responses)

    def chat(self, messages, *, stream=False, temperature=0.2, timeout=120):
        if self.script:
            text = self.script.pop(0)
        else:
            text = self._agentic_default(messages)
        if stream:
            return iter([text])
        return text

    def _agentic_default(self, messages):
        # 取最新一条 user 消息作为当前意图
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        # 已经是工具结果回灌 -> 给出自然语言总结, 结束循环
        if "[tool result" in last_user:
            return "（mock）已通过工具获取结果，任务完成。"
        p = last_user
        if re.search(r"读|查看|read|cat|内容", p, re.I):
            return ('我先读取一下文件内容：\n'
                    '```tool\n{"name":"read_file","arguments":{"path":"config.toml"}}\n```')
        if re.search(r"运行|执行|命令|run|command|cmd|终端", p, re.I):
            return ('我执行一条命令看一下：\n'
                    '```tool\n{"name":"run_command","arguments":{"command":"echo 演示命令"}}\n```')
        if re.search(r"目录|文件|list|列|dir|folder|结构", p, re.I):
            return ('我先列一下当前目录：\n'
                    '```tool\n{"name":"list_dir","arguments":{"path":"."}}\n```')
        if re.search(r"评审|审查|review|检查质量|自检|质量门禁", p, re.I):
            return ('我写完后对这份代码做一次评审自检：\n'
                    '```tool\n{"name":"review_code","arguments":{"target":"config.toml"}}\n```')
        if re.search(r"mcp|外部工具|扩展工具|插件|extension|external", p, re.I):
            # 若已接入外部 MCP 工具, 演示通过 MCP 调用外部能力
            try:
                from ..tools import registry as _reg
                mcp_tool = next((t["name"] for t in _reg.TOOL_SCHEMAS if t.get("mcp")), None)
            except Exception:
                mcp_tool = None
            if mcp_tool:
                return ('我通过外部 MCP 工具处理这个请求：\n'
                        f'```tool\n{{"name":"{mcp_tool}","arguments":{{"text":"{p[:40]}"}}}}\n```')
        return "（mock）已收到请求，任务完成。"

    def is_available(self):
        return True


class FailoverClient(LLMClient):
    """模型故障转移封装: 持有一组有序 LLMClient, 当一个模型未响应时自动轮换到下一个。

    触发轮换的"未响应"情形:
      - 网络层异常: 连接失败 / DNS 失败 / 超时 (socket.timeout, urllib.error.URLError/HTTPError)
      - HTTP 错误: 4xx/5xx (如 429 限流, 500 网关错误)
      - 空回复: 内容安全拦截或限流导致 choices:[] (treat_empty_as_failure=True 时)
    策略:
      - 从 last_good(上次成功) 开始, 按顺序轮询所有 client 直到成功。
      - 记住最近成功的 client 优先复用, 避免健康主模型每次从头探测。
      - 单个候选用较短的 per_timeout 快速失败, 不让挂掉的模型拖垮整体。
      - 全部失败时抛 RuntimeError, 由上层捕获兜底。
    """
    def __init__(self, clients, name="failover", treat_empty_as_failure=True, per_timeout=20):
        if not clients:
            raise ValueError("FailoverClient requires at least one client")
        self.clients = list(clients)
        self.name = name
        self.model = self.clients[0].model
        self.treat_empty_as_failure = treat_empty_as_failure
        self.per_timeout = per_timeout
        self.last_good = 0

    def is_available(self):
        return any(c.is_available() for c in self.clients)

    def chat(self, messages, *, stream=False, temperature=0.2, timeout=120):
        order = list(range(self.last_good, len(self.clients))) + list(range(0, self.last_good))
        last_err = None
        if stream:
            return self._stream(order, messages, temperature)
        for i in order:
            c = self.clients[i]
            try:
                text = c.chat(messages, stream=False, temperature=temperature, timeout=self.per_timeout)
            except _FAIL_EXC as e:
                last_err = "%s: %s" % (c.model, _brief_err(e))
                continue
            if self.treat_empty_as_failure and not str(text or "").strip():
                last_err = "%s: empty/blocked response" % c.model
                continue
            self.last_good = i
            return text
        raise RuntimeError("all LLM providers failed" + (": " + last_err if last_err else ""))

    def _stream(self, order, messages, temperature):
        last_err = None
        for i in order:
            c = self.clients[i]
            try:
                it = c.chat(messages, stream=True, temperature=temperature, timeout=self.per_timeout)
            except _FAIL_EXC as e:
                last_err = "%s: %s" % (c.model, _brief_err(e))
                continue
            buffered = []
            locked = False
            while True:
                try:
                    chunk = next(it)
                except StopIteration:
                    # 整个流未产出有效内容 -> 空流, 视为失败, 试下一个
                    break
                except _FAIL_EXC as e:
                    # 已锁定(已产出有效块)则无法回退, 直接上报; 否则 failover
                    if locked:
                        raise
                    last_err = "%s: %s" % (c.model, _brief_err(e))
                    break
                if str(chunk or "").strip():
                    locked = True
                    self.last_good = i
                    for b in buffered:
                        yield b
                    yield chunk
                    for rest in it:
                        yield rest
                    return
                buffered.append(chunk)
            continue
        raise RuntimeError("all LLM providers failed (stream)" + (": " + last_err if last_err else ""))


def _client_from_spec(spec, cfg, model=None):
    """从 provider 规格 dict 构建单个 LLMClient。

    spec: {"type": ollama|openai|sensenova|mock, "model"?, "base_url"?, "api_key"?, "api_key_env"?}
    """
    ctype = spec.get("type", "ollama")
    if ctype == "ollama":
        return OllamaClient(
            base_url=spec.get("base_url") or cfg["llm"]["ollama"]["base_url"],
            model=spec.get("model") or cfg["llm"]["ollama"]["model"],
        )
    if ctype in ("openai", "sensenova"):
        base = cfg["llm"].get(ctype, {})
        api_key = (spec.get("api_key")
                   or (os.environ.get(spec["api_key_env"]) if spec.get("api_key_env") else "")
                   or base.get("api_key", ""))
        return OpenAIClient(
            base_url=spec.get("base_url") or base.get("base_url", ""),
            model=spec.get("model") or base.get("model", ""),
            api_key=api_key,
        )
    if ctype == "mock":
        return MockClient(model=spec.get("model") or cfg["llm"]["mock"]["model"])
    raise ValueError("unknown provider type: " + str(ctype))


def build_client(backend=None, cfg=None, model=None):
    cfg = cfg or load_config()
    backend = backend or cfg["llm"].get("backend", "ollama")
    # 外部模型: 若 backend 命中某个已配置 provider 的 name, 直接按该 provider 规格构建。
    # 这样在 /api/chat 里传 backend=<provider name> 即可选用任意外部大模型。
    for p in (cfg["llm"].get("providers") or []):
        if p.get("name") and p.get("name") == backend:
            primary = _client_from_spec({
                "type": p.get("type", "openai"),
                "model": model or p.get("model"),
                "base_url": p.get("base_url"),
                "api_key": p.get("api_key"),
                "api_key_env": p.get("api_key_env"),
            }, cfg, model)
            # 仍套用故障转移(若配置了 llm.failover)
            fo = cfg["llm"].get("failover") or []
            if fo:
                extras = []
                for fp in fo:
                    try:
                        extras.append(_client_from_spec({
                            "type": fp.get("type", "openai"),
                            "model": fp.get("model"),
                            "base_url": fp.get("base_url"),
                            "api_key": fp.get("api_key"),
                            "api_key_env": fp.get("api_key_env"),
                        }, cfg, model))
                    except Exception:
                        continue
                if extras:
                    return FailoverClient([primary] + extras)
            return primary
    primary = _resolve_primary(backend, cfg, model)
    # 故障转移: 若配置了 llm.failover 列表, 把主 backend 与各候选组成一个有序轮换组。
    fo = cfg["llm"].get("failover") or []
    if fo:
        extras = []
        for p in fo:
            try:
                extras.append(_client_from_spec({
                    "type": p.get("type", "openai"),
                    "model": p.get("model"),
                    "base_url": p.get("base_url"),
                    "api_key": p.get("api_key"),
                    "api_key_env": p.get("api_key_env"),
                }, cfg, model))
            except Exception:
                continue
        if extras:
            return FailoverClient([primary] + extras)
    return primary


def _resolve_primary(backend=None, cfg=None, model=None):
    cfg = cfg or load_config()
    backend = backend or cfg["llm"].get("backend", "ollama")

    if backend == "auto":
        o = OllamaClient(model=model or cfg["llm"]["ollama"]["model"], base_url=cfg["llm"]["ollama"]["base_url"])
        if o.is_available():
            return o
        op = OpenAIClient(
            model=model or cfg["llm"]["openai"]["model"],
            base_url=cfg["llm"]["openai"]["base_url"],
            api_key=cfg["llm"]["openai"].get("api_key", ""),
        )
        if op.is_available():
            return op
        return MockClient(model=model or cfg["llm"]["mock"]["model"])

    if backend == "ollama":
        return OllamaClient(model=model or cfg["llm"]["ollama"]["model"], base_url=cfg["llm"]["ollama"]["base_url"])
    if backend == "openai":
        return OpenAIClient(
            model=model or cfg["llm"]["openai"]["model"],
            base_url=cfg["llm"]["openai"]["base_url"],
            api_key=cfg["llm"]["openai"].get("api_key", ""),
        )
    if backend == "sensenova":
        return OpenAIClient(
            model=model or cfg["llm"]["sensenova"]["model"],
            base_url=cfg["llm"]["sensenova"]["base_url"],
            api_key=cfg["llm"]["sensenova"].get("api_key", ""),
        )
    if backend == "mock":
        return MockClient(model=model or cfg["llm"]["mock"]["model"])
    raise ValueError("unknown backend: " + str(backend))
