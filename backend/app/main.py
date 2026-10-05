from datetime import date
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect, connect_immediate
from app.engines.fefo import expire_lots
from app.modules.consume import LAYERS, run_consume

app = FastAPI(title="Pantryfifo", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FRIDGE_SELECT = """SELECT lots.id, lots.item_id, lots.qty_in, lots.qty_remain,
       lots.expiry, lots.status, lots.data_quality,
       lots.layer AS lot_layer, items.name, items.layer AS item_layer, items.unit,
       COALESCE(lots.layer, items.layer) AS layer,
       CASE WHEN lots.layer IS NOT NULL AND lots.layer<>items.layer THEN 1 ELSE 0 END AS layer_mismatch
FROM lots JOIN items ON items.id=lots.item_id
WHERE lots.status='on_shelf'"""

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/fridge")
def fridge(layer: str | None = None):
    c = connect()
    q = FRIDGE_SELECT
    args = []
    if layer:
        if layer not in LAYERS:
            c.close(); raise HTTPException(400, "invalid_layer")
        # 层页过滤按批的有效层，不再按 items.layer（批层可与品项层不一致）
        q += " AND COALESCE(lots.layer, items.layer)=?"; args.append(layer)
    rows = [dict(r) for r in c.execute(q, args)]; c.close(); return rows

@app.get("/api/alerts")
def alerts():
    c = connect()
    warn = int(c.execute("SELECT value FROM settings WHERE key='warn_days'").fetchone()["value"])
    today = date.today().isoformat()
    rows = [dict(r) for r in c.execute(
        """SELECT lots.id, lots.item_id, lots.qty_in, lots.qty_remain, lots.expiry,
                  lots.status, lots.data_quality, lots.layer AS lot_layer,
                  items.name, items.layer AS item_layer,
                  COALESCE(lots.layer, items.layer) AS layer,
                  CASE WHEN lots.layer IS NOT NULL AND lots.layer<>items.layer THEN 1 ELSE 0 END AS layer_mismatch
           FROM lots JOIN items ON items.id=lots.item_id
           WHERE status='on_shelf' AND qty_remain>0 AND expiry IS NOT NULL""")]
    c.close()
    out = []
    for r in rows:
        if r["expiry"] <= today:
            r["level"] = "expired"
            out.append(r)
        else:
            delta = (date.fromisoformat(r["expiry"]) - date.today()).days
            if delta <= warn:
                r["level"] = "soon"; r["days_left"] = delta; out.append(r)
    return out

class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str
    layer: str | None = None  # 留空=继承品项层

@app.post("/api/lots")
def inbound(body: LotIn):
    if body.layer is not None and body.layer not in LAYERS:
        raise HTTPException(400, "invalid_layer")
    c = connect()
    item = c.execute("SELECT id FROM items WHERE id=?", (body.item_id,)).fetchone()
    if not item: c.close(); raise HTTPException(404, "item")
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,layer) VALUES (?,?,?,?,?,?,?)",
        (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean", body.layer))
    c.commit(); lid = cur.lastrowid; c.close(); return {"id": lid}

class ExpectedDeduction(BaseModel):
    lot_id: int
    take: float

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    note: str = ""
    layer: str | None = None            # 非空=层页本层消费；None=全层入口可跨层
    expected_deductions: list[ExpectedDeduction] | None = None

def _dispatch(body: ConsumeIn, dry_run: bool):
    expected = None
    if body.expected_deductions is not None:
        expected = [d.model_dump() for d in body.expected_deductions]
    status, resp = run_consume(item_id=body.item_id, qty=body.qty, layer=body.layer,
                               dry_run=dry_run, note=body.note,
                               expected_deductions=expected)
    if status != 200:
        # 400/404/503 的 resp 是 {"detail": 标记串}；409 的 resp 本身是 plan
        detail = resp.get("detail", resp) if isinstance(resp, dict) else resp
        raise HTTPException(status, detail)
    return resp

@app.post("/api/consume/preview")
def consume_preview(body: ConsumeIn):
    # 只读预演：恒 200，short 在 body.ok=false 中表达
    return _dispatch(body, True)

@app.post("/api/consume")
def consume(body: ConsumeIn):
    # 确认：BEGIN IMMEDIATE 锁内重算后写入，回包即实际写入
    return _dispatch(body, False)

@app.post("/api/expire-sweep")
def expire_sweep():
    c = connect_immediate()
    began = False
    try:
        c.execute("BEGIN IMMEDIATE")
        began = True
        lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
        ids = expire_lots(lots, date.today().isoformat())
        for i in ids:
            c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
        c.execute("COMMIT")
        began = False
    finally:
        if began:
            c.execute("ROLLBACK")
        c.close()
    return {"expired_ids": ids}

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
