// Read-only window onto signals.db (batch 6.5). The dashboard NEVER fetches
// or writes — it renders what the Saturday machine stored (ADR-0007).
// Db path: ../signals.db from the dashboard folder, or MACROSIGNAL_DB.
import Database from "better-sqlite3";
import path from "node:path";

let db;
function conn() {
  if (!db) {
    const p = process.env.MACROSIGNAL_DB ??
      path.join(process.cwd(), "..", "signals.db");
    db = new Database(p, { readonly: true, fileMustExist: true });
  }
  return db;
}

export function latestWeek() {
  return conn().prepare("SELECT MAX(week) AS w FROM states").get()?.w ?? null;
}

export function statesForWeek(week) {
  return conn()
    .prepare("SELECT market_id, state, age_weeks, scores_json FROM states" +
             " WHERE week = ? ORDER BY market_id")
    .all(week)
    .map((r) => ({ ...r, scores: JSON.parse(r.scores_json) }));
}

export function previousStates(week) {
  const prev = conn()
    .prepare("SELECT MAX(week) AS w FROM states WHERE week < ?")
    .get(week)?.w;
  if (!prev) return {};
  const out = {};
  for (const r of conn()
    .prepare("SELECT market_id, state FROM states WHERE week = ?")
    .all(prev)) out[r.market_id] = r.state;
  return out;
}

export function marketHistory(marketId, weeks = 110) {
  return conn()
    .prepare("SELECT week, state FROM states WHERE market_id = ?" +
             " ORDER BY week DESC LIMIT ?")
    .all(marketId, weeks)
    .reverse();
}

export function journalRows(limit = 200) {
  return conn()
    .prepare("SELECT date, market_id, event_type, detail, price_at_event" +
             " FROM journal WHERE event_type IN" +
             " ('state_change', 'veto', 'action_taken')" +
             " ORDER BY journal_id DESC LIMIT ?")
    .all(limit);
}

export function alarmEvents(sinceIso) {
  return conn()
    .prepare("SELECT COUNT(*) AS n FROM journal WHERE event_type IN" +
             " ('state_change', 'flag') AND date >= ?")
    .get(sinceIso).n;
}

export function lastRun() {
  return conn()
    .prepare("SELECT date, detail FROM journal WHERE event_type = 'run'" +
             " ORDER BY journal_id DESC LIMIT 1")
    .get() ?? null;
}

export function weeklyReadouts(week) {
  const row = conn()
    .prepare("SELECT world_json, forward_json, sim_json FROM" +
             " weekly_readouts WHERE week = ?")
    .get(week);
  if (!row) return null;
  return { world: JSON.parse(row.world_json),
           forward: JSON.parse(row.forward_json),
           sims: JSON.parse(row.sim_json) };
}

export function latestPrice(marketId) {
  return conn()
    .prepare("SELECT data_date, value FROM observations WHERE series_id = ?" +
             " ORDER BY data_date DESC LIMIT 1")
    .get(`price_${marketId}`) ?? null;
}
