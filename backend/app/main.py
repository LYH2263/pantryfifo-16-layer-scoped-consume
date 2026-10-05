from datetime import date
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.fefo import expire_lots
from app.services.consume import (
    BadQty, ConsumeBusy, ConsumeConflict, ItemNotFound, UnknownLayer,
    confirm_consume, preview_consume,
)

app = FastAPI(title="Pantryfifo", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/fridge")
def fridge(layer: str | None = None):
    """On-shelf lots placed by EFFECTIVE layer (lot layer wins over item layer),
    so the full-fridge columns and the per-layer pages always agree."""
    c = connect()
    q = """SELECT lots.id, lots.item_id, lots.qty_in, lots.qty_remain, lots.expiry,
                  lots.status, lots.data_quality,
                  lots.layer AS lot_layer, items.layer AS item_layer,
                  COALESCE(lots.layer, items.layer) AS layer,
                  items.name, items.unit
           FROM lots JOIN items ON items.id=lots.item_id WHERE lots.status='on_shelf'"""
    args = []
    if layer:
        q += " AND COALESCE(lots.layer, items.layer)=?"; args.append(layer)
    rows = [dict(r) for r in c.execute(q, args)]; c.close(); return rows

@app.get("/api/alerts")
def alerts():
    c = connect()
    warn = int(c.execute("SELECT value FROM settings WHERE key='warn_days'").fetchone()["value"])
    today = date.today().isoformat()
    rows = [dict(r) for r in c.execute(
        """SELECT lots.id, lots.item_id, lots.qty_remain, lots.expiry,
                  COALESCE(lots.layer, items.layer) AS layer, items.name
           FROM lots JOIN items ON items.id=lots.item_id
           WHERE status='on_shelf' AND qty_remain>0 AND expiry IS NOT NULL""")]
    c.close()
    out = []
    for r in rows:
        if r["expiry"] <= today:
            r["level"] = "expired"
            out.append(r)
        else:
            # simple day diff via fromisoformat
            delta = (date.fromisoformat(r["expiry"]) - date.today()).days
            if delta <= warn:
                r["level"] = "soon"; r["days_left"] = delta; out.append(r)
    return out

class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str

@app.post("/api/lots")
def inbound(body: LotIn):
    c = connect()
    item = c.execute("SELECT id, layer FROM items WHERE id=?", (body.item_id,)).fetchone()
    if not item: c.close(); raise HTTPException(404, "item")
    # pin the new lot to the item's current layer; later divergence is explicit
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,layer) VALUES (?,?,?,?,?,?,?)",
        (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean", item["layer"]))
    c.commit(); lid = cur.lastrowid; c.close(); return {"id": lid}

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    layer: str | None = None   # scope to one layer; None = whole fridge
    borrow: bool = False       # layer-scoped only: spill into other layers when short
    note: str = ""

def _consume(body: ConsumeIn, commit: bool):
    c = connect()
    try:
        if commit:
            return confirm_consume(c, item_id=body.item_id, qty=body.qty,
                                   layer=body.layer, borrow=body.borrow, note=body.note)
        return preview_consume(c, item_id=body.item_id, qty=body.qty,
                               layer=body.layer, borrow=body.borrow)
    except ItemNotFound:
        raise HTTPException(404, "item")
    except UnknownLayer:
        raise HTTPException(400, "unknown_layer")
    except BadQty:
        raise HTTPException(400, "qty_non_positive")
    except ConsumeConflict as e:
        # 409 carries the fresh plan: caller sees the shortage and what WOULD
        # be deducted now — never a stale success.
        raise HTTPException(409, detail=e.plan)
    except ConsumeBusy:
        raise HTTPException(409, detail={"ok": False, "reason": "busy"})
    finally:
        c.close()

@app.post("/api/consume/preview")
def consume_preview(body: ConsumeIn):
    """Dry-run: lots that WOULD be deducted, per layer. Writes nothing, so the
    per-layer numbers stay put until confirm."""
    return _consume(body, commit=False)

@app.post("/api/consume/confirm")
def consume_confirm(body: ConsumeIn):
    """Atomic re-plan + write. Response lists every lot/layer actually hit."""
    return _consume(body, commit=True)

@app.post("/api/consume")
def consume_legacy(body: ConsumeIn):
    """Back-compat alias of /api/consume/confirm (whole-fridge FEFO by default)."""
    return _consume(body, commit=True)

@app.post("/api/expire-sweep")
def expire_sweep():
    c = connect()
    lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
    ids = expire_lots(lots, date.today().isoformat())
    for i in ids:
        c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
    c.commit(); c.close(); return {"expired_ids": ids}

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
