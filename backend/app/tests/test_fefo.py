from app.engines.fefo import consume_fefo, expire_lots, sort_lots_fefo, consume_plan

def lot(id, qty, expiry, layer):
    return {"id": id, "qty_remain": qty, "expiry": expiry, "layer": layer}

def test_fefo_order():
    lots = [
        {"id": 2, "qty_remain": 3, "expiry": "2026-02-01"},
        {"id": 1, "qty_remain": 2, "expiry": "2026-01-10"},
    ]
    assert [l["id"] for l in sort_lots_fefo(lots)] == [1, 2]
    r = consume_fefo(lots, 3)
    assert r["ok"] and r["deductions"][0]["lot_id"] == 1 and r["deductions"][0]["take"] == 2
    assert r["deductions"][1]["take"] == 1

def test_short():
    r = consume_fefo([{"id": 1, "qty_remain": 1, "expiry": "2026-01-01"}], 5)
    assert r["ok"] is False and r["short"] == 4

def test_expire():
    ids = expire_lots([
        {"id": 1, "qty_remain": 1, "expiry": "2025-01-01"},
        {"id": 2, "qty_remain": 1, "expiry": "2027-01-01"},
    ], "2026-01-01")
    assert ids == [1]

# ---- 批级层 / 本层作用域 / 跨层 ----

def test_scoped_skips_earlier_lot_in_other_layer():
    # 同品项：mid 批 id=1 到期最早，upper 批 id=2 到期晚
    lots = [lot(1, 2, "2026-10-01", "mid"), lot(2, 2, "2026-10-05", "upper")]
    p = consume_plan(lots, 2, scope_layer="upper")
    assert p["ok"]
    assert [d["lot_id"] for d in p["deductions"]] == [2]
    assert p["cross_layer"] is False
    assert p["layers_touched"] == ["upper"]

def test_scoped_short_never_borrows_other_layer():
    lots = [lot(1, 2, "2026-10-01", "mid"), lot(2, 1, "2026-10-05", "upper")]
    p = consume_plan(lots, 3, scope_layer="upper")
    assert p["ok"] is False and p["reason"] == "short"
    assert p["short"] == 2 and p["available"] == 1
    # 邻层更早到期批不得出现在扣减里
    assert {d["lot_id"] for d in p["deductions"]} == {2}
    assert all(d["layer"] == "upper" for d in p["deductions"])

def test_global_cross_layer_fefo_order():
    lots = [lot(2, 2, "2026-10-05", "upper"), lot(1, 2, "2026-10-01", "mid")]
    p = consume_plan(lots, 3)
    assert p["ok"] and p["cross_layer"] is True
    assert [d["lot_id"] for d in p["deductions"]] == [1, 2]
    assert [d["take"] for d in p["deductions"]] == [2, 1]
    assert p["layers_touched"] == ["mid", "upper"]

def test_global_single_layer_not_flagged_cross():
    p = consume_plan([lot(1, 5, "2026-10-01", "mid"), lot(2, 5, "2026-10-02", "mid")], 3)
    assert p["ok"] and p["cross_layer"] is False and p["layers_touched"] == ["mid"]

def test_negative_remaining_lots_excluded():
    p = consume_plan([lot(1, -3, "2026-09-01", "mid"), lot(2, 2, "2026-10-05", "mid")], 2)
    assert p["ok"] and [d["lot_id"] for d in p["deductions"]] == [2]
    assert p["available"] == 2

def test_qty_non_positive():
    p = consume_plan([lot(1, 2, "2026-10-01", "mid")], 0)
    assert p["ok"] is False and p["reason"] == "qty_non_positive"
