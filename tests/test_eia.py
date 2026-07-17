"""EIA fetcher tests — offline via fake session + live smoke.

@context  Batch 2.1: canned v2-seriesid payload parses to as-of rows with the
          Wednesday publication lag; idempotent; loud failures.
@done     Lag arithmetic, null skipping, idempotency, HTTP/shape/empty/key
          failures, live WCESTUS1 smoke.
@todo     —
@limits   Default run offline.
@affects  src/fetchers/eia.py.
"""

import pytest

from src import db, registry
from src.fetchers import eia

ENTRY = {
    "series_id": "oil_inventories", "source": "EIA",
    "source_url": "https://example.com", "schedule": "weekly",
    "window": "same_week_5y_avg", "pub_lag_days": 5,
    "series_codes": ["PET.WCESTUS1.W"],
}

PAYLOAD = {"response": {"data": [
    {"period": "2026-07-03", "value": 421000},
    {"period": "2026-07-10", "value": None},       # null: skipped
    {"period": "2026-07-11", "value": 419500},
]}}


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


def test_rows_and_lag(conn):
    added = eia.fetch(ENTRY, conn, session=FakeSession())
    assert added == 2
    rows = conn.execute(
        "SELECT data_date, pub_date, value FROM observations"
        " WHERE series_id = 'oil_inventories' ORDER BY data_date").fetchall()
    assert rows == [("2026-07-03", "2026-07-08", 421000.0),
                    ("2026-07-11", "2026-07-16", 419500.0)]


def test_idempotent(conn):
    eia.fetch(ENTRY, conn, session=FakeSession())
    assert eia.fetch(ENTRY, conn, session=FakeSession()) == 0


def test_http_error_raises(conn):
    with pytest.raises(eia.FetchError, match="HTTP 500"):
        eia.fetch(ENTRY, conn, session=FakeSession(status=500))


def test_bad_shape_raises(conn):
    with pytest.raises(eia.FetchError, match="unexpected payload"):
        eia.fetch(ENTRY, conn, session=FakeSession(payload={"response": {}}))


def test_all_null_raises(conn):
    empty = {"response": {"data": [{"period": "2026-07-11", "value": None}]}}
    with pytest.raises(eia.FetchError, match="zero rows"):
        eia.fetch(ENTRY, conn, session=FakeSession(payload=empty))


def test_missing_key_raises(conn, monkeypatch, tmp_path):
    from src import config
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / "absent.env")
    with pytest.raises(eia.FetchError, match="EIA_API_KEY"):
        eia.fetch(ENTRY, conn)


@pytest.mark.integration
def test_live_wcestus1(conn):
    entry = next(e for e in registry.load_registry()
                 if e["series_id"] == "oil_inventories")
    added = eia.fetch(entry, conn, session=None)
    assert added > 500  # weekly since 1982, capped by API page length
    latest = conn.execute("SELECT MAX(data_date) FROM observations"
                          " WHERE series_id = 'oil_inventories'").fetchone()[0]
    assert latest >= "2026-06-01"
