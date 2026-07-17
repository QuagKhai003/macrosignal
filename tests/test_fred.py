"""FRED fetcher tests — offline via injected fake session.

@context  Batch 1.2 acceptance: canned payloads in, correct as-of rows out,
          idempotent on re-run, loud on failure. One live integration test
          (marked, excluded by default) proves the real API path.
@done     Row shape + pub-lag arithmetic, "." skipping, idempotency, HTTP and
          shape failures raise, missing key raises, live DFII10 smoke.
@todo     —
@limits   Default run offline; integration test needs FRED_API_KEY + network.
@affects  src/fetchers/fred.py, src/fetchers/base.py.
"""

import datetime as dt

import pytest

import weekly_run
from src import db, registry
from src.fetchers import fred

ENTRY = {
    "series_id": "net_liquidity", "source": "FRED",
    "source_url": "https://example.com", "schedule": "weekly",
    "window": "rolling10y", "pub_lag_days": 1,
    "series_codes": ["WALCL", "WTREGEN"],
}

PAYLOADS = {
    "WALCL": {"observations": [
        {"date": "2026-07-01", "value": "6600.5"},
        {"date": "2026-07-08", "value": "."},        # missing marker: skipped
        {"date": "2026-07-15", "value": "6590.0"}]},
    "WTREGEN": {"observations": [
        {"date": "2026-07-01", "value": "700.0"}]},
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, payloads=PAYLOADS, status=200):
        self._payloads, self._status = payloads, status

    def get(self, url, params, timeout):
        return FakeResponse(self._payloads.get(params["series_id"],
                                               {"observations": []}),
                            self._status)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_fetch_stores_rows_with_publication_lag(conn):
    added = fred.fetch(ENTRY, conn, session=FakeSession())
    assert added == 3  # 2 WALCL (one ".") + 1 WTREGEN
    rows = conn.execute(
        "SELECT series_id, data_date, pub_date, value FROM observations"
        " WHERE series_id = 'fred_walcl' ORDER BY data_date").fetchall()
    assert rows == [("fred_walcl", "2026-07-01", "2026-07-02", 6600.5),
                    ("fred_walcl", "2026-07-15", "2026-07-16", 6590.0)]


def test_fetch_is_idempotent(conn):
    fred.fetch(ENTRY, conn, session=FakeSession())
    assert fred.fetch(ENTRY, conn, session=FakeSession()) == 0


def test_http_error_raises(conn):
    with pytest.raises(fred.FetchError, match="HTTP 500"):
        fred.fetch(ENTRY, conn, session=FakeSession(status=500))


def test_bad_payload_raises(conn):
    with pytest.raises(fred.FetchError, match="unexpected payload"):
        fred.fetch(ENTRY, conn, session=FakeSession(payloads={
            "WALCL": {"nope": []}, "WTREGEN": {"nope": []}}))


def test_missing_key_raises(conn, tmp_path, monkeypatch):
    from src import config
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / "absent.env")
    with pytest.raises(fred.FetchError, match="FRED_API_KEY"):
        fred.fetch(ENTRY, conn)


@pytest.mark.integration
def test_live_dfii10(conn):
    entry = next(e for e in registry.load_registry()
                 if e["series_id"] == "real_yields")
    added = fred.fetch(entry, conn, session=None)
    assert added > 1000  # decades of daily observations
    latest = conn.execute(
        "SELECT MAX(data_date) FROM observations WHERE series_id = 'fred_dfii10'"
    ).fetchone()[0]
    assert latest > (dt.date.today() - dt.timedelta(days=14)).isoformat()
