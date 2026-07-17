"""State engine scenario table — every transition pinned.

@context  Batch 2.2 acceptance: F10 order, hysteresis gaps, the 26-week dead
          rule, BROKEN's one-week life, EARLY-only-from-NEUTRAL, age resets.
@done     The scenarios below.
@todo     —
@limits   Pure, offline.
@affects  src/engine.py.
"""

from src.engine import MarketState, step

QUIET = {"news": "quiet"}


def obs(engine=None, alive=None, party=None, momentum=None, news="quiet"):
    return {"engine": engine, "alive": alive, "party_pct": party,
            "momentum": momentum, "news": news}


def test_fresh_market_is_neutral():
    s = step(None, obs())
    assert s.state == "NEUTRAL" and s.age_weeks == 0


def test_neutral_to_early():
    s = step(MarketState(), obs(engine=True, alive=True, party=15, momentum=0))
    assert s.state == "EARLY" and s.party_empty


def test_early_holds_in_hysteresis_gap():
    prev = step(MarketState(), obs(engine=True, alive=True, party=15, momentum=0))
    s = step(prev, obs(engine=True, alive=True, party=25, momentum=0))
    assert s.state == "EARLY"  # 25 is between 20 and 35: empty flag holds
    assert s.age_weeks == 1


def test_early_exits_at_35():
    prev = step(MarketState(), obs(engine=True, alive=True, party=15, momentum=0))
    s = step(prev, obs(engine=True, alive=True, party=40, momentum=0))
    assert s.state == "NEUTRAL" and not s.party_empty and s.age_weeks == 0


def test_neutral_to_confirmed():
    s = step(MarketState(), obs(engine=True, alive=True, party=50, momentum=1))
    assert s.state == "CONFIRMED"


def test_confirmed_blocked_above_85():
    s = step(MarketState(), obs(engine=True, alive=True, party=90, momentum=1))
    assert s.state == "NEUTRAL"  # party 86+: no new CONFIRMED (F10)


def test_confirmed_to_broken_on_engine_flip():
    prev = step(MarketState(), obs(engine=True, alive=True, party=50, momentum=1))
    s = step(prev, obs(engine=False, alive=True, party=50, momentum=1))
    assert s.state == "BROKEN"


def test_broken_lasts_one_week_then_neutral():
    broken = MarketState(state="BROKEN")
    s = step(broken, obs(engine=None, alive=None, party=50, momentum=0))
    assert s.state == "NEUTRAL"


def test_broken_can_reset_straight_to_early_when_fresh():
    # after the one-week BROKEN, the market is a NEUTRAL market; fresh EARLY
    # conditions may fire immediately (documented normalize choice)
    broken = MarketState(state="BROKEN", party_empty=True)
    s = step(broken, obs(engine=True, alive=True, party=15, momentum=0))
    assert s.state == "EARLY"


def test_early_not_entered_from_confirmed():
    confirmed = MarketState(state="CONFIRMED")
    s = step(confirmed, obs(engine=True, alive=True, party=15, momentum=0))
    assert s.state == "NEUTRAL"  # EARLY only enters from NEUTRAL/EARLY


def test_dead_driver_breaks_after_26_weeks():
    s = MarketState(state="CONFIRMED", dead_weeks=0)
    for week in range(25):
        s = step(s, obs(engine=True, alive=False, party=50, momentum=1))
        assert s.state == "CONFIRMED", f"week {week}: broke too early"
    s = step(s, obs(engine=True, alive=False, party=50, momentum=1))
    assert s.state == "BROKEN" and s.dead_weeks == 26


def test_alive_resets_dead_streak():
    s = MarketState(state="CONFIRMED", dead_weeks=25)
    s = step(s, obs(engine=True, alive=True, party=50, momentum=1))
    assert s.dead_weeks == 0 and s.state == "CONFIRMED"


def test_crowded_needs_full_party_and_greedy_news():
    s = step(MarketState(), obs(engine=True, alive=True, party=90, momentum=1,
                                news="loud_greedy"))
    assert s.state == "CROWDED"
    # greedy news alone, party 50: not crowded
    s2 = step(MarketState(), obs(engine=True, alive=True, party=50, momentum=1,
                                 news="loud_greedy"))
    assert s2.state != "CROWDED"


def test_crowded_hysteresis_exits_below_70():
    crowded = step(MarketState(), obs(party=90, news="loud_greedy"))
    assert crowded.state == "CROWDED"
    still = step(crowded, obs(party=75, news="loud_greedy"))
    assert still.state == "CROWDED"  # 75 is above the 70 exit: flag holds
    out = step(still, obs(party=65, news="loud_greedy"))
    assert out.state != "CROWDED"


def test_party_flip_flop_suppressed():
    # oscillation 86/80/86/80 with quiet news: full flag stays ON throughout
    s = step(MarketState(), obs(party=86))
    for party in (80, 86, 80, 84):
        s = step(s, obs(party=party))
        assert s.party_full
    s = step(s, obs(party=69))
    assert not s.party_full


def test_engine_none_never_qualifies():
    s = step(MarketState(), obs(engine=None, alive=None, party=10, momentum=0))
    assert s.state == "NEUTRAL"
    s2 = step(MarketState(), obs(engine=None, party=50, momentum=1))
    assert s2.state == "NEUTRAL"


def test_engine_none_does_not_break():
    confirmed = MarketState(state="CONFIRMED")
    s = step(confirmed, obs(engine=None, alive=None, party=50, momentum=1))
    assert s.state == "NEUTRAL"  # loses CONFIRMED honestly, but no BROKEN


def test_party_none_keeps_flags():
    prev = MarketState(state="EARLY", party_empty=True)
    s = step(prev, obs(engine=True, alive=True, party=None, momentum=0))
    assert s.party_empty  # no data: flag carried, EARLY persists
    assert s.state == "EARLY"


def test_age_counts_consecutive_weeks():
    s = step(MarketState(), obs(engine=True, alive=True, party=50, momentum=1))
    for expected_age in (1, 2, 3):
        s = step(s, obs(engine=True, alive=True, party=50, momentum=1))
        assert (s.state, s.age_weeks) == ("CONFIRMED", expected_age)
    s = step(s, obs(engine=True, alive=True, party=50, momentum=0))
    assert s.age_weeks == 0  # state changed -> reset
