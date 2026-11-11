"""SQLite storage: the 'proof locker'. One file, no database server."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE,
    capture_id TEXT,
    item_name TEXT,
    item_type TEXT DEFAULT 'physical',      -- physical | service
    amount REAL,
    currency TEXT DEFAULT 'USD',
    buyer_email TEXT,
    created_at TEXT,
    source TEXT DEFAULT 'live',             -- live | history
    category TEXT,
    category_reason TEXT,
    risk_score INTEGER,
    risk_level TEXT,
    risk_reasons TEXT,                      -- JSON list
    carrier TEXT,
    tracking_number TEXT,
    tracking_sent_at TEXT,
    delivered INTEGER DEFAULT 0,
    completed_at TEXT,
    refunded_or_disputed INTEGER DEFAULT 0,
    explanation TEXT,
    support_message TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER,
    author TEXT,                            -- buyer | seller
    text TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS disputes (
    id TEXT PRIMARY KEY,
    sale_id INTEGER,
    reason TEXT,
    status TEXT,
    amount REAL,
    buyer_note TEXT,
    raw TEXT,
    ai_draft TEXT,
    submitted_at TEXT,
    submit_result TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    event_type TEXT,
    received_at TEXT,
    raw TEXT
);
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT,                              -- seller | assistant
    text TEXT,
    proposal TEXT,                          -- JSON: pending action waiting for seller approval
    created_at TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


# Columns added after the first version; added to older database files automatically.
_EXTRA_SALE_COLUMNS = {
    "hold_status": "TEXT",      # None | held | released  (sample data: sandbox can't create real holds)
    "hold_note": "TEXT",
    "alert_sent_at": "TEXT",
    "alert_error": "TEXT",
}


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(sales)")}
        for col, typ in _EXTRA_SALE_COLUMNS.items():
            if col not in have:
                c.execute(f"ALTER TABLE sales ADD COLUMN {col} {typ}")


def count_demo_history() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM sales WHERE source='demo'").fetchone()[0]


def delete_demo_history():
    with conn() as c:
        c.execute("DELETE FROM sales WHERE source='demo'")


def row_to_sale(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    d["risk_reasons"] = json.loads(d["risk_reasons"] or "[]")
    return d


def get_sale(sale_id: int) -> dict | None:
    with conn() as c:
        return row_to_sale(c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone())


def get_sale_by_order(order_id: str) -> dict | None:
    with conn() as c:
        return row_to_sale(c.execute("SELECT * FROM sales WHERE order_id=?", (order_id,)).fetchone())


def get_sale_by_capture(capture_id: str) -> dict | None:
    with conn() as c:
        return row_to_sale(c.execute("SELECT * FROM sales WHERE capture_id=?", (capture_id,)).fetchone())


def list_sales(live_only: bool = True) -> list[dict]:
    q = "SELECT * FROM sales" + (" WHERE source='live'" if live_only else "") + " ORDER BY created_at DESC"
    with conn() as c:
        return [row_to_sale(r) for r in c.execute(q).fetchall()]


def insert_sale(**fields) -> int:
    keys = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with conn() as c:
        cur = c.execute(f"INSERT OR IGNORE INTO sales ({keys}) VALUES ({marks})", tuple(fields.values()))
        if cur.lastrowid and cur.rowcount:
            return cur.lastrowid
        r = c.execute("SELECT id FROM sales WHERE order_id=?", (fields.get("order_id"),)).fetchone()
        return r["id"]


def update_sale(sale_id: int, **fields):
    if "risk_reasons" in fields and not isinstance(fields["risk_reasons"], str):
        fields["risk_reasons"] = json.dumps(fields["risk_reasons"])
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE sales SET {sets} WHERE id=?", (*fields.values(), sale_id))


def add_message(sale_id: int, author: str, text: str):
    with conn() as c:
        c.execute("INSERT INTO messages (sale_id, author, text, created_at) VALUES (?,?,?,?)",
                  (sale_id, author, text, now()))


def messages_for(sale_id: int) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM messages WHERE sale_id=? ORDER BY created_at", (sale_id,)).fetchall()]


def save_event(event_id: str, event_type: str, raw: str) -> bool:
    """Returns False if this webhook event was already handled (PayPal can resend)."""
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO events (id, event_type, received_at, raw) VALUES (?,?,?,?)",
                        (event_id, event_type, now(), raw))
        return cur.rowcount == 1


def upsert_dispute(**f):
    keys = list(f)
    with conn() as c:
        c.execute(
            f"INSERT INTO disputes ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)}) "
            f"ON CONFLICT(id) DO UPDATE SET " + ", ".join(f"{k}=excluded.{k}" for k in keys if k != "id"),
            tuple(f.values()))


def update_dispute(dispute_id: str, **f):
    with conn() as c:
        c.execute(f"UPDATE disputes SET {', '.join(f'{k}=?' for k in f)} WHERE id=?", (*f.values(), dispute_id))


def list_disputes() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM disputes ORDER BY rowid DESC").fetchall()]


def get_dispute(dispute_id: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM disputes WHERE id=?", (dispute_id,)).fetchone()
        return dict(r) if r else None


def add_chat(role: str, text: str, proposal: dict | None = None) -> int:
    with conn() as c:
        cur = c.execute("INSERT INTO chat (role, text, proposal, created_at) VALUES (?,?,?,?)",
                        (role, text, json.dumps(proposal) if proposal else None, now()))
        return cur.lastrowid


def chat_history(limit: int = 40) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM chat ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in reversed(rows):
        d = dict(r)
        d["proposal"] = json.loads(d["proposal"]) if d["proposal"] else None
        out.append(d)
    return out


def clear_proposal(chat_id: int, note: str):
    with conn() as c:
        c.execute("UPDATE chat SET proposal=NULL, text=text || ? WHERE id=?", (f"\n\n{note}", chat_id))


def get_chat(chat_id: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM chat WHERE id=?", (chat_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["proposal"] = json.loads(d["proposal"]) if d["proposal"] else None
    return d
