import { describe, expect, it } from "vitest";
import type { SymbolSearchResult } from "../../bindings";
import { searchResultToRow } from "../providers/openalgo";

// The data-plugin search rows (#331): the host keeps the plugin's security
// type and derives a market bucket; the adapter must land each row in the
// tab it belongs to — a plugin row with an app-level type ("index",
// "futures"…) as well as the provider's own broker-token rows (EQ, FUT, IX).

const row = (over: Partial<SymbolSearchResult>): SymbolSearchResult => ({
  ticker: "X",
  name: "X",
  market: "stocks",
  locale: null,
  exchange: "NSE",
  type: null,
  ...over,
});

describe("openalgo searchResultToRow: the plugin rows land in their tabs", () => {
  it("app-level types (the plugin's rows) map to their market and tab", () => {
    expect(searchResultToRow(row({ ticker: "SBIN-EQ", type: "stock" }))).toMatchObject({ marketType: "stock", category: "stocks" });
    expect(searchResultToRow(row({ ticker: "NIFTY", type: "index" }))).toMatchObject({ marketType: "index", category: "indices" });
    expect(searchResultToRow(row({ ticker: "BANKNIFTY25NOV", type: "futures" }))).toMatchObject({ marketType: "futures", category: "futures" });
    expect(searchResultToRow(row({ ticker: "EURINR", type: "forex" }))).toMatchObject({ marketType: "forex", category: "forex" });
  });

  it("the broker-token rows keep their mapping", () => {
    expect(searchResultToRow(row({ ticker: "SBIN", type: "EQ" }))).toMatchObject({ marketType: "stock", category: "stocks" });
    expect(searchResultToRow(row({ ticker: "NIFTYFUT", type: "FUT" }))).toMatchObject({ marketType: "futures", category: "futures" });
    expect(searchResultToRow(row({ ticker: "NIFTY", type: "IX" }))).toMatchObject({ marketType: "index", category: "indices" });
  });

  it("a row with no type falls back through its market bucket", () => {
    expect(searchResultToRow(row({ type: null, market: "indices" }))).toMatchObject({ marketType: "index", category: "indices" });
    expect(searchResultToRow(row({ type: null, market: "futures" }))).toMatchObject({ marketType: "futures", category: "futures" });
    expect(searchResultToRow(row({ type: null, market: "fx" }))).toMatchObject({ marketType: "forex", category: "forex" });
    expect(searchResultToRow(row({ type: null, market: "stocks" }))).toMatchObject({ marketType: "stock", category: "stocks" });
  });

  it("the whole symbol carries the row's exchange", () => {
    expect(searchResultToRow(row({ ticker: "NIFTY", exchange: "NSE_INDEX" })).symbolName).toBe("NSE_INDEX:NIFTY");
  });
});
