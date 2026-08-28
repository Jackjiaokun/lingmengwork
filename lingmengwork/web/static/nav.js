/* 灵梦work · 全站统一导航 (v1, 2026-08-28)
 * 在除 index.html(自带侧栏) 外的所有页面顶部注入一条精简导航:
 * 品牌 + 主要页面链接 + 当前页高亮。幂等, 无依赖。
 */
(function () {
  if (document.getElementById("lmwNav")) return;
  // 自带侧栏/导航的页面可在 <html> 标记 data-lmw-nonav 跳过注入
  if (document.documentElement.hasAttribute("data-lmw-nonav")) return;
  var ROUTES = [
    ["/", "对话"],
    ["/superagent", "超级 AGENT"],
    ["/cost", "成本"],
    ["/observability", "可观测"],
    ["/planboard", "计划"],
    ["/sandbox", "沙箱"],
    ["/plugins", "插件"],
    ["/settings", "设置"]
  ];
  var cur = location.pathname.replace(/\/+$/, "") || "/";
  if (cur === "/index.html") cur = "/";

  var bar = document.createElement("div");
  bar.id = "lmwNav";
  var html = '<div class="lmw-nav-in">'
    + '<a class="lmw-brand" href="/">灵梦<span>work</span></a>'
    + '<nav class="lmw-links">';
  for (var i = 0; i < ROUTES.length; i++) {
    var href = ROUTES[i][0], label = ROUTES[i][1];
    var on = (cur === href) ? " on" : "";
    html += '<a class="lmw-link' + on + '" href="' + href + '">' + label + "</a>";
  }
  html += '</nav><span class="lmw-nav-tip">AI 全能工作台</span></div>';
  bar.innerHTML = html;

  var style = document.createElement("style");
  style.textContent = [
    "#lmwNav{position:sticky;top:0;z-index:999;backdrop-filter:blur(10px);",
    "background:rgba(10,13,26,.82);border-bottom:1px solid rgba(130,140,200,.18);",
    "font:13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}",
    "#lmwNav .lmw-nav-in{max-width:1200px;margin:0 auto;display:flex;align-items:center;gap:4px;padding:0 16px;height:44px}",
    "#lmwNav .lmw-brand{font-weight:800;font-size:14px;text-decoration:none;color:#e7e9f5;letter-spacing:.5px;margin-right:10px}",
    "#lmwNav .lmw-brand span{background:linear-gradient(135deg,#8b5cf6,#22d3ee);-webkit-background-clip:text;background-clip:text;color:transparent}",
    "#lmwNav .lmw-links{display:flex;gap:2px;flex-wrap:wrap}",
    "#lmwNav .lmw-link{color:#9aa3c7;text-decoration:none;padding:5px 11px;border-radius:8px;font-size:12.5px}",
    "#lmwNav .lmw-link:hover{color:#e7e9f5;background:rgba(139,92,246,.16)}",
    "#lmwNav .lmw-link.on{color:#e7e9f5;background:rgba(139,92,246,.28)}",
    "#lmwNav .lmw-nav-tip{margin-left:auto;color:#6b7299;font-size:11px}",
    "@media(max-width:760px){#lmwNav .lmw-nav-tip{display:none}}"
  ].join("");
  document.head.appendChild(style);

  function mount() {
    document.body.insertBefore(bar, document.body.firstChild);
  }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
