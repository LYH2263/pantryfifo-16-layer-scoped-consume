from app.engines.fefo import consume_fefo, expire_lots, plan_consume, sort_lots_fefo

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

LOTS = [
    {"id": 1, "qty_remain": 2, "expiry": "2026-01-10", "layer": "upper"},
    {"id": 2, "qty_remain": 1, "expiry": "2026-01-05", "layer": "mid"},
    {"id": 3, "qty_remain": 4, "expiry": "2026-02-01", "layer": "upper"},
]

def test_plan_layer_scoped_only_hits_that_layer():
    r = plan_consume(LOTS, 5, layer="upper")
    assert r["ok"] and [d["lot_id"] for d in r["deductions"]] == [1, 3]
    assert r["layers_touched"] == ["upper"]
    assert r["cross_layer"] is False and r["borrowed"] is False

def test_plan_layer_short_without_borrow():
    r = plan_consume(LOTS, 7, layer="upper")
    assert r["ok"] is False and r["reason"] == "short" and r["short"] == 1
    # partial plan is disclosed, but no other layer is touched
    assert {d["layer"] for d in r["deductions"]} == {"upper"}

def test_plan_borrow_spills_to_other_layers_and_is_marked():
    r = plan_consume(LOTS, 7, layer="upper", borrow=True)
    assert r["ok"] and r["borrowed"] is True and r["cross_layer"] is True
    assert r["layers_touched"] == ["mid", "upper"]
    spill = [d for d in r["deductions"] if d["layer"] == "mid"]
    assert spill == [{"lot_id": 2, "take": 1, "expiry": "2026-01-05", "layer": "mid"}]

def test_plan_whole_fridge_crosses_layers_by_expiry():
    r = plan_consume(LOTS, 4)
    assert r["ok"] and [d["lot_id"] for d in r["deductions"]] == [2, 1, 3]
    assert r["cross_layer"] is True and r["borrowed"] is False

def test_plan_qty_non_positive():
    r = plan_consume(LOTS, 0)
    assert r["ok"] is False and r["reason"] == "qty_non_positive"
