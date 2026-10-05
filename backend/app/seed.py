from app.db import connect

LOT_LAYER_SQL = "ALTER TABLE lots ADD COLUMN layer TEXT"

def init_db():
    c = connect()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, name TEXT, layer TEXT, unit TEXT);
    CREATE TABLE IF NOT EXISTS lots(
      id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, qty_in REAL, qty_remain REAL,
      expiry TEXT, status TEXT, data_quality TEXT, layer TEXT
    );
    CREATE TABLE IF NOT EXISTS consumptions(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT, result_json TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    """)
    # 幂等迁移：旧库 lots 没有 layer 列（NULL=继承品项层）
    cols = [r["name"] for r in c.execute("PRAGMA table_info(lots)")]
    if "layer" not in cols:
        c.execute(LOT_LAYER_SQL)
        c.commit()
    # WAL 持久化在文件头：读不阻塞写，写者之间仍由 BEGIN IMMEDIATE 串行
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    if c.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0:
        c.executemany("INSERT INTO items(name,layer,unit) VALUES (?,?,?)", [
            ("牛奶", "upper", "盒"), ("鸡蛋", "mid", "个"), ("冻饺", "lower", "袋"),
        ])
        c.executemany(
            "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,layer) VALUES (?,?,?,?,?,?,?)",
            [
                (1, 2, 2, "2026-10-01", "on_shelf", "clean", None),
                (1, 1, 1, "2026-09-28", "on_shelf", "clean", None),
                (2, 12, 12, "2026-11-01", "on_shelf", "clean", None),
                (3, 1, 1, "2025-01-01", "on_shelf", "dirty", None),
                (2, -3, -3, "2026-12-01", "on_shelf", "dirty", None),
                # 鸡蛋品项层为 mid，这批显式放在 upper 且更早到期：
                # 全层入口会跨层先扣它，中层页本层消费必须跳过它
                (2, 4, 4, "2026-10-07", "on_shelf", "clean", "upper"),
            ],
        )
        c.execute("INSERT INTO settings(key,value) VALUES ('warn_days','3')")
        c.commit()
    c.close()
