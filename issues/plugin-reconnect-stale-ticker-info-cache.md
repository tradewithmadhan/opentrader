# Bug report — for filing on deepentropy/opentrader

> Drafted 21/11/2026. Confirmed present in upstream/main (0.4.30). Paste everything below the line into the issue body.

Title of the issue: **Plugin reconnect leaves stale "symbol not found" ticker info cached for an hour**

Paste everything below the line into the issue body.

---

### What happens

When a data plugin briefly cannot answer `data.symbolInfo` for a symbol — while it is still starting, before its credentials/settings are entered, or during a cold instrument-master load — the app can cache that "not found" answer for a full hour. After the plugin becomes healthy, the Symbol Info dialog (and anything else reading `getTickerInfo`) keeps showing no data for those symbols (no Type row, no other fields) until the hour expires or the app is restarted. Symbols first opened after the plugin is healthy work normally.

This is easy to hit during plugin setup/upgrade: install a plugin, open a few symbols before entering the API key (or while its master data is still loading), then enter the key — those first symbols stay broken for an hour while newly opened ones are fine.

### Root cause

Two separate caches remember "symbol doesn't exist", but only one is invalidated when a plugin reconnects:

- `src/data/unknown-symbols.ts` → the `unknown` set — cleared by `forgetUnknownSymbols()` from the `pluginsChanged` listener in `App.tsx`.
- `src/data/datafeed-rest.ts` → the `infoCache` map — **not cleared on plugin change**. A failed `getTickerInfo` whose error matches `NOT_FOUND` is deliberately kept for the full `INFO_TTL_MS`:

  ```ts
  const INFO_TTL_MS = 60 * 60_000;
  ...
  p.then(
    () => { entry.at = Date.now(); },
    (e) => {
      if (isNotFoundError(e)) entry.at = Date.now(); // kept for another hour
      else if (infoCache.get(symbol) === entry) infoCache.delete(symbol);
      noteSymbolError(symbol, e);
    },
  );
  ```

`forgetUnknownSymbols()` clears only the `unknown` signal in `unknown-symbols.ts`; it has no access to `datafeed-rest.ts`'s private `infoCache`. So after a plugin reconnect, the symbol is no longer "unknown" but its cached failed info promise is still served.

### Why it matters

- A data plugin that connects after the window (or whose settings are completed after install) leaves a stale, final-looking "no info" for every symbol touched during that window.
- The Symbol Info dialog silently omits fields; users read it as a broken symbol / bad data.
- It contradicts the intent already documented in `App.tsx`: "a plugin that connects again may know its symbols again … the sessions are read again and the charts reload when one changed" — symbol info is the one thing not refreshed.

### Suggested fix

Export a cache clear from `src/data/datafeed-rest.ts` and call it in the `pluginsChanged` listener:

```ts
// datafeed-rest.ts
export function clearTickerInfo(): void { infoCache.clear(); }
```
```ts
// App.tsx, events.pluginsChanged.listen(...)
forgetUnknownSymbols();
clearTickerInfo();       // add
wakeRetries();
void refreshPluginSessions().then((changed) => changed && emitAppEvent("chart-reload-data"));
```

Optionally reset `snapshotCache` in the same place for the same reason. A test can assert that after `clearTickerInfo()`, a second `getTickerInfo(symbol)` re-invokes the command rather than returning the cached "not found".

### Additional context

- Observed with a broker plugin installed via Settings > Plugins on 0.4.30; the first symbols opened before the API key was entered stayed blank in the Symbol Info dialog for ~1 hour, while symbols opened afterwards were correct.
- The plugin itself returns a correct `type` for every symbol once configured (verified live: stock / index / futures / option), so the stale client cache is the cause, not the data source.
- Related: #371 (restored charts/watchlists wait for a starting plugin) fixed the retry path for bars/quotes; this is the sibling gap for symbol info.
