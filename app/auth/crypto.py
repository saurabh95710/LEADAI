"""Secure password hashing utilities.

Provides bcrypt-based password hashing with automatic migration from legacy
SHA-256 hashes. When a password is verified against a legacy SHA-256 hash
and the hash format is detected, a new bcrypt hash is generated and returned
so the caller can persist the upgrade.

All functions are designed to be called from synchronous code (background
threads, sync DB drivers). The bcrypt library handles salting automatically.
"""
import hashlib
import hmac
import logging
import re
from typing import Optional, Tuple

import bcrypt

logger = logging.getLogger(__name__)

# Bcrypt cost factor — 12 is a good balance of security and performance
# (~250ms on modern hardware). Increase if hardware improves.
BCRYPT_ROUNDS = 12


def hash_password(plain: str) -> str:
    """Hash a password using bcrypt. Returns the bcrypt hash string."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(plain: str, stored_hash: str) -> Tuple[bool, Optional[str]]:
    """Verify a password against a stored hash.

    Returns (valid, new_hash):
      - valid: True if the password matches
      - new_hash: If the stored hash was a legacy SHA-256 and the password
        matched, this contains the new bcrypt hash that should be persisted.
        None if no migration is needed.

    This enables transparent migration: on successful login with a legacy
    hash, the caller replaces the old hash with the new bcrypt hash.
    """
    if not plain or not stored_hash:
        return False, None

    stored = stored_hash.strip()

    # Detect legacy SHA-256 hashes (64 hex chars, no $ prefix)
    if _is_legacy_sha256(stored):
        return _verify_legacy_sha256(plain, stored)

    # Modern bcrypt hash
    try:
        valid = bcrypt.checkpw(plain.encode("utf-8"), stored.encode("utf-8"))
        return valid, None
    except Exception:
        return False, None


def _is_legacy_sha256(h: str) -> bool:
    """True if the hash looks like an unsalted SHA-256 hex digest."""
    return bool(re.fullmatch(r"[0-9a-f]{64}", h.lower()))


def _verify_legacy_sha256(plain: str, stored_hash: str) -> Tuple[bool, Optional[str]]:
    """Verify against legacy SHA-256 and generate a bcrypt replacement."""
    guess = hashlib.sha256(plain.encode("utf-8")).hexdigest().lower()
    valid = hmac.compare_digest(guess, stored_hash.lower())
    if valid:
        # Generate a new bcrypt hash for the caller to persist
        new_hash = hash_password(plain)
        logger.info("Legacy SHA-256 password hash detected — migrating to bcrypt")
        return True, new_hash
    return False, None


def is_bcrypt_hash(h: str) -> bool:
    """True if the string is a valid bcrypt hash."""
    try:
        return h.startswith("$2") and len(h) == 60
    except Exception:
        return False
