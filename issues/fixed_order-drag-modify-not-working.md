# Bug report — for filing on tradewithmadhan/opentrader (or deepentropy/opentrader)

> Status: **FIXED upstream** in `737663cc` (#370, "Trading lines: order lines drag with a broker plugin, project order dragged by its label"). No need to re-file.

Title of the issue: **Order price lines on the chart: dragging doesn't modify the order, and new-order price lines can't be dragged at all**

Fork reference: `tradewithmadhan/opentrader`, branch `sample-web-clean`. Observed with the OpenAlgo plugin (0.1.24, app 0.4.28). Verified via the plugin's per-category log (`openalgo-api.log`): zero `broker.modify` entries even after dragging an order line. The plugin's `modify` method works when called directly (returns "modified" event, limit price updated on OpenAlgo).

Paste everything below the line into the issue body.

---

## Issue 1: Dragging an existing order line moves it visually but does not modify the order on the broker

### What happens

1. Place a limit order (e.g. on MCX gold).
2. Drag the order line on the chart to a new price.
3. The line moves visually (the chart updates the display during drag).
4. On drag end, the order is **not** modified on the broker — the broker's order stays at the old price.
5. No `broker.modify` call appears in the plugin log.

### Expected behaviour

Dragging an order line to a new price should call `broker.modify` with the new price, updating the order on the broker.

### Where the drag is handled

`src/window/chart/view/trading-lines.tsx`:
- `onChipDown` (line 245): checks `canMove(caps(), o)` then calls `track(...)` with `onMove` (updates `setDrag` for visual feedback) and `onUp` callback.
- `onUp` (line 255): if `moved` and `d.price !== start`, calls `commit(o, d.price, limit)` → `moveOrder` → `broker.modify`.
- `track` (line 221): `moved` is set to `true` only when `Math.abs(ev.clientY - y0) >= 3`.

### Possible causes

1. **3-pixel threshold too small**: the drag may register as a click instead of a move if the mouse movement is small. The threshold of 3 px means tiny drags open the ticket instead of modifying.
2. **`canMove` returns false**: if capabilities haven't loaded yet (plugin just connected), `c.modifyOrders` might be undefined/falsy, causing `onChipDown` to return early (line 250) — no drag, no modify.
3. **`broker.modify` not reaching the plugin**: the app calls `commands.brokerModify` but it might not be routed to the plugin (e.g. trading not enabled, or the order isn't found in the broker's local state).
4. **Optimistic UI update**: the chart updates the line position during drag (via `setDrag`) but doesn't roll back if `broker.modify` fails. The user sees the new price but the broker order is unchanged.

### Additional context

- The plugin's `modify` method works when called directly (verified live): place limit at 100000 → modify to 100001 → price updated on OpenAlgo, "modified" event returned.
- The plugin declares `"modifyOrders": True` in capabilities.
- Trading is enabled (`trading_enabled: true` in plugins.json).
- No `broker.modify` entries appear in the plugin log, confirming the call never reaches the plugin.

---

## Issue 2: Cannot drag the price line to adjust price when placing a new order from the chart

### What happens

1. Initiate a new order from the chart (one-click order or "Place order" from the context menu).
2. A price line appears on the chart at the current price.
3. The user tries to drag the price line to a different price before confirming.
4. **The line cannot be dragged** — the price is fixed at the initial value.
5. The user must open the order ticket / order panel to change the price manually.

### Expected behaviour

When placing a new order from the chart, the price line should be draggable so the user can adjust the entry price before confirming, the same way an existing order's line can be dragged to modify it.

### Where the drag is handled

`src/window/chart/view/trading-lines.tsx`:
- `onChipDown` (line 245): handles drag on order/bracket chips. Checks `canMove(caps(), o)` and returns early if false.
- `track` (line 221): pointer tracking for drag movement.
- `commit` (line 217): calls `moveOrder` → `broker.modify` (only for existing orders, not new ones).

### Possible causes

1. **No broker order to modify**: a new (unplaced) order has no `orderId` on the broker, so `broker.modify` can't be called. The price adjustment must update the local ticket draft, not the broker.
2. **Missing drag handler for pending orders**: the price line for a pending order might not have a drag handler wired up — it's rendered as a static line, not a draggable chip.
3. **`canMove` returns false for pending orders**: pending orders (kind !== "order") might not pass the `canMove` check, so no drag is allowed.

### Suggested fix

- For pending/new orders, dragging the price line should update the local order draft's price (`limitPrice`/`stopPrice`) instead of calling `broker.modify`.
- Alternatively, allow the ticket's price field to be editable inline from the chart (click the price label to type a new value).

### Additional context

- Existing orders can be dragged to modify (when `canMove` is true and `broker.modify` works).
- The issue is specifically for **new** orders being placed from the chart. The order panel works fine for price entry.
- A broker plugin's capabilities (`modifyOrders`) control whether drag is allowed for existing orders, but there's no equivalent for pending orders.

---

### Suggested implementation area (both issues)

- `src/window/chart/view/trading-lines.tsx` — `onChipDown`, `track`, `commit`, `moveOrder` call chain.
- `src/data/trading/order-actions.ts` — `moveOrder` (only handles existing orders via `broker.modify`).
- `src/data/trading/capabilities.ts` — `canMove` (gates drag on `modifyOrders`).
- `src/data/trading/plugin-broker.ts` — `modify` (calls `commands.brokerModify` → plugin).