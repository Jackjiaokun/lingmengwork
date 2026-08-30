"""Phase 90 · 权限预设 UI 契约测试(仿 DSH permission-presets)。

验证权限模式三件事:
1. schema 暴露 agent.permission_mode, 三选一(设置中心自动渲染下拉)
2. Registry 的三种模式行为真实有效(不是摆设):
   plan 禁写 / acceptEdits 禁执行 / bypassPermissions 全允许
3. /api/chat 在请求未指定 mode 时, 回落到 config 的 agent.permission_mode
   (让"权限预设"从设置中心真正生效, 而不是硬编码默认值)
"""
import io
import os


from lingmengwork.tools.registry import Registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "lingmengwork", "web", "server.py")


def _server_src():
    with io.open(SERVER, encoding="utf-8") as f:
        return f.read()


def _reg(mode):
    """构造最小 Registry(只依赖 permission_mode, 不需要完整 cfg)。"""
    return Registry(roots=[os.getcwd()], permission_mode=mode)


# ---------------- 1. schema ----------------

def test_schema_has_permission_mode_field():
    src = _server_src()
    assert '"agent.permission_mode"' in src, "schema 必须暴露权限模式(供设置中心渲染)"


def test_schema_permission_mode_has_three_options():
    src = _server_src()
    m = _field_opts(src, "agent.permission_mode")
    assert m, "agent.permission_mode 必须有 options"
    for o in ("bypassPermissions", "plan", "acceptEdits"):
        assert o in m, "options 缺 %s" % o


def _field_opts(src, key):
    import re
    m = re.search(r'"%s"[^}]*?options":\s*\[([^\]]+)\]' % re.escape(key), src)
    if not m:
        return None
    return [o.strip().strip('"\'') for o in m.group(1).split(",")]


# ---------------- 2. 三种模式的行为 ----------------

def test_bypass_permissions_allows_exec_and_write():
    r = _reg("bypassPermissions")
    assert r._check_permission("run_command")[0] is True
    assert r._check_permission("write")[0] is True


def test_plan_mode_allows_read_only_blocks_write():
    # 注意: 真实工具名是 read_file / write_file(不是 read / write)
    r = _reg("plan")
    allowed, reason = r._check_permission("read_file")
    assert allowed, "计划模式应允许只读探查: %s" % reason
    blocked, reason2 = r._check_permission("write_file")
    assert not blocked, "计划模式必须禁止写操作"
    assert "计划模式" in reason2, "拒绝原因应说明当前模式, 便于用户理解"
    # 拒绝提示里列举的工具名必须是真实存在的(否则会误导用户照着输却仍被拒)
    assert "read_file" in reason2, "提示中的只读工具名必须是真实工具名"


def test_plan_mode_blocks_exec():
    r = _reg("plan")
    assert not r._check_permission("run_command")[0], "计划模式必须禁止执行命令"


def test_accept_edits_allows_write_but_blocks_exec():
    r = _reg("acceptEdits")
    assert r._check_permission("write_file")[0], "自动接受编辑应允许写"
    blocked, reason = r._check_permission("run_command")
    assert not blocked, "自动接受编辑应禁止 run_command"
    assert "run_command" in reason or "执行" in reason


def test_safe_tools_always_allowed():
    """think/undo/todo 等无害工具在任何模式下都应可用。"""
    for mode in ("plan", "acceptEdits"):
        r = _reg(mode)
        assert r._check_permission("think")[0], "%s 模式应允许 think" % mode
        assert r._check_permission("todo")[0], "%s 模式应允许 todo" % mode


def test_set_permission_mode_switches_behavior():
    """运行时切换模式必须立即改变权限判定(输入框切换权限的基础)。"""
    r = _reg("bypassPermissions")
    assert r._check_permission("run_command")[0]
    r.set_permission_mode("plan")
    assert not r._check_permission("run_command")[0], "切到计划模式后应立即禁止执行"


# ---------------- 3. /api/chat 回落 config ----------------

def test_chat_falls_back_to_config_permission_mode():
    """/api/chat 未指定 mode 时, 必须回落到 config 而非硬编码 bypassPermissions。"""
    src = _server_src()
    i = src.find("def _chat_sse")
    assert i > 0, "找不到 _chat_sse"
    body = src[i:i + 6000]
    assert 'cfg["agent"].get("permission_mode")' in body, \
        "_chat_sse 必须读取 config 的 permission_mode 作为回落"
    # 且不能还是写死的默认值
    assert 'body.get("mode") or "bypassPermissions"' not in body, \
        "不应再把默认权限硬编码为 bypassPermissions"
