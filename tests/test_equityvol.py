"""Equity-universe volume fetcher + turnover-spike tests (Tier-C, R2).

@context  Weekly share volume for the equity universe → vol_<ticker>;
          insiders.turnover_spikes flags unusual volume (the pre-13D
          wolf-pack accumulation tell); world picture combines with 13D.
@done     Weekly volume aggregation, empty-universe no-op, spike gate,
          wolf-pack vs plain-spike world lines.
@limits   Offline via fake Yahoo session.
@affects  src/fetchers/equityvol.py, src/insiders.py, src/worldview.py.
"""

import datetime as dt

import pytest

from src import db, insiders, worldview
from src.fetchers import equityvol

ENTRY = {"insider_tickers": ["NVDA"]}


def chart(vols_by_ts):
    return {"chart": {"result": [{
        "meta": {"gmtoffset": 0},
        "timestamp": list(vols_by_ts),
        "indicators": {"quote": [{"volume": list(vols_by_ts.values())}]}}]}}


class FakeYahoo:
    def __init__(self, payload):
        self._p = payload

    def get(self, url, params, headers, timeout):
        class R:
            status_code = 200
            def json(self, _p=self._p):
                return _p
        return R()


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_weekly_volume_aggregates(conn):
    # two daily bars in the same ISO week sum into one weekly figure
    base_ts = int(dt.datetime(2026, 7, 6, tzinfo=dt.UTC).timestamp())  # Monday
    day = 86400
    payload = chart({base_ts: 1000.0, base_ts + day: 500.0})
    added = equityvol.fetch(ENTRY, conn, session=FakeYahoo(payload),
                            today=dt.date(2026, 7, 19))
    assert added == 1
    v = conn.execute("SELECT value FROM observations WHERE series_id ="
                     " 'vol_nvda'").fetchone()[0]
    assert v == 1500.0


def test_empty_universe_noop(conn):
    assert equityvol.fetch({"insider_tickers": []}, conn) == 0


def put_weekly_vol(conn, ticker, values):
    conn.execute("INSERT OR IGNORE INTO series VALUES (?,'Yahoo','','weekly',"
                 "'rolling3y','')", (f"vol_{ticker.lower()}",))
    start = dt.date(2026, 1, 1)
    conn.executemany(
        "INSERT INTO observations VALUES (?,?,?,?)",
        [(f"vol_{ticker.lower()}",
          (start + dt.timedelta(weeks=i)).isoformat(),
          (start + dt.timedelta(weeks=i)).isoformat(), float(v))
         for i, v in enumerate(values)])


def test_turnover_spike_gate(conn):
    # 8 flat weeks then a 3x spike -> flagged
    put_weekly_vol(conn, "NVDA", [100] * 8 + [300])
    put_weekly_vol(conn, "AMD", [100] * 8 + [120])  # mild, not flagged
    assert insiders.turnover_spikes(conn, "2026-07-19") == ["NVDA"]


def test_turnover_needs_history(conn):
    put_weekly_vol(conn, "NVDA", [100, 300])  # < 9 weeks
    assert insiders.turnover_spikes(conn, "2026-07-19") == []


def test_wolf_pack_world_line():
    # spike + an activist 13D on the same name = wolf pack forming
    lines = worldview.lines({}, "GREEN", {},
                            edgar_events={"activist stake": ["NVDA"]},
                            turnover_spikes=["NVDA", "AMD"])
    text = "\n".join(lines)
    assert "WOLF PACK forming" in text and "NVDA" in text.split("WOLF PACK")[1]
    assert "Unusual trading volume" in text and "AMD" in text.split(
        "Unusual trading volume")[1]
