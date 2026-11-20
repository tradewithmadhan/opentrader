# Feature request — for filing on tradewithmadhan/opentrader (or deepentropy/opentrader)

> Status: **FIXED upstream** in `a8a78a4b` (#372, "DOM: the broker's order book in the bid / ask size cells, their meters and the totals"). Adopted in this fork via cherry-pick; the OpenAlgo plugin now sends `broker.depth` snapshots (upstream's protocol).

Title of the issue: **Broker plugin depth: the order ticket's DOM ladder should show the broker's real book (bid/ask sizes), not an empty size column**

Fork reference: `tradewithmadhan/opentrader`, branch `sample-web-clean`. Observed with the OpenAlgo plugin (0.1.26, app 0.4.28). The plugin already streams a five-level depth book (mode 3) and sends `broker.quote` with the best bid/ask, but the app's ladder only uses those two prices: the "Bid size" / "Ask size" cells stay empty and both totals read "-".

Paste everything below the line into the issue body.

---

### What I'm trying to do

Show the broker's resting quantity at each price in the order ticket's DOM tab, and the total buy / sell quantity of the book, instead of two empty size columns and "-" totals. The ladder is the one place in the UI that is supposed to show depth, and today it shows none of it.

### What happens

- The DOM (`src/window/trading/DomPanel.tsx`) renders `Bid size` / `Ask size` columns, but the cell is just a meter bar — no text. `const TOTAL = "-"` is hard-coded for both totals.
- `DomMarket` only carries `bid` / `ask` / `last` (as price indexes); there is no size.
- `TradingQuote` (`src/data/trading/types.ts`) has `bid`, `ask`, `last`, `minTick` — no `depth`.
- `BrokerQuote` (`src-tauri/src/plugins/broker.rs`) has `bid`, `ask`, `last` — no `depth`.
- `plugin-broker.ts` stores `{ bid, ask, last }` per ticker and maps to `TradingQuote` without any book.

So even a plugin that streams a real five-level book cannot get a quantity onto the ladder. The best it can do is the best bid/ask *prices*, which the ladder already shows as coloured rows.

### How it should work

1. `BrokerQuote` gains an optional `depth: { buy: [{price, qty}], sell: [{price, qty}] }` (best level first), with `#[serde(default)]` so older plugins keep working.
2. The frontend `TradingQuote` gains the same `depth` field; the replay broker fills `depth: null`.
3. `DomPanel` maps each book level to its price row, shows `fmtQty(qty)` in the size cell, and sums the levels into the two totals (null book → "-" as today).
4. `plugins/PROTOCOL.md` documents the optional field so other broker plugins can adopt it.

### Why this matters

- The DOM tab is the only screen built around depth, and it currently displays none of it. A broker that streams a real book (OpenAlgo mode 3, IBKR market depth, etc.) is indistinguishable from a broker that streams only quotes.
- The "zero volume" / "spread" display toggles (`ot:order-ticket:dom-prices`) already exist in settings; they are waiting for real sizes to act on.

### Suggested implementation area

- `src-tauri/src/plugins/broker.rs` — `BrokerQuote` (+ `BrokerQuoteLevel`, `BrokerDepth`), `#[serde(default)]`.
- `src/data/trading/types.ts` — `TradingQuote.depth`, `BookLevel`, `Book`.
- `src/data/trading/plugin-broker.ts` — store and pass through `depth`.
- `src/data/trading/replay-broker.ts` — `depth: null`.
- `src/window/trading/dom-ladder.ts` — `bookRows`, `bookTotal`.
- `src/window/trading/DomPanel.tsx` — render sizes + totals.
- `src/styles/trading-dom.css` — `.trd-dom-size`.
- `plugins/PROTOCOL.md` — optional `depth` on `broker.quote`.

### Additional context

- The fork's implementation is committed in `643ba2b7` ("Show the broker's five-level book in the DOM ladder") and live-verified against OpenAlgo: the five buy/sell levels reached `broker.quote.depth` with quantities.
- Plugin-side companion: OpenAlgo plugin 0.1.26 sends the book from WebSocket mode 3 and the REST fallback.
