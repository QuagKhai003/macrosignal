"""XBRL earnings fetcher + F9 semis engine tests (semis batch B).

@context  Theme profit from 10-K XBRL facts → annual sum (>=8 of 10) →
          weekly E_t ratio → the F9 engine (not-dear AND profits rising),
          honest None until the 20-yr percentile window matures.
@done     FY/10-K filter + earliest-filing dedupe, zero-rows loud; annual
          sum with the >=8 gate + pub=max; E_t as-of visibility; engine
          None-immature / cheap-rising ✓ / dear ✗ / falling-profits ✗.
@todo     —
@limits   Offline; the mature-window engine cases seed synthetic series.
@affects  src/fetchers/earnings.py, src/spine.py, src/drivers.py.
"""

import datetime as dt

import pytest

from src import db, drivers, spine

from src.fetchers import earnings

PAYLOAD = {"units": {"USD": [
    {"end": "2025-12-28", "filed": "2026-02-10", "val": 1000.0,
     "form": "10-K", "fp": "FY"},
    {"end": "2025-12-28", "filed": "2027-02-09", "val": 1000.0,
     "form": "10-K", "fp": "FY"},                      # comparative repeat
    {"end": "2025-09-28", "filed": "2025-11-01", "val": 250.0,
     "form": "10-Q", "fp": "Q3"},                      # quarterly: skipped
    {"end": "2024-12-29", "filed": "2025-02-11", "val": 800.0,
     "form": "10-K", "fp": "FY"},
]}}


def test_annual_rows_filters_and_dedupes():
    rows = earnings._annual_rows(PAYLOAD)
    assert rows == [("2024-12-29", "2025-02-11", 800.0),
                    ("2025-12-28", "2026-02-10", 1000.0)]


def test_annual_rows_bad_payload_empty():
    assert earnings._annual_rows({"nope": 1}) == []


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def seed_series(conn, sid, rows):
    conn.execute("INSERT OR IGNORE INTO series VALUES (?,'s','u','weekly',"
                 "'rolling20y','')", (sid,))
    conn.executemany("INSERT INTO observations VALUES (?,?,?,?)",
                     [(sid, d, p, float(v)) for d, p, v in rows])


def test_semis_earnings_needs_eight_companies(conn):
    for i, t in enumerate(spine.SEMIS_TICKERS[:7]):  # only 7 filers
        seed_series(conn, f"earn_{t}",
                    [("2025-12-31", "2026-02-01", 100.0 + i)])
    assert spine.derive_semis_earnings(conn, "2026-07-19") == 0


def test_semis_earnings_sums_with_max_pub(conn):
    for i, t in enumerate(spine.SEMIS_TICKERS[:8]):
        seed_series(conn, f"earn_{t}",
                    [(f"2025-{6 + i % 2:02d}-30", f"2025-{8 + i % 2:02d}-01",
                      100.0)])
    assert spine.derive_semis_earnings(conn, "2026-07-19") == 1
    row = conn.execute("SELECT data_date, pub_date, value FROM observations"
                       " WHERE series_id = 'semis_earnings'").fetchone()
    assert row == ("2025-12-31", "2025-09-01", 800.0)  # latest filer gates


def test_semis_valuation_as_of_visibility(conn):
    # 8 annual totals published, then a weekly price BEFORE most filings
    years = [(f"{y}-12-31", f"{y + 1}-02-01", 1000.0)
             for y in range(2018, 2026)]
    seed_series(conn, "semis_earnings", years)
    seed_series(conn, "price_semis", [("2020-01-08", "2020-01-08", 3000.0),
                                      ("2026-07-15", "2026-07-15", 5000.0)])
    added = spine.derive_semis_valuation(conn, "2026-07-19")
    # 2020 week sees only 2 filings -> skipped; 2026 week sees all 8
    assert added == 1
    d, _p, v = conn.execute("SELECT data_date, pub_date, value FROM"
                            " observations WHERE series_id ="
                            " 'semis_valuation'").fetchone()
    assert d == "2026-07-15" and v == pytest.approx(5.0)


def wk(i):
    return (dt.date(2008, 1, 9) + dt.timedelta(weeks=i)).isoformat()


def test_semis_engine_none_until_window_matures(conn):
    seed_series(conn, "semis_valuation",
                [(wk(i), wk(i), 5.0) for i in range(200)])  # << 0.8 x 1040
    seed_series(conn, "semis_earnings", [("2024-12-31", "2025-02-01", 1.0),
                                         ("2025-12-31", "2026-02-01", 2.0)])
    assert drivers.semis_engine(conn, "2026-07-19") == {"engine": None,
                                                        "alive": None}


def test_semis_engine_cheap_and_rising(conn):
    values = [(wk(i), wk(i), 10.0) for i in range(900)]
    values[-1] = (wk(899), wk(899), 2.0)  # last ratio near the bottom
    seed_series(conn, "semis_valuation", values)
    seed_series(conn, "semis_earnings", [("2024-12-31", "2025-02-01", 1.0),
                                         ("2025-12-31", "2026-02-01", 2.0)])
    assert drivers.semis_engine(conn, "2026-07-19") == {"engine": True,
                                                        "alive": True}


def test_semis_engine_dear_blocks(conn):
    values = [(wk(i), wk(i), 10.0) for i in range(900)]
    values[-1] = (wk(899), wk(899), 50.0)  # top of its own history
    seed_series(conn, "semis_valuation", values)
    seed_series(conn, "semis_earnings", [("2024-12-31", "2025-02-01", 1.0),
                                         ("2025-12-31", "2026-02-01", 2.0)])
    assert drivers.semis_engine(conn, "2026-07-19")["engine"] is False


def test_semis_engine_falling_profits_block(conn):
    values = [(wk(i), wk(i), 10.0) for i in range(900)]
    values[-1] = (wk(899), wk(899), 2.0)
    seed_series(conn, "semis_valuation", values)
    seed_series(conn, "semis_earnings", [("2024-12-31", "2025-02-01", 2.0),
                                         ("2025-12-31", "2026-02-01", 1.0)])
    assert drivers.semis_engine(conn, "2026-07-19")["engine"] is False
