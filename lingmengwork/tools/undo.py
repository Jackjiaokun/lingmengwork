"""文件改动快照栈: 仿 Aider 的 /undo, 提供本地(无 git)改动回滚能力。

设计:
- 每次 write_file / edit_file 成功前, 把文件旧内容压入该路径的快照栈。
- undo 工具可回滚最近一次对某文件的改动, 或回滚全部(最近一个批次)。
- 纯内存栈, 进程内有效; 不依赖 git, 适合沙箱/无版本库场景。
"""
from .common import ToolError


class SnapshotStack:
    """按路径维护 (path -> [content, ...]) 的快照栈。"""

    def __init__(self):
        self._stacks = {}      # path -> list[str|None]  (栈顶在末尾; None 表示文件原本不存在)
        self._batch = []       # 当前批次改动过的路径顺序 (用于全局 undo)

    def push(self, path, old_content):
        """记录某路径改动前的旧内容。old_content=None 表示改动前文件不存在。"""
        self._stacks.setdefault(path, []).append(old_content)
        if not self._batch or self._batch[-1] != path:
            self._batch.append(path)

    def undo_file(self, path):
        """回滚单个文件的最近一次改动。

        返回:
          - "_EMPTY_" 哨兵: 该路径无快照(栈空), 调用方应报"无快照"。
          - None: 该路径改动前文件不存在(新建文件), 调用方应删除之。
          - str : 改动前的旧内容, 调用方应写回。
        """
        st = self._stacks.get(path)
        if not st:
            return "_EMPTY_"
        old = st.pop()
        # 从 batch 移除该路径最近一次出现
        for i in range(len(self._batch) - 1, -1, -1):
            if self._batch[i] == path:
                del self._batch[i]
                break
        return old

    def undo_last(self):
        """回滚最近一个被改动的批次(单个文件), 返回 (path, old_content)。"""
        if not self._batch:
            return None
        path = self._batch.pop()
        st = self._stacks.get(path)
        if not st:
            return None
        old = st.pop()
        return (path, old)

    def has_any(self):
        return bool(self._batch)


# 进程内默认栈 (由 registry 持有并传递给工具)
_default_stack = None


def get_default_stack():
    global _default_stack
    if _default_stack is None:
        _default_stack = SnapshotStack()
    return _default_stack


def reset_default_stack():
    global _default_stack
    _default_stack = SnapshotStack()
