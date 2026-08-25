# 灵梦work · 迭代综合提升100轮 路线图

> 目标：把 `lingmengwork`（本地优先 AI 编码智能体面板）从「能跑」推向「工业级、全自主、可观测、可交付」。
> 本文件是常青 backlog，按主题分类；每完成一批在对应项打 `[done]`，并追加当日小结。
> 纪律：每轮小步快跑 + 单测回归 + 功能 e2e 验证（不靠 grep 二进制判断新旧）。

---

## 批次 1 — 工具系统综合硬化 ✅ (2026-08-26 完成)
- [done] MCP 工具权限分层：`_classify_mcp_tool` 按危险度注入 `registry._READONLY/_WRITE/_EXEC_TOOLS`，`plan/acceptEdits` 不再误拦 `web_search/fs_read/code_search/db_query` 等只读工具。
- [done] 工具返回值截断：`loop._truncate_tool_result` 按 `cfg.agent.tool_result_max_chars`(默认6000) 截断超长输出，防 `web_fetch/code_search/shell` 撑爆上下文；附带 `duration_ms` 耗时可视化。
- [done] `repo_map` 多语言符号提取：从 py/js 扩展到 go/rust/java/c/c++/c#/php/ruby/swift/kotlin/scala/sh 等 21 种语言。
- [done] `tool_kind` 覆盖全部 MCP 工具（fs_read/write、git_*、shell_exec、code_search、web_*、db_*、code_review），链路可视化染色准确。
- [done] `prompt.py` 修正 12e 笔误（`fs_write` → 主写工具 `write_file`）；明确可直接按名调 MCP 外部工具。
- [done] 单测 `tests/test_tool_upgrade.py`（9 例）覆盖上述逻辑；全量 pytest **169 passed**。

---

## 批次 2 — 代码精读/检索/编辑能力增强 ✅ (2026-08-26 完成)
- [done] `grep` 增强：`context`(命中行上下各 N 行, `>` 标记命中) + `glob`(文件过滤 *.py) + `head_limit`(单文件上限), 输出 `rel:Lline:` 结构。
- [done] `read_file` 行号视图：`numbered=true` 输出 `行号 | 内容`, 精确定位便于 edit_file 对齐。
- [done] `edit_file` 模糊定位提示：old_string 未命中时给近似行/候选行号, 不再盲目失败。
- [done] `apply_patch` 诊断增强：未命中给首行相似行号/近似行; 歧义给全部命中行号。
- [done] 新增 `symbol_search` 工具 (仿 LSP 跳转定义)：跨仓库按名/正则检索 path:Lline: 签名, 支持 glob/limit; 大小写不敏感; 注册进 readonly 层 + prompt + tool_kind=search。
- [done] `repo_map` 尊重 `.gitignore` 自动排除 + `max_depth` 限深 (文件层生效)。
- [done] 单测 `tests/test_code_capability.py`(6 例) 覆盖; 全量 pytest **175 passed**。

---

## 批次 3 — 智能体循环与推理增强 ✅ (2026-08-26 完成)
- [done] **长任务断点续跑**: `run()` 命中 `max_iter` 不再硬失败, 自动 `save_session()` 落盘断点并 `emit("done", resume_available=True, session_id=...)`; 新增 `continue_run()`(复用活体状态续跑) 与 `resume_from_disk()`(跨进程从磁盘水合续跑)。Web `_chat_sse` 对「继续/continue」类意图替换为显式续跑提示, 引导模型基于已有工具结果推进到底。
- [done] **反思循环**: 配置 `agent.reflect_every`(默认 0=关); 每 N 轮注入 `_REFLECT_HINT` 自检提示(目标/进展/下一步), 与收敛护栏、循环检测构成「临近上限→死循环→周期反思」三级引导, 抗空转促收敛。
- [done] **工具结果 LLM 摘要回灌**: 配置 `agent.summarize_tool_results` + `summarize_max_chars`(默认 3000); 超长结果优先调 `client.chat(stream=False)` 摘要后回灌, 无 LLM/异常自动回退硬截断 (`_post_process_result`)。升级批次1的纯截断, 省 token 且保关键信息。
- [done] **prompt 引导增强**: 系统提示新增 12g 自验证(symbol_search/grep 自检签名一致性)、12h 主动澄清(仅真模糊时最多反问一次)、12i 长任务续跑说明。
- [done] 单测 `tests/test_agent_reasoning.py`(7 例) 覆盖反思注入/摘要优先/截断回退/强制结束续跑/continue_run 复用; 顺带修正批次1遗留的 2 处 `test_mcp.py` 过期期望(demo_echo=只读, plan 模式放行只读型 mcp); 全量 pytest **188 passed**。

---

## 批次 4 — 工具调用治理（配额/缓存/脱敏）✅ (2026-08-26 完成)
- [done] **工具调用配额**: `loop.run()` 新增 `agent.tool_call_quota`(默认 0=不限); 单任务累计调用达上限即停止执行工具、落盘续跑点并 `emit("done", quota_exceeded=True, resume_available=True)`, 与 max_iter 同源处理; 防失控循环烧钱。
- [done] **工具结果缓存层**: `registry.execute()` 对只读搜索类(web_search/code_search/db_*/symbol_search/grep/glob/repo_map/read_file/fs_read/list_dir/diff_view)同查询命中进程级内存缓存(`_RESULT_CACHE`, TTL=`agent.tool_cache_ttl`), 省 token/时延; 写/执行类永不缓存; 命中返回追加 `[缓存命中]`。
- [done] **工具结果脱敏**: `loop._redact` 纯函数, 回灌前自动遮蔽密钥/密码/令牌(`sk-`/`ghp_`/`xox[bap]-`/`AIza`/`AKIA`/JWT/PEM + `password`/`token`/`api_key`/`Authorization` 等键名), 默认开(`agent.redact_secrets=True`); 防凭证泄露进上下文/会话/日志。
- [done] **prompt 引导**: 系统提示新增 12j 工具调用节制(复用/合并/配额收尾)、12k 敏感信息(凭证自动遮蔽、勿硬编码)。
- [done] 单测 `tests/test_tool_governance.py`(7 例) 覆盖配额拦截+续跑信号/默认不限/缓存命中免重跑/默认不缓存/脱敏多格式/管线集成; 全量 pytest **195 passed**。

---

## 批次 5 — 语义检索（主题 C 开篇）✅ (2026-08-26 完成)
- [done] **零依赖本地向量近似召回**: 新建 `tools/semantic.py`, TF-IDF 向量 + 余弦相似度; 对代码(py/js/ts/go/rust/java/c/c++/c#/php/ruby/swift/kotlin/scala/sh) + 文档(md/txt/rst)建索引, 持久化 `<root>/.lmw_index/index.json`, 按 mtime 增量复用(命中复用/改文件自动重建)。
- [done] **中文语义召回**: 中文逐字 unigram + 相邻 bigram (`c:` 前缀隔离), 支持「数据库连接池配置」类意图召回; 英文/数字词级 token; `min_df=2` 平滑 IDF 去噪。
- [done] **新工具 `semantic_search`**: 参数 `query`(意图) / `scope`(all|code|docs) / `top_k`(默认8) / `glob` / `rebuild`; 返回 top-k 片段 `relpath:line score + snippet`, 引导接 `read_file`/`grep` 接力; 注册进 readonly + cacheable + prompt(12l)。
- [done] 单测 `tests/test_semantic_search.py`(7 例) 覆盖 tokenize 中英/英文召回/中文召回/增量复用/强制重建/scope 过滤/空查询; 全量 pytest **202 passed**。

---

## 批次 6 — 全球领先运行时三件套（Auto Context Compaction / 失败归因 / 证据链）✅ (2026-08-26 完成)
- [done] **自动上下文压缩 (Auto Context Compaction, 仿 Claude Code auto-compact)**: `loop._maybe_compact`/`_compact_history`/`_summarize_old`; 累计上下文字符超 `agent.context_compact_threshold`(默认 120000) 时, 把旧回合(除 system + 最近 `context_keep_recent`=6 轮)压缩为单条 `[历史压缩摘要]`; 优先调 LLM 摘要(失败/无则回退启发式提取 工具名+关键结论), 防长会话退化/溢出。emit `compact` 事件供可观测。默认开启(只触发超长)。
- [done] **工具失败自愈归因**: `_classify_failure` 纯函数, 把报错分类为 网络/权限/超时/资源/未找到/逻辑, 注入结果标记(如 `[工具 result: read_file #1] [网络异常?…]`), 模型按提示修正而非裸重试; prompt 12n 引导。
- [done] **证据链 (Provenance)**: 工具结果标记带稳定 `#seq`(如 `[tool result: read_file #1]`), 配合文件:行号可溯源每个结论到具体工具调用; chain 事件同步带 `fail_tag`。
- [done] **prompt 引导**: 新增 12m(长会话自动压缩: 信任 [历史压缩摘要] 继续) + 12n(失败归因: 按标签重试/换源/换路径)。
- [done] 单测 `tests/test_context_compaction.py`(12 例) 覆盖分类(网络/权限/超时/资源/未找到/逻辑/成功无标签)/压缩(关/启发式降长保最近轮/LLM 用摘要/防抖)/run 中 #seq 证据链 + 失败归因标签; 全量 pytest **214 passed**。

---

## 主题 A — 工具体系纵深（约 15 轮）
- [done] 工具调用配额：单任务累计工具调用次数上限，防失控循环烧钱（批次4）。
- [done] 工具结果缓存层：web_search/code_search 等只读搜索类同查询命中内存缓存，省 token 与时延（批次4）。
- [done] 工具结果脱敏：密钥/密码/令牌在回灌前自动遮蔽，防凭证泄露（批次4）。
- [ ] 工具结果结构化：MCP 返回 JSON 时自动提取关键字段（如 search 的标题/url、fetch 的正文），而非整页文本。
- [ ] 工具缓存层：`web_search`/`code_search` 同查询命中缓存，省 token 与时延。
- [ ] 危险命令沙箱增强：`run_command` 支持允许清单 + 超时 + 资源上限（CPU/内存）。
- [ ] 工具调用配额：单轮/单任务工具调用次数上限，防失控循环烧钱。
- [ ] `shell_exec` 工作目录隔离：明确 cwd 与 PATH 增强（已部分实现），补充命令白/黑名单 UI。
- [ ] `fs_*` 根目录可视化：面板展示 `LMW_FS_ROOT` 当前范围，避免越界写入。
- [ ] 增量 `repo_map`：仅重新扫描变更文件，大仓库提速。
- [ ] 工具失败自愈：MCP 连接断开时自动重连（指数退避），并回报 `tool_result` 而非崩溃。
- [ ] `db_query` 结果表格化渲染 + 行数限制 + 写操作二次确认。
- [ ] `git_*` 工具补全：`git_checkout/git_rebase/git_merg` 等，接进闭环。
- [ ] 工具调用「撤销」：`undo` 支持按 seq 回滚最近一次写操作。
- [ ] 外部工具市场 UI：面板内列出 9 个 MCP 服务器 + 已注册工具的启用/停用开关。
- [ ] 工具调用成本归因：每个工具消耗的 token/时延汇总进 `token_stats`。
- [ ] `code_review` 支持按严重度过滤 + 自动生成修复 PR 草稿。
- [ ] 工具结果语义压缩：超长输出用 LLM 摘要（可选）代替硬截断。

## 主题 B — 智能体循环与推理（约 15 轮）
- [ ] 计划模式产物：把 `think/todo` 输出渲染为可勾选任务卡，完成后自动更新。
- [ ] 子代理池：`subagent` 真正扇出并行子任务，结果聚合回主循环。
- [ ] 反思循环：每 N 轮做一次「目标-进展」自检，偏离则纠偏（超出 `_LOOP_HINT` 范畴）。
- [ ] 工具结果摘要回灌：长结果先 LLM 摘要再进上下文，省 token。
- [ ] 多模态输入：图片/截图直接进对话（已用 Read 读图，需接 web 上传）。
- [ ] 长任务断点续跑：超 `max_iter` 时落盘状态，用户「继续」即恢复而非重来。
- [ ] 证据链：每个结论标注其来源工具/文件行号，可点击溯源。
- [ ] 主动澄清：需求模糊时反问至多 1 次，避免盲目执行。
- [ ] 自验证：写完代码自动 `repo_map` + `grep` 自检接口签名一致性。
- [done] 上下文压缩：旧轮 `tool_result` 滚动摘要，支撑超长会话（批次6 Auto Context Compaction）。
- [ ] 工具选择学习：基于历史高成功率路径，优先推荐工具组合。
- [ ] 失败归因：工具报错后分类（网络/权限/逻辑），针对性重试或换工具。
- [ ] 多方案对比：复杂任务并行探索 2-3 方案，给出权衡建议再落地。
- [ ] 安全护栏强化：写入前 diff 预览强制（12e 闭环已含），加「破坏性操作二次确认」。
- [ ] 目标可达性判断：任务不可行时尽早返回，而非空转。

## 主题 C — 检索与上下文工程（约 10 轮）
- [done] 语义检索：本地向量索引（零依赖 TF-IDF + 余弦）支持 `semantic_search` 召回相关代码/文档（批次5）。
- [ ] 自动上下文裁剪：按当前任务动态选 relevant 文件注入 system。
- [ ] 文档库接入：把 `docs/` 纳入可被 `grep/语义` 检索的知识源。
- [ ] 依赖图：`repo_map` 升级为调用/导入关系图，辅助大重构。
- [ ] 变更影响分析：改某函数自动列出调用方，提示回归范围。
- [ ] `CLAUDE.md/AGENTS.md` 风格项目记忆自动生成与读取。
- [ ] 跨会话记忆：把高价值结论写入 `memory`，新会话自动召回。
- [ ] 代码注释覆盖率检查：低覆盖模块提示补注释。
- [ ] 桩/死代码检测：识别 TODO/未实现分支并预警。
- [ ] 多根工作区：支持同时挂载多个 repo 作为 roots。

## 主题 D — 安全与权限（约 10 轮）
- [ ] 权限模式 UI 化：面板切换 bypass/acceptEdits/plan，实时显示当前可调用工具集。
- [ ] 写操作审计日志：所有 `write_file/edit/fs_write/shell` 落盘审计。
- [ ] 危险模式识别：`rm -rf /`、DROP TABLE 等模式在 `shell/db` 层硬拦截。
- [ ] 凭证零落盘：`.env` 密钥不进任何工具结果、不进日志、不进会话导出。
- [done] 工具结果脱敏：自动遮蔽 token/密码/密钥后再回灌上下文（批次4）。
- [done] 工具失败自愈归因：报错分类（网络/权限/超时/资源/未找到/逻辑）并注入修正提示（批次6）。
- [ ] 沙箱网络策略：可配置允许外联域名（fetch/search 白名单）。
- [ ] 最小权限默认：`acceptEdits` 下默认只允许项目内写，越界需升级模式。
- [ ] 操作回滚点：每次写前打快照（轻量），可一键回退。
- [ ] 权限变更提示：模式切换时明确告知「现在能做什么/不能做什么」。
- [ ] 第三方 MCP 供应链校验：接入前校验服务器来源与能力声明。

## 主题 E — 可观测性与评测（约 10 轮）
- [ ] 运行追踪面板：可视化每轮 token/时延/工具调用瀑布图。
- [ ] 工具调用成功率/耗时指标：长期统计，定位慢/常败工具。
- [ ] 评测集：固定编码任务集（写/改/修/测/评），每次升级跑回归打分。
- [ ] 成本看板：按会话/任务汇总 LLM 花费，超阈值预警。
- [ ] 结构化日志：JSON 日志含 event/seq/tool/duration/ok，便于离线分析。
- [ ] 失败样本库：收集工具/循环失败案例，驱动针对性修复。
- [ ] 健康度自检：面板启动自检 9 MCP + LLM 连通，红绿状态。
- [ ] 会话导出：含工具链与成本的可读报告（HTML）。
- [ ] 性能基线：冷启动/首 token/单轮时延基线固化，防劣化。
- [ ] A/B 提示词：支持多版 system prompt 对照评测。

## 主题 F — Web UI 体验（约 12 轮）
- [ ] 工具链时间线：把 `chain` 渲染为可展开的时间线（含耗时/成败）。
- [ ] 文件树编辑器：IDE-lite 内联 diff 预览与一键接受/拒绝。
- [ ] 实时 token/成本条：对话中常驻显示估算用量。
- [ ] 多会话标签：同屏多任务并行，互不干扰。
- [ ] 命令面板：Ctrl+K 快速调工具/切模式/查状态。
- [ ] 移动端适配：窄屏下工具链与编辑器可用。
- [ ] 暗/亮主题切换（已暗色为主，补齐浅色）。
- [ ] 工具结果语法高亮：代码/JSON/表格渲染。
- [ ] 断点续跑 UI：「继续」按钮显式恢复长任务。
- [ ] 交付报告页：自动生成变更+测试+评审 HTML 报告可下载。
- [ ] 设置中心：可视化编辑 config.toml（模式/后端/截断阈值/MCP）。
- [ ] 错误边界：前端异常不白屏，给出可复制的诊断信息。

## 主题 G — 测试与 CI（约 8 轮）
- [ ] 覆盖率门禁：核心模块行覆盖 ≥80% 才能合并。
- [ ] e2e 录制回放：把 `lmw_sse_probe.py` 固化为定时回归。
- [ ] 模糊测试：对工具参数做 fuzz，防崩溃。
- [ ] 性能回归：单测内断言关键路径时延不退化。
- [ ] 跨平台矩阵：Win/Linux/macOS 各跑一轮 smoke。
- [ ] 冻结 exe 冒烟：每次重打包后自动 e2e 验证（替代人工）。
- [ ] 依赖审计：venv 依赖 CVE 扫描。
- [ ] 文档即测试：docs 中的示例可被执行校验。

## 主题 H — 打包与部署（约 5 轮）
- [ ] 重打包自动化：脚本封装「移旧→PyInstaller(plain)→校验 mtime→e2e」。
- [ ] 版本自报：面板读取 VERSION 展示，构建号进关于页。
- [ ] 一键启动器：`启动面板.bat`/`.sh` 跨平台，自动释放端口冲突。
- [ ] 增量更新：仅下发变更模块，缩短更新时间。
- [ ] 容器化：提供 Dockerfile 便于服务器常驻。

## 主题 I — 文档与生态（约 5 轮）
- [ ] 用户手册：从安装到高级玩法的完整文档。
- [ ] 工具开发者指南：如何写新 MCP 服务器/内置工具。
- [ ] 提示词手册：system prompt 设计原则与可调项。
- [ ] 示例库：典型任务的可复现会话（含工具链）。
- [ ] 贡献公约：代码风格/测试/提交规范。

---

## 进度小结
- 2026-08-26 批次1：工具系统综合硬化（权限分层/截断/repo_map多语言/可视化/prompt），169 单测全绿，已重打包+端到端验证。
- 2026-08-26 批次2：代码精读/检索/编辑能力增强（grep增强/read行号/edit模糊提示/apply_patch诊断/symbol_search/repo_map gitignore+depth），175 单测全绿，待重打包+端到端验证。
- 2026-08-26 批次3：智能体循环与推理增强（断点续跑/resume/反思循环/工具结果LLM摘要/prompt自验证+主动澄清），188 单测全绿，已重打包(01:31)+端到端验证。
- 2026-08-26 批次4：工具调用治理（配额tool_call_quota/结果缓存tool_cache_ttl/脱敏redact_secrets+prompt 12j/12k），195 单测全绿，已重打包(03:08)+e2e 验证(8318 PID 33164)。
- 2026-08-26 批次5：语义检索（主题C开篇，零依赖 TF-IDF+余弦 semantic_search，中英召回/增量持久化/readonly+cacheable+prompt 12l），202 单测全绿，已重打包(03:21)+e2e 验证(8318 PID 33784)。
- 2026-08-26 批次6：全球领先运行时三件套（自动上下文压缩 context_compact_threshold=120000/失败自愈归因 _classify_failure/证据链 #seq + prompt 12m/12n），214 单测全绿，待重打包+端到端验证。
