"""价目模块单测 (批次13 主题E)。"""
from lingmengwork.llm import pricing as P


def test_price_for_known():
    p = P.price_for("sensenova-6.8-flash-lite")
    assert p["in"] == 0.0001 and p["out"] == 0.0002
    assert "SenseNova" in p["label"]


def test_price_for_unknown_fallback():
    p = P.price_for("nonexistent-model-xyz")
    assert p == P.DEFAULT_PRICING


def test_price_for_empty():
    p = P.price_for("")
    assert p == P.DEFAULT_PRICING


def test_cost_scales_with_tokens():
    c = P.cost(1000, 1000, "sensenova-6.8-flash-lite")
    # 1000*0.0001/1000 + 1000*0.0002/1000 = 0.0001 + 0.0002 = 0.0003
    assert abs(c - 0.0003) < 1e-12


def test_reference_list_shape():
    ref = P.reference_list()
    assert isinstance(ref, list) and len(ref) >= 1
    for r in ref:
        assert {"model", "label", "in", "out"} <= set(r.keys())


def test_fmt_cny_zero_and_small():
    assert P.fmt_cny(0) == "¥0"
    assert P.fmt_cny(0.00003).startswith("¥0.0000")
    assert P.fmt_cny(1.5) == "¥1.50"
