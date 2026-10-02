/*
 * Browser drawing-image store for shells without the Tauri backend.
 *
 * Mirrors `src-tauri/src/commands/images.rs` with Web APIs: files persist in
 * localStorage (`ot:drawing-image:` + `<16 hex>.<ext>`, same name shape as the
 * backend's content hash so names stay interchangeable), sizes reuse the
 * drawing core's validators. `image-store.ts` delegates here outside Tauri;
 * the backend path is untouched.
 */
import {
  cacheImage,
  decodeImage,
  IMAGE_MAX_BYTES,
  IMAGE_TYPES,
  imageMimeOf,
} from "lightweight-charts-drawing/core/kinds/images";

const KEY_PREFIX = "ot:drawing-image:";

function bytesToB64(bytes: Uint8Array): string {
  let out = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    out += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(out);
}

function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

async function contentName(bytes: Uint8Array, ext: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 16);
  return `${hex}.${ext}`;
}

function storage(): Storage {
  try {
    const s = window.localStorage;
    // Probe: private mode can present a dead store.
    s.getItem(KEY_PREFIX);
    return s;
  } catch {
    throw new Error("browser storage is unavailable");
  }
}

/** Blob URL source for the drawing core's image cache (see `setImageReader`). */
export async function readWebImage(name: string): Promise<Blob> {
  const raw = storage().getItem(KEY_PREFIX + name);
  if (!raw) throw new Error(`unknown image: ${name}`);
  return new Blob([b64ToBytes(raw)], { type: imageMimeOf(name) });
}

/** Store a chosen file (same checks + return shape as the backend path). */
export async function saveWebImage(file: File): Promise<{ name: string; width: number; height: number }> {
  const ext = IMAGE_TYPES[file.type];
  if (!ext) throw new Error("Use a JPG, PNG or WEBP image");
  if (file.size > IMAGE_MAX_BYTES) throw new Error("The image is larger than 2MB");
  const bytes = new Uint8Array(await file.arrayBuffer());
  const loaded = await decodeImage(URL.createObjectURL(file));
  const name = await contentName(bytes, ext);
  try {
    storage().setItem(KEY_PREFIX + name, bytesToB64(bytes));
  } catch {
    throw new Error("image store is full (browser storage limit)");
  }
  cacheImage(name, loaded);
  return { name, width: loaded.width, height: loaded.height };
}

/** Open a PNG snapshot data URL in a new tab (browser fallback for the
 *  backend's open-snapshot command, which needs a desktop window). */
export function openSnapshotInBrowser(b64: string): void {
  const url = URL.createObjectURL(new Blob([b64ToBytes(b64)], { type: "image/png" }));
  window.open(url, "_blank", "noopener");
}
