"""F6/F7 scoring tests — hand-computed ratios and the state mapping.

@context  Batch 4.3: volume ratio arithmetic, loud hysteresis band, the
          30-headline minimum, direction thresholds, news_state mapping.
@done     Those, plus the July-2026-energy-shaped case (loud + scared).
@todo     —
@limits   Offline; canned volumes/labels.
@affects  src/newsscore.py.
"""

import datetime as dt

import pytest

from src import db, newsscore

AS_OF = "2026-07-18"


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    conn.execute("INSERT INTO series VALUES ('news_vol_wti', 'GDELT', 'u',"
                 " 'weekly', 'fixed_threshold', '')")
    yield conn
    conn.close()


def put_volumes(conn, counts, end=dt.date(2026, 7, 12)):
    rows = []
    for i, v in enumerate(reversed(counts)):
        day = (end - dt.timedelta(weeks=i)).isoformat()
        rows.append(("news_vol_wti", day, day, float(v)))
    conn.executemany("INSERT INTO observations VALUES (?, ?, ?, ?)", rows)


def put_headlines(conn, labels, theme="wti", date="2026-07-14"):
    for i, label in enumerate(labels):
        conn.execute("INSERT INTO headlines (theme, seen_date, title, label)"
                     " VALUES (?, ?, ?, ?)", (theme, date, f"h{i}", label))


def test_volume_ratio_hand_check(conn):
    # 52 weeks: 48 weeks of 10, then last 4 weeks of 40 each.
    # last-4 sum = 160. rolling 4-sums: mostly 40, ramping to 160 at the end.
    put_volumes(conn, [10.0] * 48 + [40.0] * 4)
    s = newsscore.score(conn, "wti", AS_OF)
    rolling = [40.0] * 45 + [70.0, 100.0, 130.0, 160.0]
    expected = 160.0 / (sum(rolling) / len(rolling))
    assert s["volume_ratio"] == pytest.approx(expected)
    assert s["loud"] is True  # ~3.5x > 3.0


def test_loud_hysteresis_band_carries(conn):
    # ratio lands between 2.0 and 3.0 -> previous loudness holds
    put_volumes(conn, [10.0] * 48 + [25.0] * 4)
    assert newsscore.score(conn, "wti", AS_OF, prev_loud=True)["loud"] is True
    assert newsscore.score(conn, "wti", AS_OF, prev_loud=False)["loud"] is False


def test_insufficient_volume_history(conn):
    put_volumes(conn, [10.0] * 20)  # < 0.8 * 52 weeks
    s = newsscore.score(conn, "wti", AS_OF)
    assert s["volume_ratio"] is None and s["news_state"] == "insufficient"


def test_greed_minimum_thirty(conn):
    put_volumes(conn, [10.0] * 52)
    put_headlines(conn, ["excited"] * 29)
    s = newsscore.score(conn, "wti", AS_OF)
    assert s["greed_ratio"] is None and s["direction"] == "insufficient"
    assert s["n_labeled"] == 29


def test_scared_loud_maps_to_loud_scared(conn):
    # the July 2026 energy shape: loud volume + fear-dominated labels
    put_volumes(conn, [10.0] * 48 + [40.0] * 4)
    put_headlines(conn, ["scared"] * 25 + ["neutral"] * 10 + ["excited"] * 5)
    s = newsscore.score(conn, "wti", AS_OF)
    assert s["greed_ratio"] == pytest.approx(5 / 30)  # < 0.4 -> scared
    assert s["direction"] == "scared"
    assert s["news_state"] == "loud_scared"


def test_greedy_loud(conn):
    put_volumes(conn, [10.0] * 48 + [40.0] * 4)
    put_headlines(conn, ["excited"] * 28 + ["scared"] * 2 + ["neutral"] * 5)
    s = newsscore.score(conn, "wti", AS_OF)
    assert s["direction"] == "greedy" and s["news_state"] == "loud_greedy"


def test_quiet_when_ratio_low(conn):
    put_volumes(conn, [10.0] * 52)  # ratio 1.0 < 1.5
    put_headlines(conn, ["neutral"] * 35)
    s = newsscore.score(conn, "wti", AS_OF)
    assert s["news_state"] == "quiet"


def test_error_labels_excluded(conn):
    put_volumes(conn, [10.0] * 52)
    put_headlines(conn, ["error"] * 40 + ["neutral"] * 10)
    s = newsscore.score(conn, "wti", AS_OF)
    assert s["n_labeled"] == 10  # errors never count
    assert s["greed_ratio"] is None  # below the 30 minimum


def test_stale_headlines_outside_week_ignored(conn):
    put_volumes(conn, [10.0] * 52)
    put_headlines(conn, ["excited"] * 40, date="2026-07-01")  # 17 days old
    s = newsscore.score(conn, "wti", AS_OF)
    assert s["n_labeled"] == 0
