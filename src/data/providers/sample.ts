/*
 * Sample frontend presentation adapter — the vendor-specific normalization for
 * the deterministic browser feed (`../sample-feed`). Mirrors the backend-side
 * split (`data/provider/sample.rs` pattern): to add a real vendor, copy this
 * file, adjust the maps, and register it in `./index.ts`.
 *
 * Vendor-specific frontend transforms:
 *   • exchange-code normalization — sample codes are already display-ready
 *     ("NSE"/"BSE"), so this is the identity with a small alias table;
 *   • symbol-search mapping — turning raw search results (sample security
 *     types "EQ"/"FUT"/"OPT"/"IX") into dialog rows, plus the Type-filter
 *     dropdown options. The generic ranking/filtering stays in ../symbol-search.
 */
import type { SymbolSearchResult } from "../../bindings";
import type { SymbolRow, SymbolCategoryId, TypeFilter } from "../symbol-search";

/** Bare tickers with no "EXCHANGE:" prefix default to this exchange. */
export const defaultExchange = "NSE";

/** Alias → display exchange label. Unmapped codes fall through unchanged. */
const EXCHANGE_NAMES: Record<string, string> = {
  NSE: "NSE",
  BSE: "BSE",
  NFO: "NFO",
  BFO: "BFO",
  MCX: "MCX",
};

/** Map a vendor exchange code to its display name, falling back to the raw
 *  value when there's no mapping (or it's already a friendly name). */
export function exchangeName(code: string | null | undefined): string {
  if (!code) return "";
  return EXCHANGE_NAMES[code.toUpperCase()] ?? code;
}

// ── Symbol search ────────────────────────────────────────────────────────────

/** "All types" dropdown options — sample `type` codes. null = no filter. */
export const typeFilters: TypeFilter[] = [
  { label: "All types", code: null },
  { label: "Equity", code: "EQ" },
  { label: "Futures", code: "FUT" },
  { label: "Options", code: "OPT" },
  { label: "Index", code: "IX" },
];

/** Sample security type → the short marketType label shown on the chip. */
const TYPE_LABELS: Record<string, string> = {
  EQ: "stock",
  FUT: "futures",
  OPT: "options",
  IX: "index",
};

/** Sample security type → category-tab override. Everything not listed lands
 *  in Stocks via the market mapping below. */
const TYPE_CATEGORY: Partial<Record<string, SymbolCategoryId>> = {
  FUT: "futures",
  OPT: "options",
  IX: "indices",
};

/** Map one raw search result into a dialog row. The vendor half of symbol
 *  search; ../symbol-search applies the generic ranking. */
export function searchResultToRow(r: SymbolSearchResult): SymbolRow {
  const ex = r.primaryExchange || "";
  const marketType = (r.type && TYPE_LABELS[r.type]) || "stock";
  return {
    symbolName: ex ? `${ex}:${r.ticker}` : r.ticker,
    ticker: r.ticker,
    description: r.name ?? "",
    marketType,
    exchange: ex,
    exchangeTooltip: ex || undefined,
    primaryExchange: !!ex,
    category: (r.type && TYPE_CATEGORY[r.type]) || "stocks",
  };
}

// ── Adapter object ───────────────────────────────────────────────────────────
// `name` must match the provider id the backend (or sample feed) reports so
// the frontend can select it via `syncProvider()` in ./index.
import type { FrontendProvider } from "./index";

export const sample: FrontendProvider = {
  name: "sample",
  defaultExchange,
  exchangeName,
  searchResultToRow,
  typeFilters,
};
