"""apply_patch: 整文件多 hunk 智能补丁 (仿 Aider / Claude Code search-replace)。

模型一次提交多个独立的 search/replace 块, 每个块针对一个文件:
  {"path": "src/foo.py", "old": "<原文片段>", "new": "<新片段>"}
所有块先校验 (文件存在 / 片段唯一匹配), 全部通过才原子应用, 任一失败则整体回滚并报告。

相比 edit_file 单点替换, apply_patch 允许单次调用完成一份文件的多个分散改动,
且通过「先全量校验再应用」避免部分写入导致的半成品状态。
"""
from .common import ToolError, resolve_path
from .undo import get_default_stack


def _validate_block(block):
    path = block.get("path") or block.get("file")
    if not path:
        raise ToolError("apply_patch 块缺少 path/file 字段")
    old = block.get("old")
    if old is None:
        raise ToolError(f"apply_patch 块(path={path}) 缺少 old 字段")
    return path, old, block.get("new", "")


def apply_patch(args, ctx):
    blocks = args.get("blocks") or args.get("patches") or []
    if not isinstance(blocks, list) or not blocks:
        raise ToolError("apply_patch 需要 blocks: [{path, old, new}, ...] 列表")

    # 阶段1: 全部校验 (不改文件), 按文件分组, 校验每个 old 唯一存在
    by_file = {}  # resolved_path -> list[(old, new)]
    for b in blocks:
        path, old, new = _validate_block(b)
        fp = resolve_path(ctx["roots"], path)
        if not fp.exists() or fp.is_dir():
            raise ToolError(f"文件不存在或不是文件: {path}")
        text = fp.read_text(encoding="utf-8")
        if old not in text:
            first = old.splitlines()[0][:40] if old.splitlines() else ""
            from difflib import get_close_matches
            lines = text.splitlines()
            locs = [i + 1 for i, ln in enumerate(lines) if first and first in ln]
            hint = ""
            if locs:
                hint = " (文件内含 %d 处与首行相似行: %s)" % (len(locs), ", ".join(map(str, locs[:5])))
            else:
                pool = [ln.strip() for ln in lines if ln.strip()]
                close = get_close_matches(first, pool, n=3, cutoff=0.5)
                if close:
                    hint = " (近似行: " + " | ".join(close) + ")"
            raise ToolError(f"apply_patch: 在 {path} 中未找到 old 片段 (首行: {first}){hint}")
        cnt = text.count(old)
        if cnt > 1:
            fl = old.splitlines()[0][:40]
            locs = [i + 1 for i, ln in enumerate(text.splitlines()) if fl and fl in ln]
            raise ToolError(f"apply_patch: old 片段在 {path} 出现 {cnt} 次存在歧义 (行号: {locs[:5]}), 请提供更大上下文或改用 edit_file")
        by_file[str(fp.resolve())] = (fp, path, by_file.get(str(fp.resolve()), (None, None, []))[2] + [(old, new)])

    # 阶段2: 逐文件串行应用 (后一块基于前一块修改后的文本), 先推快照再写
    applied = []
    for _rp, (fp, disp, pairs) in by_file.items():
        original = fp.read_text(encoding="utf-8")
        get_default_stack().push(str(fp.resolve()), original)  # 单文件一次快照(可整体 undo)
        cur = original
        for old, new in pairs:
            cur = cur.replace(old, new, 1)
        fp.write_text(cur, encoding="utf-8")
        applied.append(f"{disp} ({len(pairs)}块, +{len(cur) - len(original)} 字符)")

    return "apply_patch 已应用 %d 个块:\n- " % len(applied) + "\n- ".join(applied)
