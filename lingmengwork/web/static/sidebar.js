/* 灵梦work · 左侧全局导航侧边栏 (sidebar.js, Phase 79)
 * 在 #lmw-sidebar 容器渲染: 品牌 + 分组导航(全站主要页面) + 当前页高亮 + 外链。
 * 与 nav.js 顶栏链接一致(顶栏=精简全局, 侧栏=完整分组), 互补不冲突。
 * 页面需含 <aside class="sidebar" id="lmw-sidebar"></aside> (由统一化脚本注入到 .lmw-shell)。
 * 防御: 脚本常注入在 </head> 前(同步执行), 此时 body 未解析 -> 必须等 DOMContentLoaded。
 */
(function () {
  function render() {
    try {
      var GROUPS = [
        { grp: "工作台", items: [
          { href: "/superagent", ico: "🚀", label: "超级 AGENT" },
          { href: "/observability", ico: "📊", label: "可观测" },
          { href: "/cost", ico: "💰", label: "成本看板" },
          { href: "/planboard", ico: "🗂️", label: "计划看板" }
        ]},
        { grp: "工作区", items: [
          { href: "/notes", ico: "📝", label: "笔记" },
          { href: "/todos", ico: "✅", label: "待办清单" },
          { href: "/snippets", ico: "📎", label: "代码片段" },
          { href: "/templates", ico: "📋", label: "提示词模板" },
          { href: "/enhance", ico: "🧠", label: "专家·技能" }
        ]},
        { grp: "安全治理", items: [
          { href: "/plugins", ico: "🔌", label: "插件中枢" },
          { href: "/secrets", ico: "🔐", label: "密钥保险箱" },
          { href: "/backups", ico: "📦", label: "备份与回滚" },
          { href: "/sandbox", ico: "🛡️", label: "工作区沙箱" },
          { href: "/settings", ico: "⚙️", label: "设置中心" }
        ]},
        { grp: "中枢", items: [
          { href: "/memory-graph", ico: "🕸️", label: "记忆图谱" },
          { href: "/federation", ico: "🌐", label: "联邦路由" },
          { href: "/studio", ico: "🎬", label: "创作工作室" },
          { href: "/multimodal", ico: "🎨", label: "多模态域" },
          { href: "/automation", ico: "⚙️", label: "自动化中枢" },
          { href: "/autonomous", ico: "🤖", label: "自主智能体" },
          { href: "/heal", ico: "🩹", label: "自愈" },
          { href: "/audit", ico: "📜", label: "审计" },
          { href: "/activity", ico: "📈", label: "活动" },
          { href: "/errors", ico: "⚠️", label: "错误日志" },
          { href: "/control-center", ico: "🎛️", label: "控制中心" },
          { href: "/orchestrate", ico: "🔀", label: "编排" },
          { href: "/pipeline", ico: "🪜", label: "流水线" },
          { href: "/plans", ico: "🗺️", label: "计划" },
          { href: "/docs", ico: "📚", label: "文档" },
          { href: "/memory", ico: "🧠", label: "记忆" }
        ]}
      ];
      // 暴露给命令面板(commands.js)复用 —— 导航路由保持单一事实源, 避免两处硬编码
      try { window.LMW_NAV = GROUPS; } catch (e) {}
      var el = document.getElementById("lmw-sidebar");
      if (!el) return;
      var path = (location.pathname || "/").replace(/\/+$/, "") || "/";
      var html = '<div class="sb-brand"><span class="logo">灵</span><div><b>灵梦work</b><i>智能体工作台</i></div></div><nav class="sb-nav">';
      GROUPS.forEach(function (g) {
        html += '<div class="sb-grp">' + g.grp + "</div>";
        g.items.forEach(function (it) {
          var on = (it.href === path) ? " on" : "";
          html += '<a class="sb-item' + on + '" href="' + it.href + '">' + it.ico + " " + it.label + "</a>";
        });
      });
      html += '</nav><div class="sb-ext"><a href="/">💬 对话</a></div>';
      el.innerHTML = html;
    } catch (e) { /* 静默失败, 不影响原页面 */ }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
