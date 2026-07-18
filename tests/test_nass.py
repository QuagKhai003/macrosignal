"""USDA NASS fetcher + corn engine tests (batch 6.3).

@context  Corn's engine: quarterly grain stocks parsed from NASS, compared to
          the same-quarter 5-yr average (the oil pattern, quarterly).
@done     NASS parse (commas, withheld markers, reference-period→date, pub
          lag), idempotency, failures; corn_engine below/above seasonal,
          hysteresis band, thin-history None.
@todo     —
@limits   Offline via fake session; live smoke gated on the key.
@affects  src/fetchers/nass.py, src/drivers.py.
"""

import datetime as dt

import pytest

from src import db, drivers
from src.fetchers import nass

ENTRY = {
    "series_id": "corn_stocks", "source": "NASS",
    "source_url": "https://example.com", "schedule": "quarterly",
    "window": "same_quarter_5y_avg", "pub_lag_days": 50,
    "nass_query": {"commodity_desc": "CORN", "statisticcat_desc": "STOCKS"},
}

PAYLOAD = {"data": [
    {"year": "2025", "reference_period_desc": "FIRST OF DEC",
     "Value": "12,345,678"},
    {"year": "2025", "reference_period_desc": "FIRST OF SEP",
     "Value": "1,500,000"},
    {"year": "2025", "reference_period_desc": "FIRST OF DEC",
     "Value": "(D)"},                 # withheld: skipped
    {"year": "2025", "reference_period_desc": "MARKETING YEAR",
     "Value": "9,999"},               # non-quarterly period: skipped
]}


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload=PAYLOAD, status=200):
        self._payload, self._status = payload, status

    def get(self, url, params, timeout):
        return FakeResponse(self._payload, self._status)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_parse_periods_and_lag(conn):
    added = nass.fetch(ENTRY, conn, session=FakeSession())
    assert added == 2  # Dec + Sep; (D) and MARKETING YEAR dropped
    rows = conn.execute(
        "SELECT data_date, pub_date, value FROM observations"
        " WHERE series_id = 'corn_stocks' ORDER BY data_date").fetchall()
    # Sep 1 2025 + 50d = Oct 21; Dec 1 2025 + 50d = Jan 20 2026
    assert rows == [("2025-09-01", "2025-10-21", 1500000.0),
                    ("2025-12-01", "2026-01-20", 12345678.0)]


def test_idempotent(conn):
    nass.fetch(ENTRY, conn, session=FakeSession())
    assert nass.fetch(ENTRY, conn, session=FakeSession()) == 0


def test_http_error_raises(conn):
    with pytest.raises(nass.FetchError, match="HTTP 500"):
        nass.fetch(ENTRY, conn, session=FakeSession(status=500))


def test_all_withheld_raises(conn):
    empty = {"data": [{"year": "2025", "reference_period_desc": "FIRST OF DEC",
                       "Value": "(D)"}]}
    with pytest.raises(nass.FetchError, match="zero usable"):
        nass.fetch(ENTRY, conn, session=FakeSession(payload=empty))


# ── corn engine v2: stocks-to-USE ratio (research R1) ────────────────────────

def put_dec_ratios(conn, per_year):
    """per_year: {year: ratio} for Dec-1 stocks-to-use rows."""
    conn.execute("INSERT INTO series VALUES ('corn_stocks_use','NASS','u',"
                 "'quarterly','same_quarter_5y_avg','')")
    conn.executemany(
        "INSERT INTO observations VALUES ('corn_stocks_use', ?, ?, ?)",
        [(f"{y}-12-01", f"{y + 1}-01-20", float(v)) for y, v in per_year.items()])


def test_corn_engine_ratio_below_seasonal_average(conn):
    put_dec_ratios(conn, {y: 0.90 for y in range(2020, 2025)} | {2025: 0.70})
    assert drivers.corn_engine(conn, "2026-07-18") == {"engine": True,
                                                       "alive": True}


def test_corn_engine_ratio_above_seasonal_average(conn):
    put_dec_ratios(conn, {y: 0.90 for y in range(2020, 2025)} | {2025: 0.95})
    assert drivers.corn_engine(conn, "2026-07-18") == {"engine": False,
                                                       "alive": True}


def test_corn_engine_band_carries_previous(conn):
    put_dec_ratios(conn, {y: 0.90 for y in range(2020, 2025)} | {2025: 0.909})
    assert drivers.corn_engine(conn, "2026-07-18",
                               prev_engine=True)["engine"] is True
    assert drivers.corn_engine(conn, "2026-07-18",
                               prev_engine=None)["engine"] is False


def test_corn_engine_needs_four_prior_same_quarter(conn):
    put_dec_ratios(conn, {y: 0.90 for y in range(2023, 2025)} | {2025: 0.70})
    assert drivers.corn_engine(conn, "2026-07-18")["engine"] is None


# ── stocks-to-use derivation ─────────────────────────────────────────────────

def seed_stocks_and_production(conn):
    from src import db as _db
    for sid in ("corn_stocks", "corn_production", "corn_stocks_use"):
        conn.execute("INSERT OR IGNORE INTO series VALUES (?,'NASS','u',"
                     "'quarterly','same_quarter_5y_avg','')", (sid,))
    # 8 quarters: Mar/Jun/Sep/Dec 2024 + 2025 (values in BU)
    quarters = [("2024-03-01", 8_000), ("2024-06-01", 5_000),
                ("2024-09-01", 1_500), ("2024-12-01", 12_000),
                ("2025-03-01", 8_500), ("2025-06-01", 5_200),
                ("2025-09-01", 1_600), ("2025-12-01", 13_000)]
    conn.executemany("INSERT INTO observations VALUES ('corn_stocks',?,?,?)",
                     [(d, d, float(v)) for d, v in quarters])
    # production credited at Dec 1 of each harvest year
    conn.executemany("INSERT INTO observations VALUES ('corn_production',?,?,?)",
                     [("2024-12-01", "2025-01-15", 15_000.0),
                      ("2025-12-01", "2026-01-15", 16_000.0)])


def test_stocks_to_use_hand_check(conn):
    from src import spine
    seed_stocks_and_production(conn)
    added = spine.derive_corn_stocks_use(conn, "2026-07-18")
    assert added == 4  # ratios for the 2025 quarters (need t-4 back-quarter)
    # Dec-2025 row: use = stocks(Dec24) + prod(window (Dec24, Dec25]) − stocks(Dec25)
    #             = 12,000 + 16,000 − 13,000 = 15,000; ratio = 13,000/15,000
    d, pub, v = conn.execute(
        "SELECT data_date, pub_date, value FROM observations WHERE series_id"
        " = 'corn_stocks_use' AND data_date = '2025-12-01'").fetchone()
    assert v == pytest.approx(13_000 / 15_000)
    assert pub == "2026-01-15"  # production pub dominates (as-of honest)


def test_stocks_to_use_respects_as_of(conn):
    from src import spine
    seed_stocks_and_production(conn)
    # before the 2025 production is published (2026-01-15), the Dec-2025 ratio
    # must NOT exist
    spine.derive_corn_stocks_use(conn, "2025-12-20")
    row = conn.execute("SELECT 1 FROM observations WHERE series_id ="
                       " 'corn_stocks_use' AND data_date = '2025-12-01'").fetchone()
    assert row is None


@pytest.mark.integration
def test_live_corn_stocks(conn):
    from src import registry
    entry = next(e for e in registry.load_registry()
                 if e["series_id"] == "corn_stocks")
    added = nass.fetch(entry, conn)
    assert added > 100  # quarterly since 1975
    latest = conn.execute("SELECT MAX(data_date) FROM observations"
                          " WHERE series_id = 'corn_stocks'").fetchone()[0]
    assert latest >= "2025-01-01"
