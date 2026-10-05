"""FEFO consume: earliest expiry first among positive remaining lots."""

EPS = 1e-9

def sort_lots_fefo(lots: list[dict]) -> list[dict]:
    return sorted(
        [l for l in lots if float(l.get("qty_remain", 0)) > 0],
        key=lambda l: (l.get("expiry") or "9999-99-99", l.get("id") or 0),
    )

def _take_from(ordered: list[dict], need: float, deductions: list[dict]) -> float:
    for lot in ordered:
        if need <= EPS:
            break
        take = min(float(lot["qty_remain"]), need)
        deductions.append({
            "lot_id": lot["id"], "take": take,
            "expiry": lot.get("expiry"), "layer": lot.get("layer"),
        })
        need -= take
    return need

def plan_consume(lots: list[dict], qty: float, layer: str | None = None,
                 borrow: bool = False) -> dict:
    """Plan a consumption without mutating anything.

    lots carry an effective `layer` (lot layer if set, else item layer).
    layer=None  -> whole-fridge FEFO, may span layers.
    layer="mid" -> only that layer's lots, unless borrow=True, in which case a
                   shortage spills to the other layers, still in FEFO order.
    Every deduction is tagged with its layer so callers can see exactly which
    layers a plan touches; nothing outside the plan may be written back.
    """
    need = float(qty)
    base = {"deductions": [], "layers_touched": [], "cross_layer": False,
            "borrowed": False, "scope_layer": layer, "borrow": bool(borrow)}
    if need <= 0:
        return {**base, "ok": False, "reason": "qty_non_positive", "short": 0.0}
    deductions: list[dict] = []
    if layer is None:
        eligible = list(lots)
    else:
        eligible = [l for l in lots if l.get("layer") == layer]
    need = _take_from(sort_lots_fefo(eligible), need, deductions)
    if need > EPS and layer is not None and borrow:
        rest = [l for l in lots if l.get("layer") != layer]
        need = _take_from(sort_lots_fefo(rest), need, deductions)
    layers_touched = sorted({d["layer"] for d in deductions if d["layer"] is not None})
    borrowed = layer is not None and any(d["layer"] != layer for d in deductions)
    ok = need <= EPS
    return {
        **base,
        "ok": ok,
        "reason": "" if ok else "short",
        "deductions": deductions,
        "short": 0.0 if ok else round(need, 3),
        "layers_touched": layers_touched,
        "cross_layer": len(layers_touched) > 1,
        "borrowed": borrowed,
    }

def consume_fefo(lots: list[dict], qty: float) -> dict:
    """Return deductions list and leftover demand. Mutates copies only."""
    need = float(qty)
    if need <= 0:
        return {"ok": False, "reason": "qty_non_positive", "deductions": [], "short": 0.0}
    ordered = sort_lots_fefo(lots)
    deductions = []
    for lot in ordered:
        if need <= 0:
            break
        avail = float(lot["qty_remain"])
        take = min(avail, need)
        deductions.append({"lot_id": lot["id"], "take": take, "expiry": lot.get("expiry")})
        need -= take
    if need > 1e-9:
        return {"ok": False, "reason": "short", "deductions": deductions, "short": round(need, 3)}
    return {"ok": True, "reason": "", "deductions": deductions, "short": 0.0}

def expire_lots(lots: list[dict], today: str) -> list[int]:
    """Ids that should leave shelf: remaining>0 and expiry < today."""
    out = []
    for l in lots:
        exp = l.get("expiry")
        if exp and exp < today and float(l.get("qty_remain", 0)) > 0:
            out.append(l["id"])
    return out
