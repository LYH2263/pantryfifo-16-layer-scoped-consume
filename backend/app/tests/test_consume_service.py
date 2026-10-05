import json
import sqlite3

import pytest

from app import seed
from app.db import connect, db_path
from app.modules.consume import run_consume


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    seed.init_db()
    c = connect()
    c.execute("DELETE FROM lots")
    c.execute("DELETE FROM items")
    c.execute("DELETE FROM consumptions")
    c.commit()
    c.close()
    return tmp_path


def add_item(name="鸡蛋", layer="mid", unit="个"):
    c = connect()
    cur = c.execute("INSERT INTO items(name,layer,unit) VALUES (?,?,?)", (name, layer, unit))
    c.commit()
    item_id = cur.lastrowid
    c.close()
    return item_id

def add_lot(item_id, qty, expiry, lot_layer=None, status="on_shelf", dq="clean"):
    c = connect()
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,layer) VALUES (?,?,?,?,?,?,?)",
        (item_id, qty, qty, expiry, status, dq, lot_layer))
    c.commit()
    lid = cur.lastrowid
    c.close()
    return lid

def lot_row(lot_id):
    c = connect()
    r = dict(c.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone())
    c.close()
    return r

def consumption_count():
    c = connect()
    n = c.execute("SELECT COUNT(*) c FROM consumptions").fetchone()["c"]
    c.close()
    return n


def test_migration_from_legacy_schema(monkeypatch, tmp_path):
    d = tmp_path / "legacy"
    monkeypatch.setenv("DATA_DIR", str(d))
    # 手工造旧库：lots 无 layer 列
    c = sqlite3.connect(db_path())
    c.executescript("""
    CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT, layer TEXT, unit TEXT);
    CREATE TABLE lots(id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, qty_in REAL,
                      qty_remain REAL, expiry TEXT, status TEXT, data_quality TEXT);
    CREATE TABLE consumptions(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT, result_json TEXT, created_at TEXT);
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
    """)
    c.execute("INSERT INTO items(name,layer,unit) VALUES ('鸡蛋','mid','个')")
    c.execute("INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) "
              "VALUES (1,5,5,'2026-11-01','on_shelf','clean')")
    c.commit(); c.close()

    seed.init_db()
    c = sqlite3.connect(db_path())
    cols = [r[1] for r in c.execute("PRAGMA table_info(lots)")]
    assert "layer" in cols
    # 旧批继承品项层（NULL），数据不丢
    row = c.execute("SELECT qty_remain, layer FROM lots WHERE item_id=1").fetchone()
    assert row == (5, None)
    c.close()
    # 再跑一次不报错（迁移幂等）
    seed.init_db()


def test_wal_enabled(fresh_db):
    c = connect()
    mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    c.close()
    assert mode == "wal"


def test_scoped_confirm_only_touches_own_layer(fresh_db):
    item_id = add_item(layer="mid")
    a = add_lot(item_id, 5, "2026-11-01", lot_layer="mid")
    b = add_lot(item_id, 5, "2026-10-07", lot_layer="upper")  # 更早到期但在邻层

    status, resp = run_consume(item_id=item_id, qty=3, layer="mid", dry_run=False)
    assert status == 200 and resp["ok"]
    assert [d["lot_id"] for d in resp["deductions"]] == [a]
    assert resp["cross_layer"] is False and resp["layers_touched"] == ["mid"]
    assert resp["drifted"] is None

    assert lot_row(a)["qty_remain"] == 2 and lot_row(a)["status"] == "on_shelf"
    assert lot_row(b)["qty_remain"] == 5  # 邻层更早到期批原样


def test_preview_and_409_write_nothing(fresh_db):
    item_id = add_item(layer="mid")
    a = add_lot(item_id, 1, "2026-11-01", lot_layer="mid")
    b = add_lot(item_id, 5, "2026-10-07", lot_layer="upper")
    before = (lot_row(a)["qty_remain"], lot_row(b)["qty_remain"])
    assert consumption_count() == 0

    status, preview = run_consume(item_id=item_id, qty=3, layer="mid", dry_run=True)
    assert status == 200 and preview["ok"] is False and preview["reason"] == "short"
    assert preview["available"] == 1 and preview["short"] == 2
    assert {d["lot_id"] for d in preview["deductions"]} == {a}
    assert (lot_row(a)["qty_remain"], lot_row(b)["qty_remain"]) == before
    assert consumption_count() == 0  # 预演不留痕

    status, body = run_consume(item_id=item_id, qty=3, layer="mid", dry_run=False)
    assert status == 409 and body["reason"] == "short"
    assert (lot_row(a)["qty_remain"], lot_row(b)["qty_remain"]) == before
    assert consumption_count() == 0  # short 整单不写


def test_drifted_flag(fresh_db):
    item_id = add_item(layer="mid")
    a = add_lot(item_id, 5, "2026-11-01", lot_layer="mid")

    status, resp = run_consume(item_id=item_id, qty=2, layer="mid", dry_run=False,
                               expected_deductions=[{"lot_id": a, "take": 9}])
    assert status == 200 and resp["drifted"] is True

    b = add_lot(item_id, 3, "2026-12-01", lot_layer="mid")
    status, resp = run_consume(item_id=item_id, qty=1, layer="mid", dry_run=False,
                               expected_deductions=[{"lot_id": a, "take": 1}])
    assert status == 200 and resp["drifted"] is False
    assert resp["final_remaining"][0]["lot_id"] == a


def test_cross_layer_confirm_audit_record(fresh_db):
    item_id = add_item(layer="upper")
    a = add_lot(item_id, 2, "2026-10-01", lot_layer="mid")
    b = add_lot(item_id, 2, "2026-10-05")  # NULL 继承 upper
    status, resp = run_consume(item_id=item_id, qty=3, dry_run=False, note="x")
    assert status == 200
    assert resp["cross_layer"] is True
    assert resp["layers_touched"] == ["mid", "upper"]
    assert [d["lot_id"] for d in resp["deductions"]] == [a, b]
    assert lot_row(a)["status"] == "consumed" and lot_row(a)["qty_remain"] == 0
    assert lot_row(b)["qty_remain"] == 1
    c = connect()
    stored = json.loads(c.execute("SELECT result_json FROM consumptions WHERE id=?",
                                  (resp["consumption_id"],)).fetchone()["result_json"])
    c.close()
    assert stored["cross_layer"] is True and stored["scope_layer"] is None


def test_validation(fresh_db):
    item_id = add_item()
    for dry in (True, False):
        status, body = run_consume(item_id=item_id, qty=1, layer="sideways", dry_run=dry)
        assert status == 400 and body["detail"] == "invalid_layer"
        status, body = run_consume(item_id=item_id, qty=0, dry_run=dry)
        assert status == 400 and body["detail"] == "qty_non_positive"
        status, body = run_consume(item_id=99999, qty=1, dry_run=dry)
        assert status == 404 and body["detail"] == "item"
    assert consumption_count() == 0
