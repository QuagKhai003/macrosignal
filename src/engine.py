"""State engine — F10, hysteresis, age. Pure.

@context  The five-state machine (§5b/§13, F10), evaluated per market per
          week. First match wins: BROKEN > CROWDED > EARLY > CONFIRMED >
          NEUTRAL. Hysteresis flags (party full/empty) live in the carried
          state so thresholds can't flicker week to week.
@done     MarketState dataclass; step(prev, obs) transition with: party
          full >85/exit <70, empty <20/exit ≥35; the 26-consecutive-week
          driver-dead rule (F3) via dead_weeks streak; BROKEN only from
          EARLY/CONFIRMED, lasting one week then treated as NEUTRAL; EARLY
          entered only from NEUTRAL/EARLY (§5b "fresh from NEUTRAL"); age
          resets on any change. News values: quiet/loud_greedy/loud_scared/
          neutral/insufficient (Phase 2 feeds the stub "quiet").
@todo     Phase 4 wires real news values (loud flag gets its own hysteresis
          at the news layer).
@limits   PURE: no I/O, no dates, no db. None inputs mean "cannot answer":
          engine None never qualifies for EARLY/CONFIRMED/BROKEN; party None
          keeps prior flags and blocks CONFIRMED.
@affects  src/spine.py persistence (2.3), replay (2.5); consumes
          src/drivers.py answers and spine readouts.
"""

from dataclasses import dataclass, replace

STATES = ("NEUTRAL", "EARLY", "CONFIRMED", "CROWDED", "BROKEN")
DEAD_WEEKS_THRESHOLD = 26  # F3: driver dead after 26 consecutive not-alive weeks


@dataclass(frozen=True)
class MarketState:
    state: str = "NEUTRAL"
    age_weeks: int = 0
    party_full: bool = False   # entered >85, exits <70
    party_empty: bool = False  # entered <20, exits >=35
    dead_weeks: int = 0        # consecutive weeks driver not-alive


def step(prev: MarketState | None, obs: dict) -> MarketState:
    """One weekly transition. obs keys: engine (True/False/None),
    alive (True/False/None), party_pct (float|None), momentum (0/1/None),
    news (str)."""
    fresh = prev is None
    prev = prev or MarketState()
    flags = _update_flags(prev, obs)
    new_state = _assign(prev, flags, obs)
    age = 0 if fresh or new_state != prev.state else prev.age_weeks + 1
    return replace(flags, state=new_state, age_weeks=age)


def _update_flags(prev: MarketState, obs: dict) -> MarketState:
    party = obs.get("party_pct")
    full, empty = prev.party_full, prev.party_empty
    if party is not None:
        if party > 85:
            full = True
        elif party < 70:
            full = False
        if party < 20:
            empty = True
        elif party >= 35:
            empty = False
    alive = obs.get("alive")
    if alive is True:
        dead = 0
    elif alive is False:
        dead = prev.dead_weeks + 1
    else:
        dead = prev.dead_weeks  # no answer: streak neither grows nor resets
    return replace(prev, party_full=full, party_empty=empty, dead_weeks=dead)


def _assign(prev: MarketState, flags: MarketState, obs: dict) -> str:
    # BROKEN lasts one week; afterwards the market is a NEUTRAL market (§5b)
    prior = "NEUTRAL" if prev.state == "BROKEN" else prev.state
    engine, party = obs.get("engine"), obs.get("party_pct")
    momentum, news = obs.get("momentum"), obs.get("news", "quiet")
    greedy_loud = news == "loud_greedy"
    driver_dead = flags.dead_weeks >= DEAD_WEEKS_THRESHOLD

    if prior in ("EARLY", "CONFIRMED") and (engine is False or driver_dead):
        return "BROKEN"
    if flags.party_full and greedy_loud:
        return "CROWDED"
    if (prior in ("NEUTRAL", "EARLY") and engine is True and flags.party_empty
            and not greedy_loud and momentum == 0):
        return "EARLY"
    if (engine is True and momentum == 1
            and party is not None and party <= 85):
        return "CONFIRMED"
    return "NEUTRAL"
