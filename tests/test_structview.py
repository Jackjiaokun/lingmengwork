"""前端结构化渲染纯函数单测 (Node 运行 structview.js 的 buildStructuredHTML)。

验证:
- 数组 -> 渲染对比表格 + 一键展开原始按钮
- 对象 -> 渲染样例表
- 标量 -> 渲染值块
- 无原始文本 -> 不出现原始按钮
- 数组预览表行数 = 表头(1) + 数据行(preview_n)
"""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
STRUCTVIEW = os.path.abspath(os.path.join(HERE, "..", "lingmengwork", "web", "static", "structview.js"))

_NODE_TEST = r'''
var fs = require('fs');
var code = fs.readFileSync(%r, 'utf8');
eval(code);  // IIFE 挂到 globalThis.buildStructuredHTML
var b = globalThis.buildStructuredHTML;
if (typeof b !== 'function') { console.error('buildStructuredHTML 未挂载'); process.exit(2); }

var arr = {is_json:true, kind:'array', n:3, keys:['name','age'],
  preview:[{name:'a',age:'1'},{name:'b',age:'2'},{name:'c',age:'3'}], preview_n:3};
var arrHtml = b(arr, JSON.stringify([{name:'a',age:1},{name:'b',age:2},{name:'c',age:3}]));

var obj = {is_json:true, kind:'object', n:1, keys:['x'], sample:{x:'1'}};
var objHtml = b(obj, '{"x":"1"}');

var scal = {is_json:true, kind:'scalar', n:1, keys:[], value:'42'};
var scalHtml = b(scal, '42');

var noRaw = b({is_json:true, kind:'object', n:1, keys:['x'], sample:{x:'1'}}, null);

var trCount = (arrHtml.match(/<tr>/g) || []).length;
console.log('ARR_TABLE=' + (arrHtml.indexOf('struct-table') >= 0));
console.log('ARR_RAW_BTN=' + (arrHtml.indexOf('struct-raw-btn') >= 0));
console.log('ARR_RAW_PRE=' + (arrHtml.indexOf('struct-raw') >= 0));
console.log('OBJ_SAMPLE=' + (objHtml.indexOf('struct-sample') >= 0));
console.log('SCALAR_VAL=' + (scalHtml.indexOf('struct-scalar') >= 0));
console.log('NO_RAW_BTN=' + (noRaw.indexOf('struct-raw-btn') < 0));
console.log('ARR_ROWS=' + trCount);
'''


def test_buildStructuredHTML():
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        pytest.skip("环境中无 node, 跳过前端纯函数单测")
    js = _NODE_TEST % STRUCTVIEW
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, "node 运行失败:\n" + out.stderr + "\n" + out.stdout
    flags = {}
    counts = {}
    for ln in out.stdout.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            if k == "ARR_ROWS":
                counts[k] = int(v)
            else:
                flags[k] = (v == "true")
    assert flags.get("ARR_TABLE"), "数组应渲染对比表格"
    assert flags.get("ARR_RAW_BTN"), "数组应渲染一键展开原始按钮"
    assert flags.get("ARR_RAW_PRE"), "数组应渲染隐藏的原始 JSON 块"
    assert flags.get("OBJ_SAMPLE"), "对象应渲染样例键值表"
    assert flags.get("SCALAR_VAL"), "标量应渲染值块"
    assert flags.get("NO_RAW_BTN"), "无原始文本时不应出现原始按钮"
    assert counts.get("ARR_ROWS") == 4, "数组预览表应为 1 表头 + 3 数据行"
