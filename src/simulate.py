"""Monte Carlo forecaster — thousands of replays of history's alike-weeks.

@context  The user's ask made honest: "run the signals through simulation to
          actually forecast." Technique: bootstrap resampling — for each
          market, pool the ACTUAL weekly returns from every historical week
          that showed the current state, then assemble PATHS ( N_SIMS runs of
          HORIZON_WEEKS weeks, each week drawn from the pool) and read the
          distribution of outcomes. Every simulated week really happened;
          the simulation invents order, never returns.
@done     simulate(): seeded per (market, state) — same week, same answer,
          fully reproducible (Golden Rule: a frozen procedure, no model);
          outputs prob_higher, p10/p50/p90 ending returns; MIN_SAMPLE gate
          shared with src/forward. sentence(): plain rendering.
@todo     Dashboard panel alongside the base rates — next batch.
@limits   Weekly draws are independent (iid bootstrap): volatility
          clustering is ignored, which UNDERSTATES tail risk somewhat — the
          worst-seen figure from src/forward stays alongside as the blunt
          reminder. Simulation of the past's variety, not knowledge of the
          future (pre-registered stance).
@affects  src/report.py ("The simulation" section); weekly_run.
"""

import random
import sqlite3

from src import drivers, falsify

HORIZON_WEEKS = 13
N_SIMS = 10_000
MIN_SAMPLE = 30  # same floor as src/forward — fewer alike-weeks, no number


def simulate(conn: sqlite3.Connection, as_of: str,
             horizon: int = HORIZON_WEEKS, n_sims: int = N_SIMS) -> dict:
    """{market: {state: {prob_higher, p10, p50, p90, n_pool}}} from pooled
    same-state weekly returns."""
    states = falsify.load_states(conn)
    prices = falsify.load_weekly_prices(conn, as_of)
    out = {}
    for market in drivers.MARKETS:
        weeks = sorted(prices.get(market, {}))
        index = {w: i for i, w in enumerate(weeks)}
        pools: dict[str, list[float]] = {}
        for week, state in states.get(market, {}).items():
            i = index.get(week)
            if i is None or i + 1 >= len(weeks):
                continue
            start = prices[market][weeks[i]]
            if start:
                pools.setdefault(state, []).append(
                    prices[market][weeks[i + 1]] / start - 1.0)
        out[market] = {
            state: _run(pool, market, state, horizon, n_sims)
            for state, pool in pools.items()}
    return out


def _run(pool: list[float], market: str, state: str,
         horizon: int, n_sims: int) -> dict | None:
    if len(pool) < MIN_SAMPLE:
        return None
    rng = random.Random(f"{market}:{state}")  # frozen seed: reproducible
    endings = []
    for _ in range(n_sims):
        level = 1.0
        for _ in range(horizon):
            level *= 1.0 + rng.choice(pool)
        endings.append(100.0 * (level - 1.0))
    endings.sort()
    return {"prob_higher": 100.0 * sum(e > 0 for e in endings) / n_sims,
            "p10": endings[n_sims // 10],
            "p50": endings[n_sims // 2],
            "p90": endings[(9 * n_sims) // 10],
            "n_pool": len(pool)}


def sentence(result: dict | None, horizon: int = HORIZON_WEEKS) -> str:
    months = round(horizon / 4.33)
    if result is None:
        return ("Too few alike-weeks to simulate honestly.")
    return (f"Of {N_SIMS:,} simulated {months}-month runs built from"
            f" alike-weeks, {result['prob_higher']:.0f} in 100 ended higher;"
            f" typical outcome {result['p50']:+.1f}%; the best tenth beat"
            f" {result['p90']:+.1f}%, the worst tenth fell below"
            f" {result['p10']:+.1f}%.")
