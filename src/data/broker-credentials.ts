/*
 * Broker credentials — runtime connection details for broker-backed sources,
 * stored per provider in localStorage (browser shells only).
 *
 * Shape is generic on purpose: REST base URL + WebSocket URL + API key +
 * optional API secret covers nearly every broker; a future provider reads its
 * own id from this store instead of inventing a second mechanism. The `.env`
 * `VITE_*` values remain the dev fallback (see each provider's config fn).
 *
 * Stored only in this browser (never shipped, never committed) — per-user
 * convenience, not vault security: any script on the page can read it.
 */
import { getItem, removeItem, setItem } from "./kv";

/** Connection details every broker needs; `secret` for brokers with key pairs. */
export type BrokerCreds = {
  base: string;
  ws: string;
  key: string;
  secret?: string;
};

const keyOf = (providerId: string): string => `ot:broker:${providerId.toLowerCase()}`;

function clean(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/** Stored credentials for `providerId`, or null when never saved (or corrupt). */
export function getBrokerCreds(providerId: string): BrokerCreds | null {
  let raw: string | null = null;
  try {
    raw = getItem(keyOf(providerId));
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const p = JSON.parse(raw) as Partial<BrokerCreds>;
    const creds: BrokerCreds = { base: clean(p.base), ws: clean(p.ws), key: clean(p.key) };
    const secret = clean(p.secret);
    if (secret) creds.secret = secret;
    return creds;
  } catch {
    return null;
  }
}

/** True when every required field is filled (secret stays optional). */
export function hasBrokerCreds(c: BrokerCreds | null): c is BrokerCreds {
  return !!c && !!c.base && !!c.ws && !!c.key;
}

/** Persist credentials (values are trimmed; an empty secret is dropped). */
export function saveBrokerCreds(providerId: string, creds: BrokerCreds): void {
  const next: BrokerCreds = { base: clean(creds.base), ws: clean(creds.ws), key: clean(creds.key) };
  const secret = clean(creds.secret);
  if (secret) next.secret = secret;
  setItem(keyOf(providerId), JSON.stringify(next));
}

/** Forget the stored credentials. */
export function clearBrokerCreds(providerId: string): void {
  try {
    removeItem(keyOf(providerId));
  } catch {
    /* storage unavailable — nothing to forget */
  }
}
