"""SQLite setup. Uses the standard library only (no external DB or ORM needed)."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from backend.config import get_settings

logger = logging.getLogger("invoice_ai.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename   TEXT NOT NULL,
    stored_filename     TEXT NOT NULL,
    mime_type           TEXT,
    page_count          INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'processing',   -- processing | completed | needs_review | failed
    validation_status   TEXT,                                 -- valid | needs_review
    error_message       TEXT,
    ocr_engine          TEXT,
    ocr_mean_confidence REAL,
    manually_edited     INTEGER NOT NULL DEFAULT 0,

    invoice_number          TEXT,
    invoice_date            TEXT,
    due_date                TEXT,
    purchase_order_number   TEXT,
    reference_number        TEXT,
    currency                TEXT,
    invoice_type            TEXT,
    payment_terms           TEXT,

    supplier_name    TEXT, supplier_address TEXT, supplier_email TEXT, supplier_phone TEXT, supplier_tax_id TEXT, supplier_registration_number TEXT,
    customer_name    TEXT, customer_address TEXT, customer_email TEXT, customer_phone TEXT, customer_tax_id TEXT, customer_registration_number TEXT,

    subtotal REAL, taxable_amount REAL, discount REAL, cgst REAL, sgst REAL, igst REAL, other_charges REAL,
    shipping REAL, tax REAL, round_off REAL, grand_total REAL, amount_paid REAL, balance_due REAL,

    validation_json   TEXT,
    raw_ai_json       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    line_no     INTEGER NOT NULL,
    item_code TEXT, description TEXT, hsn_sac TEXT, quantity REAL, unit TEXT, unit_price REAL,
    discount REAL, tax_rate REAL, tax REAL, total REAL
);

CREATE TABLE IF NOT EXISTS processing_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER REFERENCES invoices(id) ON DELETE CASCADE,
    stage       TEXT NOT NULL,
    level       TEXT NOT NULL DEFAULT 'info',
    message     TEXT,
    duration_ms INTEGER,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_invoice ON invoice_items(invoice_id);
CREATE INDEX IF NOT EXISTS idx_logs_invoice ON processing_logs(invoice_id);
CREATE INDEX IF NOT EXISTS idx_invoices_created ON invoices(created_at);
"""


def connect() -> sqlite3.Connection:
    s = get_settings()
    s.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(s.database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db() -> None:
    """Create or migrate tables without deleting existing invoice history."""
    get_settings().ensure_dirs()
    with get_db() as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn, "invoices", {
            "reference_number": "TEXT", "payment_terms": "TEXT",
            "supplier_registration_number": "TEXT", "customer_registration_number": "TEXT",
            "taxable_amount": "REAL", "round_off": "REAL",
        })
        _ensure_columns(conn, "invoice_items", {
            "item_code": "TEXT", "hsn_sac": "TEXT", "tax_rate": "REAL",
        })
    logger.info("Database ready at %s", get_settings().database_path)


def check_db() -> bool:
    try:
        with get_db() as conn:
            conn.execute("SELECT 1 FROM invoices LIMIT 1")
        return True
    except Exception:
        return False
