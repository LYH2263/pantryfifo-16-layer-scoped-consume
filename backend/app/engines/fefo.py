"""FEFO consume: earliest expiry first among positive remaining lots."""

EPS = 1e-9
FAR_EXPIRY = "9999-99-99"

def _effective_layer(lot: dict):
    # 调用方应已通过 SQL COALESCE 给出有效层键 "layer"
    return lot.get("layer")

def sort_lots_fefo(lots: list[dict], scope_layer: str | None = None) -> list[dict]:
    candidates = [
        l for l in lots
        if float(l.get("qty_remain", 0)) > EPS
        and (scope_layer is None or _effective_layer(l) == scope_layer)
    ]
    return sorted(candidates, key=lambda l: (l.get("expiry") or FAR_EXPIRY, l.get("id") or 0))

def consume_plan(lots: list[dict], qty: float, scope_layer: str | None = None,
                 item_id=None, item_name: str | None = None, unit: str | None = None,
                 dry_run: bool = False) -> dict:
    """FEFO 扣减计划（只算不写）。

    lots 中每个 lot 须带有效层键 "layer"（SQL COALESCE(lots.layer, items.layer)）。
    scope_layer 非空时候选仅限该层（本层消费，绝不借邻层）；None 为全层入口，
    跨层严格按到期先后，层不参与排序。
    """
    need = float(qty)
    base = {
        "dry_run": dry_run, "item_id": item_id, "item_name": item_name, "unit": unit,
        "scope_layer": scope_layer, "requested_qty": need,
    }
    if need <= 0:
        return {**base, "ok": False, "reason": "qty_non_positive", "deductions": [],
                "short": 0.0, "available": 0.0, "cross_layer": False, "layers_touched": []}
    ordered = sort_lots_fefo(lots, scope_layer)
    available = round(sum(float(l["qty_remain"]) for l in ordered), 3)
    deductions = []
    layers = []
    for lot in ordered:
        if need <= EPS:
            break
        avail = float(lot["qty_remain"])
        take = min(avail, need)
        layer = _effective_layer(lot)
        deductions.append({"lot_id": lot["id"], "take": round(take, 3),
                           "expiry": lot.get("expiry"), "layer": layer})
        if layer is not None and layer not in layers:
            layers.append(layer)
        need -= take
    if need > EPS:
        return {**base, "ok": False, "reason": "short", "deductions": deductions,
                "short": round(need, 3), "available": available,
                "cross_layer": False, "layers_touched": layers}
    return {**base, "ok": True, "reason": "", "deductions": deductions,
            "short": 0.0, "available": available,
            # scope 模式恒 False；全层模式按实际触及层数判定（只扣到一层则非跨层）
            "cross_layer": scope_layer is None and len(layers) > 1,
            "layers_touched": layers}

def consume_fefo(lots: list[dict], qty: float) -> dict:
    """旧形状适配：供历史调用与测试，新代码用 consume_plan。"""
    p = consume_plan(lots, qty)
    return {"ok": p["ok"], "reason": p["reason"],
            "deductions": [{"lot_id": d["lot_id"], "take": d["take"], "expiry": d["expiry"]}
                           for d in p["deductions"]],
            "short": p["short"]}

def expire_lots(lots: list[dict], today: str) -> list[int]:
    """Ids that should leave shelf: remaining>0 and expiry < today."""
    out = []
    for l in lots:
        exp = l.get("expiry")
        if exp and exp < today and float(l.get("qty_remain", 0)) > 0:
            out.append(l["id"])
    return out
