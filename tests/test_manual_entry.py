"""Manual entry tests.

@context  Batch 3.2: the keyboard-fetcher must be as disciplined as the real
          ones — MANUAL series only, as-of dates, append-only.
@done     Store + dates, duplicate refusal message, non-MANUAL rejection,
          unknown-series rejection.
@todo     —
@limits   Offline; real signals.yaml (manager_cash is genuinely MANUAL).
@affects  manual_entry.py.
"""

import pytest

import manual_entry
from src import db


def test_store_and_duplicate(tmp_path):
    db_path = tmp_path / "t.db"
    msg = manual_entry.store("manager_cash", 3.6, "2026-07-15",
                             db_path=db_path)
    assert "stored manager_cash = 3.6" in msg
    conn = db.connect(db_path)
    row = conn.execute("SELECT data_date, pub_date, value FROM observations"
                       " WHERE series_id = 'manager_cash'").fetchone()
    assert row == ("2026-07-15", "2026-07-15", 3.6)
    conn.close()
    again = manual_entry.store("manager_cash", 3.7, "2026-07-15",
                               db_path=db_path)
    assert "already recorded" in again


def test_rejects_fetched_series(tmp_path):
    with pytest.raises(SystemExit, match="not a MANUAL series"):
        manual_entry.store("credit_spread", 1.0, db_path=tmp_path / "t.db")


def test_rejects_unknown_series(tmp_path):
    with pytest.raises(SystemExit, match="manager_cash"):
        manual_entry.store("nonsense", 1.0, db_path=tmp_path / "t.db")
