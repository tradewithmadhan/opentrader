# Upstream proposal — for filing on deepentropy/opentrader

Title of the issue: **Plugins: let a data plugin serve more than one exchange**

Paste everything below the line into the issue body.

---

### The shape of the use case: Indian brokers

An Indian broker account is not one market — it is **several exchange segments under one account and one login**: cash (`NSE:`, `BSE:`), F&O (`NFO:`, `BFO:`), commodity (`MCX:`), currency (`CDS:`, `BCD:`), and newer commodity segments on both exchanges (`NCO:`, `BCO:`). Every Indian broker works this way; there is no account that is "NSE only".

Gateways that front these brokers inherit the shape. [OpenAlgo](https://openalgo.in) — an open-source, self-hosted gateway that puts one REST + WebSocket API in front of many Indian brokers — is the example I'm building a plugin for, but the constraint it hits is generic: **any** broker or gateway plugin for this market would serve several exchanges from one account.

I want that plugin to serve both halves: the account's bars and live prices on the charts, and its trading from the charts. The broker half maps onto the plugin protocol cleanly today (`broker.resolveSymbol` handles any exchange). The data half doesn't — a data plugin can name only one `exchange`, so one account can't serve its symbols under their own names.

### The problem

A plugin's manifest names a single `exchange`, so every symbol a data plugin serves is `<exchange>:<ticker>` under that one prefix. That fits a source that owns one exchange, but not one that serves several segments of one account:

- It can only claim one exchange, so the rest of its symbols can't be served under their own names.
- One package per exchange means N installs, N enabled plugins and N settings forms for one account and one connection.

### Proposed design (additive)

1. **`exchanges: Vec<String>`** in the manifest, optional. When present, the plugin serves all listed exchanges; when absent, the current single `exchange` applies. Nothing about existing manifests changes.
2. **Route map** (`plugins::apply`): insert one entry per exchange instead of one per plugin. The map type stays `HashMap<exchange, plugin id>`, so `data::route`, `brokerResolveSymbol`'s data-route check and everything downstream are untouched.
3. **Validation**: each entry checked the way `exchange` is today (2–16 uppercase letters/digits/`_`, not reserved).

### Optional follow-up: narrow by settings

A plugin setting (a list of exchanges) the host intersects with the manifest's list when building the route map. A deployment whose broker covers fewer segments then unroutes an exchange in Settings instead of the plugin's maintainer repackaging — and an unrouted exchange falls back to the app's own data.

### Compatibility

The manifest parser ignores unknown fields, so a manifest carrying both `exchange` and `exchanges` validates on a current build (routing the single exchange) and on an extended one (routing all). A plugin can ship one manifest that works across both while the extension rolls out.

### Footprint

Two files, roughly a dozen lines: the manifest field and the per-exchange map build in `apply`. Everything else in the host — routing, capability gating, `BrokerStatus` — already generalizes. If the design looks right, I'm happy to send a reference PR — and a worked plugin (OpenAlgo) as the example.

### Alternatives considered

- One package per exchange: works in today's protocol, but multiplies installs and settings for one source.
- A generic prefix with the segment encoded in the ticker: keeps one package, but the chart symbol no longer names the exchange the market actually trades on, and same-named symbols across segments collide.
