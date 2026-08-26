/* 子页「同级功能」快捷切换条
 * 自包含：注入自身样式，按当前路径渲染同组功能芯片，一键互跳。
 * 与 index.html 的三个分组保持一致，保证「相似功能」在任意子页都能直达。 */
(function () {
  try {
    var GROUPS = {
      workspace: [
        { href: "/notes",      ico: "📝", label: "笔记" },
        { href: "/todos",      ico: "✅", label: "待办清单" },
        { href: "/snippets",   ico: "📎", label: "代码片段" },
        { href: "/templates",  ico: "📋", label: "提示词模板" },
        { href: "/enhance",    ico: "🧠", label: "专家·技能" }
      ],
      security: [
        { href: "/secrets",    ico: "🔐", label: "密钥保险箱" },
        { href: "/backups",    ico: "📦", label: "备份与回滚" },
        { href: "/sandbox",    ico: "🛡️", label: "工作区沙箱" },
        { href: "/settings",   ico: "⚙️", label: "设置中心" }
      ],
      ops: [
        { href: "/observability", ico: "📊", label: "可观测仪表盘" },
        { href: "/cost",          ico: "💰", label: "成本看板" },
        { href: "/planboard",     ico: "🗂️", label: "计划看板" }
      ]
    };

    var holder = document.getElementById("sibnav");
    if (!holder) return;

    var path = location.pathname;
    var curGroup = null;
    Object.keys(GROUPS).forEach(function (k) {
      GROUPS[k].forEach(function (it) { if (it.href === path) curGroup = k; });
    });
    if (!curGroup) return; // 非分组页，不渲染

    var style = document.createElement("style");
    style.textContent =
      ".sibnav{display:flex;align-items:center;gap:10px;flex-wrap:wrap;" +
      "padding:9px 22px;border-bottom:1px solid var(--line,rgba(126,146,210,.16));" +
      "background:rgba(10,14,26,.5)}" +
      ".sibnav-label{font-size:11px;color:var(--mut,#828caa);letter-spacing:.6px;" +
      "font-weight:700;text-transform:uppercase;white-space:nowrap}" +
      ".sib-chips{display:flex;gap:8px;flex-wrap:wrap}" +
      ".sib-chip{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border-radius:999px;" +
      "font-size:13px;text-decoration:none;color:var(--txt,#e9edf8);" +
      "background:var(--card,rgba(20,27,48,.62));border:1px solid var(--line,rgba(126,146,210,.16));" +
      "transition:all .15s ease;white-space:nowrap}" +
      ".sib-chip:hover{border-color:var(--acc,#7c5cff);transform:translateY(-1px);" +
      "box-shadow:0 6px 18px rgba(124,92,255,.25)}" +
      ".sib-chip.active{color:#fff;border-color:transparent;" +
      "background:linear-gradient(135deg,#7c5cff,#5b8bff);box-shadow:0 6px 20px rgba(124,92,255,.35);cursor:default}" +
      ".sib-chip .si{font-size:13px}";
    document.head.appendChild(style);

    holder.className = "sibnav";
    var label = document.createElement("span");
    label.className = "sibnav-label";
    label.textContent = "同级功能";
    var chips = document.createElement("div");
    chips.className = "sib-chips";
    GROUPS[curGroup].forEach(function (it) {
      var a = document.createElement("a");
      a.className = "sib-chip" + (it.href === path ? " active" : "");
      a.href = it.href;
      a.innerHTML = '<span class="si">' + it.ico + "</span><span>" + it.label + "</span>";
      chips.appendChild(a);
    });
    holder.appendChild(label);
    holder.appendChild(chips);
  } catch (e) { /* 静默失败，不影响原页面 */ }
})();
