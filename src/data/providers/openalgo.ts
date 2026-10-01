/*
 * OpenAlgo frontend presentation adapter — vendor-specific normalization for
 * the browser OpenAlgo source (`../sources/openalgo`). Copy pattern from
 * `./sample.ts`: to add a vendor, copy this file, adjust the maps, register
 * in `./index.ts`.
 *
 * OpenAlgo specifics (tradewithmadhan/openalgo fork docs):
 *   • exchange codes are already display-ready (NSE/BSE/NFO/BFO/CDS/BCD/MCX);
 *   • security types are EQ/FUT/CE/PE/IX (equity `/symbol` returns "" which
 *     maps like EQ);
 *   • `/search` accepts an optional exchange but no security-type filter, so
 *     the dialog's type filter is applied client-side in `sources/openalgo`.
 */
import type { SymbolSearchResult } from "../../bindings";
import type { SymbolRow, SymbolCategoryId, TypeFilter } from "../symbol-search";

/** Bare tickers with no "EXCHANGE:" prefix default to this exchange. */
export const defaultExchange = "NSE";

/** Alias → display exchange label. NSE_INDEX/BSE_INDEX are first-class
 *  exchange codes (verified live: accepted by /history and /quotes, returned
 *  by /search) — shown as-is. Unmapped codes fall through unchanged. */
const EXCHANGE_NAMES: Record<string, string> = {
  NSE: "NSE",
  BSE: "BSE",
  NFO: "NFO",
  BFO: "BFO",
  CDS: "CDS",
  BCD: "BCD",
  MCX: "MCX",
  NSE_INDEX: "NSE_INDEX",
  BSE_INDEX: "BSE_INDEX",
};

/** Map a vendor exchange code to its display name, falling back to the raw
 *  value when there's no mapping (or it's already a friendly name). */
export function exchangeName(code: string | null | undefined): string {
  if (!code) return "";
  return EXCHANGE_NAMES[code.toUpperCase()] ?? code;
}

/** Exchange prefix of a symbol's full name. Broker codes are already prefixes
 *  (NSE, BSE, NFO, MCX, …), so this is the uppercase code (the default
 *  exchange when unknown). */
export function exchangeCode(code: string | null | undefined): string {
  if (!code) return defaultExchange;
  return code.toUpperCase().replace(/\s+/g, "");
}

// ── Symbol search ────────────────────────────────────────────────────────────

/** "All types" dropdown options — OpenAlgo `instrumenttype` codes. */
export const typeFilters: TypeFilter[] = [
  { label: "All types", code: null },
  { label: "Equity", code: "EQ" },
  { label: "Futures", code: "FUT" },
  { label: "Call", code: "CE" },
  { label: "Put", code: "PE" },
  { label: "Index", code: "IX" },
];

/** OpenAlgo instrument type → the short marketType label shown on the chip.
 *  Indices arrive as "INDEX" (verified live); both map to index. */
const TYPE_LABELS: Record<string, string> = {
  EQ: "stock",
  FUT: "futures",
  CE: "options",
  PE: "options",
  IX: "index",
  INDEX: "index",
};

/** OpenAlgo instrument type → category-tab override. Equities (EQ and the
 *  empty string the `/symbol` endpoint returns for them) land in Stocks. */
const TYPE_CATEGORY: Partial<Record<string, SymbolCategoryId>> = {
  FUT: "futures",
  CE: "options",
  PE: "options",
  IX: "indices",
  INDEX: "indices",
};

/** Map one raw search result into a dialog row. The vendor half of symbol
 *  search; ../symbol-search applies the generic ranking. */
export function searchResultToRow(r: SymbolSearchResult): SymbolRow {
  const ex = exchangeName(r.primaryExchange);
  // Equity arrives as "EQ" (search) or "" (symbol detail) — both are stock.
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
    // All broker venues are Indian listings.
    country: "IN",
  };
}

// ── Adapter object ───────────────────────────────────────────────────────────
// `name` must match the source id so the frontend can select it.
import type { FrontendProvider } from "./index";

export const openalgo: FrontendProvider = {
  name: "openalgo",
  defaultExchange,
  exchangeName,
  exchangeCode,
  searchResultToRow,
  typeFilters,
};
