"""Shared fetcher helpers — series rows + append-only observation inserts.

@context  Every fetcher stores per-market/per-component observations under
          derived series_ids (e.g. fred_walcl, cot_gold). The observations FK
          requires a series row, so fetchers ensure one exists first.
@done     ensure_series_row (INSERT OR IGNORE, notes mark the parent signal),
          insert_observations (INSERT OR IGNORE, returns rows actually added —
          idempotency lives HERE, tested once).
@todo     —
@limits   Append-only: never UPDATE or DELETE observations. No network.
@affects  src/fetchers/fred.py (and 1.3/1.4 fetchers); signals.db.
"""

import sqlite3


def ensure_series_row(conn: sqlite3.Connection, series_id: str,
                      entry: dict, note: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO series VALUES (?, ?, ?, ?, ?, ?)",
        (series_id, entry["source"], entry["source_url"],
         entry["schedule"], entry["window"], note))


def insert_observations(conn: sqlite3.Connection, series_id: str,
                        rows: list[tuple[str, str, float]]) -> int:
    """rows = (data_date, pub_date, value). Returns count actually added."""
    added = 0
    for data_date, pub_date, value in rows:
        cur = conn.execute(
            "INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?)",
            (series_id, data_date, pub_date, value))
        added += cur.rowcount
    return added
