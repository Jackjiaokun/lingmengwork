"""LLM 价格表 (单一来源): 人民币元 / 千 token。

与 config.py DEFAULTS 的默认后端 (sensenova-6.8-flash-lite) 对齐;
新增模型只需在此表登记, loop.token_stats 与 Web /api/cost 自动受益。
所有价格均为「估算量级」(接口多不返回 usage, 走字符数×系数), 仅用于成本认知,
非精确账单。
"""
# 价格单位: 元 / 千 token
PRICING = {
    "sensenova-6.8-flash-lite": {
        "in": 0.0001, "out": 0.0002,
        "label": "商汤 SenseNova 6.8 Flash-Lite",
    },
    "sensenova-6.8-flash": {
        "in": 0.0004, "out": 0.0008,
        "label": "商汤 SenseNova 6.8 Flash",
    },
    "deepseek-chat": {
        "in": 0.001, "out": 0.002,
        "label": "DeepSeek Chat",
    },
    "deepseek-reasoner": {
        "in": 0.004, "out": 0.016,
        "label": "DeepSeek Reasoner",
    },
}
DEFAULT_PRICING = {"in": 0.0001, "out": 0.0002, "label": "默认估算 (flash-lite 量级)"}


def price_for(model):
    """返回某 model 的价格档 (含 in/out/label); 未知模型回退默认档。"""
    if not model:
        return dict(DEFAULT_PRICING)
    return PRICING.get(str(model).lower()) or dict(DEFAULT_PRICING)


def reference_list():
    """返回所有登记模型的价格参考 (供 Web 成本看板展示价目表)。"""
    return [{"model": m, "label": v["label"], "in": v["in"], "out": v["out"]}
            for m, v in PRICING.items()]


def cost(input_tokens, output_tokens, model):
    """按 model 价格档估算总成本 (元)。"""
    p = price_for(model)
    return input_tokens / 1000.0 * p["in"] + output_tokens / 1000.0 * p["out"]


def fmt_cny(v):
    """把成本格式化为可读人民币字符串 (小数值用更多小数位)。"""
    try:
        v = float(v)
    except Exception:
        return "¥0"
    if v == 0:
        return "¥0"
    if v < 0.01:
        return "¥%.5f" % v
    if v < 1:
        return "¥%.4f" % v
    return "¥%.2f" % v
