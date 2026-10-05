"""消费编排：预演只读 / 确认在 BEGIN IMMEDIATE 事务内重算并写入。

返回 (http_status, body)，端点只做 HTTP 适配，业务逻辑可被测试直接调用。
"""
import json
import sqlite3
from datetime import datetime, timezone

from app.db import connect, connect_immediate
from app.engines.fefo import consume_plan

LAYERS = ("upper", "mid", "lower")

CANDIDATE_SQL = """
SELECT lots.id, lots.item_id, lots.qty_remain, lots.expiry, lots.status,
       COALESCE(lots.layer, items.layer) AS layer
FROM lots JOIN items ON items.id = lots.item_id
WHERE lots.item_id=? AND lots.status='on_shelf' AND lots.qty_remain>0
"""

ITEM_SQL = "SELECT id, name, unit FROM items WHERE id=?"

def _validate(qty: float, layer):
    if layer is not None and layer not in LAYERS:
        return 400, {"detail": "invalid_layer"}
    if float(qty) <= 0:
        return 400, {"detail": "qty_non_positive"}
    return None

def _load(c, item_id):
    item = c.execute(ITEM_SQL, (item_id,)).fetchone()
    if item is None:
        return None, []
    lots = [dict(r) for r in c.execute(CANDIDATE_SQL, (item_id,))]
    return dict(item), lots

def _drifted(actual: list[dict], expected) -> bool | None:
    if expected is None:
        return None
    a = sorted((d["lot_id"], round(float(d["take"]), 3)) for d in actual)
    e = sorted((int(d["lot_id"]), round(float(d["take"]), 3)) for d in expected)
    return a != e

def run_consume(*, item_id: int, qty: float, layer: str | None = None, dry_run: bool,
                note: str = "", expected_deductions=None, _gate=None):
    bad = _validate(qty, layer)
    if bad:
        return bad

    if dry_run:
        c = connect()
        try:
            item, lots = _load(c, item_id)
            if item is None:
                return 404, {"detail": "item"}
            plan = consume_plan(lots, qty, scope_layer=layer,
                                item_id=item_id, item_name=item["name"],
                                unit=item["unit"], dry_run=True)
            return 200, plan
        finally:
            c.close()

    # 确认路径：autocommit 连接 + 显式写事务，plan 与写入在同一把写锁内
    c = connect_immediate()
    began = False
    try:
        c.execute("BEGIN IMMEDIATE")
        began = True
        if _gate is not None:
            _gate()
        item = c.execute(ITEM_SQL, (item_id,)).fetchone()
        if item is None:
            c.execute("ROLLBACK")
            return 404, {"detail": "item"}
        item = dict(item)
        lots = [dict(r) for r in c.execute(CANDIDATE_SQL, (item_id,))]
        plan = consume_plan(lots, qty, scope_layer=layer,
                            item_id=item_id, item_name=item["name"],
                            unit=item["unit"], dry_run=False)
        if not plan["ok"]:
            # short / qty_non_positive：整单不写
            c.execute("ROLLBACK")
            return 409, plan

        final_remaining = []
        for d in plan["deductions"]:
            cur = c.execute(
                "UPDATE lots SET qty_remain=qty_remain-? "
                "WHERE id=? AND status='on_shelf' AND qty_remain>=?",
                (d["take"], d["lot_id"], d["take"]))
            if cur.rowcount != 1:
                c.execute("ROLLBACK")
                return 409, {"detail": "concurrent_change", "plan": plan}
            rem = c.execute("SELECT qty_remain FROM lots WHERE id=?", (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 1e-9:
                c.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?", (d["lot_id"],))
                rem = 0.0
            final_remaining.append({"lot_id": d["lot_id"], "qty_remain": round(rem, 3),
                                    "status": "consumed" if rem == 0.0 else "on_shelf"})

        drifted = _drifted(plan["deductions"], expected_deductions)
        record = {**plan, "drifted": drifted}
        created_at = datetime.now(timezone.utc).isoformat()
        cur = c.execute(
            "INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
            (note, json.dumps(record, ensure_ascii=False), created_at))
        c.execute("COMMIT")
        began = False
        return 200, {**record, "committed": True, "consumption_id": cur.lastrowid,
                     "note": note, "created_at": created_at,
                     "final_remaining": final_remaining}
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            # 卡在 BEGIN 或 COMMIT：本事务未提交，重试安全
            if began:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
            return 503, {"detail": "database_busy_retry"}
        if began:
            c.execute("ROLLBACK")
        raise
    except Exception:
        if began:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()
