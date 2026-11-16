# Upstream proposal - for filing on deepentropy/opentrader

**Filed (as part of deepentropy/opentrader#341) and fixed upstream in `285a391c` — adopted in the
fork's `6b3727cc`, plugin 0.1.9.** The design that landed differs from this draft: no `product`
field on `OrderTicket`, but a generic `capabilities.ticketFields` (the values travel in
`fields`), and the lot size moved out of `pointValue` into `broker.instrument`'s `qtyStep`.

**Follow-up #345 (`8786ed6f`, adopted in the fork's `9f3be6d1`):** the reference app counts
futures in **contracts** — one NIFTY lot = qty 1 at pointValue 65. The plugin therefore sends
`data.symbolInfo.pointValue = lotsize`, `broker.instrument = {"qtyMin": 1, "qtyStep": 1}` (pointValue
omitted, taken from the symbol), multiplies qty by the lot on `place`/`modify`, and divides the
broker's quantities by the lot on positions/orders/fills. Both counting ways stay legal as long as
orders, positions and pointValue agree. Plugin 0.1.10 implements this.

Title of the issue: **Order ticket: broker product selection (MIS / CNC / NRML) per order**

Fork reference: `tradewithmadhan/opentrader`, branch `sample-web-clean`. The OpenAlgo plugin
(`plugins/openalgo`, Plugin API 2 - data + trading over one REST + WebSocket gateway) is built and
live-tested against a self-hosted deployment; trading works end to end. This is the one gap the
ticket itself leaves.

Paste everything below the line into the issue body.

---

Indian brokers route every order through a **product type** - intraday, delivery, or carry-forward.
OpenAlgo's mapping:

| Product | Meaning | Square-off |
|---|---|---|
| MIS | Margin Intraday Settlement | auto-squared off ~15:15 IST |
| CNC | Cash & Carry (delivery) | no auto square-off |
| NRML | Normal / carry-forward (F&O) | no auto square-off |

## Current behavior

The ticket sends only ticker / side / type / qty / prices (`OrderTicket` in
`crates/core/src/plugins/broker.rs`) - there is no product field, and the ticket UI has no product
control. A broker plugin with products can only expose a **plugin-level default** in Settings (the
OpenAlgo plugin has a "Default product" select). Changing it mid-session works, but it is global:
you cannot be intraday in one symbol and take delivery in another without opening Settings between
orders.

## What happens in practice

At 15:44 IST I tried to buy RELIANCE and got `MIS orders cannot be placed after square-off time
(15:15 IST)` - the fix is to send the same order as CNC, but there is no way to say that from the
ticket. I have to leave the chart, open Settings, flip the plugin's default product, come back,
place - and remember to flip it back for the next intraday order.

## Proposal

1. **API**: an optional `product` field on `OrderTicket` (camelCase `product`), passed through to
   `broker.place` / `broker.modify`. Plugins without product types ignore it.
   `BrokerState.capabilities` advertises the accepted values (e.g. `"products": ["MIS", "CNC",
   "NRML"]`) so the UI only shows the selector when the broker has one, and knows the default.
2. **Ticket UI**: a small selector next to the order-type picker (Market / Limit / Stop),
   defaulting to the plugin's declared default, listing the advertised values.

This keeps the plugin API backward compatible (an optional field) and makes brokers with product
semantics first-class in the ticket, the same way order types already are.

Happy to test a build against a live OpenAlgo deployment.
