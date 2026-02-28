import sqlite3
from pathlib import Path

DB_PATH = Path("data/kalshi.db")


def get_conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS orderbook (
        timestamp TEXT,
        market_id TEXT,
        bid REAL,
        ask REAL,
        mid REAL,
        spread REAL
    )
    """)

    conn.commit()
    conn.close()


def insert_snapshot(row):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO orderbook VALUES (?, ?, ?, ?, ?, ?)
    """, row)

    conn.commit()
    conn.close()