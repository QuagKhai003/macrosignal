"""Formula primitives — the frozen math (tech spec F1, F3, F4, F5).

@context  The universal normalizers every signal flows through. Windows always
          arrive as arguments (ultimately from signals.yaml) — never hardcoded
          here (build plan Phase 1 rule).
@done     pct_rank (F1 + the 0.8xW insufficient-history rule), sma200_flag
          (F4), net_liquidity + is_falling (F5), corr_of_changes (F3's rolling
          Pearson correlation of first differences).
@todo     Phase 2: F3's alive/dead status tracking (needs 26-week state);
          F2 z-score when a consumer exists.
@limits   PURE: no I/O, no network, no db. Deterministic. Insufficient data
          returns None ("insufficient history", contributes nothing) — callers
          must handle None, never guess.
@affects  Every fetcher and the Phase 2 state engine consume these; tested by
          tests/test_formulas.py against hand-computed fixtures.
"""

import pandas as pd

MIN_WINDOW_FRACTION = 0.8  # F1 minimum-data rule


def _clean(values) -> pd.Series:
    return pd.Series(list(values), dtype="float64").dropna()


def pct_rank(values, window: int) -> float | None:
    """F1: percentile rank of the LAST value within its trailing window.

    P = 100 * (count{x_i < x_t} + 0.5 * count{x_i = x_t}) / n over the last
    `window` observations. With 0.8*W <= n < W the divisor is n, the actual
    count (decision 2026-07-18: dividing by W would bias short windows down).
    Returns None when fewer than 0.8*W observations exist.
    """
    tail = _clean(values).iloc[-window:]
    n = len(tail)
    if n < MIN_WINDOW_FRACTION * window:
        return None
    x = tail.iloc[-1]
    return 100.0 * float((tail < x).sum() + 0.5 * (tail == x).sum()) / n


def sma200_flag(closes, prev: int | None = None,
                exit_band: float = 0.02) -> int | None:
    """F4 momentum gate with the §5b two-trigger hysteresis (rule decision
    2026-07-18): turns green (1) when the last close is ABOVE the 200-day
    average; turns red (0) only when it falls BELOW (1 - exit_band) x the
    average; in between, the previous answer carries (fresh market in the
    band -> 0, the conservative side). None with fewer than 200 closes."""
    s = _clean(closes)
    if len(s) < 200:
        return None
    last, sma = s.iloc[-1], s.iloc[-200:].mean()
    if last > sma:
        return 1
    if last < (1.0 - exit_band) * sma:
        return 0
    return prev if prev is not None else 0


def party_score(values, window: int, smooth_weeks: int = 4) -> float | None:
    """F8 party percentile over a smoothed net position: the percentile is
    taken on the 4-week moving average of the raw series (rule decision
    2026-07-18 — positioning is a regime indicator, §3.1; the raw weekly
    series jumps across whole hysteresis gaps in single weeks)."""
    smoothed = _clean(values).rolling(smooth_weeks).mean().dropna()
    return pct_rank(smoothed, window)


def two_week_confirm(raw, prev_raw, prev_effective):
    """Input-level dampener (rule decision 2026-07-18): an input's effective
    value changes only after the raw value has disagreed with the effective
    one for 2 consecutive weeks. None raw -> None (insufficient stays
    honest); no prior effective -> raw."""
    if prev_effective is None or raw is None:
        return raw
    if raw == prev_effective:
        return raw
    return raw if raw == prev_raw else prev_effective


def net_liquidity(walcl, wtregen, rrpontsyd):
    """F5: the money pool. Works on scalars or aligned pandas Series."""
    return walcl - wtregen - rrpontsyd


def is_falling(values, lag: int = 13) -> bool | None:
    """F5 direction: True if the latest value is below the value `lag`
    observations earlier (13 weekly obs ~ 3 months). None if too short."""
    s = _clean(values)
    if len(s) < lag + 1:
        return None
    return bool(s.iloc[-1] < s.iloc[-1 - lag])


def corr_of_changes(driver, price, window: int = 52) -> float | None:
    """F3: Pearson correlation of the last `window` first differences of
    driver and price (the driver-alive detector's rolling rho). Inputs must be
    aligned, same-frequency sequences. None when either side has fewer than
    `window` changes."""
    d = _clean(driver).diff().dropna().iloc[-window:]
    p = _clean(price).diff().dropna().iloc[-window:]
    if len(d) < window or len(p) < window:
        return None
    d, p = d.reset_index(drop=True), p.reset_index(drop=True)
    if d.std() == 0 or p.std() == 0:
        return None  # correlation undefined on a flat series
    return float(d.corr(p))
