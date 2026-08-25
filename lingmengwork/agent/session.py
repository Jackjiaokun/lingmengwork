"""会话持久化与恢复: 仿 Claude Code 的 --resume / 历史会话管理。

- 每次对话的 messages 落盘到 ~/.lingmengwork/sessions/<id>.json
- 支持 list_sessions() 列出历史、load_session(id) 恢复、save_session() 保存
- 会话文件含: id, created_at, updated_at, model, provider, messages
- 不依赖 git, 纯本地 JSON, 进程退出前由调用方触发保存
"""
import json
import os
import time
import uuid
from pathlib import Path


def _sessions_dir():
    d = Path(os.path.expanduser("~")) / ".lingmengwork" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_session_id():
    return uuid.uuid4().hex[:12]


def save_session(sid, messages, model="", provider="", base_dir=""):
    """保存/覆盖会话。messages: list[{role,content}]。"""
    d = _sessions_dir()
    meta_path = d / f"{sid}.json"
    # 读取旧时间戳(若存在)以保留 created_at
    created = time.time()
    if meta_path.exists():
        try:
            old = json.loads(meta_path.read_text(encoding="utf-8"))
            created = old.get("created_at", created)
        except Exception:
            pass
    data = {
        "id": sid,
        "created_at": created,
        "updated_at": time.time(),
        "model": model,
        "provider": provider,
        "base_dir": base_dir,
        "messages": messages,
    }
    meta_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return meta_path


def load_session(sid):
    """返回会话 dict 或 None。"""
    p = _sessions_dir() / f"{sid}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_sessions(limit=50):
    """列出会话(按更新时间倒序), 每项含摘要。"""
    d = _sessions_dir()
    items = []
    for p in d.glob("*.json"):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        msgs = obj.get("messages", [])
        # 摘要: 第一条 user 消息前 60 字符
        summary = ""
        for m in msgs:
            if m.get("role") == "user":
                summary = m.get("content", "")[:60]
                break
        items.append({
            "id": obj.get("id", p.stem),
            "created_at": obj.get("created_at", 0),
            "updated_at": obj.get("updated_at", 0),
            "model": obj.get("model", ""),
            "provider": obj.get("provider", ""),
            "messages": len(msgs),
            "summary": summary,
        })
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return items[:limit]


def delete_session(sid):
    p = _sessions_dir() / f"{sid}.json"
    if p.exists():
        try:
            p.unlink()
            return True
        except Exception:
            return False
    return False
