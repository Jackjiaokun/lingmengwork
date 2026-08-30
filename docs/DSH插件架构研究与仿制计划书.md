# DSH 插件架构研究 & lingmengwork 仿制计划书

> 输入：DSH（DeepSeek Harness）插件清单（约 150+ 项，含启用/停用状态）
> 目标：研究这些插件是什么 → 对照 lingmengwork 现状 → 列出"能仿的"→ 分期执行
> 状态：**计划书阶段，未执行**（等确认后按 P1 → P2 顺序落地）

---

## 一、DSH 插件清单解读：这是什么东西

### 1.1 架构本质
从 `cordis-host-runner` / `cordis-client-runner` 这两个名字可以确认：DSH 建立在 **Cordis** 插件框架之上（Cordis 是 Koishi 生态的依赖注入/插件容器）。它的内核不是单体应用，而是：

```
Cordis 容器
├── Host 侧（cordis-host-runner）：后端服务、工具执行、沙箱、LLM 调用
└── Client 侧（cordis-client-runner）：Web UI（web-app / ui-* 系列插件）
```

**核心设计思想 = 一切皆插件**。连"侧边栏""设置页""主题"都是一个可启用/停用的插件（`ui-sidebar`、`ui-settings`、`ui-theme`）。清单里大量同名插件出现两次（如 `tool-bash` 出现 2 次、`tool-subagent` 出现 4 次、`hmr` 出现 3 次），是因为 **Host 与 Client 各注册一次**，分别标了启用/停用。

### 1.2 插件分类全景（150+ 项归为 10 大类）

| 类别 | 代表插件 | 职责 |
|---|---|---|
| **内核/生命周期** | `include` `timer` `hmr` `modules` `runtime` `connection` | 插件装载、定时器、热更新、模块注册、连接管理 |
| **LLM 层** | `llm` `llm-deepseek` `llm-retry` `llm-pi-ai` `agent-default-model` `token-meter` | 多模型接入、重试、默认模型、token 计量 |
| **会话层** | `session` `session-title` `session-title-first-prompt-llm` `session-persistence-jsonl` `session-query-sqlite` `session-projection` `session-telemetry-otel` `session-stats` `session-checkpoint-policy` `session-log-export` | 会话持久化、自动标题、SQL 查询、投影缓存、遥测、统计、导出 |
| **Agent 层** | `agent` `agent-loop` `agent-instructions` `agent-presets` `persona` `subagent` | 循环、指令、预设人格、子代理 |
| **工具层** | `tool-bash` `tool-pwsh` `tool-fs` `tool-fs-search` `tool-jobs` `tool-web` `tool-skill` `tool-goal` `tool-todo` `tool-subagent` `tool-workflow` `tool-str-replace-editor` `tool-call-timeout-policy` `repeat-tool-reminder` | 各类工具实现 + 超时策略 + 重复调用提醒（防死循环） |
| **沙箱/权限** | `sandbox-local` `sandbox-policy` `bash-sandbox` `pwsh-sandbox` `permission-presets` `fs-sandbox` `fs-observation-policy` `shell-env` `user-approval` | 沙箱隔离、权限预设、命令审批 |
| **存储层** | `storage` `storage-json` `storage-domain` `spill-local` `spill-policy` `credentials-local` `settings-file` | 存储抽象、大输出溢出落盘、凭证、配置文件 |
| **命令/目标** | `commands` `goal` `goal-round-driver` `command-goal` `command-compact` `command-feedback` `plan-mode` | 命令系统、目标驱动、压缩、计划模式 |
| **Web/服务** | `web` `web-app` `web-app/startup` `webserver` `apiproxy` `api-remotes` `api-gateway` | Web 服务、API 网关、代理 |
| **UI 层（30+）** | `ui-theme` `ui-locale` `ui-layout` `ui-renderer` `ui-sidebar` `ui-settings*` `ui-conversation` `ui-trajectory` `ui-deliverables` `ui-reference` `ui-attachment` `ui-commands` `ui-agent-preset` `ui-permission-presets` `ui-model-selection` `ui-plan` `ui-jobs` `ui-goal` `ui-todo` `ui-skill` `ui-subagent` `ui-workflow-run` `ui-workspace` `ui-message-feedback` `ui-input-trigger` `ui-brand-official` `ui-directory-picker-native` | 前端每一个功能区域都是一个插件 |

### 1.3 关键结论
**DSH 的"插件"绝大多数不是给用户装第三方用的，而是它自己的功能模块边界划分方式。** 真正面向用户可扩展的是少数几个：`skill`（技能）、`plugin-inventory`（插件清单）、`tool-*`（工具）、`agent-presets`（预设）。

---

## 二、lingmengwork 现状对照（已实测勘察）

| DSH 能力 | lingmengwork 现状 | 差距 |
|---|---|---|
| 插件中枢 | ✅ `/plugins` 页 + `/api/plugins`（概览/连接器/专家） | UI 未现代化 |
| 权限模式 | ✅ 后端 `permission_mode` + `set_permission_mode()`（registry.py） | ❌ **无 UI** |
| 会话持久化 | ✅ `session.py` 落盘 + 水合 + MCP sqlite 查询 | ❌ **无自动标题** |
| LLM 重试/成本 | ✅ `_http_post` 重试/退避/断路器 + FailoverClient + 成本看板 | 基本对齐 |
| 工具系统 | ✅ 18 批次工具 + MCP 9 服务（fs/git/shell/fetch/grep/sqlite/search/review/demo） | ❌ 无 str-replace-editor 精确编辑 |
| 沙箱 | ✅ 工作区沙箱页（`LMW_FS_ROOT` 等）+ 破坏性护栏 | 基本对齐 |
| 存储/凭证 | ✅ 记忆与安全双引擎 + 密钥保险箱 + config.toml | ❌ 无 spill（大输出落盘） |
| 命令系统 | ✅ `commands.js` Cmd+K 命令面板（Phase 82） | 基本对齐 |
| 目标/计划 | ✅ 编排目标入口 + 计划看板 | ❌ 无 plan-mode |
| 产物/交付 | ✅ preview dock 分页签（Phase 80/81）+ 产物 tab | 基本对齐 |
| 设计系统 | ✅ `ds.css` + `sidebar.js` + 31 页统一（Phase 79） | 基本对齐 |
| Agent 预设 | ✅ 2×2 卡片网格（Phase 87） | 仅 UI，未联动 prompt |
| 提供方卡片 | ✅ trae 风卡片列表（Phase 88） | 基本对齐 |
| 多语言 | ⚠️ 仅 schema 预留 `ui.language` | ❌ **无 i18n** |
| 轨迹页 | ⚠️ 编排内有 Trace 标签 | ❌ **无独立轨迹页** |
| 参考信息 | ❌ 无 | 缺 |
| 附件上传 | ❌ 无（只有 snippets 代码片段） | 缺 |
| HMR | ❌ 无（改前端需重启） | 缺 |

---

## 三、可仿清单与优先级

### 🟢 P1 — 高价值 + 低风险（建议先做，约 3 期）

| # | 仿制项 | 对应 DSH 插件 | 具体做法 | 涉及文件 |
|---|---|---|---|---|
| 1 | **插件清单页现代化** | `plugin-inventory` `ui-settings-plugins` | 仿 trae/DSH：顶部搜索框 + 双列卡片网格 + 每张卡"名称/描述/启用开关" + 分类筛选。数据复用现有 `/api/plugins` | `plugin_hub.html` + `ds.css` |
| 2 | **权限预设 UI** | `ui-permission-presets` `permission-presets` | 后端已有 `permission_mode`；加 UI：设置中心新增"权限"section（下拉：完全访问/只读/计划模式）+ 编排页输入框权限按钮接真 API | `settings.html`（schema 加字段）+ `superagent.html` |
| 3 | **会话自动标题** | `session-title-first-prompt-llm` | 首次 prompt 时生成标题（**规则优先**：截取前 30 字 + 去换行；LLM 可选增强，失败静默回退规则）。会话列表显示标题而非裸时间戳 | `session.py` + `app.js`/会话列表渲染 |

### 🟡 P2 — 中价值（P1 完成后推进）

| # | 仿制项 | 对应 DSH 插件 | 说明 |
|---|---|---|---|
| 4 | **轨迹页** | `ui-trajectory` | 把编排内的 Trace 数据独立成 `/trajectory` 页，时间线 + 步骤展开 |
| 5 | **参考信息面板** | `ui-reference` | 右侧 dock 加"参考"分页签（任务用到的文件/知识），与产物分页签并列 |
| 6 | **附件上传** | `ui-attachment` `attachment-local` | 输入框附件按钮接真上传 + `/api/attachments` |
| 7 | **多语言骨架** | `locale` `ui-locale` | 已有 `ui.language` 字段；建 i18n 字典（先中英），`data-i18n` 属性驱动切换 |

### 🔵 P3 — 长期 / 重（暂不排期）

| 仿制项 | 对应插件 | 原因 |
|---|---|---|
| 计划模式 | `plan-mode` | 需改 Agent 循环行为，风险高 |
| 精确编辑工具 | `tool-str-replace-editor` | 需新增工具协议，影响 loop.py |
| 大输出溢出落盘 | `spill-local` `spill-policy` | 需改工具结果处理链路 |
| 子代理 UI | `ui-subagent` `tool-subagent` | 已有联邦编排，重复度高 |
| 热重载 | `hmr` | 单体 Python，收益低于成本 |

### ⛔ 明确不做（附理由）

| DSH 插件 | 不做的理由 |
|---|---|
| `cordis-host-runner` / `cordis-client-runner` | 引入 JS 插件框架到 Python 单体项目不现实；用 **Router 分层 + schema 驱动 UI** 已达成类似解耦（Phase 82/85） |
| `llm-deepseek` / `llm-pi-ai` | lingmengwork 已有商汤/openai/ollama/mock/auto 多 provider + FailoverClient |
| `web-search-deepseek` | 已有 MCP `search`（Bing/DDG 回退） |
| `tool-bash` / `tool-pwsh` 细分沙箱 | 已有 shell MCP + 工作区沙箱 + 破坏性护栏三层 |
| `storage-json` / `storage-domain` | 已有记忆与安全双引擎，语义等价 |
| `session-telemetry-otel` | 已有可观测页 + 成本看板；OTLP 导出属 P6 可观测标准化范畴 |

---

## 四、分期执行计划（每期独立可验收）

### 阶段 1：插件清单页现代化（仿 trae/DSH）
- **目标**：`/plugins` 从现有页升级为 trae 风清单（搜索 + 双列卡片 + 启停开关）
- **改动**：`plugin_hub.html` 重做布局；`ds.css` 加 `.lmw-plugin-*` 类
- **验收**：搜索过滤生效 / 开关可切换并持久化 / 契约测试 ≥ 6 条 / 全量 pytest 零回归

### 阶段 2：权限预设 UI
- **目标**：让既有后端 `permission_mode` 真正可被用户操作
- **改动**：schema 加 `agent.permission_mode` 字段；设置中心渲染下拉；编排页权限按钮接 `set_permission_mode`
- **验收**：切换权限后编排行为随之变化 / 契约测试 ≥ 5 条

### 阶段 3：会话自动标题
- **目标**：会话列表显示人类可读标题（规则生成，LLM 可选增强）
- **改动**：`session.py` 存 title；列表渲染改用 title
- **验收**：新会话自动生成标题 / 老会话回退显示原 id / 契约测试 ≥ 4 条

### 阶段 4+：P2 各项（轨迹页 / 参考面板 / 附件 / 多语言）
—— 每个阶段同样按"改动 → 契约 → 全量 → 提交"闭环推进。

---

## 五、风险与约束

1. **不改后端协议**：P1 三项均在现有 API 上加 UI/字段，不动工具协议与 loop.py 内核（避免触碰 P80/P81 已验证的行为）。
2. **每个阶段独立提交**：不攒大提交，便于回滚。
3. **契约测试先行**：每阶段至少 4-6 条 pytest 护栏（沿用 Phase 85-88 的做法）。
4. **仍遵守"暂不上传发布"**：每阶段仅本地提交，不 push。
5. **插件语义差异**：DSH 的"插件"是框架级模块边界，lingmengwork 是单体 Python —— **仿的是产品形态（清单页/开关/分类），不是框架本身**。

---

## 六、验收标准（全局）

- 每个阶段：全量 pytest **零回归**（当前基线 924 passed）
- UI 改动：有对应 Node DOM 桩或契约测试覆盖（不只是"看起来对"）
- 提交信息：说明"仿了 DSH 的哪个插件、对应 lingmengwork 的什么能力、为什么这么映射"

---

**下一步**：确认后从 **阶段 1（插件清单页现代化）** 开始执行。
