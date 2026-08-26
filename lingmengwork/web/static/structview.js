/* 主题 A 闭环增强 (结构化结果表格化对比 + 一键展开原始)
 * 零依赖纯函数模块: 仅生成 HTML 字符串, 不触碰 DOM, 便于 node 单测。
 * 挂到全局 buildStructuredHTML(s, rawText), 供 app.js 的 appendStructured 调用。
 */
(function () {
  "use strict";

  // 同时兼容浏览器(window)与 node(globalThis) 测试环境
  var g = (typeof window !== "undefined") ? window : (typeof globalThis !== "undefined" ? globalThis : this);

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // 将原始工具结果文本整理成可展示的 JSON / 原文 (截断保护)
  function fmtRaw(rawText) {
    if (rawText == null) return null;
    var str = String(rawText);
    try {
      var obj = JSON.parse(str);
      return JSON.stringify(obj, null, 2);
    } catch (e) {
      return str;
    }
  }

  // 生成结构化面板内部 HTML (不含外层 .struct-panel 容器)
  function buildStructuredHTML(s, rawText) {
    if (!s || !s.is_json) return "";

    var badge = s.kind === "array" ? "[]" : (s.kind === "object" ? "{}" : "#");
    var label;
    if (s.kind === "array") {
      label = "数组 · " + s.n + " 项" + (s.preview_n ? "（预览前 " + s.preview_n + "）" : "");
    } else if (s.kind === "object") {
      label = "对象 · " + s.n + " 字段";
    } else {
      label = "标量值";
    }

    var html = '<div class="struct-head">' +
      '<span class="struct-badge">' + badge + '</span>' +
      '<span class="struct-label">' + esc(label) + '</span>';
    var raw = fmtRaw(rawText);
    if (raw != null) {
      html += '<button class="struct-raw-btn" type="button">{} 原始</button>';
    }
    html += '</div>';

    // 键名 chip
    if (s.keys && s.keys.length) {
      html += '<div class="struct-keys">' +
        s.keys.slice(0, 24).map(function (k) { return '<span class="kchip">' + esc(k) + '</span>'; }).join("") +
        '</div>';
    }

    // 表格化渲染
    if (s.kind === "object" && s.sample) {
      var rows = Object.keys(s.sample).map(function (k) {
        return '<tr><td class="sk">' + esc(k) + '</td><td class="sv">' + esc(s.sample[k]) + '</td></tr>';
      }).join("");
      html += '<table class="struct-sample"><tbody>' + rows + '</tbody></table>';
    } else if (s.kind === "array" && s.preview && s.preview.length) {
      var cols = (s.keys && s.keys.length) ? s.keys.slice(0, 12) : [];
      var head = '<tr><th class="sk">#</th>' +
        cols.map(function (c) { return '<th class="sk">' + esc(c) + '</th>'; }).join("") + '</tr>';
      var body = s.preview.map(function (item, i) {
        var cells = cols.map(function (c) {
          var v = (item && item[c] != null) ? item[c] : "";
          return '<td class="sv">' + esc(v) + '</td>';
        }).join("");
        return '<tr><td class="sk">' + (i + 1) + '</td>' + cells + '</tr>';
      }).join("");
      html += '<div class="struct-table-wrap"><table class="struct-table">' +
        '<thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>';
    } else if (s.kind === "scalar") {
      html += '<div class="struct-scalar">' + esc(s.value != null ? s.value : "") + '</div>';
    }

    // 原始 JSON 块 (默认隐藏, 由调用方绑定按钮切换)
    if (raw != null) {
      var shown = raw.length > 8000 ? raw.slice(0, 8000) + "\n…(已截断)" : raw;
      html += '<pre class="struct-raw" style="display:none">' + esc(shown) + '</pre>';
    }
    return html;
  }

  g.buildStructuredHTML = buildStructuredHTML;
  g.__structview_esc = esc; // 供测试
})();
