/* 灵梦work · 右侧产物预览容器 (preview.js, Phase 80/81)
 * 仿 Cursor/Claude/v0/Devin 的右栏实时预览: 主区对话/产物列表 + 右侧 dock 渲染产物。
 * Phase 81: 升级为多产物分页签堆叠 (dedup by id) + 自动展开 + 页签关闭。
 * 支持类型:
 *   - html/htm  -> <iframe sandbox srcdoc> (本地生成产物可信, 允许同源+脚本)
 *   - 图片(png/jpg/gif/webp/svg/...) -> <img> (经 download 模式取二进制)
 *   - md/markdown -> 轻量 Markdown 渲染
 *   - 其它(代码/文本/json) -> <pre> 原文
 * 公共 API:
 *   LMWW.preview({path})                       // 按 outputs/superagent 内相对路径预览(入栈/聚焦)
 *   LMWW.preview({title, type, content})       // 直接渲染内容(type: html|md|text|image(+url))
 *   LMWW.togglePreview()                        // 显隐 dock
 *   LMWW.closePreview(id?)                      // 关页签(缺 id 关整个 dock)
 * 不依赖任何第三方库; 纯原生实现。
 */
(function () {
  var dock = document.getElementById("previewDock");
  var fab = document.getElementById("pdFab");
  var body = document.getElementById("pdBody");
  var nameEl = document.getElementById("pdName");
  var typeEl = document.getElementById("pdType");
  var btnNew = document.getElementById("pdNew");
  var btnClose = document.getElementById("pdClose");

  // Phase 81: 页签栏 (HTML 缺则自动建)
  var tabsEl = document.getElementById("pdTabs");
  if (!tabsEl && dock && body) {
    tabsEl = document.createElement("div");
    tabsEl.id = "pdTabs";
    tabsEl.className = "pd-tabs";
    dock.insertBefore(tabsEl, body);
  }

  var items = []; // {id, name, short, type, ext, isImg, kind:'file'|'content', path, url, content}
  var activeId = null;

  function escAttr(s) {
    return String(s).replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function escHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  var ICONS = {
    py: "🐍", js: "📜", sh: "🔧", json: "🔧", md: "📝", markdown: "📝",
    html: "🌐", htm: "🌐", png: "🖼️", jpg: "🖼️", jpeg: "🖼️", gif: "🖼️",
    webp: "🖼️", svg: "🖼️", bmp: "🖼️", ico: "🖼️"
  };

  function openDock() {
    dock.classList.add("open");
    dock.setAttribute("aria-hidden", "false");
    fab.classList.remove("show");
  }
  function closeDock() {
    dock.classList.remove("open");
    dock.setAttribute("aria-hidden", "true");
    fab.classList.add("show");
  }
  fab.addEventListener("click", function () { openDock(); renderActive(); });
  btnClose.addEventListener("click", closeDock);
  btnNew.addEventListener("click", function () {
    var it = activeItem();
    if (it && it.url) window.open(it.url, "_blank");
  });

  function setHeader(title, type) {
    nameEl.textContent = title || "产物预览";
    typeEl.textContent = type || "预览";
  }
  function empty(html) {
    body.innerHTML = '<div class="pd-empty">' + html + "</div>";
  }
  function fileUrl(path) {
    return "/api/superagent/artifacts/file?mode=download&path=" + encodeURIComponent(path);
  }
  function basename(p) {
    return String(p || "").split(/[\\/]/).pop();
  }
  function activeItem() {
    for (var i = 0; i < items.length; i++) if (items[i].id === activeId) return items[i];
    return null;
  }

  // ---------- 轻量 Markdown 渲染 (先转义, 再处理有限语法, 无外部依赖) ----------
  function inlineMd(t) {
    return t
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }
  function renderMd(src) {
    var esc = function (s) {
      return s.replace(/[&<>]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
      });
    };
    var out = "";
    var lines = (src || "").split(/\r?\n/);
    var i = 0;
    var inCode = false;
    var codeBuf = [];
    function flushCode() {
      if (codeBuf.length) {
        out += "<pre><code>" + esc(codeBuf.join("\n")) + "</code></pre>";
        codeBuf = [];
      }
    }
    while (i < lines.length) {
      var ln = lines[i];
      if (/^```/.test(ln)) {
        if (inCode) flushCode();
        inCode = !inCode;
        i++;
        continue;
      }
      if (inCode) {
        codeBuf.push(ln);
        i++;
        continue;
      }
      if (/^### /.test(ln)) { out += "<h3>" + esc(ln.slice(4)) + "</h3>"; i++; continue; }
      if (/^## /.test(ln)) { out += "<h2>" + esc(ln.slice(3)) + "</h2>"; i++; continue; }
      if (/^# /.test(ln)) { out += "<h1>" + esc(ln.slice(2)) + "</h1>"; i++; continue; }
      if (/^\s*[-*]\s+/.test(ln)) {
        var ul = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          ul.push("<li>" + inlineMd(esc(lines[i].replace(/^\s*[-*]\s+/, ""))) + "</li>");
          i++;
        }
        out += "<ul>" + ul.join("") + "</ul>";
        continue;
      }
      if (/^\s*\d+\.\s+/.test(ln)) {
        var ol = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          ol.push("<li>" + inlineMd(esc(lines[i].replace(/^\s*\d+\.\s+/, ""))) + "</li>");
          i++;
        }
        out += "<ol>" + ol.join("") + "</ol>";
        continue;
      }
      if (ln.trim() === "") { i++; continue; }
      out += "<p>" + inlineMd(esc(ln)) + "</p>";
      i++;
    }
    if (inCode) flushCode();
    return out;
  }

  function renderHtml(name, content) {
    setHeader(name, "HTML");
    var f = document.createElement("iframe");
    f.setAttribute("sandbox", "allow-scripts allow-same-origin");
    f.srcdoc = content;
    body.innerHTML = "";
    body.appendChild(f);
  }
  function renderText(name, content, ext) {
    setHeader(name, (ext || "text").toUpperCase());
    var pre = document.createElement("pre");
    pre.textContent = content || "";
    body.innerHTML = "";
    body.appendChild(pre);
  }
  function renderImageFromUrl(name, url) {
    setHeader(name, "图片");
    var img = document.createElement("img");
    img.alt = name;
    img.src = url;
    body.innerHTML = "";
    body.appendChild(img);
  }
  function renderMdInto(name, src) {
    setHeader(name, "Markdown");
    body.innerHTML = '<div class="md">' + renderMd(src) + "</div>";
  }

  function renderFileItem(it) {
    if (it.isImg) { renderImageFromUrl(it.name, it.url); return; }
    fetch("/api/superagent/artifacts/file?mode=preview&path=" + encodeURIComponent(it.path))
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) { empty("预览失败: " + (j.error || "未知")); return; }
        var c = j.content || "";
        if (it.ext === "html" || it.ext === "htm") renderHtml(it.name, c);
        else if (it.ext === "md" || it.ext === "markdown") renderMdInto(it.name, c);
        else renderText(it.name, c, it.ext);
      })
      .catch(function (e) { empty("请求异常: " + e); });
  }
  function renderContentItem(it) {
    var t = it.type, name = it.name, c = it.content || "";
    if (t === "html") renderHtml(name, c);
    else if (t === "md" || t === "markdown") renderMdInto(name, c);
    else if (t === "image" && it.url) renderImageFromUrl(name, it.url);
    else renderText(name, c, t);
  }
  function renderActive() {
    var it = activeItem();
    if (!it) { empty("从左侧产物卡点选一个文件即可在此预览"); return; }
    if (it.kind === "file") renderFileItem(it);
    else renderContentItem(it);
  }

  // ---------- 页签栏 (Phase 81) ----------
  function renderTabs() {
    if (!tabsEl) return;
    if (!items.length) { tabsEl.innerHTML = ""; return; }
    tabsEl.innerHTML = items.map(function (it) {
      var cls = it.id === activeId ? "pd-tab active" : "pd-tab";
      var ico = ICONS[it.ext] || ICONS[it.type] || "📄";
      return '<button class="' + cls + '" data-id="' + escAttr(it.id) + '">'
        + '<span class="pd-tab-ico">' + ico + '</span>'
        + '<span class="pd-tab-name">' + escHtml(it.short) + '</span>'
        + '<span class="pd-tab-x" data-x="' + escAttr(it.id) + '" title="关闭">✕</span>'
        + '</button>';
    }).join("");
  }
  function findTabBtn(el) {
    while (el && el !== tabsEl) {
      if (el.getAttribute && (el.getAttribute("data-id") || el.getAttribute("data-x"))) return el;
      el = el.parentNode;
    }
    return null;
  }
  if (tabsEl) {
    tabsEl.addEventListener("click", function (ev) {
      var btn = findTabBtn(ev.target);
      if (!btn) return;
      var x = btn.getAttribute("data-x");
      if (x) { closeItem(x); return; }
      activate(btn.getAttribute("data-id"));
    });
  }
  function activate(id) {
    activeId = id;
    renderTabs();
    renderActive();
  }
  function closeItem(id) {
    items = items.filter(function (x) { return x.id !== id; });
    if (activeId === id) {
      var last = items[items.length - 1];
      activeId = last ? last.id : null;
    }
    if (!items.length) { renderTabs(); closeDock(); return; }
    renderTabs();
    renderActive();
  }

  function makeItem(opts) {
    if (opts.path) {
      var name = opts.title || basename(opts.path);
      var ext = (name.split(".").pop() || "").toLowerCase();
      var isImg = /^(png|jpe?g|gif|webp|svg|bmp|ico)$/.test(ext);
      return {
        id: "p:" + opts.path, kind: "file", path: opts.path, name: name,
        short: name, ext: ext, isImg: isImg, url: fileUrl(opts.path)
      };
    }
    var t = opts.type || "text";
    var nm = opts.title || "预览";
    var cext = (nm.split(".").pop() || "").toLowerCase();
    return {
      id: "c:" + nm + ":" + t, kind: "content", name: nm,
      short: nm, type: t, ext: cext, url: opts.url || null, content: opts.content
    };
  }

  // ---------- 公共 API ----------
  window.LMWW = window.LMWW || {};
  window.LMWW.preview = function (opts) {
    if (!opts) return;
    var id = opts.path ? "p:" + opts.path
      : "c:" + (opts.title || "preview") + ":" + (opts.type || "text");
    var it = null;
    for (var i = 0; i < items.length; i++) if (items[i].id === id) { it = items[i]; break; }
    if (!it) { it = makeItem(opts); items.push(it); }
    activeId = id;
    openDock();
    renderTabs();
    renderActive();
  };
  window.LMWW.togglePreview = function () {
    if (dock.classList.contains("open")) closeDock();
    else { openDock(); renderActive(); }
  };
  window.LMWW.closePreview = function (id) {
    if (id) closeItem(id);
    else { items = []; if (tabsEl) tabsEl.innerHTML = ""; closeDock(); }
  };
})();
