"""SQLite database models for user accounts, Razorpay connections, and reconciliation runs."""

from __future__ import annotations

import json
import sqlite3
import hashlib
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "afc.db"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS razorpay_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            key_id TEXT NOT NULL,
            key_secret TEXT NOT NULL,
            is_test_mode INTEGER NOT NULL DEFAULT 1,
            connected_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id)
        );

        CREATE TABLE IF NOT EXISTS bank_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            bank_name TEXT NOT NULL,
            filename TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            razorpay_rows INTEGER NOT NULL DEFAULT 0,
            other_gateway_rows INTEGER NOT NULL DEFAULT 0,
            uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
            csv_data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reconciliation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            run_at TEXT NOT NULL DEFAULT (datetime('now')),
            results_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed'
        );
    """)
    conn.commit()
    conn.close()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = os.urandom(32).hex()
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return hashed, salt


def create_user(email: str, password: str) -> dict | None:
    conn = get_db()
    try:
        hashed, salt = hash_password(password)
        cursor = conn.execute(
            "INSERT INTO users (email, password_hash, salt) VALUES (?, ?, ?)",
            (email, hashed, salt),
        )
        conn.commit()
        return {"id": cursor.lastrowid, "email": email}
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def verify_user(email: str, password: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not row:
        return None
    hashed, _ = hash_password(password, row["salt"])
    if hashed != row["password_hash"]:
        return None
    return {"id": row["id"], "email": row["email"]}


def save_razorpay_connection(user_id: int, key_id: str, key_secret: str) -> None:
    conn = get_db()
    conn.execute(
        """INSERT INTO razorpay_connections (user_id, key_id, key_secret)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET key_id=?, key_secret=?, connected_at=datetime('now')""",
        (user_id, key_id, key_secret, key_id, key_secret),
    )
    conn.commit()
    conn.close()


def get_razorpay_connection(user_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM razorpay_connections WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_bank_upload(
    user_id: int, bank_name: str, filename: str,
    row_count: int, razorpay_rows: int, other_gateway_rows: int, csv_data: str,
) -> int:
    conn = get_db()
    cursor = conn.execute(
        """INSERT INTO bank_uploads
           (user_id, bank_name, filename, row_count, razorpay_rows, other_gateway_rows, csv_data)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, bank_name, filename, row_count, razorpay_rows, other_gateway_rows, csv_data),
    )
    conn.commit()
    upload_id = cursor.lastrowid
    conn.close()
    return upload_id


def get_bank_uploads(user_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, bank_name, filename, row_count, razorpay_rows, other_gateway_rows, uploaded_at FROM bank_uploads WHERE user_id = ? ORDER BY uploaded_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bank_upload_data(upload_id: int, user_id: int) -> str | None:
    conn = get_db()
    row = conn.execute(
        "SELECT csv_data FROM bank_uploads WHERE id = ? AND user_id = ?",
        (upload_id, user_id),
    ).fetchone()
    conn.close()
    return row["csv_data"] if row else None


def save_reconciliation_run(user_id: int, results_json: str, metrics_json: str) -> int:
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO reconciliation_runs (user_id, results_json, metrics_json) VALUES (?, ?, ?)",
        (user_id, results_json, metrics_json),
    )
    conn.commit()
    run_id = cursor.lastrowid
    conn.close()
    return run_id


def get_latest_reconciliation(user_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM reconciliation_runs WHERE user_id = ? ORDER BY run_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "run_at": row["run_at"],
        "results": json.loads(row["results_json"]),
        "metrics": json.loads(row["metrics_json"]),
        "status": row["status"],
    }
