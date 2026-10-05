import os, sqlite3
from pathlib import Path

BUSY_TIMEOUT_S = 5.0

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "pantryfifo.db"

def connect():
    # legacy 隐式事务连接：现有端点依赖 DML 自动 BEGIN + commit()
    c = sqlite3.connect(db_path(), timeout=BUSY_TIMEOUT_S)
    c.row_factory = sqlite3.Row
    return c

def connect_immediate():
    # 写事务专用：autocommit 模式，事务边界全部手写 BEGIN IMMEDIATE/COMMIT/ROLLBACK。
    # 不能在 legacy 连接上手写 BEGIN（会撞 "cannot start a transaction within a transaction"）。
    c = sqlite3.connect(db_path(), timeout=BUSY_TIMEOUT_S, isolation_level=None)
    c.row_factory = sqlite3.Row
    return c
