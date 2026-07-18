// Screen 1 — This Week (§2): weather banner, changes, the unchanged line.
// No charts, no numbers: sentences force verdicts.
import Link from "next/link";
import { latestWeek, statesForWeek, previousStates, lastRun } from "../lib/db";
import { MARKET_NAME, STATE, WEATHER } from "../lib/dictionary";
import { verdictSentence } from "../lib/verdict";

export const dynamic = "force-dynamic";

export default function ThisWeek() {
  const week = latestWeek();
  if (!week) {
    return <p className="nothing">No weekly run stored yet. Run the Saturday
      batch once, then reload.</p>;
  }
  const rows = statesForWeek(week);
  const prev = previousStates(week);
  const weather = WEATHER[rows[0]?.scores.weather ?? "YELLOW"];
  const changes = rows.filter((r) => (prev[r.market_id] ?? "NEUTRAL") !== r.state);
  const run = lastRun();

  return (
    <>
      <details className={`banner ${weather.cls}`}>
        <summary>
          <span aria-hidden="true">{weather.mark}</span> {weather.phrase}
        </summary>
        <p>The four market-wide gauges (professional cash, valuation, credit
          stress, the money tide) are computed each Saturday; this light is
          their combined answer. Sizes scale with it automatically.</p>
      </details>

      {changes.length === 0 ? (
        <p className="nothing">Nothing changed this week. Do nothing.</p>
      ) : (
        changes.map((r) => (
          <Link className="card" key={r.market_id}
            href={`/market/${r.market_id}`}>
            <div style={{ fontWeight: 600 }}>{MARKET_NAME[r.market_id]}</div>
            <div className="verdict">{verdictSentence(r.state, r.scores)}</div>
            <div className="soft">
              was: {STATE[prev[r.market_id] ?? "NEUTRAL"].phrase}
            </div>
          </Link>
        ))
      )}
      {changes.length > 0 && changes.length < rows.length && (
        <p className="soft">Everything else is unchanged
          ({rows.length - changes.length} markets).</p>
      )}

      <h2>All markets</h2>
      {rows.map((r) => (
        <Link className="card" key={r.market_id} href={`/market/${r.market_id}`}>
          <div className="qrow">
            <span style={{ fontWeight: 600 }}>{MARKET_NAME[r.market_id]}</span>
            <span className={`chip ${STATE[r.state].cls}`}>
              <span aria-hidden="true">{STATE[r.state].mark}</span>
              {STATE[r.state].phrase}
            </span>
          </div>
        </Link>
      ))}
      {run && <p className="soft">Week {week} — computed {run.date}.</p>}
    </>
  );
}
