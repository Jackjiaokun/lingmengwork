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

## 批次 7 — 全球领先安全与项目记忆双引擎 ✅ (2026-08-26 完成)
- [done] **破坏性操作全局硬护栏**: `registry.execute` 分发前 `_guard_destructive(name,args,mode)` 扫描所有写/执行类工具(args 文本); 致命模式(`rm -rf /`/`mkfs`/`dd if=`/`chmod -R 777 /`/关机/`git push --force`/`curl|sh` 下载即执行管道等)**任何模式都硬拦**(不可能误删根/系统/他人远程); 高危模式(`rm -rf 某目录`/`git reset --hard`/`git clean -f`/`drop table`/`delete from` 无 where 等)在 plan/acceptEdits 拦截、bypass 告警放行。受 `agent` 开关 `destructive_guard`(默认 block)。比 shell 层 `deny_patterns` 更全(覆盖所有写工具/MCP)。
- [done] **写操作审计日志**: `registry._audit` 对所有写/执行类工具调用落盘 `<root>/.lmw_audit.log`(时间|mode|tool|ok|blocked|note|脱敏args), 拦截/执行均记录, 便于合规追溯。受 `agent.security.audit_log`(默认 True) 开关; args 经 `_redact_audit` 脱敏密钥。
- [done] **项目记忆文档自动读取注入 system**: `loop._load_project_docs` 启动时读项目根 `CLAUDE.md`/`AGENTS.md`/`README.md` 注入 `project_context`(仿 Claude Code 自动上下文), 让 agent 自动获得项目约定/技术栈。受 `agent.security.read_project_docs`(默认 True) 开关。
- [done] **prompt 引导**: 新增 12o(安全护栏: 致命被拦/高危需确认/优先可逆操作)。

## 批次 8 — 结构化决策与项目记忆补全 ✅ (2026-08-26 完成)
- [done] **多方案对比 `compare_options`**: 输入任务 + 2~N 个候选方案(标题/描述/优点/缺点/工作量/风险), 输出结构化对比表 + 建议方案(评分=优点数-缺点数-0.5×(工作量+风险)), 复杂决策先比后落(主题 B 多方案对比)。
- [done] **变更影响分析 `impact_analysis`**: 输入符号名, 扫描仓库定位定义位置 + 所有调用方/使用点, 按文件聚合调用数量并列出调用点明细; 大重构/重命名前看清回归范围(主题 C 变更影响分析)。
- [done] **项目文档自动生成 `generate_project_docs`**: 扫描仓库生成 CLAUDE.md/AGENTS.md 草稿(技术栈按文件数/关键目录/入口点/测试命令/已有约定), 直接补全批次7「项目记忆文档自动读取」的「自动生成工具待补」项, 让后续会话自动获得项目认知。
- [done] **Web 端点 `POST /api/docs/generate`**: 一键生成项目文档草稿并回显, 供「项目文档」按钮使用(WEB 优先)。
- [done] **prompt 引导**: 新增 12p(多方案对比)/12q(变更影响分析)/12r(项目文档自动生成)。
- [done] 单测 `tests/test_decision_tools.py`(11 例) 覆盖三工具; 全量 pytest **234 passed**。

---

## 批次 9 — 可观测性基础（主题 E 开篇）✅ (2026-08-26 完成)
- [done] **工具调用统计埋点**: `registry.execute` 统一计时+记录(拆出 `_execute_core` 返回 `(res,ok,tag)`), 进程级 `_STATS` 聚合每工具的 调用次数/成功/失败/平均耗时/失败归因标签 + 全局 total + 最近 50 条环形事件(`recent`)。线程安全(`_STATS_LOCK`)。
- [done] **失败归因标签**: `_classify_err` 与批次6 `_classify_failure` 同源(网络/权限/资源/未找到/逻辑), 失败工具按错误类型计数(`fail_by_tag`), 定位常败根因。
- [done] **Web 端点 `GET /api/stats`**: 实时返回 `{total_calls, success_rate, tools[], recent[]}`, 供面板/外部可观测展示运行期健康。
- [done] **prompt 引导 12s**: 可观测性——系统持续统计各工具调用次数/成功率/平均耗时/失败归因, 面板 `/api/stats` 实时展示, 频繁失败/异常慢可据此定位根因。
- [done] 单测 `tests/test_observability.py`(12 例) 覆盖分类/聚合/成功率/recent 截断/真实 execute 埋点(成功+失败+权限拒绝+未知工具归因); 全量 pytest **244 passed**。

---

## 批次 10 — 全链路健康度自检（主题 E 健康度）✅ (2026-08-26 完成)
- [done] **`tools/health.py`**: 纯函数 `health_check(cfg, *, llm_probe, mcp_probe, fs_probe)` 聚合全链路红绿——LLM 连通(真实最小 chat + 线程超时保护, 不阻塞 HTTP)/ 9 MCP 服务器(枚举 `cfg['mcp']['servers']` + 模块文件校验)/ 文件系统根(`resolve_roots` 可达性)。
- [done] **探针可注入**: `enumerate_mcp_servers`/`_module_file`(由 `-m 模块` 推导源码路径)/`probe_llm`/`probe_mcp_server`/`probe_filesystem`, 单测确定性; 端点注入真实探针。
- [done] **Web 端点 `GET /api/health/full`**: 调 `health_check` + 用 `MCPManager.connect_all(cfg)`(12s 线程超时守卫)补全各 MCP 实时 `connected` 状态, 返回 `{overall, llm, mcp_servers[9], filesystem}`。
- [done] **prompt 引导 12t**: 健康度自检——面板 `/api/health/full` 自检 LLM 连通+9 MCP+文件系统, 红绿状态定位失联组件。
- [done] 单测 `tests/test_health_check.py`(9 例) 覆盖枚举/模块路径/全绿/llm失败/fail/fs失败/mcp缺模块/overall warn + 真实 config.toml 枚举 9 MCP 烟测; 全量 pytest **253 passed**。

---

## 批次 11 — 运行追踪可视化仪表盘（主题 E 可视化开篇）✅ (2026-08-26 完成)
- [done] **独立可观测仪表盘页 `web/static/observability.html`**: 零依赖自包含(内联 CSS/JS, 无外部 CDN), 消费 `GET /api/stats` + `GET /api/health/full`。含 ①全链路健康度(overall 红绿 + LLM + 9 MCP 网格卡 + 文件系统) ②工具调用运行追踪(总调用/成功率大数字 + 明细表: 工具/调用/成功/失败/成功率/平均耗时/失败归因 + CSS 条形图 调用数/耗时) ③最近事件流(recent, 用 `e.ts` 时间戳格式化)。自动刷新(可见时轮询 5s)。
- [done] **Web 路由 `GET /observability`**: `do_GET` 加 `if p == "/observability": return self._serve_file("observability.html")`, 与 `/` 同源静态服务。
- [done] **主面板入口**: `index.html` 侧边栏加「📊 可观测仪表盘」链接(href=/observability), 一键跳转到仪表盘。
- [done] **字段一致性修复**: recent 事件时间字段真实为 `ts`(unix 秒, 非 `time`), 修正前端 `e.time`→`fmtTime(e.ts)` 显示空 bug。
- [done] 单测 `tests/test_observability_page.py`(5 例) 覆盖 页面存在+引用两端点 / `/observability` 路由注册 / index 入口 / `health_check` 结构字段齐全 / `get_stats` 聚合结构与 recent 字段; 全量 pytest **258 passed**。

## 批次 12 — 可观测可视化深化（主题 E 可视化续）✅ (2026-08-26 完成)
- [done] **埋点层耗时分位样本池**: `registry` 模块级 `_DURATIONS`(每工具 `deque(maxlen=240)`)+`_DUR_ALL`(全局 `deque(maxlen=800)`) 有界; `_record` 双写样本; 新增 `_pct`(线性插值分位, `round` 就近取整规避 `int()` 浮点负偏); `get_stats` 计算每工具 `p50/p95/p99/max/min` + 全局 `p50/p95/p99/total_ms/avg_ms`; `reset_stats` 同步清样本池。
- [done] **仪表盘深化 `observability.html`**: ① 顶部 6 卡(总调用/成功率/p50/p95/p99/工具数) ② 各工具耗时分位分布三色分段条(绿 p50/黄 p95/红 p99 + max) ③ 调用时间线瀑布图(Gantt 式: 每次调用一行, 横轴相对时间, 绿/红条按 ok/fail, >1500ms 标 ⚠ 慢尖刺); 复用 5s 自动轮询。
- [done] **prompt 12u**: 运行追踪可视化——时间线瀑布图 + 耗时分位, 识别长尾慢调用与失败尖刺。
- [done] 单测 4 例(`_pct` 线性插值/聚合分位/reset 清样本池/stats 分位字段齐全); 全量 pytest **262 passed**。

---

## 批次 13 — 三路并进：成本看板 + 计划看板 + 工具结果结构化（主题 E/B/A）✅ (2026-08-26 完成)
- [done] **主题 E 成本看板**: 新建 `llm/pricing.py` 单一价目源(商汤 flash-lite/flash、DeepSeek 等元/千 token 档, 含 `price_for/cost/reference_list/fmt_cny`); `loop.token_stats` 改用共享价目(输出含 model); 新增 `GET /api/cost`(遍历活体会话汇总每会话 est token/成本 + 进程总计 + 价目参考) + 零依赖 `cost.html`(进程总计卡 + 各会话表 + 价目表, 5s 自动刷新); `index.html` 加「💰 成本看板」入口; `prompt 12v`。
- [done] **主题 B 计划看板**: `loop` 新增 `plan_artifact`(计划模式 `mode=plan` 下捕获最终产物) + `_capture_plan`(仅 plan 模式且非错误文本才存) + 纯函数 `_parse_plan_cards`(markdown→可勾选卡片: 标题/分章节/复选框与编号步骤/备注, 扁平 tasks 供进度); 新增 `GET /api/planboard?id=`(活体会话计划数据) + 零依赖 `planboard.html`(卡片渲染 + checkbox 本机 localStorage 勾选进度持久化 + 最近会话快捷选择); `index.html` 加「🗂️ 计划看板」入口; `prompt 12w`。
- [done] **主题 A 工具结果结构化**: `registry` 新增 `_extract_struct`(O(n) 括号平衡扫描, 从工具结果抽取 JSON 结构 object/array/scalar + 字段数与键名, 容忍字符串内花括号/转义) 与 `_extract_balanced`; `execute` 成功结果双写结构化到 `recent` 事件(`structured` 含 is_json/kind/n/keys); `observability.html` 事件流新增绿色 `{}` 结构化徽标(悬停显示键名); `prompt 12x`。
- [done] 单测 17 例: `test_pricing`(价目/成本/参考/格式) + `test_plan_cards`(标题/章节/复选框/编号/纯备注/空 + `_parse_plan_cards` 解析 + 计划捕获仅 plan 模式生效) + `test_structure_extract`(object/array/scalar/嵌入/嵌套括号/非 JSON + 经 registry 端到端 recent 带 structured) + `test_cost_plan_endpoints`(成本聚合/计划 found/未找到); 全量 pytest **285 passed**。
- [done] plain PyInstaller 重打包(05:54) + 宿主启动 8318(PID 34844); 冻结版含 cost.html/planboard.html; e2e: `/api/cost` 返回 sessions/total/pricing, `/cost` `/planboard` `/observability` 均 200 且标题正确, `/api/planboard` 未知会话优雅返回 found=false。

---

## 批次 14 — 设置中心：可视化查看/编辑 config.toml（主题 F 设置中心）✅ (2026-08-26 完成)
- [done] **后端**: `server.py` 新增 `_SETTINGS_SCHEMA`(4 组 17 标量字段: LLM 后端 / 循环与治理 / 安全护栏 / MCP 启用, 含 label/type/options/section/restart 元数据) + `_config_path`(命中 load_config 候选序) + `_cfg_get`/`_fmt_toml_value`/`_set_scalar_in_toml`(O(n) 行内替换, 保留注释/缩进/数组, 段内无键则插入); `GET /api/settings`(返回 path/raw/schema/values) 与 `POST /api/settings`(mode=raw 整文件覆盖并校验 TOML 语法 / mode=form 标量行内替换并校验); 保存成功后软重载 `_RUNTIME_CONFIG` 即时部分生效, 若改 MCP 类字段则 try connect_all; 标 `restart=true` 字段提示重启完全生效。
- [done] **前端**: 零依赖 `settings.html`(分组表单视图 + 高级原始 TOML 视图双模式, checkbox/select/number/textarea 控件, 保存按钮 POST, 结果区显示「✓ 已保存 + 字节数 + ⚠ 需重启」); `index.html` 加「⚙️ 设置中心」入口; `prompt 12y`。
- [done] 单测 9 例 `test_settings.py`: `_config_path` 命中/缺失回退 + `_set_scalar_in_toml` 替换/嵌套段/插入 + `_fmt_toml_value` 类型格式化 + `_cfg_get` 嵌套 + `_settings_get` 结构(存在/缺失文件) + schema 字段全覆盖; 全量 pytest **294 passed**。
- [done] plain PyInstaller 重打包(06:06) + 宿主启动 8318(PID 8464); 冻结版含 settings.html; e2e: `/api/settings` 返回 4 组 17 字段 + `/settings` 200 + 非法 TOML 返回语法错误(不写文件) + 原样 raw/form roundtrip 等价重存且写回后 backend/max_iter 不变。

## 批次 15 — 结构化结果回写对话流（主题 A 闭环）✅ (2026-08-26 完成)
- [done] **后端闭环**: `loop.py` 在 `tool_result` 事件 emit 时调用 `registry._extract_struct(res)`(成功结果), 把抽取出的结构(`is_json`/`kind`/`n`/`keys`/`sample`)随 SSE 事件下发; `_chat_sse` 的 `emit` 经 `obj.update(kw)` 自动透传, 单聊与多路任务两条流均带 `structured` 字段。
- [done] **前端渲染**: `app.js` 新增 `appendStructured(outEl, s)` —— 工具返回 JSON 时在气泡内直接渲染「结构化面板」: 类型徽标(`{}`/`[]`/`#`) + 「对象 · N 字段 / 数组 · M 项」标签 + 键名 chip 流 + 对象样例值 mini 表; 单聊(`handleEvent`)与多路任务(`/api/tasks` 流)两处 `tool_result` 均接入; `styles.css` 新增 `.struct-panel`/`.struct-badge`/`.struct-keys`/`.kchip`/`.struct-sample` 样式(绿调渐变卡片)。
- [done] `prompt 12z`: 引导模型在工具返回结构化数据时优先依据关键字段推进, 不再复述整段 JSON 原文。
- [done] 单测 2 例 `test_structure_chat.py`: ① `loop.run` 驱动 JSON 工具 → `tool_result` 事件 `structured.is_json==True` 且 kind/keys/sample 正确; ② 非 JSON 结果 `structured is None`; 全量 pytest **296 passed**。
- [done] plain PyInstaller 重打包(06:35) + 宿主启动 8318(PID 36560); 冻结版 `/static/app.js` 含 `appendStructured`(3 处) 与 `struct-panel`(1 处), 运行服务 `/static/app.js` 同源命中。

---

## 主题 A — 工具体系纵深（约 15 轮）
- [done] 工具调用配额：单任务累计工具调用次数上限，防失控循环烧钱（批次4）。
- [done] 工具结果缓存层：web_search/code_search 等只读搜索类同查询命中内存缓存，省 token 与时延（批次4）。
- [done] 工具结果脱敏：密钥/密码/令牌在回灌前自动遮蔽，防凭证泄露（批次4）。
- [done] 工具结果结构化：成功 JSON 结果自动抽取结构(object/array/scalar + 字段数与键名)，recent 事件带 `structured` 徽标，面板可一眼识别结构化返回（批次13）；批次15 进一步闭环——结构化随对话流直接渲染进聊天气泡（类型徽标 + 字段 chip + 样例表），单聊/多路任务双流均覆盖。
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
- [done] 计划模式产物：计划模式下捕获最终方案 → 解析为可勾选任务卡(`/planboard` + 零依赖页, 本机勾选进度持久化)（批次13）。
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
- [done] 多方案对比：复杂任务并行探索 2-3 方案，给出权衡建议再落地（批次8 compare_options）。
- [ ] 安全护栏强化：写入前 diff 预览强制（12e 闭环已含），加「破坏性操作二次确认」。
- [ ] 目标可达性判断：任务不可行时尽早返回，而非空转。

## 主题 C — 检索与上下文工程（约 10 轮）
- [done] 语义检索：本地向量索引（零依赖 TF-IDF + 余弦）支持 `semantic_search` 召回相关代码/文档（批次5）。
- [ ] 自动上下文裁剪：按当前任务动态选 relevant 文件注入 system。
- [ ] 文档库接入：把 `docs/` 纳入可被 `grep/语义` 检索的知识源。
- [ ] 依赖图：`repo_map` 升级为调用/导入关系图，辅助大重构。
- [done] 变更影响分析：改某函数自动列出调用方，提示回归范围（批次8 impact_analysis）。
- [done] 项目记忆文档自动读取注入 system（`CLAUDE.md`/`AGENTS.md`/`README.md`，仿 Claude Code，批次7）；自动生成工具待补。
- [ ] 跨会话记忆：把高价值结论写入 `memory`，新会话自动召回。
- [ ] 代码注释覆盖率检查：低覆盖模块提示补注释。
- [ ] 桩/死代码检测：识别 TODO/未实现分支并预警。
- [ ] 多根工作区：支持同时挂载多个 repo 作为 roots。

## 主题 D — 安全与权限（约 10 轮）
- [ ] 权限模式 UI 化：面板切换 bypass/acceptEdits/plan，实时显示当前可调用工具集。
- [done] 写操作审计日志：所有写/执行类工具调用落盘 `.lmw_audit.log`（脱敏），便于合规追溯（批次7）。
- [done] 危险模式识别：所有写/执行类工具(args 文本)全局硬拦截 `rm -rf /`、`mkfs`、`dd if=`、`git push --force`、`curl|sh` 等致命模式，高危写操作受限模式拦截（批次7 破坏性护栏，覆盖 shell/db/MCP）。
- [ ] 凭证零落盘：`.env` 密钥不进任何工具结果、不进日志、不进会话导出。
- [done] 工具结果脱敏：自动遮蔽 token/密码/密钥后再回灌上下文（批次4）。
- [done] 工具失败自愈归因：报错分类（网络/权限/超时/资源/未找到/逻辑）并注入修正提示（批次6）。
- [ ] 沙箱网络策略：可配置允许外联域名（fetch/search 白名单）。
- [ ] 最小权限默认：`acceptEdits` 下默认只允许项目内写，越界需升级模式。
- [ ] 操作回滚点：每次写前打快照（轻量），可一键回退。
- [ ] 权限变更提示：模式切换时明确告知「现在能做什么/不能做什么」。
- [ ] 第三方 MCP 供应链校验：接入前校验服务器来源与能力声明。

## 主题 E — 可观测性与评测（约 10 轮）
- [done] 运行追踪面板：可视化每轮 token/时延/工具调用瀑布图（批次11 仪表盘 + 批次12 调用时间线瀑布图 + 耗时分位 p50/p95/p99）。
- [done] 工具调用成功率/耗时指标：长期统计，定位慢/常败工具（批次9 /api/stats + 批次12 全局/每工具耗时分位）。
- [ ] 评测集：固定编码任务集（写/改/修/测/评），每次升级跑回归打分。
- [ ] 成本看板：按会话/任务汇总 LLM 花费，超阈值预警（基础版已落地：`GET /api/cost` + `cost.html` 会话级 token/成本追踪, 批次13）。
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
- [done] 设置中心：可视化编辑 config.toml（模式/后端/截断阈值/MCP）—— 批次14 落地：表单视图 + 原始 TOML 双模式，即时软重载 + 需重启提示。
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
- 2026-08-26 批次7：全球领先安全与项目记忆双引擎（破坏性操作全局硬护栏 destructive_guard/写操作审计日志 .lmw_audit.log/CLAUDE.md 自动读取注入 system + prompt 12o），223 单测全绿，待重打包+端到端验证。
- 2026-08-26 批次8：结构化决策与项目记忆补全（多方案对比 compare_options/变更影响分析 impact_analysis/项目文档自动生成 generate_project_docs + Web 端点 /api/docs/generate + prompt 12p/12q/12r），234 单测全绿，已重打包(04:25)+e2e 验证(8318 工具总数 42 含三新工具 + /api/docs/generate 生成 CLAUDE.md 草稿)。
- 2026-08-26 批次9：可观测性基础（主题E开篇，registry.execute 统一埋点统计 调用次数/成功率/平均耗时/失败归因标签+recent 环形 + GET /api/stats + _classify_err 同源批次6 + prompt 12s），244 单测全绿，已重打包+e2e 验证(8318 真实 /api/chat 触发 list_dir×2/read_file×1 均被 /api/stats 统计, success_rate=1.0)。
