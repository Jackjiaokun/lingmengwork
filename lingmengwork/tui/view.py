"""零依赖终端视图: 双栏缓冲 + 状态条 + 输入行渲染。

仅负责「画」, 不解输入/线程。所有状态由调用方 push 进缓冲。
Windows conhost / 现代终端均支持下述 ANSI 转义。
"""
import shutil

# ANSI 调色
C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_BOLD = "\033[1m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_GREY = "\033[90m"

# 光标 / 屏控制
HIDE_CUR = "\033[?25l"
SHOW_CUR = "\033[?25h"
ALT_ON = "\033[?1049h"
ALT_OFF = "\033[?1049l"
HOME = "\033[H"
CLEAR = "\033[2J"
CLEAR_LINE = "\033[2K"
MOVE_UP = "\033[A"

# 面板分隔风格
LEFT_TITLE = f"{C_BOLD}{C_CYAN}对话{C_RESET}"
RIGHT_TITLE = f"{C_BOLD}{C_MAGENTA}工具 / 事件{C_RESET}"


def _strip_ansi(s: str) -> int:
    """返回可见字符宽度 (近似: 中文算 2, ANSI 忽略)。"""
    import re
    s = re.sub(r"\033\[[0-9;?]*[a-zA-Z]", "", s)
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x2E80 else 1  # 粗略 CJK 宽度
    return w


def _wrap(text: str, width: int) -> list[str]:
    """按可见宽度折行 (保留 ANSI 不切断)。"""
    if width <= 0:
        return [text]
    lines: list[str] = []
    cur = ""
    cur_w = 0
    # 逐段处理 ANSI 与可见字符
    import re
    tokens = re.findall(r"\033\[[0-9;?]*[a-zA-Z]|[^\033]", text)
    for tok in tokens:
        if tok.startswith("\033"):
            cur += tok
            continue
        w = _strip_ansi(tok)
        if cur_w + w > width and cur:
            lines.append(cur)
            cur = ""
            cur_w = 0
        cur += tok
        cur_w += w
    if cur:
        lines.append(cur)
    return lines or [""]


class TerminalView:
    """双栏终端视图。左=对话流, 右=工具事件日志, 底部=状态条+输入行。"""

    def __init__(self):
        self.chat_buf: list[str] = []        # 已完成的对话行 (含 ANSI)
        self.event_buf: list[str] = []       # 工具/事件日志行
        self.status = ""
        self.input_line = ""
        self.input_cursor = 0
        self.title = "灵梦work · TUI"
        self.rows = 24
        self.cols = 80
        self.right_ratio = 0.42              # 右栏占比
        self._update_size()

    # ---- 尺寸 ----
    def _update_size(self):
        try:
            size = shutil.get_terminal_size((80, 24))
            self.cols, self.rows = size.columns, size.lines
        except Exception:
            self.cols, self.rows = 80, 24

    # ---- 缓冲操作 ----
    def push_chat(self, line: str):
        self.chat_buf.append(line)
        if len(self.chat_buf) > 2000:
            self.chat_buf = self.chat_buf[-2000:]

    def push_event(self, line: str):
        self.event_buf.append(line)
        if len(self.event_buf) > 2000:
            self.event_buf = self.event_buf[-2000:]

    def set_status(self, s: str):
        self.status = s

    def set_input(self, text: str, cursor: int):
        self.input_line = text
        self.input_cursor = cursor

    # ---- 渲染 ----
    def render(self) -> str:
        """返回完整的重绘字节串 (含清屏与光标归位)。"""
        self._update_size()
        split = max(20, int(self.cols * (1 - self.right_ratio)))
        right_w = self.cols - split - 1
        top_h = self.rows - 2  # 留给状态条 + 输入行

        out = [HOME, CLEAR, HIDE_CUR]

        # 标题行
        out.append(f"{C_BOLD}{self.title}{C_RESET}{C_DIM}  │  {LEFT_TITLE}  {'':<{max(0,split-12)}}│ {RIGHT_TITLE}{C_RESET}\n")
        out.append(f"{C_DIM}{'─'*split}┬{'─'*max(0,right_w-1)}{C_RESET}\n")

        # 计算可显示区域
        # 折行后组装左右两栏内容
        left_lines = self._fold_buf(self.chat_buf, split - 2)
        right_lines = self._fold_buf(self.event_buf, right_w - 1)

        body_h = top_h - 2
        left_view = left_lines[-body_h:] if len(left_lines) > body_h else left_lines
        right_view = right_lines[-body_h:] if len(right_lines) > body_h else right_lines

        for i in range(body_h):
            l = left_view[i] if i < len(left_view) else ""
            r = right_view[i] if i < len(right_view) else ""
            lpad = self._pad(l, split - 2)
            rpad = self._pad(r, right_w - 1)
            out.append(f"{lpad} │ {rpad}\n")

        out.append(f"{C_DIM}{'─'*split}┴{'─'*max(0,right_w-1)}{C_RESET}\n")
        # 状态条
        out.append(f"{C_GREY}{self._pad(self.status, self.cols-1)}{C_RESET}\n")
        # 输入行
        inp = self._pad(self.input_line, self.cols - 2)
        out.append(f"{C_GREEN}❯{C_RESET} {inp}\n")
        # 光标定位到输入行 (最后一行), 用绝对定位不可靠, 直接 SHOW + 末尾
        out.append(SHOW_CUR)
        # 把光标移到输入行首后 input_cursor 处: 简易处理 -> 末行开头
        out.append(f"\033[{self.rows};3H")
        # 精确列: 输入光标在 "❯ " 之后
        col = 3 + self._visible(self.input_line[:self.input_cursor])
        out.append(f"\033[{self.rows};{col}H")
        return "".join(out)

    # ---- 内部 ----
    def _fold_buf(self, buf: list[str], width: int) -> list[str]:
        out: list[str] = []
        for raw in buf:
            out.extend(_wrap(raw, width))
        return out

    def _visible(self, s: str) -> int:
        return _strip_ansi(s)

    def _pad(self, s: str, width: int) -> str:
        """右侧补空格到 width 可见宽度。"""
        w = self._visible(s)
        if w >= width:
            # 截断 (保留 ANSI 头)
            return self._truncate(s, width)
        return s + " " * (width - w)

    def _truncate(self, s: str, width: int) -> str:
        import re
        tokens = re.findall(r"\033\[[0-9;?]*[a-zA-Z]|[^\033]", s)
        res = ""
        w = 0
        for tok in tokens:
            if tok.startswith("\033"):
                res += tok
                continue
            cw = _strip_ansi(tok)
            if w + cw > width:
                break
            res += tok
            w += cw
        return res
