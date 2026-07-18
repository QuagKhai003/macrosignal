"""F14 grader tests — synthetic worlds with known right answers.

@context  Batch 5.1: each criterion graded against hand-built histories where
          the correct verdict is obvious; None-honesty when a side is empty.
@done     All four criteria + next-week alignment (no lookahead) + the
          non-overlapping worst-window picker.
@todo     —
@limits   Pure, offline.
@affects  src/falsify.py.
"""

import pytest

from src import falsify


def wk(i):
    return f"2020-W{i:02d}" if i < 54 else f"2021-W{i - 53:02d}"


# ── criterion 1 ──────────────────────────────────────────────────────────────

def test_state_information_passes_when_confirmed_outperforms():
    # price rises 2% in weeks following CONFIRMED, falls 1% after NEUTRAL
    states = {"gold": {}}
    prices = {"gold": {}}
    px = 100.0
    for i in range(1, 21):
        state = "CONFIRMED" if i <= 10 else "NEUTRAL"
        states["gold"][wk(i)] = state
        prices["gold"][wk(i)] = px
        px *= 1.02 if state == "CONFIRMED" else 0.99
    result = falsify.state_information(states, prices)["gold"]
    assert result["pass"] is True
    assert result["confirmed_mean"] == pytest.approx(0.02, abs=1e-9)


def test_state_information_uses_next_week_return():
    # the CONFIRMED week itself is followed by a crash: pass must be False
    # (state at t is graded on t -> t+1, never on the move INTO t)
    states = {"gold": {wk(1): "CONFIRMED", wk(2): "NEUTRAL", wk(3): "NEUTRAL"}}
    prices = {"gold": {wk(1): 100.0, wk(2): 90.0, wk(3): 95.0}}
    result = falsify.state_information(states, prices)["gold"]
    assert result["confirmed_mean"] == pytest.approx(-0.10)
    assert result["pass"] is False


def test_state_information_none_when_a_side_missing():
    states = {"gold": {wk(1): "NEUTRAL", wk(2): "NEUTRAL"}}
    prices = {"gold": {wk(1): 100.0, wk(2): 101.0}}
    assert falsify.state_information(states, prices)["gold"]["pass"] is None


# ── criterion 2 ──────────────────────────────────────────────────────────────

def build_entry_world(post_crowded=-0.2, post_confirmed=0.2):
    states, prices = {"m": {}}, {"m": {}}
    px = 100.0
    # 30 NEUTRAL weeks, then CONFIRMED entry, 26 weeks later CROWDED entry
    for i in range(1, 92):
        if i < 31:
            state = "NEUTRAL"
        elif i < 61:
            state = "CONFIRMED"
        else:
            state = "CROWDED"
        states["m"][wk(i)] = state
        prices["m"][wk(i)] = px
        if 31 <= i < 57:  # 26 weeks after the CONFIRMED entry at wk31
            px *= (1 + post_confirmed) ** (1 / 26)
        elif i >= 61:
            px *= (1 + post_crowded) ** (1 / 26)
    return states, prices


def test_crowd_test_passes_when_crowded_underperforms():
    states, prices = build_entry_world()
    result = falsify.crowd_test(states, prices)
    assert result["pass"] is True
    assert result["crowded_entries"] == 1 and result["confirmed_entries"] == 1


def test_crowd_test_none_without_crowded_entries():
    states = {"m": {wk(1): "NEUTRAL", wk(2): "CONFIRMED"}}
    prices = {"m": {wk(i): 100.0 + i for i in range(1, 40)}}
    assert falsify.crowd_test(states, prices)["pass"] is None


# ── criterion 3 ──────────────────────────────────────────────────────────────

def test_weather_overlap_clips_to_replay_period():
    # a monster crash BEFORE the weather light exists must not be graded
    index = {}
    px = 1000.0
    for i in range(1, 301):
        index[f"W{i:03d}"] = px
        px *= 0.96 if 20 <= i <= 46 else 1.003  # pre-period crash at 20-46
    weather = {f"W{i:03d}": "GREEN" for i in range(100, 301)}  # starts at 100
    result = falsify.weather_overlap(weather, index)
    assert all(s >= "W100" for _r, s, _e in result["windows"])


def test_weather_overlap_counts_hits():
    # 200 weeks; one deep crash at weeks 80-106 flagged RED, gentle rise rest
    weather, index = {}, {}
    px = 1000.0
    for i in range(1, 201):
        week = f"W{i:03d}"  # sortable synthetic labels
        index[week] = px
        weather[week] = "RED" if 80 <= i <= 106 else "GREEN"
        px *= 0.97 if 80 <= i <= 106 else 1.003
    result = falsify.weather_overlap(weather, index)
    # fewer than 10 non-overlapping windows may exist; pass is None then —
    # but the crash window must be among the chosen and counted as a hit
    assert result["hits"] >= 1
    worst = result["windows"][0]
    assert worst[0] < -0.3  # the crash window leads the list


def test_weather_overlap_pass_requires_six_of_ten():
    # 600 flat-ish weeks with 10 separated crashes; only 6 have YELLOW cover
    weather, index = {}, {}
    px = 1000.0
    crash_starts = [i * 55 + 20 for i in range(10)]
    for i in range(1, 601):
        week = f"W{i:03d}"
        index[week] = px
        in_crash = any(s <= i < s + 26 for s in crash_starts)
        covered = any(s <= i < s + 26 for s in crash_starts[:6])
        weather[week] = "YELLOW" if (in_crash and covered) else "GREEN"
        px *= 0.985 if in_crash else 1.004
    result = falsify.weather_overlap(weather, index)
    assert result["pass"] is True and result["hits"] >= 6


# ── criterion 4 ──────────────────────────────────────────────────────────────

def test_event_rate_band():
    states = {"m": {}}
    # 3 years of weekly states with 24 actionable transitions -> 8/yr: pass
    i = 1
    for cycle in range(12):
        for state, weeks in (("NEUTRAL", 8), ("CONFIRMED", 4), ("BROKEN", 1)):
            for _ in range(weeks):
                states["m"][f"W{i:03d}"] = state
                i += 1
    result = falsify.event_rate(states, years=3.0)
    assert result["events"] == 24  # 12 x (into CONFIRMED + into BROKEN)
    assert result["verdict"] == "pass"


def test_event_rate_hard_fail_when_mute():
    states = {"m": {f"W{i:03d}": "NEUTRAL" for i in range(1, 157)}}
    result = falsify.event_rate(states, years=3.0)
    assert result["verdict"] == "hard_fail" and result["events"] == 0
