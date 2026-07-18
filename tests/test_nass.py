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


# ── corn engine (seasonal, quarterly) ────────────────────────────────────────

def put_dec_stocks(conn, per_year):
    """per_year: {year: value} for Dec-1 stocks."""
    conn.execute("INSERT INTO series VALUES ('corn_stocks','NASS','u',"
                 "'quarterly','same_quarter_5y_avg','')")
    conn.executemany(
        "INSERT INTO observations VALUES ('corn_stocks', ?, ?, ?)",
        [(f"{y}-12-01", f"{y + 1}-01-20", float(v)) for y, v in per_year.items()])


def test_corn_engine_below_seasonal_average(conn):
    # 5 prior Decembers at 12,000,000; latest Dec at 10,000,000 -> tight -> on
    put_dec_stocks(conn, {y: 12_000_000 for y in range(2020, 2025)}
                   | {2025: 10_000_000})
    assert drivers.corn_engine(conn, "2026-07-18") == {"engine": True,
                                                       "alive": True}


def test_corn_engine_above_seasonal_average(conn):
    put_dec_stocks(conn, {y: 12_000_000 for y in range(2020, 2025)}
                   | {2025: 13_000_000})  # >102% of avg -> off
    assert drivers.corn_engine(conn, "2026-07-18") == {"engine": False,
                                                       "alive": True}


def test_corn_engine_band_carries_previous(conn):
    put_dec_stocks(conn, {y: 12_000_000 for y in range(2020, 2025)}
                   | {2025: 12_120_000})  # 101% -> inside (100%,102%] band
    assert drivers.corn_engine(conn, "2026-07-18",
                               prev_engine=True)["engine"] is True
    assert drivers.corn_engine(conn, "2026-07-18",
                               prev_engine=None)["engine"] is False


def test_corn_engine_needs_four_prior_same_quarter(conn):
    put_dec_stocks(conn, {y: 12_000_000 for y in range(2023, 2025)}
                   | {2025: 10_000_000})  # only 2 priors
    assert drivers.corn_engine(conn, "2026-07-18")["engine"] is None


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
