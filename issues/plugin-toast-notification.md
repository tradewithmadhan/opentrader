# Feature request — non-blocking toast notifications from a plugin, with sound

Status: **FILED** as https://github.com/deepentropy/opentrader/issues/401 (24/11/2026) as a feature request, using
the shape of `.github/ISSUE_TEMPLATE/feature_request.yml` (label: enhancement). User explicitly approved filing.

**Area:** Data plugin / imported data  ·  **Importance:** I would use it regularly  ·  **Labels:** enhancement

---

### What do you want to do?

I want a plugin to be able to tell me that something happened, without interrupting what I am doing. For example: a
session token was refreshed automatically, an instrument master finished reloading, a data source went into a
rate-limit backoff and recovered, or a long history read completed. I want a short message to appear and disappear on
its own, with an optional sound, so I can keep watching the chart.

More generally, a plugin is the thing that sees **broker-side and exchange-side updates and signals** — an order or
position status change, a margin or funds update, a corporate action, an index/contract change, a circuit or trading
status message from the venue, or a signal the plugin itself computes. Right now none of those can be surfaced as a
short, audible on-screen message: the only broker toast is the narrow `broker.event` order case, and a data plugin has
no toast at all. A general notification would make all of these useful, not just the plugin's own housekeeping.

Today a data plugin has no way to do this. Its `log` messages go only to a log file, and `plugin.prompt` opens a
**dialog the user must answer** — useful for asking for a secret, but far too intrusive for "this finished". The only
toast in the protocol belongs to `broker.event` (order events, and only when a broker is connected), and
`broker.notice` only adds a row to the account manager's Notifications log.

### Proposed behaviour

Add one plugin → app notification, for example:

```json
{"jsonrpc":"2.0","method":"plugin.toast","params":{"level":"info","message":"Instrument master reloaded","sound":true}}
```

- Shows as a **transient toast** where the app already shows alerts — no dialog, no button to press, no answer expected
  from the plugin.
- `message` is **custom text written by the plugin**, so it can describe its own event in its own words; it is shown as
  given (like other plugin-provided labels), with the plugin's name alongside it.
- `level` is `info` / `warn` / `error` and picks the toast's tint, like existing notifications in the app.
- `sound` is optional: when true, play the app's standard notification sound. It should follow the app's sound settings
  (mute / volume) rather than defining its own.
- Works for **any** plugin (data or broker), regardless of whether the account manager is open or a broker is
  connected, and shows the plugin's name so I know where it came from.
- The app caps repeats (e.g. collapse identical messages within a short window) so a plugin cannot flood the screen.
- No new capability or setting is required for a plugin to send it; a plugin that never sends it is unaffected.

### Do you know another charting app that does this?

Most trading and charting apps have this feature: a short, non-blocking toast for connection, data and order events —
for example "reconnected", "history updated", "rate limit, retrying" — often with an optional sound. They never take
focus or wait for an answer, unlike a confirmation dialog. A plugin needs the same ability: a **custom toast
notification** whose message it writes itself, so each plugin can report its own events in its own words. This is a
basic, expected capability in most apps rather than a niche request.

### Area of the app

Data plugin / imported data (plugin protocol + a small piece of shared UI for the toast).

### How important is it for you?

I would use it regularly — several plugin-side events are worth a one-line message, and today none of them can be
surfaced without a blocking dialog.

### Workarounds you use today

- `plugin.prompt` — visible, but blocks the plugin waiting for an answer and demands a click every time.
- `broker.notice` — visible only in the account manager's Notifications log, and only from the connected broker plugin.
- `log` — written to a file; I have to go looking for it.

None of these is a non-blocking, audible message on the chart screen.

### Anything else

This is deliberately **not** a request to reuse `broker.event`: that method is defined as an order notification with a
fixed set of `kind` values, and overloading it with arbitrary messages would make order toasts meaningless. It is also
not a request to make `plugin.prompt` optional — prompts are the right tool when the plugin genuinely needs an answer
(for example a secret). A separate, fire-and-forget notification fills the gap between the two.

A plugin can also decide **when a message matters**: a routine update can be a quiet `info` toast, while an exchange
halt, a rejected order or a failed connection can be `error` with sound. This is meant to be used for real broker- and
exchange-driven events and signals, not just plugin maintenance — but it stays a notification: it must never ask the
user for input or take focus away from the chart.
