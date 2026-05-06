// AES-256-GCM via Web Crypto API — mirrors app/services/crypto.py exactly.
// Key is derived from the bearer token via PBKDF2-SHA256.

const SALT = new TextEncoder().encode("syndrix-settings-v1");
const ITERATIONS = 30_000;

export async function deriveKey(token: string): Promise<CryptoKey> {
  const raw = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(token),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: SALT, iterations: ITERATIONS, hash: "SHA-256" },
    raw,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

export async function encryptWithKey(plaintext: string, key: CryptoKey): Promise<string> {
  if (!plaintext) return "";
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce },
    key,
    new TextEncoder().encode(plaintext),
  );
  const out = new Uint8Array(12 + ct.byteLength);
  out.set(nonce);
  out.set(new Uint8Array(ct), 12);
  // btoa-safe encoding without spread (avoids stack overflow on large buffers)
  let binary = "";
  out.forEach((b) => { binary += String.fromCharCode(b); });
  return btoa(binary);
}

export async function decryptWithKey(ciphertext: string, key: CryptoKey): Promise<string> {
  if (!ciphertext) return "";
  const data = Uint8Array.from(atob(ciphertext), (c) => c.charCodeAt(0));
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: data.slice(0, 12) },
    key,
    data.slice(12),
  );
  return new TextDecoder().decode(plaintext);
}
