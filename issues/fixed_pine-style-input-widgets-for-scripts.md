<!-- Filed as https://github.com/deepentropy/opentrader/issues/417 (26/11/2026), feature request, using the shape of .github/ISSUE_TEMPLATE/feature_request.yml (label: enhancement) — the account cannot add labels, so it is unlabeled. File-only marker: the published body starts after this comment and carries no Status line. -->

### What do you want to do?

> **FIXED upstream** in `5e110caa / 417` (issue #417), adopted in this fork. Kept for reference.

When I open a script's settings, I want the inputs to use the same kind of controls TradingView's Pine Script v6 gives them — in particular a **timeframe dropdown** and a **symbol search box** instead of typing `"5S"` or `NFO:NIFTY26DECFUT` into a plain text field. This matters for indicators that read other timeframes or other symbols: I use a 5-second volume-spike study on a 5-minute chart with a *Lower timeframe* and a *Symbol* input, and both are bare text boxes today.

OakScript already declares the right input types (`input.timeframe`, `input.symbol`, `input.source`, `input.time`, `input.price`, `input.color`, `input.session`, `input.text_area`…); the settings dialog just doesn't give them their Pine widgets.

### Proposed behaviour

In the **Inputs tab of the indicator/script settings dialog**, pick the widget from the input's type:

| Input | Widget wanted | Today |
|---|---|---|
| `input.int()` / `input.float()` | number field, or dropdown when `options` is given | number field (dropdown only works for `string` inputs) |
| `input.bool()` | checkbox | checkbox ✓ |
| `input.string()` | text box, or dropdown when `options` is given | text box / dropdown ✓ |
| `input.text_area()` | multi-line text box | single-line text field |
| `input.color()` | colour picker | colour picker ✓ |
| `input.source()` | dropdown of chart price sources and study plots | dropdown ✓ |
| `input.timeframe()` | **timeframe/resolution dropdown** (1, 5, 15, 60, D, W, M… and seconds such as 5S) | plain text field |
| `input.symbol()` | **ticker search box** (type to find a symbol, pick it) | plain text field |
| `input.session()` | time-of-day range selector, including weekday flags (`0930-1600:23456`) | two HH:MM fields, no day flags |
| `input.time()` | date and time picker | date + time picker ✓ |
| `input.price()` | number field that also lets me click a level on the chart | number field (type is reported as `float`) |
| `input.enum()` | dropdown populated from a script enum | not in the library at all |

The dropdown/search controls should filter as you type, keep the symbol picker's result in `EXCHANGE:TICKER` form (so `request.security` can use it), and work for script settings on a chart and in the screener, which share this dialog. An empty *Symbol* value should keep meaning "the chart symbol".

### Do you know another charting app that does this?

TradingView Pine Script v6. Its settings dialog uses a whole-number or decimal field for `input.int`/`input.float`, a checkbox for `input.bool`, a text box or dropdown for `input.string`, a multi-line box for `input.text_area`, a colour picker for `input.color`, a source dropdown for `input.source`, a timeframe/resolution dropdown for `input.timeframe`, a ticker search box for `input.symbol`, a time-of-day range selector for `input.session`, a date/time picker for `input.time`, a price field with on-chart picking for `input.price`, and an enum dropdown for `input.enum`.

### Anything else

The library side is mostly ready: oakscriptjs 0.21.0 declares all of the above except `input.enum`. This request is about the app's `IndicatorSettingsDialog.tsx` widget selection (`inputControl`, around lines 677–743): `timeframe`, `symbol` and `text_area` currently fall through to a single-line `<input type="text">`, `input.price` arrives as `float`, `session` is only two time fields, and the existing `SelectControl` has no search/filter. Adding a searchable select would cover the timeframe and symbol cases.

This is separate from #416 (dropdowns stop opening after the settings window is moved).

### Workarounds you use today

Typing timeframe strings and full ticker ids by hand, entering an extra text area as one line, typing price levels instead of clicking them on the chart, and building a "dropdown" as `input.string(..., { options })`.
