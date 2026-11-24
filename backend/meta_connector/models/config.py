import secrets
from django.db import models
from django.utils.translation import gettext_lazy as _
from meta_connector.utils import decrypt_token, encrypt_token


def _new_verify_token():
    return secrets.token_urlsafe(24)


class MetaConfig(models.Model):
    """
    App-level Meta settings, edited from Dashboard > Settings (single row).
    Per-number / per-page IDs and access tokens live on PlatformAccount.
    """
    app_name = models.CharField(max_length=150, default='Hello Guys', help_text="Meta App Name")
    verify_token = models.CharField(
        max_length=255,
        default=_new_verify_token,
        help_text="Verify Token entered in Meta App Dashboard > Webhooks"
    )
    # Fernet-encrypted (meta_connector.utils), like access tokens; read/write through the `app_secret` property.
    app_secret_encrypted = models.TextField(
        blank=True,
        default='',
        help_text="Meta App Secret (App Settings > Basic) used for HMAC SHA-256 signature verification"
    )
    graph_api_version = models.CharField(
        max_length=20,
        default='v21.0',
        help_text="Meta Graph API Version (e.g., v21.0)"
    )
    webhook_log_retention_days = models.PositiveIntegerField(
        default=30, choices=[(7, '7 days'), (30, '30 days'), (60, '60 days'), (90, '90 days'), (180, '180 days')],
        help_text="Raw webhook logs older than this are deleted automatically. Failed ones are kept at least 90 days for recovery."
    )
    last_log_cleanup_at = models.DateTimeField(null=True, blank=True)
    # Webhooks rejected for a bad X-Hub-Signature-256 (usually a wrong App Secret). Count only: the body is untrusted.
    signature_failures = models.PositiveIntegerField(default=0)
    last_signature_failure_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Meta App Configuration')
        verbose_name_plural = _('Meta App Configuration')

    def __str__(self):
        return f"{self.app_name} ({self.graph_api_version})"

    @classmethod
    def get_solo(cls):
        """Returns the single config row, creating it with defaults on first use (race-safe: fixed pk)."""
        return cls.objects.order_by('pk').first() or cls.objects.get_or_create(pk=1)[0]

    @property
    def app_secret(self) -> str:
        return decrypt_token(self.app_secret_encrypted)

    @app_secret.setter
    def app_secret(self, raw: str) -> None:
        self.app_secret_encrypted = encrypt_token(raw or '')

    @property
    def graph_base_url(self) -> str:
        return f"https://graph.facebook.com/{self.graph_api_version}"
