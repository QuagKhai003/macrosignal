// Screen 3 — Journal & System (§2): the honesty ledger + alarm budget.
// Read-only, chronological, boring on purpose.
import { journalRows, alarmEvents, lastRun, latestPrice } from "../../lib/db";
import { MARKET_NAME } from "../../lib/dictionary";

export const dynamic = "force-dynamic";

const EVENT_PHRASE = {
  state_change: "state changed",
  veto: "a buy was vetoed",
  action_taken: "action taken",
};

export default function Journal() {
  const rows = journalRows();
  const yearAgo = new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10);
  const events = alarmEvents(yearAgo);
  const run = lastRun();

  return (
    <>
      <h1>Journal &amp; system</h1>

      <div className="card">
        <div className="qrow">
          <span style={{ fontWeight: 600 }}>Alert budget, rolling year</span>
          <span className="mono">{events} of 15</span>
        </div>
        <div className="soft">The machine allows itself 5 to 15 noteworthy
          events a year. More would be noise; fewer than 3 would mean it has
          gone mute.</div>
      </div>
      {run && (
        <div className="card">
          <div className="qrow">
            <span style={{ fontWeight: 600 }}>Last weekly run</span>
            <span className="mono">{run.date}</span>
          </div>
          <div className="soft">{run.detail}</div>
        </div>
      )}

      <h2>Every decision, oldest consequences visible</h2>
      {rows.length === 0 && (
        <p className="soft">No state changes journaled yet.</p>
      )}
      {rows.map((r, i) => {
        const now = r.market_id ? latestPrice(r.market_id) : null;
        return (
          <div className={`journal-row${r.event_type === "veto" ? " watch" : ""}`}
            key={i}>
            <span className="mono">{r.date}</span>{" "}
            <strong>{r.market_id ? MARKET_NAME[r.market_id] : "machine"}</strong>{" "}
            — {EVENT_PHRASE[r.event_type] ?? r.event_type}: {r.detail}
            {r.price_at_event != null && now && (
              <span className="soft"> (price then {r.price_at_event}, now{" "}
                {now.value})</span>
            )}
          </div>
        );
      })}
    </>
  );
}
