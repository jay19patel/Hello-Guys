from datetime import timedelta
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from meta_connector.utils import encrypt_token, decrypt_token


class PlatformAccount(models.Model):
    """
    Represents a registered Meta account (WhatsApp Business Phone or Instagram Account).
    Enables multi-account and multi-channel connection in a single database.
    """
    PLATFORM_WHATSAPP = 'whatsapp'
    PLATFORM_INSTAGRAM = 'instagram'
    PLATFORM_FACEBOOK = 'facebook'

    PLATFORM_CHOICES = (
        (PLATFORM_WHATSAPP, _('WhatsApp Cloud API')),
        (PLATFORM_INSTAGRAM, _('Instagram Messaging API')),
        (PLATFORM_FACEBOOK, _('Facebook Messenger')),
    )

    platform = models.CharField(
        max_length=20,
        choices=PLATFORM_CHOICES,
        default=PLATFORM_WHATSAPP,
        db_index=True
    )
    display_name = models.CharField(max_length=255, help_text="e.g. Primary Support WhatsApp")
    phone_number_id = models.CharField(
        max_length=100,
        blank=True,
        default='',
        db_index=True,
        help_text="Meta WhatsApp Phone Number ID"
    )
    instagram_id = models.CharField(
        max_length=100,
        blank=True,
        default='',
        db_index=True,
        help_text="Meta Instagram Scoped Business Account ID / Page ID"
    )
    access_token_encrypted = models.TextField(
        blank=True,
        default='',
        help_text="Encrypted Meta permanent system user or page access token"
    )
    verify_token = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Optional custom verify token for this specific account"
    )
    is_active = models.BooleanField(default=True, db_index=True)
    ai_auto_reply = models.BooleanField(
        default=False,
        help_text="Reply to incoming DMs with the website AI assistant (one AI thread per contact)"
    )

    # Token health, filled from Meta's /debug_token (see TokenService). checked_at=None means never checked.
    token_type = models.CharField(max_length=20, blank=True, default='', help_text="PAGE, USER or SYSTEM_USER")
    token_is_valid = models.BooleanField(null=True, blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True, help_text="Empty = never expires")
    token_data_access_expires_at = models.DateTimeField(null=True, blank=True)
    token_scopes = models.JSONField(default=list, blank=True)
    token_error = models.TextField(blank=True, default='')
    token_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Platform Account')
        verbose_name_plural = _('Platform Accounts')
        ordering = ['-created_at']

    def __str__(self):
        account_id = self.phone_number_id if self.platform == self.PLATFORM_WHATSAPP else self.instagram_id
        return f"{self.get_platform_display()} - {self.display_name} ({account_id or 'No ID'})"

    def set_access_token(self, raw_token: str):
        """Encrypts and sets the access token."""
        self.access_token_encrypted = encrypt_token(raw_token)

    def get_access_token(self) -> str:
        """Decrypts and returns the raw access token."""
        if self.access_token_encrypted:
            return decrypt_token(self.access_token_encrypted)
        return ""

    REQUIRED_SCOPES = {
        PLATFORM_INSTAGRAM: ['instagram_basic', 'instagram_manage_messages', 'instagram_manage_comments', 'pages_manage_metadata'],
        PLATFORM_WHATSAPP: ['whatsapp_business_messaging', 'whatsapp_business_management'],
        PLATFORM_FACEBOOK: ['pages_messaging', 'pages_manage_metadata', 'pages_read_engagement'],
    }
    TOKEN_WARN_DAYS = 7

    @property
    def platform_icon(self) -> str:
        """FontAwesome icon class for this platform."""
        icons = {
            self.PLATFORM_WHATSAPP: 'fa-brands fa-whatsapp',
            self.PLATFORM_INSTAGRAM: 'fa-brands fa-instagram',
            self.PLATFORM_FACEBOOK: 'fa-brands fa-facebook-messenger',
        }
        return icons.get(self.platform, 'fa-solid fa-comments')

    @property
    def platform_color_class(self) -> str:
        """CSS branding color class for this platform."""
        colors = {
            self.PLATFORM_WHATSAPP: 'text-wa',
            self.PLATFORM_INSTAGRAM: 'text-ig',
            self.PLATFORM_FACEBOOK: 'text-[#0084FF]',
        }
        return colors.get(self.platform, 'text-accent')

    @property
    def token_deadline(self):
        """Earliest moment the token stops working (expiry or data-access expiry), or None."""
        dates = [d for d in (self.token_expires_at, self.token_data_access_expires_at) if d]
        return min(dates) if dates else None

    @property
    def token_health(self) -> str:
        """missing | unknown | invalid | expired | expiring | ok"""
        if not self.access_token_encrypted:
            return 'missing'
        if not self.token_checked_at or self.token_is_valid is None:
            return 'unknown'
        if not self.token_is_valid:
            return 'invalid'
        deadline = self.token_deadline
        if deadline and deadline <= timezone.now():
            return 'expired'
        if deadline and deadline - timezone.now() < timedelta(days=self.TOKEN_WARN_DAYS):
            return 'expiring'
        return 'ok'

    @property
    def missing_scopes(self) -> list:
        if not self.token_checked_at:
            return []
        return [s for s in self.REQUIRED_SCOPES.get(self.platform, []) if s not in (self.token_scopes or [])]
