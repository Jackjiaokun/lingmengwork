"""灵梦work · 长期记忆图谱 (Phase 26).

把「扁平事实记忆」(memory_mgr) 升级为 **结构化实体-关系图谱**, 支撑跨会话推理:

    Entity(name, type, confidence, trusted, last_used)   type: project/decision/api/bug/convention
    Relation(src, rel, dst, weight, confidence)          rel: depends_on/derived_from/contradicts/used_in
    Fact(subject, ts, session_id, evidence, confidence)   每条沉淀的原子事实

两条主链路(对应《终极蓝图 · 工作伙伴与超级 AGENT》Phase 26):
- **沉淀 absorb(goal, result)**: 任务结束抽取 facts(决策/约定/失败归因) → 入图。
- **召回 recall(goal)**: 新目标进来 → 图谱检索相关实体/关系 → 产出 recap 注入 system prompt。

工程信条:
- 零三方依赖(纯标准库 + sqlite3)。
- 无 LLM 亦可用(规则兜底抽取); 有 llm_call 走 LLM 抽取(失败自动回退)。
- **隐私优先**: 真密钥/账号永不进图(仅记「某域需 key」事实, 不记值); 命中的秘密串自动脱敏。
- facts 带 confidence, 长期未复用衰减; trusted 实体永不衰减。
- 落本机 `memory_graph.db`, 不进仓库/不进 exe/不上云。
"""

import os
import re
import json
import sqlite3
from datetime import datetime


# =====================================================================
# 数据模型
# =====================================================================
_ENTITY_TYPES = ("project", "decision", "api", "bug", "convention")
_REL_TYPES = ("depends_on", "derived_from", "contradicts", "used_in")
_TYPE_LABEL = {"project": "项目", "decision": "决策", "api": "接口", "bug": "故障",
               "convention": "约定", "fact": "事实"}

# 实体类型 → 强信号关键词(命中即归类)
_TYPE_KEYWORDS = {
    "decision": ["决定采用", "选择方案", "采用", "选定", "决策", "拍板", "确定用", "选型"],
    "convention": ["约定", "规范", "规矩", "约定俗成", "准则", "规约", "风格约定", "命名规范"],
    "bug": ["bug", "故障", "失败归因", "报错", "异常", "根因", "崩溃", "缺陷", "踩坑", "坑"],
    "api": ["api", "接口", "密钥", "key", "token", "sdk", "端点", "endpoint"],
    "project": ["项目", "工程", "系统", "产品", "平台", "模块", "服务", "工作台"],
}

# 关系抽取: 连接词 → rel
_REL_PATTERNS = [
    (re.compile(r"(.+?)\s*(依赖|依赖于|depends on|借助)\s*(.+)", re.I), "depends_on"),
    (re.compile(r"(.+?)\s*(来自|来源于|derived from|出自)\s*(.+)", re.I), "derived_from"),
    (re.compile(r"(.+?)\s*(冲突|相矛盾|contradicts|与.*不一致)\s*(.+)", re.I), "contradicts"),
    (re.compile(r"(.+?)\s*(用于|被.*用于|used in|服务于)\s*(.+)", re.I), "used_in"),
]

# 秘密串特征(命中即脱敏, 绝不入图)
_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key|私钥|口令)\s*[:=]\s*)\S+"
)

# 命名实体粗提取: 引号/方括号/书名号内的专有名词, 或「X 的 Y」中的 X
_NAME_RE = re.compile(r"([\"『「『]([^\"』」』]{2,30})[\"』」『]|[【\[]([^\]】]{2,30})[】\]])")


def _redact(text):
    """隐私脱敏: 密钥/账号值一律替换为 <REDACTED>, 真值永不入图。"""
    return _SECRET_RE.sub(lambda m: m.group(1) + "<REDACTED>", text or "")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _tokenize(s):
    """中英混合 token: 西文按非词字符切, 中文取二元组(bigram)以应对连续中文。"""
    base = set(w for w in re.split(r"[\s,，。、；;:.：:！!？?()（）\[\]【】\"'\"'/\\|]+",
                                   (s or "").lower()) if len(w) >= 1)
    cjk = "".join(ch for ch in (s or "") if "\u4e00" <= ch <= "\u9fff")
    if cjk:
        bg = set(cjk[i:i + 2] for i in range(len(cjk) - 1))
        if cjk:
            bg.add(cjk[0])
        base |= bg
    return base


class Entity:
    def __init__(self, name, type="fact", confidence=1.0, trusted=False,
                 last_used="", created_at=""):
        self.name = name
        self.type = type
        self.confidence = confidence
        self.trusted = bool(trusted)
        self.last_used = last_used
        self.created_at = created_at

    def asdict(self):
        return {"name": self.name, "type": self.type, "confidence": round(self.confidence, 3),
                "trusted": self.trusted, "last_used": self.last_used, "created_at": self.created_at}


class Relation:
    def __init__(self, src, rel, dst, weight=1.0, confidence=1.0, created_at=""):
        self.src = src
        self.rel = rel
        self.dst = dst
        self.weight = weight
        self.confidence = confidence
        self.created_at = created_at

    def asdict(self):
        return {"src": self.src, "rel": self.rel, "dst": self.dst,
                "weight": round(self.weight, 3), "confidence": round(self.confidence, 3),
                "created_at": self.created_at}


# =====================================================================
# 记忆图谱
# =====================================================================
class MemoryGraph:
    """实体-关系知识图谱(本机 SQLite 持久化)。"""

    def __init__(self, db_path=".lmw_memory_graph.db"):
        self.db_path = db_path
        self._conn = None
        self._init_db()

    # ---- 持久化 ----
    def _connect(self):
        if self._conn is None:
            d = os.path.dirname(os.path.abspath(self.db_path))
            if d:
                os.makedirs(d, exist_ok=True)
            # check_same_thread=False: 服务端 ThreadingHTTPServer 每请求独立线程,
            # 共享单例连接需跨线程可用(短操作 + GIL 串行, 安全)。
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self):
        c = self._connect()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'fact',
            confidence REAL NOT NULL DEFAULT 1.0,
            trusted INTEGER NOT NULL DEFAULT 0,
            last_used TEXT,
            created_at TEXT,
            PRIMARY KEY (name, type)
        );
        CREATE TABLE IF NOT EXISTS relations (
            src TEXT NOT NULL,
            rel TEXT NOT NULL,
            dst TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            confidence REAL NOT NULL DEFAULT 1.0,
            created_at TEXT,
            PRIMARY KEY (src, rel, dst)
        );
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'entity',
            ts TEXT,
            session_id TEXT,
            evidence TEXT,
            confidence REAL NOT NULL DEFAULT 1.0
        );
        CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
        CREATE INDEX IF NOT EXISTS idx_relations_src ON relations(src);
        CREATE INDEX IF NOT EXISTS idx_relations_dst ON relations(dst);
        """)
        c.commit()

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ---- 写: 实体 / 关系 ----
    def add_entity(self, name, type="fact", confidence=1.0, trusted=False):
        name = (name or "").strip()
        if not name:
            return None
        if type not in _ENTITY_TYPES:
            type = "project" if type in ("fact", "") else type
        if type not in _ENTITY_TYPES:
            type = "project"
        c = self._connect()
        now = _now()
        c.execute(
            "INSERT INTO entities(name, type, confidence, trusted, last_used, created_at) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(name, type) DO UPDATE SET "
            "confidence=excluded.confidence, "
            "trusted=MAX(entities.trusted, excluded.trusted), last_used=excluded.last_used",
            (name, type, float(confidence), 1 if trusted else 0, now, now))
        c.commit()
        return self.get_entity(name)

    def get_entity(self, name):
        c = self._connect()
        row = c.execute("SELECT * FROM entities WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        return Entity(**dict(row))

    def add_relation(self, src, rel, dst, weight=1.0, confidence=1.0):
        src, dst = (src or "").strip(), (dst or "").strip()
        if not src or not dst or rel not in _REL_TYPES:
            return None
        # 关系端点保证为已知实体(不存在则建 fact 类实体)
        if self.get_entity(src) is None:
            self.add_entity(src, type="fact")
        if self.get_entity(dst) is None:
            self.add_entity(dst, type="fact")
        c = self._connect()
        c.execute(
            "INSERT INTO relations(src, rel, dst, weight, confidence, created_at) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(src, rel, dst) DO UPDATE SET "
            "weight=excluded.weight, confidence=excluded.confidence",
            (src, rel, dst, float(weight), float(confidence), _now()))
        c.commit()
        return Relation(src, rel, dst, weight, confidence, _now())

    def add_fact(self, subject, kind="entity", session_id="", evidence="", confidence=1.0):
        c = self._connect()
        cur = c.execute(
            "INSERT INTO facts(subject, kind, ts, session_id, evidence, confidence) "
            "VALUES(?,?,?,?,?,?)",
            (_redact(subject), kind, _now(), session_id or "", _redact(evidence),
             float(confidence)))
        c.commit()
        return cur.lastrowid

    # ---- 读: 列表 / 统计 ----
    def list_entities(self, limit=200, type=None):
        c = self._connect()
        if type:
            rows = c.execute("SELECT * FROM entities WHERE type=? ORDER BY confidence DESC, last_used DESC LIMIT ?",
                             (type, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM entities ORDER BY confidence DESC, last_used DESC LIMIT ?",
                             (limit,)).fetchall()
        return [Entity(**dict(r)).asdict() for r in rows]

    def list_relations(self, limit=200):
        c = self._connect()
        rows = c.execute("SELECT * FROM relations ORDER BY weight DESC LIMIT ?", (limit,)).fetchall()
        return [Relation(**dict(r)).asdict() for r in rows]

    def stats(self):
        c = self._connect()
        e = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        r = c.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        f = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        t = c.execute("SELECT COUNT(*) FROM entities WHERE trusted=1").fetchone()[0]
        by_type = {}
        for row in c.execute("SELECT type, COUNT(*) FROM entities GROUP BY type"):
            by_type[row[0]] = row[1]
        return {"entities": e, "relations": r, "facts": f, "trusted": t, "by_type": by_type}

    # ---- 抽取: absorb ----
    def _extract_entities(self, text):
        """规则抽取实体: 命中强信号归类; 引用专有名词作为实体。"""
        entities = []  # (name, type)
        low = text.lower()
        # 1) 强信号类型归类(取首个命中类型; 一段文本可贡献多个类型)
        for t, kws in _TYPE_KEYWORDS.items():
            if any(kw in low for kw in kws):
                # 用整段前 60 字作该类型代表的实体名(去噪)
                snippet = _redact(text.strip().replace("\n", " "))
                if len(snippet) > 60:
                    snippet = snippet[:60] + "…"
                entities.append((snippet, t))
        # 2) 命名实体(引号/书名号内的专有名词), 归 project 类
        for m in _NAME_RE.finditer(text):
            nm = m.group(2) or m.group(3)
            if nm and len(nm) >= 2:
                entities.append((nm.strip(), "project"))
        return entities

    def _extract_relations(self, text):
        """规则抽取关系三元组(连接词驱动)。"""
        rels = []  # (src, rel, dst)
        for pat, rel in _REL_PATTERNS:
            for m in pat.finditer(text):
                src = m.group(1).strip().strip("。，,.;；:：").strip()
                dst = m.group(3).strip().strip("。，,.;；:：").strip()
                if src and dst and src != dst and len(src) <= 40 and len(dst) <= 40:
                    rels.append((src, rel, dst))
        return rels

    def absorb(self, goal, result_text, session_id="", llm_call=None):
        """沉淀: 从 goal+result 抽取实体/关系/事实入图。

        返回 {ok, entities_added, relations_added, facts_count, entities[], relations[]}。
        隐私: 任何秘密串经 _redact 脱敏; api 类实体只记「需 key」, 不记值。
        """
        result_text = result_text or ""
        combined = _redact("%s\n%s" % (goal or "", result_text))
        entities_added, relations_added = [], []

        # LLM 抽取(可选, 失败回退规则)
        llm_ents, llm_rels = [], []
        if llm_call:
            llm_ents, llm_rels = self._llm_extract(llm_call, goal, result_text)
        for name, t in llm_ents:
            e = self.add_entity(name, type=t)
            if e:
                entities_added.append(e.asdict())
        for src, rel, dst in llm_rels:
            r = self.add_relation(src, rel, dst)
            if r:
                relations_added.append(r.asdict())

        # 规则兜底抽取(始终执行, 与 LLM 结果并集去重)
        seen_e = set((e["name"], e["type"]) for e in entities_added)
        for name, t in self._extract_entities(combined):
            if (name, t) in seen_e:
                continue
            e = self.add_entity(name, type=t)
            if e:
                entities_added.append(e.asdict())
                seen_e.add((name, t))
        seen_r = set((r["src"], r["rel"], r["dst"]) for r in relations_added)
        for src, rel, dst in self._extract_relations(combined):
            if (src, rel, dst) in seen_r:
                continue
            r = self.add_relation(src, rel, dst)
            if r:
                relations_added.append(r.asdict())
                seen_r.add((src, rel, dst))

        # 事实落盘(每实体 + 每关系各一条, 便于审计回溯)
        facts_count = 0
        for e in entities_added:
            self.add_fact("entity:%s" % e["name"], kind="entity", session_id=session_id,
                          evidence="type=%s" % e["type"], confidence=e["confidence"])
            facts_count += 1
        for r in relations_added:
            self.add_fact("rel:%s-%s-%s" % (r["src"], r["rel"], r["dst"]), kind="relation",
                          session_id=session_id, evidence="weight=%.2f" % r["weight"],
                          confidence=r["confidence"])
            facts_count += 1

        return {
            "ok": True,
            "entities_added": len(entities_added),
            "relations_added": len(relations_added),
            "facts_count": facts_count,
            "entities": entities_added,
            "relations": relations_added,
        }

    def _llm_extract(self, llm_call, goal, result_text):
        """LLM 抽取实体/关系, 返回 (entities[(name,type)], relations[(src,rel,dst)]); 失败返空。"""
        sys = (
            "你是知识图谱抽取器。从任务目标与执行结果中抽取『值得长期记住』的实体与关系。\n"
            "实体类型限定: project(项目/系统/模块) / decision(决策/选型) / api(接口/需密钥的域) / "
            "bug(故障/失败归因) / convention(约定/规范)。\n"
            "关系类型限定: depends_on(依赖) / derived_from(来自) / contradicts(冲突) / used_in(用于)。\n"
            "只输出一个 JSON 对象: {\"entities\":[{\"name\":\"...\",\"type\":\"...\"}],"
            "\"relations\":[{\"src\":\"...\",\"rel\":\"...\",\"dst\":\"...\"}]}。无则空数组。"
        )
        user = "目标:\n%s\n\n结果:\n%s" % (goal[:2000], result_text[:3000])
        try:
            raw = llm_call(user, system=sys)
            if not isinstance(raw, str) or not raw.strip():
                return [], []
            obj = json.loads(raw)
            ents = [(e.get("name", "").strip(), e.get("type", "project"))
                    for e in obj.get("entities", []) if e.get("name")]
            rels = [(r.get("src", "").strip(), r.get("rel", ""), r.get("dst", "").strip())
                    for r in obj.get("relations", [])
                    if r.get("src") and r.get("dst") and r.get("rel") in _REL_TYPES]
            return ents, rels
        except Exception:
            return [], []

    # ---- 召回: recall ----
    def recall(self, goal, limit=12):
        """召回相关实体/关系, 产出可注入 system prompt 的 recap。

        策略: ① 目标 token 与实体名/类型重叠打分取 top-k; ② 对命中实体做 1 跳关系扩展。
        返回 {ok, entities[], relations[], recap, count}。
        """
        q_tokens = _tokenize(goal)
        c = self._connect()
        # 实体打分
        scored = []
        for row in c.execute("SELECT * FROM entities"):
            e = Entity(**dict(row))
            e_tokens = _tokenize(e.name) | {e.type}
            overlap = len(q_tokens & e_tokens)
            if overlap == 0:
                continue
            score = overlap / (len(q_tokens) or 1) * (0.5 + 0.5 * e.confidence)
            scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:limit]
        entities = [e.asdict() for _, e in top]
        # 1 跳关系扩展
        names = {e.name for _, e in top}
        rel_rows = c.execute(
            "SELECT * FROM relations WHERE src IN (%s) OR dst IN (%s)" % (
                ",".join("?" * max(1, len(names))), ",".join("?" * max(1, len(names)))),
            list(names) + list(names)).fetchall() if names else []
        rels = []
        for r in rel_rows:
            rd = Relation(**dict(r)).asdict()
            rels.append(rd)
        # bump last_used
        if names:
            c.execute("UPDATE entities SET last_used=? WHERE name IN (%s)" % (
                ",".join("?" * len(names))), [_now()] + list(names))
            c.commit()
        recap = self._render_recap(entities, rels, goal)
        return {"ok": True, "entities": entities, "relations": rels,
                "recap": recap, "count": len(entities) + len(rels)}

    def _render_recap(self, entities, rels, goal):
        """把召回结果渲染为可注入 system prompt 的『历史经验』Markdown。"""
        if not entities and not rels:
            return ""
        lines = ["## 🧠 历史经验(跨会话记忆图谱召回)", ""]
        if entities:
            lines.append("### 相关实体")
            for e in entities:
                tag = "🔒" if e["trusted"] else ""
                lines.append("- **[%s]** %s %s(置信 %.2f)" % (_TYPE_LABEL.get(e["type"], e["type"]),
                                                         e["name"], tag, e["confidence"]))
        if rels:
            lines.append("")
            lines.append("### 实体关系")
            for r in rels:
                lines.append("- %s —%s→ %s" % (r["src"], r["rel"], r["dst"]))
        lines.append("")
        lines.append("> 以上为过往任务沉淀的结构化记忆, 请据此保持决策/约定一致性。")
        return "\n".join(lines)

    # ---- 衰减 ----
    def decay(self, factor=0.95):
        """置信度衰减: 非 trusted 实体按 factor 衰减(长期未复用降权); trusted 豁免。"""
        c = self._connect()
        c.execute("UPDATE entities SET confidence = confidence * ? WHERE trusted=0", (float(factor),))
        c.execute("UPDATE relations SET confidence = confidence * ?", (float(factor),))
        c.commit()
        return self.stats()

    # ---- 导出 ----
    def export_markdown(self):
        s = self.stats()
        lines = ["# 灵梦work · 记忆图谱报告", "", _now(), "",
                 "## 概览", "- 实体: %d" % s["entities"], "- 关系: %d" % s["relations"],
                 "- 事实: %d" % s["facts"], "- 可信(trusted): %d" % s["trusted"], ""]
        if s.get("by_type"):
            lines.append("### 实体类型分布")
            for t, n in s["by_type"].items():
                lines.append("- %s: %d" % (_TYPE_LABEL.get(t, t), n))
            lines.append("")
        ents = self.list_entities(limit=100)
        if ents:
            lines.append("## 实体清单")
            for e in ents:
                tag = " 🔒" if e["trusted"] else ""
                lines.append("- [%s] %s%s (置信 %.2f)" % (_TYPE_LABEL.get(e["type"], e["type"]),
                                                         e["name"], tag, e["confidence"]))
            lines.append("")
        rels = self.list_relations(limit=100)
        if rels:
            lines.append("## 关系清单")
            for r in rels:
                lines.append("- %s —%s→ %s (权重 %.2f)" % (r["src"], r["rel"], r["dst"], r["weight"]))
            lines.append("")
        return "\n".join(lines)


# =====================================================================
# 全局单例
# =====================================================================
_GRAPHS = {}


def get_graph(base_dir=None):
    """记忆图谱单例(按 base_dir 缓存, db 落 <base_dir>/.lmw_memory_graph.db)。

    base_dir=":memory:" → 返回独立的纯内存库(不落盘, 规避临时目录锁, 用于测试/自检探针)。
    """
    if base_dir == ":memory:":
        return MemoryGraph(":memory:")
    key = base_dir or os.getcwd()
    if key not in _GRAPHS:
        db = os.path.join(key, ".lmw_memory_graph.db")
        _GRAPHS[key] = MemoryGraph(db)
    return _GRAPHS[key]


def reset_graph(base_dir=None):
    """测试/重置用: 清缓存。"""
    key = base_dir or os.getcwd()
    g = _GRAPHS.pop(key, None)
    if g:
        g.close()
