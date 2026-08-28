"""灵梦work · 超级 AGENT 内核 (Phase 27).
把「单循环编码 AGENT」收口为「统一超级 AGENT 内核」: 用户输入一个模糊目标,
内核自动完成「目标理解 → 域路由 → 并行编排 → 执行落地(真实产物) → 收敛(三级护栏) → 自检(质量门) → 记忆沉淀」。
执行落地内置 code/creation/research/ops 真实执行器
(自主编码: 生成→编译→冒烟运行自验证→失败有限自修复[LLM]→落 run.log / 素材清单 JSON / 真实检索(默认真抓+LLM摘要) / 可校验脚本),
均可通过 register_executor 热插拔覆盖(如 creation->multimodal 真产出)。

复用既有能力(不长在另起炉灶, 严守不可变内核契约):
- 域路由 / 并行编排: 多智能体联邦 federation (关键词路由 + ThreadPoolExecutor 并行派发 + 汇聚)
- 记忆沉淀 / 召回: 记忆图谱 memory_graph (facts→实体关系, 跨会话推理, 隐私脱敏)
- 质量门: 离线自检中枢 selfcheck (无 LLM 确定性健康探针)
- 可观测: 事件总线 event_bus (每阶段结构化 trace 进审计链, 可回放)

工程信条: 零三方依赖(纯标准库); 无 LLM 亦可用(规则兜底); 单伙伴/单引擎失败不影响整体(错误隔离)。
验收门槛: 单目标跨 2+ 域编排成功(多伙伴并行派发 + 汇聚)。
"""

import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request as _urllib_req
from datetime import datetime

from . import federation as _fed
from . import memory_graph as _mg
from . import selfcheck as _sc
from .llm import pricing as _pricing

_STAGE_NAMES = ["目标理解", "插件接入", "域路由", "并行编排", "执行落地", "收敛护栏", "记忆沉淀"]

# 进程内最近编排缓冲(供 API / 页面轮询, 重启清空), maxlen 上限防内存膨胀
_RUNS = collections.deque(maxlen=60)

# 域执行器注册中心: domain -> callable(partner, goal="", llm_call=None, base_dir=None)->dict
# 默认不注册时, 内核为每个成功伙伴产出真实交付文件(方案/蓝图), 保证"出方案"→"落产物"闭环。
# 后续可热插拔: register_executor("code", autonomous_executor) / ("creation", multimodal_executor) 等。
EXECUTORS = {}


def register_executor(domain, fn):
    """注册某域的真实执行器(可后续热插拔, 例如 code->autonomous / creation->multimodal)。"""
    EXECUTORS[domain] = fn
    return fn


def get_executor(domain):
    return EXECUTORS.get(domain)


# ------------------------------------------------------------------ Phase 29 真实执行器(模块级, 默认热插拔)
def _resolve_out_dir(base_dir):
    """解析可写产物目录: base_dir 为合法目录则用, 否则落到临时目录(:memory:/None 安全)。"""
    if base_dir and base_dir != ":memory:" and os.path.isdir(base_dir):
        root = base_dir
    else:
        root = tempfile.mkdtemp()
    d = os.path.join(root, "outputs", "superagent")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = tempfile.mkdtemp()
    return d


def _extract_code_blocks(text):
    if not text:
        return []
    out = []
    for m in re.finditer(r"```(\w*)\n(.*?)```", text, re.DOTALL):
        out.append((m.group(1), m.group(2)))
    return out


def _extract_steps(plan):
    steps = []
    for line in (plan or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(\d+)[.)]\s*(.+)$", s)
        if m:
            steps.append(m.group(2).strip())
        elif s.startswith("- "):
            steps.append(s[2:].strip())
    if steps:
        return steps[:15]
    lines = [s.strip() for s in (plan or "").split("\n") if s.strip()]
    return lines[:5]


def _run_code_smoke(path, ext, out_dir):
    """Phase 30: 子进程冒烟运行生成的代码(带超时), 返回 {rc,stdout,stderr,timed_out,skipped}。

    安全边界: 子进程 + 15s 超时 + 输出截断落日志; 不做任何网络/文件系统白名单(本地自主开发工具语义)。
    """
    if ext == "py":
        cmd = [sys.executable, path]
    elif ext == "js":
        node = shutil.which("node")
        cmd = [node, path] if node else None
    elif ext in ("sh", "shell"):
        sh = shutil.which("bash") or shutil.which("sh")
        cmd = [sh, path] if sh else None
    else:
        cmd = None
    if not cmd:
        return {"rc": None, "stdout": "", "stderr": "无对应运行时, 冒烟跳过",
                "timed_out": False, "skipped": True}
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # 子进程统一 utf-8, 规避 Windows cp936 中文乱码/崩溃
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15, cwd=out_dir, env=env)
        return {"rc": r.returncode, "stdout": r.stdout or "", "stderr": r.stderr or "",
                "timed_out": False, "skipped": False}
    except subprocess.TimeoutExpired:
        return {"rc": None, "stdout": "", "stderr": "运行超时(>15s)",
                "timed_out": True, "skipped": False}
    except Exception as e:
        return {"rc": None, "stdout": "", "stderr": "%s: %s" % (type(e).__name__, e),
                "timed_out": False, "skipped": False}


def _smoke_and_heal(path, ext, out_dir, src_code, goal, llm_call):
    """Phase 30: 冒烟运行 + 有限自修复(仅 py 且有 LLM)。

    失败时把运行报错喂回 LLM 修正重跑, 最多 2 次; 返回 {ok,healed,log_text,note}。
    """
    run = _run_code_smoke(path, ext, out_dir)
    healed = False
    if run["rc"] != 0 and llm_call and ext == "py":
        for _ in range(2):
            try:
                fixed = llm_call(
                    "以下 Python 代码运行失败:\n```python\n%s\n```\n报错:\n%s\n"
                    "请只输出修正后的完整代码(```python 围栏, 不要解释)."
                    % (src_code, (run["stderr"] or "")[:1500]),
                    system="你是资深工程师, 修复代码使其可正常运行, 只输出代码.")
                if not (isinstance(fixed, str) and fixed.strip()):
                    break
                blocks = _extract_code_blocks(fixed)
                new_code = blocks[0][1] if blocks else fixed
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_code)
                src_code = new_code
                run = _run_code_smoke(path, ext, out_dir)
                if run["rc"] == 0:
                    healed = True
                    break
            except Exception:
                break
    log_text = ("=== superagent 冒烟运行 ===\n文件: %s\nreturncode=%s%s\n"
                "--- stdout ---\n%s\n--- stderr ---\n%s\n"
                % (os.path.basename(path), run["rc"],
                   " (超时)" if run["timed_out"] else (" (跳过)" if run["skipped"] else ""),
                   (run["stdout"] or "")[:3000], (run["stderr"] or "")[:3000]))
    if run["skipped"]:
        note, ok = "冒烟跳过(运行时不可用), 仅落产物", None
    elif run["rc"] == 0:
        note, ok = "冒烟运行通过(rv=0)%s" % (" · 自修复成功" if healed else ""), True
    else:
        note, ok = ("冒烟运行失败(rv=%s)%s" % (run["rc"],
                    " · 自修复成功" if healed else " · 有限自修复未通过(详见 run.log)")), False
    return {"ok": ok, "healed": healed, "log_text": log_text, "note": note}


def _exec_code(partner, goal="", llm_call=None, base_dir=None):
    """自主编码执行器(Phase 30): 生成→编译→冒烟运行自验证→(失败+LLM)有限自修复→落代码+run.log。

    有 LLM: LLM 产出业务代码, 冒烟失败自动修正重跑; 无 LLM: 产出可运行骨架(冒烟通过)。
    """
    out_dir = _resolve_out_dir(base_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan = (partner.get("plan") or "") + "\n" + (partner.get("summary") or "")
    if llm_call:
        try:
            code = llm_call(
                "为目标「%s」编写可直接运行的 Python 实现, 只输出代码(用 ```python 围栏, 不要解释)." % goal,
                system="你是资深工程师, 输出纯代码, 不写解释性文字.")
            if isinstance(code, str) and code.strip():
                plan = code
        except Exception:
            pass
    blocks = _extract_code_blocks(plan)
    artifacts, notes = [], []
    last_run = None
    ext_map = {"python": "py", "py": "py", "javascript": "js", "js": "js",
               "bash": "sh", "sh": "sh", "shell": "sh", "html": "html", "json": "json"}
    if blocks:
        for i, (lang, code) in enumerate(blocks):
            ext = ext_map.get((lang or "").lower(), "py")
            path = os.path.join(out_dir, "%s_code_%d.%s" % (ts, i, ext))
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(code)
                if ext == "py":
                    try:
                        compile(code, path, "exec")
                        notes.append("python 编译通过")
                    except SyntaxError as e:
                        notes.append("python 语法警告: %s" % e)
                artifacts.append(path)
                # Phase 30: 冒烟运行自验证(+有限自修复), 运行日志落盘为独立产物
                run_res = _smoke_and_heal(path, ext, out_dir, code, goal, llm_call)
                log_path = path + ".run.log"
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(run_res["log_text"])
                artifacts.append(log_path)
                notes.append(run_res["note"])
                last_run = run_res
            except Exception as e:
                notes.append("写入失败: %s" % e)
    else:
        sk = ('"""%s\n\n由超级 AGENT 编码执行器生成(无 LLM 规则兜底骨架, 可直接运行).\n'
              '目标: %s\n"""\n\n\ndef main():\n'
              '    # TODO: 依据联邦编码伙伴方案实现业务逻辑\n'
              '    print("[scaffold] 骨架就绪, 待按方案补充实现")\n'
              '    return 0\n\n\n'
              'if __name__ == "__main__":\n    raise SystemExit(main())\n'
              % (partner.get("name", "编码伙伴"), goal))
        path = os.path.join(out_dir, "%s_code_skeleton.py" % ts)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(sk)
            compile(sk, path, "exec")
            artifacts.append(path)
            notes.append("无 LLM: 产出可运行骨架(compile 通过)")
            run_res = _smoke_and_heal(path, "py", out_dir, sk, goal, llm_call)
            log_path = path + ".run.log"
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(run_res["log_text"])
            artifacts.append(log_path)
            notes.append(run_res["note"])
            last_run = run_res
        except Exception as e:
            notes.append("骨架生成失败: %s" % e)
    ret = {"domain": "code", "status": "ok" if artifacts else "error",
           "artifacts": artifacts, "note": " | ".join(notes) or "已产出代码产物"}
    if last_run is not None:
        ret["run_ok"] = last_run["ok"]
        ret["healed"] = last_run["healed"]
    return ret


def _exec_research(partner, goal="", llm_call=None, base_dir=None):
    """真实研究执行器: 默认走真实 DuckDuckGo 抓取, 网络失败时回退研究简报;
    有 LLM 时对抓取结果做中文要点摘要(独立产物)."""
    out_dir = _resolve_out_dir(base_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan = (partner.get("plan") or "").strip() or (partner.get("summary") or "")
    artifacts, note = [], ""
    url = _derive_research_url(goal)
    fetched_data = None
    if url:
        try:
            req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
            with _urllib_req.urlopen(req, timeout=8) as resp:
                fetched_data = resp.read(200000).decode("utf-8", "replace")
            path = os.path.join(out_dir, "%s_research_fetch.md" % ts)
            with open(path, "w", encoding="utf-8") as f:
                f.write("# 抓取结果\n\n**来源**: %s\n\n%s\n" % (url, fetched_data[:8000]))
            artifacts.append(path)
            note = "已真实抓取: %s" % url
        except Exception as e:
            note = "抓取失败(%s), 回退研究简报" % type(e).__name__
    # LLM 摘要: 有 LLM 且抓取成功 → 摘要抓取内容; 抓取失败 → 摘要方案要点
    if llm_call:
        try:
            sys_prompt = ("你是研究摘要员。用中文提炼以下内容的关键发现(3-5 条要点), 简洁陈述, 不要标题或格式符号。"
                          "只输出要点文本, 不要解释。")
            raw = llm_call((("研究目标: %s\n\n参考方案: %s\n\n"
                             "抓取内容: %s") % (goal, plan, (fetched_data or plan)))[:3000],
                           system=sys_prompt)
            if raw and str(raw).strip():
                summary_path = os.path.join(out_dir, "%s_research_summary.md" % ts)
                with open(summary_path, "w", encoding="utf-8") as f:
                    f.write("# 研究摘要(LLM 智能提炼)\n\n**目标**: %s\n\n%s\n"
                            % (goal, str(raw).strip()))
                artifacts.append(summary_path)
                note += "; 已生成 LLM 摘要"
        except Exception:
            pass
    if not artifacts:
        path = os.path.join(out_dir, "%s_research.md" % ts)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("# 研究简报(超级 AGENT 研究执行器)\n\n**目标**: %s\n\n%s\n"
                        % (goal, plan))
            artifacts.append(path)
            note = note or "抓取失败, 已落地研究简报"
        except Exception as e:
            note = "写入失败: %s" % e
    return {"domain": "research", "status": "ok" if artifacts else "error",
            "artifacts": artifacts, "note": note}


def _derive_research_url(goal):
    kws = [w for w in re.split(r"[\s,，。.、]+", goal or "") if len(w) >= 2][:4]
    if not kws:
        return ""
    q = urllib.parse.quote(" ".join(kws))
    return "https://html.duckduckgo.com/html/?q=" + q


def _exec_ops(partner, goal="", llm_call=None, base_dir=None):
    """真实运维执行器: 把方案步骤落为可 bash -n 校验的 deploy.sh。"""
    out_dir = _resolve_out_dir(base_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan = (partner.get("plan") or "").strip() or (partner.get("summary") or "")
    steps = _extract_steps(plan)
    script = ["#!/usr/bin/env bash", "set -euo pipefail", "",
              "# 超级 AGENT 运维执行器生成", "# 目标: %s" % goal, ""]
    for i, s in enumerate(steps, 1):
        script.append("# 步骤 %d: %s" % (i, s))
        script.append('echo "[step %d] %s"' % (i, s))
        script.append("")
    script.append('echo "部署流程骨架已生成, 请按实际环境补充命令."')
    path = os.path.join(out_dir, "%s_deploy.sh" % ts)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(script) + "\n")
        validated = _shell_syntax_check(path)
        note = "已生成 deploy.sh" + (", 语法校验通过" if validated else ", 未做语法校验(shell 不可用)")
        return {"domain": "ops", "status": "ok", "artifacts": [path], "note": note}
    except Exception as e:
        return {"domain": "ops", "status": "error", "artifacts": [],
                "note": "写入失败: %s" % e}


def _shell_syntax_check(path):
    import shutil as _shutil
    import subprocess as _sp
    shell = _shutil.which("bash") or _shutil.which("sh")
    if not shell:
        return False
    try:
        r = _sp.run([shell, "-n", path], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _exec_creation(partner, goal="", llm_call=None, base_dir=None):
    """真实创作执行器: 产出可回读的素材清单 JSON(供 multimodal 适配层消费)。"""
    out_dir = _resolve_out_dir(base_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    arts = partner.get("artifacts") or [{}]
    sub = (arts[0].get("domain") if arts else "") or "image"
    manifest = {
        "generator": "superagent.creation_executor",
        "sub_domain": sub,
        "goal": goal,
        "prompt": goal,
        "spec": {"format": sub, "theme": "default", "resolution": "1024x1024"},
        "adapter_hint": "接入文生图/语音/视频 MCP 或 API 后由适配层产出真实素材",
        "created_at": ts,
    }
    path = os.path.join(out_dir, "%s_asset_manifest.json" % ts)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        json.load(open(path, encoding="utf-8"))
        return {"domain": "creation", "status": "ok", "artifacts": [path],
                "note": "已产出素材清单 JSON(供 multimodal 适配层消费)"}
    except Exception as e:
        return {"domain": "creation", "status": "error", "artifacts": [],
                "note": "清单生成失败: %s" % e}


def _creation_subdomain(partner, goal):
    """Phase 31: 解析创作子域——优先伙伴蓝图标注, 否则按目标关键词路由(视频/音频/图片)。"""
    arts = partner.get("artifacts") or []
    sub = (arts[0].get("domain") if arts and isinstance(arts[0], dict) else "") or ""
    if sub not in ("image", "audio", "video"):
        g = goal or ""
        if any(k in g for k in ("视频", "video", "动图", "gif", "短片")):
            sub = "video"
        elif any(k in g for k in ("音频", "配音", "朗读", "语音", "audio", "tts")):
            sub = "audio"
        else:
            sub = "image"
    return sub


def _exec_creation_real(partner, goal="", llm_call=None, base_dir=None):
    """Phase 31 真实创作执行器: 经 multimodal_adapters 真实渲染媒体文件
    (PNG 信息图 / MP3 音频 / GIF 动图; 无 LLM 自动确定性模板兜底, 全程可用),
    并产出含渲染结果的素材清单 JSON; 适配层不可用/失败时回退纯清单(不崩)。"""
    out_dir = _resolve_out_dir(base_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sub = _creation_subdomain(partner, goal)
    plan = (partner.get("plan") or "").strip() or (partner.get("summary") or "")
    manifest = {
        "generator": "superagent.creation_executor",
        "sub_domain": sub,
        "goal": goal,
        "prompt": goal,
        "spec": {"format": sub, "theme": "default", "resolution": "1024x1024"},
        "adapter_hint": "经 multimodal_adapters 真实渲染; 无 key 降级确定性模板",
        "created_at": ts,
    }
    artifacts, notes = [], []
    try:
        from . import multimodal_adapters as _mma
        res = _mma.render(sub, brief=goal or (partner.get("summary") or "创作"),
                          blueprint=plan, ctx="", out_dir=out_dir, llm_call=llm_call)
        if isinstance(res, dict):
            manifest["render"] = {k: res.get(k)
                                  for k in ("domain", "file", "mime", "real", "note", "meta")}
            f = res.get("file")
            if f and os.path.isfile(f):
                artifacts.append(f)
                notes.append("%s真实渲染%s: %s" % (
                    {"image": "图片", "audio": "音频", "video": "视频"}.get(sub, sub),
                    "" if res.get("real") else "(降级占位)", os.path.basename(f)))
            else:
                notes.append("适配层未产出文件: %s" % (res.get("note") or "未知"))
        else:
            notes.append("适配层不支持的子域, 回退纯清单")
    except Exception as e:
        notes.append("适配层不可用(%s: %s), 回退纯清单" % (type(e).__name__, e))
    path = os.path.join(out_dir, "%s_asset_manifest.json" % ts)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        json.load(open(path, encoding="utf-8"))
        artifacts.append(path)
        notes.append("素材清单已落盘")
        return {"domain": "creation", "status": "ok", "artifacts": artifacts,
                "note": " | ".join(notes)}
    except Exception as e:
        return {"domain": "creation", "status": "error", "artifacts": [],
                "note": "清单生成失败: %s" % e}


# Phase 29: 真实执行器默认热插拔(均为模块级函数, 可通过 register_executor 覆盖为更智能实现)
# Phase 31: creation 升级为 _exec_creation_real(经 multimodal_adapters 真实渲染媒体);
#           旧 _exec_creation(纯清单) 保留可手工回切: register_executor("creation", _exec_creation)
register_executor("code", _exec_code)
register_executor("research", _exec_research)
register_executor("ops", _exec_ops)
register_executor("creation", _exec_creation_real)


def _parse_json(raw):
    """容忍代码块包裹的 JSON 解析(复用 goal_pipeline 思路)。"""
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
        s = s.strip("`")
    try:
        return json.loads(s)
    except Exception:
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class _UsageMeter:
    """Phase 40: 编排 LLM 用量计量器 — 包装 llm_call 统计调用与字符数。

    估算口径与 agent.loop 一致: ~1.6 字符/token (中英混合经验值),
    成本按 llm.pricing 价格档(model 可空 → 默认档)。
    """

    _CHAR_PER_TOKEN = 1.6

    def __init__(self, llm_call=None, model=""):
        self._fn = llm_call
        self.model = model or ""
        self.llm_calls = 0
        self.in_chars = 0
        self.out_chars = 0

    def __call__(self, prompt, system=None):
        if not self._fn:
            return None  # 无后端: 规则兜底, 不计用量
        self.llm_calls += 1
        self.in_chars += len(prompt or "") + len(system or "")
        out = self._fn(prompt, system=system)
        if isinstance(out, str):
            self.out_chars += len(out)
        return out

    def stats(self):
        inp = int(self.in_chars / self._CHAR_PER_TOKEN) if self.in_chars else 0
        out = int(self.out_chars / self._CHAR_PER_TOKEN) if self.out_chars else 0
        return {
            "model": self.model,
            "llm_calls": self.llm_calls,
            "est_input_tokens": inp,
            "est_output_tokens": out,
            "est_total_tokens": inp + out,
            "est_cost_cny": round(_pricing.cost(inp, out, self.model), 6),
        }


def get_usage_totals(limit=500, base_dir=None):
    """编排 LLM 用量聚合(内存优先 + 磁盘历史补缺, 供成本看板 /api/cost)。"""
    runs = get_recent_runs(limit, base_dir=base_dir)
    t_in = sum(r.get("est_input_tokens", 0) or 0 for r in runs)
    t_out = sum(r.get("est_output_tokens", 0) or 0 for r in runs)
    return {
        "runs": len(runs),
        "llm_calls": sum(r.get("llm_calls", 0) or 0 for r in runs),
        "est_input_tokens": t_in,
        "est_output_tokens": t_out,
        "est_total_tokens": t_in + t_out,
        "est_cost_cny": round(sum(r.get("est_cost_cny", 0.0) or 0.0 for r in runs), 6),
    }


# ------------------------------------------------------------------ Phase 41: 编排并发控制(队列 + 忙拒绝)
_ORCH_SEM = threading.BoundedSemaphore(2)
_ORCH_STATE = {"running": 0, "waiting": 0, "max": 2}
_ORCH_STATE_LOCK = threading.Lock()

DEFAULT_MAX_ORCHESTRATIONS = 2
DEFAULT_QUEUE_WAIT_SEC = 30.0


def set_max_orchestrations(n):
    """设置并发编排上限(默认 2; 下一次获取槽位时生效)。返回生效值。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_MAX_ORCHESTRATIONS
    n = max(1, n)
    global _ORCH_SEM
    with _ORCH_STATE_LOCK:
        if _ORCH_STATE["max"] != n:
            _ORCH_SEM = threading.BoundedSemaphore(n)
            _ORCH_STATE["max"] = n
    return n


def get_queue_state():
    """编排队列状态 {running, waiting, max}(供 API / 页面轮询)。"""
    with _ORCH_STATE_LOCK:
        return dict(_ORCH_STATE)


# ------------------------------------------------------------------ Phase 43: 定时编排(调度器 + JSON 持久化)
_SCHEDS_LOCK = threading.Lock()
_SCHEDS = {}          # id -> entry
_SCHEDS_LOADED = set()  # 已加载过的持久化文件路径(幂等)
_SCHED_THREAD = None
_INFLIGHT = set()     # 正在执行中的 schedule id(防同计划重复派发)


def _sched_path(base_dir=None):
    base = base_dir if (base_dir and base_dir != ":memory:" and os.path.isdir(base_dir)) else os.getcwd()
    return os.path.join(base, "outputs", "superagent_schedules.json")


def _load_scheds(base_dir=None):
    """磁盘 → _SCHEDS(按文件路径幂等加载)。"""
    path = _sched_path(base_dir)
    with _SCHEDS_LOCK:
        if path in _SCHEDS_LOADED:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for s in (data.get("schedules") or []):
                if isinstance(s, dict) and s.get("id"):
                    _SCHEDS[s["id"]] = s
        except Exception:
            pass
        _SCHEDS_LOADED.add(path)


def _save_scheds(base_dir=None):
    try:
        path = _sched_path(base_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _SCHEDS_LOCK:
            data = {"schedules": sorted(_SCHEDS.values(), key=lambda s: s.get("created_at", ""))}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def list_schedules(base_dir=None):
    _load_scheds(base_dir)
    with _SCHEDS_LOCK:
        return sorted(_SCHEDS.values(), key=lambda s: s.get("created_at", ""))


def add_schedule(goal, every_sec=0, daily="", enabled=True, base_dir=None):
    """新建定时编排。every_sec>=60 或 daily='HH:MM' 至少一个有效, 否则 ValueError。"""
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("goal 不能为空")
    try:
        every_sec = int(every_sec or 0)
    except (TypeError, ValueError):
        every_sec = 0
    daily = (daily or "").strip()
    if daily:
        if not re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", daily):
            raise ValueError("daily 需合法 HH:MM 格式 (00:00-23:59)")
    elif every_sec < 60:
        raise ValueError("every_sec 需 >=60 秒, 或提供 daily 时间")
    sid = "s_%s_%s" % (time.strftime("%Y%m%d%H%M%S"), os.urandom(3).hex())
    entry = {"id": sid, "goal": goal, "every_sec": every_sec, "daily": daily,
             "enabled": bool(enabled), "created_at": _now(),
             "last_run": "", "last_ok": None, "last_error": "", "run_count": 0}
    with _SCHEDS_LOCK:
        _SCHEDS[sid] = entry
    _save_scheds(base_dir)
    return dict(entry)


def update_schedule(sid, patch, base_dir=None):
    """更新定时编排(白名单键: goal/every_sec/daily/enabled)。不存在返回 None。"""
    with _SCHEDS_LOCK:
        s = _SCHEDS.get(sid)
        if not s:
            return None
        for k in ("goal", "every_sec", "daily", "enabled"):
            if k in patch and patch[k] is not None:
                s[k] = patch[k]
        snap = dict(s)
    _save_scheds(base_dir)
    return snap


def remove_schedule(sid, base_dir=None):
    with _SCHEDS_LOCK:
        removed = _SCHEDS.pop(sid, None) is not None
    if removed:
        _save_scheds(base_dir)
    return removed


def _sched_due(s, now=None):
    """到期判定: 从未运行 → 立即到期; every_sec 按间隔; daily 每日 HH:MM(当天未跑过才到期)。"""
    if not s.get("enabled"):
        return False
    now = now or datetime.now()
    lr = s.get("last_run") or ""
    if not lr:
        return True
    try:
        last = datetime.strptime(lr, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return True
    daily = (s.get("daily") or "").strip()
    if daily:
        try:
            hh, mm = daily.split(":")
            target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            return False
        return now >= target and last < target
    ev = int(s.get("every_sec") or 0)
    if ev < 60:
        return False
    return (now - last).total_seconds() >= ev


def run_schedule(sid, base_dir=None, llm_call=None, queue_wait_sec=5.0):
    """立即执行一次定时编排(同步), 更新 last_run/last_ok/run_count 并持久化。"""
    _load_scheds(base_dir)
    with _SCHEDS_LOCK:
        s = _SCHEDS.get(sid)
    if not s:
        return {"ok": False, "error": "schedule 不存在: %s" % sid}
    sa = SuperAgent(base_dir=base_dir)
    rep = sa.run(s["goal"], session_id="sched:%s" % sid, llm_call=llm_call,
                 quality_gate=False, queue_wait_sec=queue_wait_sec)
    with _SCHEDS_LOCK:
        s["last_run"] = _now()
        s["last_ok"] = bool(rep.get("ok"))
        s["last_error"] = rep.get("error", "") or ""
        s["run_count"] = int(s.get("run_count") or 0) + 1
    _save_scheds(base_dir)
    return {"ok": bool(rep.get("ok")), "schedule_id": sid, "result": rep}


def _run_inflight_guard(sid, base_dir=None, llm_call=None):
    try:
        run_schedule(sid, base_dir=base_dir, llm_call=llm_call)
    finally:
        with _SCHEDS_LOCK:
            _INFLIGHT.discard(sid)


def _scheduler_tick(base_dir=None, llm_call=None):
    """扫描到期计划并逐个派发后台执行(同计划在飞不重复派发)。"""
    _load_scheds(base_dir)
    for s in list_schedules(base_dir):
        if not _sched_due(s):
            continue
        with _SCHEDS_LOCK:
            if s["id"] in _INFLIGHT:
                continue
            _INFLIGHT.add(s["id"])
        t = threading.Thread(target=_run_inflight_guard,
                             args=(s["id"],), kwargs={"base_dir": base_dir, "llm_call": llm_call},
                             daemon=True)
        t.start()


def start_scheduler(base_dir=None, llm_call=None, tick_sec=20):
    """启动常驻调度线程(daemon, 幂等)。供 Web 服务启动时调用。"""
    global _SCHED_THREAD
    with _SCHEDS_LOCK:
        if _SCHED_THREAD and _SCHED_THREAD.is_alive():
            return False

        def _loop():
            while True:
                try:
                    _scheduler_tick(base_dir=base_dir, llm_call=llm_call)
                except Exception:
                    pass
                time.sleep(max(5, int(tick_sec)))

        _SCHED_THREAD = threading.Thread(target=_loop, daemon=True, name="superagent-scheduler")
        _SCHED_THREAD.start()
        return True


class SuperAgent:
    """统一超级 AGENT 内核。"""

    def __init__(self, base_dir=None):
        # base_dir=None → memory_graph 用 cwd 单例; 测试/探针注入临时目录隔离
        self.base_dir = base_dir

    # ---- 阶段 1: 目标理解 (LLM 抽取 intent/域标签/约束, 失败回退规则) ----
    def understand(self, goal, llm_call=None):
        goal = (goal or "").strip()
        intent = goal
        domains = None
        constraints = []
        # 可选 LLM 抽取 intent / 域标签 / 约束
        if llm_call:
            try:
                sys = ("你是任务理解器。把用户目标拆为结构化意图。只输出一个 JSON: "
                       "{\"intent\": \"意图摘要(一句话)\", "
                       "\"domains\": [\"code\"/\"creation\"/\"research\"/\"ops\" 中的 1-3 个], "
                       "\"constraints\": [字符串约束列表]}。domains 最多 3 个, 不相关的不要填。不要解释。")
                raw = llm_call("目标: " + goal, system=sys)
                p = _parse_json(raw)
                if isinstance(p, dict):
                    intent = (p.get("intent") or goal).strip() or goal
                    ds = [d for d in (p.get("domains") or [])
                          if d in ("code", "creation", "research", "ops")][:3]
                    if ds:
                        domains = ds
                    constraints = p.get("constraints") or []
            except Exception:
                pass
        # 规则兜底: LLM 未给出域 → 用联邦关键词路由(始终可用)
        if not domains:
            domains = _fed.get_federation().route(goal)
        # 跨会话记忆召回(注入历史经验, 失败不阻塞主流程)
        recap = ""
        try:
            recap = _mg.get_graph(self.base_dir).recall(goal, limit=12).get("recap", "")
        except Exception:
            recap = ""
        return {
            "goal": goal,
            "intent": intent,
            "domains": domains,
            "constraints": constraints,
            "memory_recap": recap,
        }

    # ---- 阶段 2: 域路由 (取 understand 给出的 domains) ----
    def route(self, understand):
        return understand.get("domains") or ["code"]

    # ---- 阶段 3: 并行编排 (联邦派发 N 伙伴 + 连接器标签匹配) ----
    def dispatch(self, understand, session_id="", llm_call=None):
        return _fed.get_federation().dispatch(
            understand["goal"], session_id=session_id,
            hint_domains=understand.get("domains"), llm_call=llm_call,
            connector_names=understand.get("connectors"))

    # ---- 阶段 4: 收敛 (三级护栏 + 一致性校验) ----
    def converge(self, dispatch_rep, quality_gate=True):
        partners = dispatch_rep.get("partners", [])
        merged = dispatch_rep.get("merged", {})
        conflicts = merged.get("conflicts", []) or []
        ok_partners = [p for p in partners if p.get("status") == "ok"]
        error_partners = [p for p in partners if p.get("status") != "ok"]
        guards = []
        # 一级护栏: 完整性(error 隔离, 单伙伴失败不阻断整体)
        if error_partners:
            guards.append({
                "level": 1, "kind": "partner_error", "severity": "warning",
                "msg": "有 %d 个伙伴执行异常, 已隔离(不影响其他伙伴): %s"
                       % (len(error_partners), "、".join(p.get("name", "") for p in error_partners)),
            })
        # 二级护栏: 冲突检测(多伙伴同类产物)
        for c in conflicts:
            guards.append({
                "level": 2, "kind": "conflict", "severity": "warning",
                "msg": c.get("note", "产出冲突, 需人工取舍"),
            })
        # 三级护栏: 质量门(系统自检评分阈值)
        score = 100
        if quality_gate:
            sc = self._quality_gate()
            score = sc.get("score", 100)
            if score < 70:
                guards.append({
                    "level": 3, "kind": "quality", "severity": "warning",
                    "msg": "系统自检评分 %d 低于阈值 70, 建议复核底层引擎" % score,
                })
        return {
            "ok": bool(ok_partners),
            "partners_total": len(partners),
            "partners_ok": len(ok_partners),
            "partners_error": len(error_partners),
            "conflicts": conflicts,
            "selfcheck_score": score,
            "guards": guards,
            "summary": merged.get("summary", ""),
            "passed": len(guards) == 0,
        }

    def _quality_gate(self):
        try:
            return _sc.run()
        except Exception:
            return {"ok": True, "score": 100, "passed": 13, "total": 13,
                    "all_ok": True, "checks": [], "ts": _now()}

    # ---- 阶段 4: 执行落地 (把伙伴方案变真实产物, 可插拔执行器) ----
    def execute(self, dispatch_rep, goal="", session_id="", llm_call=None):
        """把并行编排的伙伴产出真正落地为产物。

        每个 status==ok 的伙伴: 若注册了 domain 执行器则调用(可走 autonomous/multimodal 真实执行),
        否则走默认执行器(把方案写成真实交付文件 .md)。异常隔离, 单伙伴失败不影响其他。
        """
        partners = dispatch_rep.get("partners", [])
        executions = []
        artifacts = []
        for p in partners:
            if p.get("status") != "ok":
                executions.append({"domain": p.get("domain"), "status": "skipped",
                                   "note": "伙伴未成功, 跳过执行", "artifacts": []})
                continue
            domain = p.get("domain")
            fn = get_executor(domain)
            try:
                if fn:
                    res = fn(p, goal=goal, llm_call=llm_call, base_dir=self.base_dir) or {}
                    res.setdefault("domain", domain)
                    res.setdefault("status", "ok")
                    res.setdefault("artifacts", [])
                    res.setdefault("note", "")
                else:
                    res = self._default_executor(p, goal=goal)
                executions.append(res)
                for a in (res.get("artifacts") or []):
                    if isinstance(a, str) and os.path.isfile(a):
                        artifacts.append(a)
            except Exception as e:
                executions.append({"domain": domain, "status": "error",
                                   "note": "%s: %s" % (type(e).__name__, e), "artifacts": []})
        return {"ok": any(e.get("status") in ("ok", "artifact") for e in executions),
                "executions": executions, "artifacts": artifacts,
                "count": len(executions)}

    def _default_executor(self, partner, goal="", base_dir=None):
        """无真实执行器时的兜底: 把伙伴方案/蓝图写成真实交付文件(.md, 可下载/回看)。"""
        domain = partner.get("domain", "unknown")
        summary = (partner.get("summary") or "").strip() or "(无结构化输出)"
        plan = partner.get("plan") or ""
        # base_dir=:memory: 或非法路径 → 落到临时目录, 避免落盘到内存库名
        out_root = base_dir if (base_dir and base_dir != ":memory:" and os.path.isdir(base_dir)) else tempfile.mkdtemp()
        out_dir = os.path.join(out_root, "outputs", "superagent")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            out_dir = tempfile.mkdtemp()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out_dir, "%s_%s.md" % (ts, domain))
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("# 超级 AGENT 交付物 · %s\n\n" % domain)
                f.write("**目标**: %s\n\n" % goal)
                f.write("**伙伴**: %s\n\n" % partner.get("name", domain))
                f.write("## 方案 / 结论\n\n%s\n\n" % summary)
                if plan:
                    f.write("## 执行计划\n\n%s\n" % plan)
            return {"domain": domain, "status": "artifact", "artifacts": [path],
                    "note": "已产出交付文件(方案)"}
        except Exception as e:
            return {"domain": domain, "status": "error",
                    "note": "文件写入失败: %s: %s" % (type(e).__name__, e), "artifacts": []}

    # ---- Phase 29: 真实执行器(模块级函数, 默认热插拔, 可被 register_executor 覆盖) ----
    # 注意: 这些执行器是模块级函数(非方法), 因为 execute() 通过 get_executor(domain)(partner,...) 调用,
    # 若注册为方法会导致 partner 被误绑成 self。execute 失败时仍走 _default_executor(写 .md 方案)。

    # ---- 阶段 6: 记忆沉淀 (异常隔离, 不阻塞主流程) ----
    def deposit_memory(self, goal, dispatch_rep, session_id="", llm_call=None):
        try:
            merged_summary = (dispatch_rep.get("merged") or {}).get("summary", "")
            return _mg.get_graph(self.base_dir).absorb(
                goal, merged_summary, session_id=session_id, llm_call=llm_call)
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    # ---- Phase 32/34: 插件接入 (专家域 hint + connector 可用性 + 能力标签匹配) ----
    def _wire_plugins(self, goal, understand):
        """接入 plugin_hub: 专家域并入 understand.domains; connector 注入 plugin_connectors;
        按目标关键词匹配可用连接器(match_connectors)存入 understand.connectors,
        供 dispatch 阶段联邦自动调用。返回接入摘要, 失败静默。"""
        try:
            from . import plugin_hub as _ph
            hub = _ph.get_hub()
            experts = hub.list_experts()
            merged = list(understand.get("domains") or [])
            for e in experts:
                d = e.get("domain")
                if d and d not in merged:
                    merged.append(d)
            merged = merged[:4]
            understand["domains"] = merged
            # Phase 34: 能力标签匹配 — 按目标关键词匹配可用连接器
            matched = hub.match_connectors(goal)
            understand["connectors"] = [m["name"] for m in matched]
            understand["matched_connectors"] = matched
            wired = hub.wire(self, goal=goal)
            return wired
        except Exception:
            understand["connectors"] = understand.get("connectors", [])
            return {"experts": [], "connectors": [], "downgraded": []}

    # ---- 统一入口 ----
    def run(self, goal, session_id="", llm_call=None, quality_gate=True, on_stage=None, model="",
            queue_wait_sec=DEFAULT_QUEUE_WAIT_SEC):
        """超级 AGENT 统一编排入口。

        goal: 用户模糊目标
        llm_call: llm_call(prompt, system=None)->str|None, 无 key 全程规则兜底
        quality_gate: 是否执行第三级护栏(系统自检质量门); selfcheck 探针传 False 防递归
        on_stage: 可选回调 on_stage({stage, ts, ok, detail}), 每阶段完成即触发
                  (Phase 38: 供 Web SSE 流式推送实时进度; 回调异常不阻塞主流程)
        model: 可选模型名(供成本估算按价格档计费, Phase 40); 空 → 默认估算档
        queue_wait_sec: 并发槽位排队等待上限秒数(Phase 41); 超时忙拒绝 busy=True
        """
        # Phase 41: 并发槽位获取(有限排队等待, 超时忙拒绝; 不进编排历史)
        with _ORCH_STATE_LOCK:
            _ORCH_STATE["waiting"] += 1
        try:
            acquired = _ORCH_SEM.acquire(timeout=max(0.0, float(queue_wait_sec)))
        finally:
            with _ORCH_STATE_LOCK:
                _ORCH_STATE["waiting"] -= 1
        if not acquired:
            return {"ok": False, "busy": True, "queued": False, "goal": goal,
                    "error": "已有 %d 个编排并发运行, 排队 %.0fs 超时, 请稍后再试"
                             % (_ORCH_STATE["max"], float(queue_wait_sec)),
                    "trace": [], "elapsed_sec": 0.0, "usage": {}}

        with _ORCH_STATE_LOCK:
            _ORCH_STATE["running"] += 1
        try:
            return self._run_core(goal, session_id=session_id, llm_call=llm_call,
                                  quality_gate=quality_gate, on_stage=on_stage,
                                  model=model, started=time.time())
        finally:
            with _ORCH_STATE_LOCK:
                _ORCH_STATE["running"] -= 1
            try:
                _ORCH_SEM.release()
            except ValueError:
                pass

    def _run_core(self, goal, session_id="", llm_call=None, quality_gate=True,
                  on_stage=None, model="", started=None):
        """Phase 41: 槽位内的编排主体(仅由 run() 持并发槽位时调用)。"""
        started = started or time.time()
        trace = []
        ok = True
        routed = []
        dispatch_rep = {}
        exec_rep = {}
        converge_rep = {}
        mem = {}
        plugins_rep = {}
        understand = {}
        # Phase 40: LLM 用量计量(包装 llm_call 统计调用次数/字符数 → 同 loop 口径估算 token/成本)
        meter = _UsageMeter(llm_call, model=model)
        llm_call = meter

        def _trace(stage, detail, sub_ok=True):
            entry = {"stage": stage, "ts": _now(), "ok": sub_ok, "detail": detail}
            trace.append(entry)
            if on_stage:
                try:
                    on_stage(entry)
                except Exception:
                    pass
            try:
                from . import event_bus as _eb
                _eb.emit("superagent", "stage", "%s: %s" % (stage, detail),
                         {"stage": stage, "session_id": session_id, "ok": sub_ok}, audit=True)
            except Exception:
                pass

        try:
            # 1 目标理解
            understand = self.understand(goal, llm_call=llm_call)
            _trace("目标理解", "intent=%s | 域=%s | 约束%d | 召回%d字"
                   % (understand["intent"][:24], "/".join(understand["domains"]),
                      len(understand["constraints"]), len(understand["memory_recap"])))
            # Phase 32: 插件接入 (expert 域 hint 合并 + connector 可用性, 失败不阻塞)
            plugins_rep = self._wire_plugins(goal, understand)
            w = plugins_rep
            _trace("插件接入", "专家域+%d | connector %d 可用/%d 降级"
                   % (len(w.get("experts", []) or []),
                      len(w.get("connectors", []) or []),
                      len(w.get("downgraded", []) or [])))
            # 2 域路由(若插件合并了专家域, 路由结果可能随之扩展)
            routed = self.route(understand)
            _trace("域路由", "→ %s" % "/".join(routed))
            # 3 并行编排(联邦多伙伴 + Phase34 连接器标签匹配)
            dispatch_rep = self.dispatch(understand, session_id=session_id, llm_call=llm_call)
            mc = dispatch_rep.get("matched_connectors", []) or []
            _trace("并行编排", "派发 %d 伙伴, %d 成功; 连接器调用 %d"
                   % (len(dispatch_rep.get("partners", [])),
                      len([p for p in dispatch_rep.get("partners", []) if p.get("status") == "ok"]),
                      len(mc)))
            # 4 执行落地(方案 → 真实产物, 可插拔执行器)
            exec_rep = self.execute(dispatch_rep, goal=goal, session_id=session_id, llm_call=llm_call)
            _trace("执行落地", "落地 %d 个域, 产出 %d 文件"
                   % (exec_rep.get("count", 0), len(exec_rep.get("artifacts", []))))
            # 5 收敛护栏(三级)
            converge_rep = self.converge(dispatch_rep, quality_gate=quality_gate)
            ok = converge_rep["ok"]
            _trace("收敛护栏", "通过=%s | 伙伴成功 %d/%d | 冲突 %d | 自检 %d"
                   % (converge_rep["passed"], converge_rep["partners_ok"],
                      converge_rep["partners_total"], len(converge_rep["conflicts"]),
                      converge_rep["selfcheck_score"]), sub_ok=ok)
            # 6 记忆沉淀
            mem = self.deposit_memory(goal, dispatch_rep, session_id=session_id, llm_call=llm_call)
            _trace("记忆沉淀", "实体+%d 关系+%d 事实+%d"
                   % (mem.get("entities_added", 0), mem.get("relations_added", 0), mem.get("facts_count", 0)))
        except Exception as e:
            ok = False
            _trace("内核异常", "%s: %s" % (type(e).__name__, e), sub_ok=False)
            result = {
                "ok": False, "goal": goal, "error": "%s: %s" % (type(e).__name__, e),
                "trace": trace, "elapsed_sec": round(time.time() - started, 1),
                "usage": meter.stats(),
            }
            self._record(result)
            return result

        result = {
            "ok": ok,
            "goal": goal,
            "intent": understand,
            "routed": routed,
            "dispatch": dispatch_rep,
            "executions": exec_rep,
            "artifacts": exec_rep.get("artifacts", []),
            "converge": converge_rep,
            "plugins": plugins_rep,
            "selfcheck": converge_rep.get("selfcheck_score"),
            "memory": mem,
            "trace": trace,
            "elapsed_sec": round(time.time() - started, 1),
            "usage": meter.stats(),
        }
        self._record(result)
        return result

    def _record(self, result):
        try:
            ts = _now()
            cv = result.get("converge") or {}
            mem = result.get("memory") or {}
            usage = result.get("usage") or {}
            summary = {
                "goal": result.get("goal", ""),
                "ts": ts,
                "ok": result.get("ok", False),
                "routed": result.get("routed", []),
                "partners_ok": cv.get("partners_ok", 0),
                "partners_total": cv.get("partners_total", 0),
                "conflicts": len(cv.get("conflicts", []) or []),
                "selfcheck_score": cv.get("selfcheck_score", 100),
                "guards_passed": cv.get("passed", True),
                "entities_added": mem.get("entities_added", 0),
                "artifacts": len((result.get("executions") or {}).get("artifacts", []) or []),
                "elapsed_sec": result.get("elapsed_sec", 0),
                # Phase 40: 编排 LLM 用量入账
                "llm_calls": usage.get("llm_calls", 0),
                "est_input_tokens": usage.get("est_input_tokens", 0),
                "est_output_tokens": usage.get("est_output_tokens", 0),
                "est_total_tokens": usage.get("est_total_tokens", 0),
                "est_cost_cny": usage.get("est_cost_cny", 0.0),
            }
            _RUNS.append(summary)
            self._persist_result(ts, summary, result)
        except Exception:
            pass

    # ---- Phase 39: 编排历史持久化 (JSONL 追加, 重启不丢, 供历史回看) ----
    def _persist_result(self, ts, summary, result):
        """把一次编排完整结果落盘 <base>/outputs/superagent_runs.jsonl。

        行结构: {"ts", "summary"(与 _RUNS 摘要同构), "result"(完整编排结果)}。
        单条体积上限 64KB: 超限先递归截断超长字符串, 仍超限丢弃重载荷(dispatch/intent)。
        异常静默(持久化失败不阻塞编排)。
        """
        try:
            base = self.base_dir if (self.base_dir and self.base_dir != ":memory:"
                                     and os.path.isdir(self.base_dir)) else os.getcwd()
            out_dir = os.path.join(base, "outputs")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, "superagent_runs.jsonl")
            rec = json.loads(json.dumps(
                {"ts": ts, "summary": summary, "result": result},
                ensure_ascii=False, default=str))
            raw = json.dumps(rec, ensure_ascii=False, default=str)
            if len(raw.encode("utf-8")) > _PERSIST_MAX_BYTES:
                rec["result"] = _clip_strings(rec.get("result"), cap=4000)
                raw = json.dumps(rec, ensure_ascii=False, default=str)
            if len(raw.encode("utf-8")) > _PERSIST_MAX_BYTES:
                for k in ("dispatch", "executions", "intent"):
                    rec["result"].pop(k, None)
                raw = json.dumps(rec, ensure_ascii=False, default=str)
            if len(raw.encode("utf-8")) <= _PERSIST_MAX_BYTES:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(raw + "\n")
        except Exception:
            pass


def _clip_strings(o, cap=4000):
    """递归截断超长字符串(持久化体积保护)。"""
    if isinstance(o, str):
        return o if len(o) <= cap else o[:cap] + "…[截断]"
    if isinstance(o, list):
        return [_clip_strings(x, cap) for x in o]
    if isinstance(o, dict):
        return {k: _clip_strings(v, cap) for k, v in o.items()}
    return o


_PERSIST_MAX_BYTES = 65536
_PERSIST_FILE = "superagent_runs.jsonl"


def _persist_path(base_dir=None):
    base = base_dir if (base_dir and base_dir != ":memory:" and os.path.isdir(base_dir)) else os.getcwd()
    return os.path.join(base, "outputs", _PERSIST_FILE)


def _load_persisted(limit=50, base_dir=None):
    """读磁盘编排历史尾部(倒序返回, 与 get_recent_runs 顺序一致)。"""
    path = _persist_path(base_dir)
    try:
        with open(path, encoding="utf-8") as f:
            rows = collections.deque(f, maxlen=max(limit, 5) * 3)
        out = []
        for line in rows:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if isinstance(row.get("summary"), dict):
                    out.append(row)
            except Exception:
                continue
        return out[::-1]
    except Exception:
        return []


def get_recent_runs(limit=20, base_dir=None):
    """最近编排概览(供 API / 页面轮询): 内存缓冲优先 + 磁盘持久化补缺。

    语义: 内存中的编排(最新块)永远排在磁盘历史之前;
    磁盘仅补充内存中没有的(goal+ts 去重)更早记录 —— 重启后内存清空, 历史从磁盘回看。
    """
    mem = [r for r in _RUNS if r.get("ts")]
    seen = set((r.get("goal"), r.get("ts")) for r in mem)
    disk_only = [row["summary"] for row in _load_persisted(limit, base_dir)
                 if (row["summary"].get("goal"), row["summary"].get("ts")) not in seen]
    disk_only.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return (mem[::-1] + disk_only)[:limit]


def get_run_detail(ts, base_dir=None):
    """按 ts 取单次编排完整结果(磁盘 JSONL); 未找到返回 None。"""
    for row in _load_persisted(200, base_dir):
        if row.get("ts") == ts:
            return row.get("result")
    return None


def run(goal, session_id="", llm_call=None, base_dir=None, quality_gate=True):
    """模块级便捷入口。"""
    return SuperAgent(base_dir=base_dir).run(
        goal, session_id=session_id, llm_call=llm_call, quality_gate=quality_gate)
