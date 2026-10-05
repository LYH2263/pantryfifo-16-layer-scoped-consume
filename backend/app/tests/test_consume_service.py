"""Service-level consume tests: stdlib-only so they run without pytest/fastapi.

Covers the layer-entry contract:
- preview is a dry-run (per-layer numbers unchanged until confirm)
- a layer-scoped confirm only writes that layer's on-shelf lots
- lot-level layer beats item-level layer for scoping/filtering
- shortage without borrow -> conflict, nothing written
- borrow spills across layers and the response says so
- confirm re-plans atomically: concurrent/stale confirms never oversell
"""
import os
import tempfile
import threading

from app.db import connect
from app.seed import init_db
from app.services.consume import (
    BadQty, ConsumeConflict, ItemNotFound, UnknownLayer,
    confirm_consume, preview_consume,
)


def fresh_db():
    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    init_db()
    c = connect()
    c.execute("DELETE FROM lots")
    c.execute("DELETE FROM items")
    c.execute("DELETE FROM consumptions")
    c.commit()
    c.close()


def add_item(item_id, layer, unit="个"):
    c = connect()
    c.execute("INSERT INTO items(id,name,layer,unit) VALUES (?,?,?,?)",
              (item_id, f"item{item_id}", layer, unit))
    c.commit()
    c.close()


def add_lot(item_id, qty, expiry, layer=None):
    c = connect()
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,layer)"
        " VALUES (?,?,?,?,?,?,?)",
        (item_id, qty, qty, expiry, "on_shelf", "clean", layer))
    lid = cur.lastrowid
    c.commit()
    c.close()
    return lid


def lot_row(lot_id):
    c = connect()
    row = dict(c.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone())
    c.close()
    return row


def consumption_count():
    c = connect()
    n = c.execute("SELECT COUNT(*) c FROM consumptions").fetchone()["c"]
    c.close()
    return n


def test_preview_writes_nothing():
    fresh_db()
    add_item(1, "upper")
    add_lot(1, 2, "2026-01-10")
    c = connect()
    plan = preview_consume(c, item_id=1, qty=1)
    c.close()
    assert plan["ok"] and len(plan["deductions"]) == 1
    # per-layer numbers must be exactly as before until confirm
    c = connect()
    rows = [dict(r) for r in c.execute("SELECT * FROM lots")]
    c.close()
    assert all(r["qty_remain"] == r["qty_in"] for r in rows)
    assert consumption_count() == 0


def test_layer_scoped_confirm_hits_only_that_layer():
    fresh_db()
    add_item(1, "upper")
    up = add_lot(1, 2, "2026-01-10")            # follows item layer: upper
    mid = add_lot(1, 1, "2026-01-05", layer="mid")  # lot layer pinned to mid
    c = connect()
    plan = confirm_consume(c, item_id=1, qty=2, layer="upper")
    c.close()
    assert plan["ok"] and plan["layers_touched"] == ["upper"]
    assert plan["cross_layer"] is False and plan["borrowed"] is False
    assert lot_row(up)["qty_remain"] == 0 and lot_row(up)["status"] == "consumed"
    assert lot_row(mid)["qty_remain"] == 1  # other layer untouched


def test_lot_layer_owns_the_lot_not_the_item_layer():
    fresh_db()
    add_item(1, "upper")
    mid = add_lot(1, 3, "2026-01-05", layer="mid")  # item says upper, lot says mid
    c = connect()
    plan = confirm_consume(c, item_id=1, qty=2, layer="mid")
    c.close()
    assert plan["ok"] and plan["deductions"][0]["lot_id"] == mid
    assert lot_row(mid)["qty_remain"] == 1
    # and the item's own layer sees nothing to take
    c = connect()
    try:
        confirm_consume(c, item_id=1, qty=1, layer="upper")
        assert False, "upper scope must not see the mid-pinned lot"
    except ConsumeConflict as e:
        assert e.plan["short"] == 1
    finally:
        c.close()


def test_short_without_borrow_conflicts_and_writes_nothing():
    fresh_db()
    add_item(1, "upper")
    up = add_lot(1, 2, "2026-01-10")
    mid = add_lot(1, 5, "2026-01-05", layer="mid")
    c = connect()
    try:
        confirm_consume(c, item_id=1, qty=3, layer="upper")
        assert False, "expected ConsumeConflict"
    except ConsumeConflict as e:
        assert e.plan["short"] == 1
        assert {d["layer"] for d in e.plan["deductions"]} == {"upper"}
    finally:
        c.close()
    assert lot_row(up)["qty_remain"] == 2 and lot_row(mid)["qty_remain"] == 5
    assert consumption_count() == 0


def test_borrow_spills_and_response_discloses_layers():
    fresh_db()
    add_item(1, "upper")
    up = add_lot(1, 2, "2026-01-10")
    mid = add_lot(1, 5, "2026-01-05", layer="mid")
    c = connect()
    plan = confirm_consume(c, item_id=1, qty=3, layer="upper", borrow=True)
    c.close()
    # the layer page asked for upper; the response MUST say mid was hit too
    assert plan["ok"] and plan["borrowed"] is True and plan["cross_layer"] is True
    assert plan["layers_touched"] == ["mid", "upper"]
    assert [(d["lot_id"], d["take"], d["layer"]) for d in plan["deductions"]] == [
        (up, 2, "upper"), (mid, 1, "mid")]
    assert lot_row(up)["qty_remain"] == 0 and lot_row(mid)["qty_remain"] == 4
    assert consumption_count() == 1


def test_full_entry_crosses_layers_by_expiry():
    fresh_db()
    add_item(1, "upper")
    older_mid = add_lot(1, 1, "2026-01-05", layer="mid")
    newer_up = add_lot(1, 2, "2026-01-10")
    c = connect()
    plan = confirm_consume(c, item_id=1, qty=2)
    c.close()
    assert plan["ok"] and plan["cross_layer"] is True and plan["borrowed"] is False
    assert [d["lot_id"] for d in plan["deductions"]] == [older_mid, newer_up]
    assert lot_row(older_mid)["qty_remain"] == 0
    assert lot_row(newer_up)["qty_remain"] == 1


def test_confirm_replans_against_current_stock():
    """A preview is not a reservation: after another consume drains stock, the
    original confirm must conflict instead of applying stale numbers."""
    fresh_db()
    add_item(1, "upper")
    lot = add_lot(1, 3, "2026-01-10")
    c = connect()
    preview = preview_consume(c, item_id=1, qty=3)
    assert preview["ok"]
    confirm_consume(c, item_id=1, qty=2)  # someone else got there first
    try:
        confirm_consume(c, item_id=1, qty=3)
        assert False, "stale confirm must not succeed"
    except ConsumeConflict as e:
        assert e.plan["short"] == 2
    finally:
        c.close()
    assert lot_row(lot)["qty_remain"] == 1  # only the real consume landed


def test_concurrent_layer_and_full_entry_never_oversell():
    """One layer-scoped confirm races one whole-fridge confirm for the same
    stock: exactly one wins, the other gets a conflict, qty never goes negative."""
    fresh_db()
    add_item(1, "upper")
    lot = add_lot(1, 5, "2026-01-10")
    barrier = threading.Barrier(2)
    results, errors = [], []

    def worker(layer):
        c = connect()
        barrier.wait(timeout=10)
        try:
            results.append(confirm_consume(c, item_id=1, qty=3, layer=layer))
        except ConsumeConflict as e:
            errors.append(e)
        finally:
            c.close()

    t1 = threading.Thread(target=worker, args=("upper",))   # layer entry
    t2 = threading.Thread(target=worker, args=(None,))      # full-fridge entry
    t1.start(); t2.start(); t1.join(15); t2.join(15)
    assert not t1.is_alive() and not t2.is_alive()
    assert len(results) == 1 and len(errors) == 1
    assert errors[0].plan["short"] == 1
    assert lot_row(lot)["qty_remain"] == 2  # 5 - 3, never negative
    assert consumption_count() == 1


def test_validation():
    fresh_db()
    add_item(1, "upper")
    add_lot(1, 1, "2026-01-10")
    c = connect()
    for exc, kw in [
        (ItemNotFound, dict(item_id=99, qty=1)),
        (UnknownLayer, dict(item_id=1, qty=1, layer="attic")),
        (BadQty, dict(item_id=1, qty=0)),
    ]:
        try:
            preview_consume(c, **kw)
            assert False, f"expected {exc.__name__}"
        except exc:
            pass
    c.close()


def test_migration_adds_layer_column_to_existing_db():
    """A database created before lots.layer existed keeps working: the column
    is added on startup and old lots (NULL) fall back to the item's layer."""
    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    c = connect()
    c.executescript("""
    CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT, layer TEXT, unit TEXT);
    CREATE TABLE lots(
      id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, qty_in REAL, qty_remain REAL,
      expiry TEXT, status TEXT, data_quality TEXT
    );
    CREATE TABLE consumptions(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT, result_json TEXT, created_at TEXT);
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
    INSERT INTO items VALUES (1, 'legacy', 'mid', '个');
    INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality)
      VALUES (1, 2, 2, '2026-01-10', 'on_shelf', 'clean');
    """)
    c.commit()
    c.close()
    init_db()  # startup migration
    c = connect()
    cols = [r["name"] for r in c.execute("PRAGMA table_info(lots)")]
    assert "layer" in cols
    plan = preview_consume(c, item_id=1, qty=1, layer="mid")
    assert plan["ok"] and plan["deductions"][0]["layer"] == "mid"
    c.close()
