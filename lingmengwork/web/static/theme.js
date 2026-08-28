/* 灵梦work · 全局主题切换 (Phase 51)
 * 注入浅色主题变量覆盖层 + 右上角浮动切换按钮, localStorage 记忆。
 * 用法: 页面 </body> 前 <script src="/static/theme.js"></script>
 */
(function () {
  var KEY = "lmw_theme";
  function cur() { try { return localStorage.getItem(KEY) || "dark"; } catch (e) { return "dark"; } }
  function save(t) { try { localStorage.setItem(KEY, t); } catch (e) {} }

  var CSS = [
    'html[data-theme="light"]{',
    '  --bg:#f2f5fb; --bg2:#e9edf6; --panel:rgba(255,255,255,.92); --panel2:rgba(255,255,255,.7);',
    '  --bd:rgba(30,40,90,.14); --tx:#1a2337; --sub:#4a5578; --mut:#7c86a6;',
    '  --accent:#6d4df0; --accent2:#0891b2; --good:#059669; --warn:#b45309; --bad:#dc2626;',
    '  --grad:linear-gradient(135deg,#6d4df0,#0891b2);',
    '  --txt:#1a2337; --card:rgba(255,255,255,.92); --card2:#ffffff; --line:rgba(30,40,90,.12);',
    '  --acc:#6d4df0; --ok:#059669; --fail:#dc2626; --brand2:#0891b2; color-scheme:light;',
    '}',
    'html[data-theme="light"] body{',
    '  background:#f2f5fb !important; background-image:none !important; color:#1a2337 !important;',
    '}',
    'html[data-theme="light"] .card, html[data-theme="light"] .panel,',
    'html[data-theme="light"] .panel-box, html[data-theme="light"] .stat-lg,',
    'html[data-theme="light"] .kpi, html[data-theme="light"] .run,',
    'html[data-theme="light"] textarea, html[data-theme="light"] input,',
    'html[data-theme="light"] select{',
    '  background:rgba(255,255,255,.92) !important; border-color:rgba(30,40,90,.14) !important;',
    '  color:#1a2337 !important;',
    '}',
    'html[data-theme="light"] .kpi .l, html[data-theme="light"] .muted,',
    'html[data-theme="light"] .lab{color:#5a6484 !important;}',
    'html[data-theme="light"] ::-webkit-scrollbar-thumb{background:rgba(30,40,90,.25);}',
    /* 切换浮球 */
    '#lmwThemeBtn{position:fixed;top:10px;right:12px;z-index:99999;width:34px;height:34px;',
    '  border-radius:50%;border:1px solid var(--bd,#333);background:var(--panel,rgba(0,0,0,.4));',
    '  color:var(--tx,#eee);cursor:pointer;font-size:16px;line-height:1;display:flex;',
    '  align-items:center;justify-content:center;box-shadow:0 2px 10px rgba(0,0,0,.25);}',
    '#lmwThemeBtn:hover{transform:scale(1.1);}'
  ].join("\\n");

  function inject() {
    var style = document.createElement("style");
    style.id = "lmwThemeStyle";
    style.textContent = CSS;
    document.head.appendChild(style);
    if (!document.getElementById("lmwThemeBtn")) {
      var btn = document.createElement("button");
      btn.id = "lmwThemeBtn";
      btn.title = "切换浅色/深色主题";
      btn.textContent = cur() === "light" ? "🌙" : "☀️";
      btn.addEventListener("click", function () {
        var t = cur() === "light" ? "dark" : "light";
        save(t); apply(t);
        btn.textContent = t === "light" ? "🌙" : "☀️";
      });
      document.body.appendChild(btn);
    }
  }

  apply(cur());
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inject);
  } else {
    inject();
  }
})();
