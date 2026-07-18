"""The judgment — 15-year walk-forward falsification (build plan Phase 5).

@context  Replays 2011->today with as-of discipline, then grades the machine
          against the four PRE-REGISTERED §16 criteria (src/falsify.py) and
          writes docs/VALIDATION.md. Rerunnable; the report is the go/no-go
          gate for trusting the machine.
@done     Clean-slate replay (states + state_change journal are replay
          artifacts, rebuilt from observations), criteria computation, report
          writer, honest notes (news insufficient historically -> CROWDED
          unreachable; whale override absent).
@todo     —
@limits   No network. Never edits criteria (§16 verdict discipline). Wipes
          ONLY states + state_change journal rows — observations are sacred.
@affects  states/journal tables (rebuilt), docs/VALIDATION.md.
"""

import datetime as dt
import sys

from src import db, falsify, replay

REPLAY_START = dt.date(2011, 1, 8)  # a Saturday; 15+ years to today
REPORT_PATH = "docs/VALIDATION.md"


def main(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM states")
        conn.execute("DELETE FROM journal WHERE event_type = 'state_change'")
        conn.commit()
        print(f"replaying {REPLAY_START} -> {today} ...", flush=True)
        summary = replay.run(conn, REPLAY_START, today)

        as_of = today.isoformat()
        states = falsify.load_states(conn)
        prices = falsify.load_weekly_prices(conn, as_of)
        weather = falsify.load_weather(conn)
        index = falsify.load_index_weekly(conn, as_of)
        years = (today - REPLAY_START).days / 365.25

        c1 = falsify.state_information(states, prices)
        c2 = falsify.crowd_test(states, prices)
        c3 = falsify.weather_overlap(weather, index)
        c4 = falsify.event_rate(states, years)

        report = _report(today, years, summary, c1, c2, c3, c4)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write(report)
        print(report)
    finally:
        conn.close()
    return 0


def _report(today, years, summary, c1, c2, c3, c4) -> str:
    lines = [
        "# VALIDATION — pre-registered falsification results (§16 / F14)",
        "",
        f"Replay {REPLAY_START} -> {today} ({years:.1f} years, as-of "
        "discipline; criteria frozen before the run).",
        "",
        "## Flip counts (context)",
    ]
    for market, s in summary.items():
        lines.append(f"- {market}: {s['flips']} flips / {s['weeks']} weeks")
    lines += ["", "## Criterion 1 — states carry information",
              "(mean NEXT-week return, CONFIRMED must beat NEUTRAL)"]
    for market, r in c1.items():
        if r["pass"] is None:
            lines.append(f"- {market}: NO DATA (confirmed weeks: "
                         f"{r['confirmed_weeks']})")
        else:
            lines.append(
                f"- {market}: CONFIRMED {r['confirmed_mean']:+.4%}/wk over "
                f"{r['confirmed_weeks']}w vs NEUTRAL {r['neutral_mean']:+.4%}"
                f"/wk over {r['neutral_weeks']}w -> "
                f"{'PASS' if r['pass'] else 'FAIL'}")
    lines += ["", "## Criterion 2 — the crowd meter works"]
    if c2["pass"] is None:
        lines.append(
            f"- NO VERDICT: {c2['crowded_entries']} CROWDED entries (historical"
            " news is 'insufficient' by design, so CROWDED cannot fire in"
            " replay; graded from live operation onward)")
    else:
        lines.append(f"- fwd-26wk after CROWDED {c2['crowded_fwd26']:+.2%} vs"
                     f" after CONFIRMED {c2['confirmed_fwd26']:+.2%} -> "
                     f"{'PASS' if c2['pass'] else 'FAIL'}")
    lines += ["", "## Criterion 3 — the weather light works",
              f"- worst 26-week windows hit YELLOW/RED in {c3['hits']} of"
              f" {len(c3['windows'])} -> "
              + ("PASS" if c3["pass"] else "NO VERDICT (fewer than 10 windows)"
                 if c3["pass"] is None else "FAIL")]
    for r, s, e in c3["windows"]:
        lines.append(f"    {s} -> {e}: {r:+.1%}")
    lines += ["", "## Criterion 4 — muteness",
              f"- {c4['events']} actionable events / {years:.1f} yrs = "
              f"{c4['per_year']:.1f}/yr (band 5-15, hard fail <3) -> "
              f"{c4['verdict'].upper()}", "",
              "## Honest caveats",
              "- News: no historical archive (spec: operator start) -> replay"
              " news = insufficient; EARLY's not-greedy-loud condition is"
              " trivially met; CROWDED unreachable.",
              "- Whale override + weather gauge 1 (manual cash) have no"
              " historical series -> replay weather uses the computable"
              " gauges only.",
              "- ust10y/eur/corn have no driver plugins (v1 scope) -> they"
              " contribute NEUTRAL-only histories; criteria 1/4 grade the"
              " driver-equipped markets."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
