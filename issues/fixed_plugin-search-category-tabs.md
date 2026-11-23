# Bug report — for filing on deepentropy/opentrader

> **FIXED upstream** in `121c1d3b` (issue #385), adopted in this fork. Kept for reference.


> Drafted 22/11/2026. Confirmed against current upstream/main (0.4.30). Paste everything below the line into the issue body.

Title of the issue: **Plugin search rows go to the wrong category tab (Options land in Stocks)**

Paste everything below the line into the issue body.

---

### What happens

When a data plugin serves symbol-search results, the category tabs (Stocks / Funds / Futures / Indices / Options) do not route those rows correctly: **option rows appear under Stocks**, and futures rows can be misrouted too. Only "All" shows everything as expected. The plugin sends the correct security type for each row, but the app ignores it for these tabs.

### Root cause

When a plugin answers `data.search`, the Rust host (`src-tauri/src/plugins/data.rs`, `found_rows`) sets each result's `market` via `market_of(&kind)` and copies the plugin's kind into `type`. Plugins send app-level kinds: `stock`, `futures`, `option`, `index`, `fund`.

- `market_of()` has cases for `index`, `futures`/`cfd`/`commodity`, `forex`, `crypto` — but **not `option`**, so an option row gets `market: "stocks"`.
- The frontend maps a result to a dialog row with `searchResultToRow()`. With the default backend provider (`gateway`), the gateway adapter (`src/data/providers/gateway.ts`) is active. Its `TYPE_CATEGORY` has `etf/etn/etv/fund`, `index`, `bond` — but **no `option` or `futures`**.

So `category = TYPE_CATEGORY[type] || MARKET_CATEGORY[market] || "stocks"`:
- `option` → no TYPE_CATEGORY entry, `market: "stocks"` → **Stocks** (should be Options)
- `futures` → no TYPE_CATEGORY entry, but `market: "futures"` → Futures (works via market, but fragile)
- `stock`, `index`, `fund` → fine

The plugin cannot work around this: it already sends the correct `type`; the app just doesn't map it to a tab.

### Why it matters

- Any broker plugin serving options shows its option contracts in the Stocks tab — wrong and confusing.
- The category tabs are a primary way to navigate search results; misrouting makes a correct plugin look broken.
- This is an incomplete piece of plugin support in the generic adapter, not a provider-specific issue.

### Suggested fix

Make the plugin's app-level kinds route to their tabs regardless of adapter. Add them to the gateway adapter's `TYPE_CATEGORY` (`src/data/providers/gateway.ts`):

```ts
const TYPE_CATEGORY = {
  etf: "funds", etn: "funds", etv: "funds", fund: "funds",
  index: "indices", bond: "bonds",
  stock: "stocks", futures: "futures", option: "options",   // add
};
```

Optionally also add `"option"` to the Rust `market_of()` for a consistent `market`, though the `TYPE_CATEGORY` entry alone fixes the tab because the type takes priority. A test can assert that `searchResultToRow({ type: "option", market: "stocks" }).category === "options"`.

### Additional context

- Observed with a broker plugin on 0.4.30 (Plugin API 3); the plugin's own search filters work correctly (CE → option rows, FUT → futures, IX → index). The fault is entirely in the app's category mapping.
- Same class of gap as the earlier "a data plugin's CFDs, futures and funds keep their type" change in `src-tauri/src/plugins/data.rs`, which fixed the search *label* but left the category-tab routing incomplete for options.
