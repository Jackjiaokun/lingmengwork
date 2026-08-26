"""提示词模板 (Prompt Templates): 可复用的提示词片段库。

用户在「📋 模板」管理页维护一组命名模板 (按分类组织), 在对话输入框一键插入,
也可让 Agent 通过工具在任务中调用。数据落在工作区主根的 ``.lmw_templates.json``。

存储结构::

    {
      "templates": [
        {"id":"...", "name":"代码审查", "category":"代码",
         "content":"请审查以下代码的...", "created_at":"...", "updated_at":"..."}
      ]
    }

与「专家·技能增强」的区别: 增强是注入 system 的角色设定; 模板是用户主动插入的
具体提示词文本(更偏"片段/样板"), 二者互补。
"""

import json
import os
import time
import uuid

DEFAULT_FILENAME = ".lmw_templates.json"

# 预置分类, 用户也可自定义(任意非空 category 均接受)
DEFAULT_CATEGORIES = ["通用", "代码", "写作", "调试", "翻译", "其他"]


def _default_path(base_dir=None):
    return os.path.join(base_dir or os.getcwd(), DEFAULT_FILENAME)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def load(base_dir=None):
    """读取模板库, 返回 ``{"templates": [...]}``。文件缺失/损坏返回空库。"""
    path = _default_path(base_dir)
    if not os.path.isfile(path):
        return {"templates": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"templates": []}
    if not isinstance(data, dict) or not isinstance(data.get("templates"), list):
        return {"templates": []}
    return {"templates": data["templates"]}


def save(base_dir, data):
    path = _default_path(base_dir)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_templates(base_dir=None):
    d = load(base_dir)
    tpls = sorted(d["templates"], key=lambda t: (t.get("category", "其他"), t.get("name", "")))
    cats = []
    for t in tpls:
        c = t.get("category") or "其他"
        if c not in cats:
            cats.append(c)
    return {"templates": tpls, "categories": cats}


def get_template(tid, base_dir=None):
    d = load(base_dir)
    for t in d["templates"]:
        if t.get("id") == tid:
            return t
    return None


def upsert(base_dir, name, content, category="其他", tid=None):
    """新建或更新模板。返回 (record, is_new)。name 必填且唯一(同分类内不强制, 全局唯一)。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("模板名称不能为空")
    content = content or ""
    category = (category or "其他").strip() or "其他"
    d = load(base_dir)
    tpls = d["templates"]
    rec = None
    if tid:
        for t in tpls:
            if t.get("id") == tid:
                rec = t
                break
    if rec is None:
        # 按名称查重
        for t in tpls:
            if t.get("name") == name:
                rec = t
                break
    is_new = rec is None
    if is_new:
        rec = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "category": category,
            "content": content,
            "created_at": _now(),
            "updated_at": _now(),
        }
        tpls.append(rec)
    else:
        rec["name"] = name
        rec["category"] = category
        rec["content"] = content
        rec["updated_at"] = _now()
    save(base_dir, d)
    return rec, is_new


def delete(tid, base_dir=None):
    d = load(base_dir)
    before = len(d["templates"])
    d["templates"] = [t for t in d["templates"] if t.get("id") != tid]
    removed = before - len(d["templates"])
    save(base_dir, d)
    return removed
