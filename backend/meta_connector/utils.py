import hashlib
import hmac
import logging
from typing import Optional

from cryptography.fernet import Fernet

from hello_guy_ai.crypto import decrypt, encrypt, _fernet

logger = logging.getLogger(__name__)


def get_fernet_instance() -> Fernet:
    """Return the shared Fernet cipher initialized from settings.FIELD_ENCRYPTION_KEY."""
    return _fernet()


def encrypt_token(raw_token: str) -> str:
    """Encrypt sensitive Meta tokens before saving to DB."""
    return encrypt(raw_token)


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt stored Meta tokens (returns '' on mismatch or corruption)."""
    return decrypt(encrypted_token)


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
