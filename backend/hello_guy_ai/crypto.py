"""Fernet encryption for secrets stored in the DB (LLM API keys). Key: settings.FIELD_ENCRYPTION_KEY.

Key handling matches the host's previous helper, so values encrypted before this module existed still decrypt.
`manage.py check --deploy` fails (hello_guy_ai.E001) while the development fallback key is in use or the key is not a valid Fernet key.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

# Public development key: only used with DEBUG on and FIELD_ENCRYPTION_KEY unset.
_DEV_KEY = b"uV9w8Q3s9K1l4Z7m2N5p8T1y6X4b7V9w8Q3s9K1l4Z4="


def _fernet() -> Fernet:
    raw = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip().encode()
    if not raw:
        if not settings.DEBUG:  # a key that is public in the source must never protect real secrets
            raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is required when DEBUG is off.")
        logger.warning("FIELD_ENCRYPTION_KEY is not set: using the public development key (DEBUG only).")
        return Fernet(_DEV_KEY)
    try:
        return Fernet(raw)
    except (ValueError, TypeError) as exc:
        if not settings.DEBUG:  # a low-entropy placeholder (e.g. .env.example) must never protect real secrets
            raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is not a valid Fernet key.") from exc
        # DEBUG only: derive one, same as before so old dev ciphertext stays readable
        logger.warning("Invalid FIELD_ENCRYPTION_KEY (%s); deriving dev fallback Fernet key.", exc)
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        logger.error("Could not decrypt a stored secret: FIELD_ENCRYPTION_KEY changed or data is corrupt")
        return ""
