# Agent Rules

Working agreement for AI-assisted development on this branch (`sample-web-clean`,
which tracks upstream `origin/main` via cherry-picks).

## 1. Provider independence (hard rule)

- Upstream app-core files **never change for a provider's needs**. A data
  provider adapts to the app's formats; the app is not reshaped for the
  provider's data format.
- All provider logic — fetching, caching, broker→app mapping — lives in the
  provider's own file(s): `src/data/sources/<name>.ts` (+ its `providers/`
  adapter). That is the whole surface a new provider should need.
- Additive, optional extension slots may live in branch-owned socket files
  (`src/data/sources/types.ts`, `src/data/sources/index.ts`) — zero effect on
  providers that don't implement them. These files exist for exactly this.
- Only if the app genuinely cannot support what a provider needs (no slot,
  no seam, no workaround) may an upstream-owned file change — and then
  minimally, additively, and flagged clearly in the commit message.
- Never touch: upstream data/session/feed/chart flow to accommodate one
  provider. Prefer: a bridge in untracked dev-only shims (`src/bindings.ts`,
  gitignored, never ships) over any production behaviour change.

## 2. Upstream-first merging

- This branch follows `origin/main`. Pull its fixes with `git cherry-pick -n`,
  resolving conflicts file by file.
- In conflicts, **upstream wins** where its design supersedes ours (sessions,
  symbol identity, formatting); keep only our unique layer (source routing,
  seeds, registries, browser shells, fallbacks).
- Our past work that upstream re-implements gets retired, not defended.

## 3. Verification (required before commit)

- `npx tsc --noEmit` must be clean; `npm run build` must succeed.
- Rust has no local toolchain: keep Rust edits small, mirrored on reviewed
  patterns, and let CI verify. Never break a provider trait impl silently —
  a new required method needs an impl in **every** provider.
- Behavioural changes get a node smoke (see `session-smoke` / `quote-alert`
  pattern: esbuild bundle + stubs, run real modules, assert end-to-end).
- Commit per upstream cherry-pick (or per feature), then push `sample-web-clean`.
