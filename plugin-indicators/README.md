# plugin-indicators

Scripts a plugin can publish to OpenTrader with Plugin API 8's `data.scripts`.

## Layout

- `*.js` — ordinary OakScript studies/strategies. Edit these.
- `generate.py` — turns them into the single `scripts.json` manifest.
- `scripts.json` — generated output; this is the one file a plugin fetches from a raw URL.

## Generate

```bash
python generate.py                      # writes scripts.json here
python generate.py --check              # also compiles/runs every .js first, refusing on errors
python generate.py -o ../plugins/zerodhaenctoken/scripts/scripts.json
```

`--check` uses the repo's OakScript CLI (`node node_modules/oakscriptjs/bin/oakscript.mjs check`) on 1500 daily
synthetic bars, so a study that does not compile, throws, or fails a check never reaches `scripts.json`. Warnings
are printed but do not block; a non-zero checker exit does. Needs `npm ci` first for `node_modules`.

The generator validates ids (letters, digits, `.` `_` `-`, ≤64), source size (≤512 KB), duplicates and the
50-script cap. The **file name is the script id** — keep it stable once users have the script on a chart; changing
the source under the same id redraws that chart, changing the id creates a different study. The display name and
kind come from the first `indicator("…")` or `strategy("…")` call.

## Sample scripts

| File | What it does |
|---|---|
| `Monthly_Levels.js` | Monthly high/low levels from NSE weekly-expiry dates (overlay) |
| `MA_Cloud.js` | EMA high/low cloud coloured by a MACD-like state machine (overlay) |
| `EMA_Trend.js` | Two EMAs with crossover markers (overlay) |
| `Volume_Surge.js` | Volume versus its average, with surge markers (separate pane) |
| `EMA_Cross_Strategy.js` | Long-only EMA crossover with a stop — `kind: "strategy"`, runs in the Strategy Tester |

## Publishing

Host this folder in a Git repository and point the plugin's `scriptManifestUrl` at the **raw** URL of
`scripts.json`, e.g.

```
https://raw.githubusercontent.com/<owner>/<repo>/main/plugin-indicators/scripts.json
```

A plugin polls that URL, compares the manifest with what it last sent, and calls `data.scripts` only when it changes.
Scripts appear in the Indicators dialog under the plugin's name, read-only with *Make a copy*.

Only publish scripts from a repository you trust: they execute code inside the charting app.
