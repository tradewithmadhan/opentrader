# Bug report — for filing on deepentropy/opentrader

> Drafted 22/11/2026. Paste everything below the line into the issue body.

Title of the issue: **Switching the watchlist changes it in every tab**

Paste everything below the line into the issue body.

---

### What happened

With more than one chart tab open, switching the watchlist in one tab changes the watchlist shown in **all** the other tabs too. Each tab cannot keep its own watchlist selection.

### What I expected

Each tab should remember its own watchlist choice. Switching the watchlist in one tab should not change it in the other tabs.

### Steps to reproduce

1. Open the app with at least two chart tabs.
2. In the first tab, show watchlist "A".
3. In the second tab, show watchlist "B".
4. Switch back to the first tab and choose watchlist "C".
5. Look at the second tab: it now shows "C" instead of "B".

### How often

Every time.

### Area of the app

Watchlist / tabs.

### Additional context

Verified in the source: `src/data/watchlist-store.ts` keeps a single global `activeId` (the shown watchlist) in the store, and every consumer (`RightRail`, the chart context menu, `OpenListDialog`, the quote subscription in `data/quotes.ts`) reads that one value with no per-tab key. So a switch from any tab updates the single shared value and every tab follows. Making it per-tab would require keying the active watchlist by tab id (as charts/layouts already are).
