"""Report voice tests — both voices golden-pinned.

@context  Batch 2.4: the silence voice must be the default; internal state
          names must never appear; --full adds the numbers.
@done     No-change voice, change voice with was-context, dictionary-only
          output, full appendix.
@todo     —
@limits   Pure string assertions.
@affects  src/report.py.
"""

from src import report


def result(state="NEUTRAL", age=0, size=0.0, party=50.0, momentum=0):
    return {"state": state, "age_weeks": age, "size_fraction": size,
            "party_pct": party, "momentum": momentum, "engine": None,
            "alive": None, "news": "quiet"}


RESULTS = {"gold": result(), "wti": result("CONFIRMED", 0, 0.10, 17.6, 1),
           "ust10y": result(), "eur": result(), "corn": result()}


def test_nothing_changed_voice():
    prev = {m: r["state"] for m, r in RESULTS.items()}
    text = report.build(RESULTS, prev, "2026-W29")
    assert "Nothing changed this week. Do nothing." in text
    assert "was:" not in text


def test_change_voice_uses_dictionary():
    prev = {m: "NEUTRAL" for m in RESULTS}
    text = report.build(RESULTS, prev, "2026-W29")
    assert "Oil (WTI): Green light — entry allowed (was: Nothing to see)" in text
    assert "Everything else is unchanged (4 markets)." in text
    assert "Nothing changed this week" not in text


def test_internal_names_never_shown():
    prev = {m: "NEUTRAL" for m in RESULTS}
    text = report.build(RESULTS, prev, "2026-W29", full=True,
                        summary={"real_yield_pct": 99.3})
    for internal in ("CONFIRMED", "NEUTRAL", "CROWDED", "BROKEN", "EARLY"):
        assert internal not in text


def test_full_appends_numbers():
    prev = {m: m == "wti" and "NEUTRAL" or RESULTS[m]["state"] for m in RESULTS}
    text = report.build(RESULTS, prev, "2026-W29", full=True,
                        summary={"real_yield_pct": 99.3})
    assert "how full: 18 of 100" in text
    assert "price: moving" in text
    assert "real_yield_pct: 99.3" in text
    # default (non-full) hides them
    brief = report.build(RESULTS, prev, "2026-W29")
    assert "how full" not in brief


def test_weather_line_always_first_section():
    prev = {m: r["state"] for m, r in RESULTS.items()}
    text = report.build(RESULTS, prev, "2026-W29")
    assert text.splitlines()[1] == "Weather: Caution — half sizes"
