# Feature request — for filing on deepentropy/opentrader

> **FIXED upstream** in `bde3d9b8` (issue #382), adopted in this fork. Kept for reference.


> Drafted 22/11/2026. Confirmed against current upstream/main (0.4.30). Paste everything below the line into the issue body.

Title of the issue: **Route reference-data commands to data plugins so plugins match the built-in provider**

Paste everything below the line into the issue body.

---

### What I'm trying to do

Make a data plugin a first-class peer of the built-in provider. Market data and trading already work through plugins, but a set of **reference-data features are hard-wired to the built-in provider** — a plugin is never asked, so those panels are empty for plugin symbols. The app should route these commands to a plugin the same way it already routes ticker info, snapshot, sessions and search.

### Comparison: built-in provider vs data plugin

The commands marked **route to plugin: yes** already ask the plugin. The rest only ever use the built-in provider.

| App feature | Command(s) | Route to plugin today? | Plugin result for its symbols |
|---|---|---|---|
| Search | `search_tickers` | **Yes** | works |
| Symbol info | `get_ticker_info` | **Yes** | works |
| Trading sessions | `get_symbol_session` | **Yes** | works |
| Quote snapshot | `get_ticker_snapshot` | **Yes** | works |
| Bars / history | `data.bars` | **Yes** | works |
| Live ticks / depth | `data.subscribe`, `data.quote`, `data.depth` | **Yes** | works |
| Broker account / orders | `broker.*` | **Yes** | works |
| **Fundamentals** | `get_fundamentals` | No | empty |
| **Revenue breakdown** | `get_revenue_breakdown` | No | empty |
| **Filings / read filing** | `get_filings`, `read_filing` | No | empty |
| **Dividends** | `get_dividends` | No | empty (no dividend markers) |
| **Splits** | `get_splits` | No | empty |
| **Latest news** | `get_latest_news` | No | empty |
| **Economic series** | `search_economic`, `economic_countries`, `economic_sources` | No | built-in only |
| **Screener** | `screener_*` | No | uses its own feed, not the plugin |

(Verified in `src-tauri/src/commands/events.rs` and `ticker.rs`: only `get_ticker_info`, `get_symbol_session`, `get_ticker_snapshot` and `search_tickers` contain a `plugin::` route.)

### What happens today

A user who connects, for example, an Indian broker plugin gets live charts, quotes, depth and trading — but the Details panel shows no fundamentals, no filings, no news, no dividend/split markers, and the revenue/filings agent tools return nothing for those symbols. The built-in provider shows all of it. The plugin cannot fill these in because the app never asks it — there is no `data.fundamentals` / `data.news` / `data.dividends` method in the plugin protocol and the commands do not consult the plugin route.

### Why it matters

- Plugins should be genuine replacements for the built-in data source, not a degraded mode.
- These panels are visible and their absence makes a correct plugin look incomplete.
- The routing mechanism already exists and is proven for four commands — extending it is consistent, not a new architecture.
- Many brokers do expose some of this data (corporate actions, announcements, fundamentals); once the route exists, plugins can opt in.

### Suggested direction

1. Add optional plugin methods to the protocol for the reference data a plugin can serve, e.g.:
   - `data.fundamentals {ticker}` → fundamentals
   - `data.revenueBreakdown {ticker}` → breakdown
   - `data.filings {ticker, query}` / `data.filing {…}` → filings
   - `data.corporateActions {ticker}` → dividends + splits
   - `data.news {ticker, limit}` → news items
2. Route the corresponding Rust commands to the plugin when the symbol belongs to one (the `plugin::data::route(app, symbol)` pattern already used in `get_ticker_info` etc.), falling back to the built-in provider when the plugin answers `-32601` (method not supported) or has nothing.
3. Keep it optional: a plugin that does not implement a method keeps today's behaviour (empty sections), so no existing plugin breaks.
4. Economic series and the screener may stay built-in if they are not plugin-addressable — worth stating explicitly in the design.

### Additional context

- Related: #383 (custom data series from plugins), #384 (holdings tab), #385 (search category mapping), #381 (watchlists from plugins) — all part of making plugins feature-complete with the built-in provider.
- The plugin protocol already declares capabilities (`capabilities.reference.search/snapshot/dividends/splits/news`); this request is about giving a plugin a way to actually *supply* those reference kinds, not just declare them.
