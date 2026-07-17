"""Prices fetcher tests — offline via fake session + live smoke.

@context  Batch 1.4 acceptance: canned chart JSON parses to hand-computed
          date/close rows (gmtoffset applied), nulls skipped, idempotent,
          loud failures.
@done     Date arithmetic, null skipping, idempotency, HTTP/shape/empty
          failures, live 5-symbol smoke with SMA200 sanity.
@todo     —
@limits   Default run offline.
@affects  src/fetchers/prices.py.
"""

import pytest

from src import db, formulas, registry
from src.fetchers import prices

ENTRY = {
    "series_id": "spine_prices", "source": "Yahoo",
    "source_url": "https://example.com", "schedule": "daily",
    "window": "sma200", "pub_lag_days": 0,
    "markets": {"price_gold": "GC=F"},
}

# 2026-07-14 00:00 UTC = 1783987200. Yahoo stamps bars in exchange time with
# gmtoffset -14400 (UTC-4): ts 1784016000 = 08:00 UTC; +offset -> 04:00 UTC,
# date 2026-07-14. Next bars +1 and +2 days.
PAYLOAD = {"chart": {"result": [{
    "meta": {"gmtoffset": -14400},
    "timestamp": [1784016000, 1784102400, 1784188800],
    "indicators": {"quote": [{"close": [4000.0, None, 4023.0]}]},
}]}}


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload=PAYLOAD, status=200):
        self._payload, self._status = payload, status

    def get(self, url, params, headers, timeout):
        return FakeResponse(self._payload, self._status)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_rows_dates_and_null_skipping(conn):
    added = prices.fetch(ENTRY, conn, session=FakeSession())
    assert added == 2  # the None close is skipped
    rows = conn.execute(
        "SELECT data_date, pub_date, value FROM observations"
        " WHERE series_id = 'price_gold' ORDER BY data_date").fetchall()
    assert rows == [("2026-07-14", "2026-07-14", 4000.0),
                    ("2026-07-16", "2026-07-16", 4023.0)]


def test_idempotent(conn):
    prices.fetch(ENTRY, conn, session=FakeSession())
    assert prices.fetch(ENTRY, conn, session=FakeSession()) == 0


def test_http_error_raises(conn):
    with pytest.raises(prices.FetchError, match="HTTP 429"):
        prices.fetch(ENTRY, conn, session=FakeSession(status=429))


def test_bad_shape_raises(conn):
    with pytest.raises(prices.FetchError, match="unexpected payload"):
        prices.fetch(ENTRY, conn, session=FakeSession(payload={"chart": {}}))


def test_all_null_closes_raises(conn):
    empty = {"chart": {"result": [{
        "meta": {"gmtoffset": 0}, "timestamp": [1784088000],
        "indicators": {"quote": [{"close": [None]}]}}]}}
    with pytest.raises(prices.FetchError, match="zero rows"):
        prices.fetch(ENTRY, conn, session=FakeSession(payload=empty))


@pytest.mark.integration
def test_live_all_five_markets(conn):
    entry = next(e for e in registry.load_registry()
                 if e["series_id"] == "spine_prices")
    prices.fetch(entry, conn, session=None)
    for sid in entry["markets"]:
        closes = [r[0] for r in conn.execute(
            "SELECT value FROM observations WHERE series_id = ?"
            " ORDER BY data_date", (sid,)).fetchall()]
        assert len(closes) > 1000, sid       # years of daily bars
        assert formulas.sma200_flag(closes) in (0, 1), sid
