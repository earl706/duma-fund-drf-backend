"""Encrypt / decrypt per-user Receipt AI API keys at rest."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    raw = (getattr(settings, "LLM_CREDENTIALS_KEY", None) or "").strip()
    if raw:
        key_bytes = raw.encode("ascii") if isinstance(raw, str) else raw
        try:
            return Fernet(key_bytes)
        except (ValueError, TypeError, InvalidToken):
            digest = hashlib.sha256(f"dumafund-llm-key:{raw}".encode()).digest()
            return Fernet(base64.urlsafe_b64encode(digest))
    digest = hashlib.sha256(
        f"dumafund-llm-credentials:{settings.SECRET_KEY}".encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    text = (plaintext or "").strip()
    if not text:
        return ""
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    blob = (ciphertext or "").strip()
    if not blob:
        return ""
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""


def key_last4(plaintext: str) -> str:
    text = (plaintext or "").strip()
    if not text:
        return ""
    return text[-4:] if len(text) >= 4 else text
