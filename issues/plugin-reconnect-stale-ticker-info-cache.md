# Comment for issue #371 — deepentropy/opentrader

> Drafted 21/11/2026 as a follow-up comment on
> https://github.com/deepentropy/opentrader/issues/371 (still Open). Paste everything below the line into the comment box.

Paste everything below the line into the comment box.

---

One more case in the same family, still present after 5565d13 and 4d2fe69 (I'm on the latest build).

**Symptom.** After a data plugin reconnects — or while its settings/API key are being entered after install — the Symbol Info dialog shows no fields for symbols that were opened during that window (no Type row, no other info). Symbols opened once the plugin is healthy work normally. Restarting the app clears it.

**Cause.** This time it's the symbol-info cache, not the pending-request retry or the subscribe order you already fixed. `src/data/datafeed-rest.ts` keeps a failed `getTickerInfo` for a full hour when the error is a "not found":

```ts
const INFO_TTL_MS = 60 * 60_000;
...
if (isNotFoundError(e)) entry.at = Date.now();   // line 113: cached for another hour
```

On `events.pluginsChanged`, `App.tsx` calls `forgetUnknownSymbols()`, but that only clears the `unknown` set in `unknown-symbols.ts`; it has no access to `datafeed-rest.ts`'s private `infoCache`. So a plugin that was briefly unable to answer `data.symbolInfo` (cold master before the key was entered, plugin still starting) leaves a poisoned "no info" entry for an hour, even though the plugin is now answering correctly.

In my case the plugin's live `/symbol` fallback does return a valid row with the correct `type` (stock / index / futures / option) — verified live — so the stale client cache is the cause, not the data source.

**Suggested fix**, small and in the same spirit as `forgetUnknownSymbols()`:

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

A test can assert that after `clearTickerInfo()` a second `getTickerInfo(symbol)` re-invokes the command instead of returning the cached miss. Optionally clear `snapshotCache` there too for the same reason.

Happy to confirm with OpenAlgo once it ships, as with the earlier two fixes.
