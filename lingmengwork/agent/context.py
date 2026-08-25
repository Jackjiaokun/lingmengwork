"""项目上下文自动装配: 仿 Claude Code 的自动上下文感知。

在首次任务前扫描项目, 生成结构化摘要 (目录树 + 关键配置 + .gitignore 规则),
注入 system prompt, 让模型无需每轮重复 glob/list_dir 即可把握项目全貌。

尊重 allowed_roots (只扫允许根) 与 _SKIP_DIRS (忽略噪声目录)。
"""
import os
from pathlib import Path

# 噪声目录(与 fs.py 保持一致)
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".workbuddy", "reference", "dist", "build", ".pytest_cache"}
# 关键配置文件: 抽取其全部内容作为上下文 (限制大小)
_KEY_CONFIGS = {
    "README.md", "README", "readme.md", "package.json", "pyproject.toml",
    "Cargo.toml", "go.mod", "requirements.txt", "pom.xml", "build.gradle",
    "tsconfig.json", "Makefile", "Dockerfile", "config.toml", "setup.py",
    "composer.json", "Gemfile", "pubspec.yaml",
}
# 单文件读取上限 (字符), 防超大文件撑爆上下文
_MAX_CONFIG_CHARS = 4000
# 目录树最大展示深度
_MAX_TREE_DEPTH = 3
# 目录树最多展示条目
_MAX_TREE_ENTRIES = 120


def build_project_context(roots, max_depth=_MAX_TREE_DEPTH, max_entries=_MAX_TREE_ENTRIES):
    """构建项目上下文摘要字符串。roots: 允许根列表(Path)。"""
    if not roots:
        return ""
    root = roots[0]
    parts = []
    parts.append(f"# 项目上下文 (根: {root})")
    parts.append("")

    # 1) 目录树
    tree = _build_tree(root, max_depth=max_depth, max_entries=max_entries)
    if tree:
        parts.append("## 目录结构")
        parts.append("```")
        parts.extend(tree)
        parts.append("```")
        parts.append("")

    # 2) 关键配置文件内容
    config_blocks = []
    for name in _KEY_CONFIGS:
        fp = root / name
        if fp.is_file():
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if len(text) > _MAX_CONFIG_CHARS:
                text = text[:_MAX_CONFIG_CHARS] + "\n... (已截断)"
            config_blocks.append(f"### {name}\n```\n{text}\n```")
    if config_blocks:
        parts.append("## 关键配置")
        parts.extend(config_blocks)
        parts.append("")

    # 3) .gitignore 规则 (若存在)
    gi = root / ".gitignore"
    if gi.is_file():
        try:
            rules = gi.read_text(encoding="utf-8", errors="replace").strip()
            if rules:
                parts.append("## .gitignore 规则")
                parts.append("```")
                parts.append(rules)
                parts.append("```")
                parts.append("")
        except Exception:
            pass

    return "\n".join(parts)


def _build_tree(root, max_depth, max_entries):
    lines = []
    count = [0]

    def walk(p, depth, prefix):
        if depth > max_depth or count[0] >= max_entries:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except Exception:
            return
        # 过滤噪声目录
        entries = [e for e in entries if e.name not in _SKIP_DIRS]
        for i, e in enumerate(entries):
            if count[0] >= max_entries:
                lines.append(f"{prefix}… (更多条目已省略)")
                return
            last = (i == len(entries) - 1)
            branch = "└─ " if last else "├─ "
            lines.append(f"{prefix}{branch}{e.name}{'/' if e.is_dir() else ''}")
            count[0] += 1
            if e.is_dir():
                nxt = "   " if last else "│  "
                walk(e, depth + 1, prefix + nxt)

    lines.append(root.name + "/")
    count[0] += 1
    walk(root, 1, "")
    return lines


def build_memory_context(roots, max_chars=3000):
    """读取项目根 MEMORY.md (跨会话长期记忆), 注入 system。

    返回形如 "## 项目记忆 (MEMORY.md)\n<内容>" 的字符串, 无则空串。
    """
    if not roots:
        return ""
    root = roots[0]
    mem = root / "MEMORY.md"
    if not mem.exists():
        return ""
    try:
        text = mem.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if not text.strip():
        return ""
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(MEMORY.md 已截断)"
    return "## 项目记忆 (上次会话积累, MEMORY.md)\n" + text
