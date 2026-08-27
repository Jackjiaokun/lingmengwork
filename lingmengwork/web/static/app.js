"use strict";

const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const inputEl = $("input");
const sendBtn = $("send");
const pauseBtn = $("btn-pause");
let activeAbort = null;            // 当前对话流的 AbortController
function showPause(){ if(pauseBtn){ pauseBtn.hidden = false; sendBtn.hidden = true; } }
function hidePause(){ if(pauseBtn){ pauseBtn.hidden = true;  sendBtn.hidden = false; } }
if (pauseBtn) pauseBtn.addEventListener("click", () => { if (activeAbort) activeAbort.abort(); });
const statusEl = $("status");
const toolListEl = $("tool-list");
const providerListEl = $("provider-list");

const dashTasksEl = $("dash-tasks");
const dashRunningEl = $("dash-running");
const dashProvidersEl = $("dash-providers");
const dashTokensEl = $("dash-tokens");
const dashCostEl = $("dash-cost");
const dashResultsEl = $("dash-results");

const resultListEl = $("result-list");
const resultDetailEl = $("result-detail");
const resultMdEl = $("result-md");
const resultBackBtn = $("result-back");

const fileTreeEl = $("file-tree");
const filePathEl = $("file-path");
const fileUpBtn = $("file-up");
const fileContentEl = $("file-content");
const fileSaveStateEl = $("file-save-state");
const editorWrapEl = $("editor-wrap");
const editorFileEl = $("editor-file");
const editorSaveBtn = $("editor-save");
const editorReloadBtn = $("editor-reload");
const editorCopyBtn = $("editor-copy");
const editorDeliverBtn = $("editor-deliver");
const deliverBadgeEl = $("deliver-badge");
const editorExportBtn = $("editor-export");
const editorReviewChangedBtn = $("editor-review-changed");
const editorPrBtn = $("editor-pr");
const editorReviewReportBtn = $("editor-review-report");
const deliverNoteEl = $("deliver-note");
const artListEl = $("art-list");
const artRefreshBtn = $("art-refresh");
const artifactCountEl = $("artifact-count");
const editorHintEl = $("editor-hint");
const editorGutterEl = $("editor-gutter");
const editorTextEl = $("editor-text");
const editorHlEl = $("editor-hl");
let currentEditPath = null;   // 当前在编辑器中打开的文件相对路径

const sessionListEl = $("session-list");
const sessionDetailEl = $("session-detail");
const sessionMsgsEl = $("session-msgs");
const sessionBackBtn = $("session-back");
const sessionResumeBtn = $("session-resume");

const reviewListEl = $("review-list");
const reviewDetailEl = $("review-detail");
const reviewBackBtn = $("review-back");
const reviewBodyEl = $("review-body");

let history = []; // [{role, content}]
let currentFileDir = ".";

// 智能滚动: 仅当用户已贴底时才自动跟随; 否则露出「新消息」提示
let newMsgHintEl = null;
function isNearBottom() {
  return messagesEl.scrollTop + messagesEl.clientHeight >= messagesEl.scrollHeight - 80;
}
function showNewMsgHint() {
  if (!newMsgHintEl) {
    const panel = document.getElementById("panel-chat");
    if (!panel) return;
    newMsgHintEl = document.createElement("button");
    newMsgHintEl.id = "new-msg-hint";
    newMsgHintEl.className = "new-msg-hint";
    newMsgHintEl.textContent = "↓ 新消息";
    newMsgHintEl.addEventListener("click", () => {
      messagesEl.scrollTop = messagesEl.scrollHeight;
      newMsgHintEl.classList.remove("show");
    });
    panel.appendChild(newMsgHintEl);
  }
  newMsgHintEl.classList.add("show");
}
function scrollDown() {
  if (isNearBottom()) {
    messagesEl.scrollTop = messagesEl.scrollHeight;
    if (newMsgHintEl) newMsgHintEl.classList.remove("show");
  } else {
    showNewMsgHint();
  }
}

function copyTextFrom(node) {
  const clone = node.cloneNode(true);
  clone.querySelectorAll("button, .cb-head, .msg-copy, .msg-time").forEach(b => b.remove());
  return clone.textContent;
}

let pendingQuote = null;  // 引用回复: { role, snip }

function addMessage(role) {
  if ($("chat-search") && !$("chat-search").hidden) closeChatSearch();
  const wrap = document.createElement("div");
  wrap.className = "msg " + (role === "user" ? "user" : "agent");
  const tag = document.createElement("div");
  tag.className = "role-tag";
  tag.textContent = role === "user" ? "你" : "灵梦";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const time = document.createElement("span");
  time.className = "msg-time";
  time.textContent = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  const copyBtn = document.createElement("button");
  copyBtn.className = "msg-copy";
  copyBtn.textContent = "📋";
  copyBtn.title = "复制此消息";
  copyBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(copyTextFrom(bubble))
      .then(() => toast("已复制消息", "ok"))
      .catch(() => toast("复制失败 (剪贴板不可用)", "error"));
  });
  let actBtn = null;
  if (role === "user") {
    actBtn = document.createElement("button");
    actBtn.className = "msg-resend";
    actBtn.textContent = "🔄";
    actBtn.title = "重发此消息";
    actBtn.addEventListener("click", (e) => { e.stopPropagation(); resendUserMessage(bubble.parentElement); });
  } else if (role === "agent") {
    actBtn = document.createElement("button");
    actBtn.className = "msg-regen";
    actBtn.textContent = "🔄";
    actBtn.title = "重新生成回复";
    actBtn.addEventListener("click", (e) => { e.stopPropagation(); regenerateAgent(bubble.parentElement); });
  }
  const starBtn = document.createElement("button");
  starBtn.className = "msg-star";
  starBtn.textContent = "☆";
  starBtn.title = "收藏此消息";
  starBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleStar(wrap); });
  const quoteBtn = document.createElement("button");
  quoteBtn.className = "msg-quote";
  quoteBtn.textContent = "↩";
  quoteBtn.title = "引用此消息回复";
  quoteBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const role = wrap.classList.contains("user") ? "user" : "agent";
    const snip = copyTextFrom(bubble).replace(/\s+/g, " ").slice(0, 200);
    setQuote(role, snip);
  });
  wrap.appendChild(tag);
  wrap.appendChild(bubble);
  wrap.appendChild(time);
  wrap.appendChild(copyBtn);
  if (actBtn) wrap.appendChild(actBtn);
  wrap.appendChild(starBtn);
  wrap.appendChild(quoteBtn);
  messagesEl.appendChild(wrap);
  scrollDown();
  return bubble;
}

// 用户消息渲染: 抽离连续的 "> " 引用行作为引用卡, 其余为正文; 保留模式前缀小标签
function renderUserBubble(bubble, raw) {
  const mm = raw.match(/^【[^】]*】\s*/);
  const modePrefix = mm ? mm[0] : "";
  const rest = mm ? raw.slice(mm[0].length) : raw;
  const lines = rest.split("\n");
  let i = 0;
  while (i < lines.length && lines[i].startsWith("> ")) i++;
  bubble.textContent = "";
  if (modePrefix) {
    const mp = document.createElement("span");
    mp.className = "msg-mode-prefix";
    mp.textContent = modePrefix.trim();
    bubble.appendChild(mp);
  }
  if (i > 0 && i < lines.length) {
    const q = document.createElement("blockquote");
    q.className = "quote-ref";
    q.textContent = lines.slice(0, i).map(l => l.slice(2)).join("\n");
    const body = document.createElement("div");
    body.className = "quote-body";
    body.textContent = lines.slice(i).join("\n").trim();
    bubble.appendChild(q);
    bubble.appendChild(body);
  } else {
    bubble.appendChild(document.createTextNode(rest));
  }
}

// 引用回复 (P2i)
function setQuote(role, snip) {
  pendingQuote = { role, snip };
  const bar = $("quote-bar"); const qt = $("quote-text");
  if (bar && qt) { qt.textContent = (role === "user" ? "你" : "灵梦") + "：" + snip; bar.hidden = false; }
  if (inputEl) {
    if (inputEl.value.startsWith("> ")) {
      const idx = inputEl.value.indexOf("\n\n");
      inputEl.value = idx >= 0 ? inputEl.value.slice(idx + 2) : "";
    }
    const label = role === "user" ? "你" : "灵梦";
    const prefix = "> " + label + "：" + snip + "\n\n";
    const cur = inputEl.value;
    inputEl.value = cur ? (prefix + cur) : prefix.trim();
    inputEl.focus();
  }
  toast("已引用该消息, 可补充后发送 (点 ✕ 取消引用)", "ok");
}
function clearQuote() {
  pendingQuote = null;
  const bar = $("quote-bar"); if (bar) bar.hidden = true;
}

function esc(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// 全局错误/状态 toast (弱网/端点异常时给用户明确反馈)
function toast(msg, kind = "error", ms = 4000) {
  const el = $("toast");
  if (!el) return;
  el.textContent = msg;
  el.className = "toast show " + kind;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.className = "toast"; }, ms);
}

// 简易加载态 spinner 节点
function makeSpinner(label) {
  const s = document.createElement("div");
  s.className = "spinner-row";
  s.innerHTML = `<span class="spinner"></span><span>${esc(label || "执行中…")}</span>`;
  return s;
}

// ---------- 侧栏: 健康 / 通道 / 工具 ----------
async function loadHealth() {
  try {
    const r = await fetch("/api/health");
    const d = await r.json();
    statusEl.textContent = `后端: ${d.backend} / ${d.model} · v${d.version}`;
    statusEl.className = "status ok";
  } catch (e) {
    statusEl.textContent = "无法连接服务";
    statusEl.className = "status bad";
  }
}

// 全局仪表盘: 每隔一段时间聚合 tasks + results 目录
async function refreshDashboard() {
  try {
    const r = await fetch("/api/stats");
    const d = await r.json();
    dashTasksEl.textContent = d.tasks_total || 0;
    dashRunningEl.textContent = d.tasks_running || 0;
    dashProvidersEl.textContent = `${d.providers_online || 0}/${d.providers_total || 0}`;
    dashTokensEl.textContent = (d.total_tokens || 0).toLocaleString();
    dashCostEl.textContent = "¥" + (d.total_cost_cny || 0).toFixed(4);
    dashResultsEl.textContent = d.results_total || 0;
  } catch (e) {}
}

async function loadProviders() {
  try {
    const r = await fetch("/api/providers");
    const d = await r.json();
    providerListEl.innerHTML = "";
    (d.providers || []).forEach((p) => {
      const li = document.createElement("li");
      li.innerHTML = `<div class="t-name">${esc(p.name)}</div><div class="t-desc">${esc(p.model)} · ${p.available ? "在线" : "离线"}</div>`;
      providerListEl.appendChild(li);
    });
  } catch (e) {
    providerListEl.innerHTML = "<li>通道加载失败</li>";
  }
}

async function loadTools() {
  try {
    const r = await fetch("/api/tools");
    const d = await r.json();
    toolListEl.innerHTML = "";
    (d.tools || []).forEach((t) => {
      const li = document.createElement("li");
      li.innerHTML = `<div class="t-name">${esc(t.name)}</div><div class="t-desc">${esc(t.description || "")}</div>`;
      toolListEl.appendChild(li);
    });
  } catch (e) {
    toolListEl.innerHTML = "<li>工具加载失败</li>";
  }
}

async function loadMcp() {
  const el = $("mcp-list");
  if (!el) return;
  try {
    const r = await fetch("/api/mcp");
    const d = await r.json();
    el.innerHTML = "";
    if (!d.enabled) {
      el.innerHTML = '<li class="muted">已禁用 (config mcp.enabled=false)</li>';
      return;
    }
    const servers = d.servers || [];
    if (!servers.length) {
      el.innerHTML = '<li class="muted">未配置 (config.toml [[mcp.servers]])</li>';
      return;
    }
    servers.forEach((s) => {
      const li = document.createElement("li");
      const tools = (s.tools || []).map(esc).join("、") || "—";
      li.innerHTML = `<div class="t-name">${esc(s.name)}</div><div class="t-desc">${tools}</div>`;
      el.appendChild(li);
    });
  } catch (e) {
    el.innerHTML = '<li class="muted">MCP 加载失败</li>';
  }
}

// ---------- 智能体活动状态条 ----------
// 阶段文案与图标: 思考 / 调用工具 / 读文件 / 写文件 / 完成 / 出错
const STATUS_META = {
  think: { ico: "💭", text: "正在思考" },
  tool:  { ico: "🔧", text: "正在调用工具" },
  read:  { ico: "📖", text: "正在读文件" },
  write: { ico: "✍️", text: "正在写文件" },
  done:  { ico: "✓",  text: "已完成" },
  error: { ico: "⚠",  text: "出错了" },
  paused:{ ico: "⏸",  text: "已暂停" },
};
const READ_TOOLS  = ["read_file", "read_url", "fs_read", "code_search", "grep", "search",
                     "symbol_search", "sqlite_query", "db_query", "git_status", "git_diff",
                     "git_log", "web_fetch", "list_dir", "read_project_docs"];
const WRITE_TOOLS = ["write_file", "edit_file", "fs_write", "apply_patch", "append_file", "delete_file"];

// 识别工具类别 + 取路径尾名做标注细节
function classifyTool(name, args) {
  const n = (name || "").toLowerCase();
  const pathOf = (args && (args.path || args.file || args.repo || args.target)) || "";
  const tail = pathOf ? String(pathOf).split(/[\\/]/).pop() : "";
  if (READ_TOOLS.some((t) => n.includes(t)))  return ["read", tail];
  if (WRITE_TOOLS.some((t) => n.includes(t))) return ["write", tail];
  return ["tool", n];
}

function setAgentStatus(phase, detail) {
  const bar = $("agent-status");
  if (!bar) return;
  const m = STATUS_META[phase] || STATUS_META.tool;
  bar.className = "agent-status phase-" + phase;
  bar.hidden = false;
  const ico = bar.querySelector(".as-ico");
  const txt = bar.querySelector(".as-text");
  const fill = bar.querySelector(".as-fill");
  if (ico) ico.textContent = m.ico;
  if (txt) txt.textContent = detail ? m.text + "：" + detail : m.text;
  if (fill) {
    if (phase === "done")      { fill.className = "as-fill done"; }
    else if (phase === "error") { fill.className = "as-fill err"; }
    else                        { fill.className = "as-fill indet"; }
  }
  if (phase === "done" || phase === "error") {
    clearTimeout(bar._hideT);
    bar._hideT = setTimeout(() => { bar.hidden = true; if (fill) fill.className = "as-fill"; }, 1500);
  }
}

// ---------- 单路对话 (兼容旧能力) ----------
function send() {
  let text = inputEl.value.trim();
  if (!text) return;
  if (pendingQuote) {
    const label = pendingQuote.role === "user" ? "你" : "灵梦";
    text = "> " + label + "：" + pendingQuote.snip + "\n\n" + text;
  }
  inputEl.value = "";
  clearQuote();
  sendCore(text, currentModeValue());
}

function currentModeValue() {
  const m = $("agent-mode");
  return (m && m.value) || "bypassPermissions";
}

// 重发 / 重新生成 共用: 以显式文本驱动一次完整对话回合
async function sendCore(text, submitMode) {
  const modeLabel = { bypassPermissions: "全放开", acceptEdits: "接受编辑", plan: "计划" }[submitMode] || submitMode;
  const userBubble = addMessage("user");
  renderUserBubble(userBubble, "【" + modeLabel + "】 " + text);
  userBubble.dataset.userText = text;   // 供「重发」读取原始提问
  userBubble.dataset.mode = submitMode; // 供「重发/重新生成」沿用同一模式
  history.push({ role: "user", content: text });

  const agentBubble = addMessage("agent");
  agentBubble.classList.add("typing");
  agentBubble.textContent = "";
  setAgentStatus("think");
  // 多工具调用链可视化条 (全链路时序节点)
  const chainStrip = document.createElement("div");
  chainStrip.className = "chain-strip";
  const narr = document.createTextNode("");
  const toolsBox = document.createElement("div");
  toolsBox.className = "tools";
  agentBubble.appendChild(chainStrip);
  agentBubble.appendChild(narr);
  agentBubble.appendChild(toolsBox);

  sendBtn.disabled = true;
  activeAbort = new AbortController();
  showPause();
  let wasPaused = false;
  let acc = "";
  let lastEventAt = Date.now();
  // 看门狗: 超过 90s 无任何事件 -> 提示(仍继续等待, 不中断)
  const watch = setInterval(() => {
    if (Date.now() - lastEventAt > 90000) {
      toast("后端响应较慢, 仍在等待… (如长时间无响应请检查服务是否存活)", "warn");
    }
  }, 30000);
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: currentSessionId, history: history.slice(0, -1), mode: submitMode, ...getEnhanceSel() }),
      signal: activeAbort.signal,
    });
    if (!resp.ok) {
      const errTxt = await resp.text().catch(() => "");
      throw new Error(`HTTP ${resp.status} ${errTxt.slice(0, 200)}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      lastEventAt = Date.now();
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = frame.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const evt = JSON.parse(line.slice(6));
        handleEvent(evt, narr, toolsBox, chainStrip, (s) => { acc += s; });
        if (evt.type === "done" && evt.session_id) {
          currentSessionId = evt.session_id;
          const _at = activeTab();
          if (_at && !_at.sessionId) { _at.sessionId = evt.session_id; refreshTabTitles(); }
        }
        if (evt.type === "error") {
          setAgentStatus("error", evt.message);
          toast("后端执行异常: " + (evt.message || "未知错误"), "error");
        }
      }
    }
    // 非 plan 模式: 把累积文本升级为 markdown + 代码块卡片
    if (submitMode !== "plan") {
      upgradeBubbleToMarkdown(narr);
    }
    // plan 模式: 方案生成后展示确认卡片
    if (submitMode === "plan" && acc.trim()) {
      showPlanCard(text, acc, narr);
    }
  } catch (e) {
    if (e && e.name === "AbortError") {
      // 用户主动暂停: 保留已生成内容, 不视为错误
      wasPaused = true;
      setAgentStatus("paused", "已暂停生成");
      if (submitMode !== "plan") upgradeBubbleToMarkdown(narr);
      if (acc.trim()) history.push({ role: "assistant", content: acc });
      toast("已暂停生成, 上下文已保留, 可继续对话", "ok");
      return;
    }
    agentBubble.classList.remove("typing");
    const b = document.createElement("div");
    b.className = "bubble";
    b.style.color = "var(--bad)";
    b.textContent = "请求失败: 服务可能已中断, 请检查后端进程。 (" + e + ")";
    agentBubble.replaceWith(b);
    toast("对话请求失败, 请检查服务是否存活", "error");
  } finally {
    clearInterval(watch);
    agentBubble.classList.remove("typing");
    sendBtn.disabled = false;
    hidePause();
    activeAbort = null;
    if (!wasPaused) history.push({ role: "assistant", content: acc });
    scrollDown();
  }
}

// ---------- 消息重发 / 重新生成 (P2g) ----------
function extractUserText(userWrap) {
  const b = userWrap.querySelector(".bubble");
  const raw = (b && b.dataset.userText) || (b ? b.textContent : "");
  return raw.replace(/^【[^】]*】\s*/, "");
}
function findPrecedingUser(wrap) {
  let n = wrap.previousElementSibling;
  while (n) {
    if (n.classList && n.classList.contains("msg") && n.classList.contains("user")) return n;
    n = n.previousElementSibling;
  }
  return null;
}
function removeAgentTurn(agentWrap) {
  // 删除 agent 气泡 + 紧跟的 plan-card, 直到遇到下一个 .msg 为止
  let n = agentWrap;
  while (n) {
    const next = n.nextElementSibling;
    n.remove();
    if (next && next.classList && next.classList.contains("msg")) break;
    n = next;
  }
}
// 从历史尾部弹出一个 (user+assistant) 回合, 配合重新生成保持 history 干净
function popTurnIfMatches(text) {
  if (history.length && history[history.length - 1].role === "assistant") history.pop();
  if (history.length && history[history.length - 1].role === "user" && history[history.length - 1].content === text) history.pop();
}
async function resendUserMessage(userWrap) {
  const b = userWrap.querySelector(".bubble");
  const text = extractUserText(userWrap);
  if (!text.trim()) return;
  await sendCore(text, (b && b.dataset.mode) || currentModeValue());
}
async function regenerateAgent(agentWrap) {
  const userWrap = findPrecedingUser(agentWrap);
  if (!userWrap) { toast("找不到对应的用户提问, 无法重新生成", "warn"); return; }
  const b = userWrap.querySelector(".bubble");
  const text = extractUserText(userWrap);
  if (!text.trim()) return;
  const mode = (b && b.dataset.mode) || currentModeValue();
  removeAgentTurn(agentWrap);  // 清掉旧回复 + 可能的方案卡
  userWrap.remove();           // 旧提问也一并移除, 稍后由 sendCore 重建
  popTurnIfMatches(text);      // 同步从历史尾部弹出该回合
  await sendCore(text, mode);
}

// ---------- 消息收藏 (P2h) ----------
function starKey(sid) { return "lmw:stars:" + (sid || currentSessionId || "none"); }
function getStars(sid) { try { return JSON.parse(localStorage.getItem(starKey(sid)) || "[]"); } catch { return []; } }
function setStars(arr, sid) { try { localStorage.setItem(starKey(sid), JSON.stringify(arr)); } catch {} }

// ===== 主题 F: 专家/技能 提示词增强 — 选择持久化 (localStorage) =====
const ENH_KEY = "lmw:enhance";
function getEnhanceSel() {
  try { const d = JSON.parse(localStorage.getItem(ENH_KEY) || "{}"); return { experts: d.experts || [], skills: d.skills || [] }; }
  catch { return { experts: [], skills: [] }; }
}
function setEnhanceSel(sel) { try { localStorage.setItem(ENH_KEY, JSON.stringify(sel)); } catch {} }
function renderEnhanceChips() {
  const el = document.getElementById("enhance-chips");
  if (!el) return;
  const sel = getEnhanceSel();
  const names = [...sel.experts, ...sel.skills];
  el.innerHTML = names.map(n => `<span class="enh-chip">${n.replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}</span>`).join("");
}
function msgHash(role, text) {
  let h = 5381; const s = role + "::" + text;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return "h" + h.toString(36);
}
function applyStarToWrap(wrap, starred) {
  const sb = wrap.querySelector(".msg-star");
  if (!sb) return;
  if (starred) { wrap.classList.add("starred"); sb.textContent = "⭐"; sb.title = "取消收藏"; }
  else { wrap.classList.remove("starred"); sb.textContent = "☆"; sb.title = "收藏此消息"; }
}
function applyAllStarStates() {
  const set = new Set(getStars().map(x => x.h));
  messagesEl.querySelectorAll(".msg").forEach(wrap => {
    const bubble = wrap.querySelector(".bubble"); if (!bubble) return;
    const role = wrap.classList.contains("user") ? "user" : "agent";
    const h = msgHash(role, copyTextFrom(bubble));
    applyStarToWrap(wrap, set.has(h));
  });
}
function toggleStar(wrap) {
  const bubble = wrap.querySelector(".bubble"); if (!bubble) return;
  const role = wrap.classList.contains("user") ? "user" : "agent";
  const text = copyTextFrom(bubble);
  if (!text.trim()) { toast("空消息无法收藏", "warn"); return; }
  const h = msgHash(role, text);
  const arr = getStars();
  const ex = arr.find(x => x.h === h);
  if (ex) { setStars(arr.filter(x => x.h !== h)); toast("已取消收藏", "ok"); applyStarToWrap(wrap, false); }
  else { arr.unshift({ h, role, snip: text.slice(0, 160), ts: Date.now() }); setStars(arr); toast("已收藏此消息", "ok"); applyStarToWrap(wrap, true); }
}
async function openStars() {
  const modal = $("stars-modal"); if (!modal) return;
  const list = $("stars-list"); if (!list) return;
  list.innerHTML = "";
  let sessions = [];
  try { const r = await fetch("/api/sessions"); const j = await r.json(); sessions = j.sessions || j || []; } catch {}
  const entries = [];
  for (const s of sessions) {
    const sid = s.id || s.session_id || s;
    let arr = []; try { arr = JSON.parse(localStorage.getItem("lmw:stars:" + sid) || "[]"); } catch {}
    arr.forEach(it => entries.push(Object.assign({ sid: sid, title: s.title || sid }, it)));
  }
  entries.sort((a, b) => b.ts - a.ts);
  if (!entries.length) {
    list.innerHTML = '<div class="empty">还没有收藏任何消息。把鼠标移到消息上，点 ☆ 即可收藏。</div>';
  } else {
    entries.forEach(it => {
      const card = document.createElement("div");
      card.className = "stars-card";
      card.innerHTML = `<div class="stars-meta"><span class="stars-role ${it.role}">${it.role === "user" ? "你" : "灵梦"}</span><span class="stars-sess">${esc(it.title || it.sid || "")}</span><span class="stars-ts">${new Date(it.ts).toLocaleString("zh-CN")}</span></div><div class="stars-snip">${esc(it.snip || "")}</div>`;
      card.addEventListener("click", () => jumpToStar(it));
      list.appendChild(card);
    });
  }
  modal.hidden = false;
}
async function jumpToStar(it) {
  const modal = $("stars-modal"); if (modal) modal.hidden = true;
  if (currentSessionId !== it.sid) {
    const t = openTabs.find(x => x.sessionId === it.sid);
    if (t) { await activateTab(t.tabId); }
    else { await createTab(it.sid, it.title || it.sid, true); }
  }
  setTimeout(() => {
    const nodes = messagesEl.querySelectorAll(".msg");
    let found = null;
    nodes.forEach(wrap => {
      const bubble = wrap.querySelector(".bubble"); if (!bubble) return;
      const role = wrap.classList.contains("user") ? "user" : "agent";
      if (msgHash(role, copyTextFrom(bubble)) === it.h) found = wrap;
    });
    if (found) { found.scrollIntoView({ block: "center" }); found.classList.add("star-flash"); setTimeout(() => found.classList.remove("star-flash"), 1600); }
    else { toast("该消息在当前会话中未找到 (可能已删除)", "warn"); }
  }, 450);
}

function handleEvent(evt, narr, toolsBox, chainStrip, addText) {
  if (evt.type === "text") {
    addText(evt.chunk);
    narr.textContent += evt.chunk;
    setAgentStatus("think");
  } else if (evt.type === "tool") {
    const [ph, det] = classifyTool(evt.name, evt.args);
    setAgentStatus(ph, det);
    const kindLabels = { read: "读", write: "写", command: "命令", search: "检索", mcp: "MCP", other: "工具" };
    const kind = evt.kind || "other";
    const call = document.createElement("details");
    call.className = "tool-call";
    const args = Object.entries(evt.args || {})
      .map(([k, v]) => `${k}=${v}`)
      .join(", ");
    call.innerHTML = `<summary class="tc-head"><span class="tc-kind k-${kind}">${kindLabels[kind] || "工具"}</span>⚙ ${esc(evt.name)}(${esc(args.slice(0, 120))})</summary>`;
    const out = document.createElement("div");
    out.className = "tc-out";
    out.textContent = "执行中…";
    call.appendChild(out);
    toolsBox.appendChild(call);
    call._out = out;
    // 链路节点: 运行中
    if (chainStrip) {
      const node = document.createElement("span");
      node.className = "chain-node kc-" + (evt.kind || "other") + " running";
      node.textContent = (evt.seq ? evt.seq + ". " : "") + evt.name;
      node.title = (evt.kind || "other") + " · 执行中…";
      chainStrip.appendChild(node);
      call._chainNode = node;
    }
  } else if (evt.type === "tool_result") {
    const last = toolsBox.lastElementChild;
    if (last && last._out) {
      last._out.textContent = String(evt.output || "").slice(0, 2000);
      const lc = last;
      lc.setAttribute("open", "");
      // 测试自愈闭环结果红/绿染色
      if (evt.name === "auto_test") {
        const ok = /✅|全部通过/.test(evt.output || "");
        last._out.classList.add(ok ? "ok" : "bad");
      }
      if (evt.name === "review_code") {
        const parsed = parseCodeReview(evt.output || "");
          if (parsed) {
            last._out.innerHTML = renderReviewInline(parsed);
            last._out.classList.add("review-inline");
            loadReviews();
          }
        }
        // 主题 A 闭环 (批次15): 工具返回 JSON 时, 在气泡内直接渲染「结构化字段/键名」
        if (evt.structured && evt.structured.is_json) {
          appendStructured(last._out, evt.structured, evt.output);
        }
      }
      // 链路节点: 完成态染色 (ok / fail)
    if (chainStrip && last && last._chainNode) {
      const n = last._chainNode;
      n.classList.remove("running");
      n.classList.add(evt.ok === false ? "fail" : "ok");
      n.title = (evt.kind || "other") + " · " + (evt.ok === false ? "执行失败" : "完成");
    }
  } else if (evt.type === "done") {
    setAgentStatus("done");
    collapseLongMessages();
    if (evt.truncated) {
      const note = document.createElement("div");
      note.style.color = "var(--bad)";
      note.textContent = "\n[已达最大迭代次数, 强行结束]";
      toolsBox.parentElement.appendChild(note);
    }
    // 链路汇总: 工具用量统计
    if (chainStrip && evt.chain && evt.chain.length) {
      const counts = {};
      evt.chain.forEach((c) => { counts[c.name] = (counts[c.name] || 0) + 1; });
      const summary = document.createElement("div");
      summary.className = "chain-summary";
      const total = evt.chain.length;
      const failed = evt.chain.filter((c) => c.ok === false).length;
      summary.textContent = `全链路: 共 ${total} 次工具调用` + (failed ? ` · ${failed} 次失败` : " · 全部成功") + " · " + Object.keys(counts).join(" / ");
      chainStrip.appendChild(summary);
    }
  }
}

// 主题 A 闭环 (批次15): 把工具返回的 JSON 结构渲染进气泡 (字段/键名/样例值)
// 主题 A 闭环增强: 消费 structview.js 的纯函数 buildStructuredHTML, 渲染结构化面板
// (类型徽标/键名chip/对象样例表/数组表格化对比/标量值/一键展开原始JSON)。
function appendStructured(outEl, s, rawText) {
  if (!s || !s.is_json) return;
  const panel = document.createElement("div");
  panel.className = "struct-panel";
  const builder = (typeof window !== "undefined" && typeof window.buildStructuredHTML === "function")
    ? window.buildStructuredHTML : _structFallbackHTML;
  panel.innerHTML = builder(s, rawText);
  const rawBtn = panel.querySelector(".struct-raw-btn");
  const rawPre = panel.querySelector(".struct-raw");
  if (rawBtn && rawPre) {
    rawBtn.addEventListener("click", () => {
      const hidden = rawPre.style.display !== "block";
      rawPre.style.display = hidden ? "block" : "none";
      rawBtn.textContent = hidden ? "{} 收起" : "{} 原始";
    });
  }
  outEl.appendChild(panel);
}

// 降级: structview.js 未加载时的极小渲染, 避免整段 tool_result 渲染崩溃
function _structFallbackHTML(s) {
  let html = '<div class="struct-head"><span class="struct-badge">' +
    (s.kind === "array" ? "[]" : (s.kind === "object" ? "{}" : "#")) +
    '</span><span class="struct-label">' + esc(String(s.kind || "")) + '</span></div>';
  if (s.keys && s.keys.length) {
    html += '<div class="struct-keys">' +
      s.keys.slice(0, 24).map((k) => '<span class="kchip">' + esc(k) + '</span>').join("") + '</div>';
  }
  return html;
}

// ---------- 计划模式: 方案 diff 审阅卡片 ----------
// 轻量 markdown 渲染: 支持 ``` 围栏代码块 / 标题 / 列表 / 粗体
function inlineBold(t) {
  return t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}
function renderInline(txt) {
  const lines = txt.split("\n");
  let html = "", inUl = false, inOl = false;
  const closeLists = () => { if (inUl) { html += "</ul>"; inUl = false; } if (inOl) { html += "</ol>"; inOl = false; } };
  for (const line of lines) {
    const s = line.trim();
    if (!s) { closeLists(); continue; }
    let m;
    if ((m = s.match(/^(#{1,4})\s+(.*)$/))) { closeLists(); const lvl = m[1].length; html += `<h${lvl} class="md-h">${inlineBold(esc(m[2]))}</h${lvl}>`; }
    else if ((m = s.match(/^[-*]\s+(.*)$/))) { if (!inUl) { closeLists(); html += '<ul class="md-ul">'; inUl = true; } html += `<li>${inlineBold(esc(m[1]))}</li>`; }
    else if ((m = s.match(/^\d+\.\s+(.*)$/))) { if (!inOl) { closeLists(); html += '<ol class="md-ol">'; inOl = true; } html += `<li>${inlineBold(esc(m[1]))}</li>`; }
    else { closeLists(); html += `<p class="md-p">${inlineBold(esc(s))}</p>`; }
  }
  closeLists();
  return html;
}
// 把累积文本升级为 markdown + 代码块卡片 (带复制/应用到文件)
function renderMarkdownLite(src) {
  const parts = src.split(/```/);
  let html = "";
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      const seg = parts[i].trim();
      if (seg) html += `<div class="md-seg">${renderInline(seg)}</div>`;
    } else {
      const nl = parts[i].indexOf("\n");
      let lang = "", code = parts[i];
      if (nl >= 0) { lang = parts[i].slice(0, nl).trim(); code = parts[i].slice(nl + 1); }
      code = code.replace(/\n$/, "");
      if (!code.trim()) continue;
      const rowCount = code.split("\n").length;
      const codeRows = code.split("\n").map((l, i) => `<div class="cb-line"><span class="ln">${i + 1}</span><span class="lc">${l ? esc(l) : "&nbsp;"}</span></div>`).join("");
      html += `<div class="code-block"><div class="cb-head"><span class="cb-lang">${esc(lang || "代码")}</span><span class="cb-acts"><span class="cb-stat">${rowCount} 行</span><button class="cb-copy">📋 复制</button><button class="cb-apply">📝 应用到文件</button></span></div><div class="cb-lines">${codeRows}</div></div>`;
    }
  }
  return html;
}
// 上下文弹窗专用渲染: 支持标题/列表/粗体/引用块/任务复选框 (全程 esc 转义)
function renderCtxMarkdown(src) {
  const lines = (src || "").split("\n");
  let html = "", inUl = false, inOl = false, inQuote = false;
  const close = () => {
    if (inUl) { html += "</ul>"; inUl = false; }
    if (inOl) { html += "</ol>"; inOl = false; }
    if (inQuote) { html += "</blockquote>"; inQuote = false; }
  };
  for (const line of lines) {
    const s = line.trim();
    if (!s) { close(); continue; }
    let m;
    if ((m = s.match(/^(#{1,4})\s+(.*)$/))) { close(); const lvl = m[1].length; html += `<h${lvl} class="ctx-h">${inlineBold(esc(m[2]))}</h${lvl}>`; }
    else if ((m = s.match(/^>\s?(.*)$/))) { if (!inQuote) { close(); html += '<blockquote class="ctx-quote">'; inQuote = true; } html += `<p>${inlineBold(esc(m[1]))}</p>`; }
    else if ((m = s.match(/^[-*]\s+\[([ xX])\]\s+(.*)$/))) { if (!inUl) { close(); html += '<ul class="ctx-tasks">'; inUl = true; } const done = m[1].toLowerCase() === "x"; html += `<li class="ctx-task${done ? " done" : ""}"><span class="box">${done ? "✓" : "○"}</span>${inlineBold(esc(m[2]))}</li>`; }
    else if ((m = s.match(/^[-*]\s+(.*)$/))) { if (!inUl) { close(); html += '<ul class="ctx-ul">'; inUl = true; } html += `<li>${inlineBold(esc(m[1]))}</li>`; }
    else if ((m = s.match(/^\d+\.\s+(.*)$/))) { if (!inOl) { close(); html += '<ol class="ctx-ol">'; inOl = true; } html += `<li>${inlineBold(esc(m[1]))}</li>`; }
    else { close(); html += `<p class="ctx-p">${inlineBold(esc(s))}</p>`; }
  }
  close();
  return html;
}
function bindCodeBlock(block) {
  const linesEl = block.querySelector(".cb-lines");
  if (!linesEl) return;
  const getCode = () => {
    const clone = linesEl.cloneNode(true);
    clone.querySelectorAll(".ln").forEach(e => e.remove());
    return Array.from(clone.querySelectorAll(".lc")).map(e => e.textContent).join("\n");
  };
  const copyBtn = block.querySelector(".cb-copy");
  const applyBtn = block.querySelector(".cb-apply");
  if (copyBtn) copyBtn.addEventListener("click", () => {
    navigator.clipboard.writeText(getCode())
      .then(() => toast("已复制到剪贴板", "ok"))
      .catch(() => toast("复制失败 (剪贴板不可用)", "error"));
  });
  if (applyBtn) applyBtn.addEventListener("click", async () => {
    const p = prompt("应用到文件 (相对工作区路径):", currentEditPath || "");
    if (!p) return;
    try {
      const r = await fetch("/api/fs/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: p, content: getCode() }) });
      const d = await r.json();
      if (d.error) toast(d.error, "error"); else toast("已写入 " + p + " (" + d.bytes + " 字节)", "ok");
    } catch (e) { toast("应用失败: " + e, "error"); }
  });
}
function upgradeBubbleToMarkdown(narrNode) {
  if (!narrNode || !narrNode.textContent.trim()) return;
  const container = document.createElement("div");
  container.className = "md-body";
  container.innerHTML = renderMarkdownLite(narrNode.textContent);
  container.querySelectorAll(".code-block").forEach(bindCodeBlock);
  narrNode.replaceWith(container);
}
// 规划模式横幅
function setPlanBanner(show) {
  let b = $("plan-banner");
  if (show) {
    if (!b) {
      b = document.createElement("div");
      b.id = "plan-banner";
      b.className = "plan-banner";
      b.innerHTML = '📋 <b>规划模式</b>：AI 仅出方案，审阅后点「✅ 批准全部并执行」才会改动文件。';
      messagesEl.insertBefore(b, messagesEl.firstChild);
    }
  } else if (b) {
    b.remove();
  }
}
function showPlanCard(originalPrompt, planText, narrNode) {
  // 避免与 agent 气泡文本重复: 清空源文本节点 (保留探查工具链)
  if (narrNode) narrNode.textContent = "";
  const card = document.createElement("div");
  card.className = "plan-card";
  // 拆成说明段 + 代码块段
  const parts = planText.split(/```/);
  let notes = [], items = [];
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      const nl = parts[i].indexOf("\n");
      let lang = "", code = parts[i];
      if (nl >= 0) { lang = parts[i].slice(0, nl).trim(); code = parts[i].slice(nl + 1); }
      code = code.replace(/\n$/, "");
      if (code.trim()) items.push({ lang, code });
    } else {
      const t = parts[i].trim();
      if (t) notes.push(t);
    }
  }
  const notesHtml = notes.length ? `<div class="plan-notes">${notes.map((n) => renderInline(n)).join("")}</div>` : "";
  const itemsHtml = items.map((it, idx) => {
    const path = extractPathFromText(notes.join("\n") + "\n" + it.lang + "\n" + it.code);
    return `
    <div class="plan-diff" data-i="${idx}">
      <div class="pd-head">
        <span class="pd-badge">变更 ${idx + 1}</span>
        <span class="pd-lang">${esc(it.lang || "代码")}</span>
        ${path ? `<span class="pd-path" title="目标文件">📄 ${esc(path)}</span>` : ""}
        <span class="cb-acts">
          <button class="pd-copy" data-i="${idx}">📋 复制</button>
          <button class="pd-apply" data-i="${idx}">📝 应用到文件</button>
        </span>
      </div>
      <pre class="pd-pre" data-i="${idx}"><code>${esc(it.code)}</code></pre>
      <div class="pd-diffbox" data-i="${idx}"></div>
    </div>`;
  }).join("");
  const stat = items.length ? ` · ${items.length} 个代码变更` : "";
  card.innerHTML = `
    <div class="plan-head">📋 计划方案 (只读探查完成, 尚未改动任何文件)${stat}</div>
    <div class="plan-body-md">${notesHtml || '<div class="empty">（方案为空）</div>'}${itemsHtml}</div>
    <div class="plan-actions">
      <button class="btn-primary plan-run">✅ 批准全部并执行</button>
      <button class="btn-ghost plan-dismiss">✕ 忽略</button>
    </div>`;
  card.querySelectorAll(".pd-copy").forEach((btn) => {
    btn.addEventListener("click", () => {
      const pre = card.querySelector('.pd-pre[data-i="' + btn.dataset.i + '"] code');
      if (pre) navigator.clipboard.writeText(pre.textContent)
        .then(() => toast("已复制变更 " + (Number(btn.dataset.i) + 1), "ok"))
        .catch(() => toast("复制失败", "error"));
    });
  });
  card.querySelectorAll(".pd-apply").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const it = items[Number(btn.dataset.i)];
      const def = extractPathFromText(notes.join("\n") + "\n" + it.lang + "\n" + it.code) || "";
      const p = prompt("应用到文件 (项目相对路径):", def);
      if (!p) return;
      try {
        const res = await fetch("/api/fs/save", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: p, content: it.code }),
        });
        const j = await res.json();
        if (j && (j.ok || j.success)) toast("已写入 " + p, "ok");
        else if (j && j.error) toast("写入失败: " + j.error, "error");
        else toast("已写入 " + p, "ok");
      } catch (e) { toast("写入失败: " + e, "error"); }
    });
  });
  card.querySelector(".plan-dismiss").addEventListener("click", () => card.remove());
  card.querySelector(".plan-run").addEventListener("click", async () => {
    card.querySelector(".plan-run").disabled = true;
    card.querySelector(".plan-run").textContent = "执行中…";
    // 用 acceptEdits 模式真正执行 (允许写/编辑, 但 run_command 仍需 bypass)
    await executeMode(originalPrompt, "acceptEdits");
    card.querySelector(".plan-run").textContent = "已执行";
  });
  messagesEl.appendChild(card);
  scrollDown();
  // P2a: 异步拉取真实文件, 渲染行级 diff 预览 (纯前端, 复用 /api/fs/read)
  items.forEach(async (it, idx) => {
    const path = extractPathFromText(notes.join("\n") + "\n" + it.lang + "\n" + it.code);
    const box = card.querySelector('.pd-diffbox[data-i="' + idx + '"]');
    if (!box) return;
    if (!path) { box.innerHTML = '<div class="pd-hint">未识别文件路径，仅展示计划代码；可点「📝 应用到文件」手动指定。</div>'; return; }
    try {
      const res = await fetch("/api/fs/read?path=" + encodeURIComponent(path));
      const j = await res.json();
      if (j && j.content !== undefined && !j.binary) {
        box.innerHTML = '<div class="pd-diffhd">📊 真实文件对比（当前 → 计划）</div>' + renderDiffView(j.content, it.code);
      } else if (j && j.binary) {
        box.innerHTML = '<div class="pd-hint">二进制文件，无法预览 diff，将直接写入。</div>';
      } else {
        box.innerHTML = '<div class="pd-hint">⚠️ 文件不存在，批准后将是新建文件。</div>';
      }
    } catch (e) {
      box.innerHTML = '<div class="pd-hint">读取失败: ' + esc(String(e)) + '</div>';
    }
  });
}

// ---------- 计划卡: 真实文件 diff 预览 (P2a) ----------
function extractPathFromText(t) {
  const m = t.match(/([`"']?)([\w./\\-]+\.(?:py|js|ts|tsx|jsx|html?|css|scss|json|toml|md|yaml|yml|txt|sh|bat|ps1|go|rs|c|cpp|h|java))([`"']?)/);
  if (m) return m[2];
  const m2 = t.match(/(?:文件|file|路径|path)\s*[:：=]?\s*([`"']?)([\w./\\-]+\.[\w]+)/i);
  if (m2) return m2[2];
  return null;
}
function computeLineDiff(oldT, newT) {
  const a = (oldT || "").split("\n"), b = (newT || "").split("\n");
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = []; let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ t: "ctx", s: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: "del", s: a[i] }); i++; }
    else { out.push({ t: "add", s: b[j] }); j++; }
  }
  while (i < n) out.push({ t: "del", s: a[i++] });
  while (j < m) out.push({ t: "add", s: b[j++] });
  return out;
}
function renderDiffView(oldT, newT) {
  const d = computeLineDiff(oldT, newT);
  return '<div class="diff-view">' + d.map((x) => {
    const cls = x.t === "add" ? "diff-add" : x.t === "del" ? "diff-del" : "diff-ctx";
    const sig = x.t === "add" ? "+" : x.t === "del" ? "-" : " ";
    return `<div class="${cls}"><span class="dl-sig">${sig}</span><span class="dl-tx">${esc(x.s)}</span></div>`;
  }).join("") + "</div>";
}

async function executeMode(text, mode) {
  const agentBubble = addMessage("agent");
  agentBubble.classList.add("typing");
  agentBubble.textContent = "";
  setAgentStatus("think");
  const chainStrip = document.createElement("div");
  chainStrip.className = "chain-strip";
  const toolsBox = document.createElement("div");
  toolsBox.className = "tools";
  const narr = document.createTextNode("");
  agentBubble.appendChild(chainStrip);
  agentBubble.appendChild(narr);
  agentBubble.appendChild(toolsBox);
  let acc = "";
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: currentSessionId, history: history.slice(), mode: mode, ...getEnhanceSel() }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = frame.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const evt = JSON.parse(line.slice(6));
        handleEvent(evt, narr, toolsBox, chainStrip, (s) => { acc += s; });
        if (evt.type === "done" && evt.session_id) { currentSessionId = evt.session_id; const _at = activeTab(); if (_at && !_at.sessionId) { _at.sessionId = evt.session_id; refreshTabTitles(); } }
        if (evt.type === "error") { setAgentStatus("error", evt.message); toast("后端执行异常: " + (evt.message || ""), "error"); }
      }
    }
    // 非 plan 模式 (plan 走 showPlanCard 流程): 把累积文本升级为 markdown + 代码块卡片
    if (mode !== "plan") upgradeBubbleToMarkdown(narr);
  } catch (e) {
    agentBubble.classList.remove("typing");
    agentBubble.textContent = "执行失败: 服务可能已中断 (" + e + ")";
    toast("执行失败, 请检查服务", "error");
  } finally {
    agentBubble.classList.remove("typing");
    history.push({ role: "assistant", content: acc });
    scrollDown();
  }
}


// ---------- Tab 切换 ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`panel-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "results") loadResults();
    if (btn.dataset.tab === "files") loadFiles(".");
    if (btn.dataset.tab === "sessions") loadSessions();
    if (btn.dataset.tab === "reviews") loadReviews();
    if (btn.dataset.tab === "mcp") loadMcpPanel();
    if (btn.dataset.tab === "deliver") loadDeliverCenter();
    if (btn.dataset.tab === "terminal") loadTerminal();
  });
});

// ---------- 交互式终端 (SSE 流式) ----------
const termOutputEl = $("term-output");
const termInputEl = $("term-input");
const termClearBtn = $("term-clear");
function appendTerm(text, cls){
  const div = document.createElement("div");
  div.className = "term-line " + (cls || "");
  div.textContent = text;
  termOutputEl.appendChild(div);
  termOutputEl.scrollTop = termOutputEl.scrollHeight;
}
async function runTerminal(cmd){
  if (!cmd.trim()) return;
  appendTerm("$ " + cmd, "term-cmd");
  try {
    const r = await fetch("/api/terminal", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ command: cmd, timeout: 120 }) });
    if (!r.body) { appendTerm("终端不可用 (无流式支持)", "term-err"); return; }
    const reader = r.body.getReader();
    const dec = new TextDecoder("utf-8");
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const line = chunk.replace(/^data:\s?/, "");
        try {
          const obj = JSON.parse(line);
          if (obj.type === "output") appendTerm(obj.text, "term-out");
          else if (obj.type === "exit") appendTerm(obj.timeout ? "超时退出 (code=-1)" : "退出码: " + obj.code, "term-exit");
          else if (obj.type === "error") appendTerm("错误: " + obj.text, "term-err");
        } catch (e) { if (line.trim()) appendTerm(line, "term-out"); }
      }
    }
    if (buf.trim()) {
      const line = buf.trim().replace(/^data:\s?/, "");
      try { const obj = JSON.parse(line); if (obj.type === "output") appendTerm(obj.text, "term-out"); } catch (e) {}
    }
  } catch (e) { appendTerm("终端错误: " + e, "term-err"); }
}
function loadTerminal(){ if (termInputEl) termInputEl.focus(); }
if (termInputEl) termInputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { const v = termInputEl.value; termInputEl.value = ""; runTerminal(v); }
});
if (termClearBtn) termClearBtn.addEventListener("click", () => { if (termOutputEl) termOutputEl.innerHTML = ""; });

// ---------- 代码评审趋势图 ----------
function drawReviewTrend(reviews, cvEl){
  const cv = cvEl || $("review-trend");
  if (!cv) return;
  const ctx = cv.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth || 600, H = 120;
  cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const pts = (reviews || []).filter(r => r.score != null).map((r, i) => ({ x: i + 1, y: r.score, v: r.verdict }));
  if (pts.length < 2) { ctx.fillStyle = "#8a93a6"; ctx.font = "12px monospace"; ctx.fillText("评审样本不足 2 条, 暂无趋势 (多跑几次 review_code 即可)", 12, H / 2); return; }
  const padL = 30, padB = 20, padT = 10;
  ctx.strokeStyle = "#2a3142"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(padL, padT); ctx.lineTo(padL, H - padB); ctx.lineTo(W - 10, H - padB); ctx.stroke();
  ctx.strokeStyle = "rgba(255,200,0,.35)"; ctx.setLineDash([4, 4]);
  const y75 = H - padB - (75 * (H - padT - padB) / 100);
  ctx.beginPath(); ctx.moveTo(padL, y75); ctx.lineTo(W - 10, y75); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = "rgba(255,200,0,.7)"; ctx.font = "10px monospace"; ctx.fillText("75", 8, y75 + 3);
  const n = pts.length;
  const stepX = (W - 10 - padL) / (n - 1);
  ctx.strokeStyle = "#4fd1ff"; ctx.lineWidth = 2; ctx.beginPath();
  pts.forEach((p, i) => { const X = padL + i * stepX; const Y = H - padB - (p.y * (H - padT - padB) / 100); if (i === 0) ctx.moveTo(X, Y); else ctx.lineTo(X, Y); });
  ctx.stroke();
  pts.forEach((p, i) => { const X = padL + i * stepX; const Y = H - padB - (p.y * (H - padT - padB) / 100); ctx.fillStyle = p.v === "approve" ? "#3ddc84" : "#ff5c7c"; ctx.beginPath(); ctx.arc(X, Y, 3.5, 0, Math.PI * 2); ctx.fill(); });
  ctx.fillStyle = "#8a93a6"; ctx.font = "10px monospace";
  pts.forEach((p, i) => { if (i % Math.ceil(n / 6) === 0 || i === n - 1) ctx.fillText("#" + p.x, padL + i * stepX - 6, H - 6); });
}

// ---------- 外部工具 (MCP) 交互式管理面板 ----------
const mcpListEl = $("mcp-list");
const mcpCountEl = $("mcp-count");
const mcpRefreshBtn = $("mcp-refresh");
if (mcpRefreshBtn) mcpRefreshBtn.addEventListener("click", loadMcpPanel);

async function loadMcpPanel() {
  if (!mcpListEl) return;
  try {
    const r = await fetch("/api/mcp");
    const d = await r.json();
    mcpListEl.innerHTML = "";
    if (!d.enabled) {
      mcpListEl.innerHTML = '<div class="empty">MCP 已禁用 (config mcp.enabled=false)</div>';
      return;
    }
    const servers = d.servers || [];
    if (!servers.length) {
      mcpListEl.innerHTML = '<div class="empty">未配置任何 MCP 服务器 (config.toml [[mcp.servers]])</div>';
      return;
    }
    mcpCountEl.textContent = servers.length;
    servers.forEach((s) => {
      const card = document.createElement("div");
      card.className = "mcp-card";
      const tools = s.tools || [];
      card.innerHTML = `
        <div class="mcp-card-head"><span class="mcp-srv">${esc(s.name)}</span><span class="mcp-toolcount">${tools.length} 个工具</span></div>
        <div class="mcp-tools"></div>`;
      const toolsBox = card.querySelector(".mcp-tools");
      if (!tools.length) {
        toolsBox.innerHTML = '<div class="empty" style="padding:8px 0;">该服务未暴露工具</div>';
      }
      tools.forEach((t) => toolsBox.appendChild(mcpToolEl(s.name, t)));
      mcpListEl.appendChild(card);
    });
  } catch (e) {
    mcpListEl.innerHTML = '<div class="empty">MCP 加载失败: ' + esc(e) + "</div>";
  }
}

function mcpToolEl(server, tool) {
  const det = document.createElement("details");
  det.className = "mcp-tool";
  const props = (tool.inputSchema && tool.inputSchema.properties) || {};
  const reqs = (tool.inputSchema && tool.inputSchema.required) || [];
  const rows = Object.entries(props).map(([k, v]) => {
    const req = reqs.includes(k);
    const ph = (v.description || "") + (req ? " (必填)" : "");
    const isNum = (v.type === "integer" || v.type === "number");
    return `<label class="mcp-arg"><span class="mcp-arg-name">${esc(k)}<i>${isNum ? " 数字" : ""}${req ? " ·必填" : ""}</i></span>
      <input class="mcp-input" data-arg="${esc(k)}" type="${isNum ? "number" : "text"}" placeholder="${esc(ph)}" /></label>`;
  }).join("");
  det.innerHTML = `
    <summary class="mcp-tool-head">⚙ ${esc(tool.name)}<span class="mcp-tool-desc">${esc(tool.description || "")}</span></summary>
    <div class="mcp-tool-body">
      ${rows || '<div class="empty" style="padding:6px 0;">无参数</div>'}
      <button class="btn-primary mcp-run">调用</button>
      <pre class="mcp-out"></pre>
    </div>`;
  const runBtn = det.querySelector(".mcp-run");
  const outEl = det.querySelector(".mcp-out");
  runBtn.addEventListener("click", async () => {
    const args = {};
    det.querySelectorAll(".mcp-input").forEach((inp) => {
      const k = inp.dataset.arg;
      let val = inp.value;
      if (inp.type === "number") {
        if (val === "") return;
        val = Number(val);
      } else if (val === "") {
        return;
      }
      args[k] = val;
    });
    runBtn.disabled = true;
    outEl.textContent = "调用中…";
    outEl.className = "mcp-out";
    try {
      const resp = await fetch("/api/mcp/call", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server, tool: tool.name, arguments: args }),
      });
      const d = await resp.json();
      if (d.error) {
        outEl.textContent = "错误: " + d.error;
        outEl.classList.add("bad");
      } else {
        outEl.textContent = d.output;
        outEl.classList.add(d.isError ? "bad" : "ok");
      }
    } catch (e) {
      outEl.textContent = "调用失败: " + e;
      outEl.classList.add("bad");
    } finally {
      runBtn.disabled = false;
    }
  });
  return det;
}

// ---------- 绑定 ----------
sendBtn.addEventListener("click", send);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
reviewBackBtn.addEventListener("click", () => {
  reviewDetailEl.style.display = "none";
  reviewListEl.style.display = "block";
  loadReviews();
});
const btnNew = $("btn-new");
if (btnNew) btnNew.addEventListener("click", () => { createTab(); });

$("btn-clear").addEventListener("click", () => {
  messagesEl.innerHTML = "";
  history = [];
  const at = activeTab(); if (at) at.cache = "";
});

loadHealth();
loadProviders();
loadTools();
loadMcp();
refreshDashboard();
refreshSandboxChip();
setInterval(refreshDashboard, 5000);   // 仪表盘每 5s 刷新

// 工作区沙箱状态芯片
function refreshSandboxChip() {
  const el = $("sandbox-chip");
  if (!el) return;
  fetch("/api/sandbox")
    .then(r => r.json())
    .then(d => {
      const active = d.active;
      const n = (d.roots || []).length;
      el.className = "sandbox-chip " + (active ? "ok" : "off");
      el.textContent = active ? ("🛡️ 沙箱 " + n + " 根") : "🛡️ 沙箱:关";
      el.title = d.note || "工作区沙箱状态";
    })
    .catch(() => {
      el.className = "sandbox-chip";
      el.textContent = "🛡️ 沙箱 …";
    });
}

// ---------- 代码评审自评估可视化 (WEB 专属) ----------
function parseCodeReview(text) {
  if (!text || !text.includes("[code-review]")) return null;
  const m = (re) => (text.match(re) || [])[1];
  const verdict = (m(/VERDICT:\s*(\w+)/) || "").toLowerCase();
  const score = m(/SCORE:\s*(\d{1,3})/);
  const summary = m(/SUMMARY:\s*(.*)/);
  const source = m(/评审来源:\s*(.*)/);
  const issues = [];
  let inIssues = false;
  text.split("\n").forEach((line) => {
    const st = line.trim();
    if (st.startsWith("ISSUES:")) { inIssues = true; return; }
    if (inIssues) {
      if (st.startsWith("SCORE:") || st.startsWith("SUMMARY:") || st.startsWith("VERDICT:") || st.startsWith("评审来源:")) { inIssues = false; return; }
      const mm = line.match(/-\s*\[([高中低])\]\s*(.*)/);
      if (mm) issues.push({ sev: mm[1], desc: mm[2].trim() });
    }
  });
  return { verdict, score: score ? parseInt(score, 10) : null, issues, summary: summary || "", source: source ? source.trim().replace(/[()]/g, "") : "" };
}

function scoreColor(score) {
  if (score == null) return "var(--muted)";
  if (score >= 90) return "var(--ok)";
  if (score >= 75) return "#d9c024";
  return "var(--bad)";
}

function renderReviewInline(parsed) {
  const v = parsed.verdict === "approve" ? "✅ 通过" : (parsed.verdict === "revise" ? "🔧 需修改" : parsed.verdict);
  const vclass = parsed.verdict === "approve" ? "rv-approve" : (parsed.verdict === "revise" ? "rv-revise" : "");
  const sc = parsed.score == null ? "—" : parsed.score;
  const issuesHtml = (parsed.issues || []).map((i) => `<li class="rv-issue sev-${i.sev}"><b>[${i.sev}]</b> ${esc(i.desc)}</li>`).join("");
  return `<div class="rv-inline ${vclass}">
    <div class="rv-i-head"><span class="rv-badge ${vclass}">${v}</span><span class="rv-score">评分 ${sc}</span><span class="rv-src">${esc(parsed.source)}</span></div>
    <div class="rv-bar"><div class="rv-fill" style="width:${parsed.score == null ? 0 : parsed.score}%;background:${scoreColor(parsed.score)}"></div></div>
    ${issuesHtml ? `<ul class="rv-issues">${issuesHtml}</ul>` : '<div class="rv-none">无问题</div>'}
    <div class="rv-sum">${esc(parsed.summary)}</div>
  </div>`;
}

function renderReviewStats(reviews) {
  const el = $("review-stats");
  if (!el) return;
  const rc = $("review-count");
  if (rc) rc.textContent = (reviews || []).length;
  if (!reviews || !reviews.length) {
    el.style.display = "none";
    return;
  }
  let approve = 0, revise = 0, sum = 0, scored = 0;
  reviews.forEach((rv) => {
    if (rv.verdict === "approve") approve++;
    else if (rv.verdict === "revise") revise++;
    if (rv.score != null) { sum += rv.score; scored++; }
  });
  const avg = scored ? Math.round(sum / scored) : null;
  el.style.display = "flex";
  el.innerHTML = `
    <div class="rs-item"><span class="rs-num">${reviews.length}</span><span class="rs-label">累计评审</span></div>
    <div class="rs-item ok"><span class="rs-num">${approve}</span><span class="rs-label">通过</span></div>
    <div class="rs-item bad"><span class="rs-num">${revise}</span><span class="rs-label">需修改</span></div>
    <div class="rs-item"><span class="rs-num">${avg == null ? "—" : avg}</span><span class="rs-label">平均质量分</span></div>
    <div class="rs-bar"><div class="rs-fill" style="width:${avg == null ? 0 : avg}%;background:${scoreColor(avg)}"></div></div>`;
}

async function loadReviews() {
  if (!reviewListEl) return;
  try {
    const r = await fetch("/api/reviews");
    const d = await r.json();
    const reviews = d.reviews || [];
    reviewListEl.innerHTML = "";
    renderReviewStats(reviews);
    drawReviewTrend(reviews);
    if (!reviews.length) {
      reviewListEl.innerHTML = '<div class="empty">暂无评审报告 (对话中调用 review_code 后自动归档)</div>';
      return;
    }
    reviews.forEach((rv) => {
      const card = document.createElement("div");
      card.className = "rc-card";
      const ts = rv.ts ? new Date(rv.ts * 1000).toLocaleString() : "";
      const vclass = rv.verdict === "approve" ? "rv-approve" : (rv.verdict === "revise" ? "rv-revise" : "");
      const v = rv.verdict === "approve" ? "通过" : (rv.verdict === "revise" ? "需修改" : rv.verdict);
      const issueCount = (rv.issues || []).length;
      card.innerHTML = `
        <div class="rc-top"><span class="rc-id">#${rv.id}</span><span class="rv-badge ${vclass}">${v}</span><span class="rc-status">${rv.score == null ? "—" : rv.score}分</span></div>
        <div class="rc-prompt">${esc(rv.target || "(片段/未记录路径)")}</div>
        <div class="rc-meta">${ts} · 问题 ${issueCount} · ${esc(rv.source || "")}</div>`;
      card.addEventListener("click", () => showReview(rv.id));
      reviewListEl.appendChild(card);
    });
  } catch (e) {
    reviewListEl.innerHTML = '<div class="empty">评审加载失败</div>';
  }
}

async function showReview(id) {
  try {
    const r = await fetch("/api/reviews");
    const d = await r.json();
    const rv = (d.reviews || []).find((x) => x.id === id);
    if (!rv) { alert("未找到"); return; }
    const vclass = rv.verdict === "approve" ? "rv-approve" : (rv.verdict === "revise" ? "rv-revise" : "");
    const v = rv.verdict === "approve" ? "✅ 通过" : (rv.verdict === "revise" ? "🔧 需修改" : rv.verdict);
    reviewBodyEl.innerHTML = `
      <div class="rv-detail-head">
        <span class="rv-badge ${vclass}">${v}</span>
        <span class="rv-target">${esc(rv.target || "(片段)")}</span>
        <button class="btn-primary rv-rerun" data-target="${esc(rv.target || "")}">复评</button>
      </div>
      <div class="rv-bar"><div class="rv-fill" style="width:${rv.score == null ? 0 : rv.score}%;background:${scoreColor(rv.score)}"></div></div>
      <div class="rv-score-line">质量评分: <b>${rv.score == null ? "—" : rv.score}</b> / 100</div>
      ${(rv.issues && rv.issues.length) ? `<ul class="rv-issues">${rv.issues.map((i) => `<li class="rv-issue sev-${i.sev}"><b>[${i.sev}]</b> ${esc(i.desc)}</li>`).join("")}</ul>` : '<div class="rv-none">无问题</div>'}
      <div class="rv-sum"><b>摘要:</b> ${esc(rv.summary || "")}</div>
      <div class="rv-src-line">评审来源: ${esc(rv.source || "")}</div>`;
    reviewDetailEl.style.display = "block";
    reviewListEl.style.display = "none";
    const rerunBtn = reviewBodyEl.querySelector(".rv-rerun");
    if (rerunBtn) rerunBtn.addEventListener("click", () => {
      const t = rerunBtn.dataset.target;
      $("input").value = t ? `请对目标 ${t} 重新执行 review_code 评审（critic=true），并给出可执行的改进建议` : "请对最近一次修改的代码重新执行 review_code 评审（critic=true）";
      document.querySelector('.tab[data-tab="chat"]').click();
      send();
    });
  } catch (e) {
    alert("加载失败: " + e);
  }
}

// ---------- 交付中心 (项目级交付闭环) ----------
let dcCurrent = null;
async function loadDeliverCenter() {
  const el = $("panel-deliver");
  if (!el) return;
  await dcRefreshHealth();
  if (dcCurrent) renderCenterFiles(dcCurrent);
}
async function dcRefreshHealth() {
  try {
    const r = await fetch("/api/reviews");
    const d = await r.json();
    drawReviewTrend(d.reviews || [], $("dc-trend"));
  } catch (e) { /* 健康趋势非关键 */ }
}
async function runCenterScan() {
  const btn = $("dc-scan"); if (!btn) return;
  btn.disabled = true;
  toast("扫描 git 改动并评审中…", "info");
  try {
    const r = await fetch("/api/deliver/center", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deliver: false }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { toast("扫描失败: " + (d.error || r.status), "error"); return; }
    dcCurrent = d;
    renderCenterFiles(d);
    dcShowSummary(d, false);
    if (d.total === 0) toast(d.message || "无 .py 改动", "info");
    else toast("扫描完成: %d 文件 · 通过 %d / 需改 %d".replace("%d", d.total).replace("%d", d.approve).replace("%d", d.revise), d.revise === 0 ? "ok" : "warn");
  } catch (e) { toast("扫描异常: " + e, "error"); }
  finally { btn.disabled = false; }
}
async function runCenterDeliver() {
  const btn = $("dc-deliver"); if (!btn) return;
  btn.disabled = true;
  toast("整包交付自检中: 跑全量测试 + 聚合评审…", "info");
  try {
    const r = await fetch("/api/deliver/center", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deliver: true }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { toast("交付自检失败: " + (d.error || r.status), "error"); return; }
    dcCurrent = d;
    renderCenterFiles(d);
    dcShowSummary(d, true);
    await dcRefreshHealth();
    const dv = d.delivery;
    if (dv) toast(dv.ready ? "整包可交付 ✅" : "暂不可交付 ⛔", dv.ready ? "ok" : "warn", 6000);
  } catch (e) { toast("交付异常: " + e, "error"); }
  finally { btn.disabled = false; }
}
async function runCenterPr() {
  toast("生成 PR 草稿中…", "info");
  try {
    const r = await fetch("/api/pr/draft", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: "", note: "" }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast("PR 草稿失败: " + (e.error || r.status), "error"); return; }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "pr_draft_" + Date.now() + ".md";
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    toast("PR 草稿已导出 📝", "ok");
  } catch (e) { toast("PR 异常: " + e, "error"); }
}
function dcShowSummary(d, withDelivery) {
  const el = $("dc-summary");
  if (!el) return;
  let html = '<div class="dc-stat"><b>%d</b> 文件</div><div class="dc-stat ok">%d 通过</div><div class="dc-stat bad">%d 需改</div>'
    .replace("%d", d.total || 0).replace("%d", d.approve || 0).replace("%d", d.revise || 0);
  if (withDelivery && d.delivery) {
    const dv = d.delivery;
    const rc = dv.test && dv.test.rc;
    html += '<div class="dc-stat %s">测试 %s</div>'.replace("%s", dv.ready ? "ok" : "bad")
      .replace("%s", rc === 0 ? "通过(rc=0)" : (rc != null ? "失败(rc=" + rc + ")" : "未跑"));
    html += '<div class="dc-stat %s">整包 %s</div>'.replace("%s", dv.ready ? "ok" : "bad")
      .replace("%s", dv.ready ? "可交付 ✅" : "不可交付 ⛔");
  }
  el.innerHTML = html;
  el.style.display = "flex";
}
function renderCenterFiles(d) {
  const el = $("dc-files");
  if (!el) return;
  el.innerHTML = "";
  const files = d.files || [];
  if (!files.length) { el.innerHTML = '<div class="empty">' + esc(d.message || "无 .py 改动可评审") + '</div>'; return; }
  files.forEach((f) => {
    const card = document.createElement("div");
    card.className = "rc-card dc-file";
    const vclass = f.verdict === "approve" ? "rv-approve" : (f.verdict === "revise" ? "rv-revise" : "");
    const v = f.verdict === "approve" ? "✅ 通过" : (f.verdict === "revise" ? "🔧 需修改" : "?");
    const code = f.code ? '<span class="dc-code">' + esc(f.code) + "</span>" : "";
    const iss = (f.issues || []).map((i) => "[" + esc(i.sev) + "] " + esc(i.desc)).join("; ");
    card.innerHTML =
      '<div class="rc-top"><span class="rc-id">' + code + '</span><span class="rv-badge ' + vclass + '">' + v + '</span>' +
      '<span class="rc-status">' + (f.score == null ? "—" : f.score) + "分</span></div>" +
      '<div class="rc-prompt">' + esc(f.path) + "</div>" +
      '<div class="rc-meta">' + esc(f.summary || "") + (iss ? (" · " + iss) : "") + "</div>" +
      '<div class="dc-actions"><button class="btn-ghost dc-open">打开</button><button class="btn-ghost dc-self">交付自检</button></div>';
    card.querySelector(".dc-open").addEventListener("click", () => openInEditor(f.path));
    card.querySelector(".dc-self").addEventListener("click", () => centerDeliverFile(f.path));
    el.appendChild(card);
  });
}
async function centerDeliverFile(path) {
  toast("交付自检: " + path, "info");
  try {
    const r = await fetch("/api/deliver", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { toast("失败: " + (d.error || r.status), "error"); return; }
    toast((d.delivery_ready ? "可交付 ✅ " : "不可交付 ⛔ ") + path, d.delivery_ready ? "ok" : "warn", 6000);
    await dcRefreshHealth();
  } catch (e) { toast("异常: " + e, "error"); }
}
async function openInEditor(path) {
  const tab = document.querySelector('.tab[data-tab="files"]');
  if (tab) tab.click();
  await showFileContent(path);
}

// ---------- 结果回看 (分页加载) ----------
let resultOffset = 0;
const RESULT_PAGE = 30;
async function loadResults(reset = true) {
  if (reset) resultOffset = 0;
  try {
    const r = await fetch(`/api/results?limit=${RESULT_PAGE}&offset=${resultOffset}`);
    const d = await r.json();
    if (reset) resultListEl.innerHTML = "";
    resultDetailEl.style.display = "none";
    (d.results || []).forEach((it) => {
      const card = document.createElement("div");
      card.className = "rc-card";
      const ts = it.created_at ? new Date(it.created_at * 1000).toLocaleString() : "";
      card.innerHTML = `
        <div class="rc-top"><span class="rc-id">#${esc(it.id)}</span><span class="rc-prov">${esc(it.provider || "?")}</span><span class="rc-status">${esc(it.status)}</span></div>
        <div class="rc-prompt">${esc(it.prompt)}</div>
        <div class="rc-meta">${ts} · Token ${it.est_tokens || 0} · ¥${(it.est_cost_cny || 0).toFixed(5)}</div>`;
      card.addEventListener("click", () => showResult(it.id));
      resultListEl.appendChild(card);
    });
    const total = d.total || 0;
    if (reset && !(d.results || []).length) {
      resultListEl.innerHTML = '<div class="empty">暂无已落盘结果 (运行编码任务后自动保存)</div>';
      return;
    }
    resultOffset += (d.results || []).length;
    if (resultOffset < total) {
      let more = $("result-more");
      if (!more) {
        more = document.createElement("div");
        more.id = "result-more";
        more.className = "load-more";
        more.textContent = `加载更多 (${resultOffset}/${total})`;
        more.addEventListener("click", () => loadResults(false));
        resultListEl.parentElement.appendChild(more);
      } else {
        more.textContent = `加载更多 (${resultOffset}/${total})`;
      }
    } else {
      const ex = $("result-more");
      if (ex) ex.remove();
    }
  } catch (e) {
    if (reset) resultListEl.innerHTML = '<div class="empty">结果加载失败</div>';
    toast("结果加载失败", "error");
  }
}

async function showResult(id) {
  try {
    const r = await fetch(`/api/results/${id}`);
    const d = await r.json();
    if (d.error) { alert("未找到"); return; }
    resultMdEl.textContent = d.markdown || d.final_text || "(无内容)";
    resultDetailEl.style.display = "block";
    resultListEl.style.display = "none";
  } catch (e) {
    alert("加载失败: " + e);
  }
}

resultBackBtn.addEventListener("click", () => {
  resultDetailEl.style.display = "none";
  resultListEl.style.display = "block";
  loadResults();
});

// ---------- 文件树浏览器 ----------
async function loadFiles(dir) {
  currentFileDir = dir;
  filePathEl.textContent = dir;
  fileContentEl.style.display = "none";
  try {
    const r = await fetch("/api/fs/list?path=" + encodeURIComponent(dir));
    const d = await r.json();
    if (d.error) { fileTreeEl.innerHTML = `<div class="empty">${esc(d.error)}</div>`; return; }
    fileTreeEl.innerHTML = "";
    (d.entries || []).forEach((e) => {
      const row = document.createElement("div");
      row.className = "file-row " + e.kind;
      const icon = e.kind === "dir" ? "📁" : "📄";
      const sz = e.kind === "file" && e.size ? ` (${e.size}B)` : "";
      row.innerHTML = `<span class="f-icon">${icon}</span><span class="f-name">${esc(e.name)}</span><span class="f-size">${sz}</span>`;
      row.addEventListener("click", () => {
        if (e.kind === "dir") loadFiles((dir === "." ? "" : dir + "/") + e.name);
        else showFileContent((dir === "." ? "" : dir + "/") + e.name);
      });
      fileTreeEl.appendChild(row);
    });
  } catch (e) {
    fileTreeEl.innerHTML = '<div class="empty">加载失败</div>';
  }
}

// ====== 编辑器 IDE 增强: 多标签 / 查找替换 / 转到行 / 状态栏 / 大纲 / 全局搜索 ======
const editorTabsEl = $("editor-tabs");
const editorFindMarksEl = $("editor-find-marks");
const findbarEl = $("editor-findbar");
const findInputEl = $("find-input");
const findCountEl = $("find-count");
const findPrevBtn = $("find-prev");
const findNextBtn = $("find-next");
const findRegexBtn = $("find-regex");
const findIcaseBtn = $("find-icase");
const findReplaceToggle = $("find-replace-toggle");
const replaceInputEl = $("replace-input");
const replaceOneBtn = $("replace-one");
const replaceAllBtn = $("replace-all");
const findCloseBtn = $("find-close");
const wrapToggleBtn = $("editor-wrap-toggle");
const fontDecBtn = $("editor-font-dec");
const fontIncBtn = $("editor-font-inc");
const outlineBtn = $("editor-outline-btn");
const outlineEl = $("editor-outline");
const gotoBtn = $("editor-goto-btn");
const gotoInputEl = $("editor-goto-input");
const findBtn = $("editor-find-btn");
const findFilesBtn = $("editor-find-files");
const statusLang = $("estat-lang");
const statusPos = $("estat-pos");
const statusSel = $("estat-sel");
const statusEol = $("estat-eol");
const statusEnc = $("estat-enc");
const statusWrap = $("estat-wrap");
const statusTabs = $("estat-tabs");
const fifModal = $("fif-modal");
const fifInput = $("fif-input");
const fifExt = $("fif-ext");
const fifRun = $("fif-run");
const fifRegexBtn = $("fif-regex");
const fifCloseBtn = $("fif-close");
const fifResults = $("fif-results");

let editorTabs = [];
let activeEditorTab = null;
let editorFind = { term: "", matches: [], idx: -1, regex: false, icase: true };
let editorFontSize = 13;
let editorWrapOn = false;

const LANG_EXT = { py: "python", pyw: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript", json: "json", html: "html", htm: "html", css: "css", scss: "scss", md: "markdown", markdown: "markdown", go: "go", rs: "rust", java: "java", c: "c", h: "c", cpp: "cpp", cc: "cpp", hpp: "cpp", sh: "shell", bash: "shell", yml: "yaml", yaml: "yaml", toml: "toml", sql: "sql", xml: "xml" };
const LANG_LABEL = { python: "Python", javascript: "JavaScript", typescript: "TypeScript", json: "JSON", html: "HTML", css: "CSS", scss: "SCSS", markdown: "Markdown", go: "Go", rust: "Rust", java: "Java", c: "C", cpp: "C++", shell: "Shell", yaml: "YAML", toml: "TOML", sql: "SQL", xml: "XML", text: "文本" };
function langOf(path) { const ext = (path.split(".").pop() || "").toLowerCase(); return LANG_EXT[ext] || "text"; }
function langLabel(k) { return LANG_LABEL[k] || "文本"; }
function activateFilesTab() { const t = document.querySelector('.tab[data-tab="files"]'); if (t && !t.classList.contains("active")) t.click(); }

async function openFileTab(rel, opts) {
  opts = opts || {};
  if (opts.activate !== false) activateFilesTab();
  const existing = editorTabs.find((t) => t.path === rel);
  if (existing && !opts.reload) { activateEditorTab(existing, opts.line); return; }
  try {
    const r = await fetch("/api/fs/read?path=" + encodeURIComponent(rel));
    const d = await r.json();
    if (d.error) { toast(d.error, "error"); return; }
    if (d.binary) { fileContentEl.textContent = "🔒 " + (d.hint || "二进制文件, 不可预览"); fileContentEl.style.display = "block"; editorWrapEl.style.display = "none"; toast("二进制文件不支持编辑", "warn"); return; }
    const lang = langOf(rel);
    let tab;
    if (existing) { tab = existing; tab.content = d.content || ""; tab.savedContent = d.content || ""; tab.dirty = false; tab.lang = lang; }
    else { tab = { path: rel, name: rel.split("/").pop(), content: d.content || "", savedContent: d.content || "", dirty: false, lang: lang, scrollTop: 0, selStart: 0, selEnd: 0 }; editorTabs.push(tab); }
    activateEditorTab(tab, opts.line);
  } catch (e) { toast("读取失败: " + e, "error"); }
}

function activateEditorTab(tab, line) {
  if (activeEditorTab) { activeEditorTab.content = editorTextEl.value; activeEditorTab.scrollTop = editorTextEl.scrollTop; activeEditorTab.selStart = editorTextEl.selectionStart; activeEditorTab.selEnd = editorTextEl.selectionEnd; }
  activeEditorTab = tab;
  currentEditPath = tab.path;
  editorFileEl.textContent = tab.path + (tab.dirty ? " ●" : "");
  editorTextEl.value = tab.content;
  editorWrapEl.style.display = "block";
  fileContentEl.style.display = "none";
  editorTabsEl.hidden = false;
  renderEditorTabs();
  applyWrap(); applyFont();
  syncGutter(); renderEditorLayers(); updateStatus();
  if (line && line > 0) gotoLine(line);
  editorTextEl.focus();
}

function renderEditorTabs() {
  editorTabsEl.innerHTML = "";
  editorTabsEl.hidden = editorTabs.length === 0;
  editorTabs.forEach((tab) => {
    const el = document.createElement("div");
    el.className = "editor-tab" + (tab === activeEditorTab ? " active" : "");
    el.innerHTML = (tab.dirty ? '<span class="et-dirty" title="未保存"></span>' : "") + '<span class="et-name">' + esc(tab.name) + '</span><span class="et-close" title="关闭标签">✕</span>';
    el.addEventListener("click", (e) => { if (e.target.classList.contains("et-close")) closeEditorTab(tab); else activateEditorTab(tab); });
    editorTabsEl.appendChild(el);
  });
}

function closeEditorTab(tab) {
  const i = editorTabs.indexOf(tab);
  if (i < 0) return;
  editorTabs.splice(i, 1);
  if (activeEditorTab === tab) {
    activeEditorTab = null; currentEditPath = null;
    const next = editorTabs[Math.max(0, i - 1)] || editorTabs[0] || null;
    if (next) activateEditorTab(next);
    else { editorWrapEl.style.display = "none"; editorTabsEl.hidden = true; editorTextEl.value = ""; clearFind(); outlineEl.hidden = true; }
  }
  renderEditorTabs();
}

function persistActive() {
  if (!activeEditorTab) return;
  activeEditorTab.content = editorTextEl.value;
  const was = activeEditorTab.dirty;
  activeEditorTab.dirty = activeEditorTab.content !== activeEditorTab.savedContent;
  if (was !== activeEditorTab.dirty) { editorFileEl.textContent = activeEditorTab.path + (activeEditorTab.dirty ? " ●" : ""); renderEditorTabs(); }
}

function renderEditorLayers() { highlightCode(); renderFindMarks(); }

function computeFindMatches(code) {
  if (!editorFind.term) return [];
  let re;
  try { re = editorFind.regex ? new RegExp(editorFind.term, editorFind.icase ? "gi" : "g") : new RegExp(escapeRegex(editorFind.term), editorFind.icase ? "gi" : "g"); }
  catch (e) { return []; }
  const ms = []; let m;
  while ((m = re.exec(code))) {
    if (m[0].length === 0) { re.lastIndex++; continue; }
    ms.push([m.index, m.index + m[0].length]);
    if (ms.length > 5000) break;
  }
  return ms;
}

function findMatchPos(code, i, open, close, step) {
  let depth = 0;
  for (let j = i + step; j >= 0 && j < code.length; j += step) {
    if (code[j] === open) depth++;
    else if (code[j] === close) { depth--; if (depth === 0) return j; }
  }
  return -1;
}
function computeBracketPair(code) {
  const pos = editorTextEl.selectionStart;
  const c0 = code[pos - 1], c1 = code[pos];
  const opens = { "(": ")", "[": "]", "{": "}" };
  const closes = { ")": "(", "]": "[", "}": "{" };
  let i = -1;
  if (c0 && (opens[c0] || closes[c0])) i = pos - 1;
  else if (c1 && (opens[c1] || closes[c1])) i = pos;
  else return null;
  const c = code[i];
  let oi, ci;
  if (opens[c]) { oi = i; ci = findMatchPos(code, i, c, opens[c], 1); }
  else { ci = i; oi = findMatchPos(code, i, closes[c], c, -1); }
  if (oi < 0 || ci < 0) return null;
  return [{ s: Math.min(oi, ci), e: Math.min(oi, ci) + 1 }, { s: Math.max(oi, ci), e: Math.max(oi, ci) + 1 }];
}

function renderFindMarks() {
  if (!editorFindMarksEl) return;
  const code = editorTextEl.value;
  const marks = [];
  if (editorFind.term) editorFind.matches.forEach((mm, i) => marks.push({ s: mm[0], e: mm[1], cls: i === editorFind.idx ? "t-find cur" : "t-find" }));
  const bp = computeBracketPair(code);
  if (bp) bp.forEach((b) => marks.push({ s: b.s, e: b.e, cls: "t-bracket" }));
  const ev = [];
  for (const mk of marks) { if (mk.e <= mk.s) continue; ev.push({ pos: mk.s, open: 1, cls: mk.cls }); ev.push({ pos: mk.e, open: 0, cls: mk.cls }); }
  ev.sort((a, b) => a.pos - b.pos || (a.open ? -1 : 1));
  let html = "", cursor = 0, depth = 0;
  for (const e of ev) {
    if (e.pos > cursor) { html += escapeHtml(code.slice(cursor, e.pos)); cursor = e.pos; }
    if (e.open) { html += '<span class="' + e.cls + '">'; depth++; }
    else { html += "</span>"; if (depth > 0) depth--; }
  }
  if (cursor < code.length) html += escapeHtml(code.slice(cursor));
  editorFindMarksEl.innerHTML = html + String.fromCharCode(10);
}

function updateStatus() {
  if (!activeEditorTab) return;
  const pos = editorTextEl.selectionStart;
  const before = editorTextEl.value.slice(0, pos);
  const line = before.split("\n").length;
  const col = pos - before.lastIndexOf("\n");
  const s = editorTextEl.selectionStart, e = editorTextEl.selectionEnd;
  const selLen = e - s;
  statusLang.textContent = langLabel(activeEditorTab.lang);
  statusEol.textContent = editorTextEl.value.indexOf("\r\n") >= 0 ? "CRLF" : "LF";
  statusPos.textContent = "Ln " + line + ", Col " + col;
  statusSel.textContent = "Sel " + (selLen > 0 ? selLen : 0);
  statusWrap.textContent = "换行: " + (editorWrapOn ? "开" : "关");
}

function applyWrap() {
  const ws = editorWrapOn ? "pre-wrap" : "pre";
  const wb = editorWrapOn ? "break-all" : "normal";
  [editorTextEl, editorHlEl, editorFindMarksEl].forEach((el) => { el.style.whiteSpace = ws; el.style.wordBreak = wb; });
  editorGutterEl.style.whiteSpace = ws;
  statusWrap.textContent = "换行: " + (editorWrapOn ? "开" : "关");
}
function applyFont() {
  const fs = editorFontSize + "px";
  const lh = (editorFontSize * 1.55) + "px";
  [editorTextEl, editorHlEl, editorFindMarksEl, editorGutterEl].forEach((el) => { el.style.fontSize = fs; el.style.lineHeight = lh; });
}
function syncScroll() {
  const st = editorTextEl.scrollTop, sl = editorTextEl.scrollLeft;
  editorGutterEl.scrollTop = st;
  editorHlEl.scrollTop = st; editorHlEl.scrollLeft = sl;
  editorFindMarksEl.scrollTop = st; editorFindMarksEl.scrollLeft = sl;
}
function toggleWrap() { editorWrapOn = !editorWrapOn; applyWrap(); }

function onEditorInput() {
  persistActive();
  if (editorFind.term) editorFind.matches = computeFindMatches(editorTextEl.value);
  syncGutter(); renderEditorLayers(); updateStatus();
}
function onEditorCursorMove() { updateStatus(); renderFindMarks(); }

// ---- 查找 / 替换 ----
function openFind(showReplace) {
  findbarEl.hidden = false;
  if (showReplace) { replaceInputEl.hidden = false; replaceOneBtn.hidden = false; replaceAllBtn.hidden = false; }
  editorFind.regex = findRegexBtn.classList.contains("active");
  editorFind.icase = findIcaseBtn.classList.contains("active");
  findInputEl.focus(); findInputEl.select();
  runFind();
}
function runFind() {
  editorFind.term = findInputEl.value;
  editorFind.regex = findRegexBtn.classList.contains("active");
  editorFind.icase = findIcaseBtn.classList.contains("active");
  editorFind.matches = computeFindMatches(editorTextEl.value);
  editorFind.idx = editorFind.matches.length ? 0 : -1;
  renderFindCount(); renderEditorLayers();
  if (editorFind.matches.length) selectMatch(editorFind.idx);
}
function renderFindCount() {
  if (!editorFind.term) { findCountEl.textContent = ""; return; }
  findCountEl.textContent = editorFind.matches.length ? (editorFind.idx + 1) + "/" + editorFind.matches.length : "无匹配";
}
function selectMatch(i) {
  const ms = editorFind.matches;
  if (!ms.length) return;
  i = ((i % ms.length) + ms.length) % ms.length;
  editorFind.idx = i;
  const mm = ms[i];
  editorTextEl.setSelectionRange(mm[0], mm[1]);
  editorTextEl.focus();
  const line = editorTextEl.value.slice(0, mm[0]).split("\n").length;
  const lineH = editorFontSize * 1.55;
  editorTextEl.scrollTop = Math.max(0, (line - 1) * lineH - editorTextEl.clientHeight / 2);
  syncScroll(); renderFindMarks(); renderFindCount();
}
function findNext() { if (editorFind.matches.length) selectMatch(editorFind.idx + 1); }
function findPrev() { if (editorFind.matches.length) selectMatch(editorFind.idx - 1); }
function clearFind() { editorFind.term = ""; editorFind.matches = []; editorFind.idx = -1; findInputEl.value = ""; findCountEl.textContent = ""; findbarEl.hidden = true; renderEditorLayers(); }
function replaceOne() {
  if (editorFind.idx < 0 || !editorFind.matches.length) return;
  const mm = editorFind.matches[editorFind.idx];
  const repl = replaceInputEl.value;
  const v = editorTextEl.value;
  editorTextEl.value = v.slice(0, mm[0]) + repl + v.slice(mm[1]);
  persistActive(); syncGutter(); renderEditorLayers();
  editorFind.matches = computeFindMatches(editorTextEl.value);
  if (editorFind.matches.length) { editorFind.idx = Math.min(editorFind.idx, editorFind.matches.length - 1); selectMatch(editorFind.idx); }
  else { editorFind.idx = -1; renderFindCount(); }
}
function replaceAll() {
  editorFind.term = findInputEl.value;
  editorFind.regex = findRegexBtn.classList.contains("active");
  editorFind.icase = findIcaseBtn.classList.contains("active");
  const v0 = editorTextEl.value;
  const ms = computeFindMatches(v0);
  if (!ms.length) { findCountEl.textContent = "无匹配"; return; }
  const repl = replaceInputEl.value;
  let v = v0;
  for (let k = ms.length - 1; k >= 0; k--) { const mm = ms[k]; v = v.slice(0, mm[0]) + repl + v.slice(mm[1]); }
  editorTextEl.value = v;
  persistActive(); syncGutter(); renderEditorLayers();
  editorFind.matches = []; editorFind.idx = -1;
  findCountEl.textContent = "已替换 " + ms.length + " 处";
}

// ---- 转到行 / 符号大纲 ----
function gotoLine(line) {
  const lines = editorTextEl.value.split("\n");
  line = Math.max(1, Math.min(line, lines.length));
  let pos = 0;
  for (let i = 0; i < line - 1; i++) pos += lines[i].length + 1;
  editorTextEl.setSelectionRange(pos, pos + lines[line - 1].length);
  const lineH = editorFontSize * 1.55;
  editorTextEl.scrollTop = Math.max(0, (line - 1) * lineH - editorTextEl.clientHeight / 2);
  syncScroll(); editorTextEl.focus(); updateStatus();
}
function openGoto() { gotoInputEl.hidden = false; gotoInputEl.value = ""; setTimeout(() => gotoInputEl.focus(), 0); }
function buildOutline() {
  const code = editorTextEl.value, lang = activeEditorTab ? activeEditorTab.lang : "";
  const items = [], lines = code.split("\n");
  lines.forEach((ln, idx) => {
    let m;
    if (lang === "python") { m = ln.match(/^(\s*)(async\s+)?(def|class)\s+([A-Za-z_]\w*)/); if (m) items.push({ line: idx + 1, kind: m[3] === "class" ? "class" : "def", name: m[4] }); }
    else if (lang === "javascript" || lang === "typescript") {
      if ((m = ln.match(/^\s*(export\s+)?(async\s+)?function\s+([A-Za-z_]\w*)/))) items.push({ line: idx + 1, kind: "fn", name: m[3] });
      else if ((m = ln.match(/^\s*(export\s+)?(const|let|var)\s+([A-Za-z_]\w*)\s*=/))) items.push({ line: idx + 1, kind: "var", name: m[3] });
      else if ((m = ln.match(/^\s*(export\s+)?class\s+([A-Za-z_]\w*)/))) items.push({ line: idx + 1, kind: "class", name: m[2] });
    }
    else if (lang === "go") {
      if ((m = ln.match(/^func\s+(?:\([^)]*\)\s+)?([A-Za-z_]\w*)/))) items.push({ line: idx + 1, kind: "func", name: m[1] });
      else if ((m = ln.match(/^type\s+([A-Za-z_]\w*)/))) items.push({ line: idx + 1, kind: "type", name: m[1] });
    }
    else if (lang === "markdown") { if ((m = ln.match(/^(#{1,6})\s+(.*)$/))) items.push({ line: idx + 1, kind: "h" + m[1].length, name: m[2].trim() }); }
    else if ((m = ln.match(/^\s*(def|function|func|class|method|sub|pub\s+fn|fn)\s+([A-Za-z_]\w*)/))) items.push({ line: idx + 1, kind: m[1].replace(/\s/g, ""), name: m[2] });
  });
  return items;
}
function openOutline() {
  if (outlineEl.hidden) {
    const items = buildOutline();
    let html = '<div class="ol-head">符号 · ' + items.length + ' 个</div>';
    if (!items.length) html += '<div class="ol-head">当前文件无符号</div>';
    items.forEach((it) => {
      html += '<div class="ol-item" data-line="' + it.line + '"><span class="ol-kind">' + esc(it.kind) + '</span><span class="ol-name">' + esc(it.name) + '</span><span class="ol-line">' + it.line + '</span></div>';
    });
    outlineEl.innerHTML = html;
    outlineEl.querySelectorAll(".ol-item").forEach((el) => el.addEventListener("click", () => { gotoLine(parseInt(el.getAttribute("data-line"), 10)); outlineEl.hidden = true; }));
    outlineEl.hidden = false;
  } else outlineEl.hidden = true;
}

// ---- 全局搜索 (在文件中查找) ----
function openFif() { fifModal.hidden = false; fifInput.value = ""; setTimeout(() => fifInput.focus(), 0); }
async function runFif() {
  const pat = fifInput.value;
  if (!pat) return;
  const regex = fifRegexBtn.classList.contains("active");
  const ext = fifExt.value.trim();
  const root = currentFileDir || ".";
  fifResults.innerHTML = '<div class="fif-meta">搜索中…</div>';
  try {
    const url = "/api/fs/grep?path=" + encodeURIComponent(root) + "&pattern=" + encodeURIComponent(pat) + "&regex=" + (regex ? "1" : "0") + "&ignorecase=1&ext=" + encodeURIComponent(ext);
    const r = await fetch(url);
    const d = await r.json();
    if (d.error) { fifResults.innerHTML = '<div class="fif-empty">错误: ' + esc(d.error) + '</div>'; return; }
    if (!d.results.length) { fifResults.innerHTML = '<div class="fif-empty">无匹配 (扫描 ' + d.scanned + ' 文件)' + (d.truncated ? " · 已达上限" : "") + '</div>'; return; }
    let html = '<div class="fif-meta">' + d.count + ' 处命中 · ' + d.scanned + ' 文件' + (d.truncated ? " · 已达上限" : "") + '</div>';
    let lastFile = null;
    d.results.forEach((res) => {
      if (res.file !== lastFile) { html += '<div class="fif-file">' + esc(res.file) + '</div>'; lastFile = res.file; }
      let disp;
      if (!regex) { const idx = res.text.toLowerCase().indexOf(pat.toLowerCase()); disp = idx >= 0 ? esc(res.text.slice(0, idx)) + "<mark>" + esc(res.text.slice(idx, idx + pat.length)) + "</mark>" + esc(res.text.slice(idx + pat.length)) : esc(res.text); }
      else disp = esc(res.text);
      html += '<div class="fif-row" data-file="' + esc(res.file) + '" data-line="' + res.line + '"><span class="fif-ln">' + res.line + '</span><span class="fif-txt">' + disp + '</span></div>';
    });
    fifResults.innerHTML = html;
    fifResults.querySelectorAll(".fif-row").forEach((row) => row.addEventListener("click", () => { const f = row.getAttribute("data-file"); const ln = parseInt(row.getAttribute("data-line"), 10); fifModal.hidden = true; openFileTab(f, { line: ln }); }));
  } catch (e) { fifResults.innerHTML = '<div class="fif-empty">异常: ' + esc(String(e)) + '</div>'; }
}

// ---- 简单格式化 (无外部依赖) ----
function formatDoc() {
  if (!activeEditorTab) return;
  const lang = activeEditorTab.lang;
  let v = editorTextEl.value;
  if (lang === "json") { try { v = JSON.stringify(JSON.parse(v), null, 2); } catch (e) { toast("JSON 解析失败, 未格式化", "error"); return; } }
  else { v = v.replace(/\t/g, "    ").replace(/[ \t]+\n/g, "\n").replace(/\r\n/g, "\n"); if (v && !v.endsWith("\n")) v += "\n"; }
  editorTextEl.value = v; persistActive(); syncGutter(); renderEditorLayers(); updateStatus();
  toast("已格式化 (" + langLabel(lang) + ")", "ok");
}

// ---- 查找条 / 工具栏 事件绑定 ----
findInputEl.addEventListener("input", runFind);
findInputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); e.shiftKey ? findPrev() : findNext(); }
  else if (e.key === "Escape") { e.preventDefault(); clearFind(); }
});
findNextBtn.addEventListener("click", findNext);
findPrevBtn.addEventListener("click", findPrev);
findRegexBtn.addEventListener("click", () => { findRegexBtn.classList.toggle("active"); runFind(); });
findIcaseBtn.addEventListener("click", () => { findIcaseBtn.classList.toggle("active"); runFind(); });
findReplaceToggle.addEventListener("click", () => { const show = replaceInputEl.hidden; replaceInputEl.hidden = !show; replaceOneBtn.hidden = !show; replaceAllBtn.hidden = !show; if (show) replaceInputEl.focus(); });
replaceOneBtn.addEventListener("click", replaceOne);
replaceAllBtn.addEventListener("click", replaceAll);
findCloseBtn.addEventListener("click", clearFind);
findBtn.addEventListener("click", () => openFind(false));
wrapToggleBtn.addEventListener("click", toggleWrap);
fontDecBtn.addEventListener("click", () => { editorFontSize = Math.max(10, editorFontSize - 1); applyFont(); updateStatus(); });
fontIncBtn.addEventListener("click", () => { editorFontSize = Math.min(22, editorFontSize + 1); applyFont(); updateStatus(); });
outlineBtn.addEventListener("click", openOutline);
gotoBtn.addEventListener("click", openGoto);
gotoInputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); const n = parseInt(gotoInputEl.value, 10); if (!isNaN(n)) gotoLine(n); gotoInputEl.hidden = true; }
  else if (e.key === "Escape") { e.preventDefault(); gotoInputEl.hidden = true; }
});
findFilesBtn.addEventListener("click", openFif);
fifCloseBtn.addEventListener("click", () => { fifModal.hidden = true; });
fifModal.addEventListener("click", (e) => { if (e.target === fifModal) fifModal.hidden = true; });
fifRun.addEventListener("click", runFif);
fifInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runFif(); } else if (e.key === "Escape") { fifModal.hidden = true; } });
fifRegexBtn.addEventListener("click", () => { fifRegexBtn.classList.toggle("active"); });
document.addEventListener("click", (e) => {
  if (outlineEl && !outlineEl.hidden && e.target !== outlineBtn && !outlineBtn.contains(e.target) && !outlineEl.contains(e.target)) outlineEl.hidden = true;
});
applyFont(); applyWrap();

function showFileContent(rel) { return openFileTab(rel); }

// 行号栏与文本区同步
function syncGutter() {
  const lines = editorTextEl.value.split("\n").length || 1;
  let s = "";
  for (let i = 1; i <= lines; i++) s += i + "\n";
  editorGutterEl.textContent = s;
  editorGutterEl.scrollTop = editorTextEl.scrollTop;
}
editorTextEl.addEventListener("input", syncGutter);
editorTextEl.addEventListener("scroll", syncScroll);
editorTextEl.addEventListener("input", onEditorInput);

// 轻量语法高亮: 单遍 tokenizer, 先匹配注释/字符串/数字, 再按关键字着色, 避免二次处理已生成标签
function escapeHtml(s){ return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
const _HL_KW = new Set(["def","class","import","from","return","if","else","elif","for","while","try","except","finally","with","as","in","not","and","or","is","None","True","False","async","await","lambda","yield","global","nonlocal","pass","break","continue","raise","assert","del","print","function","var","let","const","new","typeof","interface","type","public","private","static","void","int","string","bool","float","double","enum","struct","fn","pub","use","mut","match","where"]);
function highlightCode(){
  const code = editorTextEl.value;
  const re = /(\/\/.*|#.*)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)|\b(\d+\.?\d*)\b|([A-Za-z_]\w*)/g;
  let out = "", last = 0, m;
  while((m = re.exec(code))){
    out += escapeHtml(code.slice(last, m.index));
    if(m[1]) out += '<span class="t-com">'+escapeHtml(m[1])+'</span>';
    else if(m[2]) out += '<span class="t-str">'+escapeHtml(m[2])+'</span>';
    else if(m[3]) out += '<span class="t-num">'+escapeHtml(m[3])+'</span>';
    else if(m[4]) out += _HL_KW.has(m[4]) ? '<span class="t-kw">'+escapeHtml(m[4])+'</span>' : escapeHtml(m[4]);
    last = re.lastIndex;
  }
  out += escapeHtml(code.slice(last));
  editorHlEl.innerHTML = out + String.fromCharCode(10);
}
// Tab 键插入 4 空格 + 括号自动补全 + Ctrl/Cmd+S 保存
editorTextEl.addEventListener("keydown", (e) => {
  if (e.key === "Tab") {
    e.preventDefault();
    const s = editorTextEl.selectionStart, en = editorTextEl.selectionEnd;
    editorTextEl.value = editorTextEl.value.slice(0, s) + "    " + editorTextEl.value.slice(en);
    editorTextEl.selectionStart = editorTextEl.selectionEnd = s + 4;
    syncGutter();
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
    e.preventDefault();
    saveFile();
    return;
  }
  if (!e.ctrlKey && !e.metaKey && !e.altKey) {
    const pair = { "(": ")", "[": "]", "{": "}", '"': '"', "'": "'", "`": "`" };
    if (pair[e.key]) {
      const s = editorTextEl.selectionStart, en = editorTextEl.selectionEnd;
      const next = editorTextEl.value[en];
      if ((e.key === '"' || e.key === "'") && next && /\w/.test(next)) return;
      e.preventDefault();
      const close = pair[e.key];
      const sel = editorTextEl.value.slice(s, en);
      editorTextEl.setRangeText(e.key + sel + close, s, en, "end");
      editorTextEl.selectionStart = editorTextEl.selectionEnd = s + 1 + sel.length;
      onEditorInput();
      return;
    }
    if (")]}'".includes(e.key)) {
      const en = editorTextEl.selectionStart;
      if (editorTextEl.value[en] === e.key) { e.preventDefault(); editorTextEl.selectionStart = editorTextEl.selectionEnd = en + 1; return; }
    }
  }
});

async function saveFile() {
  if (!activeEditorTab) return;
  editorSaveBtn.disabled = true;
  try {
    const r = await fetch("/api/fs/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: activeEditorTab.path, content: editorTextEl.value }),
    });
    const d = await r.json();
    if (d.error) { toast(d.error, "error"); fileSaveStateEl.textContent = "✗ 保存失败"; return; }
    activeEditorTab.content = editorTextEl.value;
    activeEditorTab.savedContent = editorTextEl.value;
    activeEditorTab.dirty = false;
    fileSaveStateEl.textContent = "✓ 已保存 " + d.bytes + " 字节";
    editorFileEl.textContent = activeEditorTab.path;
    renderEditorTabs();
    toast("已保存 " + activeEditorTab.path, "ok");
  } catch (e) {
    toast("保存失败: " + e, "error");
    fileSaveStateEl.textContent = "✗ 保存失败";
  } finally {
    editorSaveBtn.disabled = false;
  }
}

async function reloadFile() {
  if (!activeEditorTab) return;
  const tab = activeEditorTab;
  try {
    const r = await fetch("/api/fs/read?path=" + encodeURIComponent(tab.path));
    const d = await r.json();
    if (d.error) { toast(d.error, "error"); return; }
    if (d.binary) { toast("二进制文件不支持编辑", "warn"); return; }
    tab.content = d.content || ""; tab.savedContent = d.content || ""; tab.dirty = false;
    editorTextEl.value = tab.content;
    editorFileEl.textContent = tab.path;
    syncGutter(); renderEditorLayers(); updateStatus(); renderEditorTabs();
    toast("已重载 " + tab.path, "ok");
  } catch (e) { toast("读取失败: " + e, "error"); }
}

async function copyFile() {
  try {
    await navigator.clipboard.writeText(editorTextEl.value);
    toast("已复制到剪贴板", "ok");
  } catch (e) {
    toast("复制失败: " + e, "error");
  }
}

// 自动交付闭环: 跑测试 + 静态评审, 给出交付判定
async function runDeliver() {
  if (!currentEditPath) { toast("请先打开一个文件", "warn"); return; }
  editorDeliverBtn.disabled = true;
  deliverBadgeEl.style.display = "none";
  toast("交付自检中: 跑测试 + 评审…", "info");
  try {
    const r = await fetch("/api/deliver", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: currentEditPath }),
    });
    const d = await r.json();
    if (d.error) { toast("交付自检失败: " + d.error, "error"); return; }
    const v = (d.review && d.review.verdict) || "unknown";
    const ok = d.delivery_ready;
    const parts = [];
    if (d.test && d.test.rc !== null && d.test.rc !== undefined) {
      parts.push(d.test.passed ? "测试通过(rc=0)" : "测试失败(rc=" + d.test.rc + ")");
    } else if (d.test) {
      parts.push("测试未运行");
    }
    parts.push("评审=" + v + (d.review && d.review.score != null ? "(" + d.review.score + ")" : ""));
    deliverBadgeEl.textContent = (ok ? "✅ 可交付" : "⛔ 不可交付") + " · " + parts.join(" · ");
    deliverBadgeEl.className = "deliver-badge " + (ok ? "ok" : "bad");
    deliverBadgeEl.style.display = "inline-block";
    // 刷新评审趋势图
    if (typeof loadReviews === "function") loadReviews();
    // 评审问题明细 toast
    if (d.review && d.review.issues && d.review.issues.length) {
      const top = d.review.issues.slice(0, 3).map(i => "[" + i.sev + "] " + i.desc).join("; ");
      toast((ok ? "可交付" : "暂不可交付") + " · " + top, ok ? "ok" : "warn", 6000);
    } else {
      toast(ok ? "可交付 ✅" : "不可交付 ⛔", ok ? "ok" : "warn");
    }
  } catch (e) {
    toast("交付自检异常: " + e, "error");
  } finally {
    editorDeliverBtn.disabled = false;
  }
}

editorSaveBtn.addEventListener("click", saveFile);
editorReloadBtn.addEventListener("click", reloadFile);
editorCopyBtn.addEventListener("click", copyFile);
editorDeliverBtn.addEventListener("click", runDeliver);
editorExportBtn.addEventListener("click", runExportReport);
editorReviewChangedBtn.addEventListener("click", runReviewChanged);
editorPrBtn.addEventListener("click", runPrDraft);
editorReviewReportBtn.addEventListener("click", runReviewReport);
if (artRefreshBtn) artRefreshBtn.addEventListener("click", loadArtifacts);
// 交付中心按钮
const dcScanBtn = $("dc-scan");
const dcDeliverBtn = $("dc-deliver");
const dcPrBtn = $("dc-pr");
const dcRefreshBtn = $("dc-refresh");
if (dcScanBtn) dcScanBtn.addEventListener("click", runCenterScan);
if (dcDeliverBtn) dcDeliverBtn.addEventListener("click", runCenterDeliver);
if (dcPrBtn) dcPrBtn.addEventListener("click", runCenterPr);
if (dcRefreshBtn) dcRefreshBtn.addEventListener("click", dcRefreshHealth);
// 切到「成果」tab 时自动加载清单
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => { if (t.dataset.tab === "artifacts") loadArtifacts(); });
});

// 导出交付报告: 调用 /api/deliver/report 生成自包含 HTML 并下载
async function runExportReport() {
  if (!currentEditPath) { toast("请先打开一个文件", "warn"); return; }
  editorExportBtn.disabled = true;
  toast("生成交付报告中…", "info");
  try {
    const r = await fetch("/api/deliver/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: currentEditPath, note: (deliverNoteEl.value || "").trim() }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      toast("报告生成失败: " + (e.error || r.status), "error");
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "delivery_report_" + new Date().toISOString().slice(0, 16).replace(/[:T]/g, "") + ".html";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast("交付报告已导出 📄", "ok");
  } catch (e) {
    toast("报告导出异常: " + e, "error");
  } finally {
    editorExportBtn.disabled = false;
  }
}

async function runReviewChanged() {
  editorReviewChangedBtn.disabled = true;
  toast("评审当前改动中…", "info");
  try {
    const r = await fetch("/api/review/changed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: "" }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast("评审失败: " + (data.error || r.status), "error");
      return;
    }
    if (data.total === 0) {
      toast(data.message || "无 .py 改动可评审", "info");
      return;
    }
    // 汇总徽标
    deliverBadgeEl.textContent = "评审 %d 文件 · 通过 %d / 需改 %d".replace("%d", data.total).replace("%d", data.approve).replace("%d", data.revise);
    deliverBadgeEl.className = "deliver-badge " + (data.revise === 0 ? "ok" : "bad");
    deliverBadgeEl.style.display = "inline-block";
    // 详情 toast
    const lines = (data.files || []).map(f => {
      const icon = f.verdict === "approve" ? "✅" : (f.verdict === "revise" ? "⛔" : "❓");
      return icon + " " + f.path + (f.score != null ? " (" + f.score + ")" : "");
    });
    toast("评审完成:\n" + lines.join("\n"), data.revise === 0 ? "ok" : "warn", 6000);
    if (typeof drawReviewTrend === "function") drawReviewTrend();
  } catch (e) {
    toast("评审异常: " + e, "error");
  } finally {
    editorReviewChangedBtn.disabled = false;
  }
}

async function runPrDraft() {
  editorPrBtn.disabled = true;
  toast("生成 PR 草稿中…", "info");
  try {
    const r = await fetch("/api/pr/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: "", title: "", note: (deliverNoteEl.value || "").trim(), review: true }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      toast("PR 草稿失败: " + (e.error || r.status), "error");
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "pr_draft_" + new Date().toISOString().slice(0, 16).replace(/[:T]/g, "") + ".md";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast("PR 草稿已导出 📝", "ok");
  } catch (e) {
    toast("PR 草稿异常: " + e, "error");
  } finally {
    editorPrBtn.disabled = false;
  }
}

// 生成多文件评审聚合报告: 调用 /api/review/report 生成自包含 HTML 并下载
async function runReviewReport() {
  if (!currentEditPath) { toast("请先打开一个文件", "warn"); return; }
  editorReviewReportBtn.disabled = true;
  toast("生成评审报告中…", "info");
  try {
    const r = await fetch("/api/review/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: [currentEditPath], note: (deliverNoteEl.value || "").trim() }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      toast("评审报告失败: " + (e.error || r.status), "error");
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "review_report_" + new Date().toISOString().slice(0, 16).replace(/[:T]/g, "") + ".html";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast("评审报告已导出 📊", "ok");
    if (typeof drawReviewTrend === "function") drawReviewTrend();
  } catch (e) {
    toast("评审报告异常: " + e, "error");
  } finally {
    editorReviewReportBtn.disabled = false;
  }
}

// 成果回看: 拉取 /api/artifacts 清单, 点击在浏览器打开原始成果
async function loadArtifacts() {
  if (!artListEl) return;
  try {
    const r = await fetch("/api/artifacts");
    const d = await r.json().catch(() => ({}));
    const items = (d.items || []);
    if (artifactCountEl) artifactCountEl.textContent = String(items.length);
    artListEl.innerHTML = "";
    if (!items.length) {
      artListEl.innerHTML = '<div style="padding:16px;color:var(--muted);">暂无成果。用「🚀 交付自检 → 📄 导出报告 / 📊 评审报告 / 📝 PR 草稿」生成后会自动归档到此。</div>';
      return;
    }
    const kindLabel = { delivery: "交付报告", review: "评审报告", pr: "PR 草稿" };
    items.forEach((it) => {
      const card = document.createElement("div");
      card.className = "rc-card";
      const ts = it.ts ? new Date(it.ts * 1000).toLocaleString() : "";
      const kl = kindLabel[it.kind] || it.kind;
      const size = it.size ? (it.size > 1024 ? (it.size / 1024).toFixed(1) + " KB" : it.size + " B") : "";
      const meta = it.meta || {};
      let extra = "";
      if (it.kind === "delivery") extra = (meta.ready ? "可交付" : "不可交付") + (meta.target ? " · " + meta.target : "");
      else if (it.kind === "review") extra = (meta.files || []).length + " 文件" + (meta.note ? " · " + meta.note : "");
      else if (it.kind === "pr") extra = (meta.title || "") + (meta.repo ? " · " + meta.repo : "");
      card.innerHTML = `
        <div class="rc-top"><span class="rc-id">${esc(kl)}</span><span class="rc-prov">${esc(size)}</span><span class="rc-status">${ts}</span></div>
        <div class="rc-prompt">${esc(extra || "")}</div>
        <div class="rc-meta">${esc(it.name || "")}</div>`;
      card.addEventListener("click", () => { window.open("/api/artifacts/" + it.id + "/raw", "_blank"); });
      artListEl.appendChild(card);
    });
  } catch (e) {
    if (artListEl) artListEl.innerHTML = '<div style="padding:16px;color:var(--bad);">加载失败: ' + esc(String(e)) + '</div>';
  }
}

fileUpBtn.addEventListener("click", () => {
  if (currentFileDir === "." || currentFileDir === "") return;
  const parent = currentFileDir.includes("/") ? currentFileDir.slice(0, currentFileDir.lastIndexOf("/")) : ".";
  loadFiles(parent);
});

// ---------- 会话历史 ----------
async function loadSessions() {
  try {
    const r = await fetch("/api/sessions");
    const d = await r.json();
    sessionListEl.innerHTML = "";
    sessionDetailEl.style.display = "none";
    (d.sessions || []).forEach((s) => {
      const card = document.createElement("div");
      card.className = "rc-card";
      const ts = s.updated_at ? new Date(s.updated_at * 1000).toLocaleString() : "";
      card.innerHTML = `
        <div class="rc-top"><span class="rc-id">${esc(s.id)}</span><span class="rc-prov">${esc(s.provider || "")}</span><span class="rc-status">${s.messages} 条</span></div>
        <div class="rc-prompt">${esc(s.summary || "(空)")}</div>
        <div class="rc-meta">${ts}</div>`;
      card.addEventListener("click", () => showSession(s.id));
      sessionListEl.appendChild(card);
    });
    if (!(d.sessions || []).length) {
      sessionListEl.innerHTML = '<div class="empty">暂无历史会话</div>';
    }
  } catch (e) {
    sessionListEl.innerHTML = '<div class="empty">会话加载失败</div>';
  }
}

let currentSessionId = null;
async function showSession(id) {
  currentSessionId = id;
  try {
    const r = await fetch(`/api/sessions/${id}`);
    const d = await r.json();
    if (d.error) { alert("未找到"); return; }
    sessionMsgsEl.innerHTML = "";
    (d.messages || []).forEach((m) => {
      const wrap = document.createElement("div");
      wrap.className = "msg " + (m.role === "user" ? "user" : "agent");
      const tag = document.createElement("div");
      tag.className = "role-tag";
      tag.textContent = m.role === "user" ? "你" : "灵梦";
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = m.content || "";
      wrap.appendChild(tag);
      wrap.appendChild(bubble);
      sessionMsgsEl.appendChild(wrap);
    });
    sessionDetailEl.style.display = "block";
    sessionListEl.style.display = "none";
  } catch (e) {
    alert("加载失败: " + e);
  }
}

sessionBackBtn.addEventListener("click", () => {
  sessionDetailEl.style.display = "none";
  sessionListEl.style.display = "block";
  loadSessions();
});

sessionResumeBtn.addEventListener("click", async () => {
  if (!currentSessionId) return;
  try {
    const r = await fetch(`/api/sessions/${currentSessionId}`);
    const d = await r.json();
    if (d.error) { alert("未找到"); return; }
    // 真续跑: 把服务端会话历史渲染进对话区, 本地 history 交由服务端持有
    messagesEl.innerHTML = "";
    history = [];
    let shown = 0;
    (d.messages || []).forEach((m) => {
      if (m.role === "system") return;
      const b = addMessage(m.role);
      b.textContent = m.content || "";
      shown++;
    });
    // 续跑就绪提示
    const note = addMessage("agent");
    note.textContent = `已恢复会话 ${currentSessionId} (${shown} 条历史, 含工具执行态), 服务端续跑已就绪, 直接继续对话即可。`;
    const at = activeTab(); if (at) { at.sessionId = currentSessionId; at.cache = messagesEl.innerHTML; refreshTabTitles(); }
    document.querySelector('.tab[data-tab="chat"]').click();
    scrollDown();
  } catch (e) {
    alert("恢复失败: " + e);
  }
});

// ===================== 多会话标签栏 (P2b) =====================
const sessionTabsEl = $("session-tabs");
const sessionTabListEl = $("session-tab-list");
const sessionTabNewBtn = $("session-tab-new");
let tabSeq = 0;
let openTabs = [];          // [{tabId, sessionId, title, cache}]
let activeTabId = null;

function activeTab() { return openTabs.find(t => t.tabId === activeTabId) || null; }

function renderSessionTabs() {
  sessionTabListEl.innerHTML = "";
  openTabs.forEach((t) => {
    const el = document.createElement("div");
    el.className = "session-tab" + (t.tabId === activeTabId ? " active" : "");
    el.dataset.tabId = t.tabId;
    el.innerHTML = `<span class="st-title">${esc(t.title || "新对话")}</span>` +
      (openTabs.length > 1 ? `<span class="st-close" title="关闭">×</span>` : "");
    el.querySelector(".st-title").addEventListener("click", () => activateTab(t.tabId));
    el.querySelector(".st-title").addEventListener("dblclick", () => renameTab(t.tabId));
    const cl = el.querySelector(".st-close");
    if (cl) cl.addEventListener("click", (e) => { e.stopPropagation(); closeTab(t.tabId); });
    sessionTabListEl.appendChild(el);
  });
}

function createTab(sessionId = null, title = "新对话", activate = true) {
  const t = { tabId: "t" + (++tabSeq), sessionId: sessionId, title: title, cache: "" };
  openTabs.push(t);
  if (activate) activateTab(t.tabId); else renderSessionTabs();
  return t;
}

async function activateTab(tabId) {
  const cur = activeTab();
  if (cur) cur.cache = messagesEl.innerHTML;
  const t = openTabs.find(x => x.tabId === tabId);
  if (!t) return;
  activeTabId = tabId;
  currentSessionId = t.sessionId;   // 可能为 null -> 后端新建会话
  if (t.sessionId) {
    await loadSessionIntoChat(t.sessionId);   // 服务端为真相源, 始终重拉历史
    t.cache = messagesEl.innerHTML;
  } else if (t.cache) {
    messagesEl.innerHTML = t.cache;
  } else {
    messagesEl.innerHTML = "";
  }
  renderSessionTabs();
  scrollDown();
}

async function loadSessionIntoChat(id) {
  try {
    const r = await fetch(`/api/sessions/${id}`);
    const d = await r.json();
    if (d.error) return;
    messagesEl.innerHTML = "";
    history = [];
    (d.messages || []).forEach((m) => {
      if (m.role === "system") return;
      const b = addMessage(m.role);
      b.textContent = m.content || "";
      history.push({ role: m.role, content: m.content || "" });
    });
  } catch (e) { /* ignore */ }
}

function closeTab(tabId) {
  const idx = openTabs.findIndex(t => t.tabId === tabId);
  if (idx < 0) return;
  openTabs.splice(idx, 1);
  if (openTabs.length === 0) { createTab(); return; }
  if (tabId === activeTabId) {
    const next = openTabs[Math.max(0, idx - 1)];
    activateTab(next.tabId);
  } else {
    renderSessionTabs();
  }
}

function renameTab(tabId) {
  const t = openTabs.find(x => x.tabId === tabId);
  if (!t) return;
  const name = prompt("重命名此会话标签：", t.title);
  if (name && name.trim()) { t.title = name.trim(); renderSessionTabs(); }
}

async function refreshTabTitles() {
  try {
    const r = await fetch("/api/sessions");
    const d = await r.json();
    (d.sessions || []).forEach((s) => {
      const t = openTabs.find(x => x.sessionId === s.id);
      if (t && s.summary) t.title = s.summary;
    });
    renderSessionTabs();
  } catch (e) { /* ignore */ }
}

if (sessionTabNewBtn) sessionTabNewBtn.addEventListener("click", () => createTab());
createTab();   // 初始默认开一个「新对话」标签

const sessionExportBtn = $("session-export");
if (sessionExportBtn) sessionExportBtn.addEventListener("click", exportChatMarkdown);

// 对话内搜索框交互绑定 (P2d)
(function bindChatSearch() {
  const inp = $("cs-input"), prev = $("cs-prev"), next = $("cs-next"), close = $("cs-close");
  if (inp) inp.addEventListener("input", () => runChatSearch(inp.value));
  if (inp) inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); e.shiftKey ? prevChatMatch() : nextChatMatch(); }
    else if (e.key === "Escape") { e.preventDefault(); closeChatSearch(); }
  });
  if (prev) prev.addEventListener("click", prevChatMatch);
  if (next) next.addEventListener("click", nextChatMatch);
  if (close) close.addEventListener("click", closeChatSearch);
})();

// ===================== 全局命令面板 (P0 交互飞跃) =====================
const cmdPaletteEl = $("cmd-palette");
const cmdInputEl = $("cmd-input");
const cmdListEl = $("cmd-list");
let cmdFiltered = [];
let cmdActive = 0;

function switchTabByName(name) {
  const t = document.querySelector(`.tab[data-tab="${name}"]`);
  if (t) t.click();
}
function gotoPage(href) { window.location.href = href; }
function triggerReviewNow() {
  const b = $("editor-review-changed");
  if (b) { switchTabByName("deliver"); b.click(); }
}

// ===================== 对话内搜索 + 长消息折叠 (P2d) =====================
let _chatMatchEls = [];
let _chatMatchIdx = -1;

function escapeRegex(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

function clearChatHighlights() {
  _chatMatchEls = [];
  _chatMatchIdx = -1;
  document.querySelectorAll("#messages mark.hl").forEach(m => {
    const parent = m.parentNode;
    if (!parent) return;
    parent.replaceChild(document.createTextNode(m.textContent), m);
    parent.normalize();
  });
  const c = $("cs-count");
  if (c) c.textContent = "";
}

function highlightWalk(root, re, out) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const targets = [];
  let n;
  while ((n = walker.nextNode())) {
    if (n.parentNode && n.parentNode.tagName === "MARK") continue;
    if (re.test(n.nodeValue)) targets.push(n);
  }
  targets.forEach(tn => {
    const frag = document.createDocumentFragment();
    let last = 0;
    const text = tn.nodeValue;
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const mark = document.createElement("mark");
      mark.className = "hl";
      mark.textContent = m[0];
      frag.appendChild(mark);
      out.push(mark);
      last = m.index + m[0].length;
      if (m[0].length === 0) re.lastIndex++;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    tn.parentNode.replaceChild(frag, tn);
  });
}

function runChatSearch(q) {
  clearChatHighlights();
  q = (q || "").trim();
  if (!q) return;
  const re = new RegExp(escapeRegex(q), "gi");
  document.querySelectorAll("#messages .msg").forEach(msg => highlightWalk(msg, re, _chatMatchEls));
  _chatMatchIdx = _chatMatchEls.length ? 0 : -1;
  updateChatCount();
  if (_chatMatchEls.length) focusChatMatch(0);
}

function updateChatCount() {
  const c = $("cs-count");
  if (!c) return;
  c.textContent = _chatMatchEls.length ? ((_chatMatchIdx + 1) + " / " + _chatMatchEls.length) : "无匹配";
}

function focusChatMatch(i) {
  if (!_chatMatchEls.length) return;
  _chatMatchIdx = (i + _chatMatchEls.length) % _chatMatchEls.length;
  _chatMatchEls.forEach((m, k) => m.classList.toggle("hl-active", k === _chatMatchIdx));
  const m = _chatMatchEls[_chatMatchIdx];
  m.scrollIntoView({ block: "center", behavior: "smooth" });
  updateChatCount();
}

function nextChatMatch() { if (_chatMatchEls.length) focusChatMatch(_chatMatchIdx + 1); }
function prevChatMatch() { if (_chatMatchEls.length) focusChatMatch(_chatMatchIdx - 1); }

function openChatSearch() {
  const bar = $("chat-search");
  if (!bar) return;
  bar.hidden = false;
  const inp = $("cs-input");
  if (inp) { inp.value = ""; inp.focus(); }
  runChatSearch("");
}
function closeChatSearch() {
  const bar = $("chat-search");
  if (bar) bar.hidden = true;
  clearChatHighlights();
}

function collapseLongMessages() {
  const MAX = 560;
  document.querySelectorAll("#messages .msg:not(.collapse-checked)").forEach(msg => {
    msg.classList.add("collapse-checked");
    const bubble = msg.querySelector(".bubble");
    if (!bubble) return;
    // 延迟一帧, 等流式渲染/布局稳定后再测量
    requestAnimationFrame(() => {
      if (bubble.scrollHeight > MAX) {
        bubble.classList.add("collapsible");
        const btn = document.createElement("button");
        btn.className = "msg-expand";
        btn.textContent = "展开全文 ⌄";
        btn.addEventListener("click", () => {
          const ex = bubble.classList.toggle("expanded");
          btn.textContent = ex ? "收起 ⌃" : "展开全文 ⌄";
        });
        bubble.appendChild(btn);
      }
    });
  });
}

// 会话导出为 Markdown (P2e, 纯前端)
function bubbleToMarkdown(bubble) {
  const mdBody = bubble.querySelector(".md-body");
  const root = mdBody || bubble;
  let out = "";
  root.childNodes.forEach(node => {
    if (node.nodeType === Node.TEXT_NODE) { out += node.textContent; return; }
    if (!(node instanceof HTMLElement)) return;
    if (node.classList.contains("msg-copy") || node.classList.contains("msg-time") || node.classList.contains("msg-expand")) return;
    if (node.classList.contains("code-block")) {
      const lang = (node.querySelector(".cb-lang") || {}).textContent || "";
      const code = Array.from(node.querySelectorAll(".lc")).map(e => e.textContent).join("\n");
      out += "\n```" + lang + "\n" + code + "\n```\n";
    } else if (node.classList.contains("plan-card")) {
      out += node.textContent + "\n";
    } else {
      out += node.textContent + "\n";
    }
  });
  return out.trim();
}
function exportChatMarkdown() {
  const msgs = document.querySelectorAll("#messages .msg");
  if (!msgs.length) { toast("当前没有可导出的对话", "error"); return; }
  let md = "# 灵梦work 对话导出\n\n> 导出时间: " + new Date().toLocaleString("zh-CN") + "\n\n";
  msgs.forEach(m => {
    const role = m.classList.contains("user") ? "你" : "灵梦";
    const bubble = m.querySelector(".bubble");
    const body = bubble ? bubbleToMarkdown(bubble) : "";
    if (body.trim()) md += "## " + role + "\n\n" + body + "\n\n";
  });
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  const at = activeTab();
  const title = (at && at.title) || "conversation";
  a.href = URL.createObjectURL(blob);
  a.download = "灵梦work-" + title + "-" + Date.now() + ".md";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast("已导出 Markdown (" + msgs.length + " 条消息)", "ok");
}

const COMMANDS = [
  { id: "search", title: "搜索当前对话", hint: "⌘F", run: openChatSearch },
  { id: "export", title: "导出当前对话为 Markdown", run: exportChatMarkdown },
  { id: "new", title: "新建会话标签", hint: "⌘⇧O", run: () => createTab() },
  { id: "clear", title: "清空对话", hint: "⌘L", run: () => { const b = $("btn-clear"); if (b) b.click(); } },
  { id: "focus", title: "聚焦输入框 / 回到对话", run: () => { switchTabByName("chat"); if (inputEl) inputEl.focus(); } },
  { id: "ed-save", title: "编辑器 · 保存文件", hint: "⌘S", run: () => { if (editorWrapEl.style.display !== "none") saveFile(); } },
  { id: "ed-find", title: "编辑器 · 查找 (Ctrl+F)", run: () => { if (editorWrapEl.style.display !== "none") openFind(false); } },
  { id: "ed-find-files", title: "编辑器 · 在文件中查找", run: openFif },
  { id: "ed-goto", title: "编辑器 · 转到行 (Ctrl+G)", run: () => { if (editorWrapEl.style.display !== "none") openGoto(); } },
  { id: "ed-symbol", title: "编辑器 · 符号大纲", run: () => { if (editorWrapEl.style.display !== "none") openOutline(); } },
  { id: "ed-wrap", title: "编辑器 · 切换自动换行 (Alt+Z)", run: () => { if (editorWrapEl.style.display !== "none") toggleWrap(); } },
  { id: "ed-format", title: "编辑器 · 格式化文档", run: () => { if (editorWrapEl.style.display !== "none") formatDoc(); } },
  { id: "ed-close", title: "编辑器 · 关闭当前标签", run: () => { if (activeEditorTab) closeEditorTab(activeEditorTab); } },
  { id: "review", title: "触发代码评审 (当前改动)", hint: "⌘E", run: triggerReviewNow },
  { id: "t-chat", title: "切换到 · 对话", run: () => switchTabByName("chat") },
  { id: "t-results", title: "切换到 · 结果回看", run: () => switchTabByName("results") },
  { id: "t-files", title: "切换到 · 文件树", run: () => switchTabByName("files") },
  { id: "t-terminal", title: "切换到 · 终端", run: () => switchTabByName("terminal") },
  { id: "t-sessions", title: "切换到 · 会话历史", run: () => switchTabByName("sessions") },
  { id: "t-reviews", title: "切换到 · 代码评审", run: () => switchTabByName("reviews") },
  { id: "t-mcp", title: "切换到 · 外部工具中枢", run: () => switchTabByName("mcp") },
  { id: "t-deliver", title: "切换到 · 交付中心", run: () => switchTabByName("deliver") },
  { id: "t-artifacts", title: "切换到 · 成果存档", run: () => switchTabByName("artifacts") },
  { id: "p-obs", title: "打开 · 可观测仪表盘", run: () => gotoPage("/observability") },
  { id: "p-cost", title: "打开 · 成本看板", run: () => gotoPage("/cost") },
  { id: "p-plan", title: "打开 · 计划看板", run: () => gotoPage("/planboard") },
  { id: "p-settings", title: "打开 · 设置中心", run: () => gotoPage("/settings") },
  { id: "p-sandbox", title: "打开 · 工作区沙箱", run: () => gotoPage("/sandbox") },
  { id: "p-orchestrate", title: "打开 · 编排中枢", run: () => gotoPage("/orchestrate") },
  { id: "p-studio", title: "打开 · 创作工作台", run: () => gotoPage("/studio") },
  { id: "p-autonomous", title: "打开 · 自主模式", run: () => gotoPage("/autonomous") },
  { id: "p-pipeline", title: "打开 · 目标流水线", run: () => gotoPage("/pipeline") },
  { id: "p-multimodal", title: "打开 · 多模态实验室", run: () => gotoPage("/multimodal") },
  { id: "p-control", title: "打开 · 统一总控台", run: () => gotoPage("/control-center") },
  { id: "p-automation", title: "打开 · 自动化调度", run: () => gotoPage("/automation") },
  { id: "p-activity", title: "打开 · 实时活动", run: () => gotoPage("/activity") },
  { id: "p-audit", title: "打开 · 操作审计", run: () => gotoPage("/audit") },
  { id: "p-heal", title: "打开 · 自主进化", run: () => gotoPage("/heal") },
  { id: "theme-code", title: "主题 · 编码", run: () => setTheme("code") },
  { id: "theme-audio", title: "主题 · 音频", run: () => setTheme("audio") },
  { id: "theme-image", title: "主题 · 图片", run: () => setTheme("image") },
  { id: "theme-video", title: "主题 · 视频", run: () => setTheme("video") },
  { id: "stars", title: "打开收藏夹 (跨会话)", run: openStars },
];

function renderCmd() {
  cmdListEl.innerHTML = "";
  if (!cmdFiltered.length) { cmdListEl.innerHTML = '<li class="cmd-empty">无匹配命令</li>'; return; }
  cmdFiltered.forEach((c, i) => {
    const li = document.createElement("li");
    li.className = "cmd-item" + (i === cmdActive ? " active" : "");
    li.innerHTML = `<span class="cmd-title">${esc(c.title)}</span>${c.hint ? `<kbd class="cmd-hint">${esc(c.hint)}</kbd>` : ""}`;
    li.addEventListener("click", () => { cmdActive = i; execCmd(c); });
    li.addEventListener("mouseenter", () => { cmdActive = i; markCmd(); });
    cmdListEl.appendChild(li);
  });
}
function markCmd() { Array.from(cmdListEl.children).forEach((el, i) => el.classList.toggle("active", i === cmdActive)); }
function scrollCmd() { const el = cmdListEl.children[cmdActive]; if (el) el.scrollIntoView({ block: "nearest" }); }
function execCmd(c) { closeCmd(); try { c.run(); } catch (e) { console.error(e); } }
function filterCmd(q) {
  q = (q || "").trim().toLowerCase();
  cmdFiltered = q ? COMMANDS.filter(c => c.title.toLowerCase().includes(q) || c.id.includes(q)) : COMMANDS;
  cmdActive = 0; renderCmd();
}
function openCmd() { cmdPaletteEl.hidden = false; cmdInputEl.value = ""; cmdFiltered = COMMANDS; cmdActive = 0; renderCmd(); cmdInputEl.focus(); }
function closeCmd() { cmdPaletteEl.hidden = true; }

cmdInputEl.addEventListener("input", () => filterCmd(cmdInputEl.value));
  const appEl = $("app");
  const sidebarOverlay = $("sidebar-overlay");
  const btnMenu = $("btn-menu");
  function toggleSidebar(force) {
    if (!appEl) return;
    const open = (force !== undefined) ? force : !appEl.classList.contains("sidebar-open");
    appEl.classList.toggle("sidebar-open", open);
  }
  if (btnMenu) btnMenu.addEventListener("click", (e) => { e.stopPropagation(); toggleSidebar(); });
  if (sidebarOverlay) sidebarOverlay.addEventListener("click", () => toggleSidebar(false));
  const btnClearEl = $("btn-clear");
  if (btnClearEl) btnClearEl.addEventListener("click", () => toggleSidebar(false));
const btnCmd = $("btn-cmd");
if (btnCmd) btnCmd.addEventListener("click", openCmd);
cmdPaletteEl.addEventListener("click", (e) => { if (e.target === cmdPaletteEl) closeCmd(); });
// 收藏夹 (P2h)
const btnStars = $("btn-stars");
if (btnStars) btnStars.addEventListener("click", openStars);
const quoteClearBtn = $("quote-clear");
if (quoteClearBtn) quoteClearBtn.addEventListener("click", clearQuote);
const starsModalEl = $("stars-modal");
if (starsModalEl) {
  starsModalEl.addEventListener("click", (e) => { if (e.target === starsModalEl) starsModalEl.hidden = true; });
  const sc = $("stars-close"); if (sc) sc.addEventListener("click", () => { starsModalEl.hidden = true; });
}
// 消息列表变动时自动恢复收藏高亮 (加载历史/新增/重发重建 均触发)
const starObserver = new MutationObserver(() => { clearTimeout(starObserver._t); starObserver._t = setTimeout(applyAllStarStates, 180); });
starObserver.observe(messagesEl, { childList: true });
// 用户手动滚回底部时隐藏「新消息」提示
messagesEl.addEventListener("scroll", () => { if (isNearBottom() && newMsgHintEl) newMsgHintEl.classList.remove("show"); });

// 规划模式横幅: 切到 plan 时提示双模审批心智
const agentModeEl = $("agent-mode");
if (agentModeEl) {
  agentModeEl.addEventListener("change", () => setPlanBanner(agentModeEl.value === "plan"));
}

document.addEventListener("keydown", (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); cmdPaletteEl.hidden ? openCmd() : closeCmd(); return; }
  if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); if (!cmdPaletteEl.hidden) return; if (editorWrapEl.style.display !== "none") openFind(false); else openChatSearch(); return; }
  if (cmdPaletteEl.hidden) {
    if (mod && e.shiftKey && e.key.toLowerCase() === "o") { e.preventDefault(); createTab(); return; }
    if (mod && e.key.toLowerCase() === "n") { e.preventDefault(); if (btnNew) btnNew.click(); return; }
    if (mod && e.key.toLowerCase() === "l") { e.preventDefault(); const b = $("btn-clear"); if (b) b.click(); return; }
    if (mod && e.key.toLowerCase() === "e") { e.preventDefault(); triggerReviewNow(); return; }
    if (mod && e.key === "/") { e.preventDefault(); openCmd(); cmdInputEl.value = "快捷键"; filterCmd("快捷键"); return; }
    if (editorWrapEl && editorWrapEl.style.display !== "none") {
      if (mod && e.key.toLowerCase() === "g") { e.preventDefault(); openGoto(); return; }
      if (mod && e.key.toLowerCase() === "h") { e.preventDefault(); openFind(true); return; }
      if (!mod && e.altKey && e.key.toLowerCase() === "z") { e.preventDefault(); toggleWrap(); return; }
    }
    return;
  }
  if (e.key === "Escape") { closeCmd(); toggleSidebar(false); return; }
  if (e.key === "ArrowDown") { e.preventDefault(); cmdActive = Math.min(cmdActive + 1, cmdFiltered.length - 1); markCmd(); scrollCmd(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); cmdActive = Math.max(cmdActive - 1, 0); markCmd(); scrollCmd(); }
  else if (e.key === "Enter") { e.preventDefault(); const c = cmdFiltered[cmdActive]; if (c) execCmd(c); }
});

// ===== 主题 F: 专家/技能 提示词增强 选择器 =====
(function setupEnhance() {
  const modal = document.getElementById("enhance-modal");
  if (!modal) return;
  const btnOpen = document.getElementById("btn-enhance");
  const btnClose = document.getElementById("enhance-close");
  const btnApply = document.getElementById("enhance-apply");
  const btnClear = document.getElementById("enhance-clear");
  const expBox = document.getElementById("enhance-experts");
  const sklBox = document.getElementById("enhance-skills");
  let lib = { experts: [], skills: [] };

  async function loadLib() {
    try { const r = await fetch("/api/enhance"); lib = await r.json(); }
    catch { lib = { experts: [], skills: [] }; }
  }
  function optHtml(items, kind) {
    const sel = getEnhanceSel();
    const chosen = new Set(kind === "experts" ? sel.experts : sel.skills);
    return (items || []).map(it => `
      <label class="enh-opt">
        <input type="checkbox" data-kind="${kind}" value="${it.name.replace(/"/g, "&quot;")}" ${chosen.has(it.name) ? "checked" : ""}>
        <span><span class="nm">${it.name.replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}</span>
        ${it.description ? `<div class="ds">${it.description.replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}</div>` : ""}</span>
      </label>`).join("") || `<div class="ds" style="padding:8px 0">暂无，去「专家·技能」页添加</div>`;
  }
  async function open() {
    await loadLib();
    expBox.innerHTML = optHtml(lib.experts, "experts");
    sklBox.innerHTML = optHtml(lib.skills, "skills");
    modal.hidden = false;
  }
  function collect() {
    const exp = [...expBox.querySelectorAll("input:checked")].map(i => i.value);
    const skl = [...sklBox.querySelectorAll("input:checked")].map(i => i.value);
    return { experts: exp, skills: skl };
  }
  btnOpen.addEventListener("click", open);
  btnClose.addEventListener("click", () => { modal.hidden = true; });
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
  btnClear.addEventListener("click", () => {
    expBox.querySelectorAll("input").forEach(i => i.checked = false);
    sklBox.querySelectorAll("input").forEach(i => i.checked = false);
    setEnhanceSel({ experts: [], skills: [] }); renderEnhanceChips();
  });
  btnApply.addEventListener("click", () => {
    setEnhanceSel(collect()); renderEnhanceChips(); modal.hidden = true;
  });
  renderEnhanceChips();
})();

// ===== 提示词模板 选择器 (插入到对话输入框) =====
(function setupTemplates() {
  const modal = document.getElementById("template-modal");
  if (!modal) return;
  const btnOpen = document.getElementById("btn-template");
  const btnClose = document.getElementById("template-close");
  const listBox = document.getElementById("template-list");
  const input = document.getElementById("input");

  function esc(s){ return (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }

  function insertAtCursor(text) {
    if (!input) return;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    const before = input.value.slice(0, start);
    const after = input.value.slice(end);
    const sep = (before && !before.endsWith("\n")) ? "\n" : "";
    input.value = before + sep + text + after;
    const pos = (before + sep + text).length;
    input.focus();
    input.setSelectionRange(pos, pos);
  }

  async function open() {
    let tpls = [];
    try { const r = await fetch("/api/templates"); const d = await r.json(); tpls = d.templates || []; }
    catch { tpls = []; }
    if (!tpls.length) {
      listBox.innerHTML = `<div class="ds" style="padding:10px 0">暂无模板。去「📋 提示词模板」页新建。</div>`;
    } else {
      listBox.innerHTML = tpls.map(t => `
        <div class="tpl-pick" data-id="${t.id}" style="background:rgba(124,92,255,.06);border:1px solid var(--line);
             border-radius:10px;padding:10px 12px;cursor:pointer">
          <div style="font-weight:600;font-size:13px">${esc(t.name)}
            <span style="color:var(--acc);font-size:11px;border:1px solid rgba(124,92,255,.25);
              border-radius:999px;padding:0 7px;margin-left:6px">${esc(t.category||"其他")}</span></div>
          <div style="color:var(--mut);font-size:12px;margin-top:4px;max-height:54px;overflow:hidden;
            white-space:pre-wrap;font-family:monospace">${esc((t.content||"").slice(0,200))}</div>
        </div>`).join("");
      listBox.querySelectorAll(".tpl-pick").forEach(el => {
        el.addEventListener("click", () => {
          const t = tpls.find(x => x.id === el.dataset.id);
          if (t) insertAtCursor(t.content || "");
          modal.hidden = true;
        });
      });
    }
    modal.hidden = false;
  }
  btnOpen.addEventListener("click", open);
  btnClose.addEventListener("click", () => { modal.hidden = true; });
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
})();

// —— 代码片段插入选择器 (📎 片段) ——
(function(){
  const modal = document.getElementById("snippet-modal");
  if (!modal) return;
  const btnOpen = document.getElementById("btn-snippet");
  const btnClose = document.getElementById("snippet-close");
  const listBox = document.getElementById("snippet-list");
  const input = document.getElementById("input");

  function esc(s){ return (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }

  function insertAtCursor(text) {
    if (!input) return;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    const before = input.value.slice(0, start);
    const after = input.value.slice(end);
    const sep = (before && !before.endsWith("\n")) ? "\n" : "";
    input.value = before + sep + text + after;
    const pos = (before + sep + text).length;
    input.focus();
    input.setSelectionRange(pos, pos);
  }

  async function open() {
    let snips = [];
    try { const r = await fetch("/api/snippets"); const d = await r.json(); snips = d.snippets || []; }
    catch { snips = []; }
    if (!snips.length) {
      listBox.innerHTML = `<div class="ds" style="padding:10px 0">暂无片段。去「📎 代码片段」页新建。</div>`;
    } else {
      listBox.innerHTML = snips.map(s => `
        <div class="tpl-pick" data-id="${s.id}" style="background:rgba(124,92,255,.06);border:1px solid var(--line);
             border-radius:10px;padding:10px 12px;cursor:pointer">
          <div style="font-weight:600;font-size:13px">${esc(s.title)}
            <span style="color:var(--code,#ffd479);font-size:11px;border:1px solid rgba(255,212,121,.25);
              border-radius:999px;padding:0 7px;margin-left:6px">${esc(s.language||"其他")}</span></div>
          <div style="color:var(--mut);font-size:12px;margin-top:4px;max-height:54px;overflow:hidden;
            white-space:pre;font-family:monospace">${esc((s.content||"").slice(0,200))}</div>
        </div>`).join("");
      listBox.querySelectorAll(".tpl-pick").forEach(el => {
        el.addEventListener("click", () => {
          const s = snips.find(x => x.id === el.dataset.id);
          if (s) insertAtCursor(s.content || "");
          modal.hidden = true;
        });
      });
    }
    modal.hidden = false;
  }
  btnOpen.addEventListener("click", open);
  btnClose.addEventListener("click", () => { modal.hidden = true; });
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
})();

/* 分组可折叠导航：折叠/展开交互 + 当前页自动展开 + 状态持久化 */
(function setupNavGroups() {
  const KEY = "lmw_nav_state";
  const groups = Array.from(document.querySelectorAll(".nav-group"));
  if (!groups.length) return;
  const cur = location.pathname;
  let activeGroup = null;
  groups.forEach(g => {
    const link = g.querySelector('.nav-item[href="' + cur + '"]');
    if (link) { link.classList.add("active"); activeGroup = g; }
  });
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { saved = null; }
  function save() {
    const st = {};
    groups.forEach(g => { st[g.dataset.group] = g.classList.contains("collapsed") ? "closed" : "open"; });
    try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {}
  }
  groups.forEach(g => {
    let open;
    if (saved && (g.dataset.group in saved)) open = saved[g.dataset.group] !== "closed";
    else if (activeGroup) open = (g === activeGroup);
    else open = true;
    g.classList.toggle("collapsed", !open);
    const head = g.querySelector(".nav-group-head");
    if (head) head.setAttribute("aria-expanded", open ? "true" : "false");
  });
  groups.forEach(g => {
    const head = g.querySelector(".nav-group-head");
    if (!head) return;
    head.addEventListener("click", () => {
      const willOpen = g.classList.contains("collapsed");
      g.classList.toggle("collapsed", !willOpen);
      head.setAttribute("aria-expanded", String(willOpen));
      save();
    });
  });
})();

// ===================================================================
// 上下文操作工具栏: 压缩 / 整理 / 拆解 当前会话上下文
// ===================================================================
(function () {
  const $ = (s) => document.querySelector(s);
  const ctxSel = $("#ctx-session");
  const modal = $("#ctx-modal");
  let lastMd = "";

  async function loadCtxSessions() {
    try {
      const r = await fetch("/api/sessions");
      const d = await r.json();
      ctxSel.innerHTML = "";
      (d.sessions || []).forEach((s) => {
        const o = document.createElement("option");
        o.value = s.id;
        const sum = (s.summary || s.id || "").slice(0, 40);
        o.textContent = (sum || s.id) + " · " + (s.messages || 0) + "条";
        ctxSel.appendChild(o);
      });
      if (!ctxSel.options.length) {
        const o = document.createElement("option");
        o.value = "";
        o.textContent = "(无历史会话)";
        ctxSel.appendChild(o);
      }
    } catch (e) { /* 忽略 */ }
  }

  async function runCtxOp(kind) {
    const sid = ctxSel.value;
    if (!sid) { alert("请先在左侧选择一个会话"); return; }
    try {
      const r = await fetch("/api/context/" + kind, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid }),
      });
      const d = await r.json();
      if (d.error) { alert(d.error); return; }
      lastMd = d.markdown || "";
      $("#ctx-result").innerHTML = renderCtxMarkdown(lastMd);
      $("#ctx-modal-title").textContent = {
        compress: "🗜 上下文压缩报告",
        organize: "🗂 上下文整理笔记",
        decompose: "🧩 上下文任务拆解",
      }[kind] || "上下文结果";
      modal.hidden = false;
    } catch (e) { alert("操作失败: " + e); }
  }

  $("#ctx-compress").onclick = () => runCtxOp("compress");
  $("#ctx-organize").onclick = () => runCtxOp("organize");
  $("#ctx-decompose").onclick = () => runCtxOp("decompose");
  $("#ctx-modal-close").onclick = () => { modal.hidden = true; };
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
  $("#ctx-copy").onclick = async () => { try { await navigator.clipboard.writeText(lastMd); } catch (e) {} };
  $("#ctx-to-memory").onclick = async () => {
    try {
      await fetch("/api/memory", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "append", title: "上下文操作结果", content: lastMd }),
      });
      alert("已存入记忆中枢");
    } catch (e) { alert("失败: " + e); }
  };
  $("#ctx-to-plan").onclick = async () => {
    const title = prompt("计划书标题:", "上下文拆解计划");
    if (!title) return;
    try {
      await fetch("/api/plans", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title, content: lastMd, status: "todo" }),
      });
      if (confirm("已存入计划书, 前往查看?")) location.href = "/plans";
    } catch (e) { alert("失败: " + e); }
  };
  loadCtxSessions();
})();

/* ============================ 主题切换 (编码/音频/图片/视频) ============================ */
(function () {
  const THEMES = ["code", "audio", "image", "video"];
  const KEY = "lmw_theme";
  const switcher = document.getElementById("theme-switcher");
  function current() {
    const t = document.documentElement.getAttribute("data-theme");
    return THEMES.indexOf(t) >= 0 ? t : "code";
  }
  function paint(theme) {
    if (!switcher) return;
    switcher.querySelectorAll(".theme-btn").forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-theme") === theme);
    });
  }
  window.setTheme = function (theme) {
    if (THEMES.indexOf(theme) < 0) return;
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem(KEY, theme); } catch (e) {}
    paint(theme);
    const meta = { code: "编码", audio: "音频", image: "图片", video: "视频" };
    toast("已切换主题 · " + (meta[theme] || theme), "ok");
  };
  if (switcher) {
    switcher.querySelectorAll(".theme-btn").forEach((b) => {
      b.addEventListener("click", () => window.setTheme(b.getAttribute("data-theme")));
    });
  }
  // 初始高亮 + 跨标签页同步
  paint(current());
  window.addEventListener("storage", (e) => {
    if (e.key === KEY && e.newValue) { document.documentElement.setAttribute("data-theme", e.newValue); paint(e.newValue); }
  });
})();

