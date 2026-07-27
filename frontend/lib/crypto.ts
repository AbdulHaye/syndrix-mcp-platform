// AES-256-GCM, key derived via PBKDF2-SHA256 from the bearer token —
// mirrors app/services/crypto.py exactly (same salt/iterations/hash/dkLen,
// same nonce+ciphertext+tag wire format), so either implementation below
// can talk to the same backend.
//
// crypto.subtle (the browser's Web Crypto API) is only available in a
// "secure context" — HTTPS, or http://localhost. A plain-HTTP deployment
// on a raw IP (no domain, so no TLS cert) is NOT a secure context, so
// crypto.subtle is undefined there and calling it throws
// "Cannot read properties of undefined (reading 'importKey')".
//
// To keep working on that kind of deployment, we fall back to @noble/ciphers
// + @noble/hashes — small, audited, pure-JS implementations of the same
// AES-GCM/PBKDF2 algorithms — verified byte-identical output to crypto.subtle
// for the same key/nonce/plaintext.

import { gcm } from "@noble/ciphers/aes.js";
import { pbkdf2 } from "@noble/hashes/pbkdf2.js";
import { sha256 } from "@noble/hashes/sha2.js";

const SALT = new TextEncoder().encode("syndrix-settings-v1");
const ITERATIONS = 30_000;

const hasWebCrypto =
  typeof crypto !== "undefined" && typeof crypto.subtle !== "undefined";

// CryptoKey when Web Crypto is available; raw key bytes otherwise.
export type DerivedKey = CryptoKey | Uint8Array;

export async function deriveKey(token: string): Promise<DerivedKey> {
  if (hasWebCrypto) {
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
  return pbkdf2(sha256, token, SALT, { c: ITERATIONS, dkLen: 32 });
}

function toBase64(bytes: Uint8Array): string {
  // btoa-safe encoding without spread (avoids stack overflow on large buffers)
  let binary = "";
  bytes.forEach((b) => { binary += String.fromCharCode(b); });
  return btoa(binary);
}

export async function encryptWithKey(plaintext: string, key: DerivedKey): Promise<string> {
  if (!plaintext) return "";
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const data = new TextEncoder().encode(plaintext);

  let ct: Uint8Array;
  if (key instanceof Uint8Array) {
    ct = gcm(key, nonce).encrypt(data);
  } else {
    ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, key, data));
  }

  const out = new Uint8Array(12 + ct.byteLength);
  out.set(nonce);
  out.set(ct, 12);
  return toBase64(out);
}

export async function decryptWithKey(ciphertext: string, key: DerivedKey): Promise<string> {
  if (!ciphertext) return "";
  const data = Uint8Array.from(atob(ciphertext), (c) => c.charCodeAt(0));
  const nonce = data.slice(0, 12);
  const body = data.slice(12);

  let plaintext: Uint8Array;
  if (key instanceof Uint8Array) {
    plaintext = gcm(key, nonce).decrypt(body);
  } else {
    plaintext = new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: nonce }, key, body));
  }
  return new TextDecoder().decode(plaintext);
}
