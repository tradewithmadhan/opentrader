# Feature request — for filing on deepentropy/opentrader

> **FIXED upstream** in `cf8580a1 / 20691763` (issue #383), adopted in this fork. Kept for reference.


> Drafted 21/11/2026. Paste everything below the line into the issue body.

Title of the issue: **Let data plugins provide custom data series (open interest, custom symbol info, extra OHLCV-like series)**

Paste everything below the line into the issue body.

---

### What I'm trying to do

Let a data plugin provide extra data beyond price bars — things like **open interest**, **price bands**, richer **symbol-info content**, or additional **OHLCV-like series** — and make that data available to the app, charts and custom indicators.

A broker or data source often exposes more than plain OHLCV: Indian brokers stream open interest and circuit/price bands; some sources supply their own symbol metadata and extra time series. Today a plugin can only serve bars, quotes, depth, symbol info and account data — there is no way to hand arbitrary named data to the app, so none of it can reach a chart or a script.

### What happens today

- The plugin protocol (`plugins/PROTOCOL.md`) has no method for custom data. A plugin can serve `data.bars`, `data.symbolInfo`, `data.quote`, `data.subscribe`, `data.ticks`, `broker.*` — nothing generic.
- A custom OakScript study can read only OHLCV bars, other symbols' bars (`request.security` / `requiredSymbols`), and a fixed set of `syminfo.*` fields. There is no `syminfo.openInterest`, no price-band fields, and no reader for a plugin-provided series.
- `request.seed` reads a `LOCAL:<name>` series from a CSV file the user imports — static, not live from a plugin.

The only workaround today is to fake the extra data as a **synthetic symbol** (serve it as another symbol's bars and read it with `request.security`). That forces the data into an OHLCV shape, clutters symbol search, and is awkward for a per-bar numeric field like open interest.

### What I'd like

An official way for a data plugin to supply custom data that the app can use, for example:

- **Open interest** and similar per-bar numeric series, aligned to the chart's bars, readable from a custom indicator.
- **Price bands / circuit limits** (upper and lower) as symbol data.
- **Richer symbol-info content** — extra fields a plugin wants to show or expose that the fixed `data.symbolInfo` shape cannot carry.
- **Additional OHLCV-like series** (a second timeframe, an adjusted series, an instrument's own richer bars) from the same plugin.

I am not prescribing the exact API or wire format — that is for the maintainers to design. The request is for the *capability*: plugin-provided custom data should be reachable by the app and by custom indicators, the way bars and `syminfo` are today.

### Why it matters

- OpenAlgo and other Indian brokers already expose open interest and price bands live; plugins can fetch them but cannot get them into a study.
- It would let custom indicators compute on the same extra data professional platforms expose (OI build-up, price-band proximity, breadth, etc.) without fake symbols.
- It keeps the plugin as the single place a source is integrated — no separate CSV import or duplicate data path.
- It is a natural extension of the plugin system: plugins already provide market data; this widens *what kind* of data they can provide.

### Additional context

- Observed with OpenTrader 0.4.30 / a broker plugin, but this is a general plugin-API and script-runtime gap, not specific to one plugin.
- Sibling request: #381 (watchlist data from plugins) — likewise about widening what a plugin can provide.
- Existing primitives that could inform a design (maintainers' call): `request.security` / `requiredSymbols` for loading a symbol's series into a study, `request.seed` for a named external series, and the plugin `data.*` methods for serving symbol data. I am not prescribing which to use — only asking that the capability exist.
