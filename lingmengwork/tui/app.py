"""灵梦work 零依赖 TUI 主循环。

特性:
- 双栏全屏: 左=对话流, 右=工具/事件日志
- 底部状态条 + 输入行
- 多路能力: 复用 agent.pool.TaskPool, /tasks 查看并发任务, 对话走默认通道可持续多轮
- Windows 用 msvcrt 逐键输入; Linux/macOS 用 tty + select
- 零外部依赖 (纯 ANSI)
"""
import os
import sys
import queue
import threading

from .. import __version__
from ..config import build_clients
from ..tools.registry import build_registry
from ..agent.loop import AgentLoop
from ..agent.pool import TaskPool
from .view import TerminalView, C_RESET, C_DIM, C_CYAN, C_GREEN, C_YELLOW, C_RED, C_MAGENTA, C_BLUE, C_BOLD

_KB_HIT = None
_KB_GET = None


def _init_keyboard():
    global _KB_HIT, _KB_GET
    if sys.platform.startswith("win"):
        import msvcrt
        _KB_HIT = msvcrt.kbhit
        _KB_GET = msvcrt.getwch
    else:
        import termios
        import tty
        import select

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)

        def hit():
            return select.select([sys.stdin], [], [], 0)[0]

        def get():
            return sys.stdin.read(1)

        _KB_HIT = hit
        _KB_GET = get
        # 退出时恢复原终端
        import atexit
        atexit.register(lambda: termios.tcsetattr(fd, termios.TCSADRAIN, old))


def _enter_alt():
    sys.stdout.write("\033[?1049h")
    sys.stdout.flush()


def _exit_alt():
    sys.stdout.write("\033[?1049l")
    sys.stdout.flush()


class TuiApp:
    def __init__(self, cfg, clients, registry, default_provider=None):
        self.cfg = cfg
        self.clients = clients
        self.registry = registry
        self.default_provider = default_provider or (next(iter(clients), None))
        self.view = TerminalView()
        self.view.title = f"灵梦work · TUI v{__version__}"
        self.q: "queue.Queue" = queue.Queue()
        self.loop = AgentLoop(self.clients[self.default_provider], registry, cfg)
        self.pool = TaskPool(cfg, base_dir=".")
        self.pool.clients = self.clients  # 复用同一组通道
        self.busy = False
        self.running = True
        self.mode = "chat"  # chat | tasks
        self.cur_iter = 0   # 当前对话迭代轮数
        self.cur_tools = 0  # 当前对话工具调用数
        self.cur_tokens = 0  # 当前对话估算 token 总量
        self.cur_cost = 0.0  # 当前对话估算成本(元)
        self.lock = threading.Lock()
        self._refresh_status()

    # ---- 事件路由 ----
    def _on_event(self, type_, kw):
        if type_ == "text":
            self.q.put(("chat", kw.get("chunk", "")))
        elif type_ == "tool":
            name = kw.get("name", "?")
            args = kw.get("arguments", {})
            a = ", ".join(f"{k}={v}" for k, v in args.items())
            self.cur_tools += 1
            self.q.put(("event", f"{C_CYAN}▶ {name}{C_DIM}({a}){C_RESET}"))
        elif type_ == "tool_result":
            out = kw.get("output", "")
            snip = (out if len(out) <= 240 else out[:240] + " …") .replace("\n", " ")
            self.q.put(("event", f"{C_DIM}  └ {snip}{C_RESET}"))
        elif type_ == "done":
            # 捕获 token/成本估算 (loop.run 在 done 事件中附带 token_stats())
            ts = kw.get("est_total_tokens")
            if ts is not None:
                try:
                    self.cur_tokens = int(kw.get("est_total_tokens", 0) or 0)
                    self.cur_cost = float(kw.get("est_cost_cny", 0.0) or 0.0)
                except Exception:
                    pass
            self.q.put(("chat", f"{C_GREEN}✓ 完成{C_RESET}\n"))
            if kw.get("truncated"):
                self.q.put(("chat", f"{C_YELLOW}⚠ 已达最大迭代, 强行结束{C_RESET}\n"))

    def _refresh_status(self):
        with self.lock:
            # 实时读取 AgentLoop 的 token 估算(对话线程在跑, 主循环逐帧刷新)
            try:
                st = self.loop.token_stats()
                self.cur_tokens = st["est_total_tokens"]
                self.cur_cost = st["est_cost_cny"]
            except Exception:
                pass
            tasks = self.pool.list_tasks()
            active = sum(1 for t in tasks if t["status"] in ("queued", "running"))
            self.view.set_status(
                f"通道:{len(self.clients)} 会话:[{self.default_provider}] "
                f"迭代:{self.cur_iter} 工具:{self.cur_tools} "
                f"Token:{self.cur_tokens} ¥{self.cur_cost:.4f} "
                f"并发任务:{len(tasks)}(活跃{active}/上限{self.pool.max_workers}) "
                f"{'● 工作中' if self.busy else '○ 空闲'} | /help 查看命令"
            )

    # ---- 提交对话 ----
    def submit_chat(self, text):
        with self.lock:
            self.busy = True
            self.cur_iter = 0
            self.cur_tools = 0
        self.view.push_chat(f"{C_BOLD}你>{C_RESET} {text}")
        self._refresh_status()
        t = threading.Thread(target=self._run_chat, args=(text,), daemon=True)
        t.start()

    def _run_chat(self, text):
        self.loop.run(text, on_event=self._on_event)
        with self.lock:
            self.busy = False
            self.cur_iter = self.loop.iteration
        self._refresh_status()

    # ---- 多路并发任务 ----
    def submit_task(self, text, provider=None):
        prov = provider or self.default_provider
        snap = self.pool.submit(text, provider=prov)
        self.view.push_event(f"{C_BLUE}➤ 任务#{snap['id']}{C_DIM}[{prov}]{C_RESET} {text[:30]}")
        self._refresh_status()
        return snap["id"]

    # ---- 任务列表视图 ----
    def _render_tasks(self):
        tasks = self.pool.list_tasks()
        self.view.chat_buf = [f"{C_BOLD}并发任务列表 ({len(tasks)}){C_RESET}"]
        for t in tasks[-40:]:
            st = t["status"]
            color = C_GREEN if st == "done" else (C_RED if st == "error" else C_YELLOW)
            self.view.chat_buf.append(
                f" #{t['id']} {color}{st}{C_RESET} {C_DIM}[{t['provider']}]{C_RESET} {t['prompt'][:40]}"
            )
        self.view.event_buf = [f"活跃任务: {sum(1 for t in tasks if t['status'] in ('queued','running'))}"]

    # ---- 键盘 ----
    def handle_key(self, ch):
        if ch in ("\r", "\n"):
            text = self.view.input_line
            self.view.input_line = ""
            self.view.input_cursor = 0
            if text.strip():
                self._dispatch(text.strip())
            return
        if ch == "\x08" or ch == "\x7f":  # backspace
            if self.view.input_cursor > 0:
                s = self.view.input_line
                self.view.input_line = s[:self.view.input_cursor-1] + s[self.view.input_cursor:]
                self.view.input_cursor -= 1
            return
        if ch == "\x1b":  # ESC -> 忽略后续 (方向键序列)
            return
        if ch in ("\x03",):  # Ctrl-C
            self.running = False
            return
        # 普通可打印字符
        if ord(ch) >= 32:
            s = self.view.input_line
            self.view.input_line = s[:self.view.input_cursor] + ch + s[self.view.input_cursor:]
            self.view.input_cursor += 1

    def _dispatch(self, text):
        low = text.lower()
        if low in ("/exit", "/quit"):
            self.running = False
        elif low == "/clear":
            self.loop.reset()
            with self.lock:
                self.view.chat_buf = []
            self.view.push_chat(f"{C_DIM}— 会话已清空 —{C_RESET}")
        elif low == "/tasks":
            self.mode = "tasks"
            self._render_tasks()
        elif low == "/chat":
            self.mode = "chat"
            self.view.chat_buf = []
            self.view.event_buf = []
        elif low == "/help":
            self._show_help()
        elif low.startswith("/provider "):
            name = text[10:].strip()
            if name in self.clients:
                self.default_provider = name
                self.loop = AgentLoop(self.clients[name], self.registry, self.cfg)
                self.view.push_chat(f"{C_GREEN}已切换通道: {name}{C_RESET}")
            else:
                self.view.push_chat(f"{C_RED}未知通道: {name}{C_RESET}")
            self._refresh_status()
        elif low.startswith("/new "):
            # 多路: 作为并发任务提交, 不走单会话
            self.submit_task(text[5:].strip())
        else:
            # 普通对话 -> 走默认通道单会话
            if self.mode == "tasks":
                self.mode = "chat"
                self.view.chat_buf = []
                self.view.event_buf = []
            self.submit_chat(text)

    def _show_help(self):
        lines = [
            f"{C_BOLD}灵梦work TUI 命令{C_RESET}",
            "  /exit        退出",
            "  /clear       清空当前对话历史",
            "  /tasks       查看并发任务列表",
            "  /chat        返回对话视图",
            "  /provider X  切换到通道 X (见 /tasks 或启动信息)",
            "  /new <任务>  作为并发任务提交(多路高并发)",
            "  /help        本帮助",
            f"{C_DIM}直接输入文字 = 与默认通道对话(多轮){C_RESET}",
        ]
        with self.lock:
            self.view.chat_buf = lines

    # ---- 主循环 ----
    def run(self):
        _init_keyboard()
        _enter_alt()
        welcome = [
            f"{C_BOLD}灵梦work · TUI v{__version__}{C_RESET}",
            f"{C_DIM}可用通道:{C_RESET} " + ", ".join(self.clients.keys()),
            f"{C_DIM}输入 /help 查看命令; 直接打字开始编程。{C_RESET}",
        ]
        with self.lock:
            self.view.chat_buf = welcome
        try:
            while self.running:
                # 消费事件队列
                try:
                    while True:
                        kind, payload = self.q.get_nowait()
                        with self.lock:
                            if kind == "chat":
                                self._append_chat_fragment(payload)
                            else:
                                self.view.push_event(payload)
                except queue.Empty:
                    pass
                # 键盘
                if _KB_HIT():
                    ch = _KB_GET()
                    self.handle_key(ch)
                # 工作中时逐帧刷新 Token/成本估算条
                if self.busy:
                    self._refresh_status()
                # 重绘
                with self.lock:
                    sys.stdout.write(self.view.render())
                    sys.stdout.flush()
        finally:
            _exit_alt()
            self.pool.shutdown(wait=False)
            sys.stdout.write("\n谢谢使用 灵梦work。\n")
            sys.stdout.flush()

    def _append_chat_fragment(self, fragment):
        """把流式文本片段追加到左栏最后一行 (合并未完成行)。"""
        if self.view.chat_buf and self.view.chat_buf[-1].startswith("\033[1m灵梦>"):
            self.view.chat_buf[-1] += fragment
        else:
            self.view.chat_buf.append(f"{C_BOLD}灵梦>{C_RESET} " + fragment)


def run_tui(cfg, clients=None, registry=None, default_provider=None):
    if clients is None:
        clients = build_clients(cfg)
    if registry is None:
        registry = build_registry(cfg)
    app = TuiApp(cfg, clients, registry, default_provider=default_provider)
    app.run()
    return 0
