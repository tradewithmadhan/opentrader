/*
 * Source selector — THE ONLY SWITCH. Every data consumer calls `source()`;
 * adding a new source means implementing `DataSource` in one sibling file
 * and adding one registry line below. No other file is touched.
 *
 * Selection order: `?source=` URL param → persisted `localStorage` choice →
 * default (sample feed in a plain browser, Tauri backend in the shell).
 * Each provider is one file (engine + `DataSource` + presentation adapter);
 * a future `real.ts` adds one line here and one in `../providers`.
 */
import { sampleSource, useSampleFeed } from "../providers/sample";
import { openalgoSource } from "../providers/openalgo";
import { tauriSource } from "./tauri";
import type { DataSource } from "./types";

/** Registry of known sources. A future `real.ts` adds one line here. */
const REGISTRY: Record<string, DataSource> = {
  tauri: tauriSource,
  sample: sampleSource,
  openalgo: openalgoSource,
};

/** Persisted override key (a future settings UI writes it; `?source=` wins). */
const SOURCE_KEY = "ot:data-source";

function explicitChoice(): string | null {
  try {
    if (typeof window === "undefined") return null;
    const q = new URLSearchParams(window.location.search).get("source");
    if (q) return q;
    return window.localStorage.getItem(SOURCE_KEY);
  } catch {
    return null;
  }
}

let active: DataSource | null = null;

/** The active data source (memoized per page load). */
export function source(): DataSource {
  if (active) return active;
  const want = explicitChoice();
  if (want && REGISTRY[want]) {
    active = REGISTRY[want];
    return active;
  }
  active = useSampleFeed() ? sampleSource : tauriSource;
  return active;
}

/** Persist a source choice and reload so the whole module graph re-seeds.
 *  Returns false when `name` is unknown. */
export function setSource(name: string): boolean {
  if (!REGISTRY[name]) return false;
  try {
    window.localStorage.setItem(SOURCE_KEY, name);
  } catch {
    return false;
  }
  window.location.reload();
  return true;
}
