"""代码片段库 (Code Snippets): 可复用的代码碎片库。

用户在「📎 代码片段」管理页维护一组带语言/标签的片段, 在对话输入框一键插入,
也可让 Agent 通过工具在任务中调用。数据落在工作区主根的 ``.lmw_snippets.json``。

存储结构::

    {
      "snippets": [
        {"id":"...", "title":"快速排序", "language":"python", "tags":["算法","排序"],
         "content":"def qsort(...): ...", "created_at":"...", "updated_at":"..."}
      ]
    }

与「提示词模板」的区别: 模板是给 LLM 的提示词文本; 片段是可直接粘贴进项目的
具体代码(带语言高亮语义, 但本模块不负责高亮渲染)。
"""

import json
import os
import time
import uuid

DEFAULT_FILENAME = ".lmw_snippets.json"

# 预置语言 (仅用于下拉提示, 任意非空 language 均接受)
DEFAULT_LANGUAGES = ["python", "javascript", "typescript", "java", "go", "rust",
                     "cpp", "c", "shell", "sql", "html", "css", "json", "yaml", "其他"]


def _default_path(base_dir=None):
    return os.path.join(base_dir or os.getcwd(), DEFAULT_FILENAME)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def load(base_dir=None):
    """读取片段库, 返回 ``{"snippets": [...]}``。文件缺失/损坏返回空库。"""
    path = _default_path(base_dir)
    if not os.path.isfile(path):
        return {"snippets": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"snippets": []}
    if not isinstance(data, dict) or not isinstance(data.get("snippets"), list):
        return {"snippets": []}
    return {"snippets": data["snippets"]}


def save(base_dir, data):
    path = _default_path(base_dir)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _norm_tags(tags):
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace(",", " ").split() if t.strip()]
    if isinstance(tags, (list, tuple)):
        return [str(t).strip() for t in tags if str(t).strip()]
    return []


def list_snippets(base_dir=None, language=None, tag=None):
    d = load(base_dir)
    snips = d["snippets"]
    if language:
        snips = [s for s in snips if (s.get("language") or "其他") == language]
    if tag:
        snips = [s for s in snips if tag in _norm_tags(s.get("tags"))]
    snips = sorted(snips, key=lambda s: (s.get("language", "其他"), s.get("title", "")))
    langs = []
    for s in d["snippets"]:
        l = s.get("language") or "其他"
        if l not in langs:
            langs.append(l)
    return {"snippets": snips, "languages": langs}


def get_snippet(tid, base_dir=None):
    d = load(base_dir)
    for s in d["snippets"]:
        if s.get("id") == tid:
            return s
    return None


def upsert(base_dir, title, content, language="其他", tags=None, tid=None):
    """新建或更新片段。返回 (record, is_new)。title 必填且唯一(同名则更新)。"""
    title = (title or "").strip()
    if not title:
        raise ValueError("片段标题不能为空")
    content = content or ""
    language = (language or "其他").strip() or "其他"
    tags = _norm_tags(tags)
    d = load(base_dir)
    snips = d["snippets"]
    rec = None
    if tid:
        for s in snips:
            if s.get("id") == tid:
                rec = s
                break
    if rec is None:
        for s in snips:
            if s.get("title") == title:
                rec = s
                break
    is_new = rec is None
    if is_new:
        rec = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "language": language,
            "tags": tags,
            "content": content,
            "created_at": _now(),
            "updated_at": _now(),
        }
        snips.append(rec)
    else:
        rec["title"] = title
        rec["language"] = language
        rec["tags"] = tags
        rec["content"] = content
        rec["updated_at"] = _now()
    save(base_dir, d)
    return rec, is_new


def delete(tid, base_dir=None):
    d = load(base_dir)
    before = len(d["snippets"])
    d["snippets"] = [s for s in d["snippets"] if s.get("id") != tid]
    removed = before - len(d["snippets"])
    save(base_dir, d)
    return removed
