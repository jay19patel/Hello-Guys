import base64
import hashlib
import hmac
import logging
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

# Fallback in-memory key for local development if FIELD_ENCRYPTION_KEY is omitted
_DEV_FERNET_KEY = b'uV9w8Q3s9K1l4Z7m2N5p8T1y6X4b7V9w8Q3s9K1l4Z4='


def get_fernet_instance() -> Fernet:
    """
    Returns a Fernet cipher instance initialized with settings.FIELD_ENCRYPTION_KEY.
    Falls back gracefully in local dev if no key was generated yet.
    """
    raw_key = getattr(settings, 'FIELD_ENCRYPTION_KEY', '') or ''
    key_bytes = raw_key.strip().encode('utf-8') if raw_key else b''

    if not key_bytes:
        if not settings.DEBUG:  # never encrypt real tokens with a key that is public in this repo
            raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is required when DEBUG is off.")
        logger.warning("FIELD_ENCRYPTION_KEY is not set: using the public development key (DEBUG only).")
        return Fernet(_DEV_FERNET_KEY)

    try:
        return Fernet(key_bytes)
    except (ValueError, TypeError) as exc:
        if not settings.DEBUG:  # a malformed key would silently weaken encryption of stored tokens
            raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is not a valid Fernet key.") from exc
        logger.warning(
            "Invalid FIELD_ENCRYPTION_KEY specified (%s). Generating fallback Fernet.",
            exc
        )
        # Ensure a 32-byte urlsafe base64 key
        derived_key = base64.urlsafe_b64encode(hashlib.sha256(key_bytes).digest())
        return Fernet(derived_key)


def encrypt_token(raw_token: str) -> str:
    """
    Encrypts sensitive tokens (Meta system user token / page access token) before saving.
    """
    if not raw_token:
        return ""
    cipher = get_fernet_instance()
    encrypted_bytes = cipher.encrypt(raw_token.encode('utf-8'))
    return encrypted_bytes.decode('utf-8')


def decrypt_token(encrypted_token: str) -> str:
    """
    Decrypts encrypted tokens.
    """
    if not encrypted_token:
        return ""
    cipher = get_fernet_instance()
    try:
        decrypted_bytes = cipher.decrypt(encrypted_token.encode('utf-8'))
        return decrypted_bytes.decode('utf-8')
    except InvalidToken:
        logger.error("Failed to decrypt access token. Key mismatch or corrupted data.")
        return ""
    except Exception as exc:
        logger.error("Unexpected error decrypting access token: %s", exc)
        return ""


def verify_meta_signature(payload_bytes: bytes, signature_header: Optional[str], app_secret: str) -> bool:
    """
    Validates X-Hub-Signature-256 header sent by Meta Webhooks.
    Header format: 'sha256=<hmac_hex_digest>'
    """
    if not app_secret:
        return False  # nothing to verify against; the view decides whether unsigned events are allowed

    if not signature_header:
        logger.warning("Webhook request missing X-Hub-Signature-256 header while APP_SECRET is configured.")
        return False

    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False
    received_hash = signature_header[len(prefix):]

    expected_hash = hmac.new(
        key=app_secret.encode('utf-8'),
        msg=payload_bytes,
        digestmod=hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(received_hash, expected_hash)


def sanitize_headers(headers_dict: dict) -> dict:
    """
    Removes sensitive authorization headers before logging to WebhookLog.
    """
    sanitized = {}
    sensitive_keys = {'authorization', 'x-hub-signature-256', 'cookie', 'token'}
    for key, value in headers_dict.items():
        if key.lower() in sensitive_keys:
            sanitized[key] = '[REDACTED]'
        else:
            sanitized[key] = value
    return sanitized
