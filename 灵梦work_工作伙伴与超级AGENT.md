# 灵梦work 工作伙伴与超级 AGENT 方案

> 配套：`灵梦work_全量升级总纲_Phase20-30.md` · `灵梦work_多模态域_音频图文视频.md`
> 目标：让灵梦work 从「单循环编码 AGENT」进化为「多智能体联邦 + 统一超级 AGENT 内核」的综合 OS。

---

## 0. 核心概念

- **工作伙伴（Partner）**：专职某域的子 AGENT（编码/创作/研究/运维）。各有独立 `loop` 实例 + 工具集 + 记忆。
- **联邦（Federation）**：伙伴注册中心，负责派发、协作、结果汇聚。
- **超级 AGENT 内核（SuperAgent）**：统一接收用户目标，做**域路由 → 并行编排 → 收敛 → 自检 → 记忆沉淀**。
- **记忆图谱（MemoryGraph）**：facts → 实体-关系，跨会话推理，驱动自主进化。

---

## 1. 工作伙伴 · 多智能体联邦（Phase 25）

### 1.1 伙伴定义
```python
@dataclass
class Partner:
    id: str            # "code" | "creation" | "research" | "ops"
    name: str
    domain: str
    tools: list        # 该伙伴可用工具集
    loop: object       # 独立 AgentLoop 实例
    max_iter: int
```

### 1.2 联邦协议
```
POST /api/federation/dispatch  { goal, hint_domains? }
  → 超级 AGENT 路由 → 派发到 1..N 个伙伴
  → 各伙伴并行 run_loop
  → federation 汇聚结果 → 结构化回写对话流
```

### 1.3 协作契约
- **派发**：目标含「编码+创作」→ 同时派发 code + creation 伙伴，并行。
- **汇聚**：`federation.merge(results)` → 去重/冲突检测/统一 struct-panel。
- **审计**：每次派发/汇聚进事件总线 + 审计链。

---

## 2. 超级 AGENT 内核（Phase 27）

### 2.1 编排流水
```
用户输入目标
  → 1. 目标理解（LLM 抽取 intent + 域标签 + 约束）
  → 2. 域路由（intent → code/audio/image/video/creation/research）
  → 3. 并行编排（多伙伴联邦派发，带预算配额）
  → 4. 收敛（结果汇聚 + 一致性校验 + 三级护栏）
  → 5. 自检（selfcheck + 质量门）
  → 6. 记忆沉淀（facts → memory_graph）
```

### 2.2 复用既有护栏
- 收敛沿用 `loop.py`：`max_iter=32` + `_CONVERGE_HINT` + `_LOOP_HINT`（同签名 3 轮）+ `_REFLECT_HINT`。
- 域预算：每个伙伴 `max_iter` 独立封顶，联邦总预算 = Σ 域预算，防上下文爆炸。

### 2.3 统一接口
```python
class SuperAgent:
    def run(self, goal: str, session_id: str) -> StructResult:
        intent = self.understand(goal)
        partners = self.route(intent)
        results = self.federation.dispatch(partners, intent)
        merged = self.converge(results)
        self.selfcheck(merged)
        self.memory_graph.absorb(goal, merged)
        return merged
```

---

## 3. 长期记忆图谱（Phase 26）

### 3.1 模型
```
Entity(name, type, confidence)  -- type: project/decision/api/bug/convention
Relation(src, rel, dst, weight)  -- rel: depends_on/derived_from/contradicts/used_in
Fact(entity_or_relation, ts, session_id, evidence)
```

### 3.2 沉淀与召回
- **沉淀**：每次任务结束，`memory_graph.absorb(goal, result)` 抽取 facts（决策/约定/失败归因）。
- **召回**：新目标进来 → 图谱检索相关实体/关系 → 注入 system prompt 作为「历史经验」。
- **衰减**：facts 带 `confidence`，长期未复用衰减；人工标注 `trusted` 永不衰减。

### 3.3 隐私
- 记忆落本机 `memory_graph.db`，不进仓库/不进 exe/不上云。
- 真实密钥/账号永不进图谱（仅记「某域需 key」事实，不记值）。

---

## 4. 自愈 2.0（Phase 20，闭环收口）

在 Phase 18/19 结构化预案基础上升级：
```
失败信号聚合 → 规则预案 → LLM 生成真实 diff（patch_plan.steps → unified diff）
  → 沙箱验证门（apply diff 到临时副本 → 跑单测 → 通过?）
  → 人工合并 UI（/api/heal/apply，一键 merge 落盘）
  → 记忆沉淀（「该故障曾用 X 修复」进图谱）
```
- **安全**：diff 永不自动落盘，必经人工合并门；沙箱验证失败即阻断并告警。
- **复用**：验证通过的修复沉淀为「可复用补丁」，下次同类故障直接建议。

---

## 5. 演进信条（收口「可自主进化」OS）

> 本地优先 · 零依赖内核 · 可审计 · 可自愈 · 可自主进化 · 隐私优先

- 每次失败都变成「可复用补丁」+「图谱事实」→ 系统越用越聪明，但不黑箱（全留 trace）。
- 多伙伴分工但不割裂：联邦统一编排，用户只见一个「超级 AGENT」。
- 所有升级长在不可变内核契约之上，不另起炉灶。

---

## 6. 验收（Phase 25–27 合计）

- 联邦：单目标跨 2+ 伙伴派发闭环跑通，结果结构化回写。
- 超级 AGENT：复杂目标（如「做段产品视频并写发布文案」→ 创作+编码伙伴）编排成功。
- 记忆图谱：隔会话召回历史约定并影响新决策。
- 自愈 2.0：失败→生成 diff→沙箱验证→人工合并，全绿。
