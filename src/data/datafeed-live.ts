/*
 * Datafeed live-tick subscription helpers — Feature 9.
 *
 * Thin typed wrappers over the active `DataSource` (see `sources/`): which
 * backend serves a call — Tauri IPC or the sample engine — is the selector's
 * concern. Intentionally hand-written (parallel to bindings.ts) so the
 * frontend can wire to the live task before `bindings.ts` regenerates.
 *
 * Event-name convention (`tauri_specta::Event`): struct name → kebab-case
 *   ChartAggregate  → "chart-aggregate"
 *   SecondAggregate → "second-aggregate"
 *   TradeTick       → "trade-tick"
 */
import type { UnlistenFn } from "@tauri-apps/api/event";
import { source } from "./sources";

export type ChartAggregate = {
  symbol: string;
  /** Bar start, UNIX seconds — matches lightweight-charts UTCTimestamp. */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  /** Today's full-session daily bar, when the transport knows it (the REST
   *  snapshot poller does). Its `time` is the snapshot's update stamp, NOT a
   *  bar boundary — the datafeed re-buckets it (see bucketLiveTick). */
  day?: {
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  } | null;
};

/** One 1-second bar from the stream (Massive `A` channel) for a charted
 *  symbol. Emitted only when the key is entitled to second bars and a chart
 *  is open; its volume covers that one second. */
export type SecondAggregate = {
  symbol: string;
  /** Bar start, UNIX seconds. */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type TradeTick = {
  symbol: string;
  /** Regular-session "Last" (not the latest pre/post trade). */
  price: number;
  /** Regular-session change / change% / volume from the REST snapshot poller.
   *  Null on the WS push path (a raw trade has none) — consumers keep the prior
   *  value. */
  change: number | null;
  changePercent: number | null;
  /** Pre/post-market move vs the regular close — the watchlist "Ext" column. */
  extChangePercent: number | null;
  volume: number | null;
  /** "live" (today's session traded) or "prev". Null on the WS path. */
  source: "live" | "prev" | null;
};

/** Declare one pane's charted-symbol subscription. Pass `null` to clear that
 *  pane's slot. `pane` scopes the slot within this window (the backend keys
 *  it "<window>:<pane>"), so multi-pane layouts each hold their own symbol
 *  instead of overwriting a single global slot. */
export async function setChartSubscription(symbol: string | null, pane = "0"): Promise<void> {
  await source().setChartSubscription(symbol, pane);
}

/** Replace THIS WINDOW's watchlist subscription with this exact set. Several
 *  features contribute symbols to the channel (watchlist rows, alert rules,
 *  tab titles — see data/subscriptions.ts); the backend merges the per-window
 *  sets, so another window's union can't clobber this one. */
export async function setWatchlistSubscription(symbols: string[]): Promise<void> {
  await source().setWatchlistSubscription(symbols);
}

export function onChartAggregate(
  fn: (ev: ChartAggregate) => void,
): Promise<UnlistenFn> {
  return source().onChartAggregate(fn);
}

export function onSecondAggregate(
  fn: (ev: SecondAggregate) => void,
): Promise<UnlistenFn> {
  return source().onSecondAggregate(fn);
}

export function onTradeTick(fn: (ev: TradeTick) => void): Promise<UnlistenFn> {
  return source().onTradeTick(fn);
}
