"""Fernet encryption for secrets stored in the DB (LLM API keys). Key: settings.FIELD_ENCRYPTION_KEY.

Key handling matches the host's previous helper, so values encrypted before this module existed still decrypt.
`manage.py check --deploy` fails (hello_guy_ai.E001) while the development fallback key is in use.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)

# Public development key: only used when FIELD_ENCRYPTION_KEY is unset (never in production, see checks.py).
_DEV_KEY = b"uV9w8Q3s9K1l4Z7m2N5p8T1y6X4b7V9w8Q3s9K1l4Z4="


def _fernet() -> Fernet:
    raw = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip().encode()
    if not raw:
        return Fernet(_DEV_KEY)
    try:
        return Fernet(raw)
    except ValueError:  # not a Fernet key: derive one, same as before so old ciphertext stays readable
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
