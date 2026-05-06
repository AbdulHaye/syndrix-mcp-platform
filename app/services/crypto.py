from __future__ import annotations

import base64
import os

# PBKDF2 params — fixed salt keeps key derivation deterministic for a given token
_SALT = b"syndrix-settings-v1"
_ITERATIONS = 30_000


def _derive_key(token: str) -> bytes:
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=_ITERATIONS,
    )
    return kdf.derive(token.encode("utf-8"))


def encrypt_value(plaintext: str, token: str) -> str:
    """AES-256-GCM encrypt. Returns base64(nonce + ciphertext+tag)."""
    if not plaintext:
        return ""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_key(token)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_value(ciphertext: str, token: str) -> str:
    """AES-256-GCM decrypt. Input is base64(nonce + ciphertext+tag)."""
    if not ciphertext:
        return ""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_key(token)
    raw = base64.b64decode(ciphertext)
    nonce, body = raw[:12], raw[12:]
    plaintext = AESGCM(key).decrypt(nonce, body, None)
    return plaintext.decode("utf-8")
