# OpenTrader: notes for coding agents

A desktop charting app: a Rust backend (Tauri 2) and a SolidJS frontend. This file is for working **on this
repository**. To work **with the running app** (charts, scripts, strategies), connect to its MCP server instead:
see "Use with a coding agent" in the README.

## Layout

```
src/                     frontend (SolidJS, TypeScript)
  App.tsx                the window: tabs, panes, the stores below wired together
  window/                UI by area: chart/, drawings/, oakscript/ (script editor), strategy-tester/, header/, shell/ …
  data/                  stores and data access (datafeed, kv persistence, alerts, scripts, strategy results)
  agent/                 the MCP server's tools, guide and protocol (answered in the window)
  backtester/            strategy engine (worker)
  bindings.ts            generated from the Rust commands: never edit, it is gitignored
src-tauri/               Rust: commands (src/commands/), windows, alerts, updates, agent.rs (MCP endpoint)
  crates/core            data side shared with the web server
  crates/server          the web build's backend
../lightweight-charts-drawing   drawing core, read from a sibling checkout (see README)
```

Indicators come from `lightweight-charts-indicators`, the script runtime from `oakscriptjs`, drawings from
`lightweight-charts-drawing`. A change to an indicator, the script API or a drawing tool belongs in that library;
each ships its own agent guide in `skills/`.

## Commands

```bash
npm ci
npm test                         # vitest (tests in __tests__/ next to the code)
npx tsc --noEmit -p tsconfig.json
cd src-tauri && cargo test --lib # Rust tests; also regenerates src/bindings.ts
npm run tauri dev                # the app with hot reload (first Rust build: minutes)
```

A new Tauri command: add it in `src-tauri/src/`, list it in `specta_builder()` (`src-tauri/src/lib.rs`), then run
`cargo test --lib export_bindings` and call it through `commands` from `src/bindings.ts`. If the web build serves it
too, put its body in `opentrader_core::commands`, add its name to `SERVED` there and route it in
`crates/server/src/invoke.rs` (tests check that the lists match).

## Conventions

- Match the code around you: comments say why, in plain words; no dead code.
- Persist through `src/data/kv.ts`, not `localStorage`.
- Chart edits go through the stores (`window/shell/chart-edits.ts`, `window/drawings/drawing-store.ts`) so they are
  undo steps and are saved.
- Do not name market-data providers in code, docs or issues.
- Dates shown to the user: dd/mm/yyyy.
- Commit only the files you changed (no `git add -A`): other work may be in the tree.
- **Ask for explicit user confirmation before any external or shared action**: filing a GitHub issue or comment,
  committing, pushing, or opening a PR. Prepare the draft/text and wait — "looks good" about the content is not
  approval to file, commit, push or publish. Never assume confirmation from an earlier "yes" about the approach.
- Cherry-picking upstream commits: strip the trailing `(#NNN)` PR reference from the message
  (`git cherry-pick -n`, then re-commit) so the push does not cross-reference and ping the upstream PR.

## Syncing from upstream

This fork has a **different git root** from `upstream/main` (it was assembled by importing releases, not forked), so
`git merge` is an unrelated-histories merge. Sync by cherry-picking, one commit at a time, oldest first. Work through
**every** upstream commit since the last sync — including version bumps and library updates, not just the fixes that
matter to this fork. `FORK_OVERVIEW.md` records what is in the fork and why each file differs.

- Fetch first (`git fetch upstream`). List the range with `git log --oneline <base>..upstream/main`, then cherry-pick
  in chronological order. A commit whose feature is already present under a fork-port message will apply as an empty
  change — that is fine; verify by checking the code, not the commit subject. Do not bulk-apply a whole range.
- Conflicts: decide by **what the change does**, not by which commit is newer. Read both sides
  (`git diff --cc <file>`, `git show <upstream-commit>:<file>`) before choosing.
  - If upstream's version is an enhancement or fix to code we also have, take theirs as the base and re-apply our
    fork-specific extras on top. A fork-local fix does not stay just because it came first: when upstream fixes the
    same thing later, adopt the upstream version and port our extras forward. Example: our DOM depth fix (`643ba2b7`)
    was replaced by upstream's `a8a78a4b`, then the OpenAlgo plugin was adapted to its `broker.depth` protocol.
    The `fixed_*` issue drafts record where this has already happened.
  - If our side is genuinely newer, or fork-specific (OpenAlgo provider, bindings fallback, branding, web build),
    keep ours. A commit that conflicts everywhere and is superseded (e.g. an old branding or data-access rewrite)
    can be dropped with `git reset --hard HEAD` — but only after confirming its functionality is already in the fork.
  - After taking upstream's side of any file, re-check that our extras are still present (grep for them) and re-run
    the tests. A conflict marker left in a source file or a silently dropped fork feature is the failure mode here.
- Version bumps come with the sync: update `package.json`, `package-lock.json`, `src-tauri/Cargo.toml`,
  `src-tauri/Cargo.lock` and `src-tauri/tauri.conf.json` together. Keep the exe's version equal to the fork's
  declared version — the updater endpoint points at upstream releases, so an old version offers an update that would
  replace the fork with upstream.
- Library bumps come with the version bump: compare `dependencies`, `devDependencies` and `overrides` in `package.json`
  with upstream. `config.drawingRef` is the git tag of the drawing core used by CI and release builds; move the sibling
  checkout (`../lightweight-charts-drawing`) to the **same tag** before testing, or local tests check one version while
  CI builds another. `vite.config.ts` imports the drawing core from that sibling, not from the tag.
- Bindings: upstream generates `src/bindings.ts` with `cargo test --lib export_bindings` and does not commit it. This
  fork keeps a hand-maintained copy and a tracked `src/bindings-fallback.ts`. When upstream adds/changes a Rust type,
  event or command (e.g. `BrokerDepth`, `broker.depth`, `broker_watch_depth`, `PluginTicks`), add the matching type,
  event listener and command signature to **both** files. Without this, TS type-checks but the event never fires at
  runtime (there is no `cargo` in this shell to regenerate it).
- i18n after any UI change: `npm run i18n:extract`, merge new keys into all six dictionaries (upstream's dictionaries
  plus the fork's own translated texts), then `npm run i18n:prune`. Keep fork-only base words/sense entries that study
  tests use. The catalog test is the completeness gate.
- Tests after a sync batch, before pushing: `npx tsc --noEmit -p tsconfig.json`, `npx vitest run`,
  `python plugins/openalgo/tests/run_all.py`. A broker-protocol change also needs a live probe against the running
  plugin (never print the API key). Rust compile is left to CI here.
- Issue drafts: when upstream fixes something we drafted, mark its file `FIXED upstream` with the commit/PR and rename
  it with a `fixed_` prefix. Update `FORK_OVERVIEW.md` when the set of added/modified files changes materially.

## Indicators in `indicators/`

- Back up once a change is confirmed working on the chart: copy `<name>.js` to `<name>_backup.js`.
- Keep only the previous backup: the next accepted change replaces it (one backup per indicator, never a growing set).
- No spaces in file names: use `_` (`MadhaN_Pivot_Study.js`, and its `MadhaN_Pivot_Study_backup.js`).

## Plugins in `plugins/`

- Test a plugin change with the regression suites in `plugins/openalgo/tests/` (tracked, so a temp cleanup or a
  fresh clone cannot lose them): `python plugins/openalgo/tests/run_all.py`. `fake_openalgo.py` is an honest fake
  deployment — the whole requested history range in one call like the real API instead of capping rows, the real
  WebSocket handshake, HTTP 400 for a bodyless GET that declares a JSON body type, and 429 for a rate-limited
  `/symbol` — so a change that mis-sizes a window, pages wrongly, counts requests wrongly or lets a rate limit
  become "Unknown symbol" fails there before it reaches a live account.
- Bump `PLUGIN_VERSION` in `plugin.py` and `version` in `opentrader-plugin.json` together, then
  `python build.py release` from `plugins/openalgo/`.

## Languages

The components keep their English text; `src/i18n/translator.ts` translates the page as it appears from a dictionary
keyed by the English text (`src/i18n/<lang>.json`). `src/i18n/catalog.json` lists every text to translate, generated
from the code, the built-in studies and `src/i18n/tools/run-time-texts.ts`.

- A text: write it whole. Values go through `translateWith("{0} bars of {1}", n, sym)`, counts through
  `translatePlural(n, { one: "{0} bar", other: "{0} bars" })`, dates and numbers through `src/i18n/format.ts`; never
  join words to values (`"Undo " + name`, a test fails). A word with two meanings takes a sense (`data-i18n-sense`,
  `translateAs`). Then `npm run i18n:extract` and add the new entries to each dictionary.
- Not interface text: data (tickers, company and script names, exchanges, hotkeys, what the user wrote, legal notices
  kept verbatim) is shown in an element with `data-no-translate`; a text the code already wrote in the language (words
  translated one by one and joined with values, dates of `format.ts`) goes through `formatted()`. Neither is looked
  up, so neither is reported as left in English.
- A language: one entry in `src/i18n/languages.ts` (name, plural locale, formats, direction, `partial` while it is
  being translated) and its `<lang>.json`; `src/i18n/glossary/<lang>.json` holds its terms.
- `npm run i18n:report` gives each language's coverage; `npm run i18n:missing -- <lang>` writes the missing texts with
  their context and glossary terms to `.tmp/i18n/<lang>-missing.json`, `npm run i18n:merge -- <lang> <file>` merges
  the filled file back (checked); `npm run i18n:prune` removes the entries the catalog lost. In the dev build,
  `window.__i18nMisses()` lists the texts left in English on screen.
- The pseudo-language "Pseudo" (`en-XA`, dev builds' language menu, `src/i18n/pseudo.ts`) shows every catalog text
  accented, a third longer and between markers ⟦…⟧: text without them is left in English. With the dev app running
  with CDP, `npm run i18n:walk -- 9231` walks its screens, menus, dialogs and panels in it and writes the English left
  on screen, the cut text and the misses to `.tmp/i18n-walk/` (report.md, screenshots), then puts the app back.
  `--language=en-XB` or `--language=fr` walks another language (`.tmp/i18n-walk/<lang>/`), `--all-shots` keeps a
  screenshot of every state to check by eye.
- Right to left: the page's `dir` is the language's. CSS uses start/end (`margin-inline-start`, `inset-inline-end`,
  `text-align: start`); left/right only for what keeps its side: the panels' places and their outer edges and resize
  handles, the chart and what sits over it (`src/styles/rtl.css` holds those rules). Code that places a menu or
  popover uses `src/components/reading-direction.ts` (`fitToWindow` and `createPopover` already do). An icon that
  points along the reading direction (back, submenu chevron, expander) takes `ot-flip-rtl`; undo/redo, replay, logos
  and drawing tools don't. A value shown inside text (price, percent, volume, time) is isolated: the `ot-value`
  class (`src/styles/rtl.css`). Check with the dev build's pseudo-language "English, right to left (test)"
  (`en-XB`, `src/i18n/pseudo-rtl.ts`): the English words in right-to-left order inside ⟦ ⟧.
