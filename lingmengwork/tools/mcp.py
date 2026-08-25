"""外部工具接入: MCP (Model Context Protocol) stdio 客户端, 零外部依赖。

让本地 agent 从「内置工具集」升级为「开放工具中枢」:
  通过 stdio 传输的 JSON-RPC 连接任意 MCP 服务器 (filesystem / git / fetch / 数据库 ...),
  把远端工具动态注册进工具注册表, 主 Agent 像调用内置工具一样调用它们。

协议要点 (MCP 2024-11-05 over stdio, 换行分隔 JSON):
  - initialize            -> 握手, 取 protocolVersion / capabilities
  - notifications/initialized (无 id) -> 握手完成通知
  - tools/list            -> 列出远端工具 (name/description/inputSchema)
  - tools/call            -> 调用工具, 返回 content:[{type:text,text:...}]

实现: 单 reader 线程持续读 stdout, 用 Future 按 id 匹配请求/响应; 写 stdin 加锁串行化。
"""
import json
import os
import subprocess
import threading
from concurrent.futures import Future

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "lingmengwork", "version": "1.0"}


class MCPClientError(Exception):
    pass


class StdioMCPClient:
    """单个 MCP 服务器的 stdio 客户端 (零依赖)。"""

    def __init__(self, command, args=None, env=None, cwd=None, timeout=30):
        self.command = command
        self.args = list(args or [])
        self.timeout = timeout
        self._id = 0
        self._lock = threading.Lock()
        self._pending = {}          # id -> Future
        self._tools = {}            # name -> tool def
        self._closed = False

        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        try:
            if os.name == "nt":
                # Windows 冻结 exe 子进程句柄继承坑 (PyInstaller):
                #   普通 stdin=PIPE 会让子进程继承 bootloader 的(无效)控制台 stdin -> 立即 EOF。
                # 修复: 用 os.pipe() 自建 fd, 再用 os.set_inheritable()(注意不是
                # set_handle_inheritable —— 后者收 HANDLE 而非 fd, 在 Windows 上静默失效,
                # 正是此前子进程 stdin 立即 EOF 的根因) 让「子需要的一端」可继承, 其余不可继承。
                r_fd, w_fd = os.pipe()          # 父->子 stdin: 子读 r_fd, 父写 w_fd
                r_out, w_out = os.pipe()        # 子->父 stdout: 子写 w_out, 父读 r_out
                os.set_inheritable(r_fd, True)  # 子 stdin 读端可继承
                os.set_inheritable(w_out, True) # 子 stdout 写端可继承
                # r_out / w_fd 保持不可继承 (父持有端, 不应被子继承)
                self._proc = subprocess.Popen(
                    [command, *self.args],
                    stdin=r_fd,
                    stdout=w_out,
                    stderr=subprocess.DEVNULL,
                    env=full_env,
                    cwd=cwd,
                    bufsize=1,
                    text=True,
                    close_fds=False,
                )
                # 父进程关闭「子端」fd 副本 (子已继承自己的独立副本), 避免句柄泄漏
                os.close(r_fd)
                os.close(w_out)
                self._wstdin = os.fdopen(w_fd, "w", buffering=1, encoding="utf-8", errors="replace")
                self._child_stdout = os.fdopen(r_out, "r", buffering=1, encoding="utf-8", errors="replace")
            else:
                self._proc = subprocess.Popen(
                    [command, *self.args],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=full_env,
                    cwd=cwd,
                    bufsize=1,
                    text=True,
                )
                self._wstdin = self._proc.stdin
                self._child_stdout = self._proc.stdout
        except Exception as e:
            raise MCPClientError(f"启动 MCP 服务失败 [{command}]: {e}")

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        try:
            self._handshake()
        except Exception:
            self.close()
            raise

    # ---- 内部: 读取循环 ----
    def _read_loop(self):
        try:
            for raw in self._child_stdout:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                # 响应 (含 id 且非请求) -> 唤醒对应 Future
                if "id" in msg and "method" not in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                # 通知 / 服务端主动请求 -> 忽略 (本客户端不实现 sampling 等反向调用)
        except Exception:
            pass
        finally:
            # 进程退出: 所有未完成请求标记异常
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(MCPClientError("MCP 服务连接已断开"))

    # ---- 内部: 发送请求并等待响应 ----
    def _request(self, method, params=None, timeout=None):
        with self._lock:
            self._id += 1
            rid = self._id
            fut = Future()
            self._pending[rid] = fut
            payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
            try:
                self._wstdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._wstdin.flush()
            except Exception as e:
                self._pending.pop(rid, None)
                raise MCPClientError(f"写入 MCP 请求失败: {e}")
        try:
            msg = fut.result(timeout=(timeout or self.timeout))
        except Exception as e:
            self._pending.pop(rid, None)
            raise MCPClientError(f"MCP 请求 {method} 超时/失败: {e}")
        if "error" in msg:
            err = msg["error"]
            raise MCPClientError(f"MCP 错误 {err.get('code')}: {err.get('message')}")
        return msg.get("result", {})

    def _handshake(self):
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        # 发送 initialized 通知 (无 id, 无需等待)
        with self._lock:
            try:
                self._wstdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
                self._wstdin.flush()
            except Exception:
                pass
        res = self._request("tools/list", {})
        for t in (res.get("tools") or []):
            name = t.get("name")
            if name:
                self._tools[name] = t

    # ---- 公共 API ----
    def list_tools(self):
        return list(self._tools.values())

    def has_tool(self, name):
        return name in self._tools

    def call_tool(self, name, arguments=None):
        if name not in self._tools:
            raise MCPClientError(f"MCP 服务无此工具: {name}")
        res = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        content = res.get("content") or []
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        out = "\n".join(parts).strip()
        if res.get("isError"):
            out = "[mcp error] " + out
        return out or "(工具返回空)"

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._wstdin.close()
        except Exception:
            pass
        try:
            self._child_stdout.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
        except Exception:
            pass


class MCPManager:
    """进程内 MCP 连接管理器 (单例): 按 config 懒连接所有配置的服务器。"""

    def __init__(self):
        self.servers = {}          # name -> StdioMCPClient
        self._lock = threading.Lock()
        self._connected = False

    def connect_all(self, cfg):
        """按 cfg['mcp']['servers'] 启动所有未连接的 MCP 服务。失败单个跳过, 不阻断其他。"""
        sec = (cfg or {}).get("mcp") or {}
        if not sec.get("enabled", True):
            return
        servers = sec.get("servers") or []
        for s in servers:
            name = s.get("name") or s.get("command")
            if not name:
                continue
            with self._lock:
                if name in self.servers:
                    continue
                cmd = s.get("command")
                if not cmd:
                    continue
                try:
                    client = StdioMCPClient(
                        cmd,
                        args=s.get("args") or [],
                        env=s.get("env"),
                        cwd=s.get("cwd"),
                        timeout=int(s.get("timeout", 30)),
                    )
                    self.servers[name] = client
                except Exception as e:
                    # 单个服务不可用 -> 跳过 (仍保留其他能力), 但留痕便于排查
                    import sys as _sys
                    _sys.stderr.write("MCP connect '%s' failed: %s\n" % (name, e))
                    _sys.stderr.flush()
        self._connected = True

    def get_tool_server(self, tool_name):
        for s in self.servers.values():
            if s.has_tool(tool_name):
                return s
        return None

    def call(self, tool_name, arguments=None):
        s = self.get_tool_server(tool_name)
        if not s:
            raise MCPClientError(f"未找到已注册的 MCP 工具: {tool_name}")
        return s.call_tool(tool_name, arguments)

    def tool_schemas(self):
        out = []
        for server_name, s in self.servers.items():
            for t in s.list_tools():
                out.append(_to_schema(server_name, t))
        return out

    def status(self):
        return [
            {"name": n, "tools": [t["name"] for t in s.list_tools()]}
            for n, s in self.servers.items()
        ]

    def close_all(self):
        with self._lock:
            for s in self.servers.values():
                try:
                    s.close()
                except Exception:
                    pass
            self.servers.clear()


# 模块级单例 + 已注册工具集合
_manager = None
_manager_lock = threading.Lock()
_registered = set()


def get_manager():
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = MCPManager()
    return _manager


def _js_type(schema):
    t = (schema or {}).get("type", "string")
    return {"type": t}


def _to_schema(server_name, tool):
    sch = tool.get("inputSchema", {}) or {}
    props = sch.get("properties", {}) or {}
    params = {k: _js_type(v) for k, v in props.items()}
    return {
        "name": tool["name"],
        "description": f"[MCP:{server_name}] " + (tool.get("description") or ""),
        "parameters": params,
        "mcp": server_name,
    }


def _mcp_tool_impl(manager, tool_name):
    def _impl(args, ctx):
        try:
            return manager.call(tool_name, args)
        except Exception as e:
            return f"[mcp error] {type(e).__name__}: {e}"
    return _impl


def populate_registry(cfg, registry=None, force=False):
    """把已连接的 MCP 工具注入全局 TOOL_SCHEMAS / _IMPLS / _EXEC_TOOLS。

    - 进程内只注册一次 (幂等); 后续 build_registry 调用直接复用。
    - 与内置工具重名则跳过, 避免覆盖内置能力。
    """
    from . import registry as _reg

    global _registered
    mgr = get_manager()
    mgr.connect_all(cfg)
    schemas = mgr.tool_schemas()
    if not schemas and not force:
        return
    with _manager_lock:
        for sch in schemas:
            name = sch["name"]
            if name in _registered:
                continue
            # 与内置工具重名保护
            if any(t["name"] == name for t in _reg.TOOL_SCHEMAS):
                continue
            _reg.TOOL_SCHEMAS.append(sch)
            _reg._IMPLS[name] = _mcp_tool_impl(mgr, name)
            _reg._EXEC_TOOLS.add(name)
            _registered.add(name)
