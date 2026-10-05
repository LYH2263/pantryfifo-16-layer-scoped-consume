import sqlite3
import threading

import pytest

from app import seed
from app.db import connect, db_path
from app.modules.consume import run_consume


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    seed.init_db()
    c = connect()
    c.execute("DELETE FROM lots"); c.execute("DELETE FROM items")
    c.execute("DELETE FROM consumptions"); c.commit(); c.close()
    return tmp_path


def setup_cross_layer_item():
    """品项层 upper；批 A 显式在 mid 且最早到期(2)，批 B 继承 upper 到期晚(2)。"""
    c = connect()
    cur = c.execute("INSERT INTO items(name,layer,unit) VALUES ('鸡蛋','upper','个')")
    item_id = cur.lastrowid
    c.execute("INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,layer) "
              "VALUES (?,?,?,?,?,?,?)", (item_id, 2, 2, "2026-10-01", "on_shelf", "clean", "mid"))
    a = c.execute("SELECT id FROM lots WHERE item_id=? AND layer='mid'", (item_id,)).fetchone()[0]
    c.execute("INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,layer) "
              "VALUES (?,?,?,?,?,?,?)", (item_id, 2, 2, "2026-10-05", "on_shelf", "clean", None))
    b = c.execute("SELECT id FROM lots WHERE expiry='2026-10-05'").fetchone()[0]
    c.commit(); c.close()
    return item_id, a, b


def reset_lots(item_id, a, b):
    c = connect()
    c.execute("UPDATE lots SET qty_remain=2, status='on_shelf' WHERE id=?", (a,))
    c.execute("UPDATE lots SET qty_remain=2, status='on_shelf' WHERE id=?", (b,))
    c.execute("DELETE FROM consumptions")
    c.commit(); c.close()


class Box:
    def __init__(self):
        self.value = None
        self.error = None


def _worker(box, **kwargs):
    try:
        box.value = run_consume(**kwargs)
    except Exception as e:  # noqa: BLE001
        box.error = e


def _run_pair(scoped_kwargs, global_kwargs, gated_kwargs=None):
    """跑 scoped/global 两笔；gated_kwargs 标识哪笔挂 _gate（先拿锁者）。"""
    scoped_box, global_box = Box(), Box()
    entered = threading.Event()
    proceed = threading.Event()

    def gate():
        entered.set()
        proceed.wait()

    def make(box, kwargs):
        kw = dict(kwargs)
        if gated_kwargs is not None and kwargs is gated_kwargs:
            kw["_gate"] = gate
        return threading.Thread(target=_worker, args=(box,), kwargs=kw)

    t_scoped = make(scoped_box, scoped_kwargs)
    t_global = make(global_box, global_kwargs)

    if gated_kwargs is not None:
        # gated 线程先启动并持写锁停在重选前，另一笔堵在 BEGIN IMMEDIATE
        gated_thread = t_scoped if gated_kwargs is scoped_kwargs else t_global
        other_started = threading.Event()
        gated_thread.start()
        assert entered.wait(5)

        def start_other():
            other_started.set()
            (t_global if gated_thread is t_scoped else t_scoped).start()

        timer = threading.Timer(0.1, start_other)
        timer.start()
        assert other_started.wait(5)
        timer.join()
        proceed.set()
        gated_thread.join(5)
        (t_global if gated_thread is t_scoped else t_scoped).join(5)
    else:
        barrier = threading.Barrier(2)
        # 无 gate：两笔在屏障后同时抢锁，赢家交给调度
        orig_scoped, orig_global = scoped_kwargs, global_kwargs

        def para(box, kwargs):
            barrier.wait()
            _worker(box, **kwargs)

        t_scoped = threading.Thread(target=para, args=(scoped_box, scoped_kwargs))
        t_global = threading.Thread(target=para, args=(global_box, global_kwargs))
        t_scoped.start(); t_global.start()
        t_scoped.join(5); t_global.join(5)

    assert scoped_box.error is None and global_box.error is None
    return scoped_box, global_box


def _assert_invariants(scoped_box, global_box, a, b):
    s_status, s_body = scoped_box.value
    g_status, g_body = global_box.value
    assert sorted([s_status, g_status]) == [200, 409]  # 恰好一成一败，无超扣

    # 层作用域这笔（成或败）的批号永远只可能属于 upper
    assert {d["layer"] for d in s_body["deductions"]} <= {"upper"}
    assert s_body.get("cross_layer") in (False, None)

    # 回包=写入：用 200 回包的 deductions 从初始(2,2)重放，必须等于库内最终余量
    taken = {a: 0.0, b: 0.0}
    for box in (scoped_box, global_box):
        status, body = box.value
        if status == 200:
            for d in body["deductions"]:
                taken[d["lot_id"]] += d["take"]
    c = connect()
    final = {r[0]: r[1] for r in c.execute("SELECT id, qty_remain FROM lots WHERE id IN (?,?)", (a, b))}
    cons = c.execute("SELECT COUNT(*) n FROM consumptions").fetchone()["n"]
    c.close()
    assert abs(final[a] - (2 - taken[a])) < 1e-9
    assert abs(final[b] - (2 - taken[b])) < 1e-9
    assert final[a] >= 0 and final[b] >= 0
    assert cons == 1  # 只有 200 落审计；409 无副作用


def test_scoped_wins_then_global_short(fresh_db):
    item_id, a, b = setup_cross_layer_item()
    scoped = dict(item_id=item_id, qty=2, layer="upper", dry_run=False)
    glob = dict(item_id=item_id, qty=3, layer=None, dry_run=False)
    scoped_box, global_box = _run_pair(scoped, glob, gated_kwargs=scoped)

    assert scoped_box.value[0] == 200
    assert [d["lot_id"] for d in scoped_box.value[1]["deductions"]] == [b]
    assert global_box.value[0] == 409
    g = global_box.value[1]
    assert g["reason"] == "short" and g["short"] == 1
    assert {d["lot_id"] for d in g["deductions"]} == {a}  # 耗尽候选只列到 A

    c = connect()
    rows = {r[0]: (r[1], r[2]) for r in c.execute("SELECT id, qty_remain, status FROM lots WHERE id IN (?,?)", (a, b))}
    c.close()
    assert rows[a] == (2, "on_shelf")   # 全层 409，mid 批原样
    assert rows[b] == (0, "consumed")


def test_global_wins_then_scoped_short(fresh_db):
    item_id, a, b = setup_cross_layer_item()
    scoped = dict(item_id=item_id, qty=2, layer="upper", dry_run=False)
    glob = dict(item_id=item_id, qty=3, layer=None, dry_run=False)
    scoped_box, global_box = _run_pair(scoped, glob, gated_kwargs=glob)

    assert global_box.value[0] == 200
    g = global_box.value[1]
    assert g["cross_layer"] is True and g["layers_touched"] == ["mid", "upper"]
    assert [(d["lot_id"], d["take"]) for d in g["deductions"]] == [(a, 2), (b, 1)]
    assert scoped_box.value[0] == 409
    s = scoped_box.value[1]
    assert s["reason"] == "short" and s["short"] == 1
    assert {d["lot_id"] for d in s["deductions"]} == {b}

    c = connect()
    rows = {r[0]: (r[1], r[2]) for r in c.execute("SELECT id, qty_remain, status FROM lots WHERE id IN (?,?)", (a, b))}
    c.close()
    assert rows[a] == (0, "consumed")
    assert rows[b] == (1, "on_shelf")  # 本层 409，upper 余量原样停在全层扣后的 1


def test_unsynchronized_races_are_order_independent(fresh_db):
    item_id, a, b = setup_cross_layer_item()
    for _ in range(10):
        reset_lots(item_id, a, b)
        scoped = dict(item_id=item_id, qty=2, layer="upper", dry_run=False)
        glob = dict(item_id=item_id, qty=3, layer=None, dry_run=False)
        scoped_box, global_box = _run_pair(scoped, glob)
        _assert_invariants(scoped_box, global_box, a, b)


def test_lock_timeout_returns_503_without_write(fresh_db, monkeypatch):
    item_id, a, b = setup_cross_layer_item()

    # 把写连接的 busy timeout 缩短到 0.3s，避免测试干等 5s
    import app.modules.consume as svc

    def fast_connect():
        conn = sqlite3.connect(db_path(), timeout=0.3, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(svc, "connect_immediate", fast_connect)

    holder = sqlite3.connect(db_path(), timeout=0.3, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # 主线程占住写锁不释放
    try:
        box = Box()
        t = threading.Thread(target=_worker, args=(box,),
                             kwargs=dict(item_id=item_id, qty=1, dry_run=False))
        t.start(); t.join(5)
    finally:
        holder.execute("ROLLBACK"); holder.close()

    assert box.error is None
    assert box.value == (503, {"detail": "database_busy_retry"})
    c = connect()
    rows = {r[0]: r[1] for r in c.execute("SELECT id, qty_remain FROM lots WHERE id IN (?,?)", (a, b))}
    cons = c.execute("SELECT COUNT(*) n FROM consumptions").fetchone()["n"]
    c.close()
    assert rows == {a: 2, b: 2} and cons == 0  # 超时这笔无任何写入
