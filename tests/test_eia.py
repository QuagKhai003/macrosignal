"""EIA fetcher tests — offline via fake session + live smoke.

@context  Batch 2.1: canned v2-seriesid payload parses to as-of rows with the
          Wednesday publication lag; idempotent; loud failures.
@done     Lag arithmetic, null skipping, idempotency, HTTP/shape/empty/key
          failures, live WCESTUS1 smoke; R3 multi-code per-series storage
          (eia_rclc1/eia_rclc4), curve-spread derivation, oil engine OR leg.
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


# ── R3: the futures-curve leg ────────────────────────────────────────────────

CURVE_ENTRY = {
    "series_id": "oil_curve", "source": "EIA",
    "source_url": "https://example.com", "schedule": "daily",
    "window": "fixed_threshold", "pub_lag_days": 1,
    "series_codes": ["PET.RCLC1.D", "PET.RCLC4.D"],
}


def test_multi_code_entry_stores_per_series(conn):
    added = eia.fetch(CURVE_ENTRY, conn, session=FakeSession())
    assert added == 4  # 2 usable rows x 2 codes
    for sid in ("eia_rclc1", "eia_rclc4"):
        assert conn.execute("SELECT COUNT(*) FROM observations WHERE"
                            " series_id = ?", (sid,)).fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE"
                        " series_id = 'oil_curve'").fetchone()[0] == 0


def put_curve(conn, front_fourth_by_date):
    from src import db as _db
    for sid in ("eia_rclc1", "eia_rclc4"):
        conn.execute("INSERT OR IGNORE INTO series VALUES (?,'EIA','u',"
                     "'daily','fixed_threshold','')", (sid,))
    for date, (c1, c4) in front_fourth_by_date.items():
        conn.execute("INSERT INTO observations VALUES ('eia_rclc1',?,?,?)",
                     (date, date, float(c1)))
        if c4 is not None:
            conn.execute("INSERT INTO observations VALUES ('eia_rclc4',?,?,?)",
                         (date, date, float(c4)))


def test_curve_spread_hand_check(conn):
    from src import spine
    put_curve(conn, {"2024-04-04": (85.0, 80.5),
                     "2024-04-05": (86.91, 84.24),
                     "2024-04-08": (87.0, None)})  # no 4th-month: skipped
    added = spine.derive_oil_curve_spread(conn, "2026-07-18")
    assert added == 2
    rows = conn.execute("SELECT data_date, value FROM observations WHERE"
                        " series_id = 'oil_curve_spread'"
                        " ORDER BY data_date").fetchall()
    assert rows[0] == ("2024-04-04", pytest.approx(4.5))
    assert rows[1] == ("2024-04-05", pytest.approx(2.67))


def test_oil_engine_backwardation_alone_turns_on(conn):
    from src import drivers, spine
    # no inventory data at all; backwardated curve -> engine ✓ (F9 OR)
    put_curve(conn, {"2024-04-05": (86.91, 84.24)})
    spine.derive_oil_curve_spread(conn, "2026-07-18")
    assert drivers.oil_engine(conn, "2026-07-18") == {"engine": True,
                                                      "alive": True}


def test_oil_engine_contango_falls_back_to_inventories(conn):
    from src import drivers, spine
    put_curve(conn, {"2024-04-05": (80.0, 84.0)})  # contango: leg off
    spine.derive_oil_curve_spread(conn, "2026-07-18")
    assert drivers.oil_engine(conn, "2026-07-18") == {"engine": None,
                                                      "alive": None}


def test_oil_engine_curve_respects_as_of(conn):
    from src import drivers, spine
    put_curve(conn, {"2024-04-05": (86.91, 84.24)})
    spine.derive_oil_curve_spread(conn, "2026-07-18")
    assert drivers.oil_curve_backwardated(conn, "2024-04-04") is None


@pytest.mark.integration
def test_live_wcestus1(conn):
    entry = next(e for e in registry.load_registry()
                 if e["series_id"] == "oil_inventories")
    added = eia.fetch(entry, conn, session=None)
    assert added > 500  # weekly since 1982, capped by API page length
    latest = conn.execute("SELECT MAX(data_date) FROM observations"
                          " WHERE series_id = 'oil_inventories'").fetchone()[0]
    assert latest >= "2026-06-01"
