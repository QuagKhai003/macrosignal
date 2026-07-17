"""Hand-computed fixtures for the formula primitives.

@context  Batch 1.1 acceptance (build plan's hand-verify rule): every expected
          value below was computed by hand, spreadsheet-style, before the code
          ran. If code and hand disagree, the code is wrong.
@done     F1 (ranks, ties, insufficient rule, short-window divisor), F4, F5
          (arithmetic + 13-lag direction), F3 (perfect +/-, flat, insufficient).
@todo     —
@limits   Offline, deterministic, pure.
@affects  src/formulas.py.
"""

import pytest

from src import formulas


# ── F1 pct_rank ──────────────────────────────────────────────────────────────

def test_pct_rank_top_of_window():
    # 1..10, x_t=10: below=9, equal=1 -> 100*(9+0.5)/10 = 95.0
    assert formulas.pct_rank(range(1, 11), window=10) == 95.0


def test_pct_rank_bottom_of_window():
    # 10..1, x_t=1: below=0, equal=1 -> 100*(0.5)/10 = 5.0
    assert formulas.pct_rank(range(10, 0, -1), window=10) == 5.0


def test_pct_rank_with_ties():
    # [1,2,2,2,2], x_t=2: below=1, equal=4 -> 100*(1+2)/5 = 60.0
    assert formulas.pct_rank([1, 2, 2, 2, 2], window=5) == 60.0


def test_pct_rank_uses_only_the_window():
    # huge history, window 4 -> only [7,8,9,10] count: 100*(3+0.5)/4 = 87.5
    assert formulas.pct_rank(range(1, 11), window=4) == 87.5


def test_pct_rank_insufficient_history_is_none():
    # 0.8*10 = 8 -> 7 observations: insufficient
    assert formulas.pct_rank(range(7), window=10) is None


def test_pct_rank_short_window_divides_by_actual_count():
    # 8 obs, window 10 (>=0.8W): 1..8, x_t=8 -> 100*(7+0.5)/8 = 93.75
    assert formulas.pct_rank(range(1, 9), window=10) == 93.75


# ── F4 sma200_flag ───────────────────────────────────────────────────────────

def test_sma200_flag_above():
    closes = [100.0] * 199 + [101.0]  # avg just above 100, last=101 -> above
    assert formulas.sma200_flag(closes) == 1


def test_sma200_flag_below():
    closes = [100.0] * 199 + [99.0]
    assert formulas.sma200_flag(closes) == 0


def test_sma200_flag_needs_200():
    assert formulas.sma200_flag([100.0] * 199) is None


def test_sma200_hysteresis_band_carries_previous():
    # avg ~100, last 99: inside the [98, 100] band -> previous answer holds
    closes = [100.0] * 199 + [99.0]
    assert formulas.sma200_flag(closes, prev=1) == 1
    assert formulas.sma200_flag(closes, prev=0) == 0
    assert formulas.sma200_flag(closes, prev=None) == 0  # fresh: conservative


def test_sma200_hysteresis_exits_below_band():
    closes = [100.0] * 199 + [97.0]  # below 98% of the average: red, always
    assert formulas.sma200_flag(closes, prev=1) == 0


# ── F8 party smoothing + two-week confirm ────────────────────────────────────

def test_party_score_damps_weekly_rank_swings():
    # a one-week spike-and-retreat whipsaws the RAW rank but barely moves the
    # smoothed one (the 4-wk mean still contains the spike both weeks)
    week1 = [10.0] * 20 + [100.0]
    week2 = week1 + [10.0]
    raw_swing = abs(formulas.pct_rank(week1, 10) - formulas.pct_rank(week2, 10))
    smooth_swing = abs(formulas.party_score(week1, 10)
                       - formulas.party_score(week2, 10))
    assert raw_swing >= 50
    assert smooth_swing <= 10
    assert smooth_swing < raw_swing


def test_two_week_confirm_blocks_one_week_blips():
    # effective 0, raw flips to 1 for one week: no change
    assert formulas.two_week_confirm(1, 0, 0) == 0
    # raw 1 held two weeks: change goes through
    assert formulas.two_week_confirm(1, 1, 0) == 1
    # agreement passes through untouched
    assert formulas.two_week_confirm(1, 1, 1) == 1
    # fresh market: raw wins; insufficient stays honest
    assert formulas.two_week_confirm(1, None, None) == 1
    assert formulas.two_week_confirm(None, 1, 1) is None


# ── F5 net liquidity ─────────────────────────────────────────────────────────

def test_net_liquidity_arithmetic():
    # 6,600 - 700 - 200 = 5,700 (billions, July-2026-ish magnitudes)
    assert formulas.net_liquidity(6600.0, 700.0, 200.0) == 5700.0


def test_is_falling_true():
    # 14 weeks: starts at 100, ends at 99 -> L_t < L_{t-13}
    series = [100.0] + [100.0] * 12 + [99.0]
    assert formulas.is_falling(series, lag=13) is True


def test_is_falling_false_when_rising():
    series = [100.0] + [100.0] * 12 + [101.0]
    assert formulas.is_falling(series, lag=13) is False


def test_is_falling_insufficient():
    assert formulas.is_falling([100.0] * 13, lag=13) is None


# ── F3 corr_of_changes ───────────────────────────────────────────────────────

def test_corr_perfectly_positive():
    driver = list(range(60))            # changes all +1
    price = [2 * x for x in range(60)]  # changes all +2, same direction
    # both series' changes are constant -> flat differences -> std 0 -> None
    assert formulas.corr_of_changes(driver, price, window=52) is None


def test_corr_positive_alternating():
    # changes alternate +1,+3 vs +2,+6 -> perfectly correlated: rho = 1.0
    driver, price, d, p = [0.0], [0.0], 0.0, 0.0
    for i in range(60):
        d += 1 if i % 2 == 0 else 3
        p += 2 if i % 2 == 0 else 6
        driver.append(d)
        price.append(p)
    assert formulas.corr_of_changes(driver, price, window=52) == pytest.approx(1.0)


def test_corr_negative_alternating():
    driver, price, d, p = [0.0], [0.0], 0.0, 0.0
    for i in range(60):
        d += 1 if i % 2 == 0 else 3
        p -= 2 if i % 2 == 0 else 6
        driver.append(d)
        price.append(p)
    assert formulas.corr_of_changes(driver, price, window=52) == pytest.approx(-1.0)


def test_corr_insufficient():
    assert formulas.corr_of_changes(range(30), range(30), window=52) is None
