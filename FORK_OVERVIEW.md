# Fork overview — tradewithmadhan/opentrader `sample-web-clean`

Generated from git. Base: upstream `0.4.28`; current fork: `0.4.29` + OpenAlgo 0.1.27.
Compare with `git diff upstream/main HEAD`.

## Summary

- **48 files added** (fork-only, mostly OpenAlgo plugin + app-side plugin host)
- **86 files modified** (a fork feature, or upstream code adopted with fork adaptations)
- **1 files deleted** (intentional — see below)

## Categories

| Category | Meaning |
|---|---|
| **A — Fork-only additions** | Exist only in our fork; no upstream equivalent. |
| **B — Upstream code adopted, fork-adapted** | Upstream feature present, but our version is newer or carries fork changes. |
| **C — Intentional fork changes** | Deliberate divergence (sponsor removal, OpenAlgo branding, web build). |
| **D — Deletions** | Removed on purpose. |

### A — Fork-only additions (47 files)

| File | Our commits touching it | Upstream origin (last commit) |
|---|---|---|
| `.github/FUNDING.yml` | — | 51fcf2f9 Remove the sponsorship: README, website, Sponsor button and About tab |
| `.github/workflows/build-windows.yml` | — | fork-only / no upstream history |
| `.github/workflows/deploy-web.yml` | — | fork-only / no upstream history |
| `build-preview-web.bat` | — | fork-only / no upstream history |
| `dev-preview-web.bat` | — | fork-only / no upstream history |
| `dev-tauri.bat` | — | fork-only / no upstream history |
| `docs/gateway-endpoints.md` | — | fork-only / no upstream history |
| `docs/new-provider-requirements.md` | — | fork-only / no upstream history |
| `docs/provider-switching-plan.md` | — | fork-only / no upstream history |
| `issues/fixed_confirm-before-deleting-script.md` | 403fbeed Deleting a script asks first and takes it off every chart; layouts, alerts and chats ask too | fork-only / no upstream history |
| `issues/fixed_dom-ladder-broker-depth.md` | 1085fca3 OpenAlgo 0.1.27: send broker.depth snapshots on upstream's protocol | fork-only / no upstream history |
| `issues/fixed_expose-chart-primitives-to-scripts.md` | d1edcde7 Scripts: the bars in view, the chart type, drawings pinned to the pane<br>7218e3e9 oakscriptjs 0.20.0, lightweight-charts-indicators 0.27.0 | fork-only / no upstream history |
| `issues/fixed_indicator-settings-dropdown-after-moving-dialog.md` | d8bce6f3 Dialogs: drop-downs open under their button after the dialog is moved | fork-only / no upstream history |
| `issues/fixed_maximized-chart-resets-on-tab-switch.md` | a639bbcd Charts: each tab keeps its maximized chart across tab switches | fork-only / no upstream history |
| `issues/fixed_order-drag-modify-not-working.md` | 1085fca3 OpenAlgo 0.1.27: send broker.depth snapshots on upstream's protocol<br>84c88327 Rename fixed upstream issue drafts with fixed_ prefix | fork-only / no upstream history |
| `issues/fixed_order-ticket-product-selection.md` | 84c88327 Rename fixed upstream issue drafts with fixed_ prefix | fork-only / no upstream history |
| `issues/fixed_pass-symbol-metadata-to-scripts.md` | c70a9458 Scripts read every syminfo field and the symbol's dividends, splits and earnings | fork-only / no upstream history |
| `issues/fixed_pine-style-input-widgets-for-scripts.md` | 5e110caa Script settings: timeframe drop-down, symbol search, text area, choice lists for numbers and enums | fork-only / no upstream history |
| `issues/fixed_plugin-seconds-bars-not-live.md` | 179426ac Plugins: seconds bars update live from the plugin's quotes, on charts and in scripts | fork-only / no upstream history |
| `issues/fixed_plugin-startup-data-not-loading.md` | 1085fca3 OpenAlgo 0.1.27: send broker.depth snapshots on upstream's protocol | fork-only / no upstream history |
| `issues/fixed_script-editor-open-my-scripts-menu.md` | cff88899 Script editor: Open script… lists every saved script | fork-only / no upstream history |
| `issues/fixed_script-session-corrections.md` | 84c88327 Rename fixed upstream issue drafts with fixed_ prefix | fork-only / no upstream history |
| `issues/fixed_study-error-not-recalculated-on-symbol-change.md` | 1fcb3034 Studies ask for their other symbols again on another chart symbol, timeframe or inputs | fork-only / no upstream history |
| `issues/fixed_upstream-app-side-history.md` | 84c88327 Rename fixed upstream issue drafts with fixed_ prefix | fork-only / no upstream history |
| `issues/fixed_upstream-broker-plugin-host-gaps.md` | 84c88327 Rename fixed upstream issue drafts with fixed_ prefix | fork-only / no upstream history |
| `issues/fixed_upstream-openalgo-plugin-changes.md` | 84c88327 Rename fixed upstream issue drafts with fixed_ prefix | fork-only / no upstream history |
| `issues/fixed_upstream-plugin-multi-exchange.md` | 84c88327 Rename fixed upstream issue drafts with fixed_ prefix | fork-only / no upstream history |
| `issues/openalgo-plugin-changes-0.1.1-0.1.8.md` | — | fork-only / no upstream history |
| `plugins/openalgo/.gitignore` | — | fork-only / no upstream history |
| `plugins/openalgo/README.md` | 1085fca3 OpenAlgo 0.1.27: send broker.depth snapshots on upstream's protocol<br>643ba2b7 Show the broker's five-level book in the DOM ladder<br>c348c6f2 OpenAlgo 0.1.25: subscribe to the five-level depth stream (real bid / ask) | fork-only / no upstream history |
| `plugins/openalgo/build.py` | — | fork-only / no upstream history |
| `plugins/openalgo/opentrader-plugin.dev.json` | — | fork-only / no upstream history |
| `plugins/openalgo/opentrader-plugin.json` | 1085fca3 OpenAlgo 0.1.27: send broker.depth snapshots on upstream's protocol<br>643ba2b7 Show the broker's five-level book in the DOM ladder<br>c348c6f2 OpenAlgo 0.1.25: subscribe to the five-level depth stream (real bid / ask) | fork-only / no upstream history |
| `plugins/openalgo/plugin.py` | 1085fca3 OpenAlgo 0.1.27: send broker.depth snapshots on upstream's protocol<br>f258635b Adapt OpenAlgo plugin to upstream's broker.depth protocol<br>643ba2b7 Show the broker's five-level book in the DOM ladder | fork-only / no upstream history |
| `plugins/openalgo/tests/fake_openalgo.py` | c348c6f2 OpenAlgo 0.1.25: subscribe to the five-level depth stream (real bid / ask)<br>d70f420c OpenAlgo 0.1.19: day charts come from the D endpoint, and history waits out a cold call<br>96fbbef1 OpenAlgo 0.1.18: a bodyless GET declares no body type, a tick pushes at once, a rate limit is retried | fork-only / no upstream history |
| `plugins/openalgo/tests/harness.py` | 96fbbef1 OpenAlgo 0.1.18: a bodyless GET declares no body type, a tick pushes at once, a rate limit is retried | fork-only / no upstream history |
| `plugins/openalgo/tests/run_all.py` | 96fbbef1 OpenAlgo 0.1.18: a bodyless GET declares no body type, a tick pushes at once, a rate limit is retried | fork-only / no upstream history |
| `plugins/openalgo/tests/test_bars.py` | d70f420c OpenAlgo 0.1.19: day charts come from the D endpoint, and history waits out a cold call<br>96fbbef1 OpenAlgo 0.1.18: a bodyless GET declares no body type, a tick pushes at once, a rate limit is retried | fork-only / no upstream history |
| `plugins/openalgo/tests/test_broker_state.py` | f258635b Adapt OpenAlgo plugin to upstream's broker.depth protocol<br>643ba2b7 Show the broker's five-level book in the DOM ladder<br>c348c6f2 OpenAlgo 0.1.25: subscribe to the five-level depth stream (real bid / ask) | fork-only / no upstream history |
| `plugins/openalgo/tests/test_http.py` | 96fbbef1 OpenAlgo 0.1.18: a bodyless GET declares no body type, a tick pushes at once, a rate limit is retried | fork-only / no upstream history |
| `plugins/openalgo/tests/test_lot.py` | b99ab708 OpenAlgo 0.1.22: drop the unsupported `validity` field from place/modify order<br>96fbbef1 OpenAlgo 0.1.18: a bodyless GET declares no body type, a tick pushes at once, a rate limit is retried | fork-only / no upstream history |
| `plugins/openalgo/tests/test_realtime.py` | c348c6f2 OpenAlgo 0.1.25: subscribe to the five-level depth stream (real bid / ask)<br>96fbbef1 OpenAlgo 0.1.18: a bodyless GET declares no body type, a tick pushes at once, a rate limit is retried | fork-only / no upstream history |
| `src-tauri/crates/core/src/data/provider/openalgo.rs` | — | fork-only / no upstream history |
| `src-tauri/crates/core/src/funding.rs` | — | 51fcf2f9 Remove the sponsorship: README, website, Sponsor button and About tab |
| `src-tauri/src/commands/funding.rs` | — | 51fcf2f9 Remove the sponsorship: README, website, Sponsor button and About tab |
| `src/bindings-fallback.ts` | fd7a040e Tick charts for data plugins with ticks; OakScript Logs panel start date and heavy script warning<br>f258635b Adapt OpenAlgo plugin to upstream's broker.depth protocol<br>643ba2b7 Show the broker's five-level book in the DOM ladder | fork-only / no upstream history |
| `src/data/__tests__/plugin-search-rows.test.ts` | — | fork-only / no upstream history |
| `src/data/broker-credentials.ts` | — | fork-only / no upstream history |
| `src/data/drawing-images-web.ts` | — | fork-only / no upstream history |
| `src/data/funding.ts` | — | 51fcf2f9 Remove the sponsorship: README, website, Sponsor button and About tab |
| `src/data/providers/__tests__/openalgo.test.ts` | — | fork-only / no upstream history |
| `src/data/providers/openalgo.ts` | — | fork-only / no upstream history |
| `src/data/sources/index.ts` | — | fork-only / no upstream history |
| `src/data/sources/tauri.ts` | — | fork-only / no upstream history |
| `src/data/sources/types.ts` | — | fork-only / no upstream history |
| `src/window/header/app-settings/DataTab.tsx` | — | fork-only / no upstream history |

### B — Upstream code adopted / fork-adapted (79 files)

| File | Our commits touching it | Upstream origin (last commit) |
|---|---|---|
| `docs/backtesting.html` | — | ef50164a Embeds: ticker tape, mini chart, quote, market movers, page colours, co |
| `docs/bar-replay.html` | — | ef50164a Embeds: ticker tape, mini chart, quote, market movers, page colours, co |
| `docs/download.html` | — | ef50164a Embeds: ticker tape, mini chart, quote, market movers, page colours, co |
| `docs/drawing-tools.html` | — | ef50164a Embeds: ticker tape, mini chart, quote, market movers, page colours, co |
| `docs/index.html` | — | ef50164a Embeds: ticker tape, mini chart, quote, market movers, page colours, co |
| `docs/scripting.html` | — | ef50164a Embeds: ticker tape, mini chart, quote, market movers, page colours, co |
| `docs/site.css` | — | 616f5b0f Embeds: markers, highlighted periods, from/to span and multi-chart layo |
| `docs/stock-screener.html` | — | ef50164a Embeds: ticker tape, mini chart, quote, market movers, page colours, co |
| `docs/technical-indicators.html` | — | ef50164a Embeds: ticker tape, mini chart, quote, market movers, page colours, co |
| `index.html` | — | aef8a994 Embeds open with no startup splash (#338) |
| `plugins/PROTOCOL.md` | 9a6b64a7 Bar Replay on tick charts: the ticks after the replay time revealed one at a time<br>fd7a040e Tick charts for data plugins with ticks; OakScript Logs panel start date and heavy script warning<br>451d8a07 DOM: the broker's order book in the bid / ask size cells, their meters and the totals | 4a374ae1 Bar Replay on tick charts: the ticks after the replay time revealed one |
| `plugins/ibkr/src/ib.rs` | 451d8a07 DOM: the broker's order book in the bid / ask size cells, their meters and the totals | a8a78a4b DOM: the broker's order book in the bid / ask size cells, their meters  |
| `src-tauri/crates/core/src/commands.rs` | — | 4aa6178f Economy search: series of 217 economies, country filter and panel from  |
| `src-tauri/crates/core/src/data/market_rest/mod.rs` | — | 3b9fe8ee Assistant tool get_revenue_breakdown: revenue by segment, product line, |
| `src-tauri/crates/core/src/data/market_rest/reference.rs` | — | a81a160b Rust backend: clippy clean outside the alert engine |
| `src-tauri/crates/core/src/data/provider/gateway.rs` | — | 190b33a2 Watchlist upload reads files as the reference app; values that cannot b |
| `src-tauri/crates/core/src/data/provider/mod.rs` | — | 3b9fe8ee Assistant tool get_revenue_breakdown: revenue by segment, product line, |
| `src-tauri/crates/core/src/history/mod.rs` | — | dc781357 Rust backend: shared command bodies, one core error type, bounded memos |
| `src-tauri/crates/core/src/lib.rs` | — | 51fcf2f9 Remove the sponsorship: README, website, Sponsor button and About tab |
| `src-tauri/crates/server/src/invoke.rs` | — | 4aa6178f Economy search: series of 217 economies, country filter and panel from  |
| `src-tauri/src/commands/mod.rs` | 8cb9f031 Exports: saved to the downloads folder with a File downloaded toast; fast ISO chart data export | 8c74d26f Exports: saved to the downloads folder with a "File downloaded" toast o |
| `src-tauri/src/commands/ticker.rs` | — | 4aa6178f Economy search: series of 217 economies, country filter and panel from  |
| `src-tauri/src/lib.rs` | fd7a040e Tick charts for data plugins with ticks; OakScript Logs panel start date and heavy script warning<br>8cb9f031 Exports: saved to the downloads folder with a File downloaded toast; fast ISO chart data export<br>451d8a07 DOM: the broker's order book in the bid / ask size cells, their meters and the totals | 681172e4 Tick charts (1T, 10T, 100T, 1000T) for data plugins with ticks; MT5 plu |
| `src-tauri/src/plugins/data.rs` | a0d11d4d Symbol search: exchange-qualified matches; data plugin CFDs, futures and funds keep their type<br>9a6b64a7 Bar Replay on tick charts: the ticks after the replay time revealed one at a time<br>fd7a040e Tick charts for data plugins with ticks; OakScript Logs panel start date and heavy script warning | 7ba4e530 Symbol search: "NASDAQ:AAPL" lists the matches on that exchange; a data |
| `src-tauri/src/plugins/mod.rs` | — | f95ef480 Plugins: a template to write one, and stop a plugin with the processes  |
| `src/App.tsx` | 7b6bfc76 Replay Trading: broker entry points<br>fd7a040e Tick charts for data plugins with ticks; OakScript Logs panel start date and heavy script warning<br>8cb9f031 Exports: saved to the downloads folder with a File downloaded toast; fast ISO chart data export | 681172e4 Tick charts (1T, 10T, 100T, 1000T) for data plugins with ticks; MT5 plu |
| `src/agent/bridge.ts` | — | 25a590b6 AI assistant panel: chat with any OpenAI-compatible model that runs the |
| `src/agent/screener-tools.ts` | — | d4443b94 Screener: one setting for the screener shown, one table of filter opera |
| `src/backtester/__tests__/oakscript.test.ts` | — | 8786ed6f A symbol's point value from data plugins: futures counted in contracts  |
| `src/backtester/oakscript.ts` | — | 8786ed6f A symbol's point value from data plugins: futures counted in contracts  |
| `src/data/__tests__/interval.test.ts` | — | 68d78420 Data plugins: the interval picker follows the intervals the plugin serv |
| `src/data/__tests__/plugin-intervals.test.ts` | 6398fd95 Plugin bar grid: a bar shared by two sessions counted once, new plugin info without a restart | e80a39b3 Plugin bar grid: a bar shared by two sessions counted once, new plugin  |
| `src/data/__tests__/symbol-info.test.ts` | — | 8786ed6f A symbol's point value from data plugins: futures counted in contracts  |
| `src/data/__tests__/watchlist-store.test.ts` | — | 32e89600 Alerts panel: watchlist alerts in the list, webhook status in the log |
| `src/data/alert-backend.ts` | — | 7f2f04ae Web build: alerts missed while the page was closed are replayed on the  |
| `src/data/alert-delivery.ts` | — | 1666ac20 Alerts: read intervals with the shared parser |
| `src/data/alert-store.ts` | — | 220fa84a Alerts: the window evaluates its rules with the shared evaluation, one  |
| `src/data/app-update.ts` | — | 03ebd34d Main menu: Open link from clipboard, Zoom, Exit; Keyboard shortcuts pan |
| `src/data/datafeed-live.ts` | fd7a040e Tick charts for data plugins with ticks; OakScript Logs panel start date and heavy script warning | 681172e4 Tick charts (1T, 10T, 100T, 1000T) for data plugins with ticks; MT5 plu |
| `src/data/deep-link.ts` | — | 17fb114f Chart links: a symbol from the address, opentrader://chart, tab menu Co |
| `src/data/kv.ts` | — | e98fb3fb kv: list the reopen-closed stack among the localStorage keys, fix comme |
| `src/data/local-series.ts` | — | 68d78420 Data plugins: the interval picker follows the intervals the plugin serv |
| `src/data/paid/plan.ts` | — | f411e9d7 Paid tier: real-time session first, probe connections, charts reload on |
| `src/data/providers/capabilities.ts` | — | d97016d5 Present OpenTrader as an independent open-source app |
| `src/data/providers/index.ts` | — | e97db812 Move data access to the gateway market API v2 |
| `src/data/providers/sample.ts` | — | fork-only / no upstream history |
| `src/data/save-file.ts` | fd7a040e Tick charts for data plugins with ticks; OakScript Logs panel start date and heavy script warning<br>8cb9f031 Exports: saved to the downloads folder with a File downloaded toast; fast ISO chart data export | 8c74d26f Exports: saved to the downloads folder with a "File downloaded" toast o |
| `src/data/screen-alerts.ts` | — | 220fa84a Alerts: the window evaluates its rules with the shared evaluation, one  |
| `src/data/screener-api.ts` | — | 99f840f4 Stock screener: download progress on the loading wheel (#229) |
| `src/data/script-screener.ts` | — | 6cbe3499 Import CSV bars as LOCAL:<name> symbols (#266) |
| `src/data/session/__tests__/symbol-sessions.test.ts` | — | 8786ed6f A symbol's point value from data plugins: futures counted in contracts  |
| `src/data/symbol-info.ts` | — | 8786ed6f A symbol's point value from data plugins: futures counted in contracts  |
| `src/data/watchlist.ts` | — | b5883378 Watchlist: panel labels, icons and menus move to right-rail, dead expor |
| `src/i18n/catalog.json` | 51af2c05 Audit upstream parity: add missing features, drop superseded assistant files, refresh i18n<br>52b718a8 Quote change against previous close; watchlist upload reads files as the reference app<br>2e92d1f3 Refresh i18n for far-date window and Replay broker entry points | 4a374ae1 Bar Replay on tick charts: the ticks after the replay time revealed one |
| `src/i18n/de.json` | 51af2c05 Audit upstream parity: add missing features, drop superseded assistant files, refresh i18n<br>73b241ad Chart links: symbol from address, opentrader://chart, tab menu Copy link<br>52b718a8 Quote change against previous close; watchlist upload reads files as the reference app | 681172e4 Tick charts (1T, 10T, 100T, 1000T) for data plugins with ticks; MT5 plu |
| `src/i18n/es.json` | 51af2c05 Audit upstream parity: add missing features, drop superseded assistant files, refresh i18n<br>73b241ad Chart links: symbol from address, opentrader://chart, tab menu Copy link<br>52b718a8 Quote change against previous close; watchlist upload reads files as the reference app | 681172e4 Tick charts (1T, 10T, 100T, 1000T) for data plugins with ticks; MT5 plu |
| `src/i18n/fr.json` | 51af2c05 Audit upstream parity: add missing features, drop superseded assistant files, refresh i18n<br>73b241ad Chart links: symbol from address, opentrader://chart, tab menu Copy link<br>52b718a8 Quote change against previous close; watchlist upload reads files as the reference app | 681172e4 Tick charts (1T, 10T, 100T, 1000T) for data plugins with ticks; MT5 plu |
| `src/i18n/ja.json` | 51af2c05 Audit upstream parity: add missing features, drop superseded assistant files, refresh i18n<br>73b241ad Chart links: symbol from address, opentrader://chart, tab menu Copy link<br>52b718a8 Quote change against previous close; watchlist upload reads files as the reference app | 681172e4 Tick charts (1T, 10T, 100T, 1000T) for data plugins with ticks; MT5 plu |
| `src/i18n/zh-Hans.json` | 51af2c05 Audit upstream parity: add missing features, drop superseded assistant files, refresh i18n<br>73b241ad Chart links: symbol from address, opentrader://chart, tab menu Copy link<br>52b718a8 Quote change against previous close; watchlist upload reads files as the reference app | 681172e4 Tick charts (1T, 10T, 100T, 1000T) for data plugins with ticks; MT5 plu |
| `src/i18n/zh-Hant.json` | 51af2c05 Audit upstream parity: add missing features, drop superseded assistant files, refresh i18n<br>73b241ad Chart links: symbol from address, opentrader://chart, tab menu Copy link<br>52b718a8 Quote change against previous close; watchlist upload reads files as the reference app | 681172e4 Tick charts (1T, 10T, 100T, 1000T) for data plugins with ticks; MT5 plu |
| `src/styles/app-settings.css` | — | 225ecc6e Bug reports: diagnostic info in Settings > About and for agents, strict |
| `src/window/chart/SymbolInfoDialog.tsx` | — | 8786ed6f A symbol's point value from data plugins: futures counted in contracts  |
| `src/window/chart/__tests__/chart-aggregate.test.ts` | — | 38e17eaf Weekly and monthly bars stamped at the first trading session (#170) |
| `src/window/chart/__tests__/countdown.test.ts` | — | 68d78420 Data plugins: the interval picker follows the intervals the plugin serv |
| `src/window/chart/__tests__/profile-poc.test.ts` | — | 68d78420 Data plugins: the interval picker follows the intervals the plugin serv |
| `src/window/chart/view/history.ts` | d678830a Sync followers load a window of bars around a far date | fc5c6d30 Sync followers load a window of bars around a far date (#310) |
| `src/window/chart/view/snapshot.ts` | 8cb9f031 Exports: saved to the downloads folder with a File downloaded toast; fast ISO chart data export | 8c74d26f Exports: saved to the downloads folder with a "File downloaded" toast o |
| `src/window/chart/view/symbol-load.ts` | — | af6564a2 The chart is given 60,000 bars around the view on a long history |
| `src/window/drawings/image-store.ts` | — | 94f20910 Drawing core import path: lightweight-charts-drawing/core |
| `src/window/header/AppSettingsDialog.tsx` | — | 1494bba4 Plugins: plugin host, Plugins settings tab and data plugins (#313, #314 |
| `src/window/header/app-settings/AboutTab.tsx` | — | 225ecc6e Bug reports: diagnostic info in Settings > About and for agents, strict |
| `src/window/right-rail/WatchlistDetail.tsx` | — | ea8edb6e Details panel follows a data plugin's live quotes (#356) |
| `src/window/right-rail/watchlist/cells.ts` | — | 37bf5dd1 Watchlist and details: prices on the symbol's tick size (#319) |
| `src/window/right-rail/watchlist/format.ts` | — | 37bf5dd1 Watchlist and details: prices on the symbol's tick size (#319) |
| `src/window/shell/app-tabs.ts` | fd7a040e Tick charts for data plugins with ticks; OakScript Logs panel start date and heavy script warning | 681172e4 Tick charts (1T, 10T, 100T, 1000T) for data plugins with ticks; MT5 plu |
| `src/window/shell/tabs.ts` | — | 2160c84d Panels per tab, synced crosshair legends, screener Maximize and loading |
| `src/window/strategy-tester/EquityChart.tsx` | 8cb9f031 Exports: saved to the downloads folder with a File downloaded toast; fast ISO chart data export | 8c74d26f Exports: saved to the downloads folder with a "File downloaded" toast o |
| `src/window/trading/AccountManager.tsx` | — | 7dd2790f Order ticket: quantity in money / % balance, Extra settings with time i |
| `src/window/trading/OrderPresets.tsx` | bb9c394b Trading: order price rules in the ticket and on dragged lines; stop limit one tick beyond its stop | 382d69b9 Trading: order price rules in the ticket and on dragged lines; stop lim |

### C — Intentional fork changes (8 files)

| File | Our commits touching it | Upstream origin (last commit) |
|---|---|---|
| `.env.example` | — | e97db812 Move data access to the gateway market API v2 |
| `.gitignore` | — | ba4d913b MetaTrader 5 plugin (#316) |
| `AGENTS.md` | 96fbbef1 OpenAlgo 0.1.18: a bodyless GET declares no body type, a tick pushes at once, a rate limit is retried | 3bf92e80 Language: every interface text in the catalog and translated, data left |
| `README.md` | — | 225ecc6e Bug reports: diagnostic info in Settings > About and for agents, strict |
| `package-lock.json` | 1e9b76f4 OpenTrader 0.4.29 version bump (drawing ref kept at v0.10.1 for the fork) | 1eff429a OpenTrader 0.4.29 |
| `package.json` | 1e9b76f4 OpenTrader 0.4.29 version bump (drawing ref kept at v0.10.1 for the fork) | 1eff429a OpenTrader 0.4.29 |
| `src-tauri/tauri.conf.json` | 1e9b76f4 OpenTrader 0.4.29 version bump (drawing ref kept at v0.10.1 for the fork) | 1eff429a OpenTrader 0.4.29 |
| `vite.config.ts` | — | 83c968ab Workers that fail to start: reported apart, started again, reload offer |

### D — Deletions (1 files)

| File | Our commits touching it | Upstream origin (last commit) |
|---|---|---|
| `src/window/right-rail/watchlist/__tests__/format.test.ts` | — | 37bf5dd1 Watchlist and details: prices on the symbol's tick size (#319) |

## Why the differences exist

- Your fork has a **different git root** and was synced in periodic batches, not as a normal fork. Many upstream features arrived under fork-port commit messages, so a naive subject/patch-id comparison calls them 'missing' even though they are present.
- Conflict policy is by **what the change does**, not commit age. Where our version is genuinely newer or fork-specific (tick charts, alerts, exports, point value, OpenAlgo code), we kept ours — the upstream commit is listed in the table for reference. Where upstream later enhanced or fixed something we had implemented locally, we took theirs and ported our extras forward (e.g. our `643ba2b7` DOM depth fix was replaced by upstream `a8a78a4b` and the plugin adapted to `broker.depth`). A superseded commit that conflicts everywhere is dropped only after verifying its functionality is already present.
- Fork-only files are the OpenAlgo plugin, the app-side plugin host, the OpenAlgo data provider, the binding fallback and the fork's own docs/tests/settings tabs.

## Our commits since the 0.4.28 base

- **51af2c05** Audit upstream parity: add missing features, drop superseded assistant files, refresh i18n (11 files)
- **73b241ad** Chart links: symbol from address, opentrader://chart, tab menu Copy link (6 files)
- **6398fd95** Plugin bar grid: a bar shared by two sessions counted once, new plugin info without a restart (1 files)
- **dea16a16** AI assistant panel: chat with an OpenAI-compatible model that runs the app's tools (4 files)
- **52b718a8** Quote change against previous close; watchlist upload reads files as the reference app (8 files)
- **2e92d1f3** Refresh i18n for far-date window and Replay broker entry points (1 files)
- **7b6bfc76** Replay Trading: broker entry points (14 files)
- **d678830a** Sync followers load a window of bars around a far date (4 files)
- **ce31815a** Refresh i18n catalog and dictionaries for all adopted upstream features (8 files)
- **221bbc0e** MT5 plugin 0.1.5, 0.1.6 and 0.1.7 in the catalog (1 files)
- **a0d11d4d** Symbol search: exchange-qualified matches; data plugin CFDs, futures and funds keep their type (7 files)
- **6d7c5b6e** Bar Replay: 1-tick updates on second, minute and hour charts of symbols with ticks (3 files)
- **9a6b64a7** Bar Replay on tick charts: the ticks after the replay time revealed one at a time (12 files)
- **fd7a040e** Tick charts for data plugins with ticks; OakScript Logs panel start date and heavy script warning (45 files)
- **f8cc5a8c** MT5 plugin 0.1.4: the order book in the DOM (4 files)
- **29bbfb30** IBKR plugin 0.1.4 in the catalog (2 files)
- **8cb9f031** Exports: saved to the downloads folder with a File downloaded toast; fast ISO chart data export (32 files)
- **b99d52a8** Legend: width modes on narrow charts (9 files)
- **6af2f866** Studies legend: Object tree order, buttons on the title's line, values on one line (6 files)
- **1e9b76f4** OpenTrader 0.4.29 version bump (drawing ref kept at v0.10.1 for the fork) (5 files)
- **1085fca3** OpenAlgo 0.1.27: send broker.depth snapshots on upstream's protocol (6 files)
- **f258635b** Adapt OpenAlgo plugin to upstream's broker.depth protocol (16 files)
- **b7362fbe** DOM: price step grouping and the Show prices settings hide rows without size (12 files)
- **451d8a07** DOM: the broker's order book in the bid / ask size cells, their meters and the totals (20 files)
- **58cb0786** Data plugins: restored charts and watchlists wait for a plugin still starting (4 files)
- **bb9c394b** Trading: order price rules in the ticket and on dragged lines; stop limit one tick beyond its stop (23 files)
- **373db8ae** Trading lines: order lines drag with a broker plugin, project order dragged by its label (3 files)
- **84c88327** Rename fixed upstream issue drafts with fixed_ prefix (7 files)
- **643ba2b7** Show the broker's five-level book in the DOM ladder (14 files)
- **c348c6f2** OpenAlgo 0.1.25: subscribe to the five-level depth stream (real bid / ask) (6 files)
- **a0b7fbc7** OpenAlgo 0.1.24: close a single position instead of all of them (2 files)
- **edf01da7** OpenAlgo 0.1.23: map stop orders to OpenAlgo's SL / SL-M pricetypes (2 files)
- **b99ab708** OpenAlgo 0.1.22: drop the unsupported `validity` field from place/modify order (3 files)
- **44a1842f** OpenAlgo: TODO for configurable pollInterval (Option B) (1 files)
- **c2ec7b4f** OpenAlgo 0.1.21: per-category file logging an AI agent can read (2 files)
- **658b6b1e** Revert "Shorten the frontend TickerInfo cache from 60 minutes to 5" (1 files)
- **7b37856e** Shorten the frontend TickerInfo cache from 60 minutes to 5 (1 files)
- **82351064** OpenAlgo 0.1.20: Symbol Info "Type" for options (CE/PE) (2 files)
- **d70f420c** OpenAlgo 0.1.19: day charts come from the D endpoint, and history waits out a cold call (4 files)
- **96fbbef1** OpenAlgo 0.1.18: a bodyless GET declares no body type, a tick pushes at once, a rate limit is retried (12 files)


## Work streams — what we added and why

### 1. OpenAlgo plugin (fork-only, `plugins/openalgo/**`, v0.1.18 → v0.1.27)
Added the whole broker plugin: `plugin.py`, manifest, `build.py`, a fake deployment and a 44-test regression suite
(`tests/`). Why: the fork trades through a self-hosted OpenAlgo deployment. Each release added a live-verified fix:
- **0.1.18** — honest fake server, tick push, rate-limit retry, bodyless-GET fix.
- **0.1.19** — day charts from the `D` endpoint; 55 s history timeout for the deployment's cold first call.
- **0.1.20–0.1.23** — option CE/PE "Type", stop `SL`/`SL-M` mapping, drop unsupported `validity`, close one position.
- **0.1.21** — per-category file logs (`openalgo-api.log`, `openalgo-ws.log`) so history fan-out is observable.
- **0.1.25** — subscribe mode 3 with `depth: 5`; real bid/ask from the book instead of a tick-size spread.
- **0.1.26** — forward the full five-level book.
- **0.1.27** — send `broker.depth` snapshots on upstream's protocol (separate from `broker.quote`).

### 2. Broker depth / DOM ladder (`src-tauri/src/plugins/broker.rs`, `src/data/trading/*`, `src/window/trading/*`)
Why: the DOM tab showed prices but no quantities. We first implemented a fork-local `depth` field inside
`broker.quote` (`643ba2b7`), then **replaced it with upstream's canonical protocol** (`451d8a07`, `f258635b`):
- Rust `BrokerDepth { ticker, snapshot, bids, asks }`, `DepthLevel`, `broker.depth` event, `broker_watch_depth` command.
- Frontend `depth-book.ts` (apply snapshot/delta), `pluginBroker.depth()` / `watchDepth()`.
- `DomPanel.tsx` + `dom-ladder.ts` render sizes, meters, totals; `b7362fbe` added price-step grouping and
  "Show prices" settings.
- `plugins/PROTOCOL.md` documents the optional book for other plugins.
- `src/bindings-fallback.ts` / `src/bindings.ts` were maintained by hand (upstream generates them; no Rust toolchain here).

### 3. Upstream sync 0.4.28 → 0.4.29 + all remaining commits
Why: keep the fork on current upstream while keeping our extras. Adopted by cherry-pick (PR refs stripped per AGENTS.md):
- #370 order-line drag + ticket price rules, #371 restored charts wait for a starting plugin (plus its follow-up: settings before subscriptions, watchlists of a plugin starting with the app),
- #367/#368 studies legend, #369 downloads-folder exports, #372/#374 DOM book and grouping,
- #375–#378 MT5/IBKR catalog, tick charts, tick-based Bar Replay, #331 symbol search,
- #379 Plugin API 3 (password settings, conditional settings, plugin data folder, prompts) and #380 per-capability plugin on/off switches; IBKR 0.3.0.
- Plugin API 4 (#381): data plugins send ready-made watchlists (our OpenAlgo plugin still speaks API 3; older-version plugins keep working).
- #384: broker plugins add their own account-manager tabs (e.g. Holdings); #382/#383/#385/#386/#387 fixed earlier; Plugin API 5/6 for events, extra series, watchlists.
- plus earlier pre-base commits that were genuinely missing: #310 far-date window, #156 Replay broker buttons,
  #325 OakScript Logs start date, #340 plugin bar grid test, #350 chart links.
Where our version was newer (point value #345, tick-size watchlist #319, plugin intervals #318, alerts, etc.),
we kept ours — those files still differ from upstream tip but contain the same feature in a newer form.

### 4. Version / packaging
Why: `1e9b76f4` bumped `package.json`, `package-lock.json`, `Cargo.toml`, `Cargo.lock`, `tauri.conf.json` to 0.4.29
so the built exe reports the right version and the updater stops offering an older upstream release. `drawingRef`
was first left at `v0.10.1` to match the sibling checkout, then updated to `v0.11.1` once that checkout was moved to
the same tag and the whole suite was re-run green — the rule now is to bump library versions together with upstream
syncs, not leave them behind.

### 5. i18n (`src/i18n/**`)
Why: every new UI text must be in the catalog and all six dictionaries (tests enforce it). The catalog was
regenerated with `npm run i18n:extract`, dictionaries merged from upstream plus the fork's own translated texts,
then pruned with `npm run i18n:prune`. Files like `AboutTab.tsx` also carry intentional fork text changes
(sponsor removal).

### 6. Fork-only app code
Why: `src/data/providers/openalgo.ts`, `src-tauri/crates/core/src/data/provider/openalgo.rs`,
`src/data/broker-credentials.ts`, `src/data/sources/**`, `src/bindings-fallback.ts`, the DataTab settings page,
web-build helpers (`*.bat`, `deploy-web.yml`), and fork docs/tests are all ours — upstream has no equivalent.

### 7. Issue drafts (`issues/**`)
Why: record upstream-worthy findings. `fixed_*` = confirmed fixed upstream (with commit/PR noted in the file);
`hidden-studies-still-fetch-bars.md` and `watchlist-mini-chart-history-fanout.md` are still open upstream drafts.
`openalgo-plugin-changes-0.1.1-0.1.8.md` documents the live protocol facts the plugin relies on.

## Verification status
- `npx tsc --noEmit -p tsconfig.json` — clean
- `npx vitest run` — 201 files / 2164 tests passed
- `python plugins/openalgo/tests/run_all.py` — 44 tests passed
- Live GOLD probe: `broker.depth` snapshot with 5 real bid/ask levels and quantities
- Rust compile not run locally (no `cargo` in this shell); CI compiles on push
