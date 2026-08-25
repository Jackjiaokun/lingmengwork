import json, sys, urllib.request

URL = "http://127.0.0.1:8318/api/chat"
prompt = (
    "请在 lmw_agent_probe 子目录中完成一个小任务, 全程使用工具: "
    "1) 先用 write_file 创建 lmw_agent_probe/calculator.py, 内容含 add/sub/mul/div 四个函数(div 除零抛 ValueError); "
    "2) 再用 write_file 创建 lmw_agent_probe/test_calculator.py, 用 pytest 风格测试这四个函数(含除零异常); "
    "3) 调用 auto_test 运行测试验证通过; "
    "4) 调用 review_code 对 calculator.py 做评审自检。 "
    "每调用一个工具前用一句话中文说明要做什么。完成后中文总结你做了什么、测试是否通过、评审结论。"
)

body = json.dumps({"message": prompt, "mode": "bypassPermissions"}).encode("utf-8")
req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"}, method="POST")

seq = 0
tool_kinds = []
print("=== SSE STREAM (tool chain) ===")
try:
    with urllib.request.urlopen(req, timeout=420) as r:
        for line in r:
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload in ("", "{\"type\":\"close\"}"):
                continue
            try:
                evt = json.loads(payload)
            except Exception:
                continue
            t = evt.get("type")
            if t == "text":
                sys.stdout.write(evt.get("chunk", "")); sys.stdout.flush()
            elif t == "tool":
                seq += 1
                tool_kinds.append(evt.get("name"))
                print(f"\n  [{seq}] TOOL: {evt.get('name')}  args={json.dumps(evt.get('args', {}), ensure_ascii=False)[:120]}")
            elif t == "tool_result":
                out = str(evt.get("output", ""))
                print(f"      -> result({len(out)} chars): {out[:120].replace(chr(10),' ')}")
            elif t == "done":
                if evt.get("truncated"):
                    print("\n[TRUNCATED]")
                sid = evt.get("session_id")
                if sid:
                    print(f"\n[SESSION] {sid}")
            elif t == "error":
                print(f"\n[ERROR] {evt.get('message')}")
except Exception as e:
    print("STREAM_ERR:", e)

print("\n=== TOOL CHAIN SUMMARY ===")
print("total tool calls:", seq)
print("kinds:", tool_kinds)
from collections import Counter
print("usage:", dict(Counter(tool_kinds)))
