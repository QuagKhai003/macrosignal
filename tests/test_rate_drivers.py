"""Rate-based driver engine tests (batch 6.2).

@context  The generalized falling-driver engine (gold pattern) applied to
          ust10y (breakeven inflation) and eur (US-EZ rate differential), plus
          the derived rate-differential series.
@done     Engine on when driver falls + relationship alive; off when rising;
          None on thin data; rate-differential derivation (as-of aligned).
@todo     —
@limits   Offline; synthetic series built with datetime arithmetic.
@affects  src/drivers.py, src/spine.py.
"""

import datetime as dt

import pytest

from src import db, drivers, spine

START = dt.date(2015, 1, 7)
AS_OF = "2026-07-18"


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    for sid in ("fred_t10yie", "price_ust10y", "us_ez_rate_diff", "price_eur",
                "fred_dgs2", "fred_ecbdfr"):
        conn.execute("INSERT INTO series VALUES (?, 's', 'u', 'weekly',"
                     " 'rolling10y', '')", (sid,))
    yield conn
    conn.close()


def wiggle(base, n, big, small):
    out, v = [], base
    for i in range(n):
        v += big if i % 2 == 0 else small
        out.append(v)
    return out


def put_weekly(conn, sid, values, start=START):
    conn.executemany(
        "INSERT INTO observations VALUES (?, ?, ?, ?)",
        [(sid, (start + dt.timedelta(weeks=i)).isoformat(),
          (start + dt.timedelta(weeks=i)).isoformat(), float(v))
         for i, v in enumerate(values)])


def test_ust10y_engine_on_when_breakeven_falls(conn):
    n = 560
    breakeven = wiggle(3.0, n, big=-0.01, small=0.005)   # net down
    # bond price moves OPPOSITE breakeven (falling inflation -> bond rally)
    price = [110.0 - 5 * b for b in breakeven]
    put_weekly(conn, "fred_t10yie", breakeven)
    put_weekly(conn, "price_ust10y", price)
    r = drivers.falling_driver_engine(conn, "fred_t10yie", "price_ust10y", AS_OF)
    assert r == {"engine": True, "alive": True}


def test_ust10y_engine_off_when_breakeven_rises(conn):
    n = 560
    breakeven = wiggle(2.0, n, big=0.01, small=-0.005)   # net up
    price = [110.0 - 5 * b for b in breakeven]
    put_weekly(conn, "fred_t10yie", breakeven)
    put_weekly(conn, "price_ust10y", price)
    r = drivers.falling_driver_engine(conn, "fred_t10yie", "price_ust10y", AS_OF)
    assert r == {"engine": False, "alive": True}


def test_eur_engine_on_when_rate_gap_narrows(conn):
    n = 560
    diff = wiggle(2.0, n, big=-0.02, small=0.01)         # narrowing (net down)
    price = [1.05 - 0.1 * d for d in diff]               # EUR up as gap narrows
    put_weekly(conn, "us_ez_rate_diff", diff)
    put_weekly(conn, "price_eur", price)
    r = drivers.falling_driver_engine(conn, "us_ez_rate_diff", "price_eur", AS_OF)
    assert r == {"engine": True, "alive": True}


def test_engine_none_on_thin_data(conn):
    put_weekly(conn, "fred_t10yie", [2.0, 2.1, 2.2])
    put_weekly(conn, "price_ust10y", [110.0, 109.0, 108.0])
    assert drivers.falling_driver_engine(
        conn, "fred_t10yie", "price_ust10y", AS_OF)["engine"] is None


def test_engines_now_cover_five_markets(conn):
    # engines() should attempt gold/ust10y/eur (falling-driver) + wti (oil);
    # corn still None (no driver yet)
    result = drivers.engines(conn, AS_OF)
    assert set(result) == {"gold", "wti", "ust10y", "eur", "corn"}
    assert result["corn"] == {"engine": None, "alive": None}


def test_rate_differential_derivation_as_of(conn):
    # DGS2 4.16 on 07-16, ECBDFR 2.25 published 07-15 -> diff 1.91
    conn.execute("INSERT INTO observations VALUES ('fred_dgs2', '2026-07-16',"
                 " '2026-07-17', 4.16)")
    conn.execute("INSERT INTO observations VALUES ('fred_ecbdfr',"
                 " '2026-07-15', '2026-07-16', 2.25)")
    assert spine.derive_rate_differential(conn, "2026-07-18") == 1
    d, v = conn.execute("SELECT data_date, value FROM observations WHERE"
                        " series_id = 'us_ez_rate_diff'").fetchone()
    assert d == "2026-07-16" and v == pytest.approx(1.91)


def test_rate_differential_respects_as_of(conn):
    conn.execute("INSERT INTO observations VALUES ('fred_dgs2', '2026-07-16',"
                 " '2026-07-17', 4.16)")
    conn.execute("INSERT INTO observations VALUES ('fred_ecbdfr',"
                 " '2026-07-15', '2026-07-20', 2.25)")  # ECB pub AFTER as_of
    assert spine.derive_rate_differential(conn, "2026-07-18") == 0
