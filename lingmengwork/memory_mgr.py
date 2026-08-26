"""记忆管理 (Memory): 工作区级别的长期记忆与每日工作日志。

用户/智能体可在「🧠 记忆」管理页查看与更新项目记忆, 数据落在工作区主根的
``.lmw_memory/`` 目录:

    .lmw_memory/
        MEMORY.md           长期项目记忆(用户可编辑/追加)
        daily/
            YYYY-MM-DD.md   每日工作日志(按段追加)

与对话解耦、可长期留存; 智能体运行时亦可读取这些文件以「记住」项目事实。
"""
import os
import time
from datetime import datetime

MEMORY_DIR = ".lmw_memory"
MEMORY_FILE = "MEMORY.md"
DAILY_DIR = "daily"

_DEFAULT_MEMORY = """# 项目长期记忆 (MEMORY.md)

> 在此记录项目的长期事实、约定与决策。智能体会在工作区启动时读取本文件。
> 用「更新记忆」按钮可直接向本文件追加一条带时间戳的笔记。

## 关键约定
- (待补充)

## 架构要点
- (待补充)

## 开放问题
- (待补充)
"""


def _root(base_dir=None):
    return os.path.join(base_dir or os.getcwd(), MEMORY_DIR)


def _ensure(base_dir=None):
    root = _root(base_dir)
    os.makedirs(os.path.join(root, DAILY_DIR), exist_ok=True)
    mp = os.path.join(root, MEMORY_FILE)
    if not os.path.isfile(mp):
        with open(mp, "w", encoding="utf-8") as f:
            f.write(_DEFAULT_MEMORY)
    return root


def read_memory(base_dir=None):
    root = _ensure(base_dir)
    mp = os.path.join(root, MEMORY_FILE)
    try:
        return open(mp, encoding="utf-8").read()
    except Exception:
        return _DEFAULT_MEMORY


def update_memory(base_dir, content):
    """整体覆盖 MEMORY.md。"""
    root = _ensure(base_dir)
    mp = os.path.join(root, MEMORY_FILE)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(content or "")
    return {"ok": True, "path": os.path.relpath(mp, base_dir or os.getcwd()), "bytes": len((content or "").encode("utf-8"))}


def append_memory(base_dir, text, title=""):
    """向 MEMORY.md 追加一条带时间戳的笔记, 返回追加后的全文。"""
    root = _ensure(base_dir)
    mp = os.path.join(root, MEMORY_FILE)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = "\n\n---\n### 📌 %s (%s)\n\n%s\n" % (title or "更新", ts, (text or "").strip() or "(空)")
    with open(mp, "a", encoding="utf-8") as f:
        f.write(block)
    return {"ok": True, "content": read_memory(base_dir)}


def list_logs(base_dir=None):
    root = _ensure(base_dir)
    d = os.path.join(root, DAILY_DIR)
    items = []
    for fn in sorted(os.listdir(d), reverse=True):
        if not fn.endswith(".md"):
            continue
        fp = os.path.join(d, fn)
        try:
            sz = os.path.getsize(fp)
        except Exception:
            sz = 0
        items.append({"date": fn[:-3], "file": fn, "size": sz})
    return {"logs": items}


def read_log(base_dir, date):
    root = _ensure(base_dir)
    fp = os.path.join(root, DAILY_DIR, "%s.md" % date)
    if not os.path.isfile(fp):
        return {"date": date, "content": "", "exists": False}
    try:
        return {"date": date, "content": open(fp, encoding="utf-8").read(), "exists": True}
    except Exception:
        return {"date": date, "content": "", "exists": False}


def append_log(base_dir, text, title="", date=None):
    """向指定日期(默认今天)的每日日志追加一段。"""
    root = _ensure(base_dir)
    date = date or datetime.now().strftime("%Y-%m-%d")
    fp = os.path.join(root, DAILY_DIR, "%s.md" % date)
    ts = datetime.now().strftime("%H:%M")
    block = "\n\n### %s · %s\n\n%s\n" % (title or "记录", ts, (text or "").strip() or "(空)")
    with open(fp, "a", encoding="utf-8") as f:
        f.write(block)
    return {"ok": True, "date": date, "content": read_log(base_dir, date)["content"]}
