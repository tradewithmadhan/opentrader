# Upstream proposal — for filing on deepentropy/opentrader

> Status: **FIXED upstream** in `285a391c` (#341). No need to re-file.
`6b3727cc` and the OpenAlgo plugin in 0.1.9 (`ba00c2bb`): the ticket's product field, lot sizing via
`broker.instrument` (pointValue now per unit, the lot as qtyStep), `broker.getState`, and -32004 for
unknown symbols.

Title of the issue: **Broker plugins: the host gaps an Indian-broker plugin hits (ticket product, lot sizing, broker-state contract, stale watchlist symbols)**

Fork reference: `tradewithmadhan/opentrader`, branch `sample-web-clean`. The OpenAlgo plugin
(`plugins/openalgo`, Plugin API 2 - data + trading over one REST + WebSocket gateway) is built and
live-tested against a self-hosted deployment: realtime ticks, charts and order placement all work.
Getting there surfaced four host-side gaps. Each one forced a plugin-side workaround; all four are
better fixed in the host once than worked around in every broker plugin. Full background in the
fork's `issues/openalgo-plugin-changes-0.1.1-0.1.8.md`.

Paste everything below the line into the issue body.

---

## 1. The order ticket has no product type (MIS / CNC / NRML)

Indian brokers route every order through a **product type**:

| Product | Meaning | Square-off |
|---|---|---|
| MIS | Margin Intraday Settlement | auto-squared off ~15:15 IST |
| CNC | Cash & Carry (delivery) | no auto square-off |
| NRML | Normal / carry-forward (F&O) | no auto square-off |

**Why it matters.** At 15:44 IST I tried to buy RELIANCE and got `MIS orders cannot be placed after
square-off time (15:15 IST)`. The fix is to send the same order as CNC — but there is no way to say
that from the ticket. The plugin can only expose a **global default** in Settings (the OpenAlgo
plugin has a "Default product" select); being intraday in one symbol and taking delivery in another
means opening Settings between orders and remembering to flip back.

**What's needed.**
- An optional `product` field on `OrderTicket` (crates/core/src/plugins/broker.rs), passed through
  to `broker.place` / `broker.modify`. Plugins without product types ignore it — backward
  compatible.
- `BrokerState.capabilities` advertises the accepted values and the default (e.g.
  `"products": ["MIS", "CNC", "NRML"], "defaultProduct": "MIS"`), so the UI shows the selector only
  when the broker has one.
- A selector in the ticket next to the order-type picker (Market / Limit / Stop).

## 2. The order ticket has no lot sizing

Indian contracts are sized in **lots** (NIFTY futures 65, an equity 1) — the instrument master
carries the lot size, and the ticket's economics already know it: the plugin serves it as
`pointValue` (adopted upstream; the ticket's risk sizing multiplies by it). But the **quantity**
input is unitless: it steps by ±1 and sends the raw number, so qty `1` reached the broker for a
lot-65 future and came back as `Quantity must be in multiples of lot size 65`.

**What's needed.**
- The qty input steps and validates by the symbol's lot size (the `pointValue` already in the
  ticket's context): arrow keys move one lot, and a non-multiple is flagged before the order goes
  out ("65 is one lot — use 65 or 130").
- The risk-based sizing (`riskUsd` / `riskPct`) already computes units through pointValue — the same
  conversion should round to whole lots there too.

Until then the plugin pre-rejects wrong quantities locally with the next valid lot ("try 65 or
more") — a workaround the host can retire.

## 3. The broker-state contract leaves a working plugin "not connected"

The host treats the **last pushed `broker.state`** as the truth and never re-asks for it. The state
a plugin pushes at connect carries `connected: false` — no book has been read yet — and a plugin
that afterwards only pushes when something changes (say, open positions exist, to move the P&L)
leaves an account **without open positions permanently "Broker not connected"**. The ticket refuses
to trade a fully working plugin; nothing in the plugin log hints at the cause. This is exactly the
trap the plugin template's pattern lays: state on connect, then on activity.

**What's needed** (either suffices; both is best):
- The host asks the plugin for its state at the moments the UI consumes it — on the broker
  connecting and when the Trade panel / ticket opens (a `broker.getState` request, or re-requesting
  `broker.state`).
- Or the plugin API docs and the template plugin encode the working contract explicitly: push
  `broker.state` at least on connect, on every connected-flag change, and periodically.

## 4. Stale watchlist symbols warn and error on every retry, all session

A watchlist entry that no longer exists — an expired options contract, a renamed symbol — makes
every `data.symbolInfo` for it warn and error, again on every retry, for the whole session. A
watchlist with a few dead entries (any options trader after expiry day) fills the log:

```
15:44:29 [warn]  symbol NFO:NIFTY09NOV2624450CE: OpenAlgo: Symbol ... not found in exchange NFO
15:44:29 [error] data.symbolInfo failed: Unknown symbol NFO:NIFTY09NOV2624450CE
```

**What's needed.**
- The host remembers an "unknown symbol" miss per session and stops hammering it (rate-limit or
  one-shot the error).
- Optionally surface it once to the user — "these watchlist entries no longer exist" with a
  cleanup action — instead of a stream of errors that look like a data problem.

---

All four are host-side; the OpenAlgo plugin works around each today and drops the workarounds as
soon as the host has the feature. Happy to test a build against a live OpenAlgo deployment.
