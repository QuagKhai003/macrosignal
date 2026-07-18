// Screen 2 — Market Detail (§2): verdict, four question cards, the plan,
// history strip, numbers behind "Show the numbers".
import { notFound } from "next/navigation";
import { latestWeek, statesForWeek, marketHistory, latestPrice } from
  "../../../lib/db";
import { MARKET_NAME, STATE, questionCards } from "../../../lib/dictionary";
import { verdictSentence, whatWouldChange } from "../../../lib/verdict";

export const dynamic = "force-dynamic";

export default async function MarketDetail({ params }) {
  const { id } = await params;
  if (!MARKET_NAME[id]) notFound();
  const week = latestWeek();
  const row = statesForWeek(week).find((r) => r.market_id === id);
  if (!row) notFound();
  const s = row.scores;
  const months = monthlyStrip(marketHistory(id));
  const price = latestPrice(id);

  return (
    <>
      <h1>{MARKET_NAME[id]}</h1>
      <p className="verdict">{verdictSentence(row.state, s)}</p>

      {questionCards(s).map((q) => (
        <div className="card" key={q.title}>
          <div className="qrow">
            <span style={{ fontWeight: 600 }}>{q.title}</span>
            <span className="mono">{q.value}</span>
          </div>
          <div>{q.answer}</div>
        </div>
      ))}

      <h2>What would change this verdict</h2>
      <ul>
        {whatWouldChange(row.state, s).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>

      <h2>The last two years</h2>
      <ul className="strip" aria-label="Monthly state history">
        {months.map((m) => (
          <li key={m.month} className={STATE[m.state].cls}
            title={`${m.month}: ${STATE[m.state].phrase}`}
            aria-label={`${m.month}: ${STATE[m.state].phrase}`} />
        ))}
      </ul>

      <details className="numbers">
        <summary>Show the numbers</summary>
        <Num label="How full is the trade" value={fmt(s.party_pct, "of 100")}
          src="CFTC weekly positions" />
        <Num label="Reason-signal answer"
          value={s.engine == null ? "no answer" : s.engine ? "yes" : "no"}
          src="market driver formulas" />
        <Num label="Price vs its own year"
          value={s.momentum == null ? "no answer" : s.momentum ? "above" : "below"}
          src="daily closes, 200-day average" />
        <Num label="News volume vs normal" value={fmt(s.news_volume_ratio, "x")}
          src="news counts, weekly" />
        <Num label="Greedy share of labeled headlines"
          value={fmt(s.greed_ratio == null ? null : s.greed_ratio * 100, "%")}
          src={`${s.n_labeled ?? 0} labeled headlines`} />
        <Num label="Suggested size if acted on"
          value={fmt((s.size_fraction ?? 0) * 100, "% of one slot")}
          src="sizing formula + weather" />
        {price && <Num label="Latest price" value={String(price.value)}
          src={`daily close, ${price.data_date}`} />}
      </details>
    </>
  );
}

function Num({ label, value, src }) {
  return (
    <div className="numrow">
      <span>{label}<br /><span className="soft">Source: {src}</span></span>
      <span className="mono">{value}</span>
    </div>
  );
}

function fmt(v, unit) {
  return v == null ? "no answer" : `${Math.round(v * 10) / 10} ${unit}`;
}

function monthlyStrip(history) {
  const byMonth = new Map();
  for (const h of history) {
    // week "2026-W29" -> month key of that ISO week's Thursday
    const [y, w] = h.week.split("-W").map(Number);
    const thursday = isoWeekThursday(y, w);
    byMonth.set(thursday.toISOString().slice(0, 7), h.state);
  }
  return [...byMonth.entries()].slice(-24)
    .map(([month, state]) => ({ month, state }));
}

function isoWeekThursday(year, week) {
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7)
    + (week - 1) * 7);
  monday.setUTCDate(monday.getUTCDate() + 3);
  return monday;
}
