"use strict";

const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const inputEl = $("input");
const sendBtn = $("send");
const statusEl = $("status");
const toolListEl = $("tool-list");
const providerListEl = $("provider-list");
const taskListEl = $("task-list");
const taskCountEl = $("task-count");
const taskPromptEl = $("task-prompt");
const taskProviderEl = $("task-provider");
const taskAddBtn = $("task-add");
const orchPromptsEl = $("orch-prompts");
const orchRunBtn = $("orch-run");
const orchProgressEl = $("orch-progress");
const orchFillEl = $("orch-fill");
const orchStatEl = $("orch-stat");

const activeOrchs = [];  // 进行中的编排 id (轮询聚合进度)
let orchTimer = null;

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

function scrollDown() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addMessage(role) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + (role === "user" ? "user" : "agent");
  const tag = document.createElement("div");
  tag.className = "role-tag";
  tag.textContent = role === "user" ? "你" : "灵梦";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  wrap.appendChild(tag);
  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  scrollDown();
  return bubble;
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
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = `${p.name} (${p.model})`;
      taskProviderEl.appendChild(opt);
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

// ---------- 单路对话 (兼容旧能力) ----------
async function send() {
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  const modeSel = $("agent-mode");
  const submitMode = (modeSel && modeSel.value) || "bypassPermissions";
  const modeLabel = { bypassPermissions: "全放开", acceptEdits: "接受编辑", plan: "计划" }[submitMode] || submitMode;
  const userBubble = addMessage("user");
  userBubble.textContent = "【" + modeLabel + "】 " + text;
  history.push({ role: "user", content: text });

  const agentBubble = addMessage("agent");
  agentBubble.classList.add("typing");
  agentBubble.textContent = "";
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
      body: JSON.stringify({ message: text, session_id: currentSessionId, history: history.slice(0, -1), mode: submitMode }),
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
          // 后端已落盘会话, 记录以便「恢复此会话」复用
          currentSessionId = evt.session_id;
        }
        if (evt.type === "error") {
          toast("后端执行异常: " + (evt.message || "未知错误"), "error");
        }
      }
    }
    // plan 模式: 方案生成后展示确认卡片
    if (submitMode === "plan" && acc.trim()) {
      showPlanCard(text, acc);
    }
  } catch (e) {
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
    history.push({ role: "assistant", content: acc });
    scrollDown();
  }
}

function handleEvent(evt, narr, toolsBox, chainStrip, addText) {
  if (evt.type === "text") {
    addText(evt.chunk);
    narr.textContent += evt.chunk;
  } else if (evt.type === "tool") {
    const call = document.createElement("details");
    call.className = "tool-call";
    const args = Object.entries(evt.args || {})
      .map(([k, v]) => `${k}=${v}`)
      .join(", ");
    call.innerHTML = `<summary class="tc-head">⚙ ${esc(evt.name)}(${esc(args.slice(0, 120))})</summary>`;
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
    }
    // 链路节点: 完成态染色 (ok / fail)
    if (chainStrip && last && last._chainNode) {
      const n = last._chainNode;
      n.classList.remove("running");
      n.classList.add(evt.ok === false ? "fail" : "ok");
      n.title = (evt.kind || "other") + " · " + (evt.ok === false ? "执行失败" : "完成");
    }
  } else if (evt.type === "done") {
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

// ---------- 计划模式: 方案确认卡片 ----------
function showPlanCard(originalPrompt, planText) {
  const card = document.createElement("div");
  card.className = "plan-card";
  card.innerHTML = `
    <div class="plan-head">📋 计划方案 (只读探查完成, 尚未改动任何文件)</div>
    <pre class="plan-body"></pre>
    <div class="plan-actions">
      <button class="btn-primary plan-run">确认执行</button>
      <button class="btn-ghost plan-dismiss">忽略</button>
    </div>`;
  card.querySelector(".plan-body").textContent = planText;
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
}

async function executeMode(text, mode) {
  const agentBubble = addMessage("agent");
  agentBubble.classList.add("typing");
  agentBubble.textContent = "";
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
      body: JSON.stringify({ message: text, session_id: currentSessionId, history: history.slice(), mode: mode }),
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
        if (evt.type === "done" && evt.session_id) currentSessionId = evt.session_id;
        if (evt.type === "error") toast("后端执行异常: " + (evt.message || ""), "error");
      }
    }
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

// ---------- 多路任务面板 ----------
function taskCardEl(task) {
  let card = $(`task-${task.id}`);
  if (!card) {
    card = document.createElement("div");
    card.id = `task-${task.id}`;
    card.className = "task-card";
    card.innerHTML = `
      <div class="tc-top">
        <span class="tc-id">#${esc(task.id)}</span>
        <span class="tc-provider">${esc(task.provider || "?")}</span>
        <span class="tc-meta"></span>
        <span class="tc-status">${esc(task.status)}</span>
      </div>
      <div class="tc-prompt">${esc(task.prompt)}</div>
      <div class="tc-narr"></div>
      <div class="tc-tools"></div>
      <div class="tc-foot">
        <button class="btn-ghost tc-export">导出</button>
        <button class="btn-ghost tc-del">删除</button>
      </div>`;
    card.querySelector(".tc-del").addEventListener("click", () => deleteTask(task.id));
    card.querySelector(".tc-export").addEventListener("click", () => exportTask(task.id));
    taskListEl.prepend(card);
  }
  card.querySelector(".tc-status").textContent = task.status;
  const meta = card.querySelector(".tc-meta");
  if (meta) {
    const it = task.iterations || 0;
    const tc = task.tool_calls || 0;
    meta.textContent = `迭代 ${it} · 工具 ${tc}`;
  }
  card.className = "task-card status-" + task.status;
  return card;
}

function exportTask(id) {
  fetch(`/api/tasks/${id}`)
    .then((r) => r.json())
    .then((t) => {
      if (!t || t.error) { alert("任务不存在"); return; }
      let md = `# 灵梦work 任务导出\n\n`;
      md += `- ID: ${t.id}\n- 通道: ${t.provider || "?"}\n- 模型: ${t.model || "?"}\n`;
      md += `- 状态: ${t.status}\n- 迭代: ${t.iterations || 0} · 工具调用: ${t.tool_calls || 0}\n`;
      md += `- 时间: ${new Date((t.created_at || 0) * 1000).toLocaleString()}\n\n`;
      md += `## 任务指令\n\n${t.prompt}\n\n`;
      md += `## 执行记录\n\n`;
      (t.events || []).forEach(([type, kw]) => {
        if (type === "text") md += kw.chunk || "";
        else if (type === "tool") md += `\n\n**🔧 ${kw.name}**\n\`\`\`\n${Object.entries(kw.args || {}).map(([k, v]) => `${k}=${v}`).join("\n")}\n\`\`\`\n`;
        else if (type === "tool_result") md += `\n> 结果: ${String(kw.output || "").slice(0, 1500)}\n`;
        else if (type === "done") md += `\n\n---\n*任务结束${kw.truncated ? " (达最大迭代)" : ""}*\n`;
      });
      const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `lingmeng-task-${t.id}.md`;
      a.click();
      URL.revokeObjectURL(a.href);
    })
    .catch((e) => alert("导出失败: " + e));
}

function upsertTask(task) {
  taskCardEl(task);
  refreshTaskCount();
}

async function refreshTaskCount() {
  try {
    const r = await fetch("/api/tasks");
    const d = await r.json();
    taskCountEl.textContent = (d.tasks || []).length;
  } catch (e) {}
}

async function createTask() {
  const prompt = taskPromptEl.value.trim();
  if (!prompt) return;
  const provider = taskProviderEl.value || null;
  taskPromptEl.value = "";
  try {
    const r = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, provider }),
    });
    const snap = await r.json();
    if (snap.error) {
      alert("创建失败: " + snap.error);
      return;
    }
    upsertTask(snap);
    subscribeTask(snap.id);
  } catch (e) {
    alert("创建失败: " + e);
  }
}

function subscribeTask(id) {
  const card = $(`task-${id}`);
  const narr = card ? card.querySelector(".tc-narr") : null;
  const toolsBox = card ? card.querySelector(".tc-tools") : null;
  // 首事件前显示 spinner, 明确「任务已提交, 等待首输出」
  let spinner = null;
  if (narr && !narr.textContent.trim()) {
    spinner = makeSpinner("任务已提交, 等待首输出…");
    narr.appendChild(spinner);
  }
  let firstEvent = false;
  const es = new EventSource(`/api/tasks/${id}/stream`);
  es.onmessage = (ev) => {
    let evt;
    try { evt = JSON.parse(ev.data); } catch { return; }
    if (!firstEvent) {
      firstEvent = true;
      if (spinner && spinner.parentNode) spinner.remove();
    }
    if (evt.type === "text" && narr) narr.textContent += evt.chunk;
    else if (evt.type === "tool" && toolsBox) {
      const call = document.createElement("details");
      call.className = "tool-call";
      const args = Object.entries(evt.args || {}).map(([k, v]) => `${k}=${v}`).join(", ");
      call.innerHTML = `<summary class="tc-head">⚙ ${esc(evt.name)}(${esc(args.slice(0, 120))})</summary>`;
      const out = document.createElement("div");
      out.className = "tc-out";
      out.textContent = "执行中…";
      call.appendChild(out);
      toolsBox.appendChild(call);
      call._out = out;
    } else if (evt.type === "tool_result" && toolsBox) {
      const last = toolsBox.lastElementChild;
      if (last && last._out) {
        last._out.textContent = String(evt.output || "").slice(0, 2000);
        last.setAttribute("open", "");
        if (evt.name === "auto_test") {
          last._out.classList.add(/✅|全部通过/.test(evt.output || "") ? "ok" : "bad");
        }
      }
    } else if (evt.type === "status") {
      const c = $(`task-${id}`);
      if (c) c.querySelector(".tc-status").textContent = evt.status;
      // 任务结束时重新拉快照, 刷新迭代/工具数
      fetch(`/api/tasks/${id}`).then((r) => r.json()).then((t) => upsertTask(t)).catch(() => {});
      refreshTaskCount();
    } else if (evt.type === "close") {
      es.close();
    }
  };
  let retried = false;
  es.onerror = () => {
    // EventSource 自动重连; 若长时间连不上给提示
    if (!firstEvent && !retried) {
      retried = true;
      toast(`任务 #${id} 流连接中断, 正在尝试重连…`, "warn");
    }
    // 不强制 close: 浏览器会自动重连; 但若已 firstEvent 且任务已结束则安全关闭
    if (firstEvent) es.close();
  };
}

async function deleteTask(id) {
  try {
    await fetch(`/api/tasks/${id}`, { method: "DELETE" });
  } catch (e) {}
  const c = $(`task-${id}`);
  if (c) c.remove();
  refreshTaskCount();
}

async function loadTasks() {
  try {
    const r = await fetch("/api/tasks");
    const d = await r.json();
    (d.tasks || []).forEach((t) => upsertTask(t));
  } catch (e) {}
}

// ---------- 并行编排 (扇出多路并发任务 + 聚合看板) ----------
async function runOrchestration() {
  const lines = (orchPromptsEl.value || "").split("\n").map((s) => s.trim()).filter(Boolean);
  if (!lines.length) {
    toast("请至少填写一行任务", "warn");
    return;
  }
  orchPromptsEl.value = "";
  orchProgressEl.style.display = "flex";
  try {
    const r = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompts: lines }),
    });
    const d = await r.json();
    if (d.error) {
      toast("编排失败: " + d.error, "warn");
      return;
    }
    (d.tasks || []).forEach((t) => {
      upsertTask(t);
      subscribeTask(t.id);
    });
    const oid = d.orchestration_id;
    if (oid && !activeOrchs.includes(oid)) activeOrchs.push(oid);
    toast(`⚡ 已扇出 ${lines.length} 路并行任务`, "info");
    startOrchPolling();
    refreshOrchestrations();
  } catch (e) {
    toast("编排失败: " + e, "warn");
  }
}

async function refreshOrchestrations() {
  if (!activeOrchs.length) return;
  let stillActive = [];
  for (const oid of activeOrchs) {
    try {
      const r = await fetch(`/api/orchestrations/${oid}`);
      const agg = await r.json();
      if (!agg || agg.error) continue;
      const done = agg.done || 0;
      const total = agg.total || 0;
      const pct = total ? Math.round((done / total) * 100) : 0;
      orchFillEl.style.width = pct + "%";
      orchStatEl.textContent =
        `完成 ${done}/${total} · 运行 ${agg.running || 0} · 失败 ${agg.error || 0}` +
        ` · Token ${agg.est_tokens || 0} · ¥${(agg.est_cost_cny || 0).toFixed(5)}`;
      if (agg.status === "done") {
        toast(`✅ 编排 ${oid} 全部完成 (${total} 路)`, "info");
      } else {
        stillActive.push(oid);
      }
    } catch (e) {
      stillActive.push(oid);
    }
  }
  // 仅保留仍在跑的; 全完成则隐藏进度条
  activeOrchs.length = 0;
  activeOrchs.push(...stillActive);
  if (!activeOrchs.length) {
    orchProgressEl.style.display = "none";
    if (orchTimer) {
      clearInterval(orchTimer);
      orchTimer = null;
    }
  }
}

function startOrchPolling() {
  if (orchTimer) return;
  orchTimer = setInterval(refreshOrchestrations, 1500);
}

// ---------- Tab 切换 ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`panel-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "tasks") { loadTasks(); if (activeOrchs.length) refreshOrchestrations(); }
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
taskAddBtn.addEventListener("click", createTask);
taskPromptEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); createTask(); }
});
orchRunBtn.addEventListener("click", runOrchestration);
orchPromptsEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runOrchestration(); }
});
reviewBackBtn.addEventListener("click", () => {
  reviewDetailEl.style.display = "none";
  reviewListEl.style.display = "block";
  loadReviews();
});
$("btn-clear").addEventListener("click", () => {
  messagesEl.innerHTML = "";
  history = [];
});

loadHealth();
loadProviders();
loadTools();
loadMcp();
refreshTaskCount();
refreshDashboard();
setInterval(refreshDashboard, 5000);   // 仪表盘每 5s 刷新
setInterval(refreshTaskCount, 5000);

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
      resultListEl.innerHTML = '<div class="empty">暂无已落盘任务 (运行多路任务后自动保存)</div>';
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

async function showFileContent(rel) {
  try {
    const r = await fetch("/api/fs/read?path=" + encodeURIComponent(rel));
    const d = await r.json();
    if (d.error) { toast(d.error, "error"); return; }
    if (d.binary) {
      fileContentEl.textContent = "🔒 " + (d.hint || "二进制文件, 不可预览");
      fileContentEl.style.display = "block";
      editorWrapEl.style.display = "none";
      toast("二进制文件不支持编辑", "warn");
      return;
    }
    // 打开编辑器
    currentEditPath = rel;
    editorFileEl.textContent = rel + (d.truncated ? "  (已截断显示前 20 万字符)" : "");
    editorTextEl.value = d.content || "";
    editorWrapEl.style.display = "block";
    fileContentEl.style.display = "none";
    fileSaveStateEl.textContent = "";
    syncGutter();
    highlightCode();
    editorTextEl.scrollTop = 0;
    editorTextEl.focus();
  } catch (e) {
    toast("读取失败: " + e, "error");
  }
}

// 行号栏与文本区同步
function syncGutter() {
  const lines = editorTextEl.value.split("\n").length || 1;
  let s = "";
  for (let i = 1; i <= lines; i++) s += i + "\n";
  editorGutterEl.textContent = s;
  editorGutterEl.scrollTop = editorTextEl.scrollTop;
}
editorTextEl.addEventListener("input", syncGutter);
editorTextEl.addEventListener("scroll", () => { editorGutterEl.scrollTop = editorTextEl.scrollTop; editorHlEl.scrollTop = editorTextEl.scrollTop; });
editorTextEl.addEventListener("input", highlightCode);

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
// Tab 键插入 4 空格 (不跳出文本区)
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
  }
});

async function saveFile() {
  if (!currentEditPath) return;
  editorSaveBtn.disabled = true;
  try {
    const r = await fetch("/api/fs/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: currentEditPath, content: editorTextEl.value }),
    });
    const d = await r.json();
    if (d.error) { toast(d.error, "error"); fileSaveStateEl.textContent = "✗ 保存失败"; return; }
    fileSaveStateEl.textContent = "✓ 已保存 " + d.bytes + " 字节";
    toast("已保存 " + currentEditPath, "ok");
  } catch (e) {
    toast("保存失败: " + e, "error");
    fileSaveStateEl.textContent = "✗ 保存失败";
  } finally {
    editorSaveBtn.disabled = false;
  }
}

async function reloadFile() {
  if (!currentEditPath) return;
  const cur = currentEditPath;
  await showFileContent(cur);
  toast("已重载 " + cur, "ok");
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
    document.querySelector('.tab[data-tab="chat"]').click();
    scrollDown();
  } catch (e) {
    alert("恢复失败: " + e);
  }
});

