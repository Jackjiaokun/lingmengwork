/* 灵梦work · 全局命令面板 (commands.js, Phase 82)
 * 唤起: Cmd+K / Ctrl+K  关闭: Esc / 点遮罩   选择: ↑↓   执行: Enter
 * 命令源: ① window.LMW_NAV (sidebar.js 暴露的全站导航, 单一事实源) ② 内置动作
 * 用法: 页面 </body> 前 <script src="/static/commands.js"></script>
 * 对外: window.LMW.cmd = { open, close, toggle, register, all, version }
 * 设计: 零依赖 IIFE; 全部操作 try 包裹, 任何异常都不影响宿主页面
 */
(function () {
  var built = false, cmds = [], filtered = [], sel = 0, dom = {};

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;";
    });
  }

  /* 模糊匹配: 连续子串优先(分数高), 其次按顺序的子序列; 返回 {score, ranges} 或 null */
  function match(q, text) {
    var s = String(text == null ? "" : text).toLowerCase();
    q = String(q == null ? "" : q).toLowerCase().trim();
    if (!q) return { score: 0, ranges: [] };
    var i = s.indexOf(q);
    if (i >= 0) return { score: 1000 - i * 5, ranges: [[i, i + q.length]] };
    var ranges = [], qi = 0, start = -1, last = -1, k;
    for (k = 0; k < s.length && qi < q.length; k++) {
      if (s.charAt(k) === q.charAt(qi)) {
        if (start < 0) start = k;
        last = k; qi++;
        if (qi === q.length) ranges.push([start, last + 1]);
      } else if (start >= 0) {
        ranges.push([start, last + 1]); start = -1; last = -1;
      }
    }
    if (qi < q.length) return null;
    return { score: 300 - (ranges.length ? ranges[0][0] : 0), ranges: ranges };
  }

  function highlight(text, ranges) {
    text = String(text == null ? "" : text);
    if (!ranges || !ranges.length) return esc(text);
    var out = "", pos = 0, i;
    for (i = 0; i < ranges.length; i++) {
      var r = ranges[i];
      out += esc(text.slice(pos, r[0])) + "<mark>" + esc(text.slice(r[0], r[1])) + "</mark>";
      pos = r[1];
    }
    return out + esc(text.slice(pos));
  }

  function go(href) { try { location.href = href; } catch (e) {} }

  function toggleTheme() {
    try {
      var html = document.documentElement;
      var t = html.getAttribute("data-theme") === "light" ? "dark" : "light";
      if (t === "light") html.setAttribute("data-theme", "light");
      else html.removeAttribute("data-theme");
      try { localStorage.setItem("lmw_theme", t); } catch (e) {}
      var b = document.getElementById("lmwThemeBtn");
      if (b) b.textContent = t === "light" ? "🌙" : "☀️";
    } catch (e) {}
  }

  function register(c) { if (c && c.title) cmds.push(c); }

  /* 懒构建: 每次打开时重建, 保证拿到最新的 window.LMW_NAV (sidebar.js 可能晚于本脚本执行) */
  function build() {
    cmds = [];
    var nav = [];
    try { nav = window.LMW_NAV || []; } catch (e) { nav = []; }
    var i, j;
    for (i = 0; i < nav.length; i++) {
      var g = nav[i] || {}, items = g.items || [];
      for (j = 0; j < items.length; j++) {
        (function (it, grp) {
          if (!it || !it.href) return;
          register({
            ico: it.ico || "·",
            title: it.label || it.href,
            sub: "前往 " + it.href,
            grp: "导航 · " + (grp || ""),
            kw: it.href + " " + (it.label || ""),
            run: function () { go(it.href); }
          });
        })(items[j], g.grp);
      }
    }
    register({ ico: "💬", title: "对话首页", sub: "前往 /", grp: "动作", kw: "/ chat 对话 home 首页", run: function () { go("/"); } });
    register({ ico: "🔄", title: "刷新当前页", sub: "reload", grp: "动作", kw: "reload refresh 刷新 重载", run: function () { try { location.reload(); } catch (e) {} } });
    register({ ico: "🌓", title: "切换深浅主题", sub: "light / dark", grp: "动作", kw: "theme light dark 主题 深浅 配色", run: toggleTheme });
    register({ ico: "📋", title: "复制当前页链接", sub: "copy url", grp: "动作", kw: "copy url link 复制 链接", run: function () { try { if (navigator.clipboard) navigator.clipboard.writeText(location.href); } catch (e) {} } });
  }

  function ensure() {
    if (built) return;
    built = true;
    try {
      var wrap = document.createElement("div");
      wrap.className = "lmw-cmdk";
      wrap.id = "lmwCmdk";
      wrap.setAttribute("hidden", "");
      wrap.innerHTML =
        '<div class="lmw-cmdk-bg" data-lmw-cmdk-close="1"></div>' +
        '<div class="lmw-cmdk-panel" role="dialog" aria-modal="true" aria-label="命令面板">' +
          '<div class="lmw-cmdk-head">' +
            '<input class="lmw-cmdk-input" id="lmwCmdkInput" type="text" placeholder="搜索页面或执行命令…" autocomplete="off" spellcheck="false">' +
            '<span class="lmw-cmdk-kbd">ESC</span>' +
          '</div>' +
          '<div class="lmw-cmdk-list" id="lmwCmdkList"></div>' +
          '<div class="lmw-cmdk-hint">↑↓ 选择 · Enter 执行 · Esc 关闭 · Cmd/Ctrl+K 唤起</div>' +
        '</div>';
      document.body.appendChild(wrap);
      dom.wrap = wrap;
      dom.bg = wrap.querySelector(".lmw-cmdk-bg");
      dom.input = wrap.querySelector(".lmw-cmdk-input");
      dom.list = wrap.querySelector(".lmw-cmdk-list");

      dom.input.addEventListener("input", function () { sel = 0; render(); });
      dom.list.addEventListener("click", function (e) {
        var t = e.target;
        while (t && t !== dom.list) {
          if (t.className && String(t.className).indexOf("lmw-cmdk-item") >= 0) {
            var idx = parseInt(t.getAttribute("data-i"), 10);
            if (!isNaN(idx)) run(idx);
            return;
          }
          t = t.parentNode;
        }
      });
      dom.bg.addEventListener("click", close);
    } catch (e) { built = false; }
  }

  function render() {
    if (!dom.list) return;
    var q = dom.input ? dom.input.value : "";
    var rows = [], i;
    for (i = 0; i < cmds.length; i++) {
      var c = cmds[i];
      var m = match(q, (c.title || "") + " " + (c.kw || "") + " " + (c.grp || ""));
      if (m) rows.push({ c: c, score: m.score });
    }
    rows.sort(function (a, b) { return b.score - a.score; });
    filtered = [];
    for (i = 0; i < rows.length; i++) filtered.push(rows[i].c);
    if (sel >= filtered.length) sel = 0;
    if (sel < 0) sel = 0;

    var html = "";
    if (!filtered.length) {
      html = '<div class="lmw-cmdk-empty">无匹配命令</div>';
    } else {
      for (i = 0; i < filtered.length; i++) {
        var cc = filtered[i];
        var mm = match(q, cc.title || "") || { ranges: [] };
        html += '<button type="button" class="lmw-cmdk-item' + (i === sel ? " on" : "") + '" data-i="' + i + '">' +
          '<span class="lmw-cmdk-ico">' + esc(cc.ico || "·") + '</span>' +
          '<span class="lmw-cmdk-txt"><b>' + highlight(cc.title, mm.ranges) + '</b>' +
          (cc.sub ? '<i>' + esc(cc.sub) + '</i>' : '') + '</span>' +
          '<span class="lmw-cmdk-grp">' + esc(cc.grp || '') + '</span>' +
        '</button>';
      }
    }
    dom.list.innerHTML = html;
    try {
      var on = dom.list.querySelector(".lmw-cmdk-item.on");
      if (on && on.scrollIntoView) on.scrollIntoView({ block: "nearest" });
    } catch (e) {}
  }

  function run(i) {
    var c = filtered[i];
    close();
    if (c && typeof c.run === "function") { try { c.run(); } catch (e) {} }
  }

  function move(d) {
    if (!filtered.length) return;
    sel = (sel + d + filtered.length) % filtered.length;
    render();
  }

  function open() {
    ensure();
    if (!dom.wrap) return;
    build();
    sel = 0;
    if (dom.input) dom.input.value = "";
    dom.wrap.removeAttribute("hidden");
    render();
    try { dom.input.focus(); } catch (e) {}
  }

  function close() {
    if (!dom.wrap) return;
    dom.wrap.setAttribute("hidden", "");
    try { if (dom.input) dom.input.blur(); } catch (e) {}
  }

  function isOpen() { return !!(dom.wrap && !dom.wrap.hasAttribute("hidden")); }
  function toggle() { if (isOpen()) close(); else open(); }

  function onKey(e) {
    try {
      var k = e.key;
      if ((e.metaKey || e.ctrlKey) && (k === "k" || k === "K")) {
        e.preventDefault();
        toggle();
        return;
      }
      if (!isOpen()) return;
      if (k === "Escape") { e.preventDefault(); close(); }
      else if (k === "ArrowDown") { e.preventDefault(); move(1); }
      else if (k === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (k === "Enter") { e.preventDefault(); run(sel); }
    } catch (err) {}
  }

  document.addEventListener("keydown", onKey);

  window.LMW = window.LMW || {};
  window.LMW.cmd = {
    open: open, close: close, toggle: toggle, register: register,
    all: function () { return cmds.slice(); },
    version: "82"
  };
})();
