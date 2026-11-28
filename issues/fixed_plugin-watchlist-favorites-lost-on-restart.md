# Bug — a plugin's watchlists and their favorites are deleted during startup before the plugin connects

> **FIXED upstream** in `25774ac8 / 423` (issue #423), adopted in this fork. Kept for reference.

Status: **FILED** as https://github.com/deepentropy/opentrader/issues/423 (27/11/2026), on the user's explicit instruction. Do not make further changes to the live issue without the user's instruction.

Paste everything below the line into the issue body.

---

### Steps to reproduce

1. Install a data plugin whose token is a per-version password setting, then open one of its ready-made watchlists and mark it as a favorite (star).
2. Install a new plugin version. The token does not carry over to the new version's setting, so at the next start the plugin is enabled but has no token.
3. Restart the app.
4. Paste a fresh token when the plugin asks for it, and let the plugin send its watchlists.
5. Check the favorite.

### Expected

The list keeps its favorite state. The plugin sends the same stable ids as before, so its lists should be updated in place.

### Actual

The favorite is gone: the list was deleted and recreated without it. Charts of the plugin's symbols also fail to load until the symbol is changed, for the same reason.

### Why it happens

`data.watchlists` replaces the whole set by id, and `setPluginList()` preserves a list's `favorite` flag when it updates an existing list — so an ordinary refresh is not the problem.

At startup, however, a plugin counts as enabled only when its token is non-empty (`plugins/mod.rs:424`). With an empty token there are no serving exchanges, so `set_exchanges()` drops the plugin's watchlists (`plugins/data.rs:321-329`). The frontend's sync then deletes every store list whose source plugin no longer appears, including the favorite flag (`plugin-watchlists.ts:54-66`). When the token is pasted and the plugin connects, the lists are created again with `favorite: false` (`watchlist-store.ts:1145`).

### Requested behaviour

Keep a known plugin's store lists (and their favorites, sort order, collapsed groups and any user alert on them) through startup, even while the plugin is still waiting for its token or has not connected yet. Delete a plugin's lists only when the plugin is genuinely removed or disabled by the user, or when it connects and sends a set that no longer contains an id. Open lists of a plugin that is temporarily unavailable should stay in place, marked as unavailable, rather than vanish and be recreated without the user's choices.

### Also observed: favourite quick-switch button in the light theme

The favourite-lists toolbar above the watchlist is unreadable in the light theme. Its active round button uses
`--color-container-fill-primary-neutral-extra-bold` as background (`#2e2e2e`) and
`--color-cold-gray-800` as the initial letter's colour (also `#2e2e2e`), so the letter disappears into the circle.
In the dark theme the same background is `#f2f2f2` and the dark letter reads correctly. This is in
`src/styles/watchlist.css:154-171`; the same colour pair is used for the checked settings checkbox at lines
770-774. Expected: the letter keeps a contrasting colour in both themes.

### How often

Every restart that begins without the plugin's token for the current plugin version — in practice, every plugin upgrade until the token is entered again.

### Additional context

The plugin's list ids are stable across versions, so the app has everything it needs to match the incoming set to the existing lists.
