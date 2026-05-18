"""SQLite database management for the platform."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from src.core.config import get_config

_local = threading.local()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evaluation_batch (
    id TEXT PRIMARY KEY,
    original_filename TEXT,
    total_rows INTEGER DEFAULT 0,
    status TEXT DEFAULT 'queued',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS customer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT REFERENCES evaluation_batch(id),
    row_index INTEGER,
    company_name TEXT,
    website TEXT,
    country_region TEXT,
    contact_name TEXT,
    contact_email TEXT,
    contact_phone TEXT,
    contact_address TEXT,
    target_products TEXT,
    priority TEXT,
    notes TEXT,
    product_fit_score INTEGER,
    product_fit_reasons TEXT,
    capability_score INTEGER,
    capability_signals TEXT,
    reputation_facts TEXT,
    reputation_concerns TEXT,
    reputation_sources TEXT,
    reputation_safety_score INTEGER,
    buyer_seller_role TEXT,
    buyer_seller_reason TEXT,
    deal_recommendation TEXT,
    next_action TEXT,
    confidence REAL,
    data_quality TEXT,
    fetched_pages TEXT,
    errors TEXT,
    overall_score_computed REAL,
    manual_review_flag TEXT,
    eval_json TEXT,
    email_subject TEXT,
    email_body TEXT,
    email_status TEXT,
    email_sent_at TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_customer_batch ON customer(batch_id);
CREATE INDEX IF NOT EXISTS idx_customer_name ON customer(company_name);
CREATE INDEX IF NOT EXISTS idx_customer_score ON customer(overall_score_computed);
CREATE INDEX IF NOT EXISTS idx_customer_recommendation ON customer(deal_recommendation);
CREATE INDEX IF NOT EXISTS idx_customer_email_status ON customer(email_status);

CREATE TABLE IF NOT EXISTS daily_send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_date TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    customer_id INTEGER,
    status TEXT NOT NULL DEFAULT 'sent',
    sent_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_daily_send_log_date ON daily_send_log(sent_date);

CREATE TABLE IF NOT EXISTS salesperson (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_salesperson_active ON salesperson(is_active);

CREATE TABLE IF NOT EXISTS email_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_id TEXT NOT NULL UNIQUE,
    customer_id INTEGER REFERENCES customer(id),
    send_log_id INTEGER,
    opened_at TEXT DEFAULT (datetime('now','localtime')),
    ip_address TEXT DEFAULT '',
    user_agent TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tracking_tracking_id ON email_tracking(tracking_id);
CREATE INDEX IF NOT EXISTS idx_tracking_customer ON email_tracking(customer_id);
"""


def _ensure_column(db: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
    """Add a column if it does not already exist (SQLite-safe)."""
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def get_db() -> sqlite3.Connection:
    """Get a thread-local SQLite connection."""
    conn = getattr(_local, "connection", None)
    if conn is None:
        cfg = get_config()
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(cfg.db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(SCHEMA_SQL)

        # Safe column migrations for existing databases
        _ensure_column(conn, "customer", "assigned_salesperson_id", "INTEGER REFERENCES salesperson(id)")
        _ensure_column(conn, "customer", "tracking_last_opened_at", "TEXT")
        _ensure_column(conn, "daily_send_log", "salesperson_id", "INTEGER")
        _ensure_column(conn, "daily_send_log", "tracking_id", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_salesperson ON customer(assigned_salesperson_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_send_log_tracking ON daily_send_log(tracking_id)")

        conn.commit()
        _local.connection = conn
    return conn


def close_db() -> None:
    conn = getattr(_local, "connection", None)
    if conn is not None:
        conn.close()
        _local.connection = None


def dict_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def dicts_from_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
