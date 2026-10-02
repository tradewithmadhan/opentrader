# Provider Switching + Frontend-Entered Credentials — Implementation Plan

Status: **planned, not implemented**. Confirm before building.
Branch: `sample-web-clean` (tracks `origin/main` via cherry-picks).

## 1. Goal

Add many data providers and let the user switch between them from the
frontend, entering API details (Host URL, WebSocket URL, API Key) in the UI —
on both the web (browser) shell and the Rust (Tauri desktop) app.

Decisions already taken:

- Secret storage (desktop): `tauri-plugin-store` file (already a dependency).
- Switch behavior: persist + reload (matches the existing `setSource` pattern;
  no live hot-swap races; entitlements re-probe naturally).
- Scope: switching + config infrastructure for existing providers
  (massive / sample / openalgo). OpenAlgo stays downstream-only and never
  goes upstream.
- Config shape is **common to all providers**: Host URL, WebSocket URL,
  API Key (all optional; sample needs none). Saved **provider-wise**.

## 2. Current state (what exists today)

### Rust (Tauri shell)

- `provider::build()` (`src-tauri/src/data/provider/mod.rs:146`) builds the
  provider **once at startup** from the `DATA_PROVIDER` env var (default
  `massive`); the `Arc<dyn DataProvider>` is managed app state, the live task
  is spawned once (`WsHandle` managed), entitlements probe once.
- Gateway token is compiled in (`OPENTRADER_GATEWAY_TOKEN` via `build.rs`,
  fallback `.env` at dev time). No runtime credential path exists.
- `credential_id()` returns a fingerprint (never the secret) and keys the
  entitlement cache. `get_data_provider` / `get_provider_capabilities`
  commands already exist for frontend readout.
- `tauri-plugin-store` is already a dependency (used for other persisted
  state) — no new plugin needed.

### Web (browser shell)

- `source()` (`src/data/sources/index.ts`) selects `?source=` →
  `localStorage["ot:data-source"]` → default (sample outside Tauri).
  `setSource(name)` persists + reloads. One-file-per-provider already holds
  engine + `DataSource` + presentation adapter.
- OpenAlgo credentials come from build-time `VITE_OPENALGO_*` (`.env`);
  editing them requires a dev restart today. There is no settings UI for
  providers; Settings has Service/Network/About tabs (Network tab is
  disabled scaffolding).

## 3. Shared contract

```ts
type ProviderConfig = { hostUrl?: string; wsUrl?: string; apiKey?: string };
```

- Registry entries gain: `label`, `needsConfig: ("hostUrl"|"wsUrl"|"apiKey")[]`,
  per-provider defaults (OpenAlgo hosts as today; Massive: key only).
- Same shape is sent to Rust (`set_active_provider`) as JSON.
- Sample ignores config entirely (needs none).

## 4. Frontend design (web + Tauri, one screen)

1. Each provider file exports its config descriptor (defaults + needed
   fields). The settings UI is fully generic: provider list (radio) + the
   same three inputs (API Key password-masked) + Save/Test.
2. New "Data provider" section in Settings (minimal additive touch on the
   upstream-owned `AppSettingsDialog`, flagged in the commit message per
   repo rules; all logic lives in a new branch-owned module).
3. **Web path:** values persist to `localStorage` per provider
   (`ot:provider:<name>`); OpenAlgo reads runtime config first with `.env`
   values as seed defaults. Switch = existing `setSource(name)` + reload.
4. **Tauri path:** the same UI calls the new `set_active_provider(name,
   config)` command; on success the frontend reloads (same robust pattern).

## 5. Rust backend design

1. `provider::build_with(name, config)` alongside `build()`. Massive takes
   token/host override from the passed config, falling back to
   compiled-in/env exactly as today. Sample ignores config.
2. New `commands/provider_config.rs` (+1 registration line in `lib.rs`):
   - `list_providers` → `[{ name, label, needsConfig, hasSavedConfig }]`
     (never secrets).
   - `get_provider_config(name)` → `{ hostUrl, wsUrl, hasKey: boolean }`
     (never the key itself).
   - `set_active_provider(name, config)` → validate → persist → rebuild
     provider → swap managed `Provider` → respawn live task + fresh
     `WsHandle` → re-run entitlements probe → emit event → return ok
     (frontend reloads on success).
3. Persistence in the existing store file: `providers.active` plus
   `providers.<name>.{hostUrl,wsUrl,apiKey}`. Startup reads it, falling
   back to `DATA_PROVIDER` env → `massive` (today's path untouched).
4. `credential_id()` incorporates configured values as a fingerprint (never
   the secret) so the entitlement cache keys correctly per
   provider+credential.
5. Security rules (non-negotiable): secrets never logged, never returned to
   the frontend, never in DOM/request logs or error strings — same bar as
   the existing ticker-icon proxy pattern.

## 6. Compatibility (what does NOT change)

- No config set anywhere → today's behavior exactly: env / compiled-in /
  `.env` / VITE paths all keep working. Switching is purely additive.
- `DATA_PROVIDER` env remains the default/fallback selector on desktop.
- Single-file-per-provider layout untouched; registries gain entries only.
- Upstream-owned files change only minimally/additively with flagged
  commits (`AppSettingsDialog` section, `lib.rs` registration line);
  preferred shape is new branch-owned files + the existing socket seams.

## 7. Verification plan

- `npx tsc --noEmit` clean; `npm run build` succeeds.
- Node smokes (existing harness): config round-trip (save → reload →
  active), switch flow sample↔openalgo in browser, wrong-key error path,
  no-secret-leak assertion (recorded IPC payloads contain no key material).
- Manual matrix: web sample↔openalgo switch; Tauri massive↔sample switch;
  restart persistence; fresh profile with no config (defaults path).
- Rust: small mirrored-pattern edits; `cargo test export_bindings`
  parity + full build via CI (no local toolchain). New command follows the
  existing `commands/*` + specta patterns.
- Commit per piece (`sample-web-clean`), push; Rust changes ride CI.

## 8. Sequencing (proposed)

1. Frontend settings UI + runtime config for browser sources (no Rust
   changes; verifiable today) + docs touch-ups.
2. Rust `build_with` + the three commands + store persistence.
3. Manual matrix + smokes, then merge/push.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Secret leaks into logs/DOM/bundle | Never return keys (`hasKey` only); mask inputs; review diffs for secret content before every commit |
| Stale live task after switch | Reload-on-switch (chosen); server respawns + re-probes deterministically |
| Rust without local toolchain | Keep edits small and pattern-mirrored; CI compiles/tests |
| Upstream-owned file touches | Minimal + additive + flagged; prefer new files and existing seams |
| VITE_ values baked into web bundles | Documented as local-dev-only; never ship a build containing them |

## 10. Explicitly out of scope

- Hot-swap without reload; OS-keyring storage (possible follow-up);
  new vendors beyond the three existing providers; screener on sample
  (still Tauri+gateway only); any change to chart/datafeed/session logic
  for this feature.
