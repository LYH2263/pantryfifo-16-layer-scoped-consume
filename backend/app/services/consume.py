"""Consume service: preview (read-only) and confirm (atomic re-plan + write).

Kept free of web-framework imports so it is testable with the stdlib only.
The confirm path re-plans inside a BEGIN IMMEDIATE transaction, so a plan
shown by preview is never applied blindly: a concurrent cross-layer consume
from another entry point serializes on the SQLite write lock, and this
transaction plans against the newest committed stock. The returned plan is
the single source of truth for what was actually deducted, per layer.
"""
import json
import sqlite3
from datetime import datetime, timezone

from app.engines.fefo import plan_consume


class ItemNotFound(Exception):
    pass


class UnknownLayer(Exception):
    pass


class BadQty(Exception):
    pass


class ConsumeConflict(Exception):
    """Stock cannot cover the request at confirm time. Carries the fresh plan."""

    def __init__(self, plan: dict):
        super().__init__("stock changed or insufficient")
        self.plan = plan


class ConsumeBusy(Exception):
    """Lost the write-lock race or a guarded update matched nothing."""


def _known_layers(c) -> set:
    rows = c.execute(
        "SELECT DISTINCT layer FROM items "
        "UNION SELECT DISTINCT layer FROM lots WHERE layer IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows if r[0]}


def _load_lots(c, item_id: int) -> list[dict]:
    """On-shelf lots with the EFFECTIVE layer: lot layer wins, else item layer."""
    q = """SELECT lots.id, lots.item_id, lots.qty_remain, lots.expiry,
                  COALESCE(lots.layer, items.layer) AS layer
           FROM lots JOIN items ON items.id = lots.item_id
           WHERE lots.item_id = ? AND lots.status = 'on_shelf' AND lots.qty_remain > 0"""
    return [dict(r) for r in c.execute(q, (item_id,))]


def _validate(c, item_id: int, layer: str | None) -> None:
    if c.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is None:
        raise ItemNotFound()
    if layer is not None and layer not in _known_layers(c):
        raise UnknownLayer()


def _plan(c, *, item_id: int, qty: float, layer: str | None, borrow: bool) -> dict:
    if float(qty) <= 0:
        raise BadQty()
    _validate(c, item_id, layer)
    plan = plan_consume(_load_lots(c, item_id), qty, layer=layer, borrow=borrow)
    plan["item_id"] = item_id
    plan["qty"] = float(qty)
    return plan


def preview_consume(c, *, item_id: int, qty: float, layer: str | None = None,
                    borrow: bool = False) -> dict:
    """Dry-run: which lots WOULD be deducted. Writes nothing."""
    layer = layer or None
    return _plan(c, item_id=item_id, qty=qty, layer=layer, borrow=borrow)


def confirm_consume(c, *, item_id: int, qty: float, layer: str | None = None,
                    borrow: bool = False, note: str = "") -> dict:
    """Apply a consumption atomically and return the plan actually written.

    Raises ConsumeConflict (with a fresh plan) when stock no longer covers the
    request, ConsumeBusy on lock contention. On any raise, nothing is written.
    """
    layer = layer or None
    try:
        c.execute("BEGIN IMMEDIATE")
        plan = _plan(c, item_id=item_id, qty=qty, layer=layer, borrow=borrow)
        if not plan["ok"]:
            raise ConsumeConflict(plan)
        for d in plan["deductions"]:
            cur = c.execute(
                "UPDATE lots SET qty_remain = qty_remain - ? "
                "WHERE id = ? AND qty_remain >= ?",
                (d["take"], d["lot_id"], d["take"]))
            if cur.rowcount != 1:
                raise ConsumeBusy()
            rem = c.execute("SELECT qty_remain FROM lots WHERE id = ?",
                            (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 1e-9:
                c.execute("UPDATE lots SET status = 'consumed', qty_remain = 0 "
                          "WHERE id = ?", (d["lot_id"],))
        c.execute(
            "INSERT INTO consumptions(note, result_json, created_at) VALUES (?,?,?)",
            (note, json.dumps(plan, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()))
        c.commit()
        return plan
    except (ItemNotFound, UnknownLayer, BadQty, ConsumeConflict, ConsumeBusy):
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    except sqlite3.OperationalError as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise ConsumeBusy() from e
