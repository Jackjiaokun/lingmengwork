/* 灵梦work · 前端共享工具模块 (common.js, Phase 83)
 *
 * 目标: 消除 31 个内联页面各自重复实现的通用工具(esc / $ / toast / api / fmt* / inlineMd)。
 *
 * 注入位置: </head> 之前 —— 页面的内联脚本位于 body 中, 共享模块必须先于它们就绪。
 * 安全性: 本文件在加载时【只定义函数, 不触碰 DOM】
 *         (sidebar.js 曾因在 head 里同步操作 DOM 而拿不到元素, 那个坑不能重踩)。
 *
 * 对外: window.LMW.{ $, $$, esc, toast, api, fmtTime, fmtDate, fmtDateTime,
 *                    fmtSize, fmtInt, fmtCny, inlineMd }
 *
 * 迁移方式: 页面里原本的 `function esc(s){...}` 替换为 `function esc(s){ return LMW.esc(s); }`
 *           —— 保留 function 声明(提升语义不变), 调用点一行不改, 零风险。
 */
(function () {
  function $(s) { return document.querySelector(s); }
  function $$(s) { return Array.prototype.slice.call(document.querySelectorAll(s) || []); }

  /* 完整 HTML 转义: & < > " '
   * 注意: 旧版 cost.html 的实现漏掉了引号(/[&<>]/g), 一旦把结果放进 HTML 属性
   *       (title="..." / data-x="...") 就会破损甚至注入。统一后此缺陷自动消失。 */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      if (c === "&") return "&amp;";
      if (c === "<") return "&lt;";
      if (c === ">") return "&gt;";
      if (c === '"') return "&quot;";
      return "&#39;";
    });
  }

  function toast(m, ok) {
    try {
      var t = document.getElementById("toast");
      if (!t) {
        // 页面没预置就动态创建一个, 保证任何页面都能给出轻提示
        t = document.createElement("div");
        t.id = "toast";
        t.className = "lmw-toast";
        document.body.appendChild(t);
      }
      t.textContent = m;
      t.style.borderColor = (ok === false) ? "var(--fail)" : "var(--ok)";
      t.classList.add("show");
      setTimeout(function () { try { t.classList.remove("show"); } catch (e) {} }, 2200);
    } catch (e) {}
  }

  /* fetch + JSON + 错误抛出(后端统一 {error: "..."} 约定) */
  function api(u, o) {
    return fetch(u, o).then(function (r) {
      return r.json().catch(function () { return { error: "解析失败" }; });
    }).then(function (d) {
      if (d && d.error) throw new Error(d.error);
      return d;
    });
  }

  function pad2(n) {
    n = Number(n) || 0;
    return (n < 10 ? "0" : "") + n;
  }

  function fmtTime(ts) {
    try {
      var d = new Date(ts);
      if (isNaN(d.getTime())) return String(ts == null ? "" : ts);
      return pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":" + pad2(d.getSeconds());
    } catch (e) { return String(ts == null ? "" : ts); }
  }

  function fmtDate(ts) {
    try {
      var d = new Date(ts);
      if (isNaN(d.getTime())) return String(ts == null ? "" : ts);
      return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
    } catch (e) { return String(ts == null ? "" : ts); }
  }

  function fmtDateTime(ts) { return fmtDate(ts) + " " + fmtTime(ts); }

  function fmtSize(b) {
    if (!b && b !== 0) return "—";
    if (b < 1024) return b + " B";
    if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " KB";
    return (b / 1024 / 1024).toFixed(2) + " MB";
  }

  function fmtInt(n) {
    n = n || 0;
    try { return n.toLocaleString("en-US"); } catch (e) { return String(n); }
  }

  function fmtCny(v) {
    v = v || 0;
    if (v === 0) return "¥0";
    if (v < 0.01) return "¥" + v.toFixed(5);
    if (v < 1) return "¥" + v.toFixed(4);
    return "¥" + v.toFixed(2);
  }

  /* 轻量行内 Markdown: `code` 与 **bold**(已先转义, 无注入风险) */
  function inlineMd(t) {
    var s = esc(t);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    return s;
  }

  window.LMW = window.LMW || {};
  var L = window.LMW;
  L.$ = $;
  L.$$ = $$;
  L.esc = esc;
  L.toast = toast;
  L.api = api;
  L.fmtTime = fmtTime;
  L.fmtDate = fmtDate;
  L.fmtDateTime = fmtDateTime;
  L.fmtSize = fmtSize;
  L.fmtInt = fmtInt;
  L.fmtCny = fmtCny;
  L.inlineMd = inlineMd;
})();
